# -*- coding: utf-8 -*-
"""
llm_common.attachments - Attachment validation and conversion.

An `attachments` array may accompany a `generate` request. Each entry
describes one file the user has attached to the conversation:

    {
        "kind":    "text" | "image",
        "name":    "main.py",
        "mime":    "text/x-python" | "image/png",
        "text":    "...",              # kind == "text"
        "data_b64": "..."              # kind == "image"
    }

This module centralizes three concerns:

  1. Validation
     Type checks, size limits, MIME whitelist, base64 sanity. Invalid
     attachments raise AttachmentError with a precise message so that
     the caller can return a clear error to the client.

  2. Multi-modal content construction
     `build_user_content()` turns a text prompt plus a list of
     validated attachments into either a plain string (no image) or
     an OpenAI-style content array (text part + image parts).

  3. History placeholder
     `placeholder_for()` returns the text that should be stored in
     the session history in place of the original attachment. Images
     are replaced by a short marker so that multi-turn sessions do
     not accumulate megabytes of base64.

Design notes
------------
* Text attachments are merged into the text part of the user message
  inside <attachment> ... </attachment> tags. They remain in history
  because their size is bounded (see MAX_TEXT_BYTES_PER_FILE).

* Image attachments are emitted as OpenAI `image_url` parts with a
  data: URL. They are NOT stored in history; only a placeholder is.

* The module is pure: it never opens files, never touches the network,
  and never logs. Callers pass already-read content in. This keeps it
  trivially unit-testable and safe to import from any layer.

* If a caller wants to extend the protocol with new attachment kinds
  (for example "document" for PDF/Word), the extension point is the
  `_VALIDATORS` dispatch table at the bottom of this file. New kinds
  should be added there, plus a corresponding branch in
  `build_user_content` and `placeholder_for`.

* Empty text is handled explicitly: a message that carries only image
  attachments produces a content array that contains ONLY the image
  parts, with no empty text part. Some OpenAI-compatible backends
  reject an empty text part, and even where they tolerate it, the
  resulting request is one part larger than necessary.

This module has no dependencies on any other llm_common module.
"""

import base64
import binascii
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union


# ----------------------------------------------------------------------
# Limits
# ----------------------------------------------------------------------

# Maximum size of a single text attachment, measured in UTF-8 bytes
# before base64 is even considered (text is sent as-is).
MAX_TEXT_BYTES_PER_FILE = 256 * 1024          # 256 KB

# Maximum total size of all text attachments in one request.
MAX_TEXT_BYTES_TOTAL = 512 * 1024             # 512 KB

# Maximum size of a single image attachment, measured in base64
# characters. A base64 string is ~1.33x the raw byte size, so 8 MB
# of base64 corresponds to roughly 6 MB of raw image data.
MAX_IMAGE_B64_PER_FILE = 8 * 1024 * 1024      # 8 MB base64

# Maximum total size of all image attachments in one request.
MAX_IMAGE_B64_TOTAL = 16 * 1024 * 1024        # 16 MB base64

# Maximum length of an attachment `name` field. Names appear in the
# <attachment name="..."> tag embedded into the prompt and in the
# history placeholder. A malicious client could otherwise send a
# multi-megabyte name that bloats the prompt and the history without
# bound. The value is generous enough for any real filename.
MAX_ATTACHMENT_NAME_LEN = 256

# MIME types accepted for image attachments. Anything else is rejected
# so that a client cannot smuggle arbitrary binary data through the
# "image" kind.
ALLOWED_IMAGE_MIMES = frozenset((
    "image/png",
    "image/jpeg",
    "image/jpg",      # tolerated alias for image/jpeg
    "image/webp",
))

# If an image attachment omits its MIME, this is assumed.
DEFAULT_IMAGE_MIME = "image/png"

# If a text attachment omits its MIME, this is assumed.
DEFAULT_TEXT_MIME = "text/plain"

# Fallback name when an attachment omits one.
DEFAULT_ATTACHMENT_NAME = "unnamed"


# ----------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------

class AttachmentError(ValueError):
    """
    Raised when an attachment fails validation.

    The message is written for the end user: it should be safe to
    return verbatim in an error response, and it should tell the user
    which attachment and which rule caused the rejection.
    """
    pass


# ----------------------------------------------------------------------
# Data structure
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class Attachment:
    """
    One validated attachment.

    Frozen so that it cannot be mutated after validation. Callers that
    need to transform an attachment should build a new one.

    Fields:
        kind      "text" or "image".
        name      Original filename, or DEFAULT_ATTACHMENT_NAME.
        mime      MIME type. Always populated after validation.
        text      UTF-8 text content, only for kind == "text".
        data_b64  Base64 image data, only for kind == "image".
    """
    kind: str
    name: str
    mime: str
    text: Optional[str] = None
    data_b64: Optional[str] = None


# ----------------------------------------------------------------------
# Validation helpers
# ----------------------------------------------------------------------

def _utf8_len(s: str) -> int:
    """
    Return the UTF-8 byte length of a Python string.

    Empty strings short-circuit to 0 to avoid a pointless encode call.
    """
    if not s:
        return 0
    return len(s.encode("utf-8"))


# Precomputed base64 alphabet for the cheap pre-check in
# `_is_valid_base64`. The full base64 alphabet is A-Z, a-z, 0-9, '+',
# '/', and '=' for padding.
_B64_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789+/="
)


def _is_valid_base64(s: str) -> bool:
    """
    Return True if `s` is syntactically valid base64.

    The check is done in two stages:

      1. A cheap O(n) character set + padding sanity pass that rejects
         obvious garbage without ever touching the decoder. This
         matters because the input can be up to MAX_IMAGE_B64_PER_FILE
         characters, and `base64.b64decode` would decode the whole
         string just to fail.
      2. A full `b64decode(validate=True)` pass to confirm the
         structure (correct padding at the end, no stray '=' in the
         middle, etc.). This is O(n) as well, but it runs only for
         strings that already look plausible.
    """
    if not s:
        return False

    # Cheap pass: reject any character that is not in the base64
    # alphabet. This is O(n) but with a tiny constant.
    for ch in s:
        if ch not in _B64_ALPHABET:
            return False

    # Padding may only appear at the very end, and at most twice.
    stripped = s.rstrip("=")
    padding = len(s) - len(stripped)
    if padding > 2:
        return False

    # Length must be a multiple of 4 once padded.
    if len(s) % 4 != 0:
        return False

    # Full structural validation. Any failure here means the cheap
    # pass accepted something the decoder still rejects.
    try:
        base64.b64decode(s, validate=True)
        return True
    except (binascii.Error, ValueError):
        return False


def _coerce_name(raw: Dict[str, Any]) -> str:
    """
    Return the attachment name, clamped to MAX_ATTACHMENT_NAME_LEN.

    Missing or blank names become DEFAULT_ATTACHMENT_NAME. Overlong
    names are truncated to the limit so that the rest of the pipeline
    can assume a bounded length without further checks.
    """
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        return DEFAULT_ATTACHMENT_NAME
    if len(name) > MAX_ATTACHMENT_NAME_LEN:
        return name[:MAX_ATTACHMENT_NAME_LEN]
    return name


def _validate_text(
    raw: Dict[str, Any],
    name: str,
    stats: Dict[str, int],
) -> Attachment:
    """
    Validate one text attachment and update running totals in `stats`.

    `stats` accumulates the total text byte count across the whole
    request so that MAX_TEXT_BYTES_TOTAL can be enforced.
    """
    text = raw.get("text")
    if not isinstance(text, str):
        raise AttachmentError(
            f"attachment '{name}': text attachment requires a 'text' "
            f"field of type string"
        )

    size = _utf8_len(text)
    if size > MAX_TEXT_BYTES_PER_FILE:
        raise AttachmentError(
            f"attachment '{name}': text size {size} bytes exceeds "
            f"the per-file limit of {MAX_TEXT_BYTES_PER_FILE} bytes"
        )

    stats["text_total"] += size
    if stats["text_total"] > MAX_TEXT_BYTES_TOTAL:
        raise AttachmentError(
            f"attachment '{name}': cumulative text size "
            f"{stats['text_total']} bytes exceeds the total limit of "
            f"{MAX_TEXT_BYTES_TOTAL} bytes"
        )

    mime = raw.get("mime")
    if not isinstance(mime, str) or not mime.strip():
        mime = DEFAULT_TEXT_MIME

    return Attachment(kind="text", name=name, mime=mime, text=text)


def _validate_image(
    raw: Dict[str, Any],
    name: str,
    stats: Dict[str, int],
) -> Attachment:
    """
    Validate one image attachment and update running totals in `stats`.

    `stats` accumulates the total base64 character count across the
    whole request so that MAX_IMAGE_B64_TOTAL can be enforced.

    The MIME check runs BEFORE the base64 sanity check, so a request
    that declares an unsupported MIME type is rejected without ever
    decoding the (potentially very large) base64 payload.
    """
    data_b64 = raw.get("data_b64")
    if not isinstance(data_b64, str) or not data_b64:
        raise AttachmentError(
            f"attachment '{name}': image attachment requires a "
            f"'data_b64' field of type string"
        )

    size = len(data_b64)
    if size > MAX_IMAGE_B64_PER_FILE:
        raise AttachmentError(
            f"attachment '{name}': base64 size {size} chars exceeds "
            f"the per-file limit of {MAX_IMAGE_B64_PER_FILE} chars"
        )

    # MIME check first: cheap and rejects a whole class of bad
    # requests before we pay for base64 validation.
    mime = raw.get("mime")
    if not isinstance(mime, str) or not mime.strip():
        mime = DEFAULT_IMAGE_MIME

    if mime not in ALLOWED_IMAGE_MIMES:
        raise AttachmentError(
            f"attachment '{name}': MIME type '{mime}' is not allowed "
            f"for image attachments; allowed types are: "
            f"{', '.join(sorted(ALLOWED_IMAGE_MIMES))}"
        )

    # Normalize the tolerated alias so downstream code sees one value.
    if mime == "image/jpg":
        mime = "image/jpeg"

    # Base64 structural validation.
    if not _is_valid_base64(data_b64):
        raise AttachmentError(
            f"attachment '{name}': data_b64 is not valid base64"
        )

    stats["image_total"] += size
    if stats["image_total"] > MAX_IMAGE_B64_TOTAL:
        raise AttachmentError(
            f"attachment '{name}': cumulative base64 size "
            f"{stats['image_total']} chars exceeds the total limit of "
            f"{MAX_IMAGE_B64_TOTAL} chars"
        )

    return Attachment(kind="image", name=name, mime=mime,
                      data_b64=data_b64)


# Dispatch table for attachment kinds. New kinds (for example
# "document") should be added here.
_VALIDATORS = {
    "text":  _validate_text,
    "image": _validate_image,
}


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------

def validate_attachments(raw: Any) -> List[Attachment]:
    """
    Validate an `attachments` array from a generate request.

    Accepts:
        None or []          -> returns []
        list of dict        -> validates each entry, returns a list of
                               Attachment objects

    Raises:
        AttachmentError     if any entry fails validation. The
                            message names the offending attachment and
                            the rule that was violated.

    The whole request is rejected on the first invalid attachment. This
    is the "strict" policy: a client that sends a bad attachment should
    learn about it, not silently have the attachment dropped.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise AttachmentError(
            "attachments must be a JSON array"
        )
    if not raw:
        return []

    stats = {"text_total": 0, "image_total": 0}
    result: List[Attachment] = []

    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise AttachmentError(
                f"attachment at index {idx}: expected a JSON object"
            )

        kind = entry.get("kind")
        if not isinstance(kind, str):
            raise AttachmentError(
                f"attachment at index {idx}: missing or invalid 'kind'"
            )

        validator = _VALIDATORS.get(kind)
        if validator is None:
            raise AttachmentError(
                f"attachment at index {idx}: unsupported kind '{kind}'; "
                f"supported kinds are: "
                f"{', '.join(sorted(_VALIDATORS.keys()))}"
            )

        name = _coerce_name(entry)
        result.append(validator(entry, name, stats))

    return result


def build_user_content(
    text: str,
    attachments: List[Attachment],
    vision_enabled: bool,
) -> Union[str, List[Dict[str, Any]]]:
    """
    Build the `content` value for the user message.

    Returns:
        * A plain string when there are no image attachments. This
          keeps the pure-text path byte-for-byte identical to the
          pre-upgrade behaviour and remains compatible with text-only
          backends.
        * A list of content parts when at least one image attachment
          is present. The first part is a `text` part containing the
          original prompt plus any text attachments wrapped in
          <attachment> tags; each image contributes one `image_url`
          part with a data: URL.
        * If the original text is empty AND there are no text
          attachments, the text part is omitted entirely, so a
          pure-image message produces a content array containing only
          image parts. This matches the OpenAI convention and avoids
          an empty text part that some backends reject.

    Args:
        text:            The user's prompt.
        attachments:     Validated attachments (output of
                         validate_attachments).
        vision_enabled:  Whether the server is allowed to emit image
                         parts. If False and any image attachment is
                         present, AttachmentError is raised. This is
                         the strict policy: never silently drop an
                         image the user asked to send.

    Raises:
        AttachmentError: if vision_enabled is False but image
                         attachments are present.
    """
    if not attachments:
        return text

    text_parts: List[str] = []
    if text:
        text_parts.append(text)

    image_parts: List[Dict[str, Any]] = []

    for att in attachments:
        if att.kind == "text":
            # Wrap so the model can see the file boundary. The name is
            # quoted so that a filename containing spaces or angle
            # brackets does not confuse the model.
            text_parts.append(
                f'<attachment name="{att.name}">\n'
                f'{att.text}\n'
                f'</attachment>'
            )
        elif att.kind == "image":
            if not vision_enabled:
                raise AttachmentError(
                    f"attachment '{att.name}': image attachments are "
                    f"not supported by this server (vision is disabled)"
                )
            image_parts.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{att.mime};base64,{att.data_b64}",
                },
            })
        else:
            # Should be unreachable: validate_attachments already
            # filtered the kinds. Defensive in case the dispatch table
            # is extended without updating this method.
            raise AttachmentError(
                f"attachment '{att.name}': unsupported kind '{att.kind}'"
            )

    combined_text = "\n\n".join(text_parts)

    if not image_parts:
        # No images: keep the plain-string path for maximum
        # compatibility with text-only backends.
        return combined_text

    if not combined_text:
        # Only images were provided: omit the text part entirely so
        # that the content array is not polluted with an empty text
        # entry. Some OpenAI-compatible backends reject an empty text
        # part; even where it is tolerated, the extra part is noise.
        return image_parts

    return [{"type": "text", "text": combined_text}] + image_parts


def placeholder_for(att: Attachment) -> str:
    """
    Return the text to store in session history in place of `att`.

    * Text attachments return an empty string: their full content is
      already merged into the text part of the user message, and that
      message is what gets stored in history. Calling this function for
      a text attachment is therefore a no-op from the caller's point
      of view.

    * Image attachments return a short marker such as
      "[image: chart.png]". The original base64 is not stored, so
      multi-turn sessions do not accumulate megabytes of image data.

    Returns:
        A string. Empty for text attachments, non-empty for images.
    """
    if att.kind == "image":
        return f"[image: {att.name}]"
    # Text attachments are fully embedded in the user message, so no
    # separate placeholder is needed in history.
    return ""