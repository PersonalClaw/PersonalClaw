# Getting started

PersonalClaw is a self-hosted personal AI agent: a local gateway process that
serves a web dashboard, runs agents with tools/memory/skills, and connects to
channels. This guide takes you from a clone to your first chat.

## Prerequisites

- **Python 3.12+**
- **Node.js 18+** (to build the web dashboard once)
- macOS or Linux
- An API key for at least one model provider (Anthropic, OpenAI, an
  OpenAI-compatible endpoint, AWS Bedrock credentials, or a local Ollama —
  anything from the Store's 16 model-provider apps)

## 1. Install

```bash
git clone https://github.com/PersonalClaw/PersonalClaw.git personalclaw
cd personalclaw

python3 -m venv .venv
source .venv/bin/activate

pip install -e .
```

This installs the `personalclaw` command (core dependencies only — provider
SDK extras like `boto3` for Bedrock install on demand or via
`pip install -e ".[bedrock]"`).

## 2. Build the dashboard

The web UI is a React SPA built once with Vite:

```bash
make web-build          # from the repo root  (or: cd web && npm ci && npm run build)
```

`make web-build` compiles `web/dist` and links it where the gateway serves it
from. Rebuilds are picked up live — no gateway restart needed for UI changes.

## 3. First run

```bash
personalclaw gateway
```

The gateway binds to port **10000** by default (`--port` or `PERSONALCLAW_PORT`
to change) and opens the dashboard in your browser (`--no-open` to skip). All
state lives under `~/.personalclaw/` (relocatable with `PERSONALCLAW_HOME`).

If you need the URL again later — it is token-authenticated — run:

```bash
personalclaw token
```

which prints a ready-to-open URL with a fresh auth token.

First-run onboarding in the dashboard asks for your name and walks you to
provider setup.

## 4. Configure a model provider

Model providers are installable apps — nothing is hardwired to a vendor.

1. Open **Apps** (the Store) in the dashboard sidebar.
2. Install the provider app for your vendor (e.g. *Anthropic Models*,
   *OpenAI Models*, *Bedrock Models*, *Ollama Models*, or *OpenAI-compatible*
   for any compatible endpoint).
3. The provider appears under **Settings → Providers** — add your API key /
   endpoint there and hit **Test** to verify connectivity.
4. Go to **Settings → Models** and bind a model to the **chat** use case
   (bindings live in `~/.personalclaw/active_models.json`, not `config.json`).
   The same panel binds models for background work, embeddings, ingestion,
   speech, and more — they can all be different providers.

Prefer the terminal? `personalclaw setup` runs a credential wizard, and
`personalclaw doctor` verifies the result end to end.

## 5. First chat

Open the dashboard's **Chat** page and send a message — or from the terminal:

```bash
personalclaw chat -m "hello"
```

Tool calls the agent wants to make appear as approval prompts (default
`agent.approval_mode: auto`; see the
[configuration reference](../reference/configuration.md) to tune approval,
sandboxing, and security policy).

## Where to go next

- **Explore the platform** — Skills, Agents, Tasks, goal Loops, Knowledge,
  Memory, Inbox, Triggers, and Workflows all live in the sidebar; each page has
  inline explanations.
- **Install more apps** — search providers, speech (STT/TTS), local models,
  channel connectors (e.g. Slack), and agent runtimes are all Store apps.
- **Run it permanently** — `personalclaw service install` registers a systemd
  unit (Linux) or launchd agent (macOS) so the gateway survives reboots. Docker
  Compose files live under `deploy/compose/`.
- **Back it up** — `personalclaw snapshot` creates a portable state archive;
  `personalclaw restore` brings it back.

## Reference docs

- [Configuration reference](../reference/configuration.md) — every config field,
  its default, and where to set it.
- [CLI reference](../reference/cli.md) — every command and flag.
- [API overview](../reference/api-overview.md) — the full REST/WS surface.
- [Roadmap](../roadmap/roadmap.md) — where the project is heading.

## Troubleshooting

- **Dashboard shows nothing / 404 assets** — the SPA isn't built or the dist
  link is stale: run `make web-build`, then restart the gateway if it was
  already running when the dist directory changed.
- **"Gateway not running" from CLI commands** — `status`/`stop`/`token` need a
  live gateway on the resolved port; pass `--port` if you changed it.
- **Backend code changes don't take effect** — Python changes need a gateway
  restart (`personalclaw restart`); only frontend rebuilds are live.
- **Model errors in chat** — check **Settings → Models** has a chat binding and
  the provider's **Test** passes; `personalclaw doctor` reports the live
  binding.
