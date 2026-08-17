"""Task-pool concurrency: leases, evented unblock, hand-offs, blueprints (TASKS-SOPS §5 R10 — S60).

PClaw already runs concurrent co-tenant sessions and batch `subagent_run` children sharing one task
pool. What the pool lacked was concurrency SEMANTICS — three of them:

* **A ranked projection over ALL tasks** — `frontier` (everything unblocked, ranked) and `next` (the
  one top task). RETIRED HERE by `PP-13`: it lives on the unified admission core as
  `admission.ready` / `admission.next_ready`, where the ordering is `admission.rank_key` and the
  leased-work exclusion is the composed `Lease` policy rather than a private `if`. The lease
  DECISION functions below are that policy's implementation and stay — only the second projection
  went, because a scheduler implemented twice decides differently exactly once and then lies.
* **TTL'd lease claims.** Without compare-and-swap leases, engine-projected tasks WILL be
  double-executed by concurrent sessions. The claim is a `os.rename`-class primitive, not a
  read-then-write: S57 measured `unlink`-based single-use failing 36 of 40 races, and a lease that
  loses a race is worse than no lease because both holders believe they own the work.
* **Evented unblock.** A completed task must unblock its dependents; a FAILED one must cascade
  `blocked(kind=dependency_failed)` carrying the blocker's reason. Before this, only
  workflow-bound tasks got unblocked (via `frontier()`), so a standalone dependent sat
  misleadingly `open` after its prerequisite died — the board lied about what was workable.

Measured before building (S60):

* **`TaskComplete` is a declared hook event that NOTHING fires.** It is in `HOOK_EVENTS`, it is
  allowlisted in `validation.py::ALLOWED_HOOK_EVENTS`, and the hook UI renders it — so a user can
  configure "when a task finishes" and get nothing. `validation.py` says so itself: "the rest are
  reserved for future firing sites and currently never trigger". This module supplies the payload
  and the edge-trigger rule that make it fireable. The plan's `TaskCreated` is NOT a shipped event
  name; adding one here would create a vocabulary the hook UI does not render, so task creation
  stays out until the event is declared where users can see it.
* **Acyclicity is ALREADY enforced server-side** by `tasks/native.py` via
  `reconcile.would_create_cycle` on both create and update. The plan's "the server-side write path
  adds the authoritative check" is already true — so this module reuses that function for the
  pool's edge-planning rather than adding a second cycle checker that could disagree with it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Sequence

# ── leases ──

#: Hard ceiling on a lease. An hour, per the plan: long enough for a real unit of work, short
#: enough that a crashed holder's task returns to the pool the same day.
MAX_LEASE_SECS = 3600

#: Default lease length. Deliberately well under the ceiling — a holder that needs longer renews,
#: which proves it is alive, whereas a long initial TTL just delays discovering that it is not.
DEFAULT_LEASE_SECS = 900


class LeaseError(str, Enum):
    """Why an acquire or release was refused. Typed because a surface switches on it.

    HELD_BY_OTHER and EXPIRED are different situations: the first means wait, the second means the
    previous holder died and this caller may take over.
    """

    HELD_BY_OTHER = "held_by_other"
    NOT_HELD = "not_held"
    WRONG_HOLDER = "wrong_holder"
    NO_HOLDER_ID = "no_holder_id"


@dataclass
class Lease:
    """An exclusive TTL'd claim on one task.

    `holder` is a session/subagent key rather than a boolean, because "is this claimed" is the less
    useful question — the board shows WHO, and a stuck claim is diagnosable only if it names
    someone.
    """

    task_id: str
    holder: str
    acquired_at: float
    ttl_seconds: int = DEFAULT_LEASE_SECS
    renewals: int = 0

    def expires_at(self) -> float:
        return self.acquired_at + min(max(1, self.ttl_seconds), MAX_LEASE_SECS)

    def expired(self, now: float) -> bool:
        return now >= self.expires_at()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "holder": self.holder,
            "acquired_at": self.acquired_at,
            "ttl_seconds": self.ttl_seconds,
            "renewals": self.renewals,
            "expires_at": self.expires_at(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Lease:
        d = d or {}
        return cls(
            task_id=str(d.get("task_id", "") or ""),
            holder=str(d.get("holder", "") or ""),
            acquired_at=float(d.get("acquired_at", 0.0) or 0.0),
            ttl_seconds=int(d.get("ttl_seconds", DEFAULT_LEASE_SECS) or DEFAULT_LEASE_SECS),
            renewals=int(d.get("renewals", 0) or 0),
        )


def acquire(
    existing: Lease | None,
    *,
    task_id: str,
    holder: str,
    now: float,
    ttl_seconds: int = DEFAULT_LEASE_SECS,
) -> tuple[Lease | None, str]:
    """Decide an acquire. Returns `(lease, error)` — the DECISION, not the write.

    Pure so the decision is testable without a filesystem, and so the write path (a flocked
    read-modify-write on the task's JSON, per the plan) has exactly one rule to implement.

    An EXPIRED lease is takeable, and the taker starts at `renewals=0`: carrying the dead holder's
    renewal count forward would make a stuck task look actively worked.

    A re-acquire by the SAME holder is a renewal rather than an error. A session that lost its
    in-memory lease after a restart would otherwise be locked out of its own task until the TTL
    ran down.
    """
    if not holder.strip():
        return None, LeaseError.NO_HOLDER_ID.value
    if existing is not None and not existing.expired(now):
        if existing.holder == holder:
            return renew(existing, holder=holder, now=now)
        return None, LeaseError.HELD_BY_OTHER.value
    return (
        Lease(task_id=task_id, holder=holder, acquired_at=now, ttl_seconds=ttl_seconds),
        "",
    )


def renew(existing: Lease, *, holder: str, now: float) -> tuple[Lease | None, str]:
    """Extend a lease the caller holds.

    Renewing an EXPIRED lease is refused: between expiry and renewal another session may already
    have taken the task, and silently extending would produce two holders who both think they
    won — the double-execution this mechanism exists to prevent.
    """
    if existing.holder != holder:
        return None, LeaseError.WRONG_HOLDER.value
    if existing.expired(now):
        return None, LeaseError.NOT_HELD.value
    return (
        Lease(
            task_id=existing.task_id,
            holder=holder,
            acquired_at=now,
            ttl_seconds=existing.ttl_seconds,
            renewals=existing.renewals + 1,
        ),
        "",
    )


def release(existing: Lease | None, *, holder: str) -> tuple[None, str]:
    """Release a lease. Only the holder may.

    A non-holder release is refused even for an EXPIRED lease: the expired case is already handled
    by acquire's takeover path, and allowing anyone to release would let one session drop another's
    live claim by racing the expiry boundary.
    """
    if existing is None:
        return None, LeaseError.NOT_HELD.value
    if existing.holder != holder:
        return None, LeaseError.WRONG_HOLDER.value
    return None, ""


def sweep_expired(leases: Iterable[Lease], now: float) -> list[str]:
    """Task ids whose leases have expired — the auto-release the diagnostics sweep performs.

    Returns ids rather than mutating, so the sweep's caller owns the writes and one code path
    remains responsible for touching task files.
    """
    return sorted(lease.task_id for lease in leases if lease.expired(now))


# ── evented unblock and dependency cascade ──


class UnblockKind(str, Enum):
    """What a blocker's terminal state does to its dependents."""

    UNBLOCK = "unblock"
    CASCADE_FAILED = "cascade_failed"
    NONE = "none"


@dataclass
class Transition:
    """One dependent's projected change from a blocker's terminal state."""

    task_id: str
    kind: UnblockKind
    blocked_kind: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "kind": self.kind.value,
            "blocked_kind": self.blocked_kind,
            "reason": self.reason,
        }


def plan_unblock(
    *,
    blocker_id: str,
    blocker_status: str,
    blocker_reason: str = "",
    dependents: dict[str, Sequence[str]],
    statuses: dict[str, str] | None = None,
) -> list[Transition]:
    """What happens to a blocker's dependents when it reaches a terminal state.

    `dependents` maps a dependent id to ALL its prerequisite ids, because a task with two
    prerequisites is not unblocked by one of them finishing. That is the bug a naive
    "completion unblocks dependents" rule ships with: work becomes visible before its other
    prerequisite is done.

    A FAILED blocker cascades `blocked(kind=dependency_failed)` carrying the blocker's reason —
    the dependent's board card should say WHY, not just that something upstream broke.
    """
    done = {"done", "complete", "completed"}
    failed = {"failed", "cancelled", "canceled"}
    status_of = dict(statuses or {})
    out: list[Transition] = []

    for dep_id in sorted(dependents):
        prereqs = list(dependents.get(dep_id) or [])
        if blocker_id not in prereqs:
            continue
        if blocker_status in failed:
            out.append(
                Transition(
                    task_id=dep_id,
                    kind=UnblockKind.CASCADE_FAILED,
                    blocked_kind="dependency_failed",
                    reason=(
                        f"{blocker_id} {blocker_status}"
                        + (f": {blocker_reason}" if blocker_reason else "")
                    ),
                )
            )
            continue
        if blocker_status not in done:
            continue
        others = [p for p in prereqs if p != blocker_id]
        unfinished = [p for p in others if status_of.get(p, "open") not in done]
        if unfinished:
            out.append(
                Transition(
                    task_id=dep_id,
                    kind=UnblockKind.NONE,
                    reason=f"still waiting on {', '.join(sorted(unfinished))}",
                )
            )
            continue
        out.append(Transition(task_id=dep_id, kind=UnblockKind.UNBLOCK))
    return out


def coalesce(transitions: Sequence[Transition]) -> tuple[list[Transition], str]:
    """Collapse a cascade burst into ONE notification, per §1's debounce rule.

    A parallel fan-in failure produces N cascade transitions at once; N alerts for one upstream
    failure is the noise that makes a user mute the channel. Returns the transitions plus the
    single summary line.
    """
    cascaded = [t for t in transitions if t.kind is UnblockKind.CASCADE_FAILED]
    if not cascaded:
        return list(transitions), ""
    if len(cascaded) == 1:
        return list(transitions), cascaded[0].reason
    return list(transitions), (
        f"{len(cascaded)} tasks blocked by one upstream failure: {cascaded[0].reason}"
    )


# ── task lifecycle events on the hook bus ──

#: The one existing task lifecycle hook event. Measured (S60): `TaskComplete` is declared in
#: `hooks.HOOK_EVENTS` and allowlisted in `validation.py`, so a user can configure a hook against
#: it — and NO call site in the repo fires it. Emitting it is what makes trigger-based automation
#: on task completion actually work.
HOOK_EVENT_TASK_COMPLETE = "TaskComplete"


def lifecycle_payload(
    *, task_id: str, title: str, status: str, run_id: str = "", node_id: str = ""
) -> dict[str, Any]:
    """The context payload for a task lifecycle hook fire.

    Uses `hooks.fire(event, context=...)`'s existing shape rather than adding hook variables: the
    hook UI renders a fixed `vars` tuple per event, so a new variable that the UI does not list is
    one no user can discover. Workflow provenance rides the context string, which every hook
    already receives.
    """
    parts = [f"task={task_id}", f"status={status}"]
    if title:
        parts.append(f"title={title[:120]}")
    if run_id:
        parts.append(f"run={run_id}")
    if node_id:
        parts.append(f"node={node_id}")
    return {"event": HOOK_EVENT_TASK_COMPLETE, "context": " ".join(parts)}


def should_fire_completion(previous_status: str, new_status: str) -> bool:
    """Whether this status change is a completion worth firing.

    Edge-triggered, not level-triggered: a save that rewrites an already-done task must not fire
    again, or an idempotent projection recompute (which §1 makes the NORMAL path) would fire a
    hook per rebuild.
    """
    done = {"done", "complete", "completed"}
    return new_status in done and previous_status not in done


# ── write-time acyclicity, reusing the shipped checker ──


def plan_edges(
    tasks: dict[str, Any], *, task_id: str, new_prereq_ids: Sequence[str]
) -> tuple[list[str], str]:
    """Whether these prerequisite edges are safe to write, via the SHIPPED cycle checker.

    Delegates to `tasks.reconcile.would_create_cycle` rather than re-deriving a DFS. Measured
    (S60): `tasks/native.py` already calls it on both create and update, so the write path is
    authoritative today — a second checker here would be a second answer, and the looser one would
    let a deadlock through.

    Returns `(cycle_path, error)`; an empty path with an empty error means the edges are safe.
    """
    try:
        from personalclaw.tasks import reconcile
    except Exception as exc:  # noqa: BLE001 - a missing checker must not read as "safe"
        return [], f"cycle check unavailable: {exc}"
    cycle = reconcile.would_create_cycle(tasks, task_id, list(new_prereq_ids))
    if cycle:
        return list(cycle), "these edges would create a dependency cycle"
    return [], ""


# ── hand-off edges (R7) ──


@dataclass
class HandOff:
    """A declared template-to-template transition.

    `hands_off_to` makes a transition a graph EDGE instead of improvisation: without it, "now run
    the bugfix SOP" is something a user has to remember, and what a user remembers is not a
    procedure.
    """

    target_def: str
    condition: str = ""
    context_fields: list[str] = field(default_factory=list)
    requires_user_request: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_def": self.target_def,
            "condition": self.condition,
            "context_fields": list(self.context_fields),
            "requires_user_request": self.requires_user_request,
        }


#: The codified edges the seed library ships, per the plan. `review → fix` carries
#: `requires_user_request` because a review that auto-proposes fixing what it just criticized reads
#: as the system arguing with itself — the plan calls this out explicitly.
SEED_HANDOFFS: dict[str, tuple[HandOff, ...]] = {
    "incident-response": (
        HandOff(
            target_def="bug-fix",
            condition="root cause identified",
            context_fields=["incident_id", "root_cause"],
        ),
    ),
    "bug-fix": (
        HandOff(
            target_def="feature-work",
            condition="fix reveals missing capability",
            context_fields=["bug_id", "gap"],
        ),
    ),
    "code-review": (
        HandOff(
            target_def="bug-fix",
            condition="review found a defect",
            context_fields=["review_id", "findings"],
            requires_user_request=True,
        ),
    ),
}


def suggest_handoffs(
    def_name: str,
    *,
    outcome: dict[str, Any] | None = None,
    user_requested: bool = False,
    edges: dict[str, tuple[HandOff, ...]] | None = None,
) -> list[HandOff]:
    """Which follow-on defs a completing run SUGGESTS.

    Suggests — never starts. A completing SOP that auto-started its successor would spend a second
    run's budget on a decision the user did not make, and the whole surfacing discipline exists to
    keep that from happening.
    """
    table = dict(edges if edges is not None else SEED_HANDOFFS)
    out: list[HandOff] = []
    for edge in table.get(def_name, ()):  # declared order is the author's ranking
        if edge.requires_user_request and not user_requested:
            continue
        out.append(edge)
    return out


def carry_context(edge: HandOff, outcome: dict[str, Any]) -> dict[str, Any]:
    """The context a hand-off carries forward — ONLY the declared fields.

    An allowlist rather than the whole outcome: passing everything would carry a previous run's
    credentials, artifacts and free text into a new run's inputs, and a hand-off is exactly the
    seam where nobody would look for that.
    """
    return {key: outcome[key] for key in edge.context_fields if key in (outcome or {})}


# ── blueprint sessions (R16 — the third surfacing mode) ──


@dataclass
class Blueprint:
    """A pre-seeded template CONVERSATION: guidance with zero engine overhead.

    Between passive injection (text, no structure) and a full run (gates, projection, budget) sits
    the cheapest possible "walk me through this". A guidance-grade SOP that needs no gates and no
    status projection should not pay for a run — and today the only alternatives are a wall of
    injected text or a whole engine invocation.
    """

    id: str
    title: str
    messages: list[dict[str, str]] = field(default_factory=list)
    open_on_first_load: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "messages": [dict(m) for m in self.messages],
            "openOnFirstLoad": self.open_on_first_load,
        }


@dataclass
class Hydration:
    """The record that one blueprint was materialized into one session.

    Exists so rehydration can be replace-not-merge and idempotent: without a record, a second
    hydration appends the steps again and the user reads step 1 twice.
    """

    template_id: str
    session_id: str
    hydrated_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "templateId": self.template_id,
            "sessionId": self.session_id,
            "hydratedAt": self.hydrated_at,
        }


def build_blueprint(
    *, def_name: str, title: str, steps: Sequence[str], digest: str = ""
) -> Blueprint:
    """Turn a def's steps into a numbered guided conversation.

    Steps become ASSISTANT messages, numbered. Assistant rather than system because the user is
    meant to read and answer them — a system message is invisible, and an invisible checklist is
    the same as no checklist.

    The digest, when present, leads as context so the first thing the user reads is why they are
    doing this rather than the first mechanical step.
    """
    messages: list[dict[str, str]] = []
    if digest.strip():
        messages.append({"role": "assistant", "text": digest.strip()})
    for index, step in enumerate(steps, start=1):
        text = str(step).strip()
        if text:
            messages.append({"role": "assistant", "text": f"{index}. {text}"})
    return Blueprint(id=f"bp-{def_name}", title=title or def_name, messages=messages)


def plan_hydration(
    blueprint: Blueprint, *, session_id: str, now: float, existing: Hydration | None = None
) -> tuple[list[dict[str, str]], Hydration, bool]:
    """What to write into the session, plus the hydration record.

    REPLACE, not merge — and re-hydrating the same blueprint into the same session is a no-op
    (`replaced=False`), because the defensive case is a client that retries the open. A merge would
    duplicate every step, and duplicated instructions read as a system that has lost its place.
    """
    if (
        existing is not None
        and existing.template_id == blueprint.id
        and (existing.session_id == session_id)
    ):
        return [], existing, False
    record = Hydration(template_id=blueprint.id, session_id=session_id, hydrated_at=now)
    return [dict(m) for m in blueprint.messages], record, True


class SurfaceRoute(str, Enum):
    """Which of the three modes a def should use."""

    PASSIVE = "passive"
    BLUEPRINT = "blueprint"
    RUN = "run"


def route(
    *, surface_mode: str, has_gates: bool, max_turns: int, has_schema: bool, guided: bool = False
) -> SurfaceRoute:
    """Pick the mode for one def.

    The structural heuristic the plan states, with blueprint slotted in: a def that needs
    gates, multi-turn stages or a schema is a RUN (it needs the engine); a def explicitly
    marked guided is a BLUEPRINT; everything else is passive text.

    Gates decide FIRST, before `surface_mode`. A def with an approval gate CANNOT be a blueprint —
    a blueprint has no engine, so there is nothing to pause, and rendering a gate as a numbered
    message would show the user an approval that approves nothing.

    Measured (S61): short-circuiting on `off` BEFORE the structural check reported a gated def as
    PASSIVE, which tells a caller it may be injected as text and silently drops the gate. This
    function answers "what IS this def" — whether it may surface is S58's `veto_reasons`, which is a
    separate question with a separate answer. What `off` does govern is BLUEPRINT: materializing a
    guided conversation for a def the user switched off would put it on screen anyway.
    """
    if has_gates or has_schema or max_turns > 1:
        return SurfaceRoute.RUN
    if guided and surface_mode != "off":
        return SurfaceRoute.BLUEPRINT
    return SurfaceRoute.PASSIVE


# ── the lease WRITE path (S61d) ──


def leases_dir() -> Path:
    """Where lease records live.

    A SIDECAR file per task under `config_dir()/task_leases/`, not a field on `Task`. Three reasons,
    in order of how much they'd hurt:

    * A lease is ephemeral and contended; the task JSON is the durable entity. Putting a
      once-a-minute renewal into the entity file means every renewal rewrites the task, and every
      rewrite races a concurrent edit to a field that has nothing to do with claiming.
    * `Task` is the SHARED model across every task provider. A native-only concurrency concept on it
      would make every provider's task carry a field only one of them can honour.
    * A sidecar can be deleted to force-release without touching user data.
    """
    from personalclaw.config.loader import config_dir

    return Path(config_dir()) / "task_leases"


def _lease_path(task_id: str) -> Path:
    """The lease file for a task id, with the id sanitized.

    A task id reaches this from an HTTP path. `t-<8hex>` is the native shape, but a provider id is
    not a trust boundary, so anything outside the safe set is replaced rather than trusted to stay
    inside the directory.
    """
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", task_id or "unknown")
    return leases_dir() / f"{safe}.json"


def read_lease(task_id: str) -> Lease | None:
    """The current lease for a task, or None.

    An unreadable or malformed file reads as NO LEASE. Degrading to unclaimed risks a double-claim;
    degrading to claimed would strand the task permanently with no holder to release it — and a task
    nobody can ever work is worse than one two sessions might briefly contend for, because the
    contention resolves and the strand does not.
    """
    path = _lease_path(task_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("holder"):
        return None
    return Lease.from_dict(data)


def claim_task(
    task_id: str, *, holder: str, now: float, ttl_seconds: int | None = None
) -> tuple[Lease | None, str]:
    """Acquire or renew a lease, under an exclusive lock. Returns `(lease, error)`.

    The read-modify-write is wrapped in `single_flight` — the established flock primitive — because
    per-entity JSON files have no transactions, so "read the lease, decide, write the lease" is
    otherwise a race between the read and the write. A LOSER of the lock is told the task is held
    rather than proceeding: single-flight means don't double-run, and a caller that ignored the miss
    would be doing exactly the double-claim the lock exists to prevent.

    That lock covers THREADS as well as processes (flock is per open file description and
    `single_flight` opens a fresh one per call), which is the property `PP-12`'s `Lease` admission
    policy rests on: the engine fans out in-process with `asyncio.create_task`, so a cross-process-
    only claim would not cap the fan-out shape that actually occurs. Measured both ways — 16 threads
    on one resource yield exactly one holder with this wrapper, and 3 to 15 holders without it.

    The decision itself is `acquire`, unchanged — this function is only the durability around it, so
    there is one rule and one place it is applied.
    """
    from personalclaw.concurrency import single_flight

    if ttl_seconds is None:
        # From config, not the constant: `workflows.lease_ttl_secs` is live-editable (S61k).
        from personalclaw.workflows.settings import lease_ttl_secs

        ttl_seconds = lease_ttl_secs()
    with single_flight(f"task-lease:{task_id}") as acquired:
        if not acquired:
            return None, LeaseError.HELD_BY_OTHER.value
        lease, error = acquire(
            read_lease(task_id),
            task_id=task_id,
            holder=holder,
            now=now,
            ttl_seconds=ttl_seconds,
        )
        if lease is None:
            return None, error
        _write_lease(lease)
        return lease, ""


def release_task(task_id: str, *, holder: str) -> tuple[bool, str]:
    """Release a lease the caller holds. Returns `(released, error)`.

    Under the same lock as the claim: an unlocked release could delete a lease another caller took
    microseconds earlier at the expiry boundary.
    """
    from personalclaw.concurrency import single_flight

    with single_flight(f"task-lease:{task_id}") as acquired:
        if not acquired:
            return False, LeaseError.HELD_BY_OTHER.value
        _none, error = release(read_lease(task_id), holder=holder)
        if error:
            return False, error
        _lease_path(task_id).unlink(missing_ok=True)
        return True, ""


def _write_lease(lease: Lease) -> None:
    """Persist a lease atomically, through the store's writer.

    Reuses `store.atomic_write` rather than a bare `write_text`: a torn lease file reads as no lease
    (see `read_lease`), which silently drops a live claim.
    """
    from personalclaw.workflows import store as _store

    path = _lease_path(lease.task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    _store.atomic_write(path, json.dumps(lease.to_dict(), indent=2))


def sweep_task_leases(now: float) -> list[str]:
    """Delete every expired lease file and return the freed task ids.

    The auto-release the diagnostics sweep performs. Reads whatever is on disk rather than taking a
    list, because the point is to find claims whose HOLDER is gone — a caller that could enumerate
    live leases would not need the sweep.
    """
    freed: list[str] = []
    root = leases_dir()
    if not root.is_dir():
        return freed
    for path in sorted(root.glob("*.json")):
        try:
            lease = Lease.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            # An unparseable lease file is already "no lease" to every reader, so removing it is
            # cleanup rather than a decision.
            path.unlink(missing_ok=True)
            continue
        if lease.expired(now):
            path.unlink(missing_ok=True)
            freed.append(lease.task_id)
    return freed
