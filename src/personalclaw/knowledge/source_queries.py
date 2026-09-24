"""Saved source queries — a filter over a stream IS a stream (WATCHED-SOURCES §6.4, WS-7).

The FreshRSS lesson: once a user can save a filter over their item stream, that filter becomes
an addressable event source of its own. So a :class:`SavedSourceQuery` is evaluated against
every newly-ingested item and emits ``SourceQueryMatched`` when it matches.

**Zero tokens is the design, not an optimisation.** §6.4: "*Deterministic rule language before
LLM: 90% of triage costs zero tokens.*" This module therefore imports NOTHING from
``llm_helpers``/``providers``, and the grammar below is a hand-rolled matcher over strings —
deliberately small and boring. ``test_watched_sources_queries.py`` pins the claim by making
any LLM entry point explode and asserting a match still happens.

**What is matched, and why not the event payload.** Matching reads the STRUCTURAL record —
title/url/content as the provider emitted them — never the ``SourceItemIngested`` payload,
whose title is fenced (§6.1: "*payload content never participates in pattern matching*"). A
fenced string is not matchable: ``intitle:release`` would have to see through
``<untrusted_content …>`` markers, and a matcher that strips a fence to look inside it is a
fence-break with extra steps.

**The trigger seam is the EXISTING one.** A match fires through
``trigger_sources.registry.emit`` — the single app-source ingestion point — which namespaces
the event to ``app:watched-sources:SourceQueryMatched``, fences its text at origin and hands
it to ``event_triggers.emit_event``. A user subscribes with an ordinary ``AppEvent``/``event``
trigger whose ``event_glob`` matches that name. §6.4 describes the subscription as
``{source: SourceQueryMatched, pattern: {query_id}}``; the SHIPPED matcher's ``source`` is one
of three enum values (``memory``/``inbox``/``app``) and its pattern kinds are a closed set, so
the plan's literal shape would require a FOURTH event source — a second matcher for one
producer, which the substrate's own amendment forbids ("no new trigger kind, no second
matcher"). The query id rides ``meta.query_id`` instead, which is where the inbox bridge puts
its ``sender``/``address`` for exactly the same reason.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

logger = logging.getLogger(__name__)

#: The registered trigger-source name. The event namespace is derived from it by core, never
#: from an emit call, so nothing else can forge this source's events.
TRIGGER_SOURCE_NAME = "watched-sources"

#: Which haystack a term reads. ``any`` spans title + url + content.
FIELD_ANY = "any"
FIELD_TITLE = "title"
FIELD_URL = "url"
FIELD_CONTENT = "content"

#: The field prefixes the grammar understands. Closed, so ``foo:bar`` (a colon inside a plain
#: term — a URL, a time) stays a literal term instead of becoming a silently-empty field match
#: that makes the whole query true.
FIELD_PREFIXES: dict[str, str] = {
    "intitle": FIELD_TITLE,
    "inurl": FIELD_URL,
    "incontent": FIELD_CONTENT,
}

_TOKEN_RE = re.compile(r'"[^"]*"|\S+')


@dataclass(frozen=True)
class QueryTerm:
    """One parsed term: a substring, the field it reads, and whether it is negated."""

    text: str
    field: str = FIELD_ANY
    negated: bool = False


@dataclass(frozen=True)
class SavedSourceQuery:
    """A named, saved filter over the source item stream (§6.4).

    ``id`` is stable — a trigger binds to it through ``meta.query_id``, so renaming the query
    must not retire the automation the user built on it.
    """

    id: str
    name: str
    query: str
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "query": self.query, "enabled": self.enabled}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SavedSourceQuery":
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or ""),
            query=str(data.get("query") or ""),
            enabled=bool(data.get("enabled", True)),
        )


# ── the grammar (deterministic, zero tokens) ───────────────────────────────────


def parse_query(text: str) -> tuple[QueryTerm, ...]:
    """Parse ``intitle:release !beta`` into terms. Never raises — a garbage query yields no
    terms, and a query with no terms matches NOTHING (see :func:`matches`).

    Supported, and nothing more: ``term`` (anywhere), ``intitle:``/``inurl:``/``incontent:``
    (that field), ``!term`` (must be absent), ``"two words"`` (a phrase). Every term is
    required — an implicit AND — because a saved query's job is to narrow a firehose, and an
    implicit OR would make each added word fire MORE often, which is the opposite of what a
    user typing a second word means.
    """
    terms: list[QueryTerm] = []
    for raw in _TOKEN_RE.findall(text or ""):
        token = raw.strip()
        negated = False
        if token.startswith("!"):
            negated = True
            token = token[1:]
        field_name = FIELD_ANY
        prefix, sep, rest = token.partition(":")
        if sep and prefix.lower() in FIELD_PREFIXES:
            field_name = FIELD_PREFIXES[prefix.lower()]
            token = rest
        if token.startswith('"') and token.endswith('"') and len(token) >= 2:
            token = token[1:-1]
        token = token.strip()
        if not token:
            continue
        terms.append(QueryTerm(text=token.lower(), field=field_name, negated=negated))
    return tuple(terms)


def matches(
    terms: Sequence[QueryTerm], *, title: str = "", url: str = "", content: str = ""
) -> bool:
    """True when EVERY term is satisfied. An empty term list matches nothing.

    The empty case is the load-bearing one: a query that failed to parse (or was saved blank)
    would otherwise match every item in the library and turn a saved filter into a firehose
    that fires a user's automation on everything. Refusing to match is the safe direction —
    a filter that fires too little is visible, one that fires on everything is an incident.
    """
    if not terms:
        return False
    haystacks = {
        FIELD_TITLE: (title or "").lower(),
        FIELD_URL: (url or "").lower(),
        FIELD_CONTENT: (content or "").lower(),
    }
    haystacks[FIELD_ANY] = " \n".join(
        (haystacks[FIELD_TITLE], haystacks[FIELD_URL], haystacks[FIELD_CONTENT])
    )
    for term in terms:
        present = term.text in haystacks.get(term.field, "")
        if present == term.negated:
            return False
    return True


def matching_query_ids(
    queries: Iterable[SavedSourceQuery],
    *,
    title: str = "",
    url: str = "",
    content: str = "",
) -> list[str]:
    """Ids of the enabled queries this item satisfies. Pure, deterministic, no I/O."""
    out: list[str] = []
    for query in queries:
        if not query.enabled:
            continue
        if matches(parse_query(query.query), title=title, url=url, content=content):
            out.append(query.id)
    return out


# ── persistence ────────────────────────────────────────────────────────────────


def queries_path() -> Path:
    """``<home>/sources/saved_queries.json`` — resolved per call, never at import, so a test's
    ``PERSONALCLAW_HOME`` redirect binds (an import-bound path writes the real home)."""
    from personalclaw.config.loader import config_dir

    return config_dir() / "sources" / "saved_queries.json"


@dataclass
class SavedQueryStore:
    """JSON-file store for saved source queries.

    A file rather than a ``knowledge.db`` table on purpose: a saved query is user
    configuration over the stream, not an item IN it, and a new table would make every
    knowledge-store consumer carry a migration for a list of three strings.
    """

    path: Path | None = None
    _cache: list[SavedSourceQuery] | None = field(default=None, init=False, repr=False)

    def _file(self) -> Path:
        return self.path if self.path is not None else queries_path()

    def list_queries(self) -> list[SavedSourceQuery]:
        try:
            raw = json.loads(self._file().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except Exception:  # noqa: BLE001 — a corrupt file must not stall every poll
            logger.warning("saved source queries file is unreadable; treating as empty")
            return []
        rows = raw.get("queries") if isinstance(raw, dict) else raw
        if not isinstance(rows, list):
            return []
        return [SavedSourceQuery.from_dict(r) for r in rows if isinstance(r, dict)]

    def save_all(self, queries: Sequence[SavedSourceQuery]) -> None:
        path = self._file()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"queries": [q.to_dict() for q in queries]}
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def add(self, name: str, query: str, *, query_id: str = "") -> SavedSourceQuery:
        import uuid

        saved = SavedSourceQuery(
            id=query_id or f"sq-{uuid.uuid4().hex[:8]}", name=name, query=query
        )
        self.save_all([*self.list_queries(), saved])
        return saved

    def remove(self, query_id: str) -> bool:
        rows = self.list_queries()
        kept = [q for q in rows if q.id != query_id]
        if len(kept) == len(rows):
            return False
        self.save_all(kept)
        return True


# ── the trigger bridge ─────────────────────────────────────────────────────────


class WatchedSourcesTriggerSource:
    """The registered producer of ``SourceQueryMatched`` on the existing event bus.

    Duck-typed against :class:`personalclaw.trigger_sources.base.TriggerSourceProvider` rather
    than subclassing it: the ABC's ``start(emit)`` contract is a PUSH watch loop an app owns,
    and this producer has none — the SourceEngine's own poll loop is the watcher, and the
    registration exists only so ``registry.emit`` accepts the source (it refuses unregistered
    names). Declaring a ``start`` that took an ``emit`` callable and ignored it would be a
    second, dead emit path.

    ``events`` declares ONLY ``SourceQueryMatched``. ``SourceItemIngested`` and
    ``SourcePollCompleted`` stay on the spool and are deliberately NOT declared here: an event
    declared in a browsable vocabulary that never reaches the bus is the "declared kind without
    a runtime" defect, and bridging a per-item event to triggers is a firehose this atom was
    not asked to open.
    """

    name = TRIGGER_SOURCE_NAME
    display_name = "Watched Sources"

    @property
    def events(self) -> tuple[str, ...]:
        from personalclaw.knowledge.source_streams import SOURCE_QUERY_MATCHED

        return (SOURCE_QUERY_MATCHED,)

    async def start(self, emit: Any) -> None:  # pragma: no cover - no push loop to start
        return None

    async def stop(self) -> None:  # pragma: no cover - nothing acquired
        return None


def ensure_registered() -> None:
    """Register the producer if it is not already. Idempotent, never raises.

    Lazy rather than at import: the registry is process-global, and registering from an import
    would put this source in every CLI invocation's registry whether or not a source is watched.
    """
    try:
        from personalclaw.trigger_sources.registry import get_source, register_source

        if get_source(TRIGGER_SOURCE_NAME) is None:
            register_source(WatchedSourcesTriggerSource())  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 — a registration fault must not fail a poll
        logger.debug("watched-sources trigger source registration failed", exc_info=True)


def fire_query_matched(query_id: str, item_id: str, *, title: str = "", source_id: str = "") -> str:
    """Emit ``SourceQueryMatched`` onto the event bus; return the namespaced name (or "").

    ``title`` is handed over RAW: ``registry.emit`` fences at origin with its own provenance,
    and pre-fencing here would either double-wrap or (via ``is_fenced``) replace the origin's
    richer attributes with a coarser set.
    """
    try:
        from personalclaw.knowledge.source_streams import SOURCE_QUERY_MATCHED
        from personalclaw.trigger_sources.base import SourceEvent
        from personalclaw.trigger_sources.registry import emit

        ensure_registered()
        return emit(
            TRIGGER_SOURCE_NAME,
            SourceEvent(
                event=SOURCE_QUERY_MATCHED,
                key=item_id,
                text=title,
                meta={"query_id": query_id, "item_id": item_id, "source_id": source_id},
            ),
        )
    except Exception:  # noqa: BLE001 — a trigger fault must not fail the poll that fed it
        logger.debug("SourceQueryMatched fire failed for %s", query_id, exc_info=True)
        return ""


def evaluate(
    *,
    item_id: str,
    source_id: str,
    title: str = "",
    url: str = "",
    content: str = "",
    spool: Any,
    store: Any | None = None,
) -> list[str]:
    """Match one newly-ingested item against every saved query (§6.4).

    Per match: a ``SourceQueryMatched`` record on the spool AND a fire on the event bus. Both,
    because they answer different questions — the spool is the durable stream a digest reads,
    the bus is what makes a Trigger run now. Emitting only one would leave either the history
    or the automation silently missing.

    Returns the matched query ids so a caller (and a test) can see the decision, not just its
    side effects.
    """
    from personalclaw.knowledge.source_streams import SOURCE_QUERY_MATCHED

    queries = (store if store is not None else SavedQueryStore()).list_queries()
    matched = matching_query_ids(queries, title=title, url=url, content=content)
    for query_id in matched:
        spool.emit(SOURCE_QUERY_MATCHED, {"query_id": query_id, "item_id": item_id})
        fire_query_matched(query_id, item_id, title=title, source_id=source_id)
    return matched
