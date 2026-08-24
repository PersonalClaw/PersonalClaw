# APP-PLATFORM-EVOLUTION — atomic plans

**Source plan:** [`APP-PLATFORM-EVOLUTION`](../plans/APP-PLATFORM-EVOLUTION.md)  
**Code:** `APE`  
**Source status:** proposed

12 atoms; APE-5 (native capability contract) + APE-7 (update surfacing, PR #929) + APE-8 + APE-9 + APE-10 + APE-12 shipped, the rest todo. Session 1 = APE-1..3 (background+event capabilities), Session 2 = APE-4..8 (quality bar, native evolution, update surfacing, fix-with-AI), Sessions 3-4 = APE-9..11 (app-to-app broker, cross-app read, richer UI SDK). APE-12 was filed later, for a traced defect in APE-9's consent half.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `APE-1` | ✅ | Manifest: backgroundTasks + eventSubscriptions permissions (parse/serialize/consent) | `EXT:PROVIDER-BOUNDARY-COMPLETION:manifest-field to_dict/from_dict-parity pattern` | round-trip (to_dict/from_dict) tests pass; install consent UI surfaces the two new grants; unknown-field preservation intact |
| `APE-2` | ✅ | Platform event registry (app_events.py) + emit sites + declared-subscription WS filter | `APE-1` | app_events.py registers session.created/knowledge.ingested/task.completed at their emit sites; a fixture app subscribed to task.completed receives it; an unsubscribed app never does; SEL clean |
| `APE-3` | ✅ | Background worker SDK (sdk/background.py) + backend_runtime supervised hosting | `APE-1`, `EXT:AUTONOMY-GUARDRAILS:ModelCallGuard budgets + kill-switch (plan Risks: E6 if shipped before this exists)` | fixture app worker runs, survives a crash (watchdog), stops on disable; budget breach pauses it + notifies; V1: uninstall leaves no orphan worker (PPID-reaping verified) |
| `APE-4` | ⬜ | quality manifest block + Store card rendering + first-party CI verification | `EXT:DESIGN-SYSTEM-CONSISTENCY:token-lint + axe a11y verification` | a dishonest first-party quality declaration turns apps-repo CI red (tested=CI green, designSystem=token-lint pass, a11y=axe pass); Store cards render honest badges |
| `APE-5` | ✅ | Native capability contract: optional provider.py + native SDK subset + 2-3 exemplar bundles | — | a native bundle gains a real provider method via the documented native SDK subset without core edits; apps import-boundary test still green — DONE 2026-08-16: `apps/native_contract.py` + one load rule for both tiers + a never-skipping rail; `personalclaw-ui-docs` owns its provider and GAINED `ui_list`. MEASURED: the "native SDK subset" IS `personalclaw.sdk.*` (a narrower native-only allowlist would be a second boundary); 1 exemplar not 2-3, because a census of all 27 bundles found exactly one whose implementation is SDK-expressible today |
| `APE-6` | ⬜ | Migrate Minutes + Growth backend+UI apps to the current design system | `APE-11`, `EXT:DESIGN-SYSTEM-CONSISTENCY:tokens/primitives to consume` | both apps pass token-lint and look native (screenshot check) using tokens + shell primitives via the UI SDK |
| `APE-7` | ✅ | Update surfacing: catalog.updates_available() + card/nav badges + kind-registered notification | `EXT:INBOX-NOTIF-UNIFICATION:kind registry + emit_attention_item (dual-honesty: plain notify before its gate is ON)` | bump a local source's version -> installed-card badge + Store nav count + ONE notification; re-view -> no re-nag (dedup by name+latest_version); zero polling processes added |
| `APE-8` | ✅ (#918) | Fix-with-AI: InstallResult.log_excerpt + Store error button -> prefilled fenced chat | — | a deliberately broken app's failed install offers the button; the opened chat (via ne:launch-chat) contains the log wrapped in fence_untrusted(source=app_install_log:<name>); fence verified |
| `APE-9` | ✅ (#914) | appMessaging permission + /api/apps/message gateway broker (double-declaration, fence, cap, SEL) | — | two fixture apps exchange a typed message through the broker (V3-4: one app drives another); an undeclared pair -> 403 + SEL; payload capped and fenced; no direct app-to-app sockets |
| `APE-10` | ✅ | storageRead/storageShared manifest pair + consent + read-only env mount + sdk/util.shared_app_data_dir | `APE-9` | fixture consumer reads the sharer's file via PERSONALCLAW_APP_SHARED_DIR_<NAME> (read-only); undeclared pair gets no mount; a write attempt fails; consent lists the grant (capability_grant SEL); import-boundary test green |
| `APE-11` | ⬜ | UI SDK exports design-system shell primitives + tokens + uiCapabilities block + generative-widget path | `EXT:DESIGN-SYSTEM-CONSISTENCY:shell primitives/tokens to export`, `EXT:AMBIENT-SURFACES:generative-UI layer` | a fixture app page renders using host Button/Surface/tokens via @personalclaw/app-sdk and is indistinguishable from a native page; generative-widget contribution path works |
| `APE-12` | ✅ (#PENDING) | Disclose appMessaging targets at install consent (the last mile APE-9 left open) | `APE-9` | the Store names the apps a declaring app may message, in both surfaces `PermissionList` serves, among the permissions the gateway ENFORCES (not beside the advisory network row EI-12 D2 created); a trailing-`*` target reads as the name prefix it is and a bare `*` as any installed app; declaring none is stated as messaging no app (deny by default), not left silent; `AppPermissionsWire` declares the field and a test pins the server leg end to end (both the pre-install catalog payload and `GET /api/apps`), plus a two-sided rail that every key `Permissions.to_dict()` emits is declared on the wire; `apps/manifest.py`'s "this is the install-consent surface" comment is true after the change — DONE 2026-08-13 (backend 18956 passed / 0 failed — baseline 18953 + 3 new; web 1832 passed across 192 files + 5 new; tsc + build clean; validated live in an isolated home) |

## Atom scopes

### `APE-1` — Manifest: backgroundTasks + eventSubscriptions permissions (parse/serialize/consent)

**Status:** done

Session 1 T1.1; Contract C1 (manifest additions, additive §3.8)

**Done when:** round-trip (to_dict/from_dict) tests pass; install consent UI surfaces the two new grants; unknown-field preservation intact

### `APE-2` — Platform event registry (app_events.py) + emit sites + declared-subscription WS filter

**Status:** done

Session 1 T1.2; Contract C4 (typed platform events registry)

**Done when:** app_events.py registers session.created/knowledge.ingested/task.completed at their emit sites; a fixture app subscribed to task.completed receives it; an unsubscribed app never does; SEL clean

### `APE-3` — Background worker SDK (sdk/background.py) + backend_runtime supervised hosting

**Status:** done

Session 1 T1.3 + V1; Contract C2 (background worker SDK)

**Done when:** fixture app worker runs, survives a crash (watchdog), stops on disable; budget breach pauses it + notifies; V1: uninstall leaves no orphan worker (PPID-reaping verified)

### `APE-4` — quality manifest block + Store card rendering + first-party CI verification

**Status:** todo

Session 2 T2.1 + V2; Contract C1 (quality block)

**Done when:** a dishonest first-party quality declaration turns apps-repo CI red (tested=CI green, designSystem=token-lint pass, a11y=axe pass); Store cards render honest badges

### `APE-5` — Native capability contract: optional provider.py + native SDK subset + 2-3 exemplar bundles

**Status:** done

Session 2 T2.2

**Done when:** a native bundle gains a real provider method via the documented native SDK subset without core edits; apps import-boundary test still green

**Shipped 2026-08-16.** `apps/native_contract.py` is the contract: a bundled app may ship
its own module and point `provider.implementation` at a bundle-relative path
(`provider:create_provider`; the discriminator is "no dot"). `providers/loader.py` now
resolves the module by ONE rule for both tiers — a file inside the app's own dir loads from
there under a namespaced `sys.modules` name, anything else is a dotted package import —
which also *fixes a latent collision*: the old branch chose namespacing by TIER, so the
moment two bundles shipped `provider.py` one would have silently loaded the other's code.
The namespaced load is cached (the availability probe and the factory used to re-exec app
code, minting two classes for one provider).

**The "native SDK subset" is `personalclaw.sdk.*` — the same rule installed apps live
under, not a second, narrower list.** A bundled app is loaded by the same loader seam,
registered through the same typed handler and shipped by the same release, so a native-only
allowlist would be a second boundary to keep in step for no gain. What is native-specific
is documented as caveats, not import bans (in-process ⇒ no per-app backend env, so
`sdk.util.shared_app_data_dir` is always `None`; no own dependencies; packaged assets by
path are fine because the bundle ships in the same distribution).

**Exemplar: `personalclaw-ui-docs`** — `tool_providers/ui_docs.py` moved into
`apps/native/personalclaw-ui-docs/provider.py` importing `personalclaw.sdk.tool`, and
`create_ui_docs_provider` was deleted from `tool_providers/registry.py` (clean break, no
dual path). It then GAINED `ui_list` — the enumeration `ui_search` cannot do, since
`ui_search` requires a query, so an agent had to guess a keyword to discover a primitive
existed — with no edit to any core module that implements, resolves or dispatches it.

**DEVIATION — 1 exemplar, not 2-3.** A census of all 27 bundles measured why: every other
bundle's implementation depends on core beyond the SDK. `code_map` needs
`codegraph.CodeGraphIndex` + `config.loader.default_workspace_dir`; the 7 mcp-backed tool
bundles wrap `agents.native.tools.InProcessMcpToolProvider` over a core `mcp_*` module; the
8 action bundles are registered a SECOND time by `action_providers/registry.py` as
"intrinsic actions" bound to `guardrails.rungs.CORE_ACTION_TYPES`; `filesystem-inbox` is
core's own last-resort default (`inbox_providers/__init__.py`); the entity providers
(`tasks.native`, `prompt_providers.native_provider`, `skills.native`, `agents.marketplace`)
have 2-7 core importers each. Converting any of them needs new SDK exports or an untangling
that is its own atom — so the contract landed with the one bundle it fits, and the rail
makes each further conversion mechanical.

**DISCOVERY — the installed-app rail was vacuous here.**
`tests/test_apps_import_boundary.py` resolves the workspace `apps/` dir and
`pytest.skip`s the whole module when it is absent — which is the case in a standalone clone
AND in this project's own workspace, whose apps checkout is named `PersonalClawApps`. It
skipped for this run too (recorded green). `tests/test_native_capability_contract.py` never
skips and carries a vacuity floor (at least one bundled app must ship a module).

**DISCOVERY — one residual core touch for a new agent TOOL.** A new tool name still needs a
`manifest_meta.TOOL_META` entry, because that map is the hand-maintained input to the agent
manifest and `test_api_manifest_drift` fails on a tool without one (plus the generated
`src/personalclaw/reference/*`). That is catalogue data about the distribution's agent
surface, not provider implementation — so "no core edits" is exact for provider behaviour
and not yet exact for tool metadata. Bundle-declared tool metadata is the follow-up.

**DISCOVERY — the exemplar retired two inert SDK exports.** `inert-surface-baseline.json`
shrank by 2 (`sdk_export:ToolDefinition`, `sdk_export:ToolResult` on `sdk/tool.py`): no app
had ever imported them, and the bundled provider now does.

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

**Status:** done (PR #918)

Amendment 2026-07-26 T2.5 (Fix with AI; folds into Session 2)

**Done when:** a deliberately broken app's failed install offers the button; the opened chat (via ne:launch-chat) contains the log wrapped in fence_untrusted(source=app_install_log:<name>); fence verified

### `APE-9` — appMessaging permission + /api/apps/message gateway broker (double-declaration, fence, cap, SEL)

**Status:** done (PR #914)

Sessions 3-4 T3.1 + V3-4 (app-to-app demo half); Contract C3 (app-to-app broker)

**Done when:** two fixture apps exchange a typed message through the broker (V3-4: one app drives another); an undeclared pair -> 403 + SEL; payload capped and fenced; no direct app-to-app sockets

### `APE-10` — storageRead/storageShared manifest pair + consent + read-only env mount + sdk/util.shared_app_data_dir

**Status:** done

Amendment 2026-07-26 T3.2 (consented cross-app read; folds into Session 3)

**Done when:** fixture consumer reads the sharer's file via PERSONALCLAW_APP_SHARED_DIR_<NAME> (read-only); undeclared pair gets no mount; a write attempt fails; consent lists the grant (capability_grant SEL); import-boundary test green

The file-sharing mirror of APE-9's messaging broker, reusing its DOUBLE-DECLARATION shape rather than inventing a third seam. `Permissions` gained `storageShared: bool` (the SHARER opts in to exposing its own `app_data_dir`) and `storageRead: list[str]` (the CONSUMER names the apps whose data it reads — exact or trailing-`*`, same grammar as `appMessaging`), both wired through `to_dict`/`from_dict`. `PermissionChecker.can_read_shared_storage(target)` grants ONLY when the consumer names `target` AND `target`'s own manifest declares `storageShared` (deny-by-default, no one-sided grant); `can_expose_shared_storage()` is the sharer half. Enforcement is where storage is granted: `backend_runtime.shared_storage_env(consumer)` mounts each granted sharer's data dir READ-ONLY into the consumer backend as `PERSONALCLAW_APP_SHARED_DIR_<SHARER>` (upper-snaked via `manager.shared_dir_env_name`) and emits a `capability_grant` SEL per active grant; an undeclared pair yields no key. `sdk.util.shared_app_data_dir(name)` reads that env var and hands back a `_ReadOnlyPath` — reads pass through, every mutating op (and any child path) raises `PermissionError`; `None` when ungranted. Writes stay broker-only (APE-9). Both permissions reach install consent via `to_dict` → `catalog._manifest_consent` → the Store (`AppPermissionsWire` declares the pair — the two-sided key rail stays green — and `PermissionList` names the grants among the enforced bullets).

DONE 2026-08-15. Falsified both halves: dropping the sharer's `storageShared` check reds `test_undeclared_pair_gets_no_mount` + `test_sharer_without_storage_shared_is_denied`; returning a plain writable `Path` reds `test_write_to_shared_dir_fails`. Targeted gate green: `make lint` clean (flake8 + isort + mypy); `pytest -n 0 --no-cov tests/test_app*.py tests/test_apps_import_boundary.py tests/test_manifest*.py` → 334 passed / 2 skipped / 0 failed (includes the new `tests/test_app_shared_storage.py` 10-case suite, the `test_consent_wire_declares_exactly_the_permissions_the_server_emits` two-sided rail, and the apps import-boundary test).

### `APE-11` — UI SDK exports design-system shell primitives + tokens + uiCapabilities block + generative-widget path

**Status:** todo

Sessions 3-4 T4.1 + V3-4 (contributed-page half); Contract C1 (uiCapabilities)

**Done when:** a fixture app page renders using host Button/Surface/tokens via @personalclaw/app-sdk and is indistinguishable from a native page; generative-widget contribution path works

### `APE-12` — Disclose appMessaging targets at install consent (the last mile APE-9 left open)

**Status:** done (2026-08-13)

Traced defect, not a task row. `apps/manifest.py:313` said of `permissions.appMessaging`: *"This is the install-consent surface for who an app can talk to, shown in the Store via `to_dict`."* The server half was true — `to_dict` emits it, `catalog._manifest_consent` carries it into the Store's pre-install entry, and `GET /api/apps` returns it — but `AppPermissionsWire` (`web/src/lib/api.ts`) never declared the field and `PermissionList` never rendered it, so the browser received the targets and dropped them. Measured on the unchanged component with `{cron: true, appMessaging: ["receiver", "mail-*"]}`: the entire consent output was *"Permissions the gateway enforces • Scheduled jobs"* plus EI-12 D2's network advisory. A user installing that app was never told it may message `receiver` or anything named `mail-*`.

Owned here rather than reopened as APE-9 because APE-9's `done_when` covers the broker only (typed message exchanged, undeclared pair 403 + SEL, capped, fenced, no direct sockets) and every clause of it holds: the enforcement is real (`apps/messaging.py:167` → `can_use_app_messaging`). It was the manifest comment that overreached. Discovered while executing EI-12 D2 and recorded in that plan's log as needing its own atom.

Not the same shape as D2. D2 made `network` an **advisory** row because the platform cannot confine app egress; `appMessaging` IS enforced, so it belongs in the enforced bullets beside Storage / Scheduled jobs / Run background agents, and its copy does not hedge. The one thing borrowed from D2 is the treatment of silence: declaring no target is stated, because deny-by-default is the real behaviour and saying nothing would leave the user to guess. D2's load-bearing `Object.keys(permissions).length > 0` guard on the Store panel is untouched — `{}` still means "manifest not fetched OR declares nothing", and claiming "messages no app" about a manifest we never read would be a new false statement.

**Done when:** the Store names the apps a declaring app may message, in both surfaces `PermissionList` serves, among the permissions the gateway ENFORCES; a trailing-`*` target renders as the name prefix it is (mirroring `_matches_any`, since the grant covers every current and future app under the prefix) and a bare `*` as any installed app; declaring none is disclosed as messaging no app rather than left silent; `AppPermissionsWire` declares the field and a test pins the server leg end to end (the pre-install catalog payload AND `GET /api/apps`, not just the component in isolation), backed by a two-sided rail that the wire type declares exactly the keys `Permissions.to_dict()` can emit; `apps/manifest.py`'s comment is true afterwards. No first-party app declares `appMessaging`, so the rendering cases are synthetic manifests — stated plainly rather than implied — DONE 2026-08-13 (backend 18956 passed / 0 failed — baseline 18953 + 3 new; web 1832 passed across 192 files + 5 new; tsc + build clean; validated live in an isolated home)

