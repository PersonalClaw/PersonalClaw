# The App Platform

Apps are how PersonalClaw grows capabilities without core edits: model
providers, channels, agents, search engines, tools, and full backend+UI
dashboards are all apps. This doc covers the runtime: install/update
lifecycle, the security scan, the backend subprocess model, the permission
system, crons, and the MCP bridge. Paths are relative to
`PersonalClaw/src/personalclaw/`.

## Three tiers

| Tier | Location | Notes |
|---|---|---|
| Native (26) | `apps/native/` in-package | seeded on first run, locked on (e.g. `native-agents`, `personalclaw-memory`, the action bundles) |
| First-party (36) | workspace `apps/` | Slack channel, model providers, speech, Minutes/Growth dashboards |
| Third-party | user sources → `~/.personalclaw/apps/` | fixtures at `third-party-apps/` (`hello-search`, `demo-dashboard`) |

The gateway loads **installed copies** at `~/.personalclaw/apps/<name>/`.
Editing the repo `apps/` tree does nothing to a running gateway until you push
via `POST /api/apps/{name}/update` (plus a restart for already-imported
modules). App sources for the Store are managed at
`/api/apps/sources` (`dashboard/handlers/apps.py`).

## Install lifecycle (`apps/app_manager.py`)

Install is: **copy → stage in quarantine → validate manifest → scan staged
content → platform gate → pip deps → `setup.onInstall` hook (bounded
subprocess, 60s cap) → register providers/prompts/MCP servers/crons → start
backend**.

- **Quarantine first** — staged under `~/.personalclaw/apps/.quarantine/`;
  dangerous content never touches the live tree.
- **The scan** is the shared `SkillScanner` (`supply_chain.py`). Verdicts:
  *clean* installs; *warning* → HTTP 409 `needs_consent` (the caller must
  explicitly confirm); *dangerous* → terminal refusal, **non-overridable**.
  The install invariant is scanned-bytes == installed-bytes (no
  swap-after-scan window).
- **Update** is atomic with rollback: the previous install is preserved at
  `~/.personalclaw/apps/.{name}.rollback` for the duration.
- **Removal** distinguishes deactivate (providers deregistered, files kept)
  from force-uninstall.

## Permissions (`apps/permissions.py`)

The manifest's `permissions` block is enforced:

| Permission | Enforcement |
|---|---|
| `api` | prefix-allowlist middleware over gateway API paths — pathname only, query string stripped (server and SDK agree on this) |
| `events` | WebSocket fan-out filter — an app's socket only receives event types it declared |
| `mcpTools` | which MCP tools the app may invoke |
| `memory` | tiered scopes (app-scoped by default) |
| `cron` | whether manifest crons register |
| `storage` | a private DATA_DIR handed to the backend |
| `agent` | two independent gates for agent invocation |
| `network` | **DECLARATION-ONLY, unenforced by design** — an app backend is its own OS process with its own network stack; there is no chokepoint. The declaration is surfaced honestly at install consent, and gateway-mediated reach is already bounded by `api`. |

The app identity claim is adopted in **all** auth modes — including
`AUTH_MODE=none`, where a dedicated middleware still extracts the app token so
the permission sandbox holds even with auth off (see
[security.md](security.md#auth-modes)).

## Backend subprocess model (`apps/backend_runtime.py`)

An app with a backend gets its own subprocess:

- auto-assigned port + health check on start;
- a **30-second watchdog** (`start_backend_watchdog`) revives crashed
  backends;
- **PPID-guarded orphan reaping** — after a hard gateway kill, orphaned
  backends re-parent to init; only processes with PPID 1 are reaped, so a
  live sibling's process is never touched;
- `PERSONALCLAW_SKIP_APP_BACKENDS=1` disables backend spawning (test
  isolation).

### The reverse proxy & token model

`dashboard/handlers/apps.py::api_app_proxy` forwards
`/apps/{name}/api/{tail}` to the app's backend, and is where the credential
boundary lives:

- the owner's session credential (cookie + `Authorization`) and any inbound
  app-identity headers are **stripped** — an app backend must never see a
  token it could replay against the full gateway API;
- a **fresh 1-hour app-scoped Bearer token** (`generate_token(user,
  app=name)`, `_APP_TOKEN_TTL_SECS = 3600`) plus `X-PersonalClaw-App` are
  injected, so the backend has an identity bounded to its own declared
  permissions.

## The App SDK

- **Python**: `sdk/` (26 modules) is THE stable app-facing import surface —
  apps import core **only** via `personalclaw.sdk.*`
  (boundary-lint-enforced by `tests/test_apps_import_boundary.py`). Modules
  cover models, channels, tools, search, memory, knowledge, STT/TTS,
  credentials, settings (`ProviderSettings` — each app's persisted store),
  security helpers, and `provider_helpers.register_branded_app` for
  protocol-thin branded model apps.
- **Frontend**: `web/src/app/appSdk.tsx` — a contributed UI gets
  `createAppApi` / `createAppEvents` and mounts via `mount(el, ctx)`; the host
  resolves bare `react` / `@personalclaw/app-sdk` imports so app UIs don't
  bundle their own React.

## Crons

Manifest-declared crons are reconciled by `apps/app_crons.py` on every
lifecycle transition (install/enable/disable/uninstall) and always register
`silent=True`: app crons are headless — no owner-DM or dashboard notification
on their runs (honored on failure too). The manifest `silent` field is
advisory and converged to true. See
[tasks-triggers.md](tasks-triggers.md#app-manifest-crons).

## MCP bridge

An app may ship its **own MCP server(s)** under `manifest.mcpServers`
(distinct from MCP servers it merely depends on). `apps/mcp_bridge.py` writes
them into the live MCP store (`~/.personalclaw/mcp.json`) on enable/install
and removes them on disable/uninstall. Entries are namespaced
`{app}:{server}` so apps can't collide on a server key and deregistration
removes exactly this app's servers. App-shipped stdio servers run with
`cwd=<app dir>` (`mcp_client.py` / `mcp_discovery.py`).

## Extension registration

`providers/loader.py` loads each enabled app, pins the app directory on
`sys.path` for the process lifetime, and registers every contribution through
its typed `ToolTypeHandler` — model providers, transports, search providers,
inbox sources, actions, prompts, skills. Provider REST surfaces live in
`providers/routes.py` / `entity_routes.py` / `instance_routes.py`.

## Related docs

- What belongs in an app vs core: [provider-boundary.md](provider-boundary.md)
- The scanner and install-integrity invariants: [security.md](security.md)
- Channel apps specifically: [inbox-channels.md](inbox-channels.md)
