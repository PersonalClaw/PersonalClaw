"""Investigate Anywhere — one chat-with-context primitive for every entity row (plan 60).

Every entity row gets an **investigate** affordance that opens a chat pre-loaded
with that entity's full context. One shared primitive:

* a per-kind **resolver registry** — the owning module registers a pure-read
  function that composes an :class:`InvestigateContext` (typed envelope: kind, id,
  title, snapshot, back-link, suggested agent + task mode, opening prompt) from
  its own store. A client can't forge a snapshot — composition is server-side.
* ``POST /api/investigate`` creates a fresh chat session in ``ask`` mode (read-only
  investigation — propose-don't-write), stages the envelope on the session, and
  returns the session key.
* at the session's FIRST turn, ``chat_runner._inject_investigate_context`` prepends
  the envelope to the model-bound message — ``fence_untrusted`` wrapped, DATA not
  instructions — exactly like the knowledge/attachment injections. The user's
  visible message stays clean, and the user always fires the first turn.

Soul guardrails: resolvers are PURE READS (investigating never mutates the
entity); the envelope always passes ``fence_untrusted`` before reaching a prompt;
no surface grows its own bespoke "chat about this" wiring.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Snapshot cap: a huge entity must not blow the first turn's budget. Truncation
# is VISIBLE (the notice line) so the model knows it saw a partial snapshot.
_SNAPSHOT_CAP = 8_192
_TRUNCATE_NOTICE = "\n…[snapshot truncated — open the source surface for the full entity]"


@dataclass
class InvestigateContext:
    """The typed envelope one investigate action stages onto a chat session."""

    kind: str  # registry key (inbox_item | loop_finding | notification | …)
    id: str  # entity id within the owning store
    title: str  # human label for the chat header chip
    snapshot: str  # composed server-side; capped; fenced at INJECTION (never here)
    back_link: str  # hash route to the source surface (e.g. "#/loops/abc")
    suggested_agent: str = ""  # "" = session default
    suggested_task_mode: str = "ask"  # ask | plan | agent — default ask (read-only)
    opening_prompt: str = ""  # composer pre-fill (editable, never auto-sent)

    def to_dict(self) -> dict:
        return asdict(self)


# (entity_id, dashboard state) -> envelope | None. Registered by owning modules
# at import/boot; pure reads only (no store writes) — tested per kind.
#
# A resolver may be sync OR async. Several owning stores expose their by-id read
# only as a coroutine (tasks go through provider indirection; schedule-run detail
# is lock-guarded), and the alternative — reaching into their private ``_*_sync``
# back doors — would couple this registry to internals that exist for other
# reasons. ``resolve`` is async and awaits whatever a resolver returns, so both
# shapes register the same way and neither kind of store has to change.
Resolver = Callable[[str, object], Any]

_RESOLVERS: dict[str, Resolver] = {}


def register_investigate_resolver(kind: str, fn: Resolver) -> None:
    """Register the resolver for one entity kind. Last registration wins
    (idempotent across re-imports); the kind key is the registry's vocabulary."""
    _RESOLVERS[kind] = fn


def known_kinds() -> tuple[str, ...]:
    return tuple(sorted(_RESOLVERS))


async def resolve(kind: str, entity_id: str, state) -> InvestigateContext | None:
    """Dispatch to the kind's resolver; None = unknown entity. Raises KeyError on
    an unknown KIND (the route maps it to 400 vs the entity 404). Awaits async
    resolvers so a store whose by-id read is a coroutine needs no sync back door."""
    fn = _RESOLVERS[kind]
    ctx = fn(entity_id, state)
    if inspect.isawaitable(ctx):
        ctx = await ctx
    if ctx is None:
        return None
    if len(ctx.snapshot) > _SNAPSHOT_CAP:
        ctx.snapshot = ctx.snapshot[:_SNAPSHOT_CAP] + _TRUNCATE_NOTICE
    return ctx


# ── Core resolvers (the S1 reference pair) ───────────────────────────────────
# Registered here (not in the owning modules) so importing personalclaw.investigate
# is sufficient — the route handler imports this module, guaranteeing registration
# without adding import-order coupling to inbox_service/loop startup.


def _resolve_inbox_item(entity_id: str, state) -> InvestigateContext | None:
    """An inbox item: sender/channel/classification + the message body and thread
    context. The body is EXTERNAL text — it rides the snapshot raw here and is
    fenced once, at injection."""
    try:
        svc = getattr(state, "_inbox_svc", None)
        item = svc.inbox.items.get(entity_id) if svc is not None else None
    except Exception:  # noqa: BLE001
        item = None
    if item is None:
        return None
    lines = [
        f"Inbox item {item.id}",
        f"From: {item.sender_name or item.sender_id}",
        f"Channel: {item.channel_name or item.channel}",
        f"Classification: {item.classification} (confidence: {item.confidence})",
        f"Status: {item.status}",
    ]
    for turn in (item.thread_context or [])[-8:]:
        who = str(turn.get("sender_name") or turn.get("sender") or "someone")
        txt = str(turn.get("text") or "").strip()
        if txt:
            lines.append(f"[thread] {who}: {txt}")
    lines.append(f"Message: {item.message or ''}")
    if item.draft:
        lines.append(f"Drafted reply: {item.draft}")
    return InvestigateContext(
        kind="inbox_item",
        id=entity_id,
        title=f"Inbox: {item.sender_name or item.sender_id}",
        snapshot="\n".join(lines),
        back_link="#/inbox",
        opening_prompt="Help me understand this message — what does it need from me?",
    )


def _resolve_loop_finding(entity_id: str, state) -> InvestigateContext | None:
    """A loop finding, addressed ``<loop_id>:<cycle>`` (the FE's target form) or a
    bare loop id (→ the latest finding). Includes the loop's goal + the finding +
    that cycle's judge verdict when one exists."""
    from personalclaw.loop import store as loop_store

    loop_id, _, cycle_s = entity_id.partition(":")
    loop = loop_store.get(loop_id)
    if loop is None:
        return None
    findings = loop_store.get_findings(loop_id)
    if not findings:
        return None
    finding = None
    if cycle_s:
        try:
            want = int(cycle_s)
            finding = next((f for f in findings if int(f.get("cycle", -1)) == want), None)
        except ValueError:
            finding = None
    if finding is None:
        finding = findings[-1]
    cycle = finding.get("cycle", "?")
    lines = [
        f"Loop: {loop.name or loop.id} (kind: {loop.kind}, status: {loop.status})",
        f"Task: {loop.task}",
        f"Finding (cycle {cycle}):",
    ]
    for key in ("summary", "key_insight", "evidence"):
        val = str(finding.get(key) or "").strip()
        if val:
            lines.append(f"  {key}: {val}")
    try:
        verdicts = loop_store.get_verdicts(loop_id)
        v = next((v for v in verdicts if v.get("cycle") == finding.get("cycle")), None)
        if v:
            lines.append(
                f"Judge verdict (cycle {cycle}): done={v.get('done')} "
                f"quality={v.get('quality_score')} marginal={v.get('marginal_value')} "
                f"reasoning: {v.get('done_reason') or v.get('reasoning') or ''}"
            )
    except Exception:  # noqa: BLE001 — the verdict is enrichment, not a requirement
        pass
    return InvestigateContext(
        kind="loop_finding",
        id=entity_id,
        title=f"Finding · {loop.name or loop.id}",
        snapshot="\n".join(lines),
        back_link=f"#/loops/{loop_id}",
        opening_prompt=(
            "Walk me through this finding — what did the loop discover and does it hold up?"
        ),
    )


def _cadence(job) -> str:
    """A job's cadence in words. ``ScheduleDefinition`` is a plain dataclass (no
    renderer), so read its three shapes directly."""
    sched = getattr(job, "schedule", None)
    kind = str(getattr(sched, "kind", "") or "?")
    every = getattr(sched, "every_secs", None)
    cron = getattr(sched, "cron_expr", None)
    at_ts = getattr(sched, "at_ts", None)
    if kind == "every" and every:
        return f"every {every}s"
    if kind == "cron" and cron:
        return f"cron {cron}"
    if kind == "at" and at_ts:
        return f"once at {at_ts}"
    return kind


# ── The attention path: notifications ────────────────────────────────────────


def _resolve_notification(entity_id: str, state) -> InvestigateContext | None:
    """A notification, addressed by its ``ts`` (the id the whole system uses —
    notifications are plain dicts in ``state._notification_log``, no store class).

    Failure notifications are the richest case and the reason this kind matters:
    a cron/loop/subagent failure carries the LINK to what failed (``job_id`` /
    ``loop_id`` / ``session``), so the snapshot resolves that too — the user asks
    "why did this fail?" and the model already has the run state."""
    log = list(getattr(state, "_notification_log", None) or [])
    note = next((n for n in log if str(n.get("ts")) == entity_id), None)
    if note is None:
        return None
    kind = str(note.get("kind") or "info")
    lines = [
        f"Notification ({kind}) at {note.get('ts')}",
        f"Title: {note.get('title') or ''}",
        f"Read: {'yes' if note.get('acked') else 'no'}",
        f"Body: {note.get('body') or ''}",
    ]

    # Follow the failure link. Each of these is best-effort enrichment: a missing
    # or deleted target still yields a useful notification-only snapshot.
    job_id = str(note.get("job_id") or "")
    if job_id:
        lines.append(f"\nLinked schedule job: {job_id}")
        try:
            svc = getattr(state, "crons", None)
            job = next(
                (
                    j
                    for j in (svc.list_jobs(include_disabled=True) if svc else [])
                    if j.id == job_id
                ),
                None,
            )
            if job is not None:
                lines.append(f"  Name: {job.name}")
                lines.append(f"  Cadence: {_cadence(job)}")
                lines.append(f"  Enabled: {job.enabled}")
                lines.append(f"  Last status: {job.last_status or '?'}")
                if job.last_error:
                    lines.append(f"  Last error: {job.last_error}")
                lines.append(f"  Consecutive failures: {job.consecutive_failures}")
        except Exception:  # noqa: BLE001 — enrichment only
            logger.debug("notification job enrichment failed", exc_info=True)

    loop_id = str(note.get("loop_id") or "")
    if loop_id:
        lines.append(f"\nLinked autonomous run: {loop_id} (kind: {note.get('loop_kind') or '?'})")
        try:
            from personalclaw.loop import store as loop_store

            loop = loop_store.get(loop_id)
            if loop is not None:
                lines.append(f"  Task: {loop.task}")
                lines.append(f"  Status: {loop.status} (cycle {getattr(loop, 'cycle', '?')})")
                findings = loop_store.get_findings(loop_id)
                if findings:
                    last = findings[-1]
                    summary = str(last.get("summary") or "").strip()
                    if summary:
                        lines.append(f"  Latest finding (cycle {last.get('cycle')}): {summary}")
        except Exception:  # noqa: BLE001
            logger.debug("notification loop enrichment failed", exc_info=True)

    session = str(note.get("session") or note.get("session_key") or "")
    if session:
        lines.append(f"\nLinked session: {session}")

    back = "#/notifications"
    if loop_id:
        back = f"#/loops/{loop_id}"
    elif job_id:
        back = f"#/triggers?open=schedule:{job_id}"
    failed = kind in ("error", "warning") or "fail" in str(note.get("title") or "").lower()
    return InvestigateContext(
        kind="notification",
        id=entity_id,
        title=f"Notification: {note.get('title') or kind}",
        snapshot="\n".join(lines),
        back_link=back,
        opening_prompt=(
            "Why did this fail, and what should I do about it?"
            if failed
            else "What is this notification telling me, and does it need anything from me?"
        ),
    )


# ── Work: tasks ──────────────────────────────────────────────────────────────


async def _resolve_task(entity_id: str, state) -> InvestigateContext | None:
    """A task with the context that makes it answerable: its criteria/plan/notes,
    where it sits in the Project → list hierarchy, and WHY it's blocked (the
    dependency reason, resolved against its siblings)."""
    from personalclaw.tasks.registry import get_task

    task = await get_task(entity_id)
    if task is None:
        return None
    lines = [
        f"Task {task.id}: {task.title}",
        f"Status: {getattr(task.status, 'value', task.status)}",
        f"Priority: {getattr(task.priority, 'value', task.priority)}",
    ]
    if task.project:
        lines.append(f"Project: {task.project}")
    if task.assignee:
        lines.append(f"Assignee: {task.assignee}")
    if task.due:
        lines.append(f"Due: {task.due}")
    if task.labels:
        lines.append(f"Labels: {', '.join(task.labels)}")
    if task.description:
        lines.append(f"\nDescription: {task.description}")
    for criterion in task.exit_criteria or []:
        lines.append(
            f"[exit criterion] {criterion.get('description', '')} "
            f"— {'met' if criterion.get('met') else criterion.get('status', 'open')}"
        )
    for step in task.action_plan or []:
        mark = "x" if step.get("completed") else " "
        lines.append(f"[plan {step.get('sequence', '?')}] [{mark}] {step.get('content', '')}")
    for bucket, label in (
        (task.notes, "note"),
        (task.research_notes, "research"),
        (task.execution_notes, "execution"),
    ):
        for entry in (bucket or [])[-5:]:
            content = str(entry.get("content") or "").strip()
            if content:
                lines.append(f"[{label}] {content}")

    # Why it's blocked — the derived reason, not just the raw dependency ids.
    try:
        from personalclaw.tasks.native import NativeTaskProvider
        from personalclaw.tasks.reconcile import block_reason

        reason = block_reason(task, NativeTaskProvider()._task_map())
        if reason.get("is_blocked"):
            lines.append(f"\nBlocked: {reason.get('message') or ''}")
            for title in reason.get("blocking_task_titles") or []:
                lines.append(f"  waiting on: {title}")
    except Exception:  # noqa: BLE001 — enrichment only
        logger.debug("task block-reason enrichment failed", exc_info=True)

    return InvestigateContext(
        kind="task",
        id=entity_id,
        title=f"Task: {task.title}",
        snapshot="\n".join(lines),
        back_link="#/tasks",
        opening_prompt="Help me work out how to move this task forward.",
    )


# ── Runs: schedule + trigger ─────────────────────────────────────────────────


async def _resolve_schedule_run(entity_id: str, state) -> InvestigateContext | None:
    """One schedule run, addressed ``<job_id>:<run_id>`` (a run is only readable
    through its job's history file, so the id must carry both — same composite
    shape as ``loop_finding``). A bare job id resolves its most recent run."""
    job_id, _, run_id = entity_id.partition(":")
    svc = getattr(state, "crons", None)
    if svc is None or not job_id:
        return None
    try:
        if run_id:
            run = await svc.get_run(job_id, run_id)
        else:
            rows, _total = await svc.list_runs(job_id, offset=0, limit=1)
            run = rows[0] if rows else None
    except Exception:  # noqa: BLE001 — a bad/unsafe job id is an entity miss
        logger.debug("schedule-run read failed for %s", entity_id, exc_info=True)
        return None
    if not run:
        return None
    job = next((j for j in svc.list_jobs(include_disabled=True) if j.id == job_id), None)
    lines = [
        f"Schedule run {run.get('run_id') or '?'} of job {job_id}",
        f"Job: {job.name if job else '(deleted)'}",
        f"Trigger: {run.get('trigger') or '?'}",
        f"Status: {run.get('status') or '?'}",
        f"Duration: {run.get('duration_ms', 0)} ms",
    ]
    if job is not None:
        lines.append(f"Cadence: {_cadence(job)}")
        lines.append(f"Action: {job.provider or '?'} ({job.exec_mode or '?'})")
        if job.message:
            lines.append(f"Prompt/message: {job.message}")
    if run.get("summary"):
        lines.append(f"\nSummary: {run['summary']}")
    if run.get("error"):
        lines.append(f"Error: {run['error']}")
    if run.get("trace"):
        lines.append(f"\nTrace:\n{run['trace']}")
    failed = str(run.get("status") or "") in ("failure", "timeout")
    return InvestigateContext(
        kind="schedule_run",
        id=entity_id,
        title=f"Run · {job.name if job else job_id}",
        snapshot="\n".join(lines),
        back_link=f"#/triggers?open=schedule:{job_id}",
        opening_prompt=(
            "Why did this run fail, and how do I stop it happening again?"
            if failed
            else "Walk me through what this run did."
        ),
    )


def _resolve_trigger_run(entity_id: str, state) -> InvestigateContext | None:
    """A trigger's run history, addressed ``<kind>:<id>`` (``lifecycle:`` /
    ``event:``; a bare or ``schedule:`` id is delegated to ``schedule_run``).

    HONEST SCOPE: only SCHEDULE triggers keep per-run rows. Lifecycle hooks and
    event triggers persist aggregate counters only (last run + status + count) —
    the API's own history endpoint returns empty for them — so for those kinds
    this resolves the trigger's LAST-RUN SUMMARY rather than inventing a per-run
    entity that doesn't exist."""
    kind, _, raw = entity_id.partition(":")
    if not raw:  # bare id → the schedule convention
        kind, raw = "schedule", entity_id
    if kind == "schedule":
        return None  # use kind="schedule_run" (composite job:run addressing)
    if kind == "lifecycle":
        try:
            from personalclaw.hooks import get_global_hook_store

            store = get_global_hook_store() or getattr(state, "_hook_store", None)
            hook = store.get(raw) if store is not None else None
        except Exception:  # noqa: BLE001
            hook = None
        if hook is None:
            return None
        import datetime as _dt

        last = (
            _dt.datetime.fromtimestamp(hook.last_run, _dt.timezone.utc).isoformat()
            if hook.last_run
            else "never"
        )
        lines = [
            f"Lifecycle trigger {hook.id}: {hook.name}",
            f"Event: {hook.event}",
            f"Matcher: {hook.matcher or '(any)'}",
            f"Action: {hook.provider}",
            f"Enabled: {hook.enabled}",
            f"Runs: {hook.run_count}",
            f"Last run: {last}",
            f"Last status: {hook.last_status or '?'}",
            "",
            "NOTE: lifecycle triggers record aggregate counters only — there is no "
            "per-run history for this trigger kind, so the above is the latest state, "
            "not one run's transcript.",
        ]
        return InvestigateContext(
            kind="trigger_run",
            id=entity_id,
            title=f"Trigger: {hook.name}",
            snapshot="\n".join(lines),
            back_link=f"#/triggers?open=lifecycle:{raw}",
            opening_prompt=(
                "Why is this trigger behaving this way?"
                if hook.last_status in ("error", "timeout", "blocked")
                else "Explain what this trigger does and when it fires."
            ),
        )
    if kind == "event":
        try:
            from personalclaw.config.loader import config_dir
            from personalclaw.event_triggers import EventTriggerStore

            store = EventTriggerStore(config_dir() / "event_triggers.json")
            trig = next((t for t in store.load() if t.id == raw), None)
        except Exception:  # noqa: BLE001
            trig = None
        if trig is None:
            return None
        lines = [
            f"Event trigger {trig.id}",
            f"Pattern: {trig.pattern}",
            f"Key glob: {trig.key_glob or '(none)'}",
            f"Content regex: {trig.content_re or '(none)'}",
            f"Action: {trig.action_provider}",
            f"Enabled: {trig.enabled}",
            f"Fires: {trig.fire_count}" + (f" of max {trig.max_fires}" if trig.max_fires else ""),
            f"Last fired at: {trig.last_fired_at or 'never'}",
            "",
            "NOTE: event triggers record aggregate counters only — individual fires "
            "are not persisted.",
        ]
        return InvestigateContext(
            kind="trigger_run",
            id=entity_id,
            title="Event trigger",
            snapshot="\n".join(lines),
            back_link=f"#/triggers?open=event:{raw}",
            opening_prompt="Explain what this trigger watches for and whether it's working.",
        )
    return None


# ── Autonomous runs: one cycle ───────────────────────────────────────────────


def _resolve_loop_cycle(entity_id: str, state) -> InvestigateContext | None:
    """One CYCLE of an autonomous run, addressed ``<loop_id>:<cycle>``. Where
    ``loop_finding`` answers "does this conclusion hold up?", this answers "what
    happened on this iteration?" — the cycle's finding, verdict, and any nudge
    the user injected, in run context."""
    from personalclaw.loop import store as loop_store

    loop_id, _, cycle_s = entity_id.partition(":")
    loop = loop_store.get(loop_id)
    if loop is None:
        return None
    try:
        want = int(cycle_s) if cycle_s else None
    except ValueError:
        want = None
    findings = loop_store.get_findings(loop_id)
    if want is None:
        want = int(findings[-1].get("cycle", 0)) if findings else 0
    lines = [
        f"Autonomous run: {loop.name or loop.id} (kind: {loop.kind}, status: {loop.status})",
        f"Task: {loop.task}",
        f"Cycle {want} of {getattr(loop, 'total_cycles', '?')}",
    ]
    finding = next((f for f in findings if int(f.get("cycle", -1)) == want), None)
    if finding:
        for key in ("summary", "key_insight", "evidence", "next_step"):
            val = str(finding.get(key) or "").strip()
            if val:
                lines.append(f"  {key}: {val}")
    else:
        lines.append("  (no finding recorded for this cycle)")
    try:
        verdict = next(
            (v for v in loop_store.get_verdicts(loop_id) if v.get("cycle") == want), None
        )
        if verdict:
            lines.append(
                f"Judge verdict: done={verdict.get('done')} "
                f"quality={verdict.get('quality_score')} "
                f"marginal={verdict.get('marginal_value')}"
            )
            reason = verdict.get("done_reason") or verdict.get("reasoning") or ""
            if reason:
                lines.append(f"  reasoning: {reason}")
    except Exception:  # noqa: BLE001
        logger.debug("loop-cycle verdict enrichment failed", exc_info=True)
    try:
        for nudge in loop_store.get_nudges(loop_id):
            if int(nudge.get("sent_at_cycle", -1)) == want:
                lines.append(f"[user nudge] {nudge.get('text') or ''}")
    except Exception:  # noqa: BLE001
        logger.debug("loop-cycle nudge enrichment failed", exc_info=True)
    return InvestigateContext(
        kind="loop_cycle",
        id=entity_id,
        title=f"Cycle {want} · {loop.name or loop.id}",
        snapshot="\n".join(lines),
        back_link=f"#/loops/{loop_id}",
        opening_prompt="What happened on this cycle, and did it move the run forward?",
    )


# ── Knowledge ────────────────────────────────────────────────────────────────


def _resolve_knowledge_item(entity_id: str, state) -> InvestigateContext | None:
    """A knowledge item: its content plus what the system derived from it. The
    body is user/scraped text — it rides the snapshot raw and is fenced once, at
    injection."""
    try:
        store = getattr(state, "knowledge_store", None)
        item = store.get_item(entity_id) if store is not None else None
    except Exception:  # noqa: BLE001
        logger.debug("knowledge read failed for %s", entity_id, exc_info=True)
        item = None
    if not item:
        return None
    lines = [
        f"Knowledge item {item.get('id')}: {item.get('title') or '(untitled)'}",
        f"Type: {item.get('type') or item.get('item_type') or '?'}",
        f"Status: {item.get('status') or '?'}",
    ]
    if item.get("tags"):
        lines.append(f"Tags: {', '.join(str(t) for t in item['tags'])}")
    if item.get("url"):
        lines.append(f"Source URL: {item['url']}")
    if item.get("file_path"):
        lines.append(f"File: {item['file_path']}")
    lines.append(f"Indexed for search: {'yes' if item.get('has_embedding') else 'no'}")
    if item.get("processing_error"):
        lines.append(f"Processing error: {item['processing_error']}")
    if item.get("summary"):
        lines.append(f"\nSummary: {item['summary']}")
    insights = item.get("insights")
    if isinstance(insights, dict) and insights:
        for key, val in list(insights.items())[:8]:
            lines.append(f"[insight] {key}: {val}")
    lines.append(f"\nContent:\n{item.get('content') or ''}")
    return InvestigateContext(
        kind="knowledge_item",
        id=entity_id,
        title=f"Knowledge: {item.get('title') or entity_id}",
        snapshot="\n".join(lines),
        # The item route is #/knowledge/item/<id> (KnowledgeSection matches on the
        # "item" segment) — #/knowledge/<id> falls through to the list.
        back_link=f"#/knowledge/item/{entity_id}",
        opening_prompt="Help me think about what's in this — what matters here?",
    )


# ── Memory: records and lessons ──────────────────────────────────────────────


def _memory_service(state):
    """The MemoryService the dashboard itself uses, without the provider-admin
    side effects (no embed-fn auto-wiring, no standalone-store construction) — a
    resolver is a pure read."""
    from personalclaw.memory_service import MemoryService

    mem = getattr(getattr(state, "context_builder", None), "memory", None)
    store = getattr(mem, "vector_store", None)
    return MemoryService.over_vector_store(store) if store is not None else None


def _resolve_memory_record(entity_id: str, state) -> InvestigateContext | None:
    """One memory record (semantic key or episodic uuid — one id lookup covers
    both tables)."""
    svc = _memory_service(state)
    if svc is None:
        return None
    try:
        rec = svc.get_record(entity_id)
    except Exception:  # noqa: BLE001
        logger.debug("memory record read failed for %s", entity_id, exc_info=True)
        return None
    if rec is None:
        return None
    lines = [
        f"Memory record {rec.id}",
        f"Kind: {getattr(rec.kind, 'value', rec.kind)}",
        f"Tier: {getattr(rec.tier, 'value', rec.tier)} · scope: "
        f"{getattr(rec.scope, 'value', rec.scope)}",
        f"Confidence: {rec.confidence} · importance: {rec.importance}",
        f"Recalled: {rec.recall_count}× · created {rec.created_at} · updated {rec.updated_at}",
    ]
    if rec.source:
        lines.append(f"Recorded by: {rec.source}")
    if rec.category:
        lines.append(f"Category: {rec.category}")
    if rec.tags:
        lines.append(f"Tags: {', '.join(str(t) for t in rec.tags)}")
    if rec.superseded_by:
        lines.append(f"Superseded by: {rec.superseded_by}")
    lines.append(f"\nContent: {rec.text or rec.value or ''}")
    return InvestigateContext(
        kind="memory_record",
        id=entity_id,
        title=f"Memory: {(rec.text or rec.value or entity_id)[:48]}",
        snapshot="\n".join(lines),
        back_link="#/settings/memory",
        opening_prompt="Is this memory still accurate and worth keeping?",
    )


def _resolve_memory_lesson(entity_id: str, state) -> InvestigateContext | None:
    """A learned lesson, with the provenance that makes "why do you believe this?"
    answerable: how it was learned, how confident, how often it's been recalled,
    and its SUPERSESSION CHAIN (lessons are replaced, not deleted — the chain is
    the real history of how this belief changed).

    Addressable EITHER by the ``lesson.<hash>`` key or by the rule TEXT. The text
    form matters: ``GET /api/lessons`` returns only ``{rule, category, ts}``, so the
    frontend never holds the key — and re-deriving the hash client-side would
    duplicate a server-side identity rule in the browser. Resolving the text here
    keeps that rule in one place.

    Honest about the limits: a lesson carries no session/episode back-link, so the
    snapshot says what provenance exists rather than implying a full audit trail."""
    svc = _memory_service(state)
    if svc is None:
        return None
    try:
        rec = svc.get_record(entity_id)
        if rec is None:
            # Not a key — treat the id as the rule text and match it against the
            # lesson set (exact first, then prefix, since list rows may be clipped).
            lessons = svc.get_lessons() or []
            needle = entity_id.strip()

            def _rule_of(row: dict) -> str:
                raw = row.get("value_json") or ""
                try:
                    import json as _json

                    return str(_json.loads(raw))
                except Exception:  # noqa: BLE001 — legacy rows stored the bare string
                    return str(raw)

            hit = next((r for r in lessons if _rule_of(r) == needle), None)
            if hit is None:
                hit = next((r for r in lessons if _rule_of(r).startswith(needle)), None)
            if hit is not None:
                rec = svc.get_record(str(hit.get("key") or ""))
    except Exception:  # noqa: BLE001
        logger.debug("lesson read failed for %s", entity_id, exc_info=True)
        return None
    if rec is None:
        return None
    rule = rec.text or rec.value or ""
    lines = [
        f"Learned lesson {rec.id}",
        f"Rule: {rule}",
        "",
        "Provenance:",
        f"  How it was learned: {rec.source or 'unknown'}",
        f"  Confidence: {rec.confidence}",
        f"  Times recalled: {rec.recall_count}",
        f"  First recorded: {rec.created_at} · last updated: {rec.updated_at}",
    ]
    if rec.category:
        lines.append(f"  Category: {rec.category}")
    try:
        store = getattr(
            getattr(getattr(state, "context_builder", None), "memory", None), "vector_store", None
        )
        chain = store.get_supersession_chain(rec.id) if store is not None else []
        if len(chain) > 1:
            lines.append("\nHow this belief changed (supersession chain, oldest first):")
            for link in chain:
                val = str(link.get("value_json") or "")
                lines.append(f"  {link.get('key')}: {val[:200]}")
    except Exception:  # noqa: BLE001 — enrichment only
        logger.debug("lesson supersession read failed", exc_info=True)
    lines.append(
        "\nNOTE: lessons record how they were learned (the source above) but do not "
        "link back to the specific conversation that produced them."
    )
    return InvestigateContext(
        kind="memory_lesson",
        id=entity_id,
        title=f"Lesson: {rule[:48]}",
        snapshot="\n".join(lines),
        back_link="#/settings/memory",
        opening_prompt="Why do you believe this, and does it still hold?",
    )


# ── Health: doctor findings + crash reports ──────────────────────────────────


async def _resolve_doctor_finding(entity_id: str, state) -> InvestigateContext | None:
    """A Doctor finding, addressed by ``<capability>`` or ``<capability>:<probe_id>``.

    Findings are NOT persisted — they're recomputed by running the capability's
    probes, so this re-runs them (read-only probes) and picks the row. Includes the
    remediation preview when the finding offers a fix, so the chat can discuss the
    fix without applying it (propose-don't-write)."""
    from personalclaw.resilience.doctor import DoctorContext, run_capability

    capability, _, probe_id = entity_id.partition(":")
    if not capability:
        return None
    try:
        report = await run_capability(
            capability, DoctorContext(state=state, port=int(getattr(state, "port", 0) or 0))
        )
    except Exception:  # noqa: BLE001 — an unknown capability is an entity miss
        logger.debug("doctor probe run failed for %s", entity_id, exc_info=True)
        return None
    # run_capability returns {capability, ok, probes: [...]} (and `unknown` for a
    # capability with no registered probes) — not a bare row list.
    if not isinstance(report, dict) or report.get("unknown"):
        return None
    rows: list[dict] = [r for r in (report.get("probes") or []) if isinstance(r, dict)]
    if not rows:
        return None
    row = next((r for r in rows if r.get("id") == probe_id), None) if probe_id else None
    # No probe named → the whole capability's findings (failures first).
    picked: list[dict] = [row] if row else sorted(rows, key=lambda r: bool(r.get("ok")))
    lines = [f"Doctor: {capability}"]
    for r in picked:
        lines.append(
            f"\n[{'OK' if r.get('ok') else 'PROBLEM'}] {r.get('title') or r.get('id')} "
            f"(probe {r.get('id')}, tier {r.get('tier')})"
        )
        if r.get("detail"):
            lines.append(f"  {r['detail']}")
        evidence = r.get("evidence")
        if isinstance(evidence, dict) and evidence:
            for key, val in list(evidence.items())[:10]:
                lines.append(f"  evidence · {key}: {str(val)[:400]}")
        fix_id = r.get("fix_id")
        if fix_id:
            lines.append(f"  offered fix: {fix_id}")
            try:
                from personalclaw.resilience.fixes import get_fix

                fix = get_fix(fix_id)
                if fix is not None:
                    lines.append(f"    {fix.title} — impact: {fix.impact}")
                    # dry_preview is read-only by contract; apply() is never called.
                    lines.append(f"    preview: {fix.dry_preview()}")
            except Exception:  # noqa: BLE001
                logger.debug("fix preview failed for %s", fix_id, exc_info=True)
    healthy = all(r.get("ok") for r in picked)
    return InvestigateContext(
        kind="doctor_finding",
        id=entity_id,
        title=f"Doctor: {capability}",
        snapshot="\n".join(lines),
        back_link="#/settings/doctor",
        opening_prompt=(
            "Everything here looks healthy — what does this check actually verify?"
            if healthy
            else "What's wrong here, and what should I do about it?"
        ),
    )


def _resolve_crash_report(entity_id: str, state) -> InvestigateContext | None:
    """A crash artifact, addressed by its filename (``<ts>-<kind>.json``). Carries
    the exception, the in-flight tool, and recent-turn digests."""
    from personalclaw.resilience.crashes import read_crash

    try:
        crash = read_crash(entity_id)
    except Exception:  # noqa: BLE001
        logger.debug("crash read failed for %s", entity_id, exc_info=True)
        return None
    if not crash:
        return None
    exc = crash.get("exception") or {}
    lines = [
        f"Crash report {entity_id}",
        f"Kind: {crash.get('kind')} · at {crash.get('ts')}",
        f"Version: {crash.get('version')} · uptime {crash.get('uptime_secs')}s",
        f"Session: {crash.get('session_key') or '(none)'}",
        f"Active model: {crash.get('active_model') or '?'}",
        "",
        f"Exception: {exc.get('type')}: {exc.get('message')}",
    ]
    tool = crash.get("in_flight_tool")
    if isinstance(tool, dict) and tool:
        lines.append(f"In-flight tool: {tool.get('name')} args={tool.get('args_clipped')}")
    for turn in crash.get("last_turns") or []:
        lines.append(f"[recent turn] {turn}")
    if exc.get("traceback"):
        lines.append(f"\nTraceback:\n{exc['traceback']}")
    return InvestigateContext(
        kind="crash_report",
        id=entity_id,
        title=f"Crash: {exc.get('type') or crash.get('kind')}",
        snapshot="\n".join(lines),
        back_link="#/settings/doctor",
        opening_prompt="What caused this crash, and is it likely to happen again?",
    )


# ── Audit: one security-event-log entry ──────────────────────────────────────

# How far back to scan the tail for one entry / its request neighbours. The SEL is
# an unbounded append-only log with no index, so a read-by-id is a bounded tail
# scan — stated in the snapshot when the entry isn't found within it.
_SEL_SCAN = 500


def _resolve_audit_event(entity_id: str, state) -> InvestigateContext | None:
    """One audit (security-event-log) entry by ``event_id``, plus its NEIGHBOURS —
    the other entries sharing its ``request_id`` (one approval flow) or, failing
    that, its session. The log is append-only with no index, so this is a bounded
    tail scan; a miss is reported as "not in the recent window", not as absent."""
    from personalclaw.sel import sel

    try:
        entries = sel().recent(limit=_SEL_SCAN)
    except Exception:  # noqa: BLE001
        logger.debug("SEL read failed", exc_info=True)
        return None
    entry = next((e for e in entries if str(e.get("event_id")) == entity_id), None)
    if entry is None:
        return None
    lines = [
        f"Audit event {entry.get('event_id')} at {entry.get('timestamp')}",
        f"Type: {entry.get('event_type')} · outcome: {entry.get('outcome')}",
        f"Operation: {entry.get('operation')}",
        f"Caller: {entry.get('caller_identity')} · source: {entry.get('source')}"
        f" · agent: {entry.get('agent') or '(none)'}",
    ]
    if entry.get("tool_kind"):
        lines.append(f"Tool kind: {entry['tool_kind']}")
    if entry.get("downstream_service"):
        lines.append(f"Downstream service: {entry['downstream_service']}")
    if entry.get("resources"):
        lines.append(f"Resources: {entry['resources']}")
    if entry.get("error"):
        lines.append(f"Error: {entry['error']}")
    if entry.get("metadata"):
        lines.append(f"Metadata: {entry['metadata']}")

    # Neighbours: the same approval flow, else the same session.
    request_id = str(entry.get("request_id") or "")
    if request_id:
        siblings = [
            e
            for e in entries
            if str(e.get("request_id")) == request_id and str(e.get("event_id")) != entity_id
        ]
        label = f"Same request ({request_id})"
    else:
        caller = str(entry.get("caller_identity") or "")
        siblings = (
            [
                e
                for e in entries
                if str(e.get("caller_identity")) == caller and str(e.get("event_id")) != entity_id
            ][:10]
            if caller
            else []
        )
        label = f"Same session ({caller})"
    if siblings:
        lines.append(
            f"\n{label} — {len(siblings)} related entr" f"{'y' if len(siblings) == 1 else 'ies'}:"
        )
        for sib in siblings[:10]:
            lines.append(
                f"  {sib.get('timestamp')} {sib.get('event_type')} "
                f"{sib.get('operation')} → {sib.get('outcome')}"
            )
    lines.append(
        f"\nNOTE: the audit log is append-only with no index; this was read from the "
        f"most recent {_SEL_SCAN} entries."
    )
    denied = str(entry.get("outcome") or "") in ("denied", "rejected", "failed")
    return InvestigateContext(
        kind="audit_event",
        id=entity_id,
        title=f"Audit: {entry.get('operation') or entry.get('event_type')}",
        snapshot="\n".join(lines),
        back_link="#/settings/security",
        opening_prompt=(
            "Why was this blocked, and was that the right call?"
            if denied
            else "Explain what happened in this audit entry."
        ),
    )


# ── Artifacts: iterate on a versioned artifact ───────────────────────────────


def _resolve_artifact(entity_id: str, state) -> InvestigateContext | None:
    """An artifact, staged so the agent can ITERATE on it rather than just read it.

    This resolver is the only one that suggests ``agent`` mode. Every other kind
    stages a read-only investigation, but iterating on an artifact means calling
    ``artifact_update`` against this one slug — and (owner ruling, 2026-07-29) the
    work legitimately needs the wider toolset too: searching the web, reading
    knowledge, running commands, investigating the project. A narrower mode would
    produce a panel where the agent cannot do the thing the panel is for.

    The body rides the snapshot RAW and is fenced once at injection, matching every
    other resolver — an artifact body is agent-authored content, so fencing it twice
    would corrupt it and fencing it not at all would trust it.
    """
    try:
        from personalclaw.artifacts import registry

        prov = registry.get_provider("native")
        art = prov.get(entity_id) if prov is not None else None
    except Exception:  # noqa: BLE001
        logger.debug("artifact read failed for %s", entity_id, exc_info=True)
        art = None
    if art is None:
        return None

    lines = [
        f"Artifact `{art.slug}`: {art.name}",
        f"Kind: {art.kind}",
        f"Version: v{art.version}",
    ]
    if art.description:
        lines.append(f"Description: {art.description}")
    if art.tags:
        lines.append(f"Tags: {', '.join(str(t) for t in art.tags)}")
    if art.source_path:
        # A file-backed artifact's real source of truth is the workspace file; the
        # agent must edit THAT, not the snapshot, or the next read reverts its work.
        lines.append(f"File-backed — live source: {art.source_path}")
        if art.live_dirty:
            lines.append("The live file currently differs from the latest snapshot.")
    versions = []
    try:
        versions = prov.list_versions(art.slug) if prov is not None else []
    except Exception:  # noqa: BLE001
        logger.debug("artifact version list failed for %s", art.slug, exc_info=True)
    if len(versions) > 1:
        lines.append(f"Existing versions: {', '.join('v' + str(v) for v in versions[-8:])}")

    body = art.content or ""
    if art.kind in ("image",):
        # A binary body is a raw URL reference, never bytes — putting a data URL in
        # the snapshot would blow the turn budget for no benefit.
        lines.append(f"\nBinary artifact; body served at: {body}")
    else:
        lines.append(f"\nCurrent content (v{art.version}):\n{body}")

    return InvestigateContext(
        kind="artifact",
        id=art.slug,
        title=f"Artifact: {art.name}",
        snapshot="\n".join(lines),
        back_link=f"#/artifacts/{art.slug}",
        suggested_task_mode="agent",
        # Names the slug and the tool explicitly: the agent must update THIS artifact
        # in place (a new version on the same slug), not create a near-duplicate —
        # which is exactly what a vaguer prompt produces.
        opening_prompt=(
            f"Iterate on artifact `{art.slug}`. Use artifact_update on that same slug "
            f"so the change lands as a new version rather than a new artifact. What "
            f"would you like changed?"
        ),
    )


register_investigate_resolver("inbox_item", _resolve_inbox_item)
register_investigate_resolver("loop_finding", _resolve_loop_finding)
register_investigate_resolver("notification", _resolve_notification)
register_investigate_resolver("task", _resolve_task)
register_investigate_resolver("schedule_run", _resolve_schedule_run)
register_investigate_resolver("trigger_run", _resolve_trigger_run)
register_investigate_resolver("loop_cycle", _resolve_loop_cycle)
register_investigate_resolver("knowledge_item", _resolve_knowledge_item)
register_investigate_resolver("memory_record", _resolve_memory_record)
register_investigate_resolver("memory_lesson", _resolve_memory_lesson)
register_investigate_resolver("doctor_finding", _resolve_doctor_finding)
register_investigate_resolver("crash_report", _resolve_crash_report)
register_investigate_resolver("audit_event", _resolve_audit_event)
register_investigate_resolver("artifact", _resolve_artifact)
