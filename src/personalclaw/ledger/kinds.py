"""The ledger's event vocabulary — one registry, shared by every producer.

These names were born inside the workflow engine, but nothing about them is workflow-shaped: a
`step_started` is "a unit of work began", a `judge_verdict` is "something assessed something", a
`breaker_trip` is "a loop was cut off". The vocabulary lives here rather than in
`workflows/journal.py` so a second producer can speak it without importing the engine — which is
the only alternative that keeps ONE vocabulary. A loop that emitted its own `cycle_finished`
alongside `step_completed` would not be a second producer of the ledger; it would be a fifth
dialect for a reader to reconcile, which is the failure the extraction exists to prevent.

Adding a kind is therefore additive here and nowhere else: the constant, its comment saying what
question the kind answers, and (if a refiner must read it) an entry in :data:`LEDGER_KINDS`.
"""

from __future__ import annotations

STEP_STARTED = "step_started"
STEP_COMPLETED = "step_completed"
STEP_FAILED = "step_failed"
STEP_SKIPPED = "step_skipped"
STEP_CACHED = "step_cached"
#: One try at one node — typed, so a retry gets actionable feedback rather than prose,
#: and so the flywheel can later see WHICH corrections actually worked (WF2-R4).
STEP_ATTEMPT = "step_attempt"
#: Retries spent or the breaker tripped — a typed decision record, not a bare failure.
STEP_ESCALATED = "step_escalated"
GATE_REJECTED = "gate_rejected"
GATE_CRITERION = "gate_criterion"
#: A human answered a waiting gate (WF2-R7). Journaled with the answer so a later reader
#: knows WHO decided what, not merely that the run continued.
GATE_RESOLVED = "gate_resolved"
#: A reviewer answered a gate with `revise{step_ref, comment}` (UP): one step was patched and the
#: gate re-asks. A DISTINCT kind from `gate_resolved` on purpose — a revise is neither an approval
#: nor a rejection, and folding it into the resolved event would make `introspection.gate_stats`
#: count it as a said-no, reporting a reviewer who asked for a wording change as one who declined
#: the work.
GATE_REVISED = "gate_revised"
EFFECT = "effect"
#: A node wrote outside its declared `allowed_write_paths` (WF2-R19). Ledgered whether
#: the mode was warn or reject — an escape a `warn` run continued past still has to be
#: findable afterwards.
STEP_SCOPE = "step_scope_violation"
ITERATION = "iteration"
USER_EDITED_MID_FLIGHT = "user_edited_mid_flight"
#: A queued batch failed its TOCTOU re-verify (state moved under the preview). Journaled
#: because a silently dropped mutation is indistinguishable from an applied one.
MUTATION_REJECTED = "mutation_rejected"
#: A done node whose inputs changed but which is NOT being re-run (WF2-R2 #3) — better a
#: visible flag than an answer computed from inputs that no longer exist.
INPUTS_STALE = "inputs_stale"
CONSULTED = "consulted"
CHILD_RUN_ATTACH = "child_run_attach"
RUN_ABANDONED = "run_abandoned"
CRYSTALLIZED = "crystallized"
#: Context-lifecycle records (WF2-R6). Journaled rather than held in memory so a rewind or fork
#: REPLAYS them — a handoff reconstructed after the fact is a summary, which is the thing it exists
#: to replace.
HANDOFF = "handoff"
CARRYOVER = "carryover"
DECISION = "decision"
RUN_STARTED = "run_started"
RUN_FINISHED = "run_finished"
#: LOOPS-EVOLUTION R4/R14: the middleware's own observable events. `breaker_trip` and
#: `steering` are ledger kinds because a refiner needs to know a run was nudged or
#: steered — a verdict that followed a human's mid-run instruction is not evidence about
#: the template, and without the event there is no way to tell the two apart.
BREAKER_TRIP = "breaker_trip"
STEERING = "steering"
#: WV-13: one `foreach` with `on_item_error: collect` finished, and these items failed. A ledger
#: kind because COLLECT's whole contract is "run everything, then hand me the failures" — the
#: failures ARE the deliverable, and a fan-out that fails the run without saying which of its
#: fifty items broke has collected nothing. One record per fan-out per epoch, not one per item:
#: the point is the set, and a reader that had to reassemble it from fifty `step_failed` records
#: would have to know the fan-out's item-path shape to do it.
ITEMS_COLLECTED = "items_collected"
JUDGE_VERDICT = "judge_verdict"
JUDGE_DIVERGENCE = "judge_divergence"
#: KNOWLEDGE-SYNTHESIS §4: long-run watcher mechanics. `watcher_reaped` is a ledger kind
#: because a watcher stopped early produced fewer cycles than its cadence implies, and a
#: refiner reading cycle counts without it would conclude the template under-performed.
#: `seen_set` and `buffer_seal` are what make a months-long run's cost auditable — the whole
#: point of the seen-set is invisible without a record of what it suppressed.
WATCHER_REAPED = "watcher_reaped"
SEEN_SET = "seen_set"
BUFFER_SEAL = "buffer_seal"
DELAY_CLAMPED = "delay_clamped"

#: PP-6: one entry of the nondeterminism envelope a run depended on. A run's decision path is
#: reproducible only if every non-deterministic input it read is journaled — provider responses
#: are already spilled by `output_ref` and the resolved prompt is already stored by `_store_prompt`,
#: which leaves the WALL CLOCK. `frontier()` is pure and reads no clock; the controller does, in
#: `_wake_due_nodes`, where a `wait`/`gate` deadline crossing wall time is what advances the run.
#: That read is journaled here — the value the run resolved a parked node against — so a replay can
#: substitute a recorded clock and reach the same node in the same order. A ledger kind rather than
#: a side file because it IS run-history a replayer reads back, and the extraction's rule is that a
#: reader reconciles ONE vocabulary; a parallel clock channel would be a second dialect.
CLOCK_READ = "clock_read"

#: AMBIENT-SURFACES §2.3 (AS-2): one chatless tile refresh. A ledger kind because the refresh
#: is a LEDGER-WEIGHT fire — it deliberately spawns no run directory, so this row is the only
#: record that it happened, what it cost (near-zero: a pure render transform), and which of its
#: data sources answered. The tile header reads exactly this row for its freshness stamp and its
#: per-source ok/error dots, which is why the per-node outcomes ride ON the row rather than in a
#: parallel status file: a second channel would let "the tile says fresh" and "the ledger says the
#: fetch failed" disagree, and the silent-stale panel is the complaint §2.4 exists to kill.
TILE_REFRESHED = "tile_refreshed"

#: LEARNING-FLYWHEEL §3.3 (LEARN-R18), generalized by PP-9: the pending→resolved outcome
#: lifecycle, open to ANY producer. A producer journals `pending_outcome`
#: {producer, subject, metric, metric_source, horizon, baseline} at BET time — before the
#: outcome is knowable — and the resolver writes `outcome_resolved` once the horizon has elapsed
#: and ground truth has been read. Both are ledger kinds because what LANDED is the richest
#: refiner signal there is, and a `pending_outcome` with no matching `outcome_resolved` is the
#: "open question" retention must never evict. Keyed to each other by `pending_event_id`, so the
#: resolver is idempotent — a second tick finds the resolution and skips. The facility and its
#: producer/resolution/source vocabulary live in :mod:`personalclaw.ledger.outcomes`.
PENDING_OUTCOME = "pending_outcome"
OUTCOME_RESOLVED = "outcome_resolved"

#: TASKS-SOPS §1/§4/§5 (S61e): the task-projection events. Ledger kinds rather than a parallel
#: channel, because every one of them answers a question a reader asks of the ledger and nowhere
#: else: WHY does this task exist (`task_materialized`), WHO answered this gate
#: (`confirmation_pending`/`confirmation_resolved`), WHAT evidence flipped it (`task_verified`),
#: and WHICH upstream failure blocked it (`cascade_blocked`). Without them a projected task's whole
#: provenance is invisible — the board shows a task and the ledger shows the run, with nothing
#: connecting the two.
TASK_MATERIALIZED = "task_materialized"
CONFIRMATION_PENDING = "confirmation_pending"
CONFIRMATION_RESOLVED = "confirmation_resolved"
TASK_VERIFIED = "task_verified"
CASCADE_BLOCKED = "cascade_blocked"

#: WORK-CONTAINERS §4.1 (WF2WOR-4): what happened to the run's workspace. A ledger kind because
#: WHERE a run worked changes how its result reads — a stage that failed on a missing dependency
#: after a setup step failed is a different fact from one that failed on its own logic, and a
#: refiner comparing two runs of one template cannot tell them apart without this. `teardown` is
#: separate because it fires long after the run, on a deletion path, and folding it into the
#: provisioning record would mean rewriting a journal line after the run ended.
WORKSPACE_PROVISIONED = "workspace_provisioned"
WORKSPACE_TEARDOWN = "workspace_teardown"

#: PROACTIVE-ASSISTANT §1.2/§1.3: what the triage digest DID NOT do. `skipped_triage` is the
#: classifier gate's drop with the rationale and the rule that drove it; `proposal_refused` is a
#: proposal the ordinal contract, the action set or the cap rejected. Ledger kinds because a
#: digest's most dangerous failure is a SILENT one — an item the gate swallowed because a rule
#: was broader than the user thought, or a proposal dropped because the model invented an id,
#: both look identical to "nothing happened" from the digest body. These two rows are the only
#: place that difference exists, and the reply grammar's `always no` audit trail (criterion 3)
#: reads the first of them.
SKIPPED_TRIAGE = "skipped_triage"
PROPOSAL_REFUSED = "proposal_refused"

#: PROACTIVE-ASSISTANT §1.6 (PA-3): what the triage digest DID, unattended, and what it refused
#: to do on spend grounds. `auto_executed` carries the matched rule's name and the provider's
#: undo handle, which is what makes bound 4 ("every auto-execution is a ledger row with the rule
#: named, and one-click undo where the provider supports it") a fact rather than a promise —
#: without the handle on the row there is nothing for an undo click to resolve against.
#: `skipped_budget` is the NEW-1 floor's refusal. It is a SEPARATE kind rather than an
#: `auto_executed` row with a different outcome for the reason `skipped_triage` is separate from
#: `proposal_refused`: a budget breach is the one auto-execution failure a user must be able to
#: count without parsing prose, because its remedy (raise the ceiling, or wait for the window to
#: reset) is different from every other reason a proposal stayed pending.
AUTO_EXECUTED = "auto_executed"
SKIPPED_BUDGET = "skipped_budget"

#: EXECUTION-ISOLATION §7 (EI-9): one line-anchored Finding a review stage emitted, in the
#: WORKFLOWS-V2 Canonical Finding shape plus `auto_fixable`. A LEDGER kind rather than a side file
#: because the triage panel reads findings back AFTER the node settled — sometimes after the run
#: finished — and the ledger is the one channel that survives that. It is also the only way the
#: flywheel can pair a reviewer's output with the `judge_divergence` rows its rejections produce:
#: a reviewer whose findings are always rejected is a fake gate, and that claim is only provable
#: when both halves live in the same vocabulary.
REVIEW_FINDING = "review_finding"

#: PROACTIVE-ASSISTANT §5.1 (PA-5): the user's own answer to one digest proposal, whether it was
#: tapped in the digest card or typed as a channel reply. Written to the DIGEST'S OWN run, keyed by
#: `(run_id, item_ordinal)`, and that is the whole point: it is what makes a reply idempotent
#: across a gateway restart (criterion 9). A reply handler that kept its acted-set in memory would
#: re-execute after a restart, and one that kept it in a new store would be a second thing to back
#: up for a fact the run's ledger is already the record of. The row carries the parsed verb, so
#: "you answered and it did nothing" is distinguishable from "you never answered".
TRIAGE_REPLY = "triage_reply"

#: The subset a downstream refiner reads. Named so a drift test can assert the engine
#: still emits all of them.
LEDGER_KINDS = frozenset(
    {
        SKIPPED_TRIAGE,
        PROPOSAL_REFUSED,
        AUTO_EXECUTED,
        SKIPPED_BUDGET,
        TRIAGE_REPLY,
        STEP_COMPLETED,
        STEP_FAILED,
        STEP_SKIPPED,
        STEP_CACHED,
        STEP_ATTEMPT,
        STEP_ESCALATED,
        GATE_REJECTED,
        GATE_CRITERION,
        GATE_RESOLVED,
        GATE_REVISED,
        EFFECT,
        STEP_SCOPE,
        MUTATION_REJECTED,
        INPUTS_STALE,
        ITERATION,
        USER_EDITED_MID_FLIGHT,
        CONSULTED,
        CHILD_RUN_ATTACH,
        RUN_ABANDONED,
        CRYSTALLIZED,
        HANDOFF,
        CARRYOVER,
        TASK_MATERIALIZED,
        CONFIRMATION_PENDING,
        CONFIRMATION_RESOLVED,
        TASK_VERIFIED,
        CASCADE_BLOCKED,
        DECISION,
        BREAKER_TRIP,
        STEERING,
        ITEMS_COLLECTED,
        JUDGE_VERDICT,
        JUDGE_DIVERGENCE,
        REVIEW_FINDING,
        WATCHER_REAPED,
        SEEN_SET,
        BUFFER_SEAL,
        DELAY_CLAMPED,
        CLOCK_READ,
        TILE_REFRESHED,
        PENDING_OUTCOME,
        OUTCOME_RESOLVED,
        WORKSPACE_PROVISIONED,
        WORKSPACE_TEARDOWN,
    }
)
