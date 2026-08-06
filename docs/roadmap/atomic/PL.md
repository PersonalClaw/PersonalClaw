# PLATFORM-LEGIBILITY — atomic plans

**Source plan:** [`PLATFORM-LEGIBILITY`](../plans/PLATFORM-LEGIBILITY.md)  
**Code:** `PL`  
**Source status:** done



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `PL-1` | ✅ | /api/manifest self-description endpoint + drift test | `EXT:WORKFLOWS-V2:embed workflow_manifest under tools[].workflow_manifest_ref when WF2-R12 lands (federate, not rebuild)` | GET /api/manifest returns {apiVersion,tools,routes,app_surfaces,providers} generated from list_all_tools()+route table+extension registry; test_api_manifest_drift.py reds when a tool/route ships without a TOOL_META/exclusion entry; canonical-route leak regression green; FE api.manifest() typed |
| `PL-2` | ✅ | Platform-wide WHAT/WHY/FIX AgentError envelope + append-only code registry | `PL-1` | errors.py AgentError{code,what,why,fix,suggestions} attached at ToolResult/ActionResult/three dispatch seams/ProviderResolutionError/validation rejections; ERROR_CODES seeded (ERR_TOOL_ARG_INVALID/ERR_MODEL_UNRESOLVED/ERR_HOOK_PROVIDER_UNKNOWN/ERR_ACTION_PROVIDER_FAILED); test_error_codes_append_only.py proves no removal/reword; ALLOWED_HOOK_PROVIDERS rejection carries allowed set as suggestions; FE ToolCard renders WHAT/WHY/FIX rows; PL-1 drift test's declared-error-codes guard now non-vacuous |
| `PL-3` | ✅ | pclaw-api first-party skill + offline reference + 5-task eval gate | `PL-1`, `PL-2` | skills/bundled/pclaw-api/SKILL.md + personalclaw.reference/ subpackage rendered at build time from the §1 manifest generator (one source, two renderings); doctor --paths resolves reference dir; test_agent_reference.py byte-compares checked-in vs fresh render; eval_pclaw_api_battery.py checked in; with/without eval scored 5/5 first-try, 0 silent misses (bar ≥4/5) vs 1-2/5 baseline |
| `PL-4` | ✅ | App legibility — declared skills through the chokepoint + auto-surfaced route tools | `PL-1`, `PL-2` | revived app.json skills field seeds via install_guarded (scan/lock/SEL, DANGEROUS refuses, provenance-keyed removal); backend.routes[] + AppRoutesToolProvider surface app_<name>_<op> tools resynced on enable/disable/update; one call-app-route action added to ALLOWED_HOOK_PROVIDERS; declared-vs-live drift files app.route.drift notification; app_surfaces[] populated in /api/manifest; Growth+Minutes manifests retrofitted in PersonalClawApps as proving pair |
| `PL-5` | ✅ | UI-primitive doc objects + ui_search/ui_get retrieval tools | `PL-1` | each web/src/ui/ primitive ships colocated .doc.ts; buildUiDocs.mjs compiles them + prop types into web/dist/ui-docs.json (70 components); UiDocsToolProvider serves ui_search (budgeted brief + follow-up hint) and ui_get (props + bestPractices), listed in /api/manifest; uiDocs.drift.test.ts reds if a primitive ships without a doc object or props mismatch |
| `PL-6` | ✅ | PClaw as routed-context provider — context_router, marker-fenced adapters, get_context | `PL-1`, `EXT:MCP-READONLY-INBOUND:external get_context exposure via NEW-10 fail-closed MCP server (in-process only until then)` | context_router.assemble() builds RoutedContext with rules-top / scored-middle / L0-unloaded-catalog-bottom preserving distinct memory vs knowledge headings (pointers not bodies); apply_block() marker-fenced replace-in-place idempotent, raises on malformed fence; POST /api/projects/{id}/context-adapters/regenerate writes CLAUDE.md/AGENTS.md/.cursorrules gated by legibility.context_adapters (default off, 403 when off); in-process get_context tool registered on mcp_core with TOOL_META entry; FE refresh button on project hub |
| `PL-7` | ✅ | Capability discovery — Discover hub (owner-reshaped from §6 tool-usage power-ups) | `PL-1` | tool-usage power_ups.py/tool_usage.py DELETED clean-break; legibility/discover.py CATALOG of 10 hand-authored area tips with per-area engagement checks; GET /api/legibility/discover + POST .../dismiss; Discover.tsx dashboard rotating spotlight + DiscoverPage.tsx hub (command palette, no nav tile); dismiss persists forever AND auto-hides once area used; nothing auto-enabled; legibility.discover_tips kill switch wired through the four config points; test_discover.py green; reference regenerated |

## Atom scopes

### `PL-1` — /api/manifest self-description endpoint + drift test

**Status:** done

§1 ("/api/manifest — the self-description endpoint"), §1.1 sections table, §1.2 drift test, §1.3 typed-envelope discipline; §9 Session 1

**Done when:** GET /api/manifest returns {apiVersion,tools,routes,app_surfaces,providers} generated from list_all_tools()+route table+extension registry; test_api_manifest_drift.py reds when a tool/route ships without a TOOL_META/exclusion entry; canonical-route leak regression green; FE api.manifest() typed

### `PL-2` — Platform-wide WHAT/WHY/FIX AgentError envelope + append-only code registry

**Status:** done

§2 ("Platform-wide WHAT/WHY/FIX error envelope"), §2.1 envelope, §2.2 attach seams, §8 provider-fidelity wiring; §9 Session 2

**Done when:** errors.py AgentError{code,what,why,fix,suggestions} attached at ToolResult/ActionResult/three dispatch seams/ProviderResolutionError/validation rejections; ERROR_CODES seeded (ERR_TOOL_ARG_INVALID/ERR_MODEL_UNRESOLVED/ERR_HOOK_PROVIDER_UNKNOWN/ERR_ACTION_PROVIDER_FAILED); test_error_codes_append_only.py proves no removal/reword; ALLOWED_HOOK_PROVIDERS rejection carries allowed set as suggestions; FE ToolCard renders WHAT/WHY/FIX rows; PL-1 drift test's declared-error-codes guard now non-vacuous

### `PL-3` — pclaw-api first-party skill + offline reference + 5-task eval gate

**Status:** done

§3 ("First-party PersonalClaw skill + offline reference"), §3.1 artifacts, §3.2 acceptance bar; §9 Session 3

**Done when:** skills/bundled/pclaw-api/SKILL.md + personalclaw.reference/ subpackage rendered at build time from the §1 manifest generator (one source, two renderings); doctor --paths resolves reference dir; test_agent_reference.py byte-compares checked-in vs fresh render; eval_pclaw_api_battery.py checked in; with/without eval scored 5/5 first-try, 0 silent misses (bar ≥4/5) vs 1-2/5 baseline

### `PL-4` — App legibility — declared skills through the chokepoint + auto-surfaced route tools

**Status:** done

§4 ("App legibility — declared skills + auto-surfaced route tools"), §4.1 skill seeding, §4.2 route-table surfacing, §8 wiring; §9 Session 4

**Done when:** revived app.json skills field seeds via install_guarded (scan/lock/SEL, DANGEROUS refuses, provenance-keyed removal); backend.routes[] + AppRoutesToolProvider surface app_<name>_<op> tools resynced on enable/disable/update; one call-app-route action added to ALLOWED_HOOK_PROVIDERS; declared-vs-live drift files app.route.drift notification; app_surfaces[] populated in /api/manifest; Growth+Minutes manifests retrofitted in PersonalClawApps as proving pair

### `PL-5` — UI-primitive doc objects + ui_search/ui_get retrieval tools

**Status:** done

§5 ("UI-primitive doc objects + ui_search/ui_get"); §9 Session 5 (S5b)

**Done when:** each web/src/ui/ primitive ships colocated .doc.ts; buildUiDocs.mjs compiles them + prop types into web/dist/ui-docs.json (70 components); UiDocsToolProvider serves ui_search (budgeted brief + follow-up hint) and ui_get (props + bestPractices), listed in /api/manifest; uiDocs.drift.test.ts reds if a primitive ships without a doc object or props mismatch

### `PL-6` — PClaw as routed-context provider — context_router, marker-fenced adapters, get_context

**Status:** done

§7 ("PClaw as context provider for external agents"), §8 config four-point wiring (legibility.context_adapters); §9 Session 5 (S5a config + S5d)

**Done when:** context_router.assemble() builds RoutedContext with rules-top / scored-middle / L0-unloaded-catalog-bottom preserving distinct memory vs knowledge headings (pointers not bodies); apply_block() marker-fenced replace-in-place idempotent, raises on malformed fence; POST /api/projects/{id}/context-adapters/regenerate writes CLAUDE.md/AGENTS.md/.cursorrules gated by legibility.context_adapters (default off, 403 when off); in-process get_context tool registered on mcp_core with TOOL_META entry; FE refresh button on project hub

### `PL-7` — Capability discovery — Discover hub (owner-reshaped from §6 tool-usage power-ups)

**Status:** done

§6 ("Capability-discovery power-ups") as SUPERSEDED by the 2026-07-25 owner-directed reshape (Success Criterion #7 + the §6-reshaped DEVIATION log entry); §8 config wiring (legibility.discover_tips); §9 Session 5 (S5c, then reshaped)

**Done when:** tool-usage power_ups.py/tool_usage.py DELETED clean-break; legibility/discover.py CATALOG of 10 hand-authored area tips with per-area engagement checks; GET /api/legibility/discover + POST .../dismiss; Discover.tsx dashboard rotating spotlight + DiscoverPage.tsx hub (command palette, no nav tile); dismiss persists forever AND auto-hides once area used; nothing auto-enabled; legibility.discover_tips kill switch wired through the four config points; test_discover.py green; reference regenerated

