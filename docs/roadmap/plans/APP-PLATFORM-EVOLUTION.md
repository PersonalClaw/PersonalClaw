# APP-PLATFORM-EVOLUTION

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/APE.md`](../atomic/APE.md) as 12 atomic plan(s).

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

### Coordination — app-contributed agent worlds (inbound from AMBIENT-SURFACES `A2-3`)

**Doc note, not a task row here yet.** `AMBIENT-SURFACES` `A2-3` shipped the *seam* for
agent worlds — the `AgentActivityFeed` contract, the `useAgentActivity()` hook and one
first-party world — and explicitly left the app-contributed half to this plan. The
contract is stable and needs no change to accept a third-party world; what lands here is
a UI-module kind plus the host plumbing.

Full note, with the four things this plan has to add and why each one is a hazard if
skipped: [`docs/architecture/agent-activity-feed.md`](../../architecture/agent-activity-feed.md)
§"Coordination note — app-contributed worlds". The short version:

- A **`world` UI-module kind** — render-only, declaring **no** `permissions.api` and no
  `permissions.events`. Folding the data behind the contract is what makes a world the
  cheapest app contribution a user can consent to; a world that fetched would undo that.
- **The feed is passed IN, never fetched.** The host owns one `useAgentActivity()`
  instance. Two mounted worlds must not become two sockets and eight polls.
- **The host keeps the chrome** — `role="img"` name, the visible summary sentence, the
  empty/unknown states, and the reduced-motion decision passed in as a flag. A world
  must not be able to opt out of the accessibility or motion contract by omission.
- An app contributing **entities into** the feed is a *different, larger* question (it
  widens `AgentActivityKind`, which every existing world switches on) and should be its
  own atom after the render-only kind ships. No new provider type is needed for the
  read path.

## Execution log
- [2026-08-24][S2 · atom `APE-4`] **PARTIAL — the core half is complete and railed; the apps-repo CI job is
  the one clause this repo cannot land.** Shipped: `quality` on `AppManifest` (`QualityDeclaration`,
  round-trip + `_KNOWN_FIELDS` + `designSystem` enum validation), the verifier
  `apps/quality.py` (`python -m personalclaw.apps.quality <tree>`), the wire on BOTH app payloads
  (`/api/apps` via `_quality_wire`, `CatalogEntry.quality` at all three construction sites), and
  `web/src/pages/apps/qualityBadges.tsx` rendered on the Store card + both detail panels.

  **The load-bearing artifact is not the schema and not the badge — it is that a lie fails the build**,
  so each axis was proven by planting one and observing the red, each paired with an honest-declaration
  floor that stayed green (`tests/test_app_quality_enforcement.py`, 36 tests):
  `tested: true` with no `test_*.py` (caught statically — the runner raises if consulted) and with tests
  that FAIL; `designSystem: "v2"` on a frontend carrying a raw hex + inline px; `a11y: true` with axe
  violations, with no report, with a report that cannot say "zero", and with a report produced against a
  PREVIOUS version (freshness — one honest scan must not launder every later release).

  **Two vacuity traps closed on purpose.** (a) A claim with *nothing to check* is a violation, not a free
  pass: `"v2"` with no frontend to lint would otherwise earn the badge because the lint found no files to
  fail. (b) The CLI exits 1 on a tree with no `*/app.json` at all — a checker that silently checked
  nothing is precisely this atom's defect class.

  **Absent ≠ false ≠ passing, held at three layers.** `from_dict` keeps `quality` as `None` when the block
  is absent (a mutation collapsing it to an all-false block reds two tests); `to_dict`/`_quality_wire` emit
  only DECLARED axes, so an undeclared `a11y` never arrives as `false`; and `qualityBadges` renders a met
  badge, a distinct muted MISS badge, and *nothing at all* — with one test asserting the three rendered
  markups are pairwise distinct, which none of the individual assertions can do alone.

  **One token-lint rule, not two.** Verifying `designSystem` from Python needed the host's lint, so the
  patterns became data — `apps/token_lint_rules.json`, packaged — with a thin consumer on each side
  (`apps/quality.py`, the extracted `web/src/design/tokenLintRule.ts` that `tokenLint.test.ts` now
  imports). Re-implementing the regexes would have minted a second dialect of the same lint: the exact
  declared-vs-actual drift this atom exists to prevent, one layer down. `tokenLintRuleParity.test.ts` pins
  the two byte-for-byte AND behaviourally over a corpus with known per-line verdicts; drifting the TS
  `HEX` from `{3,8}` to `{6,8}` reds it.

  **FINDING — `text-positive` emits no CSS, and the card already relies on it.** The first badge draft used
  `text-positive`; `design/inertUtilities.test.ts` caught it as a utility that compiles to nothing. The
  allowlist confirms it is a known BUG (`text-positive -> text-ok`) and that
  `pages/apps/AppsSection.tsx` is already listed for it — i.e. **the Store card's existing green
  "Installed" affordance is unstyled today**. The badge now uses the real `text-ok`; fixing the
  neighbouring `text-positive`/`text-negative` is a visible behaviour change the allowlist explicitly
  reserves for its own change, so it was NOT folded in here.

  **FINDING — an uncapped spawn needed classifying, and its exemption got a premise rail.**
  `run_bundle_tests` spawns `python -m pytest <bundle>`, which `test_spawn_ceiling_audit` reds as an
  unmapped spawn site. Classified operator-exempt on the ground that `apps.quality` has ZERO runtime
  importers — and that ground is now itself a test (`test_the_verifier_has_no_gateway_call_site`,
  AST-scanned, falsified by adding an importer to `apps/manager.py`), so wiring the verifier into a
  request path forces the classification to be re-argued instead of silently inherited.

  **FINDING — the CI-call-site test was a fake green under `make test`.** `python -m
  personalclaw.apps.quality` in a child process resolved `personalclaw` through the venv's editable
  install (the MAIN checkout), not the worktree, so the child exited 1 for *"no such module"* while the
  test read exit-1 as *"caught the liar"* — a green proving the opposite of its claim. The child's
  `PYTHONPATH` is now pinned to the tree the test imported from.

  **MEASURED on the real first-party tree (read-only, nothing committed there): 45 bundles, not the
  plan's "36".** 43 of 45 ship test files (`alibaba-models` and `meta-muse-spark` do not, so
  `tested: true` on either is a statically-caught lie). Exactly TWO ship frontend source — `growth`
  (8 token-lint violations) and `minutes` (9) — so `designSystem: "v2"` on either is a lie **today**, and
  the honest value for both is `"legacy"` until `APE-6` lands; that is the atom's real-world red, not just
  a fixture's. ZERO ship an axe report, so `a11y: true` is currently unearnable by any first-party app.
  Nothing declares `quality` yet, so the new job is green on day one and adoption is opt-in per app.

  **NOT MET — the apps-repo CI job (cross-repo, deliberately not committed).** `PersonalClawApps` needs
  ONE job added to `.github/workflows/ci.yml`, mirroring the existing `manifest-validate` job's
  uv-venv + `$CORE_SPEC` install and then `needs: [tests]` (so a declaring app whose suite is red cannot
  reach a green quality job), running `python -m personalclaw.apps.quality .` from the repo root. An app
  declaring `a11y: true` additionally has to produce `a11y/axe-report.json`
  (`{"appVersion", "tool", "violations": []}`) from its own harness. Until that job exists the enforcement
  is proven only in core; the verifier, its exit codes and all three reds are pinned here, and the
  remaining delta is a workflow step, not logic.

  Gates: `make lint` clean (mypy 996 files) · `make test` **25761 passed / 0 failed** / 30 skipped /
  12 xfailed · full web suite **476 files, 4999 tests, all passed** · `typecheck:web` + `npm run build`
  clean · `gate_report.py` 6/6 PASS. `docs/design/consistency-audit.json` moved by one `filesScanned`
  (551→552) with driftHits unchanged at 8 — the generated reporter noticing the new component.
- [2026-08-18][S1 · atom `APE-2`] **DONE** — `apps/app_events.py` registers the three platform events, each
  **at the site the fact becomes true**: `session.created` at `dashboard/state.py:1619` (right after
  `self._sessions[name] = session`), `knowledge.ingested` at `knowledge/pipeline/runner.py:349` (the same
  terminal point the SSE `ingest_complete` fires from, so the app-facing fact and the UI-facing one cannot
  disagree), `task.completed` at `tasks/native.py:403` (the same edge-triggered boundary
  `pool.should_fire_completion` already gates the `TaskComplete` user hook on — one edge, two observers).
  Each is proven by driving the PRODUCTION function (`get_or_create_session`, `ingest_item`, `update_task`),
  not by calling `emit` and trusting a call site exists; deleting any one of the three reds its own test.
  **Reused, not reinvented — there was no platform bus to extend.** Measured first: `state.broadcast_ws` is the
  only fan-out in core and it is the WS/dashboard path gated by `can_use_event`; the three names appeared
  nowhere in `src/` except APE-1's comment. So a registry was necessary — but the TRANSPORT was not. A
  delivered event is appended to the app's existing broker-owned inbox (`apps/messaging._append_to_queue`,
  `config_dir()/app_messages/<app>.json`) which the app already drains read-once over `GET /api/apps/message`.
  That buys the depth cap, the atomic write and a live consumer for free, and adds no route.
  **DEVIATION (deliberate) — the atom title says "WS filter"; delivery deliberately does NOT touch the WS
  path.** Routing platform subscriptions through `state.broadcast_ws`/`_app_may_see_event` would have merged
  `eventSubscriptions` into `permissions.events` — exactly what APE-1 closed. The filter instead sits at
  dispatch, on its own accessor.
  **The gate APE-1 deferred now exists where it gates:** `PermissionChecker.can_receive_platform_event`
  (`apps/permissions.py:109`), consulted at `apps/app_events.py:211` — deny by default and **exact match only**
  (like `desktop`, unlike `api`/`events`), so `task.completed` never matches `task.completed.extra` or `task.*`.
  A disabled app is not a subscriber. `permissions.py`'s enforcement roll-call gained the bullet.
  **FINDING — APE-1's axis-separation test only covers ONE direction.**
  `test_event_subscriptions_do_not_widen_the_ws_event_allowlist` asserts a subscription does not grant the WS
  type; it says nothing about the reverse. Falsified: making `can_receive_platform_event` also read
  `permissions.events` left that test GREEN. The new
  `test_the_two_event_vocabularies_stay_separate` asserts both directions and reds.
  **DISCOVERY — `get_or_create_session` is ALSO the rehydration path, so a naive emit re-announced every
  restored session on every restart.** `chat_persistence.restore_recent_sessions` (bulk, at startup),
  `_rehydrate_session_from_history`, and the resume / post-to-an-old-session routes all reach its CREATE branch
  for a session that already exists on disk — a subscribed app would have double-counted sessions it already
  saw, once per gateway restart. Guarded on "has no persisted conversation metadata"
  (`DashboardState._has_persisted_history`, asked provider-agnostically via `resolve_history_key`, so a channel
  thread counts as persisted too) rather than on a `restored=True` kwarg: a flag would have to be threaded
  correctly through all eight call sites and the post-to-an-old-session path would have been wrong on day one.
  Fails OPEN (an unreadable log reads as "no history"), so the worst case is a re-announcement, never a
  swallowed creation.
  **DISCOVERY — `knowledge.ingested` was firing for FAILED runs, and its exits disagreed with each other.** The
  runner has three earlier failure exits that return before the emit site, so a graph-build failure or a
  mid-pipeline exception announced nothing while a run whose nodes all failed on the normal terminal path
  announced `status: "failed"` under a name that says "ingested". Narrowed to `status in ("done", "partial")` —
  `partial` is in the store and searchable, `failed`/`unreachable` ingested nothing — so now no failure
  announces, from any exit. A `status` field is not a licence to fire the wrong event. Pinned with a real
  failing node on the normal terminal path; the early exits would have passed the test under either design.
  **Payloads carry identifiers, never prose — a security rule, not tidiness.** The registry pins each event's
  payload keys and an undeclared key is dropped. If an event carried a task title or an item's text, a
  subscribed app with no matching `permissions.api` grant would receive content it cannot otherwise read, and
  the subscription would silently widen `can_use_api`. A subscription grants **timing, not content**.
  Residual, disclosed rather than hidden: `session.created` carries the session NAME, because the name is its
  address in this codebase — user-authored text, capped at 200 chars, and named verbatim at install consent.
  **SEL decision (deliberate): ordinary fan-out and ordinary non-delivery write NOTHING.** An app never
  *requests* a platform event — dispatch is host-initiated — so a non-delivery is not an access attempt, and one
  row per (installed app × emitted event) would drown the real rows in the HMAC chain. Audited instead: an emit
  naming an **unregistered** event (`outcome="rejected"`, a code defect that would otherwise vanish) and a
  delivery that **failed for a subscriber** (`outcome="error"`, an app silently missing an event it earned).
  Both bounded and rare; the "zero rows" assertion has a positive control in the same file, so it is not vacuous.
  **Consent surface moved with the enforcement (the D2 defect, inverted).** APE-1 put `eventSubscriptions` in
  the Store's *"Declared, not yet in effect"* block because nothing delivered. It is enforced now, so leaving it
  there would understate a live capability and the user would weigh a real grant as disclosure-only.
  `installConsent.tsx` moves it into the enforced bullets (naming every declared event) and drops the "or
  deliver platform events yet" clause from the pending caption; `backgroundTasks` stays pending until `APE-3`
  ships a host. `manifest.py`'s field comment no longer says "NOT ENFORCED TODAY" — the permission fields
  themselves are untouched.
  Falsified eight ways, each restored from a file copy: removing the dispatch check delivered `task.completed`
  to all four installed apps (`['listener','nearmiss','quiet','wildcard']`); swapping exact match for
  `_matches_any` leaked to the `task.*` subscriber; merging the two axes reds only the new test; deleting each
  of the three emit-site calls reds only that site's test; removing the rehydration guard re-announced a
  restored session; removing the ingested-status guard announced a failed run.
  Gate: `make lint` 0 (mypy 912) · 320 targeted (incl. `test_inert_surface_baseline.py` — no counter rose;
  the registry has three live emit sites and a live reader) · full `make test` 22196 passed / 30 skipped /
  12 xfailed · web 400 files / 4048 tests · typecheck + build clean. Not live-driven in a browser: the delivery
  evidence is the real drain route (`GET /api/apps/message`) against really-installed fixture apps, and the
  consent claim rests on component tests plus the server wire rail.

- [2026-08-18][S1 · atom `APE-1`] **DONE** — `permissions.backgroundTasks` (bool) and
  `permissions.eventSubscriptions` (list of exact platform-event names, **no wildcard**) parse, serialize and
  disclose. Round-trip is a fixed point for declared / absent / empty, and absent-or-empty emits **no key** —
  a spurious `backgroundTasks: false` would render as a grant the app never made. Verified on the server legs
  too (`catalog._manifest_consent` and a real `GET /api/apps`), not just the dataclass, because the wire has
  dropped a server-emitted field before.
  **DISCOVERY — nothing enforces either grant today, and the copy says so.** No core code hosts an app worker
  (that is `APE-3`) and nothing delivers a platform event (`APE-2`), so the consent surface gets a third,
  declared-only block reading *"Declared, not yet in effect … this grants the app nothing today — it is
  disclosure, not capability. It takes effect without asking you again once that support ships."* Disclosed
  because the declaration is a standing grant that goes live with **no second consent prompt**; kept OUT of the
  enforced bullets, pinned by a rail that reds if either is pushed into them.
  **DEVIATION (deliberate):** no `can_use_*` accessor was added. Every existing checker in `apps/permissions.py`
  has a live call site; an accessor with none is an enforcement point that enforces nothing, so `APE-2`/`APE-3`
  should add the check where it gates. `permissions.py` is untouched despite T1.1's file list naming it.
  **Contract pinned:** `eventSubscriptions` is NOT `events` — declaring `session.created` does not widen the
  gateway's WS allowlist (`test_event_subscriptions_do_not_widen_the_ws_event_allowlist`), or `APE-2` would
  inherit a second, wider path to the same data.
  **Coherence note for `APE-3`:** there are now three adjacent "run something unattended" grants — `cron`,
  `agent`, `backgroundTasks`. Do not mint a fourth.
  Gate: `make lint` 0 (mypy 909) · 88 targeted · full `make test` 22079 passed · web 391 files / 3980 tests ·
  typecheck + build clean. Not live-driven in a browser: the consent claim rests on component tests, the HTTP
  payload leg and the two-sided wire rail.


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

- **2026-08-23 — `APE-3` COMPLETE (all five clauses, proven with real processes). Atom stays `todo` only
  because this code is unmerged**; flip it when the PR lands.
  `APE-1` shipped the `backgroundTasks` permission and `manifest.py` said so in its own comment: *"NOT
  ENFORCED TODAY, and honestly so: nothing in core hosts an app worker yet"*, and *"unlike
  ``backgroundTasks`` above, **whose host still does not exist**"*. This is the host.
  **END TO END, with a real app rather than a fake:** a fixture app declaring the permission and shipping a
  `worker.py` written against the SDK contract:

      declared_workers -> [WorkerSpec(name='worker', entry_point='worker.py', restart=True)]
      start()          -> 1 worker, pid 73527, alive
      kill -9 + sweep  -> revived, old_pid=73527 new_pid=73529
      revoke backgroundTasks + sweep -> still supervised: False
      stop()           -> nothing alive

  **The worker shape is a unit of work, not a loop.** `BackgroundWorker.run_once(ctx)` returns and
  `run_worker` owns the loop, because a host that must both STOP and PAUSE needs a moment when the worker
  is between units and the control state can be consulted. It is an entry script under `sys.executable`
  with its context handed over in env, matching `_launch_cmd`'s existing convention rather than inventing a
  second launch mechanism.
  **Pause and stop are distinct states with three enforced asymmetries**, each of which is a way a naive
  single flag fails in production rather than in a test. Probed live: `resume()` after a stop stays
  `STOPPING` (resume cannot undo a stop); `pause()` after a stop stays `STOPPING` (stop wins, or an
  uninstall leaves an orphan); and `request_stop()` **releases** a paused worker (or budget-pause then
  disable wedges forever). `STOPPING` ≠ `STOPPED`: the first is the graceful window, the second is when
  reaping is safe.
  **THE CROSS-BRANCH DEFECT, and why lint could not see it.** The runtime imported three names the
  contract never defined — `WorkerSpec`, `declared_workers`, `WORKER_NAME_ENV`. Both branches were green
  and `make lint` clean on each, because `pyproject.toml` sets `ignore_missing_imports = true` and a
  missing MODULE is invisible to mypy; the runtime agent reported observing **silence, not a red**. Three
  decisions resolved it:
  1. **`WORKER_NAME_ENV` was a second spelling of an existing variable** — its stub value
     `"PERSONALCLAW_APP_WORKER"` is exactly what the contract exports as `WORKER_ID_ENV`. Collapsed onto
     the one name; no alias.
  2. **`WorkerSpec`/`declared_workers` are parent-side and were re-homed into `worker_runtime`.** An app
     author never reads them, and routing them through the app-facing SDK facade would export names with
     no app-side consumer — which the inert-sdk-export gate proved by failing the moment I tried it.
  3. **The declaration source did not exist.** `APE-1` shipped only a boolean: no worker list, no entry
     path. `manifest.py`'s own comment settles it — an app *"may register **a** long-lived supervised
     worker"*, singular — so the permission IS the declaration and `worker.py` is the filename it implies.
     An app that grants the permission and ships no `worker.py` simply has no worker, which beats a
     manifest field that can disagree with the files on disk.
  That third decision created a fresh drift risk (the filename an app is told to use vs the one the
  supervisor resolves), now pinned by a rail that doubles as the SDK constants' real consumer.
  **Two full-suite-only gates, both real.** The inert-surface baseline caught two SDK exports with no
  consumer, and the SDK agent **fixed them at the source rather than regenerating**: an inert
  `StopReason.UNINSTALLED` was REMOVED (from the child's vantage an uninstall is a disable that never comes
  back; the part that differs is parent-side PPID reaping) and `DEFAULT_POLL_INTERVAL` got a real reader.
  It then correctly diagnosed a THIRD failure as downstream of those two — a structural-baseline test
  asserting *"3 of 6 gate(s) FAILED"* saw 4, because its other entries are the test's own synthetic
  injections.
  **DISCOVERY — the spawn-ceiling audit pins the CLASSIFICATION, not the CEILING.** Adding
  `apps/worker_runtime.py::WorkerSupervisor._spawn::subprocess.Popen` to
  `tests/test_spawn_ceiling_audit.py` was required (a full-suite red). But measured: deleting
  `spawn_shim_argv` from `_spawn` entirely and launching the bare command leaves that audit **fully green,
  3 passed** — so its entry reading *"tool ceiling via spawn_shim_argv"* is a claim nothing checked. An
  unceilinged worker matters more than an unceilinged one-shot: it is long-lived and unattended. A
  behavioural AST rail now lives in this atom's own suite (removing the shim reds 1 of 16); widening the
  shared audit is deliberately left to its owner rather than rewritten at the end of a tick, since it would
  red every other site that is classified but unverified — a discovery, not a regression.
  *That rail was itself broken on first run:* `ast.parse` on an indented METHOD source raises
  `IndentationError`, making it a permanent false RED until dedented. Fail-closed is the safe direction for
  a rail to break, and running it is what found it.
  **The consent surface moved, because the manifest comment demanded it.** `APE-1` deliberately kept
  `backgroundTasks` out of "Permissions the gateway enforces" — listing an unenforced grant there is the
  EI-12 D2 defect — and put it in a "Declared, not yet in effect" box, rendered as divs so
  `permissionConsent.test.tsx`'s `enforcedRows` (which reads every `<li>` as enforced) stayed true. With a
  host shipping, leaving it there would UNDERSTATE a live capability: the mirror image of that defect. So
  it joins the bullets, exactly the move `APE-2` made for `eventSubscriptions`, and the box is **deleted**
  — it had one feeder, and a box that renders for nothing is where a future grant falls silently. Two tests
  asserting the old truth were restated with what they used to claim recorded.
  **Wiring, and where.** `providers/loader.py` starts the sweep at boot beside the backend watchdog, with
  no paired `start_enabled_app_workers()` — the sweep is self-healing, and a second boot entry point could
  disagree with it about who should be running. `app_manager._stop_worker` runs on the same
  disable/uninstall path as `_stop_backend`, and that placement is load-bearing: `reap_orphans` needs the
  entry path, which is only resolvable while the app directory still exists. The sweep alone cannot deliver
  the V1 clause, because a process re-parented to init is in no supervisor's table and nothing would look
  for it once the directory is gone.
  **`permissions.can_run_background_tasks()`** is now the accessor the host consults, so the grant denies
  as well as declares — and it is re-asked at every spawn, which is why revoking it in an app update stops
  the next revival and not merely the first launch (a gap the runtime's own tests caught mid-build).
  **Reaping is reused, not re-derived:** `WorkerSupervisor.reap_orphans` delegates to
  `BackendSupervisor.reap_orphans`, keeping ONE PPID walk for the tree. A second walk that disagreed with
  the first is how a live gateway's children — or a concurrent test run's — get killed.
  **Honest limits, recorded rather than glossed.** "Alive" is `proc.poll() is None` and nothing stronger: a
  worker has no inbound surface, so a wedged-but-running worker is invisible here, and telling it apart
  from one sleeping between units needs a declared heartbeat interval that is not in the manifest. The
  budget breach is measured against the meter's **DAY** scope, not per-app: nothing charges a per-worker
  key today, so a per-app ceiling does not exist in usable form and none was invented — `ModelCallGuard`
  already refuses the worker's individual calls, and the supervisor stops the process spinning against the
  wall and says why it went quiet. `stdout`/`stderr` go to `DEVNULL` matching backend precedent, so a
  crash-looping worker's traceback is unavailable; a per-worker log file is a named follow-up.
  **DISCOVERY (pre-existing): `docs/architecture/app-platform.md` said "`sdk/` (26 modules)" and was
  already stale by 6** before this atom (32 on `main`). Corrected to 33. Nothing asserts the number.
