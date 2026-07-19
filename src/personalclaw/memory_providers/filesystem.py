"""Filesystem plain-text memory provider — the capability-degraded fallback.

The VISION fallback chain, made real (memory-architecture.md §3.4): when no
embedding model is configured, the native record/vector provider reports
``capabilities.vector=False`` and the service degrades retrieval to keyword
search. This provider is the *plain-text* end of that chain — it persists
records as markdown + an FTS5 index (no vectors at all), so it works on a machine
with no embedder, no FAISS, no numpy.

It implements the SAME v2 ``MemoryProvider`` contract as the native store, so the
service drives it identically and an external/Mem0/Letta provider would slot in
the same way. ``capabilities().vector`` is always False → ``vector_query`` returns
[] and the service falls back to ``query`` + FTS, exactly like Knowledge degrades
to FTS+graph.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from personalclaw.memory_providers.base import MemoryProvider

if TYPE_CHECKING:
    from personalclaw.memory import MemoryStore
    from personalclaw.memory_record import MemoryCapabilities, MemoryRecord

logger = logging.getLogger(__name__)


class FilesystemMemoryProvider(MemoryProvider):
    """A vector-less provider backed by the markdown projection's FTS5 index.

    Wraps a ``MemoryStore`` (preferences.md / projects.md / daily history + the
    FTS5 ``memory_fts`` index) and exposes it through the record contract. Records
    are the markdown files as ``kind=preference|note`` rows; ``query`` lists them,
    ``vector_query`` is empty (no vectors), and search degrades to the FTS index.
    """

    def __init__(self, store: "MemoryStore") -> None:
        self._store = store

    @property
    def name(self) -> str:
        return "filesystem"

    def init(self) -> None:
        self._store.init()

    def capabilities(self) -> "MemoryCapabilities":
        from personalclaw.memory_record import MemoryCapabilities

        # No vectors, no event log — just FTS keyword search over markdown.
        return MemoryCapabilities(
            vector=False, transactional_batch=False,
            event_log=False, full_text_search=True,
        )

    # ── record CRUD over the markdown files ───────────────────────────────────

    def put(self, records: "list[MemoryRecord]") -> None:
        """Append text records to the preferences/notes markdown (best-effort).

        A plain-text provider has no key-addressable rows; it appends a record's
        text to preferences (the durable human-readable store). Episodic/lesson
        records land in history as timestamped notes."""
        from personalclaw.memory_record import MemoryKind

        for rec in records:
            text = rec.text or (rec.value if isinstance(rec.value, str) else json.dumps(rec.value))
            if not text:
                continue
            if rec.kind in (MemoryKind.PREFERENCE, MemoryKind.SEMANTIC, MemoryKind.NOTE):
                self._store.add_preference(text)
            else:
                self._store.append_history(text)

    def get(self, record_id: str) -> "MemoryRecord | None":
        # Plain-text files are not key-addressable; no single-record fetch.
        return None

    def delete(self, record_id: str, *, source: str = "user_explicit") -> bool:
        return False

    def query(
        self,
        *,
        kinds: "set[str] | None" = None,
        scope: str | None = None,
        scope_ref: str | None = None,
        include_deleted: bool = False,
        limit: int | None = None,
    ) -> "list[MemoryRecord]":
        """The markdown content as preference/note records."""
        from personalclaw.memory_record import MemoryKind, MemoryRecord

        out: list[MemoryRecord] = []
        prefs = self._store.read_preferences().strip()
        if prefs and (kinds is None or MemoryKind.PREFERENCE.value in kinds):
            out.append(MemoryRecord(id="preferences", kind=MemoryKind.PREFERENCE, text=prefs))
        projects = self._store.read_projects().strip()
        if projects and (kinds is None or MemoryKind.NOTE.value in kinds):
            out.append(MemoryRecord(id="projects", kind=MemoryKind.NOTE, text=projects))
        return out[:limit] if limit is not None else out

    # ── vector ops — always degraded (no embedder) ────────────────────────────

    def vector_query(
        self,
        *,
        text: str = "",
        embedding: "list[float] | None" = None,
        k: int = 8,
        kinds: "set[str] | None" = None,
    ) -> "list[dict]":
        """No vectors — degrade to the FTS keyword index."""
        if not text:
            return []
        hits = self._store.search(text, limit=k)
        # Normalize FTS hits ({path, snippet, rank}) into the scored-hit shape the
        # service expects ({text, score, ...}).
        return [
            {"id": h.get("path", ""), "text": h.get("snippet", ""),
             "score": -float(h.get("rank", 0.0)), "source": "fts"}
            for h in hits
        ]

    def embed(self, text: str) -> "list[float] | None":
        return None

    # ── event log — unsupported (no WAL on plain text) ─────────────────────────

    def append_event(self, **_kw) -> int:
        return 0

    def read_events(self, *, limit: int = 50, offset: int = 0) -> "list[dict]":
        return []
