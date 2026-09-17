# -*- coding: utf-8 -*-
"""
llm_common.session_base - Plain-text session state base class.

A SessionState holds one ongoing conversation:

  - a fixed `client_name` (destination for streaming notifications)
  - a fixed `system_message` (snapshotted at creation time)
  - a message history that grows with each successful generation
  - status flags used by the request handler and the watchdog

The base class is deliberately plain-text only. It knows nothing about
tool calls, attachments, or multi-modal content. The two subclasses in
the toolchain extend it:

  - llm_proxy.py uses SessionState directly (plain text proxy).
  - llm_proxy_tool.py uses MultimodalSessionState (defined in
    session_multimodal.py), which adds tool-call message methods and a
    character-budget-aware trimming policy.

  - llm_service.py currently keeps its OWN Session class because it
    tracks a three-value `status` field ("idle" / "running" /
    "closing") and a `current_cancel_event` alias. The compatibility
    properties at the bottom of this class are RESERVED for a future
    migration of llm_service to this base; they are documented but not
    used by any code in the current codebase. Do not assume that
    llm_service.Session is an alias of this class.

Thread safety
-------------
Every mutation of `self.messages` and every read-modify-write on the
status flags is guarded by `self.lock`. Callers must NOT hold the lock
while doing I/O (network, disk, logging to a slow sink); acquire,
mutate, release.

`begin_run` / `try_begin_run` / `end_run` are the intended ways to
flip the `running` flag. Direct assignment is possible but discouraged:
the methods keep `cancel_event` and `running` in sync.

`touch()` is called by the request handler when a new `generate`
arrives. The watchdog uses `last_active` to decide whether a session
is idle.

This module has no dependencies on any other llm_common module.
"""

import threading
import time
from typing import Any, Dict, List, Optional


# Canonical default cap on the number of messages retained per session.
# Callers override this via the constructor. The three services each
# declare a local `DEFAULT_MAX_HISTORY` constant of the same value;
# those copies exist so that each service file is readable in
# isolation. If the canonical value ever changes, the services should
# be updated together, or better, they should import this constant.
DEFAULT_MAX_HISTORY = 512


class SessionState:
    """
    One persistent conversation, plain-text only.

    Attributes (all public, but treat as read-mostly):
        session_id      Unique ID assigned by the caller.
        client_name     LingoFuse app name that receives notifications.
        system_message  Snapshot of the system prompt at creation.
        messages        List of OpenAI-format message dicts.
        created_at      Unix timestamp at construction.
        last_active     Unix timestamp of the most recent activity.
        cancel_event    threading.Event while a run is in progress,
                        None otherwise.
        running         True while a run is in progress.
        lock            threading.Lock guarding all mutations.
        max_history     Per-session message count cap.
    """

    def __init__(
        self,
        session_id: str,
        client_name: str,
        system_message: str = "",
        max_history: int = DEFAULT_MAX_HISTORY,
    ) -> None:
        """
        Args:
            session_id:      Unique ID assigned by the caller. The base
                             class does not generate one; callers use
                             uuid.uuid4() or an equivalent scheme.
            client_name:     LingoFuse app name that receives streaming
                             notifications for this session.
            system_message:  System prompt for this session. Captured
                             at construction and never changed. The
                             empty string means "no system message".
            max_history:     Maximum number of messages retained. The
                             oldest messages are dropped first when
                             the cap is exceeded. Negative values are
                             clamped to 0 (which means "keep no
                             history"), matching the historical
                             behaviour of always treating the count as
                             a non-negative limit.
        """
        self.session_id = session_id
        self.client_name = client_name
        self.system_message = system_message
        # Clamp: a negative max_history would otherwise translate into
        # an `excess` larger than the list length, silently wiping the
        # entire history on every append.
        self.max_history = max(0, int(max_history))

        self.messages: List[Dict[str, Any]] = []

        # Wall-clock timestamps (seconds since the Unix epoch). This is
        # the same unit used by llm_service.Session and by the
        # list_sessions response shape, so that a client parsing the
        # timestamps sees a consistent meaning across all three
        # sibling services.
        self.created_at = time.time()
        self.last_active = self.created_at

        self.cancel_event: Optional[threading.Event] = None
        self.running: bool = False

        self.lock = threading.Lock()

    # ------------------------------------------------------------------
    # Message mutators
    # ------------------------------------------------------------------

    def add_user(self, content: Any) -> None:
        """
        Append a user message.

        `content` may be a plain string (text-only request) or a list
        of OpenAI content parts (multi-modal request). The base class
        does not interpret it; the caller is responsible for building
        the correct shape.
        """
        with self.lock:
            self.messages.append({"role": "user", "content": content})
            self._trim_locked()

    def add_assistant(self, content: str) -> None:
        """
        Append an assistant message.

        Always a plain string: the assistant's final answer is text,
        even for multi-modal requests.
        """
        with self.lock:
            self.messages.append({
                "role": "assistant",
                "content": content,
            })
            self._trim_locked()

    # ------------------------------------------------------------------
    # Trimming (overridable)
    # ------------------------------------------------------------------

    def _trim_locked(self) -> None:
        """
        Enforce the message-count cap.

        Called under `self.lock` by every mutator. Drops the oldest
        messages until the count is at or below `max_history`.

        Subclasses may override this to add additional constraints,
        for example a total character budget or an invariant that must
        not split an assistant.tool_calls message from its matching
        role=tool replies. Overrides MUST keep the base behaviour
        (count cap) in addition to whatever they add.

        The base implementation is deliberately simple: a single
        `del` on the front of the list. This is O(n) per call, but n
        is bounded by `max_history` and mutators are called at most a
        few times per generation, so the cost is negligible.
        """
        if len(self.messages) <= self.max_history:
            return
        excess = len(self.messages) - self.max_history
        del self.messages[:excess]

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    def snapshot(self) -> List[Dict[str, Any]]:
        """
        Return a shallow copy of the message list for safe iteration.

        The copy protects the caller from concurrent appends while it
        is building the request payload. The nested dicts are shared
        references; callers must not mutate them. In particular, a
        multi-modal user message whose `content` is a list shares that
        list with the original message, so appending to
        `snapshot[i]["content"]` would corrupt the session.

        Returns:
            A new list containing the same message dict objects.
        """
        with self.lock:
            return [dict(m) for m in self.messages]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def touch(self) -> None:
        """
        Update `last_active` to now.

        Called by the request handler when a new generate request
        arrives. The watchdog uses `last_active` to decide whether a
        session has been idle long enough to reclaim.
        """
        with self.lock:
            self.last_active = time.time()

    def begin_run(self) -> threading.Event:
        """
        Mark the session as running and install a fresh cancel event.

        Returns:
            The new threading.Event. The caller passes it to the
            worker so that `request_cancel` can signal it.

        Note:
            This method does NOT check whether a run is already in
            progress. If a previous run is still marked as running,
            its event is discarded and a fresh one is installed. This
            is intentional for callers that own the state machine and
            know they are starting a new run unconditionally.

            Callers that want the safe "start only if idle" semantics
            should use `try_begin_run()` instead; that method performs
            the check and the state change in the same critical
            section.
        """
        with self.lock:
            ev = threading.Event()
            self.cancel_event = ev
            self.running = True
            return ev

    def try_begin_run(self) -> Optional[threading.Event]:
        """
        Atomically start a run if the session is not already running.

        This is the recommended entry point for request handlers that
        must reject concurrent generations on the same session. It
        performs the running check and the state transition inside a
        single critical section, so two concurrent callers cannot
        both succeed.

        Returns:
            A fresh threading.Event on success. None if a run was
            already in progress, in which case the session state is
            left untouched and the caller should reject the request
            (typically with an "already running" error).
        """
        with self.lock:
            if self.running:
                return None
            ev = threading.Event()
            self.cancel_event = ev
            self.running = True
            return ev

    def end_run(self) -> None:
        """
        Clear the running flag and the cancel event, and touch the
        session.

        Called from the worker's finally block so that the session is
        never left stuck in the running state, even if an exception
        escaped the main loop. Calling this when no run is in progress
        is harmless.
        """
        with self.lock:
            self.cancel_event = None
            self.running = False
            self.last_active = time.time()

    def request_cancel(self) -> bool:
        """
        Signal the currently running task to cancel.

        Returns:
            True  if a run was in progress and its event was set.
            False if no run was in progress (nothing to cancel).
        """
        with self.lock:
            if self.cancel_event is None:
                return False
            self.cancel_event.set()
            return True

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def to_summary(self) -> Dict[str, Any]:
        """
        Return a JSON-serializable summary of this session.

        The shape is stable and used by `list_sessions` responses:

            {
              "session_id":      "...",
              "client_name":     "...",
              "created_at":      1234567890.123,
              "last_active_at":  1234567890.456,
              "status":          "running" | "idle",
              "message_count":   7,
            }

        `last_active_at` and `status` are the field names historically
        used by llm_service.py's list_sessions response, so the same
        client code can parse responses from any server kind.

        The timestamps are Unix wall-clock seconds (as returned by
        time.time()), matching llm_service.Session. Clients that
        format them for display get consistent output across all
        three sibling services.
        """
        with self.lock:
            return {
                "session_id": self.session_id,
                "client_name": self.client_name,
                "created_at": self.created_at,
                "last_active_at": self.last_active,
                "status": "running" if self.running else "idle",
                "message_count": len(self.messages),
            }

    # ------------------------------------------------------------------
    # Compatibility aliases (RESERVED - currently unused)
    # ------------------------------------------------------------------
    #
    # These properties expose the same state under the historical field
    # names used by llm_service.Session. They are read-only; writes go
    # through the canonical fields (last_active / cancel_event) or
    # through the lifecycle methods (begin_run / try_begin_run /
    # end_run).
    #
    # They exist so that IF llm_service is ever migrated to this base
    # class, its existing callers keep working without a rename pass.
    # In the current codebase no caller uses them; treat them as
    # reserved API and do not rely on them being removed.

    @property
    def last_active_at(self) -> float:
        """
        Read-only alias for `last_active`, matching llm_service's
        historical field name. Reserved for a future migration; not
        currently used by any caller.
        """
        return self.last_active

    @property
    def current_cancel_event(self) -> Optional[threading.Event]:
        """
        Read-only alias for `cancel_event`, matching llm_service's
        historical field name. Reserved for a future migration; not
        currently used by any caller.
        """
        return self.cancel_event