# -*- coding: utf-8 -*-
"""
llm_common.runtime - Frozen-executable detection and --help helpers.

These helpers let each LLM service adapt its `--help` output to the
current packaging mode:

  - Source mode:     python llm_service.py [OPTIONS]
  - Frozen exe mode: llm_service.exe [OPTIONS]

The detection logic is intentionally identical across every tool in the
toolchain so that `--help`, error messages, and logging all present the
same program name to the user.

Detection
---------
A process is considered "frozen" when either of the following is true:

  * sys.frozen is set (PyInstaller one-file / one-dir mode).
  * sys._MEIPASS exists (PyInstaller one-file extraction directory).

Nuitka also sets sys.frozen, so this covers Nuitka builds as well.

Invocation name resolution
--------------------------
This module is shared by every LLM service. Using __file__ would always
return "runtime.py" regardless of which service is running, which is
wrong. The correct source of truth is sys.argv[0]: the path that the
user (or the frozen launcher) actually asked Python to run.

In frozen mode, sys.executable points at the frozen exe, so the
executable's basename is used directly.

This module has no dependencies on any other llm_common module.
"""

import os
import sys


# Fallback used only when sys.argv is empty or argv[0] is blank. This is
# extremely rare for a CLI process, but keeps every helper total (never
# raises) so that a startup banner or --help render can never crash the
# service.
_FALLBACK_NAME = "(unknown)"


def is_frozen_exe() -> bool:
    """
    Return True when the current process is a frozen executable.

    Covers PyInstaller (both one-file and one-dir) and Nuitka, which
    all set sys.frozen. Also checks sys._MEIPASS as a fallback, which
    PyInstaller sets in one-file mode.

    Returns:
        True  if running from a frozen executable.
        False if running from a Python interpreter with source files.
    """
    return getattr(sys, 'frozen', False) or hasattr(sys, '_MEIPASS')


def _script_name_from_argv() -> str:
    """
    Return the basename of the script currently being executed.

    Uses sys.argv[0], which is the path Python was asked to run. This is
    the only reliable way for a shared helper module to report the
    *caller's* name: __file__ would always point at this module.

    Falls back to _FALLBACK_NAME when sys.argv is empty or argv[0] is
    blank. Never raises.

    Returns:
        A bare basename such as "llm_proxy.py", or _FALLBACK_NAME.
    """
    argv0 = sys.argv[0] if sys.argv else ""
    if not argv0:
        return _FALLBACK_NAME
    return os.path.basename(os.path.abspath(argv0))


def get_invocation_name() -> str:
    """
    Return the program name for use as argparse's `prog=` value.

    - Frozen exe: the executable's basename, e.g. "llm_service.exe".
    - Source:     the script's basename, e.g. "llm_service.py".

    The returned string is what the user sees at the top of `--help`
    and in usage errors. It must match how the user actually launched
    the program.

    Returns:
        The program name as a bare string. Falls back to "(unknown)"
        if sys.argv is empty (rare; should not happen for a CLI).
    """
    if is_frozen_exe():
        return os.path.basename(sys.executable)
    return _script_name_from_argv()


def get_example_invocation() -> str:
    """
    Return the command prefix used in `--help` examples.

    - Frozen exe: "llm_service.exe"
    - Source:     "python llm_service.py"

    The exact form depends on how the current process was launched. In
    frozen mode, sys.executable already points at the exe, so no
    interpreter prefix is needed. In source mode, sys.executable is the
    Python interpreter, and the caller's script name is appended.

    Returns:
        A command prefix suitable for embedding in example strings.
    """
    if is_frozen_exe():
        return os.path.basename(sys.executable)
    return (
        f"{os.path.basename(sys.executable)} "
        f"{_script_name_from_argv()}"
    )


def get_script_dir() -> str:
    """
    Return the directory that contains the running script or exe.

    - Frozen exe: the directory of the executable.
    - Source:     the directory of the script being executed.

    This is used to locate sidecar resources (config files, temporary
    attachment directories, log files) next to the program rather than
    next to the current working directory, which the user may have
    changed.

    Like get_invocation_name, this resolves the *caller's* location, not
    this module's location, so that shared helper modules inside
    llm_common do not accidentally report their own directory.

    Note:
        The three LLM services (llm_service / llm_proxy / llm_proxy_tool)
        currently compute their script directory inline with
        os.path.dirname(os.path.abspath(__file__)), which is correct for
        them but duplicated. New code should prefer this helper. Existing
        inline computations are harmless and left in place for now to
        keep the diff minimal.

    Returns:
        An absolute directory path. Falls back to the current working
        directory if sys.argv is empty (rare; should not happen for a
        CLI).
    """
    if is_frozen_exe():
        return os.path.dirname(os.path.abspath(sys.executable))
    argv0 = sys.argv[0] if sys.argv else ""
    if not argv0:
        return os.path.abspath(os.getcwd())
    return os.path.dirname(os.path.abspath(argv0))