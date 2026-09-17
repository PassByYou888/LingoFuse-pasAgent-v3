# -*- coding: utf-8 -*-
"""
llm_common.session_multimodal - Session state for the tool bridge.

MultimodalSessionState extends SessionState with:

  - message kinds that only exist in a tool-calling conversation
        assistant.tool_calls     (the model asked to call tools)
        role=tool                (the result of one tool call)
  - a character-budget-aware trimming policy so that long-lived
    sessions do not grow without bound when tool results are large
  - an invariant that never splits an assistant.tool_calls message
    from its matching role=tool replies

The name "multimodal" reflects the fact that this class is used by the
tool bridge (LTB), which after the attachment upgrade may also carry
multi-modal user content. The class itself does not distinguish
between plain text and multi-modal user messages: the base class
already accepts `Any` for user content, and the tool-call logic is
orthogonal to the content shape.

Message shapes
--------------
The messages list may contain any of:

    {"role": "user",      "content": "..." or [content parts]}
    {"role": "assistant", "content": "..."}
    {"role": "assistant", "content": None, "tool_calls": [ ... ]}
    {"role": "tool",      "tool_call_id": "...", "content": "..."}

The first three are handled by the base class or by the methods below.
The last two are specific to a tool-calling conversation.

Trimming invariants
-------------------
The OpenAI wire format requires that every id listed in an
assistant.tool_calls message has a matching role=tool reply, in the
same order, immediately following. If trimming ever removed an
assistant.tool_calls message but left its role=tool replies, the next
request to the backend would be rejected with a 400.

To prevent this, `_trim_locked` treats the group

    [assistant.tool_calls, role=tool, role=tool, ...]

as an atomic unit. When the group at the head of the list must be
dropped, all of its members are dropped together. As an extra
defensive measure, the trim loop also drops any leftover role=tool
message that ends up at the head after a removal, so the head of the
list is never a role=tool message.

Performance
-----------
The character budget is maintained incrementally:

  - Every mutator (add_user, add_assistant, add_assistant_tool_calls,
    add_tool_result) adds the new message's estimated character count
    to `_total_chars_cache` while holding self.lock.
  - Every removal inside `_trim_locked` subtracts the removed
    message's estimated character count from the same cache.
  - `_total_chars_locked()` is therefore O(1) and the trim loop is
    O(k) where k is the number of messages actually removed.

The previous implementation recomputed the total by iterating over
the full message list on every trim iteration, which was O(n^2) and
noticeable once a session held thousands of messages.

As a self-healing safeguard, `_trim_locked` checks whether the cache
has gone negative; if so it rebuilds it in one O(n) pass. This can
only happen if a future mutator forgets to update the cache, and the
rebuild keeps the rest of the code correct.

This module depends on llm_common.session_base only.
"""

from typing import Any, Dict, List, Optional

from .session_base import (
    DEFAULT_MAX_HISTORY,
    SessionState,
)


# Default character budget for one session's history. The exact value
# is a policy decision; callers override it via the constructor.
DEFAULT_MAX_HISTORY_CHARS = 200_000

# Rough per-tool_call character cost, used by the character budget
# calculation. A single tool_call entry as serialized by the backend
# is typically 80 to 150 characters of JSON (id + type + function
# name + arguments). 100 is a conservative middle estimate.
_TOOL_CALL_CHAR_ESTIMATE = 100

# Rough per-image character cost, used when a message content list
# carries an image_url part. This branch is not exercised by the
# current tool bridge (which stores images as short "[image: name]"
# placeholders), but is kept so that if a future revision stores
# image content directly in history, the budget calculation remains
# monotonic and non-zero for images.
_IMAGE_PART_CHAR_ESTIMATE = 500


class MultimodalSessionState(SessionState):
    """
    Session state for a tool-calling conversation.

    All the base-class methods are inherited unchanged. The additions
    are the two tool-message mutators and an override of
    `_trim_locked` that adds a character budget and the group
    invariant described in the module docstring.
    """

    def __init__(
        self,
        session_id: str,
        client_name: str,
        system_message: str = "",
        max_history: int = DEFAULT_MAX_HISTORY,
        max_history_chars: int = DEFAULT_MAX_HISTORY_CHARS,
    ) -> None:
        """
        Args:
            session_id, client_name, system_message, max_history:
                Forwarded to SessionState unchanged.

            max_history_chars:
                Maximum total character count of the message history.
                The oldest groups are dropped until the count AND the
                character total are both within their limits.
        """
        super().__init__(
            session_id=session_id,
            client_name=client_name,
            system_message=system_message,
            max_history=max_history,
        )
        self.max_history_chars = max_history_chars

        # Incremental character counter. Always equal to
        # sum(_chars_of(m) for m in self.messages) while the lock is
        # held. Initialized to 0 because the base class starts with an
        # empty message list.
        self._total_chars_cache: int = 0

    # ------------------------------------------------------------------
    # Character cost of one message
    # ------------------------------------------------------------------

    @staticmethod
    def _chars_of(message: Dict[str, Any]) -> int:
        """
        Estimate the character count of a single message.

        The estimate is deliberately approximate: it exists to cap
        unbounded growth, not to match a tokenizer. Tool calls and
        image parts use fixed per-entry estimates because their exact
        serialized length is not known without re-serializing, which
        would be expensive and pointless for a safety valve.

        This helper is O(1) with respect to the size of the history;
        it only inspects the one message passed in.
        """
        total = 0
        c = message.get("content")
        if isinstance(c, str):
            total += len(c)
        elif isinstance(c, list):
            # Multi-modal user content: sum the text parts and count
            # each image part at a fixed size. The exact base64 length
            # is already reflected in the memory cost, so this
            # estimate only needs to be monotonic.
            for part in c:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        t = part.get("text")
                        if isinstance(t, str):
                            total += len(t)
                    elif part.get("type") == "image_url":
                        # See module docstring: kept for future
                        # direct-storage of images in history.
                        total += _IMAGE_PART_CHAR_ESTIMATE
        tcs = message.get("tool_calls")
        if isinstance(tcs, list):
            total += _TOOL_CALL_CHAR_ESTIMATE * len(tcs)
        return total

    # ------------------------------------------------------------------
    # Message mutators (override)
    # ------------------------------------------------------------------
    #
    # The base class implements add_user / add_assistant directly on
    # self.messages. We override them here so that the character cache
    # is updated in the same critical section as the append. The lock
    # is the same lock object the base class uses (self.lock), so
    # there is no additional synchronization cost.

    def add_user(self, content: Any) -> None:
        """
        Append a user message and update the character cache.

        `content` may be a plain string (text-only request) or a list
        of OpenAI content parts (multi-modal request). This override
        does not interpret it; the shape is the caller's
        responsibility, exactly as in the base class.
        """
        with self.lock:
            msg: Dict[str, Any] = {"role": "user", "content": content}
            self.messages.append(msg)
            self._total_chars_cache += self._chars_of(msg)
            self._trim_locked()

    def add_assistant(self, content: str) -> None:
        """
        Append an assistant message and update the character cache.

        Always a plain string: the assistant's final answer is text,
        even for multi-modal requests.
        """
        with self.lock:
            msg: Dict[str, Any] = {
                "role": "assistant",
                "content": content,
            }
            self.messages.append(msg)
            self._total_chars_cache += self._chars_of(msg)
            self._trim_locked()

    def add_assistant_tool_calls(
        self,
        tool_calls: List[Dict[str, Any]],
    ) -> None:
        """
        Append an assistant message that carries tool_calls.

        The content is set to None (not ""), matching the OpenAI wire
        format: a message whose purpose is to request tool calls has
        no assistant text of its own.

        Args:
            tool_calls: List of tool-call dicts in the OpenAI format.
                Each entry must have at least an "id" and a
                "function" object with "name" and "arguments".
        """
        with self.lock:
            msg: Dict[str, Any] = {
                "role": "assistant",
                "content": None,
                "tool_calls": tool_calls,
            }
            self.messages.append(msg)
            self._total_chars_cache += self._chars_of(msg)
            self._trim_locked()

    def add_tool_result(
        self,
        tool_call_id: str,
        content: str,
    ) -> None:
        """
        Append a role=tool message carrying the result of one tool
        call.

        The tool_call_id must match the id of an entry in the most
        recent assistant.tool_calls message; the backend rejects the
        next request otherwise.

        Args:
            tool_call_id: The id from the originating tool_call.
            content:      The tool result as a string. Callers are
                          responsible for JSON-encoding structured
                          results and for truncating oversized ones.
        """
        with self.lock:
            msg: Dict[str, Any] = {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": content,
            }
            self.messages.append(msg)
            self._total_chars_cache += self._chars_of(msg)
            self._trim_locked()

    # ------------------------------------------------------------------
    # Character budget
    # ------------------------------------------------------------------

    def _total_chars_locked(self) -> int:
        """
        Return the cached total character count of the history.

        Called under `self.lock`. The value is maintained incrementally
        by the mutators and the trim loop, so this method is O(1) and
        can be called on every iteration of `_trim_locked` without
        turning the trim into an O(n^2) operation.

        Returns:
            An integer estimate of the total character count.
        """
        return self._total_chars_cache

    def _recompute_total_chars_locked(self) -> int:
        """
        Rebuild the character cache from scratch. O(n).

        Called only when the cache has gone negative, which can only
        happen if a future mutator forgets to update it. The rebuild
        keeps the rest of the code correct without adding a check on
        every append.
        """
        total = 0
        for m in self.messages:
            total += self._chars_of(m)
        return total

    # ------------------------------------------------------------------
    # Trimming (override)
    # ------------------------------------------------------------------

    def _trim_locked(self) -> None:
        """
        Enforce both the message-count cap and the character budget.

        Called under `self.lock` by every mutator. The loop:

          1. Re-checks both limits (O(1) thanks to the cache).
          2. If the head of the list is an assistant.tool_calls
             message, drops the whole group (the assistant message
             plus all of its immediately following role=tool replies)
             in one go.
          3. Otherwise drops the single oldest message.
          4. After either removal, drops any role=tool message that
             ended up at the head, so the head is never a dangling
             tool reply.

        A safety counter guarantees progress even if the configured
        limits are pathological (for example max_history = 0). The
        loop terminates after at most len(messages) + 8 iterations.

        The character cache is updated on every removal, keeping
        `_total_chars_locked()` accurate at O(1).

        Overrides of this method in further subclasses MUST preserve
        the group invariant: an assistant.tool_calls message and its
        role=tool replies are removed together, never split.
        """
        # Self-heal a corrupted cache. The check is cheap and only
        # triggers a rebuild in the (rare) case where a future mutator
        # bypasses the four override methods above.
        if self._total_chars_cache < 0:
            self._total_chars_cache = (
                self._recompute_total_chars_locked()
            )

        # Safety: guarantee progress even with pathological limits.
        safety_budget = len(self.messages) + 8

        while self.messages and safety_budget > 0:
            safety_budget -= 1

            over_count = len(self.messages) > self.max_history
            over_chars = (
                self._total_chars_cache > self.max_history_chars
            )
            if not over_count and not over_chars:
                break

            # ---- Step 1: drop a whole tool-call group if the head is
            # one. The three conditions below are all required:
            #   * there must be at least two messages;
            #   * the head must be an assistant message that carries a
            #     tool_calls list (defensive: a bare assistant message
            #     followed by a "tool" message would be malformed);
            #   * the second message must be a role=tool reply.
            if (
                len(self.messages) >= 2
                and self.messages[0].get("role") == "assistant"
                and isinstance(
                    self.messages[0].get("tool_calls"), list
                )
                and self.messages[1].get("role") == "tool"
            ):
                j = 1
                while (
                    j < len(self.messages)
                    and self.messages[j].get("role") == "tool"
                ):
                    j += 1
                # Subtract the whole group from the cache before
                # deleting, so the cache never observes a partial
                # state.
                for k in range(j):
                    self._total_chars_cache -= self._chars_of(
                        self.messages[k]
                    )
                del self.messages[:j]
            else:
                # ---- Step 2: drop the single oldest message.
                self._total_chars_cache -= self._chars_of(
                    self.messages[0]
                )
                del self.messages[0]

            # ---- Step 3: never leave a role=tool message at the
            # head. This can happen if the list was already malformed
            # (for example a tool reply without a preceding assistant
            # message) or if an unusual message ordering was inserted
            # by a future extension.
            while (
                self.messages
                and self.messages[0].get("role") == "tool"
            ):
                self._total_chars_cache -= self._chars_of(
                    self.messages[0]
                )
                del self.messages[0]