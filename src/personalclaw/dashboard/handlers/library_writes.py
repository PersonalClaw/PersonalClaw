"""The library's writes and its own settings, beside its one read (`handlers.library`).

* ``POST /api/library/bulk`` applies one operation to many things across both stores and answers
  each: ``{ref, ok, error?}``, never stopping at the first failure. Its operations are the
  knowledge store's own (``collect``, ``uncollect``, ``read_state``, ``favorite``, ``archive``,
  ``restore``, ``pin``), plus ``project`` and ``delete``. Each goes through its store's own write:
  on an artifact, pin and archive are its library flags, a collection is its label, and Pin is
  what Favorite means; a report acts through its write-up and a findings log through its own
  state. A delete is taken only with ``expect``, the number of refs the confirm showed.
* ``GET`` and ``PUT /api/library/settings``: ``power_user_view``, in
  ``entity_settings/library.json``. A body that names none of its keys is refused.
* :class:`LibraryWatch` pushes a ``knowledge`` hint whenever what the library serves changes.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from aiohttp import web

from personalclaw import project_items
from personalclaw.cancellation import cancel_and_wait
from personalclaw.http_errors import json_error
from personalclaw.request_validation import bool_field, json_object_body

logger = logging.getLogger(__name__)

BULK_OPS = (
    "collect",
    "uncollect",
    "read_state",
    "favorite",
    "archive",
    "restore",
    "pin",
    "project",
    "delete",
)
#: The most refs one bulk call takes.
BULK_MAX = 500

NOT_FOUND, NOT_APPLICABLE, FAILED = "not_found", "not_applicable", "failed"

#: The library's settings and their defaults (`entity_settings/library.json`).
SETTINGS_ENTITY = "library"
SETTINGS_DEFAULTS: dict[str, Any] = {"power_user_view": False}

#: How often the watch looks at what the library serves.
WATCH_EVERY_SECS = 2.0


# ── bulk ─────────────────────────────────────────────────────────────────────


class _Bulk:
    """One bulk call's operation and its checked arguments."""

    def __init__(self, op: str, *, store: Any, prov: Any, args: dict) -> None:
        self.op = op
        self.store = store
        self.prov = prov
        self.args = args

    def one(self, ref: str) -> dict:
        kind, _, ident = ref.partition(":")
        try:
            error = {
                "knowledge": self._knowledge,
                "artifact": self._artifact,
                "report": self._report,
                "findings": self._findings,
            }.get(kind, lambda _ident: NOT_FOUND)(ident)
        except Exception:  # noqa: BLE001 — one thing's failure is its answer, and the call goes on
            logger.warning("library bulk %s on %s failed", self.op, ref, exc_info=True)
            error = FAILED
        return (
            {"ref": ref, "ok": True} if error is None else {"ref": ref, "ok": False, "error": error}
        )

    # Each answers None for done, or the error.

    def _knowledge(self, item_id: str) -> str | None:
        if not item_id or self.store is None:
            return NOT_FOUND
        if self.op == "delete":
            from personalclaw.dashboard.handlers.knowledge import delete_item_and_files

            return None if delete_item_and_files(self.store, item_id) else NOT_FOUND
        if self.op == "project":
            pid = self.args["project_id"]
            return None if self.store.move_item_project(item_id, pid) else NOT_FOUND
        kept = {k: v for k, v in self.args.items() if k in ("collection_id", "state", "value")}
        done = self.store.bulk_apply(self.op, [item_id], **kept)
        return NOT_FOUND if item_id in done["missing"] else None

    def _artifact(self, slug: str) -> str | None:
        if self.prov is None or not slug:
            return NOT_FOUND
        if self.op == "read_state":
            return NOT_APPLICABLE
        if self.op == "delete":
            return None if self.prov.delete(slug) else NOT_FOUND
        art = self.prov.get(slug)
        if art is None:
            return NOT_FOUND
        flags: dict[str, Any] = {}
        if self.op in ("pin", "favorite"):  # Pin merges the star
            flags["pinned"] = bool(self.args.get("value", True))
        elif self.op in ("archive", "restore"):
            flags["archived"] = self.op == "archive"
        elif self.op == "project":
            flags["project_id"] = self.args["project_id"]
        elif self.op == "collect":
            flags["collection"] = self.args["collection_name"]
        elif self.op == "uncollect":
            if (art.collection or "") != self.args["collection_name"]:
                return None
            flags["collection"] = ""
        return None if self.prov.set_library_flags(slug, **flags) is not None else FAILED

    def _report(self, report_id: str) -> str | None:
        from personalclaw.knowledge.research_reports import get_report

        defn = get_report(report_id)
        if defn is None:
            return NOT_FOUND
        if self.op in ("project", "delete"):
            return NOT_APPLICABLE
        # A report is kept as its write-up: what is done to the report is done to that item.
        if not defn.finding_item_id:
            return NOT_APPLICABLE
        return self._knowledge(defn.finding_item_id)

    def _findings(self, intent_id: str) -> str | None:
        from pathlib import Path

        from personalclaw.knowledge.intents import IntentStore

        intents = IntentStore(Path(self.store.db_path).parent / "intents.json")
        if intents.get(intent_id) is None:
            return NOT_FOUND
        if self.op in ("pin", "favorite"):
            self.store.set_intent_log_state(intent_id, pinned=bool(self.args.get("value", True)))
        elif self.op in ("archive", "restore"):
            self.store.set_intent_log_state(intent_id, archived=self.op == "archive")
        elif self.op == "read_state":
            self.store.set_intent_log_state(intent_id, read=self.args["state"] == "read")
        else:
            return NOT_APPLICABLE
        return None


async def api_library_bulk(request: web.Request) -> web.Response:
    """POST /api/library/bulk — one operation on many things, answered for each.

    ``{op, refs, ...}``: ``refs`` are library refs (``knowledge:``, ``artifact:``, ``report:``,
    ``findings:``). ``collect``/``uncollect`` name ``collection_id`` (a manual collection),
    ``read_state`` names ``state``, ``pin``/``favorite`` take ``value``, ``project`` names
    ``project_id`` ("" takes things out of their project), and ``delete`` names ``expect``, the
    count of refs. Answers ``{op, results: [{ref, ok, error?}]}`` in the order of ``refs``."""
    from personalclaw.artifacts.registry import get_provider
    from personalclaw.dashboard.handlers import knowledge as kh

    body = await json_object_body(request)
    op = str(body.get("op") or "")
    if op not in BULK_OPS:
        return json_error("unknown_op", message=f"op is one of {', '.join(BULK_OPS)}.", status=400)
    refs = body.get("refs")
    if not isinstance(refs, list) or not refs or not all(isinstance(r, str) for r in refs):
        return json_error("refs_required", message="refs is a list of library refs.", status=400)
    if len(refs) > BULK_MAX:
        return json_error("too_many_refs", message=f"At most {BULK_MAX} refs a call.", status=400)
    store = kh._store(request)
    args: dict[str, Any] = {}
    if op == "delete":
        expect = body.get("expect")
        if isinstance(expect, bool) or expect != len(refs):
            return json_error(
                "confirm_required",
                message="A delete names expect, the number of things the confirm showed.",
                status=400,
            )
    if op == "project":
        pid, why = project_items.requested_project(body.get("project_id"))
        if why:
            return json_error("unknown_project", message=why, status=404)
        args["project_id"] = pid
    if op in ("collect", "uncollect"):
        coll = store.get_collection(str(body.get("collection_id") or ""))
        if not coll:
            return json_error(
                "unknown_collection", message="No collection has that id.", status=404
            )
        if coll.get("kind") == "smart":
            return json_error(
                "smart_collection_immutable",
                message="A smart collection holds what its search finds.",
                status=400,
            )
        args["collection_id"] = str(coll["id"])
        args["collection_name"] = str(coll.get("name") or "")
    if op == "read_state":
        state = str(body.get("state") or "")
        if state not in store.VALID_READ_STATES:
            return json_error(
                "bad_state",
                message=f"state is one of {', '.join(store.VALID_READ_STATES)}.",
                status=400,
            )
        args["state"] = state
    if op in ("pin", "favorite"):
        # On when it is left out, as the knowledge bulk route reads it.
        args["value"] = bool_field(body, "value", default=True)
    bulk = _Bulk(op, store=store, prov=get_provider(), args=args)
    results = [bulk.one(ref) for ref in refs]
    await kh._sel_log_async(
        "library.bulk", op=op, refs=len(refs), failed=sum(not r["ok"] for r in results)
    )
    return web.json_response({"op": op, "results": results})


# ── settings ─────────────────────────────────────────────────────────────────


def _settings() -> dict[str, Any] | None:
    from personalclaw.providers.entity_routes import _load_entity_settings

    stored = _load_entity_settings(SETTINGS_ENTITY)
    if stored is None:
        return None
    return {
        k: stored.get(k, v) if isinstance(stored.get(k), type(v)) else v
        for k, v in SETTINGS_DEFAULTS.items()
    }


async def api_library_settings_get(request: web.Request) -> web.Response:
    """GET /api/library/settings — the library's own settings: ``power_user_view``."""
    found = _settings()
    if found is None:
        return json_error(
            "settings_unreadable", message="The library's settings file can't be read.", status=503
        )
    return web.json_response({"power_user_view": found["power_user_view"]})


async def api_library_settings_put(request: web.Request) -> web.Response:
    """PUT /api/library/settings — change the library's own settings.

    A body names one or more of its keys; one that names none of them is refused, and so is a
    value of the wrong type."""
    from personalclaw import record_files
    from personalclaw.providers.entity_routes import _save_entity_settings

    body = await json_object_body(request)
    if not any(k in body for k in SETTINGS_DEFAULTS):
        return json_error("nothing_to_update", message="Name power_user_view.", status=400)
    unknown = sorted(set(body) - set(SETTINGS_DEFAULTS))
    if unknown:
        return json_error(
            "unknown_setting",
            message=f"No library setting is called {', '.join(unknown)}.",
            status=400,
        )
    for key, default in SETTINGS_DEFAULTS.items():
        if key in body and not isinstance(body[key], type(default)):
            return json_error("invalid_setting", message=f"{key} is true or false.", status=400)
    current = _settings()
    if current is None:
        return json_error(
            "settings_unreadable", message="The library's settings file can't be read.", status=503
        )
    current.update({k: body[k] for k in SETTINGS_DEFAULTS if k in body})
    try:
        _save_entity_settings(SETTINGS_ENTITY, current)
    except record_files.Unreadable:
        return json_error(
            "settings_unreadable", message="The library's settings file can't be read.", status=503
        )
    return web.json_response({"power_user_view": current["power_user_view"]})


# ── the knowledge hint ───────────────────────────────────────────────────────


class LibraryWatch:
    """Push a ``knowledge`` hint when what the library serves has changed: the knowledge store's
    revision counter, which every writer moves (an item made, read through, changed or deleted,
    a finding kept, a call on a conflict), and the reports a run holds (a run starting or
    ending). The first look is only the baseline."""

    def __init__(self, knowledge: Callable[[], Any], push: Callable[[str], Any]) -> None:
        self._knowledge = knowledge
        self._push = push
        self._seen: tuple | None = None

    def sample(self) -> tuple:
        from personalclaw.dashboard.handlers.library import running_reports

        return (self._knowledge().library_revision(), tuple(sorted(running_reports())))

    def saw(self, now: tuple) -> bool:
        """Take *now* (a :meth:`sample`); push the hint when it differs from the last look."""
        moved = self._seen is not None and now != self._seen
        self._seen = now
        if moved:
            self._push("knowledge")
        return moved

    def check(self) -> bool:
        return self.saw(self.sample())


WATCH_TASK = web.AppKey("library_watch_task", asyncio.Task)


async def _watch(app: web.Application) -> None:
    state = app["state"]
    if not hasattr(state, "push_refresh"):
        return  # a gateway with no page to tell
    watch = LibraryWatch(lambda: state.knowledge_store, lambda kind: state.push_refresh(kind))
    while True:
        try:
            watch.saw(await asyncio.to_thread(watch.sample))
        except Exception:  # noqa: BLE001 — a look that fails is tried again at the next
            logger.debug("library: the watch could not look", exc_info=True)
        await asyncio.sleep(WATCH_EVERY_SECS)


async def _start_watch(app: web.Application) -> None:
    app[WATCH_TASK] = asyncio.create_task(_watch(app))


async def _stop_watch(app: web.Application) -> None:
    await cancel_and_wait([app.get(WATCH_TASK)], what="the library's watch")


def setup_library_write_routes(app: web.Application) -> None:
    """Mount the library's writes and settings, and its watch."""
    app.router.add_post("/api/library/bulk", api_library_bulk)
    app.router.add_get("/api/library/settings", api_library_settings_get)
    app.router.add_put("/api/library/settings", api_library_settings_put)
    app.on_startup.append(_start_watch)
    app.on_cleanup.append(_stop_watch)
