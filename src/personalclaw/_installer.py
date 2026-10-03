"""Which tool installs packages into *this* environment (issues #46, #51).

Four separate code paths used to hardcode ``[sys.executable, "-m", "pip", ...]``:
the app dependency installer, the pip-kind self-updater, the startup dep repair,
and the git-checkout updater. A ``uv``-created virtualenv **ships no pip** — uv is
the installer — so every one of them died with ``No module named pip`` on the
project's own documented dev setup (``uv venv`` + ``uv pip install -e ".[dev]"``),
and on the uv-based end-user install path.

The fix is one resolver, used by all four, rather than four copies of the same
detection drifting apart. Order (first available wins):

1. **``uv``** on PATH → ``uv pip install --python <sys.executable> …``. Explicitly
   targeted at the running interpreter: uv otherwise resolves its own notion of
   the active environment (``VIRTUAL_ENV``, or a discovered ``.venv``), which can
   be a *different* env than the one the gateway is importing from — installing
   there would report success while the import still fails.
2. **``pip``** as an importable module → the historical command. Probed by
   spec, not by running it, so detection costs no subprocess.
3. Neither → :class:`NoInstallerError`, which names both remedies. Previously this
   surfaced as a bare ``No module named pip`` that pointed at the wrong problem.

``pip`` is checked as a MODULE (``python -m pip``) and never as a bare ``pip``
executable on PATH: a stray system-wide ``pip`` would install into some other
interpreter's site-packages, which is the failure this whole module exists to
prevent.

App packages are the one install that does NOT target this environment: they go into
``<home>/app-python`` with ``--prefix`` (``apps/app_python.py``), resolved AGAINST this
environment. That install is pip-only — :func:`prefix_install_argv` — because uv's
``--prefix`` does not treat the running environment's packages as installed. An app's
engine goes into the app's own environment, installed by this environment's pip run under
that environment's interpreter (:func:`env_install_argv`).

Both run the pip of this environment, which is why pip is one of PersonalClaw's own declared
dependencies. Undeclared, it was there only by accident of how the environment was made: a
``python -m venv`` environment gets one from ``ensurepip``, uv seeds none, and Debian and Ubuntu
strip ``ensurepip``'s wheels from their system Python, so on that Python (measured: Ubuntu
24.04's 3.12.3) no app with Python packages could be installed.
"""

from __future__ import annotations

import importlib.util
import logging
import shutil
import sys
from pathlib import Path
from types import MappingProxyType

logger = logging.getLogger(__name__)

#: What every pip, uv and npm run PersonalClaw starts carries in its environment: no cache. pip
#: and uv keep what they download in the user's own cache (``~/Library/Caches/pip``,
#: ``~/.cache/pip``, ``~/.cache/uv``), and PersonalClaw leaves nothing outside its home:
#: installing one app that declares a Python package was measured filling pip's. What a cache
#: would hold is installed anyway (an app's packages in the home, PersonalClaw's own in its
#: installation), so a reinstall downloads it again. The variables, not ``--no-cache-dir``: pip
#: hands its environment, and not its flags, to the pip it runs for an sdist's build
#: requirements. Node keeps the code it compiles in the temp folder
#: (``node-compile-cache``, which npm turns on at every start) unless told not to.
_NO_CACHE_ENV = MappingProxyType(
    {"PIP_NO_CACHE_DIR": "1", "UV_NO_CACHE": "1", "NODE_DISABLE_COMPILE_CACHE": "1"}
)

#: The folder in the home for what those runs keep. npm cannot run without a cache, so its cache
#: (and the logs it keeps there) is ``npm``. ``tmp`` is the temp folder they run with: what one of
#: them leaves in its temp folder stays in the home (uv never removes the lock it takes to build
#: a setuptools project, ``uv-setuptools-<digest>.lock``). The durability inventory ignores the
#: folder as a cache.
INSTALLER_CACHE_DIRNAME = "installer-cache"


def installer_cache_env() -> dict[str, str]:
    """The settings every pip, uv and npm run PersonalClaw starts carries, over whatever else its
    environment holds: no pip, uv or node cache, npm's cache in the home, and a temp folder in
    the home, made here because a ``TMPDIR`` that does not exist is passed over for ``/tmp``.

    A child that runs code PersonalClaw did not write gets them on top of the child allowlist
    (``sandbox.build_child_env(..., extra=installer_cache_env())``); PersonalClaw's own installs
    through :func:`installer_env`. ``tests/test_installer_resolution.py`` holds every spawn of
    pip, uv or npm to one of the two.
    """
    from personalclaw.config.loader import config_dir

    root = config_dir() / INSTALLER_CACHE_DIRNAME
    tmp = root / "tmp"
    tmp.mkdir(mode=0o700, parents=True, exist_ok=True)
    return {**_NO_CACHE_ENV, "npm_config_cache": str(root / "npm"), "TMPDIR": str(tmp)}


#: Where a Node CLI PersonalClaw starts keeps the code it compiles. Node puts it in the temp folder
#: (``node-compile-cache``) whenever a program turns the cache on, which npm does at every start,
#: so each agent CLI, MCP server, app backend or bundler run through Node could leave one there.
#: ``NODE_COMPILE_CACHE`` moves it into the home, beside what the installers keep; the CLI keeps
#: its faster start.
NODE_COMPILE_CACHE_DIRNAME = "node-compile-cache"


def node_cli_env() -> dict[str, str]:
    """What every Node CLI PersonalClaw starts carries (an ACP agent, an MCP server, an app's
    backend or worker, the artifact bundler): its compile cache in ``<home>/installer-cache``,
    made here, owner-only, before the CLI looks for it."""
    from personalclaw.config.loader import config_dir

    cache = config_dir() / INSTALLER_CACHE_DIRNAME / NODE_COMPILE_CACHE_DIRNAME
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    return {"NODE_COMPILE_CACHE": str(cache)}


def installer_env() -> dict[str, str]:
    """This process's environment with :func:`installer_cache_env` over it, for an install or
    build of PersonalClaw itself (a self-update, the startup dependency repair, a frontend
    rebuild)."""
    from personalclaw.env import gateway_env

    return {**gateway_env(), **installer_cache_env()}


class NoInstallerError(RuntimeError):
    """No usable package installer for the running interpreter.

    Raised instead of letting a ``No module named pip`` escape: it names the
    interpreter and the fix, where that message named neither. A missing pip also
    carries the sentence's two halves, ``problem`` and ``fix`` (:func:`missing_pip`),
    for a surface that shows what broke and what to do apart (an engine's install).
    """

    def __init__(self, message: str, *, problem: str = "", fix: str = "") -> None:
        super().__init__(message)
        self.problem = problem
        self.fix = fix


def _have_uv() -> bool:
    return shutil.which("uv") is not None


def _have_pip() -> bool:
    """True if ``python -m pip`` would work, without spending a subprocess.

    ``find_spec`` can itself raise (a half-removed distribution leaves an import
    hook that errors rather than returning None), and a broken pip must read as
    "no pip" so the caller falls through to uv instead of crashing here.
    """
    try:
        return importlib.util.find_spec("pip") is not None
    except Exception:  # noqa: BLE001 — a pip that can't even be probed is not usable
        return False


def installer_name() -> str:
    """``"uv"``, ``"pip"``, or ``""`` when neither is usable. For diagnostics."""
    if _have_uv():
        return "uv"
    if _have_pip():
        return "pip"
    return ""


def missing_pip(python: str = "") -> tuple[str, str]:
    """``(problem, fix)`` for *python* (the running interpreter by default) having no ``pip``
    module: the words every refusal here uses and ``personalclaw doctor`` prints, so the
    installer and the doctor say the same thing.

    pip is one of PersonalClaw's own dependencies, so an environment without it is an install
    missing part of itself, and the fix is the reinstall that puts it back. Never ``ensurepip``,
    which a Debian or Ubuntu system Python does not have, nor the distribution's ``python3-pip``,
    which installs into the system's own packages rather than this environment.
    """
    from personalclaw.python_support import reinstall_command

    problem = (
        f"{python or sys.executable} has no `pip` module, though pip is one of "
        "PersonalClaw's own dependencies"
    )
    command = reinstall_command()
    if command:
        return problem, f"reinstall PersonalClaw (`{command}`) to put it back"
    return problem, "reinstall PersonalClaw into that environment to put it back"


def _pip() -> list[str]:
    """``python -m pip`` on the running interpreter. Raises :class:`NoInstallerError`, naming
    the fix, when this environment has no ``pip`` module."""
    if not _have_pip():
        problem, fix = missing_pip()
        raise NoInstallerError(
            f"{problem}. {fix[0].upper()}{fix[1:]}, then try again", problem=problem, fix=fix
        )
    return [sys.executable, "-m", "pip"]


# pip flags that ``uv pip install`` does not accept. uv has no version self-check
# to disable; every other flag these call sites pass (``-U``, ``-e``, ``--quiet``)
# uv accepts with identical meaning, so only this one needs dropping. Forwarding an
# unknown flag makes uv exit non-zero, failing the install for a reason that has
# nothing to do with the packages.
_UV_REJECTS: frozenset[str] = frozenset({"--disable-pip-version-check"})


def install_argv(args: list[str]) -> list[str]:
    """The argv that installs *args* into the running interpreter's environment.

    Args:
        args: installer arguments AFTER the ``install`` verb — requirement specs
            and/or pip-shaped flags (e.g. ``["-U", "personalclaw==0.1.2"]``).
            Flags the chosen installer would reject are dropped.

    Returns:
        A complete argv list for ``subprocess``.

    Raises:
        NoInstallerError: neither uv nor pip is available.
    """
    if _have_uv():
        # --python pins the TARGET env to the interpreter we are running as; see
        # the module docstring for why letting uv infer it is not safe here.
        kept = [a for a in args if a not in _UV_REJECTS]
        return ["uv", "pip", "install", "--python", sys.executable, *kept]
    if _have_pip():
        return [sys.executable, "-m", "pip", "install", *args]
    problem, fix = missing_pip()
    raise NoInstallerError(
        f"No package installer is available for this environment: {problem}, and `uv` is not "
        f"on PATH. Install uv (https://docs.astral.sh/uv/), or {fix}, then retry."
    )


def prefix_install_argv(args: list[str]) -> list[str]:
    """The argv that runs ``pip install <args>`` on the running interpreter, for an install into a
    separate ``--prefix`` that must RESOLVE AGAINST this environment (app packages).

    pip only — never uv, even when uv is on PATH, because the two disagree on exactly the
    property this install depends on. Measured (pip 26.1, uv 0.12): with ``requests`` already in
    the environment, ``pip install --prefix P requests-toolbelt`` installed ONE package, while
    ``uv pip install --python <exe> --prefix P requests-toolbelt`` installed six — ``requests``,
    ``urllib3`` and the rest of the closure, as if nothing were installed. For app packages that
    means re-downloading and duplicating what core already ships (PyTorch among them), and a pin
    on a version only the running environment has (the image's ``torch …+cpu``) cannot be
    satisfied from an index at all.

    Raises:
        NoInstallerError: this environment has no ``pip`` module (see :func:`missing_pip`).
    """
    return [*_pip(), "install", *args]


def env_install_argv(python: str | Path, args: list[str]) -> list[str]:
    """The argv that runs ``pip install <args>`` into the environment *python* belongs to (an app
    engine's own), with the pip of this environment.

    pip's ``--python`` runs this copy of pip under that interpreter, so that environment needs no
    pip of its own and is made without one (``python -m venv --without-pip``). Making it with one
    asks ``ensurepip``, and a Debian or Ubuntu system Python has none unless ``python3-venv`` is
    installed: ``python -m venv`` fails there, ``--without-pip`` does not (measured on Ubuntu
    24.04). pip only, for the reason :func:`prefix_install_argv` gives.

    Raises:
        NoInstallerError: this environment has no ``pip`` module (see :func:`missing_pip`).
    """
    return [*_pip(), "--python", str(python), "install", *args]
