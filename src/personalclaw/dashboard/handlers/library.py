"""The library's one read, ``GET /api/library``: every thing you keep, one row per thing.

The library is two stores and two record files. The knowledge store holds notes, files, links,
decisions and the write-ups scheduled reports keep. The artifact store holds pages, apps and the
documents an agent made. ``research_reports.json`` defines the reports, and ``intents.json`` the
intents, whose findings the knowledge store keeps by value. Each used to be listed by its own route,
ordered and filtered its own way, and an artifact's search mirror sat beside it in the knowledge
store. Here they are one list:

- a knowledge item is a ``knowledge:<id>`` row; an artifact's mirror is never one, since the
  artifact is the ``artifact:<slug>`` row;
- a scheduled report is one ``report:<id>`` row however many runs it has made, carrying its
  write-up;
- an intent with at least one finding is one ``findings:<id>`` row, and an intent with none is no
  row.

An archived thing is a row the read leaves out until its filter asks for ``state=archived``.

Every row has one shape (:func:`_row`). The read answers with every row the filter leaves (up to
:data:`MAX_ROWS`), a true ``total``, the count of each filter value, and ``failed``: each store it
couldn't read, by its ``store`` value. A store that fails leaves the other store's rows and counts.

Which project a thing is in is :mod:`personalclaw.project_items`' one rule, which
``GET /api/projects/{id}/linked`` reads too. Times are written ``%Y-%m-%dT%H:%M:%SZ``, "" when the
source keeps none, never now.

An unchanged library answers 304 to its ``ETag``. The revision (:func:`revision`) is read without
reading a row: the knowledge store's own counter, a stat of each artifact's ``meta.json``, the
record files' times, the projects and loops, and the current minute (a row's ``from.title`` is no
part of it, so a renamed chat shows its new name within a minute). The read sets
``Cache-Control: no-cache`` itself: under the gateway's default ``no-store`` the browser keeps no
copy to revalidate.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from aiohttp import web

from personalclaw import project_items
from personalclaw.knowledge.artifact_ingest import ARTIFACT_ITEM_TYPE
from personalclaw.knowledge.semantics import RESEARCH_FINDING_KIND

logger = logging.getLogger(__name__)

#: The most rows one read answers. A library past it says so: ``complete`` is false.
MAX_ROWS = 10_000

#: How many of a row's tags it carries (the row shows a few; the Filter has them all).
ROW_TAGS = 3

#: The list's text preview, as ``GET /api/knowledge/items`` cuts it.
EXCERPT_CHARS = 280

#: The two stores a row comes from, as the ``store`` filter and ``failed`` name them.
STORE_ARTIFACTS = "artifacts"
STORE_KNOWLEDGE = "knowledge"

#: The library's kinds: one vocabulary over the two stores' types (Notes, Documents, Links, Images
#: and media, Pages and apps, Reports). A row's ``format`` keeps its exact type.
KIND_NOTE, KIND_DOCUMENT, KIND_LINK, KIND_MEDIA, KIND_PAGE, KIND_REPORT = (
    "note",
    "document",
    "link",
    "media",
    "page",
    "report",
)

_KNOWLEDGE_KINDS = {
    "note": KIND_NOTE,
    "fleeting": KIND_NOTE,
    "journal": KIND_NOTE,
    "gist": KIND_NOTE,
    "decision": KIND_NOTE,
    "bookmark": KIND_LINK,
    "image": KIND_MEDIA,
    "audio": KIND_MEDIA,
    "video": KIND_MEDIA,
    "pdf": KIND_DOCUMENT,
    "document": KIND_DOCUMENT,
    "sheet": KIND_DOCUMENT,
    "slides": KIND_DOCUMENT,
}
_FILE_TYPES = frozenset({"image", "audio", "video", "pdf", "document", "sheet", "slides"})

_ARTIFACT_KINDS = {
    "widget": KIND_PAGE,
    "html": KIND_PAGE,
    "react": KIND_PAGE,
    "infographic": KIND_PAGE,
    "image": KIND_MEDIA,
    "video": KIND_MEDIA,
    "svg": KIND_MEDIA,
}

#: Who made a thing (the Filter's Made by), and the word each is said with.
MADE_YOU, MADE_CHATS, MADE_AUTOMATIONS, MADE_SOURCES, MADE_NOT_RECORDED = (
    "you",
    "chats",
    "automations",
    "sources",
    "not_recorded",
)
MADE_LABELS = {
    MADE_YOU: "You",
    MADE_CHATS: "Chats",
    MADE_AUTOMATIONS: "Automations",
    MADE_SOURCES: "Sources",
    MADE_NOT_RECORDED: "Not recorded",
}

#: A row's ``state``: where its reading stands. "" for one that is read, or never needed reading.
STATE_BEING_READ, STATE_UNREADABLE = "being_read", "unreadable"

#: The ``state`` filter's values, beside the reading states: the read state of a knowledge item.
READ_STATES = frozenset({"unread", "reading", "read"})

SORT_ACTIVITY, SORT_ADDED, SORT_NAME = "activity", "added", "name"
_SORTS = (SORT_ACTIVITY, SORT_ADDED, SORT_NAME)

#: The filter value that asks for the things in no project.
NO_PROJECT = "none"

#: The facets the read counts, in the order the Filter lists them.
FACETS = ("store", "kind", "made", "project", "collection", "tag", "state")


def stamp(value: Any) -> str:
    """*value*, a stored time (ISO text or epoch seconds), written ``%Y-%m-%dT%H:%M:%SZ`` in UTC;
    "" for none or for one that can't be read. Never now."""
    if value in (None, "", 0):
        return ""
    try:
        if isinstance(value, (int, float)):
            moment = datetime.fromtimestamp(float(value), tz=timezone.utc)
        else:
            moment = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return ""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _excerpt(text: Any) -> str:
    return str(text or "").strip()[:EXCERPT_CHARS]


def _metadata(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    try:
        loaded = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


@dataclass
class _Context:
    """What one read resolves once and every row reads: the projects, and the titles of the things
    rows come from (chats, loops, runs, sources, intents), resolved only when a row names one."""

    projects: project_items.Projects
    reader: str = ""
    sources: dict[str, str] = field(default_factory=dict)
    intents: dict[str, str] = field(default_factory=dict)
    project_tags: frozenset[str] = frozenset()
    _chats: dict[str, str] | None = None
    _loops: dict[str, str] | None = None
    _runs: dict[str, str] = field(default_factory=dict)
    #: Who reads the library (`subagent_reach.reader_of`): a workflow run another chat holds as
    #: its own is no run to them (`workflows.chat_runs`), so a row names it with no title.
    runs_reader: Any = None
    #: The reports whose run holds its claim now.
    running_reports: frozenset[str] = frozenset()

    def chat_title(self, key: str) -> str:
        """The chat's title as the chat list shows it, "" once its transcript is gone. The list
        names a chat by its transcript's file name, which is the key spelled as a file name."""
        from personalclaw import history

        if self._chats is None:
            self._chats = {}
            try:
                for entry in history.ConversationLog().list_sessions():
                    self._chats[str(entry.get("key") or "")] = str(entry.get("title") or "")
            except Exception:
                logger.debug("library: chat titles unreadable", exc_info=True)
        return self._chats.get(history._safe_key(key), "")

    def loop_title(self, loop_id: str) -> str:
        if self._loops is None:
            self._loops = {}
            try:
                from personalclaw.loop import store as loop_store

                for lp in loop_store.list_all():
                    self._loops[lp.id] = str(lp.name or lp.task[:60] or "")
            except Exception:
                logger.debug("library: loop titles unreadable", exc_info=True)
        return self._loops.get(loop_id, "")

    def run_title(self, run_id: str) -> str:
        if run_id not in self._runs:
            title = ""
            try:
                from personalclaw.workflows import chat_runs
                from personalclaw.workflows import store as run_store

                run = run_store.get(run_id)
                if run is not None and self.runs_reader is not None:
                    run = run if chat_runs.reads(self.runs_reader, run) else None
                title = str(getattr(run, "workflow_name", "") or "") if run else ""
            except Exception:
                logger.debug("library: run %s unreadable", run_id, exc_info=True)
            self._runs[run_id] = title
        return self._runs[run_id]

    def came_from(self, ref: str) -> dict | None:
        """``{ref, title}`` for the thing *ref* names, its title "" once it is gone."""
        if not ref:
            return None
        kind, _, ident = ref.partition(":")
        lookups: dict[str, Callable[[str], str | None]] = {
            "chat": self.chat_title,
            "loop": self.loop_title,
            "workflow": self.run_title,
            "source": self.sources.get,
            "intent": self.intents.get,
        }
        lookup = lookups.get(kind)
        title = lookup(ident) if lookup is not None else ""
        return {"ref": ref, "title": str(title or "")}

    def project(self, project_id: str) -> dict | None:
        if not self.projects.exists(project_id):
            return None
        return {"id": project_id, "name": self.projects.name(project_id)}


def _row(**fields: Any) -> dict:
    """One row, in the one shape every thing in the library has. Fields a later part of the read
    fills, and the ones the filter reads but the answer leaves out (``_``-named), are given here so
    every row carries them."""
    row: dict[str, Any] = {
        "ref": "",
        "title": "",
        "kind": "",
        "format": "",
        "store": "",
        "made": MADE_NOT_RECORDED,
        "made_label": MADE_LABELS[MADE_NOT_RECORDED],
        "from": None,
        "contributor": "",
        "project": None,
        "collections": [],
        "tags": [],
        "created_at": "",
        "activity_at": "",
        "read": None,
        "pinned": False,
        "archived": False,
        "running": False,
        "state": "",
        "excerpt": "",
        "size": None,
        "version": None,
        "thumb": None,
        "match": None,
        "_tags": [],
        "_projects": set(),
    }
    row.update(fields)
    row["made_label"] = MADE_LABELS.get(row["made"], "")
    return row


# ── knowledge ────────────────────────────────────────────────────────────────


def _knowledge_made(item_type: str, provider: str, meta: dict, source_id: str) -> str:
    if source_id or provider not in ("", "native"):
        return MADE_SOURCES
    if meta.get("run_id"):
        return MADE_AUTOMATIONS
    origin = str(meta.get("origin") or "")
    if item_type == "decision" or origin == "chat":
        return MADE_CHATS
    if item_type in _FILE_TYPES or origin == "owner":
        return MADE_YOU
    return MADE_NOT_RECORDED


def _knowledge_from(meta: dict, source_id: str) -> str:
    if meta.get("run_id"):
        return f"workflow:{meta['run_id']}"
    if source_id:
        return f"source:{source_id}"
    if meta.get("session_key"):
        return f"chat:{meta['session_key']}"
    return ""


def _knowledge_state(processing_status: str) -> str:
    if processing_status in ("queued", "processing"):
        return STATE_BEING_READ
    if processing_status in ("failed", "partial"):
        return STATE_UNREADABLE
    return ""


def _shown_tags(tags: Iterable[str], hidden: frozenset[str]) -> list[str]:
    return [t for t in tags if t not in hidden and not t.startswith(project_items.LOOP_TAG_PREFIX)]


def knowledge_rows(store: Any, ctx: _Context) -> list[dict]:
    """The knowledge store's things, one row each: every item that is not archived, not an
    artifact's search mirror and not a report's write-up (a report is its own row). One read of the
    items, one of their tags and one of their collections, whatever the library's size."""
    from personalclaw.identity import contributor_label
    from personalclaw.knowledge import project_scope, sharing

    tags: dict[str, list[str]] = {}
    for item_id, name in store.db.execute(
        "SELECT it.item_id, t.name FROM item_tags it JOIN tags t ON t.id = it.tag_id "
        "ORDER BY t.name"
    ):
        tags.setdefault(item_id, []).append(name)
    collections: dict[str, list[dict]] = {}
    for item_id, cid, cname in store.db.execute(
        "SELECT ci.item_id, c.id, c.name FROM collection_items ci "
        "JOIN collections c ON c.id = ci.collection_id ORDER BY c.position, c.created_at"
    ):
        collections.setdefault(item_id, []).append({"id": cid, "name": cname})
    rows: list[dict] = []
    for r in store.db.execute(
        "SELECT id, title, item_type, kind, provider, source_id, file_metadata, created_at, "
        "updated_at, read_state, processing_status, content, mime_type, file_size, thumbnail_path, "
        "is_pinned, favorited, is_archived "
        "FROM items WHERE item_type != ? AND COALESCE(kind, '') != ?",
        (ARTIFACT_ITEM_TYPE, RESEARCH_FINDING_KIND),
    ):
        item_id = str(r["id"])
        item_type = str(r["item_type"] or "")
        meta = _metadata(r["file_metadata"])
        source_id = str(r["source_id"] or "")
        owner, _run, _policy = project_scope.item_scope(meta)
        who = sharing.contributor_of(meta)
        own_tags = tags.get(item_id, [])
        visible_in = (
            {
                pid
                for pid in ctx.projects.names
                if project_scope.visible_in_project(meta, project_id=pid)
            }
            if owner
            else set()
        )
        size = r["file_size"] if item_type in _FILE_TYPES else None
        if size is None and item_type in _FILE_TYPES:
            size = meta.get("file_size")
        rows.append(
            _row(
                ref=f"knowledge:{item_id}",
                title=str(r["title"] or ""),
                kind=_KNOWLEDGE_KINDS.get(item_type, KIND_NOTE),
                format=item_type,
                store=STORE_KNOWLEDGE,
                made=_knowledge_made(item_type, str(r["provider"] or ""), meta, source_id),
                **{"from": ctx.came_from(_knowledge_from(meta, source_id))},
                contributor=who if who and contributor_label(who, ctx.reader) else "",
                project=ctx.project(owner),
                collections=collections.get(item_id, []),
                tags=_shown_tags(own_tags, ctx.project_tags)[:ROW_TAGS],
                created_at=stamp(r["created_at"]),
                activity_at=stamp(r["updated_at"]),
                read=str(r["read_state"] or "unread"),
                # Pin merges the star: either one keeps a note at the top.
                pinned=bool(r["is_pinned"]) or bool(r["favorited"]),
                archived=bool(r["is_archived"]),
                state=_knowledge_state(str(r["processing_status"] or "")),
                excerpt=_excerpt(r["content"]),
                size=int(size) if isinstance(size, (int, float)) and size else None,
                thumb=f"/api/knowledge/items/{item_id}/thumbnail" if r["thumbnail_path"] else None,
                _tags=own_tags,
                _projects=visible_in,
            )
        )
    return rows


def report_rows(store: Any, ctx: _Context) -> list[dict]:
    """Each scheduled report, one row however many runs it has made, carrying its write-up (the
    item its runs keep upserting)."""
    from personalclaw.knowledge.research_reports import load_reports
    from personalclaw.knowledge.semantics import logical_key

    found = list(
        store.db.execute(
            "SELECT id, logical_key, content, created_at, updated_at FROM items WHERE kind = ?",
            (RESEARCH_FINDING_KIND,),
        )
    )
    by_id = {str(r["id"]): r for r in found}
    by_key = {str(r["logical_key"] or ""): r for r in found}
    rows: list[dict] = []
    for defn in load_reports():
        key = logical_key(RESEARCH_FINDING_KIND, defn.name)
        # Its own write-up, by id; a report saved before reports kept it reads the item of its
        # name until the migration gives it one (`research_reports.migrate_write_ups`).
        kept = defn.finding_item_id
        up = by_key.get(key) if kept is None else by_id.get(kept)
        rows.append(
            _row(
                ref=f"report:{defn.id}",
                title=str(defn.name or ""),
                kind=KIND_REPORT,
                format="report",
                store=STORE_KNOWLEDGE,
                made=MADE_AUTOMATIONS,
                created_at=stamp(defn.created_ts),
                activity_at=stamp(up["updated_at"]) if up else "",
                excerpt=_excerpt(up["content"]) if up else "",
                running=defn.id in ctx.running_reports,
                _write_up=str(up["id"]) if up else "",
            )
        )
    return rows


def findings_rows(store: Any, ctx: _Context, intents: Iterable[Any]) -> list[dict]:
    """Each intent that has kept at least one finding, one row: its findings log. An intent with
    none is no row."""
    first: dict[str, str] = {}
    last: dict[str, str] = {}
    newest: dict[str, str] = {}
    for intent_id, created_at, takeaway in store.db.execute(
        "SELECT intent_id, created_at, takeaway FROM intent_outcomes ORDER BY created_at, rowid"
    ):
        first.setdefault(intent_id, created_at)
        last[intent_id] = created_at
        newest[intent_id] = takeaway
    logs = store.intent_log_states()
    rows: list[dict] = []
    for intent in intents:
        if intent.id not in last:
            continue
        log = logs.get(intent.id, {})
        rows.append(
            _row(
                ref=f"findings:{intent.id}",
                title=str(intent.goal or getattr(intent, "name", "") or ""),
                kind=KIND_REPORT,
                format="findings",
                store=STORE_KNOWLEDGE,
                made=MADE_AUTOMATIONS,
                **{"from": ctx.came_from(f"intent:{intent.id}")},
                created_at=stamp(first[intent.id]),
                activity_at=stamp(last[intent.id]),
                excerpt=_excerpt(newest[intent.id]),
                read="read" if log.get("read") else "unread",
                pinned=bool(log.get("pinned")),
                archived=bool(log.get("archived")),
            )
        )
    return rows


# ── artifacts ────────────────────────────────────────────────────────────────


def _artifact_made(art: Any) -> str:
    if project_items.loop_ids_in_tags(art.tags or ()):
        return MADE_AUTOMATIONS
    return {
        "manual": MADE_YOU,
        "import": MADE_YOU,
        "chat": MADE_CHATS,
        "cron": MADE_AUTOMATIONS,
        "subagent": MADE_AUTOMATIONS,
    }.get(str(art.source or ""), MADE_NOT_RECORDED)


def _artifact_from(art: Any) -> str:
    loops = project_items.loop_ids_in_tags(art.tags or ())
    if loops:
        return f"loop:{loops[0]}"
    events = list(getattr(art, "events", None) or ())
    session = str(getattr(events[0], "session_id", "") or "") if events else ""
    return f"chat:{session}" if session and art.source == "chat" else ""


def _artifact_root(prov: Any) -> str:
    """The artifact store's root, checked once for the read (``_ensure_root``), "" when it is not
    there or not to be read: checking it for each artifact cost the read as much as the list."""
    try:
        return str(prov._ensure_root())
    except Exception:
        logger.debug("library: the artifact root is unreadable", exc_info=True)
        return ""


def _body_size(root: str, art: Any) -> int | None:
    """The artifact's body file's size, by a stat alone under the store's *root*; None when it has
    no body file."""
    from personalclaw.artifacts.models import is_valid_slug

    if not root or not is_valid_slug(str(art.slug or "")):
        return None
    try:
        sizes = [
            e.stat().st_size
            for e in os.scandir(os.path.join(root, art.slug))
            if e.is_file() and e.name != "meta.json"
        ]
    except OSError:
        return None
    return max(sizes) if sizes else None


def artifact_rows(prov: Any, ctx: _Context, collection_ids: dict[str, str]) -> list[dict]:
    """The artifact store's things, one row each. An artifact's one ``collection`` label is its
    collection, matched to the knowledge store's collection of that name."""
    rows: list[dict] = []
    root = _artifact_root(prov)
    for art in prov.list():
        pid = project_items.artifact_project_id(art, ctx.projects)
        label = str(art.collection or "")
        rows.append(
            _row(
                ref=f"artifact:{art.slug}",
                title=str(art.name or ""),
                kind=_ARTIFACT_KINDS.get(str(art.kind or ""), KIND_DOCUMENT),
                format=str(art.kind or ""),
                store=STORE_ARTIFACTS,
                made=_artifact_made(art),
                **{"from": ctx.came_from(_artifact_from(art))},
                project=ctx.project(pid),
                collections=[{"id": collection_ids.get(label, ""), "name": label}] if label else [],
                tags=_shown_tags(art.tags or (), ctx.project_tags)[:ROW_TAGS],
                created_at=stamp(art.created_at),
                activity_at=stamp(art.updated_at),
                excerpt=_excerpt(art.description),
                size=_body_size(root, art),
                version=int(art.version or 1),
                pinned=bool(getattr(art, "pinned", False)),
                archived=bool(getattr(art, "archived", False)),
                thumb=f"/api/artifacts/{art.slug}/raw" if art.kind == "image" else None,
                _tags=list(art.tags or ()),
                _projects={pid} if pid else set(),
            )
        )
    return rows


# ── the read ─────────────────────────────────────────────────────────────────


@dataclass
class Query:
    """The read's filter, sort and search, from its query string. Multi-valued keys take a comma
    list: ``kind=note,link``."""

    q: str = ""
    kind: frozenset[str] = frozenset()
    made: frozenset[str] = frozenset()
    project: str = ""
    store: str = ""
    collection: str = ""
    tag: frozenset[str] = frozenset()
    state: frozenset[str] = frozenset()
    sort: str = SORT_ACTIVITY
    entity: str = ""

    @classmethod
    def parse(cls, params: Any) -> Query:
        def many(key: str) -> frozenset[str]:
            return frozenset(v.strip() for v in (params.get(key) or "").split(",") if v.strip())

        sort = (params.get("sort") or SORT_ACTIVITY).strip()
        return cls(
            q=(params.get("q") or "").strip(),
            kind=many("kind"),
            made=many("made"),
            project=(params.get("project") or "").strip(),
            store=(params.get("store") or "").strip(),
            collection=(params.get("collection") or "").strip(),
            tag=many("tag"),
            state=many("state"),
            sort=sort if sort in _SORTS else SORT_ACTIVITY,
            entity=(params.get("entity") or "").strip(),
        )


#: The ``state`` values that are flags rather than reading states.
STATE_PINNED, STATE_ARCHIVED = "pinned", "archived"


def _states(row: dict) -> set[str]:
    out = {row["state"]} if row["state"] else set()
    if row["read"]:
        out.add(row["read"])
    if row["pinned"]:
        out.add(STATE_PINNED)
    if row["archived"]:
        out.add(STATE_ARCHIVED)
    return out


def _facet_values(row: dict, facet: str) -> Iterable[str]:
    if facet == "store":
        return (row["store"],)
    if facet == "kind":
        return (row["kind"],)
    if facet == "made":
        return (row["made"],)
    if facet == "project":
        return tuple(row["_projects"]) or (NO_PROJECT,)
    if facet == "collection":
        return tuple(c["id"] or c["name"] for c in row["collections"])
    if facet == "tag":
        return tuple(row["_tags"])
    if facet == "state":
        # An archived row is listed only under Archived, so it is counted only there.
        return (STATE_ARCHIVED,) if row["archived"] else tuple(_states(row))
    return ()


def _keeps(row: dict, query: Query, *, but: str = "", entity_refs: frozenset[str] | None) -> bool:
    """Whether *row* passes every filter of *query* except the facet *but* (a facet's own counts
    are taken over the rows every other filter leaves)."""
    if but != "store" and query.store and row["store"] != query.store:
        return False
    if but != "kind" and query.kind and row["kind"] not in query.kind:
        return False
    if but != "made" and query.made and row["made"] not in query.made:
        return False
    if but != "project" and query.project:
        if query.project == NO_PROJECT:
            if row["_projects"]:
                return False
        elif query.project not in row["_projects"]:
            return False
    if but != "collection" and query.collection:
        if query.collection not in {c["id"] or c["name"] for c in row["collections"]}:
            return False
    if but != "tag" and query.tag and not query.tag & set(row["_tags"]):
        return False
    if but != "state":
        # An archived thing is listed only when the filter asks for Archived, as the stores'
        # own lists hide it until asked.
        if row["archived"] and STATE_ARCHIVED not in query.state:
            return False
        if query.state and not query.state & _states(row):
            return False
    if entity_refs is not None and row["ref"] not in entity_refs:
        return False
    return True


def _sorted(rows: list[dict], sort: str) -> list[dict]:
    if sort == SORT_NAME:
        return sorted(rows, key=lambda r: (r["title"].casefold(), r["ref"]))
    key = "created_at" if sort == SORT_ADDED else "activity_at"
    return sorted(rows, key=lambda r: (r[key], r["ref"]), reverse=True)


def _entity_refs(store: Any, name: str) -> frozenset[str]:
    """The knowledge rows the entity *name* is mentioned in, as ``get_entity_items`` reads them."""
    ent = store.find_entity(name)
    if not ent:
        return frozenset()
    return frozenset(
        f"knowledge:{item_id}"
        for (item_id,) in store.db.execute(
            "SELECT DISTINCT m.item_id FROM mentions m WHERE m.entity_id = ?", (ent["id"],)
        )
    )


@dataclass
class Sources:
    """Where the read finds each store: callables, so a store that can't be opened fails alone."""

    knowledge: Callable[[], Any]
    artifacts: Callable[[], Any]
    intents: Callable[[], Iterable[Any]]


def read_library(sources: Sources, query: Query, *, runs_reader: Any = None) -> dict:
    """The library's rows for *query*, its counts, and the stores it could not read."""
    from personalclaw.identity import current_username
    from personalclaw.knowledge.session_brief import project_tag

    projects = project_items.read_projects()
    ctx = _Context(
        runs_reader=runs_reader,
        projects=projects,
        project_tags=frozenset(project_tag(pid) for pid in projects.names),
        running_reports=running_reports(),
    )
    try:
        ctx.reader = current_username()
    except Exception:
        logger.debug("library: reader unknown", exc_info=True)
    rows: list[dict] = []
    failed: list[str] = []
    store: Any = None
    collection_ids: dict[str, str] = {}
    entity_refs: frozenset[str] | None = None
    found: dict[str, Any] = {}
    reports: dict[str, str] = {}
    try:
        store = sources.knowledge()
        ctx.sources = {str(s["id"]): str(s.get("name") or "") for s in store.list_sources()}
        intents = list(sources.intents())
        ctx.intents = {i.id: str(i.goal or getattr(i, "name", "") or "") for i in intents}
        collection_ids = {
            str(c["name"]): str(c["id"])
            for c in store.db.execute("SELECT id, name FROM collections")
        }
        rows += knowledge_rows(store, ctx)
        reported = report_rows(store, ctx)
        rows += reported
        # A hit on a report's write-up is its report's row: by the write-up's id, since two
        # reports of one name keep one each.
        reports = {
            write_up: row["ref"].partition(":")[2]
            for row in reported
            if (write_up := row.pop("_write_up"))
        }
        rows += findings_rows(store, ctx, intents)
        found[STORE_KNOWLEDGE] = store
        if query.entity:
            entity_refs = _entity_refs(store, query.entity)
    except Exception:
        logger.warning("library: the knowledge store could not be read", exc_info=True)
        failed.append(STORE_KNOWLEDGE)
        rows = []
        if query.entity:
            entity_refs = frozenset()
    try:
        prov = sources.artifacts()
        if prov is None:
            raise RuntimeError("no artifact provider")
        rows += artifact_rows(prov, ctx, collection_ids)
        found[STORE_ARTIFACTS] = prov
    except Exception:
        logger.warning("library: the artifact store could not be read", exc_info=True)
        failed.append(STORE_ARTIFACTS)
    searched: dict[str, Any] = {}
    if query.q:
        order, hits, searched = search(found, query.q, reports)
        rank = {ref: i for i, ref in enumerate(order)}
        rows = [r for r in rows if r["ref"] in rank]
        for r in rows:
            hit = hits.get(r["ref"])
            r["match"] = {"passage": hit.passage, "meaning_only": hit.meaning_only} if hit else None
    kept = [r for r in rows if _keeps(r, query, entity_refs=entity_refs)]
    facets: dict[str, dict[str, int]] = {}
    for facet in FACETS:
        counts: Counter[str] = Counter()
        for r in rows:
            if _keeps(r, query, but=facet, entity_refs=entity_refs):
                counts.update(set(_facet_values(r, facet)))
        facets[facet] = dict(counts)
    if query.q:  # a search ranks, and ungroups
        ordered = sorted(kept, key=lambda r: rank[r["ref"]])
    else:
        ordered = _sorted(kept, query.sort)
    items = [{k: v for k, v in r.items() if not k.startswith("_")} for r in ordered[:MAX_ROWS]]
    return {
        "items": items,
        "total": len(kept),
        "complete": not failed and len(kept) <= MAX_ROWS,
        "failed": failed,
        "facets": facets,
        "sort": "rank" if query.q else query.sort,
        **searched,
    }


# ── search ───────────────────────────────────────────────────────────────────

#: How many hits each arm of a search may bring, as the answer states them.
SEARCH_CAP = {STORE_KNOWLEDGE: 300, STORE_ARTIFACTS: 100}
#: RRF's constant, the one the knowledge search fuses its own arms with.
RRF_K = 60


@dataclass
class _Hit:
    passage: str = ""
    meaning_only: bool = False


def _knowledge_arm(store: Any, q: str, reports: dict[str, str]) -> tuple[list[str], dict, int]:
    """The knowledge search's hits as row refs, best first: the hybrid search (keyword, graph and
    meaning, fused), a mirror's hit folded into its artifact and a write-up's into its report."""
    from personalclaw.dashboard.handlers import knowledge as kh
    from personalclaw.history import match_snippet
    from personalclaw.knowledge.retrieval import HybridRetriever

    embedder = kh._embedder()
    embed_fn = embedder.embed if embedder and embedder.is_available() else None
    results = HybridRetriever(store, embedder=embed_fn).search(q, limit=SEARCH_CAP[STORE_KNOWLEDGE])
    ids = [str(r["id"]) for r in results]
    shape: dict[str, Any] = {}
    for start in range(0, len(ids), 400):
        chunk = ids[start : start + 400]
        marks = ",".join("?" * len(chunk))
        for row in store.db.execute(
            "SELECT id, item_type, kind, guid, logical_key FROM items "  # noqa: S608
            f"WHERE id IN ({marks})",
            chunk,
        ):
            shape[str(row["id"])] = row
    needle = q.casefold()
    order: list[str] = []
    hits: dict[str, _Hit] = {}
    for r in results:
        row = shape.get(str(r["id"]))
        if row is None:
            continue
        if row["item_type"] == ARTIFACT_ITEM_TYPE:
            ref = f"artifact:{row['guid']}" if row["guid"] else ""
        elif row["kind"] == RESEARCH_FINDING_KIND:
            ref = f"report:{reports[row['id']]}" if row["id"] in reports else ""
        else:
            ref = f"knowledge:{row['id']}"
        if not ref or ref in hits:
            continue
        order.append(ref)
        hits[ref] = _Hit(
            passage=match_snippet([str(r.get("title") or ""), str(r.get("content") or "")], needle),
            meaning_only=str(r.get("match_type") or "") == "vector",
        )
    return order, hits, len(results)


def _findings_arm(store: Any, q: str) -> tuple[list[str], dict]:
    """Findings logs whose findings say *q*, the one with the newest such finding first."""
    from personalclaw.history import match_snippet

    needle = q.casefold()
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    order: list[str] = []
    hits: dict[str, _Hit] = {}
    for intent_id, takeaway in store.db.execute(
        "SELECT intent_id, takeaway FROM intent_outcomes WHERE takeaway LIKE ? ESCAPE '\\' "
        "ORDER BY created_at DESC, rowid DESC",
        (f"%{escaped}%",),
    ):
        ref = f"findings:{intent_id}"
        if ref not in hits:
            order.append(ref)
            hits[ref] = _Hit(passage=match_snippet([str(takeaway or "")], needle))
    return order, hits


def _artifact_arm(prov: Any, q: str) -> tuple[list[str], dict, int]:
    """The artifact store's own search, ranked: a name match first, then its description or tags,
    then its body, each newest first."""
    from personalclaw.history import match_snippet

    needle = q.casefold()
    found = list(prov.list(q=q))

    def where(art: Any) -> int:
        if needle in str(art.name or "").casefold():
            return 0
        said = " ".join([str(art.description or ""), *[str(t) for t in art.tags or ()]])
        return 1 if needle in said.casefold() else 2

    found.sort(key=lambda a: str(a.updated_at or ""), reverse=True)
    found.sort(key=where)
    order: list[str] = []
    hits: dict[str, _Hit] = {}
    for art in found[: SEARCH_CAP[STORE_ARTIFACTS]]:
        ref = f"artifact:{art.slug}"
        texts = [str(art.name or ""), str(art.description or ""), " ".join(art.tags or ())]
        passage = match_snippet(texts, needle)
        if not passage:
            try:
                body = prov.get(art.slug)
                passage = match_snippet([str(getattr(body, "content", "") or "")], needle)
            except Exception:
                logger.debug("library: the body of %s is unreadable", art.slug, exc_info=True)
        order.append(ref)
        hits[ref] = _Hit(passage=passage)
    return order, hits, len(found)


def search(
    sources_found: dict[str, Any], q: str, reports: dict[str, str]
) -> tuple[list[str], dict, dict]:
    """The search's hits as refs, best first, each with its passage and whether it matched by
    meaning only; and the caps. The arms are fused by their ranks (RRF), as the knowledge search
    fuses its own, since their scores are on scales that can't be compared."""
    from personalclaw.knowledge.retrieval import HybridRetriever

    lists: list[list[tuple[str, int]]] = []
    hits: dict[str, _Hit] = {}
    capped = False
    store, prov = sources_found.get(STORE_KNOWLEDGE), sources_found.get(STORE_ARTIFACTS)
    if store is not None:
        order, found, n = _knowledge_arm(store, q, reports)
        capped |= n >= SEARCH_CAP[STORE_KNOWLEDGE]
        lists.append([(ref, rank) for rank, ref in enumerate(order)])
        hits.update(found)
        order, found = _findings_arm(store, q)
        lists.append([(ref, rank) for rank, ref in enumerate(order)])
        for ref, hit in found.items():
            hits.setdefault(ref, hit)
    if prov is not None:
        order, found, n = _artifact_arm(prov, q)
        capped |= n > SEARCH_CAP[STORE_ARTIFACTS]
        lists.append([(ref, rank) for rank, ref in enumerate(order)])
        for ref, hit in found.items():
            if ref not in hits or not hits[ref].passage:
                hits[ref] = hit
            else:
                hits[ref].meaning_only = False
    fused = [ref for ref, _score in HybridRetriever._rrf_fuse(*lists, k=RRF_K)]
    return fused, hits, {"cap": dict(SEARCH_CAP), "capped": capped}


# ── the revision ─────────────────────────────────────────────────────────────


def _artifact_stat(prov: Any) -> tuple[int, int]:
    """``(count, newest st_mtime_ns)`` over every artifact's ``meta.json``, by a stat alone."""
    root = getattr(prov, "_root", None)
    count = newest = 0
    if root is None:
        return count, newest
    try:
        entries = list(os.scandir(root))
    except OSError:
        return count, newest
    for entry in entries:
        if not entry.is_dir():
            continue
        try:
            st = os.stat(os.path.join(entry.path, "meta.json"))
        except OSError:
            continue
        count += 1
        newest = max(newest, st.st_mtime_ns)
    return count, newest


def _mtime_ns(path: Any) -> int:
    try:
        return os.stat(path).st_mtime_ns
    except (OSError, TypeError):
        return 0


def running_reports() -> frozenset[str]:
    """The ids of the reports a run holds the claim of now (`_hold_claim`)."""
    from personalclaw.dashboard.handlers.research_reports import report_claim_id
    from personalclaw.knowledge.research_reports import load_reports
    from personalclaw.triggers import claims

    try:
        return frozenset(d.id for d in load_reports() if claims.is_running(report_claim_id(d.id)))
    except Exception:
        logger.debug("library: report claims unreadable", exc_info=True)
        return frozenset()


def revision(sources: Sources, *, intents_path: Any, reports_path: Any, reader: str = "") -> str:
    """The library's revision: it changes when anything a row is read from changes, and when the
    minute turns. Read without reading a row."""
    parts: list[Any] = [int(time.time() // 60), reader]
    try:
        parts.append(sources.knowledge().library_revision())
    except Exception:
        parts.append("knowledge?")
    try:
        prov = sources.artifacts()
        parts.append(_artifact_stat(prov) if prov is not None else "artifacts?")
    except Exception:
        parts.append("artifacts?")
    parts += [_mtime_ns(intents_path), _mtime_ns(reports_path), sorted(running_reports())]
    projects = project_items.read_projects()
    parts.append(sorted(projects.names.items()))
    parts.append(sorted(projects.loops.items()))
    return hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()[:32]


# ── the route ────────────────────────────────────────────────────────────────


def _sources(request: web.Request) -> Sources:
    from personalclaw.artifacts.registry import get_provider
    from personalclaw.dashboard.handlers import knowledge as kh

    return Sources(
        knowledge=lambda: kh._store(request),
        artifacts=get_provider,
        intents=lambda: kh._intent_store(request).load(),
    )


def _record_paths(request: web.Request) -> tuple[Any, Any]:
    from personalclaw.dashboard.handlers import knowledge as kh
    from personalclaw.knowledge import research_reports

    try:
        intents_path = kh._intent_store(request)._path
    except Exception:
        intents_path = None
    return intents_path, research_reports._store_path()


async def api_library(request: web.Request) -> web.Response:
    """GET /api/library — every thing in the library, one row per thing, in one read.

    With a true total, each filter value's count, and the stores it couldn't read.

    Keys: ``q``, ``kind``, ``made``, ``project`` (an id, or ``none``), ``store`` (``artifacts`` or
    ``knowledge``), ``collection``, ``tag``, ``state``, ``sort`` (``activity``, ``added`` or
    ``name``) and ``entity``. An unchanged library answers 304 to its ``ETag``.
    """
    sources = _sources(request)
    intents_path, reports_path = _record_paths(request)
    from personalclaw.subagent_reach import reader_of

    reader = reader_of(request, request.app.get("state"))
    who = "" if reader.everyone else str(reader.work or "?")
    rev = await asyncio.to_thread(
        revision, sources, intents_path=intents_path, reports_path=reports_path, reader=who
    )
    headers = {"ETag": f'"{rev}"', "Cache-Control": "no-cache"}
    if request.headers.get("If-None-Match") == headers["ETag"]:
        return web.Response(status=304, headers=headers)
    query = Query.parse(request.query)
    body = await asyncio.to_thread(read_library, sources, query, runs_reader=reader)
    return web.json_response(body, headers=headers)


def setup_library_routes(app: web.Application) -> None:
    """Mount the library's routes."""
    app.router.add_get("/api/library", api_library)
