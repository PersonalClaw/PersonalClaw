# PLATFORM-RESILIENCE — atomic plans

**Source plan:** [`PLATFORM-RESILIENCE`](../plans/PLATFORM-RESILIENCE.md)  
**Code:** `PR2`  
**Source status:** done

6 sessions shipped (Doctor core, no-model degraded contract, mid-turn queue/cancel-replace, confirm-gated fixes + simulators + crash capture, health-scored remediation engine, mid-turn steering). 5 todo atoms capture the E6-deferred cross-plan integration remainders and one intra-plan cleanup (full heartbeat-maintenance retirement, success criterion #6).

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `PR2-1` | ✅ | Doctor tiered-probe framework + per-capability probe packs + read-only Settings tab | — | resilience/doctor.py runs tiers 0-3 short-circuiting downward, grouped by capability; healthy core + dead capability reports core OK with no restart flag (doctrine test, success criterion #1); GET /api/doctor (30s cache) + GET /api/doctor/{capability}; Settings Doctor tab renders capability cards with tier-failed badges; static/dist symlink-vs-copy detection (criterion #2 detection half). |
| `PR2-2` | ✅ | Platform-wide no-model degraded contract, registry, chip + honesty lint | `PR2-1` | resilience/degraded.py DegradedContract registry + evaluate(); GET /api/resilience/degraded; shell degraded chip + down/recovery transition notifications; lint test asserts every one_shot_completion call-site maps to a registered contract; ResilienceConfig guard flags (doctor_enabled, degraded_indicator) round-trip through the 5-point config contract. |
| `PR2-3` | ✅ | Mid-turn message handling — queue + cancel-and-replace + ActiveJobTracker | `PR2-2` | resilience/active_jobs.py tracker (register at turn start / clear at turn end); cancel_and_replace policy on an interactive channel soft-cancels via stop_turn(preserve_queue=True), broadcasts superseded, and queues the new message; queue policy delivers next turn; loop/cron/subagent sessions never cancelled (criterion #7); mid_turn_policy + cancel_replace_min_interval_secs round-trip. |
| `PR2-4` | ✅ | Confirm-gated fixes + surfacing/memory-pipeline simulators + structured crash capture | `PR2-1`, `PR2-2` | resilience/fixes.py registry (symlink-repair with shadow-copy backup, orphan-prune, prune-bindings) SEL-audited behind a two-step confirm (400 without confirm); POST /api/doctor/simulate/surfacing returns per-signal breakdown with zero LLM calls (criterion #4 surfacing half); crashes + memory-pipeline Doctor probes; redacted, 20-file-capped crash artifacts; POST /api/model-providers/{name}/selftest. |
| `PR2-5` | ✅ | Health-scored self-remediation engine (deficit scoring, capped plan) on the heartbeat | `PR2-1`, `PR2-2`, `EXT:AUTONOMY-GUARDRAILS:SpendMeter + model_calls.jsonl cost audit for the judgment lane` | resilience/remediation.py deficit scoring with max_reachable ceilings + reachable-exclusion; dependency-ordered plan (after: edges) stops at target_score / max_cost_usd / exhausted; judgment lane charges guardrails SpendMeter under run_key doctor; per-job success-only cooldown; doctor/remediation.jsonl ledger + doctor/jobs.json; runs as ONE adaptive-cadence heartbeat job; Doctor Maintenance section renders score + recent runs (criterion #6 score-raise + ledger). |
| `PR2-6` | ✅ | Third mid-turn policy `steer` + ACP capability gate + composer affordance | `PR2-3` | steer joins the mid_turn_policy enum (5-point wiring + PATCH values + roundtrip); a native turn injects a mid-turn steer at the next tool boundary (drain called before the no-tool-call return, keyed on the namespaced session key); non-capable runtime falls back to queue loudly; add_steer keyed on a wired drain source (no cross-turn leak); composer steer control + steered strip; validated as a user on Bedrock. |
| `PR2-7` | ✅ | Automation would-execute rendering on the trust surface (§3.3) | `PR2-4`, `EXT:WORKFLOWS-V2-AUTOMATION-SUBSTRATE:automation_run(dry_run) + AUTO-R15 dry-fire smoke gate` | the trust surface renders a per-trigger would-execute description (resolved next-fire, rendered action_config with $vars, target session key, capability grants, observe-mode result) via AUTOMATION-SUBSTRATE's automation_run(dry_run)/AUTO-R15 dry-fire, beside the surfacing simulator (criterion #4 automation half). |
| `PR2-8` | ⬜ | Re-home remediation engine onto AUTOMATION-SUBSTRATE adaptive-clock trigger + runs-inbox digest (§4.3/§4.4) | `PR2-5`, `EXT:WORKFLOWS-V2-AUTOMATION-SUBSTRATE:triggers.json + adaptive-clock trigger kind + runs-inbox digest` | the remediation engine runs as ONE adaptive-clock trigger (created_by: system) on the Automations page instead of the heartbeat job, and its runs are picked up by the runs-inbox learned-overnight digest like any other run. |
| `PR2-9` | ✅ | Richer memory-pipeline alarm + flywheel/knowledge degraded floors (§3.2 richer, §5.2) | `PR2-2`, `PR2-4`, `EXT:WORKFLOWS-V2-LEARNING-FLYWHEEL:LEARN-R19 staging outcomes + cost metering`, `EXT:WORKFLOWS-V2-KNOWLEDGE-SYNTHESIS:KNOW-R17 heuristic extraction floor` | the memory-pipeline Doctor row raises a FLUSH_OK-streak WARN from real LEARN-R19 records (staging backlog + per-op cost, criterion #5); degraded knowledge_ingest registers the KNOW-R17 heuristic extractor, memory_extraction registers the LEARN-R19 staging drain, and the synthesis-watcher append_evidence contract is registered with a drain (criterion #3 full re-enrichment flow). |
| `PR2-10` | ✅ | ACP mid-turn steer delivery path (§6.2 remainder) | `PR2-6`, `EXT:ACP-AGENT-PARITY:authenticated ACP CLI (owner-gated) + mid-turn session/prompt delivery seam` | an ACP tool-boundary drain seam is built so a dialect flipping supports_mid_turn_prompt actually delivers a mid-turn steer instead of silently dropping it, validated against an authenticated ACP CLI. |
| `PR2-11` | ✅ | Retire duplicate heartbeat maintenance jobs into the remediation engine (§4.4) | `PR2-5` | heartbeat FTS rebuild, daily history/SEL prunes, skill-curator aging, and inbox 6h maintenance are removed from the heartbeat and run only as remediation-engine registered jobs (verify_skill_integrity finally scheduled), so success criterion #6 'old heartbeat maintenance no longer runs independently' holds after the engine has soaked. |

## Atom scopes

### `PR2-1` — Doctor tiered-probe framework + per-capability probe packs + read-only Settings tab

**Status:** done

§1 Doctor — Tiered Probe Framework

**Done when:** resilience/doctor.py runs tiers 0-3 short-circuiting downward, grouped by capability; healthy core + dead capability reports core OK with no restart flag (doctrine test, success criterion #1); GET /api/doctor (30s cache) + GET /api/doctor/{capability}; Settings Doctor tab renders capability cards with tier-failed badges; static/dist symlink-vs-copy detection (criterion #2 detection half).

### `PR2-2` — Platform-wide no-model degraded contract, registry, chip + honesty lint

**Status:** done

§5 Platform-Wide No-Model Degraded Mode

**Done when:** resilience/degraded.py DegradedContract registry + evaluate(); GET /api/resilience/degraded; shell degraded chip + down/recovery transition notifications; lint test asserts every one_shot_completion call-site maps to a registered contract; ResilienceConfig guard flags (doctor_enabled, degraded_indicator) round-trip through the 5-point config contract.

### `PR2-3` — Mid-turn message handling — queue + cancel-and-replace + ActiveJobTracker

**Status:** done

§6 Mid-Turn Message Handling — Prompt Queue + Cancel-and-Replace

**Done when:** resilience/active_jobs.py tracker (register at turn start / clear at turn end); cancel_and_replace policy on an interactive channel soft-cancels via stop_turn(preserve_queue=True), broadcasts superseded, and queues the new message; queue policy delivers next turn; loop/cron/subagent sessions never cancelled (criterion #7); mid_turn_policy + cancel_replace_min_interval_secs round-trip.

### `PR2-4` — Confirm-gated fixes + surfacing/memory-pipeline simulators + structured crash capture

**Status:** done

§2 Confirm-Gated Auto-Fixes; §3.1 Surfacing simulator; §3.2 Memory-pipeline probe set; §6.5 Structured Crash Capture

**Done when:** resilience/fixes.py registry (symlink-repair with shadow-copy backup, orphan-prune, prune-bindings) SEL-audited behind a two-step confirm (400 without confirm); POST /api/doctor/simulate/surfacing returns per-signal breakdown with zero LLM calls (criterion #4 surfacing half); crashes + memory-pipeline Doctor probes; redacted, 20-file-capped crash artifacts; POST /api/model-providers/{name}/selftest.

### `PR2-5` — Health-scored self-remediation engine (deficit scoring, capped plan) on the heartbeat

**Status:** done

§4 Health-Scored Self-Remediation Engine (Wave 3)

**Done when:** resilience/remediation.py deficit scoring with max_reachable ceilings + reachable-exclusion; dependency-ordered plan (after: edges) stops at target_score / max_cost_usd / exhausted; judgment lane charges guardrails SpendMeter under run_key doctor; per-job success-only cooldown; doctor/remediation.jsonl ledger + doctor/jobs.json; runs as ONE adaptive-cadence heartbeat job; Doctor Maintenance section renders score + recent runs (criterion #6 score-raise + ledger).

### `PR2-6` — Third mid-turn policy `steer` + ACP capability gate + composer affordance

**Status:** done

Amendment (2026-07-26) mid-turn steering; Execution log Session 6 (S6.1–S6.3)

**Done when:** steer joins the mid_turn_policy enum (5-point wiring + PATCH values + roundtrip); a native turn injects a mid-turn steer at the next tool boundary (drain called before the no-tool-call return, keyed on the namespaced session key); non-capable runtime falls back to queue loudly; add_steer keyed on a wired drain source (no cross-turn leak); composer steer control + steered strip; validated as a user on Bedrock.

### `PR2-7` — Automation would-execute rendering on the trust surface (§3.3)

**Status:** done — PR #2023

§3.3 Automation dry-run affordance

**Done when:** the trust surface renders a per-trigger would-execute description (resolved next-fire, rendered action_config with $vars, target session key, capability grants, observe-mode result) via AUTOMATION-SUBSTRATE's automation_run(dry_run)/AUTO-R15 dry-fire, beside the surfacing simulator (criterion #4 automation half).

### `PR2-8` — Re-home remediation engine onto AUTOMATION-SUBSTRATE adaptive-clock trigger + runs-inbox digest (§4.3/§4.4)

**Status:** todo

§4.3 Adaptive idle cadence; §4.4 What it absorbs (disposition)

**Done when:** the remediation engine runs as ONE adaptive-clock trigger (created_by: system) on the Automations page instead of the heartbeat job, and its runs are picked up by the runs-inbox learned-overnight digest like any other run.

### `PR2-9` — Richer memory-pipeline alarm + flywheel/knowledge degraded floors (§3.2 richer, §5.2)

**Status:** done — PR #2022

§3.2 Memory-pipeline probe set; §5.2 Per-surface tiers (knowledge / memory / synthesis)

**Done when:** the memory-pipeline Doctor row raises a FLUSH_OK-streak WARN from real LEARN-R19 records (staging backlog + per-op cost, criterion #5); degraded knowledge_ingest registers the KNOW-R17 heuristic extractor, memory_extraction registers the LEARN-R19 staging drain, and the synthesis-watcher append_evidence contract is registered with a drain (criterion #3 full re-enrichment flow).

### `PR2-10` — ACP mid-turn steer delivery path (§6.2 remainder)

**Status:** done

Execution log Session 6 S6.2 (ACP capability gate) + §6 not-done ACP delivery

**Done when:** an ACP tool-boundary drain seam is built so a dialect flipping supports_mid_turn_prompt actually delivers a mid-turn steer instead of silently dropping it, validated against an authenticated ACP CLI.

### `PR2-11` — Retire duplicate heartbeat maintenance jobs into the remediation engine (§4.4)

**Status:** todo — **every HEARTBEAT-driven pass landed 2026-08-13**; the inbox 6h pass is
outstanding (it was never on the heartbeat — see the plan's `## Execution log`)

§4.4 What it absorbs (disposition)

**What landed.** The four passes the heartbeat actually drove now have registered engine
jobs, and the heartbeat no longer runs them while `resilience.remediation.enabled` is true:
FTS rebuild → `memory.rebuild-fts`, history prune → `memory.prune-history`, SEL prune →
`sel.prune`, skill aging → `skills.age` (already registered). Each new job was driven under
an isolated home and proven to do the WORK, not merely to run (index reconciled to disk,
files deleted plus their orphan FTS rows, 300 aged SEL entries removed; score 59 → 100, one
ledger row, second pass a no-op). `HeartbeatService._legacy_maintenance` keeps the old
cadence as the **declared** fallback for `enabled=false`, so disabling the engine never
leaves a system with no maintenance — tested both ways (criterion #6: exactly one owner per
tick, never both).

**`verify_skill_integrity` is scheduled as a DETECTOR, not a job.** It cannot reduce a
deficit — nothing un-tampers a skill, and re-baselining a mutated one would launder the
tamper — so it runs as a measured, `reachable=False`, job-less deficit (`skills_tampered`)
on every engine pass and Doctor read, surfaced on the Doctor's deficit list with its
existing SEL audit. Previously it only ran when a human opened the Skills page.

**Discovery (fixed here).** Two shipped deficits (`orphan_locks`, `skill_aging_due`) capped
at `max_penalty=10.0` — exactly `100 − target_score`. `run_remediation` returns before
planning while `score_before >= target_score`, so at ANY backlog those jobs scored 90.0 and
stopped with "target_score already met": `skills.age` could never be scheduled by its own
deficit, which would have made retiring the heartbeat's aging pass a silent gap. Both
ceilings raised to 12.0 and the floor is now pinned by a rail
(`_MIN_SCHEDULABLE_PENALTY`).

**Remainder.** Inbox 6h maintenance is NOT heartbeat-driven (`InboxService._loop`,
`inbox_service.py:198`, its own `_MAINTENANCE_EVERY_SECS` timer), and its pass mutates the
LIVE `InboxStore`/`InboxState` plus `check_retire_candidates(state=_dashboard_state())`.
The module-global job registry has no handle on the running service, so registering it
means gateway-side registration bound to `self.inbox_svc`; constructing a fresh store in
the engine thread instead would fork the live store. Left running where it is.

**Done when:** heartbeat FTS rebuild, daily history/SEL prunes, skill-curator aging, and inbox 6h maintenance are removed from the heartbeat and run only as remediation-engine registered jobs (verify_skill_integrity finally scheduled), so success criterion #6 'old heartbeat maintenance no longer runs independently' holds after the engine has soaked.

