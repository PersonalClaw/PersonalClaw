# Plan: Platform Primitives — Edges, Verdicts and Policies as First-Class Nouns

**Status:** IN PROGRESS — 15 of 16 atoms shipped (see `## Execution log`). `PP-16` (a Loop becomes a WorkflowRun) is the only atom not yet done. 16 atoms in [`../atomic/PP.md`](../atomic/PP.md).
<!-- Header consolidated 2026-08-18: four sibling branches each left their own
`**Status:**` block on merge. The 2026-08-14 note claiming they had been folded into one
line was never actually applied — all four blocks were still present, and all four quoted a
shipped-atom count the atoms had already overtaken. They are replaced by the single line
above, whose count is derived from `../atomic/dag.json`. -->

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

- **2026-08-20 — `PP-16` slice DONE: one action-guard vocabulary (the backend mirror the
  2026-08-18 census left "whole rather than half-converged"). Atom stays `todo`.** The status
  slice unified how a loop's state is *narrated*; this unifies what a state *permits*.

  **The defect, measured.** `loop.loop:ACTION_SOURCE_STATES` is the only thing that decides whether
  a lifecycle action is accepted or answered with a 409 (`loop_routes.py:534`). Six frontend guards
  across five files each hand-wrote their own copy, with **three different vocabularies** (three,
  four and five states). **Five of six omitted `blocked`** — which the backend has always accepted a
  `resume` from — so **a blocked loop was unresumable from every surface in the app**. `failed` was
  resumable from two cockpits but not from the list or the in-chat card, so the same loop offered a
  different action set depending on where you opened it. And `LoopCockpitPage:585` gated Start on
  `ready` alone, missing `review`, which the sibling design cockpit already offered.

  🔴 **The sixth guard was invisible to the census that found the other five.** It sits in
  `pages/code/CodeCockpitPage.tsx` written as a chained disjunction
  (`status === 'paused' || status === 'blocked' || …`) rather than an array literal, so a
  `[...].includes(status)` search reported the app clean while a guard carrying its own third
  vocabulary survived. It was also the **only** one that matched the backend exactly. A census must
  match the shape a developer would write, not the shape the last one happened to use.

  🔴 **And the root cause was one layer down: the wire TYPE.** `api.ts` carried **three** unions for
  one backend enum — `LoopStatus` (eleven members, **omitting `blocked`**), `CodeStatus` (twelve) and
  `UnifiedLoopStatus` (twelve). A `status === 'blocked'` comparison against the eleven-member type is
  a **compile error**, which is the most likely reason every hand-written guard dropped that state:
  the type made the correct guard un-writable. The two per-kind copies are retired and their fields
  repointed; each had exactly one consumer, swept across `web/ src/ tests/ harness/` first.

  **What shipped.** `LOOP_ACTION_SOURCE_STATUSES` in `lib/loopStatus.ts` — a map of
  `ReadonlySet<string>` per action, following the file's own two landed mirrors, with `stop` holding
  `ACTIVE_LOOP_STATUSES` **by reference** rather than restating its five members. All six guards on
  five surfaces now read it, plus the `specFrozen` check that was a fifth hand-written copy of
  `PRELAUNCH_STATUSES` (converted only after eight behaviour legs pinned its semantics as unchanged
  *before* the change). One copy gap closed too: the cockpit's "Fix the underlying cause, then
  Resume" hint was gated on `failed`/`stagnant`, so the two states that newly offer Resume would
  have got the button with no explanation — it now follows the button.

  **Rails, four, each with a vacuity floor.** Per-action **equality** (not subset — a subset
  assertion is exactly what let `blocked` go missing) between the mirror and the imported backend
  dict; the surviving union versus the enum; a **both-shape** census for hand-written guards; and an
  adoption half asserting each of the five lifecycle surfaces actually reaches the mirror, because
  "no bad guard" is also true of a file whose controls were deleted.

  🪤 **The census was over-broad on its first pass and had to be narrowed.** Matching any membership
  test over lifecycle states flagged two innocents: an `attention` display flag (which deliberately
  includes the terminal `stopped`) and a CTA-copy selector keyed on the *effective* status.
  Converting either would have been wrong. The discriminator is proximity to the `act(...)` dispatch
  — what the rail is for is a control whose availability disagrees with the backend, so it only
  counts a shape sitting in front of the call that asks. Both directions are asserted: the shapes
  match their own samples, and the display flag must NOT count.

  **Driven as a user**, seeded blocked + review loops on an isolated dev home, gateway serving this
  worktree's own bundle, zero console errors on every surface: the blocked loop's row in
  `#/loops/history` now offers **Resume and Stop** (it offered neither), and its cockpit shows
  **Resume with the hint**. An honest nuance found only by driving: a `review` loop's normal product
  path is the plan **walkthrough**, not the cockpit, so the Start fix is real wherever the cockpit
  renders but a user in `review` meets the launch flow first. Two probe errors were mine, not the
  product's — loop ids are `uuid4().hex[:8]` and a full dashed UUID 400s as "Invalid loop id", and
  the goal list does not show `kind=code` rows.

  **Gate:** `make lint` clean (mypy 940); Python **23,204 passed, 30 skipped, 12 xfailed**; web
  **449 files / 4,644 tests**, `typecheck` + `build` clean. Falsifications re-run independently at
  assembly: `blocked` dropped from the mirror (*"disagrees per action … {'resume': …}"*), the old
  three-state literal restored at the list's icon Resume (site-scoped red — the menu assertion
  stayed green), the same at the in-chat card (*"the backend accepts resume from blocked"* AND
  *"from failed"*), the eleven-member union reinstated (*"backend-only=['blocked']"*), the chained
  guard reintroduced (census red, naming the shape), and a surface stripped of the mirror (adoption
  red).

  **Still open on PP-16, unchanged by this slice** (the noun change itself, one adoption/reaping
  path, retiring `LoopKindStrategy`, one projection to tasks) — plus the owner decisions the field
  map surfaced, of which this slice took only the action-guard half. Reported and not taken here:
  the union of all four source sets omits `intake` and `planning`, so a loop wedged in either (a
  dead classifier) has **no available action at all** and `DELETE` is its only exit.


- **2026-08-18 — `PP-16` PARTIAL (atom stays `todo`).** The capstone is multi-session; two slices
  landed complete, with no dual path and nothing half-migrated. **What is shipped ALREADY, measured,
  not assumed:** *one ledger* — `loop/journal.py` is the SECOND `personalclaw.ledger` producer
  (PP-5), same kinds, same reader, so the flywheel already sees loop runs; *one attention path* —
  `loop/watchdog.py:301` raises inbox items through `inbox.emit_attention_item`, the same seam
  `workflows/attention.py` uses; *the five kinds are already bundled templates* —
  `general-project`, `goal-pursuit-open-ended`/`-verifiable`, `code-project`, `design-project`,
  `deep-research` ship under `workflows/bundled/`, and `loop_aliases.KIND_TO_TEMPLATE` resolves a
  legacy kind to one at READ time. **What is NOT:** the loop row, its store, its watchdog, its
  adoption path and its cockpit are all live and coexisting with the run path *by design* today
  (`workflows/watchdog.py:236` names the coexistence and `_publish_to_equivalent_loop_hub` bridges
  run events onto the loop hub) — so restart-adoption is still implemented twice
  (`loop/manager.reap_orphaned_loops:537`, called from `gateway.py:2313`, vs
  `workflows/watchdog._boot_sweep`+`_adopt`), the projection to tasks is still twice
  (`loop/tasks_link.py` provisions Tasks Projects imperatively; `workflows/materialize.py` projects
  run state), and the two status vocabularies still both exist.
- **2026-08-18 — `PP-16` step 1 DONE: the field map.** `workflows/loop_run_map.py` maps all **39**
  `Loop` dataclass fields onto `WorkflowRun`/`SupervisorPolicy`/`WorkflowDef`/`Intent`/a template
  input/a node `config` key/a projection — the plan's own first step ("name the ones with no home
  BEFORE writing code"). Deliberately inert with the `WF2LOO-12` honesty marker, in the
  `POLICY_KNOB_MAP` idiom, and railed by `tests/test_pp16_loop_field_map.py`: exhaustive in both
  directions, **18 destination paths resolved attribute-by-attribute** against the real dataclasses,
  every `RUN_INPUT` checked against the shipped `bundled/*/workflow.json` inputs, the homeless set
  pinned as a shrink-only ratchet, and `STATUS_VOCABULARY_DELTA` computed from the two enums rather
  than asserted. Falsified four ways, each red: a new `Loop` field (*"Loop fields with no row"*), a
  renamed `WorkflowRun.elapsed_seconds` (*"WorkflowRun has no field 'elapsed_seconds'"*), a bogus
  template input (*"which no bundled template declares"*), and a seventh homeless field (*"the set
  of Loop fields with NO home changed"*).
- **2026-08-18 — `PP-16` OWNER DECISIONS the field map surfaced (recorded, not taken).** (1) **A run
  has no user-facing title.** The runs list labels a row `{workflow_name} — run {id}`
  (`WorkflowsListPage.tsx:372`); `Loop.name` is user-set (`store.rename`) and shown on every loop
  surface. Declare a `title` on `WorkflowRun`, or accept `extra['name']` — a tolerant-reader
  spillover dict, which is a shape decision, not a migration detail. (2) **The status vocabularies
  are not a superset relationship:** `intake`/`planning`/`review`/`ready`/`stagnant`/`blocked`/
  `stopped` have no `RunStatus` member and `draft`/`cancelled`/`escalated` have no `LoopStatus` one,
  so "one status vocabulary" costs a decision per orphan. (3) **`WorkflowRun.task_list_id` is
  declared and inert** — no writer or reader outside `models.py` — and singular, where a loop keeps
  one TaskList per phase. (4) Four more fields have no home at all: `provider_agent`,
  `strategy_id`, `strategy_config`, `auto_teardown_on_complete`. (5) Recorded fidelity losses:
  `Loop.model` (a concrete model id) vs a node's `model_tier`; `Loop.roster` (N personas on ONE work
  unit) vs one `agent` per node; loop epoch-float timestamps vs run ISO strings.
- **2026-08-18 — `PP-16` DONE-NOW: one loop-status vocabulary (the frontend half of "one status
  vocabulary, one cockpit contract").** One backend enum was narrated by **two** frontend tables:
  `lib/loopStatus.ts` (Code list, in-chat SDLC card, Projects linked-work rows) and
  `pages/loops/loopStatusMeta.ts` (Loops list, dashboard Active Work). They disagreed word-for-word
  (*Stalled/Stagnant*, *Analyzing/Intake*, *Complete/Completed*, *Needs you/Needs input*) **and
  tone-for-tone** (`running` green in one, primary in the other; `complete` the reverse), so green
  meant "in flight" on one surface and "finished" on the next. The second table is deleted; the
  survivor's words follow `workflowMeta`'s for states both nouns share. **The locked
  terminal-label ruling was being violated by the file its own rail could not see:**
  `terminalSuccessLabel.test.ts` named the other two registries by path and matched a
  `{ label: … }` shape `lib/loopStatus.ts` did not use, so the bare adjective *"Complete"* shipped
  on the Code surfaces; the rail now points at the surviving registry. Six hand-written
  active-status set literals collapsed onto `ACTIVE_LOOP_STATUSES`/`PRELAUNCH_LOOP_STATUSES`
  (mirrors of `loop.loop:ACTIVE_STATUSES`/`PRELAUNCH_STATUSES`), which fixed **three live
  defects**: the Loops-list filter had `blocked` in neither its active nor its done bucket, so a
  blocked loop matched NO filter and was invisible under the default view; `LoopCockpitPage` and
  `DesignCockpitPage` both dropped `blocked`, so a blocked loop's cockpit read as finished; and the
  Stop affordance on the Loops list and the in-chat SDLC card was gated by the FILTER bucket, which
  offered Stop on the four pre-launch statuses (`loop_routes.py:534` 409s: *"Cannot stop a loop in
  'ready' state"*) and withheld it from `blocked`. A fourth vocabulary inside
  `LoopCockpitPage.statusLabel` (which shipped a `draft` key `LoopStatus` has never had) is gone
  too. Railed cross-tier by `tests/test_loop_status_vocabulary.py`, four tests, each with a vacuity
  floor; falsified five ways (missing member, stale member, a set missing `blocked`, a reintroduced
  second registry, a reintroduced hand-written literal) plus both vacuity floors.
- **2026-08-18 — `PP-16` UNMET, with the census evidence (why no more was started).** Doctrine is
  clean break, so a slice that cannot finish must not begin. Still open: **the noun change itself**
  (`loop/store.py`'s 1241-line row + `loop/loop.py`'s entity, read by `loop_routes.py`,
  `agents/native/sdlc_tools.py`, `tasks/hierarchy_handlers.py`, `learning/loop_end.py`,
  `investigate.py`, `legibility/discover.py`, `dashboard/chat_*` — 20 modules outside `loop/`);
  **one adoption/reaping path** (the two implementations differ in kind — the loop side re-ARMS
  through `kinds.launch_blocker` + `concurrency.reap_orphans`, the run side decides substrate
  liveness in `_boot_sweep` then resumes from the journal — so unifying them is a policy merge, not
  an extraction); **one status vocabulary end-to-end** (blocked on owner decision 2 above);
  **retiring `LoopKindStrategy`** (`loop/kinds/*` is 3.2k lines, `sdlc.py` alone 1566);
  **one projection to tasks**; **the backend action-guard mirror** — `ACTION_SOURCE_STATES`
  `resume` still has a hand-written frontend counterpart on several surfaces (`['paused',
  'stagnant', 'needs_input']` on the Loops list vs the backend's five), which is a second
  unification and was left whole rather than half-converged.
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
- **2026-08-14 — `PP-14` DONE.** `workflows/supervisor_policy.py` lands the ONE `SupervisorPolicy`
  declaration the two half-policies converge on, plus its tolerant parser and (in
  `workflows/validator.py`) six typed authoring-time codes. **All ten fields REUSE the types that
  already exist** — no parallel vocabulary was minted: `RubricCriterion`/`clamp_marginal` from
  `judge_contract`, `Rung`/`DEFAULT_LADDER`/`FailureClass` (and the canonical `_resolve_ladder`
  parser) from `loop_middleware`, `StepConfig` from `loop.tick`, `Attention` from `autonomy`,
  `ScopeMode` from `scope`, and the `reasoning|standard|fast` tier set the `WF_BAD_MODEL_TIER` lint
  already owns. The only new structure is `WriteScope` (a paths+`ScopeMode` bundle) and the closed
  `POLICY_FIELDS` set, which is the contract itself.
- **2026-08-14 — `PP-14` the honesty rail (the atom's centre).** A static AST **call/construction
  census** over `src/personalclaw` (excluding tests and the declaration itself) counts code that
  CONSTRUCTS a `SupervisorPolicy` or invokes `parse_supervisor_policy` — the acts that WIRE it in.
  Following the `detectors.gate` precedent it is call-based, NOT import-based, so the authoring-time
  validator consulting `POLICY_FIELDS` is correctly not a caller and `POLICY_FIELDS` can stay a single
  source of truth. Railed both directions: **direction 1** asserts `callers == 0` (a new caller reds
  it); **direction 2** asserts the module still carries its `zero production callers` / `PP-15` marker
  (stripping it reds it); a coupling test asserts `HAS_ZERO_PRODUCTION_CALLERS == (callers == 0)` so
  the claim can never silently drift. **Two-sided vacuity floor:** a positive control proves the
  detector returns 2 on a snippet that DOES call, and a scan floor asserts ≥50 modules were seen.
- **2026-08-14 — `PP-14` DECISION: tolerant reads vs the closed set.** The parser never raises —
  missing/blank/malformed values all fall to the strict defaults (the `hints_from_dict` pattern), and
  it IGNORES unknown top-level keys. The closed-set contract lives in the VALIDATOR, which emits
  `WF_SUPERVISOR_UNKNOWN_FIELD` for a stray key and `WF_SUPERVISOR_{NOT_OBJECT,BAD_TIER,BAD_RUNG,
  BAD_HITL,BAD_FAILURE_CLASS}` for bad shapes/values — accumulated, never one-per-turn. Zero runtime
  change: `loop/tick.py` and `controller.py` are untouched; the new codes fire only in
  `validate_node_tree`. Census of the shipped population: **zero bundled templates declare a
  `supervisor` key**, so the new codes ship against a population that trivially passes them.
- **2026-08-14 — `PP-14` DECISION: no CHANGELOG entry.** The declaration is deliberately inert — no
  user can observe a `SupervisorPolicy` until `PP-15` wires it into `evaluate`. The CHANGELOG is
  user-facing (the in-app Updates panel), so the entry belongs to `PP-15`.
- **2026-08-14 — `PP-14` falsifications (all reverted from `cp` backups, probe markers grepped).**
  (1) a fake `parse_supervisor_policy(...)` caller added under `src/` reds
  `test_DIRECTION_1_no_production_caller_exists_while_the_marker_claims_zero` + the coupling test;
  (2) stripping `zero production callers` from the module reds
  `test_DIRECTION_2_the_inert_module_declares_itself_inert`; (3) disabling the unknown-field check
  reds `test_an_unknown_field_is_a_typed_error` while the missing-field tolerance test stays green.
- **2026-08-15 — `PP-5` DONE.** The loop engine is the SECOND producer of the ledger.
  `loop/journal.py`'s `LoopJournal(LedgerWriter)` binds `_store` to `loop.store` (now a
  `LedgerStore`: `append_jsonl`/`read_jsonl`/`write_output`/`write_artifact`, keyed by loop_id) and
  emits the four kinds the atom names — a cycle → `step_started`/`step_completed` carrying the
  finding, a supervisor assessment → `judge_verdict`, a stall → `breaker_trip`, an orphan reap →
  `watcher_reaped` — reusing `ledger/kinds.py`, minting nothing. The four live emit points:
  `store.record_cycle_findings` (watchdog `_poll_once`, ingesting the worker's finding files),
  `store.write_verdict` (goal/sdlc kinds), `store.record_breaker_trip` (watchdog stagnation),
  `manager.reap_orphaned_loops` (`store.record_watcher_reaped`). `store.get_findings`/`get_verdicts`
  became PROJECTIONS over the ledger's `step_completed`/`judge_verdict` events — the findings/ and
  verdicts/ FILE store is retired as a reader source (clean break, no dual write). `learning/
  loop_end.py` mirrors `run_end.py`: a terminal loop mines its own ledger via the `journal=`/`store=`
  seams `learning.mining` already exposed, wired behind the RUN_END LearningGate in the watchdog's
  `_complete`. Verified by driving real loop cycles through the ingest path and reading the
  trajectory back FROM THE LEDGER ALONE, and by the flywheel filing a TEMPLATE proposal from three
  loops' evidence where it saw nothing before.

- **2026-08-15 — DEVIATION (`PP-5`): the worker's finding FILE stays; it is the ingest source, not a
  second store.** The atom says the findings/verdicts files "become projections over the ledger". A
  verdict was a platform write (`store.write_verdict`), so it moved wholesale — verdicts/ is gone.
  But a FINDING is authored by the worker subagent writing `findings/cycle_NNN.json` as its per-cycle
  deliverable (baked into every kind's prompt + the `loop-worker` skill), so that file is the worker's
  OUTPUT, not the reader's store — analogous to a workflow node's raw output under `outputs/`. The
  clean break is that `get_findings`/`get_verdicts` no longer READ a parallel file store: the
  controller ingests each file ONCE into the ledger (`record_cycle_findings`, idempotent by source
  filename) and every reader projects the ledger. Rewriting the worker's file interface would be a
  different atom (`PP-16`'s "a Loop becomes a WorkflowRun"), not this one.

- **2026-08-15 — DISCOVERY (`PP-5`): `step_started` is journal-only, not a durable ledger kind.**
  `LEDGER_KINDS` (from PP-4) contains `step_completed` but NOT `step_started`, so
  `LedgerWriter.write` mirrors a cycle's completion to `events.jsonl` (what `learning.mining` reads)
  while `step_started` lands only in `journal.jsonl` — exactly the workflow engine's own split
  between the durable Run Ledger and the resume/progress log. The loop inherits it for free: the
  flywheel mines completed steps, and the full ordered trajectory (started + completed markers) is in
  the journal for a trajectory reader. The ledger-boundary rail was hardened to ban
  `personalclaw.loop` under `ledger/` too (not just `personalclaw.workflows`), since the loop is now
  the second producer and the reverse-import ban must cover it.

- **2026-08-15 — `PP-5` proof.** Falsified three times, each target line read first and restored from
  a `cp` backup (never `git checkout --`): suppressing the `step_completed` emit in `LoopJournal.cycle`
  reds the headline `test_flywheel_produces_a_proposal_from_loop_evidence` (and
  `test_mining_reads_loop_steps_off_the_ledger`) — the load-bearing flywheel bar, since a loop with no
  completed steps mines no trace; emitting the supervisor assessment as a bare `{cycle, note}` instead
  of the reconciled `JudgeVerdict` shape reds
  `test_judge_verdict_carries_the_reconciled_vocabulary`; perturbing the projection (`get_findings`
  dropping its first finding) reds `test_trajectory_reconstructs_from_the_ledger_alone`, which
  reconstructs the findings/verdicts view purely from the ledger after the raw files are deleted.
- **2026-08-15 — `PP-8` DONE.** `introspection.edge_stats(runs)` projects, across a template's
  runs, per-`branch` case counts (`BranchStats`) and per-judge verdict counts (`JudgeStats`), and
  flags the two findings the atom names: a **case never taken** and a **degenerate selector** (a
  branch that always routes one way / a judge that returns one verdict). Both ride the SAME
  sample gate as the said-no badge — `EDGE_STATS_MIN_RUNS = FAKE_CHECK_MIN_RUNS` — so a distribution
  over three runs is never a finding. Lives beside `gate_stats` in `workflows/introspection.py`,
  wired into `service.introspect()` (top-level `edges` + `answers.risky.edges`, findings folded into
  the template card's shared `warnings` list), and rendered as a new **Edges** section in the
  existing `IntrospectPanel` — no new surface. Falsified three ways (sample-gate → 0, dead-case
  check always "taken", degeneracy check disabled); each reds the matching test and restores clean.
- **2026-08-15 — `PP-8` DISCOVERY: the `branch` case selection is NOT recorded as a dedicated
  inline ledger event.** `engine.dispatch_branch` produces `{"case": label}`, but that output is
  offloaded through `store_output` behind an `output_ref` (body under `outputs/`, never inline in
  the event), and the branch's `declined_edges` are held on the instance and never journaled
  (`controller._decline` mutates memory only). The generic `DECISION` kind is written for
  wip-limit holds and loop-iteration decisions, never for a `branch` route. **The selection
  survives in the event stream only as the case subtree's instance PATH** — the taken case runs and
  every untaken case's whole subtree is `step_skipped` at `<branch>.cases[<label>]`
  (`tick._visit_branch` + `controller._skip`; asserted in `test_workflows_controller`). So the
  projection reads case labels from those paths, which keeps it a PURE projection over the event
  list like `gate_stats` — **no output-store read, no new ledger kind minted.** The judge half uses
  the existing `JUDGE_VERDICT` events. No new writer was added; if a future need arises for the
  declined edges to be first-class, that is a real ledger-kind decision, not a silent mint.
- **2026-08-15 — `PP-8` DECISION: CHANGELOG entry added.** Unlike `PP-11`, this atom adds a
  user-observable surface (a new Edges panel section with two new findings), so it is announced in
  the in-app Updates panel. Header "N of 16 shipped" left unchanged (a pre-existing 3-way merge
  artifact with other atoms in flight).

- [PP-12] **BLOCKED — owner decision needed on what guarantee `Lease` provides.** An implementation of
  this atom already exists, unshipped, on the local branch `feature-pp12-lease-dwell-policies`
  (`04c9e226`, rebased to `183d27c8`): 297 added lines in `workflows/admission.py`, 455 in
  `controller.py`, 28 in `pool.py`, and a 579-line `tests/test_workflows_admission_policies.py`. It was
  found by sweeping local branches for commits absent from `origin/main`; of ~30 such branches it and
  `DCU-1` were the only two whose atom is still `todo` on `main` — the other 25 are stale branches whose
  atoms have already landed.
  **It has never been green.** `test_sixteen_concurrent_claims_on_one_resource_produce_exactly_one_holder`
  — the race the atom is written around, and its central safety property — fails: **6 to 7 of 16 workers
  each win the same lease**. Measured 4 times (once at load average 31 with five agents running, three
  times at idle: `6 holders`, `7 holders`, `7 holders`) and **also at the original pre-rebase commit**, so
  it is neither a contention flake nor a rebase regression. The branch's `dag.json`/`PP.md` flip PP-12 to
  `done`; that claim is false and must be reverted before any of it ships.
  **Root cause, established in the code rather than inferred.** `pool.acquire` is correct — an unexpired
  lease held by another holder returns `HELD_BY_OTHER` and no lease. The failure is in the durability
  around it: `claim_task` wraps its read-modify-write in `concurrency.single_flight`, whose own docstring
  says it is a **"Cross-process single-flight guard"** that yields False only when *"another live process"*
  holds the lock, with **non-blocking** acquisition. The test drives sixteen **threads in one process**,
  which is outside that guarantee — so the assertion is not wrong about what is needed, it is wrong about
  what the mechanism promises.
  **Why this is an owner decision and not a test fix.** The atom's own scope is *"cap this fan-out because
  each item holds a rate-limited endpoint"*, and the controller fans out **in-process** with
  `asyncio.create_task` (`controller.py:2234`). A cross-process-only lease therefore does not deliver the
  thing this atom exists to provide: two concurrent items in the same gateway would both hold a
  rate-limited endpoint. The two remedies differ in kind, and both are scope calls on a safety control:
  (1) give the lease in-process exclusion as well (an async/thread lock layered under `claim_task`, which
  widens a primitive `pool.py` owns and that other callers share), or (2) narrow the lease's documented
  guarantee to cross-process only and state plainly that in-process fan-out is not capped — which leaves
  the atom's stated purpose unmet and makes `Lease` misleading for its only intended caller.
  Rewriting the test to spawn processes would make the suite green while leaving the in-process hole
  open, so it is deliberately NOT done here. **Unblock:** the owner picks (1) or (2). Nothing else in the
  atom is in question — `Dwell` and `MetricGate` reuse `loop.tick.StepConfig`'s parsed thresholds and
  their tests pass (57 of 58 in the file pass; the one red is the lease race).
- [PP-12] **DONE — ruled OPTION (1), and the diagnosis above was measured against a MUTATED tree.**
  The owner picked (1): the lease must exclude in-process too, because the engine fans out with
  `asyncio.create_task` (`controller.py:2234`) and a cross-process-only lease would fail to cap the
  only fan-out shape that occurs — two items in one gateway both holding a rate-limited endpoint.
  Option (2) was rejected as shipping a control that cannot do the job it exists for.
  **Implementing (1) turned out to require deleting code, not adding it.** The branch's committed
  `pool.py` carried a leftover falsification probe: `claim_task`'s `single_flight` wrapper had been
  **stripped and replaced with `# FALSIFICATION-PROBE-PP12-CAS: check-then-act, no flock.`** — the
  whole of the branch's 28-line `pool.py` diff was that mutation, never restored. So the read-modify-
  write was running with **no lock at all**, and the root cause above ("`single_flight` is
  cross-process only, so threads are outside its guarantee") was derived from a tree in which
  `single_flight` was never called. Restoring the wrapper — `pool.py` is now byte-identical to `main`
  apart from a docstring — made the suite green with no new mechanism, no second lease, and no extra
  lock. There was never an in-process hole to close.
  **The narrower reading is simply false, and now measured rather than reasoned.** `fcntl.flock` is
  scoped to the *open file description*, and `single_flight` opens a fresh one per call, so two
  THREADS in one process contend exactly as two processes do. Probed directly: 16 threads on one key,
  each holding the critical section 20ms — **peak simultaneous holders inside it = 1**, and only 1 of
  16 acquired at all. That property is now (a) documented at `concurrency.single_flight` and
  `pool.claim_task`, whose "cross-process" wording is what caused the misdiagnosis, and (b) pinned by
  a new test, `test_the_flock_under_the_claim_excludes_THREADS_not_only_PROCESSES`, which asserts the
  PEAK rather than a serial count — a serial count cannot tell overlap from fast succession.
  **Measured holder counts, `test_sixteen_concurrent_claims_..._exactly_one_holder`.** BEFORE (probe
  in place, three runs): **3, 11, 15** holders won the one lease — plus the 6/7/7/13 recorded above,
  so seven independent multi-winner measurements. AFTER (wrapper restored): **1 holder, every run** —
  the full file green 7 times (4 × 49 tests, then 3 × 50 after the new rail), one of them at load
  average 65.9 with four sibling agents building, so the pass is not a quiet-box artifact.
  **Shared-caller census (`grep -rn "claim_task\|single_flight" src/`).** `claim_task` has exactly one
  production caller, `controller.py:1798`. `single_flight` has ten: `durability/service.py` ×4
  (export/snapshot/drill/sync), `durability/shards.py` (shard-export), `workflows/leases.py` ×2
  (`claim:{target_id}`), `workflows/overlap.py` (workflow-overlap-drain), `history.py` ×2
  (`consolidate:{key}`, mem-promote-episodic), and `pool.py` itself (claim + release). **Every one of
  them is safe by construction, because the final diff of `pool.py` and `concurrency.py` against
  `main` is COMMENTS ONLY** — no semantics were widened, nothing was layered under the RMW, and
  `single_flight`'s non-blocking cross-process behaviour is untouched. The deadlock question that
  option (1) raised is therefore moot: no lock is held across an `await` or a blocking syscall,
  because no lock was added. The critical section is still `claim_task`'s RMW and nothing else.
  **Falsified, three ways.** (i) Re-applying the probe reds the race test with `AssertionError: 3 /
  11 / 15 holders won one lease` — so the flock, not a timing shift, is what closes it. (ii) `Dwell`
  returning `None` unconditionally reds `test_dwell_holds_until_the_bake_floor_elapses_then_abstains`
  and `test_dwell_reads_the_loops_own_parser` (`assert None == 0`). (iii) Dropping `MetricGate`'s
  trailing neutral step reds `test_a_passing_metric_lets_the_step_through` (`assert 0 is None`) and
  `test_the_metric_gate_does_not_enforce_the_bake_floor_as_well` with
  `Decision(action=COMPLETE, step_index=1, reason='all steps complete')` — exactly the "plan finished"
  / "rollback cap hit" conflation that step exists to prevent.
  **Gate.** `make lint` clean (black, isort, flake8, mypy: 902 files, no issues). Targeted: 50 passed
  ×3. Collateral on the shared primitive: `-k "workflow or pool or admission or concurrency"` →
  **5085 passed, 4 skipped**. `PP-11`'s golden frontier file re-runs unchanged, with
  `test_workflows_{admission,leases,lease_confirm,pool,controller,tick,containers}` +
  `test_concurrency` → 353 passed. `test_roadmap_dag_derived` + `test_agent_reference` +
  `test_inert_surface_baseline` → 34 passed. **The branch's `dag.json` derived block was already
  flipped to PP-12-done while the atom itself still read `todo`** — the pair reds
  `test_roadmap_dag_derived`; both now say `done` and `regen_dag_derived.py` reports "already
  current", 0 non-ASCII bytes. CHANGELOG entry re-read and left as written: it is still accurate,
  since nothing user-observable changed. Plan header's "N of 16 shipped" left alone, per `PP-8`.

- [PP-13] **DONE — and the caller census is the finding: the retired projection had ZERO production
  callers, while the surface it was written for had NO ranking at all.** The sweep ran before the
  delete, as the atom demands. `pool.frontier` / `next_task` / `Candidate` / `Urgency` / `explain` /
  `PRIORITY_WEIGHT` had exactly **one** importer in the whole repo — `tests/test_workflows_pool.py`.
  Every production import of `pool` takes something else: `tasks/native.py` (`lifecycle_payload`,
  `should_fire_completion`), `workflows/surfacing_channels.py` (`HandOff`, `route`),
  `workflows/settings.py` (`DEFAULT_LEASE_SECS`, `MAX_LEASE_SECS`), `workflows/admission.py`
  (`acquire`, `Lease`, the TTL constants — `PP-12`), plus two comment-only mentions in `triggers/`.
  So this was not one of two live schedulers: it was a **complete, correct ranking reachable by
  nobody**, beside a live funnel that shipped **unranked**. `registry.ready_tasks` — the ONE funnel
  behind `GET /api/tasks/ready`, `DashboardLive`, `TasksListPage` and the agent's next-task tool —
  returned `list_all_tasks` provider order (`updated_at` desc) with no priority, no blocking-count
  and no overdue term. `Task.due` had **no reader anywhere in the product**. The `/work` board is a
  different projection entirely (`containers.group_board` over runs/loops/tasks, claims via
  `workflows/leases.py`) and never called the pool. The atom's warning was right for the opposite
  reason to the expected one: the risk was not a missed caller, it was concluding "no callers, pure
  deletion" and shipping the retirement without ever wiring the capability up.
- [PP-13] **What landed.** The ordering moved to `admission.rank_key` — a comparator, deliberately
  NOT a fourth `AdmissionPolicy`, because a policy answers *how many* and composes by minimum while
  an order answers *which first* and two orders have no tightest. `admission.ready` /
  `next_ready` compose the two: blocked items drop, everything else is put to the composed policy
  list as a `RESOURCE` request keyed by its own id (exactly how `pool.claim_task` keys the sidecar),
  and the survivors sort by `rank_key`. The leased-work exclusion is therefore the same composed
  verdict the engine's frontier gets instead of an `if candidate.leased_by`. `registry.ready_tasks`
  now ranks through it, with the clock and the lease reads confined to one adapter (`_rank_ready`)
  so `admission.py` stays inside `PP-11`'s purity rail — which still passes unweakened.
  `blocks_count` is counted over the FULL task map, not the ready subset: a bottleneck's dependents
  are the BLOCKED tasks, so counting among ready peers would score every bottleneck at zero.
  Ranking runs AFTER the ownership filter, so an excluded colleague's task cannot consume a
  position. Deleted from `pool.py`: `frontier`, `next_task`, `Candidate`, `Urgency`, `explain`,
  `PRIORITY_WEIGHT` (117 lines out, 5 in). The lease decisions stayed untouched.
- [PP-13] **The equivalence proof, and its vacuity floor.** `tests/fixtures/pool_frontier_golden/`
  — `seed.json` (12 candidates) and `ready.jsonl` (19 rows), captured from an UNMODIFIED `pool.py`
  (`ef8497ed…`) at `854529a2` with `PYTHONPATH` pinned to the worktree, per `PP-11`'s finding that a
  bare `python` imports the main checkout. Hashes: `4a0e965d…` / `7a549e87…`. Both sides read the
  ONE seed file, because a re-stated seed is a second implementation of the fixture. The new core
  reproduces the retired output element for element — id, position, urgency, score AND the `explain`
  line — across `exclusive` (8 rows, `Lease` active, asking as `OBSERVER`) and `inclusive` (10 rows,
  no `AdmissionState`, so nothing speaks to a `RESOURCE` bucket — what `include_leased=True` meant),
  plus the `next` head. The floor is a TEST, not a comment, and it denies every way "identical"
  could be trivially true: ≥5 ranked rows, ≥1 blocked item excluded, ≥2 leased items excluded, an
  overdue row, a blocking-others row, ≥4 distinct scores, a real score TIE (so both tie-breakers
  fire), and an order that is neither the seed's nor sorted by id.
- [PP-13] **DEVIATION — one behaviour is deliberately NOT preserved, and it is a bug fix.** The
  retired `frontier` filtered on `leased_by` being truthy, so it hid work whose holder was already
  gone; only a sweep could correct it. `Lease` asks `pool.acquire`, which treats an expired lease as
  takeable, so the item surfaces at once. That is the same reasoning `containers.board_row` already
  applies when it drops an expired claim badge rather than rendering it — a badge saying "taken"
  about free work is worse than no badge. Pinned by `test_an_EXPIRED_lease_no_longer_hides_work`,
  which measures both directions: a LIVE lease still excludes, and with every lease expired the
  projection equals the include-leased view.
- [PP-13] **Falsified three times; the third exposed a defect in my own test.** (i) Dropping the
  overdue term from `ReadyItem.score` reds the equivalence naming the exact position —
  `AssertionError: exclusive[3]: t-unknown-prio != t-medium-overdue` — plus three collateral reds.
  (ii) Stripping the write from `pool._write_lease` (decision kept, persistence gone) reds
  `test_a_lease_SURVIVES_a_gateway_kill` with `AssertionError: the lease was never persisted` and
  `test_the_funnel_EXCLUDES_a_task_another_holder_is_leasing` with
  `assert ['t-held', 't-free'] == ['t-free']`. (iii) Mutating `ready_tasks` to `return ready` —
  skipping the core entirely — reds three funnel tests, which is what proves the WIRING rather than
  the pure core. **But `test_an_OVERDUE_task_is_ranked_from_its_due_date` stayed GREEN under (iii)**,
  because its input list happened to be pre-sorted into its expected order: provider order and
  ranked order were the same list, so it asserted nothing about the wiring. Input reordered, and it
  now reds under (iii) as well — 4 of the 5 funnel tests do (the fifth compares `sorted()` and is
  order-insensitive on purpose). Every mutation was restored from a `cp` file copy, never
  `git checkout`.
- [PP-13] **A wrong expectation of mine, corrected against the shipped rule.** I first asserted
  "overdue beats priority" flatly and it red: `critical` (5.0) outranks `medium`+overdue (4.0),
  because overdue is a **+2.0 bump, not an override**. The code was right; the test was wrong. It
  now pins the real shape — an overdue medium outranks a plain `high` (3.0) and stays under a
  `critical` — which a flat assertion would have gotten wrong while passing against a comparator
  that clamped the bump.
- [PP-13] **Gate.** `make lint` clean (black 1752 files, isort, flake8, mypy 902 files, no issues).
  **`make lint` cannot prove a deletion**, so the covering rail was a RUNTIME import sweep:
  **32/32** modules naming `pool` or a retired symbol imported OK, and the broad rail imported
  **872/872** first-party modules OK, with a two-directional name check (nothing retired left on
  `pool`, nothing missing from `admission`). Targeted: 412 passed across
  `test_workflows_{ready_projection,pool,admission,admission_policies,frontier_golden,lease_confirm,containers}`
  + `test_task_ownership` + `test_tasks_{api,dag,hierarchy}` + `test_native_task_tools`;
  `PP-11`'s golden frontier file re-runs unchanged. `test_roadmap_dag_derived` +
  `test_agent_reference` + `test_inert_surface_baseline` → 34 passed. Broad slice
  `-k "workflow or pool or admission or work_board or task"` → **5429 passed, 4 skipped** (the same
  4 skips `PP-12` recorded). The 13 relocated projection tests were rewritten against the core, not
  deleted; the new file is 45 tests. Not in the inert-surface baseline and not under
  `personalclaw/sdk/`, so no app could have depended on the retired names. `docs/architecture/
  workflows.md` updated for both modules in the same change.

- **DONE — `PP-15` widen the convergence core and wire `SupervisorPolicy` into it.** `Action` gains
  `ESCALATE` (with `Decision.rung`) and `REPLAN`; `TickState` absorbs `loop_middleware.LoopState`'s
  counters (call/fix fingerprints, failure classes, progress marks) as TUPLES plus the persisted
  ladder position (`escalations_taken`, `attempts_at_rung`, `nudges_issued`, `recoverable_waits`,
  `replans_taken`); `evaluate` grows branches 4-7 (recoverable → environment → plan-critique
  REPLAN → the Continue→Nudge→Escalate→Surface ladder) above the unchanged progress branches, all
  vacuous on a default snapshot so a loop that never fails decides exactly what it decided before.
  **Clean break, not a second brain:** `check_middleware`, `LoopState`, `MiddlewareVerdict`,
  `_nudge_or_halt`, `_window` and `loop_middleware.Action` are DELETED (-317 net). What survives
  there is what the decision READS — the taxonomy, `call_fingerprint`, the `Rung`/ladder vocabulary,
  `nudge_for` (de-privatised), the brief, `InterruptQueue` — reused by `loop.tick`, not duplicated.
  **The ladder lost its mutability, which was the point:** `check_middleware` advanced
  `state.escalation_index` inside the decision, so it answered differently on a second call and
  again after a restart. The rung is now DERIVED; `tick.applied` is the pure write half and
  `controller._record_convergence` is the one place it lands on disk (`run.extra["convergence"]`,
  bounded 50-entry log). **Wiring:** `HAS_ZERO_PRODUCTION_CALLERS` flipped `True`→`False` —
  `RunController._supervisor_policy` parses a loop node's `supervisor:` block and
  `supervisor_policy.tick_config()` derives the `TickConfig`, so a template's ladder, budget,
  failure_mutations and gates are what the engine applies. The `WF2LOO-12` two-directional rail was
  INVERTED (it now reds if the caller disappears while the marker claims one); its coupling
  invariant `test_the_marker_and_reality_agree` is untouched. `marginal_value_band` became a real
  metric gate (only when `gates` declares none, so one loop never carries two thresholds).
  **Three findings worth recording.** (1) A trip's meaning had to be split: `max_iterations` and
  `token_cap` are DECLARED BUDGETS, not stalls (`_BUDGET_TRIPS`) and keep going straight to the
  escalation artifact — spending a fresh session on a satisfied cap re-runs the work the cap
  bounded. Only `repeated_error`/`identical_output` reach the ladder. (2) Only ONE of the breaker's
  four rules leaves failure signatures behind, so requiring `evaluate` to re-derive an
  `identical_output` trip from fingerprints it cannot have made the trip a NO-OP — the detector
  fired, the response tier saw no evidence, and the loop ran on with the breaker silently defeated.
  Fixed with `TickState.stall_confirmed`: the external detector's verdict is taken first, and the
  fingerprint detectors serve the loop kinds, which have no breaker. (3) Because the ladder keeps a
  thrash running, a loop whose iteration budget is smaller than the ladder's attempt budget walked
  off the end reporting COMPLETE; `_advance_loop` now re-asks the sole detector at the DONE point,
  restoring the terminal outcome the binary handling gave for free.
  **`REPLAN` queues a real batch**, not retry-with-a-hint: a `mutations` `insert` built from the
  judge critique, submitted through `submit_mutation` and applied at the tick loop's drain point,
  placed after the loop in the root sequence (or at the front of a container body when the loop IS
  the root). A shape with no structural target SURFACES rather than applying the batch somewhere
  the critique did not name. Verified end-to-end: the spec gains the node, `spec_version` bumps,
  `user_edited_mid_flight` is journaled, and the inserted step EXECUTES.
  **Gate:** `make lint` clean (mypy 903 files — it caught a real `recent` type collision). Runtime
  import sweep **875/875 modules OK, 0 stranded** (mypy cannot see these). Targeted 392 passed
  across `test_{loop_tick,workflows_tick,workflows_loop_middleware,workflows_validator_supervisor,
  pp15_convergence_core,workflows_admission,workflows_loop_wiring,workflows_mutations,
  ag13_autonomy_policy}` + `test_roadmap_dag_derived` + `test_agent_reference` +
  `test_inert_surface_baseline`. Broad slice `-k "tick or loop or admission or supervisor or
  mutation or resilience or breaker"` → 1548 passed, 4 skipped. Falsified three ways, each red:
  a clock in `evaluate` (`dwell_hold: the decision moved with the clock`); the last engine rung made
  unreachable (`restart_from_scratch was never selected`); REPLAN degraded to a hint (`REPLAN did
  not change the spec — this is the retry-with-a-hint behaviour it replaces`).
  **Four tests were re-contracted, none weakened:** three asserted bounds that encoded the binary
  failure (`<= 4`, `< 20`, an exact iteration count) and now assert the STRONGER property — the run
  surfaces through the ladder rather than drifting into its cap, with the nudge tier tried first.

- **2026-08-22 — `PP-16` slice DONE: no non-terminal status is actionless. Atom stays `todo`.** The
  2026-08-20 slice reported this and did not take it: *"the union of all four source sets omits
  `intake` and `planning`, so a loop wedged in either (a dead classifier) has no available action at
  all and `DELETE` is its only exit."* This closes it.
  **Why the guard was the whole obstacle, measured.** `store.update_status` refuses only transitions
  **out of** a terminal state (`store.py:437`) — there is no per-state table — so `intake → stopped`
  and `planning → stopped` were always legal. `manager.stop` tears down whatever is armed and
  `_teardown` no-ops when nothing is (`main = svc.get_by_session(...)`; `if main is not None`). So the
  backend could always service it and only `ACTION_SOURCE_STATES` (read by `loop_routes.py:534`) said
  no. Losing the record to `DELETE` was the user's only exit from a dead classifier.
  **`STOPPABLE_STATUSES`, deliberately its own name.** `ACTIVE_STATUSES` also drives the "active loop"
  list filters and badge counts, where a loop still in intake is **not** active — widening it would
  have silently changed those counts. The FE mirrors it as `STOPPABLE_LOOP_STATUSES`, composed as
  `new Set([...ACTIVE_LOOP_STATUSES, 'intake', 'planning'])` so the seven shared members are still
  never restated.
  **The rail is asserted as the general property, not as a two-state patch:**
  `test_no_non_terminal_status_is_actionless` fails for ANY non-terminal status missing from every
  row, so the next enum member added inherits the check instead of the hole.
  **Two shipped tests pinned the defect as intended behaviour, and both were corrected, not
  weakened.** `designLifecycleAffordances.test.tsx` asserted `Stop` was ABSENT on an intake loop with
  the reason *"stop 409s on a pre-launch loop"*, and `PRELAUNCH_LOOP_STATUSES`' doc comment in
  `loopStatus.ts` said *"the backend refuses `stop` on a pre-launch loop with a 409"* — both true when
  written, and together they are why the gap read as a decision rather than an oversight. Each now
  states the current contract and says what it used to say. `test_loop_entity`'s
  `stop == ACTIVE_STATUSES` became the exact relationship (`== STOPPABLE_STATUSES`, difference is
  exactly `{intake, planning}`, and a strict-superset check) rather than a bare superset assertion.
  **The mirror rail needed a real extension, not a loosened assertion.** Its parser resolved a
  referenced set by name but could not expand a `...SPREAD`, so the composed FE set read as only its
  two literal members and the per-action **equality** check failed. It now expands spreads innermost
  first, bounded by the set count so a cycle cannot hang the suite, with a post-pass assertion that
  each spread actually expanded — a parser that silently under-counts would make the equality check
  pass on a subset, which is the precise failure mode the equality (not subset) choice exists to
  prevent.
  **Falsification:** restoring `"stop": ACTIVE_STATUSES` reds **4** — the invariant, both guard
  parameters, and the mirror equality. **Blind spot measured:** under that mutation the pre-existing
  loop suites (loop-http, loop-entity, loop-gates, loop-code-stages) were **171 passed, 0 failed** —
  nothing asserted that every non-terminal state has a way out.
  **Still open on `PP-16`** (unchanged by this slice): the noun change itself, one adoption/reaping
  path (`loop/manager.reap_orphaned_loops`, 73 lines, called from `gateway.py:2417`, vs
  `workflows/watchdog._boot_sweep`+`_adopt`), retiring `LoopKindStrategy`, one projection to tasks,
  and the two status vocabularies (`LoopStatus` 13 members vs `RunStatus` 8; `stopped`≡`cancelled`,
  and `FAILED` is terminal for a run but not for a loop).
  **Unrelated drift found and NOT swept in:** `docs/design/consistency-audit.json` regenerates on
  `npm run build` and its committed copy is stale — `filesScanned` 527 → 547 with a new
  `pages/settings/ProjectionRulesPanel.tsx` row, a file this slice never touched (`driftHits` stays
  8). Restored from `HEAD` and reported here instead of riding along.
- **2026-08-22 — `PP-16` BLOCKED on an owner decision: retiring the inert `WorkflowRun.task_list_id`
  makes a Loop field homeless, and the map's own rail says that is an owner call.** The change was
  built, verified end to end, and then REVERTED rather than pushed past the rail. Recorded with the
  evidence so the decision can be made once and the edit re-applied in minutes.
  **The field is genuinely inert, measured.** Filtering out the unrelated and very live
  `Task.task_list_id` (the tasks domain, ~80 references), `WorkflowRun.task_list_id` exists as exactly
  five things: the dataclass field (`workflows/models.py:845`), its `_KNOWN` entry, `to_dict`
  (`:914`), `from_dict` (`:951`), and a persisted SQLite column (`workflows/store.py:91` DDL +
  `_COLUMNS`). **No writer sets it to a real value and no reader consumes it** — so it is a declared
  field AND a persisted column that nothing populates, which is the `loop_run_map` field map's own
  finding 3.
  **Removal is clean and backward-compatible — tested, not assumed.** With the field, the DDL row and
  the `_COLUMNS` entry gone: a run round-trips through the real store with `extra` left EMPTY (the
  column is never SELECTed, so nothing spills into the tolerant reader); and against a **legacy
  schema** — created by `ALTER TABLE runs ADD COLUMN task_list_id`, populated with `'tl-legacy'` — the
  new code reads that row fine and still writes NEW rows, because the column keeps its
  `NOT NULL DEFAULT ''`. There is no migration to write. A runtime import sweep over models, store,
  service, controller, loop_run_map, materialize and dashboard.server was clean.
  **Why it stopped.** `loop_run_map`'s `task_list_ids` row named `WorkflowRun.task_list_id` as its
  destination, so `test_every_declared_destination_resolves` correctly reds the moment the field goes.
  Re-homing that row to `NONE` then trips
  `test_the_homeless_fields_are_pinned_and_explained`, whose message is explicit: *"It must SHRINK as
  PP-16 lands; a new homeless field is an owner decision, not a detail."* Removing an inert
  destination GROWS the homeless set by one, which is precisely what that rail exists to stop a
  session doing unilaterally.
  **The decision, stated once.** `Loop.task_list_ids` is `{phase_key: task_list_id}` — one TaskList per
  phase. Either (a) `WorkflowRun` gains a per-phase destination designed for that shape and the inert
  singular field is replaced, or (b) the row re-homes to `PROJECTION` on the argument that a per-phase
  TaskList map is a projection of run state (`materialize.py` already projects tasks that way), or
  (c) the singular field stays as a reserved slot and the projection keeps provisioning imperatively
  through `loop/tasks_link.py`. **(c) is the status quo and the cost of it is a slot a later migration
  can fill with the wrong shape** — the reason this was worth attempting now while it is still a plain
  clean break.
  **One thing worth carrying:** the DDL↔`_COLUMNS` direction IS railed — adding a `_COLUMNS` entry with
  no DDL column reds **34** store tests with `sqlite3.OperationalError: table runs has no column
  named task_list_id`. What no schema rail can catch is the shape that actually occurred: a
  *consistent* DDL + `_COLUMNS` + dataclass triple that nothing writes or reads. That is why this field
  survived, and why the inert-surface baseline does not mention it either (0 hits — its detector does
  not cover a declared-but-unused dataclass field).

### `PP-16` — BLOCKED (E6 scope pressure), 2026-08-25 — needs an owner scope decision

**Not a dependency problem.** `PP-16` is READY: every non-`EXT` dep (`PP-15`, `PP-5`, `PP-13`) is
`done`, it carries no `EXT:` deps, no prior branch or worktree exists for it, and it is the last
open atom in this plan. It is blocked on **size**: the criterion is a single atom whose completion
is a multi-session clean break, and the standing rule is that a PARTIAL atom stays `todo`. One
execution slot cannot land it clean, so starting it would produce exactly the half-migration this
plan exists to eliminate.

**Measured blast radius** (`origin/main` at `827fcbdd`):

| What the criterion collapses | What is actually there today |
|---|---|
| one status vocabulary | `loop/loop.py:44 LoopStatus` **and** `workflows/models.py:440 RunStatus` |
| one adoption/reaping path | `loop/manager.py:581 reap_orphaned_loops` **and** `workflows/watchdog.py` adoption |
| `loop/store.py`'s parallel row retired | `loop/store.py` is **1253 lines**; `loop/manager.py` another **653** |
| five kinds become bundled templates + policies | `loop/kinds/` ships five modules (`design`, `general`, `goal`, `research`, `sdlc`) as pluggable Python |
| one ledger / one projection / one cockpit contract | `loop/` is **26 modules** incl. its own `journal.py`, `lifecycle.py`, `tick.py`, `gates.py`, `watchdog.py` |

Import census: **44** files under `src/` import `personalclaw.loop`, plus **61** under `tests/` and
`web/` — a ~105-file blast radius. The duplicated concerns the plan names are all really there:
budget appears in **15** `loop/` modules, park-on-human in **4**, cancel in **3**.

On top of the code change, the criterion demands *"verified as a user: each of the five kinds is
driven end-to-end through the unified path with its cockpit intact, a kill mid-run is adopted by the
single watchdog, and the flywheel produces a proposal from a loop run's ledger"* — five live
end-to-end drives against a running gateway, which is itself more than one session's work.

**What would clear it.** An owner scope decision splitting `PP-16` into sequenced sub-atoms with
their own `done_when` clauses, in the same style this plan used to decompose the engine's three
unnamed primitives. The natural seams, in dependency order, are: (1) one status vocabulary
(`LoopStatus` folded into `RunStatus`); (2) one adoption path (delete `reap_orphaned_loops`, extend
the workflows watchdog); (3) the five `loop/kinds/` modules become bundled templates carrying a
`SupervisorPolicy`; (4) `loop/store.py` retired onto the run store; (5) the cockpit/projection
contract unified; (6) the five-kind user validation as its own verification session. Each is
independently completable and independently gateable, which is what the completability amendment
asks for.

**Not requesting a roadmap edit here** — the roadmap is owner-maintained, so this entry records the
measurement and the blocker rather than re-cutting the atom. Until that decision lands, the ready
frontier holds no atom an execution slot can drive to a clean gated state: `DFE-5` is gated on owner
task 2 (the editing-library decision, E5), `WF2UNI-12`'s remaining deletion needs loops drained
(which is this atom), `PR2-8`'s `EXT` dep is genuinely absent (zero `adaptive` symbols under
`src/personalclaw/triggers/`, so AUTOMATION-SUBSTRATE's adaptive-clock trigger kind does not exist),
and `WF2LOO-9` already reads `blocked`.

### `PP-16` — 2026-08-26, slice DONE: one adoption/reaping path. Atom stays `todo`.

**The 2026-08-25 `BLOCKED` above asked for an owner scope decision splitting `PP-16` into sequenced
sub-atoms. Taken, as owner:** the six seams that entry named are the decomposition, taken one per
session in its dependency order. Its seam (1) had already landed (`9829f2d4`, terminality derived
from a shared `LifecyclePhase`); this session took **seam (2)**, which
`tests/test_pp16_convergence_census.py` also pinned as the first unconverged clause. The atom row is
NOT flipped — four clauses remain, named at the bottom of this entry.

**The convergence, and why it is a real unification rather than a relocation.** Both work-unit nouns
now decide their crash survivors through **one primitive**, `concurrency.boot_sweep`, called from the
**first poll of the supervisor that owns the noun**. `concurrency.reap_orphans` — the generic reaper
with exactly ONE production caller (the loop side) and none on the run side — was replaced by it, not
kept beside it. `loop/manager.reap_orphaned_loops` (75 lines incl. separators) is deleted; its body is
`LoopWatchdog._boot_sweep` + `_rearm_running` + `_rekick_planning`. `WorkflowWatchdog._boot_sweep`
became `async` and now funnels through the same primitive, keeping only §5.2's substrate rule as
`_sweep_one`. The gateway's separate boot hook is gone.

**What was measurably wrong with the hook, and is now structurally impossible.** The invocation
asymmetry was the part that cost a user something, and neither half is visible from a test of the
sweep's body:

1. **A failed loop sweep was lost for the life of the process.** `gateway.py` wrapped it in
   `except Exception: logger.warning("loop orphan reap at startup failed")` and then constructed and
   started the watchdog anyway. Every loop the sweep should have re-armed then sat persisted RUNNING
   with no worker — the exact "reads as still working while nothing is" failure
   `workflows/watchdog.py`'s own header names. The run side never had this: `_swept` is flipped only
   *after* the sweep returns, so a raising sweep is retried on the next poll. Both nouns now have
   that property, and `test_pp16_boot_adoption.py` asserts it on both live classes (3 polls ⇒ 3
   attempts, `_swept` still `False`) against a vacuity floor that the same driver sees exactly 1
   attempt when the sweep succeeds.
2. **Gateway startup blocked on revival.** The hook was awaited inline at `gateway.py:2467`, and
   `_rekick_planning` runs `plan_walkthrough.advance_plan` — a model call. N stranded PLANNING loops
   delayed everything after it in the startup sequence, including HTTP readiness. Boot adoption is now
   concurrent with startup by construction.

**A third defect found by moving it — the liveness predicate was wrong, not just misplaced.**
`reap_orphaned_loops` classified a loop as a crash survivor unless
`sess is not None and getattr(sess, "running", False)`. That extra `running` condition is wrong
anywhere a session can be idle: **between cycles a live loop's session exists with `running` False**
(autonudge fires a turn every `idle_secs`), so the strict predicate reads a healthy idle loop as dead
and re-arms it — and `manager.start` re-stamps the RUNNING row on the way past, which silently resets
the trust window the user granted. It was harmless only because the hook ran before any session could
exist; moving the sweep into the poll that DOES see sessions is exactly the drift that makes it bite.
**The shipped suite proved it, unprompted:** with the predicate carried over verbatim,
`test_loop_watchdog.py::TestTrustTtl::test_expired_trust_pauses_for_reauth` went red
(`assert 'running' == 'needs_input'`) with the re-arm's `AttributeError` in its captured log — a test
that asserts a live-but-idle loop is trust-expired, not restarted. The predicate is now session
ABSENCE (`_sessions.get(...) is None`), which is the whole crash signal, since `state._sessions` is
per-process. No shipped test was weakened; the new
`test_a_running_loop_with_an_idle_session_is_live_not_a_survivor` pins it directly.

**A fourth defect the move introduced and the gate caught — recorded because of HOW it was caught.**
`reap_orphaned_loops` opened with `kinds.ensure_loaded()`; the first draft of `_boot_sweep` dropped it
and relied on `_poll_once` calling it one line earlier. `_rearm_running` asks the kind for its
`launch_blocker`, and an unloaded registry answers `None` — so a brownfield loop whose workspace
vanished during downtime was silently re-armed against the gone path instead of being parked with a
question. **The behavioural test for this is order-dependent and would have shipped the bug:**
`test_brownfield_orphan_with_missing_workspace_pauses_not_rearms` passed under every `-n0` run (an
earlier test in the same process had already loaded the registry) and reds only under xdist, which is
where it surfaced. The sweep now loads the registry itself, and the property has its own
order-independent rail (`test_the_loop_boot_sweep_loads_the_kind_registry_itself`, a scan scoped to
the sliced `_boot_sweep` body with positive and negative slice-boundary controls, so the two other
`kinds.ensure_loaded()` call sites in the file cannot satisfy it).

**Per-row failure isolation is now shared too**, which the run side did not have: a row whose decision
raises no longer aborts the whole run sweep — it is logged and the remaining survivors are still
decided. That is `reap_orphans`' documented contract ("one bad row can never block startup"), which
only the loop side was getting.

**Known bounded property, shared with the run side deliberately and not papered over:** the sweep
fires on the owning watchdog's first poll rather than strictly before HTTP serving, so a loop created
in the window before that first poll could in principle be seen by it. It cannot be misread now that
the predicate is session absence (a just-started loop has a session), and this is precisely the shape
the run side has shipped with — consistency between the two nouns is the clause's point.

**Census updated in the same commit, as its own message demands.** `adoption/reaping` moved out of
`_UNCONVERGED` and into a third RATCHET beside ledger and attention
(`test_the_adoption_clause_is_converged_and_stays_converged`), which also asserts `gateway.py` has no
`reap_orphaned_loops` again — a re-added hook would restore the duplication even while still using the
shared primitive.

**Deletion sweep, with counts (runtime, not `mypy` — `ignore_missing_imports` cannot catch a stranded
first-party import).** After the change, production references to `reap_orphaned_loops` under `src/`:
**1**, and it is the new `loop/watchdog.py` docstring naming what it replaced. `concurrency.reap_orphans`
production callers: **0** (its only one was the deleted function; `apps/`'s `reap_orphans` is an
unrelated PPID walk on `BackendSupervisor`). Confirmed by importing all five touched modules and
asserting `hasattr` both ways.

**File sizes (the structural watch band):** `concurrency.py` 127→168, `loop/watchdog.py` 985→1106,
`loop/manager.py` 653→**578**, `workflows/watchdog.py` 550→567, `gateway.py` 4219→**4217**. Net −7
across the five. Nothing approaches the 2800 band or the 6000 ceiling, and `config/loader.py` is
untouched.

**Gate:** `make lint` clean (black 2125 files, isort, flake8, mypy **1043** source files, no issues);
`scripts/gate_report.py` **all 6 gates PASS** (config-baseline, inert-surface, docs-lint,
structural-size, structural-import-direction, structural-duplication); the targeted set —
`test_workflows_hardening.py`, `test_structural_baseline.py`, both `PP-16` rails, the field map,
`test_loop_manager.py`, `test_loop_watchdog.py`, `test_workflows_watchdog.py`,
`test_single_flight_reaper.py`, `test_loop_http.py` — **257 passed, 0 failed**; full Python suite
**27,417 passed, 30 skipped, 12 xfailed, 0 failed** (401s). No `web/` change, so the frontend gate is
untouched by design — the cockpit clause is a separate seam.

**Falsifications — each mutated on the LIVE line, `git grep`-confirmed applied, red observed, then
restored from a file copy at its literal path.** Messages are quoted as they actually appeared, not as
predicted:

1. `_swept = True` hoisted ABOVE `await self._boot_sweep()` in `loop/watchdog.py` ⇒
   `test_a_loop_boot_sweep_that_raises_is_retried_on_the_next_poll` reds
   *"Failed: DID NOT RAISE RuntimeError"* (1 failed / 8 passed). The message is worth recording
   because it is not the assertion's own text: with `_swept` set first, polls 2 and 3 skip the sweep
   entirely, so the `pytest.raises` block on the second poll is what fails. Same shape, right cause.
2. The same hoist on `workflows/watchdog.py` ⇒ the run-side twin
   (`test_a_run_boot_sweep_that_raises_is_retried_on_the_next_poll`), same message, 1 failed / 8
   passed. `test_the_retry_rails_can_fail` stayed GREEN under both hoists — correctly, since it
   arranges a SUCCEEDING sweep and one attempt is the right answer for it either way. Recorded so
   the vacuity floor is not mistaken for a second copy of the same assertion.
3. `survived=` restored to the old `sess.running` form ⇒ **2** reds:
   `test_a_running_loop_with_an_idle_session_is_live_not_a_survivor`
   (*"assert {'43f22f4c'} == set()"*) AND the shipped
   `test_loop_watchdog.py::TestTrustTtl::test_expired_trust_pauses_for_reauth`
   (*"assert 'running' == 'needs_input'"*), 2 failed / 55 passed.
4. `concurrency.boot_sweep(` aliased away in `loop/watchdog.py` (`_private_sweep = concurrency.boot_sweep`
   at module level, both call sites renamed — functionally identical, textually absent) ⇒ **3** reds:
   the census ratchet, `test_both_watchdogs_sweep_through_the_shared_primitive`, and
   `test_the_scan_rejects_a_symbol_that_does_not_exist`'s positive control. **Known limit, stated
   rather than hidden:** these are text scans, so an alias is exactly what evades them — the third
   red is what makes the evasion loud instead of silent, and the runtime rails (2) cover the
   behaviour a text scan cannot.
5. A `reap_orphaned_loops` hook re-added to `gateway.py` in its original try/except shape ⇒ **2**
   reds: the census and `test_the_gateway_has_no_loop_boot_adoption_hook`, 2 failed / 15 passed.

6. `kinds.ensure_loaded()` removed from `_boot_sweep` ⇒
   `test_the_loop_boot_sweep_loads_the_kind_registry_itself` reds, 1 failed / 9 passed — and it reds
   while the file still contains **2** other `kinds.ensure_loaded()` calls, which is what the sliced
   scan plus its two boundary controls exist to guarantee.

Post-restore: `git status --porcelain` clean of source changes and
`git grep 'FALSIFICATION\|if False and\|# PROBE\|_private_sweep' -- src/ tests/` shows **zero** new
hits from this diff (all hits are pre-existing, in files this slice never touched).

**Still open on `PP-16` — the four clauses that keep the atom `todo`** (unchanged by this slice):
*one projection to tasks* (`loop/tasks_link.provision` provisions imperatively vs
`workflows/materialize.plan_materialization`); *the pluggable supervisor* — `LoopKindStrategy` +
`loop/kinds/`'s five modules become bundled templates carrying a `SupervisorPolicy` (the noun half is
already done: `loop_aliases.KIND_TO_TEMPLATE` resolves all 5 kinds); *one cockpit contract* (three
frontend pairs — `useRunStream`/`useWorkflowStream`, `runFold`/`workflowFold`,
`LoopCockpitPage`/`WorkflowRunDetail`); *the noun change itself* — `loop/store.py`'s parallel row
(1253 lines) retired onto the run store. Plus the **five-kind end-to-end user validation**, which is
its own session, and the still-open `WorkflowRun.task_list_id` owner decision recorded 2026-08-22.

**For `WF2UNI-12`, the number it asked for: this slice does not move it.** Measured after the change,
`src/` importers of `personalclaw.planning` excluding the package itself: **9**
(`dashboard/chat_plan.py`, `dashboard/handlers/loop_routes.py`, `gateway.py`, `loop/store.py`,
`loop/plan_walkthrough.py`, and the four `loop/*_plan_briefs.py`); the union with modules importing
only the plan-brief modules is **13** (adding `loop/kinds/{design,goal,research,sdlc}.py`). The
chat-plan routes are **6**, at `server.py:1055-1060`. `workflows.intent` importers under `loop/` or
`dashboard/`: still **0**. The drain that changes these is the *store* seam plus the pluggable-
supervisor seam, not the adoption seam — boot adoption never touched `planning/`.

### `PP-16` — 2026-08-26, CI follow-up: the real-home rail failure was PRE-EXISTING, not this slice

PR #2111 came back 16 pass / 1 fail. Every test passed; the run failed at the session-level
real-home rail: *"1 entries under /home/runner/.personalclaw changed during this run. modified
config.json (23667 bytes)"*. **Root-caused to a defect this slice does not touch, with a captured
traceback.** No `ALLOWED_RESIDUE` entry was added and the rail was not weakened.

**The writer, exactly.** Captured by wrapping `atomic_write`/`Path.write_text` and running the full
suite against a relocated `HOME` (`real_home_guard.REAL_HOME` is `Path.home()/".personalclaw"`
resolved at import, so relocating `HOME` reproduces CI's conditions without touching a real home):

```
pytest COLLECTION  (PYTEST_CURRENT_TEST=<none>, in _pytest/python.py::importtestmodule)
  tests/test_aap9_project_stamping.py:27   import personalclaw.mcp_artifacts
    src/personalclaw/mcp_artifacts.py:14   from personalclaw.mcp_core import _resolve_session_key
      src/personalclaw/mcp_core.py:111     _API = _resolve_api_base()      ← module level
        src/personalclaw/mcp_core.py:106   cfg = AppConfig.load()
          src/personalclaw/config/loader.py:5657   cfg.save()              ← migration write-back
            src/personalclaw/config/loader.py:5749 atomic_write(config_path())
```

**Why it is not this slice's, measured not asserted.** (1) `git diff --name-only fc1aac08 HEAD`
touches **none** of `mcp_core.py`, `mcp_artifacts.py`, `test_aap9_project_stamping.py`. (2) The write
happens during **collection**, before any test, any fixture and any watchdog exists — the stack is
`pytest_collection → importtestmodule`, with `PYTEST_CURRENT_TEST` unset. Nothing this slice added
can run there. (3) Exactly **one** such write occurred in the whole suite.

**The coordinator's hypothesis was a leaked watchdog background task writing after monkeypatch
teardown. Refuted, and worth recording so it is not re-investigated:** `git grep 'LoopWatchdog('
-- tests/` finds **12** sites and **none** calls `.start()` — every test drives `_poll_once()` or
`_boot_sweep()` synchronously, so this slice creates no background task at all. `_rekick_planning`'s
model call is also not implicated: **no test both creates a PLANNING loop and calls `_poll_once`**
(measured), so the real `advance_plan` is unreachable from the suite today.

**Why CI-only, and why it reported ONE entry when TWO things changed.** Both answers matter because
each hid half the evidence. The write only fires when `config.json` **exists AND needs migration**;
a developer's own config is already migrated, so `needs_migration` is False and nothing is written —
the rail is green locally *however broken the code is*. And `load()`'s migration copies the old file
aside with `shutil.copy2`, which **preserves mtime**, so `config.json.bak` looks older than the run
and the mtime-based rail cannot see it. Reproduced exactly: a 10,024-byte pre-migration seed became
**24k** with a `.bak` carrying the seed's original mtime — the same transformation whose output CI
reported as 23,667 bytes.

**Fixed at the seam, as the rail's own message demands.** `mcp_core._API = _resolve_api_base()` is
now `mcp_core._api_base()`, resolved at CALL time, with its four use sites updated (three
`f"{_API}{path}"`, one `urlparse(_API)`); `gateway.py`'s now-stale docstring reference was updated
too. This is the **fourth** instance of the one shape `tests/conftest.py::_isolate_real_home_writers`
documents as beyond a fixture's reach — *"a home resolved into a module-level constant at import
time … If a new leak appears here, check for that shape first"* — after
`subagent_persistence._subagents_dir`, `session_map._sessions_dir` and `schedule._DEFAULT_DIR`, and
the fix is the same conversion those three got. It also fixes a **product** bug: the API base was
pinned to whatever `dashboard.url` said at first import, so a port change was invisible to every MCP
tool call until the process restarted, and an MCP child that imported before the gateway wrote its
config bound the wrong port.

**Regression test: `tests/test_import_time_config_writes.py`, deterministic by construction.** Each
rail spawns a **fresh interpreter** with `PERSONALCLAW_HOME` at a tmp home seeded with a genuinely
pre-migration `config.json`, imports the module, and compares `(mtime_ns, contents)`. A subprocess
because the import must be that interpreter's first (`sys.modules` makes a re-import a no-op) and
because the property must hold with **no conftest fixture in play** — which also makes it independent
of xdist ordering, the thing that let the original defect hide. Both `mcp_core` and its collection-time
importer `mcp_artifacts` are pinned, plus a structural ratchet against the constant returning.
**Vacuity leg (`test_the_probe_can_see_a_write`):** the rails are same/same comparisons, so a blind
probe would pass them; the leg drives `AppConfig.load()` on the same pre-migration seed and REQUIRES
the rewrite to be observed. **Falsification:** reinstating `_API = _api_base()` at module level and
pointing the four sites back at it reds **3 of 4** — both behavioural rails (*"importing
personalclaw.mcp_core REWROTE config.json"*, same for `mcp_artifacts`) and the ratchet — while the
vacuity leg correctly stays green. Restored from a file copy.

**Proof the fix holds, under the condition that distinguishes fixed from broken.** Full suite against
a **pre-migration** fake home: **zero** traced writes anywhere under it, and `config.json` still the
54-byte seed with **no `.bak`** — where the same run before the fix produced 24k + a `.bak`. A
normal-`HOME` run cannot prove this either way, because a migrated developer config never triggers
the write; that asymmetry is exactly why this shipped.

**DISCOVERIES recorded, deliberately NOT fixed here.**
1. **`AppConfig.load()` is not a pure read** — it performs a migration write-back, so *any* reader
   can mutate the user's config, and a module-level read becomes a real-home write. That is the
   deeper defect; the import-time constant was only its delivery mechanism. Left alone because
   `config/` is concurrently owner-touched territory and because the write-back (with its `.bak`) is
   deliberate behaviour whose removal is an owner scope decision, not a bugfix detail.
2. **The real-home rail under-reports when a writer uses `shutil.copy2`** — the copy inherits the
   source's mtime, so it is invisible to an mtime-since-start walk. Not a rail bug to fix blind: the
   single-walk mtime design is a deliberate performance choice (the docstring costs it out against a
   >100k-file home). Recorded so the next reader of a 1-entry report knows to check for a
   metadata-preserving sibling.
3. **The boot sweep still makes a model call** (`_rekick_planning` → `plan_walkthrough.advance_plan`).
   **The coordinator is right in principle and this entry agrees:** unbounded adoption latency, and
   because a failed sweep is now *retried every poll*, a provider outage turns the retry into a
   5-second-interval hammer. It is the same objection as this slice's defect 2, one step along.
   **Declined here on scope, not on merit:** removing the call without building its replacement
   strands every restart-interrupted PLANNING loop forever — strictly worse than the wart. The
   correct shape is the sweep leaving the row for the *ordinary* poll to advance, which means
   `_poll_once` growing a PLANNING pass (it iterates RUNNING only today) with its own
   budget/attention/stagnation coverage. That is `PP-16`'s still-open **pluggable supervisor** seam,
   so it goes there rather than being half-built inside a CI fix. The wart, the reasoning and the
   practical hazard (no test reaches it today, so a future one would silently make a real model call
   in the suite — stub `advance_plan` as `TestBootSweep::test_rekicks_planning_orphan` does) are all
   recorded in `_rekick_planning`'s docstring.

**Gate after the fix.** `make lint` clean (black **2140** files, isort, flake8, mypy **1054** source
files, no issues); `scripts/gate_report.py` **all 6 PASS**; targeted set of 14 verified paths —
including the new rail, both `PP-16` rails, `test_aap9_project_stamping` (the collection-time
importer), `test_mcp_core`, `test_mcp_importable`, `test_mcp_json_home_isolation` and
`test_real_home_guard` — **232 passed, 0 failed**; full suite **27,465 passed, 30 skipped, 12
xfailed, 0 failed** (450s), exit code **0**, which is itself the rail's verdict (it sets
`session.exitstatus = TESTS_FAILED` when it fires). Rail line verbatim:
`real-home rail: /Users/golani/.personalclaw unchanged by this run.`

**Harness note, so nobody re-derives it:** the relocated-`HOME` reproduction is a diagnostic
harness, not a gate. Two of its full-suite runs wedged in `pytest_sessionfinish` and were killed by
explicit PID; the official normal-`HOME` full suite completed cleanly in 450s, so the wedge is the
harness's own (relocated `HOME`), not the new test's. Under the relocated `HOME`,
`test_triggers_pathguard::test_it_expands_a_tilde` also fails by construction — it asserts tilde
expansion against the real home — which is likewise a harness artifact and not a finding.

### `PP-16` — PARTIAL (seam 3 of 6: the pluggable supervisor became a policy), 2026-08-26

**Owner scope decision applied.** The 2026-08-25 `BLOCKED` asked for `PP-16` to be split into the
six seams it named; the owner took that decision and this session executed **seam 3: "the five
`loop/kinds/` modules become bundled templates carrying a `SupervisorPolicy`"**. Seam 1 landed as
`9829f2d4`; seam 2 (one adoption/reaping path) is open as PR #2111 and was ABSENT at this branch
point (`origin/main` = `c9fff2f3`), so nothing here touches `concurrency.py`,
`workflows/watchdog.py`, `gateway.py`'s boot adoption, or `loop/watchdog._boot_sweep` /
`_rearm_running` / `_rekick_planning`. **`PP-16` stays `todo`** — a PARTIAL atom does.

**What the seam actually was, measured before designing.** The `LoopKindStrategy` protocol has
twelve members across 3,507 lines of per-kind Python, but exactly **four** of its dispatch sites are
the SUPERVISOR, and all four are in `loop/watchdog.py`: `is_done_signal` (`:843`), `has_done_check`
(`:855`), `budget_stop_genuine` (`:901`), and `_stagnation_disabled` (`:977`, which was not even a
strategy hook — it was `loop.kind == "goal"` hard-coded in the watchdog). Every other member is
intake (`classify`), worker framing (`build_brief`/`cycle_nudge`), planning (`walkthrough`), the
multi-cycle orchestration hook (`on_new_cycle`) or a projection key. That census is what made a
clean one-session slice possible: **the boundary is by CONCERN, not by kind.** All five kinds'
supervisor moved; zero kinds retain a supervisor method. No dual path exists in the supervisor.

**The five implementations used four mechanisms, and two of the five were `return None`.**
`design.is_done_signal` and `sdlc.is_done_signal` were bare `return None` (design deferred to its
hook; sdlc carried a stale *"the multi-stage gate orchestration lands in 2c.iii"* comment).
`general` ran a `verify_command`. `goal` branched on `goal_type` over 204 lines, and `research`
inherited it. So the pluggability bought nothing a declaration could not carry — which is the whole
argument for the closed `DONE_SIGNALS` vocabulary (`orchestrated` | `never` | `verify_command` |
`judge_assessment`) rather than a fifth per-kind hook.

**Shipped.** `workflows/supervisor_policy.py` (516 → 690) gains `ConvergenceSpec`, the closed
vocabulary, `SupervisorPolicy.convergence`, the nine-row `KIND_CONVERGENCE` table (five kinds; goal
and research carry all three `goal_type` variants) and `convergence_key`/`policy_for_kind`. New
`loop/supervisor.py` (304) is the ONE evaluator — the moved bodies of `general.is_done_signal`,
`goal.is_done_signal`, `goal._all_sub_goals_met` and `goal._assess_open_ended`, verbatim including
the P4 calibration canary, the adversarial-skeptic pass and the variance-aware exhaustion band.
`loop/watchdog.py` reads it at all four sites. Deleted: `is_done_signal` from the protocol and all
four declaring kinds, `general.has_done_check`, `goal.budget_stop_genuine`,
`goal._all_sub_goals_met`, `goal._assess_open_ended` (goal.py 699 → 495, sdlc 1602 → 1596, design
551 → 546, general 150 → 133).

**Deliberately NOT in the bundled template JSON, and this is a measured finding.** The clause says
"bundled templates plus policies", and the natural reading is a `supervisor:` block on each
template's `loop` node — the mechanism already exists (`controller._supervisor_policy` parses it,
`validator._validate_supervisor` lints it). **It does not fit: two of the five kinds ship no `loop`
node at all.** `deep-research`'s root is a `sequence` of `infer`/`branch`/`infer`, and
`code-project`'s is a `sequence` whose iteration is a `foreach`. Only `general-project`,
`goal-pursuit-verifiable` and `design-project` have a `loop` node. Declaring there would have
shipped three-fifths of the seam and left `research` and `code` on the plugin — the exact
half-migration the clean-break tenet refuses. A template JSON is also a per-poll disk read whose
absence would silently remove a loop's supervisor; a declared table cannot go missing. So the
POLICY half is a table beside `KIND_TO_TEMPLATE`'s alias, and the atom's own census already recorded
the NOUN half (every kind resolves to a bundled template) as satisfied before this session.

**DISCOVERY, preserved rather than tidied: two hooks answered one question with different keys.**
`GoalKind.budget_stop_genuine` read only `goal_type == "monitor"`, so `research` inherited it and a
research monitor loop's budget stop was GENUINE. The watchdog's `_stagnation_disabled` required
`loop.kind == "goal"` **and** monitor, so a research monitor loop *did* stagnate. Same concept, two
keys, one of them a hard-coded kind name. Both are reproduced exactly in the table
(`research:monitor` is `budget_stop_is_genuine=True, stagnation_enabled=True`), with the asymmetry
commented at the row. Converging it is a behaviour change and therefore not this seam's call — but
it is now visible in one place instead of spread over two modules, which is the point of the table.

**Import sweep for the deletions: 25 modules imported at RUNTIME, all clean** (`mypy` cannot see a
stranded first-party import — `ignore_missing_imports=true`), plus a live assertion that none of the
five registered strategies retains `is_done_signal` / `has_done_check` / `budget_stop_genuine`.
Importer count for `personalclaw.loop.kinds` is unchanged at **32 files** before and after
(18 `src/`, 12 `tests/`, 1 script, plus `loop/kinds/__init__.py` itself) — this seam removed members, not the module, because `LoopKindStrategy` legitimately
survives as the intake / worker-framing / projection seam.

**The old census row could not have caught this, which is worth recording.**
`test_pp16_convergence_census.py`'s "pluggable supervisor" row pinned `class LoopKindStrategy`
against `class SupervisorPolicy` — and `LoopKindStrategy` still exists, so the census **passed
unchanged** after the supervisor was retired. A row keyed on a CLASS name cannot measure a
per-METHOD retirement. The row is re-homed into the converged-ratchet group and re-keyed on the
retired MEMBERS plus a "every kind resolves to a declared row" vacuity floor.

**Still open on `PP-16`** — four clauses, unchanged by this seam: one status vocabulary
(`LoopStatus` 13 vs `RunStatus` 8), one adoption/reaping path (seam 2, PR #2111), one projection to
tasks, one cockpit contract; plus `loop/store.py`'s parallel row (1,253 lines) and the five-kind
user validation. What remains PLUGGABLE in `loop/kinds/` after this seam, named so the next slice
inherits a measurement: `classify`, `build_brief`, `cycle_nudge`, `walkthrough`, `on_new_cycle`,
`phase_key`, `default_kind_config`, `deliverable_name`, `launch_blocker`, `provisions_tasks`,
`validate_config`, `turn_capabilities`/`turn_directive`, `default_phases`, and the four metadata
attributes. Those are a bundled template's node prompts and graph, not the supervisor.

**The `WorkflowRun.task_list_id` owner decision (2026-08-22) was NOT reached by this seam** and
remains open — nothing here touches `loop_run_map`'s destinations or the homeless-field set.

**NOT taken, and named rather than forced:** the seam-2 agent handed over the idea that
`_poll_once` should grow a PLANNING pass so `_rekick_planning`'s boot-path model call can retire.
It does not fall out of this work — `_poll_once` iterates RUNNING rows and a PLANNING pass needs its
own budget/attention/stagnation coverage plus a classifier-advance path, which is a separate
completable slice against files seam 2 currently owns. Left for a later seam.

- **[2026-08-27] OWNER RULING — `PP-16`'s six-way decomposition is APPROVED, and three seams have now shipped under
  it.** An independent 98-atom audit measured this capstone at ~6 sessions across **eight** distinct convergences
  (status vocabulary, adoption/reaping, attention, ledger, tasks projection, cockpit contract, the store row, the
  five-kind validation) — which is why bolting it on as one atom failed twice and produced two `BLOCKED` entries.
  Shipped: seam 1 `9829f2d4`; seam 2 (one boot-adoption path) PR **#2111**; seam 3 (the supervisor becomes a declared
  policy, retiring pluggable Python across all five kinds) PR **#2135**. **Four clauses remain:** one status
  vocabulary (`LoopStatus` 13 vs `RunStatus` 8), one projection to tasks, one cockpit contract, and `loop/store.py`'s
  parallel row — plus the five-kind as-a-user validation. The remaining seams are to be authored as real atom ids so
  the roadmap tracks closable units rather than one unclosable capstone. **The `WorkflowRun.task_list_id` question
  (open since 2026-08-22) is NOT resolved by this ruling** and remains owner-gated.
