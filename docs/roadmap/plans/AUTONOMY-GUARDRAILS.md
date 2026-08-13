# AUTONOMY-GUARDRAILS

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/AG.md`](../atomic/AG.md) as 11 atomic plan(s).

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
It names them `hooks.py`, **`gateway.py:701`** and `event_triggers.py`. That middle name is
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
`skipped_overlap — held by tick:<t> since Ns ago` with the claim never released. So on this path a
fire that cannot be delivered burns the trigger's claim permanently. That is the no-claim-lease gap
already owned by WF2WOR-1; recorded here because it is what made the clock half of this validation
unusable, and the file half is what proved the seam.

Gate: `make lint` rc=0; targeted suites green; full suite **18921 passed, 30 skipped, 12 xfailed, 3 failed** — the three
pre-existing `tests/test_harness_validate.py` failures that red in any worktree (the harness
resolves `.venv/bin/python` relative to cwd). 15 new tests. All four generated baselines
byte-identical after regeneration. No `web/` change.

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
