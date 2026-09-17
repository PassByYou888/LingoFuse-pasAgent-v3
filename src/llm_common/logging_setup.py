# -*- coding: utf-8 -*-
"""
llm_common.logging_setup - Unified logging configuration.

All LingoFuse LLM services use the same logging format:

    [YYYY-MM-DD HH:MM:SS] [LEVEL] message

and write to stderr by default. This module centralizes that
configuration so that the three siblings (llm_service.py,
llm_proxy.py, llm_proxy_tool.py) and the test client (llm_test.py)
share one implementation.

Two level styles are supported
------------------------------

1. String levels, matching Python's logging module:

       "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL"

   Used by llm_proxy.py and llm_proxy_tool.py, whose command line
   exposes --log-level with a strict subset of these names.

2. Integer levels, matching llm_service.py's historical convention:

       0 = quiet   -> WARNING (only warnings and errors)
       1 = normal  -> INFO
       2 = debug   -> DEBUG

   These are preserved for backward compatibility with existing
   llm_service.py command lines and scripts.

A single helper, `level_from_int`, performs the integer-to-string
mapping so that callers can pass either style to `setup_logging`.

Return value
------------
`setup_logging` returns the ROOT logger, not a child logger. The
three services each define their own child logger with a fixed name
("llm_service", "llm_proxy", "llm_proxy_tool"); that child logger
inherits from root through the standard propagation mechanism. The
previous implementation tried to return a logger named after the
running script, which no caller ever used and which never matched the
fixed names actually in use. Returning root makes the contract
explicit and removes dead code.

This module depends on llm_common.runtime only for the convenience
`get_logger()` default name. It has no other internal dependencies.
"""

import logging
import sys
from typing import Optional, Union

from .runtime import get_invocation_name


# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------

# Format string identical to the historical one used by every service.
LOG_FORMAT = "[%(asctime)s] [%(levelname)s] %(message)s"

# Date format identical to the historical one.
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Integer-to-string mapping for llm_service.py's 0/1/2 convention.
_INT_TO_LEVEL = {
    0: "WARNING",
    1: "INFO",
    2: "DEBUG",
}

# Accepted string levels for validation. CRITICAL is accepted here
# even though no service exposes it as a --log-level choice: keeping
# it in the accepted set lets a future service opt in without
# touching this module, and lets a caller pass it programmatically.
_VALID_LEVELS = frozenset(("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"))


# ----------------------------------------------------------------------
# Level helpers
# ----------------------------------------------------------------------

def level_from_int(n: int) -> str:
    """
    Map llm_service.py's integer level convention to a string level.

    Args:
        n: 0 (quiet), 1 (normal), or 2 (debug).

    Returns:
        "WARNING" for 0, "DEBUG" for 2, and "INFO" for every other
        integer (including 1, negative numbers, and large values).
        This matches the historical behaviour of llm_service.py,
        which treated out-of-range values as the default level rather
        than raising.
    """
    if n == 0:
        return "WARNING"
    if n == 2:
        return "DEBUG"
    return "INFO"


def _normalize_level(level: Union[str, int]) -> str:
    """
    Normalize a level given as either a string or an integer.

    Args:
        level: A recognized string (case-insensitive) or an integer
            following the 0/1/2 convention. If None, "INFO" is
            returned so that a caller that forgot to initialize a
            configuration object cannot crash the service at startup.

    Returns:
        One of the strings in _VALID_LEVELS.

    Raises:
        ValueError: if a string level is not one of the accepted
            names. Integer levels are never rejected; unknown integers
            map to "INFO" via `level_from_int`.
        TypeError:  if `level` is neither a string nor an integer and
            is not None.
    """
    if level is None:
        # Defensive: a missing config value should degrade to the
        # default level rather than aborting startup.
        return "INFO"
    if isinstance(level, int) and not isinstance(level, bool):
        # `bool` is a subclass of `int`; rejecting it here makes the
        # type contract explicit. `_normalize_level(True)` would
        # otherwise silently map to INFO via level_from_int(1).
        return level_from_int(level)
    if isinstance(level, str):
        upper = level.upper()
        if upper in _VALID_LEVELS:
            return upper
        raise ValueError(
            f"Unknown log level {level!r}; expected one of: "
            f"{', '.join(sorted(_VALID_LEVELS))}"
        )
    raise TypeError(
        f"Log level must be str or int, got {type(level).__name__}"
    )


# ----------------------------------------------------------------------
# Setup
# ----------------------------------------------------------------------

def setup_logging(
    level: Union[str, int, None] = "INFO",
    *,
    stream=None,
    force: bool = True,
) -> logging.Logger:
    """
    Configure the root logger and return it.

    This is the single entry point every service should call once at
    startup, after argument parsing and before any other logging.

    Args:
        level:
            Either a string ("DEBUG" / "INFO" / "WARNING" / "ERROR" /
            "CRITICAL"), an integer following llm_service.py's 0/1/2
            convention, or None (which is treated as "INFO"). See
            `level_from_int` for the integer mapping.

        stream:
            Output stream. Defaults to sys.stderr, matching all
            existing services. Tests may pass io.StringIO() to
            capture output.

        force:
            When True (default), remove any pre-existing handlers on
            the root logger before installing the new one.

            IMPORTANT: This is the behaviour most services want, and
            it avoids duplicate log lines when a module is reloaded
            or when several services run in the same process (as
            happens during testing). But it ALSO means that calling
            `setup_logging()` a second time in the same process will
            silently replace the first call's handlers. Tests that
            want to isolate a service's log output should either
            run each service in its own process, or capture the
            stream explicitly via the `stream=` argument.

    Returns:
        The ROOT logger. Child loggers obtained via
        `logging.getLogger("llm_proxy")` etc. inherit from it through
        standard propagation. The return value is safe to ignore;
        it is offered mainly for tests that want to introspect the
        configured level.

    Raises:
        ValueError: if a non-None string level is not recognized.
        TypeError:  if `level` is neither a string, an integer, nor
            None.
    """
    normalized = _normalize_level(level)
    numeric = getattr(logging, normalized)

    if stream is None:
        stream = sys.stderr

    # Route configuration through basicConfig. `force=True` is the
    # supported way to reset handlers across repeated calls.
    logging.basicConfig(
        level=numeric,
        format=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT,
        stream=stream,
        force=force,
    )

    # Return the root logger. The three services install their own
    # child loggers (logging.getLogger("llm_proxy") etc.) which
    # inherit the level, format, and handler installed above through
    # standard propagation. There is deliberately no attempt to
    # reconfigure a child logger here: doing so would only matter if
    # the child used a different level or had its own handlers, and
    # no service in this codebase does.
    return logging.getLogger()


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    Return a logger by name, defaulting to the current program name.

    This is a thin wrapper around `logging.getLogger` that exists so
    that all LingoFuse LLM services can obtain their logger through
    one import. Callers that want a stable, source-and-exe-invariant
    name should pass one explicitly (as the three services do:
    "llm_service", "llm_proxy", "llm_proxy_tool").

    Args:
        name: Optional logger name. If None, the current program's
            invocation name is used (for example "llm_service.exe").
            This default is convenient for one-off scripts but is NOT
            what the three services use, because the invocation name
            changes between source and frozen-exe modes. Prefer an
            explicit name in long-lived code.

    Returns:
        A logging.Logger instance. Never None.
    """
    return logging.getLogger(name if name else get_invocation_name())