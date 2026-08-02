# Plan: Work-Container Hierarchy — Project as the Sole Umbrella

**Status:** PROPOSED (rev 2 — research-integrated 2026-07-12)
**Created:** 2026-07-11
**Depends on:** WORKFLOWS-V2.md Slices 0-4 (project_id threading + fork are already engine-level acceptance criteria there)
**Companions:** WORKFLOWS-V2-UNIVERSAL-PLANNING (planner collapse), WORKFLOWS-V2-LOOPS-EVOLUTION (loop retirement), WORKFLOWS-V2-LEARNING-FLYWHEEL (consumes RunStats + verification-debt metrics from here)

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
4. **The "fabrication threshold at 8–10 items" is not a thing, and Manus never published it.** The report read the Wide Research post directly: no such claim exists — it was a secondary-source artifact this plan must not encode. Measured degradation knees sit elsewhere entirely (~500 input tokens; ~570–670 residual-task tokens; batch size 2; or nowhere), and frontier models held quality to 32 batched and 100 enumerated items. The real mechanism is **task-budget displacement, not confusion**: coordination content *added* rather than displacing task content scored 150/150 correct even at a 95% coordination ratio. **[V]**
5. **Multi-agent frequently loses under fair evaluation.** In the largest unified re-benchmark (MASLab), on Qwen-2.5-72B only **2 of 9** multi-agent methods beat the single agent and **none beat plain self-consistency**. The cleanest same-model head-to-head favors the simpler scaffold: Agentless **32.00%** vs SWE-agent **18.33%** on SWE-bench Lite with the same GPT-4o, at ~28% of the cost. Fan-out costs 4–15× for single-digit gains *where it helps at all*, and the best cost outcomes in the whole literature come from **pruning** communication (AgentPrune 7.8× cheaper; G-Designer 95% fewer tokens), not adding it. **[V]**
6. **Cognition publicly reversed.** "Don't Build Multi-Agents" (2025) is partly retracted by "Multi-Agents: What's Actually Working" (2026-04-22, same author). Their current rule: **"writes stay single-threaded and the additional agents contribute intelligence rather than actions."** They now *prefer* an **unshared-context** reviewer (a blank-slate reviewer must reason backward from the implementation, and skips the coder's extraneous context) — materially retracting their own "share full agent traces" principle. Anyone citing the 2025 essay as Cognition's position is out of date. **[V that they wrote it]**
7. **The decision variable is coupling density, not item count** (r=0.65, p<0.05). Naive file-parallel fan-out bought **+0.9 points for +44% cost** on coupled work. **[V]**

### What the evidence does NOT settle (do not pretend otherwise in any task line)

- **The two clean iso-compute studies contradict each other.** Best available reconciliation: parallel *sampling* scales with budget; parallel *decomposition* does not. This is a genuine unresolved disagreement, not a gap in our reading. **[V]**
- **Anthropic's +90.2% is a real measurement but not compute-matched** (~3.75× tokens), on a private eval, on the task shape that maximally favors fan-out — and their own regression says **token usage alone explains 80% of variance**. Do not cite it as topology evidence.
- **Genspark's linear-context claim and Manus's homogeneity claim both have zero measurements.** Output-format/consistency variance across independent parallel agents — precisely Genspark's argument — **is untested anywhere**. So is duplicated work across parallel agents: no paper reports a duplication rate.
- **The noise floor governs everything above:** scorer swaps move results more than architecture does (79.0 → 25.6 in one case), format errors cause >50% of failures in some harnesses, run-to-run variance is 1–3 points, benchmarks run n=24–100, and **no paper token-matches its single-agent baseline**. **Treat any sub-5-point delta as unresolved** — including our own future measurements.

### The synthesis this plan adopts

> **Parallelize reads/analysis. Single-thread writes. Give each worker an explicit objective, output format, and boundary contract. Have one agent merge.**

This is the only position consistent with all the measured evidence, and it is where three independent parties converged (Anthropic measured the read-parallelism win; Cognition retreated to exactly this line; Genspark articulates it) — while MAST shows failures concentrate in system design (41.8%) and inter-agent misalignment (36.9%), the two things this discipline targets. **[P] — a convergence of independent positions, not a measured result.** Labelled honestly because it is the basis for a contract, and a future session must know it is not a benchmark.

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

### Cross-plan note for INTEGRATION-ARCHITECTURE (owner task)

`SubagentManager.spawn` is mutated by roughly six plans (`AUTONOMY-GUARDRAILS` `capability_class`, `EXECUTION-ISOLATION` `sandbox`, `MODEL-USE-CASES-V2` the `orchestration` axis, `HARNESS-CRAFT` worktree hydration, `WORKFLOWS-V2` `__wf_depth`, `TASKS-SOPS` foreach children) but appears **nowhere** in INTEGRATION-ARCHITECTURE's shared-seam table (§1.2), its verified-primitives list (§3), or its per-plan contract index (§5, which covers plans 31-54 and defers 1-30). That makes it an **unregistered fourth landmine** alongside the three §1.3 names. Recommend registering it with this plan as the owning contract, since §3 already declares it "the only spawn substrate."

### Amendment task table (extends §Task breakdown; run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

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
