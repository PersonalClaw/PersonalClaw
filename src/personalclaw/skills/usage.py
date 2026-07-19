"""Skill-use counter — a lightweight sidecar usage store (skill-use-counter).

The shared primitive behind `skill-semantic-surfacing` (#26 — rank/tie-break by
real use) and `skill-library-curator` (#27 — GC the cold `auto/` namespace). It
records, at *turn time*, that a skill was actually surfaced into a turn — for ALL
skills (not just ``auto/``), without ever rewriting a ``SKILL.md``.

Why a sidecar, not the frontmatter ``reuse_count``:
- Incrementing the file per use churns its ``mtime``, which would invalidate the
  loader's mtime-keyed frontmatter cache AND the mtime-keyed skill-description
  embedding cache (#26). The sidecar leaves skill files — and both caches — alone.
- The frontmatter field is ``auto/``-only; usage signal is wanted for every skill.

The frontmatter ``reuse_count`` is kept as a human-facing *snapshot*, refreshed
opportunistically at the refine seam (which already rewrites the file).

Storage: ``<skills_dir>/.usage.json`` — ``{name: {"count": int, "last_used_at":
iso8601}}``. Counts are **advisory** (ranking / GC heuristics), so the
read-modify-write tolerates a rare lost update under concurrency rather than
paying for a lock on every turn.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.skills.loader import skills_dir

logger = logging.getLogger(__name__)

_USAGE_FILE = ".usage.json"


@dataclass(frozen=True)
class SkillUsage:
    """Usage stats for one skill."""

    count: int = 0
    last_used_at: str = ""  # ISO 8601 UTC; empty if never used


class SkillUsageStore:
    """Sidecar JSON counter of per-skill turn-time use.

    Cheap by design: a single small JSON file loaded on demand. Safe for the
    common single-writer case; under concurrent writers a lost increment is
    acceptable (the counts only drive ranking/GC heuristics).
    """

    def __init__(self, path: Path | None = None):
        self._path = path or (skills_dir() / _USAGE_FILE)

    # ── read ──

    def _load(self) -> dict[str, dict]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def get(self, name: str) -> SkillUsage:
        """Return usage for *name* (zero-valued if never recorded)."""
        row = self._load().get(name)
        if not isinstance(row, dict):
            return SkillUsage()
        return SkillUsage(
            count=int(row.get("count", 0) or 0),
            last_used_at=str(row.get("last_used_at", "") or ""),
        )

    def all_usage(self) -> dict[str, SkillUsage]:
        """Return ``{name: SkillUsage}`` for every recorded skill."""
        out: dict[str, SkillUsage] = {}
        for name, row in self._load().items():
            if isinstance(row, dict):
                out[name] = SkillUsage(
                    count=int(row.get("count", 0) or 0),
                    last_used_at=str(row.get("last_used_at", "") or ""),
                )
        return out

    # ── write ──

    def record_use(self, name: str, *, now: datetime | None = None) -> int:
        """Increment *name*'s use count and stamp ``last_used_at``. Returns the
        new count (0 on write failure — best-effort, never raises)."""
        ts = (now or datetime.now(tz=timezone.utc)).isoformat(timespec="seconds")
        try:
            data = self._load()
            row = data.get(name) if isinstance(data.get(name), dict) else {}
            new_count = int(row.get("count", 0) or 0) + 1
            data[name] = {"count": new_count, "last_used_at": ts}
            atomic_write(self._path, json.dumps(data, indent=2, sort_keys=True))
            return new_count
        except Exception:  # advisory counter — never break a turn
            logger.debug("skill usage record failed for %s", name, exc_info=True)
            return 0

    def record_uses(self, names: list[str], *, now: datetime | None = None) -> None:
        """Record several skills used in one turn with a single write."""
        names = [n for n in dict.fromkeys(names) if n]  # de-dup, preserve order
        if not names:
            return
        ts = (now or datetime.now(tz=timezone.utc)).isoformat(timespec="seconds")
        try:
            data = self._load()
            for name in names:
                row = data.get(name) if isinstance(data.get(name), dict) else {}
                data[name] = {"count": int(row.get("count", 0) or 0) + 1, "last_used_at": ts}
            atomic_write(self._path, json.dumps(data, indent=2, sort_keys=True))
        except Exception:
            logger.debug("skill usage batch record failed", exc_info=True)

    def prune(self, keep: set[str]) -> None:
        """Drop usage rows whose skill no longer exists (called on GC)."""
        try:
            data = self._load()
            pruned = {k: v for k, v in data.items() if k in keep}
            if len(pruned) != len(data):
                atomic_write(self._path, json.dumps(pruned, indent=2, sort_keys=True))
        except Exception:
            logger.debug("skill usage prune failed", exc_info=True)
