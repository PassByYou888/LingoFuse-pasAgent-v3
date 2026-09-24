#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
llm_proxy_tool.py - LingoFuse LLM Tool Bridge (LTB).

Role
----
A LingoFuse SERVICE that registers as LLM_Service on ipc:llm_service and
forwards each `generate` request to an OpenAI-compatible HTTP backend,
with OPTIONAL server-side tool execution via `language_middleware`.

Sibling of llm_proxy.py (pure text proxy). Differences:

    llm_proxy.py       - pure text passthrough, no tool execution
    llm_proxy_tool.py  - adds MCP tool discovery + server-side execution

Design principles
-----------------
1. Zero client changes.
   The client sends the same `generate` request as always. It does NOT
   need to know about tools, tool_calls, or tool_results. LTB handles
   everything internally and returns a normal chunk / think / finish
   stream.

2. Server-side tool execution.
   When the backend returns tool_calls, LTB executes them via
   language_middleware.call_tool() on the server. It then feeds the
   results back to the backend as role=tool messages and continues
   the conversation until the backend produces a final text answer.

3. Default: pure-text compatibility.
   If MCP tools are unavailable (no beacon, no tool provider, or
   --no-tools), LTB behaves exactly like llm_proxy.py: the payload
   sent to the backend is byte-for-byte identical.

4. Bounded multi-round protocol.
   A single `generate` call may trigger up to --max-tool-rounds
   backend round-trips. The last round is always a forced text-answer
   round with no tools injected, so the loop always terminates. A
   per-call cap on total tool calls and total tool-result characters
   adds a second layer of protection against runaway models.

5. Server-side pre-connection (race fix).
   Server.start() internally calls LF_PrepareDone(), which starts the
   LingoFuse simulated main thread. If language_middleware were to
   connect AFTER that, its own LF_PrepareDone() would return 0 (main
   thread already active) and _connect() would mark the connection as
   failed. LTB therefore pre-connects the middleware BEFORE
   Server.start().

6. Silent during normal operation.
   At INFO level and above, the service logs only startup, shutdown,
   session lifecycle, and errors. Every per-round and per-tool detail
   is logged at DEBUG level.

JSON handling (unified)
-----------------------
All JSON payloads produced by this module are serialized through
lingofuse.lf_io.dumps_json (ensure_ascii=False, default=str). No raw
json.dumps() call remains in this file. This guarantees that no
\\uXXXX escape ever appears on a LingoFuse wire or on an HTTP request
body.

Tool-call arguments and tool results are also part of the LF JSON
policy:

  * Tool arguments arrive from the backend as a JSON text string and
    are parsed with a unified-repair fallback: fast-path json.loads,
    then lingofuse.json_repair_preprocess.repair_json_text, then a
    safe empty-dict fallback. A malformed arguments string can never
    abort the round-trip.

  * Tool results are serialized with dumps_json. A defensive
    try/except around the serializer downgrades circular-reference
    objects to a repr string, so a rogue tool result cannot abort
    the generation.

Both policies are documented in the module docstring of
lingofuse.lf_io (see the JSON serialization policy section).

Attachment handling
-------------------
Text attachments are merged into the text part of the user message,
same as in llm_proxy.py. Image attachments are forwarded as OpenAI
multi-modal content parts, but ONLY on the first round. Subsequent
rounds see a short placeholder in history, so long tool chains do not
accumulate megabytes of base64.

Structured Output handling
--------------------------
options.response_format is whitelisted as a passthrough field, exactly
as in llm_proxy.py. The value is forwarded verbatim to the backend on
EVERY round of the tool chain. This gives the operator two options:

  * With tool execution enabled (default): the model is constrained to
    emit a schema-conforming reply on every round, including rounds
    where it would otherwise try to call a tool. Most tool-capable
    models will still emit tool_calls when the schema does not apply,
    but behaviour depends on the backend. If you see the model
    refusing to call tools while response_format is active, run the
    request with --no-tools, or use a schema that does not conflict
    with the tool-call intent.

  * With --no-tools: LTB behaves as a pure passthrough proxy for
    Structured Output, identical to llm_proxy.py.

The proxy does not currently clear response_format on the forced final
round; a future revision may inject a per-round override.

All comments and log messages are in English.
"""

import atexit
import json
import logging
import os
import sys
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from lingofuse import Server, set_option, check_app
from lingofuse.core import DataHandle
from lingofuse._lf_native import LF_Sequenced_Notify
from lingofuse.lf_io import (
    cstr,
    dumps_json,
    write_json,
)
from lingofuse.json_repair_preprocess import repair_json_text

from llm_common.attachments import (
    Attachment,
    AttachmentError,
    build_user_content,
    placeholder_for,
    validate_attachments,
)
from llm_common.banner import join_list, print_banner, redact
from llm_common.capabilities import (
    SERVER_KIND_PROXY,
    build_capabilities,
    build_response,
    split_supported,
)
from llm_common.headers import (
    load_key_from_file,
    parse_extra_headers,
)
from llm_common.logging_setup import setup_logging
from llm_common.option_sanitizer import (
    COMMON_SCALAR_SPECS,
    sanitize_options,
)
from llm_common.runtime import (
    get_example_invocation,
    get_invocation_name,
)
from llm_common.session_multimodal import MultimodalSessionState
from llm_common.sse_client import OpenAIStreamClient

# ---- Optional: language_middleware for MCP tool discovery ----
#
# If the module is missing (for example the project `src/` directory is
# not on PYTHONPATH), LTB silently falls back to pure-text proxy mode.
# It never crashes on a missing import.
try:
    from language_middleware import (
        LanguageMiddleware,
        LanguageConnectionError,
        LanguageCallError,
    )
    _HAS_MIDDLEWARE = True
except ImportError:
    _HAS_MIDDLEWARE = False
    LanguageMiddleware = None
    LanguageConnectionError = Exception
    LanguageCallError = Exception


# ----------------------------------------------------------------------
# Built-in defaults
# ----------------------------------------------------------------------

DEFAULT_ENDPOINT = "ipc:llm_service"
DEFAULT_APP_NAME = "LLM_Service"
DEFAULT_NOTIFY_API = "llm_stream"

DEFAULT_BACKEND_URL = "http://127.0.0.1:12345/v1"
DEFAULT_BACKEND_MODEL = ""
DEFAULT_BACKEND_KEY = "lm-studio"
DEFAULT_BACKEND_KEY_FILE = ""
DEFAULT_BACKEND_AUTH_HEADER = "Authorization"
DEFAULT_BACKEND_AUTH_SCHEME = "Bearer"
DEFAULT_BACKEND_EXTRA_HEADERS = ""
DEFAULT_BACKEND_TIMEOUT = 300

DEFAULT_MAX_HISTORY = 512
DEFAULT_MAX_HISTORY_CHARS = 200_000
DEFAULT_MAX_SESSIONS = 1024
DEFAULT_SESSION_TIMEOUT = 1800

DEFAULT_LOG_LEVEL = "INFO"

# ---- LTB / MCP defaults ----
DEFAULT_ENABLE_TOOLS = True
DEFAULT_MCP_ENDPOINT = "ipc:agent"
DEFAULT_MCP_TIMEOUT_MS = 5000
DEFAULT_MCP_REG_AGENT_APP = "llm_proxy_agent"   # distinct from mcp_api_tool
DEFAULT_MCP_TOOL_PROVIDER_APP = "agent_main_app"
DEFAULT_MCP_AGENT_MAIN_API = "agent_main"
DEFAULT_MCP_AGENT_LOG_API = "agent_log"
DEFAULT_MAX_TOOL_ROUNDS = 100
DEFAULT_MAX_TOTAL_TOOL_CALLS = 50
DEFAULT_MAX_TOOL_RESULT_CHARS = 8000
DEFAULT_MAX_TOTAL_TOOL_RESULT_CHARS = 200_000
DEFAULT_MAX_TOOLS_PER_ROUND = 10

# Retry interval (in seconds) for the MCP middleware connection after a
# failure. Prevents hammering the beacon when the tool provider is down.
DEFAULT_MCP_RETRY_INTERVAL_SEC = 30.0

# Image forwarding is off by default: the operator must confirm that
# the backend is actually a VLM.
DEFAULT_VISION = False

# Server kind reported through the capability matrix.
SERVER_KIND = SERVER_KIND_PROXY

# Maximum length for a client_name, matching llm_service.py and
# llm_proxy.py. Enforced on new session creation.
MAX_CLIENT_NAME_LEN = 512


# ----------------------------------------------------------------------
# Logger
# ----------------------------------------------------------------------

logger = logging.getLogger("llm_proxy_tool")


# ----------------------------------------------------------------------
# Unified JSON parsing helpers (module level)
# ----------------------------------------------------------------------
#
# These helpers wrap the unified JSON repair preprocessor so that any
# malformed JSON text encountered on the tool-call path can be safely
# recovered or, failing that, safely downgraded. They are the ONLY
# places in this module that call json.loads on external input.

def _parse_json_with_repair(
    text: str,
    *,
    source: str,
    fallback: Any,
) -> Any:
    """
    Parse a JSON text with a unified-repair fallback.

    Contract:
      * Fast path: json.loads(text). If it succeeds, return the result.
      * On JSONDecodeError: call repair_json_text(text, source=...,
        report_failure=False). If the repaired text is different and
        parses as JSON, return that result.
      * On any failure, return `fallback` unchanged.

    This function never raises. It is intended for external input
    where a malformed payload must never abort the caller.

    Args:
        text:     The JSON text to parse. Must be a `str`.
        source:   A short identifier used by the repair engine in its
                  log messages, e.g. "llm_proxy_tool.tool_arguments".
        fallback: The value returned when parsing and repair both
                  fail. Typically an empty dict for tool arguments.
    """
    if not isinstance(text, str):
        return fallback

    if not text:
        return fallback

    # Fast path: valid JSON.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Repair path. The engine validates its own output, but we still
    # guard with try/except so that a broken engine can never
    # propagate up.
    try:
        repaired = repair_json_text(
            text,
            source=source,
            report_failure=False,
        )
    except Exception:
        return fallback

    if repaired == text:
        # Engine had nothing to fix; the payload is unrepairable.
        return fallback

    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        return fallback


def _dumps_json_safe(value: Any, *, source: str) -> str:
    """
    Serialize `value` to JSON with a defensive fallback.

    Contract:
      * Fast path: dumps_json(value). On success, return the string.
      * On ValueError (typically "Circular reference detected"): log
        a warning and return a small JSON object of the form
        {"__repr__": "<repr(value)>"} instead of raising.

    This function never raises. It exists so that a rogue tool result
    (for example a Pascal-provided object graph with a cycle) cannot
    abort the entire multi-round tool loop.

    Args:
        value:  The Python object to serialize.
        source: A short identifier used only in the warning message.
    """
    try:
        return dumps_json(value)
    except ValueError as exc:
        logger.warning(
            "JSON serialization failed at %s (%s); "
            "downgrading to repr",
            source, exc,
        )
        # repr() may itself fail on exotic objects; guard once more.
        try:
            fallback_repr = repr(value)
        except Exception:
            fallback_repr = "<unrepresentable>"
        return dumps_json({"__repr__": fallback_repr})


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------

class ProxyConfig:
    """
    Process-wide configuration. Populated exactly once at startup by
    `_init_global_config()`. All runtime code reads from this object.
    """

    def __init__(self) -> None:
        # ---- LingoFuse service ----
        self.endpoint: str = DEFAULT_ENDPOINT
        self.app_name: str = DEFAULT_APP_NAME
        self.notify_api: str = DEFAULT_NOTIFY_API

        # ---- Backend HTTP ----
        self.backend_url: str = DEFAULT_BACKEND_URL
        self.backend_model: str = DEFAULT_BACKEND_MODEL
        self.backend_key: str = DEFAULT_BACKEND_KEY
        self.backend_key_file: str = DEFAULT_BACKEND_KEY_FILE
        self.backend_auth_header: str = DEFAULT_BACKEND_AUTH_HEADER
        self.backend_auth_scheme: str = DEFAULT_BACKEND_AUTH_SCHEME
        self.backend_extra_headers: Dict[str, str] = {}
        self.backend_timeout: int = DEFAULT_BACKEND_TIMEOUT

        # ---- Sessions ----
        self.max_history: int = DEFAULT_MAX_HISTORY
        self.max_history_chars: int = DEFAULT_MAX_HISTORY_CHARS
        self.max_sessions: int = DEFAULT_MAX_SESSIONS
        self.session_timeout: int = DEFAULT_SESSION_TIMEOUT

        # ---- Logging ----
        self.log_level: str = DEFAULT_LOG_LEVEL

        # ---- Attachments ----
        self.vision: bool = DEFAULT_VISION

        # ---- LTB / MCP ----
        self.enable_tools: bool = DEFAULT_ENABLE_TOOLS
        self.mcp_endpoint: str = DEFAULT_MCP_ENDPOINT
        self.mcp_timeout_ms: int = DEFAULT_MCP_TIMEOUT_MS
        self.mcp_reg_agent_app: str = DEFAULT_MCP_REG_AGENT_APP
        self.mcp_tool_provider_app: str = DEFAULT_MCP_TOOL_PROVIDER_APP
        self.mcp_agent_main_api: str = DEFAULT_MCP_AGENT_MAIN_API
        self.mcp_agent_log_api: str = DEFAULT_MCP_AGENT_LOG_API
        self.mcp_retry_interval_sec: float = DEFAULT_MCP_RETRY_INTERVAL_SEC

        self.max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS
        self.max_total_tool_calls: int = DEFAULT_MAX_TOTAL_TOOL_CALLS
        self.max_tools_per_round: int = DEFAULT_MAX_TOOLS_PER_ROUND
        self.max_tool_result_chars: int = DEFAULT_MAX_TOOL_RESULT_CHARS
        self.max_total_tool_result_chars: int = (
            DEFAULT_MAX_TOTAL_TOOL_RESULT_CHARS
        )


CONFIG = ProxyConfig()


# ----------------------------------------------------------------------
# Service
# ----------------------------------------------------------------------

class LLMProxyToolService:
    """
    Main service class.

    Sits between LingoFuse clients (LLM_Service on ipc:llm_service) and
    an OpenAI-compatible HTTP backend, with server-side MCP tool
    execution via language_middleware.
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, MultimodalSessionState] = {}
        self._sessions_lock = threading.Lock()
        self._shutdown = threading.Event()
        self._backend = OpenAIStreamClient(
            base_url=CONFIG.backend_url,
            api_key=CONFIG.backend_key,
            timeout=CONFIG.backend_timeout,
            auth_header=CONFIG.backend_auth_header,
            auth_scheme=CONFIG.backend_auth_scheme,
            extra_headers=CONFIG.backend_extra_headers,
        )
        self._resolved_model: str = CONFIG.backend_model or ""

        # ---- MCP / tools (lazy) ----
        #
        # Tool discovery state machine:
        #   "uninitialized" - no attempt has been made yet
        #   "ready"         - _openai_tools_cache is populated and usable
        #   "failed"        - last attempt failed; may retry after
        #                     mcp_retry_interval_sec has elapsed
        #
        # Using an explicit state string avoids the None-vs-empty-list
        # ambiguity that made the previous implementation hard to read.
        self._mw = None
        self._openai_tools_cache: List[Dict[str, Any]] = []
        self._mcp_state: str = "uninitialized"
        self._mcp_last_error: Optional[str] = None
        self._mcp_init_lock = threading.Lock()
        self._mcp_last_attempt: float = 0.0

        self.server = Server(
            CONFIG.app_name,
            "LingoFuse LLM Tool Bridge (LTB) - proxy with server-side tools",
        )
        self._register_apis()

    # ------------------------------------------------------------------
    # Capability matrix
    # ------------------------------------------------------------------

    def _capabilities(self) -> Dict[str, int]:
        """Return this server's current capability matrix."""
        return build_capabilities(
            set_system_message=0,
            attachments=1,
            vision=1 if CONFIG.vision else 0,
            extras={
                "tools": 1,
                "tool_calls": 1,
                "tool_results": 1,
            },
        )

    # ------------------------------------------------------------------
    # Model resolution
    # ------------------------------------------------------------------

    def resolve_model(self) -> str:
        """
        Auto-discover the backend model id if not explicitly set.

        Discovery is retried on every call where the result was not
        already cached from a successful lookup. The failure path does
        NOT cache "unknown": if the backend was offline at startup, it
        may come online later, and a permanently cached "unknown" would
        poison every subsequent request.
        """
        if self._resolved_model:
            return self._resolved_model

        ids = self._backend.list_models()
        if ids:
            self._resolved_model = ids[0]
            logger.info("Auto-selected backend model: %s",
                        self._resolved_model)
            return self._resolved_model

        logger.warning(
            "Could not auto-discover backend model; "
            "set LLM_PROXY_BACKEND_MODEL or --backend-model. "
            "Will retry on the next request."
        )
        return "unknown"

    # ------------------------------------------------------------------
    # MCP: tool discovery (lazy, with retry on failure)
    # ------------------------------------------------------------------

    def _ensure_tools_ready(self) -> bool:
        """
        Lazily connect to language_middleware and fetch the MCP tool
        list. Converts each tool to OpenAI tools-schema and caches it.

        Returns True if the OpenAI-format cache is non-empty.

        Retry policy:
          * On success, the cache is populated once and never refreshed
            during this process. Restart to pick up new tools.
          * On failure, a retry is attempted at most every
            mcp_retry_interval_sec seconds, so that a beacon that
            starts late still gets picked up.

        Thread safety: guarded by self._mcp_init_lock, so concurrent
        generate calls cannot race to initialize the middleware.
        """
        if not CONFIG.enable_tools:
            return False
        if not _HAS_MIDDLEWARE:
            logger.warning("language_middleware not available; "
                           "tools are disabled")
            return False

        # Fast path: cache already populated.
        if self._mcp_state == "ready" and self._openai_tools_cache:
            return True

        now = time.time()

        # Rate-limit retries after a failed discovery.
        if self._mcp_state == "failed":
            if (now - self._mcp_last_attempt
                    < CONFIG.mcp_retry_interval_sec):
                return False

        with self._mcp_init_lock:
            # Re-check inside the lock: another thread may have
            # succeeded while we were waiting.
            if self._mcp_state == "ready" and self._openai_tools_cache:
                return True
            if self._mcp_state == "failed":
                if (now - self._mcp_last_attempt
                        < CONFIG.mcp_retry_interval_sec):
                    return False

            self._mcp_last_attempt = now
            try:
                self._mw = LanguageMiddleware.get_instance(
                    endpoint=CONFIG.mcp_endpoint,
                    timeout_ms=CONFIG.mcp_timeout_ms,
                    reg_agent_app_name=CONFIG.mcp_reg_agent_app,
                    tool_provider_app=CONFIG.mcp_tool_provider_app,
                    agent_main_api=CONFIG.mcp_agent_main_api,
                    agent_log_api=CONFIG.mcp_agent_log_api,
                )
                self._mw._ensure_connected()
                mcp_tools = self._mw.get_tools() or []
                self._openai_tools_cache = [
                    self._mcp_tool_to_openai(t)
                    for t in mcp_tools
                    if isinstance(t, dict) and t.get("name")
                ]
                if self._openai_tools_cache:
                    self._mcp_state = "ready"
                    self._mcp_last_error = None
                    logger.info("MCP tools loaded: %d tools",
                                len(self._openai_tools_cache))
                else:
                    self._mcp_state = "failed"
                    self._mcp_last_error = "no tools returned"
                    logger.warning(
                        "MCP middleware connected but returned no tools"
                    )
            except Exception as e:
                logger.error("Failed to initialize MCP tools: %s", e)
                self._openai_tools_cache = []
                self._mcp_state = "failed"
                self._mcp_last_error = str(e)

            return bool(self._openai_tools_cache)

    @staticmethod
    def _mcp_tool_to_openai(tool: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert an MCP tool entry to OpenAI tools-schema.

        MCP's `parameters` field is already a JSON Schema, so it is
        passed through unchanged. A minimal schema is filled in when
        `parameters` is missing.
        """
        params = tool.get("parameters")
        if not isinstance(params, dict):
            params = {"type": "object", "properties": {}}
        return {
            "type": "function",
            "function": {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "parameters": params,
            },
        }

    # ------------------------------------------------------------------
    # API registration
    # ------------------------------------------------------------------

    def _register_apis(self) -> None:
        @self.server.expose("generate")
        def generate(data: Dict[str, Any]) -> Dict[str, Any]:
            return self._handle_generate(data)

        @self.server.expose("create_session")
        def create_session(data: Dict[str, Any]) -> Dict[str, Any]:
            return self._handle_create_session(data)

        @self.server.expose("close_session")
        def close_session(data: Dict[str, Any]) -> Dict[str, Any]:
            return self._handle_close_session(data)

        @self.server.expose("cancel_session")
        def cancel_session(data: Dict[str, Any]) -> Dict[str, Any]:
            return self._handle_cancel_session(data)

        @self.server.expose("list_sessions")
        def list_sessions(data: Dict[str, Any]) -> Dict[str, Any]:
            return self._handle_list_sessions(data)

        @self.server.expose("set_system_message")
        def set_system_message(data: Dict[str, Any]) -> Dict[str, Any]:
            return self._handle_set_system_message(data)

        @self.server.expose("get_api_capabilities")
        def get_api_capabilities(data: Dict[str, Any]) -> Dict[str, Any]:
            return self._handle_get_api_capabilities(data)

        @self.server.expose("health")
        def health(data: Dict[str, Any]) -> Dict[str, Any]:
            return self._handle_health(data)

        logger.info(
            "Registered APIs: generate, create_session, close_session, "
            "cancel_session, list_sessions, set_system_message "
            "(unsupported), get_api_capabilities, health",
        )

    # ------------------------------------------------------------------
    # Session helpers
    # ------------------------------------------------------------------

    def _count_sessions(self) -> int:
        with self._sessions_lock:
            return len(self._sessions)

    def _lookup(self, session_id: str) -> Optional[MultimodalSessionState]:
        with self._sessions_lock:
            return self._sessions.get(session_id)

    def _create(self, client_name: str,
                system_message: str = "") -> MultimodalSessionState:
        sid = str(uuid.uuid4())
        sess = MultimodalSessionState(
            session_id=sid,
            client_name=client_name,
            system_message=system_message,
            max_history=CONFIG.max_history,
            max_history_chars=CONFIG.max_history_chars,
        )
        with self._sessions_lock:
            self._sessions[sid] = sess
        return sess

    def _drop(self, session_id: str) -> Optional[MultimodalSessionState]:
        with self._sessions_lock:
            return self._sessions.pop(session_id, None)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_generate(self, data: Any) -> Dict[str, Any]:
        """
        Entry point for the `generate` Call API.

        Accepted request fields:
            session_id      (str, optional for new sessions)
            content         (str, optional if attachments are present)
            prompt          (str, optional)
            client_name     (str, required for new sessions)
            options         (object, optional; tools / tool_choice /
                             response_format are passed through to the
                             backend)
            attachments     (array, optional)

        Structured Output:
            options.response_format is whitelisted as a passthrough
            field (see the sanitize_options call below) and forwarded
            verbatim to the backend on EVERY round of the tool chain.
            See the module docstring for the interaction with tool
            execution.

        Returns:
            {code, session_id, task_id, mode, expects_tool_results}

        `expects_tool_results` is always False for LTB, because tool
        execution is fully internal.
        """
        if not isinstance(data, dict):
            return {"code": -1, "error": "generate expects a JSON object"}

        content = data.get("content", "") or ""
        prompt = data.get("prompt", "") or ""
        client_name = data.get("client_name")
        session_id = data.get("session_id")
        options_raw = data.get("options", {}) or {}
        attachments_raw = data.get("attachments")

        if not content and not prompt and not attachments_raw:
            return {"code": -1,
                    "error": "Missing content, prompt, or attachments"}
        if not isinstance(options_raw, dict):
            return {"code": -1, "error": "options must be a JSON object"}
        if session_id is not None and not isinstance(session_id, str):
            return {"code": -1, "error": "session_id must be a string"}

        # ---- Validate attachments ----
        try:
            attachments = validate_attachments(attachments_raw)
        except AttachmentError as e:
            return {"code": -1, "error": str(e)}

        # ---- Reject image attachments when vision is disabled ----
        has_image = any(a.kind == "image" for a in attachments)
        if has_image and not CONFIG.vision:
            return {
                "code": -1,
                "error": (
                    "Image attachments are not supported by this "
                    "server: --vision is disabled. Restart the proxy "
                    "with --vision and a vision-capable backend, or "
                    "remove the image attachments."
                ),
            }

        # ---- Filter options (tools / tool_choice / response_format) ----
        #
        # COMMON_SCALAR_SPECS is passed explicitly so that the
        # whitelist is visible at the call site and not hidden behind
        # a default argument.
        #
        # Passthrough keys:
        #   - "tools":           list of OpenAI tool definitions.
        #   - "tool_choice":     str or dict controlling tool selection.
        #   - "response_format": Structured Output control. Only a
        #                        top-level dict type check is applied;
        #                        schema-level validation belongs to the
        #                        backend. Forwarded on every round.
        options = sanitize_options(
            options_raw,
            scalar_specs=COMMON_SCALAR_SPECS,
            passthrough={
                "tools":           lambda v: isinstance(v, list),
                "tool_choice":     lambda v: isinstance(v, (str, dict)),
                "response_format": lambda v: isinstance(v, dict),
            },
            debug_log=(
                logger.debug
                if logger.isEnabledFor(logging.DEBUG)
                else None
            ),
        )

        # ---- Resolve or create session (atomic occupancy) ----
        #
        # The running flag and the cancel event are set inside
        # sess.lock before the worker thread starts. This mirrors
        # llm_proxy.py's _handle_generate and prevents two generate()
        # calls on the same session from both passing the running
        # check. We deliberately do NOT use SessionState.begin_run()
        # here: that helper does not re-check running inside its own
        # lock, so a check-then-begin sequence would still race.
        sess: Optional[MultimodalSessionState] = None
        cancel_ev: Optional[threading.Event] = None
        mode: str

        if session_id:
            sess = self._lookup(session_id)
            if sess is None:
                return {"code": -1,
                        "error": f"Session not found: {session_id}"}
            if client_name and client_name != sess.client_name:
                return {"code": -1,
                        "error": "client_name does not match session owner"}
            with sess.lock:
                if sess.running:
                    return {"code": -1,
                            "error": f"Session already running: "
                                     f"{session_id}"}
                sess.running = True
                cancel_ev = threading.Event()
                sess.cancel_event = cancel_ev
            mode = "continue"
        else:
            if not client_name or not isinstance(client_name, str):
                return {"code": -1,
                        "error": "Missing client_name for new session"}
            if len(client_name) > MAX_CLIENT_NAME_LEN:
                return {"code": -1,
                        "error": f"client_name exceeds "
                                 f"{MAX_CLIENT_NAME_LEN} chars"}
            with self._sessions_lock:
                if len(self._sessions) >= CONFIG.max_sessions:
                    return {"code": -1,
                            "error": f"Session limit reached "
                                     f"({CONFIG.max_sessions})"}
                sid = str(uuid.uuid4())
                sess = MultimodalSessionState(
                    session_id=sid,
                    client_name=client_name,
                    max_history=CONFIG.max_history,
                    max_history_chars=CONFIG.max_history_chars,
                )
                sess.running = True
                cancel_ev = threading.Event()
                sess.cancel_event = cancel_ev
                self._sessions[sid] = sess
            mode = "new"

        sess.touch()
        task_id = str(uuid.uuid4())

        try:
            t = threading.Thread(
                target=self._run_generation,
                args=(sess, task_id, content, prompt,
                      options, attachments, cancel_ev),
                name=f"llm-proxy-tool-{task_id[:8]}",
                daemon=True,
            )
            t.start()
        except Exception as e:
            logger.error("Failed to start worker thread: %s", e)
            sess.end_run()
            return {"code": -1, "error": f"Failed to start task: {e}"}

        logger.debug(
            "generate queued: mode=%s session=%s task=%s attachments=%d "
            "response_format=%s",
            mode, sess.session_id, task_id, len(attachments),
            "yes" if "response_format" in options else "no",
        )
        return {
            "code": 0,
            "session_id": sess.session_id,
            "task_id": task_id,
            "mode": mode,
            "expects_tool_results": False,
        }

    def _handle_create_session(self, data: Any) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {"code": -1,
                    "error": "create_session expects a JSON object"}
        client_name = data.get("client_name")
        if not client_name or not isinstance(client_name, str):
            return {"code": -1,
                    "error": "Missing client_name (string required)"}
        if len(client_name) > MAX_CLIENT_NAME_LEN:
            return {"code": -1,
                    "error": f"client_name exceeds "
                             f"{MAX_CLIENT_NAME_LEN} chars"}
        system_message = data.get("system_message") or ""
        if not isinstance(system_message, str):
            return {"code": -1,
                    "error": "system_message must be a string"}

        with self._sessions_lock:
            if len(self._sessions) >= CONFIG.max_sessions:
                return {"code": -1,
                        "error": f"Session limit reached "
                                 f"({CONFIG.max_sessions})"}
            sid = str(uuid.uuid4())
            sess = MultimodalSessionState(
                session_id=sid,
                client_name=client_name,
                system_message=system_message,
                max_history=CONFIG.max_history,
                max_history_chars=CONFIG.max_history_chars,
            )
            self._sessions[sid] = sess

        logger.info("Session created: %s (system_message=%d chars)",
                    sess.session_id, len(system_message))
        return {
            "code": 0,
            "session_id": sess.session_id,
            "client_name": client_name,
        }

    def _handle_close_session(self, data: Any) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {"code": -1,
                    "error": "close_session expects a JSON object"}
        session_id = data.get("session_id")
        if not session_id or not isinstance(session_id, str):
            return {"code": -1, "error": "Missing session_id"}
        sess = self._drop(session_id)
        if sess is None:
            return {"code": -1,
                    "error": f"Session not found: {session_id}"}
        sess.request_cancel()
        self._emit(sess, {
            "type": "closed",
            "session_id": sess.session_id,
            "reason": "client",
        })
        logger.info("Session closed: %s", session_id)
        return {"code": 0, "status": "closed"}

    def _handle_cancel_session(self, data: Any) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {"code": -1,
                    "error": "cancel_session expects a JSON object"}
        session_id = data.get("session_id")
        if not session_id or not isinstance(session_id, str):
            return {"code": -1, "error": "Missing session_id"}
        sess = self._lookup(session_id)
        if sess is None:
            return {"code": -1,
                    "error": f"Session not found: {session_id}"}
        ok = sess.request_cancel()
        logger.debug("Cancel requested for session %s (running=%s)",
                     session_id, ok)
        return {"code": 0,
                "status": "cancel_requested" if ok else "no_active_task"}

    def _handle_list_sessions(self, data: Any) -> Dict[str, Any]:
        client_filter = None
        if isinstance(data, dict):
            client_filter = data.get("client_name")
        with self._sessions_lock:
            sessions = list(self._sessions.values())
        summaries = [
            s.to_summary() for s in sessions
            if (client_filter is None or s.client_name == client_filter)
        ]
        return {"code": 0, "sessions": summaries,
                "count": len(summaries)}

    def _handle_set_system_message(self, data: Any) -> Dict[str, Any]:
        """
        set_system_message is UNSUPPORTED in this proxy.

        Like llm_proxy.py, LTB is a stateless forwarder: each session
        captures its system message at creation and rebuilds the
        messages array before every backend call.
        """
        logger.debug("set_system_message rejected: not supported by "
                     "llm_proxy_tool (stateless forwarder)")
        return {
            "code": -1,
            "status": "unsupported",
            "error": (
                "set_system_message is not supported by "
                "llm_proxy_tool.\n"
                "The system message is fixed at session creation.\n"
                "To change it, close the current session and create\n"
                "a new one with the desired system_message.\n"
            ),
        }

    def _handle_get_api_capabilities(self, data: Any) -> Dict[str, Any]:
        return build_response(SERVER_KIND, self._capabilities())

    def _handle_health(self, data: Any) -> Dict[str, Any]:
        with self._sessions_lock:
            sessions = list(self._sessions.values())
        idle = sum(1 for s in sessions if not s.running)
        running = sum(1 for s in sessions if s.running)

        auth_configured = bool(
            CONFIG.backend_auth_header and CONFIG.backend_key
        )

        mcp_connected = False
        if self._mw is not None:
            try:
                mcp_connected = bool(self._mw.is_connected())
            except Exception:
                mcp_connected = False

        return {
            "code": 0,
            "status": "ok",
            "server_kind": SERVER_KIND,
            "backend_url": CONFIG.backend_url,
            "backend_model": self._resolved_model or CONFIG.backend_model,
            "backend_auth_header": CONFIG.backend_auth_header,
            "backend_auth_scheme": CONFIG.backend_auth_scheme,
            "backend_auth_configured": auth_configured,
            "backend_extra_headers": list(
                CONFIG.backend_extra_headers.keys()
            ),
            "notify_api": CONFIG.notify_api,
            "set_system_message_supported": False,
            "attachments_supported": True,
            "vision_supported": bool(CONFIG.vision),
            "sessions_total": len(sessions),
            "sessions_idle": idle,
            "sessions_running": running,
            "session_limit": CONFIG.max_sessions,
            "session_timeout": CONFIG.session_timeout,
            "max_history": CONFIG.max_history,
            "max_history_chars": CONFIG.max_history_chars,
            "api_capabilities": self._capabilities(),
            # ---- LTB-specific ----
            "tools_enabled": CONFIG.enable_tools,
            "mcp_connected": mcp_connected,
            "mcp_state": self._mcp_state,
            "mcp_last_error": self._mcp_last_error,
            "mcp_tools_count": len(self._openai_tools_cache),
            "max_tool_rounds": CONFIG.max_tool_rounds,
            "max_total_tool_calls": CONFIG.max_total_tool_calls,
            "max_tool_result_chars": CONFIG.max_tool_result_chars,
            "max_total_tool_result_chars":
                CONFIG.max_total_tool_result_chars,
            "tool_execution_mode": "server-side",
        }

    # ------------------------------------------------------------------
    # Core: multi-round generation with server-side tool execution
    # ------------------------------------------------------------------

    def _run_generation(
        self,
        sess: MultimodalSessionState,
        task_id: str,
        content: str,
        prompt: str,
        options: Dict[str, Any],
        attachments: List[Attachment],
        cancel_event: threading.Event,
    ) -> None:
        """
        Multi-round generation with server-side tool execution.

        Round protocol
        --------------
        * Rounds [0, max_tool_rounds - 2] : inject `tools`.
          The backend may return tool_calls; LTB executes them, appends
          assistant.tool_calls + role=tool to history, and continues to
          the next round.

        * Last round (max_tool_rounds - 1) : do NOT inject `tools`.
          This forces the model to emit a final text answer regardless
          of how many tools it has called so far.

        Attachment handling
        -------------------
        Attachments are injected ONLY on round 0. Subsequent rounds see
        the history, which contains text attachments verbatim and
        image attachments as short placeholders. This keeps long tool
        chains from accumulating megabytes of base64.

        Structured Output handling
        --------------------------
        options.response_format, when present, is passed to stream_chat
        on EVERY round. The proxy does not currently strip it on the
        forced final round. See the module docstring for the trade-offs
        of using response_format together with tool execution.

        Session occupancy
        -----------------
        `sess.running` and `sess.cancel_event` are set by the caller
        (`_handle_generate`) BEFORE this method runs, inside
        `sess.lock`. This method must always clear them via
        `sess.end_run()` in its `finally` block.

        JSON handling
        -------------
        The two JSON operations on the tool path (parsing the backend's
        tool_call arguments string, and serializing the tool result for
        the role=tool message) both go through the unified JSON
        policy:

          * Arguments are parsed with _parse_json_with_repair: fast-path
            json.loads, then the unified repair engine, then a safe {}.
            A malformed arguments string never aborts the round-trip.

          * Results are serialized with _dumps_json_safe: dumps_json
            with a defensive circular-reference fallback. A rogue tool
            result never aborts the round-trip.

        Both operations are documented in the module docstring.
        """
        user_text = content + ("\n\n" + prompt if prompt else "")

        # ---- Build the round-0 user content (with images) ----
        try:
            backend_content_r0 = build_user_content(
                text=user_text,
                attachments=attachments,
                vision_enabled=CONFIG.vision,
            )
        except AttachmentError as e:
            logger.error("Task %s: build_user_content failed: %s",
                         task_id, e)
            self._emit(sess, {
                "type": "error",
                "session_id": sess.session_id,
                "message": str(e),
            })
            self._emit(sess, {
                "type": "finish",
                "session_id": sess.session_id,
                "reason": "error",
            })
            sess.end_run()
            return

        # ---- Build the text stored in history ----
        history_text = self._build_history_text(user_text, attachments)

        model = self._resolved_model or CONFIG.backend_model
        if not model:
            model = self.resolve_model()

        # ---- Decide whether tool injection is possible at all ----
        tools_available = False
        try:
            tools_available = (
                CONFIG.enable_tools
                and self._ensure_tools_ready()
                and bool(self._openai_tools_cache)
            )
        except Exception as e:
            logger.error("Tool availability check failed: %s", e)

        if tools_available:
            logger.debug("Task %s: tools available (%d)",
                         task_id, len(self._openai_tools_cache))
        else:
            logger.debug("Task %s: no tools available; "
                         "running as pure text proxy", task_id)

        # Per-call counters for the two additional caps.
        total_tool_calls = 0
        total_tool_result_chars = 0
        force_final_round = False

        response_format_active = "response_format" in options

        try:
            for round_idx in range(CONFIG.max_tool_rounds):
                if cancel_event.is_set():
                    break

                # ---- Build messages (system + history) ----
                history = sess.snapshot()
                messages: List[Dict[str, Any]] = []
                if sess.system_message:
                    messages.append({
                        "role": "system",
                        "content": sess.system_message,
                    })
                messages.extend(history)

                # ---- Inject the round-0 user message ----
                #
                # The first round carries the full user content,
                # including any image attachments. Later rounds use the
                # history, which already contains the round-0 user
                # message with image placeholders.
                if round_idx == 0:
                    messages.append({
                        "role": "user",
                        "content": backend_content_r0,
                    })

                # ---- Decide whether to inject tools this round ----
                is_final_round = (
                    force_final_round
                    or round_idx == CONFIG.max_tool_rounds - 1
                )

                round_options = dict(options)
                # Note: `options` is already sanitized by
                # sanitize_options() in _handle_generate, which never
                # emits a "tool_results" key. The previous defensive
                # pop() was dead code and has been removed.

                if tools_available and not is_final_round:
                    round_options["tools"] = self._openai_tools_cache
                else:
                    round_options.pop("tools", None)
                    round_options.pop("tool_choice", None)

                logger.debug(
                    "Task %s round %d/%d: msgs=%d tools=%s rf=%s",
                    task_id, round_idx, CONFIG.max_tool_rounds,
                    len(messages),
                    "yes" if round_options.get("tools") else "no",
                    "yes" if response_format_active else "no",
                )

                # ---- Stream from the backend ----
                answer_pieces: List[str] = []
                tool_calls: Optional[List[Dict[str, Any]]] = None

                for delta in self._backend.stream_chat(
                    model, messages, round_options,
                    cancel_event=cancel_event,
                ):
                    if cancel_event.is_set():
                        break
                    if "reasoning_content" in delta:
                        self._emit(sess, {
                            "type": "think",
                            "session_id": sess.session_id,
                            "text": delta["reasoning_content"],
                        })
                    if "content" in delta:
                        text = delta["content"]
                        answer_pieces.append(text)
                        self._emit(sess, {
                            "type": "chunk",
                            "session_id": sess.session_id,
                            "text": text,
                        })
                    if "tool_calls" in delta:
                        tool_calls = delta["tool_calls"]

                # ---- No tool calls: this is the final text answer ----
                if not tool_calls:
                    # Persist the round-0 user message exactly once,
                    # after the first successful round. Persisting it
                    # earlier would duplicate it if a later round were
                    # to add another user message (which LTB never does
                    # in the current design, but this is defensive).
                    if round_idx == 0:
                        sess.add_user(history_text)
                    sess.add_assistant("".join(answer_pieces))
                    logger.debug(
                        "Task %s round %d: final answer (%d chars)",
                        task_id, round_idx,
                        len("".join(answer_pieces)),
                    )
                    break

                # ---- Persist the round-0 user message before tool
                #      results are added, so that history order stays
                #      user -> assistant.tool_calls -> tool results.
                if round_idx == 0:
                    sess.add_user(history_text)

                # ---- Defensive: tool_calls on the final round ----
                if is_final_round:
                    logger.warning(
                        "Task %s: model returned tool_calls on the "
                        "final round despite tools not being injected; "
                        "recording text and stopping", task_id,
                    )
                    sess.add_assistant("".join(answer_pieces))
                    break

                # ---- Decide how many tool calls to execute ----
                remaining_total = (
                    CONFIG.max_total_tool_calls - total_tool_calls
                )
                if remaining_total <= 0:
                    logger.warning(
                        "Task %s: total tool-call cap reached (%d); "
                        "switching to final text round",
                        task_id, CONFIG.max_total_tool_calls,
                    )
                    force_final_round = True
                    continue

                num_to_exec = min(
                    len(tool_calls),
                    CONFIG.max_tools_per_round,
                    remaining_total,
                )

                # Record only the tool_calls we actually execute, so
                # every id in assistant.tool_calls has a matching
                # role=tool reply in history.
                exec_calls = tool_calls[:num_to_exec]
                sess.add_assistant_tool_calls(exec_calls)

                logger.debug(
                    "Task %s round %d: executing %d of %d tool call(s)",
                    task_id, round_idx, num_to_exec, len(tool_calls),
                )

                for tc in exec_calls:
                    if cancel_event.is_set():
                        break

                    tc_id = tc.get("id", "") or ""
                    fn = tc.get("function") or {}
                    tool_name = fn.get("name", "") or ""
                    args_str = fn.get("arguments", "") or ""

                    logger.debug("  -> %s(%s)", tool_name, args_str)

                    # ---- Parse arguments (unified repair fallback) ----
                    #
                    # The arguments string is a JSON document produced
                    # by the backend inside a tool_call. It travels
                    # over SSE, not over a LingoFuse DataHandle, so it
                    # is parsed with the unified repair helper. A
                    # malformed arguments string downgrades to {} and
                    # the round-trip continues.
                    args = _parse_json_with_repair(
                        args_str,
                        source="llm_proxy_tool.tool_arguments",
                        fallback={},
                    )
                    if not isinstance(args, dict):
                        args = {}

                    # ---- Execute via middleware ----
                    try:
                        if self._mw is None:
                            raise RuntimeError(
                                "MCP middleware not initialized"
                            )
                        result = self._mw.call_tool(tool_name, args)
                        if result is None:
                            result_content = _dumps_json_safe(
                                {"result": None},
                                source="llm_proxy_tool.tool_result_none",
                            )
                        else:
                            result_content = _dumps_json_safe(
                                result,
                                source="llm_proxy_tool.tool_result",
                            )
                    except Exception as e:
                        logger.debug(
                            "  !! tool execution failed: %s", e,
                        )
                        result_content = _dumps_json_safe(
                            {"error": str(e)},
                            source="llm_proxy_tool.tool_result_error",
                        )

                    # ---- Truncate this single result ----
                    if len(result_content) > CONFIG.max_tool_result_chars:
                        result_content = (
                            result_content[:CONFIG.max_tool_result_chars]
                            + "...(truncated)"
                        )

                    # ---- Enforce the per-call total budget ----
                    remaining_chars = (
                        CONFIG.max_total_tool_result_chars
                        - total_tool_result_chars
                    )
                    if remaining_chars <= 0:
                        result_content = (
                            "...(tool-result budget exhausted)"
                        )
                    elif len(result_content) > remaining_chars:
                        result_content = (
                            result_content[:remaining_chars]
                            + "...(tool-result budget exhausted)"
                        )
                    total_tool_result_chars += len(result_content)

                    preview = result_content[:200]
                    logger.debug(
                        "  <- %s", preview
                        + ("..." if len(result_content) > 200 else ""),
                    )

                    sess.add_tool_result(tc_id, result_content)
                    total_tool_calls += 1

                # Loop back for the next round

            # ---- All rounds done; emit exactly one finish event ----
            self._emit(sess, {
                "type": "finish",
                "session_id": sess.session_id,
                "reason": "stop",
            })
            logger.debug("Task %s finished", task_id)

        except Exception as e:
            error_message = str(e)
            logger.error("Task %s backend error: %s",
                         task_id, error_message)
            try:
                self._emit(sess, {
                    "type": "error",
                    "session_id": sess.session_id,
                    "message": error_message,
                })
                self._emit(sess, {
                    "type": "finish",
                    "session_id": sess.session_id,
                    "reason": "error",
                })
            except Exception as ee:
                logger.error(
                    "Task %s: failed to emit error finish: %s",
                    task_id, ee,
                )
        finally:
            # Always release the session occupancy flag, no matter how
            # the loop exited. This guarantees that a bug in the loop
            # never leaves the session stuck in "running" state.
            try:
                sess.end_run()
            except Exception as e:
                logger.error(
                    "Task %s: failed to end_run session: %s",
                    task_id, e,
                )

    @staticmethod
    def _build_history_text(
        user_text: str,
        attachments: List[Attachment],
    ) -> str:
        """
        Build the text stored in the session history for the round-0
        user message. Text attachments are kept verbatim; images are
        replaced by a short placeholder.
        """
        if not attachments:
            return user_text

        parts: List[str] = []
        if user_text:
            parts.append(user_text)
        for att in attachments:
            if att.kind == "text":
                parts.append(
                    f'<attachment name="{att.name}">\n'
                    f'{att.text}\n'
                    f'</attachment>'
                )
            elif att.kind == "image":
                parts.append(placeholder_for(att))
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Emission
    # ------------------------------------------------------------------

    def _emit(self, sess: MultimodalSessionState,
              payload: Dict[str, Any]) -> None:
        """
        Send one structured JSON event to the client via the notify API.

        All payload I/O goes through lingofuse.lf_io:
          * write_json() serializes `payload` with ensure_ascii=False
            (no \\uXXXX escapes) and appends the NUL terminator
            required by the Pascal-side LF_ReadString.
          * cstr() supplies NUL-terminated UTF-8 bytes for the
            c_char_p parameter of LF_Sequenced_Notify.

        Failures (client offline, DataHandle issues) are logged at
        DEBUG level and swallowed. They must never crash the worker
        thread.
        """
        hnd = None
        try:
            hnd = DataHandle(CONFIG.notify_api)
            write_json(hnd.raw, payload)
            LF_Sequenced_Notify(cstr(sess.client_name), hnd.raw)
        except Exception as e:
            logger.debug(
                "Notify to '%s' failed (%s): %s",
                sess.client_name, payload.get("type"), e,
            )
        finally:
            if hnd is not None:
                try:
                    hnd.free()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Watchdog
    # ------------------------------------------------------------------

    def _watchdog_loop(self) -> None:
        """
        Reclaim sessions that have been idle past session_timeout.

        Running sessions are skipped: they are mid-generation and the
        worker thread owns their state. LTB does not own a model
        context, so a simpler single-condition policy is sufficient.
        """
        while not self._shutdown.wait(timeout=5.0):
            now = time.time()
            expired: List[str] = []
            try:
                with self._sessions_lock:
                    for sid, sess in self._sessions.items():
                        if sess.running:
                            continue
                        if now - sess.last_active > CONFIG.session_timeout:
                            expired.append(sid)
                    for sid in expired:
                        self._sessions.pop(sid, None)
            except Exception as e:
                logger.error("Watchdog iteration failed: %s", e)
                continue
            for sid in expired:
                logger.debug("Session %s expired by idle timeout", sid)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """
        Start the LTB service.

        Ordering is critical:

        Server.start() internally calls LF_PrepareDone(), which starts
        the LingoFuse simulated main thread. Once that main thread is
        running, a subsequent LF_PrepareDone() call from
        language_middleware._connect() returns 0 (not 1), and
        _connect() treats that as a hard failure, permanently
        disabling the tool cache for the rest of the process.

        To avoid that, we call _ensure_tools_ready() FIRST, so the
        middleware wins the race: it starts the main thread, then
        Server.start() detects the active main thread via
        LF_CheckMainThread() and proceeds with a warning instead of
        raising.
        """
        logger.info("Backend URL: %s", CONFIG.backend_url)
        if CONFIG.backend_auth_header and CONFIG.backend_key:
            logger.info(
                "Backend auth: header=%s scheme=%s token_chars=%d",
                CONFIG.backend_auth_header,
                CONFIG.backend_auth_scheme or "(none)",
                len(CONFIG.backend_key),
            )
        else:
            logger.info("Backend auth: disabled (no token)")
        if CONFIG.backend_extra_headers:
            logger.info("Backend extra headers: %s",
                        list(CONFIG.backend_extra_headers.keys()))
        self.resolve_model()

        # ---- CRITICAL: pre-connect MCP middleware BEFORE Server.start() ----
        if CONFIG.enable_tools and _HAS_MIDDLEWARE:
            logger.info("Pre-connecting MCP middleware before server start")
            ok = self._ensure_tools_ready()
            if ok:
                logger.info("MCP middleware ready: %d tool(s) cached",
                            len(self._openai_tools_cache))
            else:
                logger.warning(
                    "MCP middleware pre-connect did not yield any tools. "
                    "Tool execution will be disabled until a retry "
                    "succeeds. Make sure pascal_agent_service.exe and "
                    "pascal_agent_api.exe are running on ipc:agent."
                )
        elif not _HAS_MIDDLEWARE:
            logger.warning("language_middleware not importable; "
                           "running as pure-text proxy")
        elif not CONFIG.enable_tools:
            logger.info("Tools disabled by configuration (--no-tools); "
                        "running as pure-text proxy")

        threading.Thread(
            target=self._watchdog_loop,
            name="llm-proxy-tool-watchdog",
            daemon=True,
        ).start()

        self.server.start(CONFIG.endpoint)
        logger.info("LLM Tool Bridge service '%s' running on %s",
                    CONFIG.app_name, CONFIG.endpoint)

    def stop(self) -> None:
        """Graceful shutdown: cancel sessions, close MCP, stop server."""
        self._shutdown.set()
        try:
            with self._sessions_lock:
                sessions = list(self._sessions.values())
            for sess in sessions:
                sess.request_cancel()
        except Exception as e:
            logger.warning("Session cancellation during shutdown: %s", e)

        # Shutdown MCP middleware if it was ever initialized.
        if self._mw is not None:
            try:
                self._mw.shutdown()
                logger.info("MCP middleware shut down")
            except Exception as e:
                logger.warning("MCP middleware shutdown error: %s", e)
            self._mw = None

        try:
            self.server.stop(full_cleanup=True)
        except Exception as e:
            logger.warning("Server stop error: %s", e)


# ----------------------------------------------------------------------
# Banner
# ----------------------------------------------------------------------

def print_service_banner() -> None:
    """Print the startup banner with the effective configuration."""
    if CONFIG.backend_auth_header and CONFIG.backend_key:
        auth_display = (
            f"{CONFIG.backend_auth_header}: "
            f"{CONFIG.backend_auth_scheme + ' ' if CONFIG.backend_auth_scheme else ''}"
            f"{redact(CONFIG.backend_key)}"
        )
    else:
        auth_display = "(disabled)"

    extra_display = (
        ", ".join(CONFIG.backend_extra_headers.keys())
        if CONFIG.backend_extra_headers else "(none)"
    )

    caps = build_capabilities(
        set_system_message=0,
        attachments=1,
        vision=1 if CONFIG.vision else 0,
        extras={"tools": 1, "tool_calls": 1, "tool_results": 1},
    )
    supported, unsupported = split_supported(caps)

    print_banner(
        "LINGOFUSE LLM PROXY TOOL BRIDGE (LTB)",
        [
            [
                ("Service kind", SERVER_KIND),
                ("Service endpoint", CONFIG.endpoint),
                ("Service app name", CONFIG.app_name),
                ("Notify API name", CONFIG.notify_api),
                ("Backend URL", CONFIG.backend_url),
                ("Backend model",
                 CONFIG.backend_model or "(auto-discover)"),
                ("Backend auth", auth_display),
                ("Backend extra headers", extra_display),
                ("Backend timeout (s)", CONFIG.backend_timeout),
                ("Backend transport", "http.client"),
                ("Max history per session",
                 f"{CONFIG.max_history} messages / "
                 f"{CONFIG.max_history_chars} chars"),
                ("Max sessions", CONFIG.max_sessions),
                ("Session idle timeout(s)", CONFIG.session_timeout),
                ("set_system_message",
                 "unsupported (llm_service-only)"),
                ("Attachments",
                 "enabled (text always, image when --vision)"),
                ("Vision",
                 "enabled" if CONFIG.vision else "disabled"),
                ("Structured Output",
                 "enabled (options.response_format forwarded per round)"),
                ("Log level", CONFIG.log_level),
            ],
            [
                ("Tools enabled", CONFIG.enable_tools),
                ("Tool execution mode", "server-side (client-transparent)"),
                ("MCP endpoint", CONFIG.mcp_endpoint),
                ("MCP reg agent app", CONFIG.mcp_reg_agent_app),
                ("MCP tool provider app", CONFIG.mcp_tool_provider_app),
                ("Max tool rounds", CONFIG.max_tool_rounds),
                ("Max total tool calls", CONFIG.max_total_tool_calls),
                ("Max tools per round", CONFIG.max_tools_per_round),
                ("Max tool result chars", CONFIG.max_tool_result_chars),
                ("Max total tool-result chars",
                 CONFIG.max_total_tool_result_chars),
            ],
            [
                ("Supported APIs", join_list(supported)),
                ("Unsupported APIs", join_list(unsupported)),
            ],
        ],
    )


# ----------------------------------------------------------------------
# Signal handling
# ----------------------------------------------------------------------

_SHUTDOWN = threading.Event()


def _install_signal_handlers() -> None:
    """
    Convert SIGINT / SIGTERM into a shutdown signal.

    The main loop waits on _SHUTDOWN; setting it causes a graceful
    exit through service.stop().
    """
    import signal

    def _handler(signum, frame):
        logger.info("Received signal %s; shutting down", signum)
        _SHUTDOWN.set()

    for sig in ("SIGINT", "SIGTERM"):
        s = getattr(signal, sig, None)
        if s is None:
            continue
        try:
            signal.signal(s, _handler)
        except (ValueError, OSError):
            pass


# ----------------------------------------------------------------------
# Argument parsing
# ----------------------------------------------------------------------

def parse_args():
    """
    Parse command-line arguments.

    The usage line and the examples section of `--help` adapt to the
    current packaging via llm_common.runtime.
    """
    invocation = get_example_invocation()

    epilog = (
        "Examples:\n"
        f"  {invocation} --backend-url http://127.0.0.1:1234/v1\n"
        f"  {invocation} --no-tools\n"
        f"  {invocation} --vision --backend-url http://127.0.0.1:1234/v1\n"
        f"  {invocation} --max-tool-rounds 200 --max-total-tool-calls 100\n"
        f"  {invocation} --mcp-reg-agent-app llm_proxy_agent "
        f"--mcp-tool-provider-app agent_main_app\n"
        f"  {invocation} --log-level DEBUG\n"
        "\n"
        "Relationship with siblings:\n"
        "  llm_service.py      - local inference (llama.cpp)\n"
        "  llm_proxy.py        - pure text proxy\n"
        "  llm_proxy_tool.py   - proxy with server-side tools (this file)\n"
        "  mcp_api_tool.py     - MCP tool gateway (Host-side)\n"
        "\n"
        "  llm_proxy_tool and mcp_api_tool CAN coexist: they use\n"
        "  different reg_agent_app_name values (llm_proxy_agent vs\n"
        "  reg_agent) and share the same beacon on ipc:agent.\n"
        "\n"
        "  All three LLM services share the default endpoint\n"
        "  ipc:llm_service and app name LLM_Service, so only ONE may\n"
        "  run at any time unless you override --endpoint and\n"
        "  --app-name.\n"
        "\n"
        "Attachments:\n"
        "  A generate request may carry an `attachments` array whose\n"
        "  entries are of kind `text` or `image`. Text attachments\n"
        "  are always accepted. Image attachments require --vision\n"
        "  and a vision-capable backend. Images are injected only on\n"
        "  the first round of a tool chain; later rounds see a short\n"
        "  placeholder in history.\n"
        "\n"
        "Structured Output:\n"
        "  A generate request may carry options.response_format, an\n"
        "  OpenAI Structured Outputs object such as\n"
        "      {\"type\": \"json_schema\",\n"
        "       \"json_schema\": {\"name\": \"...\", \"strict\": true,\n"
        "                       \"schema\": {...}}}\n"
        "  or the simpler {\"type\": \"json_object\"}. LTB forwards\n"
        "  this field verbatim to the backend on EVERY round of a\n"
        "  tool chain. Some backends will prefer tool_calls over the\n"
        "  schema when both are present; if you see the model\n"
        "  refusing to call tools, run with --no-tools (pure\n"
        "  passthrough), or use a schema that does not conflict with\n"
        "  the tool-call intent.\n"
        "\n"
        "Environment variables (read once at startup):\n"
        "  LLM_PROXY_ENDPOINT              - endpoint\n"
        "  LLM_PROXY_APP_NAME              - app name\n"
        "  LLM_PROXY_NOTIFY_API            - notify API name\n"
        "  LLM_PROXY_BACKEND_URL           - backend base URL\n"
        "  LLM_PROXY_BACKEND_MODEL         - backend model id\n"
        "  LLM_PROXY_BACKEND_KEY           - backend API key\n"
        "  LLM_PROXY_BACKEND_KEY_FILE      - file containing the key\n"
        "  LLM_PROXY_BACKEND_AUTH_HEADER   - auth header name\n"
        "  LLM_PROXY_BACKEND_AUTH_SCHEME   - auth scheme prefix\n"
        "  LLM_PROXY_BACKEND_EXTRA_HEADERS - extra headers as JSON\n"
        "  LLM_PROXY_BACKEND_TIMEOUT       - HTTP read timeout (s)\n"
        "  LLM_PROXY_MAX_HISTORY           - max messages per session\n"
        "  LLM_PROXY_MAX_HISTORY_CHARS     - max chars per session\n"
        "  LLM_PROXY_MAX_SESSIONS          - max concurrent sessions\n"
        "  LLM_PROXY_SESSION_TIMEOUT       - idle timeout (s)\n"
        "  LLM_PROXY_VISION                - enable image attachments\n"
        "  LLM_PROXY_ENABLE_TOOLS          - enable MCP tools\n"
        "  LLM_PROXY_MCP_ENDPOINT          - beacon endpoint\n"
        "  LLM_PROXY_MCP_TIMEOUT           - MCP call timeout (ms)\n"
        "  LLM_PROXY_MCP_REG_AGENT_APP     - reg agent app name\n"
        "  LLM_PROXY_MCP_TOOL_PROVIDER_APP - tool provider app name\n"
        "  LLM_PROXY_MAX_TOOL_ROUNDS       - max tool rounds\n"
        "  LLM_PROXY_MAX_TOTAL_TOOL_CALLS  - max total tool calls\n"
        "  LLM_PROXY_MAX_TOOLS_PER_ROUND   - max tools per round\n"
        "  LLM_PROXY_MAX_TOOL_RESULT_CHARS - per-result char cap\n"
        "  LLM_PROXY_MAX_TOTAL_TOOL_RESULT_CHARS - total char cap\n"
        "  LLM_PROXY_LOG_LEVEL             - DEBUG/INFO/WARNING/ERROR\n"
    )

    import argparse

    parser = argparse.ArgumentParser(
        prog=get_invocation_name(),
        description=(
            "LingoFuse LLM Tool Bridge (LTB).\n"
            "\n"
            "A proxy that forwards to an OpenAI-compatible HTTP backend,\n"
            "with optional server-side MCP tool execution via\n"
            "language_middleware.\n"
            "\n"
            "The client sends a normal `generate` request and receives a\n"
            "normal chunk / think / finish stream. All tool-call\n"
            "plumbing happens server-side; zero client changes required.\n"
            "\n"
            "Structured Output is supported as a passthrough field:\n"
            "options.response_format is forwarded verbatim to the\n"
            "backend on every round of the tool chain. See the epilog\n"
            "for the interaction with tool execution.\n"
        ),
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # ---- LingoFuse service ----
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("LLM_PROXY_ENDPOINT", DEFAULT_ENDPOINT),
        metavar="ADDRESS",
        help="LingoFuse service endpoint. Default: ipc:llm_service",
    )
    parser.add_argument(
        "--app-name",
        default=os.environ.get("LLM_PROXY_APP_NAME", DEFAULT_APP_NAME),
        metavar="NAME",
        help="LingoFuse application name. Default: LLM_Service",
    )
    parser.add_argument(
        "--notify-api",
        default=os.environ.get("LLM_PROXY_NOTIFY_API", DEFAULT_NOTIFY_API),
        metavar="NAME",
        help="Notify API name for streaming chunks. Default: llm_stream",
    )

    # ---- Backend ----
    parser.add_argument(
        "--backend-url",
        default=os.environ.get("LLM_PROXY_BACKEND_URL", DEFAULT_BACKEND_URL),
        metavar="URL",
        help="Base URL of the OpenAI-compatible backend. "
             "Default: http://127.0.0.1:12345/v1",
    )
    parser.add_argument(
        "--backend-model",
        default=os.environ.get("LLM_PROXY_BACKEND_MODEL",
                               DEFAULT_BACKEND_MODEL),
        metavar="ID",
        help="Model id sent to the backend. Empty = auto-discover.",
    )
    parser.add_argument(
        "--backend-key",
        default=os.environ.get("LLM_PROXY_BACKEND_KEY", DEFAULT_BACKEND_KEY),
        metavar="KEY",
        help="API key / token. Default: lm-studio",
    )
    parser.add_argument(
        "--backend-key-file",
        default=os.environ.get("LLM_PROXY_BACKEND_KEY_FILE",
                               DEFAULT_BACKEND_KEY_FILE),
        metavar="PATH",
        help="File containing the API key; overrides --backend-key.",
    )
    parser.add_argument(
        "--backend-auth-header",
        default=os.environ.get("LLM_PROXY_BACKEND_AUTH_HEADER",
                               DEFAULT_BACKEND_AUTH_HEADER),
        metavar="NAME",
        help="Header carrying the token. Default: Authorization",
    )
    parser.add_argument(
        "--backend-auth-scheme",
        default=os.environ.get("LLM_PROXY_BACKEND_AUTH_SCHEME",
                               DEFAULT_BACKEND_AUTH_SCHEME),
        metavar="PREFIX",
        help="Scheme prefix. Empty string = raw token. Default: Bearer",
    )
    parser.add_argument(
        "--backend-extra-headers",
        default=os.environ.get("LLM_PROXY_BACKEND_EXTRA_HEADERS",
                               DEFAULT_BACKEND_EXTRA_HEADERS),
        metavar="JSON",
        help="Extra HTTP headers as JSON. Default: {}",
    )
    parser.add_argument(
        "--backend-timeout",
        type=int,
        default=int(os.environ.get("LLM_PROXY_BACKEND_TIMEOUT",
                                   DEFAULT_BACKEND_TIMEOUT)),
        metavar="SECONDS",
        help="HTTP read timeout for backend streaming. Default: 300",
    )

    # ---- Sessions ----
    parser.add_argument(
        "--max-history",
        type=int,
        default=int(os.environ.get("LLM_PROXY_MAX_HISTORY",
                                   DEFAULT_MAX_HISTORY)),
        metavar="N",
        help="Max messages retained per session. Default: 512",
    )
    parser.add_argument(
        "--max-history-chars",
        type=int,
        default=int(os.environ.get("LLM_PROXY_MAX_HISTORY_CHARS",
                                   DEFAULT_MAX_HISTORY_CHARS)),
        metavar="N",
        help="Max total characters of message history per session. "
             "Default: 200000",
    )
    parser.add_argument(
        "--max-sessions",
        type=int,
        default=int(os.environ.get("LLM_PROXY_MAX_SESSIONS",
                                   DEFAULT_MAX_SESSIONS)),
        metavar="N",
        help="Max concurrent sessions. Default: 1024",
    )
    parser.add_argument(
        "--session-timeout",
        type=int,
        default=int(os.environ.get("LLM_PROXY_SESSION_TIMEOUT",
                                   DEFAULT_SESSION_TIMEOUT)),
        metavar="SECONDS",
        help="Idle timeout for sessions. Default: 1800",
    )

    # ---- Attachments / vision ----
    parser.add_argument(
        "--vision",
        dest="vision",
        action="store_true",
        default=os.environ.get("LLM_PROXY_VISION", "0").lower()
        in ("1", "true", "yes"),
        help="Enable image attachment forwarding. The backend must be "
             "a vision-capable model. Default: disabled.",
    )
    parser.add_argument(
        "--no-vision",
        dest="vision",
        action="store_false",
        help="Disable image attachment forwarding (default).",
    )

    # ---- LTB / tools ----
    parser.add_argument(
        "--enable-tools",
        dest="enable_tools",
        action="store_true",
        default=os.environ.get("LLM_PROXY_ENABLE_TOOLS", "1").lower()
        in ("1", "true", "yes"),
        help="Enable MCP tool discovery and server-side execution "
             "(default).",
    )
    parser.add_argument(
        "--no-tools",
        dest="enable_tools",
        action="store_false",
        help="Disable all tool-related behavior; behave like llm_proxy.py.",
    )
    parser.add_argument(
        "--mcp-endpoint",
        default=os.environ.get("LLM_PROXY_MCP_ENDPOINT",
                               DEFAULT_MCP_ENDPOINT),
        metavar="ADDRESS",
        help="LingoFuse endpoint of the MCP tool provider. "
             "Default: ipc:agent",
    )
    parser.add_argument(
        "--mcp-timeout",
        type=int,
        default=int(os.environ.get("LLM_PROXY_MCP_TIMEOUT",
                                   DEFAULT_MCP_TIMEOUT_MS)),
        metavar="MS",
        help="MCP call timeout in ms. Default: 5000",
    )
    parser.add_argument(
        "--mcp-reg-agent-app",
        default=os.environ.get("LLM_PROXY_MCP_REG_AGENT_APP",
                               DEFAULT_MCP_REG_AGENT_APP),
        metavar="NAME",
        help="Registration agent app name for MCP middleware. "
             "Default: llm_proxy_agent (distinct from mcp_api_tool).",
    )
    parser.add_argument(
        "--mcp-tool-provider-app",
        default=os.environ.get("LLM_PROXY_MCP_TOOL_PROVIDER_APP",
                               DEFAULT_MCP_TOOL_PROVIDER_APP),
        metavar="NAME",
        help="Tool provider app name. Default: agent_main_app",
    )
    parser.add_argument(
        "--max-tool-rounds",
        type=int,
        default=int(os.environ.get("LLM_PROXY_MAX_TOOL_ROUNDS",
                                   DEFAULT_MAX_TOOL_ROUNDS)),
        metavar="N",
        help="Max tool-call rounds per generate. Default: 100",
    )
    parser.add_argument(
        "--max-total-tool-calls",
        type=int,
        default=int(os.environ.get("LLM_PROXY_MAX_TOTAL_TOOL_CALLS",
                                   DEFAULT_MAX_TOTAL_TOOL_CALLS)),
        metavar="N",
        help="Max total tool calls per generate. Default: 50",
    )
    parser.add_argument(
        "--max-tools-per-round",
        type=int,
        default=int(os.environ.get("LLM_PROXY_MAX_TOOLS_PER_ROUND",
                                   DEFAULT_MAX_TOOLS_PER_ROUND)),
        metavar="N",
        help="Max tools processed per round. Default: 10",
    )
    parser.add_argument(
        "--max-tool-result-chars",
        type=int,
        default=int(os.environ.get("LLM_PROXY_MAX_TOOL_RESULT_CHARS",
                                   DEFAULT_MAX_TOOL_RESULT_CHARS)),
        metavar="N",
        help="Truncate a single tool result above this length. "
             "Default: 8000",
    )
    parser.add_argument(
        "--max-total-tool-result-chars",
        type=int,
        default=int(os.environ.get(
            "LLM_PROXY_MAX_TOTAL_TOOL_RESULT_CHARS",
            DEFAULT_MAX_TOTAL_TOOL_RESULT_CHARS,
        )),
        metavar="N",
        help="Truncate total tool results per generate above this "
             "length. Default: 200000",
    )

    # ---- Logging ----
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LLM_PROXY_LOG_LEVEL",
                               DEFAULT_LOG_LEVEL).upper(),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log verbosity. Default: INFO",
    )

    return parser.parse_args()


def _init_global_config(args) -> None:
    """
    Copy parsed arguments into the global CONFIG object.

    After this function returns, no runtime code will read
    `os.environ` or `sys.argv` again. Everything reads from CONFIG.
    """
    # LingoFuse service
    CONFIG.endpoint = args.endpoint
    CONFIG.app_name = args.app_name
    CONFIG.notify_api = args.notify_api

    # Backend
    CONFIG.backend_url = args.backend_url.rstrip("/")
    CONFIG.backend_model = args.backend_model
    CONFIG.backend_key = args.backend_key
    CONFIG.backend_key_file = args.backend_key_file
    CONFIG.backend_auth_header = args.backend_auth_header
    CONFIG.backend_auth_scheme = args.backend_auth_scheme
    CONFIG.backend_timeout = args.backend_timeout

    # Sessions (clamped to sane minimums)
    CONFIG.max_history = max(2, args.max_history)
    CONFIG.max_history_chars = max(1000, args.max_history_chars)
    CONFIG.max_sessions = max(1, args.max_sessions)
    CONFIG.session_timeout = max(10, args.session_timeout)

    # Attachments
    CONFIG.vision = bool(args.vision)

    # LTB / MCP
    CONFIG.enable_tools = bool(args.enable_tools)
    CONFIG.mcp_endpoint = args.mcp_endpoint
    CONFIG.mcp_timeout_ms = max(100, args.mcp_timeout)
    CONFIG.mcp_reg_agent_app = args.mcp_reg_agent_app
    CONFIG.mcp_tool_provider_app = args.mcp_tool_provider_app
    CONFIG.max_tool_rounds = max(1, args.max_tool_rounds)
    CONFIG.max_total_tool_calls = max(1, args.max_total_tool_calls)
    CONFIG.max_tools_per_round = max(1, args.max_tools_per_round)
    CONFIG.max_tool_result_chars = max(64, args.max_tool_result_chars)
    CONFIG.max_total_tool_result_chars = max(
        64, args.max_total_tool_result_chars,
    )

    # Logging
    CONFIG.log_level = args.log_level

    # Extra headers
    try:
        CONFIG.backend_extra_headers = parse_extra_headers(
            args.backend_extra_headers
        )
    except ValueError as e:
        print(f"[FATAL] {e}", file=sys.stderr)
        sys.exit(1)

    # Key file overrides --backend-key
    if CONFIG.backend_key_file:
        try:
            CONFIG.backend_key = load_key_from_file(CONFIG.backend_key_file)
        except ValueError as e:
            print(f"[FATAL] {e}", file=sys.stderr)
            sys.exit(1)


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main() -> int:
    args = parse_args()
    _init_global_config(args)

    # Configure logging once, after all levels are known.
    setup_logging(CONFIG.log_level)

    if not _HAS_MIDDLEWARE:
        logger.warning(
            "language_middleware not found on PYTHONPATH. Tool features "
            "will be unavailable; the program will behave as a "
            "pure-text proxy."
        )

    print_service_banner()

    service: Optional[LLMProxyToolService] = None
    try:
        service = LLMProxyToolService()
        atexit.register(service.stop)
        _install_signal_handlers()
    except Exception as e:
        logger.error("Failed to construct service: %s", e)
        return 1

    try:
        service.start()
        logger.info("Press Ctrl+C to stop...")
        while not _SHUTDOWN.is_set():
            if _SHUTDOWN.wait(timeout=1.0):
                break
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error("Service error: %s", e)
        return 1
    finally:
        logger.info("Shutting down")
        try:
            service.stop()
        except Exception as e:
            logger.error("Final stop error: %s", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())