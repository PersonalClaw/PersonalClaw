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
"""

from __future__ import annotations

import importlib.util
import logging
import shutil
import sys

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
