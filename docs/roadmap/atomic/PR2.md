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
| `PR2-7` | ⬜ | Automation would-execute rendering on the trust surface (§3.3) | `PR2-4`, `EXT:WORKFLOWS-V2-AUTOMATION-SUBSTRATE:automation_run(dry_run) + AUTO-R15 dry-fire smoke gate` | the trust surface renders a per-trigger would-execute description (resolved next-fire, rendered action_config with $vars, target session key, capability grants, observe-mode result) via AUTOMATION-SUBSTRATE's automation_run(dry_run)/AUTO-R15 dry-fire, beside the surfacing simulator (criterion #4 automation half). |
| `PR2-8` | ⬜ | Re-home remediation engine onto AUTOMATION-SUBSTRATE adaptive-clock trigger + runs-inbox digest (§4.3/§4.4) | `PR2-5`, `EXT:WORKFLOWS-V2-AUTOMATION-SUBSTRATE:triggers.json + adaptive-clock trigger kind + runs-inbox digest` | the remediation engine runs as ONE adaptive-clock trigger (created_by: system) on the Automations page instead of the heartbeat job, and its runs are picked up by the runs-inbox learned-overnight digest like any other run. |
| `PR2-9` | ⬜ | Richer memory-pipeline alarm + flywheel/knowledge degraded floors (§3.2 richer, §5.2) | `PR2-2`, `PR2-4`, `EXT:WORKFLOWS-V2-LEARNING-FLYWHEEL:LEARN-R19 staging outcomes + cost metering`, `EXT:WORKFLOWS-V2-KNOWLEDGE-SYNTHESIS:KNOW-R17 heuristic extraction floor` | the memory-pipeline Doctor row raises a FLUSH_OK-streak WARN from real LEARN-R19 records (staging backlog + per-op cost, criterion #5); degraded knowledge_ingest registers the KNOW-R17 heuristic extractor, memory_extraction registers the LEARN-R19 staging drain, and the synthesis-watcher append_evidence contract is registered with a drain (criterion #3 full re-enrichment flow). |
| `PR2-10` | ⬜ | ACP mid-turn steer delivery path (§6.2 remainder) | `PR2-6`, `EXT:ACP-AGENT-PARITY:authenticated ACP CLI (owner-gated) + mid-turn session/prompt delivery seam` | an ACP tool-boundary drain seam is built so a dialect flipping supports_mid_turn_prompt actually delivers a mid-turn steer instead of silently dropping it, validated against an authenticated ACP CLI. |
| `PR2-11` | ⬜ | Retire duplicate heartbeat maintenance jobs into the remediation engine (§4.4) | `PR2-5` | heartbeat FTS rebuild, daily history/SEL prunes, skill-curator aging, and inbox 6h maintenance are removed from the heartbeat and run only as remediation-engine registered jobs (verify_skill_integrity finally scheduled), so success criterion #6 'old heartbeat maintenance no longer runs independently' holds after the engine has soaked. |

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

**Status:** todo

§3.3 Automation dry-run affordance

**Done when:** the trust surface renders a per-trigger would-execute description (resolved next-fire, rendered action_config with $vars, target session key, capability grants, observe-mode result) via AUTOMATION-SUBSTRATE's automation_run(dry_run)/AUTO-R15 dry-fire, beside the surfacing simulator (criterion #4 automation half).

### `PR2-8` — Re-home remediation engine onto AUTOMATION-SUBSTRATE adaptive-clock trigger + runs-inbox digest (§4.3/§4.4)

**Status:** todo

§4.3 Adaptive idle cadence; §4.4 What it absorbs (disposition)

**Done when:** the remediation engine runs as ONE adaptive-clock trigger (created_by: system) on the Automations page instead of the heartbeat job, and its runs are picked up by the runs-inbox learned-overnight digest like any other run.

### `PR2-9` — Richer memory-pipeline alarm + flywheel/knowledge degraded floors (§3.2 richer, §5.2)

**Status:** todo

§3.2 Memory-pipeline probe set; §5.2 Per-surface tiers (knowledge / memory / synthesis)

**Done when:** the memory-pipeline Doctor row raises a FLUSH_OK-streak WARN from real LEARN-R19 records (staging backlog + per-op cost, criterion #5); degraded knowledge_ingest registers the KNOW-R17 heuristic extractor, memory_extraction registers the LEARN-R19 staging drain, and the synthesis-watcher append_evidence contract is registered with a drain (criterion #3 full re-enrichment flow).

### `PR2-10` — ACP mid-turn steer delivery path (§6.2 remainder)

**Status:** todo

Execution log Session 6 S6.2 (ACP capability gate) + §6 not-done ACP delivery

**Done when:** an ACP tool-boundary drain seam is built so a dialect flipping supports_mid_turn_prompt actually delivers a mid-turn steer instead of silently dropping it, validated against an authenticated ACP CLI.

### `PR2-11` — Retire duplicate heartbeat maintenance jobs into the remediation engine (§4.4)

**Status:** todo

§4.4 What it absorbs (disposition)

**Done when:** heartbeat FTS rebuild, daily history/SEL prunes, skill-curator aging, and inbox 6h maintenance are removed from the heartbeat and run only as remediation-engine registered jobs (verify_skill_integrity finally scheduled), so success criterion #6 'old heartbeat maintenance no longer runs independently' holds after the engine has soaked.

