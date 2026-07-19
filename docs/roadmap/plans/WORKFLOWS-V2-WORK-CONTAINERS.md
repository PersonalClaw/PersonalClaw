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
