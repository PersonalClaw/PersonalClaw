"""Per-tool invocation counter — the skill-usage sidecar, one surface over.

The §6 power-ups widget needs to know which capabilities the user has actually
*touched* so it can surface an UNtouched one. Skills already have this
(:class:`personalclaw.skills.usage.SkillUsageStore`); tools didn't. This is the
analogous counter for the tool surface — same sidecar-JSON, best-effort,
never-break-a-turn contract.

Storage: ``<config>/tool_usage.json`` — ``{tool_name: {"count": int,
"last_used_at": iso8601}}``. Counts are **advisory** (they only drive which
power-up to show), so the read-modify-write tolerates a rare lost update under
concurrency rather than paying for a lock on every tool call. It is incremented
at the native runtime's single dispatch site (``_run_tool`` → ``prov.invoke``),
so every tool that flows through ``list_all_tools()`` is covered without a
per-provider hook.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir

logger = logging.getLogger(__name__)

_USAGE_FILE = "tool_usage.json"


@dataclass(frozen=True)
class ToolUsage:
    """Usage stats for one tool."""

    count: int = 0
    last_used_at: str = ""  # ISO 8601 UTC; empty if never used


class ToolUsageStore:
    """Sidecar JSON counter of per-tool invocations.

    Cheap by design: a single small JSON file loaded on demand. Safe for the
    common single-writer case; under concurrent writers a lost increment is
    acceptable (the counts only pick which capability the power-up surfaces).
    """

    def __init__(self, path: Path | None = None):
        self._path = path or (config_dir() / _USAGE_FILE)

    # ── read ──

    def _load(self) -> dict[str, dict]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def get(self, name: str) -> ToolUsage:
        """Return usage for *name* (zero-valued if never recorded)."""
        row = self._load().get(name)
        if not isinstance(row, dict):
            return ToolUsage()
        return ToolUsage(
            count=int(row.get("count", 0) or 0),
            last_used_at=str(row.get("last_used_at", "") or ""),
        )

    def all_usage(self) -> dict[str, ToolUsage]:
        """Return ``{name: ToolUsage}`` for every recorded tool."""
        out: dict[str, ToolUsage] = {}
        for name, row in self._load().items():
            if isinstance(row, dict):
                out[name] = ToolUsage(
                    count=int(row.get("count", 0) or 0),
                    last_used_at=str(row.get("last_used_at", "") or ""),
                )
        return out

    def used_names(self) -> set[str]:
        """The set of tool names with a non-zero count — the 'touched' surface."""
        return {n for n, u in self.all_usage().items() if u.count > 0}

    # ── write ──

    def record_use(self, name: str, *, now: datetime | None = None) -> int:
        """Increment *name*'s use count and stamp ``last_used_at``. Returns the
        new count (0 on write failure — best-effort, never raises)."""
        if not name:
            return 0
        ts = (now or datetime.now(tz=timezone.utc)).isoformat(timespec="seconds")
        try:
            data = self._load()
            row = _r if isinstance((_r := data.get(name)), dict) else {}
            new_count = int(row.get("count", 0) or 0) + 1
            data[name] = {"count": new_count, "last_used_at": ts}
            atomic_write(self._path, json.dumps(data, indent=2, sort_keys=True))
            return new_count
        except Exception:  # advisory counter — never break a turn
            logger.debug("tool usage record failed for %s", name, exc_info=True)
            return 0
