# Workflows — the Deterministic Orchestration Engine

A **workflow** is a declarative graph the engine executes: a spec tree of typed
nodes, driven by one conductor per run, with every outcome journaled. Where a
loop is an agent iterating toward a goal it judges for itself, a workflow is a
shape the author decided in advance — so the engine can schedule it, resume it,
edit it mid-flight, and rewind part of it without asking a model anything.

The engine lives in `PersonalClaw/src/personalclaw/workflows/`.

## The rule everything follows

**A run has exactly one writer** (WF2-R10). The `RunController` tick loop under
its own lock is it. Nothing else — not a dispatcher, not the watchdog, not an
HTTP handler — writes a run's terminal status. Handlers *request*; the loop
decides and writes.

That single rule is what makes the hard features tractable:

- **mid-flight mutation** is only safe if there is a well-defined moment when
  nothing is being scheduled. Here that moment is "between scheduling steps,
  holding the lock", which is why every mutation is *queued* and applied at the
  controller's drain point rather than written directly;
- **crash recovery** is only correct if terminal writes are serialized, or a
  resumed run and a still-dying task race to disagree about the outcome.

The tick loop is deliberately boring:

```
while not terminal:
    drain cancel intent
    compute frontier (pure)
    launch admitted work
    await *something* finishing
    apply results, persist state
```

## Module layout

| Module | Job |
|---|---|
| `models.py` | the spec algebra — node kinds, states, run/def records |
| `tick.py` | `frontier()` — a PURE function from (spec, states) to what may run |
| `controller.py` | the conductor: one per run, the only writer of run state |
| `engine.py` | one dispatcher per node kind; the only place real work happens |
| `bindings.py` | the `{{…}}` expression language and its closed pipe set |
| `conditions.py` | the ONE boolean-condition dialect: gate `expr`, loop `until`, `success_when` |
| `execution_hints.py` | the `runtime_hints.execution` half — today, WIP=1 (`single_active_feature`) |
| `journal.py` | the resume cache and the Run Ledger (one append-only file, read two ways) |
| `store.py` | persistence — runs, specs, state, outputs |
| `mutations.py` | the typed edit grammar and its structural rules |
| `checkpoints.py` | fork, revert, prune |
| `human_input.py` | typed asks and durable resume tokens |
| `gate_policy.py` | risk-scoped auto-approval |
| `attention.py` | a waiting gate → a durable inbox row + one notification |
| `context.py` | handoffs, carryover buckets, decision records |
| `compaction.py` | the two-layer prompt-compaction ladder for LLM-backed nodes: proactive at ~80% of the bound model window, then aggressive re-compaction + one retry on a length rejection, degrading to drop-with-placeholder if a summarizer raises. Wraps `personalclaw.context_compaction` — it does not reimplement it |
| `macros.py` | template macros, expanded at definition time |
| `blocks.py` | shared prompt blocks, resolved at definition time |
| `coalescer.py` | per-observer event batching in front of the SSE write |
| `projection.py` | the schema-validated run snapshot |
| `resilience.py` | retries, circuit breaker, budgets |
| `preflight.py` | run-start checks — credentials, binaries, models, providers |
| `audit.py` | the `workflow_audit` maintenance op (diagnose / heal) |
| `judge_contract.py` | the typed verdict enum, rubric ratchet, engine-computed overall, forbidden-mode denylist |
| `judge_pretier.py` | the free rule tier that runs BEFORE any judge model call, plus the deterministic `fallback_check` |
| `judge_actors.py` | the actor-transition invariant (a worker may never reach `done`) and judge isolation |
| `loop_middleware.py` | the breaker's next tier: call fingerprinting, failure-class routing, the Continue→Nudge→Escalate→Halt ladder, the interrupt queue |
| `judge_calibration.py` | the nodding-loop detector, divergence records, stuck detection, and the verdict ledger they read |
| `loop_aliases.py` | read-time aliases for legacy loop-kind references, and cockpit stream-key equivalence |
| `longrun.py` | long-run watcher mechanics: item identity, the persistent seen-set, bounded sibling views, buffer-seal, the adaptive-delay clamp, lineage caps |
| `intent.py` | the no-LLM intent classifier: the (complexity, uncertainty, stakes, time_pressure) tuple, irreversibility, and rigor routing |
| `matcher.py` | tiered template matching T1-T5: keyword index, metadata scoring, shape filter, cached embedding tie-break gated by `workflows.match_threshold`, LLM summarize-then-rematch |
| `preamble.py` | the grounding preamble (UP-R14): the deterministic entity-resolution first node with its identity guard and degraded fallback, and the topic extraction that feeds the grill's lookup channels |
| `brownfield.py` | the brownfield context pass (UP-R17): the depth-filtered tree + README head + project-metadata synthesis, tree-hashed and cached per project with a 7-day TTL, rendered as the prompt's `CODEBASE_CONTEXT` |
| `grounding.py` | the grounding bundle: node taxonomy, provider signatures (three discovery tiers), MCP servers, binding roots, model capability |
| `patterns.py` | the seven proven graph shapes, their slots, when each is WRONG, and the deterministic shape pick |
| `generation.py` | the generated planning prompt, the mechanical self-check, repair-not-regenerate, and the decline path |
| `contracts.py` | derived parameter schemas, per-stage done-means contracts and their lint, and blocking-vs-open decision typing |
| `revision.py` | typed merge-by-id patches, the NO_UPDATE sentinel, TTL'd draft sketches, and the announce-block review surface |
| `autonomy.py` | the risk-signal registry, autonomy floors and offers, HITL/AFK typing compiled to `require_hitl`, the confirmation matrix, the two interrupts, earned trust |
| `grill_protocol.py` | the structured `rigor: deep` protocol: recommendation-bearing questions, the facts-vs-decisions channel split, adaptive pacing, stress probes, the Step-0 schema, frozen prohibitions |
| `rigor.py` | the cheap end of the axis: `rigor: fast` + its auto-scheduled refinement gate, Specify's one-stage rewrite, the append-only acceptance ratchet, revise-spec-from-artifact |
| `template_pipeline.py` | chat-session mining, discover-then-freeze candidates on the scope ladder, the `suggest_template` nudge with its anti-nag rules, entity scrubbing |
| `template_store.py` | the state writer behind that pipeline (which stays pure): file-backed per-shape `NudgeState` so a cooldown or a permanent DECLINE outlives the process, and frozen `Candidate` templates so the same intent resolves to one graph across runs |
| `eval_specs.py` | per-template eval specs derived from the template artifact — fixtures, structural and parameterization checks, and the named-not-graded judge surface |
| `containers.py` | the Work board projection (state grouping, claim leases, per-section `/work` isolation), the substrate-checked boot sweep, and the project context block + wayfinder ledger contract |
| `leases.py` | the flock-backed claim files behind `containers.claim`/`release`: `single_flight`-guarded read-modify-write over a per-target lease file whose `expires_at` outlives the process, so a claim stays truthful across a gateway kill |
| `publish.py` | the `publish:` declaration, material-change version gating, typed lineage (flattened to scalar event metadata), evidence bundles, the terminal handoff report and the append-only results ledger |
| `filedrop.py` | the per-run file drop (spec-declared, approval-gated multipart ingestion into the run's `immutable` `dropped/` zone, fenced on read) and the outbox — the run's published-artifact listing projected from the publish journal rather than a second registry |
| `pinned.py` | the pinned-artifact set a user curates for the composable home (`entity_settings/pinned_artifacts.json`), owning its own entity file the way `channel_trust` does. Stores only slugs — name, kind and version are re-read from the artifact on every load, so a rename or a new version cannot leave a stale pin |
| `project_archive.py` | the archive I/O around `project_export`'s planner: an allowlist walk into `plan_export`, a manifest ZIP, and extraction into a per-call temp dir reaped in `finally`. Writes only plan-ACCEPTED entries so the archive and its manifest cannot disagree, reuses `project_export.safe_member` rather than adding a second path check, and offers optional AES-GCM via the `oauth2` extra's `cryptography` |
| `batch_compile.py` | batch `subagent_run` compiled to a `parallel[stage...]` run: the N≥2 threshold, capability classes, the single-writer lint, static depth rejection, typed leaf outputs compiled to `output_contract`, and the safety-filtered recall view. Called from `mcp_subagents._run_compiled_batch`, which persists the compiled spec as a def and starts a run against it, so the widget rebuilds from disk after a restart. Emits the top-level `workspace:` block the run-start applier reads (`provisioning.declares_workspace`), so the fan-out is provisioned into one isolated `scratch` substrate — RUN-scoped, because there is no per-node provisioning in the engine. A crash-surviving batch is therefore SUSPENDED with a Resume affordance rather than auto-adopted (§5.2), since `stamp_run` records a recoverable `worktree_path` for every isolated mode |
| `roster.py` | the slug-keyed agent catalog PROJECTION over `config.json agents{}` plus the reserved system names (WORK-R16) — owns no state and reads the SAME `AppConfig.agents` dict `subagent._validate_agent` checks, so there is one answer to "which agents exist". Consumed in production by `batch_compile.agent_lint`, which slug-resolves each leaf's declared agent and persists the resolved CONFIG KEY (never the slug — `_validate_agent` checks config keys, so a persisted slug would fail every multi-word agent name); also supplies the `unresolved_slugs` drift check the test gate runs over every bundled template |
| `workspace.py` | the `workspace` provisioning block (mode/preserve/setup/teardown/env), reserved-var rejection, the secret-filtered spawn env with presence-only flags, and tolerant `.folder.yaml` contracts |
| `ownership.py` | run-owned session keys (`workflow:<run>:<node>`), the SEL + prompt-use-case registrations, incognito/temporary inheritance read from both the durable line and the live registry, and the engine-level learning-node skip |
| `needs_input.py` | the NeedsInputItem card (block kind, blocker, attempted, evidence, recommendation, one decision), owner binding, once-only staleness re-notify, and the refs round trip |
| `worktrees.py` | code-kind run worktrees on the proven `loop/worktree.py` machinery: preserve-in, marker-guarded setup, resume safety, teardown-before-deletion, the per-run branch, the machinery-free review diff, and the two reintegration verbs |
| `provisioning.py` | the PERFORMER for `workspace.py`'s plan and `worktrees.py`'s decisions: create → preserve → setup at run start (`controller._prepare`), setup/teardown steps as no-shell ceiling-wrapped subprocesses, the PID-liveness lock OUTSIDE the workspace, the run-record stamp (`worktree_path`, `preserved_workspace_path`), teardown-before-deletion for both deletion paths, and the cockpit's diff + reintegration offer |
| `introspection.py` | the nine-question checklist, RunStats as a pure journal projection, verification debt, said-no gate statistics with a sample-gated fake-check badge, per-template p50/p95 cards, and the Proof section |
| `project_export.py` | project export/import: the allowlisted portable set, per-entity sha256 in a versioned manifest, secrets as presence flags only, import refusals (unsafe member, digest/size mismatch, unknown schema) and `imported-N` collision slots |
| `materialize.py` | tasks as a projection of run state: the exhaustive state→status table, `blocked_kind` derivation, fingerprint dedup, fan-out caps with a parent counter, the managed/produced/standalone split and the engine-owned-field write rejection |
| `verified_done.py` | engine-owned criterion execution over the `loop/gates` tristate, pass-state gating, the three-actor transition matrix, the weighted acceptance schema, cascade-fail over the binding graph, the stuck-work sweep and idempotent timing |
| `confirmation.py` | the one durable ConfirmationRequest record: construction-time redacted previews, per-type expiry policy, the four-verb resolution vocabulary, `require_hitl`, per-stage mute, tool profiles and the DagView approve/deny card |
| `surfacing.py` | SOP surfacing discipline: the `surface_mode` ladder (off/passive/suggest), trigger-phrase lint + collision check, the shared `!`-negative veto plus planning/paste/named-workflow vetoes, one-source-two-wrappers rendering with a verbatim digest fence, per-def graduation, SOP migration and the reachability doctor |
| `surfacing_channels.py` | The two non-semantic surfacing channels plus the contracts that gate a suggestion: cadence/recency (freshness gradient, overdue-first sort, once-daily escalation throttle, last-completed derived from real run history), workspace fingerprint packs (weighted globs, bounded scan, propose-don't-enable with per-project dismissal), layered scope resolution with visible shadowing and per-stage overlays, the three-state requirements preflight, schema-driven parameter pre-fill, and the reachability doctor + trigger-accuracy fixtures |
| `pool.py` | Task-pool concurrency: the `frontier`/`next` projections over all tasks (priority + blocking-count + overdue, leased work excluded), TTL'd compare-and-swap leases with takeover/renew/release rules and an expiry sweep, evented unblock with `dependency_failed` cascade and burst coalescing, delegated write-time acyclicity, hand-off edges with an allowlisted context carry, and blueprint sessions (numbered guided conversations, replace-not-merge hydration) plus the passive/blueprint/run router |
| `settings.py` | The config knobs the runtime actually reads: one resolver per live-editable `WorkflowsConfig` field (`surface_mode_default`, `max_materialized_per_foreach`, `confirmation_ttl_secs`, `lease_ttl_secs`), each with the module constant as a fail-safe fallback, clamped to the bounds the records enforce, and deliberately uncached so a PATCH takes effect without a restart |
| `scope.py` | filesystem write-scope enforcement by post-hoc diff |
| `watchdog.py` | the supervisor: adoption, reaping, per-run publishing |
| `overlap.py` | `on_overlap`: the exhaustive policy decision with a raising tail, the queued-vs-hand-made-draft marker on `run.extra`, the coalesce-to-one cap, and the single-flight drain called from the terminal writer and the watchdog poll |

## Containers do not execute

A `sequence` has no work of its own — it is a scheduling policy over its
children. So `frontier()` recurses into containers and only ever returns leaf
work, and a container's state is **derived** from its children rather than
stored. That is why `container_outcome()` exists and why nothing writes a
container's state directly: after a rewind resets children, the container's
verdict is recomputed instead of patched.

One consequence worth knowing: an *untouched* container derives as `RUNNING`,
not `PENDING` — "has unfinished children" is running by that definition. Code
that asks "has this subtree started?" must look for recorded state, not derive.

## Scheduling: three rules that carry the weight

**Active-edge join gating (WF2-R18).** A join waits only on predecessors whose
edge is on an actually-taken path. A `branch` picking `cases[bug]` leaves
`cases[feat]` unreachable, and a join waiting on "all predecessors" would
deadlock forever; a join firing on "any completed predecessor" fires early on a
fan-out whose other legs are still waiting. Both directions are bugs. The rule
that satisfies both: **a `needs` edge is satisfied by any TERMINAL predecessor,
and unreachable paths are MADE terminal by marking them skipped.** Declines are
recorded explicitly, never inferred from "the source routed elsewhere" —
inferring would starve a sibling whose `needs` merely names the branch.

The wait-entry subtlety: a `wait`/`gate` enters `WAITING` rather than
completing, and `WAITING` is not terminal, so a join behind it keeps waiting
instead of firing on the fast leg alone.

**Typed lanes (WF2-R21).** Ready work is admitted per-lane by node kind, so a
`foreach` over minute-long IO actions saturates the `io` lane while `llm` stages
keep flowing. Excess stays `ready` — the next tick admits it.

**Per-container concurrency.** A `foreach`'s `max_concurrency` caps items in
flight independently of the lane caps. They answer different questions: a lane
cap protects the *engine*, this protects the *run's shape* (this fan-out takes
two at a time because each item holds a lock).

`frontier()` reports `blocked` when nothing can run and nothing is running.
That state is a deadlock, and naming it is the whole reason it is computed
rather than assumed.

## Node kinds

Thirteen, and no more. Every orchestration pattern is a *composition*:

| Kind | Notes |
|---|---|
| `sequence` `parallel` `foreach` `loop` | containers; DAG shapes come from per-child `needs` |
| `stage` | one subagent execution — tools, session, can spawn |
| `infer` | exactly ONE bounded model call — no tools, no session |
| `visualize` | ONE bounded reasoning-axis call → a genui widget spec — no tools (agency-free) |
| `branch` | conditional dispatch on a binding |
| `transform` | zero-token pure data reshaping |
| `action` | zero-token action-provider dispatch |
| `wait` `gate` | deadline / human-input |
| `subworkflow` | a real nested CHILD run, depth ≤ 3 |

`stage` and `infer` are separate kinds for a real reason: a template that needs
a classification should not pay for a subagent, and the lane accounting depends
on distinguishing them. A judge panel of five `infer` nodes is five bounded
calls; the same panel as `stage` nodes is five concurrent sessions.

**Action arguments go under `config.with`.** A flat argument beside `provider`
reaches the provider as an empty config — it then reports its own required field
missing for a value visibly present in the spec, and every downstream binding
fails. Validation refuses the shape at authoring time.

## Bindings

`{{inputs.x}}`, `{{nodes.<id>.output}}`, `{{item}}`, `{{secret:KEY}}`, with a
**closed** pipe set. No eval, no filesystem, no arbitrary expressions — a spec
is user- and model-authored text, and an expression language would make it a
code-execution surface. An unknown pipe is *refused*, never ignored: a silently
dropped sanitization pipe leaves a spec that looks sanitized and is not.

Two asymmetries that are easy to get backwards:

- **a null output is a value; an unresolvable reference is an error** (WF2-R9). A
  node that legitimately produced nothing hands `None` downstream and `default`
  absorbs it. A binding naming something that does not exist raises — because
  the silent alternative is a prompt with a hole in it, which produces confident
  nonsense that reads like a real answer;
- **declared input defaults are applied at run start**, not lazily at each
  binding, so the run record shows the values the run actually used.

## The journal: two jobs, one file

**Resume cache.** Keyed by `(instance_path, epoch, inputs_hash,
spec_region_hash)` — all four earn their place. `epoch` because a rewind bumps
it, so a replayed region from a superseded epoch can never be mistaken for a hit
on the current one. `inputs_hash` because an upstream change makes a cached
output stale even when the node itself was untouched. `spec_region_hash` because
editing a prompt and resuming would otherwise serve the pre-edit answer.

A consequence: **a rewind produces no cache hit.** It bumps the epoch, and the
epoch is part of the key, so the region correctly *misses*. The cache serves a
resume; invalidation is what serves a rewind.

**Run Ledger.** The event subset a downstream refiner reads. These are emission
*requirements*: an evaluator that wants to know which model a step used, what it
cost, and why it failed is starved if the engine only journals free text. The
acceptance bar is that prompt → tool calls → output is reconstructable from
ledger events alone.

Everything written passes through `redact()` first. A journal is read by the
flywheel, shipped in bug reports, and rendered in a UI — a credential reaching
it is leaked to all three. Outputs past ~64KB, or matching a binary magic
prefix, spill to a file and leave a typed `result_omitted` stub.

## Mid-flight mutation

A typed op grammar (`update_node`, `insert`, `delete`, `move`, `skip`, `rewind`,
`run_from`, `fork`, …), and four things guard it:

1. **A live controller is required.** Editing a run nobody drives would write
   state with no one to apply it.
2. **Batches are queued, applied at the drain point.** `edit_run` returns
   `queued: true`; nothing has changed yet.
3. **The frozen-region invariant.** A COMPLETED node cannot be edited — its
   output is already downstream, and changing the spec that produced it would
   make the run's own history a lie. The user's order is *rewind, then edit*.
4. **TOCTOU re-verify + `expect_version`.** Two edits computed against the same
   version cannot both apply; the second was reasoned about against state that
   has moved. The version lives on `run.spec_version`.

A rewind whose cascade would re-run completed work reports
`needs_confirmation` and applies nothing until confirmed. The cascade is
computed over the **binding-dependency graph**, not the container tree, so
editing a node invalidates what actually reads it.

## Timeouts: two knobs that mean different things

`timeout_total` bounds wall time. `timeout_stall` bounds *silence*, and it is
fed by progress — a long-but-progressing node survives, a wedged one does not.
If nothing feeds the stall clock the two collapse into one and a node that is
visibly working gets killed as wedged; the `on_progress` callback threaded
through `dispatch` is what keeps them distinct.

`0` means unbounded, for both. A cap the user did not ask for that silently
halts work is worse than no cap.

## Context lifecycle for long horizons (WF2-R6)

Compaction keeps the *what* and drops the *why*, so a compacted loop
re-litigates settled decisions and re-reads verified files. Three mechanisms:

- **handoffs** — `session: fresh` (the default for iterated bodies). An
  iteration writes verified state / changes / what is NOT verified / next
  action, and the next iteration starts from that. `session: continuous`
  injects nothing, because it already holds the previous iteration;
- **carryover buckets** — typed, bounded, deduped facts (files touched with line
  spans, claims verified, children spawned). Structure survives summarization;
  prose does not;
- **decision records** — `{choice, reason, rejected, constraints}`. The rejected
  alternatives are load-bearing: without them a resumed run re-proposes the
  option already dismissed.

All three are journaled, so rewind and resume *replay* them rather than
reconstructing a summary, and all three are bounded — an unbounded bucket is a
transcript with extra steps.

## Events

Per-run SSE on `DashboardState.workflow_sse()` (key `workflow:<run_id>`), plus
WebSocket refetch signals. Deliberately NOT routed through `notify()`: that is
the user-notification gate behind mute/severity/quiet-hours, and a quiet-hours
setting would silently eat a run's entire event stream.

Every event carries `event_id` (deterministic), `seq` (monotonic) and `epoch`
(the run's), stamped at ONE publish seam — a call site that forgot one would
emit an event the FE cannot dedup or supersede, and that is invisible until a
rewind duplicates a row. High-frequency node chatter is batched per observer
into a `workflow_batch` frame; members keep publish order and their own
envelopes, so batching is a transport optimization and nothing more.

The FE folds events through one pure function (`workflowFold.ts`) with four
guards: dedup by event id, epoch supersede-drop, node-keyed patches, and a
per-node `seq` floor.

## Genealogy and nesting

`subworkflow` runs a named workflow as a real **child run** — its own journal,
state map and terminal writer. That costs a row and a directory and buys
everything that matters: the child can be rewound, resumed, forked and inspected
on its own, and a crash mid-child leaves a child run to adopt rather than a
half-written parent.

`parent_run_id` answers "who spawned this?"; `root_run_id` answers "show me
everything this request did", which at depth 3 the parent chain cannot. A
`child_run_attach` ledger event records *which node* spawned it — the run row
does not, and a rewind of that node needs it.

Depth is capped at 3 and checked **before** anything is created: a workflow
referencing itself would otherwise spawn runs until the process died.

## Write scope (WF2-R19)

Stage and action nodes declare `allowed_write_paths`; the engine snapshots the
filesystem before the node and diffs after. Out-of-scope writes flag the node
`scope_violation` in the ledger with the violating paths named.

The load-bearing asymmetry: the **watched** set is wider than the **allowed**
set. An escape lands outside what is allowed, so snapshotting only the allowed
paths would make a violation undetectable by construction. Both sides are
resolved (symlinks, `..`) at comparison time.

Opt-in, deliberately: the tree walk is real work, and a fan-out of fast
transforms must not each pay for one.

## Templates

Six ship in `workflows/bundled/`, served read-only from the package — an
upgrade ships new templates with no "did the user edit it?" reconciliation. A
user who wants to change one instantiates it and edits their copy.

Authoring conventions, the lint that enforces them, and the macro/block
libraries are documented in
[`docs/guides/workflow-templates.md`](../guides/workflow-templates.md).

## The judge contract: self-approval is impossible, not discouraged

A loop that judges its own work converges on whatever the worker finds easiest to
claim. Prompt doctrine ("be skeptical") is advice, and advice loses to a worker being
scored on completion. So four mechanisms are structural rather than instructional:

**The worker actor may never reach `done`.** It can reach `waiting` or `review`; the
terminal transition belongs to a judge or gate actor (`judge_actors.check_transition`).
A worker claiming completion produces a `review` — a *request* for adjudication, not
the adjudication. A `done` from a worker is REDIRECTED rather than refused, because the
work may genuinely be finished and the right answer is to route it to a checker.

**A PASS without cited proof is invalid.** Rejected by the contract, not frowned upon.
A completion record without proof is a claim, and the point of a checker is to stop
accepting claims.

**The rubric ratchets; the overall is engine-computed.** Under `ratchet: strict` any
criterion below target fails the stage — no averaging, because averaging is how a
broken deliverable passes on the strength of its documentation. The overall score is
recomputed from dimension scores server-side; the model's own overall survives only as
metadata, so drift between them is visible instead of authoritative.

**The judge never sees worker narration.** `assemble_judge_evidence` keeps
user/spec/tool-call/tool-output roles and drops assistant prose entirely. A worker
cannot argue its way to a PASS if its arguments never reach the judge — stronger than
any instruction to discount them. Provenance ("attempt 4 of 5") is stripped too: it
tells a judge how much patience is left, which is exactly the pressure that produces a
lenient pass.

Beneath all of it, `judge_pretier` runs the free rules first — empty output, admitted
give-ups, tool errors, stub markers, missing referenced files, zero artifacts. Loop
judges run every cycle, so a rule-solvable failure reaching the model costs tokens on
every iteration forever. The pre-tier can prove work is *unfinished*; it can never
issue a PASS, since a cheap approval would recreate self-approval with extra steps.

`fallback_check` is a standing cross-check, not only a degradation path: when the
deterministic check FAILED and the judge passed anyway, the verdict auto-escalates. A
judge that passes what `exit 1` failed is either wrong or being gamed, and both need a
human. The check is tristate — `None` means "could not run", and collapsing that into
failure would turn an uninstalled linter into a broken deliverable.

## Where things are NOT

- **no compaction ladder yet** — the two-layer proactive/error-triggered
  summarizer from WF2-R6 needs a summarizer seam that does not exist;
- **no `{{nodes.x.artifact}}` binding** — oversize outputs spill to a stub with
  an `output_ref`, but the artifact-pointer binding form and `artifact_inspect`
  action are unbuilt;
- **no lifecycle gates** — per the owner's deferral, class-B changes are clean
  breaks under the pre-1.0 banner until the migration-backed lifecycle regime
  lands.
