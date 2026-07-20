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
