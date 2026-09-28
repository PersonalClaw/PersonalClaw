# Running PersonalClaw in containers

The published Docker images are a projection of the **same release artifact** as
every other install path — the gateway image bundles the wheel (with the
prebuilt dashboard) and the web image bundles the SPA behind an nginx TLS proxy.
There are no per-channel special builds.

This guide covers self-hosted Docker deployments: ports, volumes, the `.env`
pattern, backups, and updates.

## One container

The gateway image bundles the dashboard, so a single container is a complete
install — no checkout, no compose file, no `.env`:

```bash
docker run -d --name personalclaw --restart unless-stopped -p 127.0.0.1:10000:10000 -e PERSONALCLAW_BIND_HOST=0.0.0.0 -v personalclaw_home:/data ghcr.io/personalclaw/personalclaw-gateway:latest
docker exec personalclaw personalclaw token          # prints the dashboard URL
```

`PERSONALCLAW_BIND_HOST=0.0.0.0` is what lets the published port reach the gateway
*inside* the container — its own default is loopback, which a container cannot
publish. Compose sets it for the same reason. `--restart unless-stopped` brings the
gateway back by itself after a crash, an out-of-memory kill or a Docker restart; without
it the dashboard stays down until you `docker start` it. Compose sets the same policy.

Nothing else is needed for your work to survive `docker rm` + `docker run`: the image
sets `PERSONALCLAW_WORKSPACE=/data/workspace`, so the workspace is on the volume (see
[Volumes](#volumes)).

The gateway is the container's own process, so the container runtime starts, stops and
restarts it, from the host: `docker stop personalclaw`, `docker start personalclaw`,
`docker restart personalclaw`. Inside the container, `personalclaw stop`, `personalclaw
restart` and `personalclaw service install` say so, print the host command, and change
nothing. Stopped from inside, the gateway would end the container, and the restart policy
would start it straight back.

Take the two-service deployment below instead when you want the nginx TLS/HTTP2
proxy in front (self-signed out of the box), a Slack worker, or `.env`-driven
configuration.

## Quick start (compose)

From a checkout:

```bash
cp .env.example .env         # fill in provider keys / options (all optional)
docker compose -f deploy/compose/compose.yaml up -d
```

Or from `compose.yaml` alone, with no checkout — the file is self-sufficient:

```bash
curl -fsSLO https://raw.githubusercontent.com/PersonalClaw/PersonalClaw/main/deploy/compose/compose.yaml
docker compose -f compose.yaml up -d      # a .env beside it is optional
```

Two services come up:

| Service | Image | Purpose |
|---|---|---|
| `personalclaw-gateway` | `ghcr.io/personalclaw/personalclaw-gateway` | the agent gateway (dashboard API + channels) |
| `personalclaw-web` | `ghcr.io/personalclaw/personalclaw-web` | nginx TLS/HTTP2 proxy serving the SPA + streaming to the gateway |

Pin a specific release with `PERSONALCLAW_IMAGE_TAG` in `.env` (defaults to
`latest`). Build locally instead of pulling by overlaying `compose.build.yaml`:

```bash
docker compose -f deploy/compose/compose.yaml -f deploy/compose/compose.build.yaml up -d --build
```

## Ports

| Published port | Container | What |
|---|---|---|
| `127.0.0.1:3000` | web `:80` | HTTP — 308-redirects to HTTPS |
| `127.0.0.1:3443` | web `:443` | **the app** — HTTPS + HTTP/2 (self-signed cert out of the box) |
| `127.0.0.1:10000` | gateway `:10000` | gateway API/dashboard (bound to loopback; normally reached via the web proxy) |

All ports bind to `127.0.0.1` by default — the deployment is private to the host
until you put it behind your own reverse proxy or change the bindings. Open
`https://127.0.0.1:3443` and accept the self-signed certificate (mount a real
cert over `/etc/nginx/certs/personalclaw.{crt,key}` to replace it).

## Volumes

State lives in the named volume `personalclaw_home`, mounted at `/data` inside
the gateway container (`PERSONALCLAW_HOME=/data`). It holds config, credentials,
memory, knowledge, apps, and the workspace — everything that must survive a
container recreation.

The workspace is `/data/workspace`: the image sets `PERSONALCLAW_WORKSPACE` to it, and
so does compose. The default chat workspace lives there, and the folder picker opens
there, so a project folder created with **New folder here** lands on the volume. `/data`
is the only volume, so a folder you bind anywhere else — `/home/personalclaw`, `/tmp` —
exists only inside that container and is gone when it is recreated. The project page
then says the folder no longer exists, and **Change** binds a new one.

The volume also holds the Python packages installed apps bring: the image's own environment
(`/opt/venv`) is read-only to the gateway's user, so an app's `pythonDependencies`
install into `/data/app-python`, and a new container on the same volume loads them
again. If a new image ships a different Python, or drops a package an app relied
on, the gateway reinstalls what is missing in the background after it starts (it
needs network for that). `personalclaw snapshot` leaves this directory out: it is
rebuilt from the installed apps rather than restored.

```bash
docker compose -f deploy/compose/compose.yaml exec personalclaw-gateway du -sh /data   # inspect state size
docker volume ls | grep personalclaw_home                                              # find the volume
```

State survives `docker compose down && docker compose up -d` because the volume
outlives the containers. It is **removed** by `docker compose down -v` — don't
run that unless you mean to wipe state (snapshot first).

## Environment (`.env`)

Each service declares **two** candidate `env_file` locations, both
`required: false`: `./.env` beside `compose.yaml` (the standalone layout) and
`../../.env`, the repo root (the from-a-checkout layout). Whichever exists is
loaded, the repo-root one winning if both do. A relative `env_file` path resolves
from the compose **file's** directory, never from your shell's cwd — which is why
the standalone location has to be declared for a downloaded `compose.yaml` to
work at all.

Copy `.env.example` and set only what you need — every variable is optional with
a sensible default. Common ones:

| Variable | Default | Notes |
|---|---|---|
| `PERSONALCLAW_IMAGE_TAG` | `latest` | pin a release |
| `PERSONALCLAW_PORT` | `10000` | gateway port inside the container |
| `PERSONALCLAW_BIND_HOST` | `0.0.0.0` (in compose) | so port-forwarding works |
| `PERSONALCLAW_AUTH_MODE` | `local_token` | only `none` is honored as an override, and it forces a loopback bind |
| `PERSONALCLAW_LOGIN_USER` | — | seeds the owner login once, at first boot |
| `PERSONALCLAW_LOGIN_PASSWORD` | — | the password for that login (≥12 characters) |

The images set `PERSONALCLAW_INSTALL_KIND=container` so the gateway knows it is a
container install — the in-app Updates panel then shows the host's update commands
instead of a git/pip update flow. Nothing inside a container can see how it was started,
so `compose.yaml` also sets `PERSONALCLAW_CONTAINER_STARTED_BY=compose`: with it, the
commands PersonalClaw prints for updating, stopping and restarting are the compose ones;
without it they are the [one container](#one-container)'s `docker` ones. Set it in your own
compose file too.

## Getting the dashboard URL

In the default `local_token` auth mode the access URL (with a one-time token) is
printed to the gateway logs at startup and can be regenerated:

```bash
docker compose -f deploy/compose/compose.yaml exec personalclaw-gateway personalclaw token
```

Both URLs carry the container's own port (10000). Nothing inside the container can see
which host port you published, so it does not guess: if you mapped a different one
(`-p 127.0.0.1:8080:10000`), open the URL on that port instead. The command and the
startup log both say so.

## Owner login (a password instead of a token URL)

A container has no terminal to type a password at, so the credential can be seeded from the
environment on **first boot**:

```dotenv
# .env — the password must be at least 12 characters
PERSONALCLAW_LOGIN_USER=you
PERSONALCLAW_LOGIN_PASSWORD=a-long-passphrase-you-remember
```

Then turn the login form on (once, inside the container) and restart:

```bash
docker compose -f deploy/compose/compose.yaml exec personalclaw-gateway personalclaw auth enable
docker compose -f deploy/compose/compose.yaml restart personalclaw-gateway
```

Three things worth knowing:

- **Seeding never overwrites.** If a credential already exists the variables are ignored, so
  leaving them in `.env` cannot reset a password you later changed. Rotate with
  `personalclaw auth set-password` (or clear the credential first).
- **Seeding does not enable the form.** Enrolling a credential and opening a front door are
  separate decisions — `personalclaw auth enable` is the second one. Check either with
  `personalclaw auth status`.
- **The token URL keeps working.** Login is an *additional* way in, never a replacement, so a
  misconfigured password can't lock you out of your own box.

Prefer a Docker/compose secret or an `EnvironmentFile` with 0600 permissions over a
world-readable `.env` — these two variables are as sensitive as the password itself.

> `PERSONALCLAW_AUTH_MODE` has two values: `local_token` (the default) and `none`, which
> forces a loopback bind. For headless access use the owner login above, or mint a
> longer-lived token with `personalclaw token --ttl` (up to `90d`).

## Backups

Snapshot state from **inside** the gateway container so the archive captures the
`/data` volume exactly as the gateway sees it:

```bash
# create a snapshot (written under /data/snapshots)
docker compose -f deploy/compose/compose.yaml exec personalclaw-gateway personalclaw snapshot

# list snapshots
docker compose -f deploy/compose/compose.yaml exec personalclaw-gateway personalclaw snapshot --list

# copy one out to the host (resolve the container id from `docker compose ps -q`)
docker compose -f deploy/compose/compose.yaml cp personalclaw-gateway:/data/snapshots/<file>.tar.gz .
```

A restore needs the gateway stopped, and in a container the gateway is the container's own
process, so the restore runs in a one-shot container on the same volume, between a stop and
a start:

```bash
docker compose -f deploy/compose/compose.yaml stop personalclaw-gateway
docker compose -f deploy/compose/compose.yaml run --rm personalclaw-gateway \
  personalclaw restore /data/snapshots/<file>.tar.gz
docker compose -f deploy/compose/compose.yaml start personalclaw-gateway
```

For the [one container](#one-container), the same with `docker`: `docker stop personalclaw`,
then the restore in a one-shot container of the image your container runs, on its volume
(`--rm -v personalclaw_home:/data`, with `personalclaw restore /data/snapshots/<file>.tar.gz`
as its command), then `docker start personalclaw`. An archive from somewhere else goes into
`/data/snapshots` first: `docker cp <file>.tar.gz personalclaw:/data/snapshots/` works on a
stopped container too. Take a snapshot before every upgrade.

## Updates

Container installs update by pulling the new image and recreating — there is no
in-place self-update. The app's Updates panel (and `personalclaw update`) show
exactly the commands for the image tag your `updates` channel/pin resolves to, for the
way the container was started (see [Environment](#environment-env)). `personalclaw update`
prints them only when there is a release to move to: one newer than the image you run, or
the release you pinned while you do not run it yet. Otherwise it says you are on the newest
(or the pinned) release and prints nothing to run, and the Updates panel shows commands only
for a newer release. Release candidates count: `0.3.0-rc.2` is newer than `0.3.0-rc.1`, and
`0.3.0` is newer than both.

- **stable** (default) → the moving minor `:X.Y` (e.g. `:0.2`) — stays on the
  0.2.x line;
- **beta** → `:beta` while a release candidate is the newest release. Once a release is
  newer than every candidate, beta follows that release's `:X.Y`, as stable does;
- a **pin** (`updates.pin=0.1.3`) → that exact immutable `:0.1.3`. A pin must be a
  release version (`0.1.3`, or `0.3.0-rc.1` for a release candidate) — anything else
  is refused when you save it. A well-formed pin that matches no published release
  pulls nothing (never a silent `latest`), and Settings → Updates says so.

For the [one container](#one-container), they pull the image and remove the container:

```bash
docker pull ghcr.io/personalclaw/personalclaw-gateway:0.2
docker stop personalclaw
docker rm personalclaw
```

and then make it again with the [one container](#one-container)'s `docker run`, with `:0.2`
in place of `:latest`; what PersonalClaw prints has the tag in place already. The volume is
named, so `docker rm` leaves it, and your state, where they are. If you started the container
with another name, port or volume, use yours.

With Compose, the tag is carried on `PERSONALCLAW_IMAGE_TAG`, on BOTH commands so the
recreate matches the pull:

```bash
PERSONALCLAW_IMAGE_TAG=0.2 docker compose -f deploy/compose/compose.yaml pull
PERSONALCLAW_IMAGE_TAG=0.2 docker compose -f deploy/compose/compose.yaml up -d
```

You can still pin the tag yourself in `.env` (`PERSONALCLAW_IMAGE_TAG=X.Y.Z` — image tags
carry no `v`) and run the bare `docker compose … pull` / `up -d`.

State in `personalclaw_home` carries across the recreation. Snapshot before
upgrading (see [Backups](#backups)); the [CHANGELOG](../../CHANGELOG.md) lists what
changed, and [Updating](getting-started.md#updating) says what a breaking change asks
of you (PersonalClaw is pre-1.0).

### Rolling back

Pin the older version and recreate — the `X.Y.Z` image tags are immutable, so every
release stays pullable. `personalclaw update --to` pins it and prints the commands above for
that tag; with Compose:

```bash
docker compose -f deploy/compose/compose.yaml exec personalclaw-gateway \
  personalclaw snapshot                       # first: no pre-1.0 migrations, either direction
docker compose -f deploy/compose/compose.yaml exec personalclaw-gateway \
  personalclaw update --to 0.2.0              # pins updates.pin, prints the pull for :0.2.0
PERSONALCLAW_IMAGE_TAG=0.2.0 docker compose -f deploy/compose/compose.yaml pull
PERSONALCLAW_IMAGE_TAG=0.2.0 docker compose -f deploy/compose/compose.yaml up -d
```

For the one container, `docker exec personalclaw personalclaw snapshot` and
`docker exec personalclaw personalclaw update --to 0.2.0`, then the update above on `:0.2.0`.

The pin is what makes it a rollback rather than a one-off pull: without it, the next check
resolves the channel's newest release and offers to take you straight back. While the pin
names an older release than the image you run, Settings → Updates says so — **Pinned to
v0.2.0, older than this build (v0.2.1)** — and shows the same commands `personalclaw update`
prints for it, because a container replaces its image from the host rather than patching
itself. It also offers **Roll back to v&lt;previous&gt;**, which sets that pin for you, once
PersonalClaw has seen your version change at least once.

### Applying automatically, and turning the check off

Two orthogonal switches, both in `config.json` inside the volume (or Settings → Updates):

- `updates.auto` — `off` (default) only notifies; `staged` applies at the next safe point.
  On a container install "apply" means *surface the exact pull/recreate commands*: nothing
  inside the container can replace the image it is running from.
- `updates.check_enabled` — `false` makes the updater issue **zero** outbound calls to
  GitHub: no scheduled release check at all. While it is on,
  `updates.check_interval_hours` (1–168, default 12) sets the cadence. This is the egress
  kill switch, and it is independent of `updates.auto`: one governs whether PersonalClaw
  *looks*, the other whether it *acts*.

## Slack channel (optional)

**There is no second container for this, and no extra service to start.** The Slack
listener runs *inside the gateway process*, driven by the **Slack channel app**: the gateway
starts every enabled channel app's receiver at boot, and the app reads its own tokens — from
its settings, or from `SLACK_APP_TOKEN` / `SLACK_BOT_TOKEN` in the credential store or the
environment, which is how a container passes them. Core reads no channel's tokens: whether a
channel is configured is the channel's own answer (its health). The transport is registered
by the app, not by core — `register_default_transports()` registers only the Web UI, and
enabling the channel app is what calls `register_transport`
(`src/personalclaw/channel_transports/__init__.py`).

So on a container install:

```bash
# 1. put the three values in the .env the gateway already reads
cat >> .env <<'ENV'
SLACK_APP_TOKEN=xapp-...
SLACK_BOT_TOKEN=xoxb-...
PERSONALCLAW_OWNER_ID=U0123456789
ENV

# 2. recreate the gateway so it picks them up (credentials are read at startup)
docker compose -f deploy/compose/compose.yaml up -d --force-recreate personalclaw-gateway
```

Then install and enable the Slack channel app from **Store** in the dashboard, the same way
as any other channel. Tokens come from <https://api.slack.com/apps> after creating a
Socket-Mode app. Without an owner id the handler refuses every message, by design.

A channel keeps its own owner id under `PERSONALCLAW_OWNER_ID_<PROVIDER>`
(`PERSONALCLAW_OWNER_ID_SLACK`, `…_TELEGRAM`, `…_DISCORD`), and core addresses the owner's
notifications on a channel with that channel's own, falling back to `PERSONALCLAW_OWNER_ID`
when it has none. `PERSONALCLAW_OWNER_ID` is the one key every channel used to share, and a
channel app that has not moved to its own key still reads only that one — so set it, and add
the per-channel key when a second channel needs a different owner id. A notification for the
owner tries each connected channel in turn until one delivers it; when none can (no channel
knows an owner id it can reach), it lands in the Inbox with a sentence saying why.

## Troubleshooting

- **502 from the web proxy after recreating the gateway** — the nginx config
  re-resolves the gateway hostname per request (via `NGINX_ENTRYPOINT_LOCAL_RESOLVERS`),
  so this should self-heal within seconds; if not, `docker compose restart personalclaw-web`.
- **Browser refuses the self-signed cert** — expected out of the box; accept the
  exception, or mount a real cert over `/etc/nginx/certs/personalclaw.{crt,key}`.
- **`personalclaw token` says the gateway isn't running** — check
  `docker compose ps` shows `personalclaw-gateway` healthy; the healthcheck hits
  `/api/healthz`.
- **A server on your computer is refused at `localhost`** — such as a local model server
  entered as `http://localhost:<port>`. Inside the container, `localhost` is the container
  itself. Use your computer's address as the container sees it: `host.docker.internal` with
  Docker Desktop, `host.containers.internal` with Podman, `192.168.5.2` with Finch or Lima on
  a Mac (Lima's address for the host; it reaches servers that listen on localhost only, too).
  With Docker on Linux, add `--add-host=host.docker.internal:host-gateway` to the container's
  command, and have the server listen on more than localhost. PersonalClaw says the same when
  such an endpoint refuses.
