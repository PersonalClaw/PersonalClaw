# PersonalClaw

**Your self-hosted personal AI agent — an agentic operating system for one person.**

PersonalClaw is a local-first platform where you run AI agents that accomplish
your tasks with a rich set of pluggable capabilities: chat, autonomous goal
loops, long-term memory, a knowledge base, skills, scheduled automation, and
channel integrations — all behind one gateway process and one web dashboard you
own. The core is strictly provider-agnostic: every vendor (model providers,
search, speech, channels, agent runtimes) is a removable app, so nothing ties
you to a single LLM vendor or service.

<!-- screenshot placeholder: capture the home dashboard and drop it at docs/assets/screenshot-dashboard.png -->
![Dashboard screenshot placeholder](docs/assets/screenshot-dashboard.png)

## Highlights

- **Agentic chat** — multi-session chat with tool use, approval controls,
  session forking/undo, folders/tags/kanban, side conversations, and per-session
  model overrides.
- **Goal loops** — give the agent a target and let it work autonomously: it
  classifies the goal, plans it, then loops cycle by cycle under a deterministic
  supervisor you can pause, nudge, or stop.
- **Memory that learns** — layered semantic + episodic memory with active
  recall, after-turn learning from your corrections, automatic promotion of
  repeated facts, and an optional Obsidian-compatible markdown vault.
- **Knowledge base** — ingest documents (PDF/DOCX/PPTX/HTML/…), web pages, and
  media; AI enrichment, entity extraction, a knowledge graph, and semantic
  search wired into chat context.
- **Skills** — reusable procedures (SKILL.md) with a marketplace, supply-chain
  scanning on install, session-scoped ephemeral skills, and an approval inbox
  for agent-proposed skills.
- **Automation** — cron/interval/webhook triggers, background subagents, an
  inbox that watches channels and drafts replies, and workflow SOPs surfaced
  automatically when they match.
- **App platform** — model providers, search providers, STT/TTS, local models,
  channel connectors (e.g. Slack), and full backend+UI apps install from a
  Store, each permission-gated and sandbox-scanned.
- **Agent runtimes** — the built-in native loop, plus external CLI agents over
  ACP (Agent Client Protocol) as pluggable runtimes.
- **Security posture** — tool approval modes, a shell-command denylist, an
  egress guard with allow/deny host policy, a tamper-evident (HMAC) security
  event log, and app permission enforcement.
- **Yours** — single-user, self-hosted, one JSON config, portable snapshots,
  MIT-licensed.

## Quickstart

```bash
git clone https://github.com/PersonalClaw/PersonalClaw.git personalclaw && cd personalclaw
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
make web-build
personalclaw gateway
```

The dashboard opens at `http://localhost:10000`. Then install a model-provider
app from the Store, add your API key under Settings → Providers, and bind a chat
model under Settings → Models — full walkthrough in
[Getting started](docs/guides/getting-started.md).

## Documentation

- [Getting started](docs/guides/getting-started.md) — install → first chat.
- [Configuration reference](docs/reference/configuration.md) — every field,
  default, and where to set it.
- [CLI reference](docs/reference/cli.md) — every command and flag.
- [API overview](docs/reference/api-overview.md) — the REST/WebSocket surface.

## Roadmap

Forward-looking plans live in [docs/roadmap/](docs/roadmap/roadmap.md) — a
26-plan program centered on a composable workflow execution engine, organized
into pillars and execution waves.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the project doctrine (clean-break
changes, validation-as-a-user, code-truth over documentation), dev setup, and
PR expectations.

## License

[MIT](LICENSE)
