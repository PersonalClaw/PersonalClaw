"""Where a file-backed artifact's ``source_path`` may point.

A file-backed artifact is a live pointer. Every read of it reads the file, and every save,
snapshot and revert writes it (``native.py``). So the pointer may name only a file PersonalClaw's
own surfaces already reach. The check is the file explorer's (:func:`file_roots.admit`: symlinks
and ``..`` resolved, no credential or secret file), run against the explorer's roots plus each
loop's own folder. An unbound loop keeps its deliverable in that folder, and its completion
graduates the file as a live pointer (``loop/watchdog._register_deliverable_artifact``). The home
itself is never one of these places: ``config.json`` and ``mcp.json`` are plain files there, and
writing one would bypass every refusal the config and MCP routes make (#3675).

An app's request gets the explorer's roots alone. A loop's folder holds the brief its worker reads
every cycle, and steering a loop is the owner's.

The pointer is checked when it is set (:func:`admit`, which refuses with the sentence the caller
shows) and again on every read and write (:func:`admitted`). A pointer recorded before this check
existed, or one whose file was later swapped for a symlink out, reads and writes nothing.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence

from personalclaw import file_roots

logger = logging.getLogger(__name__)


def _loop_folders() -> list[str]:
    """Each stored loop's own folder, resolved. Best-effort, like the explorer's loop roots."""
    try:
        from personalclaw.loop import files as loop_files
        from personalclaw.loop import store as loop_store

        folders: list[str] = []
        for loop in loop_store.list_all():
            folder = loop_files.safe_loop_dir(loop.id)
            if folder is not None:
                folders.append(os.path.realpath(folder))
        return folders
    except Exception:  # noqa: BLE001 — a loop-store fault narrows the places, never widens them
        logger.warning("artifact source check could not list the loop folders", exc_info=True)
        return []


def places() -> list[str]:
    """The roots a file-backed artifact may point inside, for the caller of this request.

    Resolved per call: the roots follow the workspace setting, the loops and the projects, and
    whether an app is asking. A caller checking many pointers at once (a body search) resolves
    them once and hands them to :func:`admitted`.
    """
    from personalclaw.apps.permissions import request_app

    roots = [rp for _label, rp in file_roots.dashboard_roots()]
    if not request_app():
        roots += _loop_folders()
    return roots


def admitted(raw: str, roots: Sequence[str] | None = None) -> str | None:
    """The canonical path *raw* names when a file-backed artifact may point at it, else ``None``.

    *roots* are :func:`places` already resolved for this request; ``None`` resolves them here.
    """
    if not raw or not os.path.isabs(raw):
        return None
    return file_roots.admit(raw, places() if roots is None else roots)


def admit(raw: str) -> str:
    """:func:`admitted`, or the refusal the caller shows, raised as ``ValueError``."""
    canonical = admitted(raw)
    if canonical is not None:
        return canonical
    from personalclaw.apps.permissions import request_app

    if request_app():
        raise ValueError(
            "An app can point an artifact only at a file in the folders the file explorer shows "
            f"it, and at no credential file in them — not {raw}"
        )
    raise ValueError(
        f"{raw} can't be an artifact's source. An artifact's source is the full path of a file "
        "in your workspace, the outbox, uploads, screenshots, or a project's or loop's folder, "
        "and never a credential or secret file."
    )
