# -*- coding: utf-8 -*-
"""
llm_common.banner - Unified status banner printing.

Every LingoFuse LLM service prints a startup banner with the same
visual structure:

    ======================================================================
     <TITLE>
    ======================================================================
      Field                   : value
      Field                   : value
    ----------------------------------------------------------------------
      Field                   : value
    ======================================================================

The banner is purely cosmetic: it summarizes the effective
configuration so that operators can confirm at a glance that the
service started with the values they intended.

This module provides a declarative API. Callers describe the banner
as a list of sections, where each section is a list of (label, value)
pairs. The module handles alignment, separators, and stream selection.

Contents
--------
  print_banner       - render a banner to a stream (default stdout)
  format_banner      - return the banner as a single string
  redact             - mask a secret for safe display
  join_list          - render a list, with a fallback for empty lists
  yes_no             - render a boolean as "enabled" / "disabled"

Design notes
------------
* Values are converted to strings by the caller OR by the helpers in
  this module. The banner renderer itself does not interpret values;
  it just aligns and prints them. This keeps the module free of any
  assumptions about what a "field" means.

* The label column width is adaptive. LABEL_WIDTH is the MINIMUM
  width used when every label fits; if any label is longer than
  LABEL_WIDTH, the column is widened to fit the longest label so that
  the visual alignment of the entire banner is preserved. This is why
  a single long label (for example "MCP tool provider app") does not
  misalign the rest of the banner.

* The banner never raises on odd input. If a value is not a string,
  str() is applied. If a list is passed where a string is expected,
  it is joined with ", ". This is deliberate: a startup banner must
  never crash the service.

This module has no dependencies on any other llm_common module.
"""

import logging
import sys
from typing import Any, Iterable, List, Optional, Sequence, TextIO, Tuple


# ----------------------------------------------------------------------
# Logger
# ----------------------------------------------------------------------
#
# Used only to record a failure when the output stream itself is
# broken. The debug level is deliberate: a banner is cosmetic, and a
# broken stream is usually the caller's problem to fix, not something
# the banner should escalate. In particular, using warning/error here
# would risk a recursive failure if the logging handler writes to the
# same broken stream.

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------

# Minimum width of the label column. If any label is longer than this,
# the whole banner widens so that alignment is preserved. See the
# module docstring for the rationale.
LABEL_WIDTH = 24

# Width of the '=' and '-' separator rows. This is independent of the
# label column width: the separators always span the full banner and
# do not grow with the labels.
BANNER_WIDTH = 70

# Characters used for the three separator roles.
SEP_TOP = "="
SEP_SECTION = "-"
SEP_BOTTOM = "="


# ----------------------------------------------------------------------
# Value helpers
# ----------------------------------------------------------------------

def _to_display(value: Any) -> str:
    """
    Convert a field value to a display string.

    * None becomes "(none)".
    * bool becomes "True" / "False" (the caller may prefer yes_no()).
    * list / tuple / set is joined with ", ".
    * Anything else is passed through str().

    Never raises.
    """
    if value is None:
        return "(none)"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [str(v) for v in value]
        return ", ".join(items) if items else "(none)"
    return str(value)


def yes_no(flag: bool) -> str:
    """
    Render a boolean as "enabled" or "disabled".

    This is the phrasing used by the historical banners for options
    like `Tools enabled` and `Vision enabled`.

    Never raises; non-bool input is coerced with bool().
    """
    return "enabled" if bool(flag) else "disabled"


def join_list(items: Optional[Iterable[Any]], empty: str = "(none)") -> str:
    """
    Render an iterable as a comma-separated string.

    If `items` is None or empty, returns `empty`. This is the phrasing
    used by banners for capability lists such as `Unsupported APIs`.

    Never raises; each item is passed through str(). If `items` is not
    iterable (for example an int), the function returns `empty`
    instead of propagating a TypeError, so that a malformed caller
    value cannot crash the startup banner.
    """
    if items is None:
        return empty
    try:
        rendered = [str(x) for x in items]
    except TypeError:
        # `items` is not iterable (e.g. an int). Degrade gracefully:
        # a banner must never be the reason a service fails to start.
        return empty
    if not rendered:
        return empty
    return ", ".join(rendered)


def redact(secret: Optional[str]) -> str:
    """
    Mask a secret for safe display in a banner.

    The mask shows the length of the secret but never its content:

        redact("sk-abcdef123456") -> "<redacted, 15 chars>"

    If the secret is None or empty, returns "(disabled)" so that the
    banner clearly indicates that no secret is configured.

    Args:
        secret: The secret string, or None.

    Never raises.
    """
    if not secret:
        return "(disabled)"
    return f"<redacted, {len(secret)} chars>"


# ----------------------------------------------------------------------
# Core rendering
# ----------------------------------------------------------------------

def _format_field(label: str, value: Any, label_width: int) -> str:
    """
    Format one field line as:

        "  <label padded to label_width> : <value>"

    The two leading spaces and the " : " separator are part of the
    historical banner style and are kept for visual continuity.

    label_width is always >= len(label) when called from
    format_banner, because format_banner widens the column to fit the
    longest label. str.ljust returns the string unchanged when it is
    already at least as long as the requested width, so a defensive
    caller that passes a shorter width still gets a valid line rather
    than an exception or a crash.
    """
    return (
        f"  {str(label).ljust(label_width)} : {_to_display(value)}"
    )


def format_banner(
    title: str,
    sections: Sequence[Sequence[Tuple[str, Any]]],
    *,
    label_width: int = LABEL_WIDTH,
    banner_width: int = BANNER_WIDTH,
) -> str:
    """
    Render a banner as a single string.

    Args:
        title:
            The banner title, e.g. "LINGOFUSE LLM SERVICE STATUS".
            Rendered with one leading space, matching the historical
            style.

        sections:
            A sequence of sections. Each section is a sequence of
            (label, value) pairs. Sections are separated by a row of
            '-' characters. A single-section banner has no separator.

        label_width:
            Minimum width of the label column. Defaults to
            LABEL_WIDTH (24). The effective width used at render time
            is max(label_width, len(longest label)), so that a single
            long label cannot break the alignment of the rest of the
            banner.

        banner_width:
            Width of the '=' and '-' separator rows. Defaults to
            BANNER_WIDTH (70). This is independent of the label column
            width and does not grow with it.

    Returns:
        The banner as a single string, with a trailing newline. Ready
        to be printed or logged as-is.
    """
    lines: List[str] = []

    top = SEP_TOP * banner_width
    bottom = SEP_BOTTOM * banner_width
    middle = SEP_SECTION * banner_width

    lines.append(top)
    lines.append(f" {title}")
    lines.append(top)

    section_list = list(sections)

    # First pass: compute the effective label width. This is what
    # keeps a single long label from breaking column alignment across
    # the whole banner. The scan is O(total_fields) and runs once per
    # banner, so it is negligible compared to the string formatting
    # that follows.
    max_label_len = 0
    for section in section_list:
        for label, _value in section:
            label_len = len(str(label))
            if label_len > max_label_len:
                max_label_len = label_len
    effective_width = max(label_width, max_label_len)

    # Second pass: emit the lines using the effective width.
    for idx, section in enumerate(section_list):
        if idx > 0:
            lines.append(middle)
        for label, value in section:
            lines.append(_format_field(label, value, effective_width))

    lines.append(bottom)

    return "\n".join(lines) + "\n"


def print_banner(
    title: str,
    sections: Sequence[Sequence[Tuple[str, Any]]],
    *,
    label_width: int = LABEL_WIDTH,
    banner_width: int = BANNER_WIDTH,
    stream: Optional[TextIO] = None,
) -> str:
    """
    Render a banner and write it to a stream.

    This is a thin wrapper around `format_banner`. It exists so that
    callers can write:

        print_banner("LINGOFUSE LLM PROXY", sections)

    without having to remember to add a newline or pick a stream.

    Args:
        title, sections, label_width, banner_width:
            See `format_banner`.

        stream:
            Destination stream. Defaults to sys.stdout, matching the
            historical behavior of the three services. Pass
            sys.stderr if the banner should not be mixed with
            protocol output (for example in stdio mode).

    Returns:
        The rendered banner string, for callers that also want to log
        or capture it.

    Never raises on odd input; field values are coerced with
    `_to_display`.
    """
    text = format_banner(
        title,
        sections,
        label_width=label_width,
        banner_width=banner_width,
    )
    out = stream if stream is not None else sys.stdout
    try:
        out.write(text)
        out.flush()
    except Exception as e:
        # A startup banner must never crash the service. If the
        # stream is broken (for example a closed pipe), record the
        # failure at DEBUG level and return the text anyway so the
        # caller can still log it. We deliberately do not use
        # warning/error here: the logger's own handler may write to
        # the same broken stream, and a banner is cosmetic.
        logger.debug("Failed to write banner to stream: %s", e)
    return text