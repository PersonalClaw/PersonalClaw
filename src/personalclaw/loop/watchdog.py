"""Unified Loop watchdog — the deterministic supervisor for every kind.

Owns the kind-agnostic lifecycle, polling RUNNING loops and deciding each cycle
whether to keep going, complete, stall, fail, or pause for the user. The
done-ness *signal* is always produced by something OTHER than the worker — it's
delegated to the loop's :class:`LoopKindStrategy` ``is_done_signal`` (a verify
command, a judge subagent, all-phases-gated). The watchdog *decides*; the
strategy only *advises*. This upholds the tenet that no agent certifies its own
work.

Shared lifecycle (all kinds): trust-TTL expiry → NEEDS_INPUT, attended/unattended
question handling, new-finding bookkeeping (clear guidance, stamp nudges, publish),
budget cap, stagnation, loop-exhaustion finalize, and the unresponsive deadline.
The parallel task-worker scheduler (code/design) lands in 2c(iv); this is the
sequential supervisor every kind needs.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from personalclaw import concurrency, notification_kinds, shutdown_event
from personalclaw.config.loader import AppConfig
from personalclaw.loop import instrument, kinds, manager, store
from personalclaw.loop.loop import Loop, LoopStatus

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECS = 5
#: Fallback for ``loops.stagnation_window`` when no config is reachable (WF2LOO-18).
#: Two is the floor the detectors need: content identity is a comparison BETWEEN cycles,
#: so a window of one can only ever compare a finding with itself.
DEFAULT_STAGNATION_WINDOW = 5
_MIN_STAGNATION_WINDOW = 2
_MAX_CONSECUTIVE_ERRORS = 2
_FIRST_CYCLE_GRACE_SECS = 600
_MAX_TURN_SECS = 1800
#: Per-side cap on the text handed to the loop-end skill-ladder review (`LV-1`). A monitor
#: loop's MONITOR_LOG.md grows without bound, and the ladder prompt is a forked model call —
#: matches `loop.finding_content`'s ceiling so the two loop→prompt paths truncate alike.
_LADDER_TEXT_LIMIT = 6000

#: Finding keys that are BOOKKEEPING rather than work product. `cycle` changes every
#: cycle by construction and `new_findings_count` is the worker's own progress claim —
#: leaving either in the content hash would let a worker defeat the hash by incrementing
#: a counter next to unchanged output, which is the exact evasion WF2LOO-18 closes.
_NON_CONTENT_KEYS = frozenset(
    {"cycle", "new_findings_count", "task_id", "timestamp", "ts", "created_at", "updated_at"}
)

#: Finding keys that record what a cycle went and DID — the loops-side analog of a tool
#: call. The shipped kinds write `sources_checked` (goal/research) and `files_touched`
#: (general/sdlc); `tool_calls`/`calls`/`commands` are honored for a kind that records
#: raw calls. A window where any cycle recorded none of these leaves this signal SILENT
#: (the atom's "where a cycle records tool calls") — an all-empty window is trivially
#: identical, and treating that as a stall would stall every kind that records nothing.
_CALL_RECORD_KEYS = ("tool_calls", "calls", "commands", "sources_checked", "files_touched")


def registry_key(loop_id: str) -> str:
    """The per-loop SSE registry key (one hub per loop, served by /stream)."""
    return f"loop:{loop_id}"


def _unresponsive_deadline(idle_secs: int) -> int:
    """Generous startup grace: a first work turn can take minutes before any
    finding lands, so don't trip 'unresponsive' too eagerly."""
    return max(_FIRST_CYCLE_GRACE_SECS, (idle_secs or 120) * 3)


def _finding_content(finding: dict) -> dict:
    """A finding reduced to its WORK PRODUCT — bookkeeping keys dropped."""
    return {k: v for k, v in sorted(finding.items()) if k not in _NON_CONTENT_KEYS}


def _cycle_calls(finding: dict) -> list[str]:
    """Fingerprints of the calls/targets this cycle recorded, order-independent.

    Reuses the engine's :func:`workflows.loop_middleware.call_fingerprint`, so "the same
    call" means the same thing on both work-unit paths. Sorted because re-reading the same
    three URLs in a different order is the same work, not new work.
    """
    from personalclaw.workflows.loop_middleware import call_fingerprint

    out: list[str] = []
    for key in _CALL_RECORD_KEYS:
        if key not in finding:
            continue
        raw = finding[key]
        if raw is None or raw == "" or raw == [] or raw == {}:
            continue
        items = list(raw) if isinstance(raw, (list, tuple)) else [raw]
        out.extend(call_fingerprint(key, item) for item in items)
    return sorted(out)


def _repeats_identically(values: list[Any], window: int, label: str) -> str:
    """Non-empty detail iff ``values`` are byte-identical across the whole window.

    The rule is not re-implemented here: the values are recorded into the engine's
    :class:`workflows.resilience.BreakerState` (whose ``record`` owns the output hashing)
    and the verdict comes from :func:`workflows.resilience.check_breaker` — the same
    byte-identical-output detection the workflow engine already applies. The breaker reads
    its thresholds off a node's ``config``, so the caller hands it a config-carrying node:
    a loops CYCLE is the iteration unit the breaker was written for. Imported lazily
    because ``workflows`` already reaches into ``loop`` (``controller`` → ``loop.gates``),
    and a module-level import back would close that cycle at import time.
    """
    from personalclaw.workflows.models import Node, NodeKind
    from personalclaw.workflows.resilience import BreakerState, check_breaker

    state = BreakerState()
    for value in values:
        state.record(output=value)
    # identical_streak counts repeats AFTER the first observation, so a window of N
    # identical cycles is a streak of N-1.
    node = Node(
        kind=NodeKind.LOOP,
        id=f"loop:{label}",
        config={"identical_streak": max(1, window - 1)},
    )
    verdict = check_breaker(node, state)
    if verdict.tripped and verdict.reason == "identical_output":
        return verdict.detail
    return ""


def _reported_progress(finding: dict) -> bool | None:
    """The worker's OWN progress claim for this cycle: True (claims new findings), False
    (explicitly claims none), or None — the key is absent, so no claim was made.

    None is deliberately not True. Absence used to default to ``1`` ("progressing"), which
    made a worker that simply never wrote the field permanently immune to the stall check.
    """
    if "new_findings_count" not in finding:
        return None
    raw = finding["new_findings_count"]
    if raw is None or raw == "":
        return False  # a written-but-empty count is a claim of nothing, as before
    try:
        return int(raw) > 0
    except (TypeError, ValueError):
        return None


def check_stagnation(findings: list[dict], *, window: int | None = None) -> str:
    """Why this loop is stalling, or ``""`` while it is making progress.

    THREE signals over the last ``window`` findings, only the last of which the worker
    authors — because a detector the worker writes the input to is not a detector:

    1. **Byte-identical work product** (worker-independent). The findings' content —
       bookkeeping keys removed — is unchanged across the whole window. A worker that
       re-emits the same cycle report while claiming five new findings trips this.
    2. **Identical calls** (worker-independent). Every cycle in the window recorded the
       same set of calls/targets: the prose may be freshly worded, but nothing new was
       looked at. Silent unless every cycle in the window recorded something.
    3. **The self-reported count** (kept — it is genuinely informative when honest, and
       the cheapest of the three). It can no longer be the ONLY signal, it can no longer
       VETO the two above, and its absence no longer reads as progress: a window where
       the cycles that spoke all reported zero stalls even if the rest said nothing.
    """
    w = max(_MIN_STAGNATION_WINDOW, int(window or DEFAULT_STAGNATION_WINDOW))
    if len(findings) < w:
        return ""
    recent = findings[-w:]

    if detail := _repeats_identically([_finding_content(f) for f in recent], w, "content"):
        return f"{detail} — the cycle report has not changed"

    calls = [_cycle_calls(f) for f in recent]
    if all(calls):
        if detail := _repeats_identically(calls, w, "calls"):
            return f"{detail} — every cycle checked the same sources"

    claims = [_reported_progress(f) for f in recent]
    if any(c is True for c in claims):
        return ""
    if any(c is False for c in claims):
        return f"the worker reported no new findings for {w} cycles"
    return ""


class LoopWatchdog:
    """The supervisor poll task for all autonomous loops. Construct with the
    dashboard ``state`` + the AutoNudgeService; ``start()`` on gateway startup,
    ``stop()`` on shutdown. Per-loop events publish to ``state.loop_sse()``."""

    def __init__(self, state, svc) -> None:
        self._state = state
        self._svc = svc
        self._task: asyncio.Task | None = None
        self._last_count: dict[str, int] = {}
        self._last_activity: dict[str, float] = {}
        self._running_since: dict[str, float] = {}
        self._consec_errors: dict[str, int] = {}
        #: Loops whose end-of-run skill-ladder review has already been scheduled (`LV-1`).
        #: The ladder is ONE synthesis call per RUN, and ``store.update_status`` permits
        #: COMPLETE → COMPLETE (its guard only rejects a terminal → *different* status), so
        #: ``_complete`` really can run twice for one loop. Without this set the second pass
        #: would pay for a second forked model call and race a second proposal into the queue.
        self._ladder_done: set[str] = set()
        #: Whether the ONE boot sweep has run in this process (`PP-16`). Flipped only AFTER
        #: :meth:`_boot_sweep` returns, so a sweep that raises is retried on the next poll
        #: instead of being lost — the property the gateway startup hook this replaced could
        #: not have.
        self._swept = False

    # ── lifecycle ──

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            logger.info("loop watchdog started")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    def record_turn_outcome(self, loop_id: str, *, ok: bool) -> None:
        """Fail-fast on consecutive failing worker turns (gateway _fire callback).
        After ``_MAX_CONSECUTIVE_ERRORS`` failures with no new finding between, fail
        the loop. A success / new finding resets the streak."""
        if ok:
            self._consec_errors[loop_id] = 0
            return
        n = self._consec_errors.get(loop_id, 0) + 1
        self._consec_errors[loop_id] = n
        if n < _MAX_CONSECUTIVE_ERRORS:
            return
        detail = self._last_worker_error(loop_id)
        try:
            store.update_status(
                loop_id,
                LoopStatus.FAILED,
                error_message=detail or f"Worker failed {n} cycles in a row.",
            )
        except (KeyError, store.TransitionError):
            return
        self._consec_errors.pop(loop_id, None)
        self._publish(loop_id, "failed")

    def _last_worker_error(self, loop_id: str) -> str:
        sess = self._state._sessions.get(manager.session_key(loop_id))
        if sess is None:
            return ""
        for m in reversed(getattr(sess, "messages", [])):
            if m.get("role") == "error":
                return str(m.get("content", ""))[:500]
        return ""

    # ── publishing ──

    _NOTIFY_EVENTS = {
        "complete": ("success", "Loop complete"),
        "failed": ("error", "Loop failed"),
        "stagnant": ("warning", "Loop stalled — needs direction"),
        "blocked": ("warning", "Loop blocked — needs you"),
        "needs_input": ("info", "Loop needs your input"),
        # A code loop advancing an SDLC stage is visible progress worth a heads-up
        # while the user is away (only the code strategy emits stage_advance, so the
        # "stage" wording is always accurate). Ported from the legacy code watchdog.
        "stage_advance": ("info", "Stage complete"),
        # P4 prove-the-instrument: a blind done-ness judge (canary failed) or a completion
        # the independent reproduce refused to confirm are both worth surfacing — they mean
        # the loop's self-assessment can't be trusted this run.
        "judge_blind": ("warning", "Loop paused — done-ness judge unreliable"),
        "ship_blocked": ("warning", "Completion unconfirmed — output not graduated"),
    }

    #: Events where the loop is WAITING ON THE USER, mapped to their registered attention
    #: kind. These outlive the moment they happen in, so they get a durable inbox item
    #: rather than only a toast. Progress/outcome events (complete, failed, stage_advance,
    #: and the two prove-the-instrument warnings) are deliberately NOT here: they report
    #: something that already finished, and an inbox row the user must dismiss would be
    #: busywork rather than attention.
    _ATTENTION_EVENTS = {
        "needs_input": "needs_input",
        "blocked": "needs_input",
        "stagnant": "needs_input",
    }

    def _publish(self, loop_id: str, event: str, data: Any = None) -> None:
        try:
            self._state.loop_sse().publish(
                registry_key(loop_id), event, data or {"loop_id": loop_id}
            )
        except Exception:
            logger.debug("loop_sse publish failed", exc_info=True)
        try:
            self._state.push_refresh("loops")
        except Exception:
            logger.debug("push_refresh(loops) failed", exc_info=True)
        meta = self._NOTIFY_EVENTS.get(event)
        if meta is not None:
            kind, title = meta
            try:
                # Carry the loop KIND so the notification deep-links to the right cockpit
                # (a code loop lives at /#/code/<id>, not /#/loops/<id>). Best-effort.
                loop = store.get(loop_id)
                if event in self._ATTENTION_EVENTS:
                    # A loop waiting on the user is a STANDING request, not a moment: a
                    # toast that scrolls past leaves the loop stalled with no trace. These
                    # events raise a durable inbox item AND one notification through the
                    # same helper, deduped per (loop, event) so a watchdog tick that
                    # re-observes the same wait doesn't stack rows.
                    from personalclaw.inbox import ItemKind, emit_attention_item

                    emit_attention_item(
                        self._state,
                        source="loop",
                        kind=self._ATTENTION_EVENTS[event],
                        item_kind=ItemKind.NEEDS_INPUT.value,
                        title=title,
                        body=self._loop_name(loop_id),
                        refs={"loop": loop_id, "loop_kind": loop.kind if loop else ""},
                        dedup_key=f"loop:{loop_id}:{event}",
                    )
                else:
                    self._state.notify(
                        kind,
                        title,
                        self._loop_name(loop_id),
                        meta={"loop_id": loop_id, "loop_kind": loop.kind if loop else ""},
                    )
            except Exception:
                logger.debug("loop notify failed", exc_info=True)

    def _publish_cycle_verdict(self, loop_id: str, cycle: int) -> None:
        """Publish the third-party done-ness verdict a kind persisted for ``cycle``
        (+ a ratchet_regression flag on a regression) so the cockpit's ROI rail /
        verdict panel / judge-degraded indicator update live. No-op for a kind that
        writes no verdicts (verifiable/monitor/code) — the FE listens for these and
        the legacy goal watchdog published them at the same point."""
        verdict = next(
            (v for v in reversed(store.get_verdicts(loop_id)) if int(v.get("cycle", -1)) == cycle),
            None,
        )
        if verdict is None:
            return
        self._publish(
            loop_id,
            "cycle_verdict",
            {
                "loop_id": loop_id,
                "cycle": cycle,
                "done": bool(verdict.get("done")),
                "marginal_value": verdict.get("marginal_value"),
                "quality_score": verdict.get("quality_score"),
                "regressed": bool(verdict.get("regressed")),
            },
        )
        if verdict.get("regressed"):
            self._publish(
                loop_id,
                "ratchet_regression",
                {"loop_id": loop_id, "cycle": cycle, "reason": verdict.get("done_reason", "")},
            )

    def _loop_name(self, loop_id: str) -> str:
        loop = store.get(loop_id)
        return loop.name if loop else loop_id

    def _cycle_ctx(self):
        """The capabilities handed to a kind's per-cycle orchestration hook so it
        can advance stages, provision/queue tasks, publish, and complete — without
        importing the watchdog."""

        async def _complete(loop_id: str, reason: str = "") -> None:
            await self._complete(loop_id, reason=reason)

        return kinds.CycleContext(
            svc=self._svc,
            state=self._state,
            publish=self._publish,
            complete=_complete,
        )

    def _notify_progress(self, loop_id: str, count: int, max_cycles: int) -> None:
        budget = f"/{max_cycles}" if max_cycles else ""
        try:
            loop = store.get(loop_id)
            self._state.notify(
                notification_kinds.INFO,
                "Loop progress",
                f"Cycle {count}{budget} complete — {self._loop_name(loop_id)}",
                meta={"loop_id": loop_id, "cycle": count, "loop_kind": loop.kind if loop else ""},
            )
        except Exception:
            logger.debug("loop progress notify failed", exc_info=True)

    # ── question handling ──

    def _handle_question(self, loop_id: str, *, attended: bool) -> bool:
        """True iff the loop should pause to NEEDS_INPUT. Unattended NEVER pauses —
        a stray question is discarded so 'unattended' is code-enforced."""
        q = store.pending_question(loop_id)
        if not q:
            return False
        if not attended:
            store.clear_question(loop_id)
            return False
        return True

    # ── completion ──

    async def _complete(self, loop_id: str, *, reason: str = "", genuine: bool = True) -> None:
        """Mark a loop COMPLETE. ``genuine`` (done-ness met / all stages gated) is a
        clean finish. A NON-genuine complete — the cycle budget ran out with the goal
        possibly unmet — persists ``reason`` via error_message so the cockpit can tell
        "finished the work" from "stopped on budget" even after a reload, instead of an
        identical green check. Ported from the legacy code watchdog's genuine flag."""
        fields = (
            {"error_message": None}
            if genuine
            else {"error_message": reason or "Stopped before the goal was met."}
        )
        store.update_status(loop_id, LoopStatus.COMPLETE, **fields)
        store.write_status(loop_id, LoopStatus.COMPLETE, reason=reason)
        await manager.teardown_worker(self._svc, loop_id)
        await self._reconcile_linked_tasks(loop_id)
        # P4 independent REPRODUCE: before graduating a GENUINE completion's deliverable to
        # a permanent artifact, re-confirm it with a fresh, independent ground-truth pass.
        # If that second observation DISAGREES (returns False), block the graduation and
        # surface it — a completion is never shipped on a single observation. A reproduce
        # that can't run (None) never blocks (fail-safe). Budget-stops (genuine=False) are
        # not shippable claims, so they skip the gate.
        ship_ok = True
        if genuine:
            try:
                loop = store.get(loop_id)
                if loop is not None:
                    confirmed = await instrument.reproduce_confirm(loop)
                    if confirmed is False:
                        ship_ok = False
                        self._publish(loop_id, "ship_blocked", {"loop_id": loop_id})
                        logger.warning(
                            "loop %s: reproduce disagreed with completion — "
                            "deliverable NOT graduated (ship_blocked)",
                            loop_id,
                        )
            except Exception:
                logger.debug(
                    "reproduce_confirm failed for %s — shipping anyway", loop_id, exc_info=True
                )
        # Graduate the deliverable to a permanent artifact FIRST, so a scratch loop's
        # report survives even if its raw dir is then reclaimed. Skipped when reproduce
        # blocked the ship.
        if ship_ok:
            self._register_deliverable_artifact(loop_id)
        # Scratch-workspace lifecycle (auto-campaign-scratch-workspace): if the loop
        # opted into auto-teardown, reclaim its OWN scratch dir now that the output is
        # safely graduated. Off by default → the dir persists (today's behavior).
        try:
            from personalclaw.loop import lifecycle

            loop = store.get(loop_id)
            if loop is not None and lifecycle.should_teardown(loop):
                lifecycle.teardown_scratch(loop_id)
        except Exception:
            logger.debug("scratch auto-teardown check failed for %s", loop_id, exc_info=True)
        # Loop-end learning (PP-5): a terminal loop mines its own ledger — the flywheel's RUN_END
        # cadence, now covering the loop kinds it was blind to. Best-effort; a mined draft must
        # never cost the loop its terminal status.
        self._capture_loop_end(loop_id)
        # Loop-end SKILL ladder (`LV-1`): the same 4-tier review the chat after-turn path runs,
        # fired once at the end-of-run seam. Sibling of `_capture_loop_end`, not a change to it:
        # that one MINES lessons from the ledger, this one proposes a skill/template. Scheduled,
        # never awaited — the terminal status is already written and the `complete` publish below
        # must not wait on a forked model call.
        self._schedule_loop_end_ladder(loop_id)
        self._publish(
            loop_id, "complete", {"loop_id": loop_id, "reason": reason, "genuine": genuine}
        )

    def _schedule_loop_end_ladder(self, loop_id: str) -> None:
        """Gate + schedule the end-of-run skill-ladder review (`LV-1`).

        Mirrors ``dashboard.chat_runner._maybe_skill_ladder_review``: the gate is answered
        here, synchronously, and only the expensive half is handed to the background. Gate
        order is deliberate and matches the chat path — ``decision.allowed`` and the
        ``skill_ladder`` cadence flag are checked BEFORE anything reads the loop's task or
        deliverable text, because a restricted session promised that its content feeds no
        learning and classifying that content is already a read of it.

        Fully guarded: a loop's terminal status is never at the mercy of this pass.
        """
        try:
            # Content-free, so it costs nothing to answer first: one review per RUN.
            if loop_id in self._ladder_done:
                return
            from types import SimpleNamespace

            from personalclaw.learning.gate import Cadence, LearningGate

            loop = store.get(loop_id)
            if loop is None:
                return
            cfg = AppConfig.load().learning
            session = SimpleNamespace(key=loop.session_key, is_restricted=False, _ephemeral=False)
            decision = LearningGate.for_session(session, cfg).decide(
                Cadence.RUN_END, cadence_enabled=bool(getattr(cfg, "run_end_enabled", True))
            )
            # The ladder is an expensive forked-LLM pass: it needs the strict answer AND its
            # own cadence flag, exactly like the chat path.
            if not decision.allowed or not getattr(cfg, "skill_ladder", True):
                logger.debug("loop %s: loop-end ladder gated (%s)", loop_id, decision.reason.value)
                return
            # Candidate skills to bias refinement toward (the always-on + indexed set).
            try:
                cb = getattr(self._state, "context_builder", None)
                loaded = [s["key"] for s in cb.skills.list_skills()][:40] if cb else []
            except Exception:
                loaded = []
            self._ladder_done.add(loop_id)
            tasks = getattr(self._state, "_background_tasks", None)
            coro = self._run_loop_end_ladder(loop_id, loaded)
            if tasks is None:
                coro.close()
                logger.debug("loop %s: loop-end ladder has nowhere to schedule", loop_id)
                return
            t = asyncio.create_task(coro)
            tasks.add(t)
            t.add_done_callback(tasks.discard)
        except Exception:
            logger.debug("loop %s: loop-end ladder scheduling failed", loop_id, exc_info=True)

    async def _run_loop_end_ladder(
        self, loop_id: str, loaded_skills: list[str], *, completion=None
    ) -> str | None:
        """The awaitable half of the loop-end ladder review (`LV-1`).

        Split from :meth:`_schedule_loop_end_ladder` so the body is drivable without a
        scheduler. Feeds the loop's REAL texts to the shared review — its goal as the
        "user message", its graduated deliverable (or the planner's summary when the kind
        declares no document) as the "assistant text" — so the review's own guardrails apply
        unchanged. In particular an environment-failure claim in either text is caught by
        ``_ladder_pass``'s ``is_environment_failure_claim`` check and nothing is enqueued;
        re-implementing that predicate here would be a second copy to drift.

        Best-effort: returns the chip summary or None, and never raises.
        """
        try:
            from personalclaw import after_turn_review as atr

            loop = store.get(loop_id)
            if loop is None:
                return None
            goal = (loop.task or "").strip()
            if not goal:
                return None
            return await atr.run_skill_ladder_review(
                session_key=loop.session_key,
                user_message=goal[:_LADDER_TEXT_LIMIT],
                assistant_text=self._loop_outcome_text(loop)[:_LADDER_TEXT_LIMIT],
                loaded_skills=loaded_skills,
                completion=completion,
            )
        except Exception:
            logger.debug("loop %s: loop-end ladder review failed", loop_id, exc_info=True)
            return None

    def _loop_outcome_text(self, loop: Any) -> str:
        """What the run PRODUCED, as text for the ladder's "assistant" side.

        The deliverable document when the kind declares one and it exists on disk (resolved
        by the same :meth:`_deliverable_file` the artifact graduation uses — a second
        resolution here would be a second thing to keep true), else the planner's one-line
        summary. Both may legitimately be empty: the review then sees an empty outcome and
        proposes nothing, which is the correct answer for a run with no observable output.
        """
        try:
            path = self._deliverable_file(loop)
            if path is not None:
                text = path.read_text(encoding="utf-8", errors="replace")
                if text.strip():
                    return text
        except Exception:
            logger.debug("loop %s: deliverable read failed", loop.id, exc_info=True)
        return (getattr(loop, "summary", "") or "").strip()

    def _capture_loop_end(self, loop_id: str) -> None:
        """Route a terminal loop through the LearningGate → loop-end learner (PP-5).

        Mirrors the workflow controller's `_capture_run_end`: gated by the RUN_END cadence, and the
        service is resolved best-effort. The positive-path + inversion producers run with no vector
        store; similarity is inert without one. Fully guarded — never raises into `_complete`.
        """
        try:
            from types import SimpleNamespace

            from personalclaw.learning import loop_end
            from personalclaw.learning.gate import Cadence, LearningGate

            loop = store.get(loop_id)
            if loop is None:
                return
            cfg = AppConfig.load().learning
            session = SimpleNamespace(key=loop.session_key, is_restricted=False, _ephemeral=False)
            decision = LearningGate.for_session(session, cfg).decide(
                Cadence.RUN_END, cadence_enabled=bool(getattr(cfg, "run_end_enabled", True))
            )
            if not decision.allowed:
                logger.debug("loop %s: loop-end capture gated (%s)", loop_id, decision.reason.value)
                return
            loop_end.capture(loop, self._memory_service())
        except Exception:
            logger.debug("loop %s: loop-end capture failed", loop_id, exc_info=True)

    def _memory_service(self) -> Any:
        """The memory service, or None. Similarity mining is inert without a live vector store, so
        None is a valid answer that still lets the no-service producers (traces, inversion) run."""
        try:
            from personalclaw.memory_service import service_for

            cb = getattr(self._state, "context_builder", None)
            mem = getattr(cb, "memory", None) if cb is not None else None
            return service_for(mem) if mem is not None else None
        except Exception:
            logger.debug("loop-end: memory service unavailable", exc_info=True)
            return None

    def _deliverable_file(self, loop: Any) -> Path | None:
        """The loop's document deliverable on disk, or None.

        The ONE resolution of "where did this run write its output" — consumed both by the
        artifact graduation and by the loop-end ladder review (`LV-1`), which needs the same
        answer for a different purpose. Kinds with no document deliverable (verifiable/code:
        the code/check IS the output) declare "" and get None.

        The deliverable lives in the BOUND WORKSPACE when one is set (the brief directs the
        worker to write it there so downstream loops read it — see goal.build_brief / fix
        2de9af4); it only falls back to the loop dir for an unbound loop. Resolve
        workspace-first, else the file-backed artifact is never registered (the file isn't in
        the loop dir) and the only Outputs entries are the worker's ad-hoc artifact_save calls.
        """
        kinds.ensure_loaded()
        strat = kinds.get_or_none(loop.kind)
        namer = getattr(strat, "deliverable_name", None)
        name_on_disk = (namer(loop) if namer else "") or ""
        if not name_on_disk:
            return None
        ws = (loop.workspace_dir or "").strip()
        if ws:
            cand = Path(ws) / name_on_disk
            if cand.is_file():
                return cand
        d = store.safe_loop_dir(loop.id)
        dcand = (d / name_on_disk) if d is not None else None
        if dcand is not None and dcand.is_file():
            return dcand
        return None

    def _register_deliverable_artifact(self, loop_id: str) -> None:
        """On completion, surface the loop's document deliverable (REPORT.md /
        MONITOR_LOG.md — whatever the kind declares) in the Artifacts library as a
        file-backed artifact (a live pointer to the on-disk file, not a copy), tagged
        ``loop:<id>`` so the cockpit Outputs panel finds it. Kinds with no document
        deliverable (verifiable/code: the code/check IS the output) declare "" and
        nothing is registered. Dedup by source_path so a re-completed loop bumps the
        existing artifact. Best-effort — never wedges completion."""
        try:
            loop = store.get(loop_id)
            if loop is None:
                return
            deliverable = self._deliverable_file(loop)
            if deliverable is None:
                return
            content = deliverable.read_text(encoding="utf-8", errors="replace")
            if not content.strip():
                return
            from personalclaw.artifacts import registry as artifact_registry

            prov = artifact_registry.get_provider()
            if prov is None:
                return
            source_path = str(deliverable.resolve())
            name = f"{loop.name} — deliverable" if loop.name else f"Loop {loop_id} deliverable"
            existing = prov.find_by_source_path(source_path)
            if existing is not None:
                prov.update(
                    existing.slug,
                    content=content,
                    snapshot=True,
                    event_type="iterated",
                    actor="agent",
                )
                return
            prov.create(
                name=name,
                content=content,
                kind="markdown",
                source="cron",
                source_path=source_path,
                actor="agent",
                description=(loop.task[:280] if loop.task else ""),
                tags=["loop", f"loop:{loop_id}", loop.kind],
            )
        except Exception:
            logger.debug("deliverable→artifact registration failed for %s", loop_id, exc_info=True)

    async def _reconcile_linked_tasks(self, loop_id: str) -> None:
        """On completion, close the loop's still-open linked tasks (the worker
        routinely leaves finished ones open even though the work — and its deliverable
        — is done). A task whose exit criteria are UNMET is left open on purpose (the
        update raises ValueError at the gate) so an incomplete checklist stays visible
        rather than being force-closed. Terminal tasks are skipped. Best-effort — never
        wedges completion."""
        try:
            loop = store.get(loop_id)
            if loop is None or not loop.linked_task_ids:
                return
            from personalclaw.tasks import registry

            reconciled = 0
            for tid in loop.linked_task_ids:
                try:
                    task = await registry.get_task(tid, provider_name="native")
                    if task is None or task.status.value in ("done", "cancelled"):
                        continue
                    await registry.update_task(tid, provider_name="native", status="done")
                    reconciled += 1
                except ValueError:
                    # Exit-criteria gate (or invalid transition) — leave the task open so
                    # an unmet checklist stays visible rather than being papered over.
                    logger.debug("loop %s: linked task %s not auto-completed (gated)", loop_id, tid)
                except Exception:
                    logger.debug(
                        "loop %s: linked task %s reconcile failed", loop_id, tid, exc_info=True
                    )
            if reconciled:
                logger.info("loop %s complete: marked %d linked task(s) done", loop_id, reconciled)
        except Exception:
            logger.debug("linked-task reconcile failed for %s", loop_id, exc_info=True)

    def _loop_exhausted(self, loop_id: str, max_cycles: int) -> bool:
        """True iff the autonudge loop fired its full budget (gone, or deactivated
        with cycle_count >= max). A paused-mid-budget loop is NOT exhausted."""
        if not max_cycles:
            return False
        nudge_loop = self._svc.get_by_session(manager.session_key(loop_id))
        if nudge_loop is None:
            return True
        return not nudge_loop.active and nudge_loop.cycle_count >= max_cycles

    # ── boot adoption (`PP-16`, "one adoption/reaping path") ──

    async def _boot_sweep(self) -> set[str]:
        """Decide the fate of every loop left mid-flight by a crash/restart, ONCE, on the
        first poll — through the one boot-adoption path both work-unit nouns now share
        (:func:`concurrency.boot_sweep`).

        A worker — and the planner — session lives only in memory, so a loop persisted
        RUNNING or PLANNING at startup has lost it:

        * RUNNING → :func:`manager.start` (re-arm the execution worker).
        * PLANNING → re-kick one ``advance_plan`` pass. The stepwise walkthrough runs as a
          background task spawned from an HTTP request, so a restart strands it in PLANNING
          with no live planner; ``advance_plan`` is idempotent and self-healing — it re-runs
          the in-flight step / design pass and stops at the next gate.

        PAUSED/STAGNANT/BLOCKED/NEEDS_INPUT/REVIEW await a deliberate action. Idempotent — a
        genuinely-live worker is skipped. Also GCs orphan file dirs with no backing row.

        **This was `loop/manager.reap_orphaned_loops`, awaited from a gateway startup hook** —
        the second boot-adoption path `PP-16` names beside ``workflows/watchdog``'s. Both now
        run through one primitive from the first poll of the supervisor that owns the noun,
        which fixes two defects the hook shape guaranteed: the hook's
        ``except: logger.warning`` lost loop revival for the life of the process (a loop stuck
        RUNNING with no worker, which a user reads as "still working"), and awaiting it inline
        delayed gateway readiness by however long N stranded planner passes take.
        """
        # Self-sufficient on purpose: `_rearm_running` asks the kind for its `launch_blocker`,
        # and an unloaded registry answers `None` — which silently re-arms a brownfield loop
        # against a workspace that is gone instead of parking it. `_poll_once` also calls this,
        # so the sweep must not depend on being reached only through it.
        kinds.ensure_loaded()
        loops = store.list_all()

        def _lost_its_worker(loop: Loop) -> bool:
            """A RUNNING loop with NO worker session at all.

            ``state._sessions`` is in-memory, so a loop the *previous* process armed has no
            entry here — absence is the whole crash signal. The version this replaced also
            required ``sess.running``, and that extra condition is wrong anywhere a session
            can be idle: between cycles a live loop's session exists with ``running`` False
            (autonudge fires a turn every ``idle_secs``), so the stricter predicate reads a
            perfectly healthy idle loop as a crash survivor and re-arms it. It was harmless
            only because the boot hook ran before any session could exist; moving the sweep
            into the poll that DOES see sessions is exactly the drift that would have made it
            bite. `test_expired_trust_pauses_for_reauth` is the shipped test that proves the
            difference — under the strict predicate its live-but-idle loop is re-armed instead
            of being trust-expired, and `manager.start` re-stamps the RUNNING row on the way
            past, which is how a re-arm silently resets the trust window.
            """
            if loop.status != LoopStatus.RUNNING.value:
                return False
            return self._state._sessions.get(manager.session_key(loop.id)) is None

        def _stranded_in_planning(loop: Loop) -> bool:
            return loop.status == LoopStatus.PLANNING.value

        decided = await concurrency.boot_sweep(
            "loop", loops, survived=_lost_its_worker, decide=self._rearm_running
        )
        decided |= await concurrency.boot_sweep(
            "loop-planning", loops, survived=_stranded_in_planning, decide=self._rekick_planning
        )
        try:
            reaped = store.reap_orphan_dirs()
            if reaped:
                logger.info("loop: reaped %d orphan dir(s) with no DB row", reaped)
        except Exception:
            logger.warning("loop: orphan-dir GC failed", exc_info=True)
        return decided

    async def _rearm_running(self, loop: Loop) -> bool:
        """Re-arm one RUNNING loop whose worker died with the process — or park it for the
        user if its workspace went missing. Both are decisions, so both return ``True``."""
        # The live worker was reaped by the crash/restart — record it as a `watcher_reaped`
        # ledger event (PP-5) so the flywheel sees a watcher cut off before its cadence
        # (fewer cycles than the budget implies), not a template that simply under-produced.
        try:
            store.record_watcher_reaped(
                loop.id, cycles=loop.total_cycles, reason="worker process lost to restart"
            )
        except Exception:
            logger.debug("loop: watcher_reaped emit failed for %s", loop.id, exc_info=True)
        # A workspace-needing loop (brownfield code) can have its bound dir moved/deleted
        # during downtime. start() would re-provision against the gone path; re-validate via
        # the kind's launch precondition (the same one the start action enforces) and pause
        # for the user instead of resurrecting nothing.
        strat = kinds.get_or_none(loop.kind)
        blocker = getattr(strat, "launch_blocker", None)
        reason = blocker(loop) if blocker else None
        if reason:
            store.write_question(
                loop.id, f"{reason} (the workspace went missing during a restart)."
            )
            store.update_status(loop.id, LoopStatus.NEEDS_INPUT)
            logger.warning(
                "loop: orphaned %s blocked from re-arm (%s) — paused for the user", loop.id, reason
            )
            return True
        await manager.start(self._state, self._svc, loop.id)
        logger.info("loop: re-armed orphaned %s after restart", loop.id)
        return True

    async def _rekick_planning(self, loop: Loop) -> bool:
        """Re-kick one restart-stranded PLANNING loop so it resumes instead of freezing on a
        spinner forever. Lazy import (plan_walkthrough → store, no watchdog cycle, but kept
        lazy for symmetry + cheap startup).

        **KNOWN WART, carried over from `reap_orphaned_loops` and deliberately not changed
        here: this makes a MODEL CALL from inside the boot sweep.** That is wrong in principle
        — it gives adoption unbounded latency, and because a failed sweep is now retried every
        poll, a provider outage turns the retry into a 5-second-interval hammer. The right
        shape is for the sweep to leave the row in a state the *ordinary* poll advances, which
        means `_poll_once` growing a PLANNING pass (it iterates RUNNING only today). That is
        new supervisor behaviour needing its own budget/attention/stagnation coverage, so it
        belongs to `PP-16`'s still-open "pluggable supervisor" seam, not to this one — removing
        the call without building the replacement would strand every restart-interrupted
        PLANNING loop forever, which is worse than the wart. Recorded in
        PLATFORM-PRIMITIVES' execution log.

        Practical hazard while it stands: **no test reaches this today** (measured — no test
        both creates a PLANNING loop and calls `_poll_once`), so a future test that does will
        silently start making a real model call inside the suite. Stub
        `plan_walkthrough.advance_plan` when you write it, as
        `test_loop_manager.py::TestBootSweep::test_rekicks_planning_orphan` does.
        """
        from personalclaw.loop import plan_walkthrough as pw

        await pw.advance_plan(self._state, self._svc, loop.id)
        logger.info("loop: re-kicked stranded planning loop %s after restart", loop.id)
        return True

    # ── poll loop ──

    async def _loop(self) -> None:
        while not shutdown_event.is_set():
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("loop watchdog poll errored", exc_info=True)
            try:
                await asyncio.sleep(POLL_INTERVAL_SECS)
            except asyncio.CancelledError:
                raise

    async def _poll_once(self) -> None:
        kinds.ensure_loaded()
        # The boot sweep runs ONCE, and BEFORE this poll reads a single loop: a loop persisted
        # RUNNING by a crash has no worker session, and every check below would misread that
        # as a live loop whose worker went silent. Its ids are deliberately NOT skipped the
        # way the run side skips its swept ids — a loop the sweep re-armed IS live now and
        # should be polled, and one it parked has left RUNNING so the filter below drops it.
        if not self._swept:
            await self._boot_sweep()
            self._swept = True
        cfg = AppConfig.load().loops
        running = [loop for loop in store.list_all() if loop.status == LoopStatus.RUNNING.value]
        live_ids = {loop.id for loop in running}
        for cid in list(self._last_count):
            if cid not in live_ids:
                self._last_count.pop(cid, None)
                self._last_activity.pop(cid, None)
                self._consec_errors.pop(cid, None)
                self._running_since.pop(cid, None)

        for loop in running:
            cid = loop.id
            session = self._state._sessions.get(manager.session_key(cid))

            # 1. Trust TTL — expire the worker's auto-approve grant → NEEDS_INPUT.
            if loop.started_at and time.time() - loop.started_at > cfg.trust_ttl_secs:
                if session is not None:
                    session._trust = False
                store.write_question(
                    cid,
                    "Auto-approval expired after the trust window. "
                    "Resume to re-authorize and continue.",
                )
                store.update_status(cid, LoopStatus.NEEDS_INPUT)
                self._publish(cid, "needs_input")
                continue

            # 2. Needs input — attended pause vs unattended discard.
            if self._handle_question(cid, attended=loop.attended):
                store.update_status(cid, LoopStatus.NEEDS_INPUT)
                self._publish(cid, "needs_input")
                continue

            # Ingest any new worker finding files into the ledger BEFORE reading them back — the
            # findings the rest of this poll works with are the ledger projection (PP-5).
            # Idempotent (keyed by source file), so calling it every poll is safe.
            store.record_cycle_findings(cid)
            findings = store.get_findings(cid)
            count = len(findings)

            # Seed/refresh liveness on first observation or after a (re)start.
            if cid not in self._last_count or self._last_activity.get(cid, 0.0) < (
                loop.started_at or 0.0
            ):
                self._last_count[cid] = count
                self._last_activity[cid] = time.time()
                continue

            if count > self._last_count[cid]:
                # 3. New finding — progress.
                self._last_count[cid] = count
                self._last_activity[cid] = time.time()
                self._running_since.pop(cid, None)
                self._consec_errors[cid] = 0
                store.set_total_cycles(cid, count)
                latest = findings[-1]
                store.clear_guidance(cid)
                store.mark_nudges_applied(cid, count)
                self._publish(cid, "new_finding", {"loop_id": cid, "finding": latest})

                # Done-ness — produced by something OTHER than the worker. A kind
                # with multi-cycle orchestration (code: advance the SDLC stage + run
                # the gate; design: advance the design step) runs its on_new_cycle
                # hook, which OWNS the cycle's done-ness (and its own side effects:
                # stage-advance, provisioning, publish). A kind without one falls
                # through to the generic point-in-time is_done_signal.
                strat = kinds.get_or_none(loop.kind)
                done = False
                if strat is not None:
                    hooked = await kinds.run_cycle_hook(strat, loop, findings, self._cycle_ctx())
                    if hooked is not None:
                        # The kind's orchestration owns done-ness this cycle.
                        if hooked:
                            continue  # the hook already completed the loop
                    else:
                        try:
                            signal = await strat.is_done_signal(loop, findings)
                        except Exception:
                            logger.warning("loop %s: is_done_signal errored", cid, exc_info=True)
                            signal = None
                        if signal is None:
                            # None has TWO meanings: (a) a kind that HAS a point-in-time
                            # done-check genuinely couldn't assess (judge errored / verify
                            # un-runnable) → degraded, surface it; (b) a kind that has NO
                            # such check for this loop's config (e.g. a General loop with no
                            # verify_command) → deferring to budget BY DESIGN, not a failure.
                            # Only flag (a), so we don't false-alarm "Done-ness check
                            # unavailable" on a loop that never had one.
                            has_check = getattr(strat, "has_done_check", lambda _l: True)(loop)
                            if has_check:
                                # P4: distinguish a transient judge failure from a CONFIRMED
                                # BLIND judge (the canary proved it can't tell good from empty).
                                # A blind judge won't recover by retrying, so halt the loop to
                                # NEEDS_INPUT with judge_blind rather than spinning on judge_error.
                                fresh = store.get(cid)
                                blind = (
                                    bool((fresh.kind_config or {}).get("judge_calibrated") is False)
                                    if fresh
                                    else False
                                )
                                if blind:
                                    store.update_status(cid, LoopStatus.NEEDS_INPUT)
                                    self._publish(
                                        cid, "judge_blind", {"loop_id": cid, "cycle": count}
                                    )
                                else:
                                    self._publish(
                                        cid, "judge_error", {"loop_id": cid, "cycle": count}
                                    )
                        else:
                            # A non-None signal means the kind ran a third-party assessment
                            # and persisted whatever it produced. Publish the verdict it just
                            # wrote for THIS cycle (+ a ratchet_regression flag) so the ROI
                            # rail / verdict panel / judge-degraded indicator update live —
                            # the FE listens for these. Kind-agnostic: a kind that writes no
                            # verdict (verifiable/monitor) yields none here, so nothing emits.
                            self._publish_cycle_verdict(cid, count)
                        done = signal is True
                if done:
                    await self._complete(cid, reason="done-ness signal met")
                    continue
                # A loop the hook re-fetched may have changed status (e.g. code paused
                # to BLOCKED on a stalled gate) — if it's no longer RUNNING, stop here.
                if (cur := store.get(cid)) is not None and cur.status != LoopStatus.RUNNING.value:
                    continue

                # Budget cap — max_cycles > 0 always bounds a finite loop. Reaching it
                # is NON-genuine by default (the goal may not be met → "stopped on
                # budget"), EXCEPT where the budget IS the intended stopping condition
                # (a monitor's watch window): the kind says so via budget_stop_genuine,
                # so the cockpit shows a clean completion rather than an error-flavored
                # "stopped before done" for an inherently-ongoing loop that ran its course.
                if loop.max_cycles > 0 and count >= loop.max_cycles:
                    genuine = (
                        bool(getattr(strat, "budget_stop_genuine", lambda _l: False)(loop))
                        if strat is not None
                        else False
                    )
                    await self._complete(cid, reason="cycle budget reached", genuine=genuine)
                    continue

                self._notify_progress(cid, count, loop.max_cycles)
                # Stagnation — disabled for monitor goals (a quiet cycle is a valid
                # no-op there). Gated by the kind's config. The window is a user knob
                # (loops.stagnation_window), read per poll so a change applies to the
                # next cycle without a restart.
                if not self._stagnation_disabled(loop) and (
                    why := check_stagnation(findings, window=cfg.stagnation_window)
                ):
                    store.update_status(cid, LoopStatus.STAGNANT)
                    # A stall is a `breaker_trip` on the ledger (PP-5) — the same kind the workflow
                    # breaker emits — so the flywheel sees a loop was cut off, not just that it
                    # produced fewer cycles.
                    store.record_breaker_trip(cid, count, why)
                    logger.info("loop %s stalled: %s", cid, why)
                    self._publish(cid, "stagnant", {"loop_id": cid, "reason": why})
            else:
                # 4a. Loop exhausted — autonudge fired its full budget but some
                # cycles produced no finding (a turn errored before writing one).
                if session is None or not getattr(session, "running", False):
                    if self._loop_exhausted(cid, loop.max_cycles):
                        if count > 0:
                            store.set_total_cycles(cid, count)
                            await self._complete(
                                cid, reason="cycle budget exhausted", genuine=False
                            )
                        else:
                            store.update_status(
                                cid,
                                LoopStatus.FAILED,
                                error_message="The worker produced no findings "
                                "before the cycle budget was exhausted.",
                            )
                            await manager.teardown_worker(self._svc, cid)
                            self._publish(cid, "failed")
                        self._clear_liveness(cid)
                        continue

                # 4b. Unresponsive check.
                now = time.time()
                reprompt = bool(getattr(session, "_suppress_autonudge_rearm", False))
                if (session is not None and getattr(session, "running", False)) or reprompt:
                    started = self._running_since.setdefault(cid, now)
                    if now - started <= _MAX_TURN_SECS or reprompt:
                        self._last_activity[cid] = now
                else:
                    self._running_since.pop(cid, None)
                if now - self._last_activity.get(cid, 0.0) > _unresponsive_deadline(
                    loop.idle_secs or cfg.default_idle_secs
                ):
                    # A finding the worker wrote mid-poll is only on the ledger once ingested, so
                    # ingest before the "did progress land during a long turn?" re-check.
                    store.record_cycle_findings(cid)
                    if len(store.get_findings(cid)) > count:
                        continue  # progress landed during a long turn
                    wedged = session is not None and getattr(session, "running", False)
                    store.update_status(
                        cid,
                        LoopStatus.FAILED,
                        error_message=(
                            "Worker turn ran too long without producing "
                            "a finding (wedged). Resume to continue."
                            if wedged
                            else "No activity — the worker stalled. Resume to continue."
                        ),
                    )
                    await manager.teardown_worker(self._svc, cid)
                    self._clear_liveness(cid)
                    self._publish(cid, "failed")

    def _stagnation_disabled(self, loop) -> bool:
        """Monitor goals never stagnate (a quiet cycle is a valid no-op). Other
        kinds use the stall signal."""
        return loop.kind == "goal" and str((loop.kind_config or {}).get("goal_type")) == "monitor"

    def _clear_liveness(self, cid: str) -> None:
        self._last_count.pop(cid, None)
        self._last_activity.pop(cid, None)
        self._running_since.pop(cid, None)
