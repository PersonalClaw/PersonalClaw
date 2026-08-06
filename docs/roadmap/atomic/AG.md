# AUTONOMY-GUARDRAILS — atomic plans

**Source plan:** [`AUTONOMY-GUARDRAILS`](../plans/AUTONOMY-GUARDRAILS.md)  
**Code:** `AG`  
**Source status:** in_progress

Plan is IN PROGRESS: the S1-S4 substrate (chokepoint, budgets/scan, floor, profiles/egress/health/FE) is fully shipped and catalogued as 4 done atoms. Seven todo atoms cover the inert SafetyProfile/egress-tier wiring gap (audit 2026-08-04, independently completable), the S5.1/S5.2/S6.1 earned-autonomy rung ladder (needs FEEDBACK-SIGNAL S1), and four logged deferrals (apps-repo native structured_output + channel send() live-writes; run-scope budgets awaiting AUTOMATION-SUBSTRATE; profile/trust enforcement behaviors awaiting WORKFLOWS-V2 engine per-template profiles).

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `AG-1` | ✅ | Model-call chokepoint core (§2): ModelCallGuard, breaker, timeout, audit, output_type | — | ModelCallGuard wraps the bridge-resolved reasoning-use-case provider; breaker/hard-timeout/JSONL audit live; one_shot_completion(output_type=…) returns typed data via targeted retry with zero silent None at migrated sites; full gate green (7820 passed) — DONE per Execution log 2026-07-25 |
| `AG-2` | ✅ | Budgets + outbound scan + GuardrailsConfig (§1.1, §2.2) | `AG-1` | a per-minute trigger hitting its per-day token/dollar ceiling pauses into needs-input and auto-resumes next day; a secret-shaped payload is blocked at scan (secret_leak, non-retryable); config round-trips — DONE per log 2026-07-25 (7838 passed) |
| `AG-3` | ✅ | Safety floor: denylist + incident kill switch + DISABLE_LIVE_WRITES + guard_flag (§1.2-§1.4, §5) | `AG-2` | incident stops every unattended fire within one poll interval (chat untouched, explicit-confirm resume, SEL-audited); a denylisted ~/.ssh/** or **/.env* path refused by every provider incl. app webhook-action; suite runs with DISABLE_LIVE_WRITES auto-set — DONE per log 2026-07-25 (7876 passed) |
| `AG-4` | ✅ | Safety profiles + egress tiers + provider health view + FE (§3, §4.2, §2.5, §4.4) | `AG-1`, `AG-2`, `AG-3` | Settings→Guardrails renders breaker state + p50/p90/p99 latency; profile_for_session classifies unattended session keys → HEADLESS read-only; egress tiers resolve; full web gate green — DONE per log 2026-07-25 (7898 passed, 231 vitest) |
| `AG-5` | ⬜ | Wire SafetyProfile / egress-tier into dispatch seams + spawn (close inert control) | `AG-4` | at least one dispatch/spawn seam consults profile_for_session in production; SafetyProfile.tool_grants/denylist_extra/egress_tier have non-test readers; Success Criterion #7 (unattended run resolves through HEADLESS by construction) holds outside tests/test_guardrails_profiles.py |
| `AG-6` | ⬜ | S5.1 earned-autonomy rung ladder core (guardrails/autonomy.py) | `AG-4`, `EXT:FEEDBACK-SIGNAL:S1 👍/👎 records feed derived eligibility` | a type with 10 clean approvals over 7 days + 0 rejections is eligible; one rejection demotes immediately and starts cooldown; incident clamps resolution above one_tap; eligibility is recomputed, never cached to disk |
| `AG-7` | ⬜ | S5.2 action-type declarations + manifest autonomy block + rung routing at seams | `AG-6`, `AG-5`, `EXT:INBOX-NOTIF-UNIFICATION:emit_attention_item(kind=proposal) for draft_only routing — pre-42 skills/proposals.py fallback keeps this unblocked` | an app-contributed action inherits its declared floor/ceiling with no dispatch-layer special-casing; a leaves_machine type cannot resolve autonomous without an explicit ceiling raise |
| `AG-8` | ⬜ | S6.1 promotion proposals + rung FE chips/ladder panel/undo + validation sweep | `AG-7` | promotion never happens without a click; an undo click both reverses the action and demotes the type; the rung chip answers 'why is this allowed to run by itself?' in one glance; full web gate green |
| `AG-9` | ⬜ | Apps-repo guardrails follow-ons: native structured_output + channel send() live-writes (cross-repo) | `AG-1`, `AG-3` | an ollama-bound output_type call uses native json-schema format; a channel app's send() returns a typed refusal under DISABLE_LIVE_WRITES; core's structured_output dispatch hook is exercised natively |
| `AG-10` | ⬜ | Run-scope budget enforcement + per-trigger budget fields | `AG-2`, `EXT:AUTOMATION-SUBSTRATE:Trigger.gates contract must exist before building this seam` | a run crossing its per-run ceiling mid-flight has its next LLM call refused and the run parked; per-trigger budget fields round-trip on Trigger.gates |
| `AG-11` | ⬜ | Deferred profile/trust enforcement behaviors awaiting engine consumers | `AG-5`, `EXT:WORKFLOWS-V2:engine per-template profiles + project-script-execution seam (project loop.md / Code-loop deliverable gate)` | auto-fired research spawns default-deny write/execute tools; gateway approval resolves through profile_for_session; a project folder's first script touch prompts Trust vs Preview and persists the decision |

## Atom scopes

### `AG-1` — Model-call chokepoint core (§2): ModelCallGuard, breaker, timeout, audit, output_type

**Status:** done

§2.1 seam at provider_bridge return; §2.3 per-provider three-state breaker + hard timeout; §2.4 output_type on one_shot_completion + structured_output capability-dispatch hook; §2 attempt-level model_calls.jsonl audit; migrate top parse_llm_json call sites; judge bounded-reasoning field (Session 1)

**Done when:** ModelCallGuard wraps the bridge-resolved reasoning-use-case provider; breaker/hard-timeout/JSONL audit live; one_shot_completion(output_type=…) returns typed data via targeted retry with zero silent None at migrated sites; full gate green (7820 passed) — DONE per Execution log 2026-07-25

### `AG-2` — Budgets + outbound scan + GuardrailsConfig (§1.1, §2.2)

**Status:** done

§1.1 SpendMeter + spend.json, budget checks at due-collection / gateway dispatch / mid-run chokepoint / subagent spawn, pause-into-needs-input (extends _maybe_autopause); §2.2 PII/secret scan WARN/REDACT/BLOCK; §6/§7 GuardrailsConfig (BudgetConfig+BreakerConfig+scan_mode) through the four wiring points (Session 2)

**Done when:** a per-minute trigger hitting its per-day token/dollar ceiling pauses into needs-input and auto-resumes next day; a secret-shaped payload is blocked at scan (secret_leak, non-retryable); config round-trips — DONE per log 2026-07-25 (7838 passed)

### `AG-3` — Safety floor: denylist + incident kill switch + DISABLE_LIVE_WRITES + guard_flag (§1.2-§1.4, §5)

**Status:** done

§1.2 check_action/enforce_action at the three dispatch seams (hooks/gateway/event_triggers) + sdk.guardrails re-exports; §1.3 incident.json flag + seam checks + GET|POST /api/incident + CLI; §1.4 DISABLE_LIVE_WRITES honored (net.fetch, delete_model) + conftest auto-set; §5 guard_flag fail-safe parser + safe-default schema test (Session 3)

**Done when:** incident stops every unattended fire within one poll interval (chat untouched, explicit-confirm resume, SEL-audited); a denylisted ~/.ssh/** or **/.env* path refused by every provider incl. app webhook-action; suite runs with DISABLE_LIVE_WRITES auto-set — DONE per log 2026-07-25 (7876 passed)

### `AG-4` — Safety profiles + egress tiers + provider health view + FE (§3, §4.2, §2.5, §4.4)

**Status:** done

§3 SafetyProfile frozen dataclass + six named profiles + safety_profile_for + is_unattended_session/profile_for_session classifier; §4.2 REGISTRY egress profile + egress_policy_for_tier; §2.5 provider_health() + GET /api/models/health; §4.4 FE GuardrailsPanel (incident/budgets/scan/breaker/health) + IncidentBanner (Session 4)

**Done when:** Settings→Guardrails renders breaker state + p50/p90/p99 latency; profile_for_session classifies unattended session keys → HEADLESS read-only; egress tiers resolve; full web gate green — DONE per log 2026-07-25 (7898 passed, 231 vitest)

### `AG-5` — Wire SafetyProfile / egress-tier into dispatch seams + spawn (close inert control)

**Status:** todo

Status-line audit finding 2026-08-04: §3 profile_for_session + §4.2 egress_policy_for_tier consumed at the three dispatch seams (hooks.run_script_hook, gateway._run_action_job, event_triggers._fire) + SubagentManager.spawn, giving SafetyProfile.tool_grants/denylist_extra/egress_tier real (non-test) readers. Plan assigns this to S5.2 but it carries no FEEDBACK-SIGNAL dep, so it is independently completable.

**Done when:** at least one dispatch/spawn seam consults profile_for_session in production; SafetyProfile.tool_grants/denylist_extra/egress_tier have non-test readers; Success Criterion #7 (unattended run resolves through HEADLESS by construction) holds outside tests/test_guardrails_profiles.py

### `AG-6` — S5.1 earned-autonomy rung ladder core (guardrails/autonomy.py)

**Status:** todo

Amendment 2026-07-26 §Contract-level design + S5.1 row: RUNGS ladder, ActionTypeSpec, register_action_type, resolve_rung (floor+grants clamped to ceiling, incident_active() clamp above one_tap), DERIVED promotion_eligibility over SEL tool_approved/tool_rejected + FEEDBACK-SIGNAL records, demote+cooldown; autonomy_rungs.json store (grants/demotions only); guardrails.autonomy config through the four wiring points

**Done when:** a type with 10 clean approvals over 7 days + 0 rejections is eligible; one rejection demotes immediately and starts cooldown; incident clamps resolution above one_tap; eligibility is recomputed, never cached to disk

### `AG-7` — S5.2 action-type declarations + manifest autonomy block + rung routing at seams

**Status:** todo

Amendment S5.2 row: core action-type keys at action_providers/registry.py + inbox affordances (inbox.reply_draft, sessions.auto_tag); additive manifest autonomy:{floor,ceiling} block (apps/manifest.py, keyed app:<name>.<action>); rung routing composed with enforce_action at the three dispatch seams + profile_for_session (draft_only→proposal item, one_tap→ApprovalGate.request, auto_with_undo→execute+reversal handle+notify, autonomous→SEL)

**Done when:** an app-contributed action inherits its declared floor/ceiling with no dispatch-layer special-casing; a leaves_machine type cannot resolve autonomous without an explicit ceiling raise

### `AG-8` — S6.1 promotion proposals + rung FE chips/ladder panel/undo + validation sweep

**Status:** todo

Amendment S6.1 row: user-click promotion proposals (SEL-audited like a skill install); FE rung chip on trigger/job rows + ladder panel in Settings→Guardrails (current rung, derived record, demotion history) + undo affordance on auto_with_undo notifications; as-a-user validation sweep

**Done when:** promotion never happens without a click; an undo click both reverses the action and demotes the type; the rung chip answers 'why is this allowed to run by itself?' in one glance; full web gate green

### `AG-9` — Apps-repo guardrails follow-ons: native structured_output + channel send() live-writes (cross-repo)

**Status:** todo

Session-1 deferral: BrandedProviderSpec.structured_output declaration + native json-schema enforcement (ollama format= / OpenAI-wire response_format) lighting up core's capability-dispatch hook (§2.4, Success Criterion #6 ollama half). Session-3 deferral: channel-transport send() calls sdk.guardrails.live_writes_disabled() before transmit (§1.4). Both are PersonalClawApps commits against seams core already shipped.

**Done when:** an ollama-bound output_type call uses native json-schema format; a channel app's send() returns a typed refusal under DISABLE_LIVE_WRITES; core's structured_output dispatch hook is exercised natively

### `AG-10` — Run-scope budget enforcement + per-trigger budget fields

**Status:** todo

Session-2 deferral of §1.1 run scope: thread a run-key through dispatch to activate SpendMeter's already-built run scope; add per-trigger budget fields as Trigger.gates {budget:{...}} so the pause becomes a needs-input run in the Runs inbox

**Done when:** a run crossing its per-run ceiling mid-flight has its next LLM call refused and the run parked; per-trigger budget fields round-trip on Trigger.gates

### `AG-11` — Deferred profile/trust enforcement behaviors awaiting engine consumers

**Status:** todo

Session-4 deferrals: §4.1 read-only research subagent class (SubagentManager.spawn capability_class; default-deny write/execute enforced by the tool-approval layer); the live cron-approval rewire from the ad-hoc AUTO_APPROVE/HOOK_BASED branch to profile_for_session; §4.3 Trust/Preview project-folder gate (project_trust.json, Preview→REVIEW_ONLY) for project-script execution

**Done when:** auto-fired research spawns default-deny write/execute tools; gateway approval resolves through profile_for_session; a project folder's first script touch prompts Trust vs Preview and persists the decision

