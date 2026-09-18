# -*- coding: utf-8 -*-
"""
llm_common.sse_client - OpenAI-compatible streaming client.

A minimal HTTP+SSE client built on the standard-library http.client
module. It is used by llm_proxy.py and llm_proxy_tool.py to talk to an
OpenAI-compatible backend (LM Studio, Ollama, vLLM, DeepSeek,
OpenRouter, Azure OpenAI, ...).

Why http.client instead of requests
-----------------------------------
requests / urllib3 buffer the SSE response at the socket layer in
ways that no combination of iter_content / iter_lines /
Accept-Encoding settings can disable. The symptom is multi-second
silence followed by a batch of text, even though the backend is
streaming steadily.

http.client's HTTPResponse wraps the raw socket in a BufferedReader;
iterating over the response yields a line the moment its terminating
newline arrives. Combined with Accept-Encoding: identity and
TCP_NODELAY, this gives true real-time streaming.

This module has NO third-party dependencies. It imports only from the
Python standard library (http.client, json, logging, socket, ssl,
urllib.parse). The earlier implementation used `requests` for the
list_models() probe; that dependency was removed so that the two
proxy siblings can be packaged and distributed without pulling in
urllib3 / certifi / idna / charset_normalizer.

Streamed event shapes
---------------------
`stream_chat` yields dicts with any of the following keys:

    {"content": "..."}              - user-visible answer chunk
    {"reasoning_content": "..."}    - thinking / reasoning chunk
    {"tool_calls": [ ... ]}         - fully-assembled tool calls,
                                      yielded exactly once at the
                                      end of the stream

Text events are yielded as soon as they arrive. Tool-call fragments
are accumulated by `index` and yielded once, after `[DONE]` is seen
or the stream closes, because the OpenAI wire format splits a single
tool call across many SSE frames and the `arguments` field is a
string that must be concatenated, not merged.

Callers that do not use tools simply ignore the `tool_calls` key.

Thread safety
-------------
One instance may be shared by many threads. Each call to `stream_chat`
and `list_models` creates its own HTTP connection, so there is no
shared connection state. The configuration fields are read-only after
__init__.

This module depends on llm_common.headers for jdump.
"""

import http.client
import json
import logging
import socket
import ssl
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import urlparse

from .headers import jdump


logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Defaults
# ----------------------------------------------------------------------

DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_AUTH_HEADER = "Authorization"
DEFAULT_AUTH_SCHEME = "Bearer"

# Timeout (seconds) used by list_models(). This is intentionally much
# shorter than the streaming timeout: a model listing is a small
# request that should return in well under a second on any sane
# backend, and a slow /models endpoint usually means the backend is
# not actually ready. Callers can override per-instance if needed.
DEFAULT_LIST_MODELS_TIMEOUT = 15

# Hard cap on the number of bytes we are willing to read from the
# /v1/models response. A compliant backend returns a small JSON
# document (a few KB to a few hundred KB). Anything larger is either
# a misconfigured proxy or a malicious endpoint; refusing to read it
# avoids allocating an unbounded response buffer.
_MAX_MODELS_RESPONSE_BYTES = 10 * 1024 * 1024  # 10 MB

# Scalar options forwarded verbatim to the backend.
_FORWARDED_SCALAR_KEYS = (
    "max_tokens",
    "temperature",
    "top_p",
    "top_k",
    "repeat_penalty",
)

# Passthrough options forwarded verbatim.
#
# Each key in this tuple is copied verbatim from `options` into the
# backend payload when its value is not None. The proxy does not
# interpret the value; it is the backend's responsibility to validate
# and act on it.
#
#   - "tools" / "tool_choice":
#         Standard OpenAI function-calling controls. Used by
#         llm_proxy_tool (LTB) to drive server-side tool execution.
#
#   - "response_format":
#         Structured Output control. When present, the backend is asked
#         to constrain its reply to a caller-supplied JSON Schema (or to
#         the simpler {"type": "json_object"} form). LM Studio, Ollama,
#         vLLM, and other OpenAI-compatible backends honour this field.
#         Used by vision clients to obtain detector-style bounding-box
#         JSON from multimodal models.
#
# NOTE: the forwarding loop in stream_chat() is data-driven over this
# tuple. Adding a key here is the only change required to make the
# proxy forward it; no branch inside stream_chat() needs to change.
_FORWARDED_PASSTHROUGH_KEYS = (
    "tools",
    "tool_choice",
    "response_format",
)


# ----------------------------------------------------------------------
# Client
# ----------------------------------------------------------------------

class OpenAIStreamClient:
    """
    HTTP+SSE client for OpenAI-compatible chat completions.

    The client is stateless between calls: each `stream_chat` opens its
    own connection and closes it in a finally block. This makes the
    instance safe to share across threads without locking.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        auth_header: str = DEFAULT_AUTH_HEADER,
        auth_scheme: str = DEFAULT_AUTH_SCHEME,
        extra_headers: Optional[Dict[str, str]] = None,
        list_models_timeout: int = DEFAULT_LIST_MODELS_TIMEOUT,
    ) -> None:
        """
        Args:
            base_url:
                Base URL of the backend, without the trailing
                /chat/completions. For example:
                    http://127.0.0.1:1234/v1
                    https://api.deepseek.com/v1
                A trailing slash is stripped automatically.

            api_key:
                Token sent in the auth header. Empty means no auth.

            timeout:
                Read timeout for the streaming connection, in seconds.
                Applied to the socket, not to individual SSE frames, so
                a long generation that keeps sending frames will not
                be interrupted by the timeout.

            auth_header:
                Name of the HTTP header carrying the token. Defaults to
                "Authorization". Set to "api-key" for Azure OpenAI.

            auth_scheme:
                Prefix placed before the token. Defaults to "Bearer".
                Set to "" for Azure, which expects a raw token.

            extra_headers:
                Additional headers to send on every request.

            list_models_timeout:
                Read timeout for the /v1/models probe, in seconds.
                Defaults to DEFAULT_LIST_MODELS_TIMEOUT (15). Kept
                separate from `timeout` because model listing and
                streaming have very different latency profiles.
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.auth_header = auth_header
        self.auth_scheme = auth_scheme
        self.extra_headers = dict(extra_headers or {})
        self.list_models_timeout = list_models_timeout

    # ------------------------------------------------------------------
    # Header construction
    # ------------------------------------------------------------------

    def _build_headers(self, accept: str) -> Dict[str, str]:
        """
        Build the request headers for one call.

        Always sets:
            Content-Type: application/json
            Accept: <accept>
            Accept-Encoding: identity

        The identity encoding is required for SSE: any compression
        layer (gzip, br) buffers complete deflate blocks before
        emitting data, which destroys incremental delivery.

        If auth_header and api_key are both set, the token is added as
        "<auth_header>: <scheme> <key>" (or "<auth_header>: <key>" if
        scheme is empty).

        extra_headers are merged last, so a caller can override any
        previously set header if it needs to.
        """
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": accept,
            "Accept-Encoding": "identity",
        }
        if self.auth_header and self.api_key:
            if self.auth_scheme:
                headers[self.auth_header] = (
                    f"{self.auth_scheme} {self.api_key}"
                )
            else:
                headers[self.auth_header] = self.api_key
        headers.update(self.extra_headers)
        return headers

    # ------------------------------------------------------------------
    # URL parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _split_url(url: str):
        """
        Split a URL into (host, port, path, is_https) for http.client.

        Shared by stream_chat() and list_models() so that both paths
        agree on scheme handling, default ports, and query-string
        preservation. Returns a 4-tuple; callers build the connection
        themselves so that per-call timeouts can differ.
        """
        parsed = urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port
        path = parsed.path or "/"
        if parsed.query:
            path = path + "?" + parsed.query
        is_https = parsed.scheme == "https"
        if port is None:
            port = 443 if is_https else 80
        return host, port, path, is_https

    # ------------------------------------------------------------------
    # Model discovery
    # ------------------------------------------------------------------

    def list_models(self) -> List[str]:
        """
        Probe `/v1/models` and return the list of model IDs.

        This is a best-effort helper used to auto-discover the backend
        model when the operator did not specify one. It is implemented
        with http.client (no requests dependency), reads at most
        _MAX_MODELS_RESPONSE_BYTES from the response, and never
        raises: on any failure it logs a warning and returns an empty
        list.

        Returns:
            A list of model IDs. Empty list on any failure.
        """
        url = f"{self.base_url}/models"
        headers = self._build_headers("application/json")

        # Accept-Encoding: identity is harmless here and avoids the
        # decoder overhead. The models endpoint is a single small JSON
        # document, so buffering is not a concern.
        conn = None
        try:
            host, port, path, is_https = self._split_url(url)

            if is_https:
                ctx = ssl.create_default_context()
                conn = http.client.HTTPSConnection(
                    host, port,
                    timeout=self.list_models_timeout,
                    context=ctx,
                )
            else:
                conn = http.client.HTTPConnection(
                    host, port,
                    timeout=self.list_models_timeout,
                )

            # skip_accept_encoding=True stops http.client from adding
            # its own Accept-Encoding header. We already set one in
            # _build_headers, and duplicating it would produce two
            # headers with the same name.
            conn.putrequest("GET", path, skip_accept_encoding=True)
            for k, v in headers.items():
                conn.putheader(k, v)
            conn.endheaders()

            resp = conn.getresponse()

            if resp.status != 200:
                logger.warning(
                    "Backend /v1/models returned HTTP %d; "
                    "model auto-discovery skipped", resp.status,
                )
                return []

            # Guard against a pathological response size. resp.read()
            # with a length argument reads at most that many bytes and
            # returns early if the connection closes first.
            raw = resp.read(_MAX_MODELS_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_MODELS_RESPONSE_BYTES:
                logger.warning(
                    "Backend /v1/models response exceeds %d bytes; "
                    "model auto-discovery skipped",
                    _MAX_MODELS_RESPONSE_BYTES,
                )
                return []

            try:
                data = json.loads(raw.decode("utf-8", errors="replace"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.warning(
                    "Backend /v1/models returned invalid JSON: %s", e,
                )
                return []

            if not isinstance(data, dict):
                logger.warning(
                    "Backend /v1/models returned a non-object JSON "
                    "document; model auto-discovery skipped",
                )
                return []

            ids: List[str] = []
            for entry in data.get("data", []) or []:
                if not isinstance(entry, dict):
                    continue
                mid = entry.get("id")
                if isinstance(mid, str) and mid:
                    ids.append(mid)
            return ids

        except (socket.timeout, TimeoutError) as e:
            logger.warning(
                "Backend /v1/models request timed out after %ds: %s",
                self.list_models_timeout, e,
            )
            return []
        except (OSError, http.client.HTTPException, ssl.SSLError) as e:
            logger.warning(
                "Failed to query backend /v1/models: %s", e,
            )
            return []
        except Exception as e:
            # Defensive: a helper that decides "what model should I
            # use" must never crash the caller. Any unexpected error
            # degrades to "no models discovered".
            logger.warning(
                "Unexpected error while listing backend models: %s", e,
            )
            return []
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    def stream_chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        options: Optional[Dict[str, Any]] = None,
        cancel_event=None,
    ) -> Iterator[Dict[str, Any]]:
        """
        Stream a chat completion from the backend.

        Args:
            model:
                Model ID sent in the request body.

            messages:
                OpenAI-format messages array. Each message is a dict
                with "role" and "content". The content may be either a
                string (plain text) or a list of content parts (for
                multi-modal input).

            options:
                Optional dict of sampler options. Only the keys in
                _FORWARDED_SCALAR_KEYS and _FORWARDED_PASSTHROUGH_KEYS
                are forwarded; everything else is ignored.

                Note: "response_format" is one of the passthrough keys.
                When present (and not None), it is copied verbatim
                into the backend payload, enabling Structured Output
                (JSON Schema / json_object) on backends that support it.

            cancel_event:
                Optional threading.Event. When set, the loop stops
                yielding and returns cleanly.

        Yields:
            Dicts as described in the module docstring. The final
            event, if any tool calls were accumulated, is a
            {"tool_calls": [...]} dict.

        Raises:
            TypeError: if `messages` is not a list. This is a defensive
                check to catch programming errors early: a malformed
                messages payload would otherwise be sent to the
                backend and rejected with an opaque 400.

            RuntimeError: if the backend returns an HTTP status >= 400.
                The error message includes the status code and up to
                500 characters of the response body.
        """
        if not isinstance(messages, list):
            raise TypeError(
                f"messages must be a list, got {type(messages).__name__}"
            )

        options = options or {}
        url = f"{self.base_url}/chat/completions"
        headers = self._build_headers("text/event-stream")

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        for key in _FORWARDED_SCALAR_KEYS:
            if key in options and options[key] is not None:
                payload[key] = options[key]
        for key in _FORWARDED_PASSTHROUGH_KEYS:
            if key in options and options[key] is not None:
                payload[key] = options[key]

        body = jdump(payload)

        host, port, path, is_https = self._split_url(url)

        logger.debug(
            "Backend request: url=%s model=%s msgs=%d tools=%s "
            "response_format=%s",
            url, model, len(messages),
            "yes" if payload.get("tools") else "no",
            "yes" if payload.get("response_format") else "no",
        )

        if is_https:
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(
                host, port, timeout=self.timeout, context=ctx,
            )
        else:
            conn = http.client.HTTPConnection(
                host, port, timeout=self.timeout,
            )

        # Disable Nagle so that small SSE frames are sent immediately
        # rather than being coalesced with the next write. A failure
        # here is not fatal; the stream will still work, just with
        # slightly higher latency on some networks.
        try:
            if conn.sock is None:
                conn.connect()
            try:
                conn.sock.setsockopt(
                    socket.IPPROTO_TCP, socket.TCP_NODELAY, 1,
                )
            except Exception:
                pass
        except Exception:
            pass

        try:
            conn.putrequest(
                "POST", path, skip_accept_encoding=True,
            )
            for k, v in headers.items():
                conn.putheader(k, v)
            conn.putheader("Content-Length", str(len(body)))
            conn.endheaders()
            conn.send(body)

            resp = conn.getresponse()
            logger.debug(
                "Backend response: status=%d content_type=%r",
                resp.status, resp.getheader("Content-Type"),
            )

            if resp.status >= 400:
                try:
                    detail = resp.read().decode(
                        "utf-8", errors="replace",
                    )[:500]
                except Exception:
                    detail = ""
                raise RuntimeError(
                    f"Backend HTTP {resp.status}: {detail}"
                )

            yield from self._read_sse_stream(resp, cancel_event)

        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _read_sse_stream(
        self,
        resp,
        cancel_event,
    ) -> Iterator[Dict[str, Any]]:
        """
        Parse an SSE response and yield delta dicts.

        This is split out of `stream_chat` so that the connection
        lifecycle (open / close / error handling) stays in one place
        and the parsing logic can be tested independently if needed.

        Tool-call fragments are accumulated by `index`. The
        `arguments` field is a string that arrives in pieces; it is
        concatenated, never JSON-parsed until the stream ends.
        """
        tool_calls_acc: Dict[int, Dict[str, Any]] = {}

        for raw_line in resp:
            if cancel_event is not None and cancel_event.is_set():
                logger.debug("Backend stream cancelled by client")
                return

            try:
                line = raw_line.decode(
                    "utf-8", errors="replace",
                ).rstrip("\r\n")
            except Exception:
                continue

            # SSE frames are "data: <payload>". Anything else (comments,
            # event: lines, blank keep-alive lines) is skipped.
            if not line.startswith("data: "):
                continue

            data = line[6:].strip()
            if data == "[DONE]":
                break

            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue

            delta = self._extract_delta(obj)
            if not delta:
                continue

            # Text events are emitted immediately so that the client
            # sees progress in real time, even during tool-calling
            # rounds where the answer may be empty.
            if "content" in delta or "reasoning_content" in delta:
                yield delta

            # Tool-call fragments are accumulated and not yielded yet.
            if "tool_calls" in delta:
                self._accumulate_tool_calls(
                    tool_calls_acc, delta["tool_calls"],
                )

        if tool_calls_acc:
            ordered = [tool_calls_acc[i]
                       for i in sorted(tool_calls_acc)]
            yield {"tool_calls": ordered}

    @staticmethod
    def _accumulate_tool_calls(
        acc: Dict[int, Dict[str, Any]],
        fragments: List[Any],
    ) -> None:
        """
        Merge one batch of tool_call fragments into the accumulator.

        The OpenAI wire format sends each tool call across many SSE
        frames. The first frame for an index carries the id and the
        function name; subsequent frames carry pieces of the
        `arguments` string, which must be concatenated.

        Fragments that are not dicts are ignored. Missing fields do
        not overwrite existing values; only non-empty strings are
        written.
        """
        for tc in fragments:
            if not isinstance(tc, dict):
                continue

            idx = tc.get("index", 0)
            if not isinstance(idx, int):
                idx = 0

            if idx not in acc:
                acc[idx] = {
                    "id": "",
                    "type": "function",
                    "function": {
                        "name": "",
                        "arguments": "",
                    },
                }

            if tc.get("id"):
                acc[idx]["id"] = tc["id"]

            fn = tc.get("function") or {}
            if not isinstance(fn, dict):
                continue

            if fn.get("name"):
                acc[idx]["function"]["name"] = fn["name"]
            if fn.get("arguments"):
                acc[idx]["function"]["arguments"] += fn["arguments"]

    @staticmethod
    def _extract_delta(
        obj: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Extract content / reasoning_content / tool_calls from one SSE
        frame. Does not accumulate; accumulation lives in the caller.

        Returns None if the frame carries no usable delta. This covers
        the common cases of role-only frames, keep-alive frames, and
        frames whose choices array is empty.

        Only str-typed `content` and `reasoning_content` are accepted.
        A few backends occasionally emit a content array (the multi-
        modal request shape) inside a streaming delta; forwarding that
        as a chunk would corrupt the client's text accumulator, so it
        is treated as "no text this frame". Tool calls are handled by
        a separate key and are unaffected.
        """
        if not isinstance(obj, dict):
            return None

        choices = obj.get("choices") or []
        if not choices:
            return None

        choice = choices[0]
        if not isinstance(choice, dict):
            return None

        delta = choice.get("delta") or {}
        if not isinstance(delta, dict):
            return None

        out: Dict[str, Any] = {}

        content = delta.get("content")
        if isinstance(content, str) and content:
            out["content"] = content

        reasoning = delta.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            out["reasoning_content"] = reasoning

        tcs = delta.get("tool_calls")
        if isinstance(tcs, list) and tcs:
            out["tool_calls"] = tcs

        return out or None