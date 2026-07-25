# Changelog

All notable changes to PersonalClaw are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The in-app Updates panel reads this file (`GET /api/changelog`) to show "what's new."

## [Unreleased]

Forward-looking work is tracked in [docs/roadmap/](docs/roadmap/roadmap.md).

### Added

- **PersonalClaw describes its own UI kit, proposes capabilities you haven't
  tried, and hands external agents a routed project context.** Three legibility
  surfaces land together. (1) The `ui/` component kit is now self-documenting: each
  primitive ships a `.doc.ts` object (purpose, props, best-practice tenet) compiled
  into `ui-docs.json` at build time, and two agent tools — `ui_search(query)` for a
  budgeted brief and `ui_get(name)` for machine-readable props — let an app-building
  agent find the right primitive instead of hand-rolling chrome; a drift test fails
  the build if a primitive ships without its doc. (2) A dashboard **power-ups**
  widget surfaces ONE capability you've never used at a time — a two-sentence lesson
  plus a "try it" deep link — computed against your tool-usage history; it only
  points and hides (dismissals persist per capability), never enables anything, and
  the whole widget is behind the `legibility.power_ups` config flag. (3) PersonalClaw
  can act as a **routed-context provider** for external coding agents: per project it
  assembles a tiered manifest — hard rules/brief at the top, scored memories + skills
  + knowledge *pointers* in the middle, and an L0 catalog of what was NOT loaded (with
  the tool to pull each) at the bottom — exposed as the in-process `get_context` MCP
  tool and, opt-in per project (`legibility.context_adapters`, default off), rendered
  into the project's `CLAUDE.md` / `AGENTS.md` / `.cursorrules` inside a
  `<!-- PCLAW:START -->` fence that regenerates in place and never touches your own
  content outside the markers. Memory-derived and knowledge-derived content stay under
  distinct headings, and knowledge items render as titled pointers — never inlined
  bodies. A "Refresh context files" action on the project page (re)writes the block.
- **Apps surface their skills and backend routes to the agent (declared, not
  discovered).** An app now declares two legible surfaces in `app.json`, both
  readable without executing app code. `skills[]` names SKILL.md directories the
  app ships and OWNS: on enable they seed into the user skills tree **through the
  supply-chain chokepoint** (quarantine → scan at the app's trust tier →
  `.pclaw-lock.json` provenance) — an app skill never bypasses the gate just
  because it arrived inside an app — and are removed provenance-keyed on disable,
  never touching a user's own or another app's skill. `backend.routes[]` names the
  app's agent-callable HTTP surface (`op`, method, path, summary, param/body
  hints); one generic tool provider turns every enabled app's `agentCallable`
  routes into `app_<name>_<op>` tools (risk keyed off the verb) and drives them
  through the existing loopback reverse proxy, and a `call-app-route` action lets
  hooks/crons fire the same routes — both share one resolver so the callable gate
  can't diverge. The routes also render into `GET /api/manifest`'s `app_surfaces[]`
  (a non-callable route documents the surface with `tool: null`), and a declared
  route whose backend answers 404 raises a one-shot drift notification so a
  dead-declared route is caught the moment it's called. First-party Growth (17
  routes) and Minutes (24 routes) ship their route tables.
- **Offline agent reference + `pclaw-api` skill** — an agent driving PersonalClaw
  from outside a running gateway now reads exact tool/route signatures instead of
  guessing them. The distribution ships a generated markdown reference
  (`personalclaw/reference/`: every registered tool with its input schema +
  examples, the agent-callable HTTP routes, and the provider taxonomy) rendered
  from the same source as the live `GET /api/manifest`, plus a bundled `pclaw-api`
  operator skill (the never-guess-copy-it + verify-after-mutate discipline). Locate
  the files from the installed binary with the new `personalclaw doctor --paths`,
  which prints the resolved reference / config / skills / install directories. A
  drift test byte-compares the checked-in reference against a fresh render, so a
  tool or route added without its metadata reddens the build.
- **Render-smoke gate** (`npm run smoke:render`): the built SPA is now loaded
  in headless Chromium — key routes must mount real content with no uncaught
  errors — before any frontend-affecting push (repository-owned pre-push hook,
  `npm run hooks:install`) and on every PR (CI `web` job). Closes the
  verification hole behind the v0.1.0 blank dashboard, where typecheck, unit
  tests, and the production build all passed without ever rendering the
  artifact in a browser.

### Changed

- **The dashboard's system indicators are now a docked bottom rail.** The System
  strip (uptime, version, CPU/memory/network/disk/load, triggers, subagents, and
  the update action) was the last item in the scrolling column; it's now a
  shell-like rail pinned to the bottom edge, so the live indicators stay visible
  while the rest of the dashboard scrolls. The dashboard header's at-a-glance
  pulse strip sheds two now-redundant indicators: the gateway connectivity pill
  ("Live/Offline") and the gateway-version pill. The app shell's top-right corner
  already carries a live connectivity dot on every page, and its expanded system
  card now shows the gateway version (sourced from `/api/system`) — so the header
  strip is just the live count pills. The rail itself is width-responsive (a CSS
  container query, keyed to the content-width preset + sidebar, not the viewport):
  it sheds the decorative CPU sparkline and the metric word-labels — icon + value
  keep carrying the reading, with the full text on hover — to stay on one line as
  the available width tightens, and the "Details →" action stays anchored to the
  right edge.

## [0.1.1] — 2026-07-22

### Fixed

- **Blank dashboard in v0.1.0 (critical).** The released SPA crashed at first
  render with `TypeError: Cannot read properties of null (reading 'useContext')`
  — a dependency-group bump had split the installed tree across React 18 and
  React-DOM 19 (the classic dual-React invalid-hook failure), so every install
  kind (pip/uv, container, git) served an empty page. The web toolchain is
  reverted to its known-good React-18 set, a root npm `overrides` pins
  `@types/react`/`@types/react-dom` so transitive packages cannot drag React-19
  types back in, and the lockfile is regenerated from a clean install so the
  declared and resolved trees agree.
- **`monaco-editor` was never declared as a dependency** — it is a peer of
  `@monaco-editor/react` and imported directly, but resolved only by lockfile
  accident; a clean reinstall broke the build. Now a direct dependency
  (`^0.55.1`, the version v0.1.0 shipped transitively).

## [0.1.0] — 2026-07-19

### Added

- **App-contributed CLI seams** — an app can now hook into `personalclaw setup` and
  `personalclaw doctor` via manifest `cli.setup` / `cli.doctor` (`module:function`),
  and declare its log namespaces via `loggerRoots`. `personalclaw setup --app <name>`
  runs just one app's setup step. Core names no channel vendor in its CLI.
- **CI & release engineering** — GitHub Actions for both repos: `ci.yml`
  (lint/test/web/rails, ≤10-min budget) and `full.yml` (3.12/3.13 × ubuntu/macos
  matrix, audit, coverage) on core; manifest-validate/tests/boundary on the apps repo.
  A tag-triggered `release.yml` builds the wheel (with the prebuilt SPA) + multi-arch
  GHCR images, publishes to PyPI via Trusted Publishing behind an owner-approval gate,
  and attaches an SBOM + build-provenance attestations. `uv.lock` pins the dependency
  graph (CI installs `--locked`); Dependabot watches pip/npm/actions weekly. See the
  [supply-chain posture](README.md#supply-chain).

### Changed

- **Provider-boundary completion (Slack residue retired from core):** the Slack
  channel app now ships its own token/slash-command setup and doctor probe (via the
  new `cli.setup`/`cli.doctor` seams) instead of living hardcoded in core's CLI; app
  logger roots are derived from installed manifests (`constants.APP_LOGGER_ROOTS`
  removed); `slack-sdk` is no longer a core runtime dependency (kept as the `[slack]`
  extra, and the slack-channel app declares it via manifest `pythonDependencies`, which
  the app-install pipeline installs). A residue-sweep test + a machine-checked keeps
  table (`docs/architecture/provider-boundary-keeps.txt`) prevent vendor residue from
  regrowing in core.
- **LLM SDKs demoted out of core dependencies (`openai`, `anthropic`):** a bare
  `pip install personalclaw` no longer pulls the OpenAI or Anthropic SDKs. They now
  ship via (a) the `[openai]` / `[anthropic]` packaging extras for pip/uv users, and
  (b) the branded provider apps' manifest `dependencies.pythonDependencies`, which the
  app-install pipeline installs into the shared venv (plan 32 T2.1). The provider
  adapters import their SDK lazily and now raise a clear `MissingSDKError` naming the
  exact `pip install 'personalclaw[openai]'` remedy (and `personalclaw doctor`) when a
  hosted provider is used without its SDK. This trims the default install; users who
  install a provider app or the matching extra are unaffected (plan 34 T1.4).
- **Self-update is now install-kind aware (git · pip · container · desktop):** the
  in-app updater (Settings → Updates) and the update check no longer assume a git
  checkout. The availability signal is the **latest GitHub release tag** (ETag-cached,
  offline-tolerant) compared against the running version — tags are the release truth
  for every install path. Apply adapts to the install kind: a **git** checkout runs the
  existing pull → reinstall → rebuild → restart pipeline (with a new *Developer update
  mode* toggle, `dashboard.update_dev_mode`, to track every commit instead of only
  tagged releases); a **pip/uv/pipx** install runs `pip install -U personalclaw==<tag>`
  into its own interpreter and gracefully re-execs (no web build — the wheel ships the
  dashboard); a **container** install shows the exact `docker compose … pull && up -d`
  commands (no in-place apply); a **desktop** install delegates to the app shell. The
  Updates panel renders the right affordance per kind, and git installs also surface
  commits-behind as secondary info.

  This is a **clean break** (pre-1.0): the old git-only updater is replaced directly,
  not gated — LIFECYCLE-DOCTRINE's gate machinery is deferred, so there is no
  `update_kind_aware` gate to flip (owner decision 2026-07-20). Behavior change: a git
  checkout now updates on new *release tags* by default instead of every commit — flip
  *Developer update mode* on to restore per-commit updates. **Run `personalclaw
  snapshot` before updating.** (plan 34 S4.)

### Removed

- **`personalclaw gateway --slack-only`** — the legacy alias for `--headless` is
  removed. Use `--headless`.

### Fixed

- **Release wheel now bundles the SPA when built via `python -m build`.** The release
  pipeline (and `make build`) build the sdist first, then build the wheel from that
  sdist; the built `web/dist` was not included in the sdist, so the wheel-from-sdist
  shipped without the dashboard and failed `scripts/verify_wheel.py`. A new
  `MANIFEST.in` grafts `web/dist` into the sdist, which also makes the sdist itself
  self-contained (a wheel built from the PyPI sdist serves the dashboard too). Guarded
  by `tests/test_sdist_bundles_spa.py`. (plan 34; caught in the release dry-run.)


Initial public release — the first end-to-end PersonalClaw: a self-hosted, local-first,
provider-agnostic personal AI agent behind one gateway and one web dashboard.

### Added

- **Agentic chat** — multi-session chat with tool use and approval controls, session
  forking/undo, answer variants/regenerate, folders/tags/kanban, side conversations,
  per-session model and reasoning-effort overrides, and temporary/incognito memory modes.
- **Goal loops** — give the agent a target; it classifies, plans, and loops autonomously
  under a deterministic supervisor you can pause, nudge, or stop.
- **Memory** — layered semantic/episodic/procedural memory with active recall, after-turn
  learning from corrections, promotion of repeated facts, and an Obsidian-compatible vault.
- **Knowledge base** — document/media/web ingestion, AI enrichment, entity extraction, a
  knowledge graph, and semantic search wired into chat context.
- **Skills** — SKILL.md procedures with a marketplace, supply-chain scanning on install,
  session-scoped ephemeral skills, and an approval inbox for agent-proposed skills.
- **Automation** — cron/interval/webhook triggers, background subagents, a channel-watching
  inbox with drafted replies, and workflow SOPs surfaced on match.
- **App platform** — a permission-gated, scanner-gated Store: model providers, search,
  speech (STT/TTS), local models, channel connectors, agents, and full backend+UI apps,
  each installed through a quarantine → scan → consent lifecycle with subprocess isolation.
- **Agent runtimes** — the built-in native loop plus external CLI agents over ACP
  (Agent Client Protocol) as pluggable runtimes.
- **Model layer** — per-use-case model bindings (chat, background, embedding, ingestion,
  speech) over 16 provider apps; nothing is hardwired to a vendor.
- **Security** — four auth modes (loopback-forced `none`), command screening (denylist +
  suspicious-pattern watchers), an OS child sandbox, one egress chokepoint with host
  policy, untrusted-content fencing, a non-overridable "dangerous" install verdict, an
  HMAC-chained tamper-evident security event log, and credential-excluding exports.
- **Delivery surfaces** — local gateway, Docker Compose, systemd/launchd service install,
  a desktop shell, and portable snapshot/restore.

### Notes

- Single-user, self-hosted, MIT-licensed. **Zero telemetry** — no usage data leaves your
  machine.
- Requires Python 3.12+; a model-provider API key (or a local Ollama) to start chatting.

[Unreleased]: https://github.com/PersonalClaw/PersonalClaw/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/PersonalClaw/PersonalClaw/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/PersonalClaw/PersonalClaw/releases/tag/v0.1.0
