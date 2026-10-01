"""Shared environment helpers for subprocess spawning and browser reachability."""

import os
import sys

# Path probed to detect the Windows Subsystem for Linux. Module-level so tests
# can redirect it at a fixture file without monkeypatching builtins.
_PROC_VERSION = "/proc/version"


def _is_wsl() -> bool:
    """Return True when running under the Windows Subsystem for Linux.

    Detected by ``microsoft`` appearing in ``/proc/version`` (case-insensitive),
    which both WSL1 and WSL2 kernels report. The read is guarded: on non-Linux
    platforms ``/proc/version`` is absent, so this returns False and never
    raises. Pure and side-effect-free — safe to call from any layer.
    """
    try:
        with open(_PROC_VERSION, encoding="utf-8") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


def browser_available() -> bool:
    """Return True when this process can plausibly hand a URL to a browser.

    One predicate, one owner. Two callers ask this question and must agree: the
    gateway decides whether to auto-open the dashboard, and ``personalclaw setup``
    decides whether to point at the dashboard's guided first run. A second copy of
    the heuristic would drift, and the failure is silent either way — a suppressed
    pointer, or an instruction the user cannot follow.

    The case with no browser is a headless REMOTE session: an SSH shell with no
    display server. macOS is exempt because ``open(1)`` reaches the console user's
    browser even from an SSH shell. WSL reports True — it has neither ``$DISPLAY``
    nor the SSH variables — which is correct, because ``wslview`` hands the URL to
    the Windows default browser (see :func:`personalclaw.gateway._open_dashboard`).

    Deliberately NOT the same question as "is this host remote"
    (``dashboard/origin.py``, ``cli_doctor``): a remote host WITH a display can open
    a browser, and those two format a URL differently for a remote user whether or
    not one is available. Pure and side-effect-free.
    """
    if sys.platform == "darwin":
        return True
    is_ssh = bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_CLIENT"))
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return has_display or not is_ssh


#: The environment variables that decide which program, library or code a child process runs:
#: where commands are found, what the dynamic loader injects, and what an interpreter loads before
#: the program itself. None of them is ever set by PersonalClaw while it runs: a stored secret with
#: one of these names is kept in the credential store but never mirrored into the process
#: environment (``config.credentials.mirrored_into_the_environment``), and a trigger's payload can
#: never set one for its command (``action_providers.bash_provider.PROTECTED_ENV_NAMES``).
PROGRAM_RESOLUTION_NAMES: frozenset[str] = frozenset(
    {
        # resolution + loader
        "PATH",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "DYLD_FRAMEWORK_PATH",
        # interpreter entry points
        "BASH_ENV",
        "ENV",
        "SHELL",
        "IFS",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONHOME",
        # Which bytecode a Python child runs: a prefix chosen elsewhere would load its caches.
        "PYTHONPYCACHEPREFIX",
        "PERL5LIB",
        "NODE_OPTIONS",
        "NODE_PATH",
        "RUBYOPT",
        "RUBYLIB",
        "GIT_SSH",
        "GIT_SSH_COMMAND",
        "GIT_EXTERNAL_DIFF",
    }
)

# ── the environment this process was launched with ──────────────────────────────────────────
#
# The process environment is every child's. Its PATH decides which program each command a child
# names resolves to, and its TMPDIR where the child keeps its scratch files. So a child is given
# both as the process was launched with them (`STARTUP_NAMES`), never as `os.environ` holds them at
# the moment the child is started: nothing in PersonalClaw changes either
# (tests/test_process_environment_writes_census.py), and a library that did would otherwise change
# them for every tool server, hook and script started afterwards. A restart starts the new image
# with the whole launch environment (`launch_env`), so nothing a running gateway changed in its
# own environment carries over, and every restart starts from the same one.

#: The variables a child is given as the process was launched with them.
STARTUP_NAMES: tuple[str, ...] = ("PATH", "TMPDIR")

#: Not recorded yet: the process's entry point did not ask (a test, a library caller).
_NOT_RECORDED = object()
_startup: object = _NOT_RECORDED


def record_startup_environment() -> None:
    """Remember the environment this process was launched with. The CLI's entry point calls it
    first, before anything can change the environment; a second call changes nothing."""
    global _startup
    if _startup is _NOT_RECORDED:
        _startup = dict(os.environ)


def launch_env() -> dict[str, str]:
    """The environment this process was launched with, as a restart starts its new image with it.

    A process whose entry point never recorded its launch (a test, a library caller) has nothing
    earlier to go by, so its environment now is the one it was launched with."""
    if _startup is _NOT_RECORDED:
        return dict(os.environ)
    return dict(_startup)  # type: ignore[call-overload]


def _started_with(name: str) -> str | None:
    """*name* as the process was launched with it, or ``None`` when it was launched without."""
    if _startup is _NOT_RECORDED:
        return os.environ.get(name)
    return _startup.get(name)  # type: ignore[attr-defined]


def startup_path() -> str | None:
    """The ``PATH`` this process was launched with, or ``None`` when it was launched with none."""
    return _started_with("PATH")


def gateway_env() -> dict[str, str]:
    """This process's environment as a child is handed it: every variable it holds now, with
    :data:`STARTUP_NAMES` (``PATH``, ``TMPDIR``) as the process was launched with them.

    For a child that runs with the gateway's own environment, and the base
    ``sandbox.build_child_env`` picks a child's allowlist from. A variable the gateway sets for
    its children on purpose (a credential just saved, the port it bound) reaches them; only
    where programs are found and where scratch files go stay as they were at launch."""
    env = dict(os.environ)
    for name in STARTUP_NAMES:
        value = _started_with(name)
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    return env


# Common directories where MCP server binaries may be installed.
# Order matters — earlier entries take precedence.
_EXTRA_PATH_DIRS = (
    "{home}/.local/bin",
    "{home}/.npm-packages/bin",
    "{home}/.local/share/mise/shims",
)


def augmented_path(base_path: str = "") -> str:
    """Return *base_path* prepended with well-known MCP binary directories.

    When PersonalClaw runs under systemd or another non-login shell the
    inherited ``$PATH`` may not include directories like ``~/.local/bin``.
    This helper prepends standard install locations to keep the PATH
    consistent across login and non-login contexts.
    """
    home = os.path.expanduser("~")
    extra = [d.format(home=home) for d in _EXTRA_PATH_DIRS]
    parts = extra + ([base_path] if base_path else [])
    return os.pathsep.join(parts)
