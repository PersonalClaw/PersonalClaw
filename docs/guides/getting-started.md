# Getting started

PersonalClaw is a self-hosted personal AI agent: a local gateway process that
serves a web dashboard, runs agents with tools/memory/skills, and connects to
channels. This guide takes you from **nothing installed** to your first chat.

> **Pre-1.0:** PersonalClaw is pre-1.0 and moves fast; releases may make
> breaking changes. Run `personalclaw snapshot` before upgrading.

## Prerequisites

- macOS or Linux (Windows: use the Docker Compose path below)
- An API key for at least one model provider (Anthropic, OpenAI, an
  OpenAI-compatible endpoint, AWS Bedrock credentials, or a local Ollama —
  anything from the Store's model-provider apps)

You do **not** need to install Python or Node yourself for the recommended
paths: `uv` provides its own Python 3.12, and the release wheel ships the
prebuilt dashboard. (Contributors who build from source need Python 3.12+ and
Node 18+ — see [CONTRIBUTING](../../CONTRIBUTING.md#development-setup).)

## 1. Install

Pick one path. All of them install the **same release artifact** — there are no
per-channel special builds.

| Path | Command | Best for |
|---|---|---|
| **uv tool** *(recommended)* | `uv tool install personalclaw` | anyone — `uv` brings its own Python 3.12 |
| **Bootstrap one-liner** | `curl -fsSL https://personalclaw.dev/install \| sh` | fastest start; installs `uv` if absent, then the above |
| pipx | `pipx install personalclaw` | Python users who like isolated tools |
| pip | `pip install personalclaw` | inside an existing Python 3.12+ venv |
| **Docker Compose** | see [§ Docker](#docker-compose) | self-hosters; Windows |
| Git checkout | see [CONTRIBUTING](../../CONTRIBUTING.md#development-setup) | contributors / development |

After a uv/pipx/pip install the `personalclaw` command is on your PATH:

```bash
uv tool install personalclaw
personalclaw setup      # interactive: name + first provider credential
```

### Optional extras

The base install is lean. Add an extra only if you need what it unlocks (most
users install provider **apps** from the Store instead — the app pulls its own
dependency; extras are the plain-pip path):

| Extra | Install | Unlocks | Weight |
|---|---|---|---|
| `openai` | `pip install 'personalclaw[openai]'` | the OpenAI SDK (chat/embeddings/STT/TTS) | small |
| `anthropic` | `pip install 'personalclaw[anthropic]'` | the Anthropic SDK | small |
| `bedrock` | `pip install 'personalclaw[bedrock]'` | AWS Bedrock (`boto3`) | medium |
| `mcp` | `pip install 'personalclaw[mcp]'` | Model Context Protocol servers/tools | small |
| `js-render` | `pip install 'personalclaw[js-render]'` | JS-rendered web fetch (Playwright) | large (browser) |
| `models` | `pip install 'personalclaw[models]'` | local inference: embeddings + STT + TTS | large (ML) |

> With `uv tool`, add an extra with `uv tool install 'personalclaw[bedrock]'`.
> `personalclaw doctor` reports which optional dependencies are missing and
> prints the exact command to add them.

## 2. First run

```bash
personalclaw gateway
```

The gateway binds to port **10000** by default (`--port` or `PERSONALCLAW_PORT`
to change) and opens the dashboard in your browser (`--no-open` to skip). All
state lives under `~/.personalclaw/` (relocatable with `PERSONALCLAW_HOME`).

If you need the URL again later — it is auth-gated — run `personalclaw token`,
which prints a ready-to-open URL with a fresh credential.

First-run onboarding in the dashboard asks for your name and walks you to
provider setup.

## 3. Configure a model provider

Model providers are installable apps — nothing is hardwired to a vendor.

1. Open **Apps** (the Store) in the dashboard sidebar.
2. Install the provider app for your vendor (e.g. *Anthropic Models*,
   *OpenAI Models*, *Bedrock Models*, *Ollama Models*, or *OpenAI-compatible*
   for any compatible endpoint). The app installs its own SDK dependency.
3. The provider appears under **Settings → Providers** — add your API key /
   endpoint there and hit **Test** to verify connectivity.
4. Go to **Settings → Models** and bind a model to the **chat** use case
   (bindings live in `~/.personalclaw/active_models.json`, not `config.json`).
   The same panel binds models for background work, embeddings, ingestion,
   speech, and more — they can all be different providers.

Prefer the terminal? `personalclaw setup` runs a credential wizard, and
`personalclaw doctor` verifies the result end to end.

## 4. First chat

Open the dashboard's **Chat** page and send a message — or from the terminal:

```bash
personalclaw chat -m "hello"
```

Tool calls the agent wants to make appear as approval prompts (default
`agent.approval_mode: auto`; see the
[configuration reference](../reference/configuration.md) to tune approval,
sandboxing, and security policy).

## Docker Compose

Run a published release without installing anything but Docker. From a checkout
(or after downloading `deploy/compose/compose.yaml`):

```bash
cp .env.example .env         # set provider keys / options
docker compose -f deploy/compose/compose.yaml up -d
```

The gateway comes up on `http://127.0.0.1:10000` with a persistent
`personalclaw_home` volume and a healthcheck. Pin a release with
`PERSONALCLAW_IMAGE_TAG` in `.env`. See the
[container guide](containers.md) for ports, volumes, backups, and updates.

## Where to go next

- **Explore the platform** — Skills, Agents, Tasks, goal Loops, Knowledge,
  Memory, Inbox, Triggers, and Workflows all live in the sidebar; each page has
  inline explanations.
- **Install more apps** — search providers, speech (STT/TTS), local models,
  channel connectors, and agent runtimes are all Store apps.
- **Run it permanently** — `personalclaw service install` registers a systemd
  unit (Linux) or launchd agent (macOS) so the gateway survives reboots.
- **Back it up** — `personalclaw snapshot` creates a portable state archive;
  `personalclaw restore` brings it back.

## Reference docs

- [Configuration reference](../reference/configuration.md) — every config field,
  its default, and where to set it.
- [CLI reference](../reference/cli.md) — every command and flag.
- [API overview](../reference/api-overview.md) — the full REST/WS surface.
- [Roadmap](../roadmap/roadmap.md) — where the project is heading.

## Troubleshooting

- **"Gateway not running" from CLI commands** — `status`/`stop`/`token` need a
  live gateway on the resolved port; pass `--port` if you changed it.
- **Backend code changes don't take effect** (source checkouts) — Python
  changes need a gateway restart (`personalclaw restart`); only frontend
  rebuilds are live.
- **Model errors in chat** — check **Settings → Models** has a chat binding and
  the provider's **Test** passes; `personalclaw doctor` reports the live
  binding and any missing optional dependency with the exact install command.
- **Dashboard shows nothing / 404 assets** (source checkouts only) — the SPA
  isn't built: run `make web-build`, then restart the gateway. Wheel, uv, pipx,
  and Docker installs ship the prebuilt dashboard, so this never applies to them.
