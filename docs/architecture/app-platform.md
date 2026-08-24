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
| Native (26) | `apps/native/` in-package | seeded on first run, locked on (e.g. `native-agents`, `personalclaw-memory`, the action bundles); may own its provider code — see [the native capability contract](#the-native-capability-contract-appsnative_contractpy) |
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

The manifest's `permissions` block is enforced, with one documented exception
(`network`, which the consent surface marks advisory):

| Permission | Enforcement |
|---|---|
| `api` | prefix-allowlist middleware over gateway API paths — pathname only, query string stripped (server and SDK agree on this) |
| `events` | WebSocket fan-out filter — an app's socket only receives event types it declared |
| `eventSubscriptions` | which **platform** events (`apps/app_events.py`: `session.created`, `knowledge.ingested`, `task.completed`) are delivered to the app. A DIFFERENT axis from `events` above, deliberately: `events` is the WS type allowlist, these are core-emitted facts, and holding one grants nothing about the other. `app_events.emit` is the only delivery path and is the whole gate — deny by default and **exact name only** (no prefix, no `*`), so a typo denies rather than widens. Delivered into the app's broker-owned inbox (the `appMessaging` queue, sender `@platform`, which no app can be named), drained over `GET /api/apps/message`. Payloads carry identifiers only, never prose: a subscription grants timing, not content an app's `api` scope may not cover. |
| `mcpTools` | which MCP tools the app may invoke |
| `memory` | tiered scopes (app-scoped by default) |
| `cron` | whether manifest crons register |
| `storage` | a private DATA_DIR handed to the backend |
| `agent` | two independent gates for agent invocation |
| `appMessaging` | which apps this app may send a brokered message to — `POST /api/apps/message` is the only app-to-app path and refuses an undeclared target `403` + SEL. Deny by default: declaring nothing means it can message no app. Install consent names each target, rendering a trailing-`*` entry as the name prefix it is (`PermissionList`), because the grant covers every current and future app under that prefix. |
| `network` | **DECLARATION-ONLY, unenforced by design** — there is no per-app chokepoint: provider code is imported in-process by the gateway, and an app backend is its own OS process with its own network stack. So it is disclosure, and the Store consent surface says so: the network claim renders outside the enforced-permission list, labelled advisory, whether or not the app declares it (`PermissionList`) — neither its presence nor its absence reads as containment. Gateway-mediated reach is separately bounded by `api`. See [security/limitations.md](../security/limitations.md#2-the-app-network-permission-is-declaration-only). |

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

### The backend environment — an allowlist, not an inheritance

A backend does **not** inherit the gateway's environment. It receives
`sandbox.build_child_env(site="app-backend")`: the `CHILD_ENV_BASE_NAMES`
allowlist (`PATH`, `HOME`, `TMPDIR`, `XDG_*`, locale/`TZ`, proxy + CA vars,
`PYTHONPATH`, and the three `PERSONALCLAW_HOME`/`_WORKSPACE`/`_PORT` vars) plus
any name the operator declared in `sandbox.env_passthrough`, layered with the
four variables the supervisor **computes**:

| variable | when |
|---|---|
| `PORT` | always — the resolved backend port |
| `PERSONALCLAW_APP_NAME` | always |
| `PERSONALCLAW_APP_SECRET` | always (the proxy-signature secret; fail-closed) |
| `PERSONALCLAW_APP_DATA_DIR` | only when the app declares the `storage` capability |

**Why.** An app backend is the least-trusted long-lived child in the tree —
third-party code, scanned but not trusted at install, running for as long as the
app is enabled. `config/loader.py` deliberately seeds `~/.personalclaw/.env`
credentials into `os.environ` so "trusted children" inherit them, so a full
`os.environ` copy handed every one of those credentials to every installed app's
backend. Measured on a real gateway: the pre-change copy delivered ~130
variables the backend had no declared need for, including `SSH_AUTH_SOCK`, AWS
region/SDK vars and the operator's git identity.

**If a backend needs one more variable,** the operator declares it by name in
`sandbox.env_passthrough` (`config.json`). That is an operator surface on
purpose — it is not reachable from a manifest or a trigger payload, because an
app-declared name would be an exfiltration channel. Note that the declaration is
**global**, not per-site: a name declared there reaches every child site (cron,
bash action, app backend). Withheld names are logged at DEBUG against the
`app-backend` site, so an app author whose variable stopped arriving can see
exactly which one was dropped and why. `BackendConfig` has no `env` field — an
app cannot declare its own environment.

The `storage` gate is enforced **after** the build: `PERSONALCLAW_APP_DATA_DIR`
is popped when the app lacks the capability, so declaring that name in
`sandbox.env_passthrough` cannot hand every storage-less backend a data dir and
quietly undo sandbox P3.

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

### Inbound authentication — the proxy signature (what loopback does NOT buy)

The token model above is the **outbound** boundary (what the backend may do
back at the gateway). The **inbound** boundary is separate and, until this was
added, missing: a backend binds `127.0.0.1:<ephemeral port>` — a *network*
boundary, not an *authorization* one. Loopback keeps off-box hosts out; it does
**not** keep out other local processes. Before the fix, any local process that
found the port could talk to the backend **directly**, bypassing the proxy and
therefore session auth and `app_permission_middleware` entirely. The app
platform's whole permission story assumes requests arrive through the proxy —
so that assumption is now enforced, not merely documented.

Every request the proxy forwards carries an HMAC signature the backend verifies
**fail-closed**:

- **Header:** `X-PersonalClaw-Proxy: <ts>:<hmac_hex>`.
- **Signed message:** `<ts>:<METHOD>:<raw_path?query>:<sha256_hex(body)>` —
  binding the method, the exact wire path (aiohttp's `request.raw_path`), and a
  hash of the body, so none can be altered in transit.
- **Window:** `ts` is an integer unix second; a signature more than **±60s**
  from now is refused (replay protection). The compare is constant-time
  (`hmac.compare_digest`).
- **Secret:** a per-app 256-bit key at `apps_dir()/<app>/.app_secret`, minted
  0600 on first backend start and injected into the child via
  `PERSONALCLAW_APP_SECRET`. It is never logged.
- **Verifier:** `personalclaw.sdk.security.require_proxy_signature()`, an
  aiohttp middleware every first-party backend installs (and every third-party
  backend should). It reads the body once and stashes it on
  `request["body_bytes"]` so a route reads it without consuming the single-read
  stream twice.
- **`/health` is exempt.** The 30-second watchdog probes the backend by process
  liveness today, but the health *path* is reserved for direct probing, so it
  must not require a signature — otherwise a direct health probe could never
  succeed.

**Fail-closed, both ends.** The mint is fail-closed: a backend that cannot
write/read its secret does **not** start (better missing than unprotected). The
verify is fail-closed: no secret in the environment, or an absent / malformed /
stale / mismatched signature, returns `401` and the route body never runs.

**What this does and does not buy.** It proves a request *came from the gateway
proxy* (which itself enforced session auth + permissions), so it closes the
direct-to-port bypass. It does **not** encrypt the loopback traffic, and it does
**not** defend against a local process that can read the 0600 secret file — on a
single-user machine an attacker with the owner's file access has already lost
the game. It raises the bar from "any local process" to "a process that can read
a root-only-readable file," which is the boundary a single-user posture supports.
Denials are logged (a structured stderr warning in the app process, since a
backend has no access to the gateway's SecurityEventLog).

## The App SDK

- **Python**: `sdk/` (33 modules) is THE stable app-facing import surface —
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

## The native capability contract (`apps/native_contract.py`)

A **bundled** app may own its own provider code, not just declare a capability core
implements. Historically every bundled app was `app.json`-only: its
`provider.implementation` named a core dotted path
(`personalclaw.tasks.native:create_provider`), so growing that capability meant editing
core — the one thing this platform exists to avoid.

**How a bundled app owns a provider**

1. Ship `provider.py` next to `app.json` in `apps/native/<name>/`.
2. Point the manifest at the bundle-relative module:
   `"implementation": "provider:create_provider"`. A module path with **no dot** is
   bundle-relative; a dotted one is still a core/package path, so existing bundles are
   untouched.
3. Import core **only** through `personalclaw.sdk.*`.

**Allowed imports = the published SDK, and nothing else.** This is deliberately the SAME
rule installed apps live under, not a second, narrower "native SDK" allowlist: a bundled
app is loaded by the same `providers/loader.py` seam, registered through the same typed
handler, and shipped by the same release, so a separate list would be a second boundary to
keep in step for no gain. Every `sdk/` submodule is therefore available. What is
native-specific is a set of caveats, not import bans:

| Caveat | Why |
|---|---|
| No per-app backend environment | a bundled module runs IN-PROCESS, so `sdk.util.shared_app_data_dir` is always `None` for it and the `PERSONALCLAW_APP_*` vars `backend_runtime` injects don't exist |
| No own dependencies | the manifest `dependencies` block installs into an app venv, which an in-process module never gets — a bundled module may use only core's own dependencies |
| Packaged assets by path are OK | a bundled app ships in the same distribution, so reading a packaged sibling (e.g. `static/dist/ui-docs.json`) by path is legitimate; **importing** a core module is not |
| Blocking work goes off-loop | it shares the gateway's event loop; use `asyncio.to_thread` as core does |

**Loading.** `providers/loader.py` resolves the module by ONE rule for both tiers: if the
`implementation` module path resolves to a file inside the app's own directory, it is
loaded from there under a namespaced `sys.modules` name
(`_pclaw_app_<name>__<module>`, `native_contract.namespaced_module_name`); otherwise it is
imported as a dotted package path. The namespacing is load-bearing — two apps commonly
ship the same bare `provider.py`, and a plain `import provider` would let the first one
win while the second silently mis-loaded. It is cached, so the availability probe and the
factory never re-execute app code (a re-exec would mint a second class for one provider,
breaking `isinstance` across two reads). A changed module needs a gateway restart.

**Enforcement.** `native_contract.contract_violations` is the lint;
`tests/test_native_capability_contract.py` runs it over every bundled module and carries
the vacuity floor (at least one bundled app must actually ship a module) plus the
"no core implementation" property: no core module may reference a bundle-owned module.
That rail never skips, unlike its installed-app twin
(`tests/test_apps_import_boundary.py`), which skips whenever the workspace `apps/` dir is
absent.

**Reference implementation:** `apps/native/personalclaw-ui-docs/` — the design-system docs
tools (`ui_search` / `ui_get` / `ui_list`). Its provider left `tool_providers/ui_docs.py`
entirely (no factory remains in `tool_providers/registry.py`), and `ui_list` was then added
inside the bundle with no edit to any core module that implements, resolves or dispatches
it. One residual core touch remains for a bundled app that adds an agent **tool**: a new
tool name needs a `manifest_meta.TOOL_META` entry, because that map is the hand-maintained
input to the agent manifest and `tests/test_api_manifest_drift.py` fails on a tool without
one. That is catalogue data about the shipped distribution's agent surface, not provider
implementation — but it does mean "no core edits" is exact for provider behaviour and not
yet exact for tool *metadata*.

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

## Declared quality bar (`apps/quality.py`)

An app may declare `quality` — `{tested, designSystem, a11y}` — which the Store
renders as the card's badge row (`web/src/pages/apps/qualityBadges.tsx`). Each
axis is **tri-state, and that is the contract**:

| value | meaning | rendering | verified? |
|---|---|---|---|
| absent | claims nothing | no badge at all | nothing to verify |
| `false` / `"legacy"` / `"n/a"` | an honest miss | a muted MISS badge | nothing to verify |
| `true` / `"v2"` | a claim | a MET badge | yes, for first-party |

Absent and declared-false are **different facts**. Collapsing them either way is
a lie: rendering absent as a pass is the obvious one; rendering it as a miss shows
an app failing a bar it never entered. `QualityDeclaration` keeps the distinction
at the parse boundary and emits only declared axes on the wire.

For a **first-party** app the block is not just decoration — the apps-repo CI runs
`python -m personalclaw.apps.quality .` and exits non-zero when a claim outruns the
evidence in the bundle:

- **`tested: true`** — the bundle ships `test_*.py` (root or `tests/`) *and* they
  pass. Presence alone is not evidence, or an empty `tests/` would buy the badge.
- **`designSystem: "v2"`** — every `*.ts`/`*.tsx` in the bundle passes token-lint,
  by the SAME rule the host frontend is held to. The patterns are shared data
  (`apps/token_lint_rules.json`), read by both `apps/quality.py` and
  `web/src/design/tokenLintRule.ts`; `tokenLintRuleParity.test.ts` fails if the two
  drift. A second implementation of the rule would be the same declared-vs-actual
  drift one layer down.
- **`a11y: true`** — the bundle ships `a11y/axe-report.json`
  (`{"appVersion", "tool", "violations": []}`) whose `appVersion` matches the
  manifest's, so a clean scan of the previous release cannot launder this one. axe
  needs a browser the apps-repo CI has none of, so the app produces the artifact and
  CI checks it.

A claim with **nothing to check** is a violation, not a free pass: `"v2"` with no
frontend to lint and `a11y: true` with no report would both badge a check that never
ran. The CLI also exits non-zero on a tree containing no `*/app.json` at all — a
checker that silently checked nothing is the failure mode this whole surface exists
to prevent.

Third-party declarations are unverified by construction, which is why the badge
tooltip says *declares*, never *verified*.

## Extension registration

`providers/loader.py` loads each enabled app, puts the app directory on
`sys.path` only while its module executes (so a later bare import can't pick up an app's
module by accident — the app's own module is registered under a namespaced
`sys.modules` name instead), and registers every contribution through
its typed `ToolTypeHandler` — model providers, transports, search providers,
inbox sources, actions, prompts, skills. Provider REST surfaces live in
`providers/routes.py` / `entity_routes.py` / `instance_routes.py`.

## Related docs

- What belongs in an app vs core: [provider-boundary.md](provider-boundary.md)
- The scanner and install-integrity invariants: [security.md](security.md)
- Channel apps specifically: [inbox-channels.md](inbox-channels.md)
