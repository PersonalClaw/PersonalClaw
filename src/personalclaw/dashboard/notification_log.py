"""The notification log's file: one JSON note per line.

Reads and writes only. Where the file lives stays ``dashboard.state._notifications_path``,
resolved per call through that module's ``config_dir``, which is the seam tests redirect.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from personalclaw import bounded_log
from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)


def load(path: Path) -> list[dict[str, Any]]:
    """Load persisted notifications from disk, oldest first by each note's own ``ts`` whatever
    order the file holds them in. ``GET /api/notifications`` lists the log newest first.

    EVERY valid row loads — no cap slice. The in-memory log is the write
    authority for `rewrite`, so a load that silently truncated
    below what the file holds turned the next read-state rewrite (one ack)
    into permanent deletion of every unloaded row (Issue 420: the steady-state
    file legitimately holds up to 2× the cap between trims). The size cap is
    enforced at the append seam, on memory and file together.
    """
    if not path.exists():
        return []
    try:
        entries: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return bounded_log.in_time_order(entries, at="ts")
    except Exception:
        logger.debug("Failed to load notifications", exc_info=True)
        return []


def append(path: Path, note: dict[str, str]) -> None:
    """Append a single notification to the JSONL file on disk."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(note) + "\n")
    except Exception:
        logger.debug("Failed to persist notification", exc_info=True)


def rewrite(path: Path, notifications: list[dict[str, str]]) -> None:
    """Rewrite the notifications file to exactly the given rows.

    No cap slice here: this writes what the caller holds, so a read-state
    change (ack/unack/delete) can never shorten the log below what memory —
    which now mirrors the file in full — carries. The cap lives at the
    append seam, where memory and file are trimmed together.
    """
    try:
        atomic_write(path, "".join(json.dumps(n) + "\n" for n in notifications))
    except Exception:
        logger.debug("Failed to rewrite notifications file", exc_info=True)
