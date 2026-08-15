# Plan: Platform Primitives — Edges, Verdicts and Policies as First-Class Nouns

**Status:** IN PROGRESS — 6 of 16 atoms shipped (`PP-4` ledger extraction, `PP-1` `WF_UNORDERED_DEP`, `PP-3` the `output_contract` reader cross-check, `PP-9` the general outcome record, `PP-11` the `AdmissionPolicy` seam, and `PP-10` consumer-liveness detection, all 2026-08-14 — see `## Execution log`). Startable now: `PP-2` (unblocked by `PP-1`), `PP-6`/`PP-8` (by `PP-4`), `PP-12` (by `PP-11`), and `PP-5`/`PP-14` (by `WF2LOO-16`). `PP-7` waits on `PP-6`, `PP-13` on `PP-12`. 16 atoms in [`../atomic/PP.md`](../atomic/PP.md).
**Status:** IN PROGRESS — 2 of 16 atoms shipped (`PP-4` the ledger extraction and `PP-9` the general
outcome record, both 2026-08-14 — see `## Execution log`). Five startable now (`PP-1`, `PP-6`,
`PP-8`, `PP-10`, `PP-11`): `PP-4` unblocked four, and `PP-9` landing unblocked `PP-10`. `PP-5` still
waits on `WF2LOO-16` and `PP-7` on `PP-6`. 16 atoms in [`../atomic/PP.md`](../atomic/PP.md).
**Status:** IN PROGRESS — 2 of 16 atoms shipped (`PP-4` the ledger extraction and `PP-11` the
admission seam, both 2026-08-14 — see `## Execution log`). Five startable now (`PP-1`, `PP-6`,
`PP-8`, `PP-9`, `PP-12`): `PP-4` unblocked four and `PP-11` unblocked `PP-12`. `PP-5` still waits on
`WF2LOO-16` and `PP-7` on `PP-6`. 16 atoms in [`../atomic/PP.md`](../atomic/PP.md).
**Status:** IN PROGRESS — 6 of 16 atoms shipped (`PP-4` ledger extraction, `PP-1`
`WF_UNORDERED_DEP`, `PP-3` the `output_contract` reader cross-check, `PP-9` the general outcome
record, `PP-11` the admission seam, and `PP-2` deriving ordering from bindings — all 2026-08-14,
see `## Execution log`). Startable now: `PP-6`, `PP-8`, `PP-10`, `PP-12` (deps `PP-4`/`PP-9`/`PP-11`
all landed). `PP-5` and `PP-14` are unblocked by `WF2LOO-16`; `PP-7` still waits on `PP-6`. 16 atoms
in [`../atomic/PP.md`](../atomic/PP.md).
<!-- Header consolidated 2026-08-14 (PP-2): three sibling branches (PP-1/PP-3, PP-9, PP-11) each
left their own `**Status:**` block on merge; they are folded into the single accurate line above. -->

**Pillar:** A (Execution Engine + Convergence) · **rev 17** (2026-08-14)

**Soul guardrail.** Everything here stays personal-scale: one user, local files, local SQLite,
one gateway. This plan removes duplicated machinery; it adds no fleet, no service tier, and no
distributed substrate. If an atom below starts to look like a platform for other people's
workloads, it has drifted and should be re-scoped.

---

## Why this plan exists

Two research passes — one on graph-structured agent execution, one on loop engineering — produced
twenty findings between them. Working through them, all but a handful turn out to be symptoms of the
same three absences:

> **PersonalClaw implements one engine's worth of semantics three times, because three of its
> primitives were never named: edges, verdicts, and policies.**

The graph findings are what happens when *edges* are not a primitive — ordering and dataflow are two
hand-maintained lists that nothing reconciles. The loop findings are what happens when *verdicts* and
*policies* are not primitives — "was this good enough?" is answered in six vocabularies and "how much
freedom does this work have?" in fourteen places.

Naming the three nouns is the material change. It is also cheapest right now — see §6.

This plan is the answer to the state the engine program recorded twice: the remaining work needs
*"a coherent multi-session program or an owner scope decision — not a single atomic session bolted
on"* ([`../WF2-SESSION-QUEUE.md`](../WF2-SESSION-QUEUE.md), 2026-08-12).

## Research provenance

Public prior art, cited by mechanism so a session can go read the source:

* **arXiv:2604.11378** — *From Agent Loops to Structured Graphs: A Scheduler-Theoretic Framework for
  LLM Agent Execution* (Apr 2026, position paper, 70 systems surveyed). The load-bearing idea: an
  agent loop is a *"single ready unit scheduler"*, and graph engines differ from it in **degree**, not
  kind. This is the licence for §4's unification.
* **Fluxtion, *Graph Engineering Needs a Compiler*** (2026-07-29). Two arguments this plan adopts:
  *topology underdetermines execution* (a diagram cannot say whether B precedes C, when changes become
  visible, or whether D runs if B produced no change), and *do not keep a second edge list* — declare
  structure once, derive coordination. Its codegen half is **rejected** (§7).
* **The loop-engineering corpus** (Jun–Aug 2026): the five-component loop anatomy
  (discovery / handoff / verification / persistence / scheduling), the four stacked loops whose fourth
  *"reaches inside and updates the agent loop directly"*, the maker-never-grades rule, and the field
  post-mortem of a fully autonomous pipeline that ran dead for two months because nobody owned acting
  on its output.
* **A 2026 review-loop essay** supplying the verifier failure taxonomy this plan uses by name —
  *weak test, reward hacking, correlated reviewer mistakes* — and the diagnostic question every gate
  in this repo should survive: **"what is the laziest thing that passes, and would I still accept it?"**

## §0 Ownership map — what is re-homed, not duplicated

Checked against all 69 existing plans and 620 existing atoms before writing a line. Four of the seven
theses already had owners and are **amendments to those plans**, not atoms here:

| Thesis | Owner | Disposition |
|---|---|---|
| Verdict primitive — the third vocabulary | LOOPS-EVOLUTION | **`WF2LOO-16`** (new atom there). `WF2LOO-13` reconciled two of three; `WF2LOO-12`'s design already *named* `loop/judge.CycleVerdict` as the third and left it unowned. |
| Judge model independence | LOOPS-EVOLUTION | **`WF2LOO-17`** (new atom there). |
| Worker-independent stall signal | LOOPS-EVOLUTION | **`WF2LOO-18`** (new atom there). |
| Autonomy as one policy object | AUTONOMY-GUARDRAILS | **`AG-13`** (new atom there), depending on `PP-14` so the autonomy ceiling and the supervisor policy are ONE object rather than two competing declarations. The two-level `Ceiling ∩ Profile` model it composes is PLATFORM-HARDENING-FLOORS §5's, reused not rebuilt. |
| Outcome/consumption records | PROACTIVE-ASSISTANT `PA-4` | **Coordinated**: `PP-9` generalizes LEARN-R18's existing `pending_outcome`/`outcome_resolved` pair rather than adding a second outcome facility. `PA-4`'s decision journal must land on the same one. |
| Task-pool leases | TASKS-SOPS `WF2TAS-6`/`-10` (done) | **Reused**: `PP-12` adopts the shipped compare-and-swap lease decision functions as an admission policy; `PP-13` retires only the duplicate projection. |
| Pure frontier core | WORKFLOWS-V2 `WV-3` (done) | **Extended, not replaced**: `PP-11` puts a seam behind it with a byte-identical acceptance bar. |

Everything else — edges, the ledger as a platform primitive, replay, trajectories, the admission core
and the work-unit noun — has no owner, and is §1–§5 below.

## §1 Edges — dataflow declared once, ordering derived (`PP-1`…`PP-3`)

Two hand-maintained edge lists over one dependency truth. Admission reads `needs` only, `needs` is
sibling-scoped by `validator.py:221-230`, and bindings are a separate graph feeding the cache key, the
stale-inputs check and the mutation cascade but never admission. `validator._kahn_levels` already
builds the combined graph and its own docstring says its levels are not an execution order.

`PP-1` makes the disagreement a typed error. `PP-2` derives ordering from bindings and — because the
derived graph is global and already proven acyclic — lifts the sibling-only restriction, which makes a
**diamond spanning two containers expressible for the first time**. `PP-3` closes the matching gap on
the other side: `output_contract` is checked against its producer and never against its readers.

## §2 The ledger as a platform primitive (`PP-4`…`PP-8`)

`workflows/journal.py` is the best observability surface in the tree and only one feature has it.
`learning/mining.py` derives from *"the run's own journal"*, so the outer improvement loop covers one
of four work-unit kinds while the loop kinds carry the long-horizon autonomous work.

One vocabulary unlocks seven findings at once: loop→flywheel coverage (`PP-5`), replay as a real verb
(`PP-6`), trajectory signatures (`PP-7`), edge statistics (`PP-8`), consumer liveness (`PP-10`),
attention budgeting, and per-subtree spend — the last three as projections, needing no new store.

## §3 Outcomes — measure what landed, not what ran (`PP-9`, `PP-10`)

The platform records what it *did* and never what *landed*. That one shape produces loop output nobody
reads, unmeasured routing edges, and the declared-but-inert class this repo keeps rediscovering by AST
audit rather than at runtime. The mechanism already exists, scoped to one feature: LEARN-R18's
`pending_outcome` / `outcome_resolved` pair, with `measured` kept distinct from `inconclusive` so an
unmeasurable outcome decays faster. `PP-9` generalizes it; `PP-10` uses it to surface a work unit whose
output nobody consumes — as a **proposal**, never an automatic stop, because "nobody looked yet" and
"nobody will ever look" are different facts and only the user knows which.

## §4 One admission core, N admission policies (`PP-11`…`PP-13`)

Four schedulers answer *"what may run now, given persisted state"* and share zero lines — verified:
`pool.py` imports only stdlib, and `loop/tick.py` and `workflows/tick.py` never import each other.
Each holds a capability the others structurally cannot express: leases only in the pool, dwell/metric
gates only in loops (consumed by exactly one kind), lanes only in workflows.

`PP-11` is seam-only with a byte-identical golden-file bar — a refactor that also changes behaviour
cannot be verified. `PP-12` adds `Lease` and `Dwell`/`MetricGate` on the proven seam. `PP-13` retires
the pool's duplicate projection.

## §5 One work-unit noun (`PP-14`…`PP-16`)

**A loop is not a second engine. A loop is a graph shape plus a supervisor policy.** The shape exists;
the policy has no home, which is why it got implemented twice with each side missing what the other
has. And this connects the two research tracks: *put the judgment in the edges* means a convergence
decision — continue / hold / advance / rollback / replan / escalate / complete — **is** an edge
decision, and belongs where `branch` and `gate` decisions live.

`PP-14` lands the declaration deliberately inert, with the honesty marker this program established
(`WF2LOO-12`): a control with no caller must say so, railed in both drift directions. `PP-15` widens
`loop/tick.evaluate` — pure, restartable, already the right shape, currently used by one kind — into
the single convergence brain, and makes `REPLAN` queue a real mutation batch instead of the
retry-with-a-hint it is today. `PP-16` is the noun change.

## §6 Sequencing, and the timing argument

```
PP-1 ─→ PP-2, PP-3
PP-4 ─┬─→ PP-5 (+WF2LOO-16) ─────────────┐
      ├─→ PP-6 ─→ PP-7                   │
      ├─→ PP-8                           │
      ├─→ PP-9 ─→ PP-10                  │
      └─→ PP-11 ─→ PP-12 ─→ PP-13 ───────┤
WF2LOO-16 ─→ PP-14 ─→ PP-15 ─────────────┴─→ PP-16
                  └─→ AG-13
```

**Startable now:** `PP-1`, `PP-4`, `WF2LOO-16`, `WF2LOO-17`, `WF2LOO-18`.

**If only three land: `WF2LOO-16`, `PP-1`, `PP-4`.** Verdict, edges, ledger. Cheapest, they unblock
everything else, and between them they close eleven of the twenty research findings.

**The timing argument, which is the part that needs an owner decision.** `PP-4`, `PP-5`, `PP-9` and
`PP-16` are **state-shape changes** (class B). Today they are plain clean breaks: no gate, no dual
path, no migration, no cleanup. That is not a permanent property —
[`CONTRIBUTING.md`](../../../CONTRIBUTING.md) §"The lifecycle mental model" says the migration-backed
regime is *"deliberately deferred until the architecture stops moving, on the way to 1.0"*, after
which a B change is governed as gate → dual-path → migrate → cleanup.

**Note carefully: nothing will announce that boundary.** There is no plan to wait for —
`LIFECYCLE-DOCTRINE.md` was **deleted** as a plan in #897 ("deliverables-only DAG"), and its guidance
now lives *only* as that CONTRIBUTING mental model, explicitly "a mental model, not shipped
machinery". So the window is **self-closing rather than scheduled**: it narrows as the architecture
stabilises, and no milestone will conveniently mark the deadline. These four therefore get strictly
more expensive the later they land, which is the whole argument for taking them inside this program
rather than deferring them past it.

`PP-1`, `PP-2`, `PP-3`, `PP-11`, `PP-14` and `PP-15` are pure-logic and can land at any time.

## §7 Deliberately excluded — recorded so they are not mistaken for unbuilt ideas

* **Folding `triggers/` into the admission core.** It answers whether to *start*, not what may run.
  Merging it would put wall-clock into a core whose purity is what makes rewind and replay tractable.
* **Turning the ledger into a database.** Append-only JSONL plus projections is exactly what makes
  epoch invalidation, rewind and replay work. Keep it.
* **Collapsing `stage`/`infer`/`transform`/`action`.** The cost-aware kind split is an asset — it is
  the structural form of "do it with code, then a small model, then the smallest LLM that works".
* **Compiling a generated orchestrator** (Fluxtion's full proposal). Our specs are user- and
  model-authored JSON **edited mid-flight**; codegen would kill mutation, which is a differentiator.
  Take the inference (`PP-2`), leave the codegen.
* **A worker/trigger/function substrate.** Distributed-platform architecture for a single-user local
  product — a rewrite for no user-visible gain.
* **A bi-temporal context graph** (valid-time vs transaction-time over domain entities). Enterprise
  reconciliation machinery; cost without a user here. `publish.py`'s typed lineage
  (`SOURCE`/`INFORMED_BY`/`RELATED`) already covers the provenance question we actually have.
* **A fifth verdict vocabulary**, which is the default outcome of doing `PP-11` or `PP-16` before
  `WF2LOO-16`.

## Success criteria

1. A binding whose producer is not ordered before it is refused at authoring time, and the bundled
   template population was censused before the rule got teeth.
2. A workflow diamond spanning two containers runs correctly, and WF2-R18's join tests stay green.
3. `learning/` produces a proposal from a **loop** run's evidence — not merely rows in a file.
4. A completed run replays to a byte-identical trajectory, and an edited prompt diverges at exactly
   the edited node.
5. `frontier()`'s decisions are byte-identical across the `PP-11` refactor, proven by golden file.
6. A fan-out serializes on a leased resource across a gateway restart.
7. A run re-derives its remaining steps from a judge critique (`REPLAN` does something).
8. Each of the five loop kinds is driven end-to-end through the unified work-unit path with its
   cockpit intact, and a mid-run kill is adopted by a single watchdog.
9. Every declaration this plan adds either has a production caller or says in its own docstring that it
   does not, railed in both directions.

## Execution log

- **2026-08-14 — `PP-1` DONE.** `WF_UNORDERED_DEP` (the 47th `WF_*` code) refuses a binding whose
  producer is not ordered before its reader. **The two edge lists now have to agree:** admission reads
  `needs` plus container order (`tick._visit_parallel`), while bindings are a separate graph feeding
  the resume cache, the stale-inputs check and the mutation cascade — so a spec could bind
  `{{nodes.x.output}}` from a node running *beside* `x` and die at ready-time with *"binding failed:
  check the referenced node id and field exist"*, pointing the author at an id that was perfectly
  correct. Locally plausible, globally wrong, discoverable only by running it.
- **2026-08-14 — `PP-1` design notes.** (a) `dep_ordering_edges` derives every edge from
  **`bindings.node_deps`** — the same function the cache, stale-inputs check and cascade use — so the
  rule structurally cannot disagree with them about what a binding depends on. (b) An unknown id is
  **skipped**, because `WF_UNKNOWN_NODE_REF` already owns that and reporting both would turn one typo
  into two errors. (c) The rule stays **silent when `WF_CYCLE` fired**: on a cyclic graph the only
  advice it can give ("order the producer first") is the advice that closes the loop, so the cycle is
  both the truer fact and the one to fix first. (d) The message names all three facts — reader,
  producer, and *why* the ordering is absent — so an author acts without reading the engine.
- **2026-08-14 — `PP-1` CENSUS (the atom's precondition, run BEFORE the code became an error).**
  **19** bundled `workflow.json` templates; **18 of the 19 contain `{{nodes.*}}` bindings; ZERO declare `needs` at all.** So virtually the WHOLE library depends on this rule, and it
  rests entirely on the earlier-sibling-in-a-`sequence` path, and the `needs`-chain path ships
  exercised by no shipped template. **Violators: 0** — every one of the 18 already orders its
  producers, so the new refusal ships against a population that passes it. Giving a never-run rule
  teeth against a population that fails it is an outage, not a gate; here there was nothing to fix.
- **2026-08-14 — `PP-1` zero-runtime-change proof.** `src/personalclaw/workflows/tick.py` is
  **byte-identical** (`git diff` empty). No scheduling behaviour changes; this is authoring-time
  validation only. Because ZERO templates declare `needs`, the census made the atom's **vacuity floor
  load-bearing rather than ceremonial**: a naive implementation passes by never finding a dep to
  check, so the floor asserts the rule saw a non-empty `node_deps` set on at least one shipped
  template.
- **2026-08-14 — `PP-1` DISCOVERY (process, not code).** The implementing subagent died to a stream
  watchdog mid-falsification with an uncommitted tree, having **left probe 2 applied** — an injected
  `{{nodes.find_safety.output}}` in `bundled/audit-sweep/workflow.json` plus incidental `\u2014`
  re-serialization damage from a `json.dump` round-trip. Inheriting such a tree, the probe must be
  identified and reverted BEFORE the work is trusted, then the work committed immediately as a
  recoverable checkpoint. A `wip:` commit costs nothing; reconstructing lost work costs a session.

- **2026-08-14 — `PP-6` DONE.** `workflow replay <run_id>` (`personalclaw/workflows/replay.py` +
  a CLI verb) re-drives the PURE `frontier()` against a run's OWN recorded responses and diffs the
  resulting trajectory against the one the run took, reporting the FIRST divergent node. The
  nondeterminism envelope is complete: provider responses were already spilled by `output_ref` (PP-4)
  and the resolved prompt already stored by `_store_prompt`; the missing third — the WALL CLOCK — now
  goes through a seam (`EngineServices.clock` → `RunController._clock`) and is journaled as a new
  `clock_read` ledger kind whenever `_wake_due_nodes` resolves a parked node. Divergence is a
  first-class outcome: the verb (and `replay_run`) return cleanly for both a byte-identical replay and
  a divergent one; only an unreplayable run (no spec / no steps) raises. Verified by a 3-node infer
  pipeline replaying byte-identical and by an edited prompt diverging at exactly the edited node with
  the prior node proven identical; the clock seam is exercised by a `wait`-containing run.
- **2026-08-14 — `PP-6` DEVIATION.** Replay re-drives `frontier()` in a dedicated `ReplayDriver`
  (in the new `workflows/replay.py`), NOT by re-running `RunController`. The territory scoped the
  controller change to the clock-read SEAM only, so the re-drive loop lives on the replay side and
  the recorded clock is a `RecordedClock` of the same `() -> float` shape the controller's seam
  accepts — substitutable into the controller in principle, and the seam the replayed path reads
  through in practice. `frontier()` and `tick.py` are byte-unchanged (read, not modified). The clock
  seam covers `_wake_due_nodes` and the `now` a `wait` computes its deadline against (one clock, or a
  recorded `now` would set a deadline the recorded wake never crosses); the stall/duration clocks
  stay on real `time.time()` on purpose.
- **2026-08-14 — `PP-6` DISCOVERY.** (a) `_adaptive_duration` floors `duration_secs` with `int()`,
  so a fractional wait (`0.01`) resolves INSTANTLY with no `WAITING` state and therefore no clock
  read — the envelope only records for `duration_secs >= 1`, which the clock test relies on. (b) The
  byte-identical claim is only falsifiable by a perturbed response when the perturbed node has a
  downstream CONSUMER that binds its output: perturbing an output moves the consumer's RE-RESOLVED
  prompt, not the node's own step (whose recorded prompt file is untouched), so the pipeline test
  binds each node's output into the next. (c) Adding `CLOCK_READ` to `LEDGER_KINDS` re-sweeps the
  `test_ledger_golden` emitters fixture (its `_registered_kinds()` probes every kind); the two
  emitters goldens were regenerated deliberately (one new `clock_read` line each + seq renumbering),
  and the RUN goldens are untouched because `GOLDEN_SPEC` has no `wait`.

*(append `DONE` / `DEVIATION` / `DISCOVERY` / `BLOCKED` entries here, per the roadmap session
discipline in [`AGENTS.md`](../../../AGENTS.md))*

- **2026-08-14 — plan filed.** 16 atoms authored into `../atomic/PP.md` and `dag.json`; three atoms
  re-homed to LOOPS-EVOLUTION (`WF2LOO-16`/`-17`/`-18`) and one to AUTONOMY-GUARDRAILS (`AG-13`)
  rather than duplicated here. Derived dag block regenerated: 640 atoms, 145 ready, 876 edges, 0
  dangling, no new cycles. No code touched.

- **2026-08-14 — `PP-4` DONE.** `personalclaw/ledger/` now owns the mechanism: `kinds.py` (the 44
  kind constants + `LEDGER_KINDS`), `writer.py` (`LedgerWriter` — sequencing, stamping, the
  `events.jsonl` mirror, `MAX_INLINE_OUTPUT_BYTES`/binary spill + the `result_omitted` stub, and
  the journal fold that recovers `seq`), `redaction.py` (`redact`, magic-prefix detection),
  `hashing.py` (`stable_json`, `hash_value`), `reader.py` (`read_events`, `run_totals`).
  `workflows/journal.py` went 1019 → 685 lines and is now `Journal(LedgerWriter)` with
  `_store = workflows.store`. Nothing under `ledger/` imports `personalclaw.workflows`, and that is
  now a RAIL rather than a convention: `test_the_ledger_package_does_not_import_the_workflow_engine`
  AST-scans the package (statically, because the way this creeps back is a lazy function-local
  import that no import-time probe would see) and reds naming the file and line. `PP-5` is the
  reason it exists — a loop emitter that had to pull the engine in to journal a cycle would have
  re-created the dependency this atom reversed.

- **2026-08-14 — DISCOVERY (`PP-4`): the seam is node identity, not "generic vs specific".** The
  first cut tried to split by "is this workflow-shaped?" and stalled on `_load_cache`, which folds
  the file by `cache_key` (generic) but is read by `lookup` via `SUCCESS_STATES` (an engine enum).
  The line that actually holds: a thing belongs to the facade iff it needs a node path, an `epoch`,
  an `InstanceState` or a `Failure` to mean anything. That puts the ~26 typed emitters, `CacheKey`,
  `lookup`, `invalidate_prefix` and `spec_region_hash` in `workflows/journal.py`, and everything
  else in `ledger/`. `_load_cache` lands in the WRITER, because that same pass recovers `seq` —
  a rebuilt writer that restarted its sequence would re-mint `event_id`s the file already holds,
  which is what makes a re-emit idempotent.

- **2026-08-14 — DECISION (`PP-4`): the kind constants live in `ledger/kinds.py`, not the facade.**
  §2's "one vocabulary" is the reason: `PP-5` has loop cycles emitting `step_started`,
  `judge_verdict`, `breaker_trip` and `watcher_reaped` — the SAME words, from a non-workflow
  producer. A registry left inside `workflows/` would force `loop/` to import the engine (the
  forbidden direction) or fork the vocabulary into the fifth dialect `PP-5` exists to prevent. All
  44 are re-exported from `workflows/journal.py`, so the `LEDGER_KINDS` drift tests in
  `test_workflows_longrun.py` and `test_workflows_projection_events.py` still bind unchanged.

- **2026-08-14 — DISCOVERY (`PP-4`): the `result_omitted` stub is invisible to a journal diff.**
  `store_output` writes the artifact and RETURNS the stub; the run journals only the `output_ref`.
  Diffing `journal.jsonl` + `events.jsonl` alone would have left `result_omitted`, `bytes`, `reason`
  and the head+tail `preview` — three of the four things this atom's `done_when` names — unproven.
  `tests/fixtures/ledger_golden/emitters_spill.jsonl` captures the return value of all three spill
  paths (inline / oversize / binary) so the stub shape is in the diff.

- **2026-08-14 — `PP-4` proof.** Goldens captured on the branch point with `journal.py` unmodified
  (`git status` clean, hashes recorded), then re-diffed after the move: byte-identical. Five
  fixtures — a real 3-node `RunController` run (8 journal lines, 3 events, one node spilling
  oversize and one binary), one raw `write()` per registered kind (50 journal / 47 events, which is
  how the mirroring table itself is asserted), and the three spill returns. Falsified three times:
  shortening the `event_id` stamp to `-ev-` reds both golden tests at line 1 of `run_journal.jsonl`
  and `emitters_journal.jsonl`, and dropping `MUTATION_REJECTED` from the facade's re-exports reds 3
  tests across `test_workflows_fork.py` (ImportError) and `test_workflows_mutation_queue.py`
  (AttributeError); adding `from personalclaw.workflows import store` to `ledger/reader.py` reds the
  boundary rail naming `reader.py:14`. Packaging: nothing to change — the wheel finds `ledger/` by
  auto-discovery
  (`packages.find`), and `personalclaw-backend.spec`'s `hiddenimports` exists only for
  importlib-loaded modules, which a statically-imported pure-Python package is not.

- **2026-08-14 — CENSUS (`PP-3`): the warning's population is the whole shipped library, which is
  why the warning is scoped.** Measured over the 19 bundled templates before writing the rule: **18
  of 19 carry `{{nodes.*.output}}` reads — 145 distinct reads, 45 bare and 100 at a sub-path (151
  before deduplicating a ref that appears twice in one node) — and ZERO declare an `output_contract`
  at all.** Two consequences. (1) The ERROR half has an empty population here: nothing shipped is
  wrong, and nothing shipped exercises it, so the unit tests carry the whole weight and
  `test_the_rule_RESOLVES_a_read_against_a_real_contract` is the vacuity floor that keeps a rule
  which resolved *nothing* from reading as a rule which resolved fifty satisfiable paths — both are
  silent. (2) The WARNING half, unconditionally, fires **77** times across **18 of 19** templates
  (**49** if only sub-path readers count). Every template warning on every validation is how an
  author learns to skim validator output — and it would also contradict
  `test_it_validates_STRICTLY`, whose stated contract is that a bundled template ships no warning at
  all, so the alternatives were "weaken that gate" or "author ~49 contracts across 18 shipped
  templates". The second is worse than it looks: `must_be_json` + `required_keys` is *enforced* at
  run time by `engine.check_output_contract`, so a contract guessed onto a producer whose model
  sometimes answers in prose converts a working template into a failing one.

- **2026-08-14 — DEVIATION (`PP-3`): the warning is scoped to specs that already use contracts.**
  The atom says "a producer read structurally but declaring no contract raises a WARNING naming the
  readers". Severity, content and trigger are all as written; a precondition is added — the spec must
  declare at least one `output_contract` somewhere. Rationale: an author who has adopted the
  mechanism and left one producer out has an actionable inconsistency, while a spec with no
  contracts anywhere has not adopted it and is being nagged, not validated. Two further narrowings
  fall out of the same reasoning: only sub-path reads count (a contract buys nothing checkable for
  `{{nodes.x.output}}` — there is no path it could have judged), and the warning is ONE per producer
  naming all its readers rather than one per read. **Measured volume on the shipped library: zero.**
  Honest limitation recorded rather than papered over: no generator in the tree currently produces
  both a contract and a cross-node sub-path read — `batch_compile` emits `output_contract` for every
  typed leaf, but compiled leaves do not read each other's output — so the warning's live population
  today is hand-authored specs, and its mechanism is proven by unit tests
  (`test_one_contract_anywhere_turns_the_warning_on` is the switch, asserted against its own
  contract-free twin).

- **2026-08-14 — DONE (`PP-3`): `output_contract` cross-checked against its readers.**
  `WF_UNSATISFIABLE_OUTPUT_REF` (error) fires when a reader takes a path whose first segment is
  absent from a producer's `required_keys` on a contract that also declares `must_be_json`;
  `WF_UNCONTRACTED_OUTPUT_REF` (warning, scoped as above) names the producer that declares nothing
  and the readers that take paths through it. No new vocabulary: `must_be_json` + `required_keys`
  are read exactly as `engine.check_output_contract` reads them, and `engine.py` is untouched. Only
  the FIRST path segment is judged — `required_keys` is a promise about an object's top level, and
  judging `findings.0.verdict` would mean inventing nesting the engine cannot enforce. Both halves
  of the pairing are required: `required_keys` alone is the shape
  `batch_compile.schema_to_contract` emits for a schema with no `type`, and refusing it would refuse
  the author who described their output least. **Built on `PP-1`'s edge list rather than beside it**
  — `DepEdge` gained an `output_reads` field derived from the same `bindings.refs_in` scan
  `node_deps` is built on, `validate_node_tree` computes the list ONCE and hands it to both rules,
  and `test_the_read_paths_ride_the_SAME_edge_list_as_the_ordering_rule` asserts every producer the
  paths name is one `node_deps` already found. A second reference parser would have been the exact
  two-edge-list defect this pillar exists to remove. Gate: `make lint` exit 0, `pytest -n 0` 431
  passed across `test_workflows_validator.py` (70, +33) / `test_workflows_bundled.py` (241, +5) /
  the two batch modules, full suite green. Falsified three times: (1) the atom's named probe —
  giving `knowledge-health`'s `scan` an agreeing contract (`required_keys: ["report"]`, library
  still "Spec is valid.") then renaming it to `["summary"]` while `verdict` still reads
  `output.report` — reds `test_it_validates_STRICTLY[knowledge-health]` plus three census tests with
  a message naming reader, producer and path; (2) dropping the `must_be_json` requirement reds
  `test_required_keys_WITHOUT_must_be_json_never_errors`, proving the rule does not over-refuse an
  under-declared contract; (3) making the rule resolve nothing reds both vacuity floors
  (`test_the_rule_RESOLVES_a_read_against_a_real_contract` and
  `test_the_rule_sees_the_measured_read_population`) rather than going quietly green.

- **2026-08-14 — DISCOVERY (`PP-3`): a bundled ACTION node cannot declare an `output_contract`
  without also touching a test rail.** `test_workflows_bundled.py:597`'s flat-argument rail allows
  only `("provider", "with", "context", "payload")` in an action node's config, so the falsification
  probe above tripped it as a stray argument — even though `engine.py:409` reads `output_contract`
  from exactly that config for action nodes. Left alone deliberately: no shipped template declares
  one, and widening a rail for a population of zero is speculative. Whoever adds the first
  action-node contract (or `PP-2`, which will move these edges) should widen that allowlist in the
  same change rather than discovering it as an unrelated red.
- **2026-08-14 — `PP-9` DONE.** The outcome pair is now a general facility: `ledger/outcomes.py`
  owns the producer vocabulary (`decision`/`publish`/`escalation`/`proposal`/`control`, a CLOSED set
  so a typo is a loud `ValueError` at the open rather than a producer nobody can query for), the two
  resolutions, the two metric SOURCES, the idempotency subtraction (`open_questions`), the
  benchmark-relative `score`, and the `OutcomeLedger` mixin carrying `open_outcome`/`resolve_outcome`
  — mixed into `LedgerWriter`, so every producer that can carry a ledger can open a question, not
  just the one feature that first needed it. `journal.pending_outcome`/`outcome_resolved` survive as
  thin WORKFLOW-SHAPED adapters that contribute `instance_path`/`node_id`/`epoch` and nothing else,
  which is where the `ledger/` boundary rail forces them to live. `PA-4`'s decision journal lands on
  this facility as `PRODUCER_DECISION` with its own `context` fields — one facility, stated in the
  module docstring so the next session does not build a second one. Two non-decision producers wired
  in the same change: `engine.apply_publish` opens `artifact.<slug>.consumed` (memory-sourced, a
  7-day horizon, baseline 1.0 — one consumption is the whole bet) and `controller._ask_for_input`
  opens the escalation's bet beside `confirmation_pending`, ledger-sourced on the
  `confirmation_resolved` its own `confirmation_id` will carry. Gate: `make lint` clean (black /
  isort / flake8 / mypy over 822 source files), 916 learning+ledger tests and 4593 workflows tests
  green, full suite 19 194 passed / 30 skipped / 12 xfailed / 0 failed. Collection 19 207 → 19 234;
  a function-name diff against `origin/main` shows 28 added and one removed, and that one is the
  rename of `test_no_vector_store_is_a_noop` (its clause changed, see the DEVIATION below) — no test
  was deleted. The ledger golden fixtures were regenerated in the same commit: `pending_outcome`
  gains `producer`/`metric_source` and loses `resolved_at`, `outcome_resolved` gains
  `producer`/`decay_profile`, and nothing else in 50 emitter lines moved.

- **2026-08-14 — DEVIATION (`PP-9`): the resolver is no longer inert without a vector store, and
  `resolved_at` is deleted.** LEARN-R18 returned an empty report unless a live vector store was
  injected, which was right when every metric was a semantic-memory key. It is wrong for a general
  facility: an escalation's ground truth is an event the run wrote itself. So availability is now
  per-SOURCE — a memory-sourced question with no vector store is counted `pending` and left OPEN
  (spending it as `inconclusive` would charge a missing dependency to the bet), while a
  ledger-sourced one grades on any box. `service is None` still short-circuits. Separately,
  `pending_outcome` carried a `resolved_at: ""` field that its only writer wrote empty and no reader
  ever read — the resolution is a separate event — so it is gone rather than carried forward.

- **2026-08-14 — DISCOVERY (`PP-9`): "after the bet" must be FILE POSITION, not `seq`.** The
  ledger-sourced measurement first ordered candidate events by `seq`, and the escalation test read
  its own answer as unmeasurable. Cause: `Journal(run_id)` is a fresh dataclass with `seq = 0`, and
  `_load_cache` only recovers the sequence when something asks for a cache lookup — so a SECOND
  writer built for a run that already has 40 events starts at 1 and re-mints `event_id`s the file
  already holds (the resolver itself builds one). Append order is the only ordering the log actually
  guarantees, so `measure_from_events` scans forward from the question's own position. The colliding
  `event_id` is a pre-existing hazard for anything keyed by it and is left named here rather than
  fixed inside this atom.

- **2026-08-14 — DECISION (`PP-9`): only a DECISION's outcome files a lesson proposal.** Generalizing
  the producers would otherwise generalize the queue noise: every publish would file "this
  artifact's outcome is inconclusive", which the user cannot act on and which `PP-10` is the atom
  that knows how to interpret. `_PROPOSING_PRODUCERS` is one frozenset in the resolver, and the
  publish/escalation producers write their outcome to the ledger and stop there.

- **2026-08-14 — `PP-9` proof.** Falsified three times, each with the target line read first and
  restored from a file copy (never `git checkout --`): emptying `open_questions`' answered set reds
  `test_a_second_tick_is_idempotent` and `test_an_answered_question_is_not_open`; collapsing
  `DECAY_PROFILE` so both resolutions name `speculative` reds
  `test_the_two_resolutions_map_onto_different_decay_profiles` and
  `test_an_inconclusive_outcome_decays_out_while_a_measured_one_survives` (that one asserts the
  OUTCOME — at 60 active days the kernel prunes the inconclusive evidence and keeps the measured, not
  merely that a field differs); removing the `_open_publish_outcome` call from `apply_publish` reds
  `test_publishing_an_artifact_opens_an_outcome`, which drives the real `publish:` seam against a
  fake artifact provider rather than calling the emitter directly. The escalation producer is driven
  end-to-end against a really-parked gate in `test_workflows_confirm_emission.py`, including the
  answer that measures it and the re-poll that must not open a second question.
- **2026-08-14 — `PP-11` DONE.** `workflows/admission.py` now owns the answer to "may this start?":
  `AdmissionRequest(scope, key, node)` → `AdmissionPolicy.capacity()` → `compose()` → an `Admission`
  verdict with `bounded` / `admits(in_flight)` / `hold` / `binding`. The three rules `frontier()`
  already applied are the first three policies — `Lane` (WF2-R21 typed caps), `ContainerConcurrency`
  (a `foreach`'s `max_concurrency`) and `Wip` (the run-level `single_active_feature` invariant) —
  built once per `frontier()` call by `default_policies()` and threaded down the `_visit` recursion,
  replacing the `wip: bool` that used to ride the same path. `tick.py` lost its private
  `_max_concurrency()` and its inline `cap = 1 if wip else …`; both call sites now read a composed
  verdict. `Limits` moved to `admission.py` (the lane caps ARE a policy) and `tick.Limits` keeps
  resolving because the frontier's signature genuinely uses it — a move, not a re-export shim. No
  other scheduler touched: `loop/tick.py`, `pool.py` and `triggers/` are byte-for-byte unchanged.

- **2026-08-14 — DESIGN (`PP-11`): one bucket shape covers both of today's admission questions.**
  Lane admission ("may one more `llm` node start?") and container admission ("may one more item of
  this fan-out start?") looked like different mechanisms and are the same one — *a capacity over a
  keyed bucket*. Hence one `Scope` enum rather than two policy lists, and `capacity()` returning
  `None` to ABSTAIN rather than a sentinel meaning unbounded. The abstain/unbounded distinction is
  load-bearing twice over: an inactive `Wip` composes exactly as if the policy did not exist, and an
  unbounded verdict lets `_visit_foreach` skip counting in-flight items entirely — which it always
  did (`if cap:`), and which matters because counting walks every item's subtree.

- **2026-08-14 — DECISION (`PP-11`): tightest-wins, and a TIE goes to the named refusal.** Composing
  by minimum is the only rule an added policy cannot loosen, which is the property that lets `PP-12`
  append `Lease` without re-auditing these three. But two policies genuinely tie: `max_concurrency:
  1` under WIP=1, both binding at 1. The number being equal does not make the refusals equal — one
  is a declared invariant the run records by name in `wip_held`, the other is anonymous container
  pressure recorded nowhere. So policies carry a `rank` (`RANK_INVARIANT` > `RANK_CAPACITY`) used
  ONLY to break capacity ties, and the container site now records a hold when
  `verdict.hold == Hold.WIP_HELD` rather than when a `wip` flag was passed. That is exactly what
  `cap = 1 if wip else _max_concurrency(node)` meant, moved somewhere it can be tested — and it is,
  in both directions (`test_wip_names_the_refusal_when_max_concurrency_binds_at_the_same_number`
  and its anonymous twin). Had the tie been broken by list position instead, the seam would have
  silently stopped recording that the run enforced its own invariant.

- **2026-08-14 — DISCOVERY (`PP-11`): the bundled templates alone cannot prove the seam.** The
  atom's stated bar is a golden file over the bundled library, and none of the 21 templates declares
  `max_concurrency` — so a bundled-only capture leaves one of the three policies unexercised and the
  cap tie unreachable. Worse, a naive capture leaves `to_skip` empty too: the three `branch`
  selectors read `output.tier` / `output.ran`, and a fixed stub output resolves none of them, so
  nothing ever routes. Fixed both structurally rather than by hand: `_routing_seeds()` derives the
  branch-satisfying outputs FROM THE SPEC (each selector's source field seeded with that branch's
  first declared case label), and a second fixture (`policies.jsonl`) carries a six-row synthetic
  matrix for the container policies. A vacuity guard now asserts every admission outcome appears in
  the fixtures — `ready`, `deferred`, `wip_held`, `to_skip`, `blocked`, `complete`, a tick capped at
  2 of 5 items, a tick admitting all 5, and the tie — so a capture that silently stopped exercising
  a policy reds instead of passing forever.

- **2026-08-14 — DEVIATION (`PP-11`): a fidelity bug the golden caught mid-refactor.** The first
  `ContainerConcurrency.capacity()` read `max_concurrency` with `int(raw)`. `_max_concurrency()` was
  deliberately strict (`not isinstance(raw, int) or isinstance(raw, bool)` → unbounded), because
  `int(1.5)` truncates to 1 and `int(True)` is 1, so a coercing read silently serializes a fan-out
  to one item at a time — expensive and invisible, since the run still succeeds. Ported the exact
  predicate and pinned all eight cases (`2`, `1`, `0`, `-3`, unset, `True`, `1.5`, `"2"`). This is
  the class of change a golden file over bundled templates would NOT have caught, because no bundled
  template declares the key at all — the parametrized policy test is what covers it.

- **2026-08-14 — `PP-11` proof.** `tests/fixtures/frontier_golden/{bundled,policies}.jsonl` (460
  decisions: 21 templates × 4 admission scenarios, plus the 6-row container matrix × 2 lane
  pressures) were captured and committed with `tick.py` unmodified, hashes recorded
  (`25effb24…`/`255d5111…`), then re-diffed after the refactor: byte-identical, `git status` clean on
  the fixture dir. Purity re-proven two ways — an AST rail over `tick.py` + `admission.py` for clock
  / I/O / randomness imports and `open`/`print`/`id` calls, and a runtime check that repeated calls
  agree and neither `states` nor `inputs` is mutated. Falsified three times: raising the `llm` lane
  cap from 4 to 5 reds `test_every_bundled_template_still_schedules_byte_identically` with the
  specific line; making `compose()` prefer the LAST binding policy reds
  `test_the_container_policy_matrix_still_schedules_byte_identically` plus
  `test_a_tighter_capacity_still_beats_a_higher_rank`; and a `time.monotonic()` inside
  `Wip.capacity` reds `test_the_frontier_module_reads_no_clock_and_does_no_io` naming
  `admission.py:223 imports time` — and it caught a FUNCTION-LOCAL import, which is how this would
  really creep back and which no import-time probe would see. Left deliberately: `Lease` / `Dwell` /
  `MetricGate` are `PP-12`, and `pool.py`'s private projection is `PP-13` — this atom only makes the
  seam exist.

- **2026-08-14 — DISCOVERY (`PP-11`): the golden file cannot catch a composition-ORDER bug, and that
  is why the seam has its own tests.** Two of the three falsifications above red only in
  `test_workflows_admission.py`, never in the golden: making `compose()` let a later policy override
  an earlier deferral, and putting a clock inside `Wip.capacity`. Both are invisible to the fixtures
  because today's three policies happen to be order-equivalent (each abstains outside its own scope,
  and the one pair that overlaps has WIP last) and because one clock reading gives one answer. So the
  byte-identical bar proves the refactor changed no DECISION, and the composition + purity tests prove
  the seam has the PROPERTY `PP-12` will rely on. Neither alone is sufficient, which is worth knowing
  before `PP-12` appends `Lease` to the list.

- **2026-08-14 — DISCOVERY (`PP-11`): a bare `python script.py` in a git worktree imports the
  MAIN checkout, not the worktree.** The venv's editable install resolves `personalclaw` to
  `~/PersonalProjects/PersonalClaw/PersonalClaw/src`, so the golden capture — run as
  `python tests/test_workflows_frontier_golden.py` — measured the wrong tree. `pytest` is safe
  (`pyproject.toml`'s `pythonpath = ["src", "."]` is rootdir-relative), which is why the verification
  runs were valid. Re-derived the fixture from the worktree's own pre-refactor `tick.py` with
  `PYTHONPATH` pinned: byte-identical hashes, so the capture stands — the two trees agreed on every
  module the frontier touches. Regenerate these fixtures with
  `PYTHONPATH=$PWD/src python tests/test_workflows_frontier_golden.py`, never bare.

- **2026-08-14 — DECISION (`PP-11`): no CHANGELOG entry.** The atom's acceptance bar IS
  "byte-identical for every existing spec", so there is nothing a user could observe, and the
  CHANGELOG is user-facing (the in-app Updates panel reads it). The only relocation, `Limits` from
  `workflows/tick` to `workflows/admission`, is internal: it appears nowhere under
  `personalclaw/sdk/`, so no app can be depending on it. `PP-12` is the entry that will have
  something to announce.

- **2026-08-15 — `PP-7` DONE.** `introspection.trajectory_signature(run_id, events)` projects a run's
  ledger into the ordered `(node, lane, verdict)` tuples of the path it took, hashed with the shared
  `hash_value` into a signature CLASS. Exposed on the run projection (`introspect(run_id)` now carries
  a `trajectory` block: the run's signature + steps + the template's signature-class distribution +
  the regression signal, and the regression also rides `answers.risky`) and queryable per template
  without a run in hand via `service.template_trajectory(name)` / `GET /api/workflows/{name}/trajectory`.
  `introspection.trajectory_regression(template, runs)` fires when a template's contiguous recent runs
  have SHIFTED to a signature class whose failure rate is materially higher than the path it took
  before — sample-gated like `gate_stats`/`edge_stats` (below `TRAJECTORY_REGRESSION_MIN_RUNS=10`
  total, or either regime under `MIN_CLASS_RUNS=3`, it stays silent). No new store, no new ledger kind,
  no new `StateEntry`. Verified: two REAL controller runs of one template with the same inputs produce
  equal signatures; a rewind (re-execution events appended to the ledger) produces a distinguishable
  one; and the projection is proven pure by computing it twice over a deep-frozen ledger and comparing.
- **2026-08-15 — `PP-7` DEVIATION (built new over PP-6's tuples, did NOT reuse `TrajectoryStep`).**
  The territory said "reuse PP-6's `TrajectoryStep` where it fits, or directly from ordered
  node/lane/verdict tuples." `TrajectoryStep` carries `prompt_hash`, `output_ref` and `clock` — a
  replay needs those to diff a re-drive, but a *signature* must be equal for two same-input runs, and
  `output_ref` is a content hash of the model output that varies run-to-run. So the signature is
  projected directly from the ledger's ordered node/lane/verdict tuples (the offered alternative), and
  the only thing reused is the hashing: `hash_value`, the codebase's one 16-hex content hash, rather
  than minting a parallel scheme. Two design points worth recording: (a) `lane` is read from the
  `step_started` a node emitted — the one event carrying it — and a skipped branch leg with no
  recorded lane contributes `""` rather than a guess, which keeps the projection pure; (b) the
  projection is deliberately NOT deduped by path (unlike replay's last-write-wins fold), because a
  rewind's only mark on the ledger is the re-execution events it appends, and those extra tuples are
  exactly what makes a rewound run distinguishable from a clean one.
- **2026-08-15 — `PP-7` DISCOVERY (expected additive conflict with `PP-8`).** `PP-8` (PR #1332, not
  in this stacked branch) also adds a function (`edge_stats`) to `introspection.py`. This atom adds
  `trajectory_steps`/`trajectory_signature`/`trajectory_regression` cleanly alongside
  `gate_stats`/`run_stats`, exactly as `PP-8` did — so when both merge, `introspection.py` conflicts
  additively (two independent projection functions) and resolves by keeping both. No shared symbol is
  touched. `PP-4`'s ledger-boundary rail stays green: this work lives in `workflows/`, which may import
  `ledger`; the reverse import the rail guards is untouched.
- **2026-08-14 — DONE (`PP-10`): consumer-liveness detection.** `learning/consumer_liveness.py`
  sweeps every work unit's graded publish outcomes and files ONE `retirement` proposal for a unit
  whose last `DORMANCY_CYCLES` (3) matured cycles all went untouched. "Consumer touch" is observed
  entirely through writers that already exist — an artifact `referenced` / `edited` / `reverted`
  event whose actor is not `agent` (`record_impression` from the dashboard's
  `POST /api/artifacts/{slug}/events` and from `chat_runner`; the `update`/`revert` routes), or the
  slug in `entity_settings/pinned_artifacts.json`. `created` and an agent `iterated` are excluded, or
  every work unit would look consumed by itself. Driven on the curator tick in `history.py`
  immediately AFTER `outcome_resolver.resolve`, so a cycle that matured this tick is in the window
  rather than one tick late. Stateless: no new file, no new `StateEntry`, no counter — idempotency is
  the proposal queue's own fingerprint plus decision memory.

- **2026-08-14 — DEVIATION (`PP-10`): a THIRD metric source, and the publish producer moved onto it.**
  `PP-9` opened the publish question against `SOURCE_MEMORY` with a semantic key
  (`artifact.<slug>.consumed`) nothing writes, so it always closed `inconclusive` and its own docstring
  handed the counter to `PP-10`. Rather than write that counter — a second store the atom exists to
  avoid — `ledger/outcomes.py` gains `SOURCE_CONSUMPTION` and `engine._open_publish_outcome` declares
  it. Consequences: the publish bet now grades as a real `measured` 1.0/0.0, and it grades on a box
  with NO vector store (the memory-source availability gate no longer applies to it). The reader lives
  in `learning/`, not `ledger/` — reading a provider is I/O, and `PP-4`'s rail keeps `ledger/` pure.
  `consumption_metric()` / `slug_from_metric()` are the one place the metric name is built and parsed,
  because the resolution record carries the metric and not the slug.

- **2026-08-14 — MEASURED (`PP-10`): the firing population before the control got teeth.** Per this
  program's standing rule (`PP-1`, `PP-3`, `WF2LOO-18`): **0 of the 19 bundled templates declare a
  `publish:` node** (census over `workflows/bundled/*/workflow.json` for `config.publish` at any
  nesting), so on a fresh or seeded install the sweep can fire on ZERO work units and no scoping-down
  was needed. Firing requires a user-authored publishing work unit, at least three runs of it, and
  every recent artifact untouched past its 7-day horizon. Three further anti-nag guards, each tested:
  one touch anywhere in the window is `LIVE`; an `inconclusive` cycle is `INSUFFICIENT` and never
  `DORMANT`; and the proposal BODY is stable per work unit (the volatile slugs ride in
  `evidence_refs`, outside the fingerprint) so a re-file REINFORCES the one row and a REJECTED finding
  is never re-filed.

- **2026-08-14 — DISCOVERY (`PP-10`): an un-versioned artifact edit leaves no timeline event.**
  `NativeArtifactProvider.update()` appends its `edited`/`iterated` event only on the
  `snapshot=True` branch, and `PATCH /api/artifacts/{slug}` defaults `snapshot` to False — so a
  content edit without a version bump is invisible to any consumer of the artifact timeline, this
  sweep included. Left as-is and recorded in the module docstring: widening it changes artifact event
  semantics for every timeline consumer. The failure direction is the safe one — a missed touch can
- **2026-08-14 — `PP-2` DONE.** Ordering is now DERIVED from bindings and consumed by the frontier;
  the second, hand-maintained edge list is gone. `validator.dep_ordering_edges` emits ONE list
  carrying both origins (`EDGE_BINDING`/`EDGE_NEEDS`); `tick.ordering_for` re-reads that same
  derivation, so validation and admission cannot disagree about "ordered first". `WF_UNKNOWN_NEEDS`
  became a global EXISTENCE check (`_validate_binding_targets`), the sibling-only rule in
  `_validate_shape` is deleted, and a diamond spanning two containers is now expressible — proven by a
  pure-frontier driver run to `DONE` (`test_a_diamond_spanning_two_containers_completes`).
- **2026-08-14 — `PP-2` composition with `PP-11`.** Derived ordering and admission are ORTHOGONAL
  axes, not competitors: `_ordering_satisfied` gates a node's CANDIDACY (are its producers terminal?)
  while `admission.compose(policies, …)` gates its SLOT (lane/container/WIP budget). `order`/`inst`
  are built once in `frontier()` and threaded down exactly as `PP-11`'s `policies` are — neither
  replaces the other, and both are threaded so a run-level rule cannot become per-node-optional. The
  earlier PP-2 attempt threaded `wip: bool` through `tick`; that mechanism no longer exists after
  `PP-11`, so this is a re-expression on the current base, not a rebase.
- **2026-08-14 — `PP-2` the "restriction is unnecessary" argument HELD against the post-`PP-11` code.**
  The sibling-only rule's stated reason was "cross-container edges would break the frontier's
  locality." Verified false on the current tree: `frontier()` already resolves every producer against
  the whole (global) `states` map and holds the whole spec tree, so a derived edge between any two
  nodes is honoured by re-derivation each tick. `PP-11` restructured admission but left the tree walk
  and the global state map untouched, so nothing it changed reintroduced a locality constraint.
- **2026-08-14 — `PP-2` no existing schedule changed (the honest proof).** `PP-11`'s bundled
  golden-frontier fixture (`tests/fixtures/frontier_golden/`) re-ran BYTE-IDENTICAL after deriving
  ordering over all 19 templates (18 with `{{nodes.*}}` bindings, 0 with `needs`). This is expected:
  `PP-1` already guarantees every binding edge in a validated spec is ordered by container structure,
  so the derived gate is satisfied exactly when the reader would have been visited anyway — the only
  shape that flips (a concurrent-parallel binding) was refused before, so no shipped template had one.
- **2026-08-14 — `PP-2` the `to_skip` reachability change (the named risk surface).** The asymmetry:
  a plain `needs` onto a SKIPPED node is SATISFIED (terminal — keeps a join off an untaken leg), but a
  DATAFLOW edge onto a SKIPPED producer makes the reader UNREACHABLE (its output will never exist), so
  only that reader is cascade-skipped. The deadlock check gained `and not fr.to_skip`: a tick whose
  only work is retiring an unreachable reader is PROGRESS, not deadlock, and without the guard the
  controller would FAIL a run about to proceed. Test matrix in `TestDerivedOrderingReachability`:
  decline-with-a-cross-container-reader, decline-inside-parallel (skips exactly the unreachable target,
  not the live sibling), a skipped producer inside a `foreach` body (per-item `_producer_instance`
  resolution), a live/pending producer that makes its reader WAIT and never skip (the dangerous
  early-fire direction), plus the WF2-R18 join tests staying green.
- **2026-08-14 — `PP-2` DEVIATION (plan step 3).** The plan asked for a warning on a `needs` ABSENT
  from the derived set ("real non-dataflow ordering or a stale edge"). That would fire on EVERY correct
  non-dataflow `needs` — now the field's only legitimate use — teaching authors to skim validator
  output. Instead: `WF_UNSATISFIABLE_NEEDS` (ERROR) for a `needs` the structure cannot honour (a
  contradiction that would hang the run), and `WF_REDUNDANT_NEEDS` (WARNING) for a `needs` a binding
  already implies. The `origin` tag on every `DepEdge` lets an inspection surface show non-dataflow
  `needs` without spending author attention at each save. Volume on the bundled library: zero (no
  template declares `needs`).
- **2026-08-14 — `PP-2` DISCOVERY.** A pure frontier cannot re-derive a per-item BRANCH decline whose
  selector is `{{item}}` — the item is not in the binding context during derivation, so `_select_case`
  returns None and the branch derives PENDING. Per-item routing is the CONTROLLER's job (it stores each
  item's branch output and skips the subtree per item); the frontier's reachability cascade then
  resolves per-item from the resulting state. The foreach reachability test therefore represents the
  decline by its RESULT (the producer's SKIPPED state), which is exactly what exercises
  `_producer_instance`.
