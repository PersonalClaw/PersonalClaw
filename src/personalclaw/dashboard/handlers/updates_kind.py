"""Install-kind detection (plan 34 S4, contract C1).

Self-update behaves differently per how PersonalClaw was installed, so the
updater first asks *what kind of install is this?* — a git checkout (contributor
/ dev), a pip/uv/pipx install (a wheel in ``sys.prefix``), a container image, or
the desktop shell. The check + apply surfaces (updates.py) branch on this.

Resolution order (first hit wins), per C1::

    env PERSONALCLAW_INSTALL_KIND in {"container","desktop"}  -> that
        (baked into the Dockerfiles; set by the Electron shell, plan 45)
    a resolvable project dir that contains a .git directory     -> "git"
    else                                                        -> "pip"

Pre-1.0 clean break (owner 2026-07-20): implemented directly, WITHOUT a
lifecycle gate — there is no lifecycle/gates.py machinery yet, so this is the
one behavior, not a gated alternative to the old git-only path.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

InstallKind = Literal["git", "pip", "container", "desktop"]

_ENV_KINDS: frozenset[str] = frozenset({"container", "desktop"})


def _project_dir() -> str:
    """The resolved source-tree dir, or "" — mirrors updates.py's probe.

    ``PERSONALCLAW_PROJECT_DIR`` is set at startup by ``cli._detect_project_dir``
    when the gateway runs from a checkout (it finds ``agents/`` + ``skills/``
    walking up from CWD, or a saved path). A wheel/container/desktop install has
    no such tree, so the env is unset.
    """
    return os.environ.get("PERSONALCLAW_PROJECT_DIR", "") or ""


def _has_git_dir(proj: str) -> bool:
    """True when *proj* (or its monorepo parent) is a git working tree.

    A ``.git`` entry is normally a directory, but in a git *worktree* or a
    submodule it is a file pointing at the real gitdir — accept either.
    """
    if not proj:
        return False
    root = Path(proj)
    # The project dir may be the repo root, or nested one level under it
    # (monorepo layout — see updates._package_root). Check both.
    for cand in (root, root.parent):
        if (cand / ".git").exists():
            return True
    return False


def detect_install_kind() -> InstallKind:
    """Classify the running install as git / pip / container / desktop (C1)."""
    env_kind = (os.environ.get("PERSONALCLAW_INSTALL_KIND") or "").strip().lower()
    if env_kind in _ENV_KINDS:
        return env_kind  # type: ignore[return-value]
    if _has_git_dir(_project_dir()):
        return "git"
    return "pip"
