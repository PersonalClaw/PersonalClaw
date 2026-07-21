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

from personalclaw import shutdown_event
from personalclaw.config.loader import AppConfig
from personalclaw.loop import instrument, kinds, manager, store
from personalclaw.loop.loop import LoopStatus

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECS = 5
_STAGNATION_WINDOW = 5
_MAX_CONSECUTIVE_ERRORS = 2
_FIRST_CYCLE_GRACE_SECS = 600
_MAX_TURN_SECS = 1800


def registry_key(loop_id: str) -> str:
    """The per-loop SSE registry key (one hub per loop, served by /stream)."""
    return f"loop:{loop_id}"


def _unresponsive_deadline(idle_secs: int) -> int:
    """Generous startup grace: a first work turn can take minutes before any
    finding lands, so don't trip 'unresponsive' too eagerly."""
    return max(_FIRST_CYCLE_GRACE_SECS, (idle_secs or 120) * 3)


def check_stagnation(findings: list[dict]) -> bool:
    """True iff the last ``_STAGNATION_WINDOW`` findings all reported zero new items
    (the worker is cycling but surfacing nothing) — the goal-ish stall signal."""
    if len(findings) < _STAGNATION_WINDOW:
        return False
    recent = findings[-_STAGNATION_WINDOW:]
    return all(int(f.get("new_findings_count", 1) or 0) == 0 for f in recent)


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
                "info",
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
        self._publish(
            loop_id, "complete", {"loop_id": loop_id, "reason": reason, "genuine": genuine}
        )

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
            kinds.ensure_loaded()
            strat = kinds.get_or_none(loop.kind)
            namer = getattr(strat, "deliverable_name", None)
            name_on_disk = (namer(loop) if namer else "") or ""
            if not name_on_disk:
                return
            # The deliverable lives in the BOUND WORKSPACE when one is set (the brief
            # directs the worker to write it there so downstream loops read it — see
            # goal.build_brief / fix 2de9af4); it only falls back to the loop dir for
            # an unbound loop. Resolve workspace-first, else the file-backed artifact is
            # never registered (the file isn't in the loop dir) and the only Outputs
            # entries are the worker's ad-hoc artifact_save calls.
            deliverable: Path | None = None
            ws = (loop.workspace_dir or "").strip()
            if ws:
                cand = Path(ws) / name_on_disk
                if cand.is_file():
                    deliverable = cand
            if deliverable is None:
                d = store.safe_loop_dir(loop_id)
                dcand = (d / name_on_disk) if d is not None else None
                if dcand is not None and dcand.is_file():
                    deliverable = dcand
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
                # no-op there). Gated by the kind's config.
                if not self._stagnation_disabled(loop) and check_stagnation(findings):
                    store.update_status(cid, LoopStatus.STAGNANT)
                    self._publish(cid, "stagnant")
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
