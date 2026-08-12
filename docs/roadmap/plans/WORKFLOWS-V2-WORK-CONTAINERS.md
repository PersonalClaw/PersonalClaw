# WORKFLOWS-V2-WORK-CONTAINERS

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/WF2WOR.md`](../atomic/WF2WOR.md) as 12 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Work-Container Hierarchy — Project as the Sole Umbrella

**Status:** IN PROGRESS — sessions 46-54 shipped the DECISION LAYERS (PRs #185-#193; the code is on
`main` inside squashes): `containers`, `publish`, `batch_compile`, `workspace`, `worktrees`,
`needs_input`, `introspection`, `project_export`.
🔴 **MOST CALL SITES AND MOST OF THE FE ARE STILL MISSING** (AST audit 2026-08-04, partially
superseded — see the Execution log, which wins): `workflows/{introspection,project_export,
batch_compile}.py` have **zero production importers**; `containers`' board projection
(`collect_sections`/`group_board`/`board_row`/`attention_count`/`board_state_for`) has no caller;
there is no `GET /api/projects/{id}/work` route and no React work board; `compile_batch` is never
called from `mcp_subagents`; archive I/O is decided-but-never-performed. Criteria 1/6/8/9 are
structurally unmet. **Criterion 7 is MET as of 2026-08-10 (WF2WOR-4):** `workspace`/`worktrees` now
have production callers via `workflows/provisioning.py` + `controller._provision_workspace`,
provisioning and setup/teardown I/O are performed, `sweep_decision`'s substrate now comes from
`worktrees.substrate_for`, and the code-run cockpit's diff panel + reintegration offer ship.
Status corrected 2026-08-04; criterion-7 half corrected 2026-08-10.
(rev 2 — research-integrated 2026-07-12)

---

## Research Integration (2026-07-12)

Folded from the approved WORK-CONTAINERS recommendation set (all batches incl. amendments 3-5):

- **WORK-R1** — NeedsInputItem contract + journal-event reification + resume handshake + owner binding + OS-projectable payload → §6.1, §5
- **WORK-R2** — batch-compile hardening (isolation, dual depth, lineage env, typed leaf outputs, write-holder lint, recall-view, N-variant, capability classes) → §3
- **WORK-R3** — workspace-provisioning block {mode, preserve_patterns, setup, teardown} + locking/teardown bundle + durable-branch persistence → §4.1
- **WORK-R4** — evidence bundles + standardized terminal handoff report → §2
- **WORK-R5** — attempt ledger, said-no metrics, verification debt, circuit-breaker retry guards, results-ledger artifact, live touched-items feed → §6.2
- **WORK-R6** — introspection checklist as acceptance criteria + handoff snapshot projection → §6.4, Success Criteria
- **WORK-R7** — truthful run-state lifecycle (queued/zombie/lost/suspended) + live run adoption + run-id-keyed streaming → §5.2, §6.3
- **WORK-R8** — claim-before-work leases + wayfinder sections (decisions / fog / out-of-scope) → §1.5, §6.1
- **WORK-R9** — RunStats + per-node cost/provenance metering as journal projection + trajectory replay → §6.2
- **WORK-R10** — artifact integrity: typed lineage links, material-change gating, media self-containment, version diffs → §2
- **WORK-R11** — project living overview + charter/instructions injection into all project sessions → §1.2
- **WORK-R12** — /work bulk-data semantics + local-first hub rendering + rebuild-projections repair → §1.4, §6.1
- **WORK-R13** — cockpit compact affordances (chip ribbon, split view, incremental foreach, pinned-artifact dashboard widget) → §6.5
- **WORK-R14** — project-scoped memory locality + knowledge project tagging with provenance fencing (adapted to the real cwd-partition seam) → §1.6
- **WORK-R15** — project export/import contract (validated ZIP, integrity, path safety) → §1.7
- **WORK-R16** — agent roster as slug-keyed drift-checked catalog → §3 (orchestrator paragraph)
- **WORK-R17** — per-run file drop (in) + outbox (artifact listing with typed previews) → §2
- **WORK-R18** — folder contracts (.folder.yaml, lifecycle incl. ttl_staging) → §4.2
- **WORK-R19** — per-project run-environment secrets, keychain-backed, secret-filtered leaf env → §4.3
- **WORK-R20** — optional container workspace mode + snapshot checkpoints (opt-in, deferred) → §4.4

Reality corrections made while integrating (verified against code 2026-07-12): there is **no `LearningGate` class** (the gate is `after_turn_review.should_review()` + the `session_restrictions` incognito/temporary registry, enforced per-consumer); the **dashboard has no bento/tile registry** (widgets are hard-imported — R13(d) adapted); **memory is cwd-partitioned via `memory_dir_for_cwd()`** while **knowledge is global** (R14 adapted to that seam; memory≠knowledge boundary held); **snapshot/portability cover NEITHER `projects/` nor `tasks/` nor `loop/` nor `artifacts/` today** (R15 adds net-new components, it does not "extend" coverage); projects persist at **`config_dir()/projects/p-<8hex>/project.json` (top-level, not under `tasks/`)**; SEL's `_infer_source` has **no workflow source** yet; **no per-context tool filtering exists** (leaf least-privilege rides the `__wf_depth`-style env flag checked in tool handlers).

---

## Overview

**Project is the only umbrella; WorkflowRun is the only run; everything else is either a resource the run uses (Session, Agent, Artifact, Workspace) or a projection of the run (Tasks, journal, chat widget, Work board, NeedsInput inbox).**

This plan owns four things the other plans reference but don't specify: (1) the Project↔WorkflowRun containment contract, (2) the subagent-tools↔workflow-stages relationship, (3) the run's workspace/environment contract, and (4) the Project hub FE (the "one glanceable board" for all background agency).

```
Project (umbrella — existing tasks/hierarchy.Project, persisted at projects/p-<id>/project.json, extended minimally)
 ├─ WorkflowRun[]          (project_id required; journaled; forked branches; truthful lifecycle §5.2)
 │    ├─ node instances    (per-node state + attempt ledger)
 │    ├─ checkpoints       (fork points — v2 Slice 2; container snapshots optionally anchor them §4.4)
 │    ├─ workspace         (NEW block: {mode, preserve_patterns, setup, teardown, env} — §4)
 │    ├─ Artifacts         (EXISTING artifacts/ entity — run outputs, evidence bundles, results ledgers)
 │    ├─ file drop/outbox  (approval-gated file-in; published-artifact listing out — §2)
 │    └─ child runs        (subworkflow nesting)
 ├─ TaskList/Task[]        (existing; runs materialize tasks per TASKS-SOPS; TTL'd claim leases §1.5)
 ├─ Session[]              (chat sessions tagged project_id; run-owned stage sessions)
 ├─ context/ + worktrees/  (existing project dirs, threaded into runs; folder contracts §4.2)
 ├─ overview.md + brief    (living overview revised on run completion; both injected into project sessions §1.2)
 ├─ secrets                (per-project keychain-backed run-env store, presence-flags only §4.3)
 └─ wayfinder ledgers      (decisions-so-far / not-yet-specified / out-of-scope §1.5)

Loop (legacy)              → renders in the same hub during coexistence; retired per LOOPS-EVOLUTION
WorkflowDef                → NOT under Project (scoped global/workspace/agent/session); a run binds def@version + project_id
```

---

## 1. Project — Keep as Sole Umbrella, Extend Minimally

`Project` (tasks/models.py hierarchy; **stored top-level** at `config_dir()/projects/p-<8hex>/project.json` with `context/` + `worktrees/` siblings — hierarchy.py:42-47 encodes this on purpose) is already "the first-class work unit" with the right personal-scale shape (name, brief, workspace_dir, context_dir, worktrees, protected `Personal`/`Repeatable`). **No sub-projects, no milestones, no org semantics** — that's the enterprise slope.

Seven extensions (was four; R8/R11/R14/R15 add scope):

1. `WorkflowRun.project_id` **required**, auto-resolved at run creation via `projects.resolve_project_id()` (auto-create-on-blank, exact parity with today's `project_run_create`). *Already an engine acceptance criterion in WORKFLOWS-V2.md.*

2. **Living context pair + injection (R11).** Project `context_dir` = default cwd fallback for stage nodes + a readable binding (`{{project.context_dir}}`). Beyond the existing brief, each project carries `context/overview.md` — a living overview the engine revises in place on run completion within the project (what changed, what the project now knows), explicitly distinct from the append-only Run Ledger (overview = current state; ledger = history). The `brief` + `overview` inject as a system-prompt block into **any** session inside the project — stage sessions (parity with loops' `_project_brief_block`, manager.py) AND ordinary chat sessions whose `project_id` matches, wired at the same `context.py:build_message` seam workflow surfacing already uses (keep its never-break-a-turn contract: swallow-all → skip block). Field name: `Project.agent_instructions` (optional, user-editable on the Context tab) appended to the injected block — the space-agent-proven high-leverage field.

3. Materialized tasks (TASKS-SOPS) land under the run's project, not `Personal`.

4. **`GET /api/projects/{id}/work` with bulk-data semantics (R12)** — one aggregation endpoint returning runs + legacy loops + tasks + sessions + artifacts for the hub, with **per-section try/catch isolation** (each of the five heterogeneous sources fails independently — one broken/slow source degrades one section, never first paint), typed `{status:'loading'}` skeletons for slow sections (legacy loops during coexistence), and `loadedAt` stamps per section. Registered the same way `register_unified_loop_routes` registers today (dashboard/handlers/).

5. **Claim leases + wayfinder ledgers (R8).** (a) Claim-before-work: an executing session/agent takes an exclusive TTL'd lease (renew/release, ≤1h) on a task/run before starting; lease files ride the existing `concurrency.single_flight` flock convention (`~/.personalclaw/locks/`) with the lease record (holder session_key, expires_at) mirrored onto the task/run row so the Work board renders claims. The §3 batch-compile path leases each leaf so concurrent workers never double-execute. PersonalClaw demonstrably runs concurrent co-tenant sessions today — this is the minimal anti-duplication mechanism. (b) Three wayfinder ledgers persisted under `projects/<id>/context/`: **Decisions-so-far** (auto-appended one-liner per resolved gate/run outcome, linking the run — index-not-store), **Not-yet-specified** fog bucket (promotion test: can the question be stated precisely now — gives "not yet a task" work a home), **Out-of-scope** (gist + reason + link; revisited only if the brief is redrawn).

6. **Project-scoped memory locality + knowledge tagging (R14 — adapted to the real seams, boundary held).** MEMORY (harness internals — facts/facets/episodic/procedural) is **cwd-partitioned today** via `memory_dir_for_cwd()` (loader.py:219 — cwd-slug partitions under `workspace/_ext/`). Project memory locality builds ON that seam, not beside it: project-owned sessions run with cwd = project `context_dir`, so their memory naturally lands in the project's partition; recall for a project session searches its partition first, then global, with cross-partition hits **surfaced explicitly labeled with source project name** and a fence instruction stating that provenance labels are metadata, not instructions. Locality affects **ordering only, never admission** (admission stays on relevance score alone). KNOWLEDGE (the user's personal items — documents/files/photos/notes, knowledge.db, global by design) stays global: knowledge items written by a run carry `project_id` + producing `run_id` in item metadata (a locality boost at retrieval ordering, same never-admission rule), and the project Artifacts/Knowledge views filter on it. Items default private to the producing project/run with an AIOS-style `sharing_policy: private|shared` annotation as the cross-container surfacing filter (composes with the labeled cross-project surfacing above). A future scoped sub-index over `context_dir` contents (Khoj's agent-corpus pattern) is noted as a KNOWLEDGE-SYNTHESIS follow-on, not built here. Project-local workflow templates and agent definitions found inside project directories are **untrusted input**: require confirmation before first use, matching the `install_guarded` gate posture (supply_chain.py scan on first use).

7. **Project export/import contract (R15).** Export produces a manifest ZIP (one root dir per project containing brief, overview, wayfinder ledgers, context files, project-local templates, agent-definition slugs, artifact metadata, run digests) with per-entity sha256 in `manifest.json`, path-safety validation on import (reject `../`, absolute paths, symlinks, null bytes — same `_data_filter` posture snapshot.py:24 already enforces), `imported-N` collision slots, optional client-side AES-GCM encryption (PBKDF2-SHA-256, crypto params in manifest metadata), extract-to-unique-tmp with janitor cleanup. **Secrets never travel** (presence flags only); workspace dirs excluded (too large) — only metadata, templates, digests. **Reality note:** today `snapshot.py VALID_COMPONENTS` and `portability.py` cover **neither `projects/` nor `tasks/` nor `loop/` nor `artifacts/`** (persistence recon, gotcha 10) — this is **net-new** coverage: a `projects` component is added to both (alongside the `workflows` component WORKFLOWS-V2.md already adds), reusing snapshot's merge helpers and portability's `EXPORT_EXCLUDE` sensitivity list.

**What we lose:** "runs without a project" — costs one auto-created project row per orphan run. Acceptable; it's what loops already do.

---

## 2. Artifacts — Use the EXISTING Entity

The adversarial review caught the original proposal inventing a second Artifact noun. **PersonalClaw already has a first-class Artifact entity** (`src/personalclaw/artifacts/`: named, versioned to 50 snapshots, project_id-scoped, event log, REST routes + `mcp_artifacts` tools). The design uses it as-is:

- A run's named outputs REGISTER as Artifacts (existing `artifacts.registry`), carrying `meta: {run_id, node_id}` for provenance and deep-linking.
- Refinement runs UPDATE the same artifact by name (its native versioning gives us the A2A "stable name across revisions" pattern for free).
- The plan artifact the planner produces (UNIVERSAL-PLANNING) is an Artifact of kind `plan`.
- `knowledge_persist` with `also_artifact: true` (KNOWLEDGE-SYNTHESIS plan) already points here — one noun, all plans aligned.

New work on the one noun (R4/R5/R10/R17):

1. **`publish:` declaration** on stage nodes (`publish: {artifact: <name>, kind}`) that the engine translates into a registry upsert. No new entity.

2. **Artifact integrity (R10).** (a) Typed lineage links extending `meta:{run_id,node_id}`: `SOURCE`→run/node, `INFORMED_BY`→evidence/knowledge item, `RELATED`→siblings — grouped in the project Artifacts tab with deep links both directions. (b) The `publish:` upsert **gates new versions on material content change** (`should_create_new_version`) recording a `change_note` + short diff summary, so refinement runs don't spam the 50-snapshot window. (c) On artifact finalization, referenced local files are copied into the version dir with content-hash names (`file@HASH.png`) and references rewritten (Quarkdown media storage), `@`-prefix passthrough + per-reference opt-out — versioned artifacts stop silently breaking when workspace files move. (d) Cockpit renders structured diffs between versions (section-diff for markdown, token-diff for structured) and multi-view output tabs (rendered/markdown/raw) — riding the existing `ui/content/contentTypes.ts` capability registry (one `register()` per view, the established FE extension seam).

3. **Evidence bundles + terminal handoff report (R4).** An evidence bundle is an **Artifact composition**: one schema-versioned manifest Artifact `{per-file kind, name, size, sha256, optional expiry}` grouping screenshots/video/logs/metadata a run produced. The cockpit renders a **Proof section** (Summary / Before-After / Evidence) from it; NeedsInput items (§6.1) carry the bundle inline (before/after screenshots next to the decision). Paired with a **standardized terminal-node handoff report contract** every template's final node emits: commands run / skipped-with-reasons, side-effect confirmations ("no commit/push performed" analog), known risks, follow-ups — rendered uniformly on the Work board and inbox without per-template FE code. "What did my machine do while I slept" needs proof, not prose.

4. **Results-ledger artifact kind (R5).** Append-only, for ratchet-style iterative runs — every attempt logged including reverted ones (Karpathy's results.tsv). Distinct from both journal (engine cache) and deliverable (the output).

5. **Per-run file drop + outbox (R17).** Each run exposes: an **inbound file drop** (approval-gated multipart ingestion — size cap, atomic tmp+rename via `atomic_write_bytes`, SEL audit entry per file; explicit human approval showing what + size, unless the template declares auto-accept for specific MIME types) and an **outbox** = the run's published-artifact listing (`{id, path, size, updatedAt}` newest-first; a resolve endpoint attaches preview types — markdown/sheet/image/pdf/html/text — via the same contentTypes registry). Both feature-toggled per template with honest disabled-status responses. Named "file drop", not "inbox", to avoid colliding with PersonalClaw's Inbox feature. Ingested files land in the run workspace's `immutable`-lifecycle zone (§4.2) and are fenced (`fence_untrusted`) before any prompt inclusion.

---

## 3. Subagent Tools vs Workflow Stages

**Decision: one substrate, two front doors, and the batch door compiles down.**

- **`SubagentManager` stays the only spawn substrate** (concurrency caps `_MAX_CONCURRENT=3`/auto-sized [2,8], reaper, orphan reconciliation, SEL, approval inheritance). Stage nodes call it `silent=True` + run-scoped (v2 Slice 1).
- **Single-task `subagent_run` STAYS as-is.** Ad-hoc "go check X while I keep chatting" is chat-native delegation; forcing a run record + project resolution + widget onto it is ceremony that kills the personal feel.
- **Batch `subagent_run(tasks=[...])` compiles to an implicit workflow**: same tool signature (agents already know it), but the implementation compiles `tasks[]` into an inline `parallel[stage...]` WorkflowRun (`origin: subagent-tool`, auto-project, `mode: background`) and returns the run id. The batch gains the widget, journal, per-branch retry, resume-after-restart, and fork — everything today's fire-and-forget batch lacks.
- **Threshold rule encoded in the tool: N=1 → raw spawn; N≥2 → implicit run.** No new tool names, no agent-visible migration beyond an updated tool description + orchestrator-skill text (same commit).
- **Depth/recursion:** implicit runs count against the `__wf_depth` cap (v2 Slice 1). A stage's subagent may call single `subagent_run` but not batch. Enforcement mechanism: the spawn call threads `__wf_depth` into the subagent's context the same way `__hook_depth` threads today (an env/context flag the tool handler checks — NOT per-context tool filtering, which doesn't exist; recon confirms the current no-recursion rule is prompt-level only, subagent.py:245).
- **Run-history noise control:** `origin: subagent-tool` runs render collapsed-by-default in the hub and auto-prune on the 7-day subagent cadence unless pinned (retention already specified in v2).

### Batch-compile hardening contract (R2 — the isolation/typing/least-privilege bundle)

The compiled `parallel[stage...]` run ships with the contract every production multi-agent system converged on:

1. **Isolation by default:** filesystem-touching parallel leaves each get an isolated worktree/scratch dir via the §4.1 workspace block (mode `scratch` unless the task declares `worktree`); a **compile-time lint warns when >1 concurrent worker holds Write to the same artifact/dir** (single-Write-holder rule). File access for batch workers goes through TTL-bound scoped file sessions (handle pinned to actor + canWrite, optimistic revision with TOCTOU re-verify) rather than ambient fs where the leaf declares file outputs.
2. **Dual depth enforcement:** statically (compile rejects bad topologies — which node/agent archetypes may spawn which) AND dynamically (the `__wf_depth` counter). Leaf subagents receive **no orchestration tools**, enforced at the tool-handler seam via the depth flag (see above). **Capability classes (batch-5):** each leaf declares `capability: research|mutating`; research-class leaves default to a read-only tool surface (no write tools unless declared) — Friday's shipped pattern, applied at the same handler seam. Per-leaf timeout + per-leaf error isolation: one leaf failure never rejects the batch.
3. **Lineage attribution:** parent lineage `(run_id, project_id, node_id)` threads through the spawn env alongside `__wf_depth`; every memory/knowledge/artifact write is tagged with the producing run/agent id so children announce to the correct surface and the LEARNING-FLYWHEEL gets provenance.
4. **Typed leaf outputs:** leaf workers accept a per-task `output_schema` (`additionalProperties: false`, length-capped strings, `maxItems` — Anthropic's researcher.yaml pattern) so the compiled run consumes typed data, not prose. The compiled batch creates an **"agree data contract first" coordination task** before fan-out when leaves feed each other (prevents interface drift).
5. **Safety-filtered recall-view:** any transcript projection (hub, cockpit, chat tools) strips thinking/tool XML/control tokens, redacts credentials (`security.redact()` — the existing chokepoint), and carries truncated/redacted flags. Leaf env is **secret-filtered** per §4.3.
6. **Sibling awareness + leases:** the compile pre-creates a sibling-awareness wrapper surfacing in-flight sibling runs sharing the same context key and auto-wires continuation parentage; each leaf takes a §1.5 lease before executing. Fail-closed capability-snapshot reassignment with bounded resume generations and per-behavior kill-switches on the retry path.
7. **N-variant batches:** side-by-side comparison view fed by per-child interval metric snapshots (duration, tokens, failures), with consecutive-failure halting per child.

**orchestrator_skill: SEPARATE, confirmed.** Routing policy ≠ execution engine. One shared hook: its agent roster feeds `workflow_plan`'s per-stage `agent` selection — one catalog, two consumers. **Roster format (R16):** a slug-keyed JSON catalog file (slugs = stable filename stems, never display names) with per-entry `{slug, name, description, label, icon, capabilities[], model_tier_hint, activation: always|conditional|on-demand}`. Reality anchor: AgentDefinitions live in `config.json agents{}` (the `agent` entity is an EntitySeamHandler whose source_of_truth is config — providers/registry.py:364), so the catalog is a **generated projection** over config agents + `agents/defaults.py` reserved names, and the **drift check** is a test-suite script (our CI = the pytest/vitest gate) failing when any slug referenced by a workflow template or orchestrator_skill routing doesn't resolve to a real agent. Templates reference agents by slug (rename-proof); display names are presentation-only; `activation` staging prevents oversized persona sets in simple runs. Loop-reserved agents retire with loops; template personas ("judge", "skeptic") become editable AgentDefinitions.

---

## 4. Run Workspace & Environment (NEW — R3/R18/R19/R20)

### 4.1 Workspace-provisioning block (R3)

The WorkflowRun container model gains a `workspace` block, threaded through §3's compile path and TASKS-SOPS materialization:

```yaml
workspace:
  mode: worktree | in_place | scratch | container   # container is opt-in, §4.4
  preserve_patterns: [".env", "*.local.json"]        # copy-in globs — the adoption-critical detail
  setup: "npm install"                               # idempotent; runs on EVERY resume
  teardown: "docker compose down"                    # runs BEFORE workspace deletion
  env: {...}                                         # §4.3 secrets/env section
```

- **Code-kind runs default to a per-run git worktree** under the project's existing `worktrees/` dir (`projects/<id>/worktrees` — hierarchy.py), reusing the proven `loop/worktree.py` machinery (`.worktrees/<id>` + `pclaw/task-*` branches) rather than a second implementation. `preserve_patterns` copy-in happens before `setup` runs before the first stage; `teardown` ties to run retention expiry.
- **Setup idempotency contract (batch-5, Air):** setup runs on every resume, must guard each step with marker files; setup failure does not block the run (logs retained). **Cleanup ordering:** teardown/cleanup hooks run BEFORE worktree deletion (sync artifacts out, stop services). Reserved system vars (HOME, PATH, XDG_*) are rejected as env overrides.
- **Locking + teardown bundle (sandcastle):** PID-liveness lock files OUTSIDE the workspace (fail-fast on live contention, self-healing on stale via pid probe — same fcntl/pid conventions as `concurrency.py` + `session_pid.py`); dirty-preservation on close surfaced as `preserved_workspace_path` on the run record; reuse-with-safe-ff-only-refresh for named workspaces; deterministic per-task branch/dir naming (idempotent retries); **split ownership** — closing the execution container never discards the workspace.
- **Durable-branch persistence (batch-5, Air):** when a run workspace is ephemeral, auto-commit to a per-run branch (`pclaw/run-<id>`) so the run record references git, not a filesystem; the code-run cockpit offers the two reintegration verbs **Apply Locally** vs **Checkout Branch Locally**, plus a diff/review panel over the worktree (changed files, stage/discard) so reviewing a run doesn't leave the cockpit.
- **Run-owned resources:** child resources a run spawns (browser pages, terminals, scratch dirs) carry `spawned_by: run_id` with auto-cleanup on run end and a `keep_open` override for when the resource IS the deliverable. Created-vs-invited semantics (AgentScope): spawned child workers die with the run; invited/pre-existing agents only lose association.
- **Lifecycle:** runs gain archive/restore (create/delete/archive/restore) rather than only delete+retention.

This structurally solves the destructive-test-isolation bug-class PersonalClaw has already hit (the deleted-real-model incident) and the orphaned-resource gap the plan previously left open.

**Config plumbing:** new defaults (`workflows.workspace_default_mode`, `workflows.workspace_teardown_on_expiry`) follow the FOUR wiring points — dataclass field with `_meta(label, help)` on `WorkflowsConfig`, `AppConfig.load()` explicit mapping, `to_dict()`, and `_EDITABLE_CONFIG` + FE panel if runtime-editable (loader.py / core.py:363; the two-maps-is-actually-four gotcha).

### 4.2 Folder contracts (R18)

System directories within a project workspace may declare a `.folder.yaml` contract: `{role, lifecycle: transient|ttl_staging|permanent|immutable, agent_writable: bool, required_frontmatter: [], defaults: {}}`. The engine validates required fields as **warnings, never fatal** (tolerant by design); unknown fields pass silently (forward compatibility — the 23-of-25-memories-dropped bug class). Contributed apps declare folder contracts for their storage dirs instead of hardcoding path conventions — the contract schema is published via `sdk` so apps consume it the way they consume `sdk.security`/`sdk.net` today. Lifecycle semantics:

- `transient` — auto-cleaned on run/session end. **Agent-originated file writes default here** (a staging zone that cannot be promoted to permanent without explicit action — filesystem-level enforcement of propose-don't-write).
- `ttl_staging` (batch-5, MIRIX) — day-scale TTL (default 14 days, nightly cleanup) for unprocessed run observations feeding slow-burning extraction pipelines; promoted (agent-processed) content persists per normal lifecycle, unprocessed staging expires automatically.
- `permanent` — survives runs.
- `immutable` — agent may not modify (ingested reference material, §2.5 file-drop landing zone).

### 4.3 Per-project run-environment secrets (R19)

The workspace block's `env` section may reference a **per-project encrypted secrets store**: values held in the OS keychain (macOS Keychain via `keyring`), never in committed config, run records, or journals. This *extends* the existing credential seam (loader.py `save_credential()` → `.env` 0600 + credential_store) rather than replacing it: the project store is a scoped namespace resolved at spawn time. An env entry that omits its value inherits from the host at spawn (Air's pattern); reserved vars (HOME, PATH, XDG_*) rejected; **the spawn env for leaf subagents is secret-filtered** — only explicitly granted secrets reach children (hardens §3's injection/credential-leak posture: leaf transcripts cannot contain ungranted secrets, on top of the existing `security.redact()` output pass). Cockpit shows **presence flags only, never values**. This supersedes "preserve_patterns copies raw .env everywhere" as the secrets story (preserve_patterns remains for non-secret local config).

### 4.4 Optional container mode (R20 — opt-in, deferred to last)

`mode: container` joins the workspace enum (backends: Docker/containerd, Apple Virtualization on macOS — no hard Docker dependency), declared via a typed environment manifest `{image XOR build{dockerfile,context}, user, folders/mounts, capabilities}` where the engine owns runtime semantics (entrypoint/workdir overridden, reserved mount point protected). Container snapshots taken between stages anchor **fork-from-checkpoint to workspace state**, not just journal state — the thing journal-only fork structurally cannot give code-kind runs. Strictly opt-in per template; `in_place`/`worktree` stay the defaults; **NO remote/cloud deploy modes** (local-first, single-user).

---

## 5. Session Ownership & Truthful Run Lifecycle

### 5.1 Session ownership

- A run may OWN sessions: stage spawns run under `workflow:<run_id>:<node_id>` session keys — a new source prefix registered alongside the existing conventions (`loop-<id>` — note: hyphen, not colon — `cron:<id>`, `subagent:<id>`, `dashboard:`). Two seams must both learn it: `session_map`/SEL — **`sel._infer_source` has no workflow value today** (sel.py:425), so a `workflow` source is added there — and behavior keying, which follows the loop precedent of setting `session._app = "workflow"` on the session (the gateway keys behavior off `_app`, NOT the key prefix — the `loop_`/`loop:` prefix-match in `context.py:_prompt_use_case_for` is a known near-miss; do not repeat it).
- A run may be LAUNCHED FROM a session: blocking runs mirror a completion summary into the originating chat; temporary/incognito origins inherit write-suppression. **Mechanism (corrected):** there is no `LearningGate` class — the run's owned session keys are marked via the existing `session_restrictions.mark_incognito`/`mark_temporary` process-global registry AND the durable `memory_mode` metadata line in each owned session's JSONL head (the registry forgets on restart; the JSONL line is what history consolidation re-derives from). Suppression is then enforced by the existing per-consumer checks (chat_runner learning gates, `after_turn_review.should_review()`, history consolidation, mcp_memory) — plus the engine skipping `knowledge_persist`/learning stage nodes outright when the run carries the inherited flag.
- **Scope note:** converting EXISTING `loop-<id>`/`cron:<id>` sessions to run-ownership touches `session_map.py`, `sync_bridge.py`, and Slack-thread bridging — load-bearing, actively-changing code. This plan does NOT convert them; legacy conventions stay until their owners (loops, crons) retire on their own plans' timelines. Only NEW `workflow:` keys are introduced here.

### 5.2 Truthful run-state lifecycle (R7 backend half)

The Work board must never lie after a crash:

- **Queued-before-slot:** the WorkflowRun record is written BEFORE acquiring any concurrency slot — the board distinguishes queued vs running vs deferred.
- **Zombie sweep with substrate check:** on gateway boot, a sweep marks stale `running` runs `aborted('server restarted')` with a +60s trigger stagger — mirroring the existing boot recoveries (`reap_orphaned_loops`, subagent `_reconcile_orphans` PID-probe + tombstone). **Suspended refinement (batch-5, Air):** before sweeping, check workspace/substrate liveness — isolated-workspace runs (worktree/container) whose substrate survived the restart become **`suspended`** with a Resume affordance in the Work board's state grouping, not zombies. The sweep applies only to runs whose execution substrate actually died.
- **Lost detection:** a distinct `lost` status via per-runtime liveness checks with a periodic reconciliation sweeper (the subagent reaper's 60s cadence is the precedent).
- **Projection honesty:** journal-derived projections carry completeness metadata (`complete|inferred|partial|error`).

---

## 6. FE — Project Hub + Run Cockpit

### 6.1 Project hub (evolves existing project pages) — per-project tabs

- **Work**: state-grouped rows — *Needs input* pinned first, then Working / Queued / Suspended / Ready for review / Done (the Jules/Claude-agents board anatomy + §5.2's truthful states). Rows = runs + legacy loops + tasks, with cheap-model one-line summaries, §1.5 claim badges, and §4.1 `origin: subagent-tool` runs collapsed by default. Housekeeping/heartbeat runs are suppressed from attention indicators.
- **Needs-input inbox (R1) — the decision queue.** A cross-project Work inbox aggregates needs-input items across everything — the single glanceable surface for all background agency. Contract:

  ```
  NeedsInputItem {run_id, project_id, node_id,
                  block_kind: needs_input|capability|transient|approval,
                  blocker, attempted, evidence,          # evidence = §2.3 bundle inline
                  recommendation, choices[],             # ONE decision per card
                  resume_token, created_at, expires_at}
  ```

  Needs-input/approval are **reified as first-class journal event types** (`{status, phase, message}` folded per-run) so the inbox is a **pure projection of journal events** — correct across restarts and coexistence with legacy loops (whose `needs_input` status adapts into the same item shape). Replies route back to the blocked node via the `resume_token` as a typed threaded signal (request/response, replyTo) — never ad-hoc notification rows. The reply contract carries `permission_suggestions` + `updated_input` so approvals can **modify-and-approve**, not just allow/deny. **Owner binding:** only the requesting session/user can satisfy an item from a shared surface (anti-hijack for Slack-surfaced gates — checked against session_map ownership). Every pause point persists a verbatim `next_step`; reopening a paused run shows a **resume handshake** (workflow + current stage, last completed stage + timestamp, next_step, options: continue / review first). Staleness re-notify (>24h), digest-batching of report-only notices, count pills on project cards. A second lane holds **Open Decisions** attached to completed runs — answering one offers fork/re-run from checkpoint. Background completions surface via push+signal drained at safe points (no unsafe mid-stream injection). The three-state classification (working / needs-input / done) drives OS-level notifications **through the existing gate** — `state.notify()` on DashboardState, filtered by `notification_allowed()` (mute-all/severity/quiet-hours); there is deliberately NO new delivery backend (pluggable notification delivery is an explicitly absent, "future design" seam). **Batch-5:** the NeedsInputItem payload stays fully self-contained (count + one-click approve/deny deep-linking the resume_token) so a future menu-bar/tray micro-surface can render it without the SPA — that micro-surface is a **NEW-plan candidate**, explicitly not built here. Mid-run agent Q&A renders as typed question cards in the progress widget (one decision per card).
- **Artifacts**: the existing artifacts list scoped to the project, now including run outputs, lineage groups (§2.2a), evidence bundles, outboxes.
- **Sessions**, **Context** (brief, `agent_instructions`, overview.md, wayfinder ledgers, context_dir, worktrees, secrets presence flags) — plus the **handoff snapshot projection** (R6): current focus, blockers, ordered next actions, risks/gotchas, generated from run/journal state.
- **Local-first rendering (R12):** hub and cockpit render from local projections immediately (`useCachedData` stale-while-revalidate with `{persist:true}` — the existing seam) and merge live engine status as a soft overlay that is not a hard dependency for initial rendering — the hub stays usable while the gateway restarts, exactly when the user most wants to see run state. A **"rebuild projections" repair action** recomputes the Work board / needs-input counts from run/task state (cached with explicit invalidation) so projection bugs are self-healing.

### 6.2 Run cockpit (successor to the Loop cockpit, reusing its bones)

Live node tree, journal timeline, checkpoint rail with fork buttons, gate cards showing the exact pending question with inline reply (the NeedsInputItem card, same component), plan artifact with comment-to-revise, spec editor (the graph is a VIEW over the spec file on disk — eject-hatch principle), Proof section (§2.3), code-run diff/review panel + reintegration verbs (§4.1). New observability (R5/R9):

- **Attempt ledger:** per-node `{attempt, action, outcome, error_signature, tokens_used}` for loop/retry nodes.
- **Said-no metrics:** per-run and per-template gate statistics (rejects vs passes, retries consumed, budget vs cap); templates whose gates never rejected over N runs get a visible **warning badge** — 100% pass rate is statistical proof of a fake check, computable from the Run Ledger.
- **Health metrics:** Verified Completion Rate, **verification debt** (% nodes completed without executed evidence — the number LEARNING-FLYWHEEL's evaluator-optimizer consumes from this surface), rebuild cost on resume.
- **Retry guards:** circuit-break a node after `failure_limit` consecutive failures; respawn guards skip auth-blocked/recently-succeeded/pending-review states.
- **Live touched-items feed:** knowledge entries and artifacts mutated by the run surface as click-through items as they happen — the trust mechanism for unattended supervision.
- **RunStats (R9), a pure journal projection:** every run carries `{firstByteAt, promptBytes/outputBytes/deltaCount, token + cache-token splits, resolved model/agent, costUsd}`, stamped onto published artifact versions. Journal node results carry self-describing metadata (processingTime, providers/tools hit) so the cockpit renders a live per-node cost/latency strip — no separate instrumentation layer. Handoff artifacts between stages render as first-class journal entries. **Trajectory replay:** per-stage resolved prompt + tool calls + context snapshot as a debuggable timeline; per-run spend-vs-budget; hub template cards show p50/p95 cost + duration across runs of that template — answering "what is costing money" and feeding LEARNING-FLYWHEEL the run economics.
- **Multi-worker readiness:** for runs with concurrent stage workers, the "ready" indicator gates on the coordinator; individual stragglers fail visibly per-slot with stable pinned identity colors.

### 6.3 Live adoption + streaming honesty (R7 FE half)

- A **session-key equivalence helper** treats run-scoped keys (`workflow:<run_id>:<node_id>`) as their base project/chat key so the cockpit and chat widget ADOPT in-flight runs live and auto-reload projections on run end (ClawX shipped this exact fix after strict key equality silently dropped trigger-run events).
- Streaming state is **keyed by run id** (never "the active run"); any persisted `running` status rewrites to stale/reconnecting on FE rehydrate.
- **FE plumbing reality:** new run lifecycle events on the per-run SSE stream MUST be added to the stream hook's event union (`useRunStream.ts RUN_LIFECYCLE` precedent — EventSource silently DROPS unregistered event types); hub-level liveness rides the ONE multiplexed `/api/ws` where envelopes are **refetch signals, not payloads** (DashboardLive contract) — new hub signals extend that debounced-refetch map, they do not carry payloads.

### 6.4 Introspection checklist (R6 — the definition of "glanceable")

The hub + cockpit must answer, **from structured state alone**: what is running now and why; what changed; what is blocked; what needs my approval; what failed; what is costing money; what is risky; and "what will you do next if I say nothing" (each project surfaces its queued-next work explicitly). This checklist is promoted to Success Criteria and doubles as the validation-cycle script for the implementation sessions.

### 6.5 Compact affordances (R13 — deferable behind the core cockpit)

(a) Cockpit header chip-per-node ribbon (running/succeeded/skipped/failed/substituted; click jumps to detail) with event replay on WS reconnect; (b) Work board multi-select → split cockpit view of concurrent runs (cap ~4, oldest evicted) backed by a bounded per-run replay ring buffer; (c) foreach task-projections materialize leaf items as each completes (inspectable/actionable mid-batch), not only when the node closes; (d) **pin-to-dashboard, adapted to reality:** the dashboard has NO tile registry — the bento grid + per-user layout persistence were deliberately retired; widgets are hard-imported in `DashboardPage.tsx`. So pinning an artifact registers it in a pinned-artifacts list (entity_settings-style JSON), rendered by **one new hard-imported `PinnedArtifacts` dashboard widget** (the established pattern: one component in `pages/dashboard/widgets/`), optionally with an attached refresh trigger. No per-tile registry is invented.

### 6.6 Chat + nav

- **Chat run widget** (v2 Slice 5) expands to the cockpit; gates answerable inline in chat (NeedsInputItem cards) — chat stays primary. Detection follows the SdlcProgressCard precedent (tool-name + deep-link-in-output match; the card owns its own polling with cadence read from the just-fetched status).
- Loops nav folds into the Project hub during coexistence (redirect for muscle memory; the Projects tile already carries the loops badge + highlight).

---

## 7. Disposition Summary

| Component | Verdict |
|---|---|
| `Project` / tasks hierarchy | **KEEP** umbrella + 7 extensions (§1) |
| `artifacts/` entity | **KEEP — the one Artifact noun**; runs register outputs, evidence bundles, results ledgers into it |
| `SubagentManager` + persistence | **KEEP** (the substrate); batch leaves gain the R2 hardening contract |
| `subagent_run` single | **KEEP** as-is |
| `subagent_run` batch | **COMPILE** to implicit run (N≥2) with the §3 hardening contract |
| `mcp_subagents.py` batch plumbing | **ABSORB** into the compile path |
| `loop/worktree.py` | **REUSE** as the worktree-mode backend of the §4.1 workspace block |
| `save_credential`/credential_store | **EXTEND** with the per-project keychain namespace (§4.3), not replaced |
| orchestrator_skill | **KEEP, SEPARATE**; roster = slug-keyed drift-checked catalog projection over config agents (R16) |
| Loop cockpit FE | **REUSE bones** for the Run cockpit |
| `session_restrictions` registry + `memory_mode` JSONL line | **KEEP** — the incognito mechanism runs inherit (no LearningGate class exists) |
| `state.notify()` + `notification_allowed()` | **KEEP** — the ONLY notification delivery gate; no new backend |
| Legacy session-key conventions (`loop-`, `cron:`) | **KEEP** until their owners retire (explicitly out of scope here) |
| Dashboard tile registry | **DOES NOT EXIST — not invented**; one hard-imported PinnedArtifacts widget instead |
| Menu-bar/tray micro-surface | **NEW-plan candidate** (NeedsInputItem payload kept self-contained for it) |

---

## 8. Migration Order

1. Project threading (`project_id` + `/work` endpoint **with per-section isolation** + brief/context bindings) — lands with v2 Slice 1 (already an acceptance criterion). Queued-before-slot + zombie/suspended/lost lifecycle (§5.2) land here too — they are engine-record semantics.
2. Workspace block (§4.1: worktree/in_place/scratch modes, preserve_patterns, setup/teardown idempotency, locking bundle, run-owned resource cleanup) + folder contracts (§4.2) — before any FE, since the batch compile depends on it.
3. Artifact publishing from stage nodes (`publish:` declaration → registry upsert) + integrity mechanics (§2.2) + evidence bundle/handoff report contracts (§2.3) + results-ledger kind.
4. Project hub Work tab (read-only board over runs + loops + tasks, local-first rendering, claim badges) + NeedsInputItem journal-event reification + cross-project inbox (§6.1).
5. Run cockpit (after v2 Slices 2+5: checkpoints + widget exist) with attempt ledger, RunStats strip, Proof section, live adoption helper (§6.2-6.3).
6. Batch `subagent_run` → implicit-run compile **with the §3 hardening contract** + leases + agent-roster catalog + drift check + orchestrator-skill text update (same commit).
7. Living overview + instructions injection (§1.2), wayfinder ledgers (§1.5), memory locality/knowledge tagging (§1.6), per-project secrets (§4.3).
8. Project export/import + `projects` snapshot/portability components (§1.7); file drop/outbox (§2.5); cockpit compact affordances + PinnedArtifacts widget (§6.5).
9. (Deferred, opt-in) container workspace mode + snapshot-anchored fork (§4.4).

## 9. Risk Register

| Risk | Mitigation |
|---|---|
| /work aggregates five heterogeneous stores incl. actively-changing legacy loop code | Per-section try/catch isolation + typed loading skeletons + local-first hub rendering (R12) |
| Reply-goes-nowhere on needs-input items | Typed resume_token routing as journal request/response events; inbox is a pure journal projection (R1) |
| Cross-branch contamination / recursive orchestration blowup / credential leakage in batch runs | §3 hardening contract: isolation-by-default, dual depth enforcement, secret-filtered leaf env, single-Write-holder lint (R2/R19) |
| Worktree isolation useless without local config | preserve_patterns copy-in (the emdash adoption-critical detail) + per-project secrets store for the credential half (R3/R19) |
| Verifier theater (gates that never reject) | Said-no metrics + warning badge + verification-debt metric from the Run Ledger (R5) |
| Zombie sweep killing runs whose substrate survived | Substrate-liveness check → `suspended` + Resume, not aborted (R7 batch-5) |
| Refinement runs spamming the 50-snapshot artifact window | material-change version gating + change_note (R10) |
| Concurrent co-tenant sessions double-executing work | TTL'd claim leases on tasks/runs + leased batch leaves (R8) |
| Memory/knowledge conflation | §1.6 holds the boundary: memory = cwd-partition seam (harness internals); knowledge = global user-item store with project_id metadata only |
| Import-time attacks from untrusted project ZIPs | manifest sha256 + path-safety validation + tmp-extract janitor; secrets never travel (R15) |
| Untrusted project-local templates/agents | confirm-before-first-use, install_guarded posture (R14) |

## Implementation Effort

- **9 sessions** (was 4; R1-R20 roughly double the mechanism surface), interleaved with v2 Slices 4-5 rather than strictly after. Sessions map 1:1 to Migration Order steps 1-8, with step 9 (container mode) explicitly deferred and unbudgeted until the rest has landed.

## Success Criteria

1. Every run belongs to a project; the hub's Work tab shows runs, legacy loops, and tasks in one state-grouped board — including queued, suspended (with working Resume), and claimed states; the board is truthful across a gateway kill (no phantom `running` rows after boot).
2. A batch `subagent_run(tasks=[...])` call from chat produces a live widget, survives a gateway restart, and its branches are individually retryable — AND each branch ran in an isolated workspace, returned schema-validated typed output, executed with a secret-filtered env, and held a lease (no double-execution under a concurrent co-tenant session).
3. A run output declared `publish: {artifact: report}` appears in the existing Artifacts UI, versioned **only on material change** with a change_note, with typed lineage deep links back to the producing node and to what informed it.
4. The needs-input inbox surfaces a gated run, an attention-state loop, and a blocked task in one list — each as a decision-ready NeedsInputItem card (blocker, attempted, evidence bundle inline, recommendation, one decision) whose reply resumes the exact blocked node via its resume_token; a >24h-stale item re-notifies through `notification_allowed()`.
5. A blocking run launched from an incognito session writes nothing to knowledge/learning stores — enforced via `session_restrictions` marks + `memory_mode` session metadata + engine-level skip of learning stages, and verified after a gateway restart (durable, not registry-only).
6. **Introspection checklist (R6):** from the hub + cockpit alone, an evaluator can answer: what is running now and why, what changed, what is blocked, what needs approval, what failed, what is costing money (RunStats strip + template p50/p95 cards), what is risky, and what happens next if the user says nothing.
7. A code-kind run provisions a worktree with preserve_patterns + idempotent setup, survives resume (setup re-runs safely), tears down cleanly BEFORE workspace deletion, and its cockpit diff panel + Apply Locally/Checkout Branch verbs work end-to-end.
8. A completed unattended run presents a Proof section (evidence bundle + terminal handoff report) sufficient to review it without opening the transcript; a template whose gates passed 100% over N runs shows the fake-check warning badge.
9. Exporting a project and importing it on a clean home yields brief, overview, ledgers, templates, artifact metadata, and run digests intact (sha256-verified), with zero secrets in the ZIP; the new `projects` snapshot component round-trips.

---

## Amendment (2026-07-29 — owner-commissioned evidence review: fan-out topology)

**Provenance.** The owner asked for audits + industry research before planning any Wide-Research-style fan-out, explicitly declining to plan from vendor assertion. Two investigations ran: a code audit of the as-built subagent path, and an evidence review of the multi-agent-topology literature (papers with measurements, engineering write-ups with numbers, and benchmark comparisons, each labelled by whether it was *measured* or *asserted*). This amendment records what the evidence settles, what it does not, and the resulting contract changes. **§3's core decision is unchanged and reconfirmed** — one substrate, two front doors, batch compiles down. What changes is the *shape* of a fan-out and three concrete safety/attribution gaps the audit exposed.

### What the evidence settles (act on these)

1. **Do not build a role taxonomy for fan-out.** The best-powered direct test of personas (162 roles, 4 model families, 2,410 questions, EMNLP 2024) found **no improvement**, with per-persona effects "largely random." Two independent studies found personas *degrade* results (7 of 12 datasets; −33% average via socio-demographic bias). Role prompts frequently fail to produce differentiated agents at all (cosine similarity 0.888, effective rank 2.17/3.0 across role-prompted clones). And persona changes cause **bidirectional churn** — one measured case fixes 4% while breaking 18% — which is strictly worse than a uniform loss for an autonomous system because it destroys reproducibility. **[V]**
2. **Roles are not where the reliability problem lives.** "Disobey role specification" is the **2nd-rarest of 14 measured failure modes (1.5%)** across 1,642 annotated traces (κ=0.88). The failure budget goes to **verification (~23.5%)**, **step repetition (15.7%)**, and **specification drift (11.8%)**. Investment should follow the failures, not the org chart. **[V]**
3. **What actually helps, where "roles help" results are real, is the instruction content and the model tier — not the identity.** ChatEval's identical-role arm scored *exactly* the single-agent number (53.8) while diverse roles hit 60.0 — i.e. the delta came from differing *instructions*. Heterogeneous **model**-per-role teams gained **up to 44% accuracy at matched cost** (or matched the best homogeneous team at 12× lower cost). Conversely, with the same model everywhere, a well-prompted single agent matched homogeneous workflows across seven benchmarks. **[V]**
4. **The "fabrication threshold at 8–10 items" is not a thing, and was never published as a claim.** The report read the primary source directly: no such claim exists — it was a secondary-source artifact this plan must not encode. Measured degradation knees sit elsewhere entirely (~500 input tokens; ~570–670 residual-task tokens; batch size 2; or nowhere), and frontier models held quality to 32 batched and 100 enumerated items. The real mechanism is **task-budget displacement, not confusion**: coordination content *added* rather than displacing task content scored 150/150 correct even at a 95% coordination ratio. **[V]**
5. **Multi-agent frequently loses under fair evaluation.** In the largest unified re-benchmark (MASLab), on Qwen-2.5-72B only **2 of 9** multi-agent methods beat the single agent and **none beat plain self-consistency**. The cleanest same-model head-to-head favors the simpler scaffold: Agentless **32.00%** vs SWE-agent **18.33%** on SWE-bench Lite with the same GPT-4o, at ~28% of the cost. Fan-out costs 4–15× for single-digit gains *where it helps at all*, and the best cost outcomes in the whole literature come from **pruning** communication (AgentPrune 7.8× cheaper; G-Designer 95% fewer tokens), not adding it. **[V]**
6. **Cognition publicly reversed.** "Don't Build Multi-Agents" (2025) is partly retracted by "Multi-Agents: What's Actually Working" (2026-04-22, same author). Their current rule: **"writes stay single-threaded and the additional agents contribute intelligence rather than actions."** They now *prefer* an **unshared-context** reviewer (a blank-slate reviewer must reason backward from the implementation, and skips the coder's extraneous context) — materially retracting their own "share full agent traces" principle. Anyone citing the 2025 essay as Cognition's position is out of date. **[V that they wrote it]**
7. **The decision variable is coupling density, not item count** (r=0.65, p<0.05). Naive file-parallel fan-out bought **+0.9 points for +44% cost** on coupled work. **[V]**

### What the evidence does NOT settle (do not pretend otherwise in any task line)

- **The two clean iso-compute studies contradict each other.** Best available reconciliation: parallel *sampling* scales with budget; parallel *decomposition* does not. This is a genuine unresolved disagreement, not a gap in our reading. **[V]**
- **Anthropic's +90.2% is a real measurement but not compute-matched** (~3.75× tokens), on a private eval, on the task shape that maximally favors fan-out — and their own regression says **token usage alone explains 80% of variance**. Do not cite it as topology evidence.
- **The linear-context claim and the homogeneity claim both have zero measurements.** Output-format/consistency variance across independent parallel agents — precisely the linear-context argument — **is untested anywhere**. So is duplicated work across parallel agents: no paper reports a duplication rate.
- **The noise floor governs everything above:** scorer swaps move results more than architecture does (79.0 → 25.6 in one case), format errors cause >50% of failures in some harnesses, run-to-run variance is 1–3 points, benchmarks run n=24–100, and **no paper token-matches its single-agent baseline**. **Treat any sub-5-point delta as unresolved** — including our own future measurements.

### The synthesis this plan adopts

> **Parallelize reads/analysis. Single-thread writes. Give each worker an explicit objective, output format, and boundary contract. Have one agent merge.**

This is the only position consistent with all the measured evidence, and it is where three independent parties converged (Anthropic measured the read-parallelism win; Cognition retreated to exactly this line; a third independent analysis articulates it) — while MAST shows failures concentrate in system design (41.8%) and inter-agent misalignment (36.9%), the two things this discipline targets. **[P] — a convergence of independent positions, not a measured result.** Labelled honestly because it is the basis for a contract, and a future session must know it is not a benchmark.

### Contract changes to §3 (additive; §3's decision stands)

- **(a) Fan-out workers are HOMOGENEOUS by default; heterogeneity is by MODEL, never by persona.** A `parallel[stage]` leaf inherits the parent's agent binding (today's behavior — `subagent.py:1480`) and receives **no persona prefix**. A stage may pin a *different model* per leaf (evidence item 3 — the one measured heterogeneity win, and MODEL-USE-CASES-V2 already owns per-use-case chains). **No role/persona taxonomy is introduced by this plan, and the existing goal-loop roster (`loop/classify.py`, LLM-generated, capped at 5) is NOT promoted into the engine** — it stays a prompt directive to a loop worker, which is what it is today.
- **(b) The leaf contract is the load-bearing artifact, not the leaf's identity.** Each leaf carries an explicit **objective**, a **declared output format**, and a **boundary** (what it must not touch). This is what evidence items 2 and 3 point at: specification drift and verification are the real failure classes, and instruction content is the active ingredient. §3's "typed leaf outputs" already gestures at this — it is hereby the primary requirement, not a nicety.
- **(c) Writes stay single-threaded.** A `parallel[stage]` leaf is **read/analysis-only by default**; a leaf that mutates shared state must be serialized by the engine. This aligns with §3's existing per-leaf `capability: research|mutating` field — which this amendment promotes from a field to an **enforced** rule: `mutating` leaves may not run concurrently with each other within a run. (Note the audit finding: `capability_class` was specified in AUTONOMY-GUARDRAILS §4.1 but **deliberately deferred there** to "engine per-template profiles / WORKFLOWS-V2" — see its Status section — so this plan is its intended home, and grep confirms it does not exist in `src/` yet. It is unbuilt-as-planned, not drift.)
- **(d) No item-count threshold anywhere.** Evidence item 4 kills the 8–10 heuristic. Fan-out width is bounded by the existing concurrency caps and by **coupling density** (item 7), not by a magic number. Any task line proposing "fan out when N > k" is defective.
- **(e) A fan-out must be measured before it is widened.** Because the literature's own noise floor exceeds most reported architecture deltas, any future widening of the concurrency ceiling requires a token-matched local comparison against the single-agent path on the same work, and a sub-5-point delta is reported as **inconclusive**. This is the honest version of "prove it helps."

### Audit findings that must be fixed before any width increase (blocking)

The code audit found the ceiling is **not** the resource limit — the **result-delivery path serializes and destroys context**. These are prerequisites, not enhancements:

1. **The parent-session injection wall.** Every sub-agent completion starts a full `_run_chat` turn on the parent to deliver one result (`gateway.py:2358-2371`), and the parent holds a per-session `asyncio.Semaphore(1)` (`session.py:154`) — so deliveries are strictly serial, bounded by `INJECTION_TIMEOUT = 300.0` and an outer `_ON_DONE_TIMEOUT = 1200.0`. Worse, **on timeout the handler resets the parent session** (`subagent.py:1396-1403`), wiping the orchestrator's context. A burst of completions today loses most results *and* destroys the conversation that asked for them. **This wall is already reachable at 8 with slow parent turns** — it is a present defect, not a scaling concern. Fix: batch/queue completions into a single delivery turn, and never reset the parent as a timeout remedy.
2. **Queued spawns silently lose parameters and are unaddressable.** The queue tuple is `(task, parent, agent, max_turns, cwd)` (`subagent.py:348-350`) while `spawn()` takes nine parameters — so a queued spawn drops `model`, `approval_mode`, `silent`, and `dry_run`. A queued headless spawn therefore loses `approval_mode="auto"` and re-enters the interactive gate. Queued spawns also return a fake id (`q1`, `q2`) that collides across drains and cannot be polled or cancelled.
3. **`_validate_agent` always returns `("", "")`** (`subagent.py:130-148`), so the `if err:` branch at `subagent.py:1073-1078` is dead code and an unknown agent name **silently downgrades to the default** — a fan-out could run entirely on the wrong agent with no error.
4. **No per-child cost attribution and no fan-out budget.** `subagent.py:1684-1685` is `elif event.kind == EVENT_COMPLETE: break` — the completion event's `input_tokens`/`output_tokens`/`cost_usd` are **discarded**, and `SubagentInfo` has no cost field. The day-scope budget is checked **once at spawn** with no mid-flight re-check, so N children each spending freely all pass a gate that said "you're under the ceiling right now." COST-AND-TOKEN-OBSERVABILITY §C2/T1.3 fixes the attribution half; the **run-scoped budget** belongs here.
5. **No "kill this fan-out."** `cancel(id)` kills one; `cancel_all()` is wired only to gateway shutdown (`gateway.py:2862`); `DELETE /api/spawn` clears *completed* entries without killing running ones (`messaging.py:206-229`). A user cannot stop a runaway fan-out except via the blunt incident switch, which blocks *new* spawns and does not touch in-flight ones.
6. **The session failure-breaker cannot trip for sub-agents.** `record_success` is called on completion (`subagent.py:1709`) but **`record_failure` is never called** for a sub-agent session, so `_CIRCUIT_BREAKER_THRESHOLD = 5` is unreachable there; the only real guards are the 100-turn limit and the 1800s reaper. Note also the **provider breaker is process-global**, so one rate-limited provider opens a breaker for all children at once — a stampede becomes a fleet-wide outage in ~5 failures, with no jitter or per-child backoff.
7. **Concurrency is global, not per-run.** `_running_count` is a single instance-level integer (`subagent.py:344`) with no per-parent scoping. `WORKFLOWS-V2-LOOPS-EVOLUTION:799` already records this exact concern from the other direction ("judge spawns should count against run-level, not global, concurrency") — the code confirms the premise. A run-scoped lane is needed before width increases, or one wide run starves every other.

### Cross-plan note: `SubagentManager.spawn` is a shared-seam this plan should own

`SubagentManager.spawn` is mutated by roughly six plans (`AUTONOMY-GUARDRAILS` `capability_class`, `EXECUTION-ISOLATION` `sandbox`, `MODEL-USE-CASES-V2` the `orchestration` axis, `HARNESS-CRAFT` worktree hydration, `WORKFLOWS-V2` `__wf_depth`, `TASKS-SOPS` foreach children) — it is the single spawn substrate, and yet no plan currently declares it as an owned contract. That makes it an **unregistered convergence point**: six plans reshape one signature with no single owner. Recommend this plan own it as its authoritative contract (its `Contracts & Interfaces` section names the `spawn` signature every other plan extends).

### Amendment task table (extends §Task breakdown; run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

Sessions C1-C2 are **prerequisites for any width increase** and are independently valuable — they fix present defects at today's cap of 3-8.

| ID | Task | Files | Done when |
|---|---|---|---|
| C1.1 | Fix the injection wall: batch concurrent completions into a single parent delivery turn; **remove the parent-session reset as a timeout remedy** (preserve the orchestrator's context on delivery failure — surface the failure instead) | `gateway.py` (`_subagent_done` path), `subagent.py`, tests | 8 near-simultaneous completions deliver without loss and without resetting the parent; a delivery failure leaves the parent's context intact (regression test for the reset) |
| C1.2 | Queue correctness: carry the full spawn parameter set through the queue; issue real addressable ids to queued spawns so they can be polled and cancelled | `subagent.py:348-350`, `1183-1194`, tests | a queued headless spawn retains `approval_mode`/`model`/`silent`/`dry_run`; a queued spawn is cancellable by its returned id; ids never collide across drains |
| C1.3 | Make `_validate_agent` real (unknown agent name = typed error, not a silent downgrade) and delete the now-live dead branch's redundancy | `subagent.py:130-148`, `1073-1078`, tests | spawning with an unknown agent fails with a typed error naming valid agents; no silent default substitution |
| C1.4 | Fan-out control: a **run-scoped** concurrency lane (not global) + "cancel this fan-out" (kill all children of one parent/run) exposed on the API and the UI; `record_failure` wired for sub-agent sessions so the session breaker can trip | `subagent.py:344`, `dashboard/handlers/messaging.py:206-229`, `web/src/pages/chat/ChatActivityPanel.tsx`, tests | one wide run cannot starve others; one click kills a whole fan-out; 5 consecutive child failures trip the breaker |
| C1.5 | Run-scoped **budget** re-checked mid-flight (not once at spawn), composing with AUTONOMY-GUARDRAILS' `SpendMeter` run scope; per-child cost visible (consumes COST-AND-TOKEN-OBSERVABILITY T1.3 — do not duplicate its ledger) | `subagent.py`, `guardrails/budgets.py` (read-side), tests | a fan-out that would exceed the run budget stops mid-flight with a typed reason; per-child cost is visible in the activity panel |
| C2.1 | Leaf contract per (b): explicit objective + declared output format + boundary on every `parallel[stage]` leaf; typed leaf outputs validated against the declared format | engine stage compilation, tests | a leaf without an objective/format fails compilation; a leaf returning off-format output is caught, not silently merged |
| C2.2 | Enforce (c): `capability: research|mutating` per leaf, with `mutating` leaves serialized against each other within a run; homogeneous-by-default leaves with optional per-leaf **model** pinning per (a) | engine, `subagent.py` spawn threading, tests | two `mutating` leaves never run concurrently; a persona-style role field does not exist; per-leaf model pinning works |
| C2.3 | The measurement harness per (e): a token-matched local comparison of a fan-out against the single-agent path on identical work, reporting a **sub-5-point delta as inconclusive** | `harness/` (the repo-root dev package), a documented procedure | running it on a real batch produces a token-matched comparison with an honest verdict incl. "inconclusive" |
| VC | Validation as a user: run an 8-wide fan-out on real work; confirm all 8 results are delivered, the parent keeps its context, per-child cost is visible, one click kills the fan-out mid-flight, and a `mutating` leaf never overlaps another; run C2.3's harness and record the verdict in this plan's execution log (including if it is inconclusive) | — | holds |

### Risks specific to this amendment
- **The strongest evidence argues for restraint, which is an unsatisfying answer.** MASLab and Agentless both say a good single agent is hard to beat, and pruning beats adding. The honest implication: fan-out earns its place for **read breadth, wall-clock, and context capacity** — not as a general quality strategy. Any task that widens fan-out for "better answers" without a token-matched measurement is contradicting item 5.
- **Our own future measurements will sit near the noise floor.** That is why (e) mandates reporting inconclusive results as inconclusive. A plan that only ever reports wins is not measuring.
- **Deferring the roster question.** The goal-loop roster stays as-is deliberately; if a future session wants role-specialized loop workers, evidence items 1-3 say the burden of proof is on that change, and item 3 says the productive version is *different models*, not different personas.

---

## Execution log

### 2026-08-02 — session 46 (project umbrella + truthful lifecycle + Work board) DONE

`workflows/containers.py` (new): the Work board projection with state grouping and claim leases, the
substrate-checked boot sweep, per-section `/work` isolation, the project context block and the
wayfinder ledger contract. `project_context.py` (new): the living overview, the three ledgers, the
injected block, the handoff snapshot. 83 tests across two files.

- **No second umbrella noun.** Project already had the right shape, so this adds only what the run
  engine needs from it. `WorkflowRun.project_id` was ALREADY a column and `resolve_project_id`
  already auto-creates — the binding the plan asks for exists, so the session did not re-derive it.

- **A second project-context composer was NOT created.** `chat_utils._project_context_preamble`
  already assembles the project block for chat sessions; the overview and ledgers were appended to
  IT. A parallel builder would drift, and an agent seeing a different project description in chat
  than in a run gives answers nobody can reconcile.

- **DISCOVERY — two board rules were inert from one wrong type read.** `WorkflowRun.origin` is a
  `RunOrigin` object, not a string. Reading it with `str(...)` produced a dataclass repr, so every
  origin comparison silently failed: no run was ever collapsed, and no origin was ever suppressed
  from attention. Compounding it, the collapse/suppress sets named `housekeeping` and `heartbeat` —
  neither of which exists in `OriginKind`. A rule keyed on a value that can never occur is a rule
  that never fires, and nothing reports it. Both sets are now drawn from the enum, with a test that
  every value in them is a real `OriginKind`.

- **The boot sweep checks the SUBSTRATE before calling anything a zombie.** Marking every stale
  `running` run aborted is the obvious implementation and it destroys recoverable work while
  reporting success. An isolated substrate that survived the restart yields `suspended` + resumable;
  an inline run can never be suspended, because its substrate IS the dead process and offering a
  Resume that cannot work is worse than no affordance. A run already terminal is left untouched —
  re-deciding it would let a boot sweep overwrite a real outcome with an inferred one.

- **`queued` is derived from a record with no `started_at`.** That is what §5.2's record-before-slot
  ordering buys: without the distinction, a run waiting for a concurrency slot is indistinguishable
  from one doing work, and the board reports work in flight that has not begun.

- **An expired claim is not rendered.** A badge naming a holder who no longer holds it tells the user
  the work is taken when it is free. The same holder RENEWS rather than being refused (a worker that
  lost its in-memory state would otherwise be locked out of its own work until the TTL expired), and
  only the holder may release (otherwise a second worker could steal work mid-execution by releasing
  first).

- **Per-section isolation is the `/work` contract, and it is tested by failing a source.** Five
  heterogeneous sources fail independently; a single try/catch around the aggregate would let a
  stale legacy-loop reader take down the run list and the whole first paint. All-sources-failed
  reports `ERROR`, not `PARTIAL` — "partial" on an empty board reads as "there is not much work",
  which is a claim about the user's work rather than about the failure.

- **Overview is current state; the ledgers are history.** Separate files, separate functions: the
  overview is replaced atomically in place, the ledgers are append-only with no update or delete. One
  file for all three ledgers would make every append a read-modify-write of all of them, and a torn
  write would lose two ledgers to fix one. The boundary answer splits on newlines and semicolons but
  not commas, matching S45's prohibitions rule.

- **DISCOVERY — the enriched preamble recommended redundant reads.** Measured live: the overview and
  all three ledgers appeared BOTH as inlined text and in the context-dir listing's "read any for
  continuity", inviting four tool calls to re-read what the agent had just been given. First fix
  excluded them by FILENAME, which broke a pre-existing test for the right reason: a hand-authored
  `decisions.md` that is not in ledger format inlines nothing, so a name-based exclusion would hide
  a file the agent has never seen. Hiding an unread file is the worse failure of the two — a
  redundant pointer wastes a tool call, a hidden file loses the context entirely. The exclusion is
  now content-based, and the listing header tracks whether anything was actually held back.

- **A context READ never materializes a project.** `resolve_project_id` auto-creates; a read path
  that also did would mean a typo'd id invents a project, and opening a project page could create
  one.

- **NOT DONE:** the hub Work tab FE itself (`GET /api/projects/{id}/work` route registration and the
  React board) — the projection is the backend half, and the route belongs with the dashboard
  handler slice; lease FILES on the `concurrency.single_flight` flock convention (the lease record
  and its policy are here; the flock is a storage seam this session does not open); `workflow:` SEL
  source registration and `session._app` keying (§5.1 — it touches `session_map.py`/`sync_bridge.py`,
  which §5.1 itself scopes out); the overview's automatic revision on run completion (needs the
  controller's completion hook, and writing it from a projection would put an engine write in a read
  path). Sessions 47-54 own artifacts reuse, subagent batch hardening, run workspace/environment,
  session ownership, the needs-input inbox, worktrees, introspection and export/import.

### 2026-08-02 — session 47 (artifacts reuse) DONE

`workflows/publish.py` (new): the `publish:` declaration, material-change version gating, typed
lineage, evidence bundles, the terminal handoff report, the append-only results ledger.
`engine.apply_publish` wires it at the SAME dispatch seam `required_artifacts` uses. 57 tests.

- **No second Artifact noun.** The existing `artifacts/` entity is used as-is; this session adds a
  declaration and its rules. `publish:` translates into a registry upsert, and a refinement run
  updates the same artifact BY NAME, which is where the registry's native versioning gives "stable
  name across revisions" for free.

- **A declaration is a promise about output, so a malformed `publish:` FAILS the node.** Degrading to
  "no publish" would let a node whose author declared a deliverable report success while producing
  nothing — the completion-lie class `required_artifacts` exists to catch. The deliberate asymmetry:
  a REGISTRY failure does not fail the node. A bad declaration is the author's bug (fail loudly); an
  artifact-store outage is the environment's (degrade honestly, keep the completed stage).

- **Material-change gating protects a finite resource.** The registry keeps 50 snapshots; a
  five-round refinement loop publishing each round would consume the window in five runs, so the
  window that exists to hold real revision history would hold near-duplicates. Verified live: three
  publishes of which one was identical produced exactly two versions. A NOOP is a first-class
  outcome, not a failure — "nothing material changed" is the right answer for a converged loop, and
  reporting it as an error would make convergence look broken. Publishing an EMPTY body over a real
  one is refused outright: that is the one publish that destroys work while bumping the version.

- **DISCOVERY — provenance was computed and discarded.** Driving the real registry showed the plan
  computing a full run/node lineage while the artifact landed with `event.metadata == {}`, no
  `run_id` anywhere on disk. `ArtifactEvent.metadata` and `clean_event_metadata` BOTH already
  existed, but only `record_impression` could reach them — no create/update path exposed the
  parameter. Added `event_metadata` to `create`, `update` and `create_binary` (a named parameter, not
  an open passthrough), and wired `update()`'s event write, which was constructing its
  `ArtifactEvent` without metadata at all.

- **DISCOVERY — the nested lineage dict was stringified into an unparseable repr.**
  `clean_event_metadata` bounds event metadata to string-keyed scalars ≤256 chars — a deliberate size
  bound. Passing the lineage dict through produced `"{'informed_by': ['knowledge:item-7']}"`, a
  Python repr no reader can parse. Widening the sanitizer would loosen a bound that exists on
  purpose, so the lineage flattens to scalar `lineage_<edge>` keys instead; `flatten_lineage` and
  `parse_lineage` ship together so the format is one decision in one place. Verified round-tripping
  through the real registry.

- **DISCOVERY — the publish outcome needed a DECLARED `NodeResult` field.** Setting it as an ad-hoc
  instance attribute works at runtime and never reaches the journal, so the ledger would show a
  published artifact with no record of the publish. A string output stays reachable at its original
  binding path — wrapping it in a dict would break every `{{nodes.x.output}}` downstream, so
  publishing a node's output would silently change what its consumers read.

- **Typed lineage, not a flat link list.** SOURCE is provenance, INFORMED_BY is evidence, RELATED is
  navigation; a reader following an untyped edge cannot tell which question they are answering, and
  the provenance edge is the one an audit has to trust. An unknown edge type is refused rather than
  stored.

- **Evidence bundles and the handoff report are Artifact compositions.** Bundle files are sorted by
  name so two runs producing the same evidence produce byte-identical manifests — an unstable order
  would make every bundle look changed to the material-change gate, defeating it for the artifact
  kind that is re-published most. Every handoff section is present even when empty: an absent
  `side_effects` reads as "nothing was committed or sent", which is the claim a user most wants to be
  true and least wants guessed. A skip with no reason is flagged, because the reader cannot tell a
  deliberate omission from a silent failure without re-doing the work.

- **The results ledger keeps reverted attempts and repeated attempt numbers.** An attempt log that
  dropped failures would make a five-attempt convergence look like a first-try success, and the next
  run would repeat the four failures. Two rows for attempt 3 means it was re-run; collapsing them
  would hide the retry, which is the most useful thing the ledger records.

- **NOT DONE:** media self-containment (copying referenced local files into the version dir with
  content-hash names) needs the artifact version-dir write path, which is registry-internal; the
  cockpit's structured version diffs and multi-view output tabs are FE work on the
  `contentTypes.ts` registry; the per-run file drop and outbox need the multipart ingestion route and
  its approval gate. Sessions 48-54 own the rest of section F.

### 2026-08-02 — session 48 (subagent batch hardening) DONE

`workflows/batch_compile.py` (new): the N≥2 threshold rule, capability classes, the single-writer
lint, static depth rejection, typed leaf outputs compiled to the engine's existing `output_contract`,
the lineage env, and the safety-filtered recall view. 52 tests.

- **The threshold rule is the ergonomics.** N=1 stays a raw spawn — "go check X while I keep chatting"
  is chat-native delegation, and a run record plus project resolution on it is ceremony the personal
  feel does not survive. N≥2 compiles and gains the journal, per-branch retry, resume-after-restart
  and fork.

- **DISCOVERY — per-leaf error isolation is NOT the default, contrary to my first implementation.**
  I emitted `on_error: "continue"` and wrote a comment claiming the container default already
  isolated failures. Driving `derive_state` showed the opposite: a `parallel` with `join: all` (the
  default) goes FAILED the moment one child fails, so one bad leaf would have sunk a five-way
  fan-out — the exact inverse of "four still return". And `"continue"` is not a value the engine
  recognizes at all (it checks `fail_run`, defaulting to `null_continue`). The compile now emits
  `join: quorum, quorum: 1`, measured: DONE with one leaf failed, FAILED when all fail.

- **DISCOVERY — four emitted config keys were read by nothing.** `workspace`, `capability`,
  `tool_posture` and `timeout_secs` all went into node config; a grep of the engine found no reader
  for any of them. In a module whose entire subject is least-privilege, keys that look like
  enforcement and enforce nothing is the worst version of this bug — a caller reads
  `"read_only": true` in the spec and believes it. Unenforced declarations now travel in a separate
  `postures` map, and `CompileResult.unenforced()` names which seam each one still needs (the
  tool-handler depth flag, the §4.1 workspace block, and the fact that the engine's node timeout is
  per-RUN with no per-node override to bind to).

- **Typed outputs compile into the EXISTING `output_contract`.** The engine already validates it
  before any binding resolves; a second validator over one field would disagree eventually, with the
  one that ran last winning silently. Schema fields with no contract equivalent are DROPPED rather
  than approximated — an approximated check that passes malformed data is worse than no check,
  because it is believed.

- **Capability classes default to `research`** — the safe direction to be wrong in. A research leaf
  that needed to write fails visibly and is re-declared; a mutating leaf that only needed to read has
  ambient write access nobody asked for. The write-marker list is deliberately over-inclusive so a
  newly-added write tool is denied by default; an allowlist of known writers would silently admit
  every tool added after it was written. Orchestration tools are denied at EVERY depth, because a
  leaf that can spawn can fan out without a budget and the depth counter alone would let it happen
  once per level.

- **Static depth rejection, not just a counter.** Today's no-recursion rule is prompt-level, so a
  leaf that decided to fan out again would succeed once per level before any counter noticed. The
  depth check also fires below the compile threshold: a single task at depth is still a spawn a leaf
  should not be making, and returning `compiled: False` with no finding would read as "nothing to see
  here".

- **A research leaf declaring writes is an ERROR, a multi-writer collision is a WARN.** The first is a
  contradiction only the author can resolve; the second may be disjoint regions of one directory, and
  refusing would block a legitimate fan-out — but it must be said, because two workers writing one
  file lose an update while both report success.

- **The recall view fails CLOSED.** If `security.redact` raises, the view is withheld rather than
  shown unredacted: failing closed costs the projection, failing open costs a credential. Redaction
  goes through that existing chokepoint rather than a local pattern set, because a second redactor
  drifts and the drift shows up as a credential in a UI.

- **NOT DONE:** the actual `mcp_subagents.subagent_run` cutover to call `compile_batch` — the compile
  and its contract are the mechanism, but routing the tool through it needs the spawn path to apply
  the postures, and applying them needs the tool-handler seam that does not exist yet (§3 says so
  explicitly: "NOT per-context tool filtering, which doesn't exist"). Shipping the route without the
  seam would replace a working fire-and-forget batch with one that claims least-privilege it does not
  have. Also not done: TTL-bound scoped file sessions, the sibling-awareness wrapper, N-variant
  comparison metrics, and the agent-roster projection + drift check (R16) — the roster is a
  `config.json agents{}` projection, which is a Platform-Legibility-shaped change, not a batch one.

### 2026-08-02 — session 49 (run workspace + environment) DONE

`workflows/workspace.py` (new): the `workspace` provisioning block, reserved-var rejection, the
secret-filtered spawn env with presence-only flags, and tolerant `.folder.yaml` contracts. 72 tests.

**ARCC was not queried (MCP server unavailable in this session).** Standard practice applied instead:
secrets stay in the existing credential seam (`{{secret:KEY}}` + the credential store), values never
reach a run record/journal/UI, and every surface shows presence flags only.

- **`scratch` is the default mode and `in_place` is never one.** Being wrong about isolation should
  cost a copy, not the original — `in_place` is the mode in which a destructive step runs against
  real state, which is the shape of the deleted-real-model incident. An unknown mode is FATAL rather
  than defaulted, because defaulting would silently run in a mode nobody chose and the modes differ
  in exactly the way that matters.

- **A greedy `preserve_patterns` entry is refused.** `**` copies the whole tree into the workspace it
  is being isolated from, which defeats the isolation. But real patterns matter: a worktree with no
  `.env` is one where every build fails, and a user whose first isolated run cannot install
  dependencies concludes isolation is broken rather than unconfigured.

- **DISCOVERY — the shared secret-hint list missed provider-specific credential shapes.** Wiring the
  env filter measured `GITHUB_PAT` as NON-secret, so a run declaring inherit-from-host would have
  passed a GitHub personal access token straight into a leaf's environment. A hint list is only as
  good as its worst-covered credential, and bespoke names are exactly what a generic list misses.
  Widened `SECRET_KEY_HINTS` (the SHARED list in `secrets.py`, not a second one) with `_pat`, `pat_`,
  `session_key`, `access_key`, `refresh`, `signing`, `webhook` — then checked the other direction:
  `PATTERN_FILE`, `COMPATIBILITY`, `NODE_ENV`, `PORT` and `LANG` all still read as non-secret.

- **An ungranted secret is ABSENT, not empty.** An empty string reads to a child as "this credential
  is configured and blank", producing an authentication failure rather than a
  missing-configuration error — the first is far harder to diagnose. Withheld keys are RETURNED so
  the cockpit can say "2 declared secrets were not granted" instead of a child failing invisibly.

- **`{"VAR": null}` means inherit, which is a third state.** Different from omitting the key (absent)
  and from `""` (set and empty). Inheritance is still FILTERED: a host var that is itself a secret
  must be granted explicitly, or "inherit my environment" becomes a blanket credential grant.

- **A reserved env var is REJECTED, not overridden.** `HOME`, `PATH`, `PYTHONPATH`, the `XDG_`/`LD_`/
  `DYLD_`/`PERSONALCLAW_` prefixes: redirecting any of them relocates every config file, credential
  store and binary the system resolves through them — including the machinery enforcing every other
  rule in the module.

- **Order is the provisioning contract.** preserve → setup → run, and teardown → delete. An
  `npm install` that runs before `.npmrc` is copied in reaches for the wrong registry; a teardown
  that runs after deletion runs against a directory that no longer holds the services or artifacts it
  was meant to stop and sync. Setup is marker-guarded and content-addressed, because setup runs on
  EVERY resume by contract — a `git clone` that re-runs fails, and a setup block that fails on resume
  makes resume unusable. A marker keyed by index would skip an edited step as though it had run.

- **Folder contracts are tolerant by construction.** Every problem is a warning; an unparseable
  contract yields defaults plus a warning, because the alternative is a directory that becomes
  unusable over a typo in a metadata file. Unknown fields are KEPT, not dropped — a round-trip that
  lost them would corrupt a newer app's contract when an older core rewrote the file (the
  23-of-25-dropped-memories class). `transient` is the fallback lifecycle: content that should have
  been permanent and got cleaned is recoverable from the run that made it, while content that should
  have been transient and persisted is a leak nobody notices. `agent_writable` defaults to False,
  because defaulting to writable would make every folder that forgot to declare a permission an open
  one — and forgetting is the common case. `immutable` + `agent_writable` resolves toward the safety
  declaration and reports the conflict.

- **NOT DONE:** actual provisioning I/O — `plan_provisioning` returns the ordered plan and the caller
  performs it; wiring it into the controller's run-start path is a controller change this session
  does not open. PID-liveness lock files outside the workspace, `preserved_workspace_path` on the run
  record, reuse-with-safe-ff-only-refresh for named workspaces, and the Apply Locally / Checkout
  Branch verbs need the run-record schema and the code cockpit (session 52 owns worktrees). Container
  mode (§4.4) is opt-in and explicitly deferred to last by the plan. The two new config defaults
  (`workspace_default_mode`, `workspace_teardown_on_expiry`) are not wired: the four-point config
  contract needs a `WorkflowsConfig` field plus `_EDITABLE_CONFIG` plus an FE control, and adding a
  knob nothing reads yet would be the inert-control class this program keeps finding.

### 2026-08-02 — session 50 (session ownership + incognito enforcement) DONE

`workflows/ownership.py` (new): run-owned session keys, the two seam registrations, incognito/temporary
inheritance, and the engine-level learning-node skip. `sel.py` + `context.py` each gained one prefix
entry; `engine.dispatch_stage` gained the skip and the owned parent key. 79 tests.

- **Two seams had to learn `workflow:`, and BOTH were fixed at the function that is actually called.**
  `sel._infer_source` is what `log_tool_call` invokes, so a helper elsewhere returning "workflow"
  would have been a parallel path the audit log never consults — every run-owned tool call would
  still record as `channel`, the catch-all, making "what did the run do" unanswerable from the log
  even though every event is in it. Same for `context._prompt_use_case_for`: without the entry an
  owned session resolved to the `chat` prompt, framing a stage worker as a conversational assistant.
  Both verified with a no-regression sweep over every existing prefix.

- **Behaviour keys off `_app`, not the key prefix** — verified in code (`gateway.py` and
  `chat_runner.py` both branch on `session._app == "loop"`). The `loop_`/`loop:` prefix-match in the
  prompt resolver is a known near-miss the plan says not to repeat, so ownership sets `_app` and the
  key is only an identifier.

- **DISCOVERY — the engine helper called a PHANTOM API.** The first version used `store.load()` and
  read `run.memory_mode`; NEITHER exists (`store.get` is the real reader, and there is no such
  field). Wrapped in a best-effort `except`, that would have raised on every stage and been
  swallowed — an enforcement control that silently never fires, which is the exact class this program
  keeps producing. The mode now lives in `WorkflowRun.extra` (already persisted and round-tripped),
  so no schema change is needed, and a test asserts `store.load` does NOT exist so a future rename
  cannot re-introduce the phantom.

- **DISCOVERY — my own test wrote two runs into the REAL `~/.personalclaw`.** Patching
  `personalclaw.config.loader.config_dir` does NOT reach `workflows.store`, which imports `config_dir`
  at module level. The runs then leaked into `test_context`'s `active_workflows_block()` assertions —
  two failures that passed in isolation and failed only in the full xdist mix. Root-caused rather
  than reruns: switched to patching `workflows.store.config_dir` (the established convention in
  `test_workflows_run_delete._isolated_home`), added an `_assert_isolated` guard so a future
  mis-patch fails loudly instead of writing to a real home, and cleaned the two leaked rows plus
  their two empty run dirs out of the real home after verifying the db held nothing else.

- **An unrecognized `memory_mode` parses as INCOGNITO, not NORMAL.** The value exists because someone
  asked for privacy; a typo or a newer mode name this build does not know must not read as "record
  everything". A lost note is recoverable, a memory the user believed was never written is not. The
  distinction against ABSENCE is deliberate: no recorded mode means the run predates the feature or
  came from an unrestricted origin, both genuinely unrestricted.

- **Inheritance reads BOTH sources**, following `session_search.is_restricted`'s precedent rather than
  inventing a third store: the durable JSONL line first (it survives a restart), then the
  process-global registry (it only knows sessions this process has seen). Checking only the registry
  would mean a gateway restart silently un-marks every incognito run in flight.

- **The engine SKIPS learning nodes outright in a restricted run** — the primary control, DEGRADED
  rather than FAILED because the node was deliberately not run. Trusting each persist provider's own
  gate would make correctness depend on every write path checking a flag, and a path added later
  would leak by default. Nodes can also DECLARE `persists_memory: true`, which is how an
  app-contributed writer opts in rather than leaking past a hardcoded provider list.

- **`temporary` needs BOTH registry marks.** `is_temporary` gates reads while `is_restricted` (true
  for either) gates writes, so marking only temporary would leave the write gate depending on one
  function's internals. The marks are RETURNED as names rather than performed, keeping the module
  testable without mutating process-global state every other test shares.

- **NOT DONE:** the actual `session_restrictions` marking and JSONL `memory_mode` write at run start —
  `restriction_calls` and `durable_metadata` are the contract, but performing them needs the run-start
  path in the controller/service, and stamping `extra` there is a service change this session does not
  open. Converting EXISTING `loop-<id>`/`cron:<id>` sessions to run-ownership is explicitly scoped OUT
  by §5.1 itself (it touches `session_map.py`/`sync_bridge.py`/Slack bridging); only new `workflow:`
  keys are introduced here. The completion-summary mirror into the launching session needs the
  blocking-run path.

### 2026-08-02 — session 51 (needs-input inbox) DONE

`workflows/needs_input.py` (new): the NeedsInputItem card, owner binding, once-only staleness
re-notify, and the refs round trip. `attention.raise_gate_item` now attaches the structured card. 71
tests.

- **Most of this session's nominal scope ALREADY EXISTED and was not rebuilt.** `attention.py` already
  projects a waiting gate into the inbox via `emit_attention_item`, already dedups per
  `(run, instance_path, epoch)`, already carries the resume token in `refs`, and `ItemKind.NEEDS_INPUT`
  plus `NON_CHANNEL_KINDS` were already registered. The genuine gaps were the STRUCTURE (blocker /
  attempted / evidence / recommendation, one decision), owner binding, and staleness re-notify.

- **DISCOVERY — the block classifier matched failure classes that do not exist.** The first version
  keyed on `dependency`, `capability` and `config`; the engine's real `FailureClass` values are
  `user`, `transient`, `network`, `permission`, `protocol`, `budget`, `timeout`, `internal`. So every
  capability failure fell through to "a decision" and the card asked the user to decide about a
  missing credential instead of telling them to add one. Fixed by enumerating the real enum, plus a
  parametrized sweep over EVERY value so a future class cannot land on a default by accident. Two
  consequences of reading the real list: `budget` is a capability block (a spent budget needs a human
  to raise it), and `protocol`/`internal` are deliberately NOT transient — filing a bug as retryable
  means it retries forever while nobody is told.

- **A transient block is not user-actionable.** Surfacing a rate limit as a decision asks the user to
  do the system's waiting, and it trains them to click through cards — which is how a real approval
  gets clicked through too.

- **Owner binding is anti-hijack, and an UNBOUND card is deliberately open.** Only the requesting
  session may satisfy an owned item, because a gate surfaced into a shared channel would otherwise be
  answerable by anyone who sees it. But a card with no owner is answerable from anywhere: a run the
  user started themselves should be answerable from whichever surface they are at, and requiring an
  owner match there would mean starting a run in chat and being unable to answer it from the
  dashboard. The refusal NAMES the owner, since "not allowed" leaves the user unable to act.

- **Staleness reminds exactly once, and the counter is what makes the cap real.** Reminding without
  incrementing would remind every sweep, which is the failure the cap exists to prevent. A `seen`
  card can still be reminded — seen is the read/unread boundary, not an answer, and a card the user
  glanced at and left is exactly what a reminder is for.

- **The card rides the EXISTING free-form `refs` dict.** The inbox is a general attention store shared
  with channel messages, so widening `InboxItem`'s schema for one item kind would make every other
  kind carry empty workflow fields. Today's keys (`workflow`, `workflow_name`, `workflow_node`,
  `resume_token`) are preserved verbatim so a surface written against them keeps working, and the
  card's inputs are all OPTIONAL — a required argument would have made the existing gate path a
  breaking change for a payload most callers cannot yet supply.

- **`attempted` earns the recommendation its credibility.** The same suggestion reads as a guess
  without it and as a considered next step with it. Failed attempts are KEPT: a log showing only
  successes would make a five-attempt struggle look like a first-try block, and the user would wonder
  why the system gave up so fast. Evidence is trimmed to 600 chars because an inbox row is a glance
  and the full detail is one deep link away.

- **Validated live against a real `InboxStore`** (via the `live_store` type-check seam, which the
  module's own docstring warns is isinstance-checked precisely so a MagicMock cannot absorb real
  writes): the row the inbox holds carries the card, the evidence is trimmed, the owner blocks a
  stranger, and the legacy refs keys survive.

- **NOT DONE:** journal-event reification (§6.1 asks for needs-input/approval as first-class journal
  event types so the inbox is a pure projection) — the emit path is imperative today and converting it
  is a journal-format change that Self-Verification's replay harness gates; `permission_suggestions` +
  `updated_input` on the reply contract (modify-and-approve) needs `workflow_resume`'s answer grammar,
  the same engine-surface change carried forward from S43/S45; the cross-project inbox aggregation
  view, count pills, digest-batching and the Open Decisions lane are FE work; OS-level notification
  routing already exists behind `notification_allowed()` and is deliberately untouched.

### 2026-08-02 — session 52 (code-kind worktrees) DONE

`workflows/worktrees.py` (new): preserve-in, marker-guarded setup, resume safety,
teardown-before-deletion, the per-run branch, the machinery-free review diff, and the two
reintegration verbs — all on the proven `loop/worktree.py` machinery rather than a second git
implementation. 58 tests, driven against a REAL git repo.

- **Two properties were MEASURED on the real machinery before any code was written.**
  `add_worktree` on an existing id returns the SAME path rather than failing — which is what makes
  resume free rather than something to implement. And an untracked `.env` is genuinely ABSENT from a
  fresh worktree, which is why `preserve_patterns` is adoption-critical: a worktree where every build
  fails reads to a user as "isolation is broken".

- **DISCOVERY — the review diff listed the engine's own machinery as user changes.** Driving a real
  worktree showed the changed-files panel reporting the preserved `.env` AND `.pclaw-setup/` alongside
  the one file the run actually edited. A review panel full of machinery is one the user skims, with
  the file that mattered in the same list. The first fix excluded by filename and only half worked:
  git reports an untracked directory as `.pclaw-setup/` WITH a trailing slash, so a prefix check
  written against the bare name matched nothing — an exclusion that existed and did half its job.
  Both sides of the comparison are now normalized, and preserved files are passed IN from the
  preserve pass rather than re-derived from globs (which would disagree with reality the moment a
  pattern matched something the copy skipped).

- **The status parser was verified against real `git status --porcelain` output**, not handwritten
  fixtures. The two-column form is easy to get wrong: a parser reading one column would report a
  staged deletion as unstaged, making the cockpit's stage/discard buttons act on the wrong thing.
  Real output also settled the rename shape (`R  old -> new` — the NEW path is what the user
  reviews). An unknown code is KEPT rather than dropped, because a file the parser does not
  understand is still a file the user changed.

- **DISCOVERY (pre-existing, root-caused and fixed) — `test_provider_resolution_unify.py` leaked a
  synthetic provider entry into the process-global registry.** The full gate went red on
  `test_cli.py::TestDoctor::test_doctor_with_agent` with `SystemExit: 1`, deterministically, and green
  in isolation. Three wrong hypotheses were tested and discarded before the real one (a `config_dir`
  monkeypatch, a `PATH` mutation, and a process-wide `shutil.which` patch — all correctly scoped).
  The actual cause: `SomeAcpAgent` is registered into `get_default_registry()` and never removed, so
  `cli_doctor` reported `SomeAcpAgent (acp_agent): error` and exited 1 for ANY test sharing that
  worker afterwards. Fixed with an autouse cleanup fixture at the file that owns the entry. This
  session's tests only shifted the worker interleaving that exposed it.

- **Preserve copies IN, never OUT.** A pattern that copied a worktree file back over the user's real
  tree would make an isolated run able to modify the thing it was isolated from. Denylisted names are
  refused whatever the glob matches (`.git` would corrupt the worktree's own repo state), and an
  oversize match is skipped WITH a reason — a user whose build fails needs to know their file was
  skipped for being 4MB.

- **Setup markers are content-addressed and setup NEVER blocks the run.** Setup runs on every resume
  by contract, so each step guards itself; a marker keyed by position would skip an EDITED step as
  though it had run. Refusing to run the workflow because `npm install` failed would make declaring
  setup a liability, and a user would stop declaring it.

- **Teardown before deletion, commit before removal, and `keep_open` for when the workspace IS the
  deliverable.** Teardown's job is to stop services and sync work out, and both need the directory to
  still exist. An ephemeral workspace whose run record points at a deleted directory has lost the
  work, so a per-run branch means the record references git. `keep_open` still runs teardown —
  keeping the directory is not keeping the processes.

- **The substrate is built HERE so S46's boot sweep has one source of truth.** The sweep's whole
  decision turns on whether an isolated substrate is alive; two places computing that would eventually
  disagree, and the disagreement shows up as a run aborted despite having recoverable work. Verified
  end to end: a live worktree yields SUSPENDED + resumable, a missing one yields an honest abort.

- **Reintegration is offered, never performed.** `Apply Locally` and `Checkout Branch` are both
  surfaced with conflicts named on the offer — "apply this" that then fails with a conflict is worse
  than "apply this (2 files conflict)". Checkout stays safe even with conflicts, because nothing
  merges until the user decides to.

- **NOT DONE:** the actual subprocess execution of setup/teardown commands — `pending_setup` and
  `plan_teardown` decide, and the caller performs, because running shell commands from a planning
  module would put an unbounded execution surface behind a pure API. PID-liveness lock files outside
  the workspace and the ff-only refresh for named workspaces need the run-record schema. The cockpit
  diff panel and the two verb buttons are FE work on this contract. Container mode (§4.4) remains
  opt-in and deferred by the plan.

### 2026-08-02 — session 53 (introspection checklist) DONE

`workflows/introspection.py` (new): the nine-question checklist as a checkable contract, RunStats as a
pure journal projection, verification debt, said-no gate statistics with a sample-gated fake-check
badge, per-template p50/p95 cards, and the Proof section. 43 tests, driven against real journals
written by the engine's own `Journal`.

- **No metrics store was added.** The plan is explicit — "pass-rate, failure distribution and latency
  percentiles are queries over this — not a separate metrics store" — so every number here is a
  projection over `journal.ledger()`. A test asserts the projection agrees with the engine's own
  `run_totals()` on tokens/cost/steps, because two aggregates over one stream that disagreed would
  make the cockpit and the run row show different numbers with no way to tell which was right.

- **DISCOVERY — `GATE_REJECTED` is declared in `journal.py` and emitted NOWHERE.** A said-no metric
  reading it would report zero rejections for every gate in the library and therefore flag all of them
  as fake checks — a warning badge on everything, which is the same as no badge. Rejections are
  derived from `GATE_RESOLVED`'s own `approved` field instead, which the controller does write on both
  the auto-approve and human-resolution paths. A drift test pins this: it matches the shape of an
  actual emitter (`write(... GATE_REJECTED`) rather than any mention, so it does not fail on its own
  explanation, and it was verified to catch both a qualified and a bare-name emitter while ignoring
  the declaration, a docstring reference, and a read/filter.

- **DISCOVERY — a `step_attempt` on a non-gate created a gate-table row.** Attributing every attempt
  event made `publish` (an action) appear in the said-no table with `total: 0` and a 0.0 pass rate — a
  row that reads as a gate which has never passed anything, in a table whose credibility is the only
  reason anyone reads it. Retries are now attributed only to nodes already known to be gates.

- **The fake-check badge requires a SAMPLE.** "0 rejections in 0 runs" and "0 rejections in 40 runs"
  are different claims, and only the second is evidence. A badge firing on the third run of a new
  template would teach the user to ignore badges before the metric had ever been right. One real
  rejection clears it permanently — the gate has demonstrated it can say no, which is all the badge
  was testing for.

- **Verification debt is counted by BINDING, not adjacency.** A node whose output a later gate consumed
  is verified even with three nodes between them; counting "the next node is a gate" would report a
  correctly-verified reviewer as debt, and a debt number that flags correct structure gets ignored.
  The warn threshold is deliberately NOT zero, because a plan legitimately contains zero-token actions
  whose output IS the check (S42's contract lint already exempts them).

- **p50 and p95, never a mean.** A mean hides both the typical case and the bad one: one runaway run
  moves it, and nothing tells you whether the usual run is cheap. Percentiles are nearest-rank rather
  than interpolated, so every reported figure is a run that actually happened — with the handful of
  runs a personal instance accumulates, an interpolated p95 invents a value between two real runs. A
  template with ONE run still gets a card, because withholding it would leave the newest template (the
  one most likely to be surprising) invisible on the surface that answers "what is costing money".

- **The failure rate counts RUNS, not steps.** A run with four failed steps is one bad run; counting
  steps would make a single messy run look like a systemic problem.

- **The nine questions are named in CODE.** `checklist_gaps` reports which the supplied state cannot
  answer, which is what turns "glanceable" from a taste claim into a validation script — a surface
  rendering eight of nine has a specific hole. An empty value counts as ANSWERED: "nothing is blocked"
  is an answer, and treating it as a gap would make an idle instance look broken.

- **The Proof section states its own caveats.** A section with no evidence and no warning is the worst
  possible surface, because it looks like proof. High verification debt plus a confident summary is
  exactly the shape that makes unattended work untrustworthy, so the debt earns an explicit warning.
  The summary reports counts rather than a verdict — a summary that said "succeeded" would be the run
  grading itself.

- **NOT DONE:** the cockpit and hub FE that render these projections (chip ribbon, cost/latency strip,
  template cards, Proof section, warning badges) — this session delivers the contract they read.
  Trajectory replay needs the resolved-prompt and context-snapshot payloads the journal records but
  does not yet expose as a timeline projection. The live touched-items feed needs the knowledge/artifact
  mutation events to carry run attribution, which S47's lineage started but only for artifacts. Making
  `GATE_REJECTED` a real event (so a rejection is a first-class journal fact rather than a derived one)
  is a journal-format change Self-Verification's replay harness gates.

### 2026-08-02 — session 54 (project export/import) DONE — section F CLOSED

`workflows/project_export.py` (new): the allowlisted portable set, per-entity sha256 in a versioned
manifest, secrets as presence flags only, typed import refusals, and `imported-N` collision slots.
65 tests.

**This is net-new coverage, not an extension.** `snapshot.VALID_COMPONENTS` is
`(memory, crons, config, skills, workspace, notifications, security)` — neither `projects/` nor
`tasks/` nor `artifacts/` appears in it, so a project could not be moved off the machine at all. The
plan's reality note was verified rather than trusted.

- **Secrets never travel — not encrypted, not optional, absent.** The exclude-set is
  `portability.EXPORT_EXCLUDE`, which is itself a projection of the state inventory's `secret=True`
  entries; a local copy here would re-create exactly the two-list drift that let stores escape
  coverage before. The strongest test is not "the file was skipped" but "the secret BYTES do not
  appear anywhere in the manifest", which is what matters when a manifest is pasted into a bug report.
  A presence flag takes the value's place, so an importer knows a credential is expected and can
  prompt — strictly more useful than the credential travelling.

- **DISCOVERY — the exclusion reason was matched as PROSE, and the prose lied.** The reason string for
  a never-exported directory read "directory is never exported (size or secrets)", and the
  presence-flag branch tested `"secret" in why` — so every file inside `worktrees/` was reported to
  the user as a credential they must re-enter. Measured on a real export: `big.js` and `keys.json`
  both appeared in the "credentials to re-enter" list. Exclusion reasons are now typed CODES with a
  separate human-text table; a prose string is for reading, a code is for branching, and conflating
  them makes the branch depend on wording.

- **The import safety rules were COMPARED against `snapshot._data_filter`, not assumed to match.** They
  agree on every traversal case (`../`, absolute, mid-path). The one divergence is deliberate and
  stricter: a project-scope exclusion for `worktrees/` that the general filter has no reason to know.
  Symlink and hardlink refusal stays in the filter, which is where the TOCTOU gap is — the plan-time
  check is the readable half, not a replacement, and a test pins that the filter still rejects both so
  the non-duplication stays honest.

- **A checksum mismatch REFUSES the entity, and one bad entry costs that entry.** Importing a file
  whose hash does not match is importing something the exporter did not send; whether that is
  corruption or tampering does not change what the importer should do. A partial import is the normal
  outcome for an archive that travelled, so the refusals are named individually rather than failing the
  project. An entry with NO digest is also refused: unverifiable is not the same as fine, and accepting
  it would make the manifest's integrity claim optional, which means it is not a claim.

- **An unknown manifest schema is refused rather than guessed.** Guessing at a shape this build does
  not know is how an import silently writes the wrong thing.

- **A name collision gets an `imported-N` slot, never an overwrite.** The user's existing project is
  the one thing an import must not damage, and a silent merge would be worse than either — a project
  that is neither the original nor the imported one. Slots count from the existing ones, so importing
  the same archive three times produces three projects rather than failing on the second, and a
  re-imported slot name does not double-suffix into "P (imported-1) (imported-1)".

- **The portable set is an ALLOWLIST.** A project dir accumulates whatever features write into it, so a
  denylist would export a future feature's private state by default — which is precisely how a
  credential escapes. Artifact BODIES do not travel (a 50-version image history would dwarf the
  archive; the lineage names the run that can regenerate it), and a run's JOURNAL does not travel
  (it carries every resolved prompt, the single most likely place a credential was echoed into an
  output).

- **Manifest serialization is canonical**, so two exports of an unchanged project produce identical
  digests. A digest that changed without the content changing could not detect tampering.

- **NOT DONE:** the archive I/O itself — `plan_export` decides what belongs and what it hashes to, and
  the caller writes the ZIP, because putting archive extraction behind a pure planning API would hide
  the one operation whose safety filter must run at extraction time. Optional client-side AES-GCM
  encryption (PBKDF2-SHA-256) and extract-to-unique-tmp with janitor cleanup belong with that I/O. The
  `projects` component registration in `snapshot.VALID_COMPONENTS` and `portability` is a one-line
  addition per file that needs the archive path first, or it would advertise a component that cannot
  be produced. The CLI/REST surface and the FE export button are surface work on this contract.

---

**Section F (Work Containers) is COMPLETE:** sessions 46-54, PRs #185-#193.

---

- **2026-08-09 — DONE — WF2WOR-6 (§5.1, criterion 5): `workflows/ownership.py` wired into the run
  lifecycle.** Branch `feature-wf2wor6-run-ownership-wiring`, PR #947. Session 50 shipped the ownership
  module and its enforcement helpers, but the module had ZERO production call sites — an incognito or
  temporary origin did not actually carry write-suppression down into the run it launched. This atom
  wires the seam end-to-end: (1) `service.py` stamps the inherited `MemoryMode` onto `run.extra`
  (`RUN_MODE_KEY`) via `stamp_run_mode` BEFORE `store.create`, so the posture round-trips on disk and
  survives a restart, and it drives the already-wired engine node-skip (`_restriction_skip`) and the
  run-end LearningGate; (2) `controller._prepare` enforces the inherited mode into the process-global
  `session_restrictions` registry for BOTH the origin and owned keys (per `restriction_calls`), and
  `_capture_run_end` no longer leaks writes from a restricted run; (3) the blocking-run path mirrors a
  one-line completion summary back into the launching session via the tool RESULT `body`, with
  indexability decided at source by `ownership.announcement()` (previously inert, now its first live
  call site) — a restricted origin gets the summary WITHOUT it being indexed. `parse_mode('persistent')
  → NORMAL`, fail-closed to `INCOGNITO` on any unknown value. 6 new `TestMemoryModeInheritance` async
  tests; `make lint` (black/isort/flake8/mypy, 771 files) clean; 220 targeted tests (69 tools + 151
  ownership/controller) green.

- **DEVIATION — the run's durable memory-mode head is `run.extra`, NOT a JSONL `memory_mode` line.**
  Criterion 5 names "the JSONL `memory_mode` write" for run durability, but a run owns no
  `ConversationLog` file — its stage subagents persist under their own `subagent:` keys. The run
  RECORD's `extra` dict IS the run's durable metadata head: it round-trips on disk and is what a restart
  replays. `stamp_run_mode` composes over `durable_metadata` so `RUN_MODE_KEY` and the metadata line
  are the same key rather than two literals that could drift. Pinned by
  `test_an_incognito_origin_stamps_the_run_record` (survives `WorkflowRun.from_dict(run.to_dict())`).

- **DESIGN — the mirror surface is the blocking tool RESULT `body`, not a `ConversationLog.append`.** A
  controller-side append into the origin JSONL would be clobbered by `_save_session_to_history`, which
  rebuilds the file from the chat's in-memory messages. The tool result lands in the launching chat's
  transcript as a normal message and is persisted by that chat's own full rewrite — the honest,
  non-clobbering delivery. Only blocking runs mirror (a detached run has no launching turn to land in).

---

- **2026-08-09 — DONE — WF2WOR-9 (amendment C2.1 + C2.2 + C2.3, partial VC): the fan-out leaf contract
  is now load-bearing, `mutating` leaves are serialized by the engine's own scheduler, and the
  token-matched measurement harness exists.** Branch
  `feature-wf2wor9-fanout-leaf-contract`. `batch_compile.LeafTask` grew three REQUIRED,
  default-less fields — `objective`, `output_format`, `boundary` — plus an optional per-leaf `model_ref`
  pin; two new lints (`contract_lint`, `boundary_lint`) refuse an under-specified or self-contradictory
  leaf at compile; `LeafTask.prompt()` carries all three declarations plus the literal `output_schema`
  into the leaf's own prompt; `compile_batch` emits a `needs` chain over the `mutating` leaves and a
  `config.model` pin that `engine.dispatch_stage` now threads into `spawn(model=...)`;
  `CompileResult.enforced()` joins `unenforced()` so the honest list is readable against its complement.
  `harness/fanout_measure.py` + `python -m harness fanout-measure` + a documented procedure in
  `harness/README.md` implement amendment (e). 51 new tests (31 in
  `tests/test_workflows_batch_compile.py`, 20 in `tests/test_harness_fanout_measure.py`, 1 in
  `tests/test_workflows_engine.py`); `make lint` clean (black/isort/flake8/mypy, 1500 files, 777 typed);
  160 targeted + 228 sweep tests green.

- **DISCOVERY — `Capability.MUTATING` was a DECLARED-BUT-INERT enum member until this atom.** The
  inert-surface census had it on the books (`enum:Capability.MUTATING`, the "enum member nobody writes"
  shape): S48 shipped the capability vocabulary, the posture map and the `research_leaf_writes` lint, but
  nothing in `src/` ever BRANCHED on `MUTATING` — `leaf_tool_posture` reached it only by falling off the
  end of an `is RESEARCH` check, so the value could have been any other string with identical behaviour.
  Amendment (c) is what gives it teeth: the serialization chain is its first real reader.
  `inert-surface-baseline.json` regenerated in this commit for the legitimate shrink (156 → 155,
  enum 29 → 28), per the ratchet's same-commit rule.

- **MEASURED — the `needs` chain serializes writes WITHOUT re-introducing the failure the batch had
  already fixed.** Driven through the real `tick.frontier` (the S48 note in this file records what
  assuming join semantics cost the last author, so nothing here was assumed): with 8 leaves and 3
  mutators, all 5 research leaves launch on tick 0 alongside the first mutator, and the chain advances
  exactly one mutator per tick. The property that mattered most: `_visit_parallel` satisfies a `needs`
  edge on any TERMINAL predecessor — done, degraded, skipped or FAILED alike — so a failed mutator HANDS
  THE LANE ON. Had it required success, serialization would have become a second way for one bad leaf to
  sink a batch, which is precisely what `join: quorum, quorum: 1` exists to prevent. An all-mutating
  4-leaf batch runs strictly `[1, 1, 1, 1]`, and one failed leaf in a chained tree still derives DONE.

- **DESIGN — `boundary` is NOT `writes`, and the compile refuses only where they CONTRADICT.** `writes`
  is a POSITIVE declaration consumed by the compiler to detect sibling collisions (coordination data
  between leaves); `boundary` is a NEGATIVE declaration consumed by the WORKER (instruction content —
  the active ingredient evidence items 2 and 3 point at). They are duals, not complements: an empty
  `writes` does not mean "the boundary is everything". They meet in exactly one place — a leaf declaring
  a write INSIDE its own boundary — which is a contradiction only the author can resolve, so it is an
  ERROR (`boundary_contradicts_writes`). That check compares PATH-shaped tokens only, not substrings: a
  boundary reading "do not touch the production report pipeline" must NOT flag `writes=["reports/x.md"]`,
  because a gate that cries wolf on a legitimate fan-out is a gate that gets switched off.

- **DESIGN — the contract rides into the PROMPT, not just into the lint.** The engine's existing
  `output_contract` REJECTS off-format output before any binding resolves, so a declared format the
  worker was never shown would be a gate that fails 100% of the time — which reads as a broken fan-out
  rather than as a missing declaration. The `output_schema` is serialized VERBATIM into the prompt so the
  worker is held to the same shape `check_output_contract` will hold it to; a paraphrase would let the
  two drift and the worker would satisfy the paraphrase. No second validator was added (the module's
  standing rule).

- **DEVIATION — the per-leaf model pin field is `model_ref`, not `model`.** `mutations._FIELD_ALIASES`
  already maps the author-facing name `model` onto `model_tier` (WF2-R20d), so a `workflow_edit` op
  saying `fields: {model: "..."}` against a compiled leaf would silently rewrite the TIER and leave the
  pin untouched — the author would then debug why the pin "did not apply" while looking at a key that was
  never written. The emitted NODE CONFIG key stays `model` (that is what `dispatch_stage` reads); only
  the leaf-contract field is renamed. Homogeneity stays the default by ABSENCE: no pin means no `model`
  key at all, and `dispatch_stage` sends `None` so `spawn` resolves the `orchestration` chain — passing
  `""` would have looked like a pin to nothing in particular.

- **The persona prohibition is a CHECK, not a comment.** `forbidden_declarations()` asserts no
  persona-shaped field (`persona`/`role`/`character`/`personality`/`style`/`voice`) exists on
  `LeafTask`. "We did not add one" is a fact about one commit; a check is a fact about every future one,
  and amendment (a) is a standing prohibition — a future author reaching for `role` trips the same gate
  as one reaching for `persona`.

- **C2.3 reports refusals, and exits 0 when it does.** Five verdicts, three of them refusals:
  `inconclusive` (|delta| < 5 points, OR a delta smaller than the arms' own within-arm spread),
  `not_token_matched` (spends differ >5%, or an arm spent nothing), `insufficient_trials` (<3 trials per
  arm). The within-arm-spread rule is an addition to the letter of amendment (e) and deliberate: six
  points between arms means nothing when one arm varies by seven across its own trials, and a reader
  shown only the six would stop reading. `python -m harness fanout-measure` exits **0 for every honest
  verdict including `inconclusive`** — a non-zero would make the honest answer look like a broken run,
  which is the failure mode the amendment's own risk register names ("a plan that only ever reports wins
  is not measuring"). Only a malformed observation file exits non-zero (2).

- **VC — PARTIALLY proved, and the split is recorded rather than glossed.** PROVED here, driven through
  the real engine in one test over one compiled spec: an 8-wide fan-out with 3 mutating leaves (a) has
  all 8 leaves delivered and reach terminal state, (b) never has two mutators concurrent on any tick,
  and (c) still derives DONE with one leaf FAILED; the spec also passes `validate_node_tree` with the
  chain in it. NOT proved and NOT claimed: **"per-child cost is visible"** and **"one click kills the
  fan-out mid-flight"** belong to rows **C1.4/C1.5** (shipped under WF2WOR-8, but their FE/observability
  surfaces are that atom's `done_when`, not this one's), and **"the parent keeps its context"** is
  **C1.1**'s injection-wall clause. The end-to-end live drive of a real 8-wide `subagent_run` is blocked
  on the production call site, which does not exist: `compile_batch` still has NO caller, and the
  cutover is **WF2WOR-5**, which depends on this atom. An over-claimed VC is worse than a deferred one.

- **C2.3 harness verdict: NOT YET RUN on real work — deferred to WF2WOR-5 with the call site.** The
  measurement instrument, its refusal semantics and its procedure ship here and are unit-proven
  (including that a 3-point delta reports `inconclusive` and does not report a win). Running it requires
  executing a real fan-out against a real single-agent arm on identical work at matched token spend, and
  there is no production path that starts a compiled batch yet. Recording a verdict from a synthetic
  arm would be exactly the dishonesty amendment (e) exists to prevent, so the row's "report the verdict
  in this plan's execution log" obligation is carried to the atom that can honestly discharge it.

- **NOT DONE (out of scope, by row):** the `mcp_subagents.subagent_run` compile-cutover (WF2WOR-5), the
  C1.1-C1.5 defect fixes, and the tool-handler posture seam — `unenforced()` still names the three
  posture items with no seam (tool denials/read-only, `workspace_mode`, per-node `timeout_secs`), and
  they are unchanged by this atom.

---

- **2026-08-10 — DONE — WF2WOR-4 (§4.1, criterion 7): the workspace block is now REAL, and both
  decision layers have production callers.** Branch `feature-wf2wor4-workspace-wiring`.
  `workflows/workspace.py` (S49) and `workflows/worktrees.py` (S52) shipped 1,096 lines of
  well-tested decision logic with **zero importers in `src/`** — no spec key `"workspace"` was read
  anywhere, so a template declaring `mode: worktree` ran in place exactly like one declaring
  nothing. This atom ships `workflows/provisioning.py` as the performer and
  `controller._provision_workspace` (called from `_prepare`, modelled on `_enforce_inherited_mode`)
  as its caller, plus the two config knobs, the teardown wiring at both deletion paths, the
  watchdog tightening, and the cockpit surface. 68 new tests (40 provisioning + 9 controller + 5
  API + 1 worktrees regression + 6 vitest + config round-trip); `make lint` clean (black/isort/
  flake8/mypy, 778 files); 898 vitest green including the global design ratchets.

- **DECISION — a spec with NO `workspace:` block provisions NOTHING, and this was forced by a
  measurement.** The first cut applied `workflows.workspace_default_mode` to every run, which is
  the literal reading of "the mode a run gets when its spec declares none". That broke
  `test_an_adopted_run_resumes_without_re_running_finished_work`: a scratch dir on every run made
  every stale `RUNNING` run look like an ISOLATED substrate to S46's boot sweep, so a
  crash-survivor whose journal-backed work is perfectly resumable became SUSPENDED awaiting a
  manual Resume instead of being adopted. The sweep's own DEVIATION note says inline runs stay
  owned by adoption; a default-on workspace silently took every run out of that path. So the
  default fills in an UNDECLARED MODE inside a block that declared its other fields
  (`{preserve_patterns: [...], setup: "npm ci"}`), and `declares_workspace()` is the opt-in gate.
  §4.1's own framing is that the workspace is a declaration rather than a convention — the
  measurement just made the cost of ignoring that visible.

- **DISCOVERY (measured, changed the design) — `git add -A` committed the preserved `.env` and the
  engine's own setup markers into the run branch.** Driving a real repo showed the durable-branch
  commit (§4.1's "the run record references git, not a filesystem") carrying `TOKEN=local-secret`
  and `.pclaw-setup/*.done` into git history — and both reintegration verbs would then offer to
  apply the user's own credentials back over their tree. S52 fixed the *review* half of this
  (`is_infrastructure` excludes machinery from the diff panel), but a review filter cannot
  un-commit a secret: the exclusion has to run at the ADD. `_commit_outstanding` passes git
  pathspecs (`:(exclude).pclaw-setup`, `:(exclude).pclaw-setup/**`, plus one per preserved file)
  and the test asserts against `git ls-tree` on the real branch, not against our own bookkeeping.

- **DISCOVERY (pre-existing in S52, root-caused and fixed) — `inspect_worktree("")` reported
  ALIVE.** `Path("")` is `.`, whose `is_dir()` is True, so an unprovisioned run would have named
  the GATEWAY's own working directory as its live workspace — and the boot sweep would then read
  that as a survived substrate and SUSPEND a run with nothing to resume into. No test caught it
  because nothing in `src/` called the function at all: the empty-path case only exists once there
  is a production caller. Fixed at the source with a regression test in
  `test_workflows_worktrees.py` (the module that owns the defect), not worked around here.

- **DECISION — the PID lock is BOTH flock and a pid line, because each covers the other's blind
  spot.** `fcntl.flock(LOCK_NB)` is the authority (the OS releases it on death — the property
  `concurrency.py` chose it for, so a crashed gateway can never wedge a workspace). The recorded
  pid is the EXPLANATION: flock says only "someone holds it", and a refusal that cannot name the
  holder is one a user cannot act on. Measured asymmetry in the probe: `EPERM` counts as ALIVE,
  because a process we may not signal is still a process and reading it as dead would let us steal
  a live workspace. When flock refuses but the recorded pid is gone we still REFUSE — fail-closed,
  since flock's death-release means a refusal implies a live holder that simply had not recorded
  its pid yet, and two runs writing one worktree is the corruption the lock exists to prevent.
  Fail-FAST rather than queue: waiting behind a run that may take an hour is worse than saying so.

- **DECISION — the lock covers the PROVISIONING WINDOW, not the run.** Holding a flock across a
  multi-hour run would tie the workspace to this process's lifetime, so a gateway restart would
  strand it. What actually needs mutual exclusion is the preserve+setup pass, where two processes
  writing one tree corrupt each other; the run itself is protected by the per-run path (or, for a
  named workspace, by the next provisioning attempt refusing).

- **DECISION — a declared mode that cannot be honored DEGRADES with the reason recorded; only a
  FATAL declaration refuses.** No git on PATH, a non-repo workspace, an unborn HEAD, `container`
  mode: each returns an isolated scratch dir plus a `degraded_reason`, and `isolated` is reported
  FALSE so the board never offers a Resume into isolation we do not have. A user who declared
  `worktree` wanted isolation, and the isolation is deliverable without git — refusing would trade
  the property they asked for against the mechanism they did not. The hard refusals are exactly
  `parse_workspace`'s fatal issues (unknown mode, greedy preserve pattern), because honoring those
  is impossible and running anyway is the ignored-fatal-issue shape.

- **DECISION — both deletion paths go through ONE performer, and both became async.**
  `service.teardown_workspace` is called by `service.delete_run` and by `watchdog.prune_runs`. Two
  call sites each doing the ordering would eventually disagree, and the ORDER is the contract: a
  scratch workspace lives UNDER the run dir, so an `rmtree` first would run `docker compose down`
  against a path that no longer holds the compose file. Retention was wired deliberately, not just
  the explicit delete — retention is the path that fires with nobody watching, so it is the one
  where a leak accumulates silently. `delete_run`/`prune_runs` are now `async` (their only
  production callers already were); the affected existing tests were realigned to `await`, not
  weakened.

- **DECISION — both config knobs have a REAL reader, named in a test.**
  `workspace_default_mode` is read by `provisioning.resolve_spec` (driven for all four enum
  values); `workspace_teardown_on_expiry` is read by `service.teardown_workspace` and its OFF path
  is driven end-to-end (the command is skipped, the removal still happens — otherwise the directory
  would be orphaned). An unparseable stored mode loads as `scratch`, never as the declared value
  and never as `in_place`: S49's ruling is that `in_place` is never a default, and a config typo
  must not be what puts a destructive step against the user's real tree. All four points wired,
  plus `config-baseline.json` regenerated in the same commit and a `_SPECIAL` entry for the
  enum-constrained field.

- **DECISION — `worktree_path` is written for EVERY isolated mode, not just `worktree`.** It had a
  live reader (`watchdog._substrate_for`, whose own comment named this atom) and zero writers — the
  live-reader-of-an-unwritten-key shape. A scratch workspace that survived a restart is just as
  recoverable as a git one, so keying the sweep's decision on the mode name would abort recoverable
  work for the commoner mode. It is also CLEARED when a run degrades out of isolation, or the sweep
  would read a stale path as a live substrate.

- **DECISION — reintegration stays OFFERED, and the absence of a POST is asserted structurally.**
  Two measurements settle why: `git checkout <branch>` REFUSES a branch a live worktree holds
  (fatal 128), and `git merge --squash` refuses when an untracked file would be overwritten — so
  the safe order depends on state the gateway does not own. The panel renders each verb as the
  COMMAND it corresponds to, and two tests fail if a performing route or client method is ever
  added. Conflicts are probed with `merge-tree --write-tree`, which reports without touching either
  tree (measured: exit 1 plus the conflicted paths); a real merge-and-abort would leave the user's
  index dirty for the duration of a READ.

- **DEVIATION — the task→run branch rename uses `-M`, not `-m`.** `add_worktree` names the branch
  `pclaw/task-<id>` and a RUN's branch is `pclaw/run-<id>` (S52's `run_branch`). Measured: `git
  branch -m` on a branch checked out in a LIVE worktree succeeds and the worktree follows it, which
  makes the rename free — but `-m` onto an EXISTING name fails with fatal 128, and a retried run
  finds its own previous run-branch already there, which would leave the run on a task-shaped
  branch. A failed rename keeps the worktree and reports the branch we actually have, because a
  wrong branch name on the record would send both verbs at a ref that does not exist.

- **DEVIATION — §4.2 folder contracts got no new wiring in this atom.** The atom's scope line names
  §4.2, but `parse_folder_contract`/`may_write`/`validate_frontmatter` are consumed by the FILE-WRITE
  path (an agent writing into a project directory), not by the run-start workspace path, and no
  file-write call site in this atom's blast radius reads a `.folder.yaml`. Wiring them here would
  mean inventing a caller in the provisioning module that provisioning does not need — the
  inert-control shape in reverse. The `SETUP_MARKER_DIR` half of §4.2's contract vocabulary IS live
  (shared between the two modules and honored by the performer).

- **NOT DONE:** container mode (§4.4) remains owner-deferred to **WF2WOR-12** and degrades to an
  isolated scratch dir with the reason recorded rather than refusing a template that declares it —
  the config enum accepts the word because `workspace.Mode` contains it, and accepting the word
  never promises a runtime. Reuse-with-safe-ff-only-refresh for named workspaces is unbuilt: the
  lock keys on the NAME and `cleanup_markers` exists for the reuse case, but the ff-only refresh
  itself has no caller and no named-workspace consumer exists yet. §4.3's per-project keychain
  namespace is untouched (`spawn_env`/`presence_flags` remain S49-only; the run record serializes
  env PRESENCE through `WorkspaceSpec.to_dict`, which is the surface half of it). The
  run-owned-resource cleanup for browser pages/terminals (§4.1's "spawned_by: run_id") is not
  part of this atom's criterion and stays unbuilt.
- [2026-08-11][WF2WOR-3 / Success Criterion 3] CLOSED OUT — the atom was `in_progress` with most of clause 1 shipped in session 186; this closes the three remaining clauses so it is no longer partially executed. Clause-by-clause, verified against code before building so nothing already-shipped got rebuilt:
  **Clause 1 (publish + change_note + typed lineage) — was ALREADY DONE.** `publish.py:111 parse_publish`, `:154 content_hash`, `:165 materially_changed`, `:224 upsert_plan`, consumed by `engine.py:1434 apply_publish` and genuinely called from the dispatch seam. Left untouched except where clause 2 threads through it.
  **Clause 2 (content-hash file copies) — CLOSED, and the verify-first instruction earned its keep.** Only the BODY snapshot was versioned; `content_hash` normalizes text for the change gate and never touched files, and nothing anywhere copied a referenced file. New `rewrite_media_refs`/`media_filename`/`MediaCopy` in `publish.py`, `store_version_file` on the provider protocol AND the native implementation, wired in `apply_publish`. **The rewrite runs BEFORE the material-change comparison** — after it, a second identical publish would read as changed purely because the reference names differ, which would defeat the "versioned only on material change" clause it is meant to serve.
  **Clause 3 (per-run drop + outbox) — the outbox half was already done** (`files.py:132/273/375`, registered at `server.py:937-939`); nothing was run-scoped. New `workflows/filedrop.py` + service + `POST /api/workflows/runs/{run_id}/drop` (plus GET status and GET outbox). **Approval gating is two-layer and ordered deliberately:** the surface is disabled unless the spec declares `file_drop:`, then per file a template-declared `auto_accept_mimes` match passes while anything else returns **428** carrying `{filename, size, mime}` for `?confirm=true` to answer. `confirm` is evaluated so it cannot widen the auto-accept list — a confirm that could broaden the allowlist would make the declaration decorative. Follows `api_upload_file`'s multipart shape, `_guard`ed and SEL-audited per file.
  **Clause 4 (cockpit diffs + multi-view tabs) — CLOSED by surfacing, not by a second renderer.** New `web/src/pages/workflows/OutboxPanel.tsx` mounted from `WorkflowRunDetail.tsx`; Compare delegates to the existing `ArtifactCompare`, and Rendered/Source resolve through `resolveContentType({kind})` → `ContentSurface`, so the registry entry is the artifact `kind` and no new registration was needed. A second diff implementation would have drifted from `ArtifactCompare`'s per-kind behaviour (binary/image side-by-side vs text diff).
  **DISCOVERY — a test polluted the REAL home, and the mechanism is worth recording.** `workflows.store` binds `config_dir` at IMPORT, so the five existing tests in `test_workflows_publish.py` that patch `personalclaw.config.loader.config_dir` did not isolate the journal writer added here: four run dirs (`r-escape`, `r-media`, `r-outbox`, `r-prov`) appeared under `~/.personalclaw/workflows/runs/` with `publishes.jsonl` files. Fixed in-repo by patching BOTH (verified: a re-run creates nothing and the real home stays at 22 run dirs). The four stray dirs were inspected line-by-line, confirmed to contain only probe slugs (`escape-probe`, `media-report`, `outbox-probe`, `provenance-probe`) with no user data mixed in, and removed. This is the same import-bound-store trap that cost a 35s test elsewhere today — patching the loader's `config_dir` is not isolation for any module that captured it at import.
  **DISCOVERY — the primitive-adoption ratchet caught a raw `<button>`** (275 > 274). Converted to `Button`, which required adding `ariaPressed` (it carried `ariaExpanded` only) with its `Button.doc.ts` row — the `uiDocs` ratchet requires the doc entry. A selectable row must announce its state, so this was the correct fix rather than a suppression. The panel's one remaining raw `<input>` is a hidden `type="file"`, which has no primitive and follows the identical `sr-only` + `aria-label` pattern as `PortabilityPanel`, `Composer`, `KnowledgeCreatePage` and `FileTree`.
  **Three of the implementer's own FE assertions were wrong, not the code** — `Segmented` renders `role="tab"` not `button`; a `motion.button` needs `fireEvent` rather than a bare `.click()`; and the change note lives on the selected detail. Also: mocking `lib/api` breaks the lazy `renderers` chunk under jsdom, so an inline-render probe type was registered to exercise the real registry seam order-independently.
  **Contention, settled by measurement twice.** Two `test_workflows_controller.py` reds in the broad sweep: 85/85 green serially — and my independent re-run got 85/85 while the machine was BUSY, which is stronger evidence than a clean-field pass. This atom edits `engine.py`, `handlers.py`, `service.py` and `publish.py`, all exercised by that file, so "known flake" was the one verdict not acceptable from a summary line.
  **Gate (independent, by EXIT CODE):** black/isort/flake8 all 0; `mypy src/personalclaw harness` clean on 790 files; 147 passed across the two new suites + `hardening` + the three full-suite-only ratchets; 87 passed on the publish/filedrop pair with the real home unchanged; FULL `npm run test --workspace web` 1215 tests / 128 files; `tsc --noEmit` 0. `reference/routes.md` regenerated with exactly the three new lines. The generated `consistency-audit.json` moved `filesScanned` 433→434 for the new panel with **`driftHits` unchanged** — no drift blessed.
  **UNBLOCKS WF2WOR-7:** its only unmet non-EXT dep was WF2WOR-3, so it is now dependency-clear and is next in the partial-closeout queue.
- [2026-08-11][WF2WOR-7 / Success Criteria 6 & 8] CLOSED OUT — second of the owner's partial-atom queue, taken because WF2WOR-3 (closed the same day) was its only unmet dependency. All four clauses closed; the one genuinely-unavailable piece is named rather than dropped.
  **The gap was an entire tested module with no consumer.** `workflows/introspection.py` ships `RunStats`/`run_stats`, `GateStats`/`gate_stats`, `TemplateCard`/`template_card`, `checklist_gaps`, `proof_section`, `percentile` — all written, all tested, and **zero** frontend references with **no REST route** exposing any of it. So criteria 6 & 8 needed a route and a surface, not new statistics logic; none of the stats were reimplemented.
  **Clause 1 — the nine questions, answered from the cockpit.** `service.introspect()` + `GET /api/workflows/runs/{run_id}/introspect`, rendered by new `IntrospectPanel.tsx` opened from a cockpit button. **One response, not five**, because `checklist_gaps` can only name a hole in a payload it sees whole — five calls would each report a partial view as complete. The nine come from `introspection.CHECKLIST` (verified: exactly 9 entries — running / changed / blocked / approval / failed / cost / risky / next / proof), each mapped to a named surface: the Summary list answers running, blocked, approval, failed, risky and "what happens if I say nothing"; the cost/latency strip plus the template p50/p95 card answers cost; the Timeline tab answers changed, with the attempt ledger being its rows carrying `attempt ≥ 2`; the Gates section carries the said-no fake-check badge and the backend warning verbatim, with Proof on its own tab. The node tree was ALREADY DONE (`nodeTree.ts`/`DagView`) and was not rebuilt.
  **Clause 2 — `keys_equivalent` had ZERO callers; it does now.** Called in `watchdog.py:_publish_to_equivalent_loop_hub`, which is itself invoked from a real publish path (`watchdog.py:214`) — so this is a wired call site, not a fresh inert layer, which was the specific risk given the atom's own shape. It mirrors run events onto the equivalent `loop:<id>` hub via `peek` rather than `hub`, so a mirror cannot resurrect an unwatched hub. The FE already matched all three key forms; nothing had ever published there, so the matcher had nothing to match.
  **Clause 3 — the LOOPS hook was the right one, and the reason is the mirror.** Extended `pages/loops/useRunStream.ts`, not `useWorkflowStream.ts`: the workflows hook was already correct for its own hub, but the clause-2 mirror puts `workflow_*` frames on the LOOP hub, whose `RUN_LIFECYCLE` never registered them — and EventSource silently drops an event type with no listener, so the mirror would have been invisible. Also added `unwrapRunBatch`, since the mirror forwards `workflow_batch` and that hook had no unwrapper. Kept in step with `WORKFLOW_LIFECYCLE` by a DERIVED test rather than a copied list, so the two cannot drift.
  **Clause 4 — touched feed + PinnedArtifacts.** `service.touched_items()` rides the introspect payload; `workflows/pinned.py` + two artifact routes + a hard-imported `widgets/PinnedArtifacts.tsx` on the dashboard, following `channel_trust`'s precedent of a dedicated module owning its own entity file.
  **DEVIATION — the touched feed carries artifacts and dropped files only.** Knowledge mutations have no run attribution: `journal.py` has no knowledge/lineage kind, so a knowledge row could only be guessed from timing, and a feed that guesses is worse than one that is honest about its scope. §6.5's own note says closing this is a journal-format change. Recorded in the function docstring rather than silently omitted.
  **Two real defects the whole-tree gates caught, neither visible to a path-scoped run.** (1) `primitiveAdoption`'s sibling ratchet flagged `border-outline-low` in `IntrospectPanel` — **no such token exists, so it emits zero CSS** and that divider would have been invisible in production while every test passed. (2) `tsc` rejected an `as never` route stub in the widget test. Both argue for the full `npm run test --workspace web` + `tsc` rather than a targeted vitest.
  **Real home verified clean** — no new dirs under `~/.personalclaw/workflows/runs/` (all 22 still date from Aug 9) and no `pinned_artifacts.json` there. The new test isolates via `PERSONALCLAW_HOME` **and** the `entity_routes.config_dir` binding, since patching the loader alone does not isolate a module that captured `config_dir` at import — the trap that polluted the real home earlier the same day.
  **Load flake, measured not assumed.** A broad `-k "workflows or artifact or route or server or sel or entity"` sweep gave 29 failures (`Runner is closed` / `Timeout`) across 3 files; serially with `-n0` on a clear field: 111 passed. My independent re-run of `test_workflows_controller.py` got 85/85 — and 157 passed across the new suites plus every ratchet while the machine was BUSY, which is stronger than a clean-field green.
  **Gate (independent, by EXIT CODE):** black/isort/flake8 all 0; `mypy src/personalclaw harness` clean on 792 files; 157 passed across `workflows_api` + `pinned` + `watchdog` + `transport_doctrine` + the three full-suite-only ratchets; FULL `npm run test --workspace web` 1245 tests / 130 files; `tsc --noEmit` 0; `reference/routes.md` regenerated with exactly the 3 new routes (verified twice); `consistency-audit.json` moved `filesScanned` 434→436 for the two new components with **`driftHits` held at 7** — no drift blessed, and the timestamp-only churn reverted.
  **FOLLOW-UP (same PR) — the doc-coverage ratchet caught a missing row for `pinned.py`.** `test_workflows_hardening::TestDocumentationAccuracy::test_the_doc_covers_every_module_that_EXISTS` asserts every `workflows/*.py` has a row in `docs/architecture/workflows.md`; the new module had none. A real gap, not a flake: deterministic set-difference over `root.glob("*.py")`, naming this atom's OWN new file. **My verify pass missed it** — I ran that suite on the WV-12 branch earlier the same day but only ran `workflows_api`/`pinned`/`watchdog`/`transport_doctrine` here, so a full-suite-only ratchet that a targeted selection cannot reach went unchecked twice in one stack. The rule this reinforces: a new `workflows/*.py` module means `test_workflows_hardening` is part of the gate, always.
- [2026-08-11][WF2WOR-10 / Success Criterion 9] CLOSED OUT — third of the owner's partial-atom queue. All five clauses closed; no dependency added; two inert-control discoveries recorded.
  **The gap: a complete export PLANNER with zero importers.** `workflows/project_export.py` shipped `plan_export`, `ExportPlan`/`Entry`, `artifact_digest`, `run_digest`, `safe_member`, `verify_entry`, and the secret-exclusion policy (`secret_basenames`/`excluded`) — all written, all tested, and **nothing imported it**. A planner nothing calls exports nothing, so this atom is the I/O and the surfaces, not the logic; none of the above was reimplemented and no second path-safety checker was written.
  **Clause 1 — archive I/O.** New `workflows/project_archive.py`: `export_project_archive` → `read_project_files` (allowlist walk, symlinks skipped) → `plan_export` → `write_archive`, using `portability.py:208`'s exact `zipfile.ZipFile(buf,"w",ZIP_DEFLATED,compresslevel=6)` pattern rather than a new convention. Extraction takes a per-call `tempfile.mkdtemp(prefix="pclaw-project-import-")` with `shutil.rmtree` in `finally` (the `packs/import_.py` shape). Path safety is the EXISTING `safe_member` plus a resolve-and-compare per member at write time. **Only plan-ACCEPTED entries are written**, so the archive and its manifest cannot disagree — a manifest describing a member the ZIP lacks would make sha256 verification meaningless.
  **Clause 2 — AES-GCM, with NO new dependency.** `cryptography` 49.0.0 is already declared as the `oauth2` extra (`pyproject.toml:115`); `pyproject.toml` is untouched by this commit. Guarded by `encryption_available()` so an install without the extra REPORTS the missing capability instead of failing at the click. PBKDF2-SHA256 600k iterations, per-archive salt + nonce, authenticated header, and wrong-passphrase and tampering share one refusal message — distinguishing them would tell an attacker which half they got right.
  **Clause 3 — the `projects` component.** Registered in `snapshot.VALID_COMPONENTS` + `COMPONENT_HELP`. A new `_store_selected` is shared by capture, both restore modes AND `merge_plan`, so `--dry-run --components projects` cannot describe a restore the real run would not perform — the dry-run/real divergence that makes a preview worse than none.
  **Clause 4 — the round trip is the proof, and it is a real one.** Export from home A, import into a tmp `PERSONALCLAW_HOME` that never saw the project, asserting brief + overview + 3 ledgers + template + artifacts.json + runs.json arrive byte-identical and sha256-verified **against the manifest** (not against the source, which would only prove the reader agrees with itself), with artifact bodies and run journals correctly absent. Zero-secrets is proven THREE ways against the live policy — no member is policy-excluded, no basename intersects `secret_basenames()`, and a planted sentinel appears in neither the raw ZIP bytes nor any decompressed member. The assertions READ `secret_basenames()`/`excluded()` rather than restating a list, so tightening the policy cannot leave the test asserting the old one.
  **Clause 5 — CLI/REST/FE.** `GET /api/projects/{id}/export`, `POST /api/projects/import?preview=1` (the static path registered BEFORE the `{project_id}` matcher, or the matcher would swallow it), `personalclaw project export|import` in a new `cli_project.py`, and an Export `HeaderControl` on the project header. Placed in the projects surface rather than `PortabilityPanel`: a per-project export belongs where projects live, while that panel is the whole-home portability surface.
  **DISCOVERY — `derived_within` had ZERO readers, and the consequence was a real backup defect.** Four inventory entries declare it (`projects`, `loop`, `skills`, `workspace`) and nothing read the field. `projects` declares `("*/worktrees",)`, yet snapshot's `_copytree_safe` and portability's `rglob` copied worktrees wholesale — so a backup of a home with a bound workspace carried an entire git checkout. Now enforced in BOTH directions, ancestor-matching rather than leaf-only: a leaf-only test excludes the empty directory and then carries its contents, which is why the test counts EXPORTED FILES instead of reading the predicate.
  **DISCOVERY — the checked-in `consistency-audit.json` was already stale, and I verified it is inherited rather than mine.** `filesScanned` 434→436 with `driftHits` held at 7; the two files are `IntrospectPanel.tsx` and `PinnedArtifacts.tsx` from WF2WOR-7 (HEAD~1), which added them without regenerating. Confirmed by checking HEAD~1's own file list: it touched neither the audit nor the scanner. Refreshed here as the correct fix rather than reverted as churn.
  **Gate (independent, by EXIT CODE):** black/isort/flake8 all 0; `mypy src/personalclaw harness` clean on 794 files; the 10 load-bearing tests run individually and named (round trip, zero-secrets-in-BYTES, real-home-untouched, encryption availability + round trip + no-passphrase refusal + nonce uniqueness, `derived_within` executor, safe download name); 229 passed across the new 32-test suite plus `snapshot`, `portability` and the three full-suite-only ratchets, no baseline edited; FULL `npm run test --workspace web` 1245 tests / 130 files; `tsc --noEmit` 0; `reference/*.md` regenerated with exactly the 2 new routes.
  **One pre-existing red, baseline-proven:** `test_harness_validate.py` 3/11 fail — the implementer STASHED its own work and re-ran to establish that, rather than arguing from plausibility. Known worktree-only flake (the suite shells out to a repo-relative `.venv/bin/python`); CI has a real venv. Not weakened.
  **Real home verified untouched:** 22 run dirs unchanged, no `pclaw-project-*` temp dirs anywhere, and nothing written under `~/.personalclaw` since 13:00 — checked by mtime, not by count.
  **FOLLOW-UP (same PR) — the same doc-coverage row was missing for `project_archive.py`, and I caught it BEFORE CI this time.** After fixing WF2WOR-7's `pinned.py` row I re-ran `test_workflows_hardening` on the rebased stack tip rather than assuming the fix was local, and it named this atom's own new module. Two modules in one stack missed the same full-suite-only ratchet, which makes the pattern the lesson rather than either instance: **a new `workflows/*.py` file always means `test_workflows_hardening` is part of the gate**, because no targeted `-k` selection over the atom's own surfaces can reach it.
- [2026-08-11][WF2WOR-5 / Success Criterion 2] CLOSED OUT — fourth of the owner's partial-atom queue. All four clauses closed; the earlier BLOCKED claim on the isolated workspace is WITHDRAWN as incorrect.
  **The gap: a complete compilation layer with zero callers.** `batch_compile` shipped the lints, the capability posture and the leaf contract, all reachable only from their own tests, so every batch spawn bypassed them. `subagent_run(tasks=[...])` looped N times into individual `SubagentManager` spawns instead — N fire-and-forget spawns have no run record, so they cannot render as one widget, survive a restart, or be retried per branch, and those three properties are the criterion. The cutover (N>=2; N=1 stays a raw spawn per `COMPILE_THRESHOLD`) persists the compiled spec as a def and starts a run against it, so restart survival and per-branch retry (`run-from` over the compiled node ids) are the paths every other run already uses rather than new machinery.
  **Clause 2 — the `__wf_depth` seam.** `mcp_shared.leaf_tool_denial` on `call_tool_with_logging`, the chokepoint every in-process MCP tool call crosses. Denial precedes arg validation, so a malformed call to a denied tool reads as denied rather than as a schema error. Reuses `ORCHESTRATION_TOOLS`/`is_write_tool` and `workspace.looks_secret` — no second copy of either policy.
  **DEFECT FOUND IN ITS OWN FIRST IMPLEMENTATION — the lease looked like protection while both executions proceeded.** `containers.claim` RENEWS for the same holder (so a worker that lost in-memory state is not locked out of its own work), so a holder derived from stable data — `run_id:node_id` — made every second attempt a renewal and passed both through. Adding the PID did not fix it either: two co-tenant sessions in ONE gateway share a PID, which is exactly the threat §1.5 names. Holder identity is now per-attempt (`engine.claim_holder`). A row-presence assertion would have passed the broken version, so the test asserts a second worker is REFUSED. Cost: a genuinely dead holder's branch waits out the TTL — the correct direction, since a stalled branch is visible and self-healing while a double execution is silent and can write twice.
  **DEFECT — the env carrier leaked across concurrent siblings.** The first draft read `os.environ`, which is process-global; batch leaves run concurrently in one gateway, so one leaf's read-only flag would have applied to its siblings. Rewired through the per-session `extra_env` seam, which also forces a cold session — a warm pooled worker would inherit the previous leaf's env.
  **DEVIATION WITHDRAWN — the isolated workspace was reported BLOCKED and was not.** The claim was that `dispatch_stage` has no provisioning call site. The applier is not per-node at all: it is run-level and fully wired at `controller._provision_workspace` -> `provisioning.provision`, gated by `provisioning.declares_workspace`, which reads a TOP-LEVEL `workspace:` block. The compiler wrote only `postures[node]["workspace_mode"]` — a review surface no applier reads — so provisioning silently no-opped for every compiled batch. This is the live-reader-of-an-unwritten-key shape, and `provisioning` itself names `worktree_path` as the precedent for what a second spelling costs. `Mode.SCRATCH` is in `ISOLATED_MODES`, so declaring the block gets real isolation from the existing applier; no per-node provisioning was invented.
  **...and emitting the block was NOT sufficient — it was erased mid-save.** `service.author_def` and `native_defs.save_def` each build their payload from an allowlist, neither of which carried the key, so a compiled spec that declared a workspace round-tripped to a persisted def that declared none — and the applier reads the PERSISTED def. Measured through the real save path before and after. Both allowlists now carry it, verified `author_def` -> `get_def` -> `declares_workspace` True -> `resolve_spec` -> `Mode.SCRATCH`, isolated, no fatal issues. A fix that stopped at the compiler would have been decoration.
  **BEHAVIOUR CHANGE, measured not assumed.** Declaring a workspace changes what a crash-survivor batch does at boot. `stamp_run` records `worktree_path` for EVERY isolated mode and `worktrees.substrate_for` reports anything with a recorded path as isolated, so a compiled batch now takes PAUSED / `SUSPENDED` with a Resume affordance instead of being auto-adopted. That is §5.2's designed behaviour for an isolated substrate, and it is pinned by test so a paused batch is not later read as a regression.
  **Clause 4 — roster, and a claim of mine that was WRONG.** `workflows/roster.py` had zero `src/` importers (its only caller was its own drift test), so a CI gate was standing in for a consumer; `batch_compile.agent_lint` is now the production consumer and refuses an unresolvable agent at compile. But my paired instruction — that `config["agent"]` has no reader and should carry the resolved SLUG — was wrong on both halves, and the implementer pushed back with evidence rather than complying. `config["agent"]` IS read, at `engine.py`'s dispatch (`cfg.get("agent")` -> `spawn`); my grep missed it because the dispatcher binds the config dict to `cfg`, not `config` — a spelling search where a value trace was needed. And persisting the slug would have BROKEN spawn: `_validate_agent` checks `requested in cfg.agents`, keyed by the CONFIG KEY, so `my-researcher` is rejected with a typed error while `My Researcher` is accepted — every multi-word agent in a batch would have failed. Resolution goes THROUGH the roster (slug-matching, so a display-name reference and a rename both work) and persists `entry.name`. Slugs are the matching key; the config key is the binding value. Verified live: both spellings compile to `config["agent"] == "My Researcher"`. `roster.catalog` reads `AppConfig.load().agents` — the same dict `_validate_agent` consults, one source, pinned by an AST test.
  **Gate (independent, by EXIT CODE, on the REBASED tip):** black/isort/flake8 all 0; `mypy src/personalclaw harness` clean on 795 files (required fixing a latent `FieldSpec.type` shadowing bug that made `item_type` unannotatable); the 38-test cutover suite serial; 86 passed across it plus `test_workflows_hardening`, `test_inert_surface_baseline` and `test_agent_reference` (regenerated in-commit, a genuine shrink); ratchet family 66 passed. Real home verified untouched at 22 run dirs.
  **Contention baseline, not a shrug:** a wide `workflow or engine or session` sweep showed 38 `Timeout`/`Runner is closed` reds. Isolated properly — the same three files pass 151/151 both stashed AND with the change, and `test_workflows_tools.py` passes 70/70 at `-n0` — matching the known async-workflows contention shape. The discriminator stands: a serial failure on a clear field would be a real regression.
- [2026-08-11][WF2WOR-11 / §1.6] DONE — project-scoped memory locality + knowledge project tagging. Every new field has a named live WRITER and a named live READER; nothing shipped as a decision layer awaiting a future call site.
  **Clause 1 — a project-owned run's memory is project-local.** New `memory_locality.py` (`project_memory_cwd`) + `RunController._bind_project_memory_cwd`, called LAST in `_prepare` (after `_provision_workspace`) and only when `services.cwd` is still empty, so an isolated worktree or a caller-supplied cwd — both deliberate bindings — still win. Writer: the controller sets `services.cwd`; reader: `ContextBuilder.get_memory_for(cwd)` → `memory_dir_for_cwd`, so the run's memory lands in `workspace/_ext/<slug(context_dir)>`. Driven, not asserted: `test_project_memory_locality.py` runs a real controller to completion and then writes through the resolved store, asserting the bytes land in the project partition and NOT in `_ext/_default`.
  **DISCOVERY — every project-owned run was writing into the shared `_default` partition.** `EngineServices.cwd` is never set in production (`gateway.py:2146` constructs the services without it) and is only overwritten for an ISOLATED workspace, so a non-isolated project run dispatched with `cwd=""` — which `memory_dir_for_cwd` maps to `_ext/_default`. Memory locality was not "partially built"; the partition seam existed and every run shared one pile.
  **Clause 2 — partition-first recall, ORDERING ONLY.** `context_engine.active_recall_block` (the live per-new-session recall path) now composes through `memory_locality.compose_recall`: the session's own partition first, then the GLOBAL partition, whose text is source-labeled and fenced with the real API — `fence_untrusted(source=CROSS_PARTITION_SOURCE, source_type="memory_partition", source_id="_default")` — and asserted with `security.is_fenced`, never a bare-marker substring check (an attributed fence carries no bare marker). **Never admission:** when the local partition has nothing and the global partition has the answer, the global block is returned ALONE; that inverse case has its own test, because a locality rule that dropped it would silently delete recall results. Both halves run inside the SAME bounded worker, so locality shares the existing timeout + circuit breaker rather than doubling a turn's recall budget. Opt-outs, each tested: a named `memory_store` provider (not cwd-partitioned), a session already in the global partition, and the gateway's own workspace alias (same store object → no duplicate block).
  **Clause 3 — knowledge project tagging + the sharing filter.** Writer: `knowledge-persist` stamps `project_id` / `run_id` / `sharing_policy` into `file_metadata` and files the item under `session_brief.project_tag`, with `project_id` threaded controller → `engine.dispatch` → `dispatch_action` payload (the same seam `run_id` already used, so no template restates an id it cannot know). Readers: (a) `session_brief.load_items` — the project brief; (b) new `knowledge/project_scope.py:project_items`, served on `GET /api/projects/{id}/linked` and rendered by `ProjectKnowledgeList` in `ProjectsSection.tsx` with a Private/Shared label and the owning project's name on a cross-container row.
  **DISCOVERY — the project brief was a live reader of a key NOBODY wrote.** `session_brief.load_items` has always queried the `project_tag` tag, and no writer anywhere produced it (repo-wide, `project_tag`'s only non-test reference was its own definition). So `{{brief.text}}` was empty for every project, silently, with the controller's `_session_brief` faithfully composing nothing. The provider's tag write is what makes that reader real; asserted as an OUTCOME (a non-empty rendered brief), not as a call site.
  **`sharing_policy` default = `private`, deliberately.** A run that forgets to declare a policy must not leak what it wrote into every other project's view: the safe reading of un-declared intent for a visibility control is "stays home", and widening later is a one-field edit while un-leaking is not. An unrecognised value fails CLOSED to private with an INFO log (a findable typo, not a silent swallow). The enum is closed and enumerated at every consumer — `visible_in_project` raises on an unhandled member, the FE maps it through an exhaustive `Record<SharingPolicy, string>` and ANNOUNCES an unmapped value rather than rendering blank, and both members are enumerated in the Python and vitest tests rather than spot-checked.
  **Ownership is first-writer-wins.** `project_id`/`run_id` describe who PRODUCED an item, so a second project re-persisting identical content records corroboration (mentions, `source_ref`) without moving the item into its own container; the project tag is derived from the OWNER in metadata, not from the reinforcing run, or "private to its project" would end wherever the last run happened to be. Only an EXPLICIT `sharing_policy` on a later write may change visibility. An item written outside a run gets no scope fields at all — a visibility field on a row belonging to no container reads as meaningful and is not.
  **DEVIATION — clause 1 is scoped to the RUN path, not project-bound chat sessions.** §1.2 states the contract as "project `context_dir` = default cwd fallback for **stage nodes**", and that is where it landed. Re-pointing a project-bound chat's `session.workspace_dir` (today `default_workspace_dir()`, `chat_handlers.py`) would trade a capability for a partition: cwd is also the native-tool root, so a project chat would LOSE the workspace root it can file-search today in exchange for a project-local memory partition. Not done unilaterally; the project's context dir is already granted to those sessions as an extra tool root, so the continuity half is unaffected.
  **Gate (by EXIT CODE):** `make lint` 0 (black/isort/flake8/mypy — mypy clean on 800 files); `pytest -k "project or memory or knowledge or recall"` 2138 passed / 2 skipped / 1 xfailed; `test_inert_surface_baseline` + `test_agent_reference` + `test_docs_lint_baseline` + `test_config_roundtrip` 35 passed with **no baseline regenerated** (the new modules are consumed by production call sites, so the inert-surface count did not move); FULL `npm test --workspace web` 1368 tests / 144 files, `tsc --noEmit` 0, `npm run build --workspace web` 0. No config field added, so no `config-baseline.json` change. Tests isolate via `PERSONALCLAW_HOME` **and** module-bound `config_dir` patches, and replace the process-global `context._memory_stores` partition cache per test — a leaked entry there would answer with a store rooted in another home.

### 2026-08-12 — catalog correction (WF2WOR-2 status), not a session

`WF2WOR-2` sat at `in_progress` in `docs/roadmap/atomic/dag.json` with `pr: "#190"` long after
that PR merged — session 51 above was already logged DONE, so the atom's status, not the work,
was stale. Corrected to `done` with the PR state as evidence (`#190` MERGED
2026-08-02T21:38:44Z, merge commit `11763fd2`, in `main`'s history), outcome-checked against
`workflows/needs_input.py` + `attention.py` (NeedsInputItem cards through the registered
`ItemKind.NEEDS_INPUT` seam) and the `resume_token` reply path. Audited alongside it: no other
atom in the catalog carries a `pr` marker while unfinished. `WF2LOO-9` and `WF2AUT-11` stay
`blocked` — their prerequisites are cross-plan EXT refs with no PR evidence either way, so
un-blocking them is an owner call, not a mechanical one. The rest of the correction was the
derived `dag` block, which `tools/regen_dag_derived.py` now regenerates and
`tests/test_roadmap_dag_derived.py` now ratchets.
