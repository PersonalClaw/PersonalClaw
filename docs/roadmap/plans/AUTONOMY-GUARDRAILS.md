# Plan: Autonomy Guardrails — Safety Floor + Model-Call Chokepoint

**Status:** PROPOSED (created 2026-07-12 from research synthesis)
**Created:** 2026-07-12
**Wave:** 0/1 front-runner — this plan FRONT-RUNS unattended work. It must land before AUTOMATION-SUBSTRATE's unattended waves (Wave 3+); the engine, planner, judges, and flywheel all inherit reliability from the §2 seam.
**Depends on:** nothing (Wave-0-compatible). AUTOMATION-SUBSTRATE and WORKFLOWS-V2 later *consume* the substrate (trigger gates absorb budget fields; run engine consumes typed outputs).
**Scope:** one cross-cutting safety floor consulted before anything runs unattended (budgets, denylist, kill switch, safety profiles) + one gateway seam wrapping every background/tool LLM call (metering, circuit breakers, scan, typed structured output).

---

## Research Integration (2026-07-12)

- **NEW-1** (Autonomy Guardrail Substrate: run/day/trigger token+dollar+wall-clock ceilings, path/action denylist, incident kill switch, DISABLE_LIVE_WRITES, graduated safety profiles) → §1, §3, §4, data model §6, Slices 2-4.
- **NEW-1 amendment** (fail-safe guard-flag parsing tenet: missing/null/unknown parses ENABLED) → §5 (platform tenet).
- **NEW-1 amendments5** (egress policy tiers incl. curated package-registry preset; Trust/Preview gate for untrusted project folders; named `headless` profile resolved by construction; read-only-by-default research subagent class) → §4.2-§4.5.
- **NEW-2** (Model-Call Control Chokepoint: metering, per-provider circuit breaker, hard timeout, failure-mode-classified targeted retry, ordered fallback with degraded provenance, attempt-level JSONL audit; `structured_output` capability + `output_type` on `one_shot_completion` with capability-dispatched enforcement; provider health view) → §2, §3, Slice 1.
- **NEW-2 amendments5** (composable secret/PII scan wrapper, WARN/REDACT/BLOCK, at the model-call seam) → §2.2.

---

## Overview

PersonalClaw already has three proven chokepoints: network egress (`net/guard.py:evaluate` + `net/policy.py` named profiles + `egress_policy_for`), skill installs (`skills/marketplace.py:install_guarded`), and untrusted-content fencing (`security.py:fence_untrusted`). It has **no equivalent for autonomous execution or for LLM calls themselves**. Verified gaps: WORKFLOWS-V2 leaves run budgets an open question; AUTOMATION-SUBSTRATE's only failure policy is autopause-after-5-failures (`gateway.py _maybe_autopause`); no plan owns global caps, denylists, a kill switch, or run-cost governance; and `parse_llm_json` (`llm_helpers.py:232`) only strips markdown fences and returns `None` — every call site silently degrades.

This plan builds the two missing chokepoints as one substrate, because they share a nervous system: **budgets need metering, and metering only exists once every LLM call passes through one seam.**

1. **The Guardrail Substrate (§1, §4)** — a policy floor consulted before anything fires unattended: token/dollar/wall-clock ceilings that pause into needs-input, a machine-readable path/action denylist enforced at the action-dispatch seams, a global incident mode, a process-wide `DISABLE_LIVE_WRITES` flag, and named safety profiles (modeled directly on `net/policy.py`'s frozen `EgressPolicy` + named profiles + operator-layering pattern — the architectural template this plan copies).
2. **The Model-Call Control Chokepoint (§2)** — the LLM twin of `net.fetch`: one guard wrapping every background/tool LLM call (`one_shot_completion`, `eval/judge.py:LLMJudge`, planners, synthesizers) with scan → breaker → meter → call → validate → targeted retry → fallback → audit.

**Soul guardrail:** this is a *personal* safety floor — one user, one gateway, config files plus one policy check per seam. No ops console, no RBAC, no fleet dashboards. The provider health view (§2.5) is a Settings panel derived from files already on disk, not telemetry infrastructure.

---

## 1. The Guardrail Substrate

### 1.1 Budgets — token + dollar + wall-clock ceilings

```python
@dataclass(frozen=True)
class Budget:
    max_tokens: int = 0          # 0 = unlimited
    max_dollars: float = 0.0
    max_wall_secs: int = 0
    scope: str = "run"           # run | day | trigger

# guardrails/budgets.py
class SpendMeter:
    def charge(self, scope_key, tokens, dollars) -> BudgetVerdict  # ok | warn(>80%) | exceeded
```

- **What exists today (build on, don't reinvent):** wall-clock ceilings already exist per cron job — `timeout_secs` (default 1800, clamp 1..86400, `schedule.py:_execute_with_timeout` L1241) plus the reaper (`start_reaper` L550, killpg escalation). These are KEPT and become the `max_wall_secs` enforcement for clock triggers. Token and dollar ceilings are NEW — nothing meters LLM spend anywhere today. Metering arrives with the §2 chokepoint: every attempt record carries `token_count` and a dollar estimate; `SpendMeter` folds them into per-scope counters (`~/.personalclaw/spend.json`, atomic_write, one row per (date, scope_key)).
- **Enforcement points (reality-corrected):** there is **no timer heap** — the cron engine is a single re-armed `asyncio.Task` (`schedule.py:_arm_timer` L1070) polling ≤30s. Budget checks therefore hook: (a) due-collection in `_on_timer` (a job whose day-scope budget is exhausted is skipped + paused, not fired), (b) `gateway.py:_run_action_job` (:689) and `_cron_callback` (:816) before dispatch, (c) mid-run via the §2 chokepoint (`SpendMeter.charge` on every attempt — a run that crosses its ceiling mid-flight gets its next LLM call refused and the run parked), (d) `SubagentManager.spawn` — budgets thread through spawn alongside the existing `agent.max_subagents` / `agent.subagent_max_turns` caps (already PUT-editable, `dashboard/handlers/core.py:258`).
- **Threshold behavior — pause into needs-input:** at ceiling, the trigger/job flips to `paused` state and a needs-input notification fires through `DashboardState.notify` (the existing gate, `providers/entity_routes.py:notification_allowed`). This extends the proven `_maybe_autopause` (5-consecutive-failures) precedent to budget exhaustion. When AUTOMATION-SUBSTRATE lands, these become fields on `Trigger.gates` (`{budget: {...}}`) and the pause becomes a needs-input run in the Runs inbox — the substrate absorbs, the mechanism is identical.

### 1.2 Path/action denylist — honored by ALL action providers

```python
# guardrails/denylist.py
@dataclass(frozen=True)
class DenyRule:
    paths: list[str]      # globs: ~/.ssh/**, **/.env*, secrets/**, ~/.personalclaw/sel_hmac.key ...
    actions: list[str]    # action classes: external-write, delete, credential-read
    verdict: str          # block | needs_human

def check_action(provider_name: str, action_config: dict, ctx: ActionContext) -> DenyDecision
```

- **What exists (extend, don't duplicate):** `security.py` already has `BUILTIN_DENY_PATTERNS` (tool-name fnmatch), `BUILTIN_DENIED_COMMAND_PATTERNS` (bash regexes incl. self-tamper), `is_sensitive_path`, and the operator extension `AppConfig.security.denied_commands`. What is MISSING is a *path-level* denylist for autonomous action-provider runs — the machine-readable "loop-constraints" analog. `check_action` composes the built-ins + `security.autonomy_denylist` config into one decision.
- **Enforcement placement (reality-corrected):** action providers are pluggable — apps deliver them (`apps/webhook-action` precedent), so enforcement CANNOT rely on provider cooperation. `check_action` is called at the **three dispatch seams** every action-provider execution passes through: `hooks.py:494` (script hooks), `gateway.py:701` (scheduled jobs), `event_triggers.py:214` (memory-event triggers) — an app-contributed provider inherits the denylist without knowing it exists. A blocked action returns `ActionResult(blocked=True)` with the matched rule, and logs to the SEL (`sel.py`), same as egress blocks.
- `sdk.guardrails` re-exports `check_action` (alongside the `sdk.net` / `sdk.security` precedents) so well-behaved providers can also pre-check.
- Denied ≠ silently dropped: `verdict: needs_human` routes to a needs-input notification with the action payload attached, mirroring the mandatory-human-gate pattern (security/auth paths, diffs touching credentials).

### 1.3 Incident kill switch

- One flag: `~/.personalclaw/incident.json` (`atomic_write`; `{active, reason, started_at}`) + an in-process mirror refreshed by the existing mtime-sync habit.
- **Enforcement (reality-corrected):** there is **no unified triggers store** to flip — six independent stores (`crons.json`, `hooks.json`, `event_triggers.json`, autonudge, HEARTBEAT.md, inbox). Incident mode therefore does NOT mutate stores; it is checked at the **execution seams**: `_on_timer` due-collection, `hooks.py` `_fire`/`fire_for_ids`, the event-trigger engine fire path, `autonudge._on_fire`, the heartbeat tick, inbox AI affordances (classify/draft/digest), and `SubagentManager.spawn` for non-interactive spawns. Every seam already exists; each gains one `if incident_active(): skip + record`.
- Suspension is total for unattended work within one poll interval (≤30s for crons, next tick for the rest); **interactive chat is untouched** — the user talking to their assistant during an incident is the point.
- Resume is EXPLICIT: `POST /api/incident/resume {confirm: true}` or `personalclaw incident off`. Activation/resume are SEL-audited; the incident window is recorded so the Runs surface can show "suppressed during incident."

### 1.4 DISABLE_LIVE_WRITES

- Process-wide env flag `PERSONALCLAW_DISABLE_LIVE_WRITES=1`, **auto-set in conftest** for the whole test suite. PClaw was already bitten by exactly this bug class: a destructive test with no `_models_dir` monkeypatch deleted the user's real bound L6 model.
- Honored by: external-write action providers (webhook, send-message toward non-loopback transports), channel transport `send()`, local-model `delete_model`, and `net.fetch` non-GET methods to non-loopback hosts. Each returns a typed refusal, never a silent no-op, so a test asserting a write FAILS loudly instead of passing vacuously.

---

## 2. The Model-Call Control Chokepoint

### 2.1 The seam (where it wraps — reality-grounded)

Every background/tool LLM call already funnels through two narrow points:

1. `llm_helpers.py:275 one_shot_completion(prompt, use_case=…)` — maps informal labels to the `reasoning` chat sub-category (deliberately a plain `ModelProvider`, not the native runtime) and resolves via
2. `providers/provider_bridge.py:477 resolve_provider_for_use_case` — the resolution path for ALL use-case-bound calls, including `eval/judge.py:LLMJudge` (which builds via `provider_factory("eval_judge")` and does NOT go through `one_shot_completion`).

The chokepoint is a `ModelCallGuard` adapter wrapped around the resolved `ModelProvider` **at the bridge return** for non-interactive capabilities (reasoning, background, eval_judge, summarization, planning, code_tools-one-shot) — so judges, planners, synthesizers, and every `one_shot_completion` caller inherit it without call-site changes. The interactive chat stream (the `NativeAgentRuntime` path that chat/code_tools resolution returns) is explicitly **out of scope for v1** — it has a human watching it.

Pipeline per call (each stage skippable by config, ordered cheap-first):

```
scan (PII/secret, §2.2) → circuit-breaker check (§2.3) → meter (§1.1) →
call with hard timeout → output-contract validation (§2.4) →
failure-mode-classified targeted retry → ordered fallback chain → attempt-level audit
```

- **Failure-mode taxonomy** (typed enum, recorded on every attempt): `schema_violation | constraint_violation | injection_blocked | token_overflow | timeout | circuit_open | provider_error`. The mode selects retry behavior: per-mode correction notes injected into the next attempt's prompt ("Return ONLY a valid JSON object…"); `injection_blocked` and `circuit_open` are **never retried** (retrying an injection lets a payload brute-force the guard).
- **Ordered fallback chain with degraded provenance:** on exhausted retries the guard walks the use-case's remaining active refs (the same `active_models.json` list the bridge already iterates), respecting the existing invariant that an unresolvable *pinned* ref raises (`ProviderResolutionError`, "block, don't silently fall back") — fallback applies only across refs the user actually bound. A fallback-satisfied result carries `degraded: true` so consumers (flywheel, judges) can discount it.
- **Attempt-level JSONL audit:** `~/.personalclaw/model_calls.jsonl` — one line per attempt: `{audit_id, ts, use_case, provider, model, attempt, failure_mode, latency_ms, tokens_in/out, dollars_est, passed, strategy, degraded}`. `audit_id` correlates all attempts of one request. Capped/rotated like `notifications.jsonl` (trim at 2× cap). Security-relevant events (scan blocks, breaker trips) additionally go to the SEL.

### 2.2 Secret/PII scan (WARN / REDACT / BLOCK)

- A composable wrapper at the same seam, complementing the network egress chokepoint from the *content* side: every outbound prompt passes the scan before leaving the machine.
- Builds on what exists: `security.py:redact()` (exfil URLs + credentials) supplies the redaction pass; `supply_chain.py:SkillScanner.scan_text` supplies the rule-engine shape. New: PII patterns (email/phone/key-shaped strings) + the mode ladder — `warn` (log + SEL, proceed), `redact` (apply `redact()`-style substitution, proceed), `block` (refuse the call, `injection_blocked`/`secret_leak` failure mode, no retry).
- Mode is configured per use-case class: default `redact` for calls bound to remote providers, `warn` for local-only providers (content never leaves the machine — personal-scale proportionality).

### 2.3 Per-provider circuit breaker

- Three-state FSM per `ProviderEntry.name`: CLOSED → OPEN after N consecutive failures (default 5) → HALF_OPEN after `recovery_secs` (default 30) → CLOSED on success. In-process state is fine for a single-user gateway (a restart resetting it is acceptable).
- `is_open()` is checked BEFORE any prompt work: during an outage, overnight unattended runs fail in microseconds instead of stacking 30s timeouts — the worst case the automation substrate would otherwise hit.
- Hard timeout on every call (`asyncio.wait_for`, per-use-case default), classified `timeout` (retryable, with a "respond shorter" correction note).

### 2.4 Typed structured output — `output_type` + capability dispatch

```python
result = await one_shot_completion(prompt, use_case="background",
                                   output_type=MyPydanticModel)   # or Literal[...] / Regex(...)
```

- **`structured_output` capability on the provider contract:** a new declared-capability value carried where capabilities already live — `ProviderEntry.declared_capabilities` / `ProviderCapability` (`llm/registry.py`), declared by branded apps via `BrandedProviderSpec.capabilities` (`sdk/provider_helpers.py`), and inferable in `llm/catalog.py:infer_capabilities`. Values: `none | json_mode | json_schema` (regex/cfg reserved for a future local logits path).
- **Capability-dispatched enforcement:** providers declaring `json_schema` (ollama via its `format` parameter; OpenAI-wire `response_format`) get native enforcement — the parser runs as the generator. Everything else gets parse-with-**targeted**-retry: the retry turn re-presents the schema plus the parse-error location (the dominant real-world failure cause is the schema not being visible).
- **Replaces the silent degrade:** `parse_llm_json` (`llm_helpers.py:232`) — verified today to only strip markdown fences and return `None`, with every call site silently degrading — is superseded at migrated call sites (nl_to_cron, memory lint, inbox classify/draft/digest, judge verdict parse, preference facets). Judge verdicts additionally adopt the "bounded `reasoning` field before the verdict field" schema shape (constraints must not suppress chain-of-thought).
- Typed escape hatch as contract: `output_type=Union[Plan, Literal["cannot_plan"]]`-style unions make refusal parseable instead of a parse failure.

### 2.5 Provider health view (falls out nearly free)

- Derived, not collected: breaker states (CLOSED/OPEN/HALF_OPEN), consecutive-failure counts, P50/P90/P99 latency, and failure-mode distribution computed from `model_calls.jsonl` + in-memory breaker state.
- **New backend route** `GET /api/models/health` + a panel in Settings → Models. Note (reality correction): `capableModels` is a **frontend** function (`web/src/pages/settings/ModelsPanel.tsx:43`) — there is no backend symbol to extend; the health view is a new route, and the FE panel composes it next to the existing per-use-case model rows. Directly serves the recurring provider-integrity campaign needs (ollama down, HF rate-limited).

---

## 3. Named Safety Profiles

Modeled line-for-line on the egress template: frozen dataclass + named module-level profiles + an operator-layering function (`net/policy.py:EgressPolicy` / `STRICT`/`CONNECTOR`/`WEBHOOK` / `egress_policy_for`).

```python
# guardrails/policy.py
@dataclass(frozen=True)
class SafetyProfile:
    name: str
    approval: str                 # auto | hook_based | ask
    tool_grants: ToolGrants       # read | read_write | custom allowlist
    egress_tier: str              # off | listed | registry | all   (§4.2)
    denylist_extra: tuple[DenyRule, ...]
    budget: Budget
    scan_mode: str                # warn | redact | block

INTERACTIVE = SafetyProfile(...)      # today's chat defaults
CODING      = SafetyProfile(...)      # write inside workspace, registry egress
REVIEW_ONLY = SafetyProfile(...)      # read-only tools, no external writes
CLEANUP     = SafetyProfile(...)      # delete allowed inside granted dirs only
INCIDENT    = SafetyProfile(...)      # everything denied except notify
HEADLESS    = SafetyProfile(...)      # unattended default: read-only + creation-time grants

def safety_profile_for(base: SafetyProfile) -> SafetyProfile   # layers operator config, like egress_policy_for
```

- **`headless` by construction:** unattended trigger-fired runs resolve through `HEADLESS` mechanically, keyed off the session-key conventions that already classify unattended work — the `_STATELESS_PREFIXES` set (`session.py:121`: `cron:`, `subagent:`, `channel:`, `inbox:`, `side:`) plus `loop-*` workers. Today the gateway picks `ToolApprovalPolicy.AUTO_APPROVE` vs `HOOK_BASED` ad-hoc in `_cron_callback`; the profile becomes the single object that decides approval + grants + egress + budget for a run. **Auto-fired runs default read-only; write/execute capability is a creation-time grant** stored on the job/trigger ("this cron may write under ~/notes"), reviewed by the user when the automation is created — never acquired mid-run.
- **Per-template graduated profiles:** when AUTOMATION-SUBSTRATE and WORKFLOWS-V2 land, a template names its profile (`coding` / `review-only` / `cleanup`); the profile mechanically constrains action-node tool grants. Until then, cron jobs and hooks carry an optional `safety_profile` field defaulting to `headless`.

---

## 4. Substrate Extensions (amendments)

### 4.1 Read-only-by-default research subagent class

`SubagentManager.spawn` gains `capability_class: research | mutating` (default `research` for auto-fired spawns). `research` = default-deny on write/execute tools — a declared class, enforced by the tool-approval layer, not by prompt. Existing caps (`max_subagents`, `subagent_max_turns`, the invoke-agent depth-3 + semaphore-6 guards) are untouched; budgets (§1.1) thread through the same call.

### 4.2 Egress policy tiers per run environment

Extends `net/policy.py`, does not fork it: add a `REGISTRY` named profile — a curated ~70-domain preset (pypi, npm, crates.io, docker.io, github.com, maven, …) + user wildcard additions. Run environments select a tier `off | listed | registry | all`; the tier picks/derives the `EgressPolicy` and everything else (guard.evaluate, pinned-IP resolver, redirect re-evaluation, operator layering via `egress_policy_for`) is inherited verbatim. One click gets a working sandboxed code run (`schedule_script.py:run_script_sandboxed`, loop workers) without opening the whole internet. Safety profiles carry the tier (§3).

### 4.3 Trust/Preview gate for untrusted project folders

Before a project-bound directory can execute *project* scripts (a project `<cwd>/loop.md` picked up by run-prompt, a Code-loop deliverable gate running project commands), the first touch asks **Trust** vs **Preview**. Preview = the run proceeds under `REVIEW_ONLY` (read-only, no script execution). Decisions persist in `~/.personalclaw/project_trust.json` keyed by resolved dir. Note: cron scripts are already path-fenced to `~/.personalclaw/crons/` (`schedule_script.py:resolve_script_path`) — the gate covers the *project-folder* gap, not the cron path.

### 4.4 Incident + profiles on the FE

A persistent incident banner (all pages) while active; profile chips on trigger/job rows; budget fields in the trigger create form's "Advanced" foldout (matching AUTOMATION-SUBSTRATE's two-field-form philosophy — guardrails are defaults, not homework).

---

## 5. Platform Tenet: Fail-Safe Guard-Flag Parsing

**Config flags guarding destructive/trust behavior parse missing/null/unknown values as ENABLED (fail-safe); only an explicit falsy value disables.** Applies to every guard-class field across all plans (this one, AUTOMATION-SUBSTRATE storm guards, WORKFLOWS-V2 gates).

- Helper: `guardrails.guard_flag(value) -> bool` for env/raw-dict reads (`DISABLE_LIVE_WRITES`, incident flag, denylist enabled).
- **Reality note on config-backed flags:** `_validate_config_data` (`config/loader.py:1164`) is advisory-only — it strips invalid values so *dataclass defaults* apply. The tenet therefore requires guard-class dataclass fields to have the SAFE value as their default (a config typo keeps the guard ON); `guard_flag` covers the paths that bypass the dataclass tree. A schema-test asserts every field tagged guard-class in `_meta` defaults safe.

---

## 6. Data Model & Stores

| Store | File (`~/.personalclaw/`) | Format | Notes |
|---|---|---|---|
| Guardrails config | `config.json` → `guardrails` section | `GuardrailsConfig` dataclass | Four wiring points (§7) |
| Spend meter | `spend.json` | JSON `{date: {scope_key: {tokens, dollars}}}` | atomic_write; pruned >30 days |
| Model-call audit | `model_calls.jsonl` | JSONL, one line/attempt | trim at 2× cap (notifications.jsonl pattern) |
| Incident flag | `incident.json` | JSON `{active, reason, started_at}` | atomic_write; SEL-audited transitions |
| Project trust | `project_trust.json` | JSON `{dir: {trusted, decided_at}}` | atomic_write |
| Breaker state | in-process only | — | restart resets (accepted, single-user) |

`GuardrailsConfig` (new top-level section beside `SecurityConfig`, `config/loader.py:1023`): `budgets` (default run/day ceilings), `autonomy_denylist` (paths, actions), `scan_mode` overrides, `breaker` (threshold, recovery_secs), `profile_overrides`. Snapshot/portability: `guardrails` files join `snapshot.py:CORE_FILES` (they are small JSON, cheap to include; note snapshot coverage is already partial — this plan does not claim to fix that).

---

## 7. Provider-Fidelity Wiring (where each piece plugs in)

- **No new provider TYPE.** Guardrails are substrate, not a provider family — same deliberate stance as "no space provider type" (`providers/registry.py:555`). Nothing here registers through `_TypeHandler`s.
- **Action providers:** unchanged contract (`action_providers/base.py:ActionProvider`); the denylist is enforced at the three dispatch seams (§1.2), so app-contributed providers inherit it. Any NEW action provider still MUST be added to `ALLOWED_HOOK_PROVIDERS` (`validation.py:555`) or hook create/update rejects it — this plan adds no new action providers, but the rule is restated because the substrate is where future ones will be born.
- **Model providers:** `structured_output` rides the existing capability channels — `ProviderEntry.declared_capabilities` (`llm/registry.py`), `BrandedProviderSpec.capabilities` (`sdk/provider_helpers.py`), `infer_capabilities` (`llm/catalog.py:206`). No factory signature change; the `ModelCallGuard` wraps the provider the bridge resolves, so the `model` build-kwarg override convention (`provider_bridge.py:844`) is untouched.
- **Config:** every new field wired through the FOUR points — (a) dataclass field with `_meta(label, help)` (schema reachability tests enforce), (b) `AppConfig.load()` explicit field-by-field mapping (omission = silent drop), (c) `to_dict()` (new top-level `guardrails` section added at `loader.py:1930`), (d) `_EDITABLE_CONFIG` (`dashboard/handlers/core.py:363`) + FE for the runtime-editable subset (scan_mode, breaker thresholds, default budgets, incident is NOT config — it's its own endpoint).
- **SDK:** `sdk.guardrails` re-exports `check_action`, `guard_flag`, and the scan wrapper, following the `sdk.net` / `sdk.security` facade precedent, so contributed apps can pre-check.
- **SEL:** every block, trip, incident transition, and budget pause logs to `sel.py:SecurityEventLog`, same as egress/skill-install guards.
- **Memory vs Knowledge boundary:** this plan touches neither. The audit/spend stores are harness mechanics (files under `~/.personalclaw/`), not memory entries and not knowledge items; nothing here writes to `memory.db` or `knowledge.db`. Lessons drawn from guardrail events (e.g., "this template keeps hitting schema_violation") belong to LEARNING-FLYWHEEL and stay propose-don't-write.

---

## 8. Implementation Effort

**~4 sessions.**

- **Session 1 — the chokepoint core (§2):** `ModelCallGuard` at the bridge seam; hard timeout; per-provider breaker; attempt-level JSONL audit; `output_type` on `one_shot_completion` with capability dispatch (`structured_output` capability declared for ollama + OpenAI-wire branded apps); migrate the top `parse_llm_json` call sites; judge verdict schema gains the bounded-reasoning field.
- **Session 2 — money and meters (§1.1, §2.2):** SpendMeter + `spend.json`; budget checks at due-collection / gateway dispatch / mid-run / subagent spawn; pause-into-needs-input (extending `_maybe_autopause`); PII/secret scan wrapper with WARN/REDACT/BLOCK; `GuardrailsConfig` through all four wiring points.
- **Session 3 — the floor (§1.2-§1.4, §5):** denylist + `check_action` at the three dispatch seams + `sdk.guardrails`; incident kill switch (flag + seam checks + endpoints + CLI); `DISABLE_LIVE_WRITES` honored + auto-set in conftest; `guard_flag` helper + safe-default schema test; SEL wiring throughout.
- **Session 4 — profiles and surfaces (§3, §4):** `SafetyProfile` + `safety_profile_for`; headless-by-construction resolution off session-key classes; read-only research subagent class; `REGISTRY` egress tier + per-run-environment tier selection; Trust/Preview project gate; FE (health panel, incident banner, budget/profile fields); as-a-user validation sweep.

Each session ships independently; Session 1 alone is a Wave-0 win (typed outputs + fail-fast on provider outages).

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| Chokepoint overhead on hot background paths | Cheap-first ordering (scan ~regex-speed, breaker check before any prompt work); stages individually skippable; interactive chat stream excluded in v1 |
| Breaker false-trips on flaky local models (ollama cold-start) | Per-provider thresholds; HALF_OPEN probe recovers in `recovery_secs`; local providers get a higher default threshold |
| Token/dollar metering inaccuracy (not all providers report usage) | Provider-reported usage preferred; tokenizer/char-heuristic fallback flagged `estimated: true`; budgets compare against the conservative (higher) estimate |
| Fail-safe parsing flips guards ON for existing users after upgrade | Migration note + one-time notification listing newly-active guards; defaults chosen so interactive behavior is unchanged (guards bite unattended paths first) |
| Denylist bypass via bash indirection (`cat` a denied path) | Framed honestly as defense-in-depth, not a sandbox: composes with existing `BUILTIN_DENIED_COMMAND_PATTERNS` + `is_sensitive_path` + the sandbox `wrap_argv`; the sandbox remains the containment story |
| Double-enforcement confusion with `HookManager` declarative denials | `HookManager` stays the per-tool policy layer (untouched, per AUTOMATION-SUBSTRATE's disposition); guardrails own the autonomous-run floor; `check_action` composes, never overrides, a builtin denial |
| Silent config drop (the four-wiring-points gotcha) | Explicit checklist in §7; schema reachability tests already enforce (a) and guard-default test added in Session 3 |
| Six-store incident sprawl regresses when the unified trigger store lands | Incident check lives in the execution seams the substrate will keep (dispatch, spawn) — the flag survives the store unification untouched |

---

## Success Criteria

1. A runaway per-minute trigger hits its per-day token ceiling, pauses into needs-input with a notification, and spends nothing further that day; resuming it is one click.
2. `personalclaw incident on` (or the API) stops every unattended fire — cron, hook, event trigger, autonudge, heartbeat, inbox AI — within one poll interval; interactive chat still works; resume requires explicit confirmation; the window is SEL-audited.
3. A denylisted path (`~/.ssh/**`, `**/.env*`) is refused with a `blocked` ActionResult by every action provider **including the app-contributed webhook-action**, with the matched rule in the SEL.
4. The full test suite runs with `DISABLE_LIVE_WRITES` auto-set; a deliberately destructive test cannot delete a real downloaded local model or send a real external message (the L6-model bug class is structurally closed).
5. With one provider down, the breaker opens after N failures; background calls against it fail in <1ms, fall back to the next bound ref with `degraded: true`, and the Settings health panel shows OPEN with latency percentiles — no stacked 30s hangs overnight.
6. `one_shot_completion(prompt, output_type=SomeModel)` returns typed data on both an ollama-bound model (native json-schema `format`) and an API model (schema-re-presenting targeted retry); migrated call sites have zero silent `None` degrades.
7. An unattended trigger-fired run resolves through the `headless` profile by construction (verified read-only default; a write requires a creation-time grant visible on the trigger).
8. A prompt-injection-shaped payload is blocked at the scan stage, classified `injection_blocked`, and is never auto-retried.
