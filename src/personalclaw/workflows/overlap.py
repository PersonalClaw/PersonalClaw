"""``on_overlap`` — the three policies' decision, and the queue `queue` needs to exist.

`OverlapPolicy.QUEUE` shipped as the exact OPPOSITE of its name (WV-14). The provider
compared against `SKIP` (return early) and `CANCEL_PREVIOUS` (cancel the priors, then
start) and let `queue` fall through to `store.create` + `_launch` — so the one policy
whose name promises ORDERING started a second run **beside** the still-running first one,
silently. A per-minute trigger against a slow workflow stacked runs without bound, which
is the precise hazard `OverlapPolicy`'s own docstring says the default exists to prevent.

Three things live here, in one module, because they are one decision:

**The decision is pure and exhaustive.** `decide()` names every member of the closed enum
and its unreachable tail RAISES rather than defaulting. A fourth member added tomorrow
would otherwise inherit whichever branch happened to be written last — which is exactly
how `QUEUE` inherited "start now".

**The queue is a marked DRAFT run, not a new status.** A queued start creates the run
record and its spec and then does NOT launch it. `RunStatus.DRAFT` is where an unlaunched
run already lives, so no state-machine member (and no frontend status mapping) has to
change. But DRAFT is also where a user's hand-made, deliberately-unstarted editor draft
sits, so the drain MUST NOT treat "DRAFT for this def" as "launch me": queued-ness is a
marker on the run record (`extra[QUEUED_KEY]`), and `extra` is a persisted JSON column, so
the marker survives a restart exactly as the row does. A DRAFT with no marker is never
touched by the drain — starting work a user never asked to start is the worst available
outcome of this atom, so it is the thing a test pins directly.

**The drain is single-flight and idempotent.** It reuses `concurrency.single_flight` (the
flock the claim leases are built on) rather than inventing a lock: two runs of the same def
finishing at once, or a restart landing mid-drain, must not launch the same queued run
twice or launch two at once. Three independent guards, in cheapest-first order: the flock,
the supervisor's own controller registry (`launch` is already idempotent per run id), and
the "is anything active for this def" re-check INSIDE the lock.

**The queue is capped at one, and a drop is loud.** See `MAX_QUEUE_DEPTH`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from personalclaw.concurrency import single_flight
from personalclaw.workflows import store
from personalclaw.workflows.models import OverlapPolicy, RunStatus, WorkflowRun

logger = logging.getLogger(__name__)

#: The marker that distinguishes a QUEUED run from a hand-made DRAFT. On `run.extra`, which
#: is a persisted TEXT/JSON column (`store._JSON_COLUMNS`), so it round-trips through
#: `_row_to_run` and survives a gateway restart with the row.
#:
#: Chosen over the alternatives deliberately. A new `RunStatus.QUEUED` member would be a
#: state-machine change: `active_runs()`, `TERMINAL_RUN_STATUSES`, `_ROOT_TO_RUN`,
#: `materialize`'s exhaustive state→status table and the frontend's status union and badge
#: `Record` all switch on that enum, and none of them need to. `RunOrigin` says WHO started
#: a run (`OriginKind.HOOK` here), not what it is waiting on. A journal fact would be
#: durable but unqueryable: finding "which drafts are queued" would mean opening every
#: draft's ledger, because the journal is per-run and append-only.
QUEUED_KEY = "overlap_queued"

#: When it was queued, for the log/UI reading of a queue that is not draining.
QUEUED_AT_KEY = "overlap_queued_at"

#: How many starts may be pending behind the run in flight. ONE — coalesce-to-one.
#:
#: The honest reading of "queue" for the case it exists for: a per-minute trigger against a
#: ten-minute workflow. Depth 1 keeps the promise the name makes (the fire is not dropped;
#: it runs next) while bounding the backlog. Unbounded depth does not: run N+2 does the same
#: work as run N+1 with staler inputs, and a workflow that ran long once would spend hours
#: replaying trigger fires whose reason has expired — one late run silently becoming a
#: multi-hour backlog. A dropped start is never silent: the action's returned outcome names
#: the cap and the reason, and the provider logs a warning.
MAX_QUEUE_DEPTH = 1

#: How many DRAFT rows one drain looks at. `store.list_runs` orders newest-first, and a
#: queued run is by construction newly created, so a newest-first window is the correct one;
#: the bound exists so a home with thousands of hand-made drafts cannot make the 5s poll
#: read the whole table.
_DRAFT_SCAN_LIMIT = 500


class OverlapAction(str, Enum):
    """What a start DOES, once the policy has been applied. One member per outcome a
    caller has to implement — deliberately not the same shape as `OverlapPolicy`, because
    `queue` has two outcomes (queued / dropped by the cap) and `skip` has one."""

    #: Nothing is in flight (or the policy does not care): create and launch now.
    START = "start"
    #: A prior is in flight and the policy is `skip`: create nothing, launch nothing.
    SKIP = "skip"
    #: A prior is in flight: cancel it, then create and launch now.
    CANCEL_THEN_START = "cancel_then_start"
    #: A prior is in flight and the policy is `queue`: create the run, do NOT launch it.
    QUEUE = "queue"
    #: `queue`, but the queue is already `MAX_QUEUE_DEPTH` deep: create nothing, and SAY SO.
    DROP = "drop"


def decide(policy: OverlapPolicy, *, active: int, queued: int) -> OverlapAction:
    """What a start should do, given the policy and what is already in flight/queued.

    Pure, and exhaustive over the closed enum with a RAISING tail. Every member names
    itself here — that is the ratchet (WV-14): `QUEUE` was silently inherited "start now"
    for the length of the program because nothing anywhere branched on it, and a
    behavioural test alone would happily pass a fallthrough shared by two members.

    `active` counts runs the engine is still driving for this def (RUNNING/PAUSED/
    NEEDS_INPUT — `store.active_runs`). `queued` counts starts already pending behind them.
    """
    if policy == OverlapPolicy.SKIP:
        # The default. "A per-minute trigger must not stack runs" — the enum's own words.
        return OverlapAction.SKIP if active else OverlapAction.START

    if policy == OverlapPolicy.CANCEL_PREVIOUS:
        # Unconditional: cancelling zero priors is a no-op, so this is one branch rather
        # than two, and the caller's cancel loop is the same code either way.
        return OverlapAction.CANCEL_THEN_START

    if policy == OverlapPolicy.QUEUE:
        # `queued` gates the immediate start as well as the cap. Without it, a start
        # arriving in the window between "the prior finished" and "the drain launched the
        # pending run" would JUMP the queue, and `queue` would deliver work out of order —
        # a subtler version of the bug this module exists to fix.
        if not active and not queued:
            return OverlapAction.START
        if queued >= MAX_QUEUE_DEPTH:
            return OverlapAction.DROP
        return OverlapAction.QUEUE

    raise AssertionError(
        f"no branch for OverlapPolicy.{getattr(policy, 'name', policy)} — a new member must "
        "declare its own behaviour here rather than inherit another policy's"
    )


def queued_extra() -> dict[str, Any]:
    """The marker block for a run created by a `queue` decision.

    Handed to `WorkflowRun(extra=...)` so the marker lands in the same INSERT as the row.
    Marking after `store.create` would leave a window in which the row is a plain DRAFT.
    """
    return {QUEUED_KEY: True, QUEUED_AT_KEY: datetime.now(timezone.utc).isoformat()}


def is_queued(run: WorkflowRun) -> bool:
    """True for a run the overlap policy queued. False for every hand-made draft."""
    extra = getattr(run, "extra", None)
    return bool(isinstance(extra, dict) and extra.get(QUEUED_KEY))


def queued_runs(workflow_name: str) -> list[WorkflowRun]:
    """Starts pending for `workflow_name`, oldest first. Never a hand-made draft."""
    if not workflow_name:
        return []
    rows, _total = store.list_runs(
        workflow_name=workflow_name, status=RunStatus.DRAFT, limit=_DRAFT_SCAN_LIMIT
    )
    pending = [r for r in rows if is_queued(r)]
    # `list_runs` is newest-first; the queue is FIFO.
    pending.sort(key=lambda r: (r.created_at, r.id))
    return pending


def queued_depth(workflow_name: str) -> int:
    return len(queued_runs(workflow_name))


def queued_names() -> list[str]:
    """Every def with at least one pending start. The restart/poll entry point."""
    rows, _total = store.list_runs(status=RunStatus.DRAFT, limit=_DRAFT_SCAN_LIMIT)
    seen: list[str] = []
    for run in rows:
        if is_queued(run) and run.workflow_name and run.workflow_name not in seen:
            seen.append(run.workflow_name)
    return seen


async def drain(workflow_name: str, supervisor: Any) -> str | None:
    """Launch the oldest pending start for `workflow_name` if nothing is in flight.

    Returns the launched run id, or None when there was nothing to do. Never raises: its
    live call site is inside `controller._finish`, the single terminal writer (WF2-R10),
    which must not fail a run's terminal status over the NEXT run's start.

    Idempotent and single-flight by three independent guards, cheapest first:

    1. `single_flight` — the same flock `leases.acquire_claim` uses. Cross-process (two
       gateways) and, because flock conflicts across separate open file descriptions even
       within one process, also across two coroutines here. Contended means SKIP, never
       wait in line: the other holder is doing this exact work.
    2. The supervisor's controller registry. `watchdog.launch` returns the EXISTING
       controller for a run id it already holds, so a double launch cannot double-drive;
       checking first also stops a second drain in the window before the new controller's
       tick loop has written RUNNING, which is when `active_runs()` still reads empty.
    3. The active re-check INSIDE the lock, so a run adopted between the caller's decision
       and this call is seen.
    """
    if not workflow_name or supervisor is None:
        return None
    with single_flight(f"workflow-overlap-drain:{workflow_name}") as acquired:
        if not acquired:
            logger.debug("overlap drain for %s already in flight elsewhere", workflow_name)
            return None
        pending = queued_runs(workflow_name)
        if not pending:
            return None
        held = getattr(supervisor, "controller", None)
        if callable(held) and any(held(run.id) is not None for run in pending):
            # A launch for this queue is already in flight; its controller just has not
            # written RUNNING yet.
            return None
        if any(r.workflow_name == workflow_name for r in store.active_runs()):
            return None

        run = pending[0]
        spec = store.read_spec(run.id)
        if not isinstance(spec, dict) or not spec.get("root"):
            # The spec is written in the same breath as the row, so this is a corrupt or
            # hand-deleted run directory. FAIL it rather than leaving it queued: a run the
            # drain can never launch would otherwise be re-examined on every 5s poll
            # forever, and a permanently-pending queue head blocks every start behind it.
            # Writing a terminal status outside a tick loop is safe for exactly the reason
            # the watchdog's orphan reaper is safe — a DRAFT run has no controller.
            logger.warning("queued run %s has no readable spec; failing it", run.id)
            run.status = RunStatus.FAILED
            run.error_message = "queued run could not be started: its spec is missing"
            store.save(run)
            return None

        try:
            await supervisor.launch(run, spec)
        except Exception:
            # Left DRAFT and marked, so the next poll retries. A transient launch failure
            # must not drop the start — dropping it is the bug this atom fixes.
            logger.exception("overlap drain could not launch queued run %s", run.id)
            return None
        logger.info("overlap drain launched queued run %s (%s)", run.id, workflow_name)
        return run.id


async def drain_all(supervisor: Any) -> list[str]:
    """Drain every def with a pending start. The restart path and the poll path.

    This is what makes the queue survive a gateway kill: the row (DRAFT + marker) and the
    spec file are both durable, so the first watchdog poll after a restart re-drains
    whatever was pending. A per-def failure is isolated — one def's bad queue must not
    stop another's.
    """
    launched: list[str] = []
    for name in queued_names():
        try:
            run_id = await drain(name, supervisor)
        except Exception:
            logger.debug("overlap drain failed for %s", name, exc_info=True)
            continue
        if run_id:
            launched.append(run_id)
    return launched
