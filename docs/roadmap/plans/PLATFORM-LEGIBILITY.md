# Plan: Platform Legibility Pack — Self-Description Manifest, First-Party Skill, App Auto Tool-Surfacing, WHAT/WHY/FIX Errors

**Status:** PROPOSED — created 2026-07-13 from research synthesis, promoted from backlog
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
- **Action-provider errors:** `ActionResult` (`action_providers/base.py:ActionResult`) gains the same optional field; the three dispatch seams (`hooks.py:494`, `gateway.py:701`, `event_triggers.py:214`) wrap uncaught provider exceptions into a generic envelope so **app-contributed providers inherit it without knowing it exists** (the AUTONOMY-GUARDRAILS §1.2 enforcement-placement pattern).
- **Provider-resolution errors:** `ProviderResolutionError` (`providers/provider_bridge.py`) already carries the right doctrine ("block, don't silently fall back") — it gains the envelope so a background turn that dies on a stale pin tells the agent which use-case to rebind.
- **Gate/validation errors:** `validation.py:validate_tool_args` failures and hook create/update rejections (including the `ALLOWED_HOOK_PROVIDERS` rejection at `validation.py:555`) return coded envelopes with `suggestions` = the allowed set — today that rejection is exactly the kind of opaque failure that burns an agent turn.
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
- **One static action provider, honoring the frozenset:** `ALLOWED_HOOK_PROVIDERS` (`validation.py:555`) is a static frozenset — per-app generated action providers cannot be enumerated there. So exactly ONE new action provider ships: `call-app-route` (core-native, `action_providers/`), whose `action_config` selects `{app, op, args}`; **its single name is added to `ALLOWED_HOOK_PROVIDERS`** and it refuses ops not declared `agentCallable`. Hooks/crons/event-triggers can then hit any declared app route with zero per-app registration.
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

- **No new provider TYPE.** `PROVIDER_TYPES` (`apps/manifest.py:453`) is untouched; nothing here registers through `_TypeHandler`s. The two new tool surfaces (`AppRoutesToolProvider`, `UiDocsToolProvider`) register as core `ToolProvider`s via `tool_providers/registry.py:register_provider` — the same path as the native/schedule/artifacts providers.
- **Action provider:** exactly one — `call-app-route` (§4.2) — added to `ALLOWED_HOOK_PROVIDERS` (`validation.py:555`) in the same commit that registers it, or hook create/update rejects it. Its rejection message is itself a §2 envelope with the allowed set as `suggestions`.
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
7. The dashboard shows one untouched-capability power-up with a working "try it" deep link; dismissing it persists; disabling `legibility.power_ups` removes the widget; nothing is ever auto-enabled.
8. For an opted-in project, a marker-fenced PClaw block appears in the project dir's CLAUDE.md with rules top / L0 unloaded-catalog bottom, regenerates in place without duplicating, and never modifies content outside the markers; the in-process `get_context` tool returns rules + tiered memories + the unloaded list, with memory-derived and knowledge-derived content under distinct headings (boundary preserved).
