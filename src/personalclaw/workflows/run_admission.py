"""Admission for a run's ready set: the policies that need a clock and the disk.

`frontier()` is pure, so it can apply neither a lease (occupancy that lives on disk, under a TTL)
nor a bake floor (elapsed time). Both are still ADMISSION — "may this start now, given persisted
state" — so they compose in the same list, through the same `admission.compose()`, against the same
`AdmissionRequest`; only the impure inputs are gathered here, by the one object that already owns a
clock and the run's state. That split is what keeps the frontier replayable while the new rules
still bind for real.

Everything here runs on the controller's tick, under its lock. The bookkeeping it keeps —
the leases this run holds, the holds already journaled, the moment a time-bound hold could next
change its mind — lives on the controller, next to the rest of the run's state.
"""

from __future__ import annotations

import re
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from personalclaw.loop.tick import Action as StepAction
from personalclaw.workflows import execution_hints
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import pool
from personalclaw.workflows.admission import (
    AdmissionRequest,
    AdmissionState,
    MetricGate,
    Scope,
    compose,
    default_policies,
)
from personalclaw.workflows.models import TERMINAL_STATES, RunStatus, spec_path, stamp_epoch, walk
from personalclaw.workflows.tick import Limits, ReadyNode

if TYPE_CHECKING:
    from personalclaw.workflows.controller import RunController


#: Node config keys the policies read. A spec containing none of them never builds an
#: `AdmissionState` and never asks a question — the code path from before these policies, exactly.
_ADMISSION_KEYS = ("lease", "min_dwell_secs", "metric_pass")


#: `root.children[2]` → its parent and index. Sequence children are POSITIONAL in an instance
#: path (`tick._visit`), which is what makes "the step before this one" answerable at all.
_CHILD_SEGMENT = re.compile(r"^(?P<parent>.+)\.children\[(?P<index>\d+)\]$")


async def admit_ready(ctl: RunController, ready: list[ReadyNode]) -> list[ReadyNode] | None:
    """Apply the lease / bake-floor / metric-gate policies to this tick's ready set.

    Returns what may launch, or `None` when this call FINISHED the run — a metric gate that ran
    out of rollbacks is the loop's `COMPLETE(blocked)`, and a run whose gate has given up must
    say so rather than hold a step forever.
    """
    if not _declares_admission_keys(ctl):
        return list(ready)
    _adopt_held_leases(ctl)
    _release_settled_leases(ctl)
    ctl._admission_wake = 0.0
    state = _admission_state(ctl, ready)
    wip = execution_hints.from_runtime_hints(ctl.spec.get("runtime_hints")).single_active_feature
    admitted: list[ReadyNode] = []
    stalls: list[str] = []
    for item in ready:
        ok, fatal = _admit(ctl, item, state, wip=wip, stalls=stalls)
        if fatal:
            await ctl._finish(RunStatus.FAILED, error=fatal)
            return None
        if ok:
            admitted.append(item)
    if (
        not admitted
        and not ctl._inflight
        and stalls
        and not ctl._admission_wake
        and not ctl._pending_mutations
    ):
        # Every refusal this tick was one that cannot change by itself, with nothing running to
        # change it and no rollback queued. Holding forever would be a silent hang; the tick
        # loop's no-deadline path sleeps zero, so it would be a HOT one.
        await ctl._finish(RunStatus.FAILED, error="; ".join(stalls))
        return None
    return admitted


def _admit(
    ctl: RunController,
    item: ReadyNode,
    state: AdmissionState,
    *,
    wip: bool,
    stalls: list[str],
) -> tuple[bool, str]:
    """One item's verdict: `(may_launch, fatal_error)`.

    The step gate is asked BEFORE the lease is claimed. Reversed, a step held by its metric gate
    would still take the resource and hold it for the whole bake window — a claim nothing is
    going to use.
    """
    claim = _lease_claim(ctl, item.path)
    policies = default_policies(
        ctl.services.lane_limits or Limits(),
        single_active_feature=wip,
        # The holder is per ITEM, so the snapshot is re-stamped rather than rebuilt: two items
        # of one fan-out must present different identities or the lease would never serialize
        # them (`pool.acquire` treats a same-holder re-acquire as a renewal).
        state=replace(state, holder=claim[1] if claim else ""),
    )
    request = AdmissionRequest(scope=Scope.STEP, key=item.path, node=item.node)
    verdict = compose(policies, request)
    if not verdict.admits(0):
        binding = verdict.binding
        reason = ""
        if isinstance(binding, MetricGate):
            decision = binding.decision(request)
            if decision is not None:
                reason = decision.reason
                if decision.action is StepAction.COMPLETE:
                    return False, (f"metric gate on {item.node.id or item.path}: {decision.reason}")
                if decision.action is StepAction.ROLLBACK:
                    _queue_metric_rollback(ctl, item, decision)
                else:
                    stalls.append(
                        f"metric gate on {item.node.id or item.path} holds it: "
                        f"{decision.reason}"
                    )
        _journal_admission_hold(ctl, item, verdict, reason)
        return False, ""
    if claim is None:
        return True, ""

    resource, holder, _scope, ttl = claim
    request = AdmissionRequest(scope=Scope.RESOURCE, key=resource, node=item.node)
    verdict = compose(policies, request)
    if not verdict.admits(0):
        record = state.leases.get(resource)
        _journal_admission_hold(
            ctl,
            item,
            verdict,
            f"{resource!r} is held by {record.holder!r}" if record else f"{resource!r} is held",
        )
        if record is not None:
            _note_admission_wake(ctl, record.expires_at())
        return False, ""
    lease, error = pool.claim_task(resource, holder=holder, now=state.now, ttl_seconds=ttl)
    if lease is None:
        # The verdict said yes; the flocked compare-and-swap said no. THIS is the authoritative
        # answer — a policy that advised on a stale read and a claim that lost the race are the
        # two halves of one mechanism, and skipping the claim because the advice was positive is
        # exactly the read-then-write measured failing 36 of 40 races.
        _journal_admission_hold(ctl, item, verdict, f"{resource!r} claim lost: {error}")
        _note_admission_wake(ctl, state.now + 1.0)
        return False, ""
    ctl._held_leases[resource] = holder
    return True, ""


def _declares_admission_keys(ctl: RunController) -> bool:
    """Whether any node declares an admission key, cached per spec version."""
    cached = ctl._admission_declared
    version = int(ctl.run.spec_version)
    if cached is not None and cached[0] == version:
        return cached[1]
    declared = any(
        key in (node.config or {}) for _path, node in walk(ctl.root) for key in _ADMISSION_KEYS
    )
    ctl._admission_declared = (version, declared)
    return declared


def _admission_state(ctl: RunController, ready: list[ReadyNode]) -> AdmissionState:
    """Gather the clock-and-disk inputs for this tick's ready set. The only impure step."""
    now = time.time()
    leases: dict[str, pool.Lease] = {}
    ttl = pool.DEFAULT_LEASE_SECS
    since: dict[str, float] = {}
    metrics: dict[str, float] = {}
    floors: dict[str, float] = {}
    rollbacks: dict[str, int] = {}
    for item in ready:
        claim = _lease_claim(ctl, item.path)
        if claim is not None:
            resource, _holder, _scope, ttl = claim
            record = pool.read_lease(resource)
            if record is not None:
                leases[resource] = record
        config = item.node.config or {}
        if config.get("min_dwell_secs"):
            prior_path, _prior_id = _prior_step(ctl, item.path)
            completed = ctl._instance(prior_path).completed_at if prior_path else None
            if completed:
                since[item.path] = stamp_epoch(completed)
        if config.get("metric_pass") is None:
            continue
        value = _resolve_metric(ctl, config.get("metric_from"))
        if value is not None:
            metrics[item.path] = value
        floor = _opt_metric(config.get("metric_floor"))
        if floor is not None:
            floors[item.path] = floor
        prior_path, _prior_id = _prior_step(ctl, item.path)
        if prior_path:
            # `epoch` IS the consecutive-rollback count: every rollback rewinds the prior step,
            # and `mutations.next_epoch` bumps it. Persisted, so the cap survives a restart —
            # an in-memory counter would let a crash-looping run roll back forever.
            rollbacks[item.path] = int(ctl._instance(prior_path).epoch)
    return AdmissionState(
        now=now,
        leases=leases,
        lease_ttl_secs=ttl,
        since=since,
        metrics=metrics,
        floors=floors,
        rollbacks=rollbacks,
    )


def _lease_claim(ctl: RunController, path: str) -> tuple[str, str, str, int] | None:
    """`(resource, holder, holder scope, ttl)` for a ready item under a `lease:` declaration.

    The holder scope is what RELEASES the lease: the declaring node itself, or — when the
    declaration is on a container — the ITEM of it this path belongs to, so a `foreach` holding
    `lease: "endpoint"` serializes its items instead of claiming once for the whole fan-out.

    The OUTERMOST declaration wins. One resource per item is deliberate: two would need a claim
    ORDER to stay deadlock-free, and an ordered multi-resource lock manager is the distributed
    substrate this design deliberately excludes.
    """
    nodes = dict(walk(ctl.root))
    segments = path.split(".")
    for i in range(len(segments)):
        prefix = ".".join(segments[: i + 1])
        node = nodes.get(spec_path(prefix))
        if node is None:
            continue
        config = node.config or {}
        resource = str(config.get("lease", "") or "").strip()
        if not resource:
            continue
        scope = prefix
        if i + 1 < len(segments) and "#" in segments[i + 1]:
            scope = ".".join(segments[: i + 2])
        try:
            ttl = int(config.get("lease_ttl_secs") or pool.DEFAULT_LEASE_SECS)
        except (TypeError, ValueError):
            ttl = pool.DEFAULT_LEASE_SECS
        return resource, f"{ctl.run.id}:{scope}", scope, max(1, ttl)
    return None


def _prior_step(ctl: RunController, path: str) -> tuple[str, str]:
    """`(instance path, node id)` of the step immediately before `path` in its parent sequence.

    Empty for the first child, or for a node whose parent is not a sequence: a bake floor has
    nothing to measure from and a rollback has nowhere to go, and inventing a target would roll
    back a node the author never put in front of this one.
    """
    match = _CHILD_SEGMENT.match(path)
    if match is None:
        return "", ""
    index = int(match.group("index"))
    if index == 0:
        return "", ""
    prior = f"{match.group('parent')}.children[{index - 1}]"
    node = dict(walk(ctl.root)).get(spec_path(prior))
    return prior, (node.id if node is not None else "")


def _resolve_metric(ctl: RunController, raw: Any) -> float | None:
    """Resolve `metric_from` against the run's outputs.

    Accepts the dotted form (`verify.score`) and the familiar binding form
    (`{{nodes.verify.output.score}}`) — normalising instead of ignoring, because a metric source
    the engine silently could not read would leave the gate looking enforced while abstaining on
    every tick.
    """
    source = str(raw or "").strip()
    if not source:
        return None
    source = source.strip("{} ").strip()
    if source.startswith("nodes."):
        source = source[len("nodes.") :].replace(".output.", ".", 1)
    cursor: Any = ctl._outputs
    for key in source.split("."):
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(key)
    return _opt_metric(cursor)


def _queue_metric_rollback(ctl: RunController, item: ReadyNode, decision: Any) -> None:
    """Roll the prior step back on a metric regression, through the real mutation queue.

    A `rewind` op, not a bespoke reset: `mid_flight._apply_reentry` already archives outputs, bumps
    the epoch, invalidates the journal region and drops stale approvals, and a second reset path
    would be one that forgets whichever of those it was written before.
    """
    prior_path, prior_id = _prior_step(ctl, item.path)
    if not prior_id:
        return
    key = f"{item.path}@{ctl._instance(prior_path).epoch}"
    if key in ctl._rollbacks_queued:
        return
    ctl._rollbacks_queued.add(key)
    body = ctl.submit_mutation(
        # `force`, deliberately: without it `mutations.next_epoch` leaves the epoch alone
        # and the journal's inputs-hash tier REPLAYS the step's cached output. A rollback
        # that serves the cache re-produces the metric that failed, so the gate would roll
        # back forever — and the epoch is also the persisted rollback count that caps it.
        [{"kind": "rewind", "node_id": prior_id, "force": True}],
        actor="engine",
        confirm=True,
    )
    ctl.journal.write(
        journal_mod.DECISION,
        instance_path=item.path,
        node_id=item.node.id,
        decision="metric_rollback",
        detail=(
            f"{decision.reason}; rolling back to {prior_id!r} "
            f"(queued={bool(body.get('queued'))})"
        ),
    )


def _journal_admission_hold(ctl: RunController, item: ReadyNode, verdict: Any, reason: str) -> None:
    """Record one admission refusal, once. A refusal nobody can read back is indistinguishable from
    a scheduler that lost the node — the same reasoning `_journal_wip_holds` is built on."""
    binding = verdict.binding
    hold = verdict.hold.value or "unrecorded"
    key = f"{item.path}@{hold}"
    if key in ctl._admission_logged:
        return
    ctl._admission_logged.add(key)
    ctl.journal.write(
        journal_mod.DECISION,
        instance_path=item.path,
        node_id=item.node.id,
        decision=f"admission_{hold}",
        detail=(
            f"{getattr(binding, 'name', '') or 'admission'} held this step"
            + (f": {reason}" if reason else "")
        ),
    )


def _note_admission_wake(ctl: RunController, when: float) -> None:
    """Earliest moment a time-bound hold could change its mind."""
    if when <= 0:
        return
    ctl._admission_wake = when if not ctl._admission_wake else min(ctl._admission_wake, when)


def _adopt_held_leases(ctl: RunController) -> None:
    """Re-adopt the leases THIS RUN holds, once — the restart half of the release path.

    A fresh controller has no memory of a claim its predecessor made, and only a holder may
    release. The holder string is run-scoped, so the records on disk name this run: adopting
    them is what lets a restarted gateway hand the resource on when the item settles. Without
    it the release would wait for the TTL — correct, but "the endpoint sits idle for fifteen
    minutes" is exactly the outcome a named holder exists to prevent.
    """
    if ctl._leases_adopted:
        return
    ctl._leases_adopted = True
    prefix = f"{ctl.run.id}:"
    root = pool.leases_dir()
    if not root.is_dir():
        return
    for path in sorted(root.glob("*.json")):
        record = pool.read_lease(path.stem)
        if record is not None and record.task_id and record.holder.startswith(prefix):
            ctl._held_leases[record.task_id] = record.holder


def _release_settled_leases(ctl: RunController) -> None:
    """Release every lease whose holder scope is finished. The claim is per ITEM, so the release
    is too — holding until the run ends would serialize the whole fan-out on its first item."""
    for resource, holder in list(ctl._held_leases.items()):
        scope = holder.split(":", 1)[1] if ":" in holder else ""
        if scope and not _scope_settled(ctl, scope):
            continue
        pool.release_task(resource, holder=holder)
        ctl._held_leases.pop(resource, None)


def release_held_leases(ctl: RunController) -> None:
    """Release everything this run holds. Called on the terminal write: a lease outliving its
    run strands the resource until the TTL expires, and the whole point of a named holder is
    that the holder is the one who gives it back."""
    for resource, holder in list(ctl._held_leases.items()):
        pool.release_task(resource, holder=holder)
    ctl._held_leases.clear()


def _scope_settled(ctl: RunController, scope: str) -> bool:
    """Whether every instance at or under `scope` is terminal and none is in flight."""
    if any(path == scope or path.startswith(scope + ".") for path in ctl._inflight):
        return False
    seen = False
    for path, inst in ctl.instances.items():
        if path != scope and not path.startswith(scope + "."):
            continue
        seen = True
        if inst.state not in TERMINAL_STATES:
            return False
    return seen


def _opt_metric(value: Any) -> float | None:
    """A metric, or None when there is not a number here.

    Booleans are refused: `True` would read as `1.0` and pass a `metric_pass: 1.0` gate on a field
    that was never a measurement.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
