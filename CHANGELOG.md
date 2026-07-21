# Changelog

All notable changes to PersonalClaw are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The in-app Updates panel reads this file (`GET /api/changelog`) to show "what's new."

## [Unreleased]

Forward-looking work is tracked in [docs/roadmap/](docs/roadmap/roadmap.md).

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

## [0.1.0] — 2026-07-19

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

[Unreleased]: https://github.com/PersonalClaw/PersonalClaw/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/PersonalClaw/PersonalClaw/releases/tag/v0.1.0
