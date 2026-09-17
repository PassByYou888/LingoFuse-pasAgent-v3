# -*- coding: utf-8 -*-
"""
llm_common - Shared modules for the LingoFuse LLM toolchain.

This package contains reusable components shared by:

  - llm_service.py      (local inference server)
  - llm_proxy.py        (pure text proxy)
  - llm_proxy_tool.py   (tool bridge, LTB)
  - llm_test.py         (interactive test client)

Package layout (strict dependency direction, top-down only):

  Layer 1 - no internal dependencies
      runtime             frozen exe detection, --help helpers
      headers             JSON / header / key-file utilities
      attachments         attachment validation and conversion
      option_sanitizer    whitelist filtering for options

  Layer 2 - depends on Layer 1
      logging_setup       logging configuration
      banner              status banner printing
      capabilities        API capability matrix definitions
      sse_client          OpenAI-compatible SSE streaming client

  Layer 3 - depends on Layers 1 and 2
      session_base        plain-text session state
      session_multimodal  session state with tool_calls / attachments

Module status legend
--------------------
Each module in this package is annotated with one of the following
status labels in the layer listing above and in the sections below:

  in use     - Imported by at least one top-level service and exercised
               on the normal request path.
  proxy-only - Used by one or more of the proxy siblings
               (llm_proxy.py / llm_proxy_tool.py) but not by
               llm_service.py.
  reserved   - Declared for a future migration or feature; currently
               no caller uses it. Kept because removing it would
               require touching the migration plan.

Concrete status of each module:

  in use:
      runtime, headers, attachments, option_sanitizer, logging_setup,
      banner, capabilities, sse_client, session_base, session_multimodal

  proxy-only:
      (none right now; the proxy siblings use only in-use modules)

  reserved:
      The compatibility properties on session_base.SessionState
      (`last_active_at` and `current_cancel_event`) are reserved for
      a future migration of llm_service.Session. See the module
      docstring of session_base for the migration notes.

  Notable constraints:
      - `session_base.begin_run()` intentionally does NOT check
        `running`; it is meant for callers that own the state machine.
        Handlers that must reject concurrent runs should use
        `try_begin_run()` instead.
      - `sse_client` has NO third-party dependencies. It imports only
        from the Python standard library. The earlier `requests`
        dependency was removed when list_models() was rewritten on
        top of http.client.

Dependency rule
---------------
  Top-level LLM services may import from llm_common.
  No module in llm_common may import from a top-level LLM service.
  This keeps the package reusable and independently testable.

When adding a new module
------------------------
Before adding a module to this package, check the following:

  1. Which layer does it belong to? A new module must only import
     from lower-numbered layers, never from the same layer or above.
     If a candidate module would need to import from a higher layer,
     either lower its dependency or split it.

  2. Does it truly have no dependency on top-level services? A module
     that reaches back into llm_proxy.py or llm_service.py breaks the
     reusability guarantee and must not be added here.

  3. Add it to the correct layer in the import block below, keeping
     the layer groups visually separated. Python's import system
     resolves the actual order; the grouping is for human readers.

  4. Add its name to __all__.

  5. If the module is used by only some of the services, note that
     in the module docstring and in the status legend above.

  6. Update the layer listing in this docstring so that the next
     reader sees the new module without having to open the import
     block.

Explicit re-exports
-------------------
`from llm_common import runtime, banner` works because this file
imports each submodule below. `from llm_common import print_banner`
does NOT work: functions are not re-exported at the package level,
which is deliberate. Keeping the public surface at the module level
avoids name collisions (for example, `redact` exists only in banner,
`jdump` only in headers) and makes it obvious which file owns a given
symbol:

    from llm_common.banner import print_banner, redact
    from llm_common.headers import jdump, parse_extra_headers
    from llm_common.attachments import validate_attachments

If a future revision decides to add package-level re-exports, they
should go into __all__ explicitly and be documented in a new section
of this docstring.
"""

__version__ = "1.1.0"

# ----------------------------------------------------------------------
# Layer 1 - no internal dependencies
# ----------------------------------------------------------------------
from . import runtime
from . import headers
from . import attachments
from . import option_sanitizer

# ----------------------------------------------------------------------
# Layer 2 - depends on Layer 1
# ----------------------------------------------------------------------
from . import logging_setup
from . import banner
from . import capabilities
from . import sse_client

# ----------------------------------------------------------------------
# Layer 3 - depends on Layers 1 and 2
# ----------------------------------------------------------------------
from . import session_base
from . import session_multimodal


__all__ = [
    # Layer 1
    "runtime",
    "headers",
    "attachments",
    "option_sanitizer",
    # Layer 2
    "logging_setup",
    "banner",
    "capabilities",
    "sse_client",
    # Layer 3
    "session_base",
    "session_multimodal",
]