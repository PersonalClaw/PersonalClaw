"""Lessons CRUD API handlers.

Schedule CRUD moved to the unified Trigger surface (dashboard/handlers/triggers.py);
this module now owns only the ``/api/lessons*`` endpoints. Every lesson read/write
goes through the memory service onto memory.db ``lesson.*`` records — there is no
JSONL fallback (the legacy JSONL lesson store was retired).
``_get_memory`` always returns a store with an attached record layer (the gateway
attaches one; the API-only path attaches one in ``_get_memory``), so
``service_for(...).has_vector`` is always true here and lessons persist even with
no embedder configured.

A folder's chats keep lessons in that folder's memory too (``memory_locality``). Each route takes
``?partition=``: an id names the folder's memory it is about (Settings → Memory's pick), none the
global memory, and :data:`EVERY_MEMORY` every memory she has, each lesson named by the memory it is
in: the inventory ``memory_list`` reads and ``memory_forget`` removes from.

A lesson to save or remove that a turn someone other than you asked for wants is held for your own
word (``memory_holds``): nothing changes until you allow it, and the agent is told so.
"""

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from aiohttp import web

from personalclaw import memory_locality, memory_reads, memory_writes
from personalclaw.approval_answer import work_of_request
from personalclaw.dashboard import memory_holds
from personalclaw.dashboard.state import DashboardState
from personalclaw.http_errors import json_error
from personalclaw.security import MaskConflict, redact_values_for_display, stored_name
from personalclaw.vector_memory import SemanticRejectCode

from ._shared import _change_refused_for_the_app, _get_memory, _memory_refusal

if TYPE_CHECKING:
    from personalclaw.memory_record import MemoryScope
    from personalclaw.memory_service import MemoryService

logger = logging.getLogger(__name__)


def _sel():
    """Late-binding _sel() for test monkeypatch compatibility."""
    import personalclaw.dashboard.handlers as _pkg  # noqa: F811

    return _pkg.sel()


#: The ``?partition=`` that names every memory she has: the global memory, then each folder's.
EVERY_MEMORY = "*"


def _memories(
    request: web.Request, *, writes: bool = False
) -> list[tuple[memory_locality.Partition, Any]] | None:
    """The memories a lessons request is about, each with its store: the one ``?partition=``
    names (none: the global memory), or every one for :data:`EVERY_MEMORY`. None when an id names
    no memory. A folder's memory is opened with its record store only when *writes*, so listing
    every memory makes nothing in one that keeps no lessons."""
    state: DashboardState = request.app["state"]
    asked = request.query.get("partition", "")
    if asked == EVERY_MEMORY:
        parts = memory_locality.partitions()
    else:
        part = memory_locality.partition_named(asked)
        if part is None:
            return None
        parts = [part]
    return [
        (
            part,
            (
                _get_memory(state)
                if part.is_global
                else memory_locality.open_partition(part, writes=writes)
            ),
        )
        for part in parts
    ]


def _named(part: memory_locality.Partition) -> dict[str, Any]:
    """Which memory a lesson is in, as a row names it: its id, the folder it is the memory of
    (written from ``~``, "" for the global memory), and whether that folder is gone."""
    return {"partition": part.id, "folder": part.shown, "folder_gone": part.gone}


async def api_lessons_create(request: web.Request) -> web.Response:
    """POST /api/lessons — add a lesson to memory.db ``lesson.*``."""
    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    # An app's work saves a lesson only when the app was given your memory.
    refused = _change_refused_for_the_app(state, request, "memory_remember")
    if refused is not None:
        return refused
    sk = work_of_request(request)
    if not sk:
        _sel().log_api_access(
            caller="anonymous",
            operation="memory_remember",
            outcome="denied",
            source="dashboard",
            resources="missing_session_key",
        )
        return web.json_response({"error": "missing X-Session-Key"}, status=400)
    # The lesson is the request's work (`work_of_request`: an app's own, whatever session it names,
    # or the session your pages and PersonalClaw's own processes name), judged as the work it does
    # for the chat at the top (`memory_reads.reach_of`): a subagent's or a workflow step's is saved
    # where that chat keeps memory, and refused where anything on the way keeps nothing (an
    # Incognito or Temporary chat, or one whose mode nothing can say: a chat the gateway does not
    # hold that nothing records), whatever its own key is marked. What it writes is filed under that
    # chat (`memory_writes.filed_under`). `dashboard:ui` is your own Memory page.
    reach = memory_reads.reach_of(state, sk)
    if reach.restricted_mode:
        logger.warning("Blocked memory_remember from restricted session %s", sk)
        _sel().log_api_access(
            caller=sk,
            operation="memory_remember",
            outcome="denied",
            source="dashboard",
            resources="restricted_session_block",
            error=memory_writes.REFUSAL,
        )
        return web.json_response({"error": memory_writes.REFUSAL}, status=403)
    # A turn someone other than you asked for saves nothing on its own: the lesson is held for
    # your own word below, once it is one memory would take (`memory_holds`).
    asked_by_someone_else = bool(memory_writes.asker())
    if not asked_by_someone_else:
        _sel().log_api_access(
            caller=sk,
            operation="memory_remember",
            outcome="allowed",
            source="dashboard",
            resources=reach.keys[-1] if reach.keys else "dashboard_ui",
        )
    rule = body.get("rule", "").strip()
    if not rule:
        return web.json_response({"error": "rule is required"}, status=400)
    negative = body.get("negative") or None
    # Injection gate: a memory write is untrusted content that gets
    # re-injected into future prompts. Everything the lesson stores is scanned with the shared
    # scanner: the rule, and what not to do beside it, which the agent's tool sends too and which
    # a lesson written as yours never passes the memory service's own scan for. A dangerous
    # verdict (e.g. a bidi-override or an embedded instruction-override) is refused before it
    # ever lands in the store.
    try:
        from personalclaw.supply_chain import Verdict, default_scanner

        reports = [
            default_scanner.scan_text(text, surface="memory") for text in (rule, negative) if text
        ]
        flagged = [report for report in reports if report.verdict is Verdict.DANGEROUS]
        if flagged:
            rules = {finding.rule for report in flagged for finding in report.findings}
            cats = ", ".join(sorted(rules)) or "dangerous content"
            _sel().log_api_access(
                caller=work_of_request(request),
                operation="memory_remember",
                outcome="denied",
                source="dashboard",
                resources="injection_scan",
                error=f"scanner flagged memory write: {cats}",
            )
            return web.json_response(
                {"error": f"memory write refused: scanner flagged dangerous content ({cats})"},
                status=400,
            )
    except Exception:
        logger.debug("memory-write injection scan failed (allowing)", exc_info=True)
    category = body.get("category", "knowledge")
    # Write through the memory service onto memory.db ``lesson.*``. The store is
    # always present (see module docstring), so no JSONL fallback is needed; a
    # store with no embedder still persists the lesson (vector optional).
    from personalclaw.memory_service import resolve_lesson_scope, service_for

    # REACH axis. Resolved HERE and not trusted from the caller: `mcp_memory`'s
    # memory_remember advertises scope=global|workspace and validates the pair
    # itself, but this endpoint is reachable without it, and for a while it simply
    # dropped both fields — a caller that carefully asked for a workspace lesson got
    # a global one and was told "ok". An unresolvable pair is refused, never widened:
    # storing a different scope than the one asked for is the defect, wherever it
    # happens.
    try:
        scope, scope_ref = resolve_lesson_scope(body.get("scope"), body.get("workspace"))
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    memories = _memories(request, writes=True)
    if memories is None:
        return json_error("memory_partition_not_found", status=404)
    if len(memories) != 1:
        return json_error(
            "bad_request",
            message="A lesson is added to one memory: name it, or none for every chat's.",
            status=400,
        )
    svc = service_for(memories[0][1])

    async def _save() -> tuple[SemanticRejectCode, str] | None:
        # Off the event loop: the lesson is embedded, and may be judged against the lessons it
        # could contradict, each a round trip to a model.
        refusal = await asyncio.to_thread(
            _save_lesson, svc, rule, category, negative, scope=scope, scope_ref=scope_ref
        )
        if refusal is None:
            state.push_refresh("lessons")
            return None
        _sel().log_api_access(
            caller=sk,
            operation="memory_remember",
            outcome="rejected",
            source="dashboard",
            resources=f"{refusal[0].value}:lesson",
        )
        return refusal

    if asked_by_someone_else:

        async def _on_her_allow() -> None:
            refused = await _save()
            if refused is not None:
                logger.warning("A lesson the owner allowed was not saved: %s", refused[1])

        remember = memory_holds.Change(
            operation="memory_remember",
            tool="memory_remember",
            said=f"Remember: “{rule}”" + (f"\nNot: “{negative}”" if negative else ""),
            to_do="remember this",
            ask="whether to keep it, and it is saved",
            not_yet="Not saved yet",
            answers="Allow saves it as a lesson of yours, Deny keeps nothing.",
        )
        return memory_holds.held(request, remember, _on_her_allow)
    refusal = await _save()
    if refusal is not None:
        code, reason = refusal
        return json_error(
            "lesson_refused",
            message=f"Lesson not saved, and nothing in memory changed: {reason}.",
            status=409 if code is SemanticRejectCode.CONFLICT else 422,
            error_extra={"reason": code.value},
        )
    return web.json_response({"ok": True})


def _save_lesson(
    svc: "MemoryService",
    rule: str,
    category: str,
    negative: str | None,
    *,
    scope: "MemoryScope | None",
    scope_ref: str | None,
) -> tuple[SemanticRejectCode, str] | None:
    """Write the lesson. None when memory holds it now, stored or said in full by a lesson it
    already held; otherwise why memory refused it (``MemoryService.lesson_refusal``), nothing
    having changed."""
    if svc.write_lesson(rule, category, negative, scope=scope, scope_ref=scope_ref):
        return None
    return svc.lesson_refusal(rule, negative, scope=scope, scope_ref=scope_ref)


async def api_lessons_delete(request: web.Request) -> web.Response:
    """DELETE /api/lessons — remove lessons by substring."""
    state: DashboardState = request.app["state"]
    # Block lesson deletes from a Temporary chat's work only (the chat, and the subagents and steps
    # working for it). Incognito allows memory_forget (active user action). An app's work removes
    # one only when the app was given your memory.
    refused = _change_refused_for_the_app(state, request, "lessons.delete")
    if refused is not None:
        return refused
    sk = work_of_request(request)
    if memory_reads.reach_of(state, sk).temporary:
        _sel().log_api_access(
            caller=sk,
            operation="lessons.delete",
            outcome="denied",
            source="dashboard",
            resources=sk,
        )
        return web.json_response(
            {"error": "Memory writes are not allowed in this session mode."}, status=403
        )
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    rule_sub = body.get("rule", "").strip()
    if not rule_sub:
        return web.json_response({"error": "rule substring required"}, status=400)
    # Delete through the memory service onto memory.db ``lesson.*``, in the memory asked, or in
    # every memory she has (`memory_forget`), saying which ones it removed lessons from.
    from personalclaw.memory_service import service_for

    memories = _memories(request)
    if memories is None:
        return json_error("memory_partition_not_found", status=404)
    # The list shows a rule masked (`api_lessons`), and the page deletes by the rule it showed. A
    # masked rule names the stored one it masks; a rule with no marker matches as it always has.
    # Every memory's is named before any is removed, so a rule one memory cannot name for certain
    # removes nothing anywhere.
    named: list[tuple[Any, Any, str | None]] = []
    for part, memory in memories:
        svc = service_for(memory)
        try:
            named.append((part, svc, stored_name(rule_sub, _stored_rules(svc))))
        except MaskConflict as exc:
            return web.json_response({"error": str(exc)}, status=409)

    def _remove() -> list[dict[str, Any]]:
        removed = [_named(part) for part, svc, rule in named if rule and svc.delete_lesson(rule)]
        if removed:
            state.push_refresh("lessons")
        return removed

    # A turn someone other than you asked for removes nothing on its own: held for your own word.
    if memory_writes.asker():

        async def _on_her_allow() -> None:
            _remove()

        forget = memory_holds.Change(
            operation="lessons.delete",
            tool="memory_forget",
            said=f"Forget the lessons matching “{rule_sub}”",
            to_do="remove the lessons matching this",
            ask=f"whether to remove the lessons matching “{rule_sub}”, and they are removed",
            not_yet="Nothing removed yet",
            answers="Allow removes them, Deny keeps them.",
        )
        return memory_holds.held(request, forget, _on_her_allow)
    removed = _remove()
    return web.json_response({"ok": bool(removed), "removed": removed})


def _stored_rules(svc) -> list[str]:
    """Every stored lesson's rule text, what a masked rule shown by the list is matched against."""
    rules: list[str] = []
    for e in svc.get_lessons():
        try:
            rules.append(str(json.loads(e["value_json"])))
        except (json.JSONDecodeError, TypeError):
            continue
    return rules


async def api_lessons(request: web.Request) -> web.Response:
    state: DashboardState = request.app["state"]
    # A Temporary chat's work, and an app's without the memory permission, read no lessons; the
    # answer says why, so `memory_list` does not tell the agent there are none. Incognito reads.
    refusal = _memory_refusal(state, request)
    if refusal:
        sk = work_of_request(request)
        _sel().log_api_access(
            caller=sk,
            operation="lessons.list",
            outcome="denied",
            source="dashboard",
            resources=sk,
        )
        return web.json_response({"lessons": [], "withheld": refusal})
    # Read through the memory service onto memory.db ``lesson.*``, in each memory asked.
    from personalclaw.memory_service import resolve_lesson_scope, service_for

    memories = _memories(request)
    if memories is None:
        return json_error("memory_partition_not_found", status=404)
    # `?workspace=` makes this the VISIBILITY view (global + that workspace); absent, it
    # is the full inventory across every scope. The list path takes the workspace
    # explicitly because the dashboard has no ambient working directory to infer one
    # from — the caller is the only thing that knows. Each row carries its own scope so
    # a workspace lesson never reads as a global one in a management surface.
    ws_param = request.query.get("workspace", "").strip()
    ws_ref: str | None = None
    if ws_param:
        try:
            _scope, ws_ref = resolve_lesson_scope("workspace", ws_param)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
    data: list[dict[str, Any]] = []
    for part, memory in memories:
        svc = service_for(memory)
        rows = svc.lessons_visible_in(ws_ref) if ws_param else svc.get_lessons()
        data += [{**row, **_named(part)} for row in _lesson_rows(svc, rows)]
    return web.json_response({"lessons": data})


def _lesson_rows(svc: Any, rows: list[dict]) -> list[dict[str, Any]]:
    """Each lesson of *rows* as the lists show it, with how far it stands."""
    data = []
    # Every lesson, not a page of them: this is the list the Memory studio shows, opens a cited
    # lesson in and deletes from, so a lesson left out is one the owner cannot see or remove. (It
    # kept the LAST 50 of a newest-first read, so past 50 the lessons taught most recently were
    # the ones missing.)
    #
    # Confidence + standing per row. Read from the SAME derivation the
    # prompt filter uses, so "why is it still doing that" / "why did it stop doing
    # that" are answered with the number the gate actually compared rather than a
    # second estimate computed for the UI.
    standings = svc.lesson_standings(rows)
    for e in rows:
        try:
            rule = json.loads(e["value_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        verdict = standings.get(str(e.get("key") or ""))
        evidence = getattr(verdict, "evidence", None)
        data.append(
            {
                # Masked as the memory list masks the same `lesson.*` fact. A delete by the
                # masked rule finds the stored one (`api_lessons_delete`).
                "rule": redact_values_for_display(rule),
                "category": "knowledge",
                "ts": e.get("updated_at", ""),
                "scope": e.get("scope") or "global",
                "workspace": e.get("scope_ref") or "",
                "standing": getattr(getattr(verdict, "standing", None), "value", "injected"),
                "confidence": float(getattr(verdict, "confidence", 1.0)),
                "confidence_reason": str(getattr(verdict, "reason", "")),
                "observations": int(getattr(evidence, "observations", 0)),
                "contradictions": int(getattr(evidence, "contradictions", 0)),
                "reversals": int(getattr(evidence, "reversals", 0)),
            }
        )
    return data
