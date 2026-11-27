"""What a chat keeps on disk, read without touching it.

A chat leaves five things behind: its transcript (``sessions/<key>.jsonl``, with what hangs off
it — its record ``sessions/<key>.chat.json``, its search rows, its background summary, any
archive batch of it), its working folder
(``sessions/<key>/``, the raw tool results kept for it), its turn checkpoints (pre-edit copies of
the files it changed), the files attached to its messages (``uploads/``, and ``screenshots/``
for a native capture) and the skills it was taught and not yet kept (``skills/.ephemeral/``).

What it raised in your Inbox and notifications is not its own file but rows of shared stores.
When the chat goes, each decision it asked for stays in the Inbox as a short record with none of
its words, and every other row and notification about it is deleted
(``dashboard/chat_forget_inbox.py``).

Core, beneath the dashboard that forgets a chat (``dashboard/chat_forget.py``), because the
stores that copy the home read it too: a snapshot and a shard export leave out what a running
Temporary chat keeps (:func:`kept_by_temporary_chats`), its helpers' folders and its workflow runs
with it (:func:`leave_out_temporary_runs`), since such a chat is forgotten when its session ends
and a copy would outlive it.
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
    """The rows of *path* when it is a Temporary chat's transcript, else ``None``: its record says
    that mode (``chat_record``), or its first line, the metadata row, does, which a hand edit or an
    older release may have written since. A chat whose record cannot be read may be Temporary, so it
    is taken as one."""
    from personalclaw import chat_record

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        record = chat_record.read(path)
    except chat_record.ChatRecordUnreadable:
        record = {"memory_mode": TEMPORARY}
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
    first = rows[0] if rows and rows[0].get("_type") == "metadata" else None
    by_record = record is not None and is_temporary(record)
    return rows if by_record or (first is not None and is_temporary(first)) else None


def kept_by_temporary_chats(home: Path) -> frozenset[Path]:
    """Everything the Temporary chats in *home* keep on disk, resolved: each transcript and its
    record, the working folder of each form of its key, and the files attached to it; the folder
    of each helper it started, and of each one those started in turn, with its agent session's
    files (``subagent_persistence.kept_by_helpers_of``); and the folder of each workflow run that
    keeps a Temporary chat's mode (:func:`temporary_runs`), and of each helper its steps started.

    A backup or an export of *home* leaves these out. Such a chat is forgotten when its session
    ends, and a copy taken while it ran would outlive it.
    """
    from personalclaw import chat_record, subagent_persistence
    from personalclaw.session_keys import WORKFLOW_STEP

    sessions = home / "sessions"
    kept: set[Path] = set()
    forms: set[str] = set()
    try:
        transcripts = sorted(sessions.glob("*.jsonl"))
    except OSError:
        return frozenset()
    for path in transcripts:
        rows = _read_temporary_transcript(path)
        if rows is None:
            continue
        kept.add(path.resolve())
        kept.update(p.resolve() for p in (chat_record.path_of(path), chat_record.lock_of(path)))
        name = name_of(path.stem)
        forms |= {path.stem, name, f"dashboard:{name}"}
        for form in {path.stem, name, f"dashboard:{name}"}:
            kept.add((sessions / form).resolve())
        kept.update(Path(f).resolve() for f in attached_files(rows, home=home))
    runs = temporary_runs(home)
    kept.update((home / "workflows" / "runs" / run_id).resolve() for run_id in runs)

    def owns(session: str) -> bool:
        step = session.removeprefix(WORKFLOW_STEP.prefix)
        return session in forms or (step != session and step.partition(":")[0] in runs)

    if forms or runs:
        kept |= subagent_persistence.kept_by_helpers_of(owns, home)
    return frozenset(kept)


def temporary_runs(home: Path) -> frozenset[str]:
    """The ids of the workflow runs in *home*'s ledger that keep a Temporary chat's mode
    (``workflows.ownership.run_mode``): its own, and what they started in turn. Each ends and is
    deleted with the chat (``workflows.private_runs``), so a copy taken while it ran would
    outlive it."""
    from personalclaw.workflows import store

    return frozenset(store.ids_with_mode(home / "workflows" / "runs.db", TEMPORARY))


def leave_out_temporary_runs(ledger: Path) -> int:
    """Delete from *ledger*, a backup's or an export's copy of a run ledger, the rows of the runs
    that keep a Temporary chat's mode (:func:`temporary_runs`), their bytes with them. Returns how
    many."""
    from personalclaw.workflows import store

    return store.drop_runs(ledger, store.ids_with_mode(ledger, TEMPORARY))
