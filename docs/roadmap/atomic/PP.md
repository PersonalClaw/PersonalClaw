# PLATFORM-PRIMITIVES — atomic plans

**Source plan:** [`PLATFORM-PRIMITIVES`](../plans/PLATFORM-PRIMITIVES.md)  
**Code:** `PP`  
**Source status:** todo

The three missing nouns — edges, verdicts and policies — extracted as platform primitives, so the engine's semantics stop being implemented three times.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `PP-1` | ⬜ | Refuse a binding whose producer is not ordered before it (WF_UNORDERED_DEP) | — | A new `WF_UNORDERED_DEP` error proves, for every node and every `node_deps` target, that the producer is terminal before the reader can be admitted — satisfied by being an earlier sibling within an enclosing `sequence`, or by a `needs` chain to it within the same `parallel`; anything else is an error naming the reader, the producer and the missing edge. Computed from the path→node map `validate_node_tree` already walks; ZERO runtime change and zero scheduling change (`tick.py` byte-identical). The bundled-template population is censused BEFORE the code becomes an error — giving a never-run rule teeth against a population that fails it is an outage, not a gate — and any violator is fixed in the same change. Proven able to fail by two probes (delete a `needs` that a binding relies on; add a cross-container binding with no ordering) each reverted by a targeted edit, plus a vacuity floor asserting the rule sees a non-empty dep set on at least one shipped template. |
| `PP-2` | ⬜ | Derive `needs` from bindings; lift the sibling-only restriction | `PP-1` | Ordering edges are DERIVED from bindings at definition time and consumed by the frontier; a hand-written `needs` survives only to express ordering that is not dataflow (a lock, an external side-effect sequence) and is checked against the derived set rather than trusted. `WF_UNKNOWN_NEEDS`' sibling-only rule is replaced by a global reachability computation over the derived graph, so a cross-container `needs` is honoured instead of refused, and a `branch` decline still marks exactly the unreachable derived targets SKIPPED (WF2-R18 preserved: a join must neither deadlock on an untaken leg nor fire early on a live one). Verified by a run whose diamond spans two containers completing correctly, and by the WF2-R18 join tests staying green. The `to_skip` reachability change is the risk surface and gets its own test matrix over decline-inside-parallel, decline-inside-foreach-body and decline-with-a-cross-container-reader. |
| `PP-3` | ⬜ | Cross-check `output_contract` against the bindings that read it | `PP-1` | The validator resolves every `{{nodes.<id>.output.<path>}}` reference against the producer's declared `output_contract` when it has one, and errors when the path is unsatisfiable (a key absent from `required_keys` on a contract that also declares `must_be_json`); a producer read structurally but declaring no contract raises a WARNING naming the readers, not an error. No new contract vocabulary is invented — `required_keys` is the existing field. Censused first over the bundled templates so the warning volume is known before it ships. Proven able to fail by renaming a key on one side of a shipped binding. |
| `PP-4` | ✅ | Extract the ledger as a platform primitive (`ledger/` package) | — | A `personalclaw/ledger/` package owns the append/redact/stamp/spill machinery and the kind registry; `workflows/journal.py` becomes a thin workflow-flavoured facade over it with its ledger-kind set unchanged and `LEDGER_KINDS` still asserted by the existing drift test. Byte-identical output for a workflow run: a golden-file test captures a real run's `journal.jsonl` + `events.jsonl` before the move and diffs after, so "pure extraction" is proven rather than claimed. `MAX_INLINE_OUTPUT_BYTES` spilling, binary magic-prefix detection and the `result_omitted` stub move with it. No consumer outside workflows is added here — that is `PP-5`. |
| `PP-5` | ⬜ | Loops emit the ledger (closes the flywheel's loop blind spot) | `PP-4`, `WF2LOO-16` | Loop cycles emit to the `PP-4` ledger: a cycle becomes `step_started`/`step_completed`, a supervisor assessment becomes `judge_verdict` in the reconciled vocabulary, a stall becomes `breaker_trip`, a reap becomes `watcher_reaped`. `loop/store.py`'s findings/verdicts files become projections over the ledger rather than a second store (clean break — no dual write). Verified by driving a real loop and reading its trajectory back from the ledger alone, and by `learning/mining.py` returning a non-empty result for a loop run where it previously returned nothing — the acceptance bar is the flywheel producing a proposal from loop evidence, not merely the rows existing. |
| `PP-6` | ⬜ | `workflow replay <run_id>`: recorded-response provider + trajectory diff | `PP-4` | A `workflow replay <run_id>` verb re-drives `frontier()` against a provider that returns the run's own recorded responses, and diffs the resulting trajectory against the original, reporting the first divergent node. The nondeterminism envelope a run depended on is journaled so replay is possible at all: clock reads behind a seam, provider responses by `output_ref`, and the resolved prompt already stored. Divergence is a first-class outcome, not a failure — a template edit SHOULD diverge, and the verb's job is to say where. Verified by replaying a completed multi-node run to a byte-identical trajectory, and by an edited prompt diverging at exactly the edited node and nowhere earlier. |
| `PP-7` | ⬜ | Trajectory signature as a ledger projection + template regression detection | `PP-4`, `PP-6` | A `trajectory_signature` is derived as a PURE projection over the existing ledger (ordered node/lane/verdict tuples) with no new store, exposed on the run projection and queryable per template. A regression signal fires when a template's runs shift to a signature class that historically failed more often. Verified: two runs of one template with the same inputs produce equal signatures; a rewind produces a distinguishable one; the projection is proven pure by computing it twice over a frozen ledger and comparing. |
| `PP-8` | ⬜ | Edge-decision statistics: per-`branch`/`gate` distribution + dead-case detection | `PP-4` | A ledger projection reports, per `branch` and per judge gate, the case/verdict distribution across a template's run history, and flags a case never taken and a selector whose distribution is degenerate. Rendered on the existing introspection surface rather than a new one. Sample-gated like `gate_stats` — a distribution over three runs is not a finding, and reporting it as one is how a legible surface stops being read. |
| `PP-9` | ⬜ | Generalize the outcome record beyond decisions (`pending_outcome`/`outcome_resolved`) | `PP-4` | The outcome pair becomes a general ledger facility any producer may open: a published artifact, an escalation, a proposal, a declared control. The horizon resolver stays idempotent via `pending_event_id` and still distinguishes measured from inconclusive. At least two non-decision producers are wired in the same change so the generalization is exercised rather than merely available (a `publish:` artifact and a gate escalation). Coordinated with PROACTIVE-ASSISTANT's `PA-4` decision journal so there is ONE outcome facility, not a decision-shaped one plus a general one. |
| `PP-10` | ⬜ | Consumer-liveness detection: surface a work unit whose output nobody reads | `PP-9` | A dormancy sweep reports a work unit whose last N cycles produced output with no consumer touch, surfaced as a PROPOSAL to pause or retire it — never an automatic stop, because "nobody looked yet" and "nobody will ever look" are different facts and only the user knows which. Uses the `PP-9` outcome record with a consumption horizon rather than a new counter. Verified by driving a run whose artifact is never opened (sweep fires) and one whose artifact is opened (sweep stays silent) — the second half is the one that proves it is not a blanket nag. |
| `PP-11` | ✅ | Extract `AdmissionPolicy` behind today's lanes (no behaviour change) | `PP-4` | `frontier()`'s admission step becomes an ordered list of `AdmissionPolicy` objects, with today's lane caps, per-container `max_concurrency` and WIP=1 expressed as the first three policies and composed tightest-wins. `frontier()` stays PURE — no clock, no I/O — and its output is byte-identical for every existing spec: a golden-file test over the bundled templates' frontier decisions captures before and diffs after. `wip_held` and `deferred` keep their distinct meanings (a declared invariant being enforced vs lane pressure). No new policy is added here and no other scheduler is touched — this atom only makes the seam exist. |
| `PP-12` | ⬜ | Add `Lease` and `Dwell`/`MetricGate` admission policies | `PP-11` | `Lease(ttl)` reuses `pool.py`'s proven compare-and-swap decision functions rather than a second lease implementation (S57 measured `unlink`-based single-use failing 36 of 40 races, and a lease that loses a race is worse than no lease), and `Dwell`/`MetricGate` reuse `loop/tick.StepConfig`'s parsed thresholds. Both are additive: a spec declaring neither behaves exactly as before, proven by re-running `PP-11`'s golden frontier file. Verified by a fan-out that genuinely serializes on a leased resource across a gateway restart, and by a metric regression rolling a step back inside a workflow run. |
| `PP-13` | ⬜ | Retire `pool.py`'s private frontier onto the unified core | `PP-12` | `pool.frontier`/`pool.next` are DELETED and the Work board's ready projection is computed by the unified core with a `Lease` policy plus the pool's existing priority/blocking-count/overdue ordering expressed as a comparator. The lease decision functions survive (they are the policy's implementation); only the duplicate projection goes. Verified: the Work board renders an identical ready set before and after over a seeded fixture, leases still survive a gateway kill, and `dependency_failed` cascade plus burst coalescing still fire. Retiring a legacy path is never a pure deletion — the sweep names every caller before the delete lands. |
| `PP-14` | ⬜ | `SupervisorPolicy`: the declaration, its parser and its validator | `WF2LOO-16` | A `SupervisorPolicy` declaration (rubric, escalation ladder, failure mutations, dwell/metric gates, marginal-value band, judge model tier, reproduce-before-ship, write scope, budget, HITL posture) parses from a loop node's config with tolerant reads and a closed field set, validated at authoring time with typed `WF_*` codes. Deliberately NOT wired: the module docstring states that it has zero production callers and names `PP-15` as the wiring owner, and a two-directional rail fails both when a caller appears while the marker stays and when the marker is removed while callers are still zero. Proven able to fail by two probes. |
| `PP-15` | ⬜ | Widen the convergence core and wire `SupervisorPolicy` into it | `PP-14`, `PP-12` | `Action` gains `ESCALATE(rung)` and `REPLAN`, `TickState` absorbs `loop_middleware.LoopState`'s counters (call fingerprints, fix fingerprints, failure classes, progress marks), and `evaluate` becomes the one convergence decision for both the `loop` node and the legacy loop kinds — driven by a `SupervisorPolicy` rather than by per-kind Python. Purity is preserved and re-proven: the same (cfg, state, now) yields the same `Decision`, and a restarted process re-derives it from persisted state alone. `REPLAN` queues a real mutation batch (`mutations.py`'s `insert`/`delete`/`move`/`run_from` applied at the controller's drain point) instead of the retry-with-a-hint it is today. Verified by the existing `evaluate` branch tests staying green plus new coverage for each added member, and by a run that re-derives its remaining steps from a judge critique. |
| `PP-16` | ⬜ | A Loop becomes a WorkflowRun (retire the second work-unit noun) | `PP-15`, `PP-5`, `PP-13` | A Loop is a `WorkflowRun` carrying a `SupervisorPolicy`; the five kinds are bundled templates plus policies, so the domain intelligence lives in the policy and the supervisor stops being pluggable Python. One status vocabulary, one adoption/reaping path, one attention path, one ledger, one projection to tasks, one cockpit contract. `loop/store.py`'s parallel row is retired (clean break under the pre-1.0 banner; release notes advise `personalclaw snapshot`). Verified as a user: each of the five kinds is driven end-to-end through the unified path with its cockpit intact, a kill mid-run is adopted by the single watchdog, and the flywheel produces a proposal from a loop run's ledger. Explicitly NOT in scope: `triggers/` stays a separate scheduler — it answers whether to START, and folding it in would put wall-clock into a pure core. |

## Atom scopes

### `PP-1` — Refuse a binding whose producer is not ordered before it (WF_UNORDERED_DEP)

**Status:** todo

The engine keeps TWO edge lists and nothing checks they agree. Scheduling admission comes ONLY from container order plus sibling-only `needs` (`tick._visit_parallel` gates on `child.needs`; `validator.py:221-230` refuses a `needs` that is not a sibling in the same `parallel`, reason given: "cross-container edges would make the tree a graph and break the frontier's locality"). Data dependencies are a SEPARATE graph: `node_deps` feeds the resume cache's `inputs_hash` (`controller._resolved_inputs`), the stale-inputs check (`controller.py:1471`) and the mutation cascade (`mutations.py:262`) — never admission. `validator._kahn_levels` builds the combined graph and its own docstring says reading its levels as an execution order would be wrong. None of the 46 shipped `WF_*` codes covers the disagreement.

**Done when:** A new `WF_UNORDERED_DEP` error proves, for every node and every `node_deps` target, that the producer is terminal before the reader can be admitted — satisfied by being an earlier sibling within an enclosing `sequence`, or by a `needs` chain to it within the same `parallel`; anything else is an error naming the reader, the producer and the missing edge. Computed from the path→node map `validate_node_tree` already walks; ZERO runtime change and zero scheduling change (`tick.py` byte-identical). The bundled-template population is censused BEFORE the code becomes an error — giving a never-run rule teeth against a population that fails it is an outage, not a gate — and any violator is fixed in the same change. Proven able to fail by two probes (delete a `needs` that a binding relies on; add a cross-container binding with no ordering) each reverted by a targeted edit, plus a vacuity floor asserting the rule sees a non-empty dep set on at least one shipped template.

### `PP-2` — Derive `needs` from bindings; lift the sibling-only restriction

**Status:** todo

`PP-1` makes the disagreement visible; this deletes the second edge list. Fluxtion's "Graph Engineering Needs a Compiler" (2026-07-29) names the exact defect — an explicit orchestration definition that restates relationships already present in the components is "a second edge list", and the remedy is to declare structure once and derive coordination. The sibling-only restriction exists to protect a hand-maintained ordering list from being wrong; once ordering is derived, the dependency graph is already global and `_kahn_levels` already proves it acyclic, so reachability can be computed over the derived graph and the restriction becomes unnecessary rather than load-bearing. This is the single biggest expressiveness gap versus the peer graph runtimes: a diamond spanning two containers is inexpressible today.

**Done when:** Ordering edges are DERIVED from bindings at definition time and consumed by the frontier; a hand-written `needs` survives only to express ordering that is not dataflow (a lock, an external side-effect sequence) and is checked against the derived set rather than trusted. `WF_UNKNOWN_NEEDS`' sibling-only rule is replaced by a global reachability computation over the derived graph, so a cross-container `needs` is honoured instead of refused, and a `branch` decline still marks exactly the unreachable derived targets SKIPPED (WF2-R18 preserved: a join must neither deadlock on an untaken leg nor fire early on a live one). Verified by a run whose diamond spans two containers completing correctly, and by the WF2-R18 join tests staying green. The `to_skip` reachability change is the risk surface and gets its own test matrix over decline-inside-parallel, decline-inside-foreach-body and decline-with-a-cross-container-reader.

**Design**

Two hand-maintained edge lists over one dependency truth. Measured on this tree:

| Fact | Evidence |
|---|---|
| admission reads `needs` only | `tick._visit_parallel` — `for need in child.needs:` gates readiness; nothing consults `node_deps` |
| `needs` is sibling-scoped | `validator.py:221-230` `WF_UNKNOWN_NEEDS` — "needs {n} is not a sibling in this parallel block" |
| the stated reason | same site: *"cross-container edges would make the tree a graph and break the frontier's locality"* |
| bindings are a separate graph | `node_deps` callers: `controller._resolved_inputs` (cache key), `controller.py:1471` (stale-inputs), `mutations.py:262` (cascade), `validator.py:521/549` (existence + cycle). None admit work. |
| the combined graph already exists | `validator._kahn_levels` builds it from bindings **and** `needs`, and detects `WF_CYCLE` |
| but is explicitly not a schedule | its docstring: *"Reading these levels as an execution order would be wrong."* |

**The failure it produces.** A `parallel` child whose prompt binds `{{nodes.sibling.output}}` without
declaring `needs: [sibling]` is admitted concurrently, resolves against an absent output, and dies as
a `USER` failure — *"binding failed: … check the referenced node id and field exist"* (`engine.py:195-203`).
The message points the author at a node id that is **correct**. Locally plausible, globally wrong.

**Why lifting the restriction is safe once ordering is derived.** The sibling-only rule protects a
*hand-maintained* list from being wrong. Derived edges make the dependency graph global by
construction, and `_kahn_levels` already proves it acyclic, so reachability — which paths a `branch`
decline makes unreachable — is computable rather than guessed. That is the whole reason the
restriction can go, and it is why `PP-1` lands first: the lint establishes that the derived set is a
superset of what authors declare today before anything starts depending on it.

**Implementation plan**

1. Land `PP-1` first and read its census: how many bundled templates declare a `needs` the derived
   set does not contain (hand ordering that is not dataflow — keep) and how many bind without
   ordering (a latent bug — fix).
2. Compute derived edges at definition time from `node_deps`, mapping producer node id → its
   instance path, and hand them to `frontier()` alongside container order.
3. Keep hand-written `needs` as an *additional* constraint, never a replacement: the union orders,
   and a `needs` absent from the derived set is reported (it is either real non-dataflow ordering or
   a stale edge, and the author is the only one who knows which).
4. Replace `WF_UNKNOWN_NEEDS`' sibling test with global resolution; a `needs` naming a node outside
   the enclosing `parallel` is now honoured.
5. Rewrite `to_skip` reachability over the derived graph. **This is the risk surface** — get a test
   matrix over decline-inside-`parallel`, decline-inside-`foreach`-body, decline-with-a-
   cross-container-reader, and a diamond whose two legs rejoin in a different container.
6. Prove WF2-R18 still holds in both directions: a join must not deadlock on an untaken leg, and must
   not fire early on a live one. The existing join tests are the floor, not the ceiling.
7. Drive a real run whose diamond spans two containers — the capability this atom exists to add — and
   confirm the `to_skip` set is exactly the unreachable derived targets.

### `PP-3` — Cross-check `output_contract` against the bindings that read it

**Status:** todo

`engine.check_output_contract` (`engine.py:2173`) is a good ~0.3ms gate that runs BEFORE any binding resolves — `must_be_json`, `required_keys`, length bounds, forbidden phrases. It is per-node, opt-in, and never compared against its readers: `{{nodes.a.output.foo.bar}}` is unvalidated at authoring time even when node `a` declares `required_keys: [foo]`. The peer runtimes type-validate message routing at build time; we validate the producer and the consumer independently and never the edge between them.

**Done when:** The validator resolves every `{{nodes.<id>.output.<path>}}` reference against the producer's declared `output_contract` when it has one, and errors when the path is unsatisfiable (a key absent from `required_keys` on a contract that also declares `must_be_json`); a producer read structurally but declaring no contract raises a WARNING naming the readers, not an error. No new contract vocabulary is invented — `required_keys` is the existing field. Censused first over the bundled templates so the warning volume is known before it ships. Proven able to fail by renaming a key on one side of a shipped binding.

### `PP-4` — Extract the ledger as a platform primitive (`ledger/` package)

**Status:** done

`workflows/journal.py` is the best observability surface in the tree — append-only, `redact()` on every write, deterministic `event_id`, monotonic `seq`, epoch stamping, 40+ typed kinds, one file read two ways (resume cache + Run Ledger) — and ONLY workflows have it. `loop/store.py` persists findings/verdicts/status with no event vocabulary; tasks carry status; triggers keep their own log. `learning/mining.py:15` derives from "the run's own journal", so the hill-climbing loop covers exactly one of four work-unit kinds. This atom moves the mechanism, not the semantics: no new fields, no new kinds, no behaviour change for workflow runs.

**Done when:** A `personalclaw/ledger/` package owns the append/redact/stamp/spill machinery and the kind registry; `workflows/journal.py` becomes a thin workflow-flavoured facade over it with its ledger-kind set unchanged and `LEDGER_KINDS` still asserted by the existing drift test. Byte-identical output for a workflow run: a golden-file test captures a real run's `journal.jsonl` + `events.jsonl` before the move and diffs after, so "pure extraction" is proven rather than claimed. `MAX_INLINE_OUTPUT_BYTES` spilling, binary magic-prefix detection and the `result_omitted` stub move with it. No consumer outside workflows is added here — that is `PP-5`.

**Design**

`workflows/journal.py` is the platform's best observability surface and it is scoped to one feature.
Measured: `learning/` contains **zero** references to `loop.store` or `get_findings`; `mining.py:15`
derives from *"the run's own journal"*. So the outer improvement loop — the one the field literature
calls the hill-climbing loop, *"the return arrow reaches inside and updates the agent loop directly"* —
covers workflow runs and nothing else, while the five loop kinds carry the long-horizon autonomous
work.

**Why this is an extraction and not a rewrite.** Every property worth keeping is already in
`journal.py`: append-only, `redact()` before every write, deterministic `event_id` (`<run>-evt-<seq>`)
so a re-emit is an idempotent no-op, monotonic `seq`, epoch stamping, the 64KB/binary spill to a
typed `result_omitted` stub, and one file read two ways. The move must not change any of it, which is
why the acceptance bar is a byte-identical golden file rather than a passing test suite.

**Implementation plan**

1. Capture a golden `journal.jsonl` + `events.jsonl` from a real multi-node run on `HEAD` before
   touching anything.
2. Move append/redact/stamp/spill and the kind registry into `personalclaw/ledger/`; leave
   `workflows/journal.py` as the workflow-flavoured facade with `LEDGER_KINDS` unchanged so the
   existing FE drift test still binds.
3. Re-run the same fixture and diff against the golden file. A non-empty diff fails the atom.
4. Only then wire the loop emitter (`PP-5`) — a second producer before the extraction is proven is
   how a "pure move" acquires semantics.

### `PP-5` — Loops emit the ledger (closes the flywheel's loop blind spot)

**Status:** todo

Measured: `learning/` has ZERO references to `loop.store` or `get_findings`. The five loop kinds — where the long-horizon autonomous work actually runs today — generate no learning signal at all, so LangChain's fourth loop ("the return arrow reaches inside and updates the agent loop directly", 2026-06-16) closes over workflow runs only. Depends on `WF2LOO-16` because a loop cycle cannot emit `judge_verdict` until the third verdict vocabulary speaks the contract; emitting `CycleVerdict`'s shape would add a fifth dialect to the ledger rather than closing the gap.

**Done when:** Loop cycles emit to the `PP-4` ledger: a cycle becomes `step_started`/`step_completed`, a supervisor assessment becomes `judge_verdict` in the reconciled vocabulary, a stall becomes `breaker_trip`, a reap becomes `watcher_reaped`. `loop/store.py`'s findings/verdicts files become projections over the ledger rather than a second store (clean break — no dual write). Verified by driving a real loop and reading its trajectory back from the ledger alone, and by `learning/mining.py` returning a non-empty result for a loop run where it previously returned nothing — the acceptance bar is the flywheel producing a proposal from loop evidence, not merely the rows existing.

### `PP-6` — `workflow replay <run_id>`: recorded-response provider + trajectory diff

**Status:** todo

We have a resume CACHE, not a replay. `journal.lookup` serves only SUCCESS states and the journal file is never rewritten; `harness/replay.py` replays recorded MCP traces as an offline tool server — a dev-test facility, not a run replay. The controller reads wall clock live (`_wake_due_nodes`). Fluxtion's requirement is that replay be structural rather than retrofitted: recorded inputs re-run on the same processor version reproduce a decision path. We are ~80% there and never cash it in — `_store_prompt` already persists the fully-resolved prompt and its docstring already says "Required for trajectory replay (§5)".

**Done when:** A `workflow replay <run_id>` verb re-drives `frontier()` against a provider that returns the run's own recorded responses, and diffs the resulting trajectory against the original, reporting the first divergent node. The nondeterminism envelope a run depended on is journaled so replay is possible at all: clock reads behind a seam, provider responses by `output_ref`, and the resolved prompt already stored. Divergence is a first-class outcome, not a failure — a template edit SHOULD diverge, and the verb's job is to say where. Verified by replaying a completed multi-node run to a byte-identical trajectory, and by an edited prompt diverging at exactly the edited node and nowhere earlier.

### `PP-7` — Trajectory signature as a ledger projection + template regression detection

**Status:** todo

Judging grades outputs per node; `eval_specs.py` does per-template structural checks; `introspection.py` reports p50/p95. Nothing describes the PATH a run took, so "runs of this template that went a different way" is unanswerable. Anthropic's multi-agent write-up states the problem directly — with identical starting points agents may take completely different valid paths — and the graph-engineering practice literature ranks trajectory evals above output evals as where the next failure hides. `FailureSignature` is the nearest thing and it is per-failure, not per-run.

**Done when:** A `trajectory_signature` is derived as a PURE projection over the existing ledger (ordered node/lane/verdict tuples) with no new store, exposed on the run projection and queryable per template. A regression signal fires when a template's runs shift to a signature class that historically failed more often. Verified: two runs of one template with the same inputs produce equal signatures; a rewind produces a distinguishable one; the projection is proven pure by computing it twice over a frozen ledger and comparing.

### `PP-8` — Edge-decision statistics: per-`branch`/`gate` distribution + dead-case detection

**Status:** todo

"Put the judgment in the edges — and instrument them" is the one graph-engineering practice we have no answer to. `branch` (binding-decided) and `gate`/`judge` (model-decided) are journaled individually and never aggregated, so a selector that has taken one case 47/47 times, or a case no run has ever reached, is invisible. `introspection.gate_stats` (said-no statistics with a sample-gated fake-check badge) is the closest existing surface and covers gates only.

**Done when:** A ledger projection reports, per `branch` and per judge gate, the case/verdict distribution across a template's run history, and flags a case never taken and a selector whose distribution is degenerate. Rendered on the existing introspection surface rather than a new one. Sample-gated like `gate_stats` — a distribution over three runs is not a finding, and reporting it as one is how a legible surface stops being read.

### `PP-9` — Generalize the outcome record beyond decisions (`pending_outcome`/`outcome_resolved`)

**Status:** todo

The platform records what it DID and not what LANDED. That single shape produces loop output nobody reads, unmeasured routing edges, and the whole declared-but-inert class this repo keeps rediscovering by AST audit rather than at runtime. The right mechanism already exists and is scoped to one feature: LEARN-R18's `pending_outcome` journals a bet at decision time with `{subject, metric, horizon_secs, baseline}` and `outcome_resolved` closes it, keeping `resolution: measured | inconclusive` distinct so an unmeasurable outcome decays faster than a measured one, linked by `pending_event_id` so a second curator tick is idempotent.

**Done when:** The outcome pair becomes a general ledger facility any producer may open: a published artifact, an escalation, a proposal, a declared control. The horizon resolver stays idempotent via `pending_event_id` and still distinguishes measured from inconclusive. At least two non-decision producers are wired in the same change so the generalization is exercised rather than merely available (a `publish:` artifact and a gate escalation). Coordinated with PROACTIVE-ASSISTANT's `PA-4` decision journal so there is ONE outcome facility, not a decision-shaped one plus a general one.

### `PP-10` — Consumer-liveness detection: surface a work unit whose output nobody reads

**Status:** todo

The watchdogs measure PRODUCER health — findings count, turn wall-time, stagnation, consecutive errors. Nothing asks whether a loop's output was ever read. The field post-mortem this is drawn from (2026-08-11) is a fully autonomous pipeline that had been dead for two months with nobody noticing, and its author's diagnosis was structural rather than tooling: nobody owned acting on the output. Our equivalent is a monitor-kind run writing a deliverable on a cadence into an artifact nobody opens — we detect a STALLED one and never a POINTLESS one. The raw signal already exists (artifact events, inbox read state, `pinned_artifacts`).

**Done when:** A dormancy sweep reports a work unit whose last N cycles produced output with no consumer touch, surfaced as a PROPOSAL to pause or retire it — never an automatic stop, because "nobody looked yet" and "nobody will ever look" are different facts and only the user knows which. Uses the `PP-9` outcome record with a consumption horizon rather than a new counter. Verified by driving a run whose artifact is never opened (sweep fires) and one whose artifact is opened (sweep stays silent) — the second half is the one that proves it is not a blanket nag.

### `PP-11` — Extract `AdmissionPolicy` behind today's lanes (no behaviour change)

**Status:** done

Four independent schedulers answer "what may run now, given persisted state", sharing ZERO lines: `workflows/tick.frontier()` (typed lanes + per-container `max_concurrency` + WIP=1), `loop/tick.evaluate()` (dwell / min_findings / metric_pass / metric_hold / rollback), `workflows/pool.py`'s `frontier`/`next` (priority + blocking-count + overdue with TTL'd CAS leases — verified: `pool.py` imports only stdlib), and `triggers/` `tick_once`. The first three are the same projection with different admission rules, and each has a capability the others structurally cannot express. arXiv:2604.11378's contribution is precisely that an agent loop is "a single ready unit scheduler" and graph engines differ from it in degree, not kind — so the unification is semantically available, not aspirational.

**Done when:** `frontier()`'s admission step becomes an ordered list of `AdmissionPolicy` objects, with today's lane caps, per-container `max_concurrency` and WIP=1 expressed as the first three policies and composed tightest-wins. `frontier()` stays PURE — no clock, no I/O — and its output is byte-identical for every existing spec: a golden-file test over the bundled templates' frontier decisions captures before and diffs after. `wip_held` and `deferred` keep their distinct meanings (a declared invariant being enforced vs lane pressure). No new policy is added here and no other scheduler is touched — this atom only makes the seam exist.

**Design**

Four schedulers answer the same question and share no code. Verified on this tree:

| Scheduler | Admission rule | Imports |
|---|---|---|
| `workflows/tick.frontier()` | typed lanes, per-container `max_concurrency`, WIP=1 | bindings, conditions, models |
| `loop/tick.evaluate()` | dwell / `min_findings` / `metric_pass` / `metric_hold` / rollback | stdlib only |
| `workflows/pool.py` `frontier`/`next` | priority + blocking-count + overdue, TTL'd CAS leases | **stdlib only** — no shared core |
| `triggers/` `tick_once` | wall-clock / event arm | — |

The first three are the same projection `(spec, persisted state) → ready work` with different
admission rules, and each holds a capability the others cannot express: leases only in the pool,
dwell/metric gates only in loops (and consumed by exactly one kind, `kinds/sdlc.py:_tick_decide`),
lanes only in workflows.

arXiv:2604.11378 (*From Agent Loops to Structured Graphs*, Apr 2026) is the licence for treating this
as one mechanism: an agent loop is *"a single ready unit scheduler"*, and graph engines differ from it
in **degree** — how many units are schedulable and how inspectable the selection rule is — not in
kind. Ready-set-1, lease-gated and lane-gated are three policies over one projection.

**Why this atom adds nothing.** A refactor that also changes behaviour cannot be verified, because
there is no oracle for "did the scheduler still decide the same thing". So `PP-11` is seam-only: the
policies it introduces are exactly today's three rules, and the acceptance bar is a golden frontier
file that does not move. `PP-12` is where new capability lands, on a seam already proven inert.

**Implementation plan**

1. Capture a golden file of `frontier()` decisions — `ready`/`deferred`/`wip_held`/`to_skip`/
   `blocked` — for every bundled template across a seeded state matrix.
2. Introduce `AdmissionPolicy` with `Lane(caps)`, `Wip(n)` and `ContainerConcurrency(n)` as the first
   three implementations, composed tightest-wins.
3. Keep `frontier()` **pure**: no clock, no I/O, no argument mutation. The purity contract is what
   makes rewind tractable and it is not negotiable for a refactor.
4. Preserve the distinction between `deferred` (lane pressure) and `wip_held` (a declared invariant
   being enforced) — collapsing them would make "why is item 2 not running" unanswerable from the
   ledger, which is the reason `wip_held` exists.
5. Re-run the golden file. Byte-identical or the atom is not done.

### `PP-12` — Add `Lease` and `Dwell`/`MetricGate` admission policies

**Status:** todo

With the `PP-11` seam in place, the two capabilities that live in the other schedulers become expressible in the frontier. Leases exist only in `pool.py`, so "cap this fan-out because each item holds a rate-limited endpoint" is inexpressible today — lane caps protect the engine and `max_concurrency` protects the run's shape, but neither models a resource an item HOLDS. Dwell/metric gating exists only in `loop/tick.evaluate` and is consumed by exactly one kind (`kinds/sdlc.py:_tick_decide`), so a workflow loop cannot bake a step or roll back on a regressed metric.

**Done when:** `Lease(ttl)` reuses `pool.py`'s proven compare-and-swap decision functions rather than a second lease implementation (S57 measured `unlink`-based single-use failing 36 of 40 races, and a lease that loses a race is worse than no lease), and `Dwell`/`MetricGate` reuse `loop/tick.StepConfig`'s parsed thresholds. Both are additive: a spec declaring neither behaves exactly as before, proven by re-running `PP-11`'s golden frontier file. Verified by a fan-out that genuinely serializes on a leased resource across a gateway restart, and by a metric regression rolling a step back inside a workflow run.

### `PP-13` — Retire `pool.py`'s private frontier onto the unified core

**Status:** todo

`WF2TAS-6` shipped the task pool's own `frontier`/`next` projections and `WF2TAS-10` its lease write path. With `Lease` available as an admission policy (`PP-12`), the pool's private projection is a second implementation of a solved problem. Clean break under the pre-1.0 banner: no dual path, no compatibility shim.

**Done when:** `pool.frontier`/`pool.next` are DELETED and the Work board's ready projection is computed by the unified core with a `Lease` policy plus the pool's existing priority/blocking-count/overdue ordering expressed as a comparator. The lease decision functions survive (they are the policy's implementation); only the duplicate projection goes. Verified: the Work board renders an identical ready set before and after over a seeded fixture, leases still survive a gateway kill, and `dependency_failed` cascade plus burst coalescing still fire. Retiring a legacy path is never a pure deletion — the sweep names every caller before the delete lands.

### `PP-14` — `SupervisorPolicy`: the declaration, its parser and its validator

**Status:** todo

A loop is not a second engine — it is a graph shape plus a supervisor policy. The shape already exists (`loop` node kind, `LoopMode.{counted,until,until_dry,until_cancelled}`, `foreach` with `max_concurrency`). The POLICY has no home, which is why it got implemented twice: `loop/` carries marginal-value and reproduce-before-ship while `workflows/` carries the pre-tier, the proof precondition, the actor matrix and the five-rung ladder, and neither is complete. This atom lands the declaration ONLY, deliberately inert, with the honesty marker `WF2LOO-12` established as this program's convention — a control with no caller must SAY it has no caller.

**Done when:** A `SupervisorPolicy` declaration (rubric, escalation ladder, failure mutations, dwell/metric gates, marginal-value band, judge model tier, reproduce-before-ship, write scope, budget, HITL posture) parses from a loop node's config with tolerant reads and a closed field set, validated at authoring time with typed `WF_*` codes. Deliberately NOT wired: the module docstring states that it has zero production callers and names `PP-15` as the wiring owner, and a two-directional rail fails both when a caller appears while the marker stays and when the marker is removed while callers are still zero. Proven able to fail by two probes.

### `PP-15` — Widen the convergence core and wire `SupervisorPolicy` into it

**Status:** todo

`loop/tick.evaluate` is already the right shape — pure, restartable, exhaustive branch order, every input derived from persisted state, with a documented purity contract (I/O the decision implies happens in the adapter and its RESULT is fed back as `state.metric` next tick). It is consumed by exactly ONE kind. Widening it to the full decision set makes it the single brain both engines call. Two members are missing from `Action`: `ESCALATE(rung)` — `loop_middleware`'s five rungs have no representation, so the loops engine fails binary after two consecutive errors — and `REPLAN`, whose absence is the sharper gap (see `WF2LOO-16`).

**Done when:** `Action` gains `ESCALATE(rung)` and `REPLAN`, `TickState` absorbs `loop_middleware.LoopState`'s counters (call fingerprints, fix fingerprints, failure classes, progress marks), and `evaluate` becomes the one convergence decision for both the `loop` node and the legacy loop kinds — driven by a `SupervisorPolicy` rather than by per-kind Python. Purity is preserved and re-proven: the same (cfg, state, now) yields the same `Decision`, and a restarted process re-derives it from persisted state alone. `REPLAN` queues a real mutation batch (`mutations.py`'s `insert`/`delete`/`move`/`run_from` applied at the controller's drain point) instead of the retry-with-a-hint it is today. Verified by the existing `evaluate` branch tests staying green plus new coverage for each added member, and by a run that re-derives its remaining steps from a judge critique.

**Design**

`loop/tick.evaluate` is already the right shape and is used by one kind. Its module docstring states
the purity contract this atom must preserve: *"I/O the decision implies … happens in the adapter and
its RESULT is fed back in as `state.metric` next tick"* — that is precisely what lets one pure core
serve two adapters.

Two members are missing from `Action`, and the second is the sharper gap:

* **`ESCALATE(rung)`** — `loop_middleware`'s five rungs (`classified_retry`, `fresh_session`,
  `model_switch`, `restart_from_scratch`, `surface`) have no representation in the loops engine, which
  fails binary after `_MAX_CONSECUTIVE_ERRORS = 2`.
* **`REPLAN`** — declared in `judge_contract.py:128` as *"mid-flight replanning instead of ad-hoc
  mutation"* and implemented at `engine.py:370-379` as `InstanceState.FAILED` + `TRANSIENT` +
  `recoverable=True` + a remediation string. **Nothing re-derives the remaining steps.** `loop/tick`'s
  `Action` has no replan member at all — only `ROLLBACK` to the prior step. So the most-used recovery
  move in the field literature (throw out the plan, regenerate it) has no implementation in either
  engine, while `mutations.py` already ships `insert`/`delete`/`move`/`run_from` applied at the
  controller's drain point. It is a declared strategy with a borrowed executor.

**Implementation plan**

1. Widen `Action` with `ESCALATE(rung)` and `REPLAN`, and extend `TickState` with
   `loop_middleware.LoopState`'s counters (call fingerprints, fix fingerprints, failure classes,
   progress marks). Keep the branch order documented and exhaustive — the existing docstring numbers
   its nine branches and a tenth must declare its own position, not inherit one.
2. Make `SupervisorPolicy` (`PP-14`) the source of the thresholds `evaluate` reads, so the per-kind
   Python that supplies them today has one replacement rather than two.
3. Implement `REPLAN` as a **queued mutation batch** derived from the judge's critique, applied at the
   drain point under the existing TOCTOU re-verify and `expect_version` guards. A rewind whose cascade
   would re-run completed work already reports `needs_confirmation`; replan inherits that, so it
   cannot silently discard finished work.
4. Re-prove purity: same `(cfg, state, now)` → same `Decision`, and a restarted process re-derives it
   from persisted state alone. The existing branch tests are the floor; each new member gets its own.
5. Drive a run that re-derives its remaining steps from a judge critique, and one that walks the rung
   ladder instead of failing after two errors.

### `PP-16` — A Loop becomes a WorkflowRun (retire the second work-unit noun)

**Status:** todo

The capstone. `materialize.py` already proves the direction ("tasks as a projection of run state") and `containers.py` is the Work board projection; loops are the holdout, with their own row, status enum, store, watchdog and adoption path. Consequence today: restart-adoption is implemented twice (`loop/manager.reap_orphaned_loops` and `workflows/watchdog` adoption), park-on-human twice, budget twice, cancel twice — so every future feature touching work units costs 2x. LOOPS-EVOLUTION always intended kinds to become templates; this is the half it did not name, the NOUN change rather than the brain change.

**Done when:** A Loop is a `WorkflowRun` carrying a `SupervisorPolicy`; the five kinds are bundled templates plus policies, so the domain intelligence lives in the policy and the supervisor stops being pluggable Python. One status vocabulary, one adoption/reaping path, one attention path, one ledger, one projection to tasks, one cockpit contract. `loop/store.py`'s parallel row is retired (clean break under the pre-1.0 banner; release notes advise `personalclaw snapshot`). Verified as a user: each of the five kinds is driven end-to-end through the unified path with its cockpit intact, a kill mid-run is adopted by the single watchdog, and the flywheel produces a proposal from a loop run's ledger. Explicitly NOT in scope: `triggers/` stays a separate scheduler — it answers whether to START, and folding it in would put wall-clock into a pure core.


**Design**

The capstone, and a **noun** change rather than a brain change. The direction is already established
by two shipped projections — `materialize.py` ("tasks as a projection of run state: the exhaustive
state→status table") and `containers.py` (the Work board projection). Loops are the holdout: own row,
own status enum, own store, own watchdog, own adoption path.

What that duplication costs today, measured: restart-adoption exists twice
(`loop/manager.reap_orphaned_loops` and `workflows/watchdog` adoption), park-on-human twice, budget
twice, cancel twice. Every future feature touching work units pays 2x.

**What makes it tractable now and not before.** `PP-15` gives one convergence brain, `PP-5` gives one
ledger, `PP-13` gives one admission core. Without those three the noun change would merely relocate
the divergence. With them, the Loop row is the last thing left.

**Clean break, deliberately.** This is a plain clean break today: no gate, no dual path, no
migration. Release notes advise `personalclaw snapshot`. That is a window, not a property —
`CONTRIBUTING.md` §"The lifecycle mental model" defers the migration-backed regime "until the
architecture stops moving, on the way to 1.0", after which the same change costs
gate → dual-path → migrate → cleanup. **No plan gates that transition** (`LIFECYCLE-DOCTRINE.md` was
deleted in #897; its guidance is now only that CONTRIBUTING mental model), so nothing will announce
the boundary — the window is self-closing, which is the whole timing argument for doing this inside
the program rather than after it.

**Implementation plan**

1. Map every field of the `Loop` row onto `WorkflowRun` + `SupervisorPolicy`, and name the ones with
   no home *before* writing code — an unmapped field is a feature about to be dropped silently.
2. Convert the five kinds to bundled templates plus policies. The domain intelligence moves into the
   policy; `LoopKindStrategy` is retired, not kept beside it.
3. Retire `loop/store.py`'s parallel row and the second watchdog. Retiring a legacy path is never a
   pure deletion — census every caller first.
4. Validate **as a user**, per the workspace bar: drive each of the five kinds end-to-end through the
   unified path with its cockpit intact, kill the gateway mid-run and confirm the single watchdog
   adopts it, and confirm the flywheel produces a proposal from a loop run's ledger.
5. Keep `triggers/` out. It answers whether to **start**, not what may run, and folding it in would
   put wall-clock into a core whose purity is what makes rewind and replay work.
