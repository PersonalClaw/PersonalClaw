# APP-PLATFORM-EVOLUTION — atomic plans

**Source plan:** [`APP-PLATFORM-EVOLUTION`](../plans/APP-PLATFORM-EVOLUTION.md)  
**Code:** `APE`  
**Source status:** proposed

11 atoms; APE-7 (update surfacing, PR #929) + APE-8 + APE-9 shipped, the rest todo. Session 1 = APE-1..3 (background+event capabilities), Session 2 = APE-4..8 (quality bar, native evolution, update surfacing, fix-with-AI), Sessions 3-4 = APE-9..11 (app-to-app broker, cross-app read, richer UI SDK).

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `APE-1` | ⬜ | Manifest: backgroundTasks + eventSubscriptions permissions (parse/serialize/consent) | `EXT:PROVIDER-BOUNDARY-COMPLETION:manifest-field to_dict/from_dict-parity pattern` | round-trip (to_dict/from_dict) tests pass; install consent UI surfaces the two new grants; unknown-field preservation intact |
| `APE-2` | ⬜ | Platform event registry (app_events.py) + emit sites + declared-subscription WS filter | `APE-1` | app_events.py registers session.created/knowledge.ingested/task.completed at their emit sites; a fixture app subscribed to task.completed receives it; an unsubscribed app never does; SEL clean |
| `APE-3` | ⬜ | Background worker SDK (sdk/background.py) + backend_runtime supervised hosting | `APE-1`, `EXT:AUTONOMY-GUARDRAILS:ModelCallGuard budgets + kill-switch (plan Risks: E6 if shipped before this exists)` | fixture app worker runs, survives a crash (watchdog), stops on disable; budget breach pauses it + notifies; V1: uninstall leaves no orphan worker (PPID-reaping verified) |
| `APE-4` | ⬜ | quality manifest block + Store card rendering + first-party CI verification | `EXT:DESIGN-SYSTEM-CONSISTENCY:token-lint + axe a11y verification` | a dishonest first-party quality declaration turns apps-repo CI red (tested=CI green, designSystem=token-lint pass, a11y=axe pass); Store cards render honest badges |
| `APE-5` | ⬜ | Native capability contract: optional provider.py + native SDK subset + 2-3 exemplar bundles | — | a native bundle gains a real provider method via the documented native SDK subset without core edits; apps import-boundary test still green |
| `APE-6` | ⬜ | Migrate Minutes + Growth backend+UI apps to the current design system | `APE-11`, `EXT:DESIGN-SYSTEM-CONSISTENCY:tokens/primitives to consume` | both apps pass token-lint and look native (screenshot check) using tokens + shell primitives via the UI SDK |
| `APE-7` | ✅ | Update surfacing: catalog.updates_available() + card/nav badges + kind-registered notification | `EXT:INBOX-NOTIF-UNIFICATION:kind registry + emit_attention_item (dual-honesty: plain notify before its gate is ON)` | bump a local source's version -> installed-card badge + Store nav count + ONE notification; re-view -> no re-nag (dedup by name+latest_version); zero polling processes added |
| `APE-8` | ⬜ | Fix-with-AI: InstallResult.log_excerpt + Store error button -> prefilled fenced chat | — | a deliberately broken app's failed install offers the button; the opened chat (via ne:launch-chat) contains the log wrapped in fence_untrusted(source=app_install_log:<name>); fence verified |
| `APE-9` | ⬜ | appMessaging permission + /api/apps/message gateway broker (double-declaration, fence, cap, SEL) | — | two fixture apps exchange a typed message through the broker (V3-4: one app drives another); an undeclared pair -> 403 + SEL; payload capped and fenced; no direct app-to-app sockets |
| `APE-10` | ⬜ | storageRead/storageShared manifest pair + consent + read-only env mount + sdk/util.shared_app_data_dir | `APE-9` | fixture consumer reads the sharer's file via PERSONALCLAW_APP_SHARED_DIR_<NAME> (read-only); undeclared pair gets no mount; a write attempt fails; consent lists the grant (capability_grant SEL); import-boundary test green |
| `APE-11` | ⬜ | UI SDK exports design-system shell primitives + tokens + uiCapabilities block + generative-widget path | `EXT:DESIGN-SYSTEM-CONSISTENCY:shell primitives/tokens to export`, `EXT:AMBIENT-SURFACES:generative-UI layer` | a fixture app page renders using host Button/Surface/tokens via @personalclaw/app-sdk and is indistinguishable from a native page; generative-widget contribution path works |

## Atom scopes

### `APE-1` — Manifest: backgroundTasks + eventSubscriptions permissions (parse/serialize/consent)

**Status:** todo

Session 1 T1.1; Contract C1 (manifest additions, additive §3.8)

**Done when:** round-trip (to_dict/from_dict) tests pass; install consent UI surfaces the two new grants; unknown-field preservation intact

### `APE-2` — Platform event registry (app_events.py) + emit sites + declared-subscription WS filter

**Status:** todo

Session 1 T1.2; Contract C4 (typed platform events registry)

**Done when:** app_events.py registers session.created/knowledge.ingested/task.completed at their emit sites; a fixture app subscribed to task.completed receives it; an unsubscribed app never does; SEL clean

### `APE-3` — Background worker SDK (sdk/background.py) + backend_runtime supervised hosting

**Status:** todo

Session 1 T1.3 + V1; Contract C2 (background worker SDK)

**Done when:** fixture app worker runs, survives a crash (watchdog), stops on disable; budget breach pauses it + notifies; V1: uninstall leaves no orphan worker (PPID-reaping verified)

### `APE-4` — quality manifest block + Store card rendering + first-party CI verification

**Status:** todo

Session 2 T2.1 + V2; Contract C1 (quality block)

**Done when:** a dishonest first-party quality declaration turns apps-repo CI red (tested=CI green, designSystem=token-lint pass, a11y=axe pass); Store cards render honest badges

### `APE-5` — Native capability contract: optional provider.py + native SDK subset + 2-3 exemplar bundles

**Status:** todo

Session 2 T2.2

**Done when:** a native bundle gains a real provider method via the documented native SDK subset without core edits; apps import-boundary test still green

### `APE-6` — Migrate Minutes + Growth backend+UI apps to the current design system

**Status:** todo

Session 2 T2.3 (apps repo minutes/ui, growth/ui)

**Done when:** both apps pass token-lint and look native (screenshot check) using tokens + shell primitives via the UI SDK

### `APE-7` — Update surfacing: catalog.updates_available() + card/nav badges + kind-registered notification

**Status:** done

Amendment 2026-07-26 T2.4 (update-available surfacing; folds into Session 2)

**Shipped (PR #929):** `catalog.updates_available()`/`surface_app_updates()` compare each installed app against the highest version its local sources declare (on the existing `GET /api/apps` read — no polling); the canonical `apps/manifest.py::version_tuple()` comparator; `apps/update` attention kind emitted once via `emit_attention_item` with dedup high-water marks in `entity_settings/app_updates.json` (re-view never re-nags; only a strictly newer version re-fires); FE Library card badge + detail banner + Store nav count + notification row. No config field. DISCOVERY: no reusable version comparator existed (`dashboard/handlers/` ones would invert apps→dashboard layering), so `version_tuple()` was added to `apps/manifest.py`.

**Done when:** bump a local source's version -> installed-card badge + Store nav count + ONE notification; re-view -> no re-nag (dedup by name+latest_version); zero polling processes added

### `APE-8` — Fix-with-AI: InstallResult.log_excerpt + Store error button -> prefilled fenced chat

**Status:** todo

Amendment 2026-07-26 T2.5 (Fix with AI; folds into Session 2)

**Done when:** a deliberately broken app's failed install offers the button; the opened chat (via ne:launch-chat) contains the log wrapped in fence_untrusted(source=app_install_log:<name>); fence verified

### `APE-9` — appMessaging permission + /api/apps/message gateway broker (double-declaration, fence, cap, SEL)

**Status:** todo

Sessions 3-4 T3.1 + V3-4 (app-to-app demo half); Contract C3 (app-to-app broker)

**Done when:** two fixture apps exchange a typed message through the broker (V3-4: one app drives another); an undeclared pair -> 403 + SEL; payload capped and fenced; no direct app-to-app sockets

### `APE-10` — storageRead/storageShared manifest pair + consent + read-only env mount + sdk/util.shared_app_data_dir

**Status:** todo

Amendment 2026-07-26 T3.2 (consented cross-app read; folds into Session 3)

**Done when:** fixture consumer reads the sharer's file via PERSONALCLAW_APP_SHARED_DIR_<NAME> (read-only); undeclared pair gets no mount; a write attempt fails; consent lists the grant (capability_grant SEL); import-boundary test green

### `APE-11` — UI SDK exports design-system shell primitives + tokens + uiCapabilities block + generative-widget path

**Status:** todo

Sessions 3-4 T4.1 + V3-4 (contributed-page half); Contract C1 (uiCapabilities)

**Done when:** a fixture app page renders using host Button/Surface/tokens via @personalclaw/app-sdk and is indistinguishable from a native page; generative-widget contribution path works

