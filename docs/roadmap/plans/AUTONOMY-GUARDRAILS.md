# AUTONOMY-GUARDRAILS

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/AG.md`](../atomic/AG.md) as 13 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Autonomy Guardrails — Safety Floor + Model-Call Chokepoint

**Status:** IN PROGRESS — Sessions 1-4 landed 2026-07-25 (see `## Execution log`): the §2 model-call
chokepoint, §1.1 budgets, §2.2 scan, §1.2 denylist, §1.3 incident kill switch, §1.4
DISABLE_LIVE_WRITES and §2.5 provider health are all wired to real callers and surfaced in
Settings → Guardrails.
**REMAINING:** the rev-13 amendment's S5-S6 earned-autonomy rung ladder is not started (no
`guardrails/autonomy.py`, no `resolve_rung`, no `autonomy_rungs.json`).
🔴 **Also open, found by audit 2026-08-04: §3's `SafetyProfile` family and §4.2's
`egress_policy_for_tier`/`REGISTRY` shipped with ZERO non-test callers** — repo-wide, only
`guardrails/policy.py`, `net/policy.py` and their own tests reference them. So Success Criterion #7
("an unattended trigger-fired run resolves through `headless` by construction") holds only in
`tests/test_guardrails_profiles.py`: no dispatch or spawn seam consults a profile, and
`SafetyProfile.tool_grants`/`denylist_extra`/`egress_tier` have no reader. Wiring
`profile_for_session` into the three dispatch seams + spawn belongs to S5.2. A handful of further
seams are deferred to their consumers (apps repo / AUTOMATION-SUBSTRATE / WORKFLOWS-V2), each logged.
📐 **Before executing S5.2, read [PLATFORM-HARDENING-FLOORS](PLATFORM-HARDENING-FLOORS.md) §5.**
It supplies the layering design for that wiring session so a second scheme isn't invented: a
boot-loaded **`Ceiling` the running agent cannot weaken** + today's `SafetyProfile` as the
narrow-only inner level, resolved by one rule (**tightest wins**, effective = `ceiling ∩ profile`),
with the evaluator dispatching on one of four **archetypes** — never on a scope *name*, which is
what keeps adding a scope data rather than engine code. It also carries the path-matcher rule as a
required test: normalize only the queried item, **never** `normpath` a pattern (`/a/**/../b` →
`/a/b` silently drops the `**` and widens an allow) — the wired-but-wrong-controls class.
(created 2026-07-12 from research synthesis)

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
- **Enforcement placement (reality-corrected):** action providers are pluggable — apps deliver them (`apps/webhook-action` precedent), so enforcement CANNOT rely on provider cooperation. `check_action` is called at the **three dispatch seams** every action-provider execution passes through: `hooks.py` (script hooks), `gateway.py` (scheduled jobs), `event_triggers.py` (memory-event triggers) — an app-contributed provider inherits the denylist without knowing it exists. A blocked action returns `ActionResult(blocked=True)` with the matched rule, and logs to the SEL (`sel.py`), same as egress blocks.
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
- **Action providers:** unchanged contract (`action_providers/base.py:ActionProvider`); the denylist is enforced at the three dispatch seams (§1.2), so app-contributed providers inherit it. Any NEW action provider still MUST be added to `ALLOWED_HOOK_PROVIDERS` (`src/personalclaw/validation.py`) or hook create/update rejects it — this plan adds no new action providers, but the rule is restated because the substrate is where future ones will be born.
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

---

## Execution log

### 2026-07-25 — Session 1 (§2 chokepoint core) — DONE

The Wave-0 win: the model-call seam, with breaker + hard timeout + attempt audit +
typed output. Interactive chat stream untouched (out of scope v1, per §2.1). Clean
break under the pre-1.0 banner (class B — additive on-disk state only; CHANGELOG
advises `personalclaw snapshot`). Full gate green: `make lint` (black/isort/flake8/
mypy) + `make test` (**7820 passed, 28 skipped, 13 xfailed, 0 failures**). No `web/`
changes this slice.

Shipped:

- **`src/personalclaw/guardrails/`** — new package (the LLM twin of `net/`).
  - `failure.py` — the `FailureMode` taxonomy (`none|schema_violation|constraint_violation|
    injection_blocked|token_overflow|timeout|circuit_open|provider_error`), `NON_RETRYABLE`
    (injection + circuit_open never retried), per-mode `correction_note`, and the typed
    errors `ModelCallTimeout` / `CircuitOpenError` / `OutputContractError` (all under a
    `GuardError` base carrying `.mode`).
  - `breaker.py` — the per-provider three-state FSM (`CLOSED→OPEN→HALF_OPEN→CLOSED`),
    time-derived (no background task; OPEN→HALF_OPEN promotes on read, matching the
    gateway's polling habit). Process-global registry keyed by provider name; `reset_breakers()`
    wired into an autouse conftest fixture (the SEL-singleton isolation discipline).
    In-process state is deliberate for a single-user gateway (§6: "restart resets").
  - `audit.py` — `AttemptRecord` + append-then-trim-at-2×-cap JSONL writer
    (`~/.personalclaw/model_calls.jsonl`, the `notifications.jsonl` pattern), best-effort
    (never breaks a call), plus `read_recent()` for the future health view (§2.5).
  - `model_call.py` — `ModelCallGuard(ModelProvider)`: a faithful transparent proxy
    (every ABC method delegates; `__getattr__` fallback covers provider-specific extras
    like `embed`) that intercepts only `stream`/`complete`/`stream_command` with the
    pipeline `breaker check → hard timeout (asyncio.wait_for per-chunk against a cumulative
    deadline) → attempt audit`. Cooperative cancellation (`CancelledError`/`GeneratorExit`)
    does NOT trip the breaker. `wrap_model_call_guard(...)` is idempotent.
- **`providers/provider_bridge.py`** — the wrap seam. `resolve_provider_for_use_case`
  sets a private `_guard_use_case` sentinel **only for `use_case == "reasoning"`** (the
  sole non-interactive text axis: `one_shot_completion` collapses `background`/`ingestion`→
  `reasoning`; loop judges/gates + web-extract pass `reasoning`). The sentinel threads
  through `**kwargs` to the single build point (`_resolve_from_config_registry`), which
  pops it (never leaks to the factory) and wraps the built provider. Excludes by
  construction: the interactive `NativeAgentRuntime` (returns before the build point for
  `chat`/`code_tools`), its inner model (`_force_model_axis`), and ACP CLIs.
- **`llm_helpers.py`** — `one_shot_completion` gains `output_type: type | None`. Typed
  path: parse via `_parse_llm`; on a miss, ONE targeted correction-note retry; still a
  miss → `OutputContractError` (replaces the silent `None` degrade). Raw text unchanged
  when `output_type` is `None`.
- **`llm/capabilities.py`** — new graded `StructuredOutput` enum (`none|json_mode|
  json_schema`) as a `ProviderCapability.structured_output` field (default `NONE`) — a
  descriptor a *graded* value needs, not a `Capability` flag. See deferral below.
- **Migrated silent-`None` call sites** — `web/fetch.py:web_extract` (now `output_type=dict`,
  catches `OutputContractError` for its distinct parse-miss message) and
  `inbox_service.py:classify` (typed; catches `OutputContractError` and still safe-defaults
  to needs_reply/needs_review — the human-review fallback is preserved, never a silent drop).
- **Judge bounded-reasoning field** — `eval/judge.py:JudgeVerdict` and
  `loop/judge.py:CycleVerdict` gain an optional `reasoning` field parsed BEFORE the verdict
  fields; the four bundled judge prompts (`task-eval_judge`, `task-cycle_judge`,
  `task-cycle_judge_skeptic`) now ask for reasoning first so a structured-output constraint
  doesn't suppress chain-of-thought. `CycleVerdict.to_dict` surfaces it to the cockpit.
- **Tests** — `tests/test_guardrails_model_call.py` (breaker FSM incl. half-open probe
  outcomes, audit round-trip, guard timeout/breaker-trip/circuit-open-refusal integration,
  typed-output retry-then-raise / first-try / raw paths); updated the web-extract + inbox
  mocks to mirror the new typed contract (kept honest, not weakened).

**Success criteria met this session:** #5 (breaker opens, background calls fail fast,
audit records `circuit_open`) and #6's core (`output_type` typed data via targeted
retry; migrated sites have zero silent `None` degrades). The health *panel* (#5's UI)
and native json-schema `format` (#6's ollama half) land in Sessions 2/4 + the apps repo.

**DEVIATION — native `json_schema` enforcement + `structured_output` capability
DECLARATION deferred to a coordinated apps-repo change.** §2.4/§8 name "ollama +
OpenAI-wire branded apps" as declaring the capability, but every concrete provider
adapter (ollama-models, openai-models, bedrock-models, branded OpenAI-wire) lives in
the sibling `PersonalClawApps` repo — a separate git/commit boundary. Declaring the
capability there and shaping the native request (`format=` / `response_format`) is an
apps commit, not a core one. Core ships the complete substrate: the graded descriptor
(default `NONE`), the capability-dispatch *hook*, and the **universal**
parse-with-targeted-retry that is correct for every provider immediately. This keeps
this branch atomically clean (zero dead code — no core code references a capability no
core provider can declare) and matches the plan's own framing that "Session 1 alone is
a Wave-0 win." Follow-on: an apps-repo change sets `BrandedProviderSpec.structured_output`
+ ollama `format` wiring; then core's dispatch hook lights up natively.

**DEVIATION — no formal task table.** The plan is prose-only (§8 session breakdowns, no
`| ID | Task |` grid). Session-1 tasks were derived from §2 + §8 and executed in that
scope; recorded here rather than back-filling a grid the owner didn't author.

**DISCOVERY — pre-existing xdist flake in `test_subagent.py` (NOT caused by this slice;
NOT fixed here — out of scope).** During the definition-of-done gate, two tests —
`TestSpawnWithApprovalCallback::test_rejected_spawn_logs_sel_rejection` and
`TestSubagentReaper::test_reaper_kills_expired_subagent` — fail intermittently
(`AssertionError: 'log_tool_invocation' ... Called 0 times`) only inside the full
7860-test `--dist worksteal` run, never in isolation (64/64) or small subsets.
**Causation established against clean `main` with my work stashed: the SAME two tests
flake at a comparable rate (main: pass/pass/fail over 3 runs; branch: pass/fail/pass/fail)
— so this is pre-existing and independent of the model-call chokepoint.** Mechanism: both
tests `patch("personalclaw.subagent.sel")` and assert the audit call, but the SEL log in
`_force_reap` (subagent.py:684) and `_spawn_with_approval` (subagent.py:1173) sits behind
a swallowing `try/except Exception`; under 18-worker contention against the shared
`PERSONALCLAW_HOME`, `sel()` construction / prior best-effort I/O (`_write_tombstone`,
session reset) can raise and be swallowed, so the patched mock is never called. This
contradicts the pyproject claim that the suite is "deterministic under worksteal with no
reruns" (the #8/#10 SEL/lock fixtures didn't fully close it for these two async tests).
**Left for a separate `bugfix-` branch** (owner-maintained roadmap; not this task's
scope): give these two tests their own isolated home + assert the SEL call without the
swallow masking it, or narrow the `except` in the two subagent paths. Not masked with a
rerun; recorded honestly. My slice's own tests + full gate are green on the passing runs.

### 2026-07-25 — Session 2 (§1.1 budgets + §2.2 scan + config substrate) — DONE

Money, meters, and the outbound scan. Clean break under the pre-1.0 banner (class B —
additive on-disk state `spend.json` only; CHANGELOG advises `personalclaw snapshot`).
Full gate green: `make lint` (black/isort/flake8/mypy) + `make test` (**7838 passed**,
+18 new; the only 2 reds are the pre-existing subagent SEL xdist flake from Session 1,
reproduced on clean `main`, unrelated). No `web/` changes this slice.

Shipped:

- **`config/loader.py`** — new `GuardrailsConfig` (beside `SecurityConfig`) with nested
  `BudgetConfig` (`max_tokens_per_run`, `max_tokens_per_day`, `max_dollars_per_day`) +
  `BreakerConfig` (`failure_threshold`, `recovery_secs`) + `scan_mode`
  (`warn`|`redact`|`block`). Wired through all FOUR points (§7): dataclass + `_meta`,
  `AppConfig.load()` field-by-field mapping, `to_dict()` new top-level `guardrails`
  section, and `_EDITABLE_CONFIG` PATCH allowlist (6 runtime-editable scalars; the PATCH
  handler already supports the 3-part `guardrails.budgets.*` dotted paths). Defaults are
  **unlimited budget + redact** so no existing install's behavior changes until a ceiling
  is set. `test_config_roundtrip.py` `_SECTIONS` + `_SPECIAL` extended; leaf-walker green.
- **`guardrails/budgets.py`** — `Budget` (zero = unlimited per dimension), `BudgetVerdict`
  (ok/warn>80%/exceeded), `SpendMeter.charge` folding per-attempt spend into a persisted
  **day** scope (`spend.json`, atomic_write, pruned >30d, thread-locked) + an in-memory
  **run** scope. `check_day`/`check_run` verdict against a budget. Dollar estimates reuse
  `pricing.estimate_cost` (provider-reported `cost_usd` preferred). Process-global
  `get_meter()` + `reset_meter()` (autouse conftest fixture, breaker/SEL discipline);
  `budget_from_config()`/`run_budget_from_config()` fail-open to unlimited.
- **`guardrails/scan.py`** — `scan_outbound(text, mode)` composing `security.redact_*`
  (credentials + exfil URLs) with a small PII pass (email/phone). Mode ladder: warn (log +
  original), redact (substitute + send), block (refuse). Unknown mode → warn (never a
  silent hard block).
- **`guardrails/model_call.py`** — the guard gained: a **day-budget pre-check** before the
  call (refuses with `BudgetExceededError` when the ceiling is hit — the mid-run §1.1
  pause), **spend metering** of every successful call (`meter.charge` + real `dollars_est`
  in the audit row via `_estimate_dollars`), and an **outbound `_prescan`** on
  `stream`/`stream_command` (block → `SecretLeakBlocked` + SEL audit + `secret_leak` row).
  `wrap_model_call_guard` forces `warn` for local providers (`_is_local_provider`:
  loopback base_url / ollama). `FailureMode` gained `SECRET_LEAK` + `BUDGET_EXCEEDED`
  (both non-retryable) + their errors.
- **`providers/provider_bridge.py`** — the wrap seam now reads `GuardrailsConfig` (fail-
  open) and threads the config-tuned breaker + day-budget + scan_mode into the guard.
- **`gateway.py`** — `_day_budget_exceeded(context)` helper beside `_maybe_autopause`:
  a pre-dispatch gate for the cron **agent** path (skips the fire + one-shot needs-input
  notification, re-arms once back under budget → auto-resume next day). Deterministic
  action jobs (bash/run-script — no LLM spend) are unaffected.
- **`subagent.py`** — `SubagentManager.spawn` refuses when the day budget is exhausted
  (mirrors the existing low-memory refusal: SEL log + done `SubagentInfo` with a clear
  error), before consuming a session or approval.
- **Tests** — `tests/test_guardrails_budgets.py` (18: meter scopes, verdicts, scan ladder,
  guard budget/scan integration, local-forced-warn, the gateway day-budget gate incl.
  notification de-dup/re-arm) + a subagent budget-refusal test. conftest `reset_meter`
  autouse fixture added.

**Success criteria met this session:** #1 (a per-minute trigger hits its per-day token
ceiling, pauses into needs-input, spends nothing further that day — auto-resumes next day
rather than a manual one-click, which is stronger) and #8's scan half (a secret-shaped
payload is blocked at the scan stage, classified `secret_leak`, never retried).

**DEVIATION — run-scope budget enforcement + per-trigger budget FIELDS deferred to
AUTOMATION-SUBSTRATE.** §1.1 lists a `run` scope and notes per-trigger budgets "become
fields on `Trigger.gates` when AUTOMATION-SUBSTRATE lands." The `SpendMeter` fully
supports run scope (built + unit-tested), but wiring a run-key through dispatch and adding
per-trigger budget fields would build a seam against the `Trigger.gates` contract that
plan hasn't defined yet — exactly the "no seam against an unbuilt contract" case the owner
amendment forbids. So Session 2 enforces the **day** scope (the real cost guardrail) at
the chokepoint + cron/subagent dispatch, globally from `GuardrailsConfig.budgets`; the run
scope activates when its consumer exists.

**Remaining for later sessions:** §1.2 denylist + `check_action` at the 3 dispatch seams,
§1.3 incident kill switch, §1.4 DISABLE_LIVE_WRITES, §5 `guard_flag` (Session 3); §3
SafetyProfiles, §2.5 provider health FE panel + the budget/scan Settings controls, §4
egress tiers / trust gate (Session 4).

### 2026-07-25 — Session 3 (§1.2 denylist + §1.3 incident + §1.4 DISABLE_LIVE_WRITES + §5) — DONE

The safety floor. Clean break under the pre-1.0 banner (additive config + an
`incident.json` flag file; CHANGELOG advises `personalclaw snapshot`). Full gate green:
`make lint` (black/isort/flake8/mypy) + `make test` (**7876 passed**, +~40 new; 0 reds —
the pre-existing subagent SEL flake did not fire this run). No `web/` changes this slice
(FE incident banner + Settings controls belong to Session 4's surface work).

Shipped:

- **`guardrails/flags.py`** — `guard_flag(value)` fail-safe parser (missing/null/unknown/
  empty → ENABLED; only explicit `0`/`false`/`no`/`off`/`disable[d]`/`n`/`f` → disabled).
- **`guardrails/denylist.py`** — `DenyRule` + `DenyDecision` + `check_action` composing the
  always-on built-ins (`is_sensitive_path`, `BUILTIN_DENIED_COMMAND_PATTERNS`) with the new
  `security.autonomy_denylist` config globs; `enforce_action` adds SEL audit + a
  needs-input notification for `needs_human`. Wired at all THREE dispatch seams:
  `hooks.run_script_hook`, `gateway._run_action_job`, `event_triggers._fire` — each checks
  BEFORE `provider.execute`, so an app-contributed provider inherits it. `security.autonomy_denylist`
  added to `SecurityConfig` (load + to_dict + roundtrip `_SPECIAL`).
- **`guardrails/incident.py`** — `incident.json` (`{active, reason, started_at}`) + an
  in-process mirror refreshed by file mtime (a CLI flip is seen by the running gateway
  without a restart). `incident_active()` checked at the cron callback, both dispatch
  seams, and subagent spawn — **interactive chat untouched**. `activate`/`resume` SEL-audited.
  Endpoints `GET|POST /api/incident` + `POST /api/incident/resume {confirm}`; CLI
  `personalclaw incident on|off|status` (operates on the flag file, works with or without a
  running gateway). NOTE: incident read is deliberately NOT fail-safe-on (a missing/unreadable
  file = no incident) — a kill switch must not halt all automation on a transient read miss.
- **`guardrails/writes.py`** — `live_writes_disabled()` (reads `PERSONALCLAW_DISABLE_LIVE_WRITES`
  via `guard_flag`; absent = allowed, since it's an opt-in ops/test toggle, not a default-on
  guard) + `LiveWriteDisabled` typed refusal. Honored in core at `net.fetch` (non-GET/HEAD to
  a non-loopback host) and `local_models.registry.delete_model`. **Auto-set in conftest** for
  the whole suite; a test that genuinely writes opts out explicitly (`monkeypatch.delenv`).
- **`config/loader.py`** — `guardrails.scan_mode` tagged `guard_class=True` + `safe_values`;
  the §5 schema test walks all dataclasses and fails the build if any guard-class field
  defaults unsafe.
- **`sdk` facade** — the guardrails package `__init__` re-exports `check_action`, `guard_flag`,
  `scan_outbound`, `incident_active`, `live_writes_disabled` (the `sdk.net`/`sdk.security`
  precedent) so a contributed app can pre-check.
- **Tests** — `tests/test_guardrails_flags.py` (guard_flag table + the safe-default schema
  walker) + `tests/test_guardrails_floor.py` (denylist paths/commands/needs_human, incident
  activate/resume/mtime-pickup, DISABLE_LIVE_WRITES env + net.fetch write-refusal +
  loopback/GET exemptions). Updated `test_denied_commands` (new `autonomy_denylist: []`) and
  regenerated the offline agent reference (the 3 new `/api/incident` routes).

**Success criteria met this session:** #2 (incident stops every unattended fire within one
poll interval, interactive chat still works, explicit-confirm resume, SEL-audited), #3 (a
denylisted `~/.ssh/**`/`**/.env*` path is refused by every action provider incl. the
app-contributed webhook-action, matched rule in the SEL), #4 (suite runs with
DISABLE_LIVE_WRITES auto-set; a destructive test cannot delete a real model — structurally
closed).

**DEVIATION — channel-transport `send()` DISABLE_LIVE_WRITES honor-point deferred.** §1.4
lists channel `send()` toward non-loopback transports as an honor-point, but channel
transports live in the **apps** repo (slack-channel etc.) — a separate commit boundary. Core
honors the flag at the two core-owned live-write points (`net.fetch` non-GET, local-model
delete); the channel-app honor-point is a coordinated apps-repo change (the app's `send()`
calls `sdk.guardrails.live_writes_disabled()` before transmitting). Recorded, not silently
dropped.

**Remaining for Session 4:** §3 SafetyProfiles + `safety_profile_for` + headless-by-
construction; §2.5 provider-health FE panel; the budget/scan/incident Settings + banner FE;
§4 egress tiers + Trust/Preview project gate; the as-a-user validation sweep.

### 2026-07-25 — Session 4 (§3 profiles + §4.2 egress tiers + §2.5 health + §4.4 FE) — DONE

Profiles and surfaces — the safety floor gets its cockpit. Clean break under the pre-1.0
banner (additive). Full gate green: `make lint` (black/isort/flake8/mypy, 481 files) +
`make test` (**7898 passed**, +22 new, 0 reds) + the full web gate (typecheck ✓, **231
vitest** ✓, build ✓, render-smoke ✓ all 5 routes incl. `#/settings`).

Shipped:

- **`guardrails/policy.py`** — the frozen `SafetyProfile` (approval, tool_grants,
  tool_allowlist, egress_tier, denylist_extra, budget, scan_mode) + the six named profiles
  (INTERACTIVE/CODING/REVIEW_ONLY/CLEANUP/INCIDENT/HEADLESS) + `safety_profile_for`
  (operator-config layering) + `get_profile` (fails CLOSED to HEADLESS for an unknown name).
  Modeled line-for-line on `net/policy.py`. **Headless-by-construction:**
  `is_unattended_session` / `profile_for_session` classify a run off its session key
  (`_STATELESS_PREFIXES` + `loop-*`) → HEADLESS (read-only) for unattended, INTERACTIVE for
  chat. INCIDENT forces `scan_mode=block` even under operator config.
- **`net/policy.py`** — the `REGISTRY` egress profile (curated ~22-host dev-registry preset:
  pypi/npm/crates/docker/ghcr/github/maven/rubygems/go/…) + `egress_policy_for_tier`
  (`off`→None, `registry`→REGISTRY, `listed`/`all`/unknown→STRICT).
- **`guardrails/health.py`** — `provider_health()`: per-provider breaker state +
  consecutive failures + pass rate + p50/p90/p99 latency (nearest-rank) + failure-mode
  distribution + degraded flag, derived from `model_calls.jsonl` + the breaker registry.
  A provider with an OPEN breaker but no audit rows still appears, and vice-versa.
- **`GET /api/models/health`** (`dashboard/handlers/core.py` + route + `__init__` export),
  run off the event loop via `asyncio.to_thread`. Offline agent reference regenerated.
- **FE** — `web/src/pages/settings/GuardrailsPanel.tsx` (incident toggle + budgets + scan
  mode + breaker + the derived provider-health list), registered as **Settings →
  Guardrails**; `web/src/app/IncidentBanner.tsx` mounted in the app shell (spans the content
  area on every page while active, inline Resume, 15s visible-poll). API client gained
  `incident`/`incidentOn`/`incidentResume`/`modelsHealth` + the `ProviderHealth` type. Built
  on the shared **`NumberField`** + **`Button`** primitives (the primitive-adoption ratchet
  correctly caught my initial hand-rolled `<input>`/`<button>` and I migrated to the
  primitives rather than bumping the baseline — design-system doctrine).

**Success criteria met this session:** #5's health-panel half (breaker OPEN + latency
percentiles surfaced in Settings) and #7 (an unattended trigger-fired run resolves through
`headless` by construction — verified read-only default; a write is a creation-time grant).

**DEVIATION — §4.1 research subagent class + §4.3 Trust/Preview project gate deferred.**
§4.1's per-class enforcement is "by the tool-approval layer" — a write-tool-gating BEHAVIOR
change in the subagent runtime that the plan ties to the engine's per-template profiles;
the `SafetyProfile.tool_grants` field it will consume ships now, but wiring default-deny-
write into the runtime waits for that consumer (building it now would be a seam against an
unbuilt contract). §4.3 needs the project-script-execution seam (a project `<cwd>/loop.md`
picked up by run-prompt / a Code-loop deliverable gate) — that path is WORKFLOWS-V2
territory, not built. Both recorded, not silently dropped.

**DEVIATION — the ad-hoc cron approval pick was NOT rewired to `profile_for_session`.**
§3 says the profile "becomes the single object that decides approval." The resolver +
classifier ship and are tested, but the gateway's existing
`AUTO_APPROVE if approval_mode=="auto" else HOOK_BASED` branch is LEFT in place: rewiring
live approval resolution is a behavior change best made when its consumers (the per-template
profiles) exist, and changing it now risks altering today's unattended-run approval with no
new capability to show for it. `profile_for_session` is the ready seam; the swap is a
one-line change when the engine lands.

### 2026-08-12 — Atom AG-6 / Session 5.1 (§5 rung-ladder core) — DONE

`src/personalclaw/guardrails/autonomy.py` ships the earned-autonomy ladder core: `RUNGS`
(`draft_only → one_tap → auto_with_undo → autonomous`), `ActionTypeSpec` + `PromotionRule`,
`register_action_type` / `action_type` / `registered_action_types` / `reset_action_types`,
`resolve_rung` (floor + accepted grant, clamped to ceiling, then clamped to `one_tap` while
`incident_active()`), `granted_rung` (the same without the incident clamp, so the panel can
say "granted auto-with-undo, held at one-tap by the incident"), `promotion_eligibility`
(DERIVED over SEL approval verdicts + FEEDBACK-SIGNAL 👎), `grant_rung` (the user's click —
the only upward path) and `demote` (immediate, cooldown-starting). The store is
`~/.personalclaw/autonomy_rungs.json` via `atomic_write`, holding grants and demotions ONLY,
declared in the durability inventory (`autonomy_rungs`, `DOMAIN_CONFIG`, `lww`) and carried by
the snapshot `config` component. `guardrails.autonomy` wires through its four points:
`AutonomyConfig` dataclass + `_meta`, `load()`'s field-by-field mapping (clamped via
`_safe_int`), `to_dict()` (via the existing `asdict(self.guardrails)`), and five
`_EDITABLE_CONFIG` PATCH entries. Full gate: `make lint` clean (black/isort/flake8/mypy),
`pytest tests/` **18701 passed, 30 skipped, 12 xfailed**, plus the 3 pre-existing
`test_harness_validate.py` failures that red in any worktree (the harness resolves
`.venv/bin/python` relative to cwd). `config-baseline.json` regenerated in the same commit;
`inert-surface-baseline.json`, the manifest reference and the dag derived block all
byte-identical. No `web/` changes (the FE ladder panel is S6.1 / AG-8).

**DISCOVERY — the SEL has no `tool_approved`/`tool_rejected` operation type.** The plan (and
this section above) names them, but `sel.py` mentions those strings only in its module
docstring: the verdict actually lives in `SecurityEvent.outcome` (`approved`,
`auto_approved`, `rejected`, `rejected_*`, `denied`, `not_auto_approved`) while `operation`
holds the tool name. Eligibility therefore reads `outcome`, and attributes an event to a type
by the `action_type` **metadata** key (`SEL_ACTION_TYPE_KEY`) that the S5.2 seams stamp, or by
an `operation` that IS the type key. `metadata` already flows through
`log_tool_invocation`, so S5.2 adds one dict entry rather than an event stream.

**DECISION (security) — `auto_approved` outcomes are NOT evidence.** Counting them would let
a type that already runs unattended manufacture its own promotion case and climb the rest of
the ladder on its own output. Only a human `approved` verdict counts; `auto_approved` /
`auto_approved_spawn` are excluded, with a named test for the hole.

**DEVIATION — per-type thresholds live on `ActionTypeSpec.promotion`, not in a per-type config
dict.** §Contract-level design says "thresholds per-type configurable via a new
`guardrails.autonomy` config subsection", while its own code block puts `promotion:
PromotionRule` on the spec. Both are honored by making `guardrails.autonomy` the operator-wide
default (five scalars, all PATCH-editable and bounded) and letting a type that declares its own
rule keep it. A per-type config dict keyed on an unbounded namespace (`app:<name>.<action>`)
cannot be validated by the `_EDITABLE_CONFIG` allowlist and would need a bespoke value type;
the spec is where a type is declared, so it is where a deliberate per-type bar belongs.

**DEVIATION — no `guardrails.autonomy.enabled` toggle.** Considered and dropped: the ladder
cannot grant anything without a user click, so an on/off switch would not be a safety control,
and it would have shipped as a guard-class-looking field whose safe default is arguable.
Suspending earned autonomy is what `personalclaw incident on` already does, now including this
ladder.

**Deliberately left to AG-7 (S5.2) and AG-8 (S6.1):** no action-type keys are registered (the
S5.1 row does not declare them), no dispatch seam calls `resolve_rung`, no manifest `autonomy`
block, and no FE. `leaves_machine` is nonetheless load-bearing here rather than decorative:
`promotion_eligibility` never *proposes* `autonomous` for a type that leaves the machine,
however permissive its ceiling — that rung has to be an explicit owner grant.

### 2026-08-12 — Atom AG-7 / Session 5.2 (declarations + rung routing at the seams) — DONE

AG-6 shipped the ladder with **no call sites**. This closes that: `resolve_rung` now decides
whether a real hook fire, a real data-event fire and a real store-trigger fire execute.

`src/personalclaw/guardrails/rungs.py` is the seam-facing half (autonomy.py stays the decision
layer): route constants + `RungRoute`, the `CORE_ACTION_TYPES` declaration table with
`ensure_core_action_types()`, `route_action_type` / `route_provider_action`,
`announce_withheld` (the durable row a withheld action leaves) and `record_reversal` (the
`auto_with_undo` handle + passive notify). `autonomy.py` gains `ActionTypeSpec.providers` and
the `_PROVIDER_INDEX` it feeds, `action_type_for_provider`, `unregister_action_type`, and
`clamp_untrusted_ceiling`. `guardrails/policy.py` gains `rung_ceiling_for_profile`, giving
`SafetyProfile.approval` its second production reader.

**How a declaration reaches a seam with no per-action branching.** A seam holds a provider
NAME and nothing else, so the name→type mapping lives ON the declaration
(`ActionTypeSpec.providers`), indexed once at registration. Every seam is the same two calls;
`test_the_seams_carry_no_per_action_branch` asserts no seam source mentions an action-type key
at all. Registering an app's action provider and DECLARING its rung bounds happen in one place
(`ActionTypeHandler.register` → `app:<app>.<action>`), and disabling the app drops both — a
declaration outliving its provider would let a later app inherit its earned rung.

**Rung → behaviour at a seam:** `draft_only` withholds and files a PROPOSAL row; `one_tap`
withholds and files an AGENT-REQUEST row; `auto_with_undo` executes, records the provider's
`ActionResult.reversal` handle (SEL + the notification `meta`) and passively notifies;
`autonomous` executes. Both withhold routes dedupe per (type, trigger/hook) so a
thirty-second trigger cannot stack a hundred rows. `create_task_provider` populates
`ActionResult.reversal` (`task:<provider>:<id>`), so the new field has a production writer
rather than being an unwritten key.

**DEVIATION — the plan's third seam is `_fire_store_trigger`, not `_run_action_job`.** §5's
"three dispatch seams" names `gateway._run_action_job`, which retired with `ScheduleService`
(S112). Only TWO `enforce_action` call sites exist (`hooks.py`, `event_triggers.py`) — but the
comment left at the retired method's old site says the substrate GENERALIZED it: "action
dispatch is `_fire_store_trigger`". That method executes providers for every clock, file,
webhook and chained trigger and is already listed as an execution site by
`test_action_provider_chokepoints`. Routing two of three seams would honour a declared floor
at two dispatch points and ignore it at the busiest one, which reads to a user exactly like
not honouring it at all — so the third seam is wired under its current name.
`dashboard/handlers/triggers._dispatch_store_action` (the manual Run button) is deliberately
NOT routed: a user pressing Run *is* the approval a rung withholds for.

**DEVIATION — `one_tap` raises a durable row, not `ApprovalGate.request`.** `ApprovalGate` is
a per-native-session in-process `asyncio.Future` resolved by the chat runner's approve/reject
plumbing; no unattended seam has one, so awaiting it would park the seam for 300s and then
fail closed to REJECT. The honest, complete-in-itself behaviour is: withhold, and leave a
standing attention row carrying `action_type`, `rung` and the trigger/hook id. AG-8 renders
that row as the one-tap card and supplies the approval verdict that becomes the type's SEL
evidence — until then `inbox.reply_draft` cannot climb, which is why no `one_tap` branch was
built at the affordance (a branch nothing can reach is the dead code this project forbids).

**DECISION — core declarations state today's rung, and buy the ceiling.** Every built-in
action provider already runs unattended, licensed by the creation-time grant plus decision-7's
capability fence; the ladder was added ON TOP of that floor. So each core spec declares
`floor = ceiling = autonomous` with its honest `leaves_machine`, and a machine-leaving core
action (`bash`/`run-script`, `send-message`, `call-app-route`, the spawn providers) is making
the deliberate in-tree ceiling raise §5 asks for. Declaring them lower would not harden
anything — it would stop a user's existing automations and grow a notification per fire.
`enforcing a dead control is an outage` is the recorded lesson. What the declarations do buy:
`promotion_eligibility` can never propose `autonomous` for them, the ladder panel gets its
inventory, and an app's declaration is bounded by the same vocabulary.

**DECISION (security) — an app cannot declare `leaves_machine`, and its ceiling is clamped
LOUDLY.** Core derives `leaves_machine` from the app's own `permissions.network`: an app that
could self-certify "my effect stays here" would be self-certifying its way to the top of the
ladder. A manifest asking for `ceiling: autonomous` on a network-reaching action is clamped to
`auto_with_undo` with a `logger.warning` AND a `guardrails.autonomy_ceiling_clamped` SEL row
naming both the declared and the granted ceiling — a silent downgrade is a recorded finding in
this tree (`_validate_agent`), and it would leave the manifest claiming a rung it never had.

**DECISION — an UNDECLARED provider keeps its pre-ladder behaviour** (`governed=False`,
route `execute`). Fail-closed applies to a *declared* key with no registration and to a grant
that cannot be proven — both resolve `draft_only`. Treating every undeclared provider as
withheld would stop every hook and trigger in the tree.

**DECISION — the profile may only NARROW** (PLATFORM-HARDENING-FLOORS §5, tightest wins).
`rung_ceiling_for_profile` reads `profile.approval`: `hook_based` (the unattended posture)
caps at `auto_with_undo`, because `autonomous` means an action ran silently with no handle and
nothing a user would notice. `ask`/`auto` add no bound. The incident clamp in `resolve_rung`
outranks both. SH5.1's `Ceiling` object and SH5.2's path matcher stay plan 67's rows.

**DISCOVERY — `net/policy.egress_policy_for_tier` still has no production caller**, so
`SafetyProfile.egress_tier` never reaches real egress. Untouched: that is SH5.3's scope, and
this atom's `done_when` says nothing about egress. Recorded so it is not re-found as new.

**Collateral fix — `_record_refused_fire`.** The rung refusal needs a ledger row for the same
criterion-8 reason a screened payload does, so `gateway._record_blocked_fire` was generalized
into one refusal recorder. That first made the write's status uninferable and reddened
`test_triggers_status_vocabulary` — root-caused rather than exempted, by pinning the status to
a module-level `_REFUSAL_STATUSES` tuple with an early-return guard, which both restores the
rail's inference and refuses a status the projection table cannot map. That writer's
`min_values` floor was RAISED 3 → 4.

Full gate: `make lint` clean (black/isort/flake8/mypy), `pytest tests/` **18793 passed, 30
skipped, 12 xfailed**, plus the 3 pre-existing `test_harness_validate.py` failures that red in
any worktree. 29 new tests in `tests/test_guardrails_rung_routing.py`, every behavioural one
driven through a production fire path. `inert-surface-baseline.json`, `config-baseline.json`,
the manifest reference and the dag derived block all regenerate byte-identical (the inert
baseline tracks enum / sdk-export / config surfaces, not call sites, so wiring a resolver is
invisible to it). No `web/` changes — the rung chip, ladder panel and undo click are AG-8.

### 2026-08-12 — Atom AG-8 / Session 6.1 (§6.1 promotion proposals + the ladder's user surface) — DONE

AG-6 built the decision layer, AG-7 wired it into all three dispatch seams, and both left the
ladder **invisible**: no HTTP surface, `promotion_eligibility` with zero production callers, and
`ActionResult.reversal` written by `create-task` with **nothing anywhere able to reverse a
handle**. This atom closes all three and is the LAST atom of the rung-ladder track (§5-§6).

`src/personalclaw/guardrails/ladder.py` is the user-facing half — the third module beside
`autonomy.py` (decides) and `rungs.py` (routes). It holds three things:

* **The reversal record store** (`autonomy_reversals.json`, `atomic_write`, a 50-record ring,
  declared in the durability inventory as `autonomy_reversals`/`DOMAIN_CONFIG`/`lww`). Written by
  `rungs.record_reversal`, which every `auto_with_undo` execution already goes through, so the
  record has a production writer at all three seams rather than only in a test.
* **The undo executor** (`reverse_action`). Takes a RECORD ID, never a handle: the handle comes
  out of our own persisted state, so a request can only ask to undo something this machine
  actually did and told the user about. Dispatch is `ActionProvider.reverse` on a provider the
  RECORDED action type's own declaration claims (`ActionTypeSpec.providers`) and that claims the
  handle's kind (the new `ActionProvider.reversal_kinds`) — the handle stays opaque to core, and
  it never reaches a path, a shell or a query. On success: mark reversed, then `demote`. On ANY
  refusal: named code, SEL row, effect untouched, **no demotion**.
* **The promotion proposal** (`propose_promotions`) plus `ladder_view()`, the panel's inventory —
  `promotion_eligibility`'s two production callers.

`GET /api/autonomy` (inventory + derived proposals, `asyncio.to_thread` because it reads the SEL
tail once per type) and `POST /api/autonomy/{grant,demote,undo}` in a new
`dashboard/handlers/autonomy.py`. The asymmetry is the design: only `grant` increases autonomy,
and the decision is `grant_rung`'s — the handler calls it, never re-implements its ceiling /
cooldown / no-op checks, and explains an already-final refusal afterwards via
`ladder.explain_refused_grant`. A client-supplied rung is an ASK. The grant's stored
`evidence_window` is the SERVER's recomputed record, never text from the body.

FE (`web/`): `ui/RungChip.tsx` (+ its `.doc.ts`) and `lib/rungs.ts`, the Settings → Guardrails
**Earned autonomy** panel (per-type rung, the `authority` sentence, the derived record, demotion
history, Promote / Hand back) and its **undo list**, chips on every trigger row keyed on the
action-provider name, and an **Undo & stop doing this automatically** button on an
`auto_with_undo` notification — rendered from the persisted record's pending state, not from the
`reversal_id` sitting on the notification, so an already-undone action never offers a second undo.
Rung WORDING is server-owned (`rungs.RUNG_LABELS` / `RUNG_HINTS`, served as `rung_meta`) so a
chip, the panel and the proposal that offered a promotion cannot disagree.

**done_when 3, concretely.** The chip's answer is the rung in behaviour words plus its
provenance, and there are exactly three provenances: *"Runs at runs-on-its-own because that is
the rung it was declared with; it has never been promoted."* · *"You promoted this to runs-with-
undo on 2026-08-10 — 12 clean approvals over 9.0 days with 0 rejection(s)."* · *"Granted runs-on-
its-own, held at asks-first while the incident kill switch is active."* Composed once, in
`_authority_sentence`, because a chip tooltip and a panel row describing one authority differently
is the drift this project keeps finding.

**Proved as a user drives it** (`tests/test_guardrails_ladder.py`, 40 tests): a REAL
`execute_event_action` fire of an app-declared `draft_only` action is withheld and files a
proposal row; a grant through the REAL endpoint lets the SAME fire execute and file a REAL native
task; the undo endpoint deletes that task file and demotes the type; a third fire is withheld
again. Plus: the API refuses a grant above the declared ceiling and during a demotion cooldown;
five bogus record-id shapes never reach the store; eleven unparseable handles are refused at BOTH
ends; a provider that refuses (the task was already tidied away) leaves the rung ALONE; a second
undo says `already_reversed`; every refusal AND the success are SEL-audited.

Full gate: `make lint` clean (black/isort/flake8/mypy), `pytest tests/` **18834 passed, 30
skipped, 12 xfailed** plus the 3 pre-existing `test_harness_validate.py` failures that red in any
worktree; web gate green — `tsc --noEmit` clean, **1824 vitest tests in 191 files**, `npm run
build` clean. `inert-surface-baseline.json` and `config-baseline.json` regenerate byte-identical
(no new config field, no new enum surface); the manifest reference gained the four routes and the
dag derived block was regenerated for the atom flip. `docs/design/consistency-audit.json` moved
only its `filesScanned` count (446→449, the three new FE files) with `driftHits` unchanged at 7.

**DEVIATION — the one-tap card's execute-on-APPROVE replay is not built.** AG-7's log promised
AG-8 would "render that row as the one-tap card and supply the approval verdict". Half of that
shipped: the withheld row is a standard `agent_request` the inbox already renders, and the ladder
panel is where its type's rung is decided. The other half — re-EXECUTING a withheld action when
the user approves — needs the action config, context and dispatch identity persisted with the row
and replayed later, which is a durable action-replay mechanism owned by the withhold surface
(INBOX-NOTIF-UNIFICATION's agent-request contract), not by the ladder. Building it here would
have meant a second, ladder-private dispatch path for actions the seams already know how to run.
Recorded rather than half-built; `inbox.reply_draft` therefore still cannot climb from its
`draft_only` floor, which is the state AG-7 described and the reason no `one_tap` execute branch
exists at that affordance.

**DEVIATION — no `guardrails.autonomy` config field was added for the proposal cadence.** The
scan interval is a module constant (`_AUTONOMY_PROPOSAL_INTERVAL_SECS`, 6h) rather than a config
knob: a rung is earned over days, so the cadence is not a decision a user needs, and shipping a
PATCH-able field nobody would turn is the config surface this plan already declined once (the
dropped `autonomy.enabled` toggle).

**DECISION — the proposal scan rides the file-watch poll loop, self-throttled.** It follows the
scratchpad intake's precedent in the same loop ("a periodic look-at-local-state-and-raise-a-
proposal pass with nothing to dispatch") rather than adding a fifth background task, and it sits
inside that loop's `incident_active()` guard — which is correct, since eligibility is ineligible
during an incident anyway. `--no-crons` disables it with the rest of the unattended work.

**DECISION (security) — the undo site is EXEMPT from the action-provider execution invariant, and
the exemption is asserted.** `test_action_provider_chokepoints` requires every module reaching
`get_action_provider` to carry a policy check. `ladder.py` reaches one to UNDO, which is the
opposite direction: an `incident_active` check there would refuse to take back exactly the
automatic action a user turned the kill switch on because of. So a second named exemption joins
the catalog one, with `test_the_reversal_site_undoes_and_never_executes` pinning the properties
that earn it (never `.execute(`, always `.reverse(`, resolution bounded by `reversal_kinds`).

### 2026-08-13 — Atom AG-12 (§1.2 denylist at the THIRD dispatch seam) — DONE

**PROVENANCE — the gate was lost in a retirement, not never built.** §1.2 (line 96) declares the
denylist is enforced at the three dispatch seams every action-provider execution passes through,
for a stated reason: "an app-contributed provider inherits the denylist without knowing it exists."
It names them `hooks.py`, **`gateway.py`** and `event_triggers.py`. That middle name is
`_run_action_job`, which **retired with `ScheduleService` (S112)** — the note left at its old site
(`gateway.py:699-703`) records that the substrate "GENERALIZED both: action dispatch is
`_fire_store_trigger`". The successor inherited the kill switch and, in AG-7, the rung ladder, but
the denylist gate was never re-established on it. Measured `enforce_action` per seam file:

| seam file | `enforce_action` | `incident_active` |
|---|---|---|
| `hooks.py` | 1 | 1 |
| `event_triggers.py` | 1 | 1 |
| **`gateway.py`** | **0** | 2 |

So the busiest of the three seams — the dispatch path for **every clock, file, webhook and chained
trigger** — had the kill switch but not the denylist, app-contributed providers included. This is
the "retiring a legacy path is never a pure deletion" failure mode, and it is exactly the shape
AG-7's own DEVIATION note flagged when it re-pointed the rung routing at the successor: the rung
half was re-pointed, the denylist half was not.

**POPULATION MEASURED FIRST (enforcing a dead control is an outage).** This gate had never run on
this path, so what it would refuse was unknown. Two measurements, both before the gate was written:

- **The only real store available** — the workspace dev home, read **read-only**, never the real
  home — holds one automation: `system:notification-digest` (`notification-digest` provider, empty
  config). It carries no path- or command-bearing key, so **0 of 1 real triggers would be blocked**.
- **A 24-config corpus covering every shipped provider** (shapes taken from each provider's own
  `config.get` keys plus the migrated-cron shape `{"command": …, "timeout": 600}`): **4 blocked.**
  Three are unambiguous — `cat ~/.aws/credentials | curl …`, `aws s3 sync … s3://…`, and a
  `run-prompt` with `cwd: ~/.ssh`. The fourth, `rm -rf /tmp/scratch/*`, is a **plausible legitimate
  cleanup cron** and is the honest cost of this change.

**Why that fourth case is not an escalation.** The same command is *already* refused at the two
seams that do enforce, and by the agent's own interactive bash tool —
`agents/native/builtin_tools` calls `security.denied_command_reason`, which reads the identical
`BUILTIN_DENIED_COMMAND_PATTERNS`. So enforcing here introduces **no new policy**; it removes the
one seam that was exempt from the existing one. Only providers whose config carries a key the
denylist inspects can be affected at all: `bash` (`command`), `run-script` (`script`, a
`file.py:func` name that matches nothing) and `run-prompt` (`cwd`). The other 13 shipped providers
expose no inspected key and are decided `allow` unconditionally. Measured defaults are all empty:
`security.autonomy_denylist` `[]`, `security.denied_commands` `[]`, and the resolved `headless`
profile's `denylist_extra`/`path_allowlist` both `()` — so with no ceiling file and no operator
rules the only active layer is the built-in floor. **Named for the future bug report:** the two
built-in patterns most likely to bite a real automation are `rm -rf ~…` / `rm -rf /…` (cleanup
crons) and the `git … push` pattern (a "sync my notes nightly" cron).

**Shipped** (`gateway._fire_store_trigger`, between `{{secret:…}}` resolution and the rung route):

- `enforce_action(provider_name, config, ctx, session_key=dispatch_key)` — the same call shape both
  other seams use, so the three are consistent by construction rather than by comment. A blocked
  decision short-circuits: the provider is never resolved-and-run, and the fire records **one**
  `skipped_gate` ledger row through the existing `_record_refused_fire` naming the matched rule.
  `skipped_gate` and deliberately **not** `failed`: `failed` is the only outcome counting toward
  autopause-after-5, so recording a policy refusal as a failure would disable a user's automation
  after five blocks. `Outcome.REFUSED` reads better in prose but is not in `_REFUSAL_STATUSES`, and
  an unmapped status falls to the projection table's silent `FAILED` default.
- **Placed AFTER secret resolution**, so the check judges the config the provider will actually
  receive — a `{{secret:CMD}}` that expands into a denied command is refused on its resolved value,
  not waved through as a placeholder that matches no pattern (driven as a test).
- **Placed BEFORE the rung ladder**, matching both other seams: a rung never relaxes a block. Also
  driven — with the router forced permissive, the block still holds.
- **`dispatch_key` is now computed once and shared by both gates** on this seam (the shape
  `event_triggers` uses), so the denylist and the ladder judge one fire under one resolved posture.
  Threading it is what makes the run's `SafetyProfile.denylist_extra` and its `path_allowlist`
  confinement layer here as at the other two seams — proven by outcome, since `check_action`
  consults a profile only `if session_key:`, and asserted on the resolved identity
  (`unattended:trigger:<id>`) rather than on the mere presence of a string.
- `ctx` construction moved above the gate so the denylist judges the same `(config, ctx)` pair the
  provider is handed. No new observability code: `enforce_action` already writes the SEL row and
  fires the `needs_human` notification.

**The rail** (`tests/test_action_provider_chokepoints.py`): `POLICY_CHECKS` is satisfied by ANY one
check, which is right for its question ("does this site consult policy at all?") and blind to a
*specific* control vanishing from a *specific* seam — which is what happened. So a second invariant
joins it: `DENYLIST_SEAMS` names the three §1.2 seams and requires `enforce_action` in each, found
via **AST** (the calls span one, four and five lines; a regex tuned to today's formatting would
stop seeing them silently), plus a per-seam assertion that the call passes `session_key=`. A third
test derives `DENYLIST_SEAMS` from `EXECUTION_SITES` minus the one documented exemption, so a
**fourth** execution seam cannot appear without either carrying the denylist or arguing itself an
exemption. The exemption is the manual Run path (`dashboard/handlers/triggers`): a human just
pressed Run, so it is attended by definition and gated by `manual_refusal` — asserted, not assumed.

**Before/after evidence.** With `gateway.py` reverted and the new tests kept, 6 of the 7 behavioural
tests plus the gateway parametrization of the new rail FAIL, and the exfiltration command records
`status: success` — i.e. it ran. The one test that passes both ways is
`test_an_allowed_action_STILL_FIRES`, which is the point of having it.

**Validated against a real gateway** (isolated home `/private/tmp/ag12-dev-home`, `AUTH_MODE=none`
loopback, never `~/.personalclaw` — confirmed unmodified afterwards). Two `file` triggers watching
two files, identical but for their command; changing both files let the real file-watch poll loop
dispatch through `_fire_store_trigger`:

- denied → `WARNING guardrails.denylist: action denied (block) … '.*cat.*/\.aws/.*'`, a
  `skipped_gate` history row reading `blocked by the guardrails denylist: cmd:… — action command
  matches denied pattern …`, and a SEL `api_access` row `{operation: guardrails.denylist, outcome:
  blocked, source: guardrails}`;
- allowed → `success`, unchanged.

**DISCOVERY (pre-existing, not this atom's scope).** The same drive with two `clock` triggers
(`every: 10`) never reached `_fire_store_trigger` at all: `triggers.loop` dispatched the fires to a
session, `wakeup.dispatch_fires` returned `no_session` (a bare home has no chat session and no model
to make one), and because the tick had already CLAIMED each run, every later tick recorded
`skipped_overlap — held by tick:<t> since Ns ago` with the claim not released by that path. So on
this path an undeliverable fire wedges the trigger for the whole run deadline. That is the
no-claim-lease gap already owned by WF2WOR-1; recorded here because it is what made the clock half
of this validation unusable, and the file half is what proved the seam.

**CORRECTION (PHF-9, 2026-08-13).** The sentence above originally read that such a fire "burns the
trigger's claim permanently". That overstates it, and the severity matters because a permanent burn
would be a data-shaped defect while a bounded delay is a latency one. `triggers/reaper.py:117`
(`reap_one`) calls `claims.release_claim` for any run overdue past `RUN_DEADLINE_SECS`
(`reaper.py:63` — 1800s), and the sweep is LIVE: `gateway.py:1877` starts
`_trigger_reaper_loop` (line 819), which hands the store and home to `reaper.run_forever`. So the
claim IS released — the wedge is bounded at up to 30 minutes of missed fires, not permanent. The
finding stands; only its severity was wrong.

Gate: `make lint` rc=0; targeted suites green; full suite **18921 passed, 30 skipped, 12 xfailed, 3 failed** — the three
pre-existing `tests/test_harness_validate.py` failures that red in any worktree (the harness
resolves `.venv/bin/python` relative to cwd). 15 new tests. All four generated baselines
byte-identical after regeneration. No `web/` change.

### 2026-08-15 — Atom AG-13 (consolidate the fourteen autonomy knobs into one declarative policy) — DONE

"How much freedom does this run have?" was answered in fourteen places with no composition
rule. This lands the ONE declaration — the SAME object PP-14 declared, `SupervisorPolicy`,
now carrying the guardrails half too, so a run's supervisor policy and its autonomy ceiling
are one declaration, not two. **Stacked on `feature-pp14-supervisor-policy`; PP-14's commit
was not rebased or altered.** Class-B declaration change, still **deliberately inert** — the
PP-14 honesty rail stays green (the consolidation lives inside `supervisor_policy.py`, which
the caller census skips, and nothing in the engine constructs it; PP-15 wires convergence,
AG-11 wires profile/trust). Clean break under the pre-1.0 banner.

**§5's `Ceiling ∩ Profile` model was ALREADY in code** (`guardrails/ceiling.py` + `policy.py`
— four archetypes, `resolve()` tightest-wins) — no second composition model was invented; the
consolidation composes onto it. This atom did not have to build §5.

Shipped in `src/personalclaw/workflows/supervisor_policy.py`:
- `SupervisorPolicy` extended to subsume the run's `SafetyProfile` (`autonomy` field) plus the
  knobs the loop declaration lacked: `single_active_feature`, `autonomy_mode_floor` (a `Mode`),
  `resilience` (new `BreakerLimits`), `trust_ttl_secs`, `idle_secs`. `SafetyProfile` itself is
  UNCHANGED, so its five AG-5 live readers keep working (asserted).
- `POLICY_KNOB_MAP` — the load-bearing artifact: fourteen rows, one per knob, each naming the
  ONE policy field it maps onto. The consolidation is visible in the collisions — three HITL
  knobs (`require_hitl`, the confirmation matrix + per-stage mute, loop `attended`) collapse
  onto `hitl_posture`; `RunBudget`, the gate auto-approval and `SafetyProfile` onto `autonomy`.
- `consolidate(...)` builder + `compose(ceiling, policy)` (narrows the guardrails half via the
  SAME `guardrails.ceiling.resolve`; loop-convergence knobs are run-declaration bounds the
  operator ceiling does not govern, so they pass through — a profile may only NARROW) +
  `write_scope_allows(...)` matched by the §5 `path_glob` (never `normpath` a PATTERN).

The fourteen → field map: RunBudget→`autonomy.budget.max_tokens`; single_active_feature→
`single_active_feature`; require_hitl/confirmation/attended→`hitl_posture`; gate auto-approval→
`autonomy.approval`; risk floors/earned-trust→`autonomy_mode_floor`; allowed_write_paths→
`write_scope.allowed_paths`; resilience→`resilience`; escalation ladder→`escalation_ladder`;
trust_ttl_secs→`trust_ttl_secs`; max_cycles→`budget_max_cycles`; idle_secs→`idle_secs`;
SafetyProfile→`autonomy`.

The matrix (`tests/test_ag13_autonomy_policy.py::test_the_behaviour_preservation_matrix`) proves
this is a consolidation and NOT a behaviour change: for 8 runs (HEADLESS + INTERACTIVE workflow
runs, all five bundled loop kinds, the code-project template that sets `single_active_feature`)
it composes under the shipped default (no operator ceiling) and asserts the field each of the
fourteen knobs maps to equals that knob's value read from its OWN home (`Loop`/`RunBudget`/
`ExecutionHints`/`LoopsConfig`/`SafetyProfile`/`DEFAULT_LADDER`), knob-by-knob.

**DISCOVERY (not fixed here — a behaviour change AG-13 must not make):** `workflows/scope.py:149`
(`in_scope`, the runtime write-scope matcher) `normpath`s the PATTERN and matches with `fnmatch`
— the exact §5 landmine (`/a/**/../b` collapses to `/a/b`, silently widening an allow; `fnmatch`
lowers `**` to `*`). AG-13's consolidated read surface (`write_scope_allows`) uses the
§5-correct `registries.path_glob` instead. Repointing `scope.py`'s live enforcement at the
consolidated matcher belongs to the wiring owners (PP-15/AG-11); doing it here would change what
a run permits, which this atom is forbidden to do. Logged for those atoms.

**DEVIATION:** the atom lists `AG-11`'s deferred profile/trust behaviours as "re-pointed at the
consolidated object rather than executed twice" — AG-11 is still `todo`, so there was nothing to
re-point yet; the consolidated object it will point AT now exists, which is the enabling half.

Falsifications (each mutated, verified to red naming the right thing, restored from `cp` backup,
tree byte-clean after): (1) remap `max_cycles`→`idle_secs` → matrix reds `knob 'max_cycles':
120 != 0`; (2) compose skips narrowing → `test_tightest_wins…` reds (`auto` survived where `ask`
must win); (3) `normpath` the write-scope pattern → `test_a_dotdot_pattern_does_not_widen…` reds
(`/a/b` matched `/a/**/../b`).

Gate: `make lint` clean; targeted suites green; full suite `python -m pytest --no-cov -q`
(PERSONALCLAW_HOME unset) — see the final commit's numbers. Collection diff vs the stacked base
`feature-pp14-supervisor-policy`: **+7, zero removed** (19399 → 19406 — the seven AG-13 tests).
Two pre-existing `tests/test_guardrails_ladder.py` failures are inherited from the PP-14 base
(reproduced identically at commit `6de2130e` with no AG-13 change) and are NOT attributable here.

### 2026-08-15 — Atom AG-11 (§4.1 research subagent class + cron-approval rewire + §4.3 project trust) — DONE

The three Session-4 deferrals, wired to what exists on `main`. **Deliberately NOT re-pointed at
AG-13's consolidated `SupervisorPolicy`** — that object ships in an unmerged stacked PR (#1333) and
is off-limits; the atom's done-when is "resolves through `profile_for_session`", which works on
`main` today. AG-13's log line ("AG-11 re-pointed at the consolidated object") is aspirational; the
enabling half (a shared object) is not a dependency of these three behaviours. The `scope.py`
write-scope repoint AG-13 flagged as "PP-15/AG-11 territory" needs that consolidated matcher too, so
it stays with the wiring owners — not done here.

**§4.1 — read-only research subagent class (enforced at the tool-approval layer).**
`SubagentManager.spawn` gains `capability_class`; `resolve_capability_class` defaults an AUTO-FIRED
spawn (`approval_mode="auto"`) to `research` and a human-watched one to `mutating`; an explicit class
always wins. The denial is enforced in `subagent._run_inner`'s permission loop — a research spawn's
`is_write_tool(event.title)` request is `reject_tool`'d BEFORE any auto-approve branch (placement is
load-bearing: an auto-fired research run resolves `parent_policy="auto"`, so a denial after that
branch would be dead code). It reuses `workflows.batch_compile.is_write_tool` — the SAME policy the
workflow research leaf uses (`mcp_shared.leaf_tool_denial`) — so a research subagent and a research
leaf deny identically (a coherence test asserts the three capability constants are equal). This is
enforcement, not a `.tools` list the native runtime ignores (WF2LEA-6). Callers: `run-prompt` /
`invoke-agent` default `research` (an `capability: mutating` action-config field is the creation-time
write grant); `workflows/engine.py` passes `capability_class` mirroring `cfg.capability` — ONE
capability decision now drives BOTH the leaf-env read-only flag (MCP tools, handler seam) AND the
subagent class (NATIVE tools, approval loop), closing the native-write gap the MCP-only seam left for
a research leaf; `dashboard/handlers/apps.py` app-run passes explicit `mutating`
(behaviour-preservation — an app-run agent is an established write surface).

**Cron-approval rewire.** DISCOVERY: the literal `AUTO_APPROVE if approval_mode=="auto" else
HOOK_BASED` ternary the S4 DEVIATION named no longer exists — AG-5 (#872) already rewired the
heartbeat to `approval_policy_for_session`, and the subagent SPAWN grant is deliberately
ceiling-bounded via `ceiling_permits_approval` (PHF-8), NOT profile-routed (routing it through
`profile_for_session` would let HEADLESS veto the trust toggle — the exact thing PHF-8's docstring
forbids). The one remaining un-routed ad-hoc AUTO_APPROVE in an unattended path was the subagent
RESULT-INJECTION turn (`gateway._subagent_done` → `_inject_with_retry`), which defaulted to
AUTO_APPROVE for a cron/channel parent. New `gateway.injection_approval_policy(parent_key)` routes an
UNATTENDED parent through `approval_policy_for_session` (→ `profile_for_session`) and keeps an
INTERACTIVE parent on AUTO_APPROVE. Behaviour-preserving for already-approved cases: an announce that
calls no tool is unaffected, and under HOOK_BASED the security hooks still auto-approve hook-neutral
tools — only the dangerous tools they already deny elsewhere are now gated on an unattended announce.

**§4.3 — project Trust/Preview gate.** New `guardrails/project_trust.py`:
`gate_project_capability(cwd, requested)` returns the grant for a **Trusted** folder and forces
`research` (read-only, REVIEW_ONLY) for a **Preview**/undecided one — Preview reuses the §4.1 class,
one read-only control with two entry points. The FIRST touch persists a Preview record
(`project_trust.json`, LWW, keyed by resolved dir — a new `durability/inventory.py` StateEntry) and
raises a needs-input inbox row (reusing the existing `system`/`agent_request` attention pair — no new
notification kind), deduped so it prompts ONCE. Fail-OPEN store / fail-CLOSED decision (a corrupt
file → Preview, never trusted). Wired at the `run-prompt` project-`cwd` seam and exposed at
`GET|POST /api/guardrails/project-trust` so a user can act on the prompt. Offline `reference/routes.md`
regenerated (+2 routes).

Falsifications (each mutated, verified red naming the right thing, restored from `cp` backup, tree
byte-clean after; markers grepped to 0): (1) research gate admits writes (`if False and
is_write_tool…`) → `test_auto_fired_research_spawn_denies_write_tool` + `…_bash_execute` red
(`reject_tool` awaited 0 times); (2) `injection_approval_policy` bypassed to always AUTO_APPROVE →
`test_injection_policy_unattended_resolves_through_profile` reds (`AUTO_APPROVE is not HOOK_BASED`);
(3) Preview returns the grant instead of read-only → `test_first_touch_persists_preview_and_forces_readonly`
+ `test_preview_folder_stays_readonly` red (`'mutating' == 'research'`).

Gate: `make lint` clean (black/isort/flake8/mypy, 1616 files); the new suite
`tests/test_ag11_profile_trust.py` — 16 passed. Collection diff vs the fork base `origin/main`
(`94711166`): **+16, zero removed** (19474 → 19490 — the sixteen AG-11 tests). The first full-suite
run surfaced FOUR attributable reds, all fixed in this commit: the two
`test_action_schema_executor_parity` reds ([invoke-agent] + [run-prompt]) were the new `capability`
config key being READ by the executor but not DECLARED in the action manifest — so it is now a
declared `settingsSchema` enum on both native action apps (which also gives the Triggers "Advanced"
form the write-grant control, the product half); the two `test_app_agent_run` 500s were the test's
`_FakeSubagents.spawn` double lacking the new `capability_class` kwarg — the fake now mirrors the
real signature. After the fix, full suite `python -m pytest --no-cov -q` (PERSONALCLAW_HOME unset):
**19450 passed, 30 skipped, 12 xfailed, 0 failed** (+4 vs the pre-fix run — exactly the four fixed).
A separate `-n 0` targeted run flagged two `tests/test_guardrails_ladder.py` order-isolation reds
and some subagent/`TestOnDoneTimeout` reds; both classes are PRE-EXISTING and non-attributable —
the ladder pair reproduces identically on `94711166` (a `test_a_provider_that_refuses…` →
`test_create_task…` order bug), and the subagent reds are CPU-contention flakes that pass in
isolation and did not recur under the parallel run. Base worktree removed after the diff.

---

### 2026-08-18 — Atom AG-9 (apps-repo guardrails follow-ons) — PARTIAL: clause 2 DONE, clauses 1+3 BLOCKED

AG-9's three `done_when` clauses were censused against BOTH repos' `main` before any code was
written, because the apps repo's tip (`6979f70 feat(apps): AG-9 native structured_output and
channel send refusal`) was already an AG-9 commit while the atom still read `todo`. Result: one
clause was genuinely unmet and is now done; two are blocked on a core mechanism the plan's own
Session-1 DEVIATION claims core already shipped, and which **does not exist**.

| Clause (verbatim) | Verdict | Evidence |
|---|---|---|
| "an ollama-bound `output_type` call uses native json-schema format" | **BLOCKED** — apps half complete, core forward missing | apps repo `ollama-models/provider.py` (factory at lines 786-797, declaration 728, wire 470-471 / 565-566); core `llm_helpers.py:363` + `:371-382` |
| "a channel app's `send()` returns a typed refusal under DISABLE_LIVE_WRITES" | **DONE** (was 1 of 4 apps; now 4 of 4) | apps repo `discord-channel/discord_runtime/transport.py` line 326, `slack-channel/slack_runtime/transport.py` line 160, `email-channel/email_runtime/transport.py` line 567 |
| "core's `structured_output` dispatch hook is exercised natively" | **BLOCKED** — no such hook exists | see DISCOVERY below |

**DONE — the channel `send()` honor point, at all FOUR channel apps** (apps repo commit; this repo
carries only this log entry). Session 3's deferral was read as "a channel app", and the landed AG-9
commit satisfied it for `telegram-channel` only. `provider.type == "channel"` matches **four** apps,
and `discord-channel` / `slack-channel` / `email-channel` each still had a raw `async def send()`
that consulted nothing — the wrapper was fixed and three raw siblings were missed. All three now
return the same typed, falsy, non-raising `SendRefused` telegram does, checked AFTER each app's own
token/configuration gate (an unconfigured transport reports a plain `False`: it could not have
written anything, so claiming the guard suppressed a write would be a lie).

The flag parse is mirrored per app rather than imported: core's `guardrails.writes.live_writes_disabled`
is not an SDK export and an installed bundle has no sibling to import, so each app carries a
`writes.py` whose `_EXPLICIT_FALSE` set and `guard_flag`/`live_writes_disabled` bodies are
byte-identical to telegram's. Drift is held shut two ways — each bundle's suite cross-checks its
parse against core's live symbol (test files are exempt from the import-boundary lint), and a new
repo rail (`.github/scripts/check_live_writes_posture.py`, wired as the `live-writes-posture` CI
job) AST-asserts that every `provider.type == "channel"` app calls `live_writes_disabled()` inside
its `async def send` body (not in a docstring, not in `health()`) and spells the token set exactly
as core does. The rail carries a vacuity floor: the four known channel apps must all be discovered,
so an app disappearing or a glob that stops matching reds instead of shrinking to a clean pass.
Falsified in all four directions — dropping the guard call reds it, drifting one token set reds it,
flipping one manifest's `provider.type` trips the floor, and the guard-OFF path still really
transmits.

**DISCOVERY — the "capability-dispatch hook" Session 1's DEVIATION says core shipped does not
exist.** That DEVIATION (this log, 2026-07-25) reads: "Core ships the complete substrate: the graded
descriptor (default `NONE`), the capability-dispatch *hook*, and the **universal**
parse-with-targeted-retry… Follow-on: an apps-repo change sets `BrandedProviderSpec.structured_output`
+ ollama `format` wiring; then core's dispatch hook lights up natively." The apps-repo half is now
complete and verified. The core hook is not there. Measured, not read:

- `one_shot_completion(output_type=…)` (`llm_helpers.py:289-400`) never reads a provider capability.
  Its typed path (`:371-382`) is *only* parse → one targeted-retry → `OutputContractError`. Its
  build-kwarg dict is `_bridge_kw = {} if temperature is None else {"temperature": …}` (`:363`), so
  the requested shape never leaves core. A probe that stubs `resolve_provider_for_use_case` and
  calls `one_shot_completion("hi", output_type=dict)` records the bridge receiving
  `{'use_case': 'background', 'kwargs': {}}` — **zero** build kwargs.
- Grep for `structured_output` across `src/personalclaw/**.py` finds exactly two readers of the
  capability field, neither in a request path: `workflows/grounding.py:492` (sets a legibility flag
  + a `model_notes` line on the grounding bundle) and `routing/policy.py:312`.
- So `capabilities.py:65`'s `structured_output` field has **no live native caller in the model-call
  path**, and the ollama app's `StructuredOutput.JSON_SCHEMA` declaration cannot change any request.

**DISCOVERY — `routing/policy.py:_structured_providers()` is dead code that can never match**
(pre-existing, NOT introduced or fixed here). It compares `str(getattr(cap, "value", cap)) ==
"structured_output"` over `ProviderEntry.declared_capabilities` (`_structured_providers`, `:298-316`; the comparison at `:312`). That field is typed
`frozenset[Capability]` (`llm/registry.py:87`) and `Capability` has no `structured_output` member
(`capabilities.py:14-24` — chat, code_tools, summarization, planning, embedding, vision, streaming,
tool_approval); `registry.register_entry` additionally validates the set is a subset of the type's
`cap.capabilities` (`:170`), and `catalog.infer_capabilities` (`:264-368`) emits a fixed vocabulary
that does not include it either. `StructuredOutput` is deliberately a separate GRADED field, not a
member of the flag set (`capabilities.py:27-33` says so). So the §4.1 structured-output routing
exception never fires, and the function fail-opens to an empty set on every call. This is the
"live reader of an unwritten key" shape: it is green, silent, and has never done anything.

**BLOCKED (E1 premise mismatch + E6 scope) — clauses 1 and 3 need a core mechanism, not a rail.**
The atom is fenced to the apps repo plus this log. Closing clauses 1+3 means BUILDING the missing
capability-dispatch hook in `llm_helpers.py`, which is a design decision, not a mechanical
remainder: it must choose which grade forwards what (`JSON_SCHEMA` → a schema; `JSON_MODE` → an
unschema'd JSON request), whether the universal parse-with-retry still runs as verification behind a
native constraint, and what a non-`dict`/`list` `output_type` (a Pydantic class) forwards. It also
must be capability-GATED: an unconditional forward would push an `output_type` key into a
non-declaring provider's `extra_options`, and both core protocol clients pass unknown
`extra_options` keys straight onto the wire (`llm/openai.py:218`, `llm/anthropic.py:501`) — which
would put a bogus field in a live request for every provider that never opted in. Recorded here
rather than improvised.

What the next session does NOT have to re-derive — the apps side is proven ready, so this is a
core-only change:

- `ollama-models/_factory` (`provider.py:739`; the kwarg block at `:786-797`) already accepts `format` / `output_type` as
  BUILD KWARGS on the same channel `model`/`embedding_model` ride, clears any standing entry option
  so a per-call contract wins, and folds them into `extra_options`; the constructor normalizes them
  once into `_output_format` (`:332`) and both request builders emit `body["format"]`
  (`:470-471`, `:565-566`). Measured against the real registry factory: build kwargs `{}` →
  `_output_format=None` (an ordinary turn is byte-identical); `{"output_type": dict}` →
  `{'type': 'object'}`; `{"output_type": list}` → `{'type': 'array'}`; `{"format": {...}}` → passed
  through verbatim; `OLLAMA_CAPABILITY.structured_output` → `StructuredOutput.JSON_SCHEMA`.
- The seam to copy is the temperature one, which already works end to end:
  `one_shot_completion(temperature=…)` → `_bridge_kw` → `registry.build` kwargs →
  `extra_options["temperature"]` → the wire (`sampling.py:22-24`).
- `sdk/provider_helpers.py` was deliberately NOT touched (two other changes are in flight against
  it). Note the atom's scope line names `BrandedProviderSpec.structured_output`; the landed apps
  commit reached the grade through `ProviderCapability.__dataclass_fields__[…].default` instead
  (apps repo `ollama-models/provider.py` line 89), keeping the declaration inside the SDK boundary without a new
  SDK field. Whether the branded spec should carry the field explicitly is still open.

Gate (apps repo, run per bundle — a combined multi-directory run raises `import file mismatch`
collection errors from duplicate `test_live_writes.py` basenames, which are not test failures):
`telegram-channel` 144 passed · `discord-channel` 234 passed · `slack-channel` 508 passed, 1 xfailed
· `email-channel` 342 passed · `ollama-models` 70 passed; all 43 test-bearing bundles green
(`alibaba-models` ships no test file at all — pre-existing, and CI's `compgen -G` guard skips it
too). The other apps CI jobs run locally clean: manifest-validate (45 manifests), boundary
(sdk-only imports), live-writes-posture (4 channel apps), DCO. No core source file was changed by
this atom, so no core gate applies beyond this document.
---

### 2026-08-19 — Atom AG-9 clauses 1 + 3 (core forward) — DONE; the 2026-08-18 BLOCKED is CLEARED

The previous entry's BLOCKED was correct about the mechanism and correct to stop: the seam was
half-built, apps-side consuming a request core never sent. This session built core's half. The
premise was re-verified against `cdd9dc2f` before writing anything — apps `ollama-models/provider.py`
does define `_FORMAT_FIELD`/`_OUTPUT_TYPE_KEY`, `native_format()` and the popping
`resolve_output_format()`, and core's `one_shot_completion` did assemble its per-call build kwargs in
exactly one place with `output_type` absent from it.

**One line-number drift worth recording** (the previous entry's citations are otherwise exact): it
cites `_bridge_kw` at `llm_helpers.py:363` and `one_shot_completion` as `:289-400`. On `cdd9dc2f`
`_bridge_kw` is at `:375` and the function spans `:289-506`. The symbols and the claim are unchanged;
only the offsets moved.

**Shipped — `src/personalclaw/llm_helpers.py`.** `output_type` now rides the bridge as a build kwarg
to a natively capable provider, so an ollama-bound typed call CONSTRAINS generation instead of only
asking for a shape in prose.

- New `_enforces_json_schema_natively(model_ref)` — the capability gate. `split_ref` → the
  registry's `get_entry(provider_name)` → `capability_of(entry.type).structured_output`.
- `_budget_kw` → **`_entry_kw`**, and the decision lives INSIDE it. That is the per-entry point:
  all three resolution paths (pin `:522`, chain advance `:543`, plain `:575`) already call it with
  the ref they are about to run, and the chain walk can advance from a capable entry to an incapable
  one mid-call. The old name described only one of the three things it now carries.

The three design questions the previous entry refused to improvise are answered here:

1. **Which grade forwards what — `JSON_SCHEMA` ONLY.** `JSON_MODE` is deliberately excluded and the
   code says so by comparing to the grade rather than `!= NONE`. `JSON_MODE` means OpenAI-wire
   `response_format={"type": "json_object"}`: a different request field that nothing derives from
   `output_type`, so forwarding the key to it would reproduce the exact corruption the gate exists to
   prevent (unconsumed key → `extra_options` → request body → `TypeError` in the JSON encoder). It
   also cannot express `output_type=list` at all — `json_object` mode requires an object — so for
   half of the documented inputs the weaker grade is not weaker, it is wrong. Reaching `JSON_MODE` is
   its own `response_format` kwarg plus an adapter that consumes it; a loosened comparison would be a
   silent bug, so a test pins the exclusion.
2. **The universal parse-with-retry still runs behind a native constraint — both layers stay.** A
   constraint is a request, not a promise, and constrained decoding is measured in this repo to
   return valid-but-empty documents; skipping the parse because "the provider guarantees it" converts
   a caught failure into a silent one. Falsified: making a constrained call skip the parse returns
   `'not json at all'` as the answer.
3. **A non-`dict`/`list` `output_type` (a Pydantic class) is forwarded verbatim, and that is safe
   because normalization is the PROVIDER's job, not core's.** Core forwards the request; the ollama
   app's `native_format` refuses anything it cannot express and sends no `format`, so the wire stays
   clean and the parse-with-retry governs. Core deciding a wire form for every provider would put
   vendor knowledge in the provider-agnostic path.

**Everything unverifiable degrades to "send nothing"**, matching `workflows/grounding.py`'s defensive
read: an unqualified ref, the `"gpt-oss:20b"` shape whose colon is not a provider prefix (the
`get_entry` miss rejects it exactly as the bridge does), the legacy `"Provider/model"` slash
spelling, an unregistered type, an unbootstrapped registry, and `""` (the unbound-axis plain path,
where the implicit fallback has not chosen a provider yet). `output_type=None` short-circuits before
the lookup, so the default path's kwargs are byte-for-byte unchanged and do no registry work.

Note the predicate difference, which is deliberate rather than an inconsistency: `grounding.py` asks
`!= NONE` because it answers "does schema-constrained emission exist ANYWHERE in this install", for
which `JSON_MODE` legitimately counts. This gate asks about one entry's ability to honour one key.

**Tests — `tests/test_llm_helpers.py`, +13** (`TestOneShotNativeStructuredOutput`). Asserted on the
KWARGS DICT handed to the resolution seam, not on downstream behaviour: "the provider constrained
generation" is unobservable from core, whereas "core sent the constraint to exactly the providers
that advertised it" is the contract core can be held to. A graded fixture registry spans
`JSON_SCHEMA` / `NONE` / `JSON_MODE` plus an entry whose type is registered nowhere — the last is not
a contrivance, it is what `register_entry` deliberately stores for an app that loads after
`sync_entries_from_config`, so a raising `capability_of` is a real shape. Both chain orders are
asserted, because an implementation that caches the first entry's answer passes one direction only.

**Falsified 5 ways** (mutate the live line, `grep -n` to prove it applied, `py_compile`, observe the
red, restore from a file copy — never `git checkout`): forward unconditionally → 6 red including the
`NONE`-provider test; decide once per call instead of per entry → exactly the two mixed-chain tests
red; skip the parse when a constraint was sent → the retry and contract-error tests red; treat a
raising lookup as capable → the degrade and unknown-ref tests red; loosen the grade to `!= NONE` →
the `JSON_MODE` exclusion reds alone.

**The repo's own inert-surface census independently confirms the atom's premise.** The first full
`make test` failed exactly twice, both in `test_inert_surface_baseline.py`, with
`src/personalclaw/llm/capabilities.py: committed 2 > current 1` — a legitimate SHRINK, so
`inert-surface-baseline.json` was regenerated in this same commit as the rail instructs (135 → 134;
`enum:StructuredOutput.JSON_SCHEMA` leaves the inert list). Confirmed to be this diff and not
merge drift: restoring the base `llm_helpers.py` under the same baseline makes that suite 18 passed.
Two things follow. First, the census agrees the previous entry was right that the graded field had no
live caller in the model-call path — it does now. Second, `enum:StructuredOutput.JSON_MODE` stays
inert, which is the honest record of the scoping decision above rather than an oversight: nothing
writes or reads that grade, and wiring it would be the separate `response_format` work.

Verified reachable end to end across BOTH repos, with the real objects rather than a synthetic
capability: loading the apps `origin/main` `ollama-models/provider.py`, registering its actual
`OLLAMA_CAPABILITY` (grade `JSON_SCHEMA`), and driving
`one_shot_completion("…", use_case="background", output_type=dict)` on an ollama-bound axis yields
build kwargs `{'output_type': <class 'dict'>, 'max_tokens': 4096}`, which the app's own
`resolve_output_format` POPS into `{'type': 'object'}` leaving `extra_options` empty. That also proves
the enum identity holds across the two independent derivations of `StructuredOutput` (core's class vs
the app's `type(ProviderCapability.__dataclass_fields__[…].default)`). Probe run from `/tmp`, never
added to the repo. **Caveat for anyone repeating it:** the apps working checkout was on
`feature-tse5-shared-automations-app`, whose `ollama-models/provider.py` PREDATES the AG-9 apps commit
and therefore reports grade `NONE` — probe `origin/main`, not the working tree, or the seam reads dead.

Gate: `make lint` 0 · `tests/test_llm_helpers.py` 43 passed · the 20 suites naming
`one_shot_completion` 670 passed / 5 skipped · 11 provider/registry/grounding-adjacent suites 203
passed · `tests/test_inert_surface_baseline.py` 18 passed after the regen · full `make test` 22785
passed / 30 skipped / 12 xfailed / 0 failed. No `web/` change — this seam has no surface.

**No CHANGELOG entry, deliberately.** The file feeds the in-app Updates panel and every neighbouring
entry names something a user can see or do; this adds no surface, control or affordance. The only
user-facing claim available would be a reliability improvement (fewer malformed background results on
a local model) that has NOT been measured here, and an unmeasured benefit does not belong in
"what's new". If a retry-rate delta is measured on a real ollama model, that number earns an entry.

**Still open, NOT touched here** (in-scope to name, out of scope to fix): the previous entry's second
DISCOVERY — `routing/policy.py:_structured_providers()` compares against `declared_capabilities`,
which can never contain a structured-output member, so it still fail-opens to an empty set on every
call. This change reads the graded field correctly and does not resurrect that reader; fixing it
would alter routing ORDER for structured queries, which is a different subsystem with its own tests.
Also still open, as the previous entry left it: whether `BrandedProviderSpec` should carry
`structured_output` explicitly instead of the apps side reaching the grade through
`ProviderCapability.__dataclass_fields__`.

---

## Status: all four sessions COMPLETE (2026-07-25)

Session 1 (model-call chokepoint), Session 2 (budgets + scan + config), Session 3 (denylist
+ incident + DISABLE_LIVE_WRITES + guard_flag), Session 4 (profiles + egress tiers + health
+ FE) are DONE. Deferred, each with a logged reason: native `json_schema` enforcement +
`structured_output` declaration (apps repo), channel-`send()` DISABLE_LIVE_WRITES honor-point
(apps repo), run-scope budget + per-trigger fields (AUTOMATION-SUBSTRATE), §4.1 research
subagent write-gating + §4.3 project trust gate (engine per-template profiles / WORKFLOWS-V2),
and the live cron-approval rewire to `profile_for_session` (its per-template consumers). The
substrate for all of them ships here; the deferred pieces are seams awaiting their consumers,
not gaps.

## Amendment (2026-07-26 — gap analysis round 2, owner-approved mechanisms)

**Earned autonomy per action type — the plan's second act.** The shipped floor (S1-4, all DONE) is binary: an unattended run is HEADLESS read-only, or a write is a creation-time grant. There is no graduated middle and no track record — a reply-draft that has been approved unchanged 40 times still asks every time. Sibling-platform evidence: per-action-type rung ladders with *derived* track records are what let autonomy grow without a leap of faith. This adds the ladder ON TOP of the existing floor; budgets, denylist, and the kill switch always sit BELOW it — a rung never overrides a `check_action` block, a budget pause, or `incident_active()`.

### Contract-level design

- **Action-type registry** — every autonomous WRITE action gets a stable type key, declared where actions are declared: core keys at provider registration (`action_providers/registry.py:_ensure_default_providers_registered`) and at the inbox AI affordances (`inbox_service.py:draft_reply`/`classify` → `inbox.reply_draft`, `sessions.auto_tag`); app actions via an additive `autonomy: {floor, ceiling}` block on the manifest provider extension (`apps/manifest.py` ProviderConfig — unknown-field-preserved), keyed `app:<name>.<action>`.

```python
# guardrails/autonomy.py — beside policy.py/denylist.py, same frozen-dataclass template
RUNGS = ("draft_only", "one_tap", "auto_with_undo", "autonomous")   # ordered ladder

@dataclass(frozen=True)
class ActionTypeSpec:
    key: str                    # "inbox.reply_draft" | "sessions.auto_tag" | "app:<name>.<action>"
    floor: str = "draft_only"
    ceiling: str = "one_tap"    # anything leaving the machine ceilings below "autonomous" by default
    leaves_machine: bool = False
    promotion: PromotionRule = PromotionRule(clean_approvals=10, min_days=7, max_rejections=0)

def register_action_type(spec: ActionTypeSpec) -> None
def resolve_rung(key: str) -> str            # floor + accepted grants, clamped to ceiling;
                                             # incident_active() freezes resolution above one_tap
def promotion_eligibility(key: str) -> Eligibility   # DERIVED, never stored-as-opinion
def demote(key: str, cause: str) -> None     # automatic + immediate; starts cooldown_days
```

- **Enforcement at the existing chokepoints, no new seam:** `resolve_rung` composes with `enforce_action` at the three dispatch seams (`hooks.run_script_hook`, ~~`gateway._run_action_job`~~ → **`gateway._fire_store_trigger`**, the successor it was generalized into when `ScheduleService` retired in S112 — corrected by AG-7, see the log; `event_triggers._fire` → `event_triggers.execute_event_action`, extracted in S67) and with `profile_for_session` for unattended spawns. Routing per rung: `draft_only` → proposals inbox (plan 42's `kind=proposal` via `emit_attention_item` once 42 lands; pre-42, the `skills/proposals.py` pending-item + `notify` pattern); `one_tap` → approval card (the `ApprovalGate.request` surface, `agents/native/approval.py`); `auto_with_undo` → execute + persist a reversal handle on the `ActionResult` + passive notify; `autonomous` → execute under SEL.
- **Track record DERIVED, not stored:** eligibility computes from SEL `tool_approved`/`tool_rejected` outcomes plus FEEDBACK-SIGNAL records (plan 58, created this same rev — its store is the 👍/👎 source). Only *grants* and *demotions* persist (`~/.personalclaw/autonomy_rungs.json`, atomic_write, joins snapshot CORE_FILES): `{key: {rung, granted_at, evidence_window, demotions: [{at, cause, cooldown_until}]}}`.
- **Asymmetric by design:** promotion is ALWAYS a user click — eligibility files a *proposal*, never auto-promotes. Demotion is automatic + immediate on ANY rejection, undo, or 👎 for that type, with a cooldown before re-eligibility. Thresholds per-type configurable via a new `guardrails.autonomy` config subsection (four wiring points, §7).
- **Kill switch:** `personalclaw incident on` (existing `guardrails/incident.py`) additionally clamps every resolution above `one_tap` — during an incident nothing executes-with-undo or runs autonomous, even attended-adjacent actions.

### Session placement

Wave 3, **after plan 58 S1** (its records feed eligibility). Two new sessions; honest count ~4 → **~6**.

| ID | Task | Files | Done when |
|---|---|---|---|
| S5.1 | `guardrails/autonomy.py` (RUNGS, ActionTypeSpec, registry, resolve_rung with incident clamp, derived eligibility over SEL + plan-58 records, demote+cooldown); `autonomy_rungs.json` store; `guardrails.autonomy` config through the four wiring points | `guardrails/autonomy.py`, `config/loader.py`, `dashboard/handlers/core.py`, tests | a type with 10 clean approvals over 7 days + zero rejections is eligible; one rejection demotes immediately + starts cooldown; incident clamps above one_tap; eligibility is recomputed, never cached to disk |
| S5.2 | Core action-type declarations (registry seam + inbox affordances) + manifest `autonomy` block for `app:<name>.<action>`; rung routing wired at the three dispatch seams + `profile_for_session` (draft_only→proposal item, one_tap→ApprovalGate, auto_with_undo→execute+reversal handle+notify, autonomous→SEL) | `action_providers/registry.py`, `apps/manifest.py`, `hooks.py`, `gateway.py`, `event_triggers.py`, `guardrails/policy.py` | an app-contributed action inherits its declared floor/ceiling without dispatch-layer special-casing; a leaves-machine type cannot resolve `autonomous` without an explicit ceiling raise |
| S6.1 | Promotion proposals (user-click accept, SEL-audited like a skill install) + FE: rung chip on trigger/job rows, ladder panel in Settings → Guardrails (current rung, derived record, demotion history), undo affordance on auto_with_undo notifications; as-a-user validation sweep | proposal store wiring, `web/src/pages/settings/GuardrailsPanel.tsx`, trigger row components | promotion never happens without a click; an undo click both reverses and demotes; the chip answers "why is this allowed to run by itself?" in one glance |

- **2026-08-19 — `AG-9` CLOSED: the live leg, driven against a real local model.** The
  implementation entry above wired the seam; this is the measurement it reserved, and it is a
  before/after against the same prompt, the same model and the same installed app.

  Setup: the app loaded from `PersonalClawApps@origin/main` (which declares
  `structured_output=StructuredOutput.JSON_SCHEMA`), an entry registered exactly as
  `llm/registry.py:401` builds one from config, `httpx.AsyncClient.stream` wrapped to capture the
  request body, and `one_shot_completion("Pick a colour and a count between 1 and 5. Answer as JSON
  only.", use_case="background", output_type=dict)` on `Ollama:gemma4:12b`.

  | | base `main` | with this atom |
  |---|---|---|
  | advertised grade | `JSON_SCHEMA` | `JSON_SCHEMA` |
  | `format` on the wire | **absent** | **`{"type": "object"}`** |
  | returned text | ```` ```json\n{\n  "color": "Blue",\n  "count": 3\n}\n``` ```` | `{"color": "blue", "count": 4}` |
  | `json.loads` on that text | **raises `JSONDecodeError`** | parses, keys `['color', 'count']` |
  | requests captured | 1 | 1 |
  | `output_type` leaked into the body | no | no (the app pops it) |

  Read the base column precisely: it is **not** a failure of today's code — only one request was
  captured, so `_parse_llm`'s lenient path accepted the fenced block and no retry was needed. What
  it shows is that without the constraint the *text handed back to the caller* is markdown-fenced,
  so every caller depends on the lenient parser; with the constraint the caller gets strict JSON and
  the app pops `output_type` off `extra_options` so nothing unexpected reaches the request body.

  **A second measurement worth recording, because it cuts against the usual warning.** This repo has
  a standing caution that constrained decoding degrades generative calls. Measured directly against
  ollama on this model: the same three-word *unconstrained* answer costs **764 eval tokens / 25 s**
  (the reasoning trace consumes the budget, and with a small `num_predict` returns
  `done_reason: "length"` with empty content), while the **schema-constrained** extraction above
  costs **13 eval tokens / 9 s** — the constraint suppresses the reasoning preamble entirely. So the
  caution is about *generative* calls specifically; for an extraction with a schema the constraint is
  strictly cheaper, which is exactly the shape `output_type` marks.

  **The repo's own census agrees the seam was dead and now is not:** `inert-surface-baseline.json`
  moved `capabilities.py` from 2 inert entries to 1 in the same commit, and
  `enum:StructuredOutput.JSON_MODE` **stays** inert — the honest record of the scoping decision to
  gate on `JSON_SCHEMA` only, not an oversight.

  **Three stale `plans[].status` fields corrected in this commit** (that field is authored, never
  derived, so nothing regenerates it): `AG` and `MRI` and `CC` all read `in_progress` while every one
  of their atoms is `done` — `AG` because this atom was its last, `CC` because `CC-6` closed earlier
  today, `MRI` since its fifth atom landed. The atomic-status-sync rail couples `dag.json` to
  per-atom rows, not to this plan-level field.

  **Gates:** `make lint` exit 0 · `test_llm_helpers` **43 passed** (13 new) · with
  `test_inert_surface_baseline` **61 passed** · the agent additionally ran 20 suites naming
  `one_shot_completion` (**670 passed, 5 skipped**), 11 provider/registry/grounding suites (**203
  passed**) and full `make test` (**22,785 passed / 30 skipped / 12 xfailed**). Five falsifications;
  I independently re-ran the capability-gate one — dropping the gate reds
  `test_none_provider_is_sent_nothing` with `assert 'output_type' not in {...}`.

  **Still open, deliberately** (recorded, not fixed): `routing/policy.py:_structured_providers()`
  compares against `declared_capabilities`, which can never contain a structured-output member, so it
  fail-opens to an empty set — pre-existing, and fixing it changes routing ORDER for structured
  queries, a different subsystem with its own tests. And whether `BrandedProviderSpec` should carry
  `structured_output` explicitly is an apps-repo contract question.

### 2026-08-21 — Atom AG-14 (editing a file the agent has not read stops being possible) — DONE

**No `dag.json` row exists for AG-14** and none was invented here — the atom arrived as an owner
brief, and minting a status row for an atom the plan never declared would make the mirrored status
surface lie. This entry is the durable record; the row is the owner's to file.

**The census ran BEFORE the work, as the atom requires** — enforcement over an unsurveyed path is an
outage, and the survey is what made this safe. Two write paths in `agents/native/builtin_tools.py`
(`_t_write_file`, `_t_edit_file`); the five other `_t_*_create` handlers create ENTITIES, not files.
Six modules that mention `write_file`/`edit_file` were each resolved to a verdict: `tool_retrieval.py`
and `tool_prefs.py` are NAME lists (core-tool sets), `self_model_observer.py` is a docstring example,
`chat_runner.py`'s `_WRITE_FILE_TOOLS` only READS the target for a diff chip, `turn_checkpoints.py`
writes checkpoint blobs (never the target), and `handlers/files.py` carries two HUMAN surfaces
(`api_file_write` from the markdown panel, `_write_file_restricted` for uploads) that are not the
agent seam. **One raw child the brief did not name was found by the rail, not by reading:** `_t_bash`
(`sed -i`, `> file`). It cannot be gated on an observed target because it takes a command, not a
path, so it is a DECLARED exemption (`_UNGATEABLE_WRITE_PATHS`) with its reason asserted — the honest
statement of the residual hole rather than a silent omission.

**The gate is expressed ONCE**, at `NativeBuiltinToolProvider.invoke` (`_read_gate_refusal` before
dispatch, `_read_gate_observe_write` after), driven by the `_READ_GATED_WRITE_TOOLS` registry.
Neither write handler references the gate — a rail asserts that, so a third write path inherits the
gate by adding a row rather than by re-implementing it.

**Keyed on content OBSERVED, never on "a read tool was called."** `agents/native/read_gate.py` records
the *projected output string* `read_file` returned — not the file's bytes — plus the sha256 of the
file's FULL bytes and whether either truncation axis fired (the 256 KB byte cap, the 60 000-char
projection). Three checks: an observation exists for that resolved path; its digest still equals the
file's; and the region occurs in a fragment the model was actually shown. Truncation is therefore
honoured by construction — observing bytes 1-2000 does not license editing byte 5000 — and because a
projected read already advertises `tool_result_get`, a slice pulled that way is credited as a further
observed fragment, so the refusal's next action actually works on a file larger than the cap (a slice
of a SUPERSEDED snapshot is not credited). Create-new is ungated; overwriting an existing file needs a
COMPLETE observation, since an overwrite's region is the whole file. Fail closed throughout: an
unreadable target, an expired observation, or an unrecognized operation all refuse.

**DEVIATION (scope, deliberate):** the atom says "observed in this turn". A turn window alone is
weaker than what shipped — a read taken one tool call ago is already stale if another process wrote
in between — so currency is enforced by digest equality, with the turn window layered on top
(`read_gate.begin_turn` is wired at the chat runner's existing `turn_checkpoints.begin_turn` site, so
the two turn notions cannot drift) and a TTL bounding the sessionless/loop callers that never declare
a turn. A landed write also re-observes: the agent's own edit carries its observation forward
(fragments substituted, completeness INHERITED), or consecutive edits would refuse each other while
blaming a third party.

**Eight pre-existing tests drove a write with no prior read** (5 in `test_native_builtin_tools.py`,
3 in `test_turn_checkpoints.py`) and were updated to read first — the surveyed cost of switching the
control on, and incidentally the proof that the gate works through the real tool surface.

**Falsifications (mutate the LIVE line, observe the red, restore from a file copy):** (1) replacing
the content check with the call-count shape ("any read this turn admits the write") reds exactly the
four content-dependent legs — different-file, truncated-region, partial-overwrite and
concurrent-write — while `test_edit_without_reading_is_refused` stays GREEN, which is precisely why a
call-count gate looks enforced; (2) dropping `edit_file` from the registry reds the bypass rail naming
`['edit_file']`; (3) drifting the rail's write-call vocabulary reds its VACUITY assertion instead of
reading clean.
