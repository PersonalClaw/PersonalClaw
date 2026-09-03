# SELF-VERIFICATION — atomic plans

**Source plan:** [`SELF-VERIFICATION`](../plans/SELF-VERIFICATION.md)  
**Code:** `SV`  
**Source status:** in_progress

SELF-VERIFICATION: 7 of 11 atoms done (spec harness + scanner + replay substrate + loop resume-audit/MCP + WF2 replay scenarios + workflow-run resume-audit + per-slice WF2 exemplars). Remaining: CI gate and the two-session Self-QA Companion. WF2 engine now exists so the prior blocker cleared.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `SV-1` | ✅ | Spec harness core: harness/ package, 3 spec kinds, validate/explain, profiles, AGENT.md | — | python -m harness validate passes on seeded 8 rules/3 scenarios/1 task spec; a dangling requiredTests node-id fails validation; AGENT.md exists at repo root covering the two canonical gotchas (Success Criteria #1, #9) |
| `SV-2` | ✅ | Static boundary scanner + diff-aware run + same-PR rule + Makefile wiring | `SV-1` | harness run --diff forces the replay profile when touching web/src/pages/chat/; a config field missing from AppConfig.load() fails config-four-points with a WHAT/WHY/FIX message; clean-tree calibration guard passes (Success Criterion #2) |
| `SV-3` | ✅ | Event-trace replay substrate: recorder taps, Python metrics fold, vitest fold driver, baselines | `SV-1` | PERSONALCLAW_TRACE_DIR-gated recorder writes redacted NDJSON; replaying happy-path/history-overlap traces through coalesceReducers.ts reproduces terminal state with duplicate_event_rate=0; a re-introduced K44 coalescer bug is caught by replay not a hand-written test (Success Criterion #3) |
| `SV-4` | ✅ | MCP record/replay-as-fake-server + loop resume-audit + exemplars scaffold | `SV-3` | FakeMcpServer replays a recorded mcp trace deterministically; harness resume-audit <loop_id> reconstructs a loop from loop.store alone and reports done/verified/next answerability (Success Criterion #5, loop half); harness/exemplars/README.md ships the exemplar contract |
| `SV-5` | ✅ | WF2 replay scenarios (workflow-journal-projection + rewind-during-stream) + baselines, gating the journal format | `SV-3`, `SV-4`, `EXT:WORKFLOWS-V2:journal event format + engine/journal.py (Slices 1-2) to record against` | workflow-journal-projection recorded and green with its checked-in baseline before any WF2 Slice 3+ consumer reads the journal; a format change breaking the event-fold law fails compare; a missing required scenario fails the run (Success Criterion #4) |
| `SV-6` | ✅ | Workflow-run half of resume-audit: byte-equal frontier reconstruction from the event-fold | `SV-4`, `EXT:WORKFLOWS-V2:event-fold law + frontier snapshot contract` | resume-audit kills and resumes a persisted workflow run from disk alone and verifies the journal replay reconstructs frontier state byte-equal to the pre-kill snapshot (Success Criterion #5, workflow half) |
| `SV-7` | ✅ | Wire python -m harness validate\|scan into CI (ci.yml) and fold harness/ into CI lint/test scope | `SV-2` | ci.yml runs harness validate + scanner over changed files as a required check and lints/tests harness/ under the same locked deps as core; a spec/scanner regression fails CI |
| `SV-8` | ✅ | Backfill per-slice runnable exemplars for the landed WF2 slices | `SV-4`, `EXT:WORKFLOWS-V2:landed slice mechanisms to exercise` | harness/exemplars/ holds runnable exemplars for the landed WF2 slices (e.g. Slice 2 3-node run with a failing required_artifacts gate), each with a smoke script and rationale note, runnable via harness run profile exemplars; validate flags a slice merged without its exemplar |
| `SV-9` | ✅ | Self-QA Companion core: commit-watch cron script, self-qa bundled template, self_qa config four-point wiring | `SV-3`, `EXT:WORKFLOWS-V2:Slices 0-5 (bundled template pack + run ledger + required_artifacts host)`, `EXT:AUTONOMY-GUARDRAILS:headless profile/budgets inherited when present (graceful)` | a real commit to the watched repo fires the companion within one cron interval; a test-only commit yields a ledger-only skip with a one-line rationale; a user-impacting commit generates a scenario that mutates state through the real UI via Chrome DevTools MCP; a failing scenario files one Inbox item + one Task (Success Criterion #6) |
| `SV-10` | ⬜ | Self-QA evidence bundle capture + optional fix-branch + end-to-end validation | `SV-9`, `EXT:WORKFLOWS-V2:WF2-R3 required_artifacts gate`, `EXT:WORK-CONTAINERS:WORK-R4 Proof-section rendering (graceful without it)`, `EXT:LEARNING-FLYWHEEL:LEARN-R8 failure-capsule lifecycle (companion is producer only)` | a failing scenario produces screenshots+MP4+contact-sheet+GIF+logs under one SHA256'd manifest registered as a single Artifact, and the required_artifacts gate blocks completion when any declared proof is missing (Criterion #7); with fix_branch_enabled a confirmed finding yields a never-merged pclaw/selfqa-<sha8> branch linked in the Task (Criterion #8) |
| `SV-11` | ⬜ | Retire the interim commit-watcher cron script when the AUTO-R12 vcs trigger lands | `SV-9`, `EXT:AUTOMATION-SUBSTRATE:AUTO-R12 vcs trigger preset (file kind watching .git/refs/heads/*)` | the self-qa template rebinds to AUTOMATION-SUBSTRATE's vcs trigger preset, the bundled selfqa_commit_watch.py cron script is deleted, and a rule spec asserts its absence once the vcs trigger kind exists |

## Atom scopes

### `SV-1` — Spec harness core: harness/ package, 3 spec kinds, validate/explain, profiles, AGENT.md

**Status:** done

§1.1 (three spec kinds), §1.2 (validate|explain + profiles), §4.2 (AGENT.md)

**Done when:** python -m harness validate passes on seeded 8 rules/3 scenarios/1 task spec; a dangling requiredTests node-id fails validation; AGENT.md exists at repo root covering the two canonical gotchas (Success Criteria #1, #9)

### `SV-2` — Static boundary scanner + diff-aware run + same-PR rule + Makefile wiring

**Status:** done

§1.3 (7 seed scanner checks), §1.4 (same-PR rule), §7 Session 2

**Done when:** harness run --diff forces the replay profile when touching web/src/pages/chat/; a config field missing from AppConfig.load() fails config-four-points with a WHAT/WHY/FIX message; clean-tree calibration guard passes (Success Criterion #2)

### `SV-3` — Event-trace replay substrate: recorder taps, Python metrics fold, vitest fold driver, baselines

**Status:** done

§2.1 (trace format + WS/SSE/inbox/mcp taps), §2.2 (Python + vitest replay), §2.3 (baselines.json)

**Done when:** PERSONALCLAW_TRACE_DIR-gated recorder writes redacted NDJSON; replaying happy-path/history-overlap traces through coalesceReducers.ts reproduces terminal state with duplicate_event_rate=0; a re-introduced K44 coalescer bug is caught by replay not a hand-written test (Success Criterion #3)

### `SV-4` — MCP record/replay-as-fake-server + loop resume-audit + exemplars scaffold

**Status:** done

§2.1 (MCP record/replay rider), §2.4 (resume-audit, loop half), §4.1 (exemplars dir scaffold)

**Done when:** FakeMcpServer replays a recorded mcp trace deterministically; harness resume-audit <loop_id> reconstructs a loop from loop.store alone and reports done/verified/next answerability (Success Criterion #5, loop half); harness/exemplars/README.md ships the exemplar contract

### `SV-5` — WF2 replay scenarios (workflow-journal-projection + rewind-during-stream) + baselines, gating the journal format

**Status:** done

§2.3 (required scenarios 4-5: workflow-journal-projection, rewind-during-stream), §2.1 (workflow journal→SSE projection tap)

**Done when:** workflow-journal-projection recorded and green with its checked-in baseline before any WF2 Slice 3+ consumer reads the journal; a format change breaking the event-fold law fails compare; a missing required scenario fails the run (Success Criterion #4)

### `SV-6` — Workflow-run half of resume-audit: byte-equal frontier reconstruction from the event-fold

**Status:** done

§2.4 (resume-audit workflow-run half; resume_audit.py exports only audit_loop today)

**Done when:** resume-audit kills and resumes a persisted workflow run from disk alone and verifies the journal replay reconstructs frontier state byte-equal to the pre-kill snapshot (Success Criterion #5, workflow half)

### `SV-7` — Wire python -m harness validate|scan into CI (ci.yml) and fold harness/ into CI lint/test scope

**Status:** todo

Status line (harness validate|scan not yet a CI gate — ci.yml lints only src/personalclaw+tests), §1.4 same-PR enforcement, §7 Session 2 (standard test entrypoint wiring)

**Done when:** ci.yml runs harness validate + scanner over changed files as a required check and lints/tests harness/ under the same locked deps as core; a spec/scanner regression fails CI

### `SV-8` — Backfill per-slice runnable exemplars for the landed WF2 slices

**Status:** done

§4.1 (milestone exemplars: per-slice standalone spec + ≤30s smoke script + rationale note under harness/exemplars/<slice>/)

**Done when:** harness/exemplars/ holds runnable exemplars for the landed WF2 slices (e.g. Slice 2 3-node run with a failing required_artifacts gate), each with a smoke script and rationale note, runnable via harness run profile exemplars; validate flags a slice merged without its exemplar

### `SV-9` — Self-QA Companion core: commit-watch cron script, self-qa bundled template, self_qa config four-point wiring

**Status:** todo

§3.1 (interim commit-watch cron script + state file), §3.2 steps 1-5 (triage/scenario-gen/execute/evidence/file-findings nodes), §5 (self_qa config through all four wiring points)

**Done when:** a real commit to the watched repo fires the companion within one cron interval; a test-only commit yields a ledger-only skip with a one-line rationale; a user-impacting commit generates a scenario that mutates state through the real UI via Chrome DevTools MCP; a failing scenario files one Inbox item + one Task (Success Criterion #6)

### `SV-10` — Self-QA evidence bundle capture + optional fix-branch + end-to-end validation

**Status:** todo

§3.3 (ffmpeg capture + contact sheet + GIF + SHA256 manifest + Artifact registration + required_artifacts gate), §3.2 step 6 (optional fix-branch stage), §7 Session 6 (as-a-user validation)

**Done when:** a failing scenario produces screenshots+MP4+contact-sheet+GIF+logs under one SHA256'd manifest registered as a single Artifact, and the required_artifacts gate blocks completion when any declared proof is missing (Criterion #7); with fix_branch_enabled a confirmed finding yields a never-merged pclaw/selfqa-<sha8> branch linked in the Task (Criterion #8)

### `SV-11` — Retire the interim commit-watcher cron script when the AUTO-R12 vcs trigger lands

**Status:** todo

§6 (explicit AUTO-R12 retirement row), Risk table (rule spec asserting the cron script is absent once the vcs trigger kind exists)

**Done when:** the self-qa template rebinds to AUTOMATION-SUBSTRATE's vcs trigger preset, the bundled selfqa_commit_watch.py cron script is deleted, and a rule spec asserts its absence once the vcs trigger kind exists

