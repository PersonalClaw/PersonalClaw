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
2. **stdlib ``pip``** as an importable module → the historical command. Probed by
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
``--prefix`` does not treat the running environment's packages as installed.
"""

from __future__ import annotations

import importlib.util
import logging
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


class NoInstallerError(RuntimeError):
    """No usable package installer for the running interpreter.

    Raised instead of letting a ``No module named pip`` escape, because that
    message sends the reader after pip when the real answer is usually "this is a
    uv venv, and uv isn't on PATH".
    """


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
    raise NoInstallerError(
        "No package installer is available for this environment: "
        f"{sys.executable} has no `pip` module and `uv` is not on PATH. "
        "Install uv (https://docs.astral.sh/uv/) or add pip to the environment "
        "(`python -m ensurepip --upgrade`), then retry."
    )


def _bundled_pip_wheel() -> Path | None:
    """The pip wheel this Python's ``ensurepip`` ships, or ``None``.

    pip runs straight from its wheel (``python pip-X.whl/pip install …`` — the wheel is a zip
    whose ``pip/__main__.py`` bootstraps itself), which is how ``ensurepip`` itself installs pip.
    Distributions that strip ``ensurepip`` simply answer ``None``.
    """
    try:
        import ensurepip
    except Exception:  # noqa: BLE001 — a stripped or broken ensurepip is "no bundled wheel"
        return None
    wheels = sorted((Path(ensurepip.__file__).parent / "_bundled").glob("pip-*.whl"))
    return wheels[-1] if wheels else None


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

    Order: the ``pip`` module of this environment; else the pip wheel bundled with this Python's
    ``ensurepip`` — a uv-created environment (``uv tool install personalclaw``, the recommended
    install) has no pip module, but its Python ships that wheel. Neither →
    :class:`NoInstallerError`.
    """
    if _have_pip():
        return [sys.executable, "-m", "pip", "install", *args]
    wheel = _bundled_pip_wheel()
    if wheel is not None:
        return [sys.executable, str(wheel / "pip"), "install", *args]
    raise NoInstallerError(
        f"{sys.executable} has no `pip` module and its Python ships no bundled pip wheel, so "
        "there is nothing to install packages with. Add pip to that environment "
        "(`python -m ensurepip --upgrade`, or your system's python3-pip package) and try again"
    )
