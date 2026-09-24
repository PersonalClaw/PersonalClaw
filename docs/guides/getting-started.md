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
| **Docker** | see [§ Docker](#docker) | one container, no checkout, no `.env` |
| **Docker Compose** | see [§ Docker Compose](#docker-compose) | self-hosters; Windows |
| Git checkout | see [CONTRIBUTING](../../CONTRIBUTING.md#development-setup) | contributors / development |

After a uv/pipx/pip install the `personalclaw` command is on your PATH:

```bash
uv tool install personalclaw
personalclaw setup      # interactive: workspace directory + timezone
```

`setup` does **not** ask for a model provider credential — on a fresh install
there is no provider app to hold one yet. Providers arrive in
[§3](#3-configure-a-model-provider), after the gateway is up. Run `setup` on a
real terminal: it prompts, so piping or redirecting stdin makes it fall back to
the printed defaults.

### Verify the one-liner

`curl … | sh` executes whatever the server sends, unread. If you would rather
check the bytes before running them, the digest of the current `install.sh` is
committed in this repository — a **different origin** from the site that serves
the script:

```bash
curl -fsSL -o install.sh https://personalclaw.dev/install
curl -fsSL -o install.sh.sha256 \
  https://raw.githubusercontent.com/PersonalClaw/PersonalClaw/main/deploy/website/install.sh.sha256
shasum -a 256 -c install.sh.sha256   # or: sha256sum -c install.sh.sha256
sh install.sh
```

**What this proves, exactly** — stated narrowly on purpose, because a digest is
easy to oversell:

- **It does defeat** a poisoned CDN cache, a truncated or corrupted transfer, and
  a tampered response from `personalclaw.dev`. None of those can also change the
  copy on `raw.githubusercontent.com`.
- **Fetching the digest from a second origin is the entire security value here.**
  A digest served by the same host as the script would prove almost nothing: a
  host that can hand you a modified script can hand you a matching digest. Do not
  "simplify" the recipe to a `personalclaw.dev` digest.
- **It does not defeat** a compromise of this GitHub repository or of a maintainer
  account — an attacker with that access updates the script and the digest
  together. It also covers only this file, not what the file goes on to download:
  uv's installer from `astral.sh` is fetched over TLS and **not** verified (see the
  comment in `deploy/website/install.sh`), and the `personalclaw` wheel comes from
  PyPI over TLS with a version floor. The wheel does carry
  [PEP 740](https://peps.python.org/pep-0740/) provenance, signed by GitHub for
  `PersonalClaw/PersonalClaw` `release.yml` — but no released `uv` or `pip` checks
  it at install time, so nothing in this path verifies it for you yet.
- **A mismatch is not by itself proof of an attack.** The website's copy is applied
  by hand from this repository, so the served bytes can legitimately lag it by a
  commit. On a mismatch, diff what you downloaded against
  `deploy/website/install.sh` on `main` before assuming the worst: a reworded
  message is drift, an added download is not.

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

**Already running [Ollama](https://ollama.com)?** The first-run **essentials**
step detects a local Ollama automatically and offers a one-click bind with **no
API key** — skip straight to [§4](#4-first-chat). If your Ollama runs on another
machine on your network, press **"Scan my local network"** on the same step: it
sweeps only your own private (RFC-1918) subnet for an Ollama, is time-bounded, and
never runs until you press it. Nothing scans your network on first boot, and no
credential is stored either way. Otherwise, configure a provider below.

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

Prefer the terminal? Once a provider app is installed,
`personalclaw setup --provider NAME --credential NAME=VALUE` stores the
credential without the dashboard, and `personalclaw doctor` verifies the result
end to end.

## 4. First chat

Open the dashboard's **Chat** page and send a message — or from the terminal:

```bash
personalclaw chat -m "hello"
```

Tool calls the agent wants to make appear as approval prompts (default
`agent.approval_mode: auto`; see the
[configuration reference](../reference/configuration.md) to tune approval,
sandboxing, and security policy).

## Docker

One container, nothing to check out and no `.env` — the gateway image bundles the
dashboard. From an empty directory on a machine with only Docker:

```bash
docker run -d --name personalclaw -p 127.0.0.1:10000:10000 -e PERSONALCLAW_BIND_HOST=0.0.0.0 -v personalclaw_home:/data ghcr.io/personalclaw/personalclaw-gateway:latest
```

Then print the dashboard URL (it carries a one-time token — the default auth mode):

```bash
docker exec personalclaw personalclaw token
```

State lives in the named volume `personalclaw_home`, so it survives
`docker rm`/`docker run`. `-p 127.0.0.1:…` keeps the port on the host's loopback;
`PERSONALCLAW_BIND_HOST=0.0.0.0` is what lets that published port reach the gateway
*inside* the container (its own default is loopback, which a container cannot publish).
Swap `:latest` for a release tag to pin one. This is the same command the
[README](../../README.md#docker) and the [container guide](containers.md) print; a test
keeps all four copies identical.

## Docker Compose

Compose adds a TLS web proxy in front of that gateway, and needs one file the
single-container path above does not. From a checkout (or after downloading
`deploy/compose/compose.yaml`):

```bash
cp .env.example .env         # set provider keys / options
docker compose -f deploy/compose/compose.yaml up -d
```

The gateway comes up on `http://127.0.0.1:10000` with a persistent
`personalclaw_home` volume and a healthcheck. Pin a release with
`PERSONALCLAW_IMAGE_TAG` in `.env`. See the
[container guide](containers.md) for ports, volumes, backups, and updates.

## Updating

`personalclaw update` advances whichever way you installed — it upgrades the wheel,
checks out the release tag in a git clone, or prints the `docker compose` commands for a
container. Everything below is the same on every install kind, and all of it lives in
**Settings → Updates** as well as in `config.json`.

**Channels** (`updates.channel`) — which release line you follow:

| Channel | Follows | Use it when |
|---|---|---|
| `stable` (default) | the newest normal release | almost always |
| `beta` | the newest release *including* release candidates | you want the next minor early |
| `nightly` | every commit on your checked-out branch | you are contributing (git clones only; needs a clean tree) |

**Pinning** (`updates.pin`) — stay on an exact release, whatever the channel says:

```bash
personalclaw config set updates.pin 0.2.1     # stay here
personalclaw config set updates.pin ""        # follow the channel again
```

A pin overrides the channel everywhere — the update check, the apply, and the container
image tag. A pin naming no published release is *refused* rather than quietly upgrading you.

**Rolling back.** `personalclaw update --to 0.2.0` pins that version and installs it, so a
later check cannot pull you forward again. Settings → Updates offers the same thing as
**Roll back to v&lt;previous&gt;** once PersonalClaw has seen your version change at least
once. Take a snapshot first — pre-1.0 releases carry no data migrations in either
direction:

```bash
personalclaw snapshot
personalclaw update --to 0.2.0
```

**Applying automatically is opt-in** (`updates.auto`). The default `off` only notifies
you. Set it to `staged` and an available update installs itself at the next safe point —
it holds while a session or subagent is running, and only ever lands on the release your
channel/pin resolves to, never on raw `main`.

**Turning the check off** (`updates.check_enabled`). PersonalClaw asks GitHub for the
newest release every `updates.check_interval_hours` (default 12, range 1–168). Set
`updates.check_enabled` to `false` and the updater makes **zero** outbound calls — no
scheduled check, no release probe. `personalclaw update` still works when you run it by
hand. This is a separate switch from `updates.auto`: one governs whether PersonalClaw
*looks*, the other whether it *installs*.

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
- [API overview](../reference/api-overview.md) — auth, conventions, and the behaviours
  that are easy to get wrong.
- [HTTP route reference](../reference/api-routes.md) — every registered route, generated
  from the gateway's own route table.
- Roadmap — where the project is heading.

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
