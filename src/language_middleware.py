# -*- coding: utf-8 -*-
"""
language_middleware.py - v7.7 (LingoFuse Native Multi-Language Middleware)

DESCRIPTION
    This module provides a language-agnostic middleware for LingoFuse,
    designed to act as a bridge between MCP (Model Context Protocol)
    servers (like mcp_api_tool.py) and a backend tool provider implemented
    in any language (Pascal, Python, etc.). It handles:

        - Lazy connection to a LingoFuse endpoint (IPC or TCP).
        - Dynamic retrieval of tool definitions from the backend via
          `agent_main`.
        - Log forwarding to the backend via `agent_log`.
        - Invocation of tools via `call_tool` by name.
        - Optional dynamic tool registration via the `register_agent`
          API.

    The middleware uses direct ctypes calls to the LingoFuse dynamic
    library and is fully thread-safe. It implements a singleton pattern
    to share the same connection across multiple components.

CHANGELOG (v7.7)
    * JSON handling unification (A2 / N1 / N2):
      - A2: The DEBUG-only pretty-print of the tool list response now
        goes through the standard library `json.dumps` with a full
        policy match to `lingofuse.lf_io.dumps_json`
        (`ensure_ascii=False, default=str`). The only intentional
        deviation is `indent=2`, kept for human readability in the
        debug stream; the deviation is documented at the call site.
        The previous `import json as _json` alias was removed; the
        module now imports `json` once at the top.
      - N1: `_fetch_tools_from_backend` now validates that the parsed
        response is a JSON object (dict) before calling `.get()`. A
        non-object response (list, string, number, bool, null) is
        treated as a soft failure: the tool list is cleared and the
        method returns, exactly as it does for a transport-level
        failure. Previously a non-object response would raise
        AttributeError and abort the caller's connection attempt.
      - N2: The per-tool loop in the same method now skips entries
        that are not JSON objects. A malformed tools array (for
        example one containing a bare string) previously raised
        AttributeError on `t.get(...)`; it now produces a per-entry
        warning and is skipped, so the rest of the list is still
        usable.
      The above are the only behavioural changes. All public API
      signatures, the singleton lifecycle, and the LingoFuse DataHandle
      I/O paths (which already went through `lf_io.read_json` /
      `lf_io.write_json` / `lf_io.read_json_or_bytes`) are unchanged.

    * JSON I/O audit (no code change needed):
      - `_reg_tool_callback`, `_fetch_tools_from_backend`, `log`, and
        `call_tool` all read and write LF DataHandle payloads through
        `lingofuse.lf_io`. That module is the single source of truth
        for the toolchain JSON policy (`ensure_ascii=False`,
        `default=str`, NUL framing, NUL-tolerant reads). No raw
        `json.loads` / `json.dumps` call exists on the LF payload
        path.
      - The only direct `json.dumps` call is the diagnostic pretty
        print addressed by A2 above.

CHANGELOG (v7.6)
    * All references to llm_common.lf_io were updated to
      lingofuse.lf_io, following the relocation of the lf_io module
      from the llm_common package into the lingofuse package. This
      keeps the dependency direction intact: the middleware is a
      consumer of the lingofuse package, and the unified DataHandle
      I/O now lives inside that package.

CHANGELOG (v7.5)
    * All JSON and string I/O on LingoFuse DataHandles is now delegated
      to the lf_io module. The local _write_string / _read_string
      helpers were removed. Every read and write of a payload now goes
      through lf_io.read_json / lf_io.read_json_or_bytes /
      lf_io.write_json / lf_io.cstr. This guarantees:
        - ensure_ascii=False on every JSON payload (no \\uXXXX escapes)
        - NUL termination on every string written to a DataHandle
        - NUL-tolerant reads (accepts raw JSON from HTTP bridges)
        - explicit NUL on every c_char_p LF_* parameter
      Request and response handles in _fetch_tools_from_backend(),
      log(), and call_tool() are now freed inside finally blocks, so
      an exception during payload I/O can no longer leak a DataHandle.
    * The native import list was reduced to only the functions that
      this module actually calls at the C ABI level. Byte-level
      LF_* functions (LF_WriteBuffer, LF_ReadBuffer, LF_GetPos,
      LF_GetSize, LF_GetBuffer) are now owned exclusively by the
      lf_io module. LF_SetPos is kept for a single local helper,
      `_rewind`, that resets the read position before reading a
      response.

CHANGELOG (v7.4)
    * FIXED: `_cleanup()` ordering. The previous implementation called
      LF_Shutdown() BEFORE LF_FreeApp(), which destroys the underlying
      TLF_App objects and leaves LF_FreeApp operating on a dangling
      handle. The new order is:

          LF_ExitMainThread() -> LF_FreeApp() -> LF_Shutdown()

      This matches the fix documented in test_lingofuse.py (P0-2) and
      the implementation of Server.stop(full_cleanup=True) in
      lingofuse/server.py.

    * FIXED: `_disconnect()` no longer calls LF_Shutdown(). The App
      handle owned by this middleware (self._app_hnd) must remain valid
      across a disconnect/reconnect cycle, because _update_config()
      followed by _connect() reuses the same handle. Calling
      LF_Shutdown() here would destroy the App and cause the next
      LF_PrepareClient() call to receive a dangling pointer.
      LF_Shutdown is now called only from _cleanup(), which runs at
      interpreter exit after the App has been released.

    * Both cleanup paths now use per-step try/except blocks. A failure
      in one step no longer prevents the remaining steps from running,
      and state flags are always reset.

    * Docstrings for _cleanup() and _disconnect() now document the
      ordering constraint explicitly, so a future edit does not
      accidentally reintroduce the bug.

CHANGELOG (v7.3)
    * `_read_string` now mirrors Pascal's LF_ReadString behavior: when
      no null terminator is found, it returns the entire remaining
      buffer instead of an empty bytes object. This is important for
      inputs that were not null-terminated (e.g. raw JSON from an HTTP
      bridge).
    * `call_tool` serializes arguments with `ensure_ascii=False` so
      that non-ASCII characters (e.g. Chinese) reach the backend
      unescaped.
    * `_reg_tool_callback` now reads the "name" field (matching the
      Pascal side's `do_register_agent`) instead of the non-existent
      "tool_name". Previously, every registration attempt failed with
      "Missing tool_name".
    * Removed unused native imports to keep the module self-consistent
      with the API actually exercised at runtime.

CONFIGURATION (defaults can be overridden by environment variables or
              passed to get_instance())
    LINGOFUSE_ENDPOINT              - LingoFuse endpoint (default: ipc:agent)
    LINGOFUSE_TIMEOUT_MS            - Call timeout in ms (default: 5000)
    LINGOFUSE_REG_AGENT_APP         - Local app name used by this client
                                      (default: reg_agent)
    LINGOFUSE_TOOL_PROVIDER_APP     - Backend app that provides tools
                                      (default: agent_main_app)
    LINGOFUSE_AGENT_MAIN_API        - API to fetch tool list
                                      (default: agent_main)
    LINGOFUSE_AGENT_LOG_API         - API to send logs (default: agent_log)
    LINGOFUSE_REGISTER_AGENT_API    - API for dynamic registration
                                      (default: register_agent)

USAGE EXAMPLE (Python)
    from language_middleware import LanguageMiddleware, set_debug_mode

    mw = LanguageMiddleware.get_instance(endpoint='ipc:custom')

    tools = mw.get_tools()
    for t in tools:
        print(t['name'], t['description'])

    result = mw.call_tool('add', {'a': 5, 'b': 7})

    mw.log('Hello from Python')

    set_debug_mode(True)

NOTES
    - Connection is lazy: the middleware does not connect on
      instantiation. It attempts to connect on the first call to
      get_tools(), call_tool() or log().
    - If the backend is unavailable, the middleware logs errors and
      returns empty tool lists or None for log(), but does not raise
      exceptions except when explicitly calling call_tool() (which
      will raise LanguageCallError if the tool is not found or the
      call fails).
    - Dynamic tool registration via `register_agent` does NOT modify
      the local tool cache (`self._tools`). The tool list is entirely
      sourced from the backend's `agent_main` response. This ensures
      consistency and avoids stale entries.

DEPENDENCIES
    - lingofuse package (must be installed or in PYTHONPATH), which
      now provides lingofuse.lf_io for unified DataHandle I/O
    - Python 3.7+

AUTHOR
    PassByYou888 / LingoFuse Team
"""

import json
import os
import sys
import threading
import atexit
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional

# ============================================================================
# Configuration (using LINGOFUSE_ prefix)
# ============================================================================
DEFAULT_ENDPOINT = os.environ.get("LINGOFUSE_ENDPOINT", "ipc:agent")
DEFAULT_TIMEOUT_MS = int(os.environ.get("LINGOFUSE_TIMEOUT_MS", "5000"))
DEFAULT_REG_AGENT_APP_NAME = os.environ.get("LINGOFUSE_REG_AGENT_APP", "reg_agent")
DEFAULT_TOOL_PROVIDER_APP = os.environ.get("LINGOFUSE_TOOL_PROVIDER_APP", "agent_main_app")
DEFAULT_AGENT_MAIN_API = os.environ.get("LINGOFUSE_AGENT_MAIN_API", "agent_main")
DEFAULT_AGENT_LOG_API = os.environ.get("LINGOFUSE_AGENT_LOG_API", "agent_log")
DEFAULT_REGISTER_AGENT_API = os.environ.get("LINGOFUSE_REGISTER_AGENT_API", "register_agent")

# ========================================================================
# Add lingofuse package path
# ========================================================================
_current_dir = os.path.dirname(os.path.abspath(__file__))
if _current_dir not in sys.path:
    sys.path.insert(0, _current_dir)

# ========================================================================
# Low-level LingoFuse imports
# ========================================================================
#
# Only the functions that this module calls directly at the C ABI level
# are imported here. All byte-level payload I/O (writing/reading JSON,
# NUL handling) is delegated to lingofuse.lf_io, which owns the
# remaining LF_* functions.
#
# LF_SetPos is imported for a single local helper, `_rewind`, that
# resets the read position of a freshly received response handle to 0
# before reading it. This is a position-control operation, not payload
# I/O, so it lives here rather than in lf_io.
from lingofuse._lf_native import (
    DataHnd,
    AppHnd,
    LF_CreateData,
    LF_FreeData,
    LF_CreateApp,
    LF_FreeApp,
    LF_RegisterCall,
    LF_Call,
    LF_SetOption,
    LF_SetPos,
    LF_ResetPrepare,
    LF_PrepareClient,
    LF_PrepareDone,
    LF_ExitMainThread,
    LF_Shutdown,
    LF_CheckMainThread,
    LFCallFunc,
)

# ========================================================================
# Unified LingoFuse DataHandle I/O
# ========================================================================
#
# All JSON and string reads/writes on a LingoFuse DataHandle go through
# lingofuse.lf_io. This module guarantees:
#   * ensure_ascii=False  -> no \uXXXX escapes on the wire
#   * NUL termination     -> matches Pascal's LF_ReadString
#   * NUL-tolerant reads  -> accepts raw JSON from HTTP bridges
#   * explicit NUL on c_char_p LF_* parameters
#
# Every LF payload read in this file goes through read_json or
# read_json_or_bytes; every LF payload write goes through write_json.
# The only direct `json.dumps` call in the module is the DEBUG-only
# pretty print in _fetch_tools_from_backend, which is intentionally
# scoped to stderr diagnostics (see the comment at that call site).
from lingofuse.lf_io import (
    cstr,
    read_json,
    read_json_or_bytes,
    write_json,
)

# ============================================================================
# Debug mode and log buffer (global)
# ============================================================================
DEBUG_MODE = False
_LOG_BUFFER = deque(maxlen=100)
_LOG_LOCK = threading.Lock()


def set_debug_mode(mode: bool):
    """Enable or disable verbose debug output."""
    global DEBUG_MODE
    DEBUG_MODE = mode
    sys.stderr.write(f"[DEBUG] Mode switched to {'ON' if mode else 'OFF'}\n")
    sys.stderr.flush()


def get_logs() -> List[str]:
    """Return a copy of the internal debug log buffer."""
    with _LOG_LOCK:
        return list(_LOG_BUFFER)


def clear_logs():
    """Clear the internal debug log buffer."""
    with _LOG_LOCK:
        _LOG_BUFFER.clear()


def _log_entry(entry: str):
    """Append one entry to the internal debug log buffer."""
    with _LOG_LOCK:
        _LOG_BUFFER.append(entry)


# ============================================================================
# Position helper
# ============================================================================
def _rewind(hnd: DataHnd) -> None:
    """
    Reset the read/write position of a DataHandle to the start of its
    buffer.

    A freshly returned handle from LF_Call is normally already at
    position 0, but the callback contract for LF_RegisterCall does not
    guarantee the input handle's position. Rewinding before reading
    makes the read deterministic in both cases.
    """
    LF_SetPos(hnd, 0)


# ============================================================================
# Exceptions
# ============================================================================
class LanguageMiddlewareError(Exception):
    """Base exception for all middleware-related errors."""
    pass


class LanguageConnectionError(LanguageMiddlewareError):
    """Raised when the middleware cannot connect to the backend."""
    pass


class LanguageCallError(LanguageMiddlewareError):
    """Raised when a tool invocation fails."""
    pass


# ============================================================================
# Callback for register_agent (only used if the API is enabled)
# ============================================================================
_mw_instance = None


@LFCallFunc
def _reg_tool_callback(trigger: DataHnd, inp: DataHnd, out: DataHnd):
    """
    Callback for the `register_agent` API.

    Input JSON (matching Pascal's `do_register_agent`):
        {
            "name":        "tool_name",
            "description": "...",
            "target_app":  "backend_app",
            "target_api":  "backend_api",
            "parameters":  { ... JSON Schema ... }
        }

    The middleware deliberately does NOT modify its local tool cache
    here. The authoritative tool list is always fetched from
    `agent_main`. This callback only logs the event.

    All payload I/O goes through lingofuse.lf_io: read_json() for the
    request and write_json() for the response. Both enforce
    ensure_ascii=False and NUL framing.

    Exception isolation
    -------------------
    No exception is allowed to escape into the C stack. Every failure
    path writes a JSON error response to `out` if possible; if writing
    that response itself fails, the error is logged to stderr and the
    callback returns.
    """
    global _mw_instance
    if _mw_instance is None:
        sys.stderr.write("[reg_tool] Middleware instance not available\n")
        sys.stderr.flush()
        return

    try:
        # Rewind to the start of the input buffer. The callback contract
        # does not guarantee the position, so reset it before reading.
        _rewind(inp)
        req = read_json(inp)
        if not isinstance(req, dict):
            raise ValueError("Request must be a JSON object")

        # Field names must match the Pascal producer (do_register_agent).
        name = req.get('name')
        if not name:
            raise ValueError('Missing "name" field')

        description = req.get('description', '')

        target_app = req.get('target_app')
        if not target_app:
            raise ValueError('Missing "target_app" field')

        target_api = req.get('target_api')
        if not target_api:
            raise ValueError('Missing "target_api" field')

        # Log the registration event. Do NOT touch self._tools here.
        _mw_instance._register_tool(name, description, target_app, target_api)

        write_json(out, {
            "status": "ok",
            "message": f"Tool '{name}' registered successfully",
        })
        sys.stderr.write(
            f"[reg_tool] Registered tool: {name} -> {target_app}.{target_api}\n"
        )
        sys.stderr.flush()

    except Exception as e:
        # Best-effort error response. If writing the error response
        # itself fails, log to stderr and continue; the caller sees an
        # empty output handle, which it treats as an error.
        try:
            write_json(out, {"status": "error", "message": str(e)})
        except Exception as write_err:
            sys.stderr.write(
                f"[reg_tool] Failed to write error response: {write_err}\n"
            )
        sys.stderr.write(f"[reg_tool] Error: {e}\n")
        sys.stderr.flush()


# ============================================================================
# Core singleton class
# ============================================================================
class LanguageMiddleware:
    """
    Singleton class that manages the LingoFuse connection and tool
    registry.
    """

    _instance = None
    _lock = threading.Lock()
    _initialized = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self,
                 endpoint: str = DEFAULT_ENDPOINT,
                 timeout_ms: int = DEFAULT_TIMEOUT_MS,
                 reg_agent_app_name: str = DEFAULT_REG_AGENT_APP_NAME,
                 tool_provider_app: str = DEFAULT_TOOL_PROVIDER_APP,
                 agent_main_api: str = DEFAULT_AGENT_MAIN_API,
                 agent_log_api: str = DEFAULT_AGENT_LOG_API,
                 register_agent_api: str = DEFAULT_REGISTER_AGENT_API):
        """
        Initialize the middleware with the given parameters.
        The connection is not established until first use.
        """
        if LanguageMiddleware._initialized:
            self._update_config(endpoint, timeout_ms, reg_agent_app_name,
                                tool_provider_app, agent_main_api,
                                agent_log_api, register_agent_api)
            return
        with LanguageMiddleware._lock:
            if LanguageMiddleware._initialized:
                return
            self._endpoint = endpoint
            self._timeout_ms = timeout_ms
            self._reg_agent_app_name = reg_agent_app_name
            self._tool_provider_app = tool_provider_app
            self._agent_main_api = agent_main_api
            self._agent_log_api = agent_log_api
            self._register_agent_api = register_agent_api
            self._app_hnd = None
            self._tools: Dict[str, Dict] = {}
            self._shutdown_hook_registered = False
            self._started = False
            self._connection_attempted = False
            self._connect_lock = threading.Lock()

            # Global LingoFuse options. cstr() supplies NUL-terminated
            # UTF-8 bytes for the c_char_p parameters.
            LF_SetOption(cstr("Wait_Connection_ReadyOk"), cstr("True"))
            LF_SetOption(cstr("Wait_TimeOut"), cstr(str(timeout_ms)))

            # Local app that hosts register_agent.
            self._app_hnd = LF_CreateApp(
                cstr(self._reg_agent_app_name),
                cstr("Registration Agent for tool discovery"),
            )
            if not self._app_hnd:
                raise LanguageConnectionError("Failed to create App")

            if self._register_agent_api:
                ret = LF_RegisterCall(
                    self._app_hnd,
                    cstr(self._register_agent_api),
                    cstr("Register a new agent tool"),
                    None,
                    _reg_tool_callback,
                )
                if ret != 1:
                    sys.stderr.write(
                        "[LanguageMiddleware] Warning: Failed to register "
                        "reg_tool; registration API will not work.\n"
                    )
                else:
                    sys.stderr.write("[LanguageMiddleware] Registered reg_tool API.\n")

            global _mw_instance
            _mw_instance = self

            self._started = False
            self._connection_attempted = False

            if not self._shutdown_hook_registered:
                atexit.register(self._cleanup)
                self._shutdown_hook_registered = True

            LanguageMiddleware._initialized = True
            sys.stderr.write("[LanguageMiddleware] Initialized (lazy connection mode).\n")
            sys.stderr.flush()

    def _update_config(self, endpoint, timeout_ms, reg_agent_app_name,
                       tool_provider_app, agent_main_api,
                       agent_log_api, register_agent_api):
        """
        Update configuration and force reconnect if already started.

        {!!!!!  LIFETIME  !!!!!}
        This method calls `_disconnect()` (which stops the LingoFuse
        main thread) but deliberately does NOT release the App handle
        or call LF_Shutdown. The App is owned by the singleton and is
        released exactly once, in `_cleanup()`, at interpreter exit.
        This keeps the App valid across reconnects.
        """
        if endpoint is not None:
            self._endpoint = endpoint
        if timeout_ms is not None:
            self._timeout_ms = timeout_ms
        if reg_agent_app_name is not None:
            self._reg_agent_app_name = reg_agent_app_name
        if tool_provider_app is not None:
            self._tool_provider_app = tool_provider_app
        if agent_main_api is not None:
            self._agent_main_api = agent_main_api
        if agent_log_api is not None:
            self._agent_log_api = agent_log_api
        if register_agent_api is not None:
            self._register_agent_api = register_agent_api
        if self._started:
            self._disconnect()
        self._started = False
        self._connection_attempted = False
        sys.stderr.write("[LanguageMiddleware] Configuration updated.\n")
        sys.stderr.flush()

    def _connect(self) -> bool:
        """Establish a connection to the LingoFuse endpoint and fetch tools."""
        with self._connect_lock:
            if self._started:
                return True
            if self._connection_attempted:
                return False
            self._connection_attempted = True
            try:
                sys.stderr.write(
                    f"[LanguageMiddleware] Connecting to {self._endpoint}...\n"
                )
                LF_ResetPrepare()
                LF_PrepareClient(cstr(self._endpoint), self._app_hnd)

                if LF_PrepareDone() != 1:
                    raise LanguageConnectionError(
                        f"LF_PrepareDone failed for {self._endpoint}"
                    )

                self._started = True
                sys.stderr.write(
                    f"[LanguageMiddleware] Connected to {self._endpoint}\n"
                )
                self._fetch_tools_from_backend()
                return True
            except Exception as e:
                sys.stderr.write(f"[LanguageMiddleware] Connection failed: {e}\n")
                self._started = False
                return False

    def _disconnect(self):
        """
        Stop the LingoFuse main thread and reset connection state.

        {!!!!!  DO NOT CALL LF_Shutdown HERE  !!!!!}
        The App handle held by this middleware (`self._app_hnd`) must
        remain valid across a disconnect/reconnect cycle. The
        `_update_config()` path calls `_disconnect()` and may later
        trigger a fresh `_connect()`, which reuses the same App handle
        in `LF_PrepareClient`.

        Calling `LF_Shutdown()` here would destroy the underlying
        TLF_App object, and the subsequent `LF_PrepareClient()` would
        receive a dangling handle - undefined behaviour.

        The library is unloaded exactly once, in `_cleanup()`, after
        the App has been released. This mirrors the semantics of
        `Server.stop(full_cleanup=False)` in lingofuse/server.py.
        """
        if self._started:
            try:
                LF_ExitMainThread()
                sys.stderr.write("[LanguageMiddleware] Disconnected.\n")
            except Exception as e:
                sys.stderr.write(
                    f"[LanguageMiddleware] Error during disconnect: {e}\n"
                )
            self._started = False
            self._connection_attempted = False

    def _ensure_connected(self) -> bool:
        """Ensure a connection exists; if not, attempt to connect."""
        if self._started:
            return True
        return self._connect()

    def _fetch_tools_from_backend(self):
        """
        Call the backend's agent_main API to retrieve the list of tools.
        Populates the internal _tools dictionary.

        All payload I/O goes through lingofuse.lf_io. The request
        handle and the response handle are released in finally blocks
        so that an exception during payload I/O cannot leak a handle.

        Response shape validation (N1 / N2)
        -----------------------------------
        The response is expected to be a JSON object whose "tools" key
        is a list of tool descriptors. Two defensive checks are applied
        because the backend is external and can misbehave:

          * N1: if the top-level response is not a JSON object, the
            tool list is cleared and the method returns. Previously a
            non-object response (a bare list, string, or number) would
            raise AttributeError on `data.get(...)` and abort the
            caller's connection attempt. A non-object response is now
            treated as a soft failure, matching the behaviour for a
            transport-level error.

          * N2: individual entries in the tools array that are not JSON
            objects are skipped with a warning. A malformed entry (for
            example a bare string) previously raised AttributeError on
            `t.get(...)`; it is now ignored and the rest of the list
            remains usable.

        Both checks preserve the "a bad backend cannot abort the
        connection attempt" invariant.
        """
        try:
            req = LF_CreateData(cstr(self._agent_main_api))
            if not req:
                sys.stderr.write(
                    "[LanguageMiddleware] Failed to get tool info: "
                    "could not create request handle\n"
                )
                return
            try:
                resp = LF_Call(
                    cstr(self._tool_provider_app), req, self._timeout_ms
                )
            finally:
                LF_FreeData(req)

            if not resp:
                sys.stderr.write(
                    "[LanguageMiddleware] Failed to get tool info: no response\n"
                )
                return

            try:
                _rewind(resp)
                data = read_json(resp)
            finally:
                LF_FreeData(resp)

            if data is None:
                sys.stderr.write(
                    "[LanguageMiddleware] Failed to get tool info: empty response\n"
                )
                return

            # N1: the response must be a JSON object. A non-object
            # response is a soft failure: clear the cache and return.
            if not isinstance(data, dict):
                sys.stderr.write(
                    "[LanguageMiddleware] Failed to get tool info: "
                    f"response is {type(data).__name__}, not a JSON object\n"
                )
                self._tools.clear()
                return

            if DEBUG_MODE:
                # Pretty-print the received tool info for human
                # inspection. This is a diagnostic path only; it goes
                # to stderr and never touches a LingoFuse DataHandle.
                #
                # Policy alignment (A2):
                #   The two policy-critical flags (ensure_ascii=False to
                #   avoid \uXXXX escapes, default=str as a safety net)
                #   match lingofuse.lf_io.dumps_json exactly. The only
                #   intentional deviation is indent=2, kept for human
                #   readability in the debug stream. The DEBUG output is
                #   not a wire payload and is not subject to the LF JSON
                #   contract, so the deviation is safe.
                #
                #   Errors inside this diagnostic block must never
                #   affect the tool list; the whole block is wrapped in
                #   a try/except so that a non-serializable value
                #   cannot abort tool discovery.
                try:
                    formatted = json.dumps(
                        data,
                        indent=2,
                        ensure_ascii=False,
                        default=str,
                    )
                    sys.stderr.write(
                        f"\n[LanguageMiddleware] Received tool info JSON:\n{formatted}\n\n"
                    )
                except Exception as dump_err:
                    sys.stderr.write(
                        "[LanguageMiddleware] Failed to pretty-print "
                        f"tool info for debug: {dump_err}\n"
                    )

            tools = data.get('tools', [])
            if not isinstance(tools, list):
                # A "tools" key that is present but not an array is a
                # malformed response. Treat it as an empty list rather
                # than iterating over an arbitrary value.
                sys.stderr.write(
                    "[LanguageMiddleware] Failed to get tool info: "
                    f"'tools' is {type(tools).__name__}, not an array\n"
                )
                self._tools.clear()
                return

            # Replace the entire tool dictionary with the fresh list.
            self._tools.clear()
            for t in tools:
                # N2: skip non-object entries instead of raising.
                if not isinstance(t, dict):
                    sys.stderr.write(
                        "[LanguageMiddleware] Skipping malformed tool "
                        f"entry of type {type(t).__name__}\n"
                    )
                    continue
                name = t.get('name')
                if not name:
                    continue
                self._tools[name] = {
                    'name': name,
                    'description': t.get('description', ''),
                    'target_app': t.get('target_app', self._tool_provider_app),
                    'target_api': t.get('target_api', name),
                    'parameters': t.get('parameters', {}),
                }

            sys.stderr.write(
                f"\n[LanguageMiddleware] Retrieved {len(self._tools)} tools from backend:\n"
            )
            for name, info in self._tools.items():
                sys.stderr.write(
                    f"  - {name}: {info['description']} "
                    f"-> {info['target_app']}.{info['target_api']}\n"
                )
            sys.stderr.write("\n")
            sys.stderr.flush()
        except Exception as e:
            sys.stderr.write(
                f"\n[LanguageMiddleware] Exception while fetching tools: {e}\n"
            )
            sys.stderr.flush()

    def _register_tool(self, tool_name: str, description: str,
                       target_app: str, target_api: str):
        """
        Record a tool registration event. This method does NOT modify
        the local tool cache. It only logs the event. The cache is
        exclusively updated by _fetch_tools_from_backend().
        """
        sys.stderr.write(
            f"[LanguageMiddleware] Tool registered (not cached): "
            f"{tool_name} -> {target_app}.{target_api}\n"
        )
        sys.stderr.flush()

    def _cleanup(self):
        """
        Release all LingoFuse resources held by this middleware.

        {!!!!!  ORDERING IS CRITICAL  !!!!!}
        The steps must run in this order:

            1. LF_ExitMainThread()
                   Stop the simulated main thread. Safe to call even
                   if the main thread was never started.

            2. LF_FreeApp(self._app_hnd)
                   Release the App handle created in __init__. This
                   MUST run BEFORE LF_Shutdown, because LF_Shutdown
                   destroys every object in the global pool and would
                   leave LF_FreeApp operating on a dangling pointer.
                   This matches the P0-2 fix in test_lingofuse.py and
                   Server.stop(full_cleanup=True) in
                   lingofuse/server.py.

            3. LF_Shutdown()
                   Unload the library. Safe to call even if the main
                   thread was never started, and safe to call multiple
                   times.

        Each step has its own try/except so that a failure in one step
        does not prevent the remaining steps from running. This is
        important for step 3: if step 1 raised and skipped step 3, the
        library would remain loaded and its resources would leak when
        the process exits.

        Idempotent: `LanguageMiddleware._initialized` is reset to
        False at the end, and the method is safe to call more than
        once (for example via atexit plus an explicit shutdown()).
        """
        # ---- Step 1: stop the main thread ----
        if self._started:
            try:
                LF_ExitMainThread()
                self._started = False
            except Exception as e:
                sys.stderr.write(
                    f"[LanguageMiddleware] Exception during LF_ExitMainThread: {e}\n"
                )
                sys.stderr.flush()

        # ---- Step 2: release the App (BEFORE LF_Shutdown) ----
        if self._app_hnd:
            try:
                LF_FreeApp(self._app_hnd)
            except Exception as e:
                sys.stderr.write(
                    f"[LanguageMiddleware] Exception during LF_FreeApp: {e}\n"
                )
                sys.stderr.flush()
            self._app_hnd = None

        # ---- Step 3: unload the library ----
        try:
            LF_Shutdown()
            sys.stderr.write("[LanguageMiddleware] Resources cleaned up.\n")
            sys.stderr.flush()
        except Exception as e:
            sys.stderr.write(
                f"[LanguageMiddleware] Exception during LF_Shutdown: {e}\n"
            )
            sys.stderr.flush()

        LanguageMiddleware._initialized = False

    def shutdown(self):
        """Explicitly shut down the middleware and release resources."""
        self._cleanup()

    def get_tools(self) -> List[Dict[str, Any]]:
        """
        Return the list of available tools.

        If not connected, attempts to connect and fetch tools.
        Returns an empty list if the connection fails.
        """
        if not self._ensure_connected():
            sys.stderr.write(
                "[LanguageMiddleware] Not connected, returning empty tool list.\n"
            )
            return []
        return list(self._tools.values())

    def log(self, message: str) -> Optional[Dict[str, Any]]:
        """
        Send a log message to the backend via agent_log.

        Returns the backend's JSON response (dict) on success, None on
        failure.

        All payload I/O goes through lingofuse.lf_io. The request and
        response handles are released in finally blocks.
        """
        if not self._ensure_connected():
            sys.stderr.write("[log] Not connected, log message dropped.\n")
            return None
        try:
            req = LF_CreateData(cstr(self._agent_log_api))
            if not req:
                sys.stderr.write("[log] Failed to create request handle.\n")
                return None
            try:
                write_json(req, {"message": message})
                resp = LF_Call(
                    cstr(self._tool_provider_app), req, self._timeout_ms
                )
            finally:
                LF_FreeData(req)

            if not resp:
                sys.stderr.write("[log] No response from backend.\n")
                return None

            try:
                _rewind(resp)
                return read_json(resp)
            finally:
                LF_FreeData(resp)
        except Exception as e:
            sys.stderr.write(f"[log] Failed to send log: {e}\n")
            sys.stderr.flush()
            return None

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """
        Invoke a tool by name with the given arguments.

        Raises:
            LanguageConnectionError if not connected.
            LanguageCallError if the tool is not registered or the call
            fails.
        Returns the parsed response (usually a dict) from the backend.

        All payload I/O goes through lingofuse.lf_io. The request and
        response handles are released in finally blocks. A backend
        response that is not valid JSON is returned as raw bytes,
        matching the historical behaviour of this method.
        """
        if not self._ensure_connected():
            raise LanguageConnectionError("Not connected to backend")

        tool = self._tools.get(tool_name)
        if not tool:
            error_msg = f"Tool '{tool_name}' not registered"
            sys.stderr.write(f"[call_tool] {error_msg}\n")
            raise LanguageCallError(error_msg)

        target_app = tool['target_app']
        target_api = tool['target_api']

        # Create the request handle. cstr() supplies the NUL-terminated
        # UTF-8 bytes expected by LF_CreateData's c_char_p parameter.
        req = LF_CreateData(cstr(target_api))
        if not req:
            error_msg = f"Failed to create request handle for tool '{tool_name}'"
            sys.stderr.write(f"[call_tool] {error_msg}\n")
            raise LanguageCallError(error_msg)

        try:
            # write_json() guarantees ensure_ascii=False (no \uXXXX
            # escapes) and appends the NUL terminator required by the
            # Pascal-side LF_ReadString.
            write_json(req, arguments)
            resp = LF_Call(cstr(target_app), req, self._timeout_ms)
        finally:
            LF_FreeData(req)

        if not resp:
            error_msg = f"LF_Call returned null handle for tool '{tool_name}'"
            sys.stderr.write(f"[call_tool] {error_msg}\n")
            if DEBUG_MODE:
                _log_entry(f"[ERROR] {tool_name}: {error_msg}")
            raise LanguageCallError(error_msg)

        try:
            _rewind(resp)
            result = read_json_or_bytes(resp)
        finally:
            LF_FreeData(resp)

        if DEBUG_MODE:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            entry = (
                f"[{timestamp}] {tool_name}\n"
                f"  args: {arguments!r}\n"
                f"  result: {result!r}"
            )
            _log_entry(entry)
            sys.stderr.write(
                f"[call_tool] {tool_name} called, response logged.\n"
            )

        return result

    def is_connected(self) -> bool:
        """Return True if the middleware is currently connected."""
        if not self._started:
            return False
        try:
            return LF_CheckMainThread() != 0
        except Exception:
            return False

    def reconnect(self):
        """
        Force a reconnection attempt, discarding the current connection.

        This calls `_disconnect()` (which stops the main thread but
        keeps the App handle valid) and then `_connect()`. The App
        handle is reused, so no allocation happens here.
        """
        self._disconnect()
        return self._connect()

    @classmethod
    def get_instance(cls,
                     endpoint: str = None,
                     timeout_ms: int = None,
                     reg_agent_app_name: str = None,
                     tool_provider_app: str = None,
                     agent_main_api: str = None,
                     agent_log_api: str = None,
                     register_agent_api: str = None) -> "LanguageMiddleware":
        """
        Get the singleton instance, optionally updating its
        configuration. Any parameter provided will override the current
        default value.
        """
        instance = cls.__new__(cls)
        if not LanguageMiddleware._initialized:
            instance.__init__(
                endpoint=endpoint or DEFAULT_ENDPOINT,
                timeout_ms=timeout_ms or DEFAULT_TIMEOUT_MS,
                reg_agent_app_name=reg_agent_app_name or DEFAULT_REG_AGENT_APP_NAME,
                tool_provider_app=tool_provider_app or DEFAULT_TOOL_PROVIDER_APP,
                agent_main_api=agent_main_api or DEFAULT_AGENT_MAIN_API,
                agent_log_api=agent_log_api or DEFAULT_AGENT_LOG_API,
                register_agent_api=register_agent_api or DEFAULT_REGISTER_AGENT_API,
            )
        else:
            if (endpoint is not None or timeout_ms is not None or
                    reg_agent_app_name is not None or tool_provider_app is not None or
                    agent_main_api is not None or agent_log_api is not None or
                    register_agent_api is not None):
                instance._update_config(
                    endpoint, timeout_ms, reg_agent_app_name,
                    tool_provider_app, agent_main_api,
                    agent_log_api, register_agent_api,
                )
        return instance


_default_middleware = None


def get_default_middleware() -> LanguageMiddleware:
    """Get the default global middleware instance."""
    global _default_middleware
    if _default_middleware is None:
        _default_middleware = LanguageMiddleware.get_instance()
    return _default_middleware


if __name__ == "__main__":
    print("=== LanguageMiddleware Self-test (v7.7, lazy connect) ===")
    try:
        mw = LanguageMiddleware.get_instance()
        mw._ensure_connected()
        print(f"Connected: {mw.is_connected()}")
        tools = mw.get_tools()
        print(f"Registered tools: {len(tools)}")
        for t in tools:
            print(f"  - {t['name']}: {t.get('description', '')} "
                  f"-> {t.get('target_app')}.{t.get('target_api')}")
            print(f"    parameters: {t.get('parameters', {})}")
        print("\nSelf-test passed. Press Enter to exit...")
        input()
    except Exception as e:
        print(f"\nSelf-test failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if '_default_middleware' in globals() and _default_middleware is not None:
            _default_middleware.shutdown()