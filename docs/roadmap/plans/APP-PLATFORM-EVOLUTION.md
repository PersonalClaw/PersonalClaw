# APP-PLATFORM-EVOLUTION

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/APE.md`](../atomic/APE.md) as 11 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: App Platform Evolution — Richer Capabilities, Better Apps

**Status:** DESIGNED — created 2026-07-18 (roadmap rev 10; owner ask: evolution of first-party + native apps and what platform capabilities can grow into)
**Created:** 2026-07-18
**Wave:** 2 (S1-2: capability surfaces + app quality bar) + 3 (S3-4: app-to-app, richer UI contribution)
**Depends on:** PROVIDER-BOUNDARY-COMPLETION (32 — the `cli.*`/`loggerRoots` manifest-field pattern this plan extends), PLATFORM-LEGIBILITY (19 — manifest self-description + UI-primitive doc objects), DESIGN-SYSTEM-CONSISTENCY (51 — apps must consume the same tokens/primitives), ECOSYSTEM-TOOLING (38 — the scaffold emits whatever new manifest surface this plan adds).
**Scope:** grow what an app *can be* (new capability surfaces, richer UI contribution, app-to-app messaging, background capabilities) and raise the quality bar of the shipped 26 native + 36 first-party apps. **Soul guardrail:** every new capability is a *seam* (typed contract + permission), never a vendor path in core; new power is permission-gated and consent-surfaced exactly like today's `api`/`events`/`network`. The app platform stays the ONE extension mechanism — no second plugin system. Additive-only to the manifest (unknown-field-preserving, §3.8); existing apps keep working untouched.

---

> 📎 **The App Store UI re-layout lives in [PRODUCT-EXPERIENCE-PARITY](PRODUCT-EXPERIENCE-PARITY.md) §2 (#68)** — added 2026-08-05: a persistent right-rail (categories+counts + source management, always-open on wide screens) + art-forward card polish, following a `CategoryRail`/`FeatureCard` pattern. #68 §2 **renders** this plan's S2 `quality` manifest badges rather than inventing a second badge system — land the `quality` block here, render it there. Coordinate so the Store card component isn't churned twice.

## Context (code recon, 2026-07-18)

- **The platform is already deep:** `apps/app_manager.py` (quarantine→scan→install lifecycle), `apps/backend_runtime.py` (subprocess + watchdog + PPID-reaping), `apps/permissions.py` (api/events/mcpTools/memory/cron/storage/agent/network), reverse-proxy credential-stripping + 1-hour app tokens, per-app namespaced MCP servers. UI contribution: `web/src/app/appSdk.tsx` — `AppContext`, `AppPermissions`, `createAppApi`, `createAppEvents`, `AppApiProvider`, `mount(el, ctx)`; host resolves bare `react`/`@personalclaw/app-sdk` imports.
- **26 native bundles** (`apps/native/`): entity providers (`native-{agents,knowledge,tasks,workflows,prompts,skills,vector-memory}`, `personalclaw-*` tool/memory/schedule bundles) + action bundles (`bash-action`, `run-*-action`, `notify-action`, `send-message-action`, `create-task-action`, `invoke-agent-action`) + `filesystem-inbox`, `personalclaw-artifacts`. **36 first-party** (apps repo): 16 model, 7 search, 3 agent, 3 tool, 1 channel, 1 action, 1 skills-marketplace, 2 backend+UI (Minutes, Growth).
- **Gaps this plan targets:** (1) apps can't *react to platform events beyond their declared WS types* or run richer background work than a cron; (2) apps can't talk to each other (only through core); (3) the two backend+UI apps (Minutes/Growth) predate the current design system — inconsistent UX (feeds plan 51); (4) no capability *tiers* or a declared app "quality level"; (5) the native bundles are minimal `app.json`-only — no room for evolving native capability without core edits.

## Design

- **S1 — Background & event capabilities (new manifest permissions, plan-32 pattern):** `permissions.backgroundTasks: bool` (an app may register a long-lived async worker via the SDK, subprocess-hosted, watchdog-supervised — richer than a cron; budget + kill-switch inherited from AUTONOMY-GUARDRAILS) and `permissions.events` widened to a **declared event subscription** (an app subscribes to typed platform events — `session.created`, `knowledge.ingested`, `task.completed` — delivered over its existing scoped WS, filtered by declaration). Both consent-surfaced.
- **S2 — App quality bar + native evolution:** a declared `quality` manifest block (`{tested: bool, designSystem: "v2"|"legacy"|"n/a", a11y: bool}`) shown on Store cards (honest self-declaration, verified by CI for first-party); a **native-app capability contract** so native bundles can grow richer providers without core edits (the `app.json`-only native bundles gain optional `provider.py` room + a native SDK subset); Minutes + Growth migrated to the current design system (coordinates with plan 51).
- **S3 — App-to-app messaging (gateway-brokered, never direct):** `permissions.appMessaging: ["<target-app>"]` — an app posts a typed message to another declared app through a gateway broker (`/api/apps/message`), the broker enforces both apps' declarations, fences payloads (`fence_untrusted`), SEL-logs. No direct sockets between app subprocesses (the isolation invariant holds).
- **S4 — Richer UI contribution:** app pages get access to more shell primitives via the UI SDK (the design-system components exported through `@personalclaw/app-sdk` so apps look native — plan 51 dependency), typed generative-UI widget support (coordinates with AMBIENT-SURFACES 20's generative-UI layer), and a declared `uiCapabilities` block.

## Contracts & Interfaces (extends existing manifest + UI SDK; conventions per [AGENTS.md](../../../AGENTS.md))

### C1 — Manifest additions (`apps/manifest.py`, additive, to_dict/from_dict parity, §3.8)
```jsonc
{
  "permissions": {
    "backgroundTasks": false,          // may register a supervised long-lived worker
    "appMessaging": ["other-app"],      // may message these declared apps (broker-enforced)
    "eventSubscriptions": ["session.created", "knowledge.ingested"]  // typed platform events
  },
  "quality": { "tested": true, "designSystem": "v2", "a11y": true },  // self-declared; CI-verified for first-party
  "uiCapabilities": ["shell-primitives", "generative-widget"]         // richer UI contribution (S4)
}
```

### C2 — Background worker SDK (`sdk/` new `background.py`, exported §2.8)
```python
def register_worker(name: str, coro_factory: Callable[[], Awaitable[None]], *, restart: bool = True) -> None: ...
# Subprocess-hosted (backend_runtime), watchdog-supervised, budget via AUTONOMY-GUARDRAILS ModelCallGuard.
```

### C3 — App-to-app broker (`POST /api/apps/message`)
Request `{to: "<app>", type: "<str>", payload: {...}}`; broker verifies the caller's `appMessaging` includes `to` AND `to` declares an inbound handler; delivers via the target's scoped WS as a fenced event; 403 + SEL on any mismatch. Payload capped + `fence_untrusted(source="app:<from>")`.

### C4 — Typed platform events (the subscription vocabulary — a registry like plan 42's kinds)
`src/personalclaw/app_events.py`: `PlatformEvent(domain, name, payload_schema)` registered for each broadcastable event; an app receives only events it declared. Reuses the existing WS fan-out filter (`app_permission_middleware` events path).

### Integration points
- **Calls:** `apps/manifest.py`, `apps/permissions.py` (enforcement), `backend_runtime` (worker hosting), the WS event filter, `fence_untrusted`, `sel()`, AUTONOMY-GUARDRAILS budgets.
- **Called by:** first-party + third-party apps declaring the new permissions; the scaffold (38) emits the new blocks.
- **Consumed by:** 51 (design-system components exported to apps), 20 (generative widgets), 38 (registry shows `quality`).
- **Depends on:** 32 (manifest-field pattern), 19 (self-description), 9 (worker budgets).

## Task breakdown (executor-ready — run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

### Session 1 — Background + event capabilities

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | Manifest: `permissions.backgroundTasks`, `permissions.eventSubscriptions` (parse/serialize/consent-surface) | `apps/manifest.py`, `apps/permissions.py`, install consent UI | round-trip tests; consent shows the new grants; unknown-field preservation intact |
| T1.2 | Platform event registry `app_events.py` + register the first events (`session.created`, `knowledge.ingested`, `task.completed`) at their emit sites; WS filter delivers only declared events | `src/personalclaw/app_events.py`, the 3 emit sites, WS filter | a fixture app subscribed to `task.completed` receives it; unsubscribed app never does (SEL clean) |
| T1.3 | Background worker SDK + hosting (`sdk/background.py` → backend_runtime supervised worker; budget via guardrails; kill-switch honored) | `sdk/background.py`, `apps/backend_runtime.py` | fixture app worker runs, survives a crash (watchdog), stops on disable; budget breach pauses it + notifies |
| V1 | Validation: install a fixture app declaring both new perms → worker runs, receives a subscribed event, respects budget; uninstall → clean teardown (no orphan worker, PPID-reaping verified) | — | holds |

### Session 2 — Quality bar + native evolution

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | `quality` manifest block + Store card rendering; CI check verifies first-party self-declarations (tested=CI green, designSystem=token-lint pass, a11y=axe pass) | `apps/manifest.py`, Store card, apps-repo CI | dishonest first-party declaration turns apps CI red |
| T2.2 | Native capability contract: native bundles may ship an optional `provider.py` using a native SDK subset (documented allowed imports); update 2-3 native bundles as exemplars of richer capability without core edits | `apps/native/*`, docs | a native bundle gains a real provider method via the contract; boundary test still green |
| T2.3 | Migrate Minutes + Growth backend+UI apps to the current design system (tokens + shell primitives via UI SDK) — coordinates with plan 51 | apps repo: `minutes/ui`, `growth/ui` | both apps pass the token-lint + look native (screenshot check) |
| V2 | Validation: Store shows honest quality badges; Minutes/Growth visually consistent with the host | — | holds |

### Sessions 3-4 — App-to-app + richer UI (Wave 3)

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | `appMessaging` permission + `/api/apps/message` broker (double-declaration enforcement, fencing, cap, SEL) | `apps/manifest.py`, new broker handler, `apps/permissions.py` | two fixture apps exchange a typed message; undeclared pair → 403 + SEL |
| T4.1 | UI SDK exports the design-system shell primitives + tokens to apps (`@personalclaw/app-sdk` surface); `uiCapabilities` block; a generative-widget contribution path (coordinate with plan 20) | `web/src/app/appSdk.tsx`, apps repo demo | a fixture app page renders using host Button/Surface/tokens and looks native |
| V3-4 | Validation: app-to-app demo (one app drives another via broker); a contributed app page indistinguishable from a native page | — | holds |

## Owner tasks (real world)
1. **Prioritize which native/first-party apps evolve first** — the plan migrates Minutes/Growth and 2-3 native exemplars; you pick which capabilities matter (your usage decides).
2. Approve the new-permission **consent copy** (S1/S3 — security surfaces).
3. Decide whether **third-party** apps may declare `backgroundTasks`/`appMessaging` at launch or only after a trust period (recommendation: allowed but community-tier + prominent consent).

## Risks & open questions
- **Background workers = new denial-of-wallet surface** — mitigated by inheriting AUTONOMY-GUARDRAILS budgets (do not ship the worker SDK before plan 9's ModelCallGuard; E6 if tempted).
- **App-to-app messaging could become a covert channel** — the broker fences + SEL-logs + double-declares; no direct sockets. Revisit if abuse appears (ratchet).
- **Open:** whether native bundles gaining `provider.py` blurs the native/first-party line — keep native = shipped-in-package + locked-on; the capability contract doesn't change that, only what a native provider may do.

## Amendment (2026-07-26 — ecosystem gap analysis, owner greenlight)

**The app QoL trio the ecosystem kept hand-rolling.** (a) **Update-available surfacing:** recon confirms `app_manager.update()` (`apps/app_manager.py:508`) does atomic stage→scan→swap→rollback and `POST /api/apps/{name}/update` exists — but nothing *tells the user* an update exists; the Store catalog (`apps/catalog.py`, `CatalogEntry.version`, `available_catalog()`) already computes source-side versions on request. (b) **Consented cross-app data access:** apps get exactly one private `app_data_dir(name)` (`apps/manager.py:89`, gated by `permissions.storage`, handed as `PERSONALCLAW_APP_DATA_DIR`); the common pattern of apps shuttling files through the user is the workaround. (c) **"Fix with AI":** failed installs/updates return `InstallResult{ok: False, error}` + SEL audit, and the user retypes the error into chat by hand. All three are seams, not vendor paths.

### Contract-level design

- **(a) Update surfacing — no per-app polling crons.** `apps/catalog.py` gains `updates_available() -> list[{name, installed_version, latest_version, source}]` — a compare of `installed.json` versions against the SAME catalog resolution `GET /api/apps/catalog` already performs (git-source pointers resolve on the existing catalog fetch cadence; local sources compare mtimes/manifest). Computed on catalog view + one existing-scheduler daily tick. Surfacing: badge on the installed-app card + Store nav count, and ONE notification via the standard path — plan 42's kind registry gets `("apps", "update_available", default_mode="badge")`; emitted through `emit_attention_item()` when plan 42's gate is ON, plain `DashboardState.notify` before then (dual-honesty note in the task). Dedup by `(name, latest_version)` so a version nags once.
- **(b) Consented cross-app read — reconciled with S3 honestly.** S3's `appMessaging` is *push* (broker-delivered events); this is *pull* (read another app's files) — different seam, same consent doctrine, and it deliberately reuses S3's double-declaration shape rather than inventing a third. Manifest (additive, §3.8 parity): consumer declares `permissions.storageRead: ["<other-app>"]`; the target must declare `storageShared: true` (or a subdir allowlist `storageShared: ["exports/"]`) — no silent one-sided grants. Both shown at install consent beside existing permissions. Enforcement where storage is granted: `backend_runtime.py:118-129` additionally mounts each granted target read-only as `PERSONALCLAW_APP_SHARED_DIR_<NAME>` (path into the target's `data/`); SDK helper `sdk/util.py::shared_app_data_dir(name) -> Path | None`. Read-only is the v1 rail; writes stay broker-only (S3). SEL `capability_grant` on install-time consent.
- **(c) Fix with AI.** `InstallResult` gains `log_excerpt: str` (last ~4 KiB of the failure: scan verdict / onInstall output / manifest error). The Store's failed install/update toast gains one button → prefilled chat via the existing `ne:launch-chat` seam with a template ("App `<name>` failed to `<install|update>` from `<source>`: …log…") — the excerpt is third-party content, so it is wrapped `fence_untrusted(source="app_install_log:<name>")` before entering the turn. No new backend beyond the field.

### Session placement

(a)+(c) fold into **Session 2** (they ARE the quality bar); (b) folds into **Session 3** beside `appMessaging` (one consent-copy review covers both). Session count stays 4.

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.4 | `updates_available()` + badges + kind-registered notification (dedup by version; no new scheduler) | `apps/catalog.py`, `dashboard/handlers/apps.py`, Store/installed cards, kind registration | bump a local source's version → badge + one notification; re-view → no re-nag; zero polling processes added |
| T2.5 | Fix-with-AI: `InstallResult.log_excerpt` + Store button → prefilled fenced chat turn | `apps/app_manager.py`, Store error UI | a deliberately broken app's failed install offers the button; the opened chat contains the fenced log; fence verified |
| T3.2 | `storageRead`/`storageShared` manifest pair + consent surface + read-only env mount + `sdk/util.py::shared_app_data_dir` | `apps/manifest.py`, `apps/permissions.py`, `backend_runtime.py`, `sdk/util.py`, consent UI | fixture consumer reads the sharer's file; undeclared pair gets no mount; write attempt fails; consent lists the grant; boundary test green |

## Execution log

- [2026-08-13][APE-12] DONE. **A comment claimed a consent surface that did not exist.**
  `apps/manifest.py:313` said of `permissions.appMessaging`: *"This is the install-consent surface
  for who an app can talk to, shown in the Store via `to_dict`."* The server half was true —
  `Permissions.to_dict()` emits the key, `catalog._manifest_consent` carries it into the Store's
  pre-install entry, and `GET /api/apps` returns it (measured: a fixture app declaring
  `{"appMessaging": ["receiver", "mail-*"], "cron": true}` came back with
  `"permissions": {"cron": true, "appMessaging": ["receiver", "mail-*"]}`). The last mile was
  missing: `AppPermissionsWire` (`web/src/lib/api.ts`) never declared the field, so `PermissionList`
  could not render it. **What the Store actually showed for that app, measured by rendering the
  unchanged component:** `"Permissions the gateway enforces • Scheduled jobs"` plus EI-12 D2's
  network advisory — and nothing else. Bullets: exactly `["• Scheduled jobs"]`. A user installing it
  was never told it may message `receiver`, nor that `mail-*` covers every app under that prefix.
- [2026-08-13][APE-12] **Why this is a new atom and not a reopened `APE-9`.** APE-9 (`done`, PR #914)
  shipped the broker, and every clause of its `done_when` holds: two apps exchange a typed message,
  an undeclared pair is refused 403 with a SEL denial row (`apps/messaging.py:167` →
  `can_use_app_messaging`), the payload is capped and fenced, and `POST /api/apps/message` is the
  only app-to-app path. Its `done_when` never mentioned consent — the manifest comment overreached
  on its behalf. So the enforcement was never broken; only the disclosure was. Found while executing
  EI-12 D2 and recorded in `EXECUTION-ISOLATION.md`'s log as needing its own atom.
- [2026-08-13][APE-12] **Enforced, therefore NOT shaped like D2.** The atom immediately before this
  one (EI-12 D2) moved `permissions.network` OUT of the enforced list into an advisory row, because
  PersonalClaw cannot confine an app's egress. `appMessaging` is the opposite: the broker is a real
  chokepoint, so it renders as a bullet under "Permissions the gateway enforces", beside Storage /
  Scheduled jobs / Run background agents, with no hedging copy (a component test asserts the
  messaging bullet matches neither /advisory/ nor /does not confine/, so it cannot drift into D2's
  wording). The one thing borrowed from D2 is its treatment of silence: an app that declares no
  target is now told about, not left blank — *"App messaging: none — it declared no target, and the
  gateway broker is the only way one app can reach another, so it can message no other app"* —
  because deny-by-default is the real behaviour. A trailing-`*` entry renders as the pattern it is
  (*"any app whose name starts with “mail-”"*) and a bare `*` as *"any installed app"*, mirroring
  `apps/permissions.py::_matches_any`; rendering `mail-*` as a literal app name would understate a
  grant that reaches apps the user has not installed yet. `_matches_any`'s third branch (an exact
  entry also matching `<entry>/...` as a path prefix) is inert for app names — kebab-case, no `/` —
  and is deliberately not claimed in the copy.
- [2026-08-13][APE-12] **D2's load-bearing guard is untouched.** The Store panel still gates the
  whole section on `Object.keys(item.permissions).length > 0`, which is what distinguishes "declares
  nothing" from a registry pointer whose manifest has never been fetched (both `{}`). Claiming
  "messages no other app" about a manifest we have not read would be a new false statement, so the
  deny-by-default line renders only where a manifest was actually parsed. Consequence, stated: a
  Store card for an app that declares *nothing at all* still shows no permission section, hence no
  messaging line; the installed-app panel (which always renders) does show it.
- [2026-08-13][APE-12] **The wire leg is pinned on both ends, plus a rail so the next permission
  cannot repeat this.** `tests/test_app_messaging.py` asserts the pre-install catalog payload
  (`catalog._manifest_consent`) and `GET /api/apps` both carry the targets verbatim, and that a
  declining app sends no key at all. `tests/test_app_permissions.py` adds a two-sided rail: the keys
  `Permissions.to_dict()` can emit (derived from the dataclass fields, not a hand-written list) must
  equal the optional fields declared on `AppPermissionsWire`, so a server-only key (invisible to the
  user — this defect) and a wire-only key (a consent surface promising something nothing sends) both
  red. Falsified by deleting the new field: *"server-only (never disclosed): ['appMessaging']"*.
  `web/src/pages/apps/permissionConsent.test.tsx` covers the rendering; falsified against the
  unchanged component, 4 of its 5 new cases red (the 5th is a regression guard for the new
  double-claim case, which cannot fail on code that renders nothing).
- [2026-08-13][APE-12] **No first-party app declares `appMessaging`** — verified independently: 44
  `app.json` manifests in the first-party apps repo, zero occurrences of the string anywhere in that
  tree — so every rendering case above uses a synthetic manifest fixture. Nothing in a real Library will
  start showing a messaging row from this change — which also means no first-party app exercises
  this path, and the fixtures are the whole coverage.
- [2026-08-13][APE-12] **Validated as a user**, in a throwaway home (`/private/tmp/ape12-home`, an
  `AUTH_MODE=none` loopback gateway on port 10312, deleted afterwards) with two synthetic apps added
  as a local Store source. Both surfaces, measured in a real browser with zero console errors:
  the **pre-install** panel for the declaring app reads *"Permissions the gateway enforces / • API:
  /api/knowledge / • Scheduled jobs / • App messaging: consent-demo-quiet, any app whose name starts
  with “mail-”"*, the **installed-app** panel reads the same three bullets, and the pre-install panel
  for the app that declares only `storage` reads *"• Storage"* followed by *"App messaging: none — it
  declared no target…"*. `GET /api/apps` and `GET /api/apps/catalog` were both inspected on the wire
  first and carried `"appMessaging": ["consent-demo-quiet", "mail-*"]` verbatim, confirming the data
  had always been arriving and only the browser was discarding it.
- [2026-08-16][APE-5] DONE. **A bundled app can now own its provider code.** All 27 bundles were
  `app.json`-only: `provider.implementation` named a core dotted path
  (`personalclaw.tasks.native:create_provider`), so growing a bundled capability meant editing core —
  the one thing the app platform exists to avoid. `apps/native_contract.py` is the contract; a
  bundle-relative `implementation` (no dot: `provider:create_provider`) resolves to a module in the
  bundle's own dir. `providers/loader.py` now uses ONE resolution rule for both tiers — a file inside
  the app's dir loads from there under a namespaced `sys.modules` name, anything else is a dotted
  package import. **That fixed a latent collision:** the old branch chose namespacing by TIER
  (installed apps only) and routed bundled apps through a plain `import provider` with the bundle dir
  on `sys.path`, so the second bundle to ship `provider.py` would have silently received the first
  one's factory. Two synthetic bundles now pin it. The namespaced load is also cached — `load_factory`
  and `load_availability` both resolve the same module, and the extension-list API calls the
  availability probe, so app code was being re-executed per read, minting a second class for one
  provider (an `isinstance` across two reads would have started failing).
- [2026-08-16][APE-5] **The "native SDK subset" is `personalclaw.sdk.*` — no narrower list.** The
  atom's wording implies a native-only allowlist; on inspection that would be a SECOND boundary to
  keep in step for no gain. A bundled app is loaded by the same loader seam, registered through the
  same typed handler, and shipped by the same release as an installed one, and the two candidate
  exclusions do not survive contact: `sdk.cli` IS reachable (`app_cli.py` runs `cli.setup`/`cli.doctor`
  for *each installed + enabled* app, and a native app is installed+enabled), and `sdk.util` cannot be
  excluded wholesale because only one of its symbols (`shared_app_data_dir`) is backend-env-bound. So
  the documented contract is: `personalclaw.sdk.*` only, plus three native-specific CAVEATS —
  in-process (no `PERSONALCLAW_APP_*` env, so `shared_app_data_dir` is always `None`), no own
  dependencies (the `dependencies` block installs into an app venv a bundled module never gets), and
  packaged assets by path are legitimate (same distribution) while imports are not.
- [2026-08-16][APE-5] **DEVIATION: 1 exemplar, not "2-3".** A census of all 27 bundles measured why
  the other 26 cannot convert today: `code_map` needs `codegraph.CodeGraphIndex` +
  `config.loader.default_workspace_dir`; the 7 mcp-backed tool bundles wrap
  `agents.native.tools.InProcessMcpToolProvider` over a core `mcp_*` module; the 8 action bundles are
  registered a SECOND time by `action_providers/registry.py` as "intrinsic actions" bound to
  `guardrails.rungs.CORE_ACTION_TYPES`; `filesystem-inbox` is core's own last-resort default
  (`inbox_providers/__init__.py:48`); the four entity providers have 2-7 core importers each
  (`tasks.native` ← `investigate.py` + `tasks/registry.py`, `memory_providers.registry` ← `context.py`
  + `providers/registry.py`, …). Each conversion needs new SDK exports or an untangling that is its own
  atom, so the contract landed with the one bundle it fits — `personalclaw-ui-docs`, whose only
  non-stdlib import was `tool_providers.base` (exactly what `sdk.tool` re-exports) and whose only core
  importer was its own factory. `tool_providers/ui_docs.py` moved into the bundle and
  `create_ui_docs_provider` was deleted (clean break, no dual path); the rail makes each further
  conversion mechanical rather than exploratory.
- [2026-08-16][APE-5] **The gained method: `ui_list`.** `ui_search` REQUIRES a query and `ui_get`
  needs a name, so nothing in the kit could be *enumerated* — an agent had to guess a keyword to
  discover that a primitive existed at all. `ui_list(kind=components|tokens|all)` was added inside the
  bundle with no edit to any core module that implements, resolves or dispatches it, and is driven
  through the real path in the rail (registry → typed handler → live tool registry → `invoke`),
  asserting CONTENT (alphabetical component names + descriptions, tokens-only mode, and a refused bad
  `kind`), not just `success`.
- [2026-08-16][APE-5] **DISCOVERY: the installed-app boundary rail is vacuous in this workspace.**
  `tests/test_apps_import_boundary.py` resolves `parents[2]/apps` and `pytest.skip`s the entire module
  when that dir is absent — true in a standalone core clone AND in this project's own workspace, whose
  apps checkout is named `PersonalClawApps`. It skipped for this atom's run too (the atom's `done_when`
  asks for it "still green", and green-by-skip is what it gives). The new
  `tests/test_native_capability_contract.py` never skips (the bundled tree ships inside the package)
  and carries an explicit vacuity floor: if no bundled app ships a module, the import lint is
  measuring nothing and the rail fails.
- [2026-08-16][APE-5] **DISCOVERY: one residual core touch remains for a new agent TOOL.** A bundled
  app that adds a tool NAME still needs a `manifest_meta.TOOL_META` entry, because that map is the ONE
  hand-maintained input to the agent manifest and `tests/test_api_manifest_drift.py` fails the suite on
  a tool without one (it registers every native manifest straight from `BUNDLED_DIR`, so bundled tools
  are audited while installed-app tools are not). Plus the generated `src/personalclaw/reference/*`.
  That is catalogue data about the shipped distribution's agent surface, not provider implementation —
  so "without core edits" is exact for provider BEHAVIOUR and not yet exact for tool METADATA.
  Bundle-declared tool metadata is the follow-up; it was not improvised here because `TOOL_META` and
  its drift test are a guarded contract of their own.
- [2026-08-16][APE-5] **DISCOVERY: packaging already carries a bundled `.py`, but by accident.**
  `pyproject.toml`'s `package-data` lists `apps/native/*/app.json` and no `.py` glob, so the obvious
  reading is that a bundled `provider.py` would be dropped from the wheel. Measured instead by running
  `setup.py build_py` with a probe file: the `.py` IS copied (setuptools discovers
  `personalclaw.apps.native.<bundle>` as a namespace package and copies it as a MODULE, log line
  "copying …/provider_probe.py", before the package-data phase), while a probe `.json` in the same dir
  was NOT. So no `pyproject.toml` change was needed — which matters, since that file is owned by
  another open PR. The mechanism is namespace-package discovery under `setuptools>=64`, not an explicit
  declaration, so an explicit `apps/native/*/*.py` package-data line would make it intentional rather
  than incidental. Left for whoever owns `pyproject.toml` next.
- [2026-08-16][APE-5] **DISCOVERY: the exemplar retired two inert SDK exports.**
  `inert-surface-baseline.json` shrank by 2 — `sdk_export:ToolDefinition` and `sdk_export:ToolResult`
  on `sdk/tool.py` had never been imported by any app, and the bundled provider now imports them. Also
  a small loss to note: `mypy src/personalclaw` no longer type-checks the moved file (it is outside the
  discovered packages); it was run directly and is clean, and a bundled module is a normal candidate for
  the same treatment installed apps get.
