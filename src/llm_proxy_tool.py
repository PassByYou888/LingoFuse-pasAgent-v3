#!/usr/bin/env python3

import atexit
import json
import logging
import os
import shlex
import signal
import sys
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from lingofuse import Server, set_option, check_uid, to_tp2p_uid
from lingofuse.core import DataHandle
from lingofuse._lf_native import LF_UID_Sequenced_Notify
from lingofuse.lf_io import (
    cstr,
    dumps_json,
    write_json,
)
from lingofuse.json_repair_preprocess import repair_json_text
from lingofuse.options import set_opt_debug

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

DEFAULT_ENABLE_TOOLS = True
DEFAULT_MCP_ENDPOINT = "ipc:agent"
DEFAULT_MCP_TIMEOUT_MS = 5000
DEFAULT_MCP_REG_AGENT_APP = "llm_proxy_agent"
DEFAULT_MCP_TOOL_PROVIDER_APP = "agent_main_app"
DEFAULT_MCP_AGENT_MAIN_API = "agent_main"
DEFAULT_MCP_AGENT_LOG_API = "agent_log"
DEFAULT_MAX_TOOL_ROUNDS = 100
DEFAULT_MAX_TOTAL_TOOL_CALLS = 50
DEFAULT_MAX_TOOL_RESULT_CHARS = 8000
DEFAULT_MAX_TOTAL_TOOL_RESULT_CHARS = 200_000
DEFAULT_MAX_TOOLS_PER_ROUND = 10

DEFAULT_MCP_RETRY_INTERVAL_SEC = 30.0

DEFAULT_VISION = False

SERVER_KIND = SERVER_KIND_PROXY
UID_BYTES = 32

SESSION_CLOSE_REASON_CONSOLE = "console"
SESSION_CLOSE_REASON_CLIENT = "client"
SESSION_CLOSE_REASON_SHUTDOWN = "shutdown"

logger = logging.getLogger("llm_proxy_tool")


def _parse_json_with_repair(
    text: str,
    *,
    source: str,
    fallback: Any,
) -> Any:
    if not isinstance(text, str):
        return fallback
    if not text:
        return fallback
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        repaired = repair_json_text(
            text,
            source=source,
            report_failure=False,
        )
    except Exception:
        return fallback
    if repaired == text:
        return fallback
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        return fallback


def _dumps_json_safe(value: Any, *, source: str) -> str:
    try:
        return dumps_json(value)
    except ValueError as exc:
        logger.warning(
            "JSON serialization failed at %s (%s); "
            "downgrading to repr",
            source, exc,
        )
        try:
            fallback_repr = repr(value)
        except Exception:
            fallback_repr = "<unrepresentable>"
        return dumps_json({"__repr__": fallback_repr})


class ProxyConfig:

    def __init__(self) -> None:
        self.endpoint: str = DEFAULT_ENDPOINT
        self.app_name: str = DEFAULT_APP_NAME
        self.notify_api: str = DEFAULT_NOTIFY_API

        self.backend_url: str = DEFAULT_BACKEND_URL
        self.backend_model: str = DEFAULT_BACKEND_MODEL
        self.backend_key: str = DEFAULT_BACKEND_KEY
        self.backend_key_file: str = DEFAULT_BACKEND_KEY_FILE
        self.backend_auth_header: str = DEFAULT_BACKEND_AUTH_HEADER
        self.backend_auth_scheme: str = DEFAULT_BACKEND_AUTH_SCHEME
        self.backend_extra_headers: Dict[str, str] = {}
        self.backend_timeout: int = DEFAULT_BACKEND_TIMEOUT

        self.max_history: int = DEFAULT_MAX_HISTORY
        self.max_history_chars: int = DEFAULT_MAX_HISTORY_CHARS
        self.max_sessions: int = DEFAULT_MAX_SESSIONS
        self.session_timeout: int = DEFAULT_SESSION_TIMEOUT

        self.log_level: str = DEFAULT_LOG_LEVEL

        self.vision: bool = DEFAULT_VISION

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


class LLMProxyToolService:

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
        self._model_lock = threading.Lock()

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

    def _capabilities(self) -> Dict[str, int]:
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

    def resolve_model(self) -> str:
        with self._model_lock:
            if self._resolved_model:
                return self._resolved_model
        ids = self._backend.list_models()
        with self._model_lock:
            if self._resolved_model:
                return self._resolved_model
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

    def refresh_model(self) -> str:
        with self._model_lock:
            self._resolved_model = ""
        return self.resolve_model()

    def current_model(self) -> str:
        with self._model_lock:
            return self._resolved_model

    def mcp_snapshot(self) -> Dict[str, Any]:
        state = self._mcp_state
        last_error = self._mcp_last_error
        last_attempt = self._mcp_last_attempt
        cache = list(self._openai_tools_cache) if self._openai_tools_cache else []
        mw = self._mw
        mw_connected = False
        if mw is not None:
            try:
                mw_connected = bool(mw.is_connected())
            except Exception:
                mw_connected = False
        tool_entries: List[Dict[str, str]] = []
        for t in cache:
            fn = t.get("function") if isinstance(t, dict) else None
            if not isinstance(fn, dict):
                continue
            tool_entries.append({
                "name": fn.get("name", "") or "",
                "description": fn.get("description", "") or "",
            })
        last_attempt_age_s: Optional[float] = None
        if last_attempt > 0:
            last_attempt_age_s = time.time() - last_attempt
        return {
            "state": state,
            "last_error": last_error,
            "last_attempt_age_s": last_attempt_age_s,
            "middleware_connected": mw_connected,
            "tools_cached": len(cache),
            "tools": tool_entries,
        }

    def _ensure_tools_ready(self) -> bool:
        if not CONFIG.enable_tools:
            return False
        if not _HAS_MIDDLEWARE:
            logger.warning("language_middleware not available; "
                           "tools are disabled")
            return False

        if self._mcp_state == "ready" and self._openai_tools_cache:
            return True

        now = time.time()

        if self._mcp_state == "failed":
            if (now - self._mcp_last_attempt
                    < CONFIG.mcp_retry_interval_sec):
                return False

        with self._mcp_init_lock:
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

    def _register_apis(self) -> None:
        @self.server.expose("generate", with_input=True)
        def generate(data: Dict[str, Any], *, _inp) -> Dict[str, Any]:
            return self._handle_generate(data, _inp)

        @self.server.expose("create_session", with_input=True)
        def create_session(data: Dict[str, Any], *, _inp) -> Dict[str, Any]:
            return self._handle_create_session(data, _inp)

        @self.server.expose("close_session", with_input=True)
        def close_session(data: Dict[str, Any], *, _inp) -> Dict[str, Any]:
            return self._handle_close_session(data, _inp)

        @self.server.expose("cancel_session", with_input=True)
        def cancel_session(data: Dict[str, Any], *, _inp) -> Dict[str, Any]:
            return self._handle_cancel_session(data, _inp)

        @self.server.expose("list_sessions", with_input=True)
        def list_sessions(data: Dict[str, Any], *, _inp) -> Dict[str, Any]:
            return self._handle_list_sessions(data, _inp)

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

    @staticmethod
    def _read_caller_uid(inp) -> Optional[bytes]:
        try:
            uid = inp.get_p2p_uid()
        except Exception as e:
            logger.error("Failed to read caller UID from Input: %s", e)
            return None
        if not isinstance(uid, (bytes, bytearray)) or len(uid) != UID_BYTES:
            logger.error(
                "Input handle returned a malformed UID: type=%s len=%s",
                type(uid).__name__,
                len(uid) if hasattr(uid, "__len__") else "?",
            )
            return None
        uid_bytes = bytes(uid)
        if all(b == 0 for b in uid_bytes):
            logger.error("Input handle returned an all-zero UID")
            return None
        return uid_bytes

    def _count_sessions(self) -> int:
        with self._sessions_lock:
            return len(self._sessions)

    def _lookup(self, session_id: str) -> Optional[MultimodalSessionState]:
        with self._sessions_lock:
            return self._sessions.get(session_id)

    def _create(self, client_uid: bytes,
                system_message: str = "") -> MultimodalSessionState:
        sid = str(uuid.uuid4())
        sess = MultimodalSessionState(
            session_id=sid,
            client_uid=client_uid,
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

    def _handle_generate(self, data: Any, inp) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {"code": -1, "error": "generate expects a JSON object"}

        content = data.get("content", "") or ""
        prompt = data.get("prompt", "") or ""
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

        caller_uid = self._read_caller_uid(inp)
        if caller_uid is None:
            return {"code": -1,
                    "error": "Cannot determine caller UID from Input "
                             "handle; the request was not routed "
                             "through the LingoFuse network"}

        try:
            attachments = validate_attachments(attachments_raw)
        except AttachmentError as e:
            return {"code": -1, "error": str(e)}

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

        sess: Optional[MultimodalSessionState] = None
        cancel_ev: Optional[threading.Event] = None
        mode: str

        if session_id:
            sess = self._lookup(session_id)
            if sess is None:
                return {"code": -1,
                        "error": f"Session not found: {session_id}"}
            if sess.client_uid != caller_uid:
                return {"code": -1,
                        "error": "Caller UID does not match the "
                                 "session owner"}
            with sess.lock:
                if sess.running:
                    return {"code": -1,
                            "error": f"Session already running: "
                                     f"{session_id}"}
                sess.client_uid = caller_uid
                sess.running = True
                cancel_ev = threading.Event()
                sess.cancel_event = cancel_ev
            mode = "continue"
        else:
            with self._sessions_lock:
                if len(self._sessions) >= CONFIG.max_sessions:
                    return {"code": -1,
                            "error": f"Session limit reached "
                                     f"({CONFIG.max_sessions})"}
                sid = str(uuid.uuid4())
                sess = MultimodalSessionState(
                    session_id=sid,
                    client_uid=caller_uid,
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
            "generate queued: mode=%s session=%s task=%s uid=%s "
            "attachments=%d response_format=%s",
            mode, sess.session_id, task_id,
            caller_uid.hex()[:8], len(attachments),
            "yes" if "response_format" in options else "no",
        )
        return {
            "code": 0,
            "session_id": sess.session_id,
            "task_id": task_id,
            "mode": mode,
            "expects_tool_results": False,
        }

    def _handle_create_session(self, data: Any, inp) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {"code": -1,
                    "error": "create_session expects a JSON object"}

        caller_uid = self._read_caller_uid(inp)
        if caller_uid is None:
            return {"code": -1,
                    "error": "Cannot determine caller UID from Input "
                             "handle; the request was not routed "
                             "through the LingoFuse network"}

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
                client_uid=caller_uid,
                system_message=system_message,
                max_history=CONFIG.max_history,
                max_history_chars=CONFIG.max_history_chars,
            )
            self._sessions[sid] = sess

        logger.info("Session created: %s (uid=%s, system_message=%d chars)",
                    sess.session_id, caller_uid.hex()[:8],
                    len(system_message))
        return {
            "code": 0,
            "session_id": sess.session_id,
            "client_uid_hex": caller_uid.hex(),
        }

    def _handle_close_session(self, data: Any, inp) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {"code": -1,
                    "error": "close_session expects a JSON object"}
        session_id = data.get("session_id")
        if not session_id or not isinstance(session_id, str):
            return {"code": -1, "error": "Missing session_id"}

        caller_uid = self._read_caller_uid(inp)
        if caller_uid is None:
            return {"code": -1,
                    "error": "Cannot determine caller UID from Input "
                             "handle"}

        sess = self._lookup(session_id)
        if sess is None:
            return {"code": -1,
                    "error": f"Session not found: {session_id}"}
        if sess.client_uid != caller_uid:
            return {"code": -1,
                    "error": "Caller UID does not match the session "
                             "owner"}

        self._drop(session_id)
        sess.request_cancel()
        self._emit(sess, {
            "type": "closed",
            "session_id": sess.session_id,
            "reason": SESSION_CLOSE_REASON_CLIENT,
        })
        logger.info("Session closed: %s", session_id)
        return {"code": 0, "status": "closed"}

    def _handle_cancel_session(self, data: Any, inp) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {"code": -1,
                    "error": "cancel_session expects a JSON object"}
        session_id = data.get("session_id")
        if not session_id or not isinstance(session_id, str):
            return {"code": -1, "error": "Missing session_id"}

        caller_uid = self._read_caller_uid(inp)
        if caller_uid is None:
            return {"code": -1,
                    "error": "Cannot determine caller UID from Input "
                             "handle"}

        sess = self._lookup(session_id)
        if sess is None:
            return {"code": -1,
                    "error": f"Session not found: {session_id}"}
        if sess.client_uid != caller_uid:
            return {"code": -1,
                    "error": "Caller UID does not match the session "
                             "owner"}

        ok = sess.request_cancel()
        logger.debug("Cancel requested for session %s (running=%s)",
                     session_id, ok)
        return {"code": 0,
                "status": "cancel_requested" if ok else "no_active_task"}

    def _handle_list_sessions(self, data: Any, inp) -> Dict[str, Any]:
        caller_uid = self._read_caller_uid(inp)
        if caller_uid is None:
            return {"code": -1,
                    "error": "Cannot determine caller UID from Input "
                             "handle"}

        with self._sessions_lock:
            sessions = list(self._sessions.values())
        summaries = [
            s.to_summary() for s in sessions
            if s.client_uid == caller_uid
        ]
        return {"code": 0, "sessions": summaries,
                "count": len(summaries)}

    def _handle_set_system_message(self, data: Any) -> Dict[str, Any]:
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

        with self._model_lock:
            resolved = self._resolved_model

        return {
            "code": 0,
            "status": "ok",
            "server_kind": SERVER_KIND,
            "backend_url": CONFIG.backend_url,
            "backend_model": resolved or CONFIG.backend_model,
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
        user_text = content + ("\n\n" + prompt if prompt else "")

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

        history_text = self._build_history_text(user_text, attachments)

        model = self.current_model() or CONFIG.backend_model
        if not model:
            model = self.resolve_model()

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

        total_tool_calls = 0
        total_tool_result_chars = 0
        force_final_round = False

        response_format_active = "response_format" in options

        try:
            for round_idx in range(CONFIG.max_tool_rounds):
                if cancel_event.is_set():
                    break

                history = sess.snapshot()
                messages: List[Dict[str, Any]] = []
                if sess.system_message:
                    messages.append({
                        "role": "system",
                        "content": sess.system_message,
                    })
                messages.extend(history)

                if round_idx == 0:
                    messages.append({
                        "role": "user",
                        "content": backend_content_r0,
                    })

                is_final_round = (
                    force_final_round
                    or round_idx == CONFIG.max_tool_rounds - 1
                )

                round_options = dict(options)

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

                if not tool_calls:
                    if round_idx == 0:
                        sess.add_user(history_text)
                    sess.add_assistant("".join(answer_pieces))
                    logger.debug(
                        "Task %s round %d: final answer (%d chars)",
                        task_id, round_idx,
                        len("".join(answer_pieces)),
                    )
                    break

                if round_idx == 0:
                    sess.add_user(history_text)

                if is_final_round:
                    logger.warning(
                        "Task %s: model returned tool_calls on the "
                        "final round despite tools not being injected; "
                        "recording text and stopping", task_id,
                    )
                    sess.add_assistant("".join(answer_pieces))
                    break

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

                    args = _parse_json_with_repair(
                        args_str,
                        source="llm_proxy_tool.tool_arguments",
                        fallback={},
                    )
                    if not isinstance(args, dict):
                        args = {}

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

                    if len(result_content) > CONFIG.max_tool_result_chars:
                        result_content = (
                            result_content[:CONFIG.max_tool_result_chars]
                            + "...(truncated)"
                        )

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

    def _emit(self, sess: MultimodalSessionState,
              payload: Dict[str, Any]) -> None:
        hnd = None
        try:
            hnd = DataHandle(CONFIG.notify_api)
            write_json(hnd.raw, payload)
            LF_UID_Sequenced_Notify(
                to_tp2p_uid(sess.client_uid), hnd.raw
            )
        except Exception as e:
            logger.debug(
                "Notify to uid=%s failed (%s): %s",
                sess.client_uid.hex()[:8], payload.get("type"), e,
            )
        finally:
            if hnd is not None:
                try:
                    hnd.free()
                except Exception:
                    pass

    def _watchdog_loop(self) -> None:
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

    def start(self) -> None:
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
                    "succeeds. Make sure the agent service and agent API "
                    "provider are running on ipc:agent."
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
        self._shutdown.set()
        try:
            with self._sessions_lock:
                sessions = list(self._sessions.values())
            for sess in sessions:
                sess.request_cancel()
        except Exception as e:
            logger.warning("Session cancellation during shutdown: %s", e)

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

    def close_session_console(self, session_id: str) -> str:
        sess = self._lookup(session_id)
        if sess is None:
            return f"session not found: {session_id}"
        self._drop(session_id)
        sess.request_cancel()
        self._emit(sess, {
            "type": "closed",
            "session_id": sess.session_id,
            "reason": SESSION_CLOSE_REASON_CONSOLE,
        })
        logger.info("Session %s closed via console", session_id)
        return f"session closed: {session_id}"

    def cancel_session_console(self, session_id: str) -> str:
        sess = self._lookup(session_id)
        if sess is None:
            return f"session not found: {session_id}"
        ok = sess.request_cancel()
        if not ok:
            return f"no active task for session: {session_id}"
        logger.info("Session %s cancel requested via console", session_id)
        return f"cancel requested: {session_id}"


def print_service_banner() -> None:
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
                ("Routing model", "UID-addressed (v4.0)"),
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


_SHUTDOWN = threading.Event()
_console_stop_event = threading.Event()
_console_thread: Optional[threading.Thread] = None
_config_lock = threading.RLock()

_MUTABLE_FIELDS = {
    'session_timeout':              'int > 0 (seconds)',
    'max_sessions':                 'int > 0',
    'max_history':                  'int >= 2',
    'max_history_chars':            'int > 0',
    'vision':                       'bool',
    'mcp_retry_interval_sec':       'float >= 0 (seconds)',
    'max_tool_rounds':              'int >= 1',
    'max_total_tool_calls':         'int >= 1',
    'max_tools_per_round':          'int >= 1',
    'max_tool_result_chars':        'int >= 64',
    'max_total_tool_result_chars':  'int >= 64',
}


def _parse_bool(value: str) -> bool:
    v = value.strip().lower()
    if v in ('1', 'true', 'yes', 'on'):
        return True
    if v in ('0', 'false', 'no', 'off'):
        return False
    raise ValueError(
        f"expected one of true/false, 1/0, yes/no, on/off; got {value!r}"
    )


def _console_cmd_help(_service) -> str:
    return "\n".join([
        "Available commands:",
        "  help / ? / h                 show this help",
        "  status / show / info         show configuration and runtime stats",
        "  sessions / list              list all active sessions",
        "  close <sid>                  close a session (cancels in-flight run)",
        "  cancel <sid>                 cancel the in-flight generation",
        "  model                        show the current backend model",
        "  model refresh                force a backend model re-discovery",
        "  mcp / tools                  show MCP tool discovery status",
        "  health                       print the health JSON",
        "  set <key> <value>            change a mutable field",
        "  debug on|off|toggle          toggle verbose logger output",
        "  debug:<text>                 dispatch a C4 console command",
        "  quit / exit / q              stop the service and exit",
        "",
        "Mutable fields (set <key> <value>):",
        "  session_timeout              int > 0 (seconds)",
        "  max_sessions                 int > 0",
        "  max_history                  int >= 2",
        "  max_history_chars            int > 0",
        "  vision                       bool",
        "  mcp_retry_interval_sec       float >= 0",
        "  max_tool_rounds              int >= 1",
        "  max_total_tool_calls         int >= 1",
        "  max_tools_per_round          int >= 1",
        "  max_tool_result_chars        int >= 64",
        "  max_total_tool_result_chars  int >= 64",
        "",
        "FROZEN fields (require process restart to change):",
        "  endpoint, app_name, notify_api, backend_url, backend_model,",
        "  backend_key, backend_auth_header, backend_auth_scheme,",
        "  backend_extra_headers, backend_timeout (bound to the HTTP",
        "  client), enable_tools (switching False->True at runtime may",
        "  trigger a LanguageMiddleware reconnect that resets the",
        "  LingoFuse main thread), mcp_endpoint, mcp_timeout_ms,",
        "  mcp_reg_agent_app, mcp_tool_provider_app, mcp_agent_main_api,",
        "  mcp_agent_log_api (all bound to the middleware singleton),",
        "  log_level (use 'debug' to change verbosity).",
    ])


def _console_cmd_status(service) -> str:
    with service._sessions_lock:
        total = len(service._sessions)
        idle = sum(1 for s in service._sessions.values()
                   if not s.running)
        running = total - idle
    watchdog_alive = (
        any(t.name == "llm-proxy-tool-watchdog" and t.is_alive()
            for t in threading.enumerate())
    )
    model = service.current_model() or CONFIG.backend_model or "(not resolved)"
    with service._model_lock:
        resolved = service._resolved_model
    return "\n".join([
        "=== LLM Tool Bridge (LTB) configuration ===",
        f"  endpoint           : {CONFIG.endpoint}",
        f"  app name           : {CONFIG.app_name}",
        f"  notify API         : {CONFIG.notify_api}",
        f"  server kind        : {SERVER_KIND}",
        f"  backend URL        : {CONFIG.backend_url}",
        f"  backend model      : {model}",
        f"  backend timeout    : {CONFIG.backend_timeout} s",
        f"  backend auth header: {CONFIG.backend_auth_header}",
        f"  backend auth scheme: {CONFIG.backend_auth_scheme or '(none)'}",
        f"  backend extra hdrs : {len(CONFIG.backend_extra_headers)}",
        f"  vision             : {CONFIG.vision}",
        f"  session timeout    : {CONFIG.session_timeout} s",
        f"  max sessions       : {CONFIG.max_sessions}",
        f"  max history        : {CONFIG.max_history}",
        f"  max history chars  : {CONFIG.max_history_chars}",
        f"  log level          : {CONFIG.log_level}",
        "",
        "=== LTB tool config ===",
        f"  tools enabled      : {CONFIG.enable_tools}",
        f"  mcp endpoint       : {CONFIG.mcp_endpoint}",
        f"  mcp timeout (ms)   : {CONFIG.mcp_timeout_ms}",
        f"  mcp reg agent app  : {CONFIG.mcp_reg_agent_app}",
        f"  mcp tool provider  : {CONFIG.mcp_tool_provider_app}",
        f"  mcp retry interval : {CONFIG.mcp_retry_interval_sec} s",
        f"  max tool rounds    : {CONFIG.max_tool_rounds}",
        f"  max total calls    : {CONFIG.max_total_tool_calls}",
        f"  max tools/round    : {CONFIG.max_tools_per_round}",
        f"  max result chars   : {CONFIG.max_tool_result_chars}",
        f"  max total chars    : {CONFIG.max_total_tool_result_chars}",
        "",
        "=== Runtime stats ===",
        f"  sessions total     : {total}",
        f"  sessions idle      : {idle}",
        f"  sessions running   : {running}",
        f"  watchdog alive     : {watchdog_alive}",
        f"  mcp state          : {service._mcp_state}",
        f"  mcp tools cached   : {len(service._openai_tools_cache)}",
        "",
        f"Mutable at runtime  : {', '.join(sorted(_MUTABLE_FIELDS))}",
        "Frozen after startup: endpoint, app_name, notify_api,",
        "                      backend_url, backend_model, backend_key,",
        "                      backend_auth_header, backend_auth_scheme,",
        "                      backend_extra_headers, backend_timeout,",
        "                      enable_tools, mcp_endpoint, mcp_timeout_ms,",
        "                      mcp_reg_agent_app, mcp_tool_provider_app,",
        "                      mcp_agent_main_api, mcp_agent_log_api,",
        "                      log_level",
        f"  (resolved model internally: {resolved or '(empty)'})",
    ])


def _console_cmd_sessions(service) -> str:
    with service._sessions_lock:
        sessions = list(service._sessions.values())
    if not sessions:
        return "no sessions"
    now = time.time()
    lines = [
        f"{'session_id':36s}  {'uid[:8]':8s}  {'status':8s}  "
        f"{'msgs':>5s}  {'idle_s':>8s}"
    ]
    for s in sessions:
        status = "running" if s.running else "idle"
        idle_s = now - s.last_active
        lines.append(
            f"{s.session_id:36s}  {s.client_uid.hex()[:8]:8s}  "
            f"{status:8s}  {len(s.messages):5d}  {idle_s:8.1f}"
        )
    return "\n".join(lines)


def _console_cmd_model(service, arg: str) -> str:
    if arg.strip().lower() in ('refresh', 'reload', 're-discover'):
        try:
            new_model = service.refresh_model()
        except Exception as e:
            logger.exception("model refresh failed")
            return f"model refresh failed: {e}"
        return f"backend model re-discovered: {new_model}"
    model = service.current_model() or CONFIG.backend_model or "(not resolved)"
    return (f"backend model: {model}\n"
            f"hint: 'model refresh' forces re-discovery")


def _console_cmd_mcp(service) -> str:
    snap = service.mcp_snapshot()
    lines = [
        "=== MCP tool discovery status ===",
        f"  state                : {snap['state']}",
        f"  middleware connected : {snap['middleware_connected']}",
        f"  last_error           : {snap['last_error'] or '(none)'}",
    ]
    age = snap.get("last_attempt_age_s")
    if age is None:
        lines.append("  last_attempt_age_s   : (never attempted)")
    else:
        lines.append(f"  last_attempt_age_s   : {age:.1f}")
    lines.append(f"  tools_cached         : {snap['tools_cached']}")
    lines.extend([
        f"  mcp endpoint         : {CONFIG.mcp_endpoint}",
        f"  mcp reg agent app    : {CONFIG.mcp_reg_agent_app}",
        f"  mcp tool provider    : {CONFIG.mcp_tool_provider_app}",
        f"  mcp timeout (ms)     : {CONFIG.mcp_timeout_ms}",
        f"  mcp retry interval   : {CONFIG.mcp_retry_interval_sec} s",
    ])
    if snap["tools"]:
        lines.append("  ---")
        lines.append("  Cached tools:")
        for t in snap["tools"]:
            name = t.get("name", "") or "(unnamed)"
            desc = t.get("description", "") or ""
            if desc:
                lines.append(f"    - {name}: {desc}")
            else:
                lines.append(f"    - {name}")
    else:
        lines.append("  (no tools cached)")
    lines.extend([
        "  ---",
        "  NOTE: MCP-related fields are FROZEN at startup. Changing",
        "        mcp_endpoint / mcp_reg_agent_app / mcp_tool_provider_app",
        "        requires a process restart. Do NOT attempt to force a",
        "        reconnect while this service is running: LanguageMiddleware",
        "        is a process singleton, and reconnecting it resets the",
        "        LingoFuse main thread, which tears down the Server's",
        "        own client connection.",
    ])
    return "\n".join(lines)


def _console_cmd_health(service) -> str:
    try:
        result = service._handle_health(None)
    except Exception as e:
        logger.exception("health() failed")
        return f"health check failed: {e}"
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


def _console_cmd_set(_service, tokens) -> str:
    if len(tokens) < 3:
        return "usage: set <key> <value>"
    key = tokens[1]
    value = ' '.join(tokens[2:])
    if key not in _MUTABLE_FIELDS:
        return (f"'{key}' cannot be changed at runtime "
                f"(frozen after startup); try 'status' for the full list")
    try:
        if key in ('session_timeout', 'max_sessions',
                   'max_history', 'max_history_chars',
                   'max_tool_rounds', 'max_total_tool_calls',
                   'max_tools_per_round', 'max_tool_result_chars',
                   'max_total_tool_result_chars'):
            n = int(value)
            if key == 'max_history':
                if n < 2:
                    raise ValueError("must be >= 2")
            elif key in ('max_tool_result_chars',
                         'max_total_tool_result_chars'):
                if n < 64:
                    raise ValueError("must be >= 64")
            else:
                if n <= 0:
                    raise ValueError("must be a positive integer")
            with _config_lock:
                setattr(CONFIG, key, n)
        elif key == 'vision':
            b = _parse_bool(value)
            with _config_lock:
                CONFIG.vision = b
        elif key == 'mcp_retry_interval_sec':
            f = float(value)
            if f < 0:
                raise ValueError("must be >= 0")
            with _config_lock:
                CONFIG.mcp_retry_interval_sec = f
        else:
            return f"internal error: no handler for '{key}'"
    except (ValueError, TypeError) as e:
        return f"invalid value for {key!r}: {e}"
    return f"{key} = {getattr(CONFIG, key)!r}"


def _console_cmd_debug_toggle(arg: str) -> str:
    v = arg.strip().lower()
    if v in ('on', 'true', '1', 'yes'):
        target = logging.DEBUG
    elif v in ('off', 'false', '0', 'no'):
        target = logging.INFO
    elif v in ('toggle', ''):
        target = (logging.INFO if logger.level == logging.DEBUG
                  else logging.DEBUG)
    else:
        return (f"usage: debug on|off|toggle   "
                f"(current: {logging.getLevelName(logger.level)})")
    with _config_lock:
        logger.setLevel(target)
        CONFIG.log_level = logging.getLevelName(target)
    return f"logger level = {logging.getLevelName(target)}"


def _console_cmd_debug_c4(payload: str) -> str:
    payload = payload.strip()
    if not payload:
        return ("usage: debug:<c4-console-command>   "
                "(e.g. debug:service, debug:tunnel, "
                "debug:HPC_Thread_Info)")
    try:
        set_opt_debug(payload)
    except Exception as e:
        logger.exception("set_opt_debug(%r) failed", payload)
        return f"set_opt_debug failed: {e}"
    return f"debug command dispatched: {payload!r}"


def _console_cmd_quit(_service) -> None:
    _console_stop_event.set()
    _SHUTDOWN.set()
    print("Shutting down...", flush=True)


def _dispatch_console_command(service, line: str) -> Optional[str]:
    line = line.strip()
    if not line:
        return None
    if line.lower().startswith('debug:'):
        return _console_cmd_debug_c4(line[len('debug:'):])
    try:
        tokens = shlex.split(line)
    except ValueError as e:
        return f"parse error: {e}"
    if not tokens:
        return None
    cmd = tokens[0].lower()
    if cmd in ('help', '?', 'h'):
        return _console_cmd_help(service)
    if cmd in ('status', 'show', 'info'):
        return _console_cmd_status(service)
    if cmd in ('sessions', 'list'):
        return _console_cmd_sessions(service)
    if cmd == 'close':
        sid = tokens[1] if len(tokens) > 1 else ''
        if not sid:
            return "usage: close <session_id>"
        try:
            return service.close_session_console(sid)
        except Exception as e:
            logger.exception("close_session_console failed")
            return f"close failed: {e}"
    if cmd == 'cancel':
        sid = tokens[1] if len(tokens) > 1 else ''
        if not sid:
            return "usage: cancel <session_id>"
        try:
            return service.cancel_session_console(sid)
        except Exception as e:
            logger.exception("cancel_session_console failed")
            return f"cancel failed: {e}"
    if cmd == 'model':
        arg = ' '.join(tokens[1:])
        try:
            return _console_cmd_model(service, arg)
        except Exception as e:
            logger.exception("model command failed")
            return f"model failed: {e}"
    if cmd in ('mcp', 'tools'):
        try:
            return _console_cmd_mcp(service)
        except Exception as e:
            logger.exception("mcp command failed")
            return f"mcp failed: {e}"
    if cmd == 'health':
        return _console_cmd_health(service)
    if cmd == 'set':
        return _console_cmd_set(service, tokens)
    if cmd == 'debug':
        arg = ' '.join(tokens[1:])
        return _console_cmd_debug_toggle(arg)
    if cmd in ('quit', 'exit', 'q'):
        _console_cmd_quit(service)
        return None
    return f"unknown command: {tokens[0]!r}; type 'help' for the list"


def _console_loop(service) -> None:
    while not _console_stop_event.is_set():
        try:
            line = sys.stdin.readline()
        except (EOFError, KeyboardInterrupt):
            break
        if line == '':
            logger.info("Console: EOF on stdin, command reader exiting")
            break
        if _console_stop_event.is_set():
            break
        try:
            response = _dispatch_console_command(service, line)
        except Exception as e:
            logger.exception("Console: command dispatch failed")
            response = f"command failed: {e}"
        if response:
            try:
                print(response, flush=True)
            except Exception:
                logger.exception("Console: failed to write response")


def _start_console(service) -> None:
    global _console_thread
    if not sys.stdin.isatty():
        logger.info("Console: stdin is not a TTY, command reader disabled")
        return
    print(
        "Interactive console ready. Type 'help' for commands, "
        "'quit' to exit.",
        flush=True,
    )
    _console_thread = threading.Thread(
        target=_console_loop,
        args=(service,),
        name="LLMProxyToolConsole",
        daemon=True,
    )
    _console_thread.start()
    logger.info("Console: interactive command reader started")


def _install_signal_handlers() -> None:
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


def parse_args():
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
        "Runtime console:\n"
        "  After the service is running, if stdin is a TTY, an "
        "interactive console is started. Type 'help' at the 'ltb>' "
        "prompt to see commands: help, status, sessions, close <sid>, "
        "cancel <sid>, model [refresh], mcp, health, set <key> <value>, "
        "debug on|off|toggle, debug:<text>, quit.\n"
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
        "Routing model (v4.0 - UID-only):\n"
        "  A session is addressed by the client's 32-byte LingoFuse\n"
        "  session UID. The UID is injected by the framework into the\n"
        "  header of every inbound request, so LTB learns it from the\n"
        "  Input DataHandle of the first create_session or generate\n"
        "  call, and refreshes it on every subsequent call. All stream\n"
        "  events are emitted with LF_UID_Sequenced_Notify. There is\n"
        "  no client_name field.\n"
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

    parser.add_argument(
        "--log-level",
        default=os.environ.get("LLM_PROXY_LOG_LEVEL",
                               DEFAULT_LOG_LEVEL).upper(),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log verbosity. Default: INFO",
    )

    return parser.parse_args()


def _init_global_config(args) -> None:
    CONFIG.endpoint = args.endpoint
    CONFIG.app_name = args.app_name
    CONFIG.notify_api = args.notify_api

    CONFIG.backend_url = args.backend_url.rstrip("/")
    CONFIG.backend_model = args.backend_model
    CONFIG.backend_key = args.backend_key
    CONFIG.backend_key_file = args.backend_key_file
    CONFIG.backend_auth_header = args.backend_auth_header
    CONFIG.backend_auth_scheme = args.backend_auth_scheme
    CONFIG.backend_timeout = args.backend_timeout

    CONFIG.max_history = max(2, args.max_history)
    CONFIG.max_history_chars = max(1000, args.max_history_chars)
    CONFIG.max_sessions = max(1, args.max_sessions)
    CONFIG.session_timeout = max(10, args.session_timeout)

    CONFIG.vision = bool(args.vision)

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

    CONFIG.log_level = args.log_level

    try:
        CONFIG.backend_extra_headers = parse_extra_headers(
            args.backend_extra_headers
        )
    except ValueError as e:
        print(f"[FATAL] {e}", file=sys.stderr)
        sys.exit(1)

    if CONFIG.backend_key_file:
        try:
            CONFIG.backend_key = load_key_from_file(CONFIG.backend_key_file)
        except ValueError as e:
            print(f"[FATAL] {e}", file=sys.stderr)
            sys.exit(1)


def main() -> int:
    args = parse_args()
    _init_global_config(args)
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
        logger.info(
            "Press Ctrl+C to stop, or type 'help' at the console."
        )
        _start_console(service)
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