# -*- coding: utf-8 -*-
"""
llm_common.option_sanitizer - Whitelist filtering for `options`.

The `options` field of a `generate` request is a free-form JSON object
from the client's point of view. Each server kind forwards only a
known subset to its backend and drops everything else. This module
provides a single, extensible implementation so that:

  - llm_proxy.py      forwards scalars only.
  - llm_proxy_tool.py forwards scalars plus tools / tool_choice.
  - llm_service.py    forwards scalars plus thinking / ephemeral.

Design
------
A scalar option is described by a `ScalarSpec`:

    (key, cast, lo, hi)

  * key    - the option name as it appears in the request.
  * cast   - int or float, used to coerce the incoming value.
  * lo     - inclusive lower bound applied AFTER casting.
  * hi     - inclusive upper bound applied AFTER casting.

Casting rules
-------------
* `None` values are dropped.
* Boolean values are DROPPED explicitly. Python's bool is a subclass
  of int, so `int(True) == 1` and `float(False) == 0.0` silently. A
  client that sends `{"max_tokens": true}` almost certainly made a
  mistake, and turning it into 1 hides that mistake behind a value the
  server would otherwise accept. Dropping the key lets the server
  fall back to its documented default.
* Numeric strings are accepted (e.g. `"100"` for max_tokens). This is
  a deliberate lenient behaviour kept from the original implementation;
  it costs nothing and helps clients that send options as strings.
* Cast errors and out-of-range values are dropped silently (the key
  simply does not appear in the result). This matches the historical
  behaviour of all three servers.
* Arithmetic overflow during casting (e.g. `int(float('inf'))`) is
  treated the same as a type error: the key is dropped.

Passthrough keys are handled separately: their values are copied
verbatim if they satisfy a caller-supplied predicate. This covers
`tools` (must be a list) and `tool_choice` (must be a str or dict).

Custom keys are handled by a caller-supplied callback, which receives
the raw value and may return either a value (to include) or a sentinel
to indicate "drop this key". This covers `thinking` and `ephemeral`,
whose handling differs between server kinds.

Logging
-------
This module does not import any logging framework, to keep Layer 1
dependency-free. If a `debug_log` callable is provided, it is invoked
AT MOST ONCE per sanitize_options() call, and ONLY when at least one
key was dropped. The caller decides how (or whether) to emit it.

This module has no dependencies on any other llm_common module.
"""

from typing import Any, Callable, Dict, Iterable, Optional, Tuple


# Sentinel returned by a custom handler to indicate "drop this key".
DROP = object()


# (key, cast, lo, hi) - the scalar options every server kind accepts.
# These are the five options forwarded to the OpenAI-compatible backend
# and the llama.cpp sampler alike.
COMMON_SCALAR_SPECS: Tuple[Tuple[str, type, float, float], ...] = (
    ("max_tokens",     int,   1,   1 << 20),
    ("temperature",    float, 0.0, 2.0),
    ("top_p",          float, 0.0, 1.0),
    ("top_k",          int,   0,   1000),
    ("repeat_penalty", float, 0.0, 4.0),
)


def _apply_scalar_specs(
    raw: Dict[str, Any],
    specs: Iterable[Tuple[str, type, float, float]],
    out: Dict[str, Any],
) -> None:
    """
    Coerce and clamp scalar options from `raw` into `out`.

    For each spec, if the key is present in `raw` and its value is not
    None, the value is cast with the spec's type and clamped to
    [lo, hi]. The following inputs are DROPPED rather than cast:

      * None                             - the key is simply not set
      * bool                             - `isinstance(True, int)` is
                                           True but a boolean is almost
                                           always a client mistake; see
                                           the module docstring
      * anything that raises TypeError,
        ValueError, or ArithmeticError
        during casting

    Numeric strings ("100") and numeric floats (5.0) ARE accepted for
    int-valued specs, because `int("100")` and `int(5.0)` both succeed
    and do the intuitive thing. This is the same lenient behaviour the
    original implementation had.

    This helper mutates `out` in place and returns nothing. It never
    raises.
    """
    for key, cast, lo, hi in specs:
        if key not in raw:
            continue
        value = raw[key]
        if value is None:
            continue

        # Reject bool explicitly. `bool` is a subclass of `int`, so
        # without this check `int(True)` would produce 1 and
        # `float(False)` would produce 0.0 silently. A boolean where a
        # number is expected is a client-side bug; dropping the key
        # lets the server fall back to its documented default and
        # surfaces the bug during development instead of at runtime.
        if isinstance(value, bool):
            continue

        try:
            v = cast(value)
        except (TypeError, ValueError, ArithmeticError):
            # ArithmeticError covers OverflowError, which is raised by
            # int(float('inf')). ValueError covers int(float('nan')).
            # TypeError covers int([1,2]) and friends.
            continue

        if v < lo:
            v = lo
        elif v > hi:
            v = hi
        out[key] = v


def sanitize_options(
    raw: Optional[Dict[str, Any]],
    *,
    scalar_specs: Iterable[Tuple[str, type, float, float]] = COMMON_SCALAR_SPECS,
    passthrough: Optional[Dict[str, Callable[[Any], bool]]] = None,
    custom: Optional[Dict[str, Callable[[Any], Any]]] = None,
    debug_log: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """
    Filter an `options` dict down to the keys a given server accepts.

    Args:
        raw:
            The raw options dict from the request. If None or not a
            dict, an empty result is returned.

        scalar_specs:
            Sequence of (key, cast, lo, hi) tuples for scalar options.
            Defaults to COMMON_SCALAR_SPECS (max_tokens, temperature,
            top_p, top_k, repeat_penalty).

        passthrough:
            Mapping of key -> predicate. For each key present in `raw`
            whose value satisfies the predicate, the value is copied
            verbatim into the result. Use this for `tools` (predicate:
            isinstance list) and `tool_choice` (predicate: isinstance
            str or dict).

        custom:
            Mapping of key -> handler. The handler receives the raw
            value and returns either a value to include, or the DROP
            sentinel to indicate the key should be omitted. Use this
            for server-specific fields such as `thinking` and
            `ephemeral`.

        debug_log:
            Optional callable that receives a single human-readable
            string describing the keys that were dropped. Called AT
            MOST ONCE per sanitize_options() invocation, and ONLY if
            at least one key was dropped. If None, no logging occurs.

    Returns:
        A new dict containing only the accepted keys. Never raises on
        malformed input; offending values are dropped.
    """
    result: Dict[str, Any] = {}

    if not isinstance(raw, dict):
        return result

    # 1) Scalar options: coerce, clamp, drop on error.
    _apply_scalar_specs(raw, scalar_specs, result)

    # 2) Passthrough options: copy verbatim if the predicate accepts.
    if passthrough:
        for key, predicate in passthrough.items():
            if key not in raw:
                continue
            value = raw[key]
            if value is None:
                continue
            try:
                if predicate(value):
                    result[key] = value
            except Exception:
                # A misbehaving predicate must never crash the server.
                continue

    # 3) Custom options: let the caller decide.
    if custom:
        for key, handler in custom.items():
            if key not in raw:
                continue
            value = raw[key]
            try:
                handled = handler(value)
            except Exception:
                # A misbehaving handler must never crash the server.
                continue
            if handled is DROP:
                continue
            result[key] = handled

    # 4) Diagnostic: report any keys that were not accepted, so that
    #    operators can see what the client sent and what was ignored.
    #    Called at most once, and only when at least one key was
    #    dropped.
    if debug_log is not None:
        accepted = set(result.keys())
        dropped = [k for k in raw.keys() if k not in accepted]
        if dropped:
            try:
                debug_log(
                    "Options keys not supported by this server and "
                    "ignored: " + ", ".join(sorted(dropped))
                )
            except Exception:
                pass

    return result