"""The journal — the resume cache and the Run Ledger, as the workflow engine flavours them.

Two jobs that share one append-only file, because they are the same data read two ways.

**Resume cache.** After a crash or a rewind, work already done must not be redone. A
cache entry is keyed by `(instance_path, epoch, inputs_hash, spec_region_hash)` — all
four, and each one earns its place:

* `epoch` — a rewind bumps it, so a replayed region from a superseded epoch can never be
  mistaken for a hit on the current one.
* `inputs_hash` — if an upstream output changed, this node's inputs changed, and the
  cached output is stale even though the node itself was not edited.
* `spec_region_hash` — if the node's own config was edited, the cached output came from
  a different prompt. Without this, editing a prompt mid-run and resuming would silently
  serve the pre-edit answer.

A hit emits `step_cached` (WF2-A1) rather than staying invisible: "did my edit actually
re-run anything?" is the first question a user asks after a mid-flight edit, and the
answer has to come from the ledger, not from reading logs.

**Run Ledger.** The event subset the Learning Flywheel's template-refiner reads. These
are emission REQUIREMENTS, not a nice-to-have: a downstream evaluator that wants to know
which model a step used, what it cost, and why it failed is starved if the engine only
journals free text. `resolved_prompt_ref` points at the fully-resolved post-binding
prompt so a trajectory can be replayed — the acceptance bar is that prompt → tool calls
→ output is reconstructable from ledger events alone.

Everything written here passes through `redact()` first. A journal is read back by the
flywheel, shipped in bug reports, and rendered in a UI; a credential that reaches it is
a credential leaked to all three.

**What lives here and what does not (PP-4).** The mechanism — sequencing, stamping, redaction,
the `events.jsonl` mirror, the oversize/binary spill, the event vocabulary — is
:mod:`personalclaw.ledger`, because none of it is workflow-shaped and a second producer must
speak the same words rather than invent a dialect. What stays is the WORKFLOW FLAVOUR, and the
line between them is a question about node identity: everything below needs a node path, an
`epoch`, an `InstanceState` or a `Failure` to mean anything.

* the typed emitters — their arguments are engine types;
* the resume cache's KEY and its lookup (`CacheKey`, `lookup`, `invalidate_prefix`) — an epoch is
  a rewind counter and `SUCCESS_STATES` is an engine enum. The generic half, folding the file into
  a key→record map, is the writer's, because that same pass recovers `seq`;
* `spec_region_hash`, which knows that `children`/`body`/`cases`/`default` are a node's children.

`LEDGER_KINDS` and every kind constant are re-exported unchanged, so the drift tests that assert
the engine still emits all of them keep binding to this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

# The vocabulary and the machinery are re-exported wholesale: 26 modules read these names off THIS
# module, and the drift tests assert `journal.LEDGER_KINDS` by that path.
from personalclaw.ledger import (  # noqa: F401 — re-exported for this module's importers
    BREAKER_TRIP,
    BUFFER_SEAL,
    CARRYOVER,
    CASCADE_BLOCKED,
    CHILD_RUN_ATTACH,
    CLOCK_READ,
    CONFIRMATION_PENDING,
    CONFIRMATION_RESOLVED,
    CONSULTED,
    CRYSTALLIZED,
    DECISION,
    DELAY_CLAMPED,
    EFFECT,
    EVENTS_FILE,
    GATE_CRITERION,
    GATE_REJECTED,
    GATE_RESOLVED,
    GATE_REVISED,
    HANDOFF,
    INPUTS_STALE,
    ITEMS_COLLECTED,
    ITERATION,
    JOURNAL_FILE,
    JUDGE_DIVERGENCE,
    JUDGE_VERDICT,
    LEDGER_KINDS,
    MAX_INLINE_OUTPUT_BYTES,
    MUTATION_REJECTED,
    OUTCOME_RESOLVED,
    PENDING_OUTCOME,
    RUN_ABANDONED,
    RUN_FINISHED,
    RUN_STARTED,
    SEEN_SET,
    STEERING,
    STEP_ATTEMPT,
    STEP_CACHED,
    STEP_COMPLETED,
    STEP_ESCALATED,
    STEP_FAILED,
    STEP_SCOPE,
    STEP_SKIPPED,
    STEP_STARTED,
    TASK_MATERIALIZED,
    TASK_VERIFIED,
    USER_EDITED_MID_FLIGHT,
    WATCHER_REAPED,
    WORKSPACE_PROVISIONED,
    WORKSPACE_TEARDOWN,
    LedgerStore,
    LedgerWriter,
    hash_value,
    is_binary_payload,
    outcomes,
    reader,
    redact,
)
from personalclaw.workflows import store
from personalclaw.workflows.models import Failure, InstanceState

# ── hashing ──────────────────────────────────────────────────────────────────


def inputs_hash(resolved: dict[str, Any]) -> str:
    """Hash of a node's fully-resolved inputs — what actually reached the node, not what
    the spec said. An upstream change shows up here even when this node was untouched."""
    return hash_value(resolved)


def spec_region_hash(node_dict: dict[str, Any]) -> str:
    """Hash of the node's own spec region, children EXCLUDED.

    Children are excluded deliberately: editing a child must invalidate the child, not
    silently re-run its already-completed parent container.
    """
    trimmed = {
        k: v
        for k, v in (node_dict or {}).items()
        if k not in ("children", "body", "cases", "default")
    }
    return hash_value(trimmed)


@dataclass(frozen=True)
class CacheKey:
    """All four fields participate — see the module docstring for why each is load-
    bearing. Dropping any one of them produces a cache that serves stale answers."""

    path: str
    epoch: int
    inputs_hash: str
    spec_hash: str

    def to_str(self) -> str:
        return f"{self.path}|{self.epoch}|{self.inputs_hash}|{self.spec_hash}"


# ── journal writer ───────────────────────────────────────────────────────────


@dataclass
class Journal(LedgerWriter):
    """The workflow engine's ledger: the shared writer plus the engine's typed emitters.

    Every method below exists so a caller cannot journal a step by hand — a free-text write is how
    a required field goes missing, and the refiner discovers it a week later as a starved query.
    """

    #: The run store owns `runs/<id>/`, so it is what this ledger appends through.
    _store: ClassVar[LedgerStore] = store  # type: ignore[assignment]

    # ── step lifecycle ──

    def step_started(
        self, path: str, node_id: str, *, epoch: int, lane: str, resolved_prompt_ref: str = ""
    ) -> None:
        self.write(
            STEP_STARTED,
            instance_path=path,
            node_id=node_id,
            epoch=epoch,
            lane=lane,
            resolved_prompt_ref=resolved_prompt_ref,
        )

    def step_completed(
        self,
        path: str,
        node_id: str,
        *,
        epoch: int,
        cache_key: str,
        state: InstanceState,
        duration_secs: float = 0.0,
        tokens: int = 0,
        retries: int = 0,
        model: str = "",
        provider: str = "",
        cost_usd: float = 0.0,
        degraded_reason: str = "",
        resolved_prompt_ref: str = "",
        output_ref: str = "",
    ) -> None:
        """The ledger's primary record. Every field here is required by the flywheel's
        refiner (§5 Run Ledger) — `cost_usd` is backend-authoritative with a rate-table
        floor, never a frontend estimate."""
        self.write(
            STEP_COMPLETED,
            instance_path=path,
            node_id=node_id,
            epoch=epoch,
            cache_key=cache_key,
            state=state.value,
            duration_secs=round(float(duration_secs), 3),
            tokens=int(tokens),
            retries=int(retries),
            model=model,
            provider=provider,
            cost_usd=round(float(cost_usd), 6),
            degraded_reason=degraded_reason,
            resolved_prompt_ref=resolved_prompt_ref,
            output_ref=output_ref,
        )

    def step_failed(
        self,
        path: str,
        node_id: str,
        *,
        epoch: int,
        failure: Failure,
        attempt: int = 0,
        retries_exhausted: bool = False,
        signature: dict[str, Any] | None = None,
    ) -> None:
        self.write(
            STEP_FAILED,
            instance_path=path,
            node_id=node_id,
            epoch=epoch,
            error=failure.cause_plain,
            failure=failure.to_dict(),
            failure_signature=dict(signature or {}),
            attempt=int(attempt),
            retries_exhausted=bool(retries_exhausted),
        )

    def step_skipped(self, path: str, node_id: str, *, epoch: int, actor: str = "engine") -> None:
        """`actor` distinguishes a user's deliberate skip from the engine routing around
        an untaken branch — the refiner must not read the latter as a rejection."""
        self.write(STEP_SKIPPED, instance_path=path, node_id=node_id, epoch=epoch, actor=actor)

    def step_cached(
        self,
        path: str,
        node_id: str,
        *,
        epoch: int,
        cache_key: str,
        state: InstanceState,
        output_ref: str = "",
    ) -> None:
        """A resume/rewind cache hit (WF2-A1). Emitted so a user can confirm from the
        ledger that an edit re-ran exactly the binding closure and nothing else."""
        self.write(
            STEP_CACHED,
            instance_path=path,
            node_id=node_id,
            epoch=epoch,
            cache_key=cache_key,
            state=state.value,
            output_ref=output_ref,
            cached=True,
        )

    def iteration(
        self,
        path: str,
        node_id: str,
        *,
        iteration: int,
        outcome: str,
        error_signature: str = "",
        tokens: int = 0,
    ) -> None:
        """Feeds the deterministic circuit breaker: N identical `error_signature`s in a
        row is a thrash, detectable at zero LLM cost."""
        self.write(
            ITERATION,
            instance_path=path,
            node_id=node_id,
            iteration=int(iteration),
            outcome=outcome,
            error_signature=error_signature,
            tokens=int(tokens),
        )

    def run_started(
        self,
        workflow_name: str,
        *,
        inputs: dict[str, Any],
        spec_version: int,
        resumed: bool = False,
    ) -> None:
        self.write(
            RUN_STARTED,
            workflow_name=workflow_name,
            inputs=dict(inputs or {}),
            spec_version=spec_version,
            resumed=resumed,
        )

    def run_finished(
        self, status: str, *, elapsed_secs: float = 0.0, tokens: int = 0, error: str = ""
    ) -> None:
        self.write(
            RUN_FINISHED,
            status=status,
            elapsed_secs=round(float(elapsed_secs), 3),
            tokens=int(tokens),
            error=error,
        )

    def run_abandoned(self, at_node_id: str, *, elapsed_secs: float = 0.0) -> None:
        self.write(RUN_ABANDONED, at_node_id=at_node_id, elapsed_secs=round(elapsed_secs, 3))

    def user_edited_mid_flight(self, ops: list[dict[str, Any]]) -> None:
        """The structured mutation batch, not a diff blob — the refiner needs to know
        WHAT kind of correction a human made, which a textual diff destroys."""
        self.write(USER_EDITED_MID_FLIGHT, ops=list(ops or []))

    def consulted(self, path: str, node_id: str, *, ref: str) -> None:
        self.write(CONSULTED, instance_path=path, node_id=node_id, ref=ref)

    def handoff(
        self, path: str, node_id: str, *, epoch: int, iteration: int, handoff: dict
    ) -> None:
        """One iteration's handoff to the next (WF2-R6).

        Journaled, not held in memory: a rewind to iteration 3 must replay iteration 2's handoff,
        and an in-memory one would be lost — leaving the replayed iteration to reconstruct from a
        transcript, which is the summarization failure the handoff exists to avoid.
        """
        self.write(
            HANDOFF,
            instance_path=path,
            node_id=node_id,
            epoch=epoch,
            iteration=iteration,
            **handoff,
        )

    def carryover(
        self, path: str, node_id: str, *, epoch: int, iteration: int, buckets: dict
    ) -> None:
        """The typed facts that survive a session reset."""
        self.write(
            CARRYOVER,
            instance_path=path,
            node_id=node_id,
            epoch=epoch,
            iteration=iteration,
            **buckets,
        )

    def decision(self, path: str, node_id: str, *, epoch: int, decision: dict) -> None:
        """A settled choice and why (WF2-R6).

        The rejected alternatives are the point: compaction keeps "we used X" and drops "we
        rejected Y because", so a resumed run re-proposes Y with nothing in its context saying it
        was already dismissed.
        """
        self.write(DECISION, instance_path=path, node_id=node_id, epoch=epoch, **decision)

    def items_collected(
        self,
        path: str,
        node_id: str,
        *,
        epoch: int,
        outcome: str,
        failures: list[dict[str, Any]],
    ) -> None:
        """A `collect` fan-out's per-item failures, once it is terminal (WV-13).

        `failures` is the DOCUMENTED shape a later binding would surface: one entry per failed
        instance inside the fan-out, each carrying `item_index` (so a reader groups by item),
        `item_label` (the human-readable "auth.py"), `instance_path`, `node_id` and the typed
        `failure_class`/`cause` off the instance's `Failure`. Redacted like every other journal
        payload — a cause string can quote a prompt.
        """
        self.write(
            ITEMS_COLLECTED,
            instance_path=path,
            node_id=node_id,
            epoch=epoch,
            outcome=outcome,
            failed_items=len(failures),
            failures=failures,
        )

    def pending_outcome(
        self,
        path: str,
        node_id: str,
        *,
        epoch: int,
        subject: str,
        metric: str,
        horizon_secs: float,
        baseline: float,
    ) -> dict[str, Any]:
        """Journal a decision's OPEN QUESTION at decision time (LEARN-R18).

        The bet, not the answer: this run decided `subject`, and whether that decision was
        right can only be measured later by reading `metric` after `horizon_secs` have
        elapsed and comparing to `baseline`. Returns the written record so the caller can
        note its `event_id` — the key the resolver's `outcome_resolved` cites back, making a
        second curator tick idempotent.

        The WORKFLOW-SHAPED adapter over the general facility (PP-9): it contributes
        `instance_path`/`node_id`/`epoch` and the `decision` producer, and nothing else. A
        non-decision producer calls `open_outcome` directly rather than pretending to be a node.
        """
        return self.open_outcome(
            producer=outcomes.PRODUCER_DECISION,
            subject=subject,
            metric=metric,
            horizon_secs=horizon_secs,
            baseline=baseline,
            instance_path=path,
            node_id=node_id,
            epoch=epoch,
        )

    def outcome_resolved(
        self,
        path: str,
        node_id: str,
        *,
        pending_event_id: str,
        subject: str,
        metric: str,
        baseline: float,
        measured: float | None,
        score: float,
        resolution: str,
        producer: str = outcomes.PRODUCER_DECISION,
    ) -> dict[str, Any]:
        """Journal the ground-truth resolution of a `pending_outcome` (LEARN-R18).

        `resolution` is "measured" when `metric` was readable after the horizon and
        "inconclusive" when it was not — the latter decays faster, because an outcome we
        could not measure is weaker evidence than one we could. `pending_event_id` links
        back to the open question so the resolver never re-resolves the same one.

        The workflow-shaped adapter over `resolve_outcome` (PP-9): the resolver hands back the
        `producer` it read off the question, so a resolution never re-labels the bet it closes.
        """
        return self.resolve_outcome(
            pending_event_id=pending_event_id,
            producer=producer,
            subject=subject,
            metric=metric,
            baseline=baseline,
            measured=measured,
            score=score,
            resolution=resolution,
            instance_path=path,
            node_id=node_id,
        )

    def child_run_attach(self, parent_run_id: str, child_run_id: str, node_id: str) -> None:
        self.write(
            CHILD_RUN_ATTACH,
            parent_run_id=parent_run_id,
            child_run_id=child_run_id,
            node_id=node_id,
        )

    def effect(
        self,
        path: str,
        *,
        idempotency_key: str,
        effect_status: str,
        epoch: int = 0,
        node_id: str = "",
        provider: str = "",
        output_id: str = "",
        compensation_ref: str = "",
        detail: str = "",
    ) -> None:
        """One effect-lifecycle event (WF2-R1). ATTEMPTED is written BEFORE dispatch, so
        a crash between attempt and outcome leaves evidence the effect MAY have fired —
        "unknown, possibly fired" and "never fired" demand different recovery."""
        self.write(
            EFFECT,
            instance_path=path,
            idempotency_key=idempotency_key,
            effect_status=effect_status,
            epoch=int(epoch),
            node_id=node_id,
            provider=provider,
            output_id=output_id,
            compensation_ref=compensation_ref,
            detail=detail,
        )

    def clock_read(
        self, path: str, node_id: str, *, epoch: int, clock: float, wake_at: float = 0.0
    ) -> None:
        """One load-bearing wall-clock read the run resolved a parked node against (PP-6).

        `frontier()` is pure; the controller's `_wake_due_nodes` is the one place a run reads the
        wall clock to make a scheduling decision — a `wait` deadline or a `gate` timeout crossing
        wall time. `clock` is the value it read and `wake_at` the deadline that value crossed. This
        is the missing third of the nondeterminism envelope (provider responses are spilled by
        `output_ref`, the resolved prompt by `_store_prompt`): journaled so a replay can substitute
        a recorded clock and resolve the same node at the same point in the trajectory rather than
        against a live clock that would never match.
        """
        self.write(
            CLOCK_READ,
            instance_path=path,
            node_id=node_id,
            epoch=epoch,
            clock=round(float(clock), 6),
            wake_at=round(float(wake_at), 6),
        )

    # ── resume cache ──

    def lookup(self, key: CacheKey) -> dict[str, Any] | None:
        """A cache hit, or None. Only SUCCESS states are served from cache: replaying a
        cached FAILURE would make a transient error permanent across a resume."""
        rec = self._load_cache().get(key.to_str())
        if not rec:
            return None
        try:
            state = InstanceState(str(rec.get("state", "")))
        except ValueError:
            return None
        from personalclaw.workflows.models import SUCCESS_STATES

        return rec if state in SUCCESS_STATES else None

    def invalidate_prefix(self, path_prefix: str) -> int:
        """Drop cache entries at or under a path — the in-memory half of a rewind.

        The journal FILE is never rewritten: it is append-only by contract, and the
        archival of a rewound region is Slice 4's job. This only stops the current
        process serving hits from the invalidated region.
        """
        cache = self._load_cache()
        doomed = [k for k in cache if k.split("|", 1)[0].startswith(path_prefix)]
        for k in doomed:
            cache.pop(k, None)
        return len(doomed)

    # ── TASKS-SOPS projection events (S61e) ──

    def task_materialized(
        self,
        path: str,
        node_id: str,
        *,
        task_id: str,
        fingerprint: str = "",
        refreshed: bool = False,
    ) -> None:
        """A leaf node became (or refreshed) a Task.

        `refreshed` distinguishes a rewind's dedup-merge from a first materialization. Without it a
        reader counting `task_materialized` events over-counts the run's output every time it was
        rewound — and §1 makes idempotent recompute the NORMAL path, so that is not a rare case.
        """
        self.write(
            TASK_MATERIALIZED,
            instance_path=path,
            node_id=node_id,
            task_id=task_id,
            fingerprint=fingerprint,
            refreshed=bool(refreshed),
        )

    def confirmation_pending(
        self, path: str, node_id: str, *, confirmation_id: str, kind: str = "approval"
    ) -> None:
        """A gate is waiting on a human. Paired with `confirmation_resolved` by `confirmation_id`.

        Recorded when the gate STARTS waiting, not only when it is answered: a run that sat
        unanswered for a week and one answered instantly are indistinguishable from the
        resolution alone, and the wait is the number a user cares about.
        """
        self.write(
            CONFIRMATION_PENDING,
            instance_path=path,
            node_id=node_id,
            confirmation_id=confirmation_id,
            confirmation_kind=kind,
        )

    def confirmation_resolved(
        self,
        path: str,
        node_id: str,
        *,
        confirmation_id: str,
        verb: str,
        approved: bool,
        resolved_by: str = "",
    ) -> None:
        """A human answered. Carries BOTH the verb and the boolean.

        The boolean is what the engine acted on; the verb is what the user chose. They cannot
        disagree today, but recording only the boolean would make an audit unable to distinguish a
        reject from an expiry auto-reject — which is exactly the distinction §4's per-type expiry
        policy exists to create.
        """
        self.write(
            CONFIRMATION_RESOLVED,
            instance_path=path,
            node_id=node_id,
            confirmation_id=confirmation_id,
            verb=verb,
            approved=bool(approved),
            resolved_by=resolved_by or "unknown",
        )

    def task_verified(
        self,
        path: str,
        node_id: str,
        *,
        task_id: str,
        passed: bool | None,
        criterion: str = "",
    ) -> None:
        """A done-criterion ran and the engine flipped (or withheld) the task's done state.

        `passed` is the TRISTATE, not a boolean: `None` means the check could not run (a missing
        binary, a timeout, a safety-screen refusal). Recorded as a separate `unrunnable` flag rather
        than collapsed to False — measured (S61h), `bool(None)` is `False`, which would report "your
        check failed" for a criterion that never executed and send the user to debug their code when
        the problem is their environment. §1 projects the two to DIFFERENT blocked kinds
        (`needs_input` vs `capability`) precisely because they need different fixes.

        `criterion` is recorded because "verification failed" without naming what was checked is a
        finding a user cannot act on, and the criterion is the def author's text.
        """
        self.write(
            TASK_VERIFIED,
            instance_path=path,
            node_id=node_id,
            task_id=task_id,
            passed=passed is True,
            unrunnable=passed is None,
            criterion=criterion,
        )

    def cascade_blocked(
        self, path: str, node_id: str, *, blocked_task_ids: list[str], cause: str
    ) -> None:
        """An upstream failure blocked dependents. ONE event for the whole cascade.

        The blocked ids ride as a list rather than one event each: §1 debounces the
        notification, and a ledger recording N events for one upstream failure would make
        the run look like it failed N times.
        """
        self.write(
            CASCADE_BLOCKED,
            instance_path=path,
            node_id=node_id,
            blocked_task_ids=list(blocked_task_ids),
            cause=cause,
        )

    def workspace_provisioned(self, outcome: dict[str, Any]) -> None:
        """What the run's workspace ended up being (WORK-CONTAINERS §4.1).

        Recorded even for a REFUSED or degraded workspace — especially then. A run that silently
        fell back from `worktree` to a scratch dir because git was missing behaves differently
        from one that got the isolation it asked for, and without the record the difference is
        invisible to the cockpit and to a refiner reading the ledger.

        The outcome dict comes from `provisioning.Provisioned.to_dict`, whose env block carries
        presence flags only — a journal is read by the flywheel and shipped in bug reports.
        """
        self.write(WORKSPACE_PROVISIONED, workspace=redact(outcome))

    def workspace_teardown(self, outcome: dict[str, Any], *, reason: str = "") -> None:
        """What teardown did before the workspace was deleted.

        `reason` names the deletion path (`delete` vs `retention`), because the two arrive at the
        same removal for different causes and a user asking "where did my worktree go" needs the
        cause, not just the fact.
        """
        self.write(WORKSPACE_TEARDOWN, workspace=redact(outcome), reason=reason)


# ── ledger queries ───────────────────────────────────────────────────────────


def ledger(run_id: str, *, kinds: set[str] | None = None) -> list[dict[str, Any]]:
    """Read the ledger, optionally filtered. Pass-rate, failure distribution and
    latency percentiles are queries over this — not a separate metrics store."""
    return reader.read_events(store, run_id, kinds=kinds)


def journal_records(run_id: str, *, kinds: set[str] | None = None) -> list[dict[str, Any]]:
    """Read `journal.jsonl` — for the kinds the `events.jsonl` mirror never carries.

    `run_started` and `run_finished` are outside :data:`LEDGER_KINDS`, so :func:`ledger` cannot
    see the run's inputs or its final status. This is the engine-side binding of
    :func:`personalclaw.ledger.reader.read_journal`: it exists so a consumer asks the facade
    instead of importing the run store and passing it in itself, which is how the store binding
    grows a second copy.
    """
    return reader.read_journal(store, run_id, kinds=kinds)


def run_totals(run_id: str) -> dict[str, Any]:
    """Aggregate a run's ledger into the counters the run row carries.

    Budgets are PRE-CHARGED from this on resume (WF2-R4 invariant #1): a resumed run
    must inherit what it already spent, or a crash loop becomes an unbounded spend.
    """
    return reader.run_totals(store, run_id)
