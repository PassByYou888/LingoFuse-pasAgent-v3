#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
llm_proxy.py - LingoFuse proxy forwarding to OpenAI-compatible backends.

Role
----
A LingoFuse SERVICE that registers as LLM_Service on ipc:llm_service and
forwards each `generate` request to an OpenAI-compatible HTTP backend
(LM Studio, Ollama, vLLM, DeepSeek, OpenRouter, Azure OpenAI, ...).
Streaming chunks are relayed back to LingoFuse clients through
LF_Sequenced_Notify.

New in this revision
--------------------
* Shared code (frozen detection, JSON helpers, SSE client, session
  state, logging, banner, capability matrix, option filtering) moved
  to the llm_common package. This file now contains only the proxy
  logic itself.
* Attachment support: a `generate` request may carry a list of text
  and image attachments. Text attachments are merged into the user
  message; image attachments are forwarded as OpenAI multi-modal
  content parts, provided --vision is enabled.
* Structured Output support: a `generate` request may carry
  options.response_format (an OpenAI Structured Outputs object such as
  {"type": "json_schema", "json_schema": {...}} or the simpler
  {"type": "json_object"}). llm_proxy does not interpret this field; it
  is whitelisted as a passthrough option and forwarded verbatim to the
  backend. The backend is responsible for enforcing the schema. This is
  what allows a vision client to receive a detector-style bounding-box
  JSON document from a multimodal model instead of free-form prose.

Backend transport
-----------------
Streaming uses llm_common.sse_client.OpenAIStreamClient, which is
built on Python's standard-library http.client. See that module for
the rationale behind not using requests.

set_system_message is not supported
-----------------------------------
The proxy is a STATELESS FORWARDER. Each session captures its system
message at creation time and rebuilds the messages array before every
backend call. Changing the system message of an existing session
would require the backend to re-process the entire history from a
different context, which is impossible without invalidating the
model's KV cache. `set_system_message` therefore returns a
deterministic unsupported response; the capability matrix advertises
this fact so that clients can short-circuit locally.

Attachment policy
-----------------
* Text attachments are merged into the text part of the user message
  and stay in history (their size is bounded).
* Image attachments are emitted as `image_url` parts for the current
  turn only. History stores a short `[image: name]` placeholder so
  that long conversations do not accumulate megabytes of base64.
* If the client sends an image but --vision is disabled, the whole
  request is rejected with a clear error. Silent dropping would give
  the user a false impression that the model saw the image.

Structured Output policy
------------------------
* options.response_format is whitelisted as a passthrough field. The
  proxy does not validate its contents beyond a top-level dict type
  check (see _handle_generate).
* The field is forwarded as-is to the backend on every streaming call.
  Backends that do not implement Structured Outputs typically ignore
  the field; backends that do (LM Studio, Ollama, vLLM, ...) enforce
  it.
* The proxy does not currently mutate or strip response_format on
  later turns of a multi-turn conversation. For a single-shot
  detection request this is exactly the desired behaviour.

All comments and log messages are in English.
"""

import logging
import os
import sys
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from lingofuse import Server, set_option, check_app
from lingofuse.core import DataHandle
from lingofuse._lf_native import LF_Sequenced_Notify, LF_WriteBuffer

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
    jdump,
    load_key_from_file,
    parse_extra_headers,
)
from llm_common.logging_setup import setup_logging
from llm_common.option_sanitizer import sanitize_options
from llm_common.runtime import (
    get_example_invocation,
    get_invocation_name,
)
from llm_common.session_base import SessionState
from llm_common.sse_client import OpenAIStreamClient


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
DEFAULT_MAX_SESSIONS = 1024
DEFAULT_SESSION_TIMEOUT = 1800

DEFAULT_LOG_LEVEL = "INFO"

# Image forwarding is off by default: the operator must confirm that
# the backend is actually a VLM.
DEFAULT_VISION = False

# Server kind reported through the capability matrix.
SERVER_KIND = SERVER_KIND_PROXY

# Maximum length for a client_name, matching llm_service.py. Enforced
# on new session creation to prevent a malicious client from sending a
# multi-megabyte name that would bloat the sequenced-notify routing
# table and the session registry.
MAX_CLIENT_NAME_LEN = 512


# ----------------------------------------------------------------------
# Logger
# ----------------------------------------------------------------------
#
# The logger name is fixed (not derived from the program name) so that
# log lines remain stable when the same code runs from source and from
# a frozen exe. setup_logging() configures the root logger; this
# child logger inherits from it.

logger = logging.getLogger("llm_proxy")


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
        self.max_sessions: int = DEFAULT_MAX_SESSIONS
        self.session_timeout: int = DEFAULT_SESSION_TIMEOUT

        # ---- Attachment handling ----
        # When False, image attachments cause the whole generate
        # request to be rejected. Text attachments are always allowed.
        self.vision: bool = DEFAULT_VISION

        # ---- Logging ----
        self.log_level: str = DEFAULT_LOG_LEVEL


CONFIG = ProxyConfig()


# ----------------------------------------------------------------------
# Service
# ----------------------------------------------------------------------

class LLMProxyService:
    """
    The proxy service.

    Sits between LingoFuse clients (LLM_Service on ipc:llm_service) and
    an OpenAI-compatible HTTP backend. Tool calls returned by the
    backend are passed through to the client as ordinary chunks: this
    proxy does not execute tools. Use llm_proxy_tool.py for server-side
    tool execution.
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, SessionState] = {}
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

        self.server = Server(
            CONFIG.app_name,
            "LingoFuse LLM Proxy - forwards to an OpenAI-compatible backend",
        )
        self._register_apis()

    # ------------------------------------------------------------------
    # Capability matrix (computed on demand)
    # ------------------------------------------------------------------
    #
    # The matrix depends on CONFIG.vision, which is only final after
    # _init_global_config() has run. Computing it lazily avoids the
    # "module-level constant is stale" trap.

    def _capabilities(self) -> Dict[str, int]:
        """Return this server's current capability matrix."""
        return build_capabilities(
            set_system_message=0,
            attachments=1,
            vision=1 if CONFIG.vision else 0,
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

        # Do NOT cache the failure. Returning "unknown" per call lets
        # the next generate() trigger another discovery attempt.
        logger.warning(
            "Could not auto-discover backend model; "
            "set LLM_PROXY_BACKEND_MODEL or --backend-model. "
            "Will retry on the next request."
        )
        return "unknown"

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

    def _lookup(self, session_id: str) -> Optional[SessionState]:
        with self._sessions_lock:
            return self._sessions.get(session_id)

    def _create(self, client_name: str,
                system_message: str = "") -> SessionState:
        sid = str(uuid.uuid4())
        sess = SessionState(
            session_id=sid,
            client_name=client_name,
            system_message=system_message,
            max_history=CONFIG.max_history,
        )
        with self._sessions_lock:
            self._sessions[sid] = sess
        return sess

    def _drop(self, session_id: str) -> Optional[SessionState]:
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
            options         (object, optional)
            attachments     (array, optional)

        Structured Output:
            options.response_format may be supplied by the client. It is
            whitelisted as a passthrough field (see the sanitize_options
            call below) and forwarded verbatim to the backend. The
            proxy does not inspect its contents beyond a top-level
            dict type check.

        Returns:
            {code, session_id, task_id, mode}
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
        #
        # Strict policy: never silently drop an image the user asked
        # to send. Return a clear error so the client can surface it.
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

        # ---- Filter options ----
        #
        # scalar_specs defaults to COMMON_SCALAR_SPECS. We add a
        # single passthrough key here:
        #
        #   - "response_format": Structured Output control.
        #     We only require it to be a JSON object at the top level.
        #     Anything more specific (json_schema vs json_object,
        #     schema contents, strict flag) is the backend's job to
        #     validate. This keeps the proxy agnostic to the schema
        #     language version and to the specific backend's support
        #     matrix.
        #
        # Note: scalar_specs is intentionally left at its default so
        # that this file remains visually aligned with llm_proxy_tool.py,
        # which passes COMMON_SCALAR_SPECS explicitly. Both files end
        # up with the same whitelist.
        options = sanitize_options(
            options_raw,
            passthrough={
                "response_format": lambda v: isinstance(v, dict),
            },
            debug_log=(
                logger.debug
                if logger.isEnabledFor(logging.DEBUG)
                else None
            ),
        )

        # ---- Resolve or create the session (atomic occupancy) ----
        #
        # The previous implementation checked `sess.running` outside any
        # lock and only set it inside the worker thread's begin_run().
        # Two generate() calls for the same session could therefore both
        # pass the check and start two workers concurrently. We now
        # check and set the running state atomically under sess.lock,
        # mirroring the behaviour of llm_proxy_tool.py.
        sess: Optional[SessionState] = None
        cancel_event: Optional[threading.Event] = None
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
                cancel_event = threading.Event()
                sess.cancel_event = cancel_event
                sess.running = True
            mode = "continue"
        else:
            if not client_name or not isinstance(client_name, str):
                return {"code": -1,
                        "error": "Missing client_name for new session"}
            if len(client_name) > MAX_CLIENT_NAME_LEN:
                return {"code": -1,
                        "error": f"client_name exceeds "
                                 f"{MAX_CLIENT_NAME_LEN} chars"}
            if self._count_sessions() >= CONFIG.max_sessions:
                return {"code": -1,
                        "error": f"Session limit reached "
                                 f"({CONFIG.max_sessions})"}
            sess = self._create(client_name)
            with sess.lock:
                cancel_event = threading.Event()
                sess.cancel_event = cancel_event
                sess.running = True
            mode = "new"

        task_id = str(uuid.uuid4())
        sess.touch()

        try:
            t = threading.Thread(
                target=self._run_generation,
                args=(sess, task_id, content, prompt,
                      options, attachments, cancel_event),
                name=f"llm-proxy-{task_id[:8]}",
                daemon=True,
            )
            t.start()
        except Exception as e:
            logger.error("Failed to start worker thread: %s", e)
            sess.end_run()
            return {"code": -1, "error": f"Failed to start task: {e}"}

        logger.info(
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
        if self._count_sessions() >= CONFIG.max_sessions:
            return {"code": -1,
                    "error": f"Session limit reached "
                             f"({CONFIG.max_sessions})"}
        sess = self._create(client_name, system_message)
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
        logger.info("Cancel requested for session %s (running=%s)",
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

        The proxy is a stateless forwarder: each session captures its
        system_message at creation and rebuilds the messages array
        before every backend call. Changing the system message of an
        existing session would require reprocessing the entire history
        from the backend's perspective, which cannot be done safely
        without invalidating the model's KV cache.

        The capability matrix advertises this fact (value 0 for
        `set_system_message`), so a well-behaved client will not even
        call this API. It is implemented here anyway, as a defensive
        fallback for older clients that do not consult the matrix.
        """
        logger.info("set_system_message rejected: not supported by "
                    "llm_proxy (stateless forwarder)")
        return {
            "code": -1,
            "status": "unsupported",
            "error": (
                "set_system_message is not supported by llm_proxy.\n"
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
            "api_capabilities": self._capabilities(),
        }

    # ------------------------------------------------------------------
    # Core: generation
    # ------------------------------------------------------------------

    def _run_generation(
        self,
        sess: SessionState,
        task_id: str,
        content: str,
        prompt: str,
        options: Dict[str, Any],
        attachments: List[Attachment],
        cancel_event: threading.Event,
    ) -> None:
        """
        Forward one generation request to the backend and relay the
        stream back to the client.

        Attachment handling
        -------------------
        * `backend_content` is what the backend sees: the full text
          plus text attachments, plus any image parts.
        * `history_text` is what gets stored in the session: the full
          text plus text attachments, with images replaced by a short
          placeholder. This keeps multi-turn history from growing
          without bound when images are involved.

        Structured Output handling
        --------------------------
        * `options` may contain a "response_format" entry, already
          whitelisted by _handle_generate. It is passed straight to
          stream_chat, which forwards it verbatim to the backend.
        * The proxy does not modify or clear this field between turns
          of the same session; multi-turn Structured Output is the
          caller's responsibility.

        Session occupancy
        -----------------
        `sess.running` and `sess.cancel_event` are set by the caller
        (`_handle_generate`) BEFORE this method runs, under
        `sess.lock`, so no two workers can run on the same session.
        This method must always clear them via `sess.end_run()` in its
        `finally` block, even on an unexpected exception.
        """
        # The caller already atomically set running=True and created
        # the cancel event under sess.lock. We must NOT call
        # sess.begin_run() here: doing so would create a second event
        # and overwrite the one the caller already recorded.
        user_text = content + ("\n\n" + prompt if prompt else "")

        # ---- Build the content sent to the backend ----
        try:
            backend_content = build_user_content(
                text=user_text,
                attachments=attachments,
                vision_enabled=CONFIG.vision,
            )
        except AttachmentError as e:
            # Should be unreachable: _handle_generate already checked
            # vision and validated the attachments. Defensive only.
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
            return

        # ---- Build the text stored in the session history ----
        history_text = self._build_history_text(user_text, attachments)

        # ---- Assemble the messages array ----
        history = sess.snapshot()
        messages: List[Dict[str, Any]] = []
        if sess.system_message:
            messages.append({
                "role": "system",
                "content": sess.system_message,
            })
        messages.extend(history)
        messages.append({"role": "user", "content": backend_content})

        model = self._resolved_model or CONFIG.backend_model
        if not model:
            model = self.resolve_model()

        answer_pieces: List[str] = []
        think_pieces: List[str] = []
        error_message: Optional[str] = None

        # The whole body is wrapped in try/finally so that
        # sess.end_run() is always called, even if add_user() or
        # add_assistant() raises (e.g. out of memory). Without this,
        # a session could be left permanently in the running state
        # and all subsequent generate() calls would be rejected.
        try:
            try:
                for delta in self._backend.stream_chat(
                    model, messages, options,
                    cancel_event=cancel_event,
                ):
                    if cancel_event.is_set():
                        break
                    if "reasoning_content" in delta:
                        text = delta["reasoning_content"]
                        think_pieces.append(text)
                        self._emit(sess, {
                            "type": "think",
                            "session_id": sess.session_id,
                            "text": text,
                        })
                    if "content" in delta:
                        text = delta["content"]
                        answer_pieces.append(text)
                        self._emit(sess, {
                            "type": "chunk",
                            "session_id": sess.session_id,
                            "text": text,
                        })
                    # tool_calls events are ignored: this proxy does
                    # not execute tools.
            except Exception as e:
                error_message = str(e)
                logger.error("Task %s backend error: %s",
                             task_id, error_message)
                self._emit(sess, {
                    "type": "error",
                    "session_id": sess.session_id,
                    "message": error_message,
                })

            if error_message is None:
                sess.add_user(history_text)
                sess.add_assistant("".join(answer_pieces))

            if cancel_event.is_set():
                self._emit(sess, {
                    "type": "finish",
                    "session_id": sess.session_id,
                    "reason": "cancelled",
                })
            elif error_message is not None:
                self._emit(sess, {
                    "type": "finish",
                    "session_id": sess.session_id,
                    "reason": "error",
                })
            else:
                self._emit(sess, {
                    "type": "finish",
                    "session_id": sess.session_id,
                    "reason": "stop",
                })

            logger.info(
                "Task %s finished: session=%s chars=%d think_chars=%d "
                "attachments=%d response_format=%s cancelled=%s error=%s",
                task_id, sess.session_id,
                len("".join(answer_pieces)),
                len("".join(think_pieces)),
                len(attachments),
                "yes" if "response_format" in options else "no",
                cancel_event.is_set(),
                error_message is not None,
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
        """
        Build the text stored in the session history.

        Text attachments are kept verbatim (their size is bounded).
        Images are replaced by a short placeholder so that multi-turn
        conversations do not accumulate megabytes of base64.

        The format mirrors what the backend sees for text content, so
        that the model's view of history is consistent across turns.
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

    def _emit(self, sess: SessionState,
              payload: Dict[str, Any]) -> None:
        """
        Send one structured JSON event to the client via the notify API.

        Payload is serialized with ensure_ascii=False so that
        non-ASCII content (Chinese, emoji) is preserved verbatim.

        Failures (client offline, DataHandle issues) are logged at
        WARNING level and swallowed. They must never crash the worker
        thread.
        """
        hnd = None
        try:
            hnd = DataHandle(CONFIG.notify_api)
            data = jdump(payload) + b"\x00"
            written = LF_WriteBuffer(hnd.raw, data, len(data))
            if written != len(data):
                logger.warning(
                    "Partial write to DataHandle: %d/%d bytes",
                    written, len(data),
                )
            LF_Sequenced_Notify(
                sess.client_name.encode("utf-8"), hnd.raw,
            )
        except Exception as e:
            logger.warning("Notify to '%s' failed: %s",
                           sess.client_name, e)
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
        Reclaim sessions that have been idle past `session_timeout`.

        Running sessions are skipped: they are mid-generation and the
        worker thread owns their state. This is a simpler policy than
        llm_service.py's dual-condition check because the proxy does
        not own the model context; its sessions only hold a message
        list.
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
                logger.info("Session %s expired by idle timeout", sid)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the proxy service."""
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

        threading.Thread(
            target=self._watchdog_loop,
            name="llm-proxy-watchdog",
            daemon=True,
        ).start()

        self.server.start(CONFIG.endpoint)
        logger.info("LLM Proxy service '%s' running on %s",
                    CONFIG.app_name, CONFIG.endpoint)

    def stop(self) -> None:
        """Graceful shutdown: cancel sessions, stop the server."""
        self._shutdown.set()
        with self._sessions_lock:
            sessions = list(self._sessions.values())
        for sess in sessions:
            sess.request_cancel()
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
    )
    supported, unsupported = split_supported(caps)

    print_banner(
        "LINGOFUSE LLM PROXY",
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
                ("Max history per session", CONFIG.max_history),
                ("Max sessions", CONFIG.max_sessions),
                ("Session idle timeout(s)", CONFIG.session_timeout),
                ("set_system_message",
                 "unsupported (llm_service-only)"),
                ("Attachments",
                 "enabled (text always, image when --vision)"),
                ("Vision",
                 "enabled" if CONFIG.vision else "disabled"),
                ("Structured Output",
                 "enabled (options.response_format forwarded)"),
                ("Log level", CONFIG.log_level),
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

    The main loop waits on `_SHUTDOWN`; setting it causes a graceful
    exit through `service.stop()`.
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
        f"  {invocation}\n"
        f"  {invocation} --backend-url http://127.0.0.1:1234/v1\n"
        f"  {invocation} --backend-model qwen2.5-7b-instruct\n"
        f"  {invocation} --backend-key-file ./api_key.txt\n"
        f"  {invocation} --backend-auth-header api-key "
        f"--backend-auth-scheme \"\"\n"
        f"  {invocation} --backend-extra-headers "
        f"\"{{\\\"HTTP-Referer\\\": \\\"https://example.com\\\"}}\"\n"
        f"  {invocation} --vision --backend-url http://127.0.0.1:1234/v1\n"
        f"  {invocation} --log-level DEBUG\n"
        "\n"
        "Relationship with siblings:\n"
        "  llm_service.py      - local inference (llama.cpp)\n"
        "  llm_proxy.py        - pure text proxy (this file)\n"
        "  llm_proxy_tool.py   - proxy with server-side tools (LTB)\n"
        "  mcp_api_tool.py     - MCP tool gateway (Host-side)\n"
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
        "  and a vision-capable backend (for example LM Studio with a\n"
        "  VLM loaded). When --vision is disabled, a request with an\n"
        "  image attachment is rejected with a clear error.\n"
        "\n"
        "Structured Output:\n"
        "  A generate request may carry options.response_format, an\n"
        "  OpenAI Structured Outputs object such as\n"
        "      {\"type\": \"json_schema\",\n"
        "       \"json_schema\": {\"name\": \"...\", \"strict\": true,\n"
        "                       \"schema\": {...}}}\n"
        "  or the simpler {\"type\": \"json_object\"}. llm_proxy does\n"
        "  not interpret the field; it forwards it verbatim to the\n"
        "  backend and lets the backend enforce the schema.\n"
        "\n"
        "Capability matrix (as returned by get_api_capabilities):\n"
        "  Common keys: generate, create_session, close_session,\n"
        "               cancel_session, list_sessions, health,\n"
        "               llm_stream, attachments, vision\n"
        "  Always 0 on this server: set_system_message\n"
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
        "  LLM_PROXY_MAX_SESSIONS          - max concurrent sessions\n"
        "  LLM_PROXY_SESSION_TIMEOUT       - idle timeout (s)\n"
        "  LLM_PROXY_VISION                - enable image attachments\n"
        "  LLM_PROXY_LOG_LEVEL             - DEBUG/INFO/WARNING/ERROR\n"
    )

    import argparse

    parser = argparse.ArgumentParser(
        prog=get_invocation_name(),
        description=(
            "LingoFuse LLM Proxy.\n"
            "\n"
            "Streaming to the backend uses http.client (not requests)\n"
            "to avoid the socket-level buffering that requests/urllib3\n"
            "apply to SSE responses.\n"
            "\n"
            "Both reasoning_content and content deltas are forwarded\n"
            "to the LingoFuse client as 'think' and 'chunk' events\n"
            "without any proxy-side policy.\n"
            "\n"
            "set_system_message is NOT supported by this proxy. To use\n"
            "a different system message, close the current session\n"
            "and create a new one with the desired system_message.\n"
            "\n"
            "Structured Output is supported as a passthrough field:\n"
            "options.response_format is forwarded verbatim to the\n"
            "backend. See the epilog for the accepted shapes.\n"
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
             "a vision-capable model (VLM). Default: disabled.",
    )
    parser.add_argument(
        "--no-vision",
        dest="vision",
        action="store_false",
        help="Disable image attachment forwarding (default).",
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

    # Sessions
    CONFIG.max_history = args.max_history
    CONFIG.max_sessions = args.max_sessions
    CONFIG.session_timeout = args.session_timeout

    # Attachments
    CONFIG.vision = bool(args.vision)

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

    print_service_banner()

    # Construct the service under a guard: if __init__ raises (e.g.
    # Server() fails to bind, or a config value is invalid), we want
    # a clean error return rather than a NameError inside the finally
    # block below.
    service: Optional[LLMProxyService] = None
    try:
        service = LLMProxyService()
        import atexit
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