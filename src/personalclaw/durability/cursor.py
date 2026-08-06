"""The consumed-only pull cursor (DURABILITY-AND-SYNC §4.1, DAS-6c-ii-b).

When the cycle pulls a peer's shards it merges them, and only then may it record "I have
now seen this peer up to seq N". The cursor is that durable per-peer high-water mark — the
``seen`` map :meth:`registry.Registry.new_prefixes_since` reads to decide what is still
unpulled. Its one hard rule, verbatim from §4.1:

    the pull cursor advances **only on consumed rows** — prerequisite-absent holds the
    drain, payload-bad advances+logs.

So the cursor is *not* advanced merely because a pull was attempted. Three consume verdicts:

* **consumed** — the shard set merged cleanly → advance the peer's high-water mark to that
  seq. Because prefixes are pulled oldest-first (the registry yields them ascending), the
  mark only ever moves forward by contiguous seqs; a gap is never skipped.
* **prerequisite-absent** — the shard references state this machine doesn't have yet (an
  out-of-order arrival) → **hold**: do not advance, so the same seq is retried next cycle
  once its prerequisite lands. This is the one verdict that must not advance, or the drain
  would strand work it silently skipped past.
* **payload-bad** — the shard is structurally broken and will never merge → advance anyway
  and log, so one poison object can't wedge the cursor and block every later seq behind it.

Advancement is monotonic (a stale verdict can never lower a mark) and clock-free bookkeeping
persisted as one small JSON file; the merge itself lives in :mod:`durability.merge`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

# ── consume verdicts ─────────────────────────────────────────────────────────
CONSUMED = "consumed"
PREREQ_ABSENT = "prerequisite-absent"
PAYLOAD_BAD = "payload-bad"

#: Verdicts that advance the cursor. prerequisite-absent is deliberately NOT here.
_ADVANCING = frozenset({CONSUMED, PAYLOAD_BAD})

_CURSOR_FILE = "pull_cursor.json"


class Cursor:
    """Durable per-peer high-water mark of the highest seq consumed from each peer."""

    def __init__(self, sync_root: Path) -> None:
        self._path = Path(sync_root) / _CURSOR_FILE
        self._seen: dict[str, int] = self._load()

    def _load(self) -> dict[str, int]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
            return {}
        seen: dict[str, int] = {}
        for peer, seq in (raw.get("seen", {}) if isinstance(raw, dict) else {}).items():
            try:
                seen[str(peer)] = int(seq)
            except (TypeError, ValueError):
                continue  # a corrupt row degrades to "never seen", never crashes a pull
        return seen

    def seen(self) -> dict[str, int]:
        """The ``peer_id → highest consumed seq`` map, as
        :meth:`registry.Registry.new_prefixes_since` expects. A copy — callers can't
        mutate the cursor's state behind its back."""
        return dict(self._seen)

    def seq_of(self, peer_id: str) -> int:
        return self._seen.get(peer_id, 0)

    def record(self, peer_id: str, seq: int, verdict: str) -> bool:
        """Apply a consume verdict for ``peer_id``'s seq ``seq``. Returns True if the mark
        advanced (and was persisted).

        Advances only on an advancing verdict (``consumed``/``payload-bad``) AND only when
        ``seq`` is strictly higher than what's already recorded — monotonic, so a stale or
        replayed verdict is a no-op. ``prerequisite-absent`` holds (returns False without
        writing), so the same seq is retried next cycle.
        """
        if verdict == PREREQ_ABSENT:
            return False  # hold the drain — retry this seq once its prerequisite lands
        if verdict not in _ADVANCING:
            logger.warning(
                "cursor: unknown verdict %r for %s seq %d — holding", verdict, peer_id, seq
            )
            return False
        if seq <= self._seen.get(peer_id, 0):
            return False  # monotonic: never move a mark backward
        if verdict == PAYLOAD_BAD:
            # Advance past a poison object so it can't wedge every later seq — but say so.
            logger.warning(
                "cursor: advancing past unmergeable shard for %s seq %d (payload-bad)",
                peer_id,
                seq,
            )
        self._seen[peer_id] = seq
        self._persist()
        return True

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self._path, json.dumps({"seen": self._seen}, indent=2, sort_keys=True) + "\n")
