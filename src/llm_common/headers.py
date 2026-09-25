# -*- coding: utf-8 -*-
"""
llm_common.headers - JSON, HTTP header, and key-file utilities.

Shared helpers used by the proxy siblings (llm_proxy.py and
llm_proxy_tool.py) and by any future service that speaks to an
OpenAI-compatible backend over HTTP.

Contents
--------
  jdump              - UTF-8 JSON serialization with ensure_ascii=False
  parse_extra_headers - parse a JSON object string into a header dict
  load_key_from_file  - read a single-line API key from a file

Design notes
------------
* jdump delegates to lingofuse.lf_io.dumps_json, the toolchain-wide
  JSON serialization policy. This guarantees that the HTTP path
  (sse_client.OpenAIStreamClient) and the LF DataHandle path
  (lingofuse.lf_io.write_json) share exactly one policy:

      json.dumps(obj, ensure_ascii=False, default=str)

  Consequences of that shared policy:

    - ensure_ascii=False means Chinese, emoji, and other non-ASCII
      content is emitted as literal UTF-8 bytes, never as \\uXXXX
      escapes. This is required for cross-language fidelity.

    - default=str means an unexpected non-serializable object (for
      example a tool result that contains a custom type) degrades to
      its str() representation instead of raising TypeError. See the
      jdump docstring for the trade-off.

  Centralizing the policy in lingofuse.lf_io means that a future
  change to the serialization rules (adding sort_keys, switching to
  orjson, adding an indent) is made in exactly one place and takes
  effect on both paths at once.

* The dependency direction is:

      llm_common.headers  ->  lingofuse.lf_io

  This is an ALLOWED direction. The rule "the lingofuse package must
  not import anything from llm_common" holds in the opposite
  direction, so no import cycle can form. As of the current revision,
  lingofuse.lf_io depends only on the standard library
  (ctypes, json) and on lingofuse._lf_native. Importing it from
  llm_common therefore introduces no cycle.

  This edge is not an isolated exception. Within the lingofuse
  package itself, the same module is consumed by:
    - lingofuse.core        (DataHandle payload I/O, App c_char_p args)
    - lingofuse.server      (Server c_char_p args)
    - lingofuse.client      (C4 c_char_p args)
    - lingofuse.serializers (default_serializer delegates dumps_json)
    - lingofuse.bridge      (all payload I/O and c_char_p args)
    - lingofuse.__init__    (module-level convenience c_char_p args)

  The llm_common.headers -> lingofuse.lf_io edge is therefore one of
  a family of consumer edges, all of which share the same
  serialization policy. See lingofuse.lf_io's module docstring for
  the complete list.

* parse_extra_headers raises ValueError on any malformed input so that
  the caller can exit with a clear [FATAL] message at startup rather
  than failing later during a request.

* load_key_from_file raises ValueError on any read or validation
  problem, for the same reason. It also handles UTF-8 BOM (a very
  common artifact of Windows editors) and rejects multi-line files,
  which would otherwise produce a corrupted Authorization header.

* UTF-8 encoding safety (G1 fix)
  jdump is the HTTP counterpart of lingofuse.lf_io.dumps_json. The
  resulting JSON text is now encoded to UTF-8 with errors="replace"
  so that a lone surrogate (which can only arise from a
  non-conformant producer that injected a raw "\\udXXX" escape into a
  JSON string which json.loads then decoded into a Python surrogate)
  is emitted as U+FFFD instead of raising UnicodeEncodeError.

  Rationale: dumps_json itself is well-behaved (it uses
  ensure_ascii=False and default=str, neither of which can produce a
  surrogate on its own). But dumps_json is not responsible for
  validating its input; if the object graph contains a surrogate,
  the encoder faithfully emits it as a Python str. The .encode()
  step is therefore the last place where the surrogate would be
  caught, and errors="replace" makes that step infallible. See the
  module docstring of lingofuse.lf_io for the toolchain-wide
  encoding policy.

This module depends on lingofuse.lf_io only.
"""

import json
from typing import Any, Dict

from lingofuse.lf_io import dumps_json


def jdump(obj: Any) -> bytes:
    """
    Serialize a Python object to UTF-8 JSON bytes.

    This is a thin wrapper around lingofuse.lf_io.dumps_json, which is
    the single source of truth for the toolchain's JSON serialization
    policy. The properties below are inherited from that policy:

      * ensure_ascii=False: non-ASCII characters are preserved as
        literal UTF-8 bytes instead of being escaped as \\uXXXX.

      * default=str: any object that the JSON encoder cannot serialize
        is converted with str() rather than raising TypeError. This
        keeps the server alive if a tool result happens to contain a
        custom type.

    When does `default=str` actually fire?
    --------------------------------------
    In the normal request/response flow it never fires: every value
    that reaches jdump is a plain dict/list/str/int/float/bool/None.
    It exists only as a safety net for two cases:

      1. A tool result returned by MCP contains a type the caller
         forgot to convert (for example a custom class or a datetime).
         Without default=str, the entire generate() call would abort
         with TypeError; with it, the value degrades to its string
         representation and the model still gets a usable result.

      2. A future extension adds a field whose type is not yet
         JSON-serializable and the reviewer forgets to add an encoder.
         The safety net keeps the service running while the bug is
         fixed.

    Trade-off
    ---------
    The cost is that a genuinely non-serializable value becomes a
    silent string. The alternative (raising) would be worse for a
    long-running service whose primary purpose is to keep talking to
    a model. Callers that care about strict type fidelity should
    pre-serialize their payload with a strict encoder instead.

    Encoding safety (G1 fix)
    ------------------------
    The final .encode() uses errors="replace". A lone surrogate
    cannot be encoded to valid UTF-8; without the errors parameter
    the call would raise UnicodeEncodeError and abort the entire
    request. With it, the surrogate is emitted as U+FFFD (the
    Unicode replacement character), the payload stays valid UTF-8,
    and the caller is never interrupted.

    In practice a well-formed caller never produces a surrogate.
    The fix exists so that a broken caller (or a data path that
    happened to transport a raw "\\udXXX" JSON escape from a
    non-conformant producer) cannot bring the HTTP layer down.

    Args:
        obj: Any JSON-serializable Python object.

    Returns:
        UTF-8 encoded bytes, ready to be sent over the wire.
    """
    return dumps_json(obj).encode("utf-8", errors="replace")


def parse_extra_headers(raw: str) -> Dict[str, str]:
    """
    Parse a JSON object string into a flat {header_name: value} dict.

    The input is expected to be a JSON object whose keys and values are
    both strings, for example:

        '{"HTTP-Referer": "https://example.com", "X-Title": "MyApp"}'

    Empty or whitespace-only input yields an empty dict (the common
    case when the user does not pass --backend-extra-headers).

    Input source
    ------------
    `raw` is a command-line argument or an environment variable, not
    an inbound network payload. It is parsed with the standard
    library's json.loads() and any failure is surfaced as a
    ValueError so that the caller can abort startup with a clear
    [FATAL] message.

    This is deliberately NOT routed through the unified JSON repair
    preprocessor:

      * The toolchain-wide repair policy is for inbound external
        payloads whose source is a non-conformant producer that might
        emit malformed JSON. A CLI flag is written by the operator
        and is expected to be exactly what they typed.

      * The caller (llm_proxy._init_global_config /
        llm_proxy_tool._init_global_config) already wraps this
        function in try/except ValueError and exits with a clear
        message. Repairing the flag silently would hide the typo
        from the operator.

    Args:
        raw: The raw string from the command line or environment.

    Returns:
        A dict mapping header names to header values. Empty if raw is
        blank.

    Raises:
        ValueError: if raw is not valid JSON, or is not a JSON object,
            or contains non-string keys or values. The message names
            the offending key/value pair so the user can fix the input.
    """
    if not raw or not raw.strip():
        return {}

    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"--backend-extra-headers is not valid JSON: {e}"
        ) from e

    if not isinstance(obj, dict):
        # Include the actual type name so the user can tell at a
        # glance what went wrong: an array, a bare string, a number,
        # etc. json.loads never returns None for valid input, so this
        # branch catches genuine structural mistakes, not edge cases.
        raise ValueError(
            f"--backend-extra-headers must be a JSON object, "
            f"got {type(obj).__name__}"
        )

    out: Dict[str, str] = {}
    for k, v in obj.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ValueError(
                f"--backend-extra-headers key/value must be strings: "
                f"{k!r} -> {v!r}"
            )
        out[k] = v
    return out


def load_key_from_file(path: str) -> str:
    """
    Read a single-line API key from a file.

    The entire file content is read and returned as the key. Leading
    and trailing whitespace is stripped. UTF-8 BOM is consumed
    automatically (see below). Multi-line files are rejected, because
    a key with an embedded newline would corrupt the Authorization
    header at request time with an error that is very hard to trace
    back to the file.

    BOM handling
    ------------
    On Windows it is extremely common for a text editor to save a
    small key file with a UTF-8 BOM (the bytes EF BB BF at the start).
    If the file is opened as plain "utf-8", the BOM becomes a U+FEFF
    character at the start of the string. str.strip() does NOT remove
    it (U+FEFF is not whitespace), so the key would silently be
    prefixed with an invisible character and authentication would fail
    with no useful clue.

    Opening the file with encoding="utf-8-sig" makes Python consume
    the BOM automatically when present, and behave identically to
    "utf-8" when it is absent. This is the standard, unambiguous way
    to read UTF-8 files that may or may not carry a BOM.

    Multi-line rejection
    --------------------
    After stripping the trailing newline, the returned key must not
    contain any further CR or LF characters. If it does, the file is
    almost certainly not a key file (or is a concatenation of several
    keys), and continuing would produce an Authorization header like
    "Bearer sk-a\\nsk-b", which http.client rejects at send time with
    a ValueError that does not mention the file path. Failing here
    with a precise message is both safer and more helpful.

    Args:
        path: Path to the file containing the key.

    Returns:
        The key as a non-empty string without surrounding whitespace
        and without any embedded newline.

    Raises:
        ValueError: if the file cannot be read, if it is empty after
            stripping, or if it contains an embedded newline.
    """
    try:
        # encoding="utf-8-sig" transparently handles the UTF-8 BOM
        # that Windows editors commonly add. Files without a BOM are
        # read identically to plain utf-8.
        with open(path, "r", encoding="utf-8-sig") as f:
            key = f.read().strip()
    except OSError as e:
        raise ValueError(
            f"Cannot read --backend-key-file {path!r}: {e}"
        ) from e

    if not key:
        raise ValueError(f"--backend-key-file {path!r} is empty")

    # Reject embedded newlines. strip() above only removed leading and
    # trailing whitespace; any CR or LF still inside the string means
    # the file is not a well-formed single-line key file.
    if "\n" in key or "\r" in key:
        raise ValueError(
            f"--backend-key-file {path!r} must contain a single line; "
            f"found an embedded newline. Check for a stray blank line "
            f"or for multiple keys concatenated in the file."
        )

    return key