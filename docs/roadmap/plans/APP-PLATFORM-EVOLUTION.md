# Plan: App Platform Evolution — Richer Capabilities, Better Apps

**Status:** DESIGNED — created 2026-07-18 (roadmap rev 10; owner ask: evolution of first-party + native apps and what platform capabilities can grow into)
**Created:** 2026-07-18
**Wave:** 2 (S1-2: capability surfaces + app quality bar) + 3 (S3-4: app-to-app, richer UI contribution)
**Depends on:** PROVIDER-BOUNDARY-COMPLETION (32 — the `cli.*`/`loggerRoots` manifest-field pattern this plan extends), PLATFORM-LEGIBILITY (19 — manifest self-description + UI-primitive doc objects), DESIGN-SYSTEM-CONSISTENCY (51 — apps must consume the same tokens/primitives), ECOSYSTEM-TOOLING (38 — the scaffold emits whatever new manifest surface this plan adds).
**Scope:** grow what an app *can be* (new capability surfaces, richer UI contribution, app-to-app messaging, background capabilities) and raise the quality bar of the shipped 26 native + 36 first-party apps. **Soul guardrail:** every new capability is a *seam* (typed contract + permission), never a vendor path in core; new power is permission-gated and consent-surfaced exactly like today's `api`/`events`/`network`. The app platform stays the ONE extension mechanism — no second plugin system. Additive-only to the manifest (unknown-field-preserving, §3.8); existing apps keep working untouched.

---

## Context (code recon, 2026-07-18)

- **The platform is already deep:** `apps/app_manager.py` (quarantine→scan→install lifecycle), `apps/backend_runtime.py` (subprocess + watchdog + PPID-reaping), `apps/permissions.py` (api/events/mcpTools/memory/cron/storage/agent/network), reverse-proxy credential-stripping + 1-hour app tokens, per-app namespaced MCP servers. UI contribution: `web/src/app/appSdk.tsx` — `AppContext`, `AppPermissions`, `createAppApi`, `createAppEvents`, `AppApiProvider`, `mount(el, ctx)`; host resolves bare `react`/`@personalclaw/app-sdk` imports.
- **26 native bundles** (`apps/native/`): entity providers (`native-{agents,knowledge,tasks,workflows,prompts,skills,vector-memory}`, `personalclaw-*` tool/memory/schedule bundles) + action bundles (`bash-action`, `run-*-action`, `notify-action`, `send-message-action`, `create-task-action`, `invoke-agent-action`) + `filesystem-inbox`, `personalclaw-artifacts`. **36 first-party** (apps repo): 16 model, 7 search, 3 agent, 3 tool, 1 channel, 1 action, 1 skills-marketplace, 2 backend+UI (Minutes, Growth).
- **Gaps this plan targets:** (1) apps can't *react to platform events beyond their declared WS types* or run richer background work than a cron; (2) apps can't talk to each other (only through core); (3) the two backend+UI apps (Minutes/Growth) predate the current design system — inconsistent UX (feeds plan 51); (4) no capability *tiers* or a declared app "quality level"; (5) the native bundles are minimal `app.json`-only — no room for evolving native capability without core edits.

## Design

- **S1 — Background & event capabilities (new manifest permissions, plan-32 pattern):** `permissions.backgroundTasks: bool` (an app may register a long-lived async worker via the SDK, subprocess-hosted, watchdog-supervised — richer than a cron; budget + kill-switch inherited from AUTONOMY-GUARDRAILS) and `permissions.events` widened to a **declared event subscription** (an app subscribes to typed platform events — `session.created`, `knowledge.ingested`, `task.completed` — delivered over its existing scoped WS, filtered by declaration). Both consent-surfaced.
- **S2 — App quality bar + native evolution:** a declared `quality` manifest block (`{tested: bool, designSystem: "v2"|"legacy"|"n/a", a11y: bool}`) shown on Store cards (honest self-declaration, verified by CI for first-party); a **native-app capability contract** so native bundles can grow richer providers without core edits (the `app.json`-only native bundles gain optional `provider.py` room + a native SDK subset); Minutes + Growth migrated to the current design system (coordinates with plan 51).
- **S3 — App-to-app messaging (gateway-brokered, never direct):** `permissions.appMessaging: ["<target-app>"]` — an app posts a typed message to another declared app through a gateway broker (`/api/apps/message`), the broker enforces both apps' declarations, fences payloads (`fence_untrusted`), SEL-logs. No direct sockets between app subprocesses (the isolation invariant holds).
- **S4 — Richer UI contribution:** app pages get access to more shell primitives via the UI SDK (the design-system components exported through `@personalclaw/app-sdk` so apps look native — plan 51 dependency), typed generative-UI widget support (coordinates with AMBIENT-SURFACES 20's generative-UI layer), and a declared `uiCapabilities` block.

## Contracts & Interfaces (extends existing manifest + UI SDK; conventions per [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md))

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

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

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

## Amendment (2026-07-26 — sibling-platform gap analysis, owner greenlight)

**The app QoL trio the sibling ecosystem kept hand-rolling.** (a) **Update-available surfacing:** recon confirms `app_manager.update()` (`apps/app_manager.py:508`) does atomic stage→scan→swap→rollback and `POST /api/apps/{name}/update` exists — but nothing *tells the user* an update exists; the Store catalog (`apps/catalog.py`, `CatalogEntry.version`, `available_catalog()`) already computes source-side versions on request. (b) **Consented cross-app data access:** apps get exactly one private `app_data_dir(name)` (`apps/manager.py:89`, gated by `permissions.storage`, handed as `PERSONALCLAW_APP_DATA_DIR`); the sibling pattern of apps shuttling files through the user is the workaround. (c) **"Fix with AI":** failed installs/updates return `InstallResult{ok: False, error}` + SEL audit, and the user retypes the error into chat by hand. All three are seams, not vendor paths.

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
