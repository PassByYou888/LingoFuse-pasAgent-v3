#!/usr/bin/env python3

import argparse
import atexit
import json
import logging
import os
import queue
import signal
import sys
import threading
import time
import traceback
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_SCRIPT_DIR, ".."))

from lingofuse import Server, App, set_option, check_uid, to_tp2p_uid
from lingofuse.core import DataHandle
from lingofuse._lf_native import LF_UID_Sequenced_Notify
from lingofuse.options import set_opt_debug
from lingofuse.lf_io import cstr, write_json

from llm_common.attachments import (
    Attachment,
    AttachmentError,
    build_user_content,
    validate_attachments,
)
from llm_common.banner import join_list, print_banner, redact
from llm_common.capabilities import (
    SERVER_KIND_SERVICE,
    build_capabilities,
    build_response,
    split_supported,
)
from llm_common.logging_setup import setup_logging
from llm_common.option_sanitizer import (
    COMMON_SCALAR_SPECS,
    DROP,
    sanitize_options,
)
from llm_common.runtime import (
    get_example_invocation,
    get_invocation_name,
)

logger = logging.getLogger("llm_service")

DEFAULT_THINKING = False
_TRUTHY = ("1", "true", "yes", "on")
_FALSY = ("0", "false", "no", "off")


def _parse_thinking_env(raw: Optional[str]) -> Optional[bool]:
    if raw is None:
        return None
    v = raw.strip().lower()
    if v in _TRUTHY:
        return True
    if v in _FALSY:
        return False
    return None


try:
    import llama_cpp
    LLM_BACKEND = "llama_cpp"
except ImportError:
    LLM_BACKEND = None
    print("[WARN] llama-cpp-python not installed, trying transformers...",
          file=sys.stderr)

DEFAULT_MODEL_PATH = "./NVIDIA-Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-IQ4_XS.gguf"
DEFAULT_CONTEXT_SIZE = 0
DEFAULT_MAX_TOKENS = 4096
DEFAULT_THREADS = 6
DEFAULT_GPU_LAYERS = -1
DEFAULT_SYSTEM_MESSAGE = (
    "Before answering, briefly list your reasoning steps using numbered "
    "bullets. Then give the final answer. Do not use Markdown."
)
DEFAULT_ENDPOINT = "ipc:llm_service"
DEFAULT_APP_NAME = "LLM_Service"
DEFAULT_NOTIFY_API = "llm_stream"
DEFAULT_TIMEOUT_MS = 5000
DEFAULT_SESSION_TIMEOUT = 600
DEFAULT_LOG_LEVEL = 1
DEFAULT_QUEUE_MAX_SIZE = 256
DEFAULT_MAX_SESSIONS = 1024
DEFAULT_MAX_HISTORY = 512
DEFAULT_REASONING_BUDGET_MESSAGE = ""

THINK_OPEN_MARKER = "<think>"
THINK_CLOSE_MARKER = "</think>"

SESSION_CLOSE_REASON_TIMEOUT_OFFLINE = "timeout+offline"
SESSION_CLOSE_REASON_CONSOLE = "console"

SERVER_KIND = SERVER_KIND_SERVICE
UID_BYTES = 32


def _capabilities() -> Dict[str, int]:
    return build_capabilities(
        set_system_message=1,
        attachments=1,
        vision=0,
    )


class ServiceConfig:

    def __init__(self) -> None:
        self.model_path: str = DEFAULT_MODEL_PATH
        self.context_size: int = DEFAULT_CONTEXT_SIZE
        self.context_size_actual: int = 0
        self.max_tokens: int = DEFAULT_MAX_TOKENS
        self.threads: int = DEFAULT_THREADS
        self.gpu_layers: int = DEFAULT_GPU_LAYERS
        self.system_message: str = DEFAULT_SYSTEM_MESSAGE
        self.thinking: bool = bool(DEFAULT_THINKING)
        self.endpoint: str = DEFAULT_ENDPOINT
        self.app_name: str = DEFAULT_APP_NAME
        self.notify_api: str = DEFAULT_NOTIFY_API
        self.timeout_ms: int = DEFAULT_TIMEOUT_MS
        self.session_timeout: int = DEFAULT_SESSION_TIMEOUT
        self.queue_max_size: int = DEFAULT_QUEUE_MAX_SIZE
        self.max_sessions: int = DEFAULT_MAX_SESSIONS
        self.max_history: int = DEFAULT_MAX_HISTORY
        self.chat_template_path: Optional[str] = None
        self.chat_template_resolved_path: Optional[str] = None
        self.default_template: Optional[str] = None
        self.reasoning_budget_message: str = DEFAULT_REASONING_BUDGET_MESSAGE
        self.log_level: int = DEFAULT_LOG_LEVEL
        self.vision: bool = False
        self.mmproj_path: str = ""

    @property
    def enable_chunk_logging(self) -> bool:
        return self.log_level >= 1

    @property
    def enable_warning_logging(self) -> bool:
        return self.log_level >= 2


CONFIG = ServiceConfig()


def parse_args() -> argparse.Namespace:
    invocation = get_example_invocation()

    env_raw = os.environ.get("LLM_THINKING")
    env_value = _parse_thinking_env(env_raw)
    if env_value is not None:
        thinking_default = env_value
        thinking_default_source = f"LLM_THINKING={env_raw!r}"
    else:
        thinking_default = bool(DEFAULT_THINKING)
        thinking_default_source = "DEFAULT_THINKING"
        if env_raw is not None:
            print(f"[WARN] LLM_THINKING={env_raw!r} is not a recognised "
                  f"boolean; falling back to DEFAULT_THINKING "
                  f"({DEFAULT_THINKING})", file=sys.stderr)

    caps = _capabilities()
    supported, unsupported = split_supported(caps)
    cap_lines = (
        f"  Supported APIs   : {', '.join(supported)}\n"
        f"  Unsupported APIs : {', '.join(unsupported) or '(none)'}\n"
        "    (call get_api_capabilities at runtime for the canonical map)\n"
    )

    epilog = (
        "All options can also be set via the corresponding environment "
        "variables. The service reads environment variables and "
        "command-line arguments ONCE at startup and stores everything "
        "in a process-wide configuration object; runtime code never "
        "re-reads os.environ or sys.argv.\n"
        "\n"
        "Runtime console:\n"
        "  After the service is running, if stdin is a TTY, an "
        "interactive console is started. Type 'help' at the 'llm>' "
        "prompt to see commands: help, status, sessions, close <sid>, "
        "cancel <sid>, thinking on|off, system <text>, health, "
        "set <key> <value>, debug:<text>, quit.\n"
        "\n"
        "Examples:\n"
        f"  {invocation}\n"
        f"  {invocation} --model-path "
        f"./NVIDIA-Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-IQ4_XS.gguf\n"
        f"  {invocation} --context-size 8192 --max-tokens 2048\n"
        f"  {invocation} --gpu-layers 0 --threads 8 --quiet\n"
        f"  {invocation} --endpoint ipc:llm_service "
        f"--app-name LLM_Service\n"
        f"  {invocation} --chat-template ./chat_template.jinja --debug\n"
        f"  {invocation} --session-timeout 1800 --max-sessions 64\n"
        f"  {invocation} --thinking --quiet\n"
        "\n"
        "Thinking mode:\n"
        "  The single source of truth for the default thinking behaviour\n"
        "  is the module-level constant DEFAULT_THINKING, currently set\n"
        f"  to {DEFAULT_THINKING}.\n"
        "\n"
        "  The effective value is resolved in this order (highest wins):\n"
        "    1. Per-request  : options.thinking in a generate call\n"
        "    2. Command line : --thinking / --no-thinking\n"
        "    3. Environment  : LLM_THINKING=1/true/yes or 0/false/no\n"
        "    4. Constant     : DEFAULT_THINKING\n"
        "\n"
        "Routing model (v4.0 - UID-only):\n"
        "  A session is addressed by the client's 32-byte LingoFuse\n"
        "  session UID. The UID is injected by the framework into the\n"
        "  header of every inbound request, so the server learns it\n"
        "  from the Input DataHandle of the first create_session or\n"
        "  generate call, and refreshes it on every subsequent call.\n"
        "  All stream events are emitted with LF_UID_Sequenced_Notify.\n"
        "  There is no client_name field.\n"
        "\n"
        "Attachments:\n"
        "  A generate request may carry an `attachments` array whose\n"
        "  entries are of kind `text` or `image`. Text attachments are\n"
        "  merged into the user message and kept in session history.\n"
        "  Image attachments are NOT supported by this server in the\n"
        "  current revision: the local vision path (llama.cpp + mmproj)\n"
        "  has not been validated. The capability matrix reports\n"
        "  `vision: 0`, so a well-behaved client will not send images.\n"
        "\n"
        "API capability advertisement:\n"
        f"{cap_lines}"
        "\n"
        "Environment variables (read once at startup):\n"
        "  LLM_MODEL_PATH              - GGUF model path\n"
        "  LLM_CONTEXT_SIZE            - context window size (0 = auto)\n"
        "  LLM_MAX_TOKENS              - default max tokens\n"
        "  LLM_THREADS                 - CPU threads\n"
        "  LLM_GPU_LAYERS              - GPU layers (-1=all, 0=CPU)\n"
        "  LLM_SYSTEM_MESSAGE          - default system message\n"
        "  LLM_THINKING                - overrides DEFAULT_THINKING\n"
        "  LINGOFUSE_ENDPOINT          - LingoFuse endpoint\n"
        "  LINGOFUSE_APP_NAME          - service app name\n"
        "  LINGOFUSE_NOTIFY_API        - notify API name\n"
        "  LINGOFUSE_TIMEOUT_MS        - Call timeout (ms)\n"
        "  LLM_SESSION_TIMEOUT         - idle timeout (s)\n"
        "  LLM_QUEUE_MAX_SIZE          - max queued tasks\n"
        "  LLM_MAX_SESSIONS            - max concurrent sessions\n"
        "  LLM_MAX_HISTORY             - max messages per session\n"
        "  LLM_CHAT_TEMPLATE           - explicit chat template path\n"
        "  LLM_REASONING_BUDGET_MESSAGE - reasoning guidance text\n"
        "  LLM_LOG_LEVEL               - 0=quiet, 1=normal, 2=debug\n"
        "  LLM_DEBUG / LLM_QUIET       - shortcuts for the log level\n"
    )

    parser = argparse.ArgumentParser(
        prog=get_invocation_name(),
        description=(
            "LingoFuse LLM Service - persistent multi-session streaming."
        ),
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--model-path",
        default=os.environ.get("LLM_MODEL_PATH", DEFAULT_MODEL_PATH),
        help=f"Path to the GGUF model file (or HF model directory). "
             f"Default: {DEFAULT_MODEL_PATH}",
    )
    parser.add_argument(
        "--context-size", type=int,
        default=int(os.environ.get("LLM_CONTEXT_SIZE",
                                   DEFAULT_CONTEXT_SIZE)),
        help=("Context window size in tokens. "
              "Default: 0, meaning 'use the model's maximum supported "
              "context size'."),
    )
    parser.add_argument(
        "--max-tokens", type=int,
        default=int(os.environ.get("LLM_MAX_TOKENS", DEFAULT_MAX_TOKENS)),
        help=f"Default maximum tokens to generate per request. "
             f"Default: {DEFAULT_MAX_TOKENS}",
    )
    parser.add_argument(
        "--threads", type=int,
        default=int(os.environ.get("LLM_THREADS", DEFAULT_THREADS)),
        help=f"CPU threads for llama.cpp. Default: {DEFAULT_THREADS}",
    )
    parser.add_argument(
        "--gpu-layers", type=int,
        default=int(os.environ.get("LLM_GPU_LAYERS", DEFAULT_GPU_LAYERS)),
        help=f"GPU layers to offload (-1=all, 0=CPU). "
             f"Default: {DEFAULT_GPU_LAYERS}",
    )
    parser.add_argument(
        "--system-message",
        default=os.environ.get("LLM_SYSTEM_MESSAGE",
                               DEFAULT_SYSTEM_MESSAGE),
        help="Default system message applied to NEW sessions.",
    )

    parser.add_argument(
        "--thinking", dest="thinking", action="store_true",
        default=thinking_default,
        help=(
            "Enable thinking / reasoning mode globally. Overrides both "
            "LLM_THINKING and DEFAULT_THINKING. "
            f"Computed default: {thinking_default} "
            f"(from {thinking_default_source})."
        ),
    )
    parser.add_argument(
        "--no-thinking", dest="thinking", action="store_false",
        help="Explicitly disable thinking / reasoning mode.",
    )

    parser.add_argument(
        "--endpoint",
        default=os.environ.get("LINGOFUSE_ENDPOINT", DEFAULT_ENDPOINT),
        help=f"LingoFuse endpoint (IPC or TCP). "
             f"Default: {DEFAULT_ENDPOINT}",
    )
    parser.add_argument(
        "--app-name",
        default=os.environ.get("LINGOFUSE_APP_NAME", DEFAULT_APP_NAME),
        help=f"Application name to register. Default: {DEFAULT_APP_NAME}",
    )
    parser.add_argument(
        "--notify-api",
        default=os.environ.get("LINGOFUSE_NOTIFY_API",
                               DEFAULT_NOTIFY_API),
        help=f"Notify API name for streaming chunks. "
             f"Default: {DEFAULT_NOTIFY_API}",
    )
    parser.add_argument(
        "--timeout", type=int,
        default=int(os.environ.get("LINGOFUSE_TIMEOUT_MS",
                                   DEFAULT_TIMEOUT_MS)),
        help=f"Call timeout in milliseconds. Default: {DEFAULT_TIMEOUT_MS}",
    )

    parser.add_argument(
        "--session-timeout", type=int,
        default=int(os.environ.get("LLM_SESSION_TIMEOUT",
                                   DEFAULT_SESSION_TIMEOUT)),
        help=f"Idle timeout (seconds) for sessions. Default: "
             f"{DEFAULT_SESSION_TIMEOUT}",
    )
    parser.add_argument(
        "--queue-max-size", type=int,
        default=int(os.environ.get("LLM_QUEUE_MAX_SIZE",
                                   DEFAULT_QUEUE_MAX_SIZE)),
        help=f"Maximum queued generation tasks. "
             f"Default: {DEFAULT_QUEUE_MAX_SIZE}",
    )
    parser.add_argument(
        "--max-sessions", type=int,
        default=int(os.environ.get("LLM_MAX_SESSIONS",
                                   DEFAULT_MAX_SESSIONS)),
        help=f"Maximum concurrent sessions. "
             f"Default: {DEFAULT_MAX_SESSIONS}",
    )
    parser.add_argument(
        "--max-history", type=int,
        default=int(os.environ.get("LLM_MAX_HISTORY",
                                   DEFAULT_MAX_HISTORY)),
        help=f"Maximum messages retained per session. "
             f"Default: {DEFAULT_MAX_HISTORY}",
    )

    parser.add_argument(
        "--chat-template",
        default=os.environ.get("LLM_CHAT_TEMPLATE", None),
        help=("Path to a Jinja2 chat template file. When empty "
              "(the default), the service uses tokenizer.chat_template "
              "from the GGUF metadata; if that is unavailable, "
              "create_chat_completion mode is used."),
    )
    parser.add_argument(
        "--reasoning-budget-message",
        default=os.environ.get(
            "LLM_REASONING_BUDGET_MESSAGE", DEFAULT_REASONING_BUDGET_MESSAGE
        ),
        help=("Text prepended by the chat template at the start of the "
              "thinking section."),
    )

    parser.add_argument(
        "--log-level", type=int, choices=[0, 1, 2],
        default=int(os.environ.get("LLM_LOG_LEVEL", DEFAULT_LOG_LEVEL)),
        help="Log verbosity: 0=quiet, 1=normal, 2=debug. "
             f"Default: {DEFAULT_LOG_LEVEL}",
    )
    parser.add_argument(
        "--debug", action="store_true",
        default=os.environ.get("LLM_DEBUG", "0").lower()
        in ("1", "true", "yes"),
        help="Shortcut for --log-level 2.",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        default=os.environ.get("LLM_QUIET", "0").lower()
        in ("1", "true", "yes"),
        help="Shortcut for --log-level 0.",
    )

    return parser.parse_args()


def _init_global_config(args: argparse.Namespace) -> None:
    CONFIG.model_path = args.model_path
    CONFIG.context_size = args.context_size
    CONFIG.max_tokens = args.max_tokens
    CONFIG.threads = args.threads
    CONFIG.gpu_layers = args.gpu_layers
    CONFIG.system_message = args.system_message
    CONFIG.thinking = bool(args.thinking)

    CONFIG.endpoint = args.endpoint
    CONFIG.app_name = args.app_name
    CONFIG.notify_api = args.notify_api
    CONFIG.timeout_ms = args.timeout

    CONFIG.session_timeout = args.session_timeout
    CONFIG.queue_max_size = args.queue_max_size
    CONFIG.max_sessions = args.max_sessions
    CONFIG.max_history = max(2, args.max_history)

    CONFIG.chat_template_path = args.chat_template
    CONFIG.reasoning_budget_message = args.reasoning_budget_message

    if args.debug:
        CONFIG.log_level = 2
    elif args.quiet:
        CONFIG.log_level = 0
    else:
        CONFIG.log_level = args.log_level


def load_chat_template(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logger.error("Failed to read chat template %s: %s", path, e)
        sys.exit(1)


def _resolve_explicit_template() -> None:
    if CONFIG.chat_template_path:
        resolved = os.path.abspath(CONFIG.chat_template_path)
        if not os.path.isfile(resolved):
            logger.error("Chat template not found: %s", resolved)
            sys.exit(1)
        CONFIG.chat_template_resolved_path = resolved
        CONFIG.default_template = load_chat_template(resolved)
        logger.info(
            "Loaded chat template (explicit): %s (%d bytes)",
            resolved, len(CONFIG.default_template),
        )
        return
    logger.info("No explicit chat template; will try GGUF metadata next")


def _resolve_metadata_template(llm: Any) -> None:
    if CONFIG.default_template is not None:
        return
    try:
        metadata = getattr(llm, "metadata", None)
    except Exception:
        metadata = None
    if not metadata:
        logger.info("No GGUF metadata available; falling back to "
                    "create_chat_completion mode.")
        return
    tmpl = metadata.get("tokenizer.chat_template")
    if not tmpl:
        logger.info("tokenizer.chat_template not found in GGUF metadata; "
                    "falling back to create_chat_completion mode.")
        return
    if isinstance(tmpl, dict):
        default_variant = tmpl.get("default")
        if not default_variant:
            default_variant = next(iter(tmpl.values())) if tmpl else None
        tmpl = default_variant
    if not isinstance(tmpl, str) or not tmpl:
        logger.info("tokenizer.chat_template has an unsupported type; "
                    "falling back to create_chat_completion mode.")
        return
    CONFIG.default_template = tmpl
    CONFIG.chat_template_resolved_path = None
    logger.info("Loaded chat template (from GGUF metadata): "
                "<%d bytes>", len(tmpl))


def estimate_tokens(llm, tokenizer, text: str) -> Optional[int]:
    if text is None:
        return 0
    if LLM_BACKEND == "llama_cpp" and hasattr(llm, "tokenize"):
        try:
            return len(llm.tokenize(text.encode("utf-8"),
                                    add_bos=False, special=True))
        except Exception:
            return None
    if LLM_BACKEND == "transformers" and tokenizer is not None:
        try:
            return len(tokenizer.encode(text, add_special_tokens=False))
        except Exception:
            return None
    return None


def load_llm(model_path: str, context_size: int, threads: int,
             gpu_layers: int):
    logger.info("Loading model... (this may take a while)")
    logger.info("Model path: %s", model_path)
    ctx_display = ("auto (model maximum)" if context_size == 0
                   else context_size)
    logger.info("Context size: %s, threads: %d, GPU layers: %d",
                ctx_display, threads, gpu_layers)
    if LLM_BACKEND == "llama_cpp":
        logger.info("Using backend: llama-cpp-python")
        model = llama_cpp.Llama(
            model_path=model_path,
            n_ctx=context_size,
            n_threads=threads,
            n_gpu_layers=gpu_layers,
            verbose=False,
        )
        actual_ctx = model.n_ctx()
        try:
            metadata = model.metadata
            if metadata:
                logger.debug("Model metadata:")
                for k, v in metadata.items():
                    if k == "tokenizer.chat_template":
                        logger.debug("    %s: <%d bytes>",
                                     k, len(str(v)))
                        continue
                    logger.debug("    %s: %s", k, v)
        except AttributeError:
            pass
        return model, None, actual_ctx
    raise RuntimeError("No LLM backend available")


class ThinkingParser:

    def __init__(self, initial_in_thinking: bool = False) -> None:
        self.in_thinking = initial_in_thinking
        self.buffer = ""

    @staticmethod
    def _safe_emit_len(buf: str, marker: str) -> int:
        max_check = min(len(marker) - 1, len(buf))
        for i in range(max_check, 0, -1):
            if buf.endswith(marker[:i]):
                return len(buf) - i
        return len(buf)

    def feed(self, text: str) -> List[Tuple[str, str]]:
        self.buffer += text
        out: List[Tuple[str, str]] = []
        while True:
            if self.in_thinking:
                idx = self.buffer.find(THINK_CLOSE_MARKER)
                if idx >= 0:
                    if idx > 0:
                        out.append(("think", self.buffer[:idx]))
                    self.buffer = (
                        self.buffer[idx + len(THINK_CLOSE_MARKER):]
                    )
                    self.in_thinking = False
                    continue
                emit = self._safe_emit_len(
                    self.buffer, THINK_CLOSE_MARKER,
                )
                if emit > 0:
                    out.append(("think", self.buffer[:emit]))
                    self.buffer = self.buffer[emit:]
                break
            else:
                idx = self.buffer.find(THINK_OPEN_MARKER)
                if idx >= 0:
                    if idx > 0:
                        out.append(("chunk", self.buffer[:idx]))
                    self.buffer = (
                        self.buffer[idx + len(THINK_OPEN_MARKER):]
                    )
                    self.in_thinking = True
                    continue
                emit = self._safe_emit_len(
                    self.buffer, THINK_OPEN_MARKER,
                )
                if emit > 0:
                    out.append(("chunk", self.buffer[:emit]))
                    self.buffer = self.buffer[emit:]
                break
        return out

    def flush(self) -> List[Tuple[str, str]]:
        if not self.buffer:
            return []
        result = [("think" if self.in_thinking else "chunk", self.buffer)]
        self.buffer = ""
        return result


class Session:

    def __init__(
        self,
        session_id: str,
        client_uid: bytes,
        system_message: str,
    ) -> None:
        if not isinstance(client_uid, (bytes, bytearray, memoryview)):
            raise TypeError(
                f"client_uid must be bytes/bytearray/memoryview, "
                f"got {type(client_uid).__name__}"
            )
        uid_bytes = bytes(client_uid)
        if len(uid_bytes) != UID_BYTES:
            raise ValueError(
                f"client_uid must be exactly {UID_BYTES} bytes, "
                f"got {len(uid_bytes)}"
            )
        self.session_id = session_id
        self.client_uid: bytes = uid_bytes
        self.system_message = system_message
        self.messages: List[Dict[str, Any]] = []
        self.created_at = time.time()
        self.last_active_at = self.created_at
        self.status = "idle"
        self.current_cancel_event: Optional[threading.Event] = None
        self.lock = threading.Lock()

    def update_client_uid(self, new_uid: bytes) -> None:
        if not isinstance(new_uid, (bytes, bytearray, memoryview)):
            raise TypeError(
                f"new_uid must be bytes/bytearray/memoryview, "
                f"got {type(new_uid).__name__}"
            )
        uid_bytes = bytes(new_uid)
        if len(uid_bytes) != UID_BYTES:
            raise ValueError(
                f"new_uid must be exactly {UID_BYTES} bytes, "
                f"got {len(uid_bytes)}"
            )
        with self.lock:
            self.client_uid = uid_bytes

    def to_summary(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "client_uid_hex": self.client_uid.hex(),
            "created_at": self.created_at,
            "last_active_at": self.last_active_at,
            "status": self.status,
            "message_count": len(self.messages),
        }


class GenerationTask:

    def __init__(
        self,
        task_id: str,
        session: Session,
        content: str,
        prompt: str,
        options: Dict[str, Any],
        ephemeral: bool,
        attachments: List[Attachment],
        history_text: str,
    ) -> None:
        self.task_id = task_id
        self.session = session
        self.content = content
        self.prompt = prompt
        self.options = options
        self.ephemeral = ephemeral
        self.attachments = attachments
        self.history_text = history_text
        self.cancel_event = threading.Event()


class LLMService:

    def __init__(self) -> None:
        self.backend = LLM_BACKEND
        self._system_message = CONFIG.system_message
        self._system_message_lock = threading.Lock()
        self._sessions: Dict[str, Session] = {}
        self._sessions_lock = threading.Lock()
        self._request_queue: "queue.Queue[Optional[GenerationTask]]" = \
            queue.Queue(maxsize=CONFIG.queue_max_size)
        self._shutdown_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._watchdog_thread: Optional[threading.Thread] = None
        self._cleaned_up = False

        logger.info("Loading LLM...")
        load_start = time.monotonic()
        self.llm, self.tokenizer, actual_ctx = load_llm(
            CONFIG.model_path,
            CONFIG.context_size,
            CONFIG.threads,
            CONFIG.gpu_layers,
        )
        CONFIG.context_size_actual = actual_ctx
        logger.info("Model loaded in %.2fs",
                    time.monotonic() - load_start)
        logger.info("Effective context size: %d tokens", actual_ctx)

        if self.backend == "llama_cpp":
            _resolve_metadata_template(self.llm)
        else:
            logger.info("transformers backend: no template resolution")

        self._start_worker()
        self._start_watchdog()

        self.server = Server(
            CONFIG.app_name,
            "Local LLM Service with persistent multi-session streaming",
        )
        self._register_apis()
        atexit.register(self.cleanup)

    def _start_worker(self) -> None:
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="llm-worker",
            daemon=True,
        )
        self._worker_thread.start()
        logger.info("Inference worker thread started")

    def _start_watchdog(self) -> None:
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name="llm-watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()
        logger.info("Watchdog started (session idle timeout: %ds)",
                    CONFIG.session_timeout)

    def _worker_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                task = self._request_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error("queue.get failed: %s", e)
                traceback.print_exc()
                time.sleep(0.5)
                continue
            if task is None:
                break
            try:
                self._process_task(task)
            except Exception as e:
                logger.error("Task %s worker exception: %s",
                             task.task_id, e)
                traceback.print_exc()
            finally:
                try:
                    self._request_queue.task_done()
                except Exception:
                    pass

    def _watchdog_loop(self) -> None:
        while not self._shutdown_event.wait(timeout=5.0):
            try:
                self._watchdog_tick()
            except Exception as e:
                logger.error("Watchdog iteration failed: %s", e)
                traceback.print_exc()

    def _watchdog_tick(self) -> None:
        now = time.time()
        to_close: List[Session] = []
        with self._sessions_lock:
            for sess in self._sessions.values():
                if sess.status != "idle":
                    continue
                if now - sess.last_active_at <= CONFIG.session_timeout:
                    continue
                try:
                    still_online = check_uid(sess.client_uid)
                except Exception as e:
                    logger.warning(
                        "check_uid(%s) raised %r; keeping session %s",
                        sess.client_uid.hex()[:8], e, sess.session_id,
                    )
                    continue
                if still_online:
                    if CONFIG.enable_warning_logging:
                        logger.warning(
                            "Session %s idle > %ds but client uid=%s "
                            "is still online; keeping session",
                            sess.session_id, CONFIG.session_timeout,
                            sess.client_uid.hex()[:8],
                        )
                    continue
                to_close.append(sess)
        for sess in to_close:
            try:
                idle_for = now - sess.last_active_at
                logger.info(
                    "Session %s idle for %.1fs (> %ds) and client "
                    "uid=%s is offline; reclaiming session",
                    sess.session_id, idle_for, CONFIG.session_timeout,
                    sess.client_uid.hex()[:8],
                )
                self._close_session_internal(
                    sess, reason=SESSION_CLOSE_REASON_TIMEOUT_OFFLINE,
                )
            except Exception as e:
                logger.error("Failed to close session %s: %s",
                             sess.session_id, e)

    def _register_apis(self) -> None:
        @self.server.expose("generate", with_input=True)
        def generate(data: Dict[str, Any], *, _inp) -> Dict[str, Any]:
            return self._handle_generate_safe(data, _inp)

        @self.server.expose("create_session", with_input=True)
        def create_session(data: Dict[str, Any], *, _inp) -> Dict[str, Any]:
            return self._handle_create_session_safe(data, _inp)

        @self.server.expose("close_session", with_input=True)
        def close_session(data: Dict[str, Any], *, _inp) -> Dict[str, Any]:
            return self._handle_close_session_safe(data, _inp)

        @self.server.expose("cancel_session", with_input=True)
        def cancel_session(data: Dict[str, Any], *, _inp) -> Dict[str, Any]:
            return self._handle_cancel_session_safe(data, _inp)

        @self.server.expose("list_sessions", with_input=True)
        def list_sessions(data: Dict[str, Any], *, _inp) -> Dict[str, Any]:
            return self._handle_list_sessions_safe(data, _inp)

        @self.server.expose("set_system_message")
        def set_system_message(data: Dict[str, Any]) -> Dict[str, Any]:
            return self._handle_set_system_message_safe(data)

        @self.server.expose("get_api_capabilities")
        def get_api_capabilities(data: Dict[str, Any]) -> Dict[str, Any]:
            return self._handle_get_api_capabilities(data)

        @self.server.expose("health")
        def health(data: Dict[str, Any]) -> Dict[str, Any]:
            return self._handle_health(data)

        logger.info(
            "Registered APIs: generate, create_session, close_session, "
            "cancel_session, list_sessions, set_system_message, "
            "get_api_capabilities, health"
        )

    def _handle_generate_safe(self, data: Any, inp) -> Dict[str, Any]:
        try:
            return self._handle_generate(data, inp)
        except Exception as e:
            logger.error("generate handler exception: %s", e)
            traceback.print_exc()
            return {"code": -1, "error": f"Internal error: {e}"}

    def _handle_create_session_safe(self, data: Any, inp) -> Dict[str, Any]:
        try:
            return self._handle_create_session(data, inp)
        except Exception as e:
            logger.error("create_session handler exception: %s", e)
            traceback.print_exc()
            return {"code": -1, "error": f"Internal error: {e}"}

    def _handle_close_session_safe(self, data: Any, inp) -> Dict[str, Any]:
        try:
            return self._handle_close_session(data, inp)
        except Exception as e:
            logger.error("close_session handler exception: %s", e)
            traceback.print_exc()
            return {"code": -1, "error": f"Internal error: {e}"}

    def _handle_cancel_session_safe(self, data: Any, inp) -> Dict[str, Any]:
        try:
            return self._handle_cancel_session(data, inp)
        except Exception as e:
            logger.error("cancel_session handler exception: %s", e)
            traceback.print_exc()
            return {"code": -1, "error": f"Internal error: {e}"}

    def _handle_list_sessions_safe(self, data: Any, inp) -> Dict[str, Any]:
        try:
            return self._handle_list_sessions(data, inp)
        except Exception as e:
            logger.error("list_sessions handler exception: %s", e)
            traceback.print_exc()
            return {"code": -1, "error": f"Internal error: {e}"}

    def _handle_set_system_message_safe(self, data: Any) -> Dict[str, Any]:
        try:
            return self._handle_set_system_message(data)
        except Exception as e:
            logger.error("set_system_message handler exception: %s", e)
            traceback.print_exc()
            return {"code": -1, "error": f"Internal error: {e}"}

    @staticmethod
    def _sanitize_options(raw: Dict[str, Any]) -> Dict[str, Any]:
        options = sanitize_options(
            raw,
            scalar_specs=COMMON_SCALAR_SPECS,
            custom={
                "thinking": lambda v: bool(v) if v is not None else DROP,
                "ephemeral": lambda v: bool(v) if v is not None else DROP,
            },
        )
        options.setdefault("thinking", bool(CONFIG.thinking))
        options.setdefault("ephemeral", False)
        return options

    def _count_sessions(self) -> int:
        with self._sessions_lock:
            return len(self._sessions)

    def _create_session(
        self,
        client_uid: bytes,
        system_message: Optional[str] = None,
    ) -> Session:
        if system_message is None:
            with self._system_message_lock:
                system_message = self._system_message
        session_id = str(uuid.uuid4())
        session = Session(
            session_id=session_id,
            client_uid=client_uid,
            system_message=system_message,
        )
        with self._sessions_lock:
            self._sessions[session_id] = session
        return session

    def _lookup_session(self, session_id: str) -> Optional[Session]:
        with self._sessions_lock:
            return self._sessions.get(session_id)

    def _close_session_internal(self, session: Session,
                                reason: str) -> bool:
        with self._sessions_lock:
            existing = self._sessions.pop(session.session_id, None)
        if existing is None:
            return False
        try:
            with session.lock:
                session.status = "closing"
                if session.current_cancel_event is not None:
                    session.current_cancel_event.set()
        except Exception as e:
            logger.warning("Session %s state update during close "
                           "failed: %s", session.session_id, e)
        self._emit_message_raw(session, {
            "type": "closed",
            "session_id": session.session_id,
            "reason": reason,
        })
        logger.info("Session %s closed (reason=%s)",
                    session.session_id, reason)
        return True

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

    def _handle_generate(self, data: Any, inp) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {"code": -1,
                    "error": "generate expects a JSON object payload"}
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
        if has_image:
            return {
                "code": -1,
                "error": (
                    "Image attachments are not supported by "
                    "llm_service in this revision. The local vision "
                    "path (llama.cpp + mmproj) has not been validated. "
                    "Remove the image attachments, or use llm_proxy.py "
                    "or llm_proxy_tool.py with a vision-capable backend."
                ),
            }
        options = self._sanitize_options(options_raw)
        ephemeral = bool(options.get("ephemeral", False))
        mode: str
        if session_id:
            session = self._lookup_session(session_id)
            if session is None:
                return {"code": -1,
                        "error": f"Session not found: {session_id}"}
            if session.status == "closing":
                return {"code": -1,
                        "error": f"Session is closing: {session_id}"}
            if session.client_uid != caller_uid:
                return {"code": -1,
                        "error": "Caller UID does not match the "
                                 "session owner"}
            try:
                session.update_client_uid(caller_uid)
            except Exception as e:
                logger.error(
                    "Session %s update_client_uid failed: %s",
                    session.session_id, e,
                )
                return {"code": -1,
                        "error": f"Failed to refresh session UID: {e}"}
            mode = "continue"
        else:
            if self._count_sessions() >= CONFIG.max_sessions:
                return {"code": -1,
                        "error": f"Session limit reached "
                                 f"(max {CONFIG.max_sessions})"}
            session = self._create_session(caller_uid)
            mode = "ephemeral" if ephemeral else "new"
        with self._system_message_lock:
            system_snapshot = session.system_message
        try:
            error = self._check_token_budget(
                session, content, prompt, options, system_snapshot,
                attachments,
            )
        except Exception as e:
            logger.error("Token budget check failed: %s", e)
            error = f"Internal error during token budget check: {e}"
        if error is not None:
            if mode in ("new", "ephemeral"):
                self._close_session_internal(session, reason="error")
            return {"code": -1, "error": error}
        history_text = self._build_history_text(content, prompt,
                                                attachments)
        task_id = str(uuid.uuid4())
        task = GenerationTask(
            task_id=task_id,
            session=session,
            content=content,
            prompt=prompt,
            options=options,
            ephemeral=ephemeral,
            attachments=attachments,
            history_text=history_text,
        )
        with session.lock:
            session.last_active_at = time.time()
        try:
            self._request_queue.put_nowait(task)
        except queue.Full:
            if mode in ("new", "ephemeral"):
                self._close_session_internal(session, reason="error")
            return {"code": -1,
                    "error": "Server busy: request queue is full"}
        except Exception as e:
            if mode in ("new", "ephemeral"):
                self._close_session_internal(session, reason="error")
            return {"code": -1,
                    "error": f"Failed to enqueue task: {e}"}
        logger.info(
            "Task %s queued (mode=%s, session=%s, uid=%s, "
            "thinking=%s, attachments=%d)",
            task_id, mode, session.session_id,
            session.client_uid.hex()[:8],
            options.get('thinking', False), len(attachments),
        )
        return {
            "code": 0,
            "session_id": session.session_id,
            "task_id": task_id,
            "mode": mode,
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
        system_message = data.get("system_message")
        if system_message is not None and not isinstance(system_message,
                                                        str):
            return {"code": -1,
                    "error": "system_message must be a string"}
        if self._count_sessions() >= CONFIG.max_sessions:
            return {"code": -1,
                    "error": f"Session limit reached "
                             f"(max {CONFIG.max_sessions})"}
        session = self._create_session(caller_uid, system_message)
        logger.info("Session created: %s (uid=%s)",
                    session.session_id, caller_uid.hex()[:8])
        return {
            "code": 0,
            "session_id": session.session_id,
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
        session = self._lookup_session(session_id)
        if session is None:
            return {"code": -1,
                    "error": f"Session not found: {session_id}"}
        if session.client_uid != caller_uid:
            return {"code": -1,
                    "error": "Caller UID does not match the session "
                             "owner"}
        cancel_running = bool(data.get("cancel_running", True))
        if session.status == "running" and not cancel_running:
            return {"code": -1,
                    "error": "Session is currently running; pass "
                             "cancel_running=true to force-close"}
        self._close_session_internal(session, reason="client")
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
        session = self._lookup_session(session_id)
        if session is None:
            return {"code": -1,
                    "error": f"Session not found: {session_id}"}
        if session.client_uid != caller_uid:
            return {"code": -1,
                    "error": "Caller UID does not match the session "
                             "owner"}
        with session.lock:
            ev = session.current_cancel_event
            if ev is None:
                return {"code": 0, "status": "no_active_task"}
            ev.set()
        logger.info("Cancel requested for session %s", session_id)
        return {"code": 0, "status": "cancel_requested"}

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
        if not isinstance(data, dict):
            return {"code": -1,
                    "error": "set_system_message expects a JSON object"}
        new_msg = data.get("content")
        if new_msg is None or not isinstance(new_msg, str):
            return {"code": -1,
                    "error": "Missing or invalid 'content' field"}
        with self._system_message_lock:
            self._system_message = new_msg
        preview = new_msg[:50] + ("..." if len(new_msg) > 50 else "")
        logger.info("Global default system message updated: %s "
                    "(applies to NEW sessions only)", preview)
        return {"code": 0, "status": "ok"}

    def _handle_get_api_capabilities(self, data: Any) -> Dict[str, Any]:
        return build_response(SERVER_KIND, _capabilities())

    def _handle_health(self, data: Any) -> Dict[str, Any]:
        with self._sessions_lock:
            sessions = list(self._sessions.values())
        idle = sum(1 for s in sessions if s.status == "idle")
        running = sum(1 for s in sessions if s.status == "running")
        return {
            "code": 0,
            "status": "ok",
            "server_kind": SERVER_KIND,
            "backend": self.backend,
            "model": CONFIG.model_path,
            "context_size_requested": CONFIG.context_size,
            "context_size_actual": CONFIG.context_size_actual,
            "thinking_default": CONFIG.thinking,
            "sessions_total": len(sessions),
            "sessions_idle": idle,
            "sessions_running": running,
            "session_limit": CONFIG.max_sessions,
            "session_timeout": CONFIG.session_timeout,
            "max_history": CONFIG.max_history,
            "queue_size": self._request_queue.qsize(),
            "queue_max_size": CONFIG.queue_max_size,
            "chat_template_path": CONFIG.chat_template_resolved_path,
            "chat_template_loaded": CONFIG.default_template is not None,
            "attachments_supported": True,
            "vision_supported": False,
            "api_capabilities": _capabilities(),
        }

    @staticmethod
    def _build_history_text(
        content: str,
        prompt: str,
        attachments: List[Attachment],
    ) -> str:
        user_text = content + ("\n\n" + prompt if prompt else "")
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
        return "\n\n".join(parts)

    def _check_token_budget(
        self,
        session: Session,
        content: str,
        prompt: str,
        options: Dict[str, Any],
        system_message: str,
        attachments: List[Attachment],
    ) -> Optional[str]:
        max_tokens = options.get("max_tokens", CONFIG.max_tokens)
        new_input = self._build_history_text(content, prompt, attachments)
        input_tokens = estimate_tokens(self.llm, self.tokenizer,
                                       new_input)
        if input_tokens is None:
            input_tokens = len(new_input) // 2 + 1
        history_overhead = 8 * len(session.messages) + 32
        history_tokens = sum(
            (estimate_tokens(self.llm, self.tokenizer,
                             str(m.get("content", ""))) or
             (len(str(m.get("content", ""))) // 2 + 1))
            for m in session.messages
        )
        sys_tokens = estimate_tokens(
            self.llm, self.tokenizer, system_message,
        ) or (len(system_message) // 2 + 1)
        total_needed = (
            input_tokens + sys_tokens + history_tokens + history_overhead
            + max_tokens + 32
        )
        if total_needed > CONFIG.context_size_actual:
            return (
                f"Request too large: input={input_tokens} tokens, "
                f"system={sys_tokens} tokens, "
                f"history={history_tokens} tokens, "
                f"max_output={max_tokens} tokens, "
                f"total={total_needed} > "
                f"context={CONFIG.context_size_actual}"
            )
        return None

    def _process_task(self, task: GenerationTask) -> None:
        session = task.session
        try:
            with self._sessions_lock:
                if session.session_id not in self._sessions:
                    logger.info(
                        "Task %s: session %s no longer exists; "
                        "dropping task",
                        task.task_id, session.session_id,
                    )
                    return
        except Exception as e:
            logger.warning("Task %s: session existence check failed: %s",
                           task.task_id, e)
        try:
            with session.lock:
                session.status = "running"
                session.current_cancel_event = task.cancel_event
        except Exception as e:
            logger.error("Task %s: failed to mark session running: %s",
                         task.task_id, e)
            return
        if task.cancel_event.is_set():
            self._safe_finalize(session, task, cancelled=True)
            return
        thinking = bool(task.options.get("thinking", CONFIG.thinking))
        max_tokens = task.options.get("max_tokens", CONFIG.max_tokens)
        temperature = task.options.get("temperature", 0.7)
        top_p = task.options.get("top_p", 0.95)
        top_k = task.options.get("top_k", 40)
        repeat_penalty = task.options.get("repeat_penalty", 1.1)
        logger.info(
            "Task %s generation started (session=%s, uid=%s, "
            "thinking=%s, max_tokens=%s, history=%d, attachments=%d)",
            task.task_id, session.session_id,
            session.client_uid.hex()[:8],
            thinking, max_tokens, len(session.messages),
            len(task.attachments),
        )
        try:
            with session.lock:
                history = [dict(m) for m in session.messages]
            messages = [{"role": "system",
                         "content": session.system_message}]
            messages.extend(history)
            messages.append({"role": "user",
                             "content": task.history_text})
        except Exception as e:
            logger.error("Task %s: failed to build messages: %s",
                         task.task_id, e)
            self._safe_finalize(session, task,
                                error=f"Internal error: {e}")
            return
        try:
            if self.backend == "llama_cpp":
                think_text, answer_text = self._run_llama_cpp(
                    session, task, messages,
                    max_tokens, temperature, top_p, top_k,
                    repeat_penalty, thinking,
                )
            elif self.backend == "transformers":
                self._emit_error(
                    session,
                    "transformers backend streaming is not implemented; "
                    "please use llama-cpp-python",
                )
                self._safe_finalize(
                    session, task,
                    error="transformers backend unsupported",
                )
                return
            else:
                self._safe_finalize(
                    session, task,
                    error="No LLM backend available",
                )
                return
        except Exception as e:
            msg = str(e)
            lowered = msg.lower()
            if ("out of memory" in lowered
                    or "cuda out of memory" in lowered):
                msg = ("CUDA out of memory - reduce max_tokens or use a "
                       "smaller model")
            elif "context" in lowered and "length" in lowered:
                msg = (f"Context length exceeded - maximum context size "
                       f"{CONFIG.context_size_actual} tokens")
            logger.error("Task %s generation error: %s",
                         task.task_id, msg)
            self._safe_finalize(session, task, error=msg)
            return
        self._safe_finalize(
            session, task,
            think=think_text, answer=answer_text,
            cancelled=task.cancel_event.is_set(),
        )

    def _safe_finalize(self, session: Session, task: GenerationTask,
                       **kwargs) -> None:
        try:
            self._finalize_task(session, task, **kwargs)
        except Exception as e:
            logger.error("Task %s: finalize failed: %s",
                         task.task_id, e)
            traceback.print_exc()
            try:
                with session.lock:
                    session.status = "idle"
                    session.current_cancel_event = None
                    session.last_active_at = time.time()
            except Exception as ee:
                logger.error("Task %s: fallback state reset "
                             "failed: %s", task.task_id, ee)

    def _finalize_task(
        self,
        session: Session,
        task: GenerationTask,
        think: str = "",
        answer: str = "",
        cancelled: bool = False,
        error: Optional[str] = None,
    ) -> None:
        try:
            with session.lock:
                if error is None and not cancelled:
                    session.messages.append({
                        "role": "user",
                        "content": task.history_text,
                    })
                    assistant_msg: Dict[str, Any] = {
                        "role": "assistant",
                        "content": answer,
                    }
                    if think:
                        assistant_msg["reasoning_content"] = think
                    session.messages.append(assistant_msg)
                    if len(session.messages) > CONFIG.max_history:
                        drop = (
                            len(session.messages) - CONFIG.max_history
                        )
                        session.messages = session.messages[drop:]
                session.last_active_at = time.time()
                session.status = "idle"
                session.current_cancel_event = None
        except Exception as e:
            logger.error("Task %s: state update failed: %s",
                         task.task_id, e)
            try:
                with session.lock:
                    session.status = "idle"
                    session.current_cancel_event = None
            except Exception:
                pass
        try:
            if error is not None:
                self._emit_error(session, error)
                self._emit_finish(session, "error")
            elif cancelled:
                self._emit_finish(session, "cancelled")
            else:
                self._emit_finish(session, "stop")
        except Exception as e:
            logger.error("Task %s: terminal notification failed: %s",
                         task.task_id, e)
        try:
            logger.info(
                "Task %s finished (session=%s, cancelled=%s, "
                "error=%s, history=%d)",
                task.task_id, session.session_id, cancelled,
                bool(error), len(session.messages),
            )
        except Exception:
            pass
        if task.ephemeral:
            try:
                self._close_session_internal(session, reason="ephemeral")
            except Exception as e:
                logger.error("Task %s: ephemeral close failed: %s",
                             task.task_id, e)

    def _render_prompt(
        self,
        messages: List[Dict[str, Any]],
        thinking: bool,
    ) -> Optional[str]:
        template = CONFIG.default_template
        if not template:
            return None
        try:
            from jinja2 import Template
        except ImportError:
            raise RuntimeError(
                "A chat template is in use but jinja2 is not installed. "
                "Install jinja2 or remove the chat template."
            )
        tpl = Template(template)
        prompt_str = tpl.render(
            messages=messages,
            add_generation_prompt=True,
            bos_token="<s>",
            eos_token="</s>",
            enable_thinking=bool(thinking),
            truncate_history_thinking=True,
            reasoning_budget_message=CONFIG.reasoning_budget_message,
        )
        stripped = prompt_str.rstrip()
        if thinking:
            if stripped.endswith(THINK_OPEN_MARKER):
                pass
            elif stripped.endswith(THINK_CLOSE_MARKER):
                head = stripped[:-len(THINK_CLOSE_MARKER)].rstrip()
                prompt_str = head + "\n" + THINK_OPEN_MARKER + "\n"
            else:
                prompt_str = stripped + "\n" + THINK_OPEN_MARKER + "\n"
        else:
            if stripped.endswith(THINK_CLOSE_MARKER):
                pass
            elif stripped.endswith(THINK_OPEN_MARKER):
                prompt_str = stripped + THINK_CLOSE_MARKER + "\n"
            else:
                prompt_str = (stripped + "\n"
                              + THINK_OPEN_MARKER
                              + THINK_CLOSE_MARKER + "\n")
        if CONFIG.log_level >= 2:
            tail = (prompt_str[-200:] if len(prompt_str) > 200
                    else prompt_str)
            logger.debug("rendered prompt tail: %r", tail)
        return prompt_str

    def _run_llama_cpp(
        self,
        session: Session,
        task: GenerationTask,
        messages: List[Dict[str, Any]],
        max_tokens: int,
        temperature: float,
        top_p: float,
        top_k: int,
        repeat_penalty: float,
        thinking: bool,
    ) -> Tuple[str, str]:
        think_parts: List[str] = []
        answer_parts: List[str] = []
        stream = None
        parser = (ThinkingParser(initial_in_thinking=thinking)
                  if thinking else None)
        try:
            prompt_str = self._render_prompt(messages, thinking)
            if prompt_str is not None:
                stream = self.llm.create_completion(
                    prompt=prompt_str,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    top_k=top_k,
                    repeat_penalty=repeat_penalty,
                    stream=True,
                )
                chunk_key = "text"
            else:
                stream = self.llm.create_chat_completion(
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    top_k=top_k,
                    repeat_penalty=repeat_penalty,
                    stream=True,
                )
                chunk_key = "delta"
            for chunk in stream:
                if task.cancel_event.is_set():
                    logger.info("Task %s cancelled mid-stream",
                                task.task_id)
                    break
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                if chunk_key == "text":
                    text = choice.get("text", "") or ""
                else:
                    delta = choice.get("delta") or {}
                    text = delta.get("content", "") or ""
                if not text:
                    continue
                if parser is not None:
                    for mtype, mtext in parser.feed(text):
                        if not mtext:
                            continue
                        if mtype == "think":
                            think_parts.append(mtext)
                            self._emit_message(session, "think", mtext)
                        else:
                            answer_parts.append(mtext)
                            self._emit_message(session, "chunk", mtext)
                else:
                    answer_parts.append(text)
                    self._emit_message(session, "chunk", text)
            if parser is not None:
                for mtype, mtext in parser.flush():
                    if not mtext:
                        continue
                    if mtype == "think":
                        think_parts.append(mtext)
                        self._emit_message(session, "think", mtext)
                    else:
                        answer_parts.append(mtext)
                        self._emit_message(session, "chunk", mtext)
            return "".join(think_parts), "".join(answer_parts)
        finally:
            if stream is not None:
                closer = getattr(stream, "close", None)
                if callable(closer):
                    try:
                        closer()
                    except Exception:
                        pass

    def _emit_message(self, session: Session, msg_type: str,
                      text: str) -> None:
        payload = {
            "type": msg_type,
            "session_id": session.session_id,
            "text": text,
        }
        self._send_payload(session, payload)
        if msg_type != "chunk" and CONFIG.enable_chunk_logging:
            preview = text[:120] + ("..." if len(text) > 120 else "")
            logger.info("Session %s %s: %s",
                        session.session_id, msg_type, preview)

    def _emit_error(self, session: Session, message: str) -> None:
        self._emit_message_raw(session, {
            "type": "error",
            "session_id": session.session_id,
            "message": message,
        })
        logger.error("Session %s ERROR: %s",
                     session.session_id, message)

    def _emit_finish(self, session: Session, reason: str) -> None:
        self._emit_message_raw(session, {
            "type": "finish",
            "session_id": session.session_id,
            "reason": reason,
        })

    def _emit_message_raw(self, session: Session,
                          payload: Dict[str, Any]) -> None:
        self._send_payload(session, payload)

    def _send_payload(self, session: Session,
                      payload: Dict[str, Any]) -> None:
        hnd = None
        try:
            hnd = DataHandle(CONFIG.notify_api)
            write_json(hnd.raw, payload)
            LF_UID_Sequenced_Notify(
                to_tp2p_uid(session.client_uid), hnd.raw
            )
        except Exception as e:
            logger.warning("Session %s send failed (%s): %s",
                           session.session_id,
                           payload.get('type'), e)
        finally:
            if hnd is not None:
                try:
                    hnd.free()
                except Exception:
                    pass

    def cleanup(self) -> None:
        if self._cleaned_up:
            return
        self._cleaned_up = True
        logger.info("Cleanup: signalling threads to stop...")
        self._shutdown_event.set()
        try:
            with self._sessions_lock:
                sessions = list(self._sessions.values())
        except Exception:
            sessions = []
        for sess in sessions:
            try:
                self._close_session_internal(sess, reason="shutdown")
            except Exception as e:
                logger.warning("Session close during shutdown "
                               "failed: %s", e)
        try:
            self._request_queue.put_nowait(None)
        except queue.Full:
            pass
        except Exception:
            pass
        if self._worker_thread is not None \
                and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=15.0)
            if self._worker_thread.is_alive():
                logger.warning("Worker thread did not exit in time")
        if self._watchdog_thread is not None \
                and self._watchdog_thread.is_alive():
            self._watchdog_thread.join(timeout=6.0)
        try:
            self.server.stop(full_cleanup=True)
        except Exception as e:
            logger.warning("Server stop error: %s", e)
        try:
            if hasattr(self.llm, "close"):
                self.llm.close()
        except Exception:
            pass
        logger.info("Cleanup complete")


def print_service_status() -> None:
    ctx_requested = ("auto (model maximum)" if CONFIG.context_size == 0
                     else str(CONFIG.context_size))
    if CONFIG.chat_template_resolved_path:
        template_display = CONFIG.chat_template_resolved_path
        template_display += " (explicit)"
    elif CONFIG.default_template is not None:
        template_display = "(from GGUF metadata)"
    else:
        template_display = "(none - create_chat_completion fallback)"
    rbm = CONFIG.reasoning_budget_message or ""
    rbm_preview = (rbm[:40] + "...") if len(rbm) > 40 else rbm
    rbm_preview = rbm_preview.replace("\n", "\\n")
    if not rbm_preview:
        rbm_preview = "(empty)"
    caps = _capabilities()
    supported, unsupported = split_supported(caps)
    print_banner(
        "LINGOFUSE LLM SERVICE STATUS",
        [
            [
                ("Server kind", SERVER_KIND),
                ("Backend", LLM_BACKEND),
                ("Model path", CONFIG.model_path),
                ("Context size requested", ctx_requested),
                ("Context size actual", CONFIG.context_size_actual),
                ("Default max tokens", CONFIG.max_tokens),
                ("CPU threads", CONFIG.threads),
                ("GPU layers offloaded", CONFIG.gpu_layers),
                ("Thinking (global)", CONFIG.thinking),
                ("Thinking (DEFAULT_)", DEFAULT_THINKING),
            ],
            [
                ("LingoFuse endpoint", CONFIG.endpoint),
                ("Service app name", CONFIG.app_name),
                ("Notify API name", CONFIG.notify_api),
                ("Routing model", "UID-addressed (v4.0)"),
                ("Session idle timeout(s)", CONFIG.session_timeout),
                ("Queue max size", CONFIG.queue_max_size),
                ("Max sessions", CONFIG.max_sessions),
                ("Max history per session", CONFIG.max_history),
                ("Log level", CONFIG.log_level),
            ],
            [
                ("Chat template", template_display),
                ("Reasoning budget msg", f'"{rbm_preview}"'),
                ("Attachments", "text only (image requires VLM)"),
                ("Vision",
                 "disabled (local VLM path not implemented - V2 TODO)"),
            ],
            [
                ("Watchdog policy",
                 "reclaim only when idle past the timeout AND the "
                 "client UID is offline"),
            ],
            [
                ("Supported APIs", join_list(supported)),
                ("Unsupported APIs", join_list(unsupported)),
            ],
        ],
    )


_shutdown_requested = threading.Event()
_console_stop_event = threading.Event()
_console_thread: Optional[threading.Thread] = None
_config_lock = threading.RLock()

_MUTABLE_FIELDS = {
    'thinking':        'bool',
    'session_timeout': 'int > 0 (seconds)',
    'max_tokens':      'int > 0',
    'max_sessions':    'int > 0',
    'max_history':     'int >= 2',
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
        "  thinking on|off              toggle the global thinking default",
        "  system <text>                update the global system message",
        "                               (applies to NEW sessions only)",
        "  health                       print the health JSON",
        "  set <key> <value>            change a mutable field",
        "  debug:<text>                 dispatch a C4 console command",
        "  quit / exit / q              stop the service and exit",
        "",
        "Mutable fields (set <key> <value>):",
        "  thinking         bool        global thinking default (NEW requests)",
        "  session_timeout  int > 0     idle timeout, seconds",
        "  max_tokens       int > 0     default max_tokens for NEW tasks",
        "  max_sessions     int > 0     maximum concurrent sessions",
        "  max_history      int >= 2    max messages per session (next trim)",
        "",
        "All other startup fields are frozen; 'status' reports the full list.",
    ])


def _console_cmd_status(service) -> str:
    with service._system_message_lock:
        sys_len = len(service._system_message)
    with service._sessions_lock:
        total = len(service._sessions)
        idle = sum(1 for s in service._sessions.values()
                   if s.status == "idle")
        running = total - idle
    worker_alive = (service._worker_thread is not None
                    and service._worker_thread.is_alive())
    watchdog_alive = (service._watchdog_thread is not None
                      and service._watchdog_thread.is_alive())
    return "\n".join([
        "=== LLM Service configuration ===",
        f"  endpoint           : {CONFIG.endpoint}",
        f"  app name           : {CONFIG.app_name}",
        f"  notify API         : {CONFIG.notify_api}",
        f"  server kind        : {SERVER_KIND}",
        f"  backend            : {service.backend}",
        f"  model path         : {CONFIG.model_path}",
        f"  context actual     : {CONFIG.context_size_actual} tokens",
        f"  thinking default   : {CONFIG.thinking}",
        f"  system message     : {sys_len} chars",
        f"  session timeout    : {CONFIG.session_timeout} s",
        f"  max sessions       : {CONFIG.max_sessions}",
        f"  max history        : {CONFIG.max_history}",
        f"  max tokens         : {CONFIG.max_tokens}",
        f"  log level          : {CONFIG.log_level}",
        f"  queue max size     : {CONFIG.queue_max_size}",
        "",
        "=== Runtime stats ===",
        f"  sessions total     : {total}",
        f"  sessions idle      : {idle}",
        f"  sessions running   : {running}",
        f"  queue pending      : {service._request_queue.qsize()}",
        f"  worker alive       : {worker_alive}",
        f"  watchdog alive     : {watchdog_alive}",
        "",
        f"Mutable at runtime  : {', '.join(sorted(_MUTABLE_FIELDS))}",
        "Frozen after startup: endpoint, app_name, notify_api, model_path,",
        "                      context_size, threads, gpu_layers, log_level,",
        "                      queue_max_size",
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
        idle_s = now - s.last_active_at
        lines.append(
            f"{s.session_id:36s}  {s.client_uid.hex()[:8]:8s}  "
            f"{s.status:8s}  {len(s.messages):5d}  {idle_s:8.1f}"
        )
    return "\n".join(lines)


def _console_cmd_close(service, sid: str) -> str:
    if not sid:
        return "usage: close <session_id>"
    sess = service._lookup_session(sid)
    if sess is None:
        return f"session not found: {sid}"
    service._close_session_internal(sess, reason=SESSION_CLOSE_REASON_CONSOLE)
    return f"session closed: {sid}"


def _console_cmd_cancel(service, sid: str) -> str:
    if not sid:
        return "usage: cancel <session_id>"
    sess = service._lookup_session(sid)
    if sess is None:
        return f"session not found: {sid}"
    with sess.lock:
        ev = sess.current_cancel_event
    if ev is None:
        return f"no active task for session: {sid}"
    ev.set()
    return f"cancel requested: {sid}"


def _console_cmd_thinking(arg: str) -> str:
    v = arg.strip().lower()
    if v in ('on', 'true', '1', 'yes'):
        with _config_lock:
            CONFIG.thinking = True
        return "thinking default = True (applies to NEW requests)"
    if v in ('off', 'false', '0', 'no'):
        with _config_lock:
            CONFIG.thinking = False
        return "thinking default = False (applies to NEW requests)"
    return (f"thinking default is currently {CONFIG.thinking}; "
            f"usage: thinking on|off")


def _console_cmd_system(service, text: str) -> str:
    if not text.strip():
        return "usage: system <text>"
    result = service._handle_set_system_message({"content": text})
    if result.get("code") == 0:
        return (f"system message updated ({len(text)} chars); "
                f"applies to NEW sessions only")
    return f"failed: {result.get('error', 'unknown error')}"


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
        if key == 'thinking':
            b = _parse_bool(value)
            with _config_lock:
                CONFIG.thinking = b
        elif key == 'session_timeout':
            n = int(value)
            if n <= 0:
                raise ValueError("must be a positive integer")
            with _config_lock:
                CONFIG.session_timeout = n
        elif key == 'max_tokens':
            n = int(value)
            if n <= 0:
                raise ValueError("must be a positive integer")
            with _config_lock:
                CONFIG.max_tokens = n
        elif key == 'max_sessions':
            n = int(value)
            if n <= 0:
                raise ValueError("must be a positive integer")
            with _config_lock:
                CONFIG.max_sessions = n
        elif key == 'max_history':
            n = int(value)
            if n < 2:
                raise ValueError("must be >= 2")
            with _config_lock:
                CONFIG.max_history = n
        else:
            return f"internal error: no handler for '{key}'"
    except (ValueError, TypeError) as e:
        return f"invalid value for {key!r}: {e}"
    return f"{key} = {getattr(CONFIG, key)!r}"


def _console_cmd_debug(payload: str) -> str:
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
    _shutdown_requested.set()
    print("Shutting down...", flush=True)


def _dispatch_console_command(service, line: str) -> Optional[str]:
    line = line.strip()
    if not line:
        return None
    if line.lower().startswith('debug:'):
        return _console_cmd_debug(line[len('debug:'):])
    import shlex
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
        return _console_cmd_close(service, tokens[1] if len(tokens) > 1 else '')
    if cmd == 'cancel':
        return _console_cmd_cancel(service, tokens[1] if len(tokens) > 1 else '')
    if cmd == 'thinking':
        return _console_cmd_thinking(tokens[1] if len(tokens) > 1 else '')
    if cmd == 'system':
        return _console_cmd_system(service, ' '.join(tokens[1:]))
    if cmd == 'health':
        return _console_cmd_health(service)
    if cmd == 'set':
        return _console_cmd_set(service, tokens)
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
        name="LLMServiceConsole",
        daemon=True,
    )
    _console_thread.start()
    logger.info("Console: interactive command reader started")


def _install_signal_handlers() -> None:
    def _handler(signum, frame):
        logger.info("Received signal %s; shutting down...", signum)
        _shutdown_requested.set()
    for sig in ("SIGINT", "SIGTERM"):
        s = getattr(signal, sig, None)
        if s is None:
            continue
        try:
            signal.signal(s, _handler)
        except (ValueError, OSError):
            pass


def main() -> None:
    args = parse_args()
    _init_global_config(args)
    setup_logging(CONFIG.log_level)
    if CONFIG.log_level >= 2:
        logger.debug("Effective configuration:")
        for k, v in vars(CONFIG).items():
            if k == "default_template" and v is not None:
                logger.debug("  %s = <%d bytes>", k, len(v))
            else:
                logger.debug("  %s = %s", k, v)
    if not os.path.exists(CONFIG.model_path):
        logger.error("Model file not found: %s", CONFIG.model_path)
        sys.exit(1)
    _resolve_explicit_template()
    service: Optional[LLMService] = None
    try:
        service = LLMService()
        print_service_status()
        _install_signal_handlers()
        service.server.start(CONFIG.endpoint)
        logger.info("LLM Service is running on %s", CONFIG.endpoint)
        logger.info("Press Ctrl+C to stop, or type 'help' at the console.")
        _start_console(service)
        while not _shutdown_requested.is_set():
            if _shutdown_requested.wait(timeout=1.0):
                break
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error("Service error: %s", e)
        traceback.print_exc()
        if service is not None:
            service.cleanup()
        sys.exit(1)
    finally:
        if service is not None:
            service.cleanup()


if __name__ == "__main__":
    main()