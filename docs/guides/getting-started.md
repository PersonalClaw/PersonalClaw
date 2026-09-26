# Getting started

PersonalClaw is a self-hosted personal AI agent: a local gateway process that
serves a web dashboard, runs agents with tools/memory/skills, and connects to
channels. This guide takes you from **nothing installed** to your first chat.

> **Pre-1.0:** PersonalClaw is pre-1.0 and moves fast; releases may make
> breaking changes. Run `personalclaw snapshot` before upgrading.

## Prerequisites

- macOS or Linux (Windows: use the Docker Compose path below)
- For real work, a model provider: a local Ollama, or an API key for Anthropic,
  OpenAI, an OpenAI-compatible endpoint or AWS Bedrock (anything from the Store's
  model-provider apps). You don't need one to start, because first-run setup can
  download a [small default model](#the-small-default-model) instead.

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

**No account and no Ollama?** In step 3, **Essential apps**, the model lane opens with
*No account? Start with a small offline model* and a **Download SmolLM2-135M-Instruct
(138 MiB)** button. It shows bytes, a percentage and an ETA while it runs, and a reload
picks the progress back up. When it finishes, onboarding says so, makes it your chat model
(unless you had already chosen one), checks that chat can use it, and unlocks
**Continue**. If you skip setup, the chat screen offers the same download. It happens
once, it can be cancelled, and declining costs nothing.

### The small default model

The download is `SmolLM2-135M-Instruct` (the Q8_0 GGUF build
`unsloth/SmolLM2-135M-Instruct-GGUF`, Apache-2.0) from Hugging Face, checked against
the sha256 in [`bundled-model-signoff.txt`](../../src/personalclaw/apps/native/bundled-chat/bundled-model-signoff.txt)
before it is installed. It is not in the wheel, the container image or the desktop
build, so the first chat with it needs network. It lands in
`$PERSONALCLAW_HOME/models/bundled-chat/`, stays there across upgrades, and from then on
runs on your CPU inside the gateway with no key and no network.

It answers when it is your chat model (onboarding makes it one when you download it there)
or when nothing else is set up, and the chat screen says when it is the one answering. Bind
any other model in [§3](#3-configure-a-model-provider) and it stops being used, with
nothing to undo. To remove it, delete it under **Settings → Providers**; to stop it
answering when nothing is bound, turn off **Answer when nothing else is bound** in its
settings there (it takes effect when you save).

Treat it as a way to start. It has 135 million parameters and no tools, it doesn't see
your memory, skills or knowledge, and past a greeting or a simple factual question its
answers get unreliable.
[Security limitations §5](../security/limitations.md#5-the-bundled-default-model-is-a-floor-not-an-assistant)
lists what it did with real requests.

For a machine with no network, download it once on a machine that has one and copy
`models/bundled-chat/` into the offline home. PersonalClaw checks a copied file's size,
not its sha256, so compare the digest with the record first. From a source checkout,
`PERSONALCLAW_HOME=/path make bundled-model` fetches it without the dashboard.

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

Prefer the terminal? `personalclaw setup --credential NAME=VALUE` saves a
secret in the same credential store Settings → Secrets uses, where a workflow's
`{{secret:NAME}}` and a provider entry whose `credential` is `NAME` read it.
`personalclaw doctor` verifies the result end to end.

## 4. First chat

Open the dashboard's **Chat** page and send a message — or from the terminal:

```bash
personalclaw chat -m "hello"
```

If the small default model is all you have, chat from the dashboard. `personalclaw chat`
doesn't load it, so the terminal prints a setup message instead of a reply.

Tool calls the agent wants to make appear as approval prompts (default
`agent.approval_mode: auto`; see the
[configuration reference](../reference/configuration.md) to tune approval,
sandboxing, and security policy).

## Docker

One container, nothing to check out and no `.env` — the gateway image bundles the
dashboard. From an empty directory on a machine with only Docker:

```bash
docker run -d --name personalclaw --restart unless-stopped -p 127.0.0.1:10000:10000 -e PERSONALCLAW_BIND_HOST=0.0.0.0 -v personalclaw_home:/data ghcr.io/personalclaw/personalclaw-gateway:latest
```

Then print the dashboard URL (it carries a one-time token — the default auth mode):

```bash
docker exec personalclaw personalclaw token
```

The URL carries the container's own port, 10000. If you published a different host port,
open that one instead; the command reminds you.

State lives in the named volume `personalclaw_home`, mounted at `/data`, so it survives
`docker rm`/`docker run`. That includes your work: the image puts the workspace at
`/data/workspace`, where the default chat workspace lives and where the folder picker opens
to create a project folder. A folder you bind outside `/data` exists only inside that
container and is gone when it is recreated; the project page then says so.
`--restart unless-stopped` brings the gateway back by itself after a crash, an out-of-memory
kill or a Docker restart. `-p 127.0.0.1:…` keeps the port on the host's loopback;
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
personalclaw config set updates.pin 0.2.0     # stay on exactly 0.2.0
personalclaw config set updates.pin ""        # follow the channel again
```

A pin overrides the channel everywhere — the update check, the apply, and the container
image tag. A pin naming no published release is *refused* rather than quietly upgrading you.

**Rolling back.** `personalclaw update --to 0.1.3` pins that version and installs it, so a
later check cannot pull you forward again. Settings → Updates offers the same thing as
**Roll back to v&lt;previous&gt;** once PersonalClaw has seen your version change at least
once. Take a snapshot first — pre-1.0 releases carry no data migrations in either
direction:

```bash
personalclaw snapshot
personalclaw update --to 0.1.3
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
  `personalclaw restore` brings it back. The archive never contains a credential: API keys
  and app tokens stay in this machine's credential store (the OS keychain, or
  `~/.personalclaw/.env` at mode 0600), and settings carry only references to them — so a
  restore onto a new machine asks you to enter the keys again.

## Reference docs

- [Configuration reference](../reference/configuration.md) — every config field,
  its default, and where to set it.
- [CLI reference](../reference/cli.md) — every command and flag.
- [API overview](../reference/api-overview.md) — auth, conventions, and the behaviours
  that are easy to get wrong.
- [HTTP route reference](../reference/api-routes.md) — every registered route, generated
  from the gateway's own route table.
- Roadmap — maintainer-owned and deliberately not in this repository. The written way in is
  the [contribution intake path](../../CONTRIBUTING.md#the-model): open an issue, discuss it,
  and the maintainer files or updates a plan.

## Troubleshooting

- **"Gateway not running" from CLI commands** — `status`/`stop`/`token` need a
  live gateway on the resolved port; pass `--port` if you changed it.
- **Backend code changes don't take effect** (source checkouts) — Python
  changes need a gateway restart (`personalclaw restart`); only frontend
  rebuilds are live.
- **Model errors in chat** — check **Settings → Models** has a chat binding and
  the provider's **Test** passes; `personalclaw doctor` reports the live
  binding and any missing optional dependency with the exact install command.
- **Short, off-topic answers and no tool calls** — you are talking to the small
  default model, and the notice above the composer says so. Bind a real model under
  **Settings → Models**.
- **Dashboard shows nothing / 404 assets** (source checkouts only) — the SPA
  isn't built: run `make web-build`, then restart the gateway. Wheel, uv, pipx,
  and Docker installs ship the prebuilt dashboard, so this never applies to them.
