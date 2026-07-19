# Plan: Workflows v2 — Composable Execution Platform

**Status:** PROPOSED (rev 2 — research-integrated 2026-07-12)  
**Created:** 2026-07-11  
**Revised:** 2026-07-12 (21 approved research recommendations folded in; recon-corrected against code)  
**Supersedes:** E4/EVOLVE-WORKFLOWS #28 (the current stateless SOP feature)  
**Prerequisite:** None (self-contained clean break)  
**Branch:** campaign/six-unexecuted-plans (target)

---

## Research Integration (2026-07-12)

Approved recommendation IDs folded into this revision (mechanism-level, not appendix):

- **WF2-R1** (effect ledger + idempotency + per-def dispatch lock w/ lease + on_overlap policy) → §2 Side Effects, §4 tool idempotency, §5 ledger
- **WF2-R2** (binding-dependency rewind cascade, preview, run_from, inputs-hash memoization + batch-4 amendments) → §3 Mid-Flight Mutation
- **WF2-R3** (engine-owned completion, verification ladder, required_artifacts, typed verdicts, fresh-judge) → §2 Engine-Owned Completion
- **WF2-R4** (attempt records w/ mutation hints, circuit breaker, soft budgets, pre-charge + baseline check + amendments) → §2 Failure/Retry/Budgets, resolves Open Question 4
- **WF2-R5** (degraded completion, typed error taxonomy, two-knob timeouts, foreach per-item policy + amendments/batch-5 timeout tests) → §1 Node Outcome Model, §2 Timeouts, §8 Slice 11
- **WF2-R6** (per-iteration session reset w/ journaled handoffs, decision records, output offloading + amendments) → §1 stage fields, §2 Context Lifecycle
- **WF2-R7** (typed ask payload, mode-dependent gate timeouts, durable resume tokens/continuation records, transient-hold event gates + amendments) → §5 Human-Input Contract, resolves Open Questions 2 (partial) + 3
- **WF2-R8** (capability-dispatched schema enforcement, output_contract for non-LLM nodes + amendments) → §2 Structured Output
- **WF2-R9** (binding-language hardening: type semantics, BindingError, sanitization pipes, injection lint) → §1 Binding Expressions
- **WF2-R10** (sticky cancel intent, terminal-write ownership, protocol-violation blocked state, workflow_audit) → §2 Termination Robustness, §4 tools
- **WF2-R11** (event dedup key, snapshot-then-subscribe, replay harness, workflow_observe + event-fold law + batch-5 StateMonitor coalescing) → §5 Event Pipeline, §4 tools, §8 Slice 11
- **WF2-R12** (never-throw parse, typed repromptable errors, strict mode, dry-run, preflight, generated manifest) → §4 Spec Ingestion
- **WF2-R13** (genealogy fields, per-node cost/model, crystallize-before-prune, deletion sweep contract + amendments) → §1 WorkflowRun, §5 Run Ledger, §1 Retention
- **WF2-R14** (credential_ref bindings + RedactingSink) → §1 Secrets Hygiene
- **WF2-R15** (template conventions pack: triage-first, Finding record, baseline capture, shared blocks, steering examples) → §6 Templates
- **WF2-R16** (`infer` node kind + batch-5 model-tier slot map) → §1 Node Taxonomy, §2 dispatch
- **WF2-R17** (`branch` node kind + route macro + batch-5 classifier-then-dispatch convention) → §1 Node Taxonomy, §6 Templates
- **WF2-R18** (active-edge convergence gating in frontier()) → §2 Scheduling
- **WF2-R19** (filesystem write-scope enforcement via post-hoc diff) → §2 Write Scope
- **WF2-R20** (mutation-op grammar hardening for LLM authors) → §3 Mutation Grammar
- **WF2-R21** (typed executor lanes: per-resource-kind concurrency caps) → §2 Scheduling, §1 config

Recon corrections applied in this revision (verified against code 2026-07-12): the SubagentManager "no recursion" contract is **prompt-level only** today (`_SYSTEM_PREFIX` in subagent.py — no code check exists), so `__wf_depth` is genuinely new enforcement mirroring the existing `__hook_depth` pattern (hooks.py:721, invoke_agent_provider.py:66); run events are delivered via a per-run `SseRegistry` + the multiplexed WS `_broadcast` (the loop pattern, dashboard/state.py:1056) — NOT `state.notify(...)`, which is the user-notification gate behind `notification_allowed` (mute/severity/quiet-hours) and would eat engine events; every new SSE event type MUST be registered in the FE stream hook's lifecycle union (useRunStream.ts `RUN_LIFECYCLE` — EventSource silently drops unregistered types); the old feature's match threshold is **0.62** (`workflows.match_threshold`, loader.py:1052 — 0.55 is the *skills* surfacing threshold, unrelated); config wiring is **four** points, not two (see §7.5); and deleting `workflows/registry.py` outright would orphan the `workflow` provider _TypeHandler and break `PROVIDER_TYPES` ↔ handler parity (the #47 bug class) — the handler is repointed, not removed (§7).

---

## Overview

Replace the current "mini skill / checklist SOP" workflow feature with a full composable workflow-execution platform modeled on the ultracode/Workflow tool pattern. The new feature supports:

- Declarative graph-spec workflows with composable execution constructs (sequences, parallel fan-out, loops, conditional branches, waits, gates, agent orchestration patterns, sub-workflow nesting)
- A journaled execution engine with resume, rewind, run_from, fork, and crash recovery — with external side effects tracked so time travel is safe
- Mid-flight editing of unexecuted stages via chat (rewind, skip, restructure, update prompts) with engine-computed cascade previews
- Live chat widget mirroring the loop/SdlcProgressCard pattern (blocking and background modes)
- Batteries-included workflow templates (research, project planning, design, coding, deep internet research, produce-and-audit) built on a shared conventions pack
- Chat tools for planning, executing, inspecting, observing, auditing, and managing workflow runs

The current workflow feature (stateless SOPs with embedding-based auto-surfacing) is fully deleted. The `workflow` namespace in code, config, API routes, MCP tools, and FE surfaces is reused by the new feature with zero conceptual conflicts.

---

## Architecture Decision

**Chosen approach: Declarative Graph Spec with Pure Frontier Scheduler**

After evaluating three architecture angles (declarative graph, sandboxed script DSL, and hybrid graph+script) with independent adversarial judging across feasibility, UX/editability, and engine-correctness lenses, the declarative-graph approach won on:

1. **Editability** — the spec IS the render tree; structural edits are typed tree ops validated in batch; no script-reparse needed.
2. **Widget rendering** — the node tree maps directly to the live progress widget (each node = a row/card with status).
3. **Mid-flight safety** — a pure `frontier(spec, state)` function re-derives scheduling from scratch after any mutation, with a frozen-region invariant that prevents editing running/done nodes.
4. **Template authoring** — JSON graph specs are introspectable, composable, and LLM-friendly (chat can plan and restructure them via tools).

Key ideas grafted from the other proposals:
- **Epoch-stamped journal keys** from the script-DSL angle (proven in ultracode runs) for resume/rewind cache invalidation.
- **Binding expressions in prompts** (template variables resolved late) for output→input wiring without embedding a scripting language.
- **Compile-time macro patterns** (judge_panel, verify_panel, route) as pre-defined template sub-graphs that expand at definition time into the core node types.

The research pass (batches 1-5) independently converged on this same shape across every production system studied — the rev-2 additions concentrate where the original spec was thin: external side effects under replay, what actually re-runs after rewind/mutation, who owns completion, and a node outcome model richer than done|failed.

---

## 1. Data Model

### WorkflowDef (definition / template)

```python
@dataclass
class WorkflowDef:
    name: str                   # ^[a-z0-9][a-z0-9-]{0,62}$
    version: int                # bumped on every save
    spec_semver: str            # graph-spec format version; additive-minor rules,
                                # unknown-field-tolerant readers (WF2-R12 — bundled
                                # templates + flywheel-proposed diffs outlive engine versions)
    description: str
    source: str                 # "user" | "bundled"
    provenance: str             # authoring actor: "chat" | "user" (WF2-R12)
    inputs: dict[str, InputParam]  # {param_name: {type, required, default, help}}
    defaults: RunDefaults       # {model_tier, effort, max_concurrency, node_timeout_total_secs,
                                #  node_timeout_stall_secs, budget}
    metadata: DefMetadata       # {risk, capabilities[], requirements (binaries/credentials
                                #  aggregated from referenced action providers),
                                #  steering_examples: [{event, description}]}  (WF2-R12/R15)
    on_overlap: str             # trigger-origin overlap policy: "skip"|"queue"|"cancel_previous"
                                # (WF2-R1 batch-5; default "skip")
    root: Node                  # the graph tree
    tags: list[str]
    created_at: str
    updated_at: str
```

### WorkflowRun (execution instance)

```python
@dataclass
class WorkflowRun:
    id: str                     # 8-hex
    workflow_name: str
    status: RunStatus           # draft|running|paused|needs_input|complete|failed|cancelled|escalated
    spec_version: int           # version of the live spec (bumped per mutation)
    inputs: dict                # resolved input values
    intent: str                 # verbatim user prompt OR trigger identity + payload digest
                                # that started the run (WF2-R13)
    origin: RunOrigin           # {kind: chat|schedule|event|hook|idle|subagent-tool|manual|api,
                                #  session_key?, tool_call_id?, trigger_id?}
    parent_run_id: str | None   # if spawned by another run's subworkflow node
    root_run_id: str            # propagated through subworkflow spawns and forks;
                                # indexed with status for O(1) run-tree queries (WF2-R13)
    spawned_by_node_id: str | None   # the subworkflow node that spawned this run (WF2-R13)
    branch_key: str | None      # foreach/parallel child identity {branch_index} (WF2-R13)
    forked_from: dict | None    # {run_id, checkpoint_id} when this run is a fork branch
    project_id: str             # containing Project — REQUIRED, auto-resolved at creation
                                # via projects.resolve_project_id() (auto-create-on-blank,
                                # parity with project_run_create). See WORK-CONTAINERS plan.
    task_list_id: str           # auto-provisioned TaskList for materialized tasks (TASKS-SOPS)
    mode: str                   # "blocking" | "background"
    budget: RunBudget           # {max_tokens, max_cost, max_retries} — soft caps (WF2-R4;
                                #  breach = pause resumably + ledger event + needs-input item)
    pinned: bool                # retention exemption
    created_at: str
    started_at: str | None
    completed_at: str | None
    elapsed_seconds: float
    total_tokens: int
    agent_count: int
    error_message: str
    attention: dict | None      # pending gate/input/blocker info (typed ask payload, §5)
```

### Node Taxonomy (the construct algebra)

The spec is a **tree of containers** — containers render directly as the widget tree. DAG shapes inside `parallel` use optional `needs: [sibling_ids]`.

| Kind | Key Fields | Semantics |
|---|---|---|
| `sequence` | `children: Node[]` | Run children in order |
| `parallel` | `children: Node[]`, `join: all\|any\|quorum(n)`, `max_concurrency`, per-child `needs[]` | Fan-out; intra-block DAG via `needs`; joins gated by ACTIVE edges (§2) |
| `foreach` | `items: binding`, `body: Node`, `max_concurrency`, `pipeline: bool`, `cap?`, `on_item_error: halt\|skip\|collect` (default skip) | Dynamic fan-out over resolved array; `pipeline=true` = no barrier between stages; per-item journal checkpointing so interrupted fan-outs resume mid-collection (WF2-R5) |
| `loop` | `body: Node`, `mode: counted{n}\|until{condition}\|until_dry{streak}`, `circuit_breaker` (§2) | Iterate body; `until_dry` = clean-streak termination (proven in campaign DAG runner) |
| `stage` | `prompt: template`, `agent?`, `model_tier?`, `effort?`, `schema?`, `enforcement?`, `tools_posture`, `isolation?`, `cwd?`, `allowed_write_paths?`, `required_artifacts?`, `session: fresh\|continuous`, `max_turns?`, `timeout_total?`, `timeout_stall?`, `retry?`, `on_error?` | One subagent execution via `SubagentManager.spawn` |
| `infer` | `prompt: template`, `model_tier?`, `schema?` | **NEW (WF2-R16):** one bounded model call via `one_shot_completion` — NO tools, no SubagentManager spawn, no session. For classification, extraction, scoring, synthesis, judge legs. Output schema-validated by the engine; ledger accounts it as ONE model call |
| `branch` | `on: binding_expr`, `cases: {label: Node}`, `default?: Node` | **NEW (WF2-R17):** conditional dispatch — evaluate the binding, route to the matching case (or default); unmatched without default = typed BindingError. Taken path recorded in run state (feeds active-edge join gating, §2). Spec validation requires case coverage when the binding source declares an enum |
| `transform` | `expr: binding`, `output_contract?` | Zero-token pure data reshaping (filter/map/flatten/count) |
| `action` | `provider`, `config`, `output_contract?`, `allowed_write_paths?` | Zero-token action-provider dispatch (bash, notify, create-task); effect-ledgered when side-effecting (§2); may return a clarification request → needs_input (§5) |
| `wait` | `duration_secs \| until_ts` | Timer gate, evaluated on watchdog poll |
| `gate` | `kind: approval\|verify_command\|verify_script\|event\|expression`, `verify?: {script, until: success, max_iters}`, `timeout_secs?` (mode-dependent defaults, §5), `on_timeout`, `self_judge: false` | Blocks until satisfied; `approval` → run enters `needs_input` with a typed ask payload (§5); completion is engine-executed, never agent-declared (§2) |
| `subworkflow` | `ref: name@version`, `inputs: {bindings}` | Nesting; expanded in-run at ready time; depth ≤ 3; emits `child_run_attach` ledger event (WF2-R13) |

**Model tiers (WF2-R16 batch-5):** `model_tier: reasoning|standard|fast` is a first-class field on ALL LLM-backed nodes (`stage` and `infer`), resolved against a small declarative slot map in workflows config (`workflows.model_tiers = {reasoning: "...", standard: "...", fast: "..."}`). Templates reference tiers symbolically, staying portable across whatever models the user has bound. Resolution threads the resolved model as the **`model` build kwarg** (the registry's `register_entry` raises on duplicate names — never re-register; the model kwarg wins over `entry.model` in every factory, per provider_bridge.py:844-853). `infer` nodes route through `one_shot_completion`'s reasoning-axis mapping (llm_helpers.py — the "reasoning" chat sub-category resolves a plain ModelProvider, NOT the NativeAgentRuntime that chat/code_tools would trigger). This is deliberately the whole mechanism — no routing solver, no pricing store (the AIOS ILP ceiling we explicitly do not build).

### Node Outcome Model (WF2-R5)

Richer than done|failed — retrofitting these into journal keys and widget semantics later is far more painful than speccing them now:

- **`done` may be DEGRADED**: a node can succeed with a machine-readable `degraded_reason` (e.g. `diarization_skipped:no_token|401|network`) recorded in the ledger and flagged as degraded provenance on its output — templates keep working when optional capabilities are absent (degrade, don't die). Fallback-satisfied nodes carry the same flag.
- **Typed failure taxonomy**: failure events carry `{class: user|transient|network|permission|..., cause_plain, remediation, recoverable: bool, retryable: bool, terminal_reason, suggestion}` — the scheduler retries only transient/retryable classes; the widget renders `remediation` (actionable next step) distinct from the error. Machine-written `failure_signature` records use a 4-layer taxonomy `{failing_node, stage, layer: routing|execution|verification|governance, reason, input_hash}` for cheap localization and cross-run diffing.
- **Extended terminal states**: `no_change` (inherits prior results instead of re-running downstream evaluation), `scope_violation` (§2 Write Scope), `discarded`, `escalated` (circuit breaker, §2), `blocked{kind: protocol_violation}` (§2 Termination).
- **Judge-flavored outcomes** are three-state — `passed(score)`, `failed(score)`, `verifier_absent` — with aggregate pass-rates computed over the *requested* count (dropped work counts as failure).

### Binding Expressions (output → input wiring)

```
{{inputs.topic}}                              # workflow input
{{item}}                                      # foreach current item
{{iter}}                                      # loop iteration index
{{nodes.<id>.output}}                         # another node's structured output
{{nodes.<id>.output.findings | filter('verdict','CONFIRMED')}}
{{nodes.<id>.artifact}}                       # offloaded large-output artifact pointer (§2)
{{last.output.done}}                          # loop body's last iteration
{{secret:KEY}}                                # credential_ref — resolved server-side at dispatch (§ Secrets)
```

Grammar: dotted path + closed set of pure pipes (`filter`, `map`, `flatten`, `slice`, `count`, `default`, `json`) plus sanitization pipes (`xml_escape`, `truncate(n)`, `tojson`, `slugify`) (WF2-R9). Resolved late (at node ready-time) against the run's output store.

**Hardening (WF2-R9):**
- Whole-string refs (`{{nodes.x.output}}` alone) preserve source type (dict/list/int); refs embedded in larger strings stringify via `json.dumps` — two distinct, specified resolution paths.
- "Node produced null" flows through (`filter` drops nulls — declarative `.filter(Boolean)`); "reference doesn't resolve" (unknown node id / missing path) raises a **typed `BindingError`** journaled on the node — never a silent empty string.
- Spec-validation lint **rejects** bindings whose origin is untrusted content (trigger/webhook inputs, fetched web content) flowing unfiltered into prompt positions — the mechanical seam is `fence_untrusted` (security.py, re-exported via `sdk.security`), applied by lint requirement rather than template-author discipline. Critical once AUTOMATION-SUBSTRATE feeds trigger payloads into workflow inputs.
- Soft type-compatibility warnings at validation time. (The whole-value-only floor is the cautionary tale: without filters/interpolation, comparison logic leaks into node executors as regex hacks — keep both in scope.)

### Secrets Hygiene (WF2-R14)

- `action`/`stage` node configs reference credentials via `{{secret:KEY}}` / `credential_ref` bindings resolved server-side at dispatch against the credential store (`.env` via `save_credential`, 0600) — never inline values in workflow.json, run spec, journal, outputs, or ledger (which the flywheel later reads).
- GET of a def/run strips secret-bearing config into boolean `_has*` presence flags; save re-injects from stored state via a stable node-id map so mutation ops that move/copy a node keep credentials; fork copies credential state server-side.
- The journal/events writer is wrapped in a **RedactingSink** reusing `security.redact()` (credentials + exfil URLs — the same helper the loop store applies to all reads) to mask credential-shaped values before persistence — defense in depth for secrets arriving via node OUTPUT (a fetch response echoing a token), which the spec-side seam alone can't catch.
- Spec validation flags inline secret-shaped values.

### Agent Orchestration as Compositions (not node types)

High-frequency ultracode patterns expressed as node compositions:

- **Fan-out agents** = `parallel` of `stage` nodes
- **Judge panel** = `parallel[infer judges]` → `transform(avg score, rank)` — scoring legs compile to `infer` nodes (WF2-R16), a fraction of stage cost/latency/slot consumption
- **Adversarial verify** = `foreach(findings, pipeline=true)` of `infer(refute-prompt, schema:{verdict})` → `transform(filter CONFIRMED)`
- **Route** = `infer(classify intent into enum)` → `branch(on: output.category, cases: {...})` with per-branch model-tier selection (WF2-R17 — the Anthropic routing pattern as a one-liner macro)
- **Fan-out lenses** = same input, N parallel `stage`/`infer` nodes with different prompt lenses (the multi-type-content complement to route; needs no new node kind)
- **Pipeline per-item** = `foreach{pipeline:true, body: sequence[stage1, stage2, ...]}`
- **Loop-until-dry** = `loop{mode: until_dry{streak: 3}, body: sequence[find, dedup, verify]}`

Bundled **template macros** (e.g. `verify_panel`, `judge_panel`, `route`, `research_sweep`) expand at definition time into these core nodes, giving users one-liner patterns without engine complexity.

### Persistence Layout

```
~/.personalclaw/workflows/
  defs/<name>/workflow.json          # user-editable spec (fs-watched)
  bundled/<name>/workflow.json       # shipped templates (mtime-synced on boot)
  bundled/shared/                    # shared prompt-block library (§6, WF2-R15)
  runs.db                            # SQLite WAL: lean run rows (+ index on (root_run_id, status))
  runs/<8hex>/
    spec.json                        # live run spec (versioned, atomic_write)
    spec_history/v<NNN>.json         # per-mutation record: {ops[], actor, ts, hash}
    state.json                       # {instances: {path: {state, epoch, attempt, active_edges, ...}}}
    outputs/<path-hash>.json         # per-node structured output
    outputs/attic/v<NNN>/            # rewind archive (old outputs preserved)
    journal/attic/v<NNN>/            # rewind-superseded journal regions (WF2-R2)
    artifacts/                       # offloaded large node outputs (§2 Context Lifecycle)
    checkpoints/<NNN>.json           # per-super-step state snapshot (fork points)
    journal.jsonl                    # append-only resume cache
    events.jsonl                     # emitted event log (incl. Run Ledger subset + effect records, §5)
    CANCEL                           # persisted sticky cancel INTENT (§2 Termination; replaces bare STOP)
```

All JSON writes via `atomic_write` (the platform convention); run rows in SQLite WAL mode, same idiom as `loop/store.py`.

**Run retention (scaling guard):** run rows/dirs are pruned by a tiered policy so high-frequency origins (a per-minute trigger, implicit subagent-tool runs) can't flood the disk: per-def cap (default 100 runs, matching ScheduleRun's per-job cap), origin-tiered TTL (`subagent-tool`: 7 days unless pinned; `schedule`/`event`: per-trigger cap; `chat`/`manual`: user-pruned only), and the `pinned` flag exempting any run. Terminal runs compact `journal.jsonl` (cache no longer needed) keeping only `events.jsonl` + outputs.

**Crystallize-before-prune (WF2-R13):** before a run drops a retention tier, its journal is LLM-compressed (via `one_shot_completion`, "background" use-case) into a durable digest — narrative, key decisions, outcomes, artifacts, lessons — persisted as a ledger event. Pruned runs stay rememberable and feed the flywheel's run-outcome mining. (These digests are Run Ledger data — engine mechanics feeding the LEARNING-FLYWHEEL memory work; they are NOT knowledge-store items.)

**Deletion sweep contract (WF2-R13):** retention deletion enumerates every sibling artifact kind (spec_history, outputs, attic, checkpoints, journal, artifacts), schema-validates pointer sidecars before following them off-dir, is ENOENT-tolerant, refuses paths escaping the run dir, and purges optimistic FE caches on delete (`invalidateCache` — each of these is a documented failure mode).

**Backup coverage:** `snapshot.py` `VALID_COMPONENTS` and `portability.py` gain a `workflows` component so backup/restore carries defs + pinned runs (in-flight runs quiesced or excluded). Recon confirms neither covers `~/.personalclaw/workflows/` today (persistence gotcha #10 — no "everything" component exists), so this is additive work in both files, not a checkbox.

---

## 2. Execution Engine

### Architecture (three actors, mirroring the loop triad)

1. **`RunController`** (one per active run, in-memory): owns an `asyncio.Lock` serializing scheduler ticks and mutations; owns the run's asyncio task set.

2. **Pure frontier core — `workflows/tick.py`:**
   ```python
   def frontier(spec: WorkflowSpec, states: InstanceStates, now: float, limits: Limits
   ) -> FrontierDecision:
       """Zero-I/O, deterministic. Returns: ready[], waiting[], attention[], run_verdict."""
   ```
   Every input is persisted → restart re-derives identical decisions. Exhaustively unit-testable. (Prior art: `loop/tick.py`'s pure `evaluate(cfg, state, now) -> Decision` — noting that in the loop engine only the sdlc kind consumes it; here frontier() is THE scheduler for all runs.)

3. **`WorkflowWatchdog`** — one 5s poll (registered in server startup next to `LoopWatchdog`, which uses the same `POLL_INTERVAL_SECS=5` cadence): evaluates timers/gates/stall deadlines/circuit breakers, calls `controller.tick()`. Event-driven ticks fire on node completion and after mutations, so the poll is just the wait/gate/stall clock. (There is no timer heap in the platform to reuse — the watchdog poll IS the timer mechanism, same as loops.)

### Node Instance Lifecycle

```
pending → ready → running → done | failed | skipped | cancelled
                         ↘ waiting (wait/gate nodes)
                         ↘ blocked{protocol_violation} | escalated | scope_violation | no_change
```

Terminal states are **frozen** except via explicit rewind/run_from ops. The frozen-region invariant ensures no mutation can touch done/running nodes. `done` carries the optional degraded flag; see §1 Node Outcome Model for the full taxonomy.

### Scheduling & Concurrency

`tick()` under the lock computes the frontier, launches ready stages as `asyncio.create_task` wrappers around `SubagentManager.spawn(...)`. Inherits for free:
- Global concurrency caps + FIFO queueing (`_MAX_CONCURRENT=3` fixed or `resolve_max_subagents` auto-sizing [2,8]; over-cap spawns queue with staggered drain)
- Memory/cwd guards (`check_memory_available`, `validate_cwd` against `agent.subagent_cwd_allowed_roots`) + 30-min timeout + reaper
- Transcript persistence + orphan reconciliation
- Approval/safety gates per run trust level

**Typed executor lanes (WF2-R21):** the run/global concurrency budget is partitioned into 3 typed lanes derived automatically from node kind — `llm` (stage, infer), `io` (action nodes: bash, fetch, local-model warm-up, embedding/index work), and `compute` (transform, binding eval — effectively unmetered) — each with an independent cap in `Limits`, enforced by `frontier()`/`tick()` at launch time (a ready node only launches if its lane has a free slot; excess stays `ready`). No template-author surface. Rationale: a foreach over local-model-heavy action nodes (embedding re-index, whisper transcription — minutes-long, proven gateway-hostile in this codebase's own ST-reindex history) must not head-of-line-block the run's LLM stages. Config: `workflows.max_concurrent_nodes` becomes per-lane (`{llm: 4, io: 2}`) with a single number accepted as a back-compat total. Kept exactly this small — pluggable scheduler strategies and preemption are the enterprise ceiling not to build.

**Active-edge convergence gating (WF2-R18):** convergence (join) nodes with multiple predecessors wait only on predecessors with an **ACTIVE edge** — an edge along an actually-taken execution path. On conditional routing (`branch`, gate routing), only the taken path's outgoing edges become active; untaken branches cannot deadlock the join. Critical subtlety: async/waiting nodes (`wait`, `gate`, long-running `stage`) register their outgoing edges as active at **wait-entry, not completion** — otherwise a 3-way fan-out with 1 fast + 2 async branches fires the merge after only the first completion. Active edges are tracked as a set of `"{src_id}->{dst_id}"` strings in run state, derived from actually-executed routing decisions, so `frontier()` stays pure. Two regression tests are acceptance criteria (Slice 11): (1) conditional branch with untaken path never deadlocks a join; (2) async fan-out with waiting branches does not fire a join prematurely. Without this, any spec combining conditional routing with convergence has a fundamental scheduler bug in one direction or the other — `needs: [sibling_ids]` alone does not address conditional paths.

**SubagentManager depth enforcement (required — genuinely NEW code):** recon confirms the current "no spawn recursion" contract is **prompt-level only** — `_SYSTEM_PREFIX` says "Do NOT create other agents" (subagent.py) and subagent sessions simply don't receive the mcp-core toolset by default; there is NO code check blocking a `subagent:` parent from spawning. Workflow stages need controlled recursion: a stage's subagent must be able to invoke `subagent_run` (single), and a `subworkflow` node spawns nested stages. Implement an explicit **depth counter** (`__wf_depth`, max 3), mirroring the existing `__hook_depth` pattern (hooks.py:721 injects it; invoke_agent_provider.py:66 reads and bounds it): stage spawns carry depth+1; a spawn at max depth gets single-`subagent_run` only (no batch, no workflow_start). This is a Slice 1 work item adding enforcement that does not exist today.

**Stage spawns are `silent=True` and run-scoped:** completions route to the run journal (never injected into a chat session — `silent` on SubagentInfo suppresses the parent-injection announce path); the parent chat sees only run-level events via the widget.

**Trigger-origin dispatch lock (WF2-R1 + batch-5):** a per-def dispatch lock ensures a schedule burst never runs the same def twice concurrently. The lock is a **DB-backed lease with a max_duration expiry** (the ProcessLock pattern — a crashed gateway mid-run cannot permanently wedge a scheduled def; the lock self-expires and the next trigger reacquires; the platform's `single_flight` fcntl locks are process-local and insufficient here). What happens to the loser is the def's explicit `on_overlap` policy: `skip` (a per-minute monitor trigger) | `queue` | `cancel_previous` (a "regenerate the report" trigger).

### Engine-Owned Completion (WF2-R3)

The maker-praises-its-own-work failure is the most consistently documented finding in the research corpus (agent-declared completion is systematically overconfident; case study 37.5%→87.5% with pass-state gating). This plan bakes the platform's existing loop-judge-independence tenet ("no agent certifies its own work" — engine-seams gotcha #1) into the engine primitive so LOOPS-EVOLUTION doesn't re-add it per template:

- **Node/gate completion is engine-gated**: a stage subagent may only REQUEST a transition; the engine executes the declared verification and flips the state irreversibly.
- **Verification ladder**: gates support an ordered ladder (static → runtime → system/e2e) with a no-skip invariant and per-criterion hard thresholds — any hard failure fails the gate, no averaging; per-criterion scores journaled as Run Ledger events.
- **`required_artifacts: [glob]`** on `stage`/`action` nodes — `frontier()` refuses completion until matching files exist in the run workspace; per-node artifact digests (path/size/sha256) recorded in the ledger. (Same shape as the loop deliverable gate + `effective_dir` symmetry lesson: the verifier reads where the worker writes.)
- **`gate.verify: {script, until: success, max_iters}`** runs a bundled deterministic validator, passing only on typed zero-error output. `verify_command` gates reuse the `loop/gates.py:run_verify_command` idiom (tristate True/False/None, `audit_bash_command` screen, 180s timeout, exit-127→None). A zero-LLM structural gate immediately after every LLM generation node is blessed as a bundled-template idiom.
- **Fresh-judge invariant**: a gate/loop-exit judging LLM output runs in a session (optionally model) distinct from producing nodes — `self_judge: true` is explicit opt-in. Judge gates emit a **closed verdict enum** (`PASS|RETRY|ESCALATE|REJECT`) so `frontier()` routes on data, never prose. (Mirrors `loop/judge.py`: judge on the "reasoning" use-case provider, no write tools, permission requests rejected.)

### Structured Output (WF2-R8)

Stage/infer `schema` enforcement is a **capability-dispatched seam**, not a hardcoded strategy: `enforcement: auto|constrained|json_mode|parse_retry` on the node. `auto` dispatches on the resolved provider's declared structured-output capability — provider-native constrained decoding for local models (Ollama `format=json-schema` now; a logits path later), parse-with-retry for API/CLI providers. Rules:

- Schemas and bindings compile/validate at **spec-save time** ("schema not enforceable on bound model" = spec validation error); compiled constraint artifacts cached per (schema, model).
- Parse-retry re-presents the schema plus the JSON parse-error location; markdown fencing is auto-stripped before `json.loads` (measured to fix most format failures with ZERO retries). Retry is implemented as **session-resume-with-error-feedback**, not blind redo; the produce-then-extract two-node idiom is the documented escape hatch. Compile-time check that the node prompt actually contains the extraction tag; tag+schema extraction is last-match-wins (self-correction is benign).
- Judge/verdict schemas put a bounded free-text `reasoning` field BEFORE the constrained verdict field.
- **Output truncation recovery** (WF2-R4 am.): phase-1 silent same-request retry at escalated max_tokens; phase-2 bounded continuation with anti-apology prompt.
- `action`/`transform` nodes get an optional declarative **`output_contract`** `{must_be_json, required_keys, max/min_length, forbidden_phrases}` validated by the engine (~0.3ms mechanical check) before the node is marked complete and before any `{{nodes.x.output}}` binding resolves — malformed output never silently propagates through the graph. Per-edge contracts fail-branch on mismatch.
- Action providers follow the **structured-with-fallback** convention: declare typed schema + render-to-canonical-markdown with stable headers; engine attempts structured, retries free-text; downstream bindings parse deterministically either way (eliminates the "LLM parses LLM output" node type).
- Orchestrating nodes get node-level tool allow/deny policy so they structurally cannot perform worker actions.

### Side Effects: Effect Ledger + Idempotency (WF2-R1)

The journal cache memoizes *outputs* only; without effect identity, resume/rewind/fork double-fire external effects (Slack send, create-task, inbox write) — the biggest correctness hole in a journaled-replay design. Every `action`-node side-effecting dispatch records in `events.jsonl`:

```
{idempotency_key: sha256(run_id + instance_path + epoch),
 effect_status: attempted|committed|retried|compensated|skipped,
 compensation_ref?}
```

Rules:
- Rewind/fork across a **committed** effect surfaces it in the mutation cascade preview (§3) instead of silently re-running; re-execution of a node with a committed effect requires explicit `redo_effects: true`.
- `workflow_start`/`workflow_edit` accept a caller idempotency key with a short-lived dedupe cache — retried chat tool calls return the existing run id.
- External-resource action providers follow the BYOI contract: stdout = one JSON object, stderr = journaled logs, paired teardown command receiving the output id, teardown required idempotent — makes rewind/fork over a provisioning region safe.
- The effect record extends the existing `ActionResult.outcome` honesty contract (`"launched"` = fire-and-forget started≠succeeded) rather than replacing it.

### Failure / Retry / Budgets (WF2-R4)

Per-stage `retry: {max_attempts: 2, amend_prompt: true}`. **Attempts are first-class journal records** typed by the failure-mode enum (§1): retry N injects a per-mode correction hint plus a pruned digest of attempts 1..N-1 into the fresh session (mutation hints resolved 7/10 failures on attempt 2 vs blind retries reproducing the same failure). Structured per-issue retry payloads `{expected, actual, evidence, fix_instruction, files, severity}` are journaled per attempt so retry loops get actionable feedback, not free prose. `no_retry_modes` (e.g. `prompt_injection`) never retry. Retry policy is mode-differentiated: retry capacity errors in blocking mode; fail-fast in background. The scheduler consults `retryable: bool` on the failure envelope, not blanket counts.

Exhausted retries → instance `failed`, output `null`. Node `on_error` policy:
- `null_continue` (default): parent sees null, keeps going
- `fail_branch`: parent parallel/foreach marks this path failed
- `fail_run`: run transitions to `failed`
- `pause_run`: run transitions to `needs_input`

When a loop node exhausts retries, the engine produces a first-class **escalation artifact** — five typed options (reassign / decompose / revise / accept-with-limitations / defer) surfaced as a needs-input gate.

**Deterministic circuit breaker (LLM-free, in `frontier()`):** pre-iteration trip conditions on loop nodes — max_iterations, same `error_signature` N consecutive (default 3), byte-identical output across M iterations, cumulative tokens — emitting the typed **`escalated`** terminal state (distinct from `failed`). Iteration ledger events carry `{iteration, outcome, error_signature, tokens}`. This catches the field's #1 autonomous-run failure mode (infinite fix loop / thrash / stall) at zero LLM cost.

**Budgets (resolves Open Question 4 — SOFT):** breach = pause resumably + ledger event + needs-input item. `budget: {max_tokens, max_cost, max_retries}` on WorkflowRun with template defaults; node-level budgets with extend-budget gates (warn ~80%, pause at cap into needs_input). Templates may declare per-item LLM-call budgets that the engine asserts and ledgers (flagging over-budget runs). Plan review shows a static estimated-LLM-calls figure derived from topology. Two resume invariants (acceptance criteria):
1. Budgets are **PRE-CHARGED from the journal on resume** — resume must not mint a fresh budget.
2. On resume/rewind the engine runs a template-declared cheap **`baseline_check`** before scheduling the frontier, injecting a repair node on failure — pre-existing breakage never gets obscured by new work.

### Timeouts (WF2-R5)

Two knobs, not one: `timeout_total` (max wall clock) + `timeout_stall` (max window with NO progress events — a long operation is fine, silence is not). Local-model-backed action providers get separate warm-up vs execute budgets (minutes-long cold starts look identical to hangs under one number). Batch-5 cautionary tale: a studied engine shipped a NO-OP node timeout that never fired, unnoticed because timeouts are only exercised under failure — so Slice 11 carries an explicit **"timeout actually fires" regression pair**: (1) a stalled stage must transition `failed(timeout_stall)` within the window; (2) a long-but-progressing node must NOT be killed by `timeout_stall`. Both knobs proven live, not decorative.

### Context Lifecycle for Long Nodes (WF2-R6)

The deep-research and audit-sweep templates are exactly the long-horizon shapes where compaction demonstrably fails (Anthropic: "compaction alone wasn't sufficient"; handoffs cut rebuild cost ~78%, hidden defects 43%→8%):

- **`session: fresh|continuous`** per-iteration policy on loop/foreach bodies (default fresh for long horizons) — each iteration writes a structured **handoff artifact** `{verified state, changes, broken/unverified, next action}` into the journal; the next fresh session reads it. Reset-vs-compact is a per-model `runtime_hint`. Rewind/fork replay the handoff correctly because it's journaled.
- **Typed carryover buckets** per long-running node (bounded, deduped: files touched with line spans, verified work, spawned children) survive executor compaction/reset — cheaper and more reliable than prose handoffs alone.
- **`decision` journal records** `{choice, reason, rejected_alternatives, constraints}` written by stage nodes — compaction keeps the "what" and drops the "why"; without these, resume/fork/rewind re-litigate settled decisions.
- **Output offloading**: node outputs over a size threshold keep head/tail in the journal and write the body to `runs/<id>/artifacts/`, reachable via `{{nodes.x.artifact}}` (measured: 4,000 lines of PASSING test output flooding context causes hallucination). Media/large outputs bind as artifact pointers (`[Artifact #id: hint]` text in bindings) with an `artifact_inspect` action (cheap-model fallback chain) to pull content on demand. Contract: "success is silent; failures are verbose."
- **Two-layer context ladder** for LLM-backed nodes: proactive compaction at ~80% of the bound model's window via a cheap summarizer, then error-triggered aggressive re-compaction before failing the node; degrade-to-drop-with-placeholder if the summarizer itself fails.

### Filesystem Write Scope (WF2-R19)

The frozen-region invariant protects spec state and the effect ledger protects external API effects — nothing else prevents a stage subagent writing or deleting files outside its intended scope (this platform already hit the failure class: the destructive-test-isolation incident deleted the user's real bound model). Stage/action nodes executing commands or spawning subagents declare `allowed_write_paths: [glob]` (default: the run workspace dir). The engine snapshots the filesystem tree (paths + mtimes + sizes) before node execution and diffs after completion. Out-of-scope writes (normalized path matching, `..` rejection, symlink resolution) flag the node **`scope_violation`** in the Run Ledger with violating paths listed, and optionally block completion (configurable: warn vs reject). Three enforcement layers documented: advisory (prompt instructs scope — all that exists today via `_SYSTEM_PREFIX` + `validate_cwd`), authoritative (engine diff detects), OS seatbelt (a future sandbox provider receives `allowed_write_paths` as policy).

### Termination Robustness (WF2-R10)

- The `CANCEL` sentinel is a **persisted sticky cancel INTENT** surviving gateway restarts mid-cancel — running children cancelled, new scheduling refused, run finalizes `cancelled` only when the frontier settles; startup orphan-reap honors pending cancel intents.
- **Terminal-write ownership**: only the RunController's tick loop writes a run's terminal status; pause/cancel HTTP/tool endpoints write *signals* the controller consumes (prevents a stop handler overwriting a failure — a documented race).
- **Completion-protocol invariant**: a stage subagent terminating without an explicit structured completion auto-transitions the node to typed `blocked{kind: protocol_violation}` feeding the needs-input surface — never a silent hang.
- **`workflow_audit`** maintenance operation with diagnose/heal semantics: scan for stale_running runs, dead gates, expired waits, lost runs (backing subagent vanished past grace), orphaned post-rewind journal keys — auto-repair with `dryRun`.

### Resume / Crash Recovery

On startup: reap orphaned runs (controller rebuild from `spec.json` + `state.json`), mark orphaned `running` instances as failed-attempt, honor sticky cancel intents, pre-charge budgets from the journal, run `baseline_check` where declared, and tick. **Journal cache**: every stage launch appends `{started, key}`; every result appends `{result, key, output_ref}` where `key = sha256(instance_path + node_spec_hash + resolved_inputs_hash + epoch)`. On resume/rewind, key hits serve cached outputs instantly. Journal epochs are additionally stamped with a hash of shape-affecting spec regions so resume/fork under a mutated spec forces re-frontier instead of silent state reuse (WF2-R2 am.). Journal cuts at epoch boundaries walk back until every retained tool_result has its tool_use — naive cuts produce API-rejected conversations on resume (WF2-R2 am.).

---

## 3. Mid-Flight Mutation Model

### Typed Ops (batch-applied, transactional)

| Op | Semantics |
|---|---|
| `update_node{id, fields}` | Patch node spec (prompt, schema, model_tier, etc.) |
| `insert{parent_id, index, node}` | Add a new node to a container |
| `delete{id}` | Remove a pending/skipped node from the spec |
| `move{id, new_parent, index}` | Relocate a pending node |
| `set_input{overrides}` | Override workflow input bindings |
| `skip{id}` | Mark a pending node skipped (output = null, children skipped) |
| `rewind{to_id, redo_effects?}` | Reset a done/failed node + its binding-dependency closure to pending; archive outputs + journal region; bump epoch |
| `run_from{node_id}` | **NEW (WF2-R2):** re-execute only the subgraph downstream of a node with upstream outputs pre-satisfied from the journal (prune to dependents, seed in-degrees) — the third re-entry primitive besides resume and rewind ("redo synthesis with the same gathered data") |
| `fork{checkpoint_id?, note?}` | Branch a NEW run from a checkpoint (or current state); original run + journal stay immutable. `forked_from` recorded on the child |
| `inline_subworkflow{id}` | Copy referenced def body into run spec for local editing |

**Rewind vs run_from vs fork:** three complementary re-entry semantics. `rewind` is IN-PLACE — quick fixes on a run you're steering ("redo the synthesis stage with a better prompt"). `run_from` re-runs downstream-only with upstream cache pre-satisfied. `fork` is BRANCHING — exploratory divergence where the original result must be preserved. Fork copies the spec + state at the checkpoint into a new run dir; the journal cache is shared read-only up to the fork point (cache keys match), so a fork is cheap. Fork must explicitly state which axes it isolates and force per-fork disambiguation (distinct branch/dir, randomness in unique-name generators) for non-isolated axes (WF2-R2 am.).

### Re-Entry Semantics: Binding-Dependency Cascade (WF2-R2)

The rev-1 spec reset "X + all descendants" — **tree** descendants — but data flows through `{{nodes.<id>.output}}` bindings: a later *sibling* binding X's output is NOT a tree descendant and would keep a stale input, a guaranteed silently-inconsistent-run bug class. Corrected semantics:

1. `rewind` (and `update_node`/`set_input`) compute the closure over the **binding-dependency graph**, not the container tree.
2. Every mutation batch returns an engine-computed **cascade preview** ("what re-runs / what stays", cost-tiered) — cascades that re-run completed stages require explicit confirmation; committed external effects in the cascade are surfaced (§2 Effect Ledger).
3. Done nodes with changed inputs *outside* the confirmed re-run set get a journaled `inputs_stale` flag.
4. **Inputs-hash memoization**: journal keys carry an inputs-hash tier — a cascaded node whose `(node_spec_hash, resolved_inputs_hash)` is unchanged replays its cached output instead of re-running; epoch bump is reserved for explicit force (`--force` semantics). A rewind that doesn't change a node's inputs shouldn't re-run it.
5. Rewind archives the superseded journal region alongside outputs (`journal/attic/v<NNN>/`).
6. Rewind splits into **rollback** (hard reset with preserved forward refs) vs **revert** (inverse-patch one node's effects; 409-with-conflict-named on overlapping later state), both behind the mandatory preview.

### Safety Protocol

1. **Single-writer conductor:** all mutations enter a queue; the controller drains one batch at a time between scheduling steps (never mid-node-launch).
2. **Frozen-region invariant:** ops targeting done/running nodes reject (except `rewind`/`run_from` which explicitly unfreeze). Pending/skipped/waiting nodes are mutable.
3. **TOCTOU re-verify (WF2-R2 am.):** content revision is re-verified AFTER user approval and immediately BEFORE applying any mutation op — the frozen-region check alone has a time-of-check gap while nodes complete under the preview.
4. **Validation:** after ops apply, the full spec is re-validated (acyclic, bindings resolvable, type checks, branch case coverage). Invalid batch → rollback, error to chat.
5. **Epoch bump on forced rewind:** rewound nodes get a new epoch; journal cache keys include the epoch, so old results are invalidated and re-execution is forced. Old outputs move to `outputs/attic/v<NNN>/`.
6. **Spec history:** every accepted batch appends `spec_history/v<NNN>.json` with `{ops, actor (chat|user|cron), ts, resulting_spec_hash}` — full audit trail.
7. **Optimistic concurrency for chat tools:** `expect_version` param on mutation tools; version mismatch → retry with fresh state.

### Mutation Grammar Hardening (WF2-R20)

The ops above are designed for engine correctness; these rules address the specific ways LLMs fail at *producing* them (broad coordinate guesses, two mutations per block, stale-state edits):

- (a) Ops use unique-anchor identification (`node_id`) XOR positional identification (`parent_id + index`) — never mixed in one batch.
- (b) No overlapping edits: two ops targeting the same node in one batch = batch rejection with diagnostic.
- (c) Ops apply in coordinate-preserving order (insertions/deletes in reverse-index order so earlier indices stay valid).
- (d) Common LLM aliases in node-kind and field names are tolerated via a canonical-form normalization layer.
- (e) **Atomic failure contract**: a rejected batch writes NOTHING — prior spec remains source of truth, no partial application.
- (f) Chat tools enforce **staged turns**: inspect/status ops end the tool turn, and a rendered+source echo of the current spec lands in the model's transient context for the next mutation turn — the model mutates what it just SAW, not what it remembers.

---

## 4. Chat Tool Surface

### Tool List

| Tool Name | Description | Key Params |
|---|---|---|
| `workflow_author` | Author/save a workflow definition directly (DAG spec) — the low-level authoring tool | `name, description, inputs, nodes (tree spec), save: bool` |
| `workflow_plan` | Plan-first entry: generate a spec from a natural-language goal (template-aware; see UNIVERSAL-PLANNING) — returns a plan artifact + approval gate before anything runs | `goal, rigor?: minimal\|standard\|deep, template?` |
| `workflow_list_defs` | List available workflow definitions (user + bundled templates) | `tag?, source?` |
| `workflow_get_def` | Retrieve a workflow definition in full | `name` |
| `workflow_start` | Start a workflow run (instantiate a def with inputs) | `name, inputs, mode: blocking\|background, idempotency_key?` |
| `workflow_status` | Get current run status + node-level progress | `run_id` |
| `workflow_observe` | **NEW (WF2-R11):** subscribe-briefly — timestamped event deliveries from a bounded window (clamped duration); cheaper and safer than status-polling loops in chat | `run_id, duration_ms` |
| `workflow_edit` | Mid-flight edit: apply mutation ops to unexecuted stages; returns the cascade preview when confirmation is needed | `run_id, ops[], expect_version, idempotency_key?, confirm_cascade?` |
| `workflow_skip` | Skip one or more pending nodes | `run_id, node_ids[]` |
| `workflow_rewind` | Rewind to a node in place (re-execute it and its binding dependents) | `run_id, node_id, redo_effects?` |
| `workflow_run_from` | Re-execute only the subgraph downstream of a node (upstream cache pre-satisfied) | `run_id, node_id` |
| `workflow_fork` | Branch a new run from a checkpoint; original immutable | `run_id, checkpoint_id?, note?` |
| `workflow_pause` | Pause a running workflow (in-flight nodes finish, no new launches) | `run_id` |
| `workflow_resume` | Resume a paused/needs_input workflow; `answer` supports `approve / reject / revise{step_ref, comment} / input{...}`; answers consumed atomically (§5) | `run_id, gate_id?, answer?` |
| `workflow_cancel` | Cancel a run (sticky intent; in-flight agents killed) | `run_id` |
| `workflow_output` | Retrieve a specific node's structured output (or artifact pointer) | `run_id, node_id` |
| `workflow_audit` | Diagnose/heal maintenance sweep: stale runs, dead gates, expired waits, lost runs, orphaned journal keys | `dry_run: bool` |
| `workflow_manifest` | Emit node taxonomy / pipes / mutation-op catalog GENERATED from the real registries (single source of truth, CI drift-tested) | — |
| `workflow_delete_def` | Delete a workflow definition | `name` |

19 tools. Naming note: `workflow_plan` (NL goal → reviewed plan) and `workflow_author` (spec in → def saved) are deliberately separate tools — the earlier draft overloaded `workflow_plan` with both contracts, which the design review flagged as a semantic clash.

### Spec Ingestion Validation (WF2-R12)

LLM-generated specs are a first-class path (`workflow_plan`, `workflow_author`, `workflow_edit`); ingestion is engineered for it:

- **Never-throw parsing**: a structural pass that cannot throw, plus a schema mapper accumulating typed issues `{path, code, message}` on warning/error channels. Stable append-only error codes (`ERR_UNKNOWN_NODE`, `ERR_MISSING_BINDING`, `ERR_FROZEN_REGION`, …) formatted for LLM re-prompting with did-you-mean suggestions.
- **`strict` mode** (unknown node kinds, unbound bindings = hard errors) — strict compile is what makes agent-generated specs converge within ~3 repair attempts instead of silently mis-executing.
- **Dry-run before save**: agent-side def-save requires a sandbox dry run first (bindings resolve, actions validate, zero external effects); defs record `provenance` actor (chat|user) — the personal-scale reframe of the propose/create split, keeping run-start approval as already planned.
- Validation returns the Kahn-derived level-grouped execution order (plan review + widget get parallelism lanes for free; cycles are create-time errors).
- **Run-start preflight** blocks on missing credentials/binaries (from `metadata.requirements`) instead of degrading at node 7. Model preflight uses the backend `can_resolve_use_case` probe (provider_bridge.py:672 — the cheap no-instantiate check behind onboarding `needs_model`); note `capableModels` is a *frontend* function (web ModelsPanel.tsx) and cannot serve backend preflight.
- **`workflow_manifest`** is generated from the real node/pipe/op registries with a CI drift test (the manifest-vs-UI bug class this codebase keeps refinding); the graph-spec `spec_semver` follows additive-minor rules with unknown-field-tolerant readers; bundled templates get structural contract tests.

### Prompt Injection (LLM guidance)

The model receives a `[ACTIVE WORKFLOWS]` context block (replacing the old `[PREFERRED WORKFLOW …]` injection) listing any running workflows in the current session with their status + the next action needed. This gives the LLM awareness without requiring explicit status polls. Injection lives in `context.py:build_message` and must keep the never-break-a-turn contract the old surfacing honored (swallow-all → None; the old path ran via thread-pool loop-bridges inside sync build_message).

---

## 5. Events + Live Widget

### Event Delivery (recon-corrected)

Engine events are delivered on **two channels, matching the loop pattern** — NOT via `state.notify(...)`, which is the user-notification gate behind `notification_allowed` (mute-all / severity / quiet-hours) and would silently eat engine events:

1. **Per-run SSE**: a `SseRegistry` keyed `workflow:<run_id>` registered on `DashboardState` (exactly like `loop_sse()` at dashboard/state.py:1056, served by the loop's `GET /api/loops/{id}/stream` idiom) → `GET /api/workflows/runs/{id}/stream`. This is the widget/detail-page live channel.
2. **Multiplexed WS** `_broadcast` for coarse dashboard refetch signals (`workflow_run_update` as a refetch SIGNAL, per the DashboardLive convention that WS envelopes are signals, not payloads).

`state.notify(...)` is used ONLY for genuinely user-facing moments (needs_input surfacing, run failed) where the notification gate SHOULD apply.

**FE registration requirement (recon fact):** EventSource silently DROPS unregistered event types. Every workflow SSE event type below MUST be added to the registered lifecycle union in `web/src/pages/loops/useRunStream.ts` (`RUN_LIFECYCLE`, currently the complete 22-event loop union) — or, if the widget ships its own `useWorkflowStream` hook, to an equivalent exhaustive `WORKFLOW_LIFECYCLE` union with the same must-register rule and a test asserting backend-emitted types ⊆ the FE union. Omission = silent event loss, no error.

| Event | Payload | When |
|---|---|---|
| `workflow_run_update` | `{run_id, status, spec_version}` | On any status change (WS refetch signal + SSE) |
| `workflow_node_started` | `{run_id, node_id, instance_path, epoch}` | Node begins execution |
| `workflow_node_progress` | `{run_id, node_id, item_index?, item_total?, item_label?, stage?}` | Per-item foreach progress ("[3/12] label", WF2-R5) + stall-clock feed |
| `workflow_node_done` | `{run_id, node_id, status, degraded_reason?, output_preview}` | Node reaches terminal |
| `workflow_attention` | `{run_id, node_id, kind, ask (typed payload), resume_token}` | Gate/blocker/needs_input |
| `workflow_mutation` | `{run_id, ops[], new_version}` | Spec edited mid-flight |

### Event Pipeline Correctness (WF2-R11)

The live widget is the plan's primary UX surface, and rewind/fork make it uniquely vulnerable to stale/duplicate/out-of-order updates — this codebase already fought exactly this bug class in the chat stream (the K42/K44/K45 coalescer bugs):

- **Explicit dedup key**: `run_id|node_id|epoch|seq|state`; event ids are deterministic at emit time (`{run_id}-evt-{offset}`) so re-emits are idempotent no-ops. The snapshot-vs-delta streaming contract is specified (full text replaces, delta appends, optional replace flag).
- **Event-fold law** (formal invariant): a pure fold of run events EXACTLY reconstructs run state — the FE fold gets the same treatment as `runFold.ts` (pure, unit-locked). Interrupt semantics journal synthesized `interrupted` results for all pending calls on rewind/cancel; a run-admission registry distinguishes droppable wake events from must-requeue gate-answer resumes.
- **Snapshot-then-subscribe**: on mount, the widget fetches the server-canonical snapshot BEFORE subscribing to deltas; sequence-numbered replay on reconnect from a per-run buffer is an acceptance criterion; every async emission is epoch-tagged and dropped if superseded (protects rewind/fork). Events are node-id-keyed patches, not full `nodes_summary[]` rebroadcasts.
- **Coalesced delivery (batch-5)**: per-observer dirty-version tracking with debounced push scheduling (~25ms) and burst coalescing sits between the event fold and the SSE write — a 20-node parallel fan-out completing in one tick produces ONE coalesced widget update per connection, not 20 broadcasts (directly addresses the §9 large-spec widget risk). The snapshot projection is schema-validated before transmission so a malformed projection can never corrupt the widget.
- **Oversized outputs**: a `result_omitted` spill-to-artifact-path pattern at the journal boundary (magic-prefix binary detection, byte counts in the stub) — the same ~64KB sanitizer boundary that protects live chat applies to journaled node outputs.
- **Replay regression harness** (Slice 11): recorded JSONL traces for required scenarios (happy-path, restart-during-run, rewind-during-stream, network-degraded); compute duplicate_event_rate / event_fanout_ratio / order-violations / p95 offline; CI-gated against a checked-in baseline with hard + relative-drift thresholds.
- **Streamed previews** are keyed by `(run_id, epoch, node_id)`; on run termination only that run+epoch's pending updates flush (prevents the one-global-slot overwrite bug class) (WF2-R13 am.).

### Run Ledger (acceptance criteria — required by the Learning Flywheel plan)

The `events.jsonl` journal MUST record these learning-relevant events in structured form (not just replay/debug data). The downstream template-refiner (LEARNING-FLYWHEEL plan §run-outcomes) is starved without them:

| Ledger Event | Required Fields |
|---|---|
| `step_completed` | `node_id, instance_path, duration_secs, tokens, retries, model, provider, cost_usd (backend-authoritative, rate-table floor), degraded_reason?, resolved_prompt_ref` |
| `step_failed` | `node_id, error, failure_signature {failing_node, stage, layer, reason, input_hash}, attempt, retries_exhausted: bool` |
| `step_skipped` | `node_id, actor (user\|chat\|engine)` |
| `gate_rejected` | `node_id, user_comment (verbatim)` |
| `gate_criterion` | `node_id, criterion, score, hard_fail: bool` (WF2-R3 ladder scores) |
| `effect` | `idempotency_key, effect_status, compensation_ref?` (WF2-R1) |
| `iteration` | `node_id, iteration, outcome, error_signature, tokens` (WF2-R4 breaker feed) |
| `user_edited_mid_flight` | `ops[] (the structured mutation batch, not a diff blob)` |
| `consulted` | `node_id, ref (skill/template actually loaded, distinct from injected-into-prompt)` — evolution attribution (WF2-R13) |
| `child_run_attach` | `parent_run_id, child_run_id, node_id` (WF2-R13) |
| `run_abandoned` | `at_node_id, elapsed_secs` |
| `crystallized` | `digest {narrative, decisions, outcomes, artifacts, lessons}` (WF2-R13, pre-prune) |

These are engine-emission requirements, not a separate store — the refiner reads `events.jsonl` filtered to this subset. Per-stage instances journal the **fully-resolved post-binding prompt** (or a content ref) for trajectory replay and forensics. **Acceptance criterion — reasoning-path reconstructability**: from ledger events alone one must be able to replay prompt → tool invocations → final output per node; one record shape serves the cockpit, overnight-runs inbox, flywheel evaluator, and failure forensics (WF2-R13 am.). Pass-rate / failure-distribution / P50-P99 become ledger queries.

### Human-Input Contract (WF2-R7)

Turns `needs_input` from a chat-only prompt into a durable cross-surface primitive — the linchpin for WORK-CONTAINERS' needs-input inbox and AUTOMATION-SUBSTRATE's unattended runs:

- **Typed ask payload**: `{kind: approval|choice|text|form, fields (typed, with defaults), prompt, timeout, unattended_suppress}` — one FE renderer covers every human-input node; the payload projects into the needs-input inbox as a real form.
- **Mode-dependent timeouts**: background-mode gates time out short (config ~30-60s) emitting a distinct `timed_out_unattended` event + needs-input surfacing; blocking/chat mode waits long. Otherwise background runs wedge on approvals.
- **Durable resume tokens + continuation records (batch-5)**: each needs_input transition persists a journaled **continuation record** `{node_id, instance_path, resolved_inputs, epoch, expires_at}` actionable from widget/inbox/API hours later — resume re-enters that exact step with its resolved-so-far inputs rather than re-executing the enclosing subgraph; completed progress preserved. Expiry produces a typed `resume_expired` needs-input item (offer re-run from node), never a silent dead token.
- **Atomic answers**: answers are consumed atomically (one-shot row deleted in the resume transaction) so double-resume can't replay them.
- **Handoff bundle**: pause/needs_input/checkpoint persist `{scope+status, outstanding items, checks run, next steps, risks}` — rendered by the widget when blocked.
- **Any action node may return a clarification request** → run pauses into needs_input and surfaces to the inbox, without template authors pre-placing gate nodes.
- **Remote-channel gates**: owner binding (only the run's requester can approve from a shared channel; non-owner replies ignored) + configurable timeout with default-DENY. Out-of-band reply paths (chat tool AND HTTP) so approvals work without the widget open.
- **"Always allow"** remember option scoped to (operation type + target), run-scoped, cleared on rewind, feeding run policy. **Auto-approve mode** for trigger-fired/scheduled runs where only destructive/high-risk gates still block (this resolves the unattended half of Open Question 2).
- **Transient-hold event gates**: `gate{kind: event}` does NOT consume the trigger event when a prerequisite is absent — bounded retry counter, loud give-up — distinguishing prerequisite-absent from input-invalid (a gate that eats its wake-up event then fails loses the event).

### Chat Widget — `WorkflowProgressCard.tsx`

Mirrors `SdlcProgressCard.tsx` pattern (which is REST-polling, not stream-driven — recon):
- **Detection:** tool segment detector recognizes `workflow_start` tool output containing a `/#/workflows/<run_id>` deep link — same regex-over-tool-output contract as `sdlcRefFromTool` (SdlcProgressCard.tsx:35); wired in ChatPage's renderItem the same way (done ToolSegment → card instead of ToolCard; kept out of the collapsed work fold).
- **Rendering:** the node tree IS the widget tree — each node is a row with status icon (pending/running/done/degraded/failed/skipped/escalated), label, elapsed time, remediation line on failure (distinct from the error — WF2-R5). Containers are collapsible groups; foreach rows render per-item progress ("[3/12] label"). Active nodes pulse; the frontier is highlighted. `branch` nodes render only the taken path expanded.
- **Live data:** REST snapshot poll (4s while LIVE statuses, 10s otherwise, stop on terminal — cadence read from the JUST-FETCHED status, the stale-closure fix) + the per-run SSE for deltas on the detail page. Snapshot-then-subscribe on mount (§ Event Pipeline).
- **Controls:** pause/resume/cancel inline (controllable mode); two-step armed delete for terminal runs; "Open in Workflows" deep link.
- **Attention banner:** when `needs_input`, renders the typed ask payload as a real inline form (approval/choice/text/form kinds) — same attention pattern as SdlcProgressCard, upgraded from string question to typed payload.

### Blocking vs Background Mode

| Mode | Behavior | Widget |
|---|---|---|
| **Background** | Chat continues; widget lives in the message stream as a persistent card (like loop cards today) | Polls and renders live; user can interact with other tools |
| **Blocking** | The tool call doesn't return until the run completes (or needs_input/fails) | Widget renders live during the tool's execution; chat is blocked until complete |

Blocking mode is implemented by `await run_controller.wait_for_terminal()` inside the tool handler (with periodic progress events emitted so the FE widget updates). Background mode creates the run, returns the deep-link immediately, and the widget polls independently. Retry policy is mode-differentiated (§2).

---

## 6. Batteries-Included Templates

Shipped as `bundled/<name>/workflow.json` (mtime-synced on boot, same as the old bundled SOP pattern):

| Template | Structure |
|---|---|
| `deep-research` | `infer(triage tier)` → `branch` → `loop{until_dry}` over: `parallel[multi-modal-search, web-fetch]` → `transform(dedup)` → `foreach(sources, pipeline){read, extract, verify}` → `transform(synthesize)` |
| `project-planning` | `sequence[understand-scope, parallel[research-domain, audit-codebase], design-approach, plan-milestones, review-plan]` |
| `code-implementation` | `sequence[action(baseline-capture), plan, foreach(files, pipeline){implement, test, lint}, gate(verify_command), integration-test, stage(commit-review)]` |
| `design-review` | `parallel[infer(feasibility-judge), infer(ux-judge), infer(correctness-judge)]` → `transform(synthesize scores)` → `stage(final-recommendation)` |
| `audit-sweep` | `parallel[finders...]` → `transform(dedup)` → `foreach(findings, pipeline){verify, fix}` → `stage(completeness-critic)` → `loop{until_dry}` |
| `produce-and-audit` | **NEW (WF2-R15):** domain-neutral — research → produce artifact → read-only QC gate with severity-tiered Finding records |

Users can instantiate a template, edit it (including mid-flight), or create their own from scratch via chat or UI.

### Conventions Pack (WF2-R15 — template work, not engine work)

Cross-template conventions that keep a 6+ template library effective and maintainable:

- **Triage-first**: a blessed opening pattern — an `infer` classification node whose output (`{{nodes.triage.output.tier}}`) drives a `branch` selecting among 2-3 entry subgraphs (avoids over-processing small tasks), plus an escalate-and-reclassify convention (typed mutation splicing skipped stages back in). Converges with the classifier-then-dispatch shape (WF2-R17 batch-5) recommended for ingestion-flavored templates.
- **Canonical Finding record**: `{severity: Critical|Major|Minor|Nit, location, problem, why, recommended_fix, status: Open|Fixed|Needs-decision}` as the output contract of ALL review/judge stages — gate predicates like "no open Critical|Major" become uniform, the widget renders findings identically everywhere, and the Run Ledger is minable by the flywheel.
- **Baseline capture**: code-flavored templates run an `action(run_validation)` node before the first mutating node; its journaled output later gates diffs to classify regression vs pre-existing vs implementation-detail (pairs with the engine `baseline_check` resume invariant, §2).
- **Shared prompt-block library** (`bundled/shared/`): preflight, graduated safety tiers, conventions — referenced from template specs instead of duplicated text; rule "repeated boilerplate moves to shared" + a deterministic template-lint so refs can't drift.
- **Steering examples**: template metadata `steering_examples: [{event, description}]` (kickoff + mid-flight mutation examples) surfaced in the widget and fed to `workflow_plan` as few-shot.
- **`artifact_update` action provider**: dashboard-style templates generate an HTML skeleton once and refresh it via pure transforms re-binding `{{nodes.x.output}}` slots. This is a NEW action provider → registered in the action_providers registry AND added to `ALLOWED_HOOK_PROVIDERS` (validation.py:555) — see §7.5.

---

## 7. Clean-Break / Namespace-Reuse Plan

### Phase 0: Relocate shared code trapped in the old feature

1. Move `prompt_render` tool from `mcp_workflows.py` → `mcp_prompts.py` (new file, same pattern).
2. Move `resolve_agent_id` from `workflows/composition.py:195` → `agents/identity.py` (imported by `chat_runner.py`).
3. Verify no other consumer of `_CURRENT_AGENT_ID` contextvar (mcp_core.py:84) outside workflow-create; if clean, keep it in `mcp_core.py` for the new feature.

### Phase 1: Delete the old feature entirely

Backend (delete whole files):
- `PersonalClaw/src/personalclaw/workflows/` (entire package: `__init__.py`, `models.py`, `provider.py`, `native.py`, `registry.py`, `composition.py`, `surfacing.py`, `handlers.py`, `lifecycle.py`, `bundled/` starter SOPs) — **except** the provider-registry seam is REPLACED, not orphaned (see below)
- `PersonalClaw/src/personalclaw/mcp_workflows.py` (after prompt_render relocated)
- `PersonalClaw/src/personalclaw/action_providers/run_workflow_provider.py`
- `PersonalClaw/src/personalclaw/apps/native/native-workflows/`
- `PersonalClaw/src/personalclaw/apps/native/personalclaw-workflows/`
- `PersonalClaw/src/personalclaw/apps/native/run-workflow-action/`

**Provider-type continuity (recon-corrected):** the extension registry's `workflow` `_TypeHandler` (providers/registry.py) currently registers app-contributed providers into `workflows/registry.py`, and manifest `PROVIDER_TYPES` MUST equal the runtime type-handler set (guarded by `test_manifest_types_match_handlers` — the #47 bug class: an unregistered type blocks reinstall/update of any app declaring it). Deleting `workflows/registry.py` without repointing would break both. The new feature ships a v2 def-provider registry (`workflows/defs.py` exposes `register_provider`; a `WorkflowDefProvider` ABC = read/list defs, optional write) and the `workflow` _TypeHandler is **repointed** at it in the same commit that deletes the old registry — apps can then contribute workflow-def packs (template libraries) exactly the way they contribute task/search/prompt providers today.

Backend (surgical edits):
- `context.py`: remove `force_workflow_ids` param + workflow-surfacing block in `build_message` (the `[ACTIVE WORKFLOWS]` injection lands in the same spot in Slice 6)
- `dashboard/chat_runner.py`: remove `_force_workflow_ids`, `force_workflow_ids` kwarg, workflow-related agent_id helper
- `dashboard/server.py`: remove `with_session_workflow_cleanup` composition + `register_workflow_routes` call
- `dashboard/handlers/loop_routes.py`: remove `workflow_ids` from loop-create body + capability catalog workflow half
- `validation.py`: remove `_WORKFLOW_SCOPES`, all `WORKFLOW_*_SCHEMA`, workflow entries in `MCP_CORE_SCHEMAS`, `"run-workflow"` in `ALLOWED_HOOK_PROVIDERS` (re-added for the v2 run-workflow action in Slice 3)
- `mcp_core.py`: remove `mcp_workflows` from `_AGGREGATED_CATEGORY_MODULES` (mcp_core.py:918)
- `fs_watch.py`: remove the old workflow dir from `default_config_roots` (re-added for the new `defs/` layout in Slice 0)

Frontend (delete):
- `PersonalClaw/web/src/pages/workflows/` (entire directory: `WorkflowCreatePage.tsx`, `WorkflowDetail.tsx`, `WorkflowForm.tsx`, `WorkflowsListPage.tsx`, `WorkflowsSection.tsx`, `workflowDag.ts`, `workflowMeta.ts`)

Frontend (surgical edits):
- `app/App.tsx`: remove old workflow route imports + entries (lazy import, `NAV` entry, `renderPage` case)
- `lib/api.ts`: remove old workflow API methods + `WorkflowItem`/`WorkflowGraph` types
- `lib/agents.ts`: remove the workflow comment reference (trivial)
- Nav config: remove old workflows nav entry (will be re-added by new feature)

Tests (delete):
- `test_run_workflow_action.py`, `test_workflow_list_tool.py`, `test_workflows_api.py`, `test_workflows_composition.py`, `test_workflows_evolve.py`, `test_workflows_injection.py`, `test_workflows_native.py`, `test_workflows_surfacing.py`

Config:
- The `workflows.*` config namespace is reused by the new feature (different schema); the old keys (`workflows.enabled`, `workflows.match_threshold` — default 0.62, loader.py:1052; NOT 0.55, which is the skills surfacing threshold) are replaced by new ones.
- User data at `~/.personalclaw/workflows/` is migrated: old `<name>/WORKFLOW.md` dirs are archived to `~/.personalclaw/workflows/_legacy_sops/` (non-destructive; the user can read them but the new feature ignores them).

### Phase 2: Build the new feature (implementation slices below)

---

## 7.5 Provider-Architecture Integration (where each new piece plugs in)

Every new capability plugs in the way existing ones do — no bespoke seams:

| New piece | Plugs in via |
|---|---|
| Chat tool module `mcp_workflows.py` (new) | tool-category module exposing `_list_tools`/`_call_tool`, listed in `mcp_core._AGGREGATED_CATEGORY_MODULES` (mcp_core.py:918); param schemas in `validation.py` `MCP_CORE_SCHEMAS` |
| v2 `run-workflow` action provider (hooks/triggers start runs) | `action_providers` registry (`register_action_provider`) + name added to `ALLOWED_HOOK_PROVIDERS` (validation.py:555) — omission means hook_create rejects it even though the UI offers it |
| `artifact_update` action provider (WF2-R15) | same two-step registration as above |
| App-contributed workflow-def packs | manifest `provider: {type: "workflow", implementation: "module:factory"}` → the repointed `workflow` `_TypeHandler` → the v2 def-provider registry; `PROVIDER_TYPES` ↔ handler parity preserved (test_manifest_types_match_handlers) |
| `infer` node model calls | `one_shot_completion` on the reasoning axis (plain ModelProvider — never the NativeAgentRuntime that chat/code_tools resolution returns); tier→model resolution threads the `model` build kwarg (register_entry raises on dup — never re-register) |
| Stage subagents | `SubagentManager.spawn` unchanged contract + the NEW `__wf_depth` counter (mirrors `__hook_depth`), `silent=True`, run-scoped on_done |
| Gate `verify_command` | `loop/gates.py` idiom: `audit_bash_command` screen, tristate result |
| Config: `workflows.{enabled, max_active_runs, max_concurrent_nodes (per-lane), default_effort, default_node_timeout_total_secs, default_node_timeout_stall_secs, model_tiers, retention.*, gate_timeout_unattended_secs}` | ALL FOUR wiring points: (a) `WorkflowsConfig` dataclass fields with `_meta(label, help)` (schema tests enforce reachability; `model_tiers` as a dict field or `list[dataclass]` needs per-element `_meta`), (b) `AppConfig.load()` explicit field mapping, (c) `to_dict()`, (d) PATCH `_EDITABLE_CONFIG` (+ FE `patchConfig`) for runtime-editable keys |
| Run events | per-run `SseRegistry` on `DashboardState` (loop_sse pattern) + WS `_broadcast` refetch signals; FE registration in the useRunStream.ts lifecycle union (mandatory — EventSource drops unregistered types) |
| Secrets | credential store (`save_credential` / `.env` 0600) via `{{secret:KEY}}`; RedactingSink reuses `security.redact()` |
| Untrusted-input lint | `fence_untrusted` (security.py / `sdk.security`) |
| Backup/restore | new `workflows` component in `snapshot.VALID_COMPONENTS` + `portability.py` export/import (neither covers workflows today) |
| Def files | `fs_watch.default_config_roots` gains the `defs/` dir |

**Memory vs knowledge boundary:** everything this plan persists (journal, ledger, crystallized digests, decision records, handoffs) is **engine/run state** feeding the LEARNING-FLYWHEEL memory work. Nothing here writes to the knowledge store (`knowledge.db`) or uses `knowledge_*` names — knowledge remains the user's personal items (documents, files, photos, notes).

---

## 8. Phased Implementation Slices

Each slice is independently shippable and testable. Rev-2 scope grows the estimate honestly from 17 to **31 sessions**.

### Slice 0: Scaffold + Data Model + Store + Bindings (est. 3 sessions)
- Create `PersonalClaw/src/personalclaw/workflows/` package (fresh) after Phase 0/1.
- `models.py` (WorkflowDef incl. spec_semver/metadata/on_overlap, WorkflowRun incl. genealogy/intent/budget, full Node taxonomy incl. `infer` + `branch`, RunStatus, InstanceState incl. extended outcomes, failure taxonomy dataclasses), `store.py` (SQLite run store, WAL, `(root_run_id, status)` index, same idiom as loop store), `defs.py` (def CRUD on filesystem, fs-watch registration, bundled sync, v2 def-provider registry + repointed `workflow` _TypeHandler).
- `bindings.py`: grammar, resolver, pipe functions incl. sanitization pipes, typed BindingError, two resolution paths (whole-value vs interpolated), untrusted-origin lint (WF2-R9).
- Spec-ingestion validator core (WF2-R12): never-throw structural pass, typed issue accumulation, stable error codes, strict mode, Kahn level-grouping, branch case-coverage check.
- Config wiring: all keys through the FOUR points (§7.5) incl. per-lane `max_concurrent_nodes` + `model_tiers` slot map.
- Unit tests: model serialization, store CRUD, binding resolution + BindingError + type-preservation, validator error codes.

### Slice 1: Pure Frontier Core + Engine (est. 4 sessions)
- `tick.py`: `frontier()` pure function — incl. **active-edge join gating** (WF2-R18: active-edge set in run state, wait-entry activation) and **typed executor lanes** (WF2-R21: llm/io/compute caps in `Limits`).
- `controller.py`: `RunController` with lock, task management, lifecycle transitions, terminal-write ownership (only tick loop writes terminal status — WF2-R10).
- `engine.py`: node dispatchers (stage → SubagentManager.spawn; infer → one_shot_completion with tier resolution; branch → binding eval + active-edge record; transform → binding eval + output_contract; action → action-provider; wait/gate → state update).
- SubagentManager `__wf_depth` counter (max 3; genuinely NEW enforcement — today's block is prompt-only); stage spawns `silent=True` + run-scoped completion routing.
- `WorkflowRun.project_id` threading via `projects.resolve_project_id()`; genealogy propagation (root_run_id, spawned_by_node_id, branch_key).
- `watchdog.py`: `WorkflowWatchdog` 5s poll, server-startup registration.
- `journal.py` (epoch + inputs-hash keyed resume cache; spec-region-hash stamping; dangling-tool-result-safe cuts) + Run Ledger emission (§5 table incl. per-node model/provider/cost + resolved-prompt refs).
- Resume/crash recovery: orphan reap, sticky cancel intent honor, budget pre-charge. Retention pruning (per-def cap + origin TTL + pinned + crystallize-before-prune + deletion sweep contract). Per-def dispatch lease + `on_overlap` policy.
- Property tests: `frontier()` determinism, idempotence, frozen-region, lane caps, active-edge (both WF2-R18 regression cases).
- Integration test: simple sequence of 2 stages → completion.

### Slice 2: Outcome Model + Engine-Owned Completion + Resilience (est. 3 sessions)
- Extended outcome states (degraded, escalated, no_change, scope_violation, blocked{protocol_violation}) threaded through state/journal/ledger (WF2-R5, R10).
- Engine-gated transitions + verification ladder + `required_artifacts` gates + `gate.verify{script}` + fresh-judge invariant + closed verdict enum (WF2-R3).
- Typed attempt records + mutation-hint retries + structured retry payloads + escalation artifact + circuit breaker in frontier() (WF2-R4).
- Budgets: run/node soft caps, extend-budget gates, pre-charge invariant, baseline_check hook, topology-derived call estimate (WF2-R4).
- Two-knob timeouts (total + stall; progress events feed the stall clock) + warm-up/execute split for local-model actions (WF2-R5).
- foreach `on_item_error` + per-item checkpointing + per-item progress events (WF2-R5).

### Slice 3: Side Effects + Scope + Termination + Secrets (est. 2 sessions)
- Effect ledger: idempotency keys, effect_status records, redo_effects gate, caller idempotency-key dedupe on start/edit, BYOI teardown contract (WF2-R1).
- v2 `run-workflow` action provider + registration in `ALLOWED_HOOK_PROVIDERS`.
- Write-scope enforcement: pre/post fs-tree diff, normalized matching, scope_violation ledgering, warn-vs-reject config (WF2-R19).
- Termination: sticky CANCEL intent, protocol-violation auto-transition, `workflow_audit` diagnose/heal (WF2-R10).
- Secrets: `{{secret:KEY}}` resolution, `_has*` stripping/re-injection on GET/save, fork credential copy, RedactingSink on journal/events, inline-secret spec lint (WF2-R14).

### Slice 4: Mid-Flight Mutation + Checkpoints + Fork (est. 3 sessions)
- `mutations.py`: op types incl. `run_from`, batch validator, spec-history writer, epoch/inputs-hash logic.
- Binding-dependency cascade closure + engine-computed cascade preview + inputs_stale flags + rollback-vs-revert split + TOCTOU re-verify (WF2-R2).
- Mutation queue in `RunController` (drain between scheduling steps); grammar hardening rules a-f incl. atomic failure contract + alias normalization (WF2-R20).
- Rewind: archive outputs + journal region, epoch bump on force, memoized replay on unchanged inputs, frontier recompute. Skip: subtree skipped, output null.
- Checkpoints + `fork` op: new run from checkpoint, `forked_from` + root_run_id provenance, shared read-only journal prefix, fork-axis disambiguation.
- Property tests: rewind idempotence, cascade = binding closure (NOT tree descendants), no frozen-node mutation, acyclicity preserved, fork isolation, effect-crossing preview.

### Slice 5: Human-Input Contract + Gates (est. 2 sessions)
- Typed ask payload model + mode-dependent gate timeouts + `timed_out_unattended` (WF2-R7).
- Continuation records + durable resume tokens + expiry → `resume_expired`; atomic answer consumption; handoff bundle.
- Action-node clarification → needs_input path; auto-approve policy for trigger-origin runs; owner binding + default-DENY for remote-channel gates; "always allow" run-scoped memory.
- `gate{kind: event}` transient-hold semantics (one-shot trigger integration).

### Slice 6: Chat Tools + Spec Ingestion + Prompt Injection (est. 3 sessions)
- `mcp_workflows.py` (new): all 19 tools (§4) incl. `workflow_observe`, `workflow_run_from`, `workflow_audit`, `workflow_manifest`; `workflow_plan` ships template-unaware v1 (template-aware planner lands in UNIVERSAL-PLANNING).
- Wire into `_AGGREGATED_CATEGORY_MODULES`; validation schemas in `validation.py`.
- Ingestion: strict mode + repromptable errors on author/plan/edit; dry-run-before-save; provenance actor; run-start preflight (credentials/binaries from metadata.requirements + `can_resolve_use_case` model probe); manifest generated from registries + CI drift test.
- Staged-turn contract for mutation tools (WF2-R20f).
- `[ACTIVE WORKFLOWS]` context block in `context.py:build_message` (never-break-a-turn).
- Blocking-mode handler: `await controller.wait_for_terminal()` with progress events.
- Integration test: chat session creates a workflow, starts it, edits a pending node with cascade preview, inspects output.

### Slice 7: HTTP API + FE List/Detail Pages (est. 2 sessions)
- `handlers.py` (new): REST routes for defs + runs (CRUD, status, mutations, outputs, resume tokens, per-run SSE stream endpoint).
- Register routes in `dashboard/server.py`; per-run `SseRegistry` on `DashboardState`.
- FE: `pages/workflows/` (new): `WorkflowsListPage.tsx` (defs + active runs), `WorkflowDefDetail.tsx` (view/edit def tree), `WorkflowRunDetail.tsx` (live run view, snapshot-then-subscribe).
- FE: `lib/api.ts` workflow methods + types; nav entry in Capabilities section (lazy import + `NAV` + `renderPage`).

### Slice 8: Live Chat Widget + Event Pipeline (est. 3 sessions)
- `WorkflowProgressCard.tsx` mirroring SdlcProgressCard (REST poll cadence from just-fetched status; detection regex over tool output).
- Event pipeline: dedup keys, deterministic event ids, event-fold law (pure fold, unit-locked like runFold.ts), epoch-tagged supersede-drop, node-keyed patches, per-observer debounced coalescing (~25ms) + schema-validated snapshot projection, result_omitted spill boundary (WF2-R11 + batch-5).
- FE lifecycle-union registration for ALL workflow SSE events (useRunStream.ts pattern; test asserting backend types ⊆ FE union).
- Typed ask-payload renderer (approval/choice/text/form) in the attention banner + needs-input inbox projection.
- Blocking-mode rendering; inline controls; two-step delete; per-item foreach progress rows; degraded/remediation rendering.

### Slice 9: Templates + Conventions Pack (est. 2 sessions)
- Author 6 bundled templates (incl. `produce-and-audit`) as `bundled/<name>/workflow.json`; macros (`judge_panel`, `verify_panel`, `route`, `research_sweep`) compiling to core nodes with infer legs.
- Conventions: triage-first + Finding record + baseline capture + `bundled/shared/` prompt blocks + template-lint + steering_examples (WF2-R15).
- `artifact_update` action provider (+ ALLOWED_HOOK_PROVIDERS).
- Bundled-sync logic (mtime, no-overwrite); structural contract tests per template; FE template picker; `workflow_plan` template referencing.

### Slice 10: Advanced Constructs + Context Lifecycle (est. 2 sessions)
- `foreach pipeline=true` (streaming handoff, no barrier); `loop until_dry`; `subworkflow` nesting (depth ≤ 3, namespaced instances, `child_run_attach`).
- Context lifecycle: `session: fresh` iteration resets + journaled handoffs, typed carryover buckets, decision records, output offloading to artifacts + `artifact_inspect`, two-layer compaction ladder (WF2-R6).
- Run-level token/time budget enforcement end-to-end; FE collapsible containers + iteration progress.

### Slice 11: Validation + Hardening (est. 2 sessions)
- End-to-end tests: full lifecycle (create → run → mid-flight edit → rewind → run_from → fork → complete).
- Adversarial property tests: concurrent mutations, crash-during-execution recovery (incl. mid-cancel restart), deep nesting, double-resume answer replay.
- **Timeout-fires regression pair** (WF2-R5 batch-5): stalled node killed within window; progressing node NOT killed.
- **Active-edge regression pair** (WF2-R18): untaken branch never deadlocks a join; async fan-out never fires a join prematurely.
- **Journal-replay regression harness** (WF2-R11): recorded JSONL traces (happy-path, restart-during-run, rewind-during-stream, network-degraded), duplicate/fanout/order/p95 metrics CI-gated against baseline.
- Performance: 50+ node specs schedule < 100ms; journal replay of 1000-entry runs; coalesced widget updates under 20-node fan-out.
- Security: binding sandbox (no eval, no fs access); RedactingSink coverage tests; run output redaction; write-scope escape attempts.
- Documentation: architecture doc, template authoring guide, manifest drift test.

---

## 9. Risk Register

| Risk | Mitigation |
|---|---|
| Complexity of mid-flight editing while engine is running | Single-writer conductor with lock; mutations applied only between scheduling steps; frozen-region invariant + TOCTOU re-verify prevent invalid states |
| Journal/cache correctness across rewind | Epoch + inputs-hash keys + property tests for rewind idempotence; outputs AND journal regions archived not deleted; spec-region-hash stamps force re-frontier under mutated specs |
| Stale-binding inconsistency after rewind | Cascade computed over the binding-dependency graph (not the container tree) + `inputs_stale` flags + mandatory preview (WF2-R2) |
| Double-fired external effects on resume/rewind/fork | Effect ledger with idempotency keys; committed effects gate re-execution behind `redo_effects`; idempotent teardown contract (WF2-R1) |
| Join deadlock / premature join fire under conditional+async shapes | Active-edge gating with wait-entry activation + two CI-gated regression cases (WF2-R18) |
| Infinite fix loops / thrash in unattended runs | Deterministic LLM-free circuit breaker (`escalated` state) + budget soft caps + pre-charge + baseline_check (WF2-R4) |
| Agent self-certifying completion | Engine-owned transitions, verification ladder, required_artifacts, fresh-judge invariant (WF2-R3) |
| Stage subagent writes outside its scope (already happened once platform-wide) | Post-hoc fs diff vs `allowed_write_paths` → `scope_violation` + configurable block (WF2-R19) |
| Widget stale/duplicate/out-of-order updates (the K42/K44/K45 class) | Dedup keys, snapshot-then-subscribe, event-fold law, epoch-tagged supersede-drop, coalesced delivery, replay harness (WF2-R11) |
| New SSE events silently dropped by the FE | Mandatory lifecycle-union registration (useRunStream.ts pattern) + backend⊆FE-union test |
| Secrets leaking into durable stores (spec/journal/outputs/ledger) | credential_ref seam + `_has*` stripping + RedactingSink on the writers (WF2-R14) |
| Widget performance with large specs (50+ nodes) | Collapsible containers; node-keyed patches; per-observer debounced coalescing (one update per tick per connection); terminal nodes stop updating |
| Template expressiveness vs node-type proliferation | Orchestration patterns compose from core node types; only `infer` + `branch` added (each closing a real expressiveness gap); macros expand at def time |
| Config migration from old to new `workflows.*` namespace | Old keys simply removed; new keys have sensible defaults; no silent fallback to old semantics |
| SubagentManager concurrency cap hit by large parallel blocks | Per-lane caps respected by `tick()`; excess stays `ready`; heavy local-IO nodes can't starve LLM stages (WF2-R21) |
| Run-dir flood from high-frequency origins | Tiered retention: per-def cap, origin TTL, pinned exemption, crystallize-before-prune, terminal-run journal compaction |
| Scheduled def double-run / wedged lock after crash | DB-backed lease with expiry + explicit `on_overlap` policy (WF2-R1 batch-5) |
| Spawn-recursion depth abuse (workflow → stage → subagent → workflow) | `__wf_depth` counter (max 3, NEW enforcement — today's block is prompt-only); max-depth spawns get single subagent_run only |
| LLM-authored specs/mutations failing in LLM-specific ways | Strict compile + typed repromptable errors + mutation grammar rules + staged turns (WF2-R12, R20) |
| Timeouts that never actually fire (documented in the wild) | "Timeout fires" regression pair as Slice 11 acceptance criteria (WF2-R5 batch-5) |

---

## 10. Open Questions (for user decision)

1. **Legacy SOP migration:** Should old workflow SOPs be importable as single-stage workflows in the new system, or simply archived and left for manual reference?
2. **Approval trust levels:** ~~Should workflow runs inherit the session's approval mode?~~ PARTIALLY RESOLVED (WF2-R7): trigger-fired/scheduled runs get auto-approve where only destructive/high-risk gates block; remaining question — should *chat-origin* runs inherit the session's approval mode or carry a per-def trust policy?
3. **External event gates:** RESOLVED (WF2-R7e): supported with transient-hold semantics (prerequisite-absent does not consume the trigger event; bounded retry, loud give-up) — lands in Slice 5.
4. **Token budgets per run:** RESOLVED (WF2-R4): soft cap — breach pauses resumably with a ledger event + needs-input item; extend-budget gates at ~80%; budgets pre-charged from the journal on resume.

---

## Dependencies

- `SubagentManager` (existing): subagent spawning, concurrency, transcripts, orphan reconciliation — plus the NEW `__wf_depth` enforcement this plan adds (mirroring `__hook_depth`).
- `one_shot_completion` / reasoning axis (existing): the `infer` node's execution path.
- Per-run `SseRegistry` + WS `_broadcast` (existing loop pattern, dashboard/state.py): event delivery to FE. `state.notify` only for user-facing notifications.
- `SdlcProgressCard` + `useRunStream.ts` `RUN_LIFECYCLE` union + `runFold.ts` (existing): widget architecture + FE fold prior art.
- `loop/tick.py` (existing, pure `evaluate(cfg, state, now) -> Decision`) + `loop/store.py` (SQLite WAL): prior art for pure-function scheduling + run store.
- `loop/gates.py` `run_verify_command` + `loop/judge.py` independence pattern (existing): gate verification prior art.
- `action_providers` registry + `ALLOWED_HOOK_PROVIDERS` (existing): action-node dispatch + the allowlist any new provider must join.
- `providers/registry.py` `workflow` `_TypeHandler` (existing): repointed at the v2 def-provider registry (PROVIDER_TYPES parity).
- `security.redact` / `fence_untrusted` / credential store (existing): secrets + untrusted-input seams.
- `fs_watch` (existing): def-file change detection.
- `snapshot.py` / `portability.py` (existing): gain the `workflows` backup component.

---

## Success Criteria

1. A user can author a multi-stage workflow (via chat or UI), start it, see live progress in a chat widget, edit unexecuted stages mid-flight with a cascade preview, rewind a completed stage, and see exactly its binding dependents re-execute — all within one chat session.
2. Background workflows run to completion without blocking chat; the widget updates live with no duplicate/out-of-order rendering under rewind (replay harness green).
3. Blocking workflows hold the chat turn until done, with progress visible.
4. At least 3 bundled templates work end-to-end (deep-research, code-implementation, audit-sweep), each opening with the triage-first convention and emitting canonical Finding records where applicable.
5. Resume after gateway restart works correctly: journal cache hit for completed nodes, re-execution for interrupted ones, budgets pre-charged (no fresh-budget minting), committed external effects NOT re-fired, sticky cancel intents honored.
6. Rewind/fork across a run containing committed side effects surfaces them in the preview and never double-fires without `redo_effects`.
7. The active-edge and timeout-fires regression pairs pass (joins never deadlock on untaken branches nor fire early on async fan-outs; stall timeouts kill stalled nodes and spare progressing ones).
8. A gate/judge never passes on the producing agent's self-report — engine-executed verification only; verdicts are closed-enum routed.
9. From `events.jsonl` alone, a run's reasoning path (prompt → tools → output per node, with model/cost) is reconstructable; crystallized digests survive retention pruning.
10. No residual code, config, or UI surface from the old SOP feature remains; the `workflow` provider type still round-trips app install/update (PROVIDER_TYPES ↔ handler parity test green).
