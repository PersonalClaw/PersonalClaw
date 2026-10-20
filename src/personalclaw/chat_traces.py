"""What a chat keeps on disk, read without touching it.

A chat leaves five things behind: its transcript (``sessions/<key>.jsonl``, with what hangs off
it — its search rows, its background summary, any archive batch of it), its working folder
(``sessions/<key>/``, the raw tool results kept for it), its turn checkpoints (pre-edit copies of
the files it changed), the files attached to its messages (``uploads/``, and ``screenshots/``
for a native capture) and the skills it was taught and not yet kept (``skills/.ephemeral/``).

Core, beneath the dashboard that forgets a chat (``dashboard/chat_forget.py``), because the
stores that copy the home read it too: a snapshot and a shard export leave out what a running
Temporary chat keeps (:func:`kept_by_temporary_chats`), since such a chat is forgotten when its
session ends and a copy would outlive it.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

#: The memory mode of a chat that is forgotten when its session ends.
TEMPORARY = "temporary"


def is_temporary(meta: Any) -> bool:
    """Whether a transcript's metadata row is a Temporary chat's."""
    return isinstance(meta, dict) and meta.get("memory_mode") == TEMPORARY


def name_of(key: str) -> str:
    """The chat's name for a transcript key: without its ``dashboard:`` namespace, or the
    ``dashboard_`` prefix (stacked by old resume round-trips) its file name carries."""
    name = key.removeprefix("dashboard:")
    while name.startswith("dashboard_"):
        name = name[len("dashboard_") :]
    return name or key


def attachment_roots(home: Path | None = None) -> tuple[str, ...]:
    """The folders a chat's attached files are saved in, each ending in the path separator.

    Two, because a screen capture takes one of two routes to the same chip: the browser snip
    uploads a PNG like any other file (``uploads/``), while the native ``screencapture -i`` writes
    to ``screenshots/`` and threads the path straight in. In *home*, or the active home resolved
    when asked, so a test's home is the one read.
    """
    if home is None:
        from personalclaw.config.loader import config_dir

        home = config_dir()
    return tuple(str((home / name).resolve()) + os.sep for name in ("uploads", "screenshots"))


def attached_files(messages: Iterable[dict], *, home: Path | None = None) -> list[str]:
    """The files the user attached to *messages* (their ``meta.files``) that are saved in an
    attachment folder, in the order they were attached, each once. A path anywhere else — a
    workspace file named in a message — is the user's own file, not the chat's."""
    roots = attachment_roots(home)
    found: list[str] = []
    for message in messages:
        if message.get("role") != "user":
            continue
        raw = (message.get("meta") or {}).get("files")
        if not isinstance(raw, list):
            continue
        for path in raw:
            if isinstance(path, str) and path and os.path.realpath(path).startswith(roots):
                if path not in found:
                    found.append(path)
    return found


def _read_temporary_transcript(path: Path) -> list[dict] | None:
    """The rows of *path* when it is a Temporary chat's transcript (its first line is the
    metadata row of a chat in that mode), else ``None``."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    rows: list[dict] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    if not rows or rows[0].get("_type") != "metadata" or not is_temporary(rows[0]):
        return None
    return rows


def kept_by_temporary_chats(home: Path) -> frozenset[Path]:
    """Everything the Temporary chats in *home* keep on disk, resolved: each transcript, the
    working folder of each form of its key, and the files attached to it.

    A backup or an export of *home* leaves these out. Such a chat is forgotten when its session
    ends, and a copy taken while it ran would outlive it.
    """
    sessions = home / "sessions"
    kept: set[Path] = set()
    try:
        transcripts = sorted(sessions.glob("*.jsonl"))
    except OSError:
        return frozenset()
    for path in transcripts:
        rows = _read_temporary_transcript(path)
        if rows is None:
            continue
        kept.add(path.resolve())
        name = name_of(path.stem)
        for form in {path.stem, name, f"dashboard:{name}"}:
            kept.add((sessions / form).resolve())
        kept.update(Path(f).resolve() for f in attached_files(rows, home=home))
    return frozenset(kept)
