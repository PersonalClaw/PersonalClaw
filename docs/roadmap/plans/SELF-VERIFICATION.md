# SELF-VERIFICATION

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/SV.md`](../atomic/SV.md) as 11 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Self-Verification — Spec-Driven Dev Harness + Event-Trace Replay + Self-QA Companion

**Status:** IN PROGRESS — §1 (spec harness + scanner + diff-aware run) and §2 (event-trace replay
substrate + baselines + MCP record/replay + loop resume-audit) landed 2026-07-26 (Sessions 1-4,
non-WF2 scope); see the Execution log.
🔴 **The S4 BLOCKED gate has CLEARED (verified 2026-08-04): WORKFLOWS-V2 Slices 0-11b are DONE and
`workflows/{engine,journal,store}.py` exist, so the WF2-gated remainder is now STARTABLE, not
blocked** — the two replay scenarios (`workflow-journal-projection`, `rewind-during-stream`, SV-5,
DONE) and the workflow-run half of resume-audit (`resume_audit.py::audit_workflow_run`, SV-6, DONE)
have both landed. Still open: the `harness/exemplars/` backfill (README-only today) and §3's Self-QA
Companion (zero footprint).
Also open: `python -m harness validate|scan` is not yet a CI gate — `ci.yml` lints the harness but
never runs its scanner. Original: PROPOSED (created 2026-07-13 from research synthesis, promoted from
backlog)

---

## Research Integration (2026-07-13)

- **NEW-7** (Self-QA Companion: commit trigger → per-commit user-impact triage with ledger-only skip records → deep as-a-user scenario generation (fault injection, restart/resume arcs, resource-growth assertions, real UI driving) → execution via Chrome DevTools MCP + terminal → findings to Inbox/Tasks with an evidence bundle (screenshots, MP4 + contact sheet + trimmed GIF, logs, SHA256'd manifest, Proof section) → optional fix branch) → §3. Supporting primitives it names — crabbox-style `required_artifacts` proof gates and failure capsules — are **already approved** (WORKFLOWS-V2 WF2-R3; LEARNING-FLYWHEEL LEARN-R8): §3 consumes them, never rebuilds them (see §6).
- **NEW-17** (spec-driven self-dev harness: rule/scenario/task markdown specs with YAML frontmatter, `validate/explain/run` CLI, static architectural-boundary scanner, diff-aware required-check selection, same-PR spec rule) → §1.
- **NEW-17** (event-trace replay regression: record real event streams as JSONL scenarios, replay offline into metrics — duplicate_event_rate, fanout, order violations — gate against baselines with hard + drift thresholds; MCP traffic record/replay; fresh-session resumability audit ride the same harness) → §2. The WORKFLOWS-V2-specific instance is already approved (WF2-R11, Slice 11); this plan builds the **shared substrate early** (Wave 1) and Slice 11 *consumes* it (see §6).
- **NEW-17 amendment** (step/milestone-snapshot delivery for the workflows-v2 build-out: per-wave standalone runnable exemplar + smoke script + rationale note, doubling as regression anchors and tutorials) → §4.1.
- **NEW-17 amendment** (machine-facing repo-gotchas `AGENT.md` checked into the repo: installed-apps sync, static/dist symlink, venv path) → §4.2.
- Sources: `clawx` (the working reference harness + `scripts/comms/` replay), `steipete-x-post` (the QA loop + crabbox evidence mechanics), `harness-engineering-course` (fresh-session test, review-feedback promotion, "mechanical checks beat remembered rules"), `easy-agent` (step/-snapshot delivery, AGENT.md gotchas genre).

---

## Overview

PersonalClaw's development process already has the *culture* this plan mechanizes — verified starting points:

- **The prose half exists and is unenforced.** The campaign/LEDGER protocol (`docs/prepub-campaign/`-style briefs), auto-memory gotcha notes (four-point config wiring, RUN_LIFECYCLE registration, installed-apps sync, static/dist symlink, venv interpreter), and hard-won bug-class knowledge (K42/K44/K45 stream coalescer, React-#310 hook order, destructive-test isolation that once deleted the user's real bound model) all live in markdown and memory notes. Nothing machine-checks them; every one has recurred at least once.
- **One boundary check already exists as a test** — `tests/test_action_schema_executor_parity.py` (the `ALLOWED_HOOK_PROVIDERS` ↔ action-provider-registry parity check, `validation.py:559`). It is the proof-of-pattern for §1's scanner: an architectural invariant expressed as an executable check. There are ~800 tests but no *spec layer* naming which invariants exist, which tests verify them, and which checks a given diff requires.
- **Pure folds are already the FE architecture** — `web/src/pages/chat/coalesceReducers.ts` (+ `.test.ts`, the K42/K44/K45 regression lock) and `web/src/pages/loops/runFold.ts` (unit-locked, shared by 4 surfaces). A replay harness that feeds recorded traces through these folds needs **zero refactoring** — the seam was built by the earlier bug fixes.
- **Backend event streams have narrow, tappable seams**: the multiplexed dashboard WS (`dashboard/state.py:_broadcast` :1477), per-resource SSE registries (`loop_sse()` :1056, key `loop:<id>`; the v2 engine adds `workflow:<run_id>` on the same registry), channel/inbox ingestion (`inbox_service.py:_ingest` :194), and MCP client calls (`mcp_client.py`). Each is one wrapper away from NDJSON recording.
- **The QA companion's ingredients all exist**: crons + zero-token cron scripts (`schedule_script.py` — `Skip`/`Done`/`Report` control flow, scripts path-fenced to `~/.personalclaw/crons/`, `resolve_script_path` :74), subagents (`subagent.py:spawn` :880, `silent`, `dry_run`), Chrome DevTools MCP (the exact tool the provider-integrity and manifest-vs-UI campaigns drove ≥50-cycle as-a-user validation with), the Artifacts entity (`artifacts/models.py:Artifact` :120, with `project_id`), the Inbox push sink (`native_source.post_to_inbox`, always-on), and native Tasks (`tasks/registry.py`). Missing is exactly what the backlog says: the composed template + triage prompt + evidence capture.
- **What does NOT exist** (verified): no spec layer, no boundary scanner, no trace recorder, no replay CLI, no baselines, no commit trigger of any kind (no vcs watch; `fs_watch.py` is UI-refresh-only and doesn't watch `.git/`), no screen-recording capture, no `AGENT.md`. `eval/` (scenario/runner/judge) is a *conversation-level* eval harness — a different axis (LLM behavior, not event-stream correctness) and explicitly not concurrency-safe (`eval/runner.py:216` mutates process env), so §2 does not build on it.

The three parts are one plan because they share one thesis — **the harness, not the agent (and not the human's memory), owns verification** — and one sequencing constraint: the replay substrate must exist before the v2 journal format calcifies.

**Soul guardrail:** this is repo dev-process infrastructure plus one bundled personal QA template — a few script files, markdown specs, JSONL traces, and a workflow the user's own machine runs against its own commits. No CI/CD fleet, no GitHub Actions dependency (the "CI gate" is the existing pytest/vitest suite + a Makefile target), no enterprise QA org. Findings are *proposed* (Inbox/Tasks + optional fix branch), never auto-merged — propose-don't-write applies to code the same as to memory.

---

## 1. Spec-Driven Self-Development Harness

Repo-inner layout (new, at `PersonalClaw/harness/` beside `src/`, `tests/`, `scripts/`):

```
harness/
  specs/
    rules/       # architectural invariants   (type: ai-coding-rule)
    scenarios/   # triage playbooks           (type: triage-scenario)
    tasks/       # per-fix/feature task specs (type: task)
  cli.py         # python -m harness  validate | explain | run | scan
  scanner.py     # static boundary scanner (§1.3)
  profiles.py    # profile → concrete command mapping (§1.2)
  traces/        # §2 recorded JSONL scenarios + baselines
  exemplars/     # §4.1 per-wave runnable snapshots
```

The CLI runs on the repo venv (`.venv/bin/python` at the repo root — the documented interpreter gotcha, now also §4.2 content). ClawX's reference is Node (`pnpm harness …`); PClaw's is Python for zero new toolchain.

### 1.1 The three spec kinds (markdown + YAML frontmatter, per the ClawX reference)

- **Rule specs** — one architectural invariant each: frontmatter `{id, type: ai-coding-rule, statement, appliesTo: [path globs], requiredTests: [pytest/vitest node-ids or commands], scanner: <check-id>?, source, expiry_condition}` + a body written FOR the coding agent (why the rule exists, the bug that created it, what compliance looks like). The `source`/`expiry_condition` metadata follows the harness-course rule-hygiene doctrine — rules are audited and deleted like tech debt, not accumulated.
- **Scenario specs** — triage playbooks: symptom family, scoped file paths, required rules, probe order, known causes + mitigations, acceptance criteria, redaction notes. Seed set = the recurring diagnosis genres from memory notes: gateway-restart-for-backend-validation, FE-rebuild-browser-cache, installed-app-copy-sync, stream-coalescer symptoms, local-model download-detection.
- **Task specs** — one per fix/feature: frontmatter-only `{id, title, scenario?, taskType, intent (one sentence), touchedAreas: [paths], expectedUserBehavior (observable outcomes), requiredProfiles, requiredRules, requiredTests, acceptance: {positive: [...], negative: [...]}}`. **Negative acceptance is mandatory** ("renderer does not add direct IPC calls" genre) — the half prose LEDGER entries always drop.

### 1.2 `validate | explain | run` + profiles

- `validate` — spec-shape validation per type (schema of the frontmatter, dangling rule/test references, `requiredTests` node-ids actually exist via `pytest --collect-only`/vitest list).
- `explain <task>` — unions profiles from scenario + task, prints the concrete commands + rules + tests a change must satisfy (the agent-facing "what do I owe before this is done" surface).
- `run <task|--diff>` — executes the union: mapped profile commands (`fast` → targeted pytest subset; `web` → vitest; `replay` → §2.3 compare; `full` → the sharded suite, per the full-suite-native-segfault note) + the scanner over changed files.
- **Diff-aware required-check selection:** `run --diff` (against `git merge-base`) computes touched areas and *forces* profiles independent of what the task spec claims — touching `web/src/pages/chat/` or any SSE/WS emission path forces the `replay` profile; touching `config/loader.py` or any dataclass with `_meta` forces the config-wiring scanner check; touching `action_providers/` forces the parity test. The spec author can add requirements; the diff can only add more, never remove.

### 1.3 Static architectural-boundary scanner

Pure-static checks (AST/regex over changed files, no execution), each with a stable check-id referencable from rule specs. Seed checks, all derived from *proven* PClaw bug classes:

| Check-id | Invariant (source of truth it guards) |
|---|---|
| `config-four-points` | a new field on a config dataclass with `_meta` appears in `AppConfig.load()`'s mapping AND `to_dict()` (loader.py — the silent-drop gotcha), and if runtime-editable, in `_EDITABLE_CONFIG` (`dashboard/handlers/core.py:363`) |
| `sse-event-registered` | every event type string emitted through an SSE registry publish appears in the FE lifecycle union (`useRunStream.ts` `RUN_LIFECYCLE` or the v2 `WORKFLOW_LIFECYCLE`) — EventSource silently drops unregistered types |
| `hook-provider-parity` | any new `register_action_provider` name appears in `ALLOWED_HOOK_PROVIDERS` (`validation.py:559`) — promotes the existing parity *test* into a diff-time check |
| `app-sdk-boundary` | code under repo-root `apps/` imports core only via `personalclaw.sdk.*` (the `sdk.net`/`sdk.security` facade contract) |
| `fence-at-ingestion` | new call sites reading external/channel text into prompts call `fence_untrusted` (heuristic, WARNING-level) |
| `destructive-test-isolation` | tests touching `local_models`/config dirs carry a `tmp_path`/monkeypatch fixture (the deleted-L6-model bug class) |
| `no-naive-transcript-cut` | transcript/journal truncation sites reference the dangling-tool-result walk-back helper (the easy-agent pairing invariant, adopted by WF2 rewind) |

Scanner findings are WHAT/WHY/FIX-formatted (the agent-oriented error standard) so a coding agent self-corrects without a human.

### 1.4 The same-PR rule (process, enforced by the harness itself)

Every recurring constraint or fixed bug adds/updates a rule or scenario spec **in the same commit** as the fix. Enforcement is diff-aware: `harness run --diff` warns when a diff's commit message matches fix-shaped patterns (`fix|bug|regression`) but touches nothing under `harness/specs/`. This is the mechanization of the existing memory-note habit — "every fixed bug becomes permanent" — moved from auto-memory (private, decays, per-agent) into the repo (versioned, shared with every coding agent, greppable). The LEARNING-FLYWHEEL lesson→rule-spec promotion edge (its curator proposing a repo rule spec from a recurring lesson) targets exactly this directory — this plan creates the destination; the flywheel's proposer stays in that plan.

---

## 2. Event-Trace Replay Regression Substrate

**Sequencing (the critical constraint):** WORKFLOWS-V2 already carries the journal-replay harness as an *acceptance criterion* scheduled in Slice 11 (WF2-R11) — i.e., Wave 4, after the journal format has been consumed by the flywheel, the cockpit, and retention. This plan pulls the **substrate** forward to Wave 1 so that the journal event format (`run_id|node_id|epoch|seq|state` dedup key, snapshot-vs-delta contract, event-fold law) is validated by recorded-trace replay **while Slices 0-2 are being built** — format defects get caught before migration, not after. Slice 11 then *runs* this harness against its four required scenarios instead of building one.

### 2.1 Trace format + recorder taps (real seams)

One NDJSON line per event: `{ts, stream, key, seq?, type, payload}` where `stream` ∈ `{ws, sse:<registry_key>, inbox, mcp, journal}`. Recording is opt-in via `PERSONALCLAW_TRACE_DIR` (env; no config surface — this is dev tooling, not a user feature) writing under that dir, one file per stream+session. Taps, each a ~10-line wrapper at an existing chokepoint:

- **Multiplexed WS**: `DashboardState._broadcast` (`dashboard/state.py:1477`) — every envelope.
- **Per-resource SSE**: the `SseRegistry` publish path (`loop_sse()` :1056 today; the v2 `workflow:<run_id>` registry lands on the same class, so the tap covers it for free).
- **Channel ingestion**: `inbox_service.py:_ingest` (:194) — raw item in, alert decision + broadcast out.
- **Workflow journal**: no tap needed — `events.jsonl` **is already the trace**; the recorder only captures the SSE *projection* of it, so replay can assert journal→widget-stream fidelity (the exact pipeline WF2-R11 protects).
- **MCP traffic** (NEW-17 rider): a record wrapper at the `mcp_client.py` call boundary capturing JSON-RPC request/response pairs as NDJSON; `replay` serves them back as a fake MCP server for deterministic offline debugging of tool integrations (the mcporter `record`/`replay` shape).

Traces are redacted at write time (`security.redact()` — credentials + exfil URLs) because they get checked into `harness/traces/` as fixtures.

### 2.2 Replay + metrics (offline, no gateway)

Two replay drivers, matching where the pure folds live:

- **Python** (`harness/replay.py`): folds backend-stream traces into metrics — `duplicate_event_rate` (by dedup key: `run_id|node_id|epoch|seq|state` for workflow events, per WF2-R11's specified key; per-type structural fingerprint where no seq exists, the ClawX fallback), `event_fanout_ratio`, `order_violation_count`, `reconnect_loss_count`, per-stream p50/p95 inter-event latency.
- **Vitest** (`web/src/harness/replay.test.ts`): feeds chat-stream traces through `coalesceReducers.ts` and run traces through `runFold.ts` (and the future workflow event-fold, which WF2 §5 mandates be pure and unit-locked exactly so this works) — asserting the fold's terminal state matches the trace's recorded snapshot and no intermediate state violates monotonicity. This makes the K42/K44/K45 bug class a *replayable* regression, not just a hand-written unit test.

### 2.3 Baselines + gating

`harness/traces/baselines.json` — per-scenario checked-in metric baselines. `harness run` (profile `replay`) compares with **hard thresholds** (duplicate_event_rate ≤ 0.005, order_violations = 0, message loss = 0, fanout ≤ 1.2 — the ClawX-proven values as defaults) + **relative drift tolerances** (p95 +15%) ; **a missing required scenario = fail** (silently dropping a scenario is how baselines rot). Required scenario set, recorded from real runs (not synthesized):

1. `happy-path-chat` (send → stream → tools → done)
2. `gateway-restart-during-run` (the ClawX flagship; PClaw analog: restart during a loop/workflow run, reattach)
3. `history-overlap-guard` (session reload mid-stream)
4. `workflow-journal-projection` (v2 events.jsonl → SSE, recorded against the Slice 1-2 engine as it lands — **this is the pre-migration gate**)
5. `rewind-during-stream` (v2; epoch supersede-drop proven by trace)
6. `channel-ingestion-flood` (inbox dedup + alert-once)

### 2.4 Fresh-session resumability audit (rides the same harness)

A scenario kind `resume-audit`: kill the process state, resume the persisted entity **from disk alone**, and mechanically assert the resumed context can answer *what's done / what's verified / what's next / how to verify* (the harness-course Fresh Session Test, generalized). Concrete assertions per entity: loops — `reap_orphaned_loops` re-arms and the next cycle's brief references the last finding (findings COUNT is the cycle clock, in-memory watchdog counters are documented as non-resumed); workflow runs — the journal replay reconstructs frontier state byte-equal to the pre-kill snapshot (the WF2 event-fold law, tested destructively). This audit would have caught the historical dead-resume bugs and becomes a required `replay`-profile member once the engine lands.

---

## 3. Self-QA Companion (Wave 2 — first flagship consumer of the engine)

The composed loop, per steipete: **commit → triage → scenario → as-a-user execution → evidence → findings → optional fix branch.** Every primitive it needs is either shipped or approved elsewhere; this section is the composition plus the two genuinely new pieces (triage/scenario prompts, evidence capture).

### 3.1 Commit trigger (interim seam, honestly stated)

There is **no vcs trigger today**, and the approved one (AUTOMATION-SUBSTRATE AUTO-R12: `file` kind with a `vcs` preset watching `.git/refs/heads/*`, content-hash dedup, changed-delta payload) is Wave 3. The Wave-2 companion therefore ships with the **existing** seam: a bundled zero-token cron script (`~/.personalclaw/crons/selfqa_commit_watch.py`, `schedule_script.py` contract) on an `every`-kind job that runs `git rev-parse HEAD` in the watched repo, compares against its last-seen state file, and raises `Skip` (no new commits — silent, zero cost) or `Report` with the new SHA list — which the job's action (`run-workflow` provider, already in `ALLOWED_HOOK_PROVIDERS`) turns into a QA workflow run with `{{inputs.commits}}`. When AUTO-R12's vcs preset lands, the cron script retires and the same template binds to the real trigger — the template is the durable half, the trigger is a swap. This disposition is recorded in §6.

### 3.2 The `self-qa` bundled workflow template

A v2 template (lands in the bundled template pack) with this node shape:

1. **`triage` (infer node, cheap tier)** — per-commit user-impact classification over `git show --stat` + message. Output contract: `[{sha, impact: test|none|user, rationale (one line), scenario?}]`. **Skips are ledger-only records with the rationale** ("assertion maintenance only") — the two-weight run-record discipline AUTOMATION-SUBSTRATE already adopted (its Codex-triage-inbox behavior); the run inbox shows *why* nothing ran, never silence. Impact triage as planning step zero is also UNIVERSAL-PLANNING's approved primitive — the template consumes the convention, defines nothing new.
2. **`scenario-gen` (stage node)** — for each impactful commit, generate ONE deep as-a-user scenario naming: entry surface + real-UI driving steps, at least one **state mutation** (send/create/toggle/cancel — never render-checking, per the user's reinforced validation feedback), **fault injection** where the diff touches error paths, **restart/resume arc** where it touches persistence, **resource-growth assertion** where it touches caches/queues, and backend-persistence inspection. This prompt is the institutionalization of `feedback_deep_asuser_validation` + `feedback_validate_changed_mechanisms` — the memory notes become a versioned prompt template in the repo.
3. **`execute` (stage node, `isolation` + `tools_posture` per WF2)** — a subagent (spawned through the normal `SubagentManager.spawn` engine path, `silent=True`, `__wf_depth` enforced) drives the scenario against the **live local gateway UI** via the Chrome DevTools MCP server bound through the existing MCP connector config (`mcp_client.py`/`mcp_discovery.py`) + terminal for backend inspection. It runs against the user's real instance on their machine — personal-scale by construction; the FE-rebuild-cache and gateway-restart gotchas from §4.2's AGENT.md are injected into the stage prompt as context.
4. **`evidence` (`required_artifacts` gate)** — the node cannot complete until the declared proof globs exist (WF2-R3, engine-enforced, independent of agent self-report): `screenshots/*.png`, `recording.mp4`, `manifest.json`. §3.3 supplies the capture mechanics.
5. **`file-findings` (action node)** — PASS → ledger-only record. FAIL → (a) an Inbox item via `post_to_inbox` (the native push sink — no new inbox provider) carrying the evidence bundle reference, and (b) a Task via the native task provider with the scenario text as body + reproduction steps. Reproducible failures additionally emit a **failure-capsule proposal** through LEARNING-FLYWHEEL's approved LEARN-R8 path (repro command + failure_signature + forbidden_success_modes) — the companion is a capsule *producer*, the flywheel owns the capsule lifecycle.
6. **`fix-branch` (optional stage, default OFF, config-gated)** — on a confirmed finding, spawn a coder subagent on a `pclaw/selfqa-<sha8>` branch (the existing `loop/worktree.py` worktree machinery) producing a proposed diff. **Never merged, never pushed** — the branch name lands in the Task; the human reviews. Propose-don't-write for code.

The whole run executes under the AUTONOMY-GUARDRAILS substrate once it lands (headless profile, budgets, denylist) — the companion adds no bespoke safety machinery.

### 3.3 Evidence bundle capture (the genuinely new mechanics)

- **Screen recording**: ffmpeg (`avfoundation` on macOS) capturing the driven browser window for the scenario's duration; post-process into a **contact-sheet PNG** (ffmpeg tile filter, 1 frame/5s) and a **trimmed GIF** (palettegen, failure window ±10s) — watchable proof for unattended runs, per crabbox's artifact set. ffmpeg presence is a template `metadata.requirements` entry (WF2 run-start preflight blocks cleanly if absent, instead of degrading at node 4).
- **Manifest**: `manifest.json` — schema-versioned, per-file `{kind, name, size, sha256}` — written by the execute stage, verified by the evidence gate.
- **Registration**: the bundle registers as **one Artifact** (the manifest as content, files under the artifact's dir, `project_id` set) — exactly the WORK-R4 "evidence bundle = Artifact composition" contract. The cockpit **Proof section** (Summary / Before-After / Evidence) is WORK-CONTAINERS' approved rendering; this plan produces conforming bundles and adds **no FE surface of its own**. Until WORK-CONTAINERS' Proof section ships, the bundle is still a browsable Artifact — degraded but complete.

---

## 4. Delivery-Pattern Riders (NEW-17 amendments)

### 4.1 Milestone exemplars for the v2 build-out

Each WORKFLOWS-V2 wave/slice landing adds one entry under `harness/exemplars/<slice>/`: a standalone runnable spec exercising that slice's mechanism (e.g., Slice 2: a 3-node run with a failing `required_artifacts` gate), a smoke script (run + assert, ≤30s), and a rationale note (what the slice added, what the exemplar proves, per the easy-agent `step/` pattern). Exemplars are triple-duty: regression anchors (`harness run` profile `exemplars`), recorded-trace sources for §2.3 scenarios, and tutorials for future coding agents. This is a **process obligation on WORKFLOWS-V2 sessions** (one small artifact per slice), owned/enforced by this plan's harness (`validate` flags a slice merged without its exemplar via the same-PR rule).

### 4.2 `AGENT.md` — machine-facing repo gotchas

One checked-in file at repo-inner root, deliberately distinct from human docs: the curated gotcha list any coding agent needs — installed apps sync via `POST /api/apps/{name}/update` (repo `apps/` edits don't reach the gateway), `static/dist` must be a SYMLINK to `web/dist` (a copy serves stale SPA), the venv interpreter path, gateway-restart-for-backend-changes vs FE-live-from-dist, FE-rebuild browser-cache reload, `fill()` vs React onChange, four-point config wiring, the "two config dirs" genre. Today these live only in the user's private auto-memory — invisible to any other agent, and lost if memory resets (which has happened). Each entry cross-references its rule spec where one exists.

---

## 5. Provider-Fidelity Wiring (where each piece plugs in)

- **No new provider TYPE and no new action provider.** The commit watcher is a cron *script* (the `run-script`/schedule-script seam, path-fenced); the QA run fires through the existing `run-workflow` action provider — already in `ALLOWED_HOOK_PROVIDERS` (`validation.py:559`), so **no change to that frozenset is needed**. If a later revision did add an action provider, it MUST be added there or hook create/update rejects it — restated because this plan is exactly where someone might be tempted to invent a "qa-run" provider; the answer is no, `run-workflow` + template inputs cover it.
- **Templates**: `self-qa` ships in the WORKFLOWS-V2 bundled template pack (the `defs.py` bundled-sync path, Slice 0), same as every other bundled template — no special registration.
- **MCP**: the Chrome DevTools MCP server binds through the existing MCP connector configuration (`mcp_client.py` / `mcp_discovery.py`); the record/replay wrapper (§2.1) wraps that client, changing no contract.
- **Artifacts**: evidence bundles use the existing Artifact entity + provider registry (`artifacts/registry.py:register_provider`) — no new artifact provider; the bundle is a native artifact whose content is the manifest.
- **Inbox / Tasks**: findings go through `native_source.post_to_inbox` (in-core push sink) and the native `TaskProvider` (`tasks/registry.py`) — no new source/provider.
- **Config** (the one small runtime surface — everything in §1/§2 is env/CLI, deliberately config-free): a `self_qa` sub-config on the dev/agent section: `{enabled: bool (default False), watched_repo: str, fix_branch_enabled: bool (default False), max_scenarios_per_fire: int (default 3)}` — wired through **all FOUR points**: (a) dataclass fields with `_meta(label, help)` (schema reachability tests enforce), (b) `AppConfig.load()` explicit field mapping (omission = silent drop), (c) `to_dict()`, (d) `_EDITABLE_CONFIG` (`dashboard/handlers/core.py:363`) + FE toggle for `enabled`/`fix_branch_enabled`.
- **SEL**: companion runs audit under their session keys like any cron/subagent work (`sel.py` source inference); no new source prefix invented.
- **Memory vs Knowledge boundary**: everything this plan persists — specs, traces, baselines, exemplars (repo files), evidence bundles (Artifacts), findings (Inbox/Tasks entities), commit-watch state (a file under the cron-scripts dir) — is repo/dev/harness state. **Nothing writes to `memory.db` or `knowledge.db`.** Lessons *about* QA outcomes (a template that keeps failing a scenario) flow through LEARNING-FLYWHEEL's proposal queue, propose-don't-write.

---

## 6. Disposition & Dependency Notes (no duplication of approved work)

| Approved mechanism | Plan + rec ID | This plan's relationship |
|---|---|---|
| Journal replay regression harness (4 scenarios, metrics, baselines) | WORKFLOWS-V2 **WF2-R11** (Slice 11) | §2 builds the **shared substrate in Wave 1**; Slice 11 becomes a consumer running its scenarios on it. The dedup key, snapshot-vs-delta contract, and event-fold law are WF2's spec — §2 tests against them, defines nothing competing |
| `required_artifacts` proof gates + engine-owned completion | WORKFLOWS-V2 **WF2-R3** | §3.2's evidence gate *uses* it verbatim; no parallel gate mechanism |
| Evidence bundle = Artifact manifest + Proof section + needs-input carrying bundles | WORK-CONTAINERS **WORK-R4** | §3.3 produces conforming bundles; rendering stays in WORK-CONTAINERS |
| Failure capsules (repro + failure_signature + forbidden_success_modes) + capsule replay as lesson decay | LEARNING-FLYWHEEL **LEARN-R8** (§3.3d) | §3.2 step 5 is a capsule *producer*; lifecycle/replay stays in the flywheel |
| `forbidden_success_modes` in judge hints | LOOPS-EVOLUTION **LOOP-R1** | referenced by capsule payloads; not re-specified |
| `vcs` trigger preset (file kind, content-hash dedup, delta payload) | AUTOMATION-SUBSTRATE **AUTO-R12** (Wave 3) | §3.1 ships an interim cron-script watcher; **retire it** when AUTO-R12 lands (explicit retirement item) |
| Two-weight run records / skip-rationale ledger rows; triage-first convention | AUTOMATION-SUBSTRATE §1.3 / WF2-R15 + UNIVERSAL-PLANNING impact-triage | §3.2 step 1 consumes the conventions |
| Lesson→rule-spec promotion (curator proposes machine-checkable rules) | LEARNING-FLYWHEEL (rule-spec learnings) | §1 creates the **target directory + schema**; the proposing curator stays in the flywheel |
| Held-out replay gate / GateOK (template-diff acceptance) | LEARNING-FLYWHEEL **LEARN-R2** | different axis (template quality vs event-stream correctness); no overlap — GateOK replays *runs*, §2 replays *event traces* |

**Dependency edges:** §1 depends on nothing. §2 depends on nothing to build, but its `workflow-journal-projection` scenario is recorded against WF2 Slices 1-2 output as those land (co-scheduled, Wave 1). §3 depends on WF2 Slices 0-5; §3.3's Proof rendering depends on WORK-CONTAINERS (graceful without it); §3.1 carries the AUTO-R12 retirement note; the whole of §3 inherits AUTONOMY-GUARDRAILS when present.

---

## 7. Implementation Effort

**~6 sessions.**

- **Session 1 — spec harness core (§1.1, §1.2, §4.2)** *(Wave 0/1-compatible)*: `harness/` layout; three spec schemas + `validate`/`explain`; profile→command mapping; `AGENT.md` written and cross-referenced; first 6-8 rule specs + 3 scenario specs seeded from the known bug classes.
- **Session 2 — scanner + diff-aware run (§1.3, §1.4)**: the seed check table; `run --diff` forcing profiles from touched areas; same-PR-rule warning; WHAT/WHY/FIX finding format; Makefile target wiring the harness into the standard test entrypoint.
- **Session 3 — replay substrate (§2.1, §2.2)** *(Wave 1, co-scheduled with WF2 Slice 0-1)*: trace format + redaction; WS/SSE/inbox recorder taps behind `PERSONALCLAW_TRACE_DIR`; Python metrics fold; vitest replay driver through `coalesceReducers.ts`/`runFold.ts`; record + commit the first three chat-side scenarios.
- **Session 4 — baselines + journal gate + riders (§2.3, §2.4, §4.1)** *(Wave 1, co-scheduled with WF2 Slice 2)*: baselines.json + hard/drift compare + missing-scenario-fails; record `workflow-journal-projection` + `rewind-during-stream` against the young engine and **gate the journal format on them**; MCP record/replay wrapper; `resume-audit` scenario kind; exemplars dir + the Slice 0-2 exemplars backfilled.
- **Session 5 — Self-QA companion core (§3.1, §3.2)** *(Wave 2)*: commit-watch cron script + state file; the `self-qa` bundled template (triage prompt with skip-rationale contract, scenario-gen prompt encoding the deep-as-a-user method, execute stage with DevTools MCP binding, findings→Inbox/Tasks); `self_qa` config through the four wiring points.
- **Session 6 — evidence + fix branch + validation (§3.3, §3.2 step 6)** *(Wave 2)*: ffmpeg capture + contact sheet + GIF + SHA256 manifest + Artifact registration + `required_artifacts` gate; optional fix-branch stage on the worktree machinery; end-to-end as-a-user validation — real commits, real triage table, real evidence bundle reviewed from the Inbox.

Sessions 1-2 ship standalone value immediately; 3-4 are the ones that must not slip past WF2 Slices 0-2.

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| Spec rot — specs accumulate and go stale like any docs | `source`/`expiry_condition` metadata on every rule + `validate` flags rules whose `requiredTests` no longer collect; rules are deleted like tech debt (harness-course doctrine); the flywheel curator later audits them |
| Recorded traces go stale as event schemas evolve | traces are schema-versioned; `compare` fails loudly on unknown fields; re-recording a scenario is a documented one-command operation, and the exemplars (§4.1) are the re-record sources |
| Baseline gate becomes a rubber stamp (thresholds loosened under pressure) | threshold changes must land with a rationale line in the baseline file (spec-history idiom); missing-scenario-fails prevents silent scenario deletion |
| Replay lands too late to gate the journal (the whole point) | Sessions 3-4 are explicitly co-scheduled with WF2 Slices 0-2 in Wave 1; the roadmap wave table gets this edge, not just this plan's header |
| Companion burns tokens/time on noisy commit streams | triage-first with ledger-only skips (cheap tier); `max_scenarios_per_fire` cap; `every`-kind cron ≥ hourly by default; inherits AUTONOMY-GUARDRAILS budgets when they land |
| Screen recording is fragile (permissions, ffmpeg absence, window focus) | ffmpeg + screen-recording permission are template `requirements` (preflight blocks cleanly); recording failure degrades to screenshots-only with `degraded_reason` on the node — the evidence gate's globs distinguish full vs degraded bundles |
| Self-QA driving the live UI collides with the user (or a co-tenant session) | companion runs open a NEW browser page (the documented co-tenant discipline), default to off-hours cron windows, and never mutate git state outside its own `pclaw/selfqa-*` branches |
| Interim commit watcher lingers after AUTO-R12 | explicit retirement row in §6 + a rule spec asserting the cron script is absent once the vcs trigger kind exists (the harness polices its own migration) |
| Fix branches accumulate unreviewed | fix-branch stage default OFF; branches named + linked in the Task; a scenario spec covers pruning stale `pclaw/selfqa-*` worktrees |

---

## Success Criteria

1. `python -m harness validate` passes on a seeded spec set of ≥8 rules / ≥3 scenarios / task specs for every fix merged after the harness lands; a task spec with a dangling `requiredTests` reference fails validation.
2. `harness run --diff` on a diff touching `web/src/pages/chat/` forces the replay profile even when the task spec omits it; on a diff adding a config dataclass field without the `AppConfig.load()` mapping, the `config-four-points` scanner check fails with a WHAT/WHY/FIX message naming the missing wiring point.
3. Replaying the recorded `happy-path-chat` and `history-overlap-guard` traces through `coalesceReducers.ts` reproduces the recorded terminal state with `duplicate_event_rate = 0` and zero order violations — and a deliberately re-introduced K44-class coalescer bug is caught by replay, not by a hand-written unit test.
4. The `workflow-journal-projection` scenario is recorded and green **before** any WORKFLOWS-V2 Slice 3+ consumer reads the journal format; a format change that breaks the event-fold law fails `compare` against the checked-in baseline; a missing required scenario fails the run outright.
5. The `resume-audit` scenario kills and resumes a persisted loop and a persisted workflow run from disk alone, and mechanically verifies done/verified/next are answerable from persisted state.
6. A real commit to the watched repo fires the companion within one cron interval; a test-only commit produces a ledger-only skip record with a one-line rationale (visible in the runs surface, no full run spent); a user-impacting commit produces a scenario that **mutates state through the real UI** via Chrome DevTools MCP.
7. A failing scenario files one Inbox item + one Task carrying an evidence bundle — screenshots, MP4, contact sheet, trimmed GIF, logs under one SHA256'd manifest registered as a single Artifact — and the `required_artifacts` gate blocks completion when any declared proof file is missing, independent of the agent's self-report.
8. With `fix_branch_enabled`, a confirmed finding yields a `pclaw/selfqa-<sha8>` branch with a proposed diff that is never merged or pushed automatically; the Task links it for human review.
9. `AGENT.md` exists at repo root, and a fresh coding agent following only it successfully performs the two canonical gotcha operations (push an app edit to the running gateway; rebuild the FE without serving a stale bundle) without touching auto-memory.

---

## Execution log

- [2026-07-26][S1] DONE: Spec harness core (§1.1, §1.2, §4.2). Created the repo-inner `harness/` package (`__init__.py`, `__main__.py`, `specs.py`, `validate_refs.py`, `profiles.py`, `cli.py`, `README.md`) beside `src/`/`tests/`/`scripts/` — NOT in the shipped wheel (`pyproject` finds packages only under `src/`). Three spec kinds with YAML-frontmatter shape validation (`rules`/`scenarios`/`tasks`); `python -m harness validate [--fast] | explain <task> | run <task> | scan`; profile→command registry (`fast`/`web`/`replay`/`full`/`scan`, latter two placeholders filled in S2/S3). Seeded 8 rule specs + 3 scenario specs + 1 worked task spec, all from proven bug classes. Wrote `AGENT.md` at repo root (machine-facing gotchas, cross-referencing each rule spec). 25 harness tests (`tests/test_harness_specs.py`, `tests/test_harness_validate.py`). Success Criterion #1 met: a dangling `requiredTests` node-id fails validation; #9 met: `AGENT.md` exists and covers the two canonical gotchas. Gate: `make lint` green (src+tests+harness under black/isort/flake8/mypy), `make test` green (7995 passed / 0 failed).
- [2026-07-26][S1] DEVIATION: the plan's Overview/§1.3 cite `test_action_schema_executor_parity.py` as the proof-of-pattern for the allowlist↔registry invariant. Code recon shows that test guards a DIFFERENT invariant (executor `action_config` reads ⊆ its `app.json` `settingsSchema`). The allowlist-vs-registry invariant actually lives in `tests/test_native_hook_providers.py::test_hook_provider_allowlist_includes_all_action_providers`. The `hook-provider-parity` rule spec cites the correct node-id; a note records the correction.
- [2026-07-26][S1] DEVIATION: every source line number in the plan was stale (verified drift: `validation.py` 559→600, `_EDITABLE_CONFIG` 363→436, `_broadcast` 1477→1535, `_ingest` 194→191, `eval/runner.py` 216→249, `loop_sse` ~1056→1094; `config.py` is really the `config/` package). The referenced constructs all EXIST and behave as described — only positions drifted — so this was not premise-mismatch-blocking (E1). Design response: specs reference STABLE anchors only (test node-ids, path globs, scanner check-ids) and never line numbers, which structurally eliminates this spec-rot class (plan Risk #1).
- [2026-07-26][S1] DEVIATION: added `.` to pytest `pythonpath` in `pyproject.toml` (was `["src"]`, now `["src", "."]`) so the repo-root `harness` dev package imports from `tests/`. Minimal, commented; does not affect the shipped wheel.
- [2026-07-26][S1] DISCOVERY (not fixed — adjacent): `test_apps_import_boundary.py` `pytest.skip`s at module level on this workspace because it looks for a sibling `apps/` dir but the apps clone is named `PersonalClawApps` — so its boundary lint never runs here. The harness's reference resolver handles this correctly (AST fallback confirms the test is defined even when un-collectable), but the boundary test itself is effectively dormant in this workspace layout. Left as-is (out of S1 scope); worth a scenario/rule follow-up.
- [2026-07-26][S1] DISCOVERY (not fixed — adjacent): `make lint`/`make test` default `PYTHON=python3` (system interpreter, no dev deps) and scope only to `src/personalclaw`+`tests` — the harness code is not yet under the standard gate. S2's Makefile-wiring task folds `harness/` into `make lint`/`make test`; until then the harness is linted explicitly (`.venv/bin/python -m {black,isort,flake8,mypy} harness`).
- [2026-07-26][S2] DONE: Boundary scanner + diff-aware run + same-PR rule (§1.3, §1.4). `harness/scanner.py` — 7 static checks (hook-provider-parity, sse-event-registered, config-four-points, app-sdk-boundary = ERROR; destructive-test-isolation, fence-at-ingestion, no-naive-transcript-cut = WARNING heuristics), each with a stable check-id, WHAT/WHY/FIX findings, and calibrated to **zero ERROR findings on clean HEAD** (verified). `harness/selection.py` — touched-area → forced-profile mapping (chat/loops→replay, config/loader+action_providers→scan; diff can add profiles, never remove). `harness/diff.py` — git changed-file/line + fix-shaped-commit introspection. CLI `run --diff` (forces profiles + same-PR-rule warning) and `scan [--diff]` (in-process scanner run) wired; `scan`/`replay` are marker profiles (scanner runs in-process; replay lands S3). Makefile: folded `harness/` into `format`/`lint` (black/isort/flake8/mypy) + added `harness-validate` target. 16 new tests (`tests/test_harness_scanner.py`) incl. the clean-tree calibration guard + each check firing on a synthetic violation. Success Criterion #2 met (verified by hand + tests): a config field missing from `load()` fails `config-four-points` with a WHAT/WHY/FIX message; touching `web/src/pages/chat/` forces the `replay` profile. Gate: `make lint` green (now incl. harness), `make test` green.
- [2026-07-26][S2] DEVIATION: the `scan` profile has no shell command — the scanner runs IN-PROCESS (`harness.scanner`) via the CLI so it can be diff-line-scoped, rather than as a `resolve_commands()` shell string. `scan`/`replay` are "marker" profiles: selecting them triggers behavior the CLI dispatches directly. Documented in `profiles.py`.
- [2026-07-26][S2] DISCOVERY (not fixed — heuristic residual): `destructive-test-isolation` warns on `tests/test_sdk_cli.py`, which calls `save_credential` on a test-double `ctx` (not the real store) — a false positive the heuristic can't distinguish. Left as an advisory WARNING (its designed role per §1.3); over-fitting the regex to one file is how heuristics rot.
- [2026-07-26][S3] DONE: Event-trace replay regression substrate (§2.1, §2.2, + §2.3 baselines). Recorder in CORE (`src/personalclaw/trace_recorder.py`) — env-gated by `PERSONALCLAW_TRACE_DIR`, zero overhead + no file I/O when off, redacts payloads at write via `security.redact`, one NDJSON line per event `{ts,stream,key,seq?,type,payload}`. Taps (~3 lines each, guarded by `is_recording()`): `SseRegistry.publish` (sse.py), `DashboardState._broadcast` (state.py), `inbox_service._ingest`, `mcp_client.call_tool`. Harness side: `harness/replay.py` (metrics fold: duplicate_event_rate w/ WF2-R11 `key|type|seq` dedup key + structural-fingerprint fallback, order_violation_count, reconnect_loss_count, event_fanout_ratio, per-stream latency p50/p95), `harness/baselines.py` (hard thresholds + latency drift +15% + missing-scenario-fails + loosened-threshold-needs-rationale), CLI `replay` command, `replay` profile now real (vitest replayFold + `python -m harness replay`). FE fold driver `web/src/harness/replayFold.ts` (+ `.test.ts`) replays chat traces through `coalesceReducers.ts` and run traces through `runFold.ts`. Checked-in fixtures: `happy-path-loop`, `channel-ingestion-flood` + `baselines.json`. 12 python tests (`tests/test_harness_replay.py`) + 7 vitest tests. **Success Criterion #3 met**: a re-introduced K44 coalescer duplicate is caught by replay (`adjacentDuplicateTextCount`), not a hand-written unit test.
- [2026-07-26][S3] DISCOVERY (not fixed — security gap, out of scope): `security.redact()` is narrower than the plan assumes ("credentials + exfil URLs"). Verified: it catches AWS access-key ids (`AKIA…`) + exfil URLs but does NOT catch bare OpenAI `sk-…`, GitHub `ghp_…`, or Bearer JWT tokens. The recorder applies whatever `redact()` catches, so a recorded trace could retain those token shapes at rest. Touching the redactor is E4-adjacent (a security control) and out of S3's scope — flagged here for a future Security-Hardening/redaction task. Mitigation documented in the `stream-event-duplicated-or-lost` scenario spec ("check the captured NDJSON before recording a stream you know carries an unredacted secret").
- [2026-07-26][S3] DEVIATION: the shipped trace fixtures (`happy-path-loop`, `channel-ingestion-flood`) are hand-authored representative recordings with deterministic timestamps, not captures from a live gateway run (the recorder produces byte-identical format — verified end-to-end in a scratch run). This keeps the checked-in fixtures stable across machines; the plan's chat-side `happy-path-chat`/`history-overlap-guard` scenarios are covered on the FE-fold side by `replayFold.test.ts`. The two WF2-gated scenarios (`workflow-journal-projection`, `rewind-during-stream`) are correctly deferred (need the engine) — see the S4 BLOCKED note.
- [2026-07-26][S3] DISCOVERY (pre-existing red, fixed on a SEPARATE branch): the full-suite gate surfaced `test_config_loader.py::test_unrecognized_keys_detected` failing — a Hypothesis property test whose hand-maintained `_KNOWN_TOP_KEYS` set had drifted (omitted real config sections `learning`/`legibility`/`loops`/`guardrails`/`resilience`/`workflows`), so it spuriously expected an "unrecognized" warning for a known key. **Proven pre-existing** (fails identically on clean `main` with my changes stashed) and unrelated to Self-Verification. Because it blocks the gate for any PR, fixed it as its own atomic commit on branch `bugfix-config-loader-known-top-keys` (off main): derive the test's known-keys from the authoritative `config.schema.SCHEMA_REGISTRY` so it can't re-drift. Verified the harness branch is green **in combination** with that fix (8023 passed / 0 failed). The harness branch itself does NOT modify config-loader — the fix is independently reviewable/mergeable.
- [2026-07-26][S4] DONE (non-WF2 scope): baselines infra (landed early in S3), MCP record/replay-as-fake-server, resume-audit (loop scope), and the exemplars scaffold. `harness/replay.py::FakeMcpServer` replays a recorded `mcp` trace as a deterministic offline tool server (arg-order-independent, successive-response, miss surfaces the gap — the mcporter record/replay shape; recorder tap already at `mcp_client.call_tool`). `harness/resume_audit.py::audit_loop` reconstructs a loop from `loop.store` (SQLite row + file dir) ALONE and reports whether done/verified/next/how-to-verify are answerable — the harness-course Fresh Session Test for loops; **Success Criterion #5 (loop half) met**. CLI `resume-audit <loop_id>`. `harness/exemplars/README.md` ships the §4.1 exemplar contract (dir empty until WF2 slices exist). Scenario specs `loop-dead-after-restart` + (S3) `stream-event-duplicated-or-lost` added. 10 tests (`tests/test_harness_resume_audit.py`). Gate: `make lint` green, harness tests green.
- [2026-07-26][S4] BLOCKED (E-none — expected dependency, not a defect): the Workflows-v2-gated deliverables are correctly NOT built this session because the engine + journal do not exist yet: (1) replay scenarios `workflow-journal-projection` + `rewind-during-stream` (§2.3 required set) — need WF2 Slices 1-2 output to record against; (2) the workflow-run half of resume-audit (byte-equal frontier reconstruction from the event-fold, §2.4); (3) all of §3 (the Self-QA Companion — needs WF2 template hosting + `required_artifacts` + Run Ledger). The SSE recorder tap already covers the future `workflow:<run_id>` registry for free (it lands on the same `SseRegistry` class), and the `replay`/`exemplars` profiles + scaffold are in place, so when WF2 lands these are additive: record the two scenarios, add their baselines, and drop the workflow-run resume assertions in. Recorded here per the deviation-ledger discipline (AGENTS.md) so the plan's Wave-1 co-scheduling with WF2 Slices 0-2 is unblocked the moment the engine starts.
- [2026-08-08][SV-6] DONE: workflow-run half of resume-audit — byte-equal frontier reconstruction from the event-fold (§2.4, **Success Criterion #5 workflow half met**). `harness/resume_audit.py::audit_workflow_run(run_id)` (beside `audit_loop`) reconstructs a persisted run from DISK ALONE — a fresh `RunController(run, spec, services=EngineServices())` reading `runs.db` + `spec.json` + `state.json`, no gateway services so it launches nothing and touches no network — and asserts the frontier it derives (`RunController._frontier()` folded with the node-state map into a canonical sorted-JSON snapshot via `_frontier_snapshot`) is BYTE-EQUAL to the pre-kill snapshot. Independently it projects the run's `journal.jsonl` to the SSE event shape the live gateway publishes (`_JOURNAL_TO_SSE`: `step_started→workflow_node_started`, `step_completed/step_cached→workflow_node_done`, etc.) and folds it through SV-5's `harness/replay.py::fold_workflow` (REUSED, not reimplemented), cross-checking the folded node states against the persisted ones — so a divergent replay (corrupted/truncated journal) fails even when `state.json` alone looks intact. Returns a `WorkflowResumeReport` mirroring `ResumeReport`'s shape (`ok`, `failures()`). CLI `workflow-resume-audit <run_id>` wired beside `resume-audit`. The "DEFERRED" note atop `resume_audit.py` is removed (clean break — the deferral is closed). 10 tests (`tests/test_harness_workflow_resume_audit.py`) prove: the completed-run + **mid-flight-kill** (a node still RUNNING at kill, resumed byte-equal) positive cases, idempotent reconstruction without a live snapshot, and two NEGATIVE cases (a corrupted `step_completed` state, and a dropped final node event) that FAIL the fold check. **No engine change was needed** — `RunController` already reconstructs synchronously from disk and `_frontier()` is a pure read; SV-6 only READS these seams. Gate: `make lint` clean under `uv sync --locked` (mcp 1.28.1); `python -m harness validate` green; targeted pytest green.
- [2026-08-08][SV-6] DEVIATION (naming): the "Done when" says "resume-audit kills and resumes" — the existing CLI verb `resume-audit` is the LOOP audit, so the workflow half is exposed as a sibling verb `workflow-resume-audit <run_id>` rather than overloading `resume-audit` (which takes a `loop_id`). Same audit, distinct entity, distinct id namespace — overloading one verb across two id types is the ambiguity `resume_run`'s token discipline exists to avoid. `audit_workflow_run` is the importable function the tests drive; the CLI verb wraps it exactly as `cmd_resume_audit` wraps `audit_loop`.
- [2026-08-08][SV-5] DONE: WF2 replay scenarios + baselines gating the journal format (§2.3 required scenarios 4-5, §2.1 workflow projection tap). The S4 BLOCKED gate had cleared (WF2 engine + `workflows/journal.py` exist). **No engine change was needed** — the §2.1 "no tap needed" claim held end to end: a workflow run publishes through `RunController._publish` → `EventCoalescer` → `watchdog._raw_publish` → `workflow_sse().publish` → `SseRegistry.publish`, which the S3 tap at `dashboard/sse.py` already records for the `workflow:<run_id>` key. Added a pure Python **event-fold** (`harness/replay.py::fold_workflow`, mirroring `web/src/pages/workflows/workflowFold.ts` — dedup-by-event-id, epoch supersede-drop, per-node seq floor) whose deterministic terminal state is attached as `Metrics.fold` for any scenario carrying a `workflow:` projection (non-workflow baselines untouched). `harness/baselines.py`: an EXACT `fold` compare (a fold-law break fails), a `REQUIRED_SCENARIOS` named set so an absent required recording fails the run outright (the disk-scan `required_scenarios()` cannot express "must EXIST"), and a fold-pinned-but-projection-missing failure. Checked in `traces/workflow-journal-projection/` (clean 3-node run) + `traces/rewind-during-stream/` (epoch 0→1 rewind, stale in-flight event dropped: `dropped:1`, terminal `epoch:1`) + their `baselines.json` entries. 9 tests (`tests/test_harness_wf2_replay.py`) prove all four SC#4 halves: both green, fold-law-break fails compare, missing-required fails, projection recordable through the live tap. **Success Criterion #4 met.** Gate: `make lint` clean + `python -m harness replay` green (4 scenarios) + `harness validate` (15 specs) + targeted pytest 643 passed / 2 skipped — all under `uv sync --locked` (mcp 1.28.1, the CI deps). Fixtures are hand-authored with deterministic timestamps (same discipline as the S3 fixtures; the recorder produces byte-identical format — verified in a scratch run).
- [2026-08-08][SV-8] DONE: Per-slice runnable WF2 exemplars (§4.1). Backfilled `harness/exemplars/<slice>/` (README-only until now) with a three-file bundle (`exemplar.py` + `smoke.sh` + `RATIONALE.md`) for each landed WF2 slice in the SV-8 scope — Slices 0–5, with Slice 2 the named `required_artifacts` example. Every exemplar drives the REAL `RunController`/`EngineServices` with a fake `completion` under an isolated `PERSONALCLAW_HOME` (no network, same mechanism as `test_workflows_controller.py`) and self-asserts its slice's mechanism, printing one `PASS <slice>:` line: s0 validator+bindings (sound spec validates with Kahn levels; a dangling ref is a typed `WF_UNKNOWN_NODE_REF` issue, not a throw), s1 frontier ordering + one `STEP_COMPLETED` per node, s2 the engine-owned artifact gate failing a claimed-but-unwritten node (run FAILED, `finalize` never runs, `STEP_FAILED` on the ledger), s3 secret resolution + on-disk redaction, s4 `binding_closure` = {sibling-consumer, not the unrelated node} + edit/resume re-running exactly that closure with the prefix served from the resume cache (`STEP_CACHED`), s5 approval `needs_input` + unattended-timeout FAIL. Wiring: `discover_exemplars()`/`incomplete_slices()` as the single shared enumeration, `python -m harness.exemplars` runner, an `exemplars` harness profile, and `tests/test_harness_exemplars.py` (9 tests) proving every smoke script runs to exit 0 and pinning the expected slice set so a dropped slice fails rather than silently shrinking coverage. Gate: `make lint` exit 0 (762 files), `pytest tests/test_harness_exemplars.py` 9 passed, `python -m harness.exemplars` 6/6 (~4.3s; slowest smoke ~3.2s, under the 30s ceiling). PR #927 off `main` (fresh atom, no dependency on the prior merged stack). Scope note: `WF2-SESSION-QUEUE.md` shows Slices 0–11 landed, but the SV-8 atom + §4.1 scope this backfill to 0–5; later slices add their own `slice_N/` going forward (README + `incomplete_slices()` enforce it).
- [2026-08-23][SV-9] DONE (clauses 1, 2, 4 met; clause 3 PARTIAL — see the note below): Self-QA Companion core (§3.1, §3.2 steps 1-5, §5). `src/personalclaw/selfqa/` — `triage.py` (deterministic path classifier → `user`/`test`/`none` + a one-line rationale that is never empty), `ledger.py` (`record_triage` writes `step_skipped` for a skip and `decision` for an impactful commit through the PP-4 ledger primitive, refusing a rationale-less row), `findings.py` (`file_finding` — exactly one Inbox item + exactly one Task, per-sink progress so a partial failure resumes instead of restarting), `install.py` (materializes the cron script + its config file into `<home>/crons/`, and `reconcile()` converges the `system:selfqa-commit-watch` interval trigger from `agent.self_qa`), `scripts/selfqa_commit_watch.py` (the zero-token watcher: `git rev-parse HEAD` vs a state file beside it, `Skip` on nothing new, else `workflow_start` + `Report`). Two action providers (`selfqa-triage`, `selfqa-file-finding`) registered in `action_providers/registry.py`, added to `ALLOWED_HOOK_PROVIDERS` (`validation.py`) and classed in `guardrails/rungs.py` (`action.selfqa_triage` autonomous; filing shares `action.create_task`). Bundled template `workflows/bundled/self-qa/workflow.json` (triage → route → foreach{scenario-gen → execute → evidence → verdict{file-findings, optional fix-branch}}), with `required_artifacts` on the evidence node and `ffmpeg`/`git` as `metadata.requirements` so a run blocks at preflight rather than degrading at node 4. `agent.self_qa` wired through all four points and each verified by hand, not only by `test_config_roundtrip.py` (which covers three of five): dataclass + `_meta` on all four fields, `AppConfig.load()` explicit mapping with `max_scenarios_per_fire` clamped to [1,20], `to_dict()` round-trip (measured: a non-default write reads back field-for-field), `_EDITABLE_CONFIG` PATCH allowlist + a live-apply reconcile on `agent.self_qa.*`, and a Settings → Agent defaults section (toggle / path / ceiling / fix-branch) in `AgentDefaultsPanel.tsx`. 61 tests in `tests/test_selfqa_companion.py`, one class per clause, each with a vacuity floor; the watcher tests import the INSTALLED script file rather than the package module, so they prove the file a Schedule would execute. Gate: `make lint` exit 0 (1982 files black, mypy 982 sources clean), `make test` 25189 passed / 0 failed / 30 skipped / 12 xfailed, `npm run typecheck:web` + `npm run test:web` (471 files, 4953 tests) + `npm run build` all exit 0, real-home rail clean on every pytest leg.
- [2026-08-23][SV-9] DEVIATION (inventory ratchets, found by the full gate): five committed inventories reject a new provider/template/config field until they are updated, and the first pass of this atom updated none of them. All five are now correct, and three further reds were purely downstream of them: `config-baseline.json` regenerated (`scripts/generate_config_baseline.py`, +4 `agent.self_qa.*` rows, purely additive); `tests/fixtures/frontier_golden/bundled.jsonl` regenerated with the documented worktree-safe invocation (`PYTHONPATH=$PWD/src python tests/test_workflows_frontier_golden.py` — a bare `python` would resolve `personalclaw` through the venv's editable install and capture the MAIN checkout's decisions as this branch's proof; verified additive, 13 added / 0 removed, every added line a `self-qa` decision); both providers classified in `triggers/screen.py` (`selfqa-triage` READ_ONLY — read-only git, zero tokens, a ledger row and nothing user-visible, so it sits with the deterministic knowledge probes; `selfqa-file-finding` WRITE_CAPABLE — it raises an inbox item, the same unattended "puts something in front of the user" write that puts `notification-digest` on that side, notwithstanding that `create-task` alone is read-only); both spawn sites classified in `test_spawn_ceiling_audit.py`; and `test_guardrails_ladder.py`'s exact-list pin on `action.create_task`'s providers updated to name `selfqa-file-finding` rather than loosened to `in` — the exact list is the ratchet that makes an unnoticed addition to a governed class red. Downstream and now green without further change: `test_gate_report.py::test_all_gates_pass_on_a_clean_tree` (the config-baseline gate), `test_structural_baseline.py::test_three_simultaneous_structural_violations_report_as_three` (it injects 3 violations and asserts "3 of 6 FAILED"; a pre-existing 4th made it 4), and `test_capability_table_ids.py`.
- [2026-08-23][SV-9] DISCOVERY (real vulnerability, FIXED here): the triage path had an **option-injection** hole. `triage_commit` passed its `sha` straight into `git -C <repo> show --name-only --pretty=format: <sha>`, and that value is model-reachable — the template binds `{{inputs.commits}}`, and a run can be started by an agent calling `workflow_start` with a `commits` list of its choosing. The argv is fixed and there is no shell, so this was not command injection; it was worse than nothing: `--output=<path>` is a real `git show` diff option. **Measured, not reasoned about** — with the guard removed, a `sha` of `--output=/tmp/selfqa-pwned` caused git to write the commit subject to exactly that path (the file was created, inspected, and removed). Closed with a hex shape check (`_SHA_RE = [0-9a-fA-F]{4,64}`) applied before the ref reaches git, the same discipline `durability/state_history.py` applies to the one caller-supplied value it lets through — and a refused ref still returns a verdict (`none`, rationale `"refused: …"`) so a refusal cannot masquerade as "the companion never ran". The `_OPERATOR_EXEMPT` entries for both spawn sites cite the validation, so the exemption is true rather than asserted. Test + vacuity floor in `test_an_option_shaped_ref_never_reaches_git`; its payload path is under `tmp_path`, because the first version asserted on a shared `/tmp` path and was failed by its own earlier falsification artifact.
- [2026-08-23][SV-9] DISCOVERY (pre-existing, NOT fixed — out of scope): `tests/test_guardrails_ladder.py` has intra-file state leakage that xdist's distribution normally masks. Run whole-file single-process (`-n0`), `test_a_provider_that_refuses_leaves_the_rung_ALONE` (`assert 200 == 400`) and `test_create_task_deletes_the_row_it_filed` (`assert 0 == 1`) both fail; `test_create_task_deletes_the_row_it_filed` passes when run alone. **Proven pre-existing**: both fail identically on a clean `origin/main` worktree with the same command, with none of this branch's changes present. Unrelated to SV-9 (neither test touches the companion) and invisible under the normal `make test`, which is green at 25189 passed. Flagged for whoever owns that file; do not read a red from an `-n0` whole-file run of it as a regression.
- [2026-08-23][SV-9] DEVIATION: §3.2 step 1 specifies triage as an **`infer` node (cheap tier)**; it ships as a deterministic path classifier behind an `action` node. Two reasons, both load-bearing. Cost: a path classifier is zero tokens, so the watcher can run on a 5-minute interval without a budget. Assertability: with a prompt, "a test-only commit was skipped for the right reason" and "the companion never fired" are indistinguishable — both produce zero findings — which is the exact failure this loop exists to catch. `action` rather than `infer` also because the verdict must land in the run ledger with its rationale and only a node holding the run id can write one. Judgment is not removed, it moved: the deep-as-a-user method stays in `scenario-gen`'s prompt, which is where §3.2 step 2 puts it.
- [2026-08-23][SV-9] DEVIATION: §5 opens "**No new provider TYPE and no new action provider**", and this lands two action providers. §5's own next sentence states the rule for the case it contemplates ("if a later revision did add an action provider, it MUST be added to `ALLOWED_HOOK_PROVIDERS` or hook create/update rejects it") and that is followed. Neither is the `qa-run` provider §5 forbids — the QA run still fires through the existing `workflow_start`/`run-workflow` seam and no new provider TYPE, inbox source, or task provider is introduced. The alternative for step 5 was a `stage` node instructing a subagent to call two tools "exactly once each", which is a count no test can assert and no engine enforces — precisely the one-is-a-ceiling failure the criterion is about.
- [2026-08-23][SV-9] DEVIATION: §3.1 describes the watcher raising `Report` "which the job's `run-workflow` action turns into a QA workflow run with `{{inputs.commits}}`". No such hand-off exists — a script's `Report` payload does not flow into a second action's inputs. The script therefore starts the run itself via `ctx.call_tool("workflow_start", {…, "idempotency_key": f"selfqa-{head}"})`, which routes through the Tool entity, so one `run-script` interval trigger is the whole seam and the `Report` is a record rather than an instruction. Verified against the shipped schema: `WORKFLOW_START_SCHEMA` accepts `idempotency_key` (max_len 128). The state file advances BEFORE the run starts, so a run that fails downstream does not re-fire the same commits every interval.
- [2026-08-23][SV-9] PARTIAL — clause 3 (`a user-impacting commit generates a scenario that mutates state through the real UI via Chrome DevTools MCP`) is **not observed end to end, and nothing in this change pretends otherwise**. Measured: the `chrome-devtools` MCP server is configured and reachable at the harness level (`claude mcp list` → `chrome-devtools: npx chrome-devtools-mcp@1.6.0 - ✔ Connected`), but its tools are not exposed to the implementing session, and driving the clause for real additionally needs a gateway built from the commit under test plus a live model on the `scenario-gen`/`execute` stages. What IS enforced is every property of the shipped template the clause depends on, each with its own test: the triage→scenario routing, the execute stage naming the Chrome DevTools MCP driver and a NEW page (co-tenancy), `scenario-gen`'s state-mutation requirement as a schema field AND a `success_when` gate (so a render-only scenario fails the node instead of driving nothing), the three repo gotchas injected into the execute prompt (no-hot-reload, `static/dist` symlink, hard-reload), and the engine-enforced `required_artifacts` proof gate. `TestClauseThreeScenarioDrivesTheUI`'s docstring states the gap in the file, so the next session reads it there rather than re-deriving it.
- [2026-08-23][SV-9] DISCOVERY (citation defect, corrected in code comments — no behavior change): the SV-9 atom's `done_when` attributes "a failing scenario files one Inbox item + one Task" to **Success Criterion #6**, which does not contain that clause. #6 covers the watcher interval, the skip record, and the UI drive only. The filing requirement is the atom's own and is implemented as such; `selfqa/findings.py` records the misattribution rather than propagating it. Conversely, #6's skip clause carries a requirement the atom drops — "(visible in the runs surface, no full run spent)". The "no full run spent" half is met (a skip is a ledger row and nothing else). The **runs-surface rendering half is NOT met**: the FE renders node `state` values including `skipped` (`nodeTree.ts`, `workflowFold.ts`) but nothing renders a `step_skipped` ledger row's `sha`/`impact`/`rationale`, and the triage node writes several such rows under one node id, so the rationale is readable through the ledger reader and not from the runs UI. Out of this atom's `done_when`; flagged as the remaining surfacing task for whoever closes #6.


## Execution log — `SV-9` (Self-QA Companion core: §3.1 commit-watch, §3.2 steps 1-5, §5 config) — **PARTIAL**, atom stays `todo`

- [2026-08-23][SV-9] **PARTIAL.** Clauses 1, 2 and 4 are MET and railed; clause 3 (a scenario mutating
  state through the real UI via Chrome DevTools MCP) is **environment-gated** and recorded as PARTIAL
  rather than simulated. Ships `selfqa/` (triage / ledger / findings / install / cron script), two action
  providers, the `self-qa` bundled template, `agent.self_qa` four-point wiring, an FE section in
  `AgentDefaultsPanel.tsx`, and guardrails rung classes. Gate at integration: `make lint` 0 (mypy 982
  files), 61 targeted, `make test` 25189 passed / 0 failed, web green, probe residue 0.

- [2026-08-23][SV-9] 🔴 **A REAL OPTION-INJECTION HOLE, proved by writing a file to an arbitrary path.**
  `triage_commit` passed a **model-reachable** `sha` straight into `git show`: the template binds
  `{{inputs.commits}}`, and an agent can call `workflow_start`. argv was already a list with no shell, so
  this is not command injection — but `--output=<path>` is a genuine `git show` **diff option**, and the
  falsification (disabling `_SHA_RE`) **actually created `/tmp/selfqa-pwned` containing the commit
  subject**. Closed with a hex check (`triage.py:195`) before the ref reaches git; a refused ref still
  returns a verdict, so a refusal cannot masquerade as "never ran". The `_OPERATOR_EXEMPT` entries cite the
  validation, so the exemption is true rather than asserted. Re-falsified by me at integration: neutering
  the guard reds `test_an_option_shaped_ref_never_reaches_git` with
  `assert 'refused' in 'no changed paths — nothing to exercise'` (1 failed, 60 passed).

- [2026-08-23][SV-9] 🔴 **A REAL "exactly one" hole in the filing path.** `file_finding` posted the Inbox
  item, created the Task, and only *then* set an all-or-nothing dedup flag — so a Task failure left nothing
  recorded, and the replay that the ceiling test itself cites posted a **second** interrupt. Replaced with
  per-sink progress. The rails now assert `len(inbox_items) == 1` and `len(tasks) == 1` (never `>= 1`) with
  pre-call `== 0` on both stores and id cross-checks, a ceiling (filing twice still `1`/`1`), and **two**
  vacuity floors: a *different* `scenario_id` on the same sha files its own (`2`/`2`, catching a sha-only
  dedup key that would swallow every finding after the first), and the new partial-failure test asserts
  `1` inbox **and** `1` task after a task failure plus replay — so the ceiling catches double-posting while
  the floor catches a flag set too early. The task dir is redirected **per test**, so "exactly one" is not
  measured against a running total.

- [2026-08-23][SV-9] **Clause 3 is gated by session tool policy, not by a missing server.**
  `claude mcp list` reports `chrome-devtools: npx chrome-devtools-mcp@1.6.0 - ✔ Connected` and the package
  is on disk, but **no `mcp__chrome-devtools__*` tool is exposed to the driving session** — absent from the
  deferred-tool list, and a `ToolSearch` for `navigate_page,take_snapshot,click` returned *"No matching
  deferred tools found."* So the shipped design (binding via the ambient MCP connector config +
  `tools_posture: "full"`) is sound; what is unproven is the end-to-end drive, which additionally needs a
  gateway built from the commit under test and live model calls on `scenario-gen`/`execute`. **No UI
  mutation was faked.** The template contract is fully enforced meanwhile: routing, MCP naming + NEW-page
  discipline, mutation as a schema field **and** a `success_when` gate, the three repo gotchas,
  engine-enforced `required_artifacts`, and an `ffmpeg` preflight.

- [2026-08-23][SV-9] **The atom mis-cites Success Criterion #6, and drops half of it.** SC #6 contains no
  "one Inbox item + one Task" clause — that is the atom's own `done_when` addition. SC #6 reads: watcher
  interval; a skip record with a one-line rationale *"(visible in the runs surface, no full run spent)"*;
  and the UI drive. **The "visible in the runs surface" half is NOT met:** the FE renders node `state`
  values including `skipped`, but nothing renders a `step_skipped` row's `sha`/`impact`/`rationale`, and
  triage writes several rows under one node id. Recorded as the remaining surfacing task for whoever closes
  SC #6 — and as a reason this atom's criterion and its plan's criterion should be reconciled.

- [2026-08-23][SV-9] **Five committed inventory ratchets were never updated by the first pass**, leaving
  `make test` red in 8 places with 3 more purely downstream. `config-baseline.json`,
  `tests/fixtures/frontier_golden/bundled.jsonl`, **both** capability tables in `triggers/screen.py`,
  `test_guardrails_ladder.py`'s exact-list pin, and `test_spawn_ceiling_audit.py`. Both regenerations were
  verified purely additive, and the frontier golden used the documented worktree-safe invocation — a bare
  `python` would have captured the **main checkout's** decisions.

- [2026-08-23][SV-9] **Three tests were asserting less than they claimed, and one fixture leaked.**
  `test_enabled_with_a_repo_arms_the_watcher` **never asserted arming** (it does arm — `next_fire_at`
  +300 s with a `run-script` frozen grant — assertion added); the cron script's module docstring
  contradicted its own code, claiming a `run-workflow` action turns the `Report` into a run when the script
  calls `ctx.call_tool("workflow_start", …)` itself and the trigger action is `run-script`; `findings.py`
  misquoted SC #6; and the `inbox_state` fixture leaked `ns.set_dashboard_state` where its peer
  `test_inbox_native_source.py` clears it.

- ⚠️ **Pre-existing intra-file leakage recorded, not fixed.** Run whole-file single-process,
  `tests/test_guardrails_ladder.py` fails `test_a_provider_that_refuses_leaves_the_rung_ALONE` (`200 == 400`)
  and `test_create_task_deletes_the_row_it_filed` (`0 == 1`). **Both fail identically on a clean
  `origin/main` worktree** — xdist's distribution masks it. Not this atom's, and not weakened.

- [2026-08-24][SC#6 surfacing] DONE: **Success Criterion #6's "(visible in the runs surface)" half is now met.**
  The SV-9 entry above was right that nothing rendered a `step_skipped` row's `sha`/`impact`/`rationale` — but
  it found only the FE half. There were **TWO** breaks, and the first made the second unobservable:
  1. 🔴 **The row was stamped with the node id, not the engine's instance key.** `record_triage` wrote
     `instance_path="triage"`, while `models.walk` names that instance `root.children[0]`.
     `service.inspect_node` — the read behind the runs surface — builds a node's ledger slice by filtering the
     run's ledger on `instance_path == <target>`, so **every triage row fell outside its own node's slice**.
     Measured, not reasoned: a run with three test-only commits surfaced `0` of `3` skips (`assert 0 == 3`
     under the reverted line). Fixed by threading the instance path into the action payload — the controller is
     the only layer that knows it (`instance_path=item.path` → `dispatch` → `_dispatch_inner` →
     `dispatch_action` → `payload.setdefault`) — beside the `run_id`/`project_id` provenance already there.
     `node_id` cannot substitute: a `foreach` body shares one id across every item. `record_triage` now
     **refuses** an empty path for the same reason it refuses an empty rationale.
  2. **The surface rendered a row's `kind` and nothing else.** `NodeInspectorDrawer`'s ledger list printed
     `e.kind` per row, so three skips rendered as three identical words. Now each row renders its `sha`
     (whole — a 40-char hex a user can paste into `git show`), its `impact` class, and its `rationale` in
     full, via a generic projection (`web/src/pages/workflows/ledgerRowDetail.ts`) that reads those fields off
     any row that carries them. **Nothing truncates**: a one-line reason clipped mid-sentence answers "why did
     nothing run?" no better than silence.
  **The several-rows-under-one-node-id property is the load-bearing one** and is asserted directly: fixtures
  carry THREE skips (two sharing an impact class) so a content-keyed fold drops one, `ledgerRowKey` is
  identity/positional and never content-derived, and the rendered-DOM pins count rows, shas, impacts and
  rationales. Vacuity floors both ways: rows carrying none of the three fields must render ZERO of the new
  elements, and a row stamped with the bare node id must still be DROPPED by the slice (the pre-fix state, so
  the fix cannot be reverted green). Falsified by mutating five live lines — the rationale render, a row fold,
  `record_triage`'s stamp, the engine's `payload.setdefault`, and the controller's kwarg — each observed red,
  each restored from a file copy.
  **"No full run spent" is distinguishable** on two surfaces: per commit, `step_skipped` vs `decision` are
  rendered verbatim in the ledger list (no new verdict vocabulary minted); per run, the untaken `scenarios`
  branch renders as `Skipped` through `workflowMeta`'s existing state map. No change was needed for either.
  Gate: `make lint` clean (mypy 992 files) · targeted pytest 338 passed · `make test` **25640 passed / 30
  skipped / 12 xfailed / 0 failed** · `gate_report.py` 6/6 · `typecheck:web` clean · full web suite
  **475 files / 4991 tests passed** · `npm run build` clean · probe sweep 16 pre-existing, 0 introduced.

- [2026-08-24][SC#6 surfacing] DISCOVERY (out of scope, unfixed): **the learning refiner reads field names no
  ledger row carries.** `learning/refiner.py:399` keys its clusters on `event.get("node") or
  event.get("path")`, but the ledger writer stamps `node_id` / `instance_path`. So every `step_skipped` the
  refiner folds — including the ones this entry made visible — is attributed to the node `""`. Not touched
  here (it is neither a surfacing defect nor SV-9's), but it means the "a repeatedly SKIPPED step is a failure
  of the template" mechanism at `refiner.py:410` cannot name the step it is talking about.

- [2026-08-24][SV-9 clause 3] 🔴 **A REAL DEAD BRANCH, found by actually driving the template — and
  clause 3 is still BLOCKED behind a second, deeper one.** Drove `self-qa` from the dashboard's
  Definitions tab against a gateway built from this branch (isolated home, port 10077), with the commit
  under test being a real user-impacting one (`07b0c24a`, the SC#6 surfacing commit).
  1. **FIXED here.** The run died at node 2 with `branch selector matched no case and the node has no
     default`. `triage` returns a real Python `bool`; `tick._select_case` keyed the selector on
     `str(value)` → `"True"`; a template is JSON so the only spelling an author can write is `true`.
     So `route` matched neither case — the branch was dead in BOTH directions, and the whole scenario
     subtree was unreachable from any real run. **Three of self-qa's branches and one of
     knowledge-lint's** had the same shape (`verdict`, `fix-route`; `knowledge-lint.route`), so this was
     a library-wide class defect, not a typo. Normalised in the new `tick.case_key`, at the one place a
     selector becomes a key — `_select_case` is called by branch dispatch AND twice by the frontier, so
     normalising at a call site would let dispatch and the edge decisions disagree about which case a
     run took. `bool` only; the string `"True"` still fails to route, and that is the vacuity floor
     (folding it in would merge two cases an author wrote as distinct). After the fix, measured on a
     second run: `route → done`, `no-impact → skipped`, scenario subtree RUNNING.
     **`test_an_impactful_commit_reaches_the_scenario_subtree` passed the entire time it was broken** —
     it asserted `route["cases"]["true"]` was PRESENT in the JSON, never that the engine could select
     it. Replaced by `test_the_ENGINE_selects_that_case_not_just_the_JSON_declaring_it`, which drives
     the shipped node through the shipped dispatcher with the shipped provider's output shape, plus a
     library-wide ratchet in `test_workflows_bundled.py` that reds naming `knowledge-lint.route`.
  2. **BLOCKED — not this atom's, not fixed: a `stage` node's spawn completion has NO CONSUMER.**
     `engine.dispatch_stage` spawns, returns `RUNNING` carrying `{"subagent_id": info.id}`, and
     **nothing in the repo reads `subagent_id`** (`git grep` finds only the write site).
     `controller._apply`'s RUNNING branch pops the instance out of `_inflight`, so neither
     `_enforce_stall_timeouts` nor `node_timeout_total` can see it either — both iterate `_inflight`.
     Its comment says "the watchdog reconciles it"; `workflows/watchdog.py` only reaps at the RUN level
     (`_reap_if_finished` requires every instance already terminal). Measured: `scenario-gen`'s subagent
     reported `done: True, error: ""` while the node stayed RUNNING with no `step_completed` fifteen
     minutes later, controller spinning at ~20% CPU. Corroborated independently — the dev home carried
     two runs stuck `Running` (`produce-and-audit`, `deep-research`) and one `Cancelled` at 324h 27m.
     This blocks every template with a `stage` node, so it is a WF2 engine seam and a multi-session
     scope, not SV-9's. **Clause 3 cannot close until it lands.**
  3. **The MCP half of clause 3 IS satisfied, measured.** With `chrome-devtools` in
     `$PERSONALCLAW_HOME/mcp.json` and the (already installed) `mcp-tools` app, the running gateway's
     `/api/tools` lists all 29 `mcp/chrome-devtools/*` tools — so the earlier entry's "gated by session
     tool policy" was about the *implementing* session, and the product's own path works. What is
     missing is that **nothing declares or checks that dependency**: the template declares
     `git`/`ffmpeg` under `metadata.requirements.binaries` and `preflight` has no MCP-server check, so a
     run without the server burns a scenario instead of blocking at start.
  4. **Two inert template fields on the execute stage.** `tools_posture: "full"` and
     `isolation: "fresh"` are never read by `dispatch_stage` — `tools_posture` appears in `src/` only in
     bundled templates, `loop_run_map.py`'s prose and `template_lint.py`; `isolation` is read only by
     GATE dispatch (`engine.py:1562`, `cross_model`). The previous entry's claim that the design binds
     "via the ambient MCP connector config + `tools_posture: full`" rests half on a field the stage
     dispatcher ignores. Not touched (the field is on ten bundled templates; deleting only self-qa's
     would be inconsistent), but it is not a control.
  Gate: `make lint` clean · targeted pytest 12 passed (clause-3 class + the library ratchet) · both
  vacuity floors falsified by mutating `case_key` live and observing red · `gate_report.py` 6/6.

- **[2026-08-25][`SV-9`] THE WF2 ENGINE SEAM IS FIXED — this atom's clause-3 blocker is cleared.** The
  entry above recorded that `engine.dispatch_stage` returns RUNNING carrying `{"subagent_id": info.id}`
  and that nothing reads it, hanging every template with a `stage` node, and correctly scoped the fix as
  a WF2 seam rather than SV-9's own work. That seam now has a consumer.
  **Two corrections to the diagnosis in that entry.** The pop is not in `_apply` — `_apply`'s RUNNING
  branch (`controller.py:2794-2800`) only sets state and stashes output; the pop is at `:2686` in
  `_await_progress`, which drains every done task *before* `_apply` runs. And the node was **doubly**
  invisible: `_await_progress` is the ONLY caller of `_enforce_stall_timeouts` (`:2682`), so once
  `_inflight` empties the sweep stops being invoked at all rather than merely missing the entry, and the
  tick loop spins on `await asyncio.sleep(0)` (`:571-578`) — which is also the CPU burn that was
  observed. `node_timeout_total` never could have seen it either: it bounds the awaited dispatcher
  coroutine (`:2539`), which for a stage ends at the spawn.
  **Half the fix was already built and simply unread.** `SubagentManager._reaper_loop`
  (`subagent.py:739`) already force-kills a child past its timeout and `_force_reap` already sets
  `info.done=True` with `info.error="Reaped after Ns..."` (`:791-793`). So the deadline governing a
  spawned worker existed; one reader closed both halves and no new timeout was invented. Liveness stays
  owned solely by `SubagentManager.get` (`subagent.py:1632`) — the same lookup `sessions.py:388` uses —
  reached from the controller via `self.services.subagents`, so there is no second registry.
  **`_inflight` was deliberately NOT the home**, and the reason is worth keeping: every consumer of that
  dict assumes `entry.task` is a live awaitable — `_await_progress` selects on it with FIRST_COMPLETED,
  and the stall sweep and `_cancel_inflight` cancel it. Re-inserting a finished task makes
  `asyncio.wait` return instantly forever and re-apply the same RUNNING result every tick; a
  never-resolving placeholder means fabricating a fake task to satisfy a signature. What the controller
  legitimately owns is the *id*, which is instance state — so `NodeInstance.subagent_id` is a foreign
  key exactly as `output_ref` points into the output store.
  **The reconciler is reached, proved at integration.** Removing its one call site
  (`controller.py:858`) reds **4 of 6** in `tests/test_workflows_stage_completion.py`, including *"the
  controller never asked the subagent manager whether the spawn finished"* and *"a finished stage is
  still running — this is the fifteen-minute hang"*. And the bug itself was proved real the same way:
  the test commit was landed separately from the fix, so checking out the test-only commit reproduces
  **4 failed / 2 passed** on a fix-free tree.
  **Fixed en route:** `_outputs.setdefault(node.id, ...)` was keyed by NODE id, so a `foreach` fan-out of
  stages kept only the first leaf's id — and it put an engine internal into the `{{nodes.X}}` binding
  namespace.
  🔴 **Deliberately NOT fixed, flagged rather than smuggled in.** (a) The `asyncio.sleep(0)` busy-spin at
  `:571-578` still spins while a stage is dispatched — now for the subagent's duration instead of
  forever; fixing it properly changes `_await_progress`/the idle branch for EVERY node kind, which is
  outside a bug fix. (b) `dispatch_stage` deliberately retains its lease (`engine.py:773-776`), so a
  rewind inside the TTL still gets `DEGRADED "another worker holds the claim"` — pre-existing.
  (c) `cache_key=""` on the reconciled `step_completed`: the awaited dispatch that owned the key returned
  at the spawn, so inventing one would let a resume serve a cache hit for a subagent result the run never
  computed. Consequence: a resumed run re-runs a completed stage, which is also today's behaviour since
  stages never complete at all. (d) `audit.py:39 STALE_RUNNING_SECS = 6h` still only *reports*
  stuck-RUNNING nodes; it remains the backstop for the unknown-id case.
  **`SV-9` stays `todo`** — this clears its clause-3 blocker, it does not implement SV-9.
  **Gate:** `make lint` clean (mypy 1011 files); the new file **6 passed** (4 failed / 2 passed without
  the fix); **all 61 suites importing `workflows.controller`/`workflows.engine` → 2370 passed, 0 failed**;
  `gate_report.py` 6/6 PASS; probe sweep 16. No `web/` or `desktop/` files.
  **`_apply`'s RUNNING branch is reachable only by `stage`** — `engine.py:778` is the only
  `InstanceState.RUNNING` return in the engine, asserted by an `ast` ratchet rather than a grep, because
  a text scan counts comments.

- [2026-08-25][SV-9 clause 3] **Clause 3 stays PARTIAL — but the gate MOVED, and the new one is named.**
  The 2026-08-23 entry above blamed *session tool policy*: no `mcp__chrome-devtools__*` tool was exposed
  and a `ToolSearch` for `navigate_page,take_snapshot,click` returned *"No matching deferred tools found."*
  **That half has cleared.** The same `ToolSearch` now returns full schemas for `navigate_page`,
  `take_snapshot`, `click`, `new_page`, `list_console_messages`, `fill`, `select_page`, `list_pages`,
  `evaluate_script` and `take_screenshot` — the tools reach the driving session. A gateway built from this
  commit was stood up on an isolated dev home (`--port 10731`, `PERSONALCLAW_AUTH_MODE=none`, SPA built and
  `static/dist` symlinked via the Makefile recipe, never a `cp -R`) and served the SPA at `/` (200).
  **What blocks the drive now is browser-profile CONTENTION, and it is not something a driving session can
  route around.** `new_page` and `list_pages` both fail with *"The browser is already running for
  `~/.cache/chrome-devtools-mcp/chrome-profile`. Use --isolated…"*. Root-caused in the server's own build,
  not guessed:
  1. `browser.js:139` derives the profile dir as
     `~/.cache/chrome-devtools-mcp/chrome-profile{-<channel>}` — a function of the server's `--channel`
     CLI arg only. **No tool parameter can change it**, so every concurrent session contends for ONE dir.
  2. A live sibling session holds it. Chrome pid 14504 (16h45m old, `about:blank`) was launched by
     `chrome-devtools-mcp` pid 20305, whose own parent is a *running* claude-code process — so it is a live
     sibling's browser, not an orphan, and killing it is out of bounds (the pool shares the machine).
  3. It cannot be attached to either. It runs `--remote-debugging-pipe`, which writes **no**
     `DevToolsActivePort` file, and `--auto-connect` resolves a browser *only* by reading that file out of
     `options.userDataDir` (`browser.js:61-79`). Pipe transport + absent port file = unattachable.
  4. `ensureBrowserLaunched` has no fallback: the puppeteer `SingletonLock` failure is re-thrown verbatim
     (`browser.js:196-204`). `--isolated` is a server-launch flag, unreachable from inside a session.
  A private Chrome on a private profile was tried and **abandoned as a foreign-instance hazard**: port 9222
  is already held by the user's own real Chrome (pid 903), the private one only got the IPv6 bind, and
  `--auto-connect` would not have found it anyway (point 3). It was killed by PID.
  **Owner decision, outside this atom's fence:** add `--isolated` (or an explicit per-session
  `--user-data-dir`) to the `chrome-devtools` MCP server args in the harness config. That retires this whole
  contention class permanently and is the last thing standing between clause 3 and a real drive. Until then
  clause 3 is blocked on **harness configuration**, not on PersonalClaw and not on tool availability.
  **No UI mutation was faked. Again.**

- [2026-08-25][SV-9 clause 3] **The backend half of the drive WAS proved live, and is labelled as exactly
  that — not as clause 3.** Against the gateway from this commit, all four fields the Self-QA panel writes
  were PATCHed through `/api/config/personalclaw` and **read back off disk** from
  `.dev-home/config.json`: `enabled false→true`, `watched_repo ""→"/private/tmp/sv9drive-wt"`,
  `max_scenarios_per_fire 3→7`, `fix_branch_enabled false→true`, each `HTTP 200`, and a bogus
  `agent.self_qa.nope` correctly `HTTP 400 "field not editable"`. **This is the API path, not the UI path** —
  it de-risks the eventual drive and proves the round-trip lands, but it is *not* the clause, which requires
  the mutation to travel through the rendered control.
  **The template's full autonomous loop did not run** and was not attempted: `scenario-gen` (GENERATIVE) and
  `execute` (AGENTIC, which is the node that would hold the Chrome DevTools MCP tools) both need live model
  calls, and `execute` needs the very browser that is contended. The blocking sub-step is `execute`.

- [2026-08-25][SV-9 clause 3] 🔴 **Railed instead: the quiet-revert the drive exists to catch was UNRAILED
  on SV-9's own FE surface.** `AgentDefaultsPanel.tsx:76-79` warns in a comment that reusing the flat
  `patch` for a Self-QA row would PATCH `agent.<field>` — *"a path the server's allowlist rejects, so the
  control would appear to work and then quietly revert."* Nothing asserted it. The existing
  `test_every_field_has_a_write_path` derives its paths from `SelfQaConfig`, so it is **one-sided**: it
  proves dataclass↔allowlist agreement and stays green no matter which closure the panel actually calls.
  A one-token drift (`patch={patch}` for `patch={patchSelfQa}`) ships an inert control whose failure mode is
  *looking like it saved* — precisely the class a render check never sees, and precisely why clause 3 exists.
  Closed by `TestTheSelfQaPanelRowsPatchTheNestedPath`, which reads the **call site** (the panel source) and
  then closes the loop on the backend by asserting the flat path each mis-bound row *would* have sent is
  **not** editable — because if `agent.<field>` were editable, a mis-bind would be harmless and the rail
  would measure nothing. Both `agent.self_qa.<f>` present and `agent.<f>` absent are asserted for all four.
  Scope and matcher hazards handled deliberately: the section is bounded by real markup
  (`<Section title="Self-QA companion"` → the next `</Section>`), never a character window; elements are
  split on `<` followed by an uppercase letter, because scanning to the first `>` truncates at the `<sha>`
  inside a `hint` string, and that same rule excludes `</Section>` and `<div`.
  **Two vacuity floors**, since a scanner that matches nothing reads as clean: the discovered row set is
  pinned to the exact four names (not a count, so a rename cannot be absorbed by an addition), and the block
  must NOT contain `approval_mode` — a flat-`patch` row in a *sibling* section — and must be shorter than the
  whole file, so a slipped boundary reds rather than silently widening.
  **Falsified three times on LIVE lines, each grepped back before running and each restored from a file
  copy** (never `git checkout`): (1) `field="enabled" patch={patchSelfQa}` → `patch={patch}` reds
  `test_every_row_is_bound_to_the_nested_patcher` with
  `assert 'patch={patchSelfQa}' in 'ToggleRow label="Enable the companion" … patch={patch}…'` (1 failed);
  (2) inserting `"agent.enabled": {"type": "bool"}` into `_EDITABLE_CONFIG` reds
  `test_the_flat_path_each_row_would_have_sent_is_rejected` with `assert 'agent.enabled' not in {…}`
  (1 failed); (3) renaming a row's `field` reds **both** the vacuity floor and the backend half
  (2 failed / 2 passed). Tree verified clean after every restore.
  ⚠️ **A manual drive is not a regression rail** — this rail does not make clause 3 met. It makes the one
  contract the drive would have exercised permanent, so that when the harness flag lands the drive is
  confirming a rail rather than discovering a bug.
  **SV-9 stays `todo`.** Clauses 1/2/4 remain MET; clause 3 remains PARTIAL, now blocked on harness config.
