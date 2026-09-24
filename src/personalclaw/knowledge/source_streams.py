"""Source stream events + the interim JSONL spool (WATCHED-SOURCES §6.1, WS-7).

Three events describe a watched source's life as a stream:

* :data:`SOURCE_ITEM_INGESTED` ``{source_id, item_id, guid, title, url, change}`` — one per
  item the engine (re-)indexed this poll.
* :data:`SOURCE_POLL_COMPLETED` ``{source_id, new_count, escalations, budget_spent}`` — one
  per poll, success or not.
* :data:`SOURCE_QUERY_MATCHED` ``{query_id, item_id}`` — a saved source query matched a
  newly-ingested item (§6.4, :mod:`personalclaw.knowledge.source_queries`).

**Why a spool and not a bus.** These belong on AUTOMATION-SUBSTRATE's event bus, which does
not exist yet (verified: no ``event_bus``/``EventBus`` anywhere under ``src/``). The plan's
own dependency note sanctions the interim: "*Pre-bus interim: fires spool to the engine's own
JSONL and drain when the bus lands (the substrate's spool/cursor rule).*" So this file is
deliberately NOT a bus — no subscriber registry, no dispatch, no delivery semantics. It is an
append-only log plus a cursor, which is the smallest thing a bus can later drain. Building
bus machinery here would mint a second mechanism for the substrate to then delete.

**What rides the payload.** Item *content* lives in the knowledge store; the payload carries
at most a fenced title snippet, wrapped by the ONE core fence
(:func:`personalclaw.security.fence_untrusted`) with ``source=f"source:{source_id}"`` per §8.
A spool record is read back by digest synthesis, which is an LLM boundary — so the snippet is
fenced at WRITE time rather than trusting every future reader to remember. The consequence is
deliberate and load-bearing: **a fenced title is not matchable**, which is exactly §6.1's
"payload content never participates in pattern matching". Saved queries therefore match the
structural record (title/url/content as the provider emitted them), never this payload.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

#: One item the engine (re-)indexed. Payload: ``{source_id, item_id, guid, title, url, change}``.
SOURCE_ITEM_INGESTED = "SourceItemIngested"
#: One completed poll. Payload: ``{source_id, new_count, escalations, budget_spent}``.
SOURCE_POLL_COMPLETED = "SourcePollCompleted"
#: A saved source query matched an ingested item. Payload: ``{query_id, item_id}``.
SOURCE_QUERY_MATCHED = "SourceQueryMatched"

#: The closed vocabulary. A reader (a Trigger's ``source``) validates against this rather than
#: accepting any string, so a typo'd subscription is a startup error and not a silent no-fire.
STREAM_EVENTS: tuple[str, ...] = (
    SOURCE_ITEM_INGESTED,
    SOURCE_POLL_COMPLETED,
    SOURCE_QUERY_MATCHED,
)

#: How much of an item's title rides the event payload, BEFORE fencing. A snippet, not the
#: content: §6.1 keeps content in the store so an event can never become a content channel.
SNIPPET_CHARS = 200

#: Trim threshold. The spool is interim, but "interim" has historically meant months, and an
#: unbounded append-only log on a user's disk is a defect, not a deferral. Absolute ``seq``
#: survives a trim (it is written into each record), so a reader's cursor stays valid across
#: one — it just may skip records that aged out, which is the same loss a bus with a retention
#: window has.
MAX_SPOOL_RECORDS = 5000
TRIM_KEEP_RECORDS = 2500


def spool_path() -> Path:
    """``<home>/sources/events.jsonl``.

    Resolved per call through :func:`personalclaw.config.loader.config_dir` — never captured
    at import — so a test's ``PERSONALCLAW_HOME`` redirect actually binds. An import-time
    constant here would write a real ``~/.personalclaw`` spool from every test run.
    """
    from personalclaw.config.loader import config_dir

    return config_dir() / "sources" / "events.jsonl"


def fenced_snippet(text: str, source_id: str) -> str:
    """A title snippet fenced as untrusted, for an event payload (§8).

    Delegates to the ONE core fence with the plan's exact provenance shape
    (``source="source:<id>"``). Truncation happens BEFORE fencing: truncating after would cut
    the closing marker off a long title and hand the next reader an unterminated fence, which
    is a fence-break the caller performed on the fence's behalf.
    """
    from personalclaw.security import fence_untrusted

    snippet = (text or "").strip()[:SNIPPET_CHARS]
    if not snippet:
        return ""
    return fence_untrusted(
        snippet,
        source=f"source:{source_id}",
        source_type="watched_source",
        source_id=source_id,
        transformation_path="poll",
    )


class SourceEventSpool:
    """Append-only JSONL spool of source stream events, with an absolute-``seq`` cursor.

    ``path`` defaults to :func:`spool_path` resolved lazily on first use, so an engine built
    before ``PERSONALCLAW_HOME`` is set still writes where the test told it to.

    Every write failure is swallowed to a debug log. That direction is deliberate: the spool
    is an observability/handoff surface, and a full disk must not turn a successful poll into
    a lost item. The reverse (a poll that fails because its event could not be logged) would
    make the interim mechanism more dangerous than the gap it fills.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path: Path | None = Path(path) if path is not None else None
        self._next_seq: int | None = None

    @property
    def path(self) -> Path:
        if self._path is None:
            self._path = spool_path()
        return self._path

    # ── writing ────────────────────────────────────────────────────────────────────

    def emit(self, event: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Append one record; return it (or ``None`` when the write failed).

        An event outside :data:`STREAM_EVENTS` is refused rather than logged, so the spool
        cannot accumulate a fourth vocabulary the bus would later have to interpret.
        """
        from datetime import datetime, timezone

        if event not in STREAM_EVENTS:
            logger.warning("refusing unknown source stream event %r", event)
            return None
        record = {
            "seq": self._reserve_seq(),
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "event": event,
            "payload": dict(payload),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001 — a spool write must never fail a poll
            logger.debug("source event spool write failed for %s", event, exc_info=True)
            return None
        self._maybe_trim()
        return record

    def _reserve_seq(self) -> int:
        if self._next_seq is None:
            self._next_seq = self._last_seq_on_disk() + 1
        seq = self._next_seq
        self._next_seq = seq + 1
        return seq

    def _last_seq_on_disk(self) -> int:
        """Highest ``seq`` already written, so a restart continues the sequence instead of
        replaying numbers a consumer's cursor has already passed."""
        best = 0
        for record in self._read_raw():
            try:
                best = max(best, int(record.get("seq", 0)))
            except (TypeError, ValueError):
                continue
        return best

    def _maybe_trim(self) -> None:
        try:
            records = self._read_raw()
            if len(records) <= MAX_SPOOL_RECORDS:
                return
            kept = records[-TRIM_KEEP_RECORDS:]
            # Rewritten through the ONE durable-write helper so a crash mid-trim cannot leave a
            # half-written spool: the reader sees either the old file or the new one. A local
            # mkstemp+os.replace here would be a second implementation of that guarantee.
            from personalclaw.atomic_write import atomic_write

            atomic_write(
                self.path,
                "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in kept),
            )
        except Exception:  # noqa: BLE001 — trimming is hygiene, never a failure path
            logger.debug("source event spool trim failed", exc_info=True)

    # ── reading ────────────────────────────────────────────────────────────────────

    def _read_raw(self) -> list[dict[str, Any]]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except Exception:  # noqa: BLE001
            logger.debug("source event spool read failed", exc_info=True)
            return []
        out: list[dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception:  # noqa: BLE001 — one torn line must not hide the rest
                continue
            if isinstance(record, dict):
                out.append(record)
        return out

    def read(
        self,
        *,
        after_seq: int = 0,
        events: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Records with ``seq > after_seq``, optionally filtered to some event names.

        ``after_seq`` is the consumer's cursor — the exact shape the digest advances (§6.2)
        and the shape the bus will inherit when it drains this file.
        """
        wanted = set(events) if events is not None else None
        out = []
        for record in self._read_raw():
            try:
                seq = int(record.get("seq", 0))
            except (TypeError, ValueError):
                continue
            if seq <= after_seq:
                continue
            if wanted is not None and record.get("event") not in wanted:
                continue
            out.append(record)
        return out

    def latest_seq(self) -> int:
        """The highest ``seq`` on disk — a consumer's "caught up" mark."""
        return self._last_seq_on_disk()
