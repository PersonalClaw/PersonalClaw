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
- git 2.12 or newer, for what runs git: time travel, a workflow's or a loop's own
  worktree, updating a git checkout, and the git apps (Git Sync, Git Repository, Notes,
  Spec Builder). PersonalClaw refuses an older git for these, because an older one
  ignores the settings that stop a repository's own configuration from running a
  program. `personalclaw doctor` shows the git it found and its version.

You do **not** need to install Python or Node yourself for the recommended
paths: they ask `uv` for Python 3.13, which it downloads when your machine has
none, and the release wheel ships the prebuilt dashboard. The base Python
package also carries the IANA timezone database, so minimal Linux installs do
not need an operating-system `tzdata` package before `personalclaw setup`. And
it brings pip, which installs the Python packages and engines apps declare, so a
system Python whose `ensurepip` is stripped (Debian's and Ubuntu's) needs no
`python3-venv` or `python3-pip` package for them. `personalclaw doctor` shows that
pip on its `pip:` row, and if it is missing, the reinstall that puts it back.
(Contributors who build from source need Python 3.12 or 3.13 and Node 18+ — see
[CONTRIBUTING](../../CONTRIBUTING.md#development-setup).)

## 1. Install

Pick one path. All of them install the **same release artifact** — there are no
per-channel special builds.

| Path | Command | Best for |
|---|---|---|
| **uv tool** *(recommended)* | `uv tool install --python 3.13 personalclaw` | anyone — `uv` downloads Python 3.13 if it's missing |
| **Bootstrap one-liner** | `curl -fsSL https://personalclaw.dev/install \| sh` | fastest start; installs `uv` if absent, then the above |
| pipx | `pipx install --python python3.13 personalclaw` | Python users who like isolated tools (needs `python3.13` on your PATH) |
| pip | `pip install personalclaw` | inside an existing Python 3.12 or 3.13 venv |
| **Docker** | see [§ Docker](#docker) | one container, no checkout, no `.env` |
| **Docker Compose** | see [§ Docker Compose](#docker-compose) | self-hosters; Windows |
| Git checkout | see [CONTRIBUTING](../../CONTRIBUTING.md#development-setup) | contributors / development |

After a uv/pipx/pip install the `personalclaw` command is on your PATH:

```bash
uv tool install --python 3.13 personalclaw
personalclaw setup      # interactive: workspace directory + timezone
```

The `--python` matters: uv does not enforce the top of the Python range a
package declares, so without it uv builds the install on the newest Python it
can find or download, which may be one PersonalClaw does not support, such as
3.14. `personalclaw doctor` checks the Python an install runs on against that
range and fails it, with the command that moves it, when it is outside.

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
| `js-render` | `pip install 'personalclaw[js-render]'` | JS-rendered web fetch (Playwright) | large (browser) |
| `models` | `pip install 'personalclaw[models]'` | local inference: embeddings + STT + TTS | large (ML) |

> With `uv tool`, add an extra with `uv tool install --python 3.13 'personalclaw[bedrock]'`.
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
provider setup. Until you finish or skip it, the dashboard's other pages wait for
it, except the security controls: **Settings → Devices**, **Security**, **Sender
trust**, **Guardrails** and **External access** open at any time, so you can sign a
device out or turn on the incident kill switch in the middle of setup. Setup links
to them as **Security controls**.

**Already use Claude Code or Codex?** Step 2, **Bring your setup over**, reads nothing of theirs
until you ask: press **Look in Claude Code and Codex** and it lists what each holds (instructions,
memories, MCP servers, skills, agents, prompts and conversations) for you to pick from. Nothing in
their folders is changed, and nothing comes over until you import it. The Tools page offers the
same **Look in** press for their MCP servers. To have both list a tool on every visit without the
press, turn it on in **Settings → Security → Outside PersonalClaw's home**.

**Already running [Ollama](https://ollama.com)?** The first-run **essentials**
step detects a local Ollama automatically and offers a one-click bind with **no
API key** — skip straight to [§4](#4-first-chat). The model it proposes is one the
server says calls tools, since every chat turn offers the agent its tools. If your
Ollama runs on another machine on your network, press **"Scan my local network"**
on the same step: it sweeps only your own private (RFC-1918) subnet for an Ollama,
is time-bounded, and never runs until you press it. Nothing scans your network on
first boot, and no credential is stored either way. Otherwise, configure a provider
below.

**Want a cloud provider and a local one?** Set the cloud provider up first. Once
its chat model reads Ready, the same lane offers **"Also add a local model"**, with
the Ollama it found and the same scan. **Add this model** adds it as one more
provider and changes no binding, so chat keeps the model you picked. To chat with
the local model too, or to fall back to it, add it to Chat in Settings → Models.

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
   speech, and more — they can all be different providers. Each model's **Test**
   makes one small real call for the use case it is listed under (a one-word
   reply, one embedded word, the smallest image) and says what came back; a
   model that can't be tested says why instead. Each row offers the
   models that can do its job, by what the provider says each model does (its
   own model record where the vendor publishes one, else the model's id):
   an embedding model, a reranker or a moderation model is never offered for
   chat, and binding one there through the API is refused.

Prefer the terminal? `personalclaw setup --credential NAME=VALUE` saves a
secret in the same credential store Settings → Secrets uses, where a workflow's
`{{secret:NAME}}` and a provider entry whose `credential` is `NAME` read it.
Settings → Secrets can also keep a secret for one project: only that project's
work (its workflow runs, loops and chats) reads it, ahead of a global secret of
the same name, and nothing outside the project does.
`personalclaw doctor` verifies the result end to end.

## 4. First chat

Open the dashboard's **Chat** page and send a message — or from the terminal:

```bash
personalclaw chat -m "hello"
```

`personalclaw chat` talks to the gateway you started above, so a chat in the terminal is
a chat like the dashboard's, on the same model, and it is listed on the Chat page too.
With no gateway running it says so, and how to start one.

Tool calls the agent wants to make appear as approval prompts (default
`agent.approval_mode: interactive`, Ask each time; see the
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
checks out the release tag in a git clone, or prints the commands that replace a
container's image. When nothing newer is published it says you are on the newest release
and changes nothing. Everything below is the same on every install kind, and all of it lives
in **Settings → Updates** as well as in `config.json`.

**The installer is the one that made your environment.** A wheel or a git clone is installed
into with the tool that set its environment up: uv for `uv tool install`, `uv sync` or
`uv pip install` (a git clone's update runs `uv sync --locked --inexact`, so it follows the
lockfile and never removes a package you added), and pip for pip, pipx or a `python -m venv`
clone (`pip install -e .`). When that tool is not on the PATH PersonalClaw runs with, the
update changes nothing and says what to run instead, such as `personalclaw update` from a
terminal where `uv` works. A service runs with the PATH it was installed with, so a uv
installed later may be one only your terminal can see.

**A git clone's failed update puts it back.** When the install does not finish (the
network, a resolver conflict, a full disk, or PersonalClaw stopping while it runs), the
update puts the clone back on the exact commit and branch it was on, so PersonalClaw keeps
running the release its packages belong to, and says what failed. It never forces git: if
git refuses (another git command holds the repository, say), the message names where the
clone is and the one `git -C … checkout …` command that puts it back, to run before
PersonalClaw next starts. That is also why an update never starts on uncommitted changes to
tracked files.

**Cancel stops an update for real.** The update screen offers Cancel while the update fetches
and installs the new release. It stops the installer, puts the clone back the same way, and
the screen then says what that left: "Nothing was changed", or what is not as it was and how to
put it right. Once the new release is installed, the update builds the dashboard and restarts
on its own; it can no longer be cancelled, and the screen says so. A Ctrl-C of
`personalclaw update` stops it the same way.

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
once, and while a pin names an older release than the one running it says so — **Pinned to
v0.1.3, older than this build (v0.2.0)** — beside a **Roll back to v0.1.3** that installs
it. Take a snapshot first — pre-1.0 releases carry no data migrations in either
direction:

```bash
personalclaw snapshot
personalclaw update --to 0.1.3
```

**Applying automatically is opt-in, and only a source checkout can** (`updates.auto`). The
default `off` only notifies you. On a source checkout, set it to `staged` and an available
update installs itself at the next safe point — it holds while a session or subagent is
running, and only ever lands on the release your channel/pin resolves to, never on raw
`main`. An install from pip, uv or pipx never installs an update on its own: press
**Update** in Settings → Updates (or run `personalclaw update`), and there `staged` notifies
exactly as `off` does.

**Turning the check off** (`updates.check_enabled`). PersonalClaw asks GitHub for the
newest release when it starts, then every `updates.check_interval_hours` (default 12, range
1–168) while the dashboard is open. Set `updates.check_enabled` to `false` (Settings → Updates
→ **Automatic update checks**) and the update check makes **zero** calls to GitHub on its own
— no check at start, none on the schedule, none when a page shows the update status. **Check now** in
Settings → Updates still checks once when you press it, and `personalclaw update` still works
when you run it by hand. This is a separate switch from `updates.auto`: one governs whether
PersonalClaw *looks*, the other whether it *installs*.

### Upgrading from 0.1.x

0.2.0 is a pre-1.0 clean break: state shapes changed with no automatic migration, several
defaults flipped, and some routes refuse input they used to accept. Run
`personalclaw snapshot` first, then update. What changes for you:

- **Updates.** `auto_update` and `dashboard.update_dev_mode` are retired, along with
  `POST /api/update/auto` and `POST /api/update/dev-mode`. One legacy mapping applies once
  on load: `auto_update: true` becomes `updates.channel=stable` + `updates.auto=staged`,
  `auto_update: false` becomes `updates.auto=off`, and `dashboard.update_dev_mode: true`
  becomes `updates.channel=nightly`. Nothing else in your config is touched. The four
  fields to know are `updates.channel`, `updates.pin`, `updates.auto` and
  `updates.check_enabled` (above), and `git reset --hard` is gone from every apply path.
- **Timed triggers change the hour they fire.** A trigger with no explicit timezone uses
  local wall-clock time, not UTC. If you left the field blank to mean UTC, declare `UTC`.
- **Existing automations honour the action denylist.** Scheduled, file-watch, webhook and
  chained automations never did. Adjust the command rather than the guardrail.
- **Hooks, cron scripts and app backends no longer inherit PersonalClaw's environment.**
  Use `sandbox.env_passthrough`; credential-shaped names stay refused even if declared.
- **Python 3.14 is not supported**: `requires-python` is `>=3.12,<3.14`. pip refuses it, but
  uv does not enforce the upper bound, so an install made with a bare `uv tool install` may be
  running on 3.14, and `personalclaw update` keeps the Python it runs on. `personalclaw doctor`
  says so (`python: ❌`); for a uv tool install the fix it prints is
  `uv tool upgrade --python '<3.14,>=3.12' personalclaw`.
- **Config fields removed** (a stored value is ignored on load): `workflows.max_active_runs`,
  `knowledge.conflict_model_pass`, `knowledge.lint_every_n_persists`,
  `learning.min_session_score`, `knowledge.idempotent_persist`,
  `workflows.max_concurrent_nodes` and `agent.sandbox`.
- **`inbound` is renamed `external_access`.** The master switch `external_access.enabled`
  must also be on, and surface tokens move to the credential store, so re-mint an MCP token
  with `personalclaw inbound token create mcp`.
- **API breaks.** `GET /api/inbox/pending` is `GET /api/inbox/open`, and
  `/api/inbox/status` carries `open_count` instead of `pending_count`. Consent is the JSON
  literal `true` only: the string `"true"`, `1` and `yes` are refused. Three destructive
  routes require `confirm: true`, a destructive `POST /api/tools/invoke` needs
  `"confirm_risk": "destructive"`, six orphaned `/api/memory/*` embedding endpoints are
  gone, and workflow `rewind`/`run-from` require `confirm_cascade=true`.
- **State shapes changed with no migration.** `sessions.json` rows in the old shape are
  discarded on read (one `personalclaw token` re-mint); loop rows written before
  `stop_reason` read as completed; the `runs` table no longer declares `task_list_id`; and
  a knowledge library written earlier reports that a re-index is due.
- **SDK breaks for app authors.** `run_chat` is no longer exported from
  `personalclaw.sdk.channel`, and `register_acp_cli_entry` no longer accepts
  `agent_config_dir`: both fail at import. The `kiro` runner id is now `kiro-cli`. Update
  installed apps alongside the core upgrade.
- **Chat's Activity → Index tab is gone.** The Session Map is the session's index.

### After updating from 0.2.0

- **An agent no chat started asks before it acts, unless its automation or loop was allowed to
  run on its own.** Settings → Agent defaults → Approval mode now ships as Ask each time, so a
  trigger's Invoke Agent agent whose step does not set its own approval, or a subagent started
  outside a chat, asks you in your Inbox. A config written before keeps what it holds: if it says
  Auto (one written while Auto was the default usually does), those agents still approve every
  call they make, and Doctor and Settings → Agent defaults say so. Nothing records whether that Auto was
  chosen, so it is left for you to keep or change there.
- **The first start re-embeds your library once.** Knowledge items and memories that 0.2.0
  embedded do not record which embedding model wrote their vectors, and nothing else can
  tell one model's vector from another's, so the first start re-embeds them in the
  background with the model bound in Settings → Models. Until an item is re-embedded,
  search finds it by keyword. Settings → Models and the Knowledge page show the re-index
  and its progress while it runs, and if PersonalClaw stops part way, the next start
  resumes where it stopped. No model bound means nothing is re-embedded until you choose
  one.
- **A webhook is fired with a token made for it.** A webhook automation's `token_ref`, which
  nothing read, is gone (the first start takes it out of each one): make the automation a sender
  token on its page on the Triggers page, or with
  `personalclaw inbound webhook create <automation-id>`, and give that to the program that fires
  it. `POST /api/hooks/agent` no longer takes PersonalClaw's internal credential: a program on
  this machine sends your webhook token, which must now be at least 32 characters
  (`personalclaw config set hooks.webhook_token <token>`). Both take requests only from programs
  on this machine; from another, forward a port over SSH
  ([automations](automations.md#when-a-program-starts-one-webhooks)).
- **An app says what its agent work may use, and its agents approve nothing.** An app's
  `agent` permission now names a tier: `text` (the model is handed only the text the app sends,
  with no tools), `read` (read-only tools) or `tools` (your tools, each call that needs approval
  asking you). An app you installed that still declares `"agent": true` runs no agent tasks
  until you update it to a version that names its tier, and that update asks you again in the
  tier's words. Minutes and Growth declare `text`.

## Where to go next

- **Explore the platform** — Skills, Agents, Tasks, goal Loops, Knowledge,
  Memory, Inbox, Triggers, and Workflows all live in the sidebar; each page has
  inline explanations.
- **Install more apps** — search providers, speech (STT/TTS), local models,
  channel connectors, and agent runtimes are all Store apps.
- **Run it permanently** — `personalclaw service install` registers a systemd
  unit (Linux) or launchd agent (macOS) so the gateway survives reboots. Run it
  from the shell that has your settings: it carries `PERSONALCLAW_HOME`,
  `AWS_PROFILE` and [the other variables listed here](../reference/cli.md#the-services-environment)
  into the service, and never a secret. In a container there is no service to install:
  the container's restart policy does this ([containers](containers.md#one-container)).
- **Back it up** — `personalclaw snapshot` creates a portable state archive;
  `personalclaw restore` brings it back. The archive never contains a credential: API keys
  and app tokens stay in this machine's credential store (the OS keychain, or
  `~/.personalclaw/.env` at mode 0600), and settings carry only references to them — so a
  restore onto a new machine asks you to enter the keys again. A replace restore also brings your
  automations back paused, so a new machine does not send the same briefs and digests as the one
  it replaces: resume them on the Triggers page (**Resume all**) once the old one is retired.

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

- **"Gateway not running" from CLI commands** — `status`/`token` need a
  live gateway on the resolved port; pass `--port` if you changed it. `stop` finds
  this home's gateway wherever it listens, from the record the gateway keeps in its home.
- **Backend code changes don't take effect** (source checkouts) — Python
  changes need a gateway restart (`personalclaw restart`); only frontend
  rebuilds are live.
- **Model errors in chat** — check **Settings → Models** has a chat binding and
  that model's **Test** passes; `personalclaw doctor` reports the live
  binding and any missing optional dependency with the exact install command.
- **Short, off-topic answers and no tool calls** — you are talking to the small
  default model, and the notice above the composer says so. Bind a real model under
  **Settings → Models**.
- **Dashboard shows nothing / 404 assets** (source checkouts only) — the SPA
  isn't built: run `make web-build`, then restart the gateway. Wheel, uv, pipx,
  and Docker installs ship the prebuilt dashboard, so this never applies to them.
