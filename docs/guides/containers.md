# Running PersonalClaw in containers

The published Docker images are a projection of the **same release artifact** as
every other install path — the gateway image bundles the wheel (with the
prebuilt dashboard) and the web image bundles the SPA behind an nginx TLS proxy.
There are no per-channel special builds.

This guide covers a self-hosted Docker Compose deployment: ports, volumes, the
`.env` pattern, backups, and updates.

## Quick start

From a checkout (or after downloading `deploy/compose/compose.yaml` and
`.env.example`):

```bash
cp .env.example .env         # fill in provider keys / options (all optional)
docker compose -f deploy/compose/compose.yaml up -d
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

```bash
docker compose -f deploy/compose/compose.yaml exec personalclaw-gateway du -sh /data   # inspect state size
docker volume ls | grep personalclaw_home                                              # find the volume
```

State survives `docker compose down && docker compose up -d` because the volume
outlives the containers. It is **removed** by `docker compose down -v` — don't
run that unless you mean to wipe state (snapshot first).

## Environment (`.env`)

Compose reads the repo-root `.env` (via each service's `env_file`). Copy
`.env.example` and set only what you need — every variable is optional with a
sensible default. Common ones:

| Variable | Default | Notes |
|---|---|---|
| `PERSONALCLAW_IMAGE_TAG` | `latest` | pin a release |
| `PERSONALCLAW_PORT` | `10000` | gateway port inside the container |
| `PERSONALCLAW_BIND_HOST` | `0.0.0.0` (in compose) | so port-forwarding works |
| `PERSONALCLAW_AUTH_MODE` | `local_token` | only `none` is honored as an override, and it forces a loopback bind |
| `PERSONALCLAW_LOGIN_USER` | — | seeds the owner login once, at first boot |
| `PERSONALCLAW_LOGIN_PASSWORD` | — | the password for that login (≥12 characters) |

The images set `PERSONALCLAW_INSTALL_KIND=container` so the gateway knows it is a
container install — the in-app Updates panel then shows the correct update
instructions (pull + up) instead of a git/pip update flow.

## Getting the dashboard URL

In the default `local_token` auth mode the access URL (with a one-time token) is
printed to the gateway logs at startup and can be regenerated:

```bash
docker compose -f deploy/compose/compose.yaml exec personalclaw-gateway personalclaw token
```

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

> `PERSONALCLAW_AUTH_MODE=api_key` is **not** wired up: `AuthConfig.from_env` honors only
> `none` (which forces a loopback bind). Use the owner login above for headless access, or
> mint a long-lived token with `personalclaw token --ttl`.

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

Restore by copying an archive back in and running
`personalclaw restore <path>` inside the container. Take a snapshot before every
upgrade.

## Updates

Container installs update by pulling the new image and recreating — there is no
in-place self-update (the app's Updates panel shows exactly these commands for a
container install):

```bash
# pin the new release first if you don't track `latest`
#   PERSONALCLAW_IMAGE_TAG=vX.Y.Z   (in .env)
docker compose -f deploy/compose/compose.yaml pull
docker compose -f deploy/compose/compose.yaml up -d
```

State in `personalclaw_home` carries across the recreation. Snapshot before
upgrading (see [Backups](#backups)); read the
[CHANGELOG](../../CHANGELOG.md) for breaking changes (PersonalClaw is pre-1.0).

## Slack channel (optional)

The compose file includes an opt-in `personalclaw-slack` service behind the
`with-slack` profile (it runs `personalclaw slack` against the same volume):

```bash
docker compose -f deploy/compose/compose.yaml --profile with-slack up -d
```

## Troubleshooting

- **502 from the web proxy after recreating the gateway** — the nginx config
  re-resolves the gateway hostname per request (via `NGINX_ENTRYPOINT_LOCAL_RESOLVERS`),
  so this should self-heal within seconds; if not, `docker compose restart personalclaw-web`.
- **Browser refuses the self-signed cert** — expected out of the box; accept the
  exception, or mount a real cert over `/etc/nginx/certs/personalclaw.{crt,key}`.
- **`personalclaw token` says the gateway isn't running** — check
  `docker compose ps` shows `personalclaw-gateway` healthy; the healthcheck hits
  `/api/healthz`.
