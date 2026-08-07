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
blocked** — the two replay scenarios (`workflow-journal-projection`, `rewind-during-stream`), the
workflow-run half of resume-audit (`resume_audit.py` still exports `audit_loop` only), the
`harness/exemplars/` backfill (README-only today), and §3's Self-QA Companion (zero footprint).
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
