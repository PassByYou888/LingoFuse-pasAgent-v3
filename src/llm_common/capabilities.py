# -*- coding: utf-8 -*-
"""
llm_common.capabilities - API capability matrix definitions.

Every LingoFuse LLM service publishes a capability matrix through the
`get_api_capabilities` Call API. The matrix tells clients which API
names are supported by the currently running server kind, so that
clients can short-circuit unsupported operations locally instead of
paying for a wasted RPC round-trip.

Response shape (identical across all server kinds):

    {
      "code": 0,
      "server_kind": "service" | "proxy",
      "capabilities": {
          "<api_name>": 0 | 1,
          ...
      }
    }

A value of 1 means "supported by this server", 0 means "not supported"
(the API belongs to a sibling server kind).

Contents
--------
  SERVER_KIND_SERVICE / SERVER_KIND_PROXY
      Canonical server kind strings. Use these instead of literal
      strings to avoid typos.

  COMMON_CAPABILITY_KEYS
      The set of keys every server kind exposes (all with value 1 by
      default).

  KNOWN_EXTRA_CAPABILITY_KEYS
      The set of additional keys that LTB (llm_proxy_tool.py) may add
      on top of the common set. These are NOT part of the common
      protocol: a well-behaved client must consult the capability
      matrix rather than assuming any of them exist.

  build_capabilities(...)
      Build a capabilities dict from a small set of named knobs plus
      an optional `extras` dict for server-kind-specific keys.

  build_response(server_kind, caps)
      Wrap a capabilities dict in the standard response envelope.

  split_supported(caps)
      Return (supported_names, unsupported_names), both sorted. Used
      by banner printing and by the health response.

  all_known_capability_names()
      Return the sorted union of common + known extras. Useful for
      clients that want to iterate over "every API this protocol
      might advertise" without hard-coding the list.

Design notes
------------
* The set of COMMON keys is fixed and defined here. Every server kind
  must expose exactly these keys (plus optionally `extras`). Adding a
  new common key is a protocol change: update this module, the
  clients, and the documentation together.

* `set_system_message`, `attachments`, and `vision` are not "common
  keys with a fixed value"; they are "common keys whose value depends
  on the server kind and its configuration". They are therefore
  exposed as keyword arguments rather than being part of the base
  dict.

* `extras` is reserved for server-kind-specific keys such as
  `tools`, `tool_calls`, and `tool_results` on the LTB server. These
  keys are not part of the common set and must not appear on servers
  that do not support them. KNOWN_EXTRA_CAPABILITY_KEYS lists the
  extras currently defined by the protocol; new extras should be
  added there so that `all_known_capability_names()` stays accurate.

* Values in the returned dict are ALWAYS int 0 or 1. `build_capabilities`
  coerces every input (including True/False, arbitrary truthy/falsy
  objects, and any int) through `int(bool(value))` so that clients can
  rely on a strict integer type.

This module has no dependencies on any other llm_common module.
"""

from typing import Any, Dict, List, Optional, Tuple


# ----------------------------------------------------------------------
# Server kind strings
# ----------------------------------------------------------------------

SERVER_KIND_SERVICE = "service"
SERVER_KIND_PROXY = "proxy"


# ----------------------------------------------------------------------
# Common capability keys
# ----------------------------------------------------------------------

# Keys that every server kind exposes, all with value 1 by default.
# These are the APIs that the client may invoke unconditionally once
# it has confirmed the server is reachable.
COMMON_CAPABILITY_KEYS: Tuple[str, ...] = (
    "generate",
    "create_session",
    "close_session",
    "cancel_session",
    "list_sessions",
    "health",
    "llm_stream",
)

# Keys that are always present after build_capabilities() but whose
# value is server-dependent. Listed here for documentation purposes
# and so that all_known_capability_names() can include them.
_SERVER_DEPENDENT_KEYS: Tuple[str, ...] = (
    "set_system_message",
    "attachments",
    "vision",
)

# Additional keys that a specific server kind may advertise. These are
# not part of the common set; a client must consult the matrix.
KNOWN_EXTRA_CAPABILITY_KEYS: Tuple[str, ...] = (
    "tools",
    "tool_calls",
    "tool_results",
)


# ----------------------------------------------------------------------
# Builders
# ----------------------------------------------------------------------

def build_capabilities(
    *,
    set_system_message: int = 0,
    attachments: int = 1,
    vision: int = 0,
    extras: Optional[Dict[str, Any]] = None,
) -> Dict[str, int]:
    """
    Build a capabilities dict from a small set of named knobs.

    Every key in the returned dict is an integer: 1 means supported,
    0 means not supported.

    Args:
        set_system_message:
            1 if this server supports the set_system_message API
            (llm_service.py), 0 otherwise (llm_proxy.py and
            llm_proxy_tool.py, both stateless forwarders).

        attachments:
            1 if this server accepts the `attachments` field of a
            generate request. All server kinds accept text attachments
            after the attachment upgrade, so the default is 1.

        vision:
            1 if this server can forward image attachments to a
            vision-capable backend. The default is 0; each server must
            opt in explicitly via its --vision flag.

        extras:
            Optional additional keys to merge in. Used for LTB's
            `tools`, `tool_calls`, and `tool_results` keys, which are
            not part of the common set.

            Every value is coerced with `int(bool(value))`. This means
            a caller may pass True / False / 1 / 0 / "yes" / "" and the
            result is always a strict integer 0 or 1. The coercion is
            deliberate: it lets internal callers use Python truthiness
            without worrying about what the wire format expects.

            If `extras` is None or not a dict, it is ignored. The
            returned dict is still well-formed.

    Returns:
        A new dict mapping capability names to integers. The caller
        owns the dict and may mutate it freely.
    """
    caps: Dict[str, int] = {key: 1 for key in COMMON_CAPABILITY_KEYS}

    caps["set_system_message"] = int(bool(set_system_message))
    caps["attachments"] = int(bool(attachments))
    caps["vision"] = int(bool(vision))

    if isinstance(extras, dict):
        for key, value in extras.items():
            caps[str(key)] = int(bool(value))

    return caps


def build_response(
    server_kind: str,
    caps: Dict[str, int],
) -> Dict[str, Any]:
    """
    Wrap a capabilities dict in the standard response envelope.

    The envelope is what `get_api_capabilities` returns:

        {
          "code": 0,
          "server_kind": "<server_kind>",
          "capabilities": { ... }
        }

    The `capabilities` sub-dict is copied so that the caller's dict is
    not aliased into the response. This prevents a later mutation of
    the server's internal dict from silently changing the response
    that a client already received.

    Args:
        server_kind: Either SERVER_KIND_SERVICE or SERVER_KIND_PROXY.
        caps:        The capabilities dict to embed. A non-dict value
                     is treated as an empty capabilities map rather
                     than raising, so that a programming error in one
                     handler does not break the whole response.

    Returns:
        A fresh response dict, safe to serialize to JSON.
    """
    if isinstance(caps, dict):
        embedded = dict(caps)
    else:
        embedded = {}
    return {
        "code": 0,
        "server_kind": server_kind,
        "capabilities": embedded,
    }


def split_supported(
    caps: Dict[str, int],
) -> Tuple[List[str], List[str]]:
    """
    Split a capabilities dict into (supported, unsupported) name lists.

    Both lists are sorted alphabetically so that banner output and
    health responses are deterministic across runs and platforms.

    A key is considered supported when its value is truthy, and
    unsupported when it is falsy. The strict wire format uses int 0
    and 1, but using truthiness rather than `== 1` makes the function
    forgiving: a caller that passes True / "yes" / a positive int
    still gets the intuitive result. This matters because
    `build_capabilities` already coerces every value with `int(bool(...))`,
    but a caller that hand-builds a capabilities dict for testing or
    debugging might not.

    Args:
        caps: A capabilities dict. If it is not a dict, both lists are
              returned empty.

    Returns:
        A tuple (supported, unsupported), where each element is a list
        of API names. Names are never duplicated.
    """
    supported: List[str] = []
    unsupported: List[str] = []

    if not isinstance(caps, dict):
        return supported, unsupported

    for name, value in caps.items():
        if bool(value):
            supported.append(str(name))
        else:
            unsupported.append(str(name))
    supported.sort()
    unsupported.sort()
    return supported, unsupported


# ----------------------------------------------------------------------
# Discovery helper
# ----------------------------------------------------------------------

def all_known_capability_names() -> List[str]:
    """
    Return the sorted union of every capability name this protocol
    knows about.

    This includes:

      * the common keys (COMMON_CAPABILITY_KEYS),
      * the server-dependent keys that build_capabilities() always
        emits (set_system_message, attachments, vision),
      * the extras that a specific server kind may add (LTB's tools,
        tool_calls, tool_results).

    The result is what a client could reasonably expect to find in a
    capability matrix. It is NOT what any single server returns; a
    client must always consult the matrix it actually received.

    Returns:
        A sorted list of capability names. Never empty.
    """
    names = set(COMMON_CAPABILITY_KEYS)
    names.update(_SERVER_DEPENDENT_KEYS)
    names.update(KNOWN_EXTRA_CAPABILITY_KEYS)
    return sorted(names)