#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LingoFuse LLM Test Client - interactive multi-session REPL (v4.0).

Matches the v4.0 server protocol (UID-addressed streaming):

  Streaming messages received on the notify API:
      {"type": "chunk",  "session_id": "...", "text": "..."}
      {"type": "think",  "session_id": "...", "text": "..."}
      {"type": "finish", "session_id": "...", "reason": "stop|..."}
      {"type": "error",  "session_id": "...", "message": "..."}
      {"type": "closed", "session_id": "...", "reason": "client|timeout|shutdown"}

  Call APIs:
      generate(content, prompt?, session_id?, options?, attachments?)
          -> {code, session_id, task_id, mode}
      create_session(system_message?)          -> {code, session_id, client_uid_hex}
      close_session(session_id, cancel_running?) -> {code, status}
      cancel_session(session_id)               -> {code, status}
      list_sessions()                          -> {code, sessions, count}
      set_system_message(content)              -> {code, status}
      get_api_capabilities()                   -> {code, server_kind,
                                                         capabilities}
      health()                                 -> {code, status, ...}

CHANGELOG (v4.0)
    * UID-addressed streaming protocol.
      The client no longer generates an app name, no longer creates an
      App, and no longer calls LF_BindApp. Instead, it registers the
      notify API directly into the process-wide P2P namespace via
      lingofuse.register_p2p_notify().

      The LingoFuse framework injects the client's 32-byte session UID
      into the header of every outbound request, so the server learns
      the UID from the Input DataHandle of the client's first
      create_session call. All stream events are then routed back via
      LF_UID_Sequenced_Notify using that UID.

      The `client_name` field is GONE from every request payload.
      The server ignores it if a legacy client sends it.

    * `STATE.client_name` and all its uses are removed.
    * The startup banner shows the P2P registration instead of a
      generated app name.
    * `cmd_new` and `run_once` no longer send `client_name` in the
      payload.
    * `cmd_sessions` no longer sends a `client_name` filter. The
      server implicitly filters by the caller's UID, so the client
      only sees its own sessions.
    * `/sessions` displays `client_uid_hex` (prefix) instead of
      `client_name`.
    * Shutdown no longer calls `app.free()`; only
      `LF_ExitMainThread` + `LF_Shutdown`.

CHANGELOG (v3.10)
    * JSON handling unification (T1 / T2 / T3 / T4):
      - T1: remote_call guards the entire request phase and the entire
        response phase with try/except blocks. Any failure is caught
        and converted into a `None` return value.
      - T2: cmd_new and run_once use resp.get("session_id") with an
        explicit empty check.
      - T3: cmd_sessions uses s.get(...) with safe defaults.
      - T4: cmd_health converts every capability name to str.

CHANGELOG (v3.9)
    * Import path follow-up: lf_io was relocated from llm_common into
      the lingofuse package.

CHANGELOG (v3.8)
    * All JSON and string I/O on LingoFuse DataHandles is delegated to
      lingofuse.lf_io.

SERVER CAPABILITY DISCOVERY
---------------------------
Two sibling server kinds may be running on the same endpoint:
  - llm_service     : local inference, supports set_system_message
  - llm_proxy       : stateless forwarder, does NOT support
                      set_system_message
  - llm_proxy_tool  : proxy with server-side tool execution (LTB)

This client discovers the running server's API capability matrix by
calling `get_api_capabilities`. The matrix is fetched once after
connection and cached in STATE.api_capabilities. The convenience
function `llm_supported(api_name)` returns True/False for a single API
name and will re-fetch the matrix on a cache miss.

Commands that depend on optional APIs (for example /sys which relies on
set_system_message) consult `llm_supported` before invoking the server.

ATTACHMENTS
-----------
A one-shot invocation may attach text files and image files to the
single generate request:

    python llm_test.py --content "review this code" --text main.py
    python llm_test.py --content "describe this chart" --image a.png

Client-side size limits match the server's exactly, so an oversized
attachment fails fast with a clear message.

CRITICAL DESIGN NOTE
--------------------
The `client_name` field is NO LONGER used. The server learns the
client's routing UID from the Input handle of the first call. The
client does not need to (and cannot) tell the server its UID.

Thinking stream
---------------
Reasoning models emit reasoning_content separately from the final
content. The server forwards reasoning_content as `think` events and
content as `chunk` events. This client renders think text in a dim
ANSI style (when the console supports it).

Console / emoji support
-----------------------
_setup_console_encoding() runs before any output and:
  - switches the Windows console code page to UTF-8 (65001),
  - enables virtual terminal processing for ANSI colours,
  - reconfigures Python stdout/stderr/stdin to UTF-8.

Usage
-----
Interactive REPL (default):
    python llm_test.py

One-shot mode:
    python llm_test.py --content "print('hi')" --prompt "Explain"
    python llm_test.py --content "review" --text main.py
    python llm_test.py --content "describe" --image chart.png

Interactive commands
--------------------
    /new                Create a new session
    /use <session_id>   Switch the current session
    /sessions           List sessions belonging to this client
    /close [id]         Close a session (default: current)
    /cancel             Cancel the currently running generation
    /sys <message>      Update the global default system message
    /health             Query the server health/status
    /capabilities       Show the server's API capability matrix
    /capabilities refresh  Force re-fetch the capability matrix
    /thinking on|off    Toggle thinking mode for subsequent turns
    /help               Show this help
    /quit, /exit        Quit

    Anything else is sent to the current session as user input.

All comments and logs are in English.
"""

import os
import sys
import time
import base64
import argparse
import threading
from typing import Any, Dict, List, Optional


# ----------------------------------------------------------------------
# Frozen-executable detection and help-text invocation helpers
# ----------------------------------------------------------------------
def is_frozen_exe() -> bool:
    """
    Return True if this process is running from a frozen executable.

    Detects PyInstaller (one-file or one-dir) and Nuitka by checking
    both `sys.frozen` and `sys._MEIPASS`.
    """
    return getattr(sys, 'frozen', False) or hasattr(sys, '_MEIPASS')


def get_invocation_name() -> str:
    """
    Return the program name shown at the top of `--help`.
    """
    if is_frozen_exe():
        return os.path.basename(sys.executable)
    return os.path.basename(os.path.abspath(__file__))


def get_example_invocation() -> str:
    """
    Return the full command prefix used in the `--help` examples.
    """
    if is_frozen_exe():
        return os.path.basename(sys.executable)
    return f"{os.path.basename(sys.executable)} {os.path.basename(os.path.abspath(__file__))}"


# ----------------------------------------------------------------------
# Console preparation - MUST run before any non-ASCII output.
# ----------------------------------------------------------------------
def _setup_console_encoding() -> None:
    """
    Prepare the console for UTF-8 output and emoji rendering.

    Windows consoles default to a legacy code page (CP936 / GBK on
    Chinese systems) that cannot represent emoji. Without preparation,
    Python raises UnicodeEncodeError or silently substitutes '?' when
    the server sends an emoji in a chunk.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32

            # STD_OUTPUT_HANDLE = -11, STD_ERROR_HANDLE = -12
            # ENABLE_PROCESSED_OUTPUT            = 0x0001
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            for handle_id in (-11, -12):
                h = kernel32.GetStdHandle(handle_id)
                if not h or h == -1:
                    continue
                mode = ctypes.c_uint32()
                if kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                    kernel32.SetConsoleMode(
                        h, mode.value | 0x0001 | 0x0004)

            kernel32.SetConsoleOutputCP(65001)
            kernel32.SetConsoleCP(65001)
        except Exception:
            pass

    for stream_name in ("stdout", "stderr", "stdin"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_setup_console_encoding()


# ----------------------------------------------------------------------
# LingoFuse imports
# ----------------------------------------------------------------------
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".."))

from lingofuse import (
    set_option,
    check_main_thread,
    register_p2p_notify,
)
from lingofuse.core import DataHandle
from lingofuse._lf_native import (
    LF_ResetPrepare, LF_PrepareClient, LF_PrepareDone,
    LF_Call, LF_ExitMainThread, LF_Shutdown,
    LF_SetPos, LF_FreeData,
)

# ----------------------------------------------------------------------
# Unified LingoFuse DataHandle I/O
# ----------------------------------------------------------------------
from lingofuse.lf_io import (
    cstr,
    read_json,
    write_json,
)


# ----------------------------------------------------------------------
# Defaults
# ----------------------------------------------------------------------
DEFAULT_ENDPOINT = "ipc:llm_service"
DEFAULT_SERVER_APP = "LLM_Service"
DEFAULT_NOTIFY_API = "llm_stream"
DEFAULT_TIMEOUT_MS = 30000

# ANSI dim style used for the thinking stream.
THINK_COLOR = "\033[2m"
RESET_COLOR = "\033[0m"

# Client-side attachment limits. These MUST stay in sync with the
# server-side limits defined in llm_common/attachments.py.
MAX_TEXT_BYTES = 256 * 1024
MAX_IMAGE_B64_BYTES = 8 * 1024 * 1024

# MIME guess table for common text file extensions.
_TEXT_MIME_BY_EXT = {
    ".txt":  "text/plain",
    ".md":   "text/markdown",
    ".json": "application/json",
    ".py":   "text/x-python",
    ".c":    "text/x-c",
    ".h":    "text/x-c",
    ".cpp":  "text/x-c++",
    ".pas":  "text/x-pascal",
    ".log":  "text/plain",
    ".csv":  "text/csv",
    ".xml":  "text/xml",
    ".yaml": "text/yaml",
    ".yml":  "text/yaml",
}

# MIME guess table for image file extensions.
_IMAGE_MIME_BY_EXT = {
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


# ----------------------------------------------------------------------
# Argument parsing
# ----------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    """
    Parse command-line arguments.

    The usage line and the examples section of `--help` adapt to the
    current packaging.
    """
    invocation = get_example_invocation()

    epilog = (
        "Interactive REPL (default):\n"
        f"  {invocation}\n"
        "\n"
        "One-shot mode (send one prompt and exit):\n"
        f"  {invocation} --content \"print('hi')\" --prompt \"Explain\"\n"
        f"  {invocation} --content \"hello\" --thinking\n"
        f"  {invocation} --content \"hello\" --keep\n"
        "\n"
        "One-shot mode with attachments:\n"
        f"  {invocation} --content \"review this code\" --text main.py\n"
        f"  {invocation} --content \"describe this chart\" --image a.png\n"
        f"  {invocation} --text a.py --text b.py --image chart.png\n"
        "\n"
        "Reuse an existing session (server-side session id):\n"
        f"  {invocation} --session-id <SESSION_ID> --content \"continue\"\n"
        "\n"
        "Interactive commands (once running):\n"
        "  /new                     Create a new session\n"
        "  /use <session_id>        Switch the current session\n"
        "  /sessions                List sessions belonging to this client\n"
        "  /close [id]              Close a session (default: current)\n"
        "  /cancel                  Cancel the current generation\n"
        "  /sys <message>           Update the global default system message\n"
        "  /health                  Query server health/status\n"
        "  /capabilities            Show server API capability matrix\n"
        "  /capabilities refresh    Force re-fetch the capability matrix\n"
        "  /thinking on|off         Toggle thinking mode\n"
        "  /help                    Show this help\n"
        "  /quit, /exit             Quit\n"
        "\n"
        "Routing model (v4.0 - UID-only):\n"
        "  The client registers its notify API ('llm_stream') into the\n"
        "  process-wide P2P namespace. It does NOT create an app and\n"
        "  does NOT call LF_BindApp. The LingoFuse framework injects\n"
        "  the client's 32-byte session UID into the header of every\n"
        "  outbound request, so the server learns the UID from the\n"
        "  Input handle of the first create_session call. All stream\n"
        "  events are routed back with LF_UID_Sequenced_Notify.\n"
        "\n"
        "Environment variables (read once at startup):\n"
        "  LINGOFUSE_ENDPOINT   - LingoFuse endpoint (default: ipc:llm_service)\n"
        "  LLM_SERVER_APP       - Server application name (default: LLM_Service)\n"
        "  LLM_NOTIFY_API       - Notify API name (default: llm_stream)\n"
        "  LINGOFUSE_TIMEOUT    - Call timeout in ms (default: 30000)\n"
        "  LLM_DEBUG            - Set to 1/true/yes to enable debug output\n"
    )

    parser = argparse.ArgumentParser(
        prog=get_invocation_name(),
        description=(
            "LingoFuse LLM Test Client - interactive multi-session REPL "
            "(v4.0, UID-addressed)"
        ),
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # ---- Connection / protocol ----
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("LINGOFUSE_ENDPOINT", DEFAULT_ENDPOINT),
        metavar="ADDRESS",
        help=f"LingoFuse endpoint (IPC or TCP). Default: {DEFAULT_ENDPOINT}",
    )
    parser.add_argument(
        "--server-app",
        default=os.environ.get("LLM_SERVER_APP", DEFAULT_SERVER_APP),
        metavar="NAME",
        help=f"Server application name. Default: {DEFAULT_SERVER_APP}",
    )
    parser.add_argument(
        "--notify-api",
        default=os.environ.get("LLM_NOTIFY_API", DEFAULT_NOTIFY_API),
        metavar="NAME",
        help=f"Notify API name for streaming chunks. "
             f"Default: {DEFAULT_NOTIFY_API}",
    )
    parser.add_argument(
        "--timeout", type=int,
        default=int(os.environ.get("LINGOFUSE_TIMEOUT", DEFAULT_TIMEOUT_MS)),
        metavar="MS",
        help=f"Call timeout in milliseconds. Default: {DEFAULT_TIMEOUT_MS}",
    )

    # ---- One-shot mode ----
    parser.add_argument(
        "--content",
        default=None,
        metavar="TEXT",
        help="One-shot mode: content to send. When set, the client runs "
             "a single turn and exits (no interactive REPL).",
    )
    parser.add_argument(
        "--prompt",
        default="",
        metavar="TEXT",
        help="One-shot mode: extra prompt appended after --content.",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        metavar="ID",
        help="One-shot mode: reuse an existing server-side session instead "
             "of creating a new one.",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="One-shot mode: keep the session alive after the turn "
             "finishes (default: close it).",
    )
    parser.add_argument(
        "--thinking",
        action="store_true",
        help="One-shot mode: enable thinking for this turn.",
    )

    # ---- One-shot mode attachments ----
    parser.add_argument(
        "--text",
        action="append",
        default=[],
        metavar="PATH",
        help="One-shot mode: attach a text file (repeatable). The file "
             "is decoded as UTF-8, GBK, or Latin-1 and merged into the "
             "user message.",
    )
    parser.add_argument(
        "--image",
        action="append",
        default=[],
        metavar="PATH",
        help="One-shot mode: attach an image file (repeatable). The "
             "file is base64-encoded and forwarded as an OpenAI "
             "image_url content part. Requires a server with "
             "vision=1.",
    )

    # ---- Session defaults ----
    parser.add_argument(
        "--system-message",
        default=None,
        metavar="TEXT",
        help="System message used when creating the first session.",
    )

    # ---- Logging ----
    parser.add_argument(
        "--debug",
        action="store_true",
        default=os.environ.get("LLM_DEBUG", "0").lower()
        in ("1", "true", "yes"),
        help="Print the effective configuration at startup.",
    )

    return parser.parse_args()


# ----------------------------------------------------------------------
# Attachment loading
# ----------------------------------------------------------------------
def _read_text_file(path: str) -> str:
    """
    Read a text file and return its content as a Python string.

    The decoder tries UTF-8 first, then GBK, then Latin-1.
    """
    size = os.path.getsize(path)
    if size > MAX_TEXT_BYTES:
        raise ValueError(
            f"text file '{path}' is {size} bytes, which exceeds the "
            f"per-file limit of {MAX_TEXT_BYTES} bytes"
        )

    with open(path, "rb") as f:
        raw = f.read()

    for encoding in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _read_image_file(path: str) -> str:
    """
    Read an image file and return its content as a base64 string.
    """
    size = os.path.getsize(path)
    b64_size = ((size + 2) // 3) * 4
    if b64_size > MAX_IMAGE_B64_BYTES:
        raise ValueError(
            f"image file '{path}' is {size} bytes, which encodes to "
            f"{b64_size} base64 characters, exceeding the per-file "
            f"limit of {MAX_IMAGE_B64_BYTES} base64 characters"
        )

    with open(path, "rb") as f:
        raw = f.read()

    return base64.b64encode(raw).decode("ascii")


def _guess_mime(path: str, is_image: bool) -> str:
    """
    Return a best-effort MIME type based on the file extension.
    """
    ext = os.path.splitext(path)[1].lower()
    table = _IMAGE_MIME_BY_EXT if is_image else _TEXT_MIME_BY_EXT
    if is_image:
        return table.get(ext, "image/png")
    return table.get(ext, "text/plain")


def _build_attachments(args: argparse.Namespace) -> List[Dict[str, Any]]:
    """
    Build the `attachments` array from --text / --image arguments.
    """
    attachments: List[Dict[str, Any]] = []

    for path in args.text:
        text = _read_text_file(path)
        attachments.append({
            "kind": "text",
            "name": os.path.basename(path),
            "mime": _guess_mime(path, is_image=False),
            "text": text,
        })

    for path in args.image:
        data_b64 = _read_image_file(path)
        attachments.append({
            "kind": "image",
            "name": os.path.basename(path),
            "mime": _guess_mime(path, is_image=True),
            "data_b64": data_b64,
        })

    return attachments


def _describe_attachments(
    attachments: List[Dict[str, Any]],
) -> str:
    """
    Return a short human-readable summary of an attachments array.
    """
    if not attachments:
        return "(none)"
    parts = []
    for a in attachments:
        kind = a.get("kind", "?")
        name = a.get("name", "unnamed")
        if kind == "text":
            size = len(a.get("text", ""))
            parts.append(f"text:{name}({size}B)")
        elif kind == "image":
            size = len(a.get("data_b64", ""))
            parts.append(f"image:{name}({size}b64)")
        else:
            parts.append(f"{kind}:{name}")
    return ", ".join(parts)


# ----------------------------------------------------------------------
# Client state
# ----------------------------------------------------------------------
class ClientState:
    """State shared between the REPL thread and the notify callback."""

    def __init__(self) -> None:
        self.finish_event = threading.Event()
        self.finish_reason: Optional[str] = None
        self.error_message: Optional[str] = None
        self.current_session_id: Optional[str] = None
        self.thinking_enabled: bool = False
        self.exiting: bool = False
        # NOTE: v4.0 has no `client_name`. The server learns the
        # client's UID from the Input handle of the first call.
        self.closed_sessions: List[str] = []
        self.lock = threading.Lock()
        # Cached API capability matrix.
        self.api_capabilities: Dict[str, int] = {}
        # Identifies which server kind is answering ("service" or
        # "proxy"). Populated alongside api_capabilities.
        self.server_kind: Optional[str] = None


STATE = ClientState()


# ----------------------------------------------------------------------
# Remote call helper
# ----------------------------------------------------------------------
def remote_call(server_app: str, api_name: str,
                request_json: Dict[str, Any],
                timeout_ms: int) -> Optional[Dict[str, Any]]:
    """
    Send a Call request and return the parsed JSON response.
    Returns None on any failure (serialization, transport, parse).

    Failure isolation (T1)
    ----------------------
    Every step of the round trip is guarded:
      * DataHandle creation, write_json serialization, and LF_Call are
        wrapped together.
      * DataHandle._from_raw is wrapped separately.
      * LF_SetPos and read_json are wrapped together.
    """
    hnd = None
    res_hnd = None

    # ---- Phase 1: request ----
    try:
        try:
            hnd = DataHandle(api_name)
            write_json(hnd.raw, request_json)
            res_hnd = LF_Call(cstr(server_app), hnd.raw, timeout_ms)
        except Exception as e:
            print(f"[Client] ERROR: request phase failed for "
                  f"{api_name}: {e}", file=sys.stderr)
            return None
    finally:
        if hnd is not None:
            try:
                hnd.free()
            except Exception:
                pass

    if not res_hnd:
        print(f"[Client] ERROR: Call to {api_name} returned a null handle",
              file=sys.stderr)
        return None

    # ---- Phase 2: wrap response handle ----
    try:
        resp = DataHandle._from_raw(res_hnd, owned=True)
    except Exception as e:
        print(f"[Client] ERROR: failed to wrap response handle for "
              f"{api_name}: {e}", file=sys.stderr)
        try:
            LF_FreeData(res_hnd)
        except Exception:
            pass
        return None

    # ---- Phase 3: read + parse response ----
    try:
        LF_SetPos(resp.raw, 0)
        return read_json(resp.raw)
    except Exception as e:
        print(f"[Client] ERROR: Failed to parse response "
              f"from {api_name}: {e}", file=sys.stderr)
        return None
    finally:
        try:
            resp.free()
        except Exception:
            pass


# ----------------------------------------------------------------------
# Server capability discovery
# ----------------------------------------------------------------------
def fetch_capabilities(state: ClientState, server_app: str,
                       timeout_ms: int) -> bool:
    """
    Call the server's `get_api_capabilities` API and cache the result.
    """
    resp = remote_call(server_app, "get_api_capabilities", {}, timeout_ms)
    if resp is None or resp.get("code") != 0:
        return False
    caps = resp.get("capabilities") or {}
    if not isinstance(caps, dict):
        return False
    normalized: Dict[str, int] = {}
    for k, v in caps.items():
        try:
            normalized[str(k)] = int(v)
        except (TypeError, ValueError, ArithmeticError):
            normalized[str(k)] = 0

    raw_kind = resp.get("server_kind")
    state.server_kind = raw_kind if isinstance(raw_kind, str) else None

    state.api_capabilities = normalized
    return True


def llm_supported(state: ClientState, api_name: str,
                  server_app: str, timeout_ms: int) -> bool:
    """
    Return True if the running server supports the given API name.
    """
    if api_name in state.api_capabilities:
        return state.api_capabilities[api_name] == 1

    if fetch_capabilities(state, server_app, timeout_ms):
        return state.api_capabilities.get(api_name, 0) == 1

    return False


# ----------------------------------------------------------------------
# Notify callback
# ----------------------------------------------------------------------
def on_llm_stream(trigger, inp: DataHandle) -> None:
    """
    Handle structured streaming messages from the server.

    chunk  -> print as normal text
    think  -> print in dim ANSI style
    finish -> signal completion
    error  -> record and print to stderr
    closed -> record session closure

    The entire body is wrapped in a try/except that sets
    STATE.finish_event on any exception, so a malformed payload cannot
    leave the REPL waiting forever.
    """
    try:
        if inp.get_size() == 0:
            return
        LF_SetPos(inp.raw, 0)
        data = read_json(inp.raw)
        if not isinstance(data, dict):
            return

        msg_type = data.get("type")
        session_id = data.get("session_id", "")

        if msg_type == "closed":
            with STATE.lock:
                STATE.closed_sessions.append(session_id)
            if session_id == STATE.current_session_id:
                print(f"\n[Client] Session {session_id} closed "
                      f"(reason={data.get('reason', 'unknown')})")
            return

        if msg_type == "chunk":
            text = data.get("text", "")
            if text:
                print(text, end="", flush=True)

        elif msg_type == "think":
            text = data.get("text", "")
            if text:
                print(f"{THINK_COLOR}{text}{RESET_COLOR}",
                      end="", flush=True)

        elif msg_type == "error":
            msg = data.get("message", "unknown error")
            STATE.error_message = msg
            print(f"\n[Client] Server error: {msg}", file=sys.stderr)

        elif msg_type == "finish":
            reason = data.get("reason", "unknown")
            STATE.finish_reason = reason
            print()  # newline after streaming text
            STATE.finish_event.set()

    except Exception as e:
        print(f"\n[Client] Callback exception: {e}", file=sys.stderr)
        STATE.finish_event.set()


# ----------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------
def cmd_new(state: ClientState, server_app: str, timeout_ms: int,
            system_message: Optional[str] = None) -> None:
    """
    Create a new session.

    The server learns the caller's UID from the Input handle. The
    payload therefore carries no client identity. The server returns
    the new session's `client_uid_hex` for the client's information.
    """
    payload: Dict[str, Any] = {}
    if system_message:
        payload["system_message"] = system_message

    resp = remote_call(server_app, "create_session", payload, timeout_ms)
    if resp is None or resp.get("code") != 0:
        print(f"[Client] Failed to create session: {resp}", file=sys.stderr)
        return

    new_sid = resp.get("session_id")
    if not new_sid or not isinstance(new_sid, str):
        print(f"[Client] Server did not return session_id: {resp}",
              file=sys.stderr)
        return

    state.current_session_id = new_sid
    uid_hex = resp.get("client_uid_hex", "?")
    print(f"[Client] Created session {new_sid} "
          f"(uid={uid_hex[:8]}...)")


def cmd_use(state: ClientState, session_id: Optional[str]) -> None:
    if not session_id:
        print("[Client] Usage: /use <session_id>")
        return
    state.current_session_id = session_id
    print(f"[Client] Now using session {session_id}")


def cmd_sessions(state: ClientState, server_app: str, timeout_ms: int) -> None:
    """
    List the sessions owned by this client.

    The server implicitly filters by the caller's UID, so the client
    only sees its own sessions. No filter is sent in the payload.
    """
    resp = remote_call(server_app, "list_sessions", {}, timeout_ms)
    if resp is None or resp.get("code") != 0:
        print(f"[Client] Failed to list sessions: {resp}", file=sys.stderr)
        return
    sessions = resp.get("sessions", [])
    if not isinstance(sessions, list):
        sessions = []
    if not sessions:
        print("[Client] No sessions on the server for this client.")
        return
    print(f"[Client] {len(sessions)} session(s):")
    for s in sessions:
        if not isinstance(s, dict):
            print(f"    (malformed session entry: {type(s).__name__})")
            continue
        sid = s.get("session_id", "?")
        status = s.get("status", "?")
        count = s.get("message_count", "?")
        uid_hex = s.get("client_uid_hex", "?")
        marker = " *" if sid == state.current_session_id else "  "
        print(f"  {marker} {sid}  "
              f"uid={uid_hex[:8]}  "
              f"status={status}  "
              f"messages={count}")


def cmd_close(state: ClientState, session_id: Optional[str],
              server_app: str, timeout_ms: int) -> None:
    sid = session_id or state.current_session_id
    if not sid:
        print("[Client] No session to close.")
        return
    resp = remote_call(server_app, "close_session",
                       {"session_id": sid, "cancel_running": True},
                       timeout_ms)
    if resp is None or resp.get("code") != 0:
        print(f"[Client] Failed to close session: {resp}", file=sys.stderr)
        return
    print(f"[Client] Session {sid} closed.")
    if sid == state.current_session_id:
        state.current_session_id = None


def cmd_cancel(state: ClientState, server_app: str, timeout_ms: int) -> None:
    if not state.current_session_id:
        print("[Client] No current session.")
        return
    resp = remote_call(server_app, "cancel_session",
                       {"session_id": state.current_session_id},
                       timeout_ms)
    if resp is None or resp.get("code") != 0:
        print(f"[Client] Cancel failed: {resp}", file=sys.stderr)
        return
    print(f"[Client] Cancel requested (status={resp.get('status')}).")


def cmd_sys(state: ClientState, message: str,
            server_app: str, timeout_ms: int) -> None:
    """
    Update the global default system message.

    This command relies on set_system_message, which is only available
    on llm_service. On llm_proxy / llm_proxy_tool the API is not
    supported. The capability matrix is consulted first.
    """
    if not message:
        print("[Client] Usage: /sys <message>")
        return

    if not llm_supported(state, "set_system_message", server_app, timeout_ms):
        print("[Client] This server does not support set_system_message.")
        if state.server_kind:
            print(f"[Client]   Detected server kind: {state.server_kind}")
        print("[Client]   Recommended workflow for proxy servers:")
        print("[Client]     1. /new                 "
              "(create a new session)")
        print("[Client]     2. Or launch llm_test with "
              "--system-message \"...\" --keep")
        print("[Client]   The system message is fixed at session "
              "creation time on this server.")
        return

    resp = remote_call(server_app, "set_system_message",
                       {"content": message}, timeout_ms)
    if resp is None or resp.get("code") != 0:
        print(f"[Client] Failed to set system message: {resp}",
              file=sys.stderr)
        return
    print("[Client] Global default system message updated "
          "(applies to NEW sessions).")


def cmd_health(state: ClientState, server_app: str, timeout_ms: int) -> None:
    resp = remote_call(server_app, "health", {}, timeout_ms)
    if resp is None:
        print("[Client] Health check failed.", file=sys.stderr)
        return
    print("[Client] Server health:")
    for k, v in resp.items():
        if k == "api_capabilities" and isinstance(v, dict):
            supported = [str(n) for n, flag in v.items() if flag == 1]
            unsupported = [str(n) for n, flag in v.items() if flag == 0]
            print(f"    api_capabilities (supported)  : "
                  f"{', '.join(supported)}")
            print(f"    api_capabilities (unsupported): "
                  f"{', '.join(unsupported) or '(none)'}")
        else:
            print(f"    {k}: {v}")


def cmd_capabilities(state: ClientState, arg: str,
                     server_app: str, timeout_ms: int) -> None:
    """
    Show, and optionally refresh, the server's API capability matrix.

    Usage:
        /capabilities          Use the cached matrix (fetch on miss).
        /capabilities refresh  Force a fresh fetch from the server.
    """
    force = arg.strip().lower() in ("refresh", "reload")

    if force or not state.api_capabilities:
        ok = fetch_capabilities(state, server_app, timeout_ms)
        if not ok:
            print("[Client] Failed to query get_api_capabilities.")
            print("[Client]   The server may be an older version that "
                  "does not expose this API.")
            return

    if not state.api_capabilities:
        print("[Client] Capability matrix is empty.")
        return

    kind = state.server_kind or "unknown"
    print(f"[Client] Server kind: {kind}")

    supported = sorted(
        k for k, v in state.api_capabilities.items() if v == 1
    )
    unsupported = sorted(
        k for k, v in state.api_capabilities.items() if v == 0
    )

    print(f"[Client] Supported APIs ({len(supported)}):")
    for name in supported:
        print(f"    [1] {name}")
    if unsupported:
        print(f"[Client] Unsupported APIs ({len(unsupported)}):")
        for name in unsupported:
            print(f"    [0] {name}")
    else:
        print("[Client] Unsupported APIs: (none)")


def cmd_thinking(state: ClientState, arg: str) -> None:
    arg = arg.strip().lower()
    if arg in ("on", "1", "true", "yes"):
        state.thinking_enabled = True
    elif arg in ("off", "0", "false", "no"):
        state.thinking_enabled = False
    else:
        print(f"[Client] Thinking mode is currently "
              f"{'ON' if state.thinking_enabled else 'OFF'}. "
              f"Usage: /thinking on|off")
        return
    print(f"[Client] Thinking mode set to "
          f"{'ON' if state.thinking_enabled else 'OFF'}")


def print_help() -> None:
    print("""Commands:
  /new                     Create a new session
  /use <session_id>        Switch the current session
  /sessions                List sessions belonging to this client
  /close [id]              Close a session (default: current)
  /cancel                  Cancel the current generation
  /sys <message>           Update the global default system message
  /health                  Query server health/status
  /capabilities            Show server API capability matrix
  /capabilities refresh    Force re-fetch the capability matrix
  /thinking on|off         Toggle thinking mode
  /help                    Show this help
  /quit, /exit             Quit
Anything else is sent to the current session as user input.""")


# ----------------------------------------------------------------------
# Send one turn and wait for completion
# ----------------------------------------------------------------------
def send_turn(state: ClientState, text: str,
              server_app: str, timeout_ms: int,
              attachments: Optional[List[Dict[str, Any]]] = None) -> None:
    """
    Send one generate request and block until the server emits a
    finish event.
    """
    if not state.current_session_id:
        print("[Client] No current session. Use /new to create one.")
        return

    state.finish_event.clear()
    state.finish_reason = None
    state.error_message = None

    payload: Dict[str, Any] = {
        "session_id": state.current_session_id,
        "content": text,
        "prompt": "",
        "options": {"thinking": state.thinking_enabled},
    }
    if attachments:
        payload["attachments"] = attachments

    resp = remote_call(server_app, "generate", payload, timeout_ms)
    if resp is None:
        print("[Client] Transport error while sending generate.",
              file=sys.stderr)
        return
    if resp.get("code") != 0:
        print(f"[Client] Server rejected request: {resp.get('error')}",
              file=sys.stderr)
        return

    task_id = resp.get("task_id")
    mode = resp.get("mode")
    if mode != "continue":
        state.current_session_id = resp.get(
            "session_id", state.current_session_id)
    print(f"[Client] Task {task_id} queued (mode={mode}), streaming...")
    print("-" * 60)

    try:
        while not state.finish_event.wait(timeout=0.5):
            pass
    except KeyboardInterrupt:
        print("\n[Client] Interrupted while waiting for the model. "
              "The server-side generation will continue; use /cancel to "
              "stop it.")
        return

    print("-" * 60)
    reason = state.finish_reason or "unknown"
    if state.error_message:
        print(f"[Client] Turn ended with error: {state.error_message}")
    else:
        print(f"[Client] Turn finished (reason={reason})")


# ----------------------------------------------------------------------
# One-shot mode
# ----------------------------------------------------------------------
def run_once(args: argparse.Namespace) -> int:
    server_app = args.server_app
    timeout_ms = args.timeout

    if args.session_id:
        session_id = args.session_id
    else:
        payload: Dict[str, Any] = {}
        if args.system_message:
            payload["system_message"] = args.system_message
        resp = remote_call(server_app, "create_session", payload, timeout_ms)
        if resp is None or resp.get("code") != 0:
            print(f"[Client] Failed to create session: {resp}",
                  file=sys.stderr)
            return 1

        new_sid = resp.get("session_id")
        if not new_sid or not isinstance(new_sid, str):
            print(f"[Client] Server did not return session_id: {resp}",
                  file=sys.stderr)
            return 1
        session_id = new_sid
        uid_hex = resp.get("client_uid_hex", "?")
        print(f"[Client] Created session {session_id} "
              f"(uid={uid_hex[:8]}...)")

    STATE.current_session_id = session_id
    STATE.thinking_enabled = args.thinking

    # ---- Build the text part of the user message ----
    text = args.content or ""
    if args.prompt:
        text = text + "\n\n" + args.prompt

    # ---- Build the attachments array (may be empty) ----
    try:
        attachments = _build_attachments(args)
    except (OSError, ValueError) as e:
        print(f"[Client] Failed to load attachments: {e}", file=sys.stderr)
        return 1

    # ---- Require at least one of text or attachments ----
    if not text and not attachments:
        print(
            "[Client] Nothing to send: provide --content, --text, "
            "or --image.",
            file=sys.stderr,
        )
        return 1

    if attachments:
        print(f"[Client] Attachments: {_describe_attachments(attachments)}")

    send_turn(STATE, text, server_app, timeout_ms,
              attachments=attachments or None)

    if not args.keep:
        cmd_close(STATE, session_id, server_app, timeout_ms)
    else:
        print(f"[Client] Session {session_id} kept alive.")
    return 0


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main() -> int:
    args = parse_args()

    if args.debug:
        print("[DEBUG] Configuration:", file=sys.stderr)
        for k, v in vars(args).items():
            print(f"  {k} = {v}", file=sys.stderr)

    print(f"[Client] Connecting to {args.endpoint} ...")
    LF_ResetPrepare()
    set_option("Wait_Connection_ReadyOk", "True")

    # ------------------------------------------------------------------
    # Register the notify API into the process-wide P2P namespace.
    #
    # v4.0 no longer creates a business App, so the notify API is
    # registered directly. The framework will bind the process-global
    # LF_P2P_APP to the network once the main thread starts, so the
    # notify callback is reachable by UID-addressed sends from the
    # server.
    #
    # Registration happens BEFORE LF_PrepareDone so that the API is
    # ready from the moment the client becomes visible on the network.
    # ------------------------------------------------------------------
    print(f"[Client] Registering P2P notify API: {args.notify_api}")
    register_p2p_notify(
        args.notify_api,
        on_llm_stream,
        "LLM stream callback (v4.0 UID-addressed client)",
    )

    ret = LF_PrepareClient(cstr(args.endpoint), None)
    if ret == -1:
        print(f"[Client] ERROR: LF_PrepareClient failed for "
              f"'{args.endpoint}'", file=sys.stderr)
        return 1

    if LF_PrepareDone() != 1:
        if check_main_thread():
            print("[Client] WARNING: LF_PrepareDone returned non-1, "
                  "but main thread is active. Continuing.")
        else:
            print("[Client] ERROR: LF_PrepareDone failed", file=sys.stderr)
            LF_ExitMainThread()
            LF_Shutdown()
            return 1

    print("[Client] Network connection established (P2P notify mode).")

    # ---- Discover the server's API capability matrix (best-effort) ----
    if fetch_capabilities(STATE, args.server_app, args.timeout):
        kind = STATE.server_kind or "unknown"
        supported = sum(1 for v in STATE.api_capabilities.values() if v == 1)
        unsupported = sum(1 for v in STATE.api_capabilities.values() if v == 0)
        print(f"[Client] Server kind: {kind} "
              f"(supported={supported}, unsupported={unsupported})")
    else:
        print("[Client] Note: server did not advertise "
              "get_api_capabilities; capability-gated commands will "
              "be attempted unconditionally.")

    try:
        # One-shot mode is entered when the user supplies any of:
        #   * --content <TEXT>
        #   * --text <PATH>     (one or more)
        #   * --image <PATH>    (one or more)
        if args.content is not None or args.text or args.image:
            rc = run_once(args)
            return rc

        print()
        print("Interactive mode. Type /help for commands, /quit to exit.")
        cmd_new(STATE, args.server_app, args.timeout,
                system_message=args.system_message)
        print()

        while not STATE.exiting:
            try:
                line = input("> ")
            except EOFError:
                break
            except KeyboardInterrupt:
                print()
                continue

            line = line.strip()
            if not line:
                continue

            if line.startswith("/"):
                parts = line[1:].split(None, 1)
                cmd = parts[0].lower() if parts else ""
                arg = parts[1] if len(parts) > 1 else ""

                if cmd in ("quit", "exit"):
                    STATE.exiting = True
                    break
                elif cmd == "new":
                    cmd_new(STATE, args.server_app, args.timeout)
                elif cmd == "use":
                    cmd_use(STATE, arg)
                elif cmd == "sessions" or cmd == "list":
                    cmd_sessions(STATE, args.server_app, args.timeout)
                elif cmd == "close":
                    cmd_close(STATE, arg or None,
                              args.server_app, args.timeout)
                elif cmd == "cancel":
                    cmd_cancel(STATE, args.server_app, args.timeout)
                elif cmd == "sys":
                    cmd_sys(STATE, arg, args.server_app, args.timeout)
                elif cmd == "health":
                    cmd_health(STATE, args.server_app, args.timeout)
                elif cmd == "capabilities" or cmd == "caps":
                    cmd_capabilities(STATE, arg,
                                     args.server_app, args.timeout)
                elif cmd == "thinking":
                    cmd_thinking(STATE, arg)
                elif cmd == "help":
                    print_help()
                else:
                    print(f"[Client] Unknown command: /{cmd}")
            else:
                send_turn(STATE, line, args.server_app, args.timeout)

    finally:
        print()
        print("[Client] Shutting down...")
        # v4.0: no App to free; P2P registration is torn down by
        # LF_Shutdown automatically.
        LF_ExitMainThread()
        LF_Shutdown()
        print("[Client] Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())