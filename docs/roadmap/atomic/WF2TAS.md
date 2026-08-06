# WORKFLOWS-V2-TASKS-SOPS — atomic plans

**Source plan:** [`WORKFLOWS-V2-TASKS-SOPS`](../plans/WORKFLOWS-V2-TASKS-SOPS.md)  
**Code:** `WF2TAS`  
**Source status:** done

WORKFLOWS-V2-TASKS-SOPS is fully shipped (PRs #195-#215, on main). 11 done atoms catalogued at the plan's 7 planned session seams plus the S61b-S61k wiring epic that made each inert decision module reachable. No open work remains in this plan.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `WF2TAS-1` | ✅ (##195) | Projection core: WorkflowTaskBinding + Task fields + auto-materialization | `EXT:WORKFLOWS-V2:engine InstanceState taxonomy + SUCCESS_STATES/TERMINAL_STATES sets` | tasks/models.py gains WorkflowTaskBinding, TaskStatus.SKIPPED and six projection fields; workflows/materialize.py owns the state→status table, fingerprint dedup, fan-out caps and the write-rejection rule; round-trip tests green (92 tests, S55) |
| `WF2TAS-2` | ✅ | Verified done + enforcement: engine-owned criterion, actor matrix, cascade-fail, stuck-work sweep | `WF2TAS-1`, `EXT:WORKFLOWS-V2:loop/gates run_verify_command tristate + audit_bash_command screen` | workflows/verified_done.py: engine-owned criterion execution over loop/gates tristate, pass-state gating, weighted acceptance schema, three-actor transition matrix, binding-graph cascade with debounced notify, stuck-work sweep, idempotent timing (70 tests, S56) |
| `WF2TAS-3` | ✅ | ConfirmationRequest + gates: durable typed record, atomic single-use resolution, require_hitl, per-stage mute/tool-profiles | `WF2TAS-1` | workflows/confirmation.py: one durable record, per-type expiry policy, four-verb resolution vocabulary, require_hitl, per-stage mute, tool profiles; shipped defects fixed in human_input.py (os.rename claim primitive) + security.py (redact gaps) (58 tests, S57) |
| `WF2TAS-4` | ✅ | Surfacing core: surface_mode enum, trigger-phrase match_text, negative triggers, metadata split, one-source-two-wrappers | `WF2TAS-1` | workflows/surfacing.py: surface_mode enum, trigger-phrase match_text with word cap + collision/negative triggers, 0.62/0.7 algorithm port preserving never-break-a-turn bridge, metadata split, drift() coexistence check, unreachable() floor (80 tests, S58) |
| `WF2TAS-5` | ✅ | Surfacing channels + resolution: cadence, workspace-fingerprint packs, scope resolution, param pre-fill, requirements preflight, doctor | `WF2TAS-4` | workflows/surfacing_channels.py: cadence channel + once-daily overdue escalation via create-task, fingerprint packs, scope resolution/shadowing, availability three-state preflight, reachability doctor accepting any channel (113 tests, S59); DEVIATION: link block moved to cadence ledger (create-task drops unknown keys) |
| `WF2TAS-6` | ✅ | Pool + hand-offs + blueprints: frontier/next projections, evented unblock, TTL'd lease decisions, task lifecycle events, blueprint sessions | `WF2TAS-1`, `WF2TAS-2` | workflows/pool.py: frontier/next projections over all tasks, evented auto-unblock (all-prereqs rule), lease CAS decision rules, edge-triggered TaskComplete payload builder, delegated server-authoritative acyclicity, hand-off suggest with allowlisted fields, blueprint hydration replace-not-merge (78 tests, S60) |
| `WF2TAS-7` | ✅ | Def-side surfacing fields on DefMetadata + one adapter per record type | `WF2TAS-4`, `WF2TAS-5`, `WF2TAS-6` | DefMetadata gains typed surface_mode/cadence_days/escalation/packs/hands_off_to/guided with safe-direction coercion; adapters meta_from_def/cadence_from_def/handoffs_from_def/doctor_entry/route_from_def bridge defs to the S55-S60 record types; route() structure-first fix (33 tests, S61 RE-SCOPED) |
| `WF2TAS-8` | ✅ | Backend wiring making surfacing reachable: author_def metadata param, list_defs_surfacing route, TaskComplete fired | `WF2TAS-7` | author_def gains metadata param (write via DefMetadata.from_dict().to_dict()); GET /api/workflows/surfacing + list_defs_surfacing as second route; TaskComplete emitted from NativeTaskProvider.update_task edge-triggered via pool.should_fire_completion; route ordering test (23 tests, S61b backend) |
| `WF2TAS-9` | ✅ | FE surfacing surfaces: surfacingMeta.ts, composer chips, validated deep-links, templates-list freshness/scope/pack rendering | `WF2TAS-8` | surfacingMeta.ts mirrors workflowMeta discipline reading backend-computed state; off def gets no chip, suggest gets run affordance; deep-link params allowlisted against declared inputs with reported rejections; list degrades not blanks; validated as-a-user against live gateway with zero console messages (34 FE tests, S61c) |
| `WF2TAS-10` | ✅ | Lease write path + confirmation resolve endpoint + task-projection events on both channels | `WF2TAS-3`, `WF2TAS-6` | sidecar lease file with single_flight CAS (0 multi-winner across 8 processes); POST /runs/{id}/confirm riding resume_run with verb→boolean; five ledger kinds + SSE events (task_materialized/confirmation_pending/confirmation_resolved/task_verified/cascade_blocked) with FE WORKFLOW_LIFECYCLE union guard tests (S61d + S61e, 34+20 py + 6 FE tests) |
| `WF2TAS-11` | ✅ | Engine call sites + Task write + verified-done/confirmation emission + DagView composition + config four-point (+fifth) wiring | `WF2TAS-1`, `WF2TAS-2`, `WF2TAS-3`, `WF2TAS-9`, `WF2TAS-10`, `EXT:WORKFLOWS-V2:RunController tick/completion path + _publish stream` | RunController projects at node settle (correct id/TaskSpec keys), writes the managed Task via engine actor with reject_write guard, executes done_criterion (passed+unrunnable tristate) and emits confirmation-gate events; WorkflowRunDetail renders DagView with wired Approve/Deny; four WorkflowsConfig fields + fifth settings.py resolver point wired and validated as-a-user; checklist checked-locks-drag + two-stage delete (S61f–S61k, ~120 tests); DEVIATION: match_threshold not re-added |

## Atom scopes

### `WF2TAS-1` — Projection core: WorkflowTaskBinding + Task fields + auto-materialization

**Status:** done (PR ##195)

§1 State Projection (R1), Projection Enforcement (R5-core), Task Body Contract (R12), New Field WorkflowTaskBinding, Materialization Flow

**Done when:** tasks/models.py gains WorkflowTaskBinding, TaskStatus.SKIPPED and six projection fields; workflows/materialize.py owns the state→status table, fingerprint dedup, fan-out caps and the write-rejection rule; round-trip tests green (92 tests, S55)

### `WF2TAS-2` — Verified done + enforcement: engine-owned criterion, actor matrix, cascade-fail, stuck-work sweep

**Status:** done

§1 Verified Done (R2), Projection Enforcement three-actor matrix (R5-rest), Cascade-Fail Propagation (R17); §5 diagnostics

**Done when:** workflows/verified_done.py: engine-owned criterion execution over loop/gates tristate, pass-state gating, weighted acceptance schema, three-actor transition matrix, binding-graph cascade with debounced notify, stuck-work sweep, idempotent timing (70 tests, S56)

### `WF2TAS-3` — ConfirmationRequest + gates: durable typed record, atomic single-use resolution, require_hitl, per-stage mute/tool-profiles

**Status:** done

§4 ConfirmationRequest ONE Durable Typed Record (R6), Guardrails & Postconditions (R9), Per-Stage Mute/Tool Profiles/Approval Memory (R13)

**Done when:** workflows/confirmation.py: one durable record, per-type expiry policy, four-verb resolution vocabulary, require_hitl, per-stage mute, tool profiles; shipped defects fixed in human_input.py (os.rename claim primitive) + security.py (redact gaps) (58 tests, S57)

### `WF2TAS-4` — Surfacing core: surface_mode enum, trigger-phrase match_text, negative triggers, metadata split, one-source-two-wrappers

**Status:** done

§2 Surfacing Discipline (R3), Surfacing Metadata & Injection Contract (R4), Composition-Direction Lint (R14), Dual Mode; §2 Migration Path

**Done when:** workflows/surfacing.py: surface_mode enum, trigger-phrase match_text with word cap + collision/negative triggers, 0.62/0.7 algorithm port preserving never-break-a-turn bridge, metadata split, drift() coexistence check, unreachable() floor (80 tests, S58)

### `WF2TAS-5` — Surfacing channels + resolution: cadence, workspace-fingerprint packs, scope resolution, param pre-fill, requirements preflight, doctor

**Status:** done

§2 Channel 2 Cadence (R8), Channel 3 Workspace Fingerprint (R19), Layered Scope Resolution & Shadowing (R18), Parameter Pre-Fill + Requirements Preflight (R11), Reachability doctor (R3)

**Done when:** workflows/surfacing_channels.py: cadence channel + once-daily overdue escalation via create-task, fingerprint packs, scope resolution/shadowing, availability three-state preflight, reachability doctor accepting any channel (113 tests, S59); DEVIATION: link block moved to cadence ledger (create-task drops unknown keys)

### `WF2TAS-6` — Pool + hand-offs + blueprints: frontier/next projections, evented unblock, TTL'd lease decisions, task lifecycle events, blueprint sessions

**Status:** done

§5 Frontier/Next Projections, Evented Unblock, Leases (R10); §2 Hand-Off Edges (R7), Blueprint Sessions (R16)

**Done when:** workflows/pool.py: frontier/next projections over all tasks, evented auto-unblock (all-prereqs rule), lease CAS decision rules, edge-triggered TaskComplete payload builder, delegated server-authoritative acyclicity, hand-off suggest with allowlisted fields, blueprint hydration replace-not-merge (78 tests, S60)

### `WF2TAS-7` — Def-side surfacing fields on DefMetadata + one adapter per record type

**Status:** done

§2 Surfacing Metadata (R4) def fields; §8 item 1 New fields on WorkflowDef

**Done when:** DefMetadata gains typed surface_mode/cadence_days/escalation/packs/hands_off_to/guided with safe-direction coercion; adapters meta_from_def/cadence_from_def/handoffs_from_def/doctor_entry/route_from_def bridge defs to the S55-S60 record types; route() structure-first fix (33 tests, S61 RE-SCOPED)

### `WF2TAS-8` — Backend wiring making surfacing reachable: author_def metadata param, list_defs_surfacing route, TaskComplete fired

**Status:** done

§9 Provider & Config Integration Map; §7 board liveness; §2 Learning Path author_def

**Done when:** author_def gains metadata param (write via DefMetadata.from_dict().to_dict()); GET /api/workflows/surfacing + list_defs_surfacing as second route; TaskComplete emitted from NativeTaskProvider.update_task edge-triggered via pool.should_fire_completion; route ordering test (23 tests, S61b backend)

### `WF2TAS-9` — FE surfacing surfaces: surfacingMeta.ts, composer chips, validated deep-links, templates-list freshness/scope/pack rendering

**Status:** done

§7 Surfacing UX (R15) — composer chips, validated deep-links, templates list freshness gradient/scope states/pack proposals

**Done when:** surfacingMeta.ts mirrors workflowMeta discipline reading backend-computed state; off def gets no chip, suggest gets run affordance; deep-link params allowlisted against declared inputs with reported rejections; list degrades not blanks; validated as-a-user against live gateway with zero console messages (34 FE tests, S61c)

### `WF2TAS-10` — Lease write path + confirmation resolve endpoint + task-projection events on both channels

**Status:** done

§5 TTL'd lease claims write path (R10); §4 ConfirmationRequest resolve (R6); §7/§8 items 4/11 engine events + FE stream union

**Done when:** sidecar lease file with single_flight CAS (0 multi-winner across 8 processes); POST /runs/{id}/confirm riding resume_run with verb→boolean; five ledger kinds + SSE events (task_materialized/confirmation_pending/confirmation_resolved/task_verified/cascade_blocked) with FE WORKFLOW_LIFECYCLE union guard tests (S61d + S61e, 34+20 py + 6 FE tests)

### `WF2TAS-11` — Engine call sites + Task write + verified-done/confirmation emission + DagView composition + config four-point (+fifth) wiring

**Status:** done

§1 Materialization Flow call site + Task write (ENGINE actor); §7 DagView Approve/Deny wiring + checklist edit UX; §9 Config four points + settings.py resolvers

**Done when:** RunController projects at node settle (correct id/TaskSpec keys), writes the managed Task via engine actor with reject_write guard, executes done_criterion (passed+unrunnable tristate) and emits confirmation-gate events; WorkflowRunDetail renders DagView with wired Approve/Deny; four WorkflowsConfig fields + fifth settings.py resolver point wired and validated as-a-user; checklist checked-locks-drag + two-stage delete (S61f–S61k, ~120 tests); DEVIATION: match_threshold not re-added

