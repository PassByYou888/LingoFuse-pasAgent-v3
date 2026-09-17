#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LingoFuse LLM Test Client - interactive multi-session REPL (v3.7).

Matches the v3.0 server protocol:

  Streaming messages received on the notify API:
      {"type": "chunk",  "session_id": "...", "text": "..."}
      {"type": "think",  "session_id": "...", "text": "..."}
      {"type": "finish", "session_id": "...", "reason": "stop|..."}
      {"type": "error",  "session_id": "...", "message": "..."}
      {"type": "closed", "session_id": "...", "reason": "client|timeout|shutdown"}

  Call APIs:
      generate(content, prompt?, client_name?, session_id?, options?,
               attachments?)
          -> {code, session_id, task_id, mode}
      create_session(client_name, system_message?)  -> {code, session_id}
      close_session(session_id, cancel_running?)    -> {code, status}
      cancel_session(session_id)                    -> {code, status}
      list_sessions(client_name?)                   -> {code, sessions}
      set_system_message(content)                   -> {code, status}
      get_api_capabilities()                        -> {code, server_kind,
                                                        capabilities: {...}}
      health()                                      -> {code, status, ...}

SERVER CAPABILITY DISCOVERY
---------------------------
Two sibling server kinds may be running on the same endpoint:
  - llm_service  : local inference, supports set_system_message
  - llm_proxy    : stateless forwarder to an OpenAI-compatible backend,
                   does NOT support set_system_message
  - llm_proxy_tool : proxy with server-side tool execution (LTB)

This client discovers the running server's API capability matrix by
calling `get_api_capabilities`, which returns a dictionary of the form:

    {"generate": 1, "create_session": 1, "set_system_message": 0, ...}

where 1 = supported and 0 = not supported. The matrix is fetched once
after connection and cached in STATE.api_capabilities. The convenience
function `llm_supported(api_name)` returns True/False for a single API
name and will re-fetch the matrix on a cache miss.

Commands that depend on optional APIs (for example /sys which relies on
set_system_message) consult `llm_supported` before invoking the server,
so the user gets a friendly hint with an alternative workflow instead of
a raw "unsupported" error from the backend.

ATTACHMENTS (v3.7)
------------------
A one-shot invocation may attach text files and image files to the
single generate request:

    python llm_test.py --content "review this code" --text main.py
    python llm_test.py --content "describe this chart" --image a.png
    python llm_test.py --text a.py --text b.py --image chart.png

The client reads each file, wraps it into the standard `attachments`
array, and sends it alongside the `content`. Text files are decoded as
UTF-8 with GBK and Latin-1 fallbacks; image files are base64-encoded.
Client-side size limits match the server's exactly, so an oversized
attachment fails fast with a clear message rather than a server error.

Attachment support is a one-shot mode feature only. The interactive
REPL does not expose an /attach command; use the command-line flags
for one-shot invocations that need attachments.

CRITICAL DESIGN NOTE
--------------------
`client_name` in every Call above MUST be the real LingoFuse application
name that this process registered with `LF_BindApp`. Only that app is
reachable by the server's `LF_Sequenced_Notify`. Using an arbitrary
human-readable string will result in silent delivery failures with the
server printing `no found app(...)` for every streamed token.

In this client, the app name is generated automatically by
`generate_app_name()` after `LF_PrepareDone()` and stored as
`STATE.client_name`. All session creation calls use that exact value.

Thinking stream
---------------
Reasoning models emit reasoning_content separately from the final
content. The server forwards reasoning_content as `think` events and
content as `chunk` events. This client renders think text in a dim ANSI
style (when the console supports it), so the user can watch the model
reason in real time instead of waiting in silence before the final
answer starts.

Console / emoji support
-----------------------
_setup_console_encoding() runs before any output and:
  - switches the Windows console code page to UTF-8 (65001),
  - enables virtual terminal processing for ANSI colours,
  - reconfigures Python stdout/stderr/stdin to UTF-8 with
    errors='replace'.

Rendering emoji at full colour still requires a terminal that supports
Unicode glyphs (Windows Terminal, VSCode integrated terminal, or a
PowerShell window with a font that includes emoji).

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
import json
import time
import base64
import argparse
import threading
from typing import Any, Dict, List, Optional


# ----------------------------------------------------------------------
# Frozen-executable detection and help-text invocation helpers
#
# The client can be launched in two ways:
#   1. From source:    python llm_test.py [OPTIONS]
#   2. As a frozen exe: llm_test.exe [OPTIONS]
#
# The `--help` output adapts its usage line and examples accordingly so
# the user always sees the correct command for the current packaging.
# ----------------------------------------------------------------------
def is_frozen_exe() -> bool:
    """
    Return True if this process is running from a frozen executable.

    Detects PyInstaller (one-file or one-dir) and Nuitka by checking
    both `sys.frozen` and `sys._MEIPASS`. This mirrors the detection
    used by llm_service.py, llm_proxy.py, mcp_api_tool.py and mcp_api_proxy.py
    in the same project.
    """
    return getattr(sys, 'frozen', False) or hasattr(sys, '_MEIPASS')


def get_invocation_name() -> str:
    """
    Return the program name shown at the top of `--help` (the `prog=`
    value).

    - Frozen exe: the exe filename, e.g. "llm_test.exe".
    - Script:     the script filename, e.g. "llm_test.py".
    """
    if is_frozen_exe():
        return os.path.basename(sys.executable)
    return os.path.basename(os.path.abspath(__file__))


def get_example_invocation() -> str:
    """
    Return the full command prefix used in the `--help` examples.

    - Frozen exe: "llm_test.exe"
    - Script:     "python llm_test.py"
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

    Steps:
      1. Switch the Windows console code page to UTF-8 (65001) for
         both input and output.
      2. Enable virtual terminal processing so ANSI escapes (dim,
         bold, colours) are interpreted rather than printed.
      3. Reconfigure Python stdout/stderr/stdin to UTF-8 with
         errors='replace'.
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

from lingofuse import App, generate_app_name, set_option, check_main_thread
from lingofuse.core import DataHandle
from lingofuse._lf_native import (
    LF_ResetPrepare, LF_PrepareClient, LF_PrepareDone,
    LF_Call, LF_ExitMainThread, LF_Shutdown,
    LF_BindApp,
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
# server-side limits defined in llm_common/attachments.py. Matching
# the values here lets us reject an oversized attachment immediately,
# with a message that names the file and the actual size, instead of
# forwarding it to the server and letting the server reject it with a
# generic error.
MAX_TEXT_BYTES = 256 * 1024          # 256 KB per text file
MAX_IMAGE_B64_BYTES = 8 * 1024 * 1024  # 8 MB base64 per image file

# MIME guess table for common text file extensions. The server does
# not enforce a MIME whitelist on text attachments, so this table is
# purely informational: it appears in the `<attachment name="...">`
# wrapper and in the client's own log line. Extensions not listed fall
# back to text/plain.
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

# MIME guess table for image file extensions. The server DOES enforce
# a whitelist on image attachments, so an unrecognized extension here
# will be rejected by the server with a clear error. We still attempt
# the upload so that the user sees the server's message.
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
    current packaging: when running from source they show
    "python llm_test.py ...", when running as a frozen exe they show
    "llm_test.exe ...".
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
        "One-shot mode with attachments (v3.7):\n"
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
        "Server capability discovery:\n"
        "  Two sibling servers may be running on the same endpoint:\n"
        "    llm_service - supports set_system_message\n"
        "    llm_proxy   - does NOT support set_system_message\n"
        "  The client calls get_api_capabilities after connecting to\n"
        "  learn which APIs the running server supports. Commands that\n"
        "  depend on optional APIs (e.g. /sys) consult the capability\n"
        "  matrix and provide a friendly fallback when unsupported.\n"
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
            "(v3.7)"
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

    # ---- One-shot mode attachments (v3.7) ----
    parser.add_argument(
        "--text",
        action="append",
        default=[],
        metavar="PATH",
        help="One-shot mode: attach a text file (repeatable). The file "
             "is decoded as UTF-8, GBK, or Latin-1 and merged into the "
             "user message. See also --image.",
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
# Attachment loading (v3.7)
# ----------------------------------------------------------------------
def _read_text_file(path: str) -> str:
    """
    Read a text file and return its content as a Python string.

    The decoder tries UTF-8 first (the modern default), then GBK
    (still common on Windows in Chinese environments), then Latin-1
    (which never fails because every byte sequence is valid Latin-1).
    The returned string is what the server will embed into the user
    message inside an <attachment> block.

    Raises:
        FileNotFoundError: if the path does not exist.
        IsADirectoryError: if the path is a directory.
        OSError: on any other read failure.
        ValueError: if the file exceeds MAX_TEXT_BYTES.
    """
    # os.path.getsize is a cheap stat; using it before read() lets us
    # reject an oversized file without pulling it into memory.
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
    # latin-1 never fails, so this line is unreachable. It exists
    # only to satisfy the type checker.
    return raw.decode("latin-1", errors="replace")


def _read_image_file(path: str) -> str:
    """
    Read an image file and return its content as a base64 string.

    The result is what the server will embed into a data: URL of the
    form "data:image/png;base64,<result>". The base64 alphabet has no
    line breaks in the standard (non-MIME) variant, which is what the
    OpenAI image_url part expects.

    Raises:
        FileNotFoundError: if the path does not exist.
        IsADirectoryError: if the path is a directory.
        OSError: on any other read failure.
        ValueError: if the base64 representation would exceed
            MAX_IMAGE_B64_BYTES.
    """
    size = os.path.getsize(path)
    # base64 expands by 4/3 plus padding; add 4 bytes for the padding
    # so the check matches the server's limit exactly.
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

    The server does not enforce a MIME whitelist for text attachments,
    so an unrecognized text extension falls back to text/plain and is
    accepted. Image attachments DO have a server-side whitelist, so an
    unrecognized image extension maps to image/png and will be
    rejected by the server with a clear error message naming the
    extension and the allowed set.
    """
    ext = os.path.splitext(path)[1].lower()
    table = _IMAGE_MIME_BY_EXT if is_image else _TEXT_MIME_BY_EXT
    if is_image:
        return table.get(ext, "image/png")
    return table.get(ext, "text/plain")


def _build_attachments(args: argparse.Namespace) -> List[Dict[str, Any]]:
    """
    Build the `attachments` array from --text / --image arguments.

    Order is: all text files first (in the order they appear on the
    command line), then all image files. This keeps the prompt layout
    predictable: text always precedes images in the assembled user
    message.

    Returns:
        A list of dicts ready to be placed in the generate payload's
        "attachments" field. Empty list if no --text / --image flags
        were given.

    Raises:
        FileNotFoundError / IsADirectoryError / OSError / ValueError:
            propagated so that run_once() can print a precise error
            and exit with a non-zero status.
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
    Return a short human-readable summary of an attachments array for
    the client's own log line. Not sent to the server.
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
        # Real LingoFuse app name (set once after PrepareDone). This is
        # the only client_name we ever send to the server.
        self.client_name: Optional[str] = None
        self.closed_sessions: List[str] = []
        self.lock = threading.Lock()
        # Cached API capability matrix returned by the server's
        # get_api_capabilities Call API. Keys are API names, values are
        # integers: 1 = supported, 0 = not supported.
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
    Returns None on transport or parse error.
    """
    hnd = None
    res_hnd = None
    try:
        hnd = DataHandle(api_name)
        hnd.write_json(request_json)
        res_hnd = LF_Call(server_app.encode("utf-8"), hnd.raw, timeout_ms)
    finally:
        if hnd is not None:
            hnd.free()

    if not res_hnd:
        print(f"[Client] ERROR: Call to {api_name} returned a null handle",
              file=sys.stderr)
        return None

    resp = DataHandle._from_raw(res_hnd, owned=True)
    try:
        return resp.read_json()
    except Exception as e:
        print(f"[Client] ERROR: Failed to parse response "
              f"from {api_name}: {e}", file=sys.stderr)
        return None
    finally:
        resp.free()


# ----------------------------------------------------------------------
# Server capability discovery
# ----------------------------------------------------------------------
def fetch_capabilities(state: ClientState, server_app: str,
                       timeout_ms: int) -> bool:
    """
    Call the server's `get_api_capabilities` API and cache the result.

    The response has the shape:

        {
          "code": 0,
          "server_kind": "service" | "proxy",
          "capabilities": {
              "<api_name>": 0 | 1,
              ...
          }
        }

    On success, STATE.api_capabilities and STATE.server_kind are
    updated. Returns True on success, False on transport or parse
    failure.

    Compatibility note: older servers (before the capability API was
    introduced) will return an error. In that case this function
    returns False and STATE.api_capabilities remains empty; callers
    treat an empty cache as "unknown" and fall back to unconditional
    invocation.

    Normalization: each capability value is coerced to int 0 or 1.
    Values that cannot be coerced (including OverflowError from
    int(float('inf'))) are treated as 0 (unsupported), which is the
    conservative choice. A malformed matrix therefore disables the
    optional features rather than enabling them by accident.
    """
    resp = remote_call(server_app, "get_api_capabilities", {}, timeout_ms)
    if resp is None or resp.get("code") != 0:
        return False
    caps = resp.get("capabilities") or {}
    if not isinstance(caps, dict):
        return False
    # Normalize values to ints so that later comparisons are cheap and
    # unambiguous.
    normalized: Dict[str, int] = {}
    for k, v in caps.items():
        try:
            normalized[str(k)] = int(v)
        except (TypeError, ValueError, ArithmeticError):
            # ArithmeticError covers OverflowError, which int() raises
            # for float('inf') and float('nan'). Such a value is not a
            # legitimate capability flag; treat it as unsupported.
            normalized[str(k)] = 0

    # server_kind must be a string or absent. Anything else (a dict, a
    # list, an int) would confuse the display code; normalize to None.
    raw_kind = resp.get("server_kind")
    state.server_kind = raw_kind if isinstance(raw_kind, str) else None

    state.api_capabilities = normalized
    return True


def llm_supported(state: ClientState, api_name: str,
                  server_app: str, timeout_ms: int) -> bool:
    """
    Return True if the running server supports the given API name.

    Uses the cached capability matrix when possible. On a cache miss
    (for example the first call before the matrix has been fetched,
    or an API name that the server did not list), the matrix is
    re-fetched once. If the re-fetch fails or still does not contain
    the API, the function returns False.

    A missing entry is treated as unsupported. This is the
    conservative choice: do not invoke what the server has not
    explicitly advertised. It matches the semantics of the server-side
    capability dictionary, where only entries with value 1 are
    supported.
    """
    if api_name in state.api_capabilities:
        return state.api_capabilities[api_name] == 1

    # Cache miss: attempt one re-fetch, then look up again.
    if fetch_capabilities(state, server_app, timeout_ms):
        return state.api_capabilities.get(api_name, 0) == 1

    # Could not fetch (old server, transport failure, ...). Be
    # conservative and report unsupported.
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
    """
    try:
        if inp.get_size() == 0:
            return
        data = inp.read_json()
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
    if not state.client_name:
        print("[Client] Internal error: STATE.client_name is not set",
              file=sys.stderr)
        return

    payload: Dict[str, Any] = {"client_name": state.client_name}
    if system_message:
        payload["system_message"] = system_message

    resp = remote_call(server_app, "create_session", payload, timeout_ms)
    if resp is None or resp.get("code") != 0:
        print(f"[Client] Failed to create session: {resp}", file=sys.stderr)
        return
    state.current_session_id = resp["session_id"]
    print(f"[Client] Created session {state.current_session_id} "
          f"(client_name={state.client_name})")


def cmd_use(state: ClientState, session_id: Optional[str]) -> None:
    if not session_id:
        print("[Client] Usage: /use <session_id>")
        return
    state.current_session_id = session_id
    print(f"[Client] Now using session {session_id}")


def cmd_sessions(state: ClientState, server_app: str, timeout_ms: int) -> None:
    payload: Dict[str, Any] = {}
    if state.client_name:
        payload["client_name"] = state.client_name
    resp = remote_call(server_app, "list_sessions", payload, timeout_ms)
    if resp is None or resp.get("code") != 0:
        print(f"[Client] Failed to list sessions: {resp}", file=sys.stderr)
        return
    sessions = resp.get("sessions", [])
    if not sessions:
        print("[Client] No sessions on the server for this client.")
        return
    print(f"[Client] {len(sessions)} session(s):")
    for s in sessions:
        marker = " *" if s["session_id"] == state.current_session_id else "  "
        print(f"  {marker} {s['session_id']}  "
              f"status={s['status']}  "
              f"messages={s['message_count']}")


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
    on llm_service. On llm_proxy (stateless forwarder) the API is not
    supported. The capability matrix is consulted first so that the
    user receives a clear, actionable hint instead of a raw
    "unsupported" error from the backend.
    """
    if not message:
        print("[Client] Usage: /sys <message>")
        return

    if not llm_supported(state, "set_system_message", server_app, timeout_ms):
        print("[Client] This server does not support set_system_message.")
        if state.server_kind:
            print(f"[Client]   Detected server kind: {state.server_kind}")
        print("[Client]   Recommended workflow for llm_proxy:")
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
    # The api_capabilities field is a nested dict; flatten it into a
    # single line so the output stays readable.
    for k, v in resp.items():
        if k == "api_capabilities" and isinstance(v, dict):
            supported = [n for n, flag in v.items() if flag == 1]
            unsupported = [n for n, flag in v.items() if flag == 0]
            print(f"    api_capabilities (supported)  : {', '.join(supported)}")
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

    `attachments` is an optional list of dicts already built by
    _build_attachments(). When None or empty, the payload is
    byte-for-byte identical to the pre-v3.7 request, so the
    interactive REPL and any script that does not use attachments are
    unaffected by the new field.
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
        state.current_session_id = resp.get("session_id",
                                            state.current_session_id)
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
        payload: Dict[str, Any] = {"client_name": STATE.client_name}
        if args.system_message:
            payload["system_message"] = args.system_message
        resp = remote_call(server_app, "create_session", payload, timeout_ms)
        if resp is None or resp.get("code") != 0:
            print(f"[Client] Failed to create session: {resp}",
                  file=sys.stderr)
            return 1
        session_id = resp["session_id"]
        print(f"[Client] Created session {session_id}")

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
        # FileNotFoundError / IsADirectoryError / PermissionError are
        # subclasses of OSError. ValueError covers the size checks.
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

    ret = LF_PrepareClient(args.endpoint.encode("utf-8"), None)
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

    print("[Client] Network connection established.")

    client_name = generate_app_name()
    STATE.client_name = client_name
    print(f"[Client] Client app name: {client_name}")

    app = App(client_name, "Interactive multi-session LLM client")
    app.register_notify(args.notify_api, on_llm_stream)

    bound = LF_BindApp(app.raw)
    if bound == 0:
        print("[Client] WARNING: LF_BindApp returned 0; the client may "
              "not be reachable by the server.", file=sys.stderr)
    else:
        print(f"[Client] Bound to {bound} client(s).")

    # ---- Discover the server's API capability matrix (best-effort) ----
    #
    # This is an optional step: if the running server predates the
    # capability API, fetch_capabilities returns False and the cache
    # stays empty. Commands that consult llm_supported will then fall
    # back to unconditional invocation, preserving backward
    # compatibility.
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
        # A pure-attachment invocation (no --content) is legitimate:
        # the user may simply want to hand a file to the model and let
        # it decide what to do. The requirement that at least one of
        # content / text / image is present is enforced in run_once().
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
        try:
            app.free()
        except Exception:
            pass
        LF_ExitMainThread()
        LF_Shutdown()
        print("[Client] Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())