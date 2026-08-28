# PLATFORM-LEGIBILITY

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/PL.md`](../atomic/PL.md) as 7 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Platform Legibility Pack — Self-Description Manifest, First-Party Skill, App Auto Tool-Surfacing, WHAT/WHY/FIX Errors

**Status:** DONE — all five sessions shipped 2026-07-24/25 (S1 manifest + drift, S2 WHAT/WHY/FIX
errors, S3 `pclaw-api` skill + offline reference, S4 app legibility, S5 UI docs + Discover hub +
context provider). Created 2026-07-13 from research synthesis, promoted from backlog. §6 shipped
reshaped by owner direction — see the 2026-07-25 DEVIATION entry in the Execution log and Success
Criterion #7 below.
**Created:** 2026-07-13
**Wave:** 0 — every slice is v2-independent and shippable piecemeal; the manifest + error envelope (Sessions 1-2) pay off every subsequent roadmap session (contributed apps, code loops, workflow authoring) and should land before the build-heavy waves that consume them.
**Depends on:** nothing hard. Federates (does not rebuild) WORKFLOWS-V2's `workflow_manifest` (WF2-R12) when that lands; §7's MCP `get_context` is exposed *externally* only when NEW-10's hardened MCP server ships (in-process today).
**Scope:** make PersonalClaw legible to the agents that build on and drive it — (a) a registry-generated `/api/manifest` with a drift test + typed UI-primitive doc objects behind `ui_search`/`ui_get`; (b) a first-party PersonalClaw SKILL.md + offline API reference shipped in-distribution (Quarkdown's 4/5-vs-0/5 eval as the acceptance bar); (c) app-declared skills seeding through the install chokepoint + one generic tool/action surface auto-generated from declared app route tables, resynced on `/update`; (d) a platform-wide WHAT/WHY/FIX error envelope on everything returned into an LLM session; (e) usage-telemetry-driven capability power-ups on the dashboard; (f) PClaw as context *provider* for external agents (routed-context manifest → per-tool adapters + `get_context`).

---

## Research Integration (2026-07-13)

- **NEW-13 core (a)** (`/api/manifest` generated from actual registries + drift test; typed doc objects for UI primitives; token registry behind `ui_search`/`ui_get`) → §1, §5. Sources: `meta-astryx` (capability manifest from Commander metadata + `manifest.test.mjs` CI drift test; typed `.doc.mjs` objects; 2-tool search/get MCP surface with per-result token budgets + follow-up hints), `cult-ui` (registry-as-protocol; bundled authoring skill).
- **NEW-13 core (b)** (first-party SKILL.md + offline API/tool-signature reference in the distribution; with/without eval as acceptance bar) → §3. Source: `quarkdown` (skill resolved via `doctor get install-dir`, orient-then-drill, exact-signature lookups, mandatory verify loop, published eval 4/5 vs 0/5 first-try).
- **NEW-13 core (c)** (app.json-declared skills auto-registering on install; MCPify-style auto-generation of agent tools/action providers from installed app route tables, resynced on `/update`) → §4. Recon fact honored: the manifest-vs-UI dead-path audit precedent becomes a build step, not a memory note.
- **NEW-13 core (d)** (platform-wide WHAT/WHY/FIX error envelope on every tool/gate/provider error returned into an LLM session) → §2. Source: `harness-engineering-course` (agent-oriented error messages, OpenAI Codex pattern; L10 "converts failures into self-correction"); `meta-astryx` (append-only stable error codes + `suggestions`, "branch on codes, never on prose").
- **NEW-13 amendment (e)** (capability-discovery power-ups: usage-telemetry-driven interactive mini-lessons cycling one untouched capability at a time as a dashboard widget) → §6. Attacks the proven manifest-vs-UI discovery gap from the *user* side.
- **NEW-13 amendment (f)** (PClaw as context provider for external agents: neutral routed-context manifest — rules top, L0 index bottom, scored middle per lost-in-the-middle — rendered into per-tool adapters in PClaw-managed project dirs + an MCP `get_context` tool returning rules + scored tiered memories) → §7. Source: `ai-context-os` (adapter-first router, "adapters are derived, never the source of truth", marker-fenced managed blocks, unloaded-list visibility).

---

## Overview

PersonalClaw's own history is the argument for this plan. The Growth/Minutes sessions proved that contributed apps ship TESTED backend routes no UI or agent ever reaches, and that agents hand-roll UIs when no machine-readable component surface exists (the documented manifest-vs-UI dead-path audit). The `PROVIDER_TYPES` ↔ type-handler parity guard (`test_manifest_types_match_handlers`, the #47 bug class) already demonstrates the fix pattern this plan generalizes: **describe the surface FROM the registry, and make drift a test failure.**

Verified starting points (all paths under `src/personalclaw/` unless noted):

- **Tool aggregation seam exists:** `tool_providers/registry.py:list_all_tools()` already aggregates `ToolDefinition`s (name/description/schema) from every registered `ToolProvider` (native, schedule, artifacts, workflows, memory, subagents + MCP adapters), with per-provider load-failure recording. The manifest generator reads this — it does not invent a second inventory.
- **Tool schemas are typed but hand-scattered:** `validation.py` holds ~40 `ToolSchema` constants; `mcp_core.py:_list_tools()` (line 112) hand-writes tool dicts for `skill_invoke`/`skill_search`/`hook_register`. These are inputs to §1, and the drift test is what keeps them honest.
- **No `/api/manifest` exists** (verified: zero hits in `dashboard/`). HTTP routes register imperatively via `app.router.add_*` in `dashboard/server.py` — enumerable at runtime via aiohttp's route table.
- **Error machinery is half-built:** `ToolResult` already carries `recovery_hints` (TokenJuice appends a `tool_result_get(result_id=…)` hint, `tool_providers/projection.py:project_and_retain`), and the FE `ToolSegment` type already has a `recoveryHints` field (`web/src/pages/chat/chatTypes.ts`). AMBIENT-SURFACES independently specified typed LLM-friendly validation errors for its generative-UI registry. What's missing is the *platform-wide envelope and stable code registry* — §2 supplies it.
- **App-shipped skills have a dead field:** `apps/manifest.py:_KNOWN_FIELDS` lists `skills` as a LEGACY stripped field ("no runtime consumer"). The live precedents to copy are `prompts[]` app-owned seeding (`apps/prompt_seed.py`: idempotent, non-clobbering, removal keyed by the app's own files) and the skill install chokepoint (`skills/marketplace.py:install_guarded` — quarantine, scan, `.pclaw-lock.json`, SEL audit). §4.1 revives the field through BOTH.
- **App route tables are invisible:** first-party app backends (e.g. `apps/growth/backend/server.py:419` `r.add_get("/artifacts", …)`) register routes only inside app code, proxied at `/apps/{name}/api/*` (`apps/backend_runtime.py`). Nothing tells an agent they exist — the exact dead-path the audit keeps refinding. §4.2 makes routes manifest-declared and auto-surfaced.
- **FE has the registry DNA but no doc objects:** `web/src/design/tokenRegistry.ts` is already "the single declarative registry of every tunable"; `web/src/ui/` primitives carry conventions only as comments (the HeaderActions ordering tenet). §5 adds co-located typed doc objects and two retrieval tools.
- **A bundled self-description skill exists but is prose:** `skills/bundled/pclaw-features/SKILL.md` describes capabilities channel-neutrally with NO exact signatures — exactly the "0/5 without exact-signature reference" failure Quarkdown measured. §3 upgrades it.
- **Usage telemetry exists for skills only:** `skills/usage.py:SkillUsageStore` (`.usage.json` sidecar). §6 adds the analogous per-tool counter as a by-product of the manifest.
- **`personalclaw doctor` exists** (`cli.py:214`) — the natural anchor for install-dir/docs-path resolution (§3), mirroring Quarkdown's `doctor get install-dir`.

**Soul guardrail:** this is documentation-as-data for ONE user's machine — a generator, a test, a skill, and two retrieval tools. No docs portal, no versioned API gateway, no OpenAPI toolchain dependency. The power-ups widget proposes lessons; it never auto-enables anything (propose-don't-write).

---

## 1. `/api/manifest` — the self-description endpoint

### 1.1 What it describes, and where each part comes from

`GET /api/manifest` returns one JSON document with `apiVersion: 1` and four sections, each **generated from the live registry that owns it** (never a parallel hand-maintained list):

| Section | Generated from | Notes |
|---|---|---|
| `tools[]` | `tool_providers/registry.py:list_all_tools()` + `mcp_core._list_tools()` | name, provider, description, input schema, `response_type`, `error_codes[]`, 1-2 `examples[]` |
| `routes[]` | aiohttp route table (`app.router.routes()` walked at startup) | method, path, handler docstring summary, `agent_callable` flag; explicit `_MANIFEST_EXCLUDE` set for internal/static routes |
| `app_surfaces[]` | installed app manifests' declared route tables (§4.2) | per enabled app: routes + generated tool names |
| `providers{}` | the extension registry (`providers/registry.py:get_provider_registry()`) + `PROVIDER_TYPES` | type taxonomy, registered providers, enabled/error state |

The two facts registries don't carry — `response_type` discriminators and `examples` — live in a small `manifest_meta.py` allowlist map, exactly Astryx's `JSON_SUPPORTED`/`RESPONSE_TYPES` solution for what Commander metadata lacked.

### 1.2 The drift test (the point of the whole section)

`test_api_manifest_drift.py`, following the `test_manifest_types_match_handlers` precedent:

- Every tool returned by `list_all_tools()` + `mcp_core._list_tools()` MUST have a manifest entry with a non-empty description and at least one example → adding a tool without describing it **fails the suite**.
- Every registered HTTP route MUST be in the manifest or in `_MANIFEST_EXCLUDE` (with a one-line reason) → the Growth-style dead-path (route exists, nothing points at it) becomes a red test instead of a later audit finding.
- Every `error_code` referenced by a tool entry MUST exist in the §2 code registry.

### 1.3 Typed envelope discipline

Manifest-listed tool responses adopt the `{type, data}` discriminator convention incrementally (e.g. `task.detail`, `skill.search.results`) — new tools MUST ship it; existing tools migrate opportunistically. **Overlap honored:** WORKFLOWS-V2 (WF2-R12) already owns `workflow_manifest` + the workflow tool error codes (`ERR_UNKNOWN_NODE`, …); when it lands, `/api/manifest` *embeds* its output under `tools[].workflow_manifest_ref` rather than regenerating the node taxonomy. Until then the workflow tools appear as ordinary `tools[]` entries.

---

## 2. Platform-wide WHAT/WHY/FIX error envelope

### 2.1 The envelope

```python
# errors.py (new, tiny)
@dataclass(frozen=True)
class AgentError:
    code: str        # stable, append-only: "ERR_MODEL_UNRESOLVED", "ERR_HOOK_PROVIDER_UNKNOWN", ...
    what: str        # what failed, with the concrete value: "provider 'Bedrock' cannot build for use_case 'stt'"
    why: str         # the mechanism: "the pinned active ref names a provider absent from config.json"
    fix: str         # the exact next action: "rebind stt in Settings → Models, or call model_bind(...)"
    suggestions: list[str] = ()   # did-you-mean candidates (nearest tool/provider/skill names)

ERROR_CODES: dict[str, str] = {...}  # code → meaning; APPEND-ONLY (test asserts no removal/redefinition)
```

Rendered to the LLM as three labeled lines (`WHAT: … / WHY: … / FIX: …`) — the format the harness-engineering course measured converting failures into self-correction loops — and carried structurally so the FE and external clients can branch on `code`, never on prose.

### 2.2 Where it attaches (existing seams, no new dispatch layer)

- **Tool errors:** `ToolResult` (`tool_providers/base.py`) gains an optional `agent_error: AgentError`; the existing `recovery_hints` field becomes the `fix`/`suggestions` carrier it always wanted to be. Native builtin tools (`agents/native/builtin_tools.py`) and the MCP adapter populate it at their catch sites.
- **Action-provider errors:** `ActionResult` (`action_providers/base.py:ActionResult`) gains the same optional field; the three dispatch seams (`hooks.py`, `gateway.py`, `event_triggers.py`) wrap uncaught provider exceptions into a generic envelope so **app-contributed providers inherit it without knowing it exists** (the AUTONOMY-GUARDRAILS §1.2 enforcement-placement pattern).
- **Provider-resolution errors:** `ProviderResolutionError` (`providers/provider_bridge.py`) already carries the right doctrine ("block, don't silently fall back") — it gains the envelope so a background turn that dies on a stale pin tells the agent which use-case to rebind.
- **Gate/validation errors:** `validation.py:validate_tool_args` failures and hook create/update rejections (including the `ALLOWED_HOOK_PROVIDERS` rejection at `src/personalclaw/validation.py`) return coded envelopes with `suggestions` = the allowed set — today that rejection is exactly the kind of opaque failure that burns an agent turn.
- **FE:** `ToolSegment.recoveryHints` already renders; the tool-card error state adds the WHAT/WHY/FIX rows (no new segment type).

**Disposition:** AMBIENT-SURFACES' generative-UI validation errors (`unknown-component`, `missing-required`) and WORKFLOWS-V2's spec-ingestion codes are *instances* of this convention, defined in their own plans; this plan owns the shared `AgentError` type + the append-only code-registry test they both cite. Retrofit is incremental — the drift test only requires codes for manifest-listed tools' *declared* errors, not exhaustive coverage on day one.

---

## 3. First-party PersonalClaw skill + offline reference

### 3.1 The artifacts (shipped in-distribution)

- `skills/bundled/pclaw-api/SKILL.md` — the driving skill for any agent operating PersonalClaw (external Claude Code sessions, PClaw's own code loops working on contributed apps, subagents). Encodes Quarkdown's measured methodology: **orient-then-drill** (read the reference index first, then only the relevant sections), **exact-signature lookups** ("never guess a tool signature; fetch it from the manifest — hallucinated params are the dominant failure"), a **mandatory verify loop** (after driving a mutating endpoint, read the entity back), and **explicit negative scope** (don't hand-roll UI when §5 tools exist; don't bypass `/api/apps/{name}/update` by editing installed copies; don't call routes marked `agent_callable: false`).
- `docs/agent-reference/` (in-distribution, offline) — the API/tool-signature reference **generated from the same `/api/manifest` generator at build time** (one source, two renderings — the Astryx CLI-as-truth rule), plus a hand-written index page and the repo-gotcha invariants that keep resurfacing (installed-app sync, `static/dist` symlink, venv interpreter).
- Resolution: `personalclaw doctor` (existing subcommand, `cli.py:214`) gains a `--paths` output including the reference dir, so an external agent can locate the docs from the binary alone — the `doctor get install-dir` pattern verbatim.
- The existing `skills/bundled/pclaw-features/SKILL.md` (prose, channel-neutral, user-facing) stays for "what can you do" questions; `pclaw-api` is the operator twin and cross-references it.

### 3.2 The acceptance bar (non-negotiable)

Quarkdown's published eval shape, sized personally: **5 representative driving tasks** (create+wire a trigger via API, add a knowledge item and verify retrieval, drive an app backend route, bind a model to a use case, author+install a skill), each run **with and without** the skill+reference in context, fresh context-free sessions (the Astryx vibe-test invariant: never leak expected answers, only the context varies). Scored on first-try-success and *silent misses* (task "completed" but verification shows it didn't take). **The slice does not ship until with-skill ≥4/5 first-try with 0 silent misses.** The task battery is checked in as the regression harness for future manifest changes.

---

## 4. App legibility — declared skills + auto-surfaced route tools

### 4.1 App-declared skills, seeded through the chokepoint

**Backlog said "auto-register on install"; the real seams are `enable` + `install_guarded` — adapted accordingly.** The legacy `skills` manifest field (currently stripped, `apps/manifest.py:_KNOWN_FIELDS`) is revived as a typed field: `skills: [{path: "skills/my-skill/"}]`, paths relative to the app dir — mirroring `prompts[]`.

- **On enable** (and the startup bundled-discovery path): each declared skill dir is installed via `skills/marketplace.py:install_guarded` at the app's trust tier (first-party/community per the existing app trust ledger) — quarantine, `scan_dir`, DANGEROUS-refuses-always, `.pclaw-lock.json` with per-file sha256, SEL audit. **An app skill never bypasses the supply-chain gate just because it arrived inside an app.** Idempotent + non-clobbering: an existing user-edited skill of the same name is left untouched (the `prompt_seed.py` contract).
- **On disable/uninstall:** remove only skills this app shipped (keyed by the app's own declaration + lock provenance), never a user's skill — again the prompt-seed removal contract.
- **On `/update`** (`POST /api/apps/{name}/update`): re-seed; a changed skill re-passes the scan.

### 4.2 Route-table tool surfacing (MCPify, adapted to the real constraints)

App backends declare their agent-callable surface in `app.json` (readable **without executing app code** — the manifest module's stated design rule):

```jsonc
"backend": {
  "entryPoint": "backend/server.py", "port": "auto",
  "routes": [
    {"op": "list_artifacts", "method": "GET",  "path": "/artifacts", "summary": "...", "params": {...}, "agentCallable": true},
    {"op": "create_digest",  "method": "POST", "path": "/digests",   "summary": "...", "body": {...},   "agentCallable": true}
  ]
}
```

- **One generic ToolProvider, not N generated ones:** a new `AppRoutesToolProvider` registered via `tool_providers/registry.py:register_provider` (beside `create_native_provider` et al.) exposes `app_<name>_<op>` tools for every enabled app's `agentCallable` routes, invoking through the **existing reverse proxy** (`/apps/{name}/api/*`, `backend_runtime.py`) under the `LOOPBACK_INTERNAL` egress stance. Resynced on enable/disable/`/update` (the update handler already re-reads the manifest).
- **One static action provider, honoring the frozenset:** `ALLOWED_HOOK_PROVIDERS` (`src/personalclaw/validation.py`) is a static frozenset — per-app generated action providers cannot be enumerated there. So exactly ONE new action provider ships: `call-app-route` (core-native, `action_providers/`), whose `action_config` selects `{app, op, args}`; **its single name is added to `ALLOWED_HOOK_PROVIDERS`** and it refuses ops not declared `agentCallable`. Hooks/crons/event-triggers can then hit any declared app route with zero per-app registration.
- **Drift closes the audit loop:** a startup/`/update` check compares the app's *declared* routes against the backend's live route table (probe the app's `/health`-style introspection or match on first proxy 404) and files a warning notification for undeclared or dead-declared routes — the manifest-vs-UI dead-path audit as a build step. App-route tools and declarations flow into `/api/manifest` §1's `app_surfaces[]`.

---

## 5. UI-primitive doc objects + `ui_search`/`ui_get`

- **Typed doc objects, co-located:** each `web/src/ui/` primitive gains a `<Name>.doc.ts` exporting `{name, keywords[], description, props[], bestPractices: [{guidance: boolean, description}], anatomy[]}` — the Astryx `.doc.mjs` shape, machine-readable Do/Don't included. The conventions currently living as comments become data: the HeaderActions ordering tenet, SidePanel `urlKey` contract, WorkbenchLayout skeleton, DashboardLive signals-not-payloads rule, token-lint ratchet ("use tokens, not magic values").
- **Token registry rides along free:** `design/tokenRegistry.ts` is *already* the typed registry — the build step serializes `TOKENS` + all doc objects into `web/dist/ui-docs.json`; the gateway serves it (it already serves `static/dist`).
- **Two tools, per the measured pattern:** `ui_search(query)` (keyword index inverted from `keywords[]`, brief results with a per-result token budget and a follow-up `hint: "call ui_get('HeaderActions') for full props"`) and `ui_get(name, section?)`. They live on a small `UiDocsToolProvider` registered via `tool_providers/registry.py` — surfaced to app-building agents and code loops through the normal tool path, and listed in `/api/manifest`. Deliberately two tools, not a tool per component.
- **Drift test (FE side):** a vitest test asserts every exported `ui/` primitive has a doc object and every doc object's `props[]` matches the component's exported prop type — the token-lint-ratchet enforcement pattern applied to docs.

---

## 6. Capability-discovery power-ups (dashboard)

- **Telemetry inputs (all existing or free by-products):** `skills/.usage.json` (`SkillUsageStore`), a new per-tool invocation counter (same sidecar pattern, `~/.personalclaw/tool_usage.json`, incremented at the `list_all_tools()`-fed invoke path — best-effort like skill usage), and the §1 manifest as the *denominator* — "capabilities that exist" minus "capabilities you've touched" is now computable for the first time.
- **The widget:** `pages/dashboard/widgets/PowerUps.tsx`, hard-imported into `DashboardPage.tsx` (**recon-honored: there is NO widget registry — adding a tile = editing DashboardPage.tsx**, and this plan does not build one). One card cycling ONE untouched capability at a time: a two-sentence mini-lesson + a "try it" deep link (hash route) + dismiss. Data from a new `GET /api/legibility/power-ups` endpoint on the SLOW_POLL cadence via the existing `DashboardLive` slices.
- **Propose-don't-write:** mini-lessons are deterministic templates over manifest entries (optional `one_shot_completion(use_case="background")` polish); dismissals persist (`entity_settings` pattern); the widget never toggles or configures anything on the user's behalf. Kill switch: `legibility.power_ups` config flag.
- **Disposition:** LEARNING-FLYWHEEL owns surfacing *into agent context* (LEARN-R7 slot allocator, composer chips); this widget surfaces *to the user* on the dashboard and shares only the usage stores. No overlap in mechanism.

## 7. PClaw as context provider for external agents

- **Neutral routed-context manifest first, adapters second** (the ai-context-os doctrine: adapters are derived, never canonical). A `context_router.py` assembles, per PClaw-managed project (projects already own `projects/<id>/context/` dirs — `tasks/hierarchy.py`): hard rules/directives at the **top**, scored mid-tier content in the **middle** (relevant memories via the existing recall path, surfaced skills index, knowledge-item pointers), and an L0 one-liner **catalog of what was NOT included** at the bottom with a retrieval affordance — lost-in-the-middle positioning by construction.
- **Per-tool adapters with managed markers:** rendered into `CLAUDE.md` / `AGENTS.md` / `.cursorrules` inside the project's bound `workspace_dir`, fenced by `<!-- PCLAW:START -->`/`<!-- PCLAW:END -->` markers, replaced in place on regeneration (never appended twice, user content outside the fence untouched — the Astryx `agent-docs.mjs` pattern). Regeneration: manual button on the project page + on project-context change; opt-in per project (`legibility.context_adapters` default off — writing files into user project dirs is consent-gated).
- **MCP `get_context` tool:** registered on the in-process MCP core surface (`mcp_core.py`), tool description embedding the protocol ("call at the start of every task; returns rules + scored tiered memories + a list of available-but-unloaded items you can request"). **Recon-honored: `mcp_core.py` serves tools in-process only** — external exposure arrives when NEW-10's fail-closed MCP server lands and curates this tool into its read-only subset; this plan ships the tool, not the transport.
- **MEMORY vs KNOWLEDGE boundary (explicit):** the router *reads* both but never conflates them in output — memory-derived content (lessons, preferences; `memory.db`) renders under a "how this user works" heading; knowledge items (the user's documents/files; `knowledge.db`) render as titled *pointers* with retrieval instructions, never inlined bodies. Adapters are derived files in project dirs — nothing here writes to either store, and adapter regeneration never feeds back into memory.

---

## 8. Provider-Fidelity Wiring (where each piece plugs in)

- **No new provider TYPE.** `PROVIDER_TYPES` (`apps/manifest.py:914`) is untouched; nothing here registers through `_TypeHandler`s. The two new tool surfaces (`AppRoutesToolProvider`, `UiDocsToolProvider`) register as core `ToolProvider`s via `tool_providers/registry.py:register_provider` — the same path as the native/schedule/artifacts providers.
- **Action provider:** exactly one — `call-app-route` (§4.2) — added to `ALLOWED_HOOK_PROVIDERS` (`src/personalclaw/validation.py`) in the same commit that registers it, or hook create/update rejects it. Its rejection message is itself a §2 envelope with the allowed set as `suggestions`.
- **App manifest:** `skills` moves out of the legacy-stripped list into a typed field; `backend.routes[]` is a new typed sub-field — both with unknown-field-tolerant parsing (an old gateway reading a new manifest degrades to warnings, never rejects — the tolerant-parser doctrine).
- **Config — FOUR wiring points for every new field:** new `legibility` fields (`power_ups: bool = True`, `context_adapters: bool = False`) wired through (a) dataclass fields with `_meta(label, help)` (schema reachability tests enforce), (b) `AppConfig.load()`'s explicit field-by-field mapping (`config/loader.py` — omission = silent drop), (c) `to_dict()` (new section added at the `loader.py:1930` block), (d) `_EDITABLE_CONFIG` (`dashboard/handlers/core.py:363`) + `web/src/lib/api.ts` + a Settings toggle for the runtime-editable pair.
- **Skill installs:** ONLY via `install_guarded` (§4.1) — app-shipped skills inherit quarantine/scan/lock/SEL like any marketplace skill; trust tier from the app's ledger.
- **Egress:** `AppRoutesToolProvider`/`call-app-route` invoke through the reverse proxy on loopback — no new egress surface; anything else uses `net.fetch` (nothing here fetches externally).
- **SEL:** app-skill installs (already audited by `install_guarded`), route-declaration drift warnings, and adapter-file writes into user project dirs log to `sel.py`.
- **FE:** manifest/power-ups endpoints added to the flat `api` object (`web/src/lib/api.ts` — high merge-conflict surface, noted); PowerUps widget hard-imported per the no-registry reality; doc-object drift test joins the vitest suite beside the token-lint ratchet.

---

## 9. Implementation Effort

**~5 sessions** (backlog estimated ~4 for cores a-d; amendments e-f add one).

- **Session 1 — manifest + drift (§1):** `/api/manifest` generator over `list_all_tools()` + route table + extension registry; `manifest_meta.py` response-type/examples map; `test_api_manifest_drift.py`; FE `api.manifest()`.
- **Session 2 — error envelope (§2):** `errors.py` `AgentError` + append-only `ERROR_CODES` + no-redefinition test; attach at `ToolResult`/`ActionResult`/the three dispatch seams/`ProviderResolutionError`/validation rejections; FE tool-card WHAT/WHY/FIX rows.
- **Session 3 — first-party skill + reference (§3):** `pclaw-api` SKILL.md; build-time `docs/agent-reference/` rendering from the manifest generator; `doctor --paths`; the 5-task with/without eval — **session doesn't close below 4/5 + 0 silent misses.**
- **Session 4 — app legibility (§4):** typed `skills` field + `install_guarded` seeding + removal contract; `backend.routes[]` + `AppRoutesToolProvider` + `call-app-route` (+ `ALLOWED_HOOK_PROVIDERS`); resync on enable/disable/`/update`; declared-vs-live drift warning; retrofit Growth + Minutes manifests as the proving pair.
- **Session 5 — UI docs + power-ups + context provider (§5-§7):** `.doc.ts` objects for the `ui/` kit + `ui-docs.json` build step + `UiDocsToolProvider` (`ui_search`/`ui_get`) + FE drift test; `tool_usage.json` counter + power-ups endpoint/widget; `context_router.py` + marker-fenced adapters + in-process `get_context`; config through the four points; as-a-user validation sweep.

Each session ships independently; Sessions 1-2 alone make every subsequent roadmap session cheaper.

---

## 10. Risks

| Risk | Mitigation |
|---|---|
| Manifest becomes a second source of truth that drifts | It is GENERATED — the only hand-maintained parts (`manifest_meta.py`, `_MANIFEST_EXCLUDE`) are exactly what the drift test audits; an undescribed tool/route fails the suite |
| Envelope retrofit sprawls across hundreds of catch sites | Scope discipline: envelope required only for manifest-declared error codes + the seam-level generic wrap; everything else migrates opportunistically; prose errors keep working (envelope is additive on `ToolResult`/`ActionResult`) |
| App-declared routes lie (declared but dead, or undeclared but live) | The §4.2 declared-vs-live check at enable/update files a warning notification — dishonest manifests are visible, and `agentCallable` tools 404 loudly through the proxy with a §2 envelope |
| App-shipped skill smuggles a dangerous payload | Non-risk by construction: seeding goes through `install_guarded` — DANGEROUS refuses always regardless of tier; the app enable proceeds with the skill skipped + notified |
| `ui-docs.json` staleness vs live components | The vitest drift test (doc object per export, props parity) fails the FE build; the JSON is regenerated by the same `npm run build` that produces the chunks it documents |
| Adapter files annoy users or fight other tools' CLAUDE.md content | Opt-in per project (config default off); marker-fenced replace-in-place never touches content outside the fence; regeneration is user-triggered or change-triggered, never on a timer |
| Power-ups widget becomes nagware | One capability at a time, dismiss persists forever per capability, global config kill switch, no LLM call required for the deterministic template path |
| Token-count bloat from two more tool providers in agent context | `ui_search`/`ui_get` + app-route tools are exactly the surfaces NEW-22 (dynamic tool-group activation) would gate; until then, app-route tools surface only for sessions that opt in via the existing tool-prefs path, and the ui-docs tools only for app-building/loop contexts |
| Four-wiring-points silent config drop | Explicit checklist in §8; schema reachability tests enforce (a) |

---

## Success Criteria

1. `GET /api/manifest` describes every registered tool (native + MCP-core + app-route) and every non-excluded HTTP route with description, schema, response type, error codes, and an example — and adding a new tool without a manifest description **fails the test suite** (drift test red).
2. The 5-task with/without eval passes the Quarkdown bar: with `pclaw-api` SKILL.md + offline reference in context, a fresh context-free agent achieves ≥4/5 first-try success with 0 silent misses (vs a measured without-skill baseline), and the battery is checked in as a regression harness.
3. Every error surfaced into an LLM session from a manifest-declared failure path carries WHAT/WHY/FIX + a stable code; the code registry test proves codes are append-only; an agent hitting the `ALLOWED_HOOK_PROVIDERS` rejection receives the allowed set as `suggestions` and self-corrects in the next turn.
4. Installing/enabling an app whose `app.json` declares skills results in those skills scanned, locked (`.pclaw-lock.json`), and live in the skill index; disabling removes exactly them; a user-edited same-name skill survives untouched; a DANGEROUS-verdict skill is refused with the app still enabling.
5. With Growth's manifest declaring its route table, `app_growth_list_artifacts`-style tools appear in `list_all_tools()` and `/api/manifest`, a hook can fire `call-app-route` against a declared op, `/update` resyncs a changed route table, and a declared-but-dead route produces a drift warning notification — the manifest-vs-UI dead-path class is caught by machinery, not by audit sessions.
6. An app-building agent can `ui_search("header buttons overflow")` → get a budgeted brief with a follow-up hint → `ui_get("HeaderActions")` → receive props + the ordering tenet as machine-readable bestPractices; the FE drift test fails if a `ui/` primitive ships without a doc object.
7. ~~The dashboard shows one untouched-capability power-up with a working "try it" deep link; dismissing it persists; disabling `legibility.power_ups` removes the widget; nothing is ever auto-enabled.~~ **SUPERSEDED by the owner-directed §6 reshape (2026-07-25).** The tool-derived power-up widget and its `legibility.power_ups` flag were deleted outright — the tool surface is an implementation detail users are never meant to drive by hand, so "you haven't called `knowledge_add` yet" is noise. As shipped: a **Discover page** (`#/discover`, reachable from the command palette) lists hand-authored tips for user-facing *areas*, each deep-linking to the page that owns it, with a rotating spotlight on the dashboard linking in. Tips hide on explicit dismiss (forever) and auto-hide once the area is used. Nothing is ever auto-enabled — that part held. Full rationale in the Execution log.
8. For an opted-in project, a marker-fenced PClaw block appears in the project dir's CLAUDE.md with rules top / L0 unloaded-catalog bottom, regenerates in place without duplicating, and never modifies content outside the markers; the in-process `get_context` tool returns rules + tiered memories + the unloaded list, with memory-derived and knowledge-derived content under distinct headings (boundary preserved).

---

## Execution log

### 2026-07-24 — S1 (§1) manifest + drift — DONE

Shipped the self-description endpoint and its drift guard:

- **`src/personalclaw/manifest.py`** — the generator. `build_manifest(app=None)` →
  `{apiVersion, tools, routes, app_surfaces, providers}`. `_tools_section()` reads the
  ONE aggregation seam (`list_all_tools()`), skips the per-install `mcp` fan-in, and
  enriches each tool with the response-type discriminator + examples from `TOOL_META`.
  `_routes_section(app)` walks the live aiohttp route table (filtering the HEAD
  auto-companion + static `_handle`), applies `is_excluded_route`, and marks
  `agent_callable` for non-ws `/api/*`. `_providers_section()` renders the extension
  taxonomy + registered instances. `app_surfaces` is an empty (shape-stable) list until
  S4 populates it. `API_VERSION = 1` bumps only on a SHAPE change.
- **`src/personalclaw/manifest_meta.py`** — the one hand-maintained input the drift test
  audits: `TOOL_META` (all 57 tools of the union → `{response_type, error_codes: [],
  examples}`, every example arg schema-faithful), `MANIFEST_EXCLUDE` (8 canonical
  non-`/api` route keys, each with a one-line reason), and the `canonical_route()` /
  `is_excluded_route()` helpers.
- **`src/personalclaw/dashboard/handlers/manifest.py`** + **`dashboard/server.py`** —
  `GET /api/manifest` served live from `build_manifest(request.app)` (one source, two
  renderings — the live walk here, the S3 offline reference later).
- **`tests/test_api_manifest_drift.py`** — 8 tests: every live tool has a faithful
  `TOOL_META` entry (real params only), no stale entries, shape check; every non-`/api`
  route is excluded with a reason (AST walk, house route-handler precedent — no boot),
  no stale exclusions; the canonical-space leak regression; `canonical_route` unit; and
  the (vacuous-until-S2) declared-error-codes guard.
- **`web/src/lib/api.ts`** — `Manifest`/`ManifestTool`/`ManifestRoute`/`ManifestProvider`
  types + `api.manifest()`.

**DISCOVERY — canonical-path leak (fixed in-slice).** Found by live-driving the dev
gateway (isolated `.dev-home`, tokenless loopback): aiohttp's `resource.canonical`
reports a `{name:regex}` segment with the regex stripped (`/apps/{name}/api/{tail:.*}`
→ `/apps/{name}/api/{tail}`), but `MANIFEST_EXCLUDE` was keyed on the source form. So
`is_excluded_route` matched the AST walk (source vs source) yet FAILED at runtime, and 2
app-proxy routes leaked into the live `/api/manifest` — precisely the two-rendering
divergence this slice exists to prevent, invisible to a source-only test. Fix: added
`canonical_route()` and applied it consistently in the exclusion keys, the live walk, and
the AST test; added `test_excluded_routes_dont_leak_into_the_live_walk` +
`test_canonical_route_strips_regex` as regressions. Re-validated live: 498 routes, 0
non-`/api` leaked; 57 tools; 26 providers.

**Gate:** `make lint` clean (black/isort/flake8/mypy); `make test` 7710 passed / 28
skipped / 13 xfailed; web typecheck + test (225) + build all green. No E1–E6 blocker.
Committed clean-break under the pre-1.0 banner (no gates/migrations — this slice adds no
persisted state). Local branch `feature-platform-legibility`, unpushed.

### 2026-07-25 — S2 (§2) WHAT/WHY/FIX error envelope — DONE

Shipped the platform-wide `AgentError` carrier + its append-only registry, attached at
exactly the seams §2.2 names — no new dispatch layer:

- **`src/personalclaw/errors.py`** (new, cycle-free — zero personalclaw imports) — the
  `@dataclass(frozen=True) AgentError{code, what, why, fix, suggestions}` with
  `render()` (the `WHAT:/WHY:/FIX:` + optional `DID YOU MEAN:` lines fed into the model)
  and `to_dict()` (the structural carrier for the FE card + external clients). Plus
  `ERROR_CODES` seeded with the 4 codes this slice's seams raise:
  `ERR_TOOL_ARG_INVALID`, `ERR_MODEL_UNRESOLVED`, `ERR_HOOK_PROVIDER_UNKNOWN`,
  `ERR_ACTION_PROVIDER_FAILED`. The module docstring pins the §2.2-vs-AgentError
  boundary (HTTP `lowercase_snake` on the wire vs `ERR_UPPER_SNAKE` into the session —
  orthogonal by convention AND by surface, so they never collide).
- **Tool seam** — `ToolResult` (`tool_providers/base.py`) gains optional
  `agent_error: AgentError`; `format_tool_result` (`agents/native/tools.py`) renders
  `agent_error.render()` in place of the bare error, hints appended after; the native
  runtime meta serializer (`runtime.py:~954`) carries `agent_error.to_dict()` on failure.
- **Action-provider seam** — `ActionResult` (`action_providers/base.py`) gains the same
  optional field; a module-level `provider_failure(provider_name, exc) → AgentError`
  (`ERR_ACTION_PROVIDER_FAILED`) wraps uncaught provider exceptions at all three dispatch
  seams (`hooks.py`, `gateway.py`, `event_triggers.py`) so **app-contributed providers
  inherit the envelope without knowing it exists**. At the fire-and-forget
  `event_triggers` seam (no result surface) the coded envelope becomes the
  `logger.warning(...render())` line — legible where it used to be an opaque debug
  traceback.
- **Provider-resolution seam** — `ProviderResolutionError`
  (`providers/provider_bridge.py`) gains an optional `agent_error`; its `render()` becomes
  the exception message when present (else the human message survives as fallback), so a
  background turn that dies on a stale/absent pin tells the agent which use-case to rebind
  (`ERR_MODEL_UNRESOLVED`, both the stale-pin and no-provider raises).
- **Gate/validation seam** — `ValidationError` gains an optional `agent_error`;
  `FieldSpec` gains `enum_error_code` (default `ERR_TOOL_ARG_INVALID`) so the allowed-set
  branch raises a coded envelope with `suggestions` = the allowed set. The
  `HOOK_CREATE/UPDATE` `provider` specs override it to `ERR_HOOK_PROVIDER_UNKNOWN`. The
  message reaches the model verbatim through `mcp_shared.call_tool_with_logging`'s
  `f"Error: {e}"` (the exception str IS `render()`), so the wiring is live, not dead-ended.
- **FE** — `chatTypes.ts` gains `ToolSegment.agentError` (+ an `AgentError` interface) and
  wires `m.meta?.agent_error` in both merge + new-segment paths; `chat_runner.py` spreads
  `agent_error` into the `tool_result` WS payload and persists it to message meta;
  `ChatPage.tsx` threads it in `case 'tool_result'`; `ToolCard.tsx` renders a
  danger-bordered What/Why/Fix `<dl>` + did-you-mean suggestion pills above the recovery
  hints — no new segment type.

**Append-only enforcement.** `tests/test_error_codes_append_only.py` freezes the 4
released codes + meanings in a `_RELEASED` baseline (a deliberate copy, not a subset
import — it detects an in-place reword) and asserts: no removal, no meaning change, the
`ERR_UPPER_SNAKE` convention (regex), no collision with the §2.2 `lowercase_snake` space,
and every code has a non-empty meaning. `test_agent_error_envelope.py` covers
render/to_dict/frozen, envelope-over-bare-error + envelope-then-hints + fallback,
`provider_failure`, `ProviderResolutionError` render-is-message, and the hook-provider /
generic-enum code selection. The S1 drift test's `test_declared_error_codes_exist` guard
is now non-vacuous (imports the real `ERROR_CODES`); per §2 line 99, per-tool
`error_codes` retrofit stays incremental — `TOOL_META` entries remain `[]` until a tool
declares one, and the guard enforces the moment one is added.

**DEVIATION — updated 4 pre-existing assertions (root-caused, not weakened).**
`test_active_selection_naming_unknown_provider_raises_immediately`, `test_string_allowed`,
`test_learn_add_bad_category`, `test_learn_invalid_category` pinned the OLD prose
(`"isn't available"`, `"must be one of"`) that the envelope deliberately replaces —
exactly the S2 deliverable. Grep confirmed NO production code branches on that prose (only
the raise sites themselves), so this is a documented behavior change, not a test to keep.
Each assertion was re-pointed at the STRONGER new contract (the offending value is still
named, plus `agent_error.suggestions` carries the allowed set / `ERR_MODEL_UNRESOLVED` +
the fix names rebind-or-install), never softened to green.

**Boundary held.** No route handler emits `AgentError`; §2.2 route-error responses are
untouched. Clean-break under the pre-1.0 banner (no gates/migrations — no persisted-state
shape change; the FE reads `meta.agent_error` defensively).

**Gate:** `make lint` clean (black/isort/flake8/mypy — 897 files, 457 source files);
targeted pytest (20, the two new suites) green; `make test` 7730 passed / 28 skipped / 13
xfailed; web typecheck + test (225) + build all green. No E1–E6 blocker. Local branch
`feature-platform-legibility-s2` (off S1's branch), unpushed.

### 2026-07-25 — S3 (§3) `pclaw-api` skill + offline reference + eval gate — DONE

Shipped the operator-facing half of legibility: the skill that teaches the driving
methodology, the offline reference it points to, and the eval battery that proves the
pair works — one source, two renderings from the S1 manifest generator.

- **`src/personalclaw/manifest_reference.py`** (new) — the build-time renderer. Reuses the
  S1 drift-test seam (clear `tool_reg._providers` + `prov_reg._registry`, register every
  `BUNDLED_DIR` native manifest with a `.provider`, then `build_manifest(app=None)`) so
  tools + providers render WITHOUT booting a gateway. Routes come from a static AST walk of
  `dashboard/*.py` (the design rule `manifest.py` states — booting has security-critical
  startup side effects: extension load, binding migration), with a global name→docstring
  index resolving every handler reference (bare `api_x`, `handlers.api_x`, `_up.api_x` — the
  callable's final identifier). `_ROUTE_SIG_PREFIX`/`_clean_summary` strip a docstring's
  leading `GET /api/foo —` restatement (the markdown already prints method+path).
  `render_reference()` → `{filename: markdown}` deterministic (sorted, no timestamps);
  `reference_dir()` resolves via `importlib.resources` (wheel/editable/source);
  `python -m personalclaw.manifest_reference` regenerates.
- **`src/personalclaw/reference/`** (new subpackage) — the GENERATED `index.md` (orient-then-
  drill map + repo gotchas + what-NOT-to-do), `tools.md` (57 tools / 10 providers, full input
  schemas + worked JSON examples), `routes.md` (424 agent-callable routes of 426), `providers.md`
  (taxonomy + 26 registered). Shipped via `pyproject.toml` package-data (`reference/*.md`).
- **`src/personalclaw/skills/bundled/pclaw-api/SKILL.md`** (new) — the operator twin of
  `pclaw-features`: orient-then-drill, never-guess-copy-it, the mandatory verify-after-mutate
  loop (read the entity back; a silent miss is a failure), branch-on-`code` error-envelope
  reading (the S2 deliverable), 5 worked patterns, and scope guardrails. Discoverable through
  the native marketplace (audit=pass).
- **`personalclaw doctor --paths`** (`cli.py` + `cli_doctor.py::_doctor_paths`) — prints
  tab-separated `key<TAB>path` for reference / config / skills / install, so an external agent
  locates the reference from the installed binary alone (the `doctor get install-dir` pattern).
- **`tests/test_agent_reference.py`** (new, 7) — byte-compares the checked-in reference against
  a fresh render (the drift guard — a tool/route added without its `TOOL_META`/route entry
  reddens the build), asserts the four files, prefix-stripping, provider coverage, valid-JSON
  examples, and the skill's cross-references + `doctor --paths` resolution.
- **`tests/eval_pclaw_api_battery.py`** (new, 3 + `score_answers`) — the checked-in 5-task
  regression harness the plan requires: the task prompts, the code-verified `ANSWER_KEY`, the
  scorer (right tool/route + exact params + a verify step = correct; right action minus verify
  = silent miss), and a test asserting the key still matches the LIVE manifest (a signature
  change breaks THIS battery, forcing reference + key to regenerate together).

**Eval gate PASSED (§3.2 / §9 Session 3 — the ship blocker).** Ran the with/without eval on
fresh context-free subagents. The **with** arm (skill + reference in context, forbidden from
grepping the repo) scored **5/5, 5/5, 5/5** first-try, 0 silent misses — bar cleared on all
three. The **without** arm (one-paragraph description only) scored **2/5 and 1/5**, failing on
exactly the invented signatures the reference exists to kill: `schedule_create`/`schedule`
instead of `hook_register`; a fabricated `PUT /api/models/bindings/chat` + `{model, provider}`
body instead of the real `PUT /api/models/active/{use_case}` + `{models:[...]}`; and
`skill_create(name, description, content)` instead of `skill_remember(title, body)`. The
1–2 → 5 lift is the measured value of the slice.

**DEVIATION — reference ships as the `personalclaw.reference` subpackage, not the plan's
literal `docs/agent-reference/`.** Repo-root `docs/` does not ship in a wheel, and §3.1's whole
point is that an EXTERNALLY-installed agent reads exact signatures — so the docs must be
package-data resolvable via `importlib.resources`, which `doctor --paths` surfaces from the
installed binary. Same content, same generator, same drift discipline; only the on-disk home
moved to where a `pip install` can find it. (Owner-approved during the session.)

**Gate:** `make lint` clean (black/isort/flake8/mypy — 901 files, 459 source files); targeted
pytest (18 — the eval battery + reference + S1 drift) green; `make test` 7737 passed / 28
skipped / 13 xfailed; `web/` untouched (no frontend surface — the skill/reference are backend +
CLI), so the web gate is N/A. CHANGELOG `Added` entry landed. No E1–E6 blocker. Clean-break
under the pre-1.0 banner (the reference is generated build artifact, not persisted user state).
Local branch `feature-platform-legibility-s3` (off S2's branch), unpushed.

### 2026-07-25 — S4 (§4) app legibility — declared skills + auto-surfaced route tools — DONE

Made an installed app's two agent-facing surfaces LEGIBLE and DRIVABLE from a static
manifest declaration, both readable without executing app code — closing the exact
manifest-vs-UI dead-path the audit kept refinding (§4 recon facts, log lines 32–33).

**§4.1 — app-declared skills, seeded through the chokepoint.** Revived the dead
`skills` manifest field (was a LEGACY stripped no-consumer field) as a typed
`list[AppSkill]` (`{path}`, dir relative to app root, holding a `SKILL.md`).
- **`src/personalclaw/apps/skill_seed.py`** (new) — the prompt-seed twin with the one
  rule prompts don't need: an app skill NEVER bypasses the supply-chain gate. A
  transient single-app `_AppSkillsMarketplace` (subclasses `SkillsMarketplace`) feeds
  each declared dir through `install_scanned` (quarantine → `scan_dir` at the app's
  trust tier → commit + `.pclaw-lock.json` provenance/SEL) — a DANGEROUS verdict
  refuses always, WARNING without force. Idempotent + non-clobbering (an existing
  same-named dir is left untouched); `remove_app_skills` deletes ONLY dirs whose lock
  records `source == app:<name>`, never a user's own or another app's.
- **`apps/manifest.py`** — `AppSkill` dataclass + `skills` threaded through
  `to_dict`/`from_dict`/`validate` (traversal-guarded path). **`providers/loader.py`** —
  `_seed_extension_skills` at startup discovery (bundled `builtin` tier; installed apps
  at their ledger origin) + `_seed_promptonly_installed_apps` covers no-provider apps.
  **`apps/app_manager.py`** — seed on enable/install, remove on disable/uninstall,
  remove-old→re-seed-new across `/update` (re-scans a changed skill).

**§4.2 — route-table tool surfacing (MCPify, adapted).** One generic provider, never
N generated ones.
- **`apps/manifest.py`** — `RouteEntry` (`op`/`method`/`path`/`summary`/`params`/`body`/
  `agentCallable`) + `BackendConfig.routes`; `validate()` enforces `ROUTE_OP_RE`, unique
  ops, `/`-rooted traversal-free paths.
- **`src/personalclaw/tool_providers/app_routes.py`** (new) — `AppRoutesToolProvider`
  re-reads enabled apps on every `list_tools` (enable/disable/`/update` resync for
  free — no registration churn), surfacing `app_<name>_<op>` tools with verb-keyed risk
  (GET→SAFE, POST/PUT/PATCH→CAUTION, DELETE→DESTRUCTIVE). `resolve_route` is the SINGLE
  gate (refuses an undeclared/non-`agentCallable` op with an `ERR_APP_ROUTE_UNKNOWN`
  envelope carrying the callable ops as `suggestions`), substitutes path placeholders,
  and splits leftover args to query (safe verbs) vs JSON body (mutating). `call_app_route`
  proxies through the existing reverse proxy under `LOOPBACK_INTERNAL` with a fresh
  app-scoped token (`ERR_APP_BACKEND_UNAVAILABLE` when the backend's down). `app_surfaces()`
  renders the same declarations for the manifest (`tool: null` for a documented-but-not-
  callable route); `note_proxy_status` fires a deduped `app.route.drift` notification on
  the first proxy 404 (dead-declared route caught the moment it's called). Registered once
  in `loader.py`.
- **`action_providers/call_app_route_provider.py`** (new) — the ONE `call-app-route` action
  (per-app generated providers can't be enumerated in the static allowlist), sharing
  `resolve_route`+`call_app_route` so the tool path and action path can't diverge. Added to
  `ALLOWED_HOOK_PROVIDERS` (`validation.py`) + registered in `action_providers/registry.py`
  in the same change (else hook create/update rejects it).
- **`manifest.py`** — `/api/manifest`'s `app_surfaces[]` now delegates to `app_surfaces()`.
- **Proving pair retrofitted:** Growth `app.json` (17 routes) + Minutes `app.json` (24
  routes) in `PersonalClawApps` — both validate clean and round-trip.

**Drift-safe by design:** the manifest drift test's fixture registers only native
`BUNDLED_DIR` provider manifests into a cleared tool registry, and a bare test home has no
enabled apps with routes — so the runtime-only `AppRoutesToolProvider` and its dynamic
`app_<name>_<op>` tools never appear there (no `TOOL_META` needed), while at runtime they
DO flow into `tools[]` + `app_surfaces[]`.

**Tests:** `tests/test_app_owned_skills.py` (7 — chokepoint provenance, idempotent
non-clobber, provenance-keyed removal leaving user + other-app skills untouched, skip flag,
empty no-op) and `tests/test_app_routes.py` (25 — callable-only tool generation + verb risk,
disabled→zero, param-schema union, gate refusals with suggestions, path/query/body split,
`app_surfaces` null-tool + sorting, one-shot drift, proxy up/down/404, action refusals +
shared-gate + allowlist/registration).

**Gate:** `make lint` clean (black/isort/flake8/mypy — 906 files, 462 source files; fixed a
mypy arg-type on `_AppSkillsMarketplace` by making it subclass `SkillsMarketplace`); targeted
pytest (105 — new pair + prompts + manifest + drift + supply-chain) green; `make test` 7762
passed / 28 skipped / 13 xfailed; `web/` untouched (backend + manifest only), web gate N/A.
CHANGELOG `Added` entry landed. No E1–E6 blocker. Clean-break under the pre-1.0 banner (the
`skills` seed writes provenance-locked user-tree state, removable on disable; route tools are
read-through, no persisted state). Local branch `feature-platform-legibility-s4` (off S3's
branch), unpushed.

### 2026-07-25 — S5 (§5–§7) UI docs + power-ups + context provider — DONE

The final slice, landing three legibility surfaces as ONE conceptual commit (S5a config
plumbing, S5b UI docs, S5c power-ups, S5d context provider). Each is propose-don't-write and
reads without executing anything it describes — the soul guardrail for a single user's machine.

**S5a — config through the four wiring points (the round-trip contract).** New `legibility`
section: `power_ups: bool = True` + `context_adapters: bool = False`, each wired through
(a) `LegibilityConfig` dataclass fields with `_meta(label, help)` (`config/loader.py`),
(b) `AppConfig.load()`'s explicit field-by-field mapping, (c) `to_dict()`, and
(d) `_EDITABLE_CONFIG`'s PATCH allowlist (`dashboard/handlers/core.py`) + `api.ts` +
a Settings › Legibility toggle panel (`web/src/pages/settings/LegibilityPanel.tsx`).
`test_config_roundtrip.py` proves no silent drop.

**S5b — the `ui/` kit describes itself (§5).** Each primitive ships a colocated `.doc.ts`
object (purpose / props / a best-practice tenet); `web/scripts/buildUiDocs.mjs` compiles them
+ TS-extracted prop types into `web/dist/ui-docs.json` as a Vite build step (70 components).
- **`src/personalclaw/tool_providers/ui_docs.py`** (new) — `UiDocsToolProvider` serves
  `ui_search(query)` (a budgeted keyword brief over names/keywords/descriptions, with a
  `ui_get` follow-up hint) and `ui_get(name, section?)` (full props + `bestPractices`), reading
  the built JSON. Registered as a native `ToolProvider` via `tool_providers/registry.py`.
- **`src/personalclaw/apps/native/personalclaw-ui-docs/app.json`** (new) — the provider's
  bundled home. **`web/src/ui/uiDocs.drift.test.ts`** — a doc object per `ui/` export + props
  parity; a primitive shipping without its doc reddens the FE build.

**S5c — capability-discovery power-ups (§6).** `src/personalclaw/legibility/tool_usage.py`
(new) — a `<config>/tool_usage.json` counter (`used_names()`); the untouched-capability
denominator is the `/api/manifest` tool set minus used minus dismissed. `power_ups.py` (new,
pure selection + `entity_settings/legibility.json` dismissal persistence) backs
`GET /api/legibility/power-ups` (`dashboard/handlers/legibility.py`) on the `DashboardLive`
SLOW_POLL feed. Widget `web/src/pages/dashboard/widgets/PowerUps.tsx` (one card, deep-link +
dismiss, hard-imported into `DashboardPage.tsx` — no widget registry exists). Kill switch:
`legibility.power_ups`. Deterministic templates — no LLM call on the default path.

**S5d — PClaw as routed-context provider (§7).** The neutral manifest first, adapters second
(adapters are DERIVED, never canonical).
- **`src/personalclaw/legibility/context_router.py`** (new) — the pure, store-free assembler.
  `assemble()` builds a `RoutedContext` with deliberate tier ordering: **top** = hard rules
  (project `brief` + `agent_instructions_template`), **middle** = scored memories + skills index
  + knowledge *pointers*, **bottom** = the L0 catalog of what was NOT loaded, each with the tool
  that pulls it — lost-in-the-middle by construction. `render()`/`to_dict()` keep the MEMORY vs
  KNOWLEDGE boundary structural: memory-derived content under "How this user works", knowledge
  items as titled pointers under a distinct heading with a `GET /api/knowledge/items/{id}/content`
  affordance — **never an inlined body** (knowledge has no MCP tool; it is HTTP-only).
  `route_context()` orchestrates live retrieval, every store best-effort/degradable (a failing
  store contributes an empty tier, never an exception). `apply_block()` is the marker-fenced
  replace-in-place splice — idempotent, first-write-appends, and it RAISES on a malformed fence
  rather than risk clobbering user content.
- **`src/personalclaw/dashboard/handlers/context.py`** (new) — the only place the router touches
  live stores. `GET /api/context` (project resolution: explicit `project_id` → the session's
  bound project → the Personal default) returns the manifest; `POST /api/projects/{id}/context-
  adapters/regenerate` renders the block into `workspace_dir`'s `CLAUDE.md`/`AGENTS.md`/
  `.cursorrules`, gated 403 when `legibility.context_adapters` is off and 400 with no bound
  workspace — every write SEL-audited.
- **`src/personalclaw/mcp_core.py`** — `get_context` registered in-process (`_list_tools` def +
  `_call_tool_inner` dispatch), description embedding the protocol ("call at the START of every
  task…"). **Recon-honored:** `mcp_core` is in-process only; external exposure waits on NEW-10's
  fail-closed MCP server. **`manifest_meta.py`** — its `TOOL_META` entry (drift test requires one).
- **FE:** `api.regenerateContextAdapters(id)` + a "Refresh context files" `Button` on the project
  hub's workspace bar, shown only when adapters are enabled AND a workspace is bound.

**DEVIATION — the three new raw `<button>`s adopted the `Button`/`IconButton` primitives instead
of raising the primitive-adoption ratchet.** S5c's PowerUps widget (dismiss + "try it" CTA) and
S5d's refresh button pushed the live raw-`<button>` count to 281 > the committed baseline of 278,
reddening `primitiveAdoption.test.ts`. Per the S2 ratchet doctrine (the baseline may only shrink,
never grow), converted all three to the shell primitives (dismiss → `IconButton`, CTA + refresh →
`Button variant="tonal"/"ghost" size="xs"`); the live count returned to 278 and the ratchet is
green without touching the baseline. No new bespoke chrome shipped.

**Tests:** `tests/test_context_router.py` (21 — assembler tier ordering, the distinct
memory/knowledge headings + pointer-not-body boundary, honest no-silent-cap unloaded notes,
`route_context` store degradation + skill cap, `apply_block` idempotency/first-write/malformed-
fence, and the endpoint consent gate: 403 adapters-off / 400 no-workspace / a write that preserves
user content outside the fence + SEL audit). `tests/test_power_ups.py` (S5c selection + dismissal +
kill switch) and `tests/test_tool_usage.py` green. `web/src/ui/uiDocs.drift.test.ts` (6) green.

**Gate:** targeted `pytest tests/test_context_router.py` 21 passed + `test_api_manifest_drift.py`
8 passed; `web/` typecheck clean, `npm test` 231 passed (27 files, ratchet green), `npm run build`
built the SPA + `ui-docs.json` (70 components). `make lint` + full `make test` run as the final
DoD gate before the commit. No E1–E6 blocker. Clean-break under the pre-1.0 banner
(`tool_usage.json` + `entity_settings/legibility.json` are re-derivable counters; adapter files are
opt-in derived artifacts in user project dirs, removable by hand — no migration-bearing state).
Local branch `feature-platform-legibility-s5` (off S4's branch), unpushed. **Platform-Legibility
Pack complete (S1–S5).**

### 2026-07-25 — §6 reshaped: tool-derived power-ups → curated Discover hub — DEVIATION (owner-directed)

**DEVIATION from §6 as written.** §6 shipped (S5c) a tool-usage-telemetry-driven "power-ups"
widget: it cycled ONE untouched *tool* at a time, its lesson a deterministic template over
`/api/manifest` entries, its denominator the manifest tool set minus used (`tool_usage.json`) minus
dismissed. Owner feedback rejected the *source*: the tool surface is an implementation detail the
user is never meant to drive by hand, so "you haven't called `knowledge_add` yet" is noise, not
guidance. Owner locked three decisions: (1) tip source = a **hand-authored catalog** of user-facing
*areas* (Chat, goal loops, automation, Tasks, Projects, Inbox, Knowledge, Memory, Skills, Apps),
each a deep link into the page that owns it — the tool-derived generator DELETED outright (clean
break); (2) hub form = a **dedicated Discover page** (`#/discover`, in the command palette) listing
every tip grouped by area, dismissable, with the dashboard section a rotating spotlight linking in;
(3) dismiss rule = **explicit dismiss (persists forever) AND auto-hide once the area is used**,
"used" detected from state that already exists. The soul guardrail (propose-don't-write: points +
hides, never enables) is preserved unchanged.

**Clean break.** Deleted `legibility/power_ups.py`, `legibility/tool_usage.py`, `tests/test_power_ups.py`,
`tests/test_tool_usage.py`, and the `ToolUsageStore().record_use(...)` call in
`agents/native/runtime.py` (the counter had no remaining reader). New `legibility/discover.py`: a
frozen-dataclass `CATALOG` of 10 tips, per-area cheap `_engaged_*` checks (one dir listing / JSON
read / SQLite count each, isolated so a failure reads "not engaged"), `select_visible` (catalog
minus dismissed minus engaged areas, catalog order preserved), `_group_by_area`, and
`compute_discover` honoring the renamed kill switch. Config flag `legibility.power_ups` →
`discover_tips` (dataclass `_meta`, `load()`, `_EDITABLE_CONFIG`, `api.ts`, both Settings surfaces).
Endpoints `GET /api/legibility/power-ups` → `GET /api/legibility/discover` +
`POST /api/legibility/discover/dismiss`; the standalone `/dismissed` GET dropped (the discover
payload already excludes dismissed). Dismissal-persistence field
`entity_settings/legibility.json:dismissed_power_ups` → `dismissed_discover_tips`. FE: `PowerUps.tsx`
→ `Discover.tsx` (dashboard spotlight, `SPOTLIGHT=3` + "See all" → hub) + new
`pages/discover/DiscoverPage.tsx` (WorkbenchLayout hub, all areas grouped, per-tip dismiss); wired
into `App.tsx` (`ROUTABLE` + `renderPage` + palette; NO nav-rail tile — the dashboard spotlight is
the persistent entry point, matching the owner's "links into the full hub" model). `routes.md` +
`index.md` regenerated (430 total / 428 agent-callable; discover routes present, power-ups gone —
`test_agent_reference.py` green).

**Tests:** `tests/test_discover.py` (18 — catalog integrity: unique ids, every `engaged_key`
registered, every tip has a deep link, `to_dict`/`try_it` shape is the FE contract; visible
selection drops dismissed + auto-hides engaged + preserves catalog order; `_group_by_area`;
engagement-check isolation on raise; dismissal persistence to `entity_settings/legibility.json`;
`compute_discover` kill switch + grouped-visible + auto-hide). `test_context_router.py` stub updated
`power_ups=True` → `discover_tips=True`.

**Gate:** `web/` typecheck clean; `npm test` 231 passed (27 files — `primitiveAdoption`,
`tokenLint`, `consistencyAudit`, `bento` ratchets green; the "See all" link adopted
`Button variant="ghost" size="xs"` rather than raising the raw-`<button>` baseline); `npm run build`
+ `npm run smoke:render` (5 routes) green; `pytest tests/test_discover.py tests/test_context_router.py
tests/test_config_roundtrip.py tests/test_agent_reference.py` 51 passed. `make lint` + full `make
test` as the final DoD gate. No E1–E6 blocker. Clean break under the pre-1.0 banner (the renamed
dismissal set is a re-derivable hide list — no migration-bearing state; the deleted `tool_usage.json`
counter is regenerable and now unused). Local branch `feature-discover-hub`, unpushed.

---

### `PL-9` — DONE (2026-08-21) — the API version becomes a negotiated contract

**Premise verified before implementing.** `API_VERSION` had exactly **five** occurrences in
`src/personalclaw`, and **zero** of them were a comparison: the definition (`manifest.py:32`), the
emission into the manifest document (`manifest.py:154`), one import, and two f-string emissions into
the generated reference docs (`manifest_reference.py:208`, `:331`). The SPA typed it
(`web/src/lib/api.ts` `interface Manifest { apiVersion: number }`) and never sent it. The clause's
premise — write-only — was exact.

**Shipped.**
* `src/personalclaw/api_version.py` — the ONE origin. `API_VERSION = 1`,
  `MIN_SUPPORTED_API_VERSION = 1` (a DECLARED floor, not an emergent tolerance), `VERSION_HEADER`,
  `supported_window()`, `ApiVersionRefusal`, `ApiVersionOutcome`, and `negotiate()` — the single
  comparison. The **bump rule** lives in this module's docstring and nowhere else: seven "bump for"
  clauses (field removed/renamed, type or units changed, meaning changed under a stable name, a
  required request parameter added, an enum member removed, a route moved/deleted) and six
  "do NOT bump for" clauses (new route, new optional field, new optional parameter, appended enum
  member, new tool/provider in a generated section, prose). `manifest.py` now RE-EXPORTS the constant
  (`manifest.API_VERSION is api_version.API_VERSION`) rather than declaring a literal.
* `src/personalclaw/dashboard/api_version_gate.py` — the ONE chokepoint, installed in
  `server.py`'s explicit `app.middlewares[:]` list immediately after `no_cache_middleware` and
  **before** csrf/token-auth: a stale bundle whose cookie is still valid should read "your build is
  too old" rather than a 403 from a layer it would actually pass, and the gate publishes nothing to
  a caller who declares nothing (the refusal fires only on an explicit out-of-window declaration).
* **The refusal** goes through PL-8's envelope only — new registered code
  `api_version_unsupported` (400), `error_extra` carrying `client_version`, `server_version`,
  `min_supported_version` and `upgrade` (`"client"`|`"server"`). No second error shape; the two raw
  children at `handlers/auth.py:593`/`:621` were left to #1854.
* **Absent ⇒ oldest supported**, with the reason stated in code. To make that rule *load-bearing*
  rather than cosmetic (with a one-version-wide window, "absent ⇒ floor" and "absent ⇒ current" are
  indistinguishable on accept/reject), the gate ECHOES the negotiated version back in the same
  header on every accepted response. So the resolution is a fact on the wire — `curl -sD-` with no
  declaration reports the floor — and it has a consumer instead of being a second write-only value.
* **SPA declares once.** `web/src/lib/apiVersion.ts` holds `CLIENT_API_VERSION` +
  `API_VERSION_HEADER` + `apiVersionHeaders`; `api.ts` spreads it into the shared `SK` header object
  every request helper already used, so no call site carries the number. The refusal needs no new FE
  code: `errText.ts` already lifts `error.message` out of PL-8's envelope into the `ApiError` every
  helper throws.

**Exemptions, each deliberate** (`api_version_gate.py`'s docstring carries the reasons): everything
outside `/api/` and `/mcp` (the SPA document and `/assets/**` ARE the recovery path — gating them
means a refused client can never fetch the bundle that would fix it); `GET /api/healthz` (a non-200
reads as "gateway down" to a supervisor and can trigger a restart loop); `GET /api/manifest` (it
PUBLISHES `apiVersion` — refusing it is circular); the pre-session front door (`/api/token/local`,
`/api/logout`, `/api/auth/status`, `/api/auth/login`, `/api/auth/enroll/complete`,
`/api/devices/pair/complete` — a version wall in front of login turns "reload the page" into "you
cannot authenticate far enough to see why you were refused"); `/mcp` (negotiates its own protocol
version per the MCP spec); and WebSocket upgrades (`/api/ws**` — a browser `WebSocket` cannot set a
request header, so the only carrier would be a query parameter at each of four socket sites, i.e.
four declaration sites instead of one, and a socket is only opened by an already-mounted SPA whose
HTTP calls the chokepoint has necessarily already judged). A rail asserts every EXACT exemption names
a path the gate would otherwise have caught, so no row can be a dead decision.

**Tests.** `tests/test_api_version_negotiation.py` (21 — the resolution rule at the function, driven
over a WIDER window because the shipped one-version window hides it; the chokepoint end to end,
including a deliberately mismatched client in both directions and the echoed negotiated version) and
`tests/test_api_version_one_origin.py` (14 — the anti-drift rail).

**The rail is non-vacuous — seven falsifications, each observed RED, each restored from a file copy
(never `git checkout --`):** (a) `if False and client > srv` → 3 red incl. the e2e (`200 != 400`);
(b) absent resolved to `srv` instead of `floor` → 5 red, incl. the end-to-end
"an undeclared client was credited with the current version instead of the oldest supported one";
(c1) `LEGACY_API_VERSION = 1` added to `manifest.py` → the Python one-origin rail red; (c2) the same
literal appended to `api.ts` → the TypeScript one-origin rail red; (d1) `upgrade` stripped from
`as_error_extra` → `KeyError: 'upgrade'` on both refusal tests; (d2) `server_version` stripped →
`KeyError: 'server_version'`; (e) a second `negotiate()` call added to `api_healthz` → the
one-chokepoint rail red naming both callers; (f) `api_version_middleware()` removed from
`server.py`'s middleware list → "the negotiation would never run"; (g) `...apiVersionHeaders` dropped
from `SK` → "every request would go out undeclared". Post-sweep
`grep -rn "FALSIFICATION\|if False and\|# PROBE\|MUTANT" src/personalclaw tests` = 13 (the benign
baseline) and the tree clean apart from `docs/design/consistency-audit.json`, which drifts on `main`.

**Gate.** `make lint` (black/isort/flake8/mypy, 953 files) clean; `npm run typecheck:web` clean;
full `npm run test:web` 4805 passed (460 files — the global design/a11y ratchets included);
`npm run build --workspace web` green; `python -m personalclaw.manifest_reference` re-run with
`PYTHONPATH` pinned to this worktree produced **no** diff (no route added, version unchanged) and the
main checkout stayed clean; targeted `pytest` + full `make test`. No E1–E6 blocker. Clean break under
the pre-1.0 banner — no state shape changed; the only wire addition is a request/response header and
one appended error code, both additive, so `API_VERSION` stays at 1 by its own bump rule.

**Live drive (found and fixed one thing the tests could not).** Driven against a real gateway on an
isolated dev home (`PYTHONPATH` pinned to this worktree — the venv is an editable install of the MAIN
checkout, so the naive invocation runs main's code and silently proves nothing): an undeclared
`GET /api/status` answered `200` + `X-PersonalClaw-API-Version: 1`; `2` was refused
`upgrade=server`, `0` refused `upgrade=client`, `banana-…` refused with the value truncated to 32
chars; and every exemption answered non-400 (`healthz` 200, `manifest` 200 with `apiVersion:1`,
`token/local` 403 from its OWN secret check, the POST-only front-door routes 405 from routing, `/`
200 HTML, `/login` 302, and a real WS upgrade to `/api/ws` returned **101** with version `99`).
The drive exposed copy the tests had accepted: with the shipped one-version window the message read
"this gateway speaks 1-1" — machine-shaped phrasing on the one surface this plan exists to make
legible, and the ONLY phrasing anyone would ever see. `_window_phrase()` now renders
"speaks version 1" for a one-wide window and "speaks versions 2-5" for a wider one, with three tests
covering both widths and the shipped window whatever its width.

**Left for its owner:** `handlers/auth.py:593`/`:621` read PL-8's `{code, message}` object as a
string inside a JS string literal; #1854 owns that fix and nothing here copies the pattern.
