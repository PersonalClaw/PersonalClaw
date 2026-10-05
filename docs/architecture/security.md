# Security Model

Defense in depth for a system that runs an autonomous agent on your machine:
authentication modes, command screening, an OS sandbox, one egress chokepoint,
scoped tokens, supply-chain gates, untrusted-content fencing, and a
tamper-evident audit log. Paths are relative to
`PersonalClaw/src/personalclaw/`.

> This is the internal architecture reference. For the externally-facing view —
> trust boundaries, the OWASP Agentic Top-10 (ASI) mapping, and an honest
> statement of limitations — see the public [threat model](../security/threat-model.md)
> and [limitations](../security/limitations.md). To report a vulnerability, see
> [`SECURITY.md`](../../SECURITY.md).

## Auth modes

`auth/modes.py` defines two modes, and `AuthConfig.from_env()` selects between them:
`PERSONALCLAW_AUTH_MODE=none`, or the `local_token` default for anything else.

| Mode | Behavior |
|---|---|
| `none` | No token auth — **bind is forced to loopback** by `effective_bind` (an unauthenticated gateway must never leave the host). Dev convenience. |
| `local_token` | The default: token auth with a login page; static assets bypass the check (the real asset surface only — `dashboard/token_auth.py`). An opt-in, IP-gated local-network bypass exists. |

`api_key` and `oauth2` used to be declared here too, with their request-side halves built
(a Bearer-key check and an OIDC JWT verifier) and no configuration able to select either.
They were deleted: an authentication path nothing can reach still has to be read, audited
and kept secure, and it misleads whoever finds it. A browser SSO sign-in, if it is ever
built, is one more issuer of the existing session (see
[`sso-oidc-integration.md`](sso-oidc-integration.md)), not a mode.

**The runtime says so out loud.** A value that is not a mode — `api_key`, `oauth2` or any
other string — is not ignored *silently*: `classify_auth_mode_request()` in `auth/modes.py`
describes the request, `AuthConfig.from_env()` logs a warning naming the requested value,
that it was not applied, and the mode actually in force, and `personalclaw doctor` prints
the same sentence as an `auth mode:` row. It fails **closed** — `local_token` stays in
force, so the cost of a typo is lost access, not weakened auth. This is legibility only: it
changes no admission decision, and `doctor`'s exit status is unaffected.

### The `AUTH_MODE=none` sandbox fix

Skipping the token-auth middleware in none-mode used to silently disable the
**entire app permission sandbox**: the middleware is what adopts the `app`
claim from an app-scoped token, and without it an app-scoped request could
reach ANY `/api` path. The fix (`dashboard/server.py`, the
`_dev_user_middleware`) adopts the claim in none-mode: `token_auth.presented_app`
reads the Bearer/`?app_token=` token, validates it (`validate_token_with_app`), and
the middleware sets `request["app"]` so `app_permission_middleware` and the WS event filter
scope the request. The local-network bypass (`PERSONALCLAW_BYPASS_LOCAL_NETWORKS=1`) admits a
request without its sign-in too, and adopts the claim by the same reader: it used to drop it, so on
a bypassed network an app's page or backend reached every route you do, as you. The app token
only *narrows* the reach of whoever the mode admits the request as — the permission model, and
whose work a request is, hold in every auth mode.

### The desktop app's gateway

The Electron shell starts its own gateway in the default `local_token` mode on loopback, as
every install runs. Its environment builder (`desktop/gatewayEnv.js`) sets no authentication
switch, and drops an inherited `PERSONALCLAW_AUTH_MODE`, `PERSONALCLAW_BYPASS_LOCAL_NETWORKS`
or `PERSONALCLAW_BIND_HOST`, so a shell started from a terminal that exports one still starts a
gateway that asks for a sign-in. App tokens, the internal credential's operation list and the
rule that only you answer an approval therefore hold on a desktop install exactly as elsewhere.

The shell signs itself in with the owner session the gateway's `--json-ready` line mints for each
start (`desktop/localSignIn.js`), which it reads from the child's stdout pipe and keeps in
main-process memory:

- its windows carry it as the `pc_token_<port>` session cookie: `HttpOnly`, `SameSite=Lax`,
  host-only, and with no expiry, so Chromium keeps it in memory and never writes it to the app's
  cookie file;
- its own requests (the tray's counts, the capability registration) carry it as
  `Authorization: Bearer`;
- it is never put in a URL, a log line, a file or a message to a renderer. The `?token=` link
  would have left it in the window's history and a 30-day cookie on disk.

A restart in place prints a new ready line: the shell moves the cookie to the new port, reloads
each window that showed the old address, re-registers its capabilities and signs the previous
session out. Quitting signs the session out (`POST /api/auth/logout` with that session's own
cookie) before the gateway stops. A gateway started on a port the system picks names its session
cookie for the port it serves on (`token_auth.session_cookie_name`), which is the name the shell
sets.

The shell's windows show only the active gateway's origin, and `desktop/systemBrowser.js` holds the
rule for every other page with its three doors: the window handler lets a window open only on that
origin and denies the rest, a blank window included; the navigation guard cancels a navigation or
redirect that leaves it; and both hand an `http` or `https` page to the system browser. The bridge's
`systemBrowser.open(url)` is the same rule as a call that answers whether the browser opened, which
the Tools page uses for a remote MCP server's sign-in instead of a blank window. The address is
checked in the main process, whatever the renderer sent: anything that is not an `http` or `https`
URL (a file, another app's scheme, a blank page) is refused and never reaches `shell.openExternal`,
and the address is never logged, since a sign-in page's carries its single-use `state`.

## Token scoping

`dashboard/token_auth.py`:

- **One credential, three carriers.** `_select_request_credentials` is the one rule
  every auth path uses to pick the credential that authorizes a request. `?token=`
  is the browser's entry link: bound to the first client address that uses it and
  exchanged for the `HttpOnly` `pc_token_<port>` cookie. `Authorization: Bearer` is
  for every client that is not a browser: it accepts exactly the sessions the cookie
  accepts, judged by the same rules (signature, session lifetime, a live nonce), and
  it is stateless — no cookie, no address binding — and keeps the token out of URLs.
  A different owner token in the header beside `?token=` is refused
  (`auth_credential_conflict`); a Bearer with nothing else to stand on that is not a
  live owner session — expired, revoked, forged, an app's, another surface's — gets
  the one `auth_bearer_invalid`, and no refusal echoes or audits the token. Only the
  Bearer scheme is the gateway's: another scheme in the same header (a reverse
  proxy's `Basic` login) is ignored, not refused.
- `mint_session(user_id, ttl, issuer=...)` mints every session, naming the door it came
  through (the startup link, the harness token, `personalclaw token`, a password, a device
  code, a pairing, an app); `generate_token(user_id, ttl_seconds, app=...)` is core's mint for
  the `token` door, with an optional **`app` claim**. A channel app mints only its owner's
  "open the dashboard" link, with `personalclaw.sdk.channel.owner_sign_in_token`, which refuses
  anyone else; the SDK publishes no mint for an arbitrary id. App-scoped tokens bound a
  request to that app's declared permissions. In the Bearer header an app token only narrows
  the owner session it is presented beside, for the same user.
- **Whose work a request is, is who signed it in** (`approval_answer.work_of_request`). An app's
  token is the app's own work (`app:<name>`) whatever else the request carries: a session it names
  in `X-Session-Key` is not its work, so the app is held to the spend caps for work nobody
  watches, what it writes is filed under the app and its audit rows name it. The session a request
  names is its work only for your signed-in session (the chat your page is in) and for
  PersonalClaw's own processes (the internal credential). A caller a route signs in itself (a
  client of the OpenAI-compatible endpoint, a webhook's sender) names none, and that route says
  whose work the request is. Every gate that judges a request's work asks this one answer: the
  spend caps, whether anybody watches it, what of your memory it reads and changes, what it is
  filed under and what its audit rows name.
  `tests/test_a_requests_work_is_the_caller_its_sign_in_proves.py` fails any other reader of the
  header.
- App backends never see the owner's credential: the reverse proxy strips
  cookie + Authorization and injects the app's own 1-hour app-scoped token
  (see [app-platform.md](app-platform.md#the-reverse-proxy--token-model)).
- No session, link or token lasts longer than 90 days (`MAX_SESSION_TTL_SECS`): a long-lived
  credential is replaced at least every 90 days, because the longer a link or token keeps
  working, the longer anyone who copies it can use the dashboard. A request for longer — `personalclaw token --ttl`, `?ttl=` on
  `/api/token/local`, `auth.session_ttl` (Settings → Security sets it, and offers nothing longer),
  a channel's `owner_sign_in_token` — is refused with a sentence
  naming the limit and why, never shortened; a config file that already says longer is applied
  as 90 days and `personalclaw doctor` says so; and a token minted longer before the limit
  existed stops 90 days after it was issued. Browser sign-ins last `auth.session_ttl`, 30 days by
  default, and a token that names no lifetime 20 hours. How many may be signed in is bounded per kind — 20 browsers, 20 paired
  devices, 20 tokens, and each app separately — over the durable store, so the limit holds
  across restarts, and the one it signs out is the least recently used of its own kind. Every
  sign-in and sign-out is an SEL row (`session_signed_in` / `session_signed_out`, with the
  reason). Every refusal a browser or the desktop app can meet carries a sentence saying why and
  how to sign in (`session_signed_out`, `session_expired`, `session_required`); a request that
  presents garbage, or a token another key signed, reads exactly what presenting nothing reads.
  A script's Bearer keeps the one uniform `auth_bearer_invalid`.
- **An integration's token is under the same limit** (`inbound/tokens.py`). The token an external
  agent reaches an inbound surface with — a surface's own (`personalclaw inbound token create
  <surface> --ttl`) or a registered client's (`POST /api/external-access/clients` with a `ttl`) —
  lasts at most 90 days, and a request for longer is refused. A surface token's lifetime is
  recorded by the token's SHA-256 in `inbound_tokens.json`, never the token itself: one from
  before lifetimes existed, or set outside the CLI, lasts 90 days from the first time the gateway
  sees it, and a client registered before them lasts 90 days from its registration. A configured
  value that briefly cannot be read (a locked keychain) keeps the lifetime it had, a revoked token
  stays refused for as long as it is configured, and a registry that cannot be read refuses every
  surface token rather than give each a fresh 90 days. Settings → Devices lists every integration
  token with when it stops working, and revokes any of them at once; each start and end is a
  `session_signed_in` / `session_signed_out` row. A token that expired, was revoked or was
  replaced is told which, and when, under the same `unauthorized` code every other inbound refusal
  carries; a token the gateway never issued is told nothing more.
- **Minting a lasting credential needs the owner here now, not just a live session**
  (`dashboard/owner_presence.py`). A session lasts weeks, so a phone left unlocked or a copied
  cookie would otherwise be enough to plant a way in that outlives it. Pairing a device, making a
  device sign-in code, creating an integration or webhook token, pairing a channel's owner (who
  can then ask that channel for a dashboard link), setting the first password, making sign-in
  less strict (`auth.*` loosened) and storing a secret whose name changes who can sign in
  (Settings → Secrets: the 2FA seed, an inbound surface's token, a channel owner's id, the
  local-network bypass and the other sign-in settings the gateway reads from its environment,
  `secrets_vault.is_sign_in_key`; a connector pack may not name one) each call
  `require_owner_presence`, which accepts a sign-in from
  the last 10 minutes, the local machine secret (`X-Local-Secret` from this computer), the
  session the gateway handed the process that started it used from this computer (the desktop
  app's own windows, `personalclaw run`, a harness), or authentication being off. Anything else
  gets `401 fresh_sign_in_required` with the sentence saying how to sign in again; the dashboard
  asks then (`POST /api/auth/confirm` with the password, which replaces this device's sign-in, or
  a new `personalclaw token` link where password sign-in is off) and sends the write once more.
  Changing an existing password asks for the current one, and the 2FA code when one is enrolled,
  whatever the session; a wrong one is refused, logged and counted toward the sign-in lockout.
  Containment never asks: signing devices out, replacing the key, revoking a token, turning
  incident mode on, tightening sign-in. `tests/test_owner_presence_census.py` fails a route that
  mints a credential without asking, and one that contains and does.
- **The keys that are not sign-ins have a lifetime too, each the one its job allows.**
  - An app backend's proxy secret (`apps/<app>/.app_secret`, the HMAC key the reverse proxy signs
    with) is minted afresh each time the backend starts (`apps/app_secret.mint_app_secret`), so it
    lasts as long as that backend, and a copy stops working the next time it starts.
  - The 2FA seed lasts as long as 2FA is enrolled: `personalclaw auth totp disable` deletes it, and
    enrolling again mints a new one. Turning 2FA off is refused while `auth.require_totp` is on,
    since login would then ask for a code nothing can produce.
  - A trigger's question (`triggers/parks.py`) is answerable for the window a workflow's resume
    token has (`workflows/human_input.DEFAULT_RESUME_TTL_SECS`, 7 days); after that its answer is
    refused, its Inbox row closed, and the trigger asks again, with a new token, the next time it
    stops.
  - The SEL integrity key (`sel_hmac.key`) lives as long as the log it signs: a new key would
    leave every earlier record unverifiable, which is the one thing the chain is for. No export
    carries it; a snapshot does, so a restore can still verify the records it brings back.
  - The web-push keypair lives until `personalclaw push init --force` replaces it: every paired
    device's subscription is bound to its public key, so replacing it signs every device out of
    push, which is the right trade after a compromise and the wrong one on a timer. Each push
    message's own VAPID signature lasts at most 12 hours.

### The internal credential

PersonalClaw's own processes call their gateway over loopback: an agent's tools (in the gateway
itself, and in the `mcp-core` server an agent CLI runs), the artifact store written in any process
but the gateway, naming the artifact it wrote (`POST /api/artifacts/{slug}/changed`), a tool the
gateway runs for a request (`POST /api/tools/invoke`), a scheduled script's `ctx.notify` and
`ctx.call_tool`, `personalclaw cron trigger` and `personalclaw auth rotate-key`. They carry the
gateway's internal credential,
`X-Internal-Secret`, which the gateway writes to `<home>/.local_secret` (0600) each time it starts.
Every caller reads it from the same home, resolved at the call (the gateway declares
`PERSONALCLAW_HOME` to each `mcp-core` server it starts), and none makes a home to look in one.

- **It opens a list of operations and nothing else.** `dashboard/server.py` names each one as a
  method and a route in the router's own syntax (`INTERNAL_ROUTES`, `MIXED_INTERNAL_ROUTES`,
  parsed by `token_auth.InternalRoute`), matched whole: an entry opens neither a route under it nor
  another method on the same route. A strict entry is refused from off this computer. A mixed one
  is the dashboard's too, so a browser's session is judged there from any address.
  `tests/test_every_internal_call_names_an_operation_that_takes_it.py` reads the calls and the
  lists out of the source, and fails when either names what the other does not.
- **Presented anywhere else, it is refused as what it is:** `403 internal_route_refused`, whatever
  credential rides beside it, with an audit row. It used to be ignored there, so the call was judged
  as a browser that had not signed in and answered with the sign-in sentence, on every install. The
  development server's local-network bypass admitted it, so this refusal runs ahead of that bypass.
- **A call names the work it is for, or it is not made.** The credential says only that a call
  comes from one of PersonalClaw's processes; what it may reach is decided by the work it names in
  `X-Session-Key`, read as any session is: an agent's tools name their chat (a warm-pool agent CLI
  is tied to the chat that claims it, `session_pid.tie_to_session`), a tool the gateway runs for a
  request names that request's work (an app's, a scheduled job's, yours), a scheduled script its
  job (`cron:<id>`), `personalclaw cron trigger` the chat whose shell runs it, your own command
  (`cli:`) when you type it at a terminal and a dispatch with no session when a script runs it,
  and `personalclaw auth rotate-key` your own command. A call that names none was read as yours:
  it read and changed your
  memory, and a run it started belonged to nobody and kept everything. It is refused before any
  handler runs (`dashboard/memory_write_gate.py`, `403 internal_call_names_no_work`), with an audit
  row, and the tool server an agent CLI runs makes no call it cannot name the chat of, its own part
  included (`mcp_core._call_as_its_session`).
- **It goes to the gateway and nowhere else.** Every request that carries it is sent through
  `home_gateway.open_loopback` (a scheduled script's launcher, which cannot import PersonalClaw,
  builds the same opener): never through a proxy the environment names, which urllib hands even a
  loopback request to, headers included, and which a child is handed on purpose for its own
  downloads; and never on to where a redirect points. A command run for a home first asks the
  gateway at the port it found which home it serves (`home_gateway.reach`), and sends nothing more
  to another home's gateway. `tests/test_a_command_reaches_only_its_own_homes_gateway.py` finds
  every module that puts the credential in a header and fails one that opens a request another
  way.
- **A credential this gateway did not issue** is refused with `403 internal_secret_invalid`, whose
  sentence says so. **A caller with none to read sends nothing:** a tool's result names the home it
  looked in (`mcp_core._internal_secret`), and a scheduled script's call back returns
  `{"ok": false, "error": {"code": "internal_secret_unavailable", …}}` while the rest of the script
  runs.

### Webhooks

The webhook is how a program outside PersonalClaw starts work in it over HTTP
(`inbound/webhook.py`, which holds the rules and the words). It has two doors:

| Door | Starts | Credential the program sends |
|---|---|---|
| `POST /api/triggers/<automation-id>/fire` | that webhook automation's action | `Authorization: Bearer <sender token>`, a token made for that automation |
| `POST /api/hooks/agent` | an agent turn: a callback the agent registered with `hook_register`, once the owner allowed it, or the owner's own integration | `Authorization: Bearer <webhook token>` (or `x-personalclaw-token`), the owner's `hooks.webhook_token` |

Both keep the inbound surfaces' rules:

- **They sign their callers in themselves.** The dashboard's sign-in lets both through
  (`token_auth._BYPASS_EXACT`, `_BYPASS_TEMPLATES`), and neither takes the internal credential:
  presented, it is refused as one (`403 internal_route_refused`, a Security-log row). It opens
  every internal operation, so no outside program, and no relay for one, is ever handed it.
- **Programs on this machine only.** Each takes a request only from loopback, whatever address the
  gateway listens on; one from any other address is refused `403` before its token is read, with
  the sentence that says where it came from and how to reach the door: forward a port to the
  machine's 127.0.0.1 over SSH, or run a relay on the machine that forwards to that address. A
  relay is how an owner exposes a webhook deliberately, and the token still decides. (The CSRF
  check refuses such a request first when it carries no browser origin, in the same words and
  with the same rows: `dashboard/origin.py::origin_refusal`.)
- **Held, capped and audited.** An active incident refuses both (`503`). Each is rate-capped
  (`429`, `Retry-After`): a sender token per sender, the agent-turn door per caller before its
  token is read. Every request either door answers, and every one the gateway refuses before it
  for coming from another machine, is one row of `inbound_audit.jsonl` (surface `webhook`); a
  refusal is a Security-log row too, and so is an accepted call, under its sender
  (`inbound:webhook:<client>` or `inbound:webhook`, outcome `allowed`), since it starts work
  nobody watches.
- **Refused in a sentence**, under the one error envelope: no token or an unknown one (`401
  unauthorized`, saying what to send and where the owner makes one), and at the fire door a sender
  token that was revoked or ran its lifetime (`401`, saying so and when), one made for another
  automation (`403`), an automation that is not there, is switched off, or was written on another
  machine (`404`, one answer for all three), an automation whose action its owner has not allowed
  (`403`), and a body over its 64 KB cap (`413`).

**A sender token** is a registered inbound client (`inbound/clients.py`) bound to the `webhook`
surface alone and pinned to one webhook automation (`scope.trigger`, the automation's id as its
page shows it, `store:webhook:<name>`); it carries no agent, tools or upstream. The owner makes one
on the automation's page, with `personalclaw inbound webhook create <automation-id>`, or through
Settings' route (`POST /api/external-access/clients`), which refuses any other shape with a
sentence. It is 64 random characters, shown once and kept only as a SHA-256 hash, and works at
most 90 days. The automation's page and Settings → Devices list it and revoke it, and Settings →
External Access lists it, switches it off and revokes it. Deleting the automation revokes its
sender tokens (`TriggerStore.delete`): one made again under the same id (an automation that
never ran, a restore, an import) gets the same address, and a token made for the first must not
fire it. The fire is then held as any fire of the automation is, because it is one: its own switch
and its grant; its admission (`triggers/service.py::admit_fire`), the one the clock's and an
event's fires walk, so its hourly cap, spacing, quiet hours, budget and overlap hold it
(`429` or `409 fire_held`, saying which, and a skipped row in its history); and the dispatch every
fire runs through (`gateway._fire_store_trigger`, which the gateway hands the dashboard), so the
body is screened and fenced as data, the action denylist and the autonomy ladder judge what it
runs as work nobody answers, the day's budget pauses it, and its run is recorded as a fire, whose
failures count toward the streak that pauses the automation. Nothing the body says chooses where
the work happens: an action's folder, project, chat or session is its automation's own, from its
settings, or the run's for a workflow step, and never the fire's payload. Every action reads a
run's folder, project and identity through one door that only a workflow step's dispatch opens
(`action_providers.base.run_identity`), so no fire's payload can stand in for them.

**The webhook token** is `hooks.webhook_token` in `config.json`, a `{{secret:…}}` reference there,
resolved from the credential store at the check (`config/secret_refs.py`), and compared in
constant time (`dashboard/handlers/hooks.py::_hook_token_refusal`). No token, a reference the store
cannot answer or that names another owner's credential, a token shorter than 32 bytes, or one that
is also the internal credential or a surface token: every request is refused. A session key a
callback the agent registered names (`webhook_callbacks.py`) starts a turn only once the owner
allowed that callback: until then the answer is `403 not_allowed`.

The turn a webhook starts runs unattended, under the headless profile
(`guardrails.policy`, a `hook:` session): its tool grants are `read`, so a call
whose tool does not declare it only reads is refused before anything could
approve it; a call that needs approval is declined at once, since nobody would
see the prompt; and no tool that asks a person something is offered. A turn on
an agent CLI, which runs its tools where the host cannot hold them to those
grants, is refused before its message is sent, and the owner is told why.

## Command screening (`security.py`)

- **Deny list** — `BUILTIN_DENIED_COMMAND_PATTERNS` (112 shell patterns) is
  merged with user-configured `security.denied_commands` **at read time**
  (`denied_command_patterns()`), so config edits apply immediately, and the
  Security panel shows the same list. `denied_command` is the one check every
  path that runs a command someone wrote asks before it runs it, matched
  case-insensitively: the native bash tool (which Tools → Try it and a script's
  `call_tool` reach through `/api/tools/invoke`, asking the tool's pre-flight
  before any confirmation), a command an agent CLI asks the host to run
  (`acp.permission_authority.screen_tool_call`, the hook chain's verdict read on the
  command the call's input gives, as text or as a list of words, whatever its title
  says: the one screen every path that approves or asks about a call asks first,
  whatever its approval mode, in a chat, a channel's own conversation, a subagent, a
  room, a background call under every mode and an evaluation, and PersonalClaw's own
  agent's gate for a call its approval policy answers unasked), a loop's or a workflow's check
  (`loop.gates.run_verify_command`), a workflow step or effect teardown, a bash
  action however it started (and a payload value its command runs as a command,
  `sh -c "$CMD"`), an app's setup hook, and the action dispatch denylist
  (`guardrails.denylist.check_action`). Refused before anyone is asked; an
  unattended run records it on the run (a loop pauses with the rule as its
  question, a workflow gate fails with it, a trigger fire's history names it).
  A loop plan or an automation whose command it refuses is refused when it is
  saved, and a dry run of an automation saved before says a real run is refused.
  Each refusal, by the denylist, the credential-path check or the file tools'
  reach, is one `refused` row in the audit log naming the control and its rule
  (`llm.events.TOOL_META_REFUSED_RULE` for a tool call, `command_refused` for a
  command path), whatever answered the call's ask, and one WARNING line in the
  gateway log.
  `tests/test_every_command_path_asks_the_denylist.py` fails a spawn site that
  runs a written command without asking, and
  `tests/test_every_approval_path_asks_the_deny_list_first.py` a place that approves
  a call without asking the screen. What a text pattern cannot see is
  [limitations §15](../security/limitations.md#15-the-shell-denylist-reads-a-commands-text).
- **Suspicious-pattern watchers** — `SUSPICIOUS_BASH_PATTERNS` (52 patterns)
  flag rather than block.
- **Tool-name denies** — `BUILTIN_DENY_PATTERNS` (fnmatch over tool names)
  with a documented `_DENY_EXCEPTIONS` escape hatch, and the operator's own
  `hooks.auto_deny_tools`, both read by the hook chain at each call through the same
  screen (`screen_tool_call`): on an agent CLI's call and on PersonalClaw's own agent's,
  whatever grant would answer it, and on Tools → Try it and a scheduled script's tool call
  by the tool's name. An exception is applied only to a line the shell reads as one command
  (`shell_syntax.one_command`), so a push joined to `git stash push`, the background `&`
  included, is refused as the push it is.
- **An operator's auto-approve pattern approves one command** (`hooks.auto_approve_tools`,
  `hooks._pattern_verdict`). A pattern that names a command approves that command only, read
  as the shell reads it (`shell_syntax`, the reader the task-mode gate reads commands with): a
  second command joined by any operator the shell runs, the background `&` included, a redirect
  other than one descriptor copied onto another (`2>&1`), and a substitution or other syntax
  the reader does not parse are each more than it names, and the call asks. A shell call is
  decided on the command its input gives (`task_modes.shell_call_command`), never on the
  permission frame's title, which can be shorter than the command. `*`, a class-wide pattern
  (`Running: *`) and a pattern that itself joins commands are matched as they are written, and
  a pattern naming the shell tool itself (`bash`, `Terminal`) approves every command of it. A
  call that runs no shell command is matched on its tool's name.
- **The operator's blocking hooks are asked first, on every path** (`pre_tool_hooks`). The
  `PreToolUse` hooks bound to the agent a turn runs as (the default agent's, for a turn that
  names none) are asked once about each call, at one step: past the deny list, the task mode and
  a run's own bounds, grants and tier, before any pattern, grant or person can approve the call.
  Every path that runs a call asks them there: the built-in runtime, for each call it makes; the
  gate a chat, a subagent and an evaluation put an agent CLI's call to; the background helper
  (a heartbeat task, a subagent's report, a room member's turn); and a channel app's own turn,
  through `personalclaw.sdk.channel.ask_pre_tool_hooks`. A call the built-in runtime asks a host
  about met them at its own step, so no host asks them again. A hook that blocks the call refuses
  it in its own words, and so does a hook that fails to run: the agent's bindings cannot be read,
  the store cannot fire its hooks, or a hook did not run to an exit of its own (its command could
  not start, was refused its sandbox or timed out, its action was held or refused, its provider
  failed). A hook that ran and exited with another code lets the call go on, as a non-blocking
  error. A call no agent makes (Tools → Try it, a scheduled script's own tool call) has no bound
  hooks to ask.
- **Credential screen** — `is_sensitive_bash_command`, run by the native bash tool
  (before its deny list), a bash action and the ACP permission hook. It refuses a command
  that names a file only its owner reads (`SensitivePaths` without the `$HOME`
  folders: PersonalClaw's credential store and keys in the active home and the
  default one, `_pclaw_homes`, and another tool's sign-in, `_sign_in_files` and
  `SIGN_IN_FILE_BASENAMES`), one that returns a credential folder under
  `$HOME` (`_SENSITIVE_HOME_DIRS`), and one that names such a folder itself or a glob
  the shell expands to it or into it (`_shows_credential_folder`, with the shell's own
  glob rule from `command_paths.shell_expands_to`). For the first and the third, the
  paths a command names are read by `command_paths.named_paths`, the same reading the owner-only fence uses: the homes
  written out as a shell or a one-liner spells them, a relative path against the folder
  it runs in and every folder a `cd` moves to, links, globs and brace lists. The file
  tools and the dashboard ask `is_sensitive_path`, the same declarations. What no
  reading of the text can see is
  [limitations §13](../security/limitations.md#13-the-agents-shell-is-screened-not-fenced-from-your-credential-files).
- **Redaction** — sensitive-path and credential redaction, including
  vendor-token detection patterns (e.g. `xox[bpas]-`). These vendor-shaped
  patterns are deliberate keeps: they are secret-*detection* data; renaming
  them would break the control (see
  [provider-boundary.md](provider-boundary.md)).

### Who is watching (`session_keys.py`)

Whether anybody watches a piece of work is decided by the kind of session key it runs under, and
every gate on this page asks the same table: the safety profile a run resolves (`HEADLESS` for work
nobody watches), the approval posture of a run nobody can be asked in, the dollar caps an unattended
image, video, speech or transcription is held to, the self-stop check below, the rules a
subagent's report is handed back under, the check of an agent CLI's adapter, and the autonomy
ladder's ceiling. Nobody watches a trigger's fire or a scheduled job's run (`cron:`), a subagent
(`subagent:`), a caller from outside the dashboard (`inbound:`), a webhook's turn (`hook:`), a
dispatch with no session (`unattended:`), a Home tile's refresh (`tile:`, its button included: what
its data sources fetch was written into the tile), an app's own work (`app:`: a request its backend
makes with its token, a tool it invokes, an agent it starts), and the other kinds the table lists.
Watched: a chat you are in, a room's members (you approve every action a room takes), the prompt
optimizer and an agent's test chat. A loop's own sessions (`loop-`: its stage worker, a worker per
task and its planner) are judged by the loop's Mode, read from the loop each time a gate asks and
in either form of the key, the bare one and the `dashboard:` one its tools run under: an Unattended
loop's are watched by nobody, so its worker cannot stop or restart PersonalClaw, and an Attended
loop's are watched, since you answer what they ask. A loop whose Mode cannot be read is judged
unattended. A loop's session is still never the person a room asks, nor a chat the Inbox can ask to
try again. A key no row names is a chat's own name, and a chat is watched, so
`tests/test_session_key_census.py` reads the source for every key it mints and fails one whose kind
has no row, or one minted with its prefix spelled out instead of read from its row; it holds the
readers that tell one kind of key from another (where a subagent's report goes, the interface the
security log names, a run's prompt and runtime, a turn's origin, the idle sweep) to the table too. A
media call (an image, a video, speech, a transcription) that names no session is the work it is
made in, for its cap and for its Usage row: speech an app's request asks for is held to the dollar
caps, as the app's other calls are, and so are the speech and transcriptions a client of the
OpenAI-compatible endpoint asks for, which run as that client's work in the spend scope its chat
turns run in (`inbound.spend.spend_scope`).

### What an unattended action is refused (`guardrails/denylist.py`)

Every dispatch that runs an action with nobody answering it asks `enforce_action` before the
action's provider runs: a stored trigger's fire through the gateway (clock, event, file, web
watch, chained, a webhook's fire from an outside caller, a view's refresh), a lifecycle hook, a
workflow run's action step (whoever started the run), a dashboard tile's refresh and the triage
digest's auto-execution. A run anyone but you asks for by name is the automation firing, and
runs through the gateway's dispatch as every fire does (`trigger_runs._run_asked`): a Run now an
agent starts (`automation_run`, or `personalclaw cron trigger` in its shell) in any session, a
chat you are in included, since you answer what the agent runs and not what the automation does;
one an app or another automation's own work asks for; and `personalclaw cron trigger` run by a
script. Only a run you start yourself (Run now, `personalclaw cron trigger` typed at a terminal,
your answer to the question a run stopped on, the restart review's Run now) takes the run-by-hand
dispatch (`trigger_runs._dispatch_store_action`), which you answer, so it does not ask. Which run
is yours is decided once, from what the request proved (`triggers/run_source.py::of_request`),
and every row the run writes keeps it: only yours passes over the automation's hourly cap and its
failure streak. An app's action provider is asked about at the same seams, so it inherits the
check without knowing it exists.

Every path that runs a command with nobody answering it asks the same rules (`check_command`,
`check_action` for a command) before it runs anything: a loop's check and a workflow's verify gate
(`loop.gates.run_verify_command`), a workflow's setup and teardown steps
(`workflows.provisioning.run_step`), an effect's teardown (`workflows.effects.run_teardown`),
and the agent's bash tool under its session, which a session nobody is in (a schedule's, an
Unattended loop's or a subagent's turn, a scheduled script's call, an app's tool call) is held to
and a chat you are in, or an Attended loop's turn, is not.
Each asks them before the shell denylist, the order `check_action` asks its own rules, so a
command both catch (`personalclaw update`) is refused for its effect, in the same words on
every path.

`check_action` refuses, first match wins: every action while the security config cannot
be read; a path that names a credential file; a path outside the ceiling's `paths`; a path the operator's `security.autonomy_denylist` names
(its `needs_human` verdict also notifies you); a command that would stop, restart,
update or reinstall the gateway running it (`guardrails/self_destruct.py`); a command that
would delete your home folder, the filesystem root or the folder it runs in
(`guardrails.denylist.unattended_protected_delete`, below); and a
command the shell denylist refuses. The fourth is classified by the command's effect
rather than its text: it reads through variables, wrappers (`sudo`, `env`, `nohup`,
`sh -c`) and paths to the program that would run, and refuses `personalclaw stop`,
`restart`, `update` and `service install`/`uninstall`, this gateway's service under its
service manager, and a kill aimed at it, leaving `personalclaw status` and a restart of
another service alone. A command it cannot classify that reaches for PersonalClaw or a
lifecycle verb is refused closed. It holds for unattended work only: you stop or update
PersonalClaw from your own shell or Settings → Updates.

A refusal never reaches the provider or a shell. A dispatch's is a `guardrails.denylist` row in
the audit log naming the rule, its reason and the command; a command path's is a
`command_refused` row naming the control (`action_denylist`) and the rule, or for the bash tool
the call's own audit row; each with a warning in the gateway log. Each quotes the path or the
command as it was written. A dispatch that fills each `{{secret:NAME}}` in before asking (a
trigger's fire, whoever asked for it, a workflow step, the bash tool) is judged on
the value it filled in and quoted with the reference left a reference (`check_action`'s
`written`), so a secret's value is in no refusal, record or log. A refusal the work makes itself as
it runs (a bash action whose sandbox cannot start, a workflow check the shell denylist refuses)
quotes the command as it would have run, each value its dispatch filled in masked, as every record
that work writes is (`filled_secrets.handed`). Wherever the work is recorded,
it says the same words, the rule's code and its sentence (`DenyDecision.refusal`): a trigger's
history row (`skipped_gate`, saying what started the fire), a workflow step, which fails
`permission` and is named in the run's ending,
a hook's run, a tile's refresh, a deferral of the triage digest, a loop, which pauses with it as
its question, a workflow gate, which fails with it, and a setup or teardown step.
`tests/test_action_provider_chokepoints.py` fails an execution site that reaches a provider,
under any name, without asking, and `tests/test_every_command_path_asks_the_denylist.py` a
command runner nobody answers that does not.

## Sandbox (`sandbox.py`)

Credential-hiding child-process isolation for tool execution, including an
environment-variable denylist (credential env vars like `SLACK_BOT_TOKEN`
never reach a sandboxed child).

Child **environments** are built by allowlist, not inherited: `build_child_env`
gives the native agent's bash commands, a hook, cron-script or bash-action child, a
loop's check command, a workflow's setup and teardown steps (a
durable step included: it starts under `env -i`, so the tmux server's own environment
never reaches it) and effect teardowns, a runner CLI's version probe, every ACP agent
CLI (plus the variables its app declares for it and the session it answers for), every
git PersonalClaw runs (`net/git.py::git_env`: the state history, the updater, a loop's
worktree, the file browser, the run review and self-QA reads, the doctor's probe, and the
Store reading and cloning an app source, the guarded listing fetch included), and
everything the gateway starts for an app (the pip and npm that install what it declares,
its engine's venv and pip, its setup hooks, backend, worker, sidecar and MCP servers). An
app's provider module runs inside the gateway, so the children IT starts get the same base
only when it asks for it: `personalclaw.sdk.util.child_process_env(extra=None, *,
installer="")` (or `app_packages_env()` for a Python child that imports the app packages),
an SDK addition: `extra` is what that child needs beyond the base, and `installer` names a
package manager whose own settings pass through. A git an app runs asks for
`personalclaw.sdk.git` instead (below). `tests/test_spawn_env_audit.py` classifies every spawn
site by where its child's environment comes from; the ones that keep the gateway's
environment, each with its reason there, are PersonalClaw's own processes, installs and
updates, the owner's terminal and editor, an MCP server of the owner's own config, the
container CLI talking to the owner's daemon, and fixed-argv host tools and probes. Each
child listed above gets a minimal base
(`PATH`, locale, home-equivalents, proxy/CA settings, and the three
`PERSONALCLAW_*` vars) plus whatever names the operator declared in
`sandbox.env_passthrough`. An install also gets its installer's own settings
(`installer="pip"`: `PIP_*`; `installer="npm"`: `npm_config_*`), less the ones
that would move where it lands and npm's `_auth*` login keys. Nothing else from
the gateway environment reaches them, so a credential the gateway holds is not
readable by `printenv`. An inherited value with a login in it (a proxy address,
an index URL) arrives without it: `http://ada:pw@proxy:3128` becomes
`http://proxy:3128`, and the gateway logs once per site which name lost one. A
name the operator declared arrives as it is. The sensitive-prefix list above is
the floor: a declaration cannot pass `AWS_SECRET*`, `AWS_SESSION*`,
`SSH_AUTH_SOCK`, `GNUPGHOME` or `GIT_ASKPASS`. Withheld names are listed in the
debug log at each spawn, so a script that needs one more variable is diagnosable
rather than mysteriously broken.

Every child, built or keeping the gateway's environment, resolves its programs on the
**`PATH` the gateway started with**, and keeps its scratch files in the `TMPDIR` it started with
(`env.gateway_env`, from what the CLI's entry point records before anything else runs), plus what
its spawner adds on purpose (the MCP and agent CLI
install folders `env.augmented_path` names, a server's own `PATH`). Nothing PersonalClaw runs
changes the process's own `PATH`, which every child inherits: a program it needs, such as ffmpeg,
is found by its absolute path and handed to what runs it (`ffmpeg_binary`), and
`tests/test_process_environment_writes_census.py` classifies every write to the process
environment, none of which names `PATH` or `TMPDIR`. A stored secret named after a variable that decides
which programs run (`PATH`, the loader's `LD_*`/`DYLD_*`, `PYTHONPATH`, `NODE_OPTIONS`…:
`env.PROGRAM_RESOLUTION_NAMES`) is stored and resolved through its reference like any other, but
never mirrored into the process environment. A restart (the dashboard's, an applied update's)
starts the new image in place of the old with the environment the gateway was launched with
(`env.launch_env`) plus the sign-in mode it pins, never `os.environ` as it then stands, so nothing
a running gateway changed in its own environment carries over and every restart starts from the
same launch.

**The SSH agent, and PersonalClaw's own git.** The one way past that floor is the SSH agent's
socket, for a child that signs in over ssh with the owner's keys: `build_child_env(ssh_agent=True)`
adds `SSH_AUTH_SOCK` and nothing else. A git command that talks to a remote (`clone`, `fetch`,
`pull`, `push`, `ls-remote`, `remote`, `submodule`) gets it, and so does a program an app starts
with `personalclaw.sdk.util.child_process_env(ssh_agent=True)`, such as rsync to the owner's own
host. `GIT_SSL_CAINFO` and `GIT_SSL_CAPATH` are in every child's base, and the service
install carries them, so a clone behind a proxy that re-signs TLS verifies the server. And
because an agent's shell can write a repository's `.git` as easily as its files, a git that
runs in a repository an agent can write also runs with `net/git.py::git_argv`: settings on
git's command line, after the caller's own options, that stop the repository's configuration
from running a program. No hook runs; nor does a file-system monitor, a password or editor
program, an external diff, a diff driver or textconv filter, the pager, the command that lists
a borrowed repository's refs, a signing or signature-check program, or the hook a garbage
collection asks about old objects. The `ext`, `file` and `git` transports are refused, so a
remote at a local path is too. The repository's ssh command and credential helpers are
replaced: a command that talks to a remote uses the owner's own, from their own git
configuration files, and any other command uses plain `ssh` and none. Those files are the ones
the owner's own git reads: `git_env` keeps `GIT_CONFIG_GLOBAL`, `GIT_CONFIG_SYSTEM` and
`GIT_CONFIG_NOSYSTEM` (`net/git.py::CONFIG_FILE_ENV`), which is also how a test suite keeps the
machine's configuration, and its keychain helper, out of PersonalClaw's git. A git older than 2.12
ignores some of these settings (`protocol.<name>.allow` arrived in 2.12, `core.hooksPath` in
2.9), so `git_argv` refuses it before it runs: `GitTooOld`, an `OSError` whose message names
the version needed, the one found and what to do, and the doctor's git row says the same. What
no setting given there can reach is in
[limitations §12](../security/limitations.md#12-a-git-driver-a-repository-assigns-to-its-own-files-still-runs).
A clone into a directory PersonalClaw has just made (the Store's) runs without these settings:
nothing an agent wrote can be in its configuration, and a Store source may be a local path.
`tests/test_personalclaw_git_is_neutral.py` plants each of those programs in a real
repository and holds every git spawn in the tree to `git_argv`. An app's provider that runs
git gets the same two helpers from `personalclaw.sdk.git`: `git_argv(args)` and
`git_env(remote=False)`, with the same refusal (`GitTooOld`), and `git_problem()` for a doctor
or setup step that says so before anything runs. A token an app keeps for an https remote, in
a sensitive setting and so in the credential store, signs in with `git_argv(args, token=True)`
and `git_env(remote=True, token=…, username=…)`: the command's one credential helper answers
git with the token from that command's environment, and none of the owner's helpers runs. So
the token is on no command line, in no repository's configuration and in no helper's store
(a keychain entry for the host can neither answer in its place nor be replaced by it), and a
value with a line break or a NUL, which git would read as more than one answer, is refused.
`tests/test_a_git_token_signs_in_and_is_kept_nowhere.py` drives it against a repository
served through `git http-backend` behind Basic auth.

### What the sandbox does and does not do

This is a **credential-hiding sandbox, not a confinement sandbox** — a precise distinction
that the rest of this section, and any public claim, must respect. The macOS Seatbelt profile
is allow-by-default (`(version 1)\n(allow default)`) with targeted `deny file-read*` rules over
credential paths (`~/.aws`, `~/.gnupg`, `~/.config/gcloud`, `~/.azure`, `~/.docker`, `~/.kube`,
`.npmrc`, `.pypirc`, `.netrc`, `.git-credentials`, and the credential store's `.env` in the home in
use and the default one, plus `~/.ssh` in `strict`; the single files only in `cc` and `strict`,
`_hidden_files`); the Linux path is equivalent (bind-mount empty dirs over those paths). It raises the
cost of credential theft. The places it confines writes are the home's owner-only paths and, on
macOS, the owner's own git settings and shell startup files (below); it does not stop an agent from
doing anything else.

| It **does** | It does **not** |
|---|---|
| Hide credential dirs/files from the agent child (macOS Seatbelt deny-reads; Linux bind-mounts) | Confine filesystem **writes** (except `~/.ssh` on macOS `strict`, the home's owner-only paths, and on macOS the owner's own git settings and shell startup files) |
| Refuse writes to the home's owner-only paths at every level (macOS deny-writes; Linux holds the home's entries — below), and on macOS to the owner's own git settings and shell startup files | Fence a repository's git settings and hook scripts (screened instead — below) |
| Scrub credential env vars from the child, every mode | Restrict **network / egress** from the child |
| Deny `~/.ssh` writes (macOS `strict` only) | Limit processes, CPU, or memory (no rlimits) |
| Path-allowlist a subagent's cwd (advisory — the prompt tells the agent its scope) | Provide a filesystem **jail** or a real execution boundary |
| Enforce app `api`/`storage`/`memory`/`cron` permissions server-side | Enforce the app `network` permission (declaration-only — see `docs/security/limitations.md`) |

The honest, complete statement of limitations lives in
[`../security/threat-model.md`](../security/threat-model.md) and
[`../security/limitations.md`](../security/limitations.md); this section is the architectural
summary, not a substitute for them.

### What runs as the owner is owner-only (`owner_only.py`)

Five places in the home hold what runs as the owner and what they allowed: `config.json` (among
much else, the agent CLI's own hooks, `agent.agent_hooks`), `mcp.json` (the MCP servers PersonalClaw
starts, each a command it runs as the owner), `hooks/` (the scripts those hooks import), `agents/`
(the agent CLI's config and every agent definition) and `grants/` (the owner's yes to what an agent
wrote, `owner_grants.py`). An agent writes none of them, at three layers that each read
`owner_only`:

- **The fence**: the sandbox around the agent's shell denies the write. On macOS a Seatbelt
  `deny file-write*` names each path at every level. On Linux the fence is the home's own entries,
  not whichever files are there when the shell starts: the home is bound onto itself read-only, and
  every entry already in it except these is bound back writable. Each read-only remount keeps the
  nosuid, nodev and noexec its mount has, which a user namespace does not let it drop, so a home
  mounted with them is fenced like any other (`_kept_flags` in the namespace launcher). A bind on
  one file holds that inode — it cannot hold a name that does not exist yet, and the kernel
  dissolves it when the file
  is replaced, which every config save does — so an owner-only name is refused whether it exists or
  not, and however often it is replaced. The price, on Linux only: the shell cannot add, remove or
  rename an entry at the top of the home, and a top-level file PersonalClaw replaces while the shell
  runs is read-only in that shell until it restarts. On both, the home and every folder above it
  the owner could rename are pinned (a Seatbelt literal on the folder's own entry; a mount point on
  Linux), so the home cannot be moved aside, edited there and moved back. This is the kernel
  refusing, so it holds however the command spells the path.
- **The screen**: `HookManager.on_tool_call`, which every approval path consults before a card,
  an auto-approve pattern or an unattended default, and the native `bash` tool refuse a call that
  names one, with the reason. Defence in depth — a command can build the path out of pieces no
  reading of its text sees.
- **The roots**: no file root reaches into the home except a root that is itself inside it
  (`file_roots.within`), so the file explorer, file-backed artifacts, apps and the native file
  tools never name them however a workspace or a loop is bound.

Git's own settings and hook scripts, and the owner's shell startup files, are owner-only too: her
own git and her own shell run what they hold, outside any sandbox.

- **Git's.** In a repository's git folder (one named `.git`, a submodule's and a linked worktree's
  folders inside it, or any folder git takes for one, as it takes a bare repository: it holds `HEAD`
  and `objects`, or `HEAD` and `commondir`): its settings `config`, a linked worktree's
  `config.worktree`, `commondir` (where a linked worktree's settings and hooks are), the `hooks/`
  folder, and the steps of a rebase in progress (`rebase-merge/git-rebase-todo`). In a working tree:
  `.gitmodules`, and the `.git` file a linked worktree or a submodule keeps in place of the folder.
  And the owner's own git settings, `~/.gitconfig` and `git/config` in `$XDG_CONFIG_HOME`, and the
  machine's (an `etc/gitconfig`). Names are compared regardless of case, as a case-insensitive disk
  opens them. The rest of a repository stays the agent's: `.gitignore`, `.gitattributes`,
  `.git/info/exclude` and every file of the project.
- **The shell's.** The startup files of sh, bash, zsh, ksh and the csh family in her home folder
  (`.profile`, `.bashrc`, `.bash_profile`, `.bash_login`, `.bash_logout`, `.bash_aliases`,
  `.zshenv`, `.zprofile`, `.zshrc`, `.zlogin`, `.zlogout`, `.kshrc`, `.mkshrc`, `.cshrc`, `.tcshrc`,
  `.login`, `.logout`), zsh's in `$ZDOTDIR` as well, and fish's `config.fish`, `conf.d/` and
  `functions/` in `$XDG_CONFIG_HOME/fish`.

Every path an agent writes by refuses a change to one, before anyone is asked and whatever the
chat's Trust, YOLO or a standing grant would answer, in the words the home's refusal uses: the
native file tools wherever a place reaches one (`file_scope.FileScope.resolve`: a repository in the
workspace, the owner's home folder when an allowed working directory covers it); an agent CLI's own
write, edit and patch where it asks (`acp.permission_authority.screen_tool_call` reads the files the
call's input names, a patch's changes included); a file-backed artifact, which never points at one
(`artifacts/source_files.py`), so no save of it writes one; an automation's files to change
(`write_scope.problem`); and the agent's shell, whose screen refuses a command that names one and
does more than read it, and a `git config` that sets, unsets or edits a setting at any scope
(`command_effects` reads which settings it writes). Reading them is untouched, and so is running a
startup file in the agent's own shell (`source ~/.zshrc`).

The fence holds less of them. The macOS profile denies writes to the owner's own git settings and
shell startup files that sit in her home folder, at every level, in both spellings of a link into a
dotfiles folder, and pins that folder so it cannot be moved aside; the ones deeper in `~/.config`
are held by the screen alone, since holding them would mean refusing to make `~/.config` where a
program's first run needs it. The Linux launcher holds none of the owner's own files: it could
only by making her whole home folder read-only. And neither platform fences a repository's: git
writes those itself in the agent's ordinary work (`git init`, `git clone`, `git remote add`,
`git push -u`), and a kernel rule cannot tell that from a planted setting.
[Limitations §22](../security/limitations.md#22-what-git-and-your-shell-run-as-you-refused-on-every-write-path-fenced-only-in-part)
says what that leaves.

The owner is untouched: their own editor, and the gateway writing for the owner's surfaces. And
because a fence is not consent, the agent CLI's hooks also run only once the owner allowed them
(`agent_hook_grants.py`): a hook the owner has not allowed, or whose file changed since, is left
out of the agent CLI's config and listed on the Agents page with Allow. What the CLI runs is a copy
of the file as the owner allowed it (`<home>/hooks/.allowed/<seal>`), not the file, so an edit to
it — a script outside the home is no owner-only path — never runs on the old yes.

An MCP server runs only once the owner allowed what it runs, too (`mcp_grants.py`), sealed to its
definition: how it is reached, its command, arguments and folder, the names of the variables it
sets, and a remote server's address and header names. Values are not in the seal, since nobody is
shown them. The Tools page's Add and Edit and the MCP Tool Servers card ask before they save, with
exactly what will run; every other way a definition arrives (Import from another tool, bringing a
setup over, a pack's connector, an app's manifest, a restore, a hand edit) writes one that waits,
listed on the Tools page with Allow, which asks the same question first. The probe
(`mcp_discovery.probe_server`) and the agents' connections (`mcp_client._personalclaw_mcp_specs`)
both ask before they start anything. PersonalClaw's own server is defined by its code
(`agent._MANAGED_MCP_SERVERS`), never read from `mcp.json`.

### Where the file tools reach (`file_scope`)

The native file tools (`read_file`, `write_file`, `edit_file`, `list_dir`, `glob`, `grep`,
`repo_map`) and `code_map` reach one scope, defined once in `file_scope.FileScope` and read again
at every call:

- **the workspace**: the session's folder and the extra roots a loop's worker is given. Read and
  change.
- **the folders a workflow step reads** beside the one it works in: the tree its run's project is
  bound to, and the folder its batch was started in when a spawn may work there
  (`provisioning.step_reads`, from the run's record). Read only, so a step in an isolated worktree
  or scratch folder cannot change the original through them; what it may change stays its own
  folder's, and its tier's.
- **the allowed working directories** (Settings → Agent defaults, `agent.subagent_cwd_allowed_roots`).
  Read and change; a change meets the same approval, with the same diff, as one in the workspace,
  and a turn's rewind restores it (the folder is recorded with the turn's checkpoint).
- **the folders added as knowledge sources** (a Watched Directory in Knowledge → Sources, while it
  is on). Read only, and only the files that source takes in (`dir_source.resolve_in` and
  `dir_source.takes`, the rule its own scan uses: inside the folder once links are resolved, its
  file patterns, nothing hidden, nothing below the top when it is not recursive). The scan leaves
  out a link to a file or folder outside the folder and says how many on the source's row, so a
  link cannot carry content she did not share into the library either.
  `knowledge_search` and `knowledge_get` name a note's file, so the agent can open it.
- **each installed skill's own folder** in the skills library (`skills/` in the home, where every
  install, import and bundled skill lands). Read only, and only what a skill ships beside its
  instructions: a folder below the library's top that holds a `SKILL.md`, as the loader finds a
  skill, once links are resolved, so a link or a skill folder that leads out of the library reaches
  nothing; nothing hidden, so the library's own records (use counts, refinements, proposals) and an
  install's lock file stay out. A skill's `SKILL.md` is not among its files: `skill_invoke` loads
  the instructions, the one place a body is read (`SkillsLoader.load_skill`, which applies accepted
  refinements and the bench's suppression), and counts the use; the file tools point there. So a
  skill that names a file of its own ("use template.md") is followed as written, and `skill_invoke`
  names the folder a skill's files are in. A skill found in the folder other AI tools share is not
  in the library and is not reached.

Everywhere else is refused, read or change, with a sentence that says which folders the tools
reach and where the owner adds one. An entry naming the filesystem root or a system folder is not a
place, and neither is a source its own provider would refuse to poll. A folder taken out of the
setting, or a source paused or pointed elsewhere, is out of reach at the next call.
`file_scope.refusal` answers from a call's arguments, the session's folder and the settings alone,
and the file tools' pre-flight asks it (`ToolProvider.preflight`), so a call it refuses is answered
with its reason and hint before anyone is asked to approve it. Each turn the native loop tells the
model which folders it reaches beyond the workspace (the `[file places]` note), and when it has the
knowledge tools, to search the library first. The skills library is left out of that note, since
it is in every home: a skill's folder is named where the skill is loaded.

The calendars in those folders are part of the same scope, not a new one. When the session has
`calendar_events` (the bundled Calendar Tools app), the note also names each calendar file in the
allowed working directories and the knowledge sources, by its name (`X-WR-CALNAME`, else the file
name), its kind and its `~` path, and sends a question about plans to that tool
(`calendar_files.find`: a few folders deep, a bounded number of entries per folder, hidden and
dependency folders left out, and only a file a read there may open, `FileScope.admits`). The tool
reads those files, or one `.ics` file it names that `FileScope.resolve` admits, and nothing else;
it only reads, so it asks nobody. It answers with the events between two days in the user's time
zone (the zone the request's date line is written in): a repeating event expanded at its own
wall-clock time across a clock change, its exceptions and moved repeats applied, and an all-day
event shown through its last day, never the exclusive end the file stores. A repeat rule it does
not expand is said, with the event's first start listed.

Inside every place the check the Files view and `/api/file-read` make still holds
(`file_roots.Admission`): symlinks and `..` resolved, so a link or a climb out of a place reaches
nothing; the PersonalClaw home reached only through a place inside it (`file_roots.within`), and a
place that contains the home (a worker in `~`) says nothing about a path in it, so the skills
library's rule is all a session working there reads of the library; no
protected credential location (`~/.ssh`, `~/.aws`, the keychain, the home's own `.env`, `auth/`,
`governance/`); no PersonalClaw key, `.env`, `sessions.json`, `session_key`, `*.key`, `*.pem`
or `*.secret` file, nor any alias of one; and no change to what git or the owner's shell runs as
her (`owner_only`: a repository's `.git/config` and `.git/hooks/`, its `.gitmodules`, her
`~/.gitconfig`, her `~/.zshrc` and the rest above). A path that starts with `~/` names the
owner's home, as the owner writes it, and meets every one of these checks as any absolute path
does (`~name` stays a plain name). The home is the one the gateway runs with (`HOME`, as its shell reads `~`), and a
path the tools show in it is written from `~` (`home_paths.from_home`: a search hit, a place in the
`[file places]` note, a note's file), as is every path in the home the request names; a path
outside the home is shown in full. The request says once what `~` is, and asks the agent to name a
file in the home from `~`: spelling a long home out, it cut the middle and named a folder that was
not the owner's. A `glob` or `grep` pattern is relative to the folder searched (the workspace, or
the call's `path`); one that is absolute, starts at `~` or climbs with `..` is refused as a whole,
and every match is checked one by one, so a listing, a search or a map leaves out what the tools
could not open. `code_map` indexes the workspace, an allowed working directory or a folder inside
one, never a knowledge source's folder, whose other files the tools do not read; its index skips
the same files (`codegraph.CodeGraphIndex`).

A file the agent names in a chat opens where the owner reads the chat: the Files view's read
surfaces (`/api/file-read`, `/api/file-raw`, `/api/file-watch`) also admit what the agent's file
tools may read (`files._agent_readable_path`, the same scope), for the owner's own requests and
never an app's, and never for a write. A `~/` mention opens as written, the gateway reading `~` as
its home. A mention that names a note by its file alone resolves to the one watched-folder note
with that file (`dir_source.note_file`); a name two notes share resolves to neither.

PersonalClaw's own stores inside the workspace (every state-inventory entry inside its `workspace`
entry, and every partition an entry declares there: the knowledge library's database and stored
documents, the lexicon, and each working folder's memory database and learning log under `_ext`)
are not files to the agent. The file tools refuse them and leave them out of every listing, the
tools that read a file past them read none of them (`file_scope.held_from_reads`: an artifact's
`content_file`, the file `notify_attachment` sends, an artifact that shows a file), and the
tool-call screen (`hooks.HookManager.on_tool_call`) refuses a shell command that names one before
any approval, with the tool to use instead (`file_scope.store_named_in`): a raw read hands the model
pages of a database past the masking its own tools apply, and a write breaks the store.

### A tool reads only when it declares so (`task_modes.py`)

Every posture that runs a read without asking — Ask and Plan mode (and `personalclaw run` without
`--allow`, which is Ask mode), every approval mode, `--approval reads`, a dry run (which executes
reads for real) and the `read` tool grant of a research step, subagent or room critic — asks one
question:
does this call only read? The answer comes from what the tool DECLARES, never from its name, and
from nothing else except a shell command's own text:

- **The declaration is `RiskLevel.SAFE`** (`tool_providers/base.py`): "a call changes nothing but
  the record that it was read". An in-process tool states it in its MCP-shaped dict
  (`annotations.readOnlyHint`, true or false — `tests/test_research_class_tool_census.py` requires
  every shipped tool to say one or the other); a `ToolDefinition` that states nothing is
  `CAUTION`; an app's route reads only with `"readOnly": true` in its manifest (a `DELETE` is
  destructive whatever it says).
- **A declared read asks nobody**, in every mode, so a chat does not stop for it and the postures
  with nobody to ask (`personalclaw run`, a dry run, an unattended agent) run it instead of
  declining it. A `ToolDefinition` that declares `SAFE` has `requires_approval` false whoever built
  it — PersonalClaw's own tools, an app's routes and tools, a trusted MCP server's reads — and
  `tests/test_a_declared_read_asks_nobody.py` holds every definition core writes to saying so
  itself. Over an agent CLI, which asks the host about every call, the host answers a declared read
  itself (`approval_grants.DECLARED_READ`) at each place that would otherwise ask a person: the
  chat's gate, a background agent's and a room member's, each past the refusals that come first.
  The declaration decides there, never the effective risk, so a read-only shell command is still
  Trust reads' to approve. **Nor does a call whose work asks for itself** (`_meta`
  `WORK_ASKS_META_KEY`, read only from PersonalClaw's own tools): `subagent_run` starts what asks
  her, one task's start or a batch's one ask naming every task, so the call is not asked about
  first (`requires_approval` false on PersonalClaw's runtime; over an agent CLI the host answers it,
  `approval_grants.WORK_ASKS`). It is no read: Ask mode, Trust reads, a dry run and a `read` grant
  still treat it as the change it is, and the operator ceiling bounds the grants that start its
  work, never the call. No tool switches between reading and changing on an argument: a listing
  is its own tool (`triage_rules_list`) and so is each preview — `workflow_check` (a spec
  `workflow_author` would save), `workflow_edit_preview`, `workflow_audit` (beside
  `workflow_repair`) and `automation_dry_run` — and a call that sends a preview argument to the
  change is refused.
- **Trust reads** approves what a declared read does not already cover: a read-only shell command.
- **An approval's `is_read_only` is a yes or a no** (`task_modes.reads_only`): whether the call is
  established as a read, by its declaration or its screened command. Anything else is the change it
  may be.
- **A tool that declares nothing is a change**, so it asks: an ACP CLI's own tools, and an
  external MCP server's tools unless the owner trusts that server's labels. An MCP server may label
  anything read-only, so `readOnlyHint` counts only for a server whose labels the owner trusts, and
  only for its tools as they were when she trusted them (`mcp_read_only_trust`). The trust keeps a
  digest of each tool's name, description, input schema and labels (the canonical form is in the
  module), and the approval gate's one read of it, `mcp_client.declared_risk` through `believes`,
  compares the tool's digest: a tool the server adds or redefines later asks like an untrusted
  server's until she reviews it on the Tools page, whose card says what was added, changed and
  removed, and whose Review seals the tools again as shown. A running chat judges its tools again at
  its next turn when the trust is written or a server lists its tools differently. The trust is per
  server, given only on the Tools page (which asks first, naming the tools it lets run unasked), and
  kept in the owner-only `grants/mcp_read_only.json`;
  `tests/test_mcp_read_only_trust_has_one_read.py` fails on any read of it that skips the digest.
  A server whose labels she does not trust that changes a tool's description raises a quiet
  notice, since a description is text the model reads. Its `destructiveHint` counts from anyone,
  because it only adds a question.
  Every approval shows a server's labels as its word (`ToolDefinition.annotations`): an untrusted
  read-only label reads "Server says it only reads" and still asks, a destructive label "Writes
  files", an open-world label "Uses the network". A tool's name is read word by word
  (`list_commits` is not a `commit`), and a server's read-only label outweighs a write word in it.
- **A shell call's command decides** (`command_effects`, a per-program allowlist of read-only
  forms): for a declared call only the platform's `bash` — a name the registry reserves — is a
  shell; for an ACP CLI's call, one it reports as `execute`, a shell tool's name, or a `Running: `
  title. A command is a read only when every program in it runs in a form known to only read; an
  unknown program, option, subcommand or piece of shell syntax is not one. Its risk is the
  command's, whatever the shell declares (`task_modes.read_call`): `safe` for a read, `destructive`
  for an established delete, `caution` for an established write or network call, and `unchecked`
  ("Not checked") for anything the screen cannot vouch for, which the Tools page's confirmation and
  the card's withheld standing grants treat as they treat `destructive`. The same reading gives
  the facets every approval surface shows (`approval_brief.call_blast_radius`), carried with the
  approval as `blast_radius`.
- **Two more declarations ride beside the level**: `builds` (a Build-mode deliverable's producer,
  which Build mode runs as long as it is not destructive) and `proposes` (its only effect is a
  proposal the owner reviews: not a read — Ask mode and Trust reads treat it as the change it is —
  but a research step's `read` grant admits it, because filing for review is what those steps do).
- **Over ACP**, a call to PersonalClaw's own `personalclaw-core` tools carries that tool's
  declaration (`acp.mcp_servers.core_tool_declaration`), matched by the exact name in the shape
  each CLI sends it and only where the call cannot be something else wearing the name: its
  arguments must be ones the tool takes, and on kiro-cli, whose shell calls share the title shape,
  only a destructive declaration is taken. An ACP kind never admits a call; it only decides whether
  an ungated one stops the turn (`REPORTED_READ_KINDS`).
- **A `read` grant is shown only what it can run.** A research subagent on PersonalClaw's own loop
  is offered exactly the tools some call to which the grant admits (`guardrails.policy.
  offer_refusal`): declared reads, proposals, the platform `bash` (each command screened as above),
  a notice to the owner for an automation's agent, and a file write into the files an automation
  was given. A call to any other tool, by a name the model has from elsewhere, is refused before
  any approval as outside its read-only tools, and a shell command that does more than read is
  refused the same way (`granted_call_refusal`). A room member's `read` tier is stricter: it is
  not shown the shell (`rooms.posture.member_tool_refusal`), and its instructions say its tools
  are read-only and what that tier may not do, on every turn (`rooms.posture.member_reach`), so it
  never offers a change it cannot make. A room member's call that asks is put to you through the
  approval registry, as a chat's is, and nothing else answers it
  (`rooms.posture.registry_approver`, [inbox-channels.md](inbox-channels.md)). A subagent whose
  every call was refused did nothing it was asked, so it ends not done, naming the tools and why
  (`subagent_tier.refused_every_call`), and the workflow step it ran for fails as a refusal.
  A subagent on an agent CLI is held to the same tier: its session hands the tier to the
  `personalclaw-core` tool server (`mcp_shared.TOOL_TIER_KEY`, declared in the server's own
  environment, `acp.mcp_servers.core_mcp_servers`), which lists only the tools the tier offers
  (`mcp_shared.offered_tools`) and refuses a call to any other in the same words
  (`tier_call_denial`). Before, such a subagent was listed every core tool. A read-only run is
  told which servers' reads it was not shown and why: a server whose read-only labels the owner
  has not trusted, named, with where to trust them, and a trusted server's tools that are new or
  changed since, with where she reviews them (`policy.unshown_reads_note`).
- **An agent CLI is told to ask on every turn, unattended work included**
  (`acp.permission_authority.sanitize_mode`). The host forwards the CLI's asking mode (`default`,
  or the CLI's own most restrictive spelling of it), never one that lets it approve its own calls,
  so each call it asks about meets the task mode, the deny-list and the owner-only screen
  (`screen_tool_call`), the hooks and, in work nobody watches, the run's bounds, before Trust, YOLO
  or a run's standing grant can approve it. In work nobody watches a call nothing approves is
  refused at once, with its reason (`chat_refusals.refuse_unattended`), not left waiting on a
  person who is not there. The one exception is the owner's choice for one Unattended loop whose
  agent CLI's unasked calls PersonalClaw declares (`agent_cli_self_approval`: the loop's page, off
  by default, turned on only with her consent, audited as `loop.agent_cli_self_approval` either
  way and read at each turn): while the loop's own grant stands, and under the operator ceiling,
  its CLI is told its self-approving mode, audited as `mode_change:self_approval_allowed`.
- **A call an ACP CLI ran without asking is said on its own card** (`dashboard/ungated_calls.py`).
  The CLI decides which of its calls ask first, so one its own settings allow runs with no approval
  request and the host learns of it when its result lands. Its card says so, live and after a reload
  ("Ran without asking you — allowed by Claude Code's own settings.", or "… never asks about this
  tool." for an accepted residual, `acp.permission_authority.ungated_call_note`), and so does the
  chat's export. The folded work counts such steps, the audit
  row reads `ungated`, and one log line names the runtime and the tool (WARNING; INFO for an
  accepted residual), never the call's arguments. Every host that runs an agent CLI judges and
  audits such a call the same way (`acp/ungated.py`): a room member's is said on the room's
  transcript ("talk-editor ran Terminal without asking you — …") and its row names the member, and
  a background turn's (the heartbeat, a subagent's result in its chat) is audited `ungated` too. A
  call that may have changed something stops the turn under a posture that allows no change: a
  chat in Ask or Plan mode, or a room member whose tier does not cover it.
- **A gateway log line that names a tool call writes its title masked** (`audit_subject.log_title`).
  An agent CLI titles a shell call with its command, so a line that named a call by its raw title
  (a permission asked or refused, a call refused with its batch or on an unattended run, a source's
  grant, a command that is more than a pattern names, a call that ran without asking or keeps
  failing) wrote whatever the command carried, and all of it. Each writes the title as the audit
  log writes a call's command (`audit_text`): masked, on one line, cut at 160 characters with a
  marker saying how much it left out. The log sinks' own mask (`MaskingFormatter`) is the floor
  under every record; `tests/test_a_call_the_log_names_is_written_masked.py` fails a log line that
  hands an event's title or input over as it came.

### An agent's tool list (`agents/tool_list.py`)

An agent's `tools` setting is the least-privilege list its turns are held to on PersonalClaw's own
runtime. It is read once, where the runtime is built (`provider_bridge._build_native_runtime`), for
the agent the turn runs as (the default agent for a chat that names none), and held by the runtime
itself, so every path that builds a native turn is held to it: a chat, a subagent, a loop's
worker, an automation, a workflow step, a heartbeat task and the OpenAI-compatible endpoint. A tool
the list does not allow is left out of the tool block, the deferred catalog, `tool_search`,
`tool_schema` and the dispatch index at every catalog build, and a call that names one anyway is
refused first, ahead of the dry run, the deny list, the task mode, the grants, the hooks and any
approval, with the refusal a policy block is answered with (`refused_by: agent_tools`, audited
`denied`). An empty list is every tool, `tool_result_get` is kept on every list, and a list that
cannot be read allows nothing else, as none can while `config.json` itself cannot be read (the
loader's discard, `config_discard`): its defaults know nothing of the lists set in it. The list only
narrows: a tool switched off on the Tools page, a subagent's tier and the operator ceiling's `tools`
scope still hold beside it. A built-in agent's list is PersonalClaw's own, and its refusals say so
rather than point at the Agents page. The template refiner's
(`agents.defaults.TEMPLATE_REFINER_TOOLS`: its evidence tool and its proposal tool) is one of the
two holds its propose-only design rests on, beside the research class of the workflow stages that
run it. An agent CLI runs its own tools where no list of PersonalClaw's can hold them, so the list
does not apply to one, and the Agents page says so on such an agent. The rules an entry follows are in
[configuration](../reference/configuration.md#an-agents-tool-list-agentstools);
`tests/test_every_turn_path_holds_an_agent_to_its_tool_list.py` drives each path.

The list is also a security control on the write path (`SecurityControl` in the agent write table,
`dashboard/handlers/agents.py`): a save that widens it, by an entry the stored list does not cover or
by emptying it, which is every tool, needs the owner's `confirm: true`, asked in the consent dialog
(`400 confirmation_required` otherwise), and a save that only narrows it needs none. The direction
is the list's own matcher's (`tool_list.widens`), so the first tool written to an empty list
narrows it. The agent CLI's runtime file lists tool servers, where no entry is none, so there a list
widens only by gaining one. `tests/test_widening_an_agents_tool_list_asks_first.py` drives both.

## Governance ceiling (`guardrails/ceiling.py`)

Two levels, one rule — **tightest wins**. Level 1 is the operator's `Ceiling`,
read ONCE at boot; level 2 is the run's `SafetyProfile`
(`guardrails/policy.py`), which may only **narrow**. Effective posture =
`resolve(ceiling, profile)`, composed inside `profile_for_session` — the single
object every dispatch seam already consults (rung routing, the action denylist,
the tool-approval pick, spawn, egress), so there is no seam that reads a profile
the ceiling did not bound.

- **Where it lives.** `$PERSONALCLAW_HOME/governance/ceiling.json`, or an
  absolute path in `PERSONALCLAW_CEILING_FILE`. Schema:
  `{"version": 1, "scopes": {...}}` over six governed scopes — `approval`,
  `scan`, `egress` (ordinal), `paths` (ruleset), `tools` (capability gate),
  `budget` (scoped map).
- **Four archetypes, one compose function each** (`compose_ordinal`,
  `compose_ruleset`, `compose_gate`, `compose_map`). The evaluator dispatches on
  **archetype, never on scope name**, so adding a governed scope is one
  `ScopeSpec` row of data.
- **Enforcer-owned registries** (`guardrails/registries.py`): matchers and
  ordinal scales live in code and are never sourced from the governed file — a
  rule that could name its own matcher or reorder a scale could widen itself
  while reading as narrower. An unknown matcher/scale/scope/value, a corrupt or
  unreadable file, or a scope naming an unknown archetype **aborts governance
  boot** with WHAT/WHY/FIX. Fail-closed: "governance could not be established"
  is a stop, not a degraded mode.
- **Every standing grant asks it** (`approval_grants.stands`). A grant approves a call or a spawn
  without asking anyone: the chat's Trust, YOLO and Trust reads, an agent's "Always allow", a
  spawn's own `approval_mode: "auto"`, the owner's Approval mode "Auto", the hook settings and
  patterns, a listed source, the `--approval` flag, a remembered or policy-approved workflow gate,
  a workflow step's start its owner allowed before a restart cut the step off (for the same
  request, within the step's time limit, `approval_grants.APPROVED_BEFORE_RESUME`), a subagent
  batch's tasks starting on what allowed that batch's start (`approval_grants.BATCH_ALLOWED`: her
  answer to its one ask, or, for a batch that only reads, the grant that starts its chat's
  subagents; an app's batch never starts on a grant of hers, `apps.app_work`),
  the triage digest's auto-execution, a heartbeat task's calls on the task's own Allow
  (`approval_grants.HEARTBEAT_TASK`), a subagent's announce turn, an app's conversation, the
  agent CLI of an Unattended loop its owner let approve its own calls, a session policy that never
  asks, and the eval
  runner's allowlist of read-only tools. A channel app that runs a conversation itself (Slack's
  threads) approves a call only on core's answer (`chat_trust.chat_grant`): the decision a chat
  makes for a call put to its gate (an operator's hook pattern, what the call's tool declares,
  the chat's Trust, Trust reads and YOLO), with each grant held to the allowed hosts and to this
  ceiling (`approval_grants.stands_for_call`). It keeps no pattern, setting or approval mode of
  its own that approves a call, refuses first a call the deny-list refuses (`screen_tool_call`),
  then one the operator's blocking hooks refuse (`ask_pre_tool_hooks`), and one nobody approves
  is asked on its own prompt. Under
  `{"approval": {"value": "ask"}}` none of them stands, and each refusal is audited
  (`approval.grant_refused`, naming the grant). A switch the owner presses (the chat's mode
  pill, a card's wider scope) is refused with `409 approval_grant_refused`, whose message names
  the file and says a restart applies a change to it. An operator's hook pattern is checked at
  the `hook_based` level, which a `hook_based` ceiling still permits. A grant also no longer makes
  a run headless: an agent whose grant the ceiling refused asks you for each call, through the
  same relay as any other.
- **An unattended run is approved by its own consent, never by a default.** Settings → Agent
  defaults → Approval mode ships as `interactive`, the strictest end of its scale, so an agent no
  chat started (a trigger's Invoke Agent agent whose step does not set its own approval, a
  subagent started outside a chat) asks in the Inbox before each call that needs approval. What
  approves one without asking is the consent given for that run: the step's own `approval_mode`,
  saved with the owner's yes; a Run prompt action, whose Allow says what its agent may do; a
  workflow run's own unattended grant; the Mode its loop was started under; a heartbeat task's
  Allow. A background run under the `hook_based` policy with nobody to ask (a subagent's report in
  a chat nobody watches) approves only what an operator's pattern names and what asks nobody
  anywhere (a declared read, a start that asks you itself), and refuses the rest with its reason:
  nothing approves a call because no hook named it. An app's scheduled
  job is none of these: its agent runs at the app's agent tier as the app's work, and asks for
  each call that needs approval (`apps/app_crons.start_job`). The setting reads as a grant in one place
  (`approval_grants.setting_grant`, held there by `tests/test_approval_setting_one_reader_rail.py`),
  and only once the owner chooses "Auto", whose loosening asks consent in words that say what it
  does. A stored "Auto" from before the mode shipped asking cannot be told from a chosen one, so it
  is kept, and the Doctor's `security.approval_mode` row and the Settings row name it. A gateway
  with nowhere to ask (no dashboard, no channel) refuses such a call rather than approving it
  (`approval_grants.NO_SURFACE` names the refusal).
- **Starting an agent without asking approves none of its calls.** Starting a subagent and
  approving what it does are two decisions. The hook setting that starts subagents without
  asking (`hooks.auto_approve_subagent_spawn`) decides the start alone
  (`SubagentManager._start_grant`), as a trigger's Allow decides its agent's start. The agent it
  starts has its calls decided as any subagent's are (`SubagentManager._standing_grant`), never
  by that setting: an Invoke Agent step's agent asks before each call unless the step approves
  its own calls, the owner's Approval mode "Auto" does, or the hook setting for subagents' tool
  calls (`hooks.auto_approve_subagent_tools`) does, and the step's Allow names the one that will
  (`automation_posture.AgentRunPolicy.approved_by`). The operator ceiling bounds every one of
  them.
- **What a workflow's step may do is the owner's yes, at every door that writes one.** A step that
  approves its own tool calls (`approval_mode: "auto"`) or may change things (`capability:
  "mutating"`) does so each time its workflow runs, so every write of a definition goes through
  one writer that screens it (`workflows.service._write_definition`, held there by
  `tests/test_workflow_definition_writer_census.py`). A step that would do more than the same step
  does now (matched by its node id: a loosened key, or an allowed key on a step that no longer runs
  as it ran) is written only with her yes. Her editor's save asks it as the consent question; an
  agent's `workflow_author` hands the save to the gateway (`POST /api/workflows/agent-saves`),
  which asks her once through the approval registry (`workflows.owner_allow`: the chat's card, the
  Inbox, the phone, a channel; no standing grant, Trust or YOLO answers it), saves on her Allow
  exactly what she was shown, saves nothing on a Deny or no answer, and refuses a session that acts
  on its own. An accepted refiner diff that would let a step do more is not applied, and a pack's
  template arrives with every step asking before it acts and only reading, as another machine's
  definition does. An edit of a running workflow is held to the same rule
  (`workflows.mid_flight.posture_refusal`): an agent's is refused, and the owner's asks her first.
- **A spawn's approval mode is the owner's to set.** `POST /api/spawn` refuses a body that names an
  `approval_mode` (`400 approval_mode_not_accepted`), whoever sends it, the internal credential
  included, so a subagent started over HTTP asks as her own settings say. What lets a subagent
  approve its own calls is consent given for that run and passed in-process: a workflow step's
  saved posture, a trigger's step. No app's agent work is given it.
- **The `tools` scope and a spawn's capability class hold under a grant.** Both are enforced where
  the host is asked about a call, and a runtime that answers its own asks (the native one, while
  a grant stands) asked about none: a read-only research run's write tools ran. The native
  runtime now asks the spawn's tool grants before its own approval
  (`NativeAgentRuntime.set_tool_grants`), and the agent CLI of an Unattended loop its owner let
  approve its own calls does so only while the ceiling leaves the tools unrestricted. What that
  leaves for an ACP CLI is in
  [limitations §1](../security/limitations.md#1-an-agent-cli-is-held-to-the-rails-only-for-the-calls-it-asks-about).
- **A workflow stage is a leaf to its own tools on every runtime.** A stage runs with its lineage
  and posture — its run, its depth, whether it may write (`engine.leaf_spawn_env`) — and
  `mcp_shared.leaf_tool_denial` refuses an orchestration tool to every leaf and holds a research
  stage's in-process tools to `read`, refusing in the words the tier's own refusal uses
  (`granted_call_refusal`). An agent CLI's tool server gets the lineage as its
  environment; the native runtime runs its tools in the gateway process, so it binds the stage's
  lineage around each tool call instead (`mcp_shared.bind_leaf_lineage`), and every reader goes
  through one accessor (`mcp_shared.leaf_value`). Before, a native stage read depth 0: its
  `subagent_run` spawned, its research posture was not seen, and `resume_run_id: "self"` found no
  run.
- **Path matching** normalizes only the queried item (`~`/`$VAR`, then
  `abspath`) and **never** runs a pattern through `normpath`, which would
  collapse `/a/**/../b` to `/a/b` and silently drop the `**`
  (`tests/test_guardrails_path_matcher.py` is the table).
- **What the layer buys**: no HTTP write surface (absent from the
  `_EDITABLE_CONFIG` PATCH allowlist, no PUT of its own); agent write paths
  refuse it (`governance/` is in the built-in sensitive-path denylist); no
  mid-run widening (read once and cached, so a tamper needs a restart an
  operator can see); tamper evidence (boot SEL-audits source + digest). Every
  clamp is logged and SEL-audited (`guardrails.ceiling_clamp`).
- **What it does NOT buy**: OS-level immutability against a process running as
  the operator. On a single-user machine that requires the file to live outside
  `$HOME`, owned by another uid and mode `0444` — which is what
  `PERSONALCLAW_CEILING_FILE` is for.

## Who answers an approval (`approval_answer.py`)

An approval is a question put to you: a tool call waiting for Allow, a workflow's gate, the
question a trigger's action stopped on, a control-bridge action waiting to be confirmed, an app's
proposal, the proactive digest's proposals. One rule decides who may answer any of them, and every
door that applies an answer asks it (`approval_answer.refusal`):

- **Only you answer.** That is a signed-in session of yours (the dashboard, a paired phone, the link
  `personalclaw token` prints), or you on a paired chat channel. A channel's app checks that a press
  is its paired owner's, which is the contract of `ChannelDelivery.request_approval`. The decision's
  audit row names which: `you`, or `channel:<provider>`.
- **Never the asker.** An approval records who asked it as it is asked (`asked_by`: the chat's agent,
  the app that started the chat, a subagent, the run, the trigger, the bridge client), and an answer
  from that principal is refused.
- **Nobody else.** An app's token, an agent's tool, a trigger, a workflow run and a control-bridge
  client answer nothing. An agent's tool is recognised by the gateway's internal secret, which only
  the gateway's own tool servers and scripts present. That holds in `auth_mode=none` and under the
  local-network bypass too, where the middleware treats every loopback caller as you.
- **One exception: an `event` gate.** It parks a run until something happens, and asks nobody's
  permission. The trigger it waits for (a monitor's self-scheduled wake, for example) answers it,
  and you still can. A trigger answers no other gate, so a trigger an agent armed against its own
  run cannot approve that run's approval gate. What it answers an event gate with is only a wake
  ([workflows.md](workflows.md)): it cannot decline the run, rewrite one of its steps with a
  `revise`, or leave an "always allow" behind.

A refused answer decides nothing and leaves the approval pending. It writes one
`approval.answer_refused` audit row naming who tried, what, and who asked. An HTTP door answers
`403 approval_owner_only`; an agent's tool gets the same sentence as its result. What each door
used to allow:

- **Tool approvals.** `POST /api/approvals/{id}/{action}` relayed an app's answer for any chat but
  the app's own. It and the chat card's route took an agent's tool as you wherever the gateway
  admits every loopback caller as you (`auth_mode=none`, the local-network bypass).
- **Workflow gates.** An agent's `workflow_resume` answered gates, its own run's included; it now
  only lifts a pause. `POST /api/workflows/runs/{id}/confirm` was open to apps, and the resume route
  took an agent's tool as you where every loopback caller is you.
- **A trigger's question.** `POST /api/triggers/{id}/answer` accepted the internal secret, which
  the gateway admitted on every trigger route for an agent's `/run`. The credential now opens that
  one route ([the internal credential](#the-internal-credential)).
- **Control-bridge confirmations.** The client that asked redeemed its own token at `/confirm`, with
  the bearer it asked with, and `personalclaw inbound confirm` did the same with the bridge's token.
  A confirmation now waits in your Inbox, where you confirm or decline it
  (`POST /api/external-access/bridge/confirmations/{id}`). `/confirm` refuses every client, and the
  CLI verb is gone.
- **The digest.** Its replies were open to apps. Where every loopback caller is you, an agent's
  `triage_rules` tool could teach it an approve rule, which answers every matching proposal before
  it is asked. Approve rules are now yours, and a deny rule, which only takes away, is anyone's.
  A reply on the chat channel the digest reached answers it as you on that channel only when the
  channel's own owner sent it (`owner_id_for`, the id its owner pairing stored), in the DM that
  digest reached; the channel's app does not decide that, core does (`proactive.channel_reply`).
  Your answer is not a grant, so it is never refused for one: the operator ceiling's `ask` and
  incident mode hold the digest's own auto-execution, and leave your answer running as attended
  work, still held to the action denylist and the spend floor.

What this cannot tell apart: a process running as you on this machine
([limitations §10](../security/limitations.md#10-a-process-running-as-you-can-answer-as-you)).

## Egress chokepoint (`net/`)

`net/client.py` + `net/guard.py` + `net/policy.py` form the ONE outbound-HTTP
chokepoint:

- Named policies: `STRICT`, `CONNECTOR` (knowledge scraping), `WEBHOOK`
  (user-configured POSTs), `LOOPBACK_INTERNAL` (loopback only — **never
  widened** by config), `REGISTRY`/`LISTED` (exclusive allow-lists),
  `FETCH_ACTION` (the `net-fetch` action provider — exclusive over an EMPTY
  base list, so an unconfigured instance reaches nowhere), `LISTING` (git
  fetching an app from where a registry listing says it lives — public hosts
  only, plus the owner's allow-list and the host of the registry source they
  added), `MEDIA` (a generated image or video a provider's answer points at —
  public hosts only, sized for a clip at 200 MB and 180 s; a larger file is
  refused whole, never saved cut off).
- `net/git.py` is the same chokepoint for `git`, which owns its sockets: it
  resolves names itself and follows redirects, so a check made before
  `git clone` checks a name, not the connection. `run_git_guarded` points git
  at a loopback CONNECT tunnel (`http.proxy`) that evaluates EVERY host git
  connects to, redirect hops included, dials only the addresses the guard
  returned, and allows port 443 only. Git runs HTTPS-only
  (`GIT_ALLOW_PROTOCOL`), with no saved credentials, no global or system
  config, and none of the environment that would route it around the tunnel
  (`https_proxy`, `NO_PROXY`, injected `GIT_CONFIG_*`). Used for registry
  listings (`apps/source.py`); an owner-typed URL clones as before.
- The **exclusive** profiles — `LISTED`, `SYNC`, `FETCH_ACTION`, and the derived
  `capture`/`a2a-outbound` — are the ones where a caller, not a person, picks the
  URL. Each is `allow_only=True` over an empty base, so "nothing named yet" means
  "nowhere to go". `METADATA_SERVICE_HOSTS` is denied on the two that can be
  pointed at an operator-named host (`SYNC`, `FETCH_ACTION`): a deny is evaluated
  before the allow-list AND before DNS, so it survives an operator who lists the
  cloud metadata service by hand.
- `egress_policy_for(base)` is the single config-layering seam: the Security
  panel's allow/deny hosts and `allow_private` are layered onto a base policy
  at the `web_fetch`/`web_extract`/render entry (`web/fetch.py`) and at
  webhook/knowledge-connector call sites (`knowledge/connectors/web_url.py`).
  Raw `net.fetch` stays config-free for fixed-posture internal callers.
- `allow_only` inverts `allow_hosts` from ADDITIVE (waive the private-range
  block) to EXCLUSIVE (only a listed host is reachable), checked before DNS
  resolution. It is what makes an egress TIER able to narrow anything.
- `egress_policy_for_run(base, session_key=None)` is the one reader of the RUN's
  `SafetyProfile.egress_tier` (through `profile_for_session`, so the ceiling bounds
  it), and the guard asks it about every request (`guard.evaluate`): the tier holds
  at every door that asks the guard, whatever policy the door built — `net.fetch`
  (every app that fetches through the SDK: a search provider, an image provider's
  download, a webhook), `open_url`, `web_fetch`/`web_extract` and the headless
  render, the browser's navigations. The surfaces that refuse before they ask ask it
  too: a watched source's poll (`triggers/web_poll.py`), the hosts the shell reaches
  unasked (`run_bounds.shell_egress_policy`) and a program an app's code starts
  (`apps/launch_egress.py`). The run is the session the call is made for, bound
  around every tool call (`mcp_core.set_current_session_key`: the native runtime's
  dispatch, the tool server an agent CLI runs, `POST /api/tools/invoke`), or the run
  an automation's action is dispatched for, held around the action alone
  (`net.policy.egress_held_to`, egress only, under the key the action denylist judges
  an unattended run of it by: a trigger's fire `unattended_dispatch_key("trigger:<id>")`,
  which every hand run of it takes too, whoever starts it, a hook its parent session or
  `"hook:<id>"`, a workflow step `"workflow:<run>"`). A session bound inside the held
  work (an agent the action starts) is the inner run.
  A call made for no run (the owner's own action, a background job) keeps to the
  Network egress settings alone.
- A remote MCP server's connection (`mcp_client.McpServerConn`) asks the guard about
  the server's URL before its start and before each tool call, in the caller's
  context, under `net.policy.MCP_SERVER`: the owner's configured server may be on
  their machine or network, the metadata service never, and the caller's run narrows
  it like any request. The native runtime lists a turn's tools held to its session's
  run (`_build_catalog`), since a listing starts the connection. A refused call sends
  nothing and answers in `guard.refusal_for`'s words; the open connection, shared by
  every run, is made for none (`egress_held_to("")`), so a run's refusal never marks
  the server failed.
- A command a run starts is held to the tier where it is launched (`sandbox.wrap_argv`,
  which asks `no_network_for_commands`, the other reader of the tier, through the same
  binding), since nothing it reaches asks the guard. Only a tier of `all`, or no run, keeps
  its network: the OS can take a program's network away, not keep it to a list, so under
  `off`, `listed` and `registry` the namespace launcher adds `unshare(CLONE_NEWNET)` (a
  namespace whose one interface, its loopback, is down) and the Seatbelt profile adds
  `(deny network*)`, each launch an `egress_launch` row (`denied`) in the audit log. Where
  the sandbox cannot do that (`mode="off"`, no backend, no network namespaces:
  `_probe_unshare_net`) the command is refused (`SandboxEnforcementUnavailable`, in `_no_network_refusal`'s words,
  audited as `command_refused` with control `sandbox`), and `wrap_refusal` gives the same
  answer to a tool's pre-flight. The runners that use no path sandbox (a loop's or a
  workflow's check, a workflow's setup and teardown steps, an effect's teardown) hold their
  launch to their unattended identity and take the sandbox only when the network goes
  (`sandbox.egress_bound_argv`). An agent CLI's process and the owner's terminal keep the
  network (`sandbox.wrap_program_argv`, the sandbox providers' launch): a CLI reaches its
  model itself. `tests/test_a_command_a_run_starts_keeps_to_its_egress_tier.py` fails a
  site that runs a command someone wrote without the sandbox's launch.
- `egress_policy_for_profile(base, tier)` is the composition — tightest wins, and
  caps only tighten. `off` returns `None`, and the guard refuses every host as
  `egress_off` before it is looked up, audited as an `egress_fetch` refusal (the
  render's pre-flight as `web.render`). `listed`/`registry` make an additive base
  exclusive, with the tier's preset unioned onto the base's own hosts; an exclusive
  base keeps exactly its own hosts, since a union would widen it. A `loopback_only`
  base (the gateway's calls to itself and to an app's backend) is never narrowed.
  What the tier does not reach is in
  [limitations §18](../security/limitations.md#18-a-runs-egress-tier-holds-where-its-requests-ask-the-guard).
- `web_fetch` (and `web_extract`, which fetches through it) also asks whether the
  conversation was **given the link**. Text the agent reads — a fetched page, a
  tool's output, a fenced span — can carry instructions, and the cheapest is "now
  open this link", with what the agent knows in its query string. So in a chat the
  agent opens a link only when the user gave it in their own message (typed, pasted,
  or the source link of a library item attached to it — recorded as the user's when
  the message is taken in: a send, a queued or steering send, an edit and resend, a
  plan comment, a channel message, a line posted in a room, for each member's
  session), or a `web_search` returned it, or it is the page
  a `web_fetch` already opened. A link inside a fetched page or a tool's output, an
  app's message, a widget's payload and anything inside an `<untrusted_content>`
  fence grant nothing. The refusal names that rule and tells the agent to ask the
  user for the link rather than fetch it another way. This check never widens the
  egress settings above: a link the user typed to a host they deny, or to a private
  host they have not allowed, is still refused by the guard. The record is in-process
  per session; deleting or forgetting a chat drops it (`dashboard/chat_forget.py`).

## Where a run reaches without a person saying yes (`run_bounds.py`)

The fetch surfaces above own their sockets; the agent's shell and the programs an app's code
starts do not, so what they reach is read from the command they run
(`command_effects`: the hosts a command names, and the paths its writes name) and held there:

- **Network.** `shell_egress_policy` is `LISTED` layered with `security.egress` (the operator's
  allow and deny hosts, exclusively) and narrowed by the run's egress tier (in a run whose tier
  is not `all` the command then runs with no network at all, above). A shell command whose
  network facet names a host off it, or names no host it can read, is refused in an unattended
  run (the native runtime's `_guard_and_invoke`, and the dashboard's gate for an agent CLI's ask)
  and put to a person in an attended one: every grant that would answer a call unasked (Trust,
  YOLO, a hook's auto-approve, a subagent's standing grant, the relay's grant) skips it, and the
  pending approval carries the host (`reach`) to every surface that shows it, a channel's prompt
  included (the approval brief's `reach`), none of which offers a standing answer for it. A switch
  the owner turns on while it waits does not answer it either: a Trust or YOLO switch, which
  answers every pending approval it covers, leaves it asking (`DashboardState.answered_alone`). A
  channel's own turn asks it on the channel's prompt (`chat_trust.chat_grant`), and an
  evaluation, which has nobody to ask, refuses it (`eval.runner`). The native runtime asks the
  call's tool first (`_preflight`): a call its tool refuses anyway, such as a denied command, is
  refused with the tool's reason and put to nobody.
- **Writes.** An unattended run's writes are held to its own folders (`session_roots`: the folder
  it works in and the ones it was given) and its own temporary folder (`scratch_dir`, its shell's
  `TMPDIR`). A native file write or a shell command's write facet outside them is refused; a write
  whose path the command does not name is too.
- **Protected folders.** A shell command that would delete your home folder, the filesystem root
  or the folder the call runs in (a chat's working folder, the folder a channel's conversation, a
  subagent or a run works in), a folder that holds one, or everything inside one, is put to a
  person past every grant, exactly as a call past the allowed hosts is, and refused in an
  unattended run (`protected_folders.py`). It is decided on the command, never its text:
  `command_effects` names what each delete removes (`rm`, `rmdir`, `unlink`, `find -delete`, a
  `find` that runs one, through `sudo`, `env`, `sh -c`, `eval`, `xargs` and a leading `cd`), and
  each removed path is resolved as the shell and the kernel would resolve it (the home shorthand
  and variable expanded, a relative path read from the call's folder, `.`, `..`, repeated and
  trailing slashes normalised, links followed, letter case ignored; a glob removes every path it
  can expand to). A delete whose path the reading cannot name (a variable it does not know, the
  paths `xargs` hands one, a line it cannot split or a `sh -c` command built from a variable that
  names a delete program) is put to a person too. The card's `reach` line names the folder ("This
  would delete your home folder"), and the refusal does. Paths with nobody to ask refuse such a
  command too: the action denylist for every unattended command path
  (`unattended_protected_delete`), a bash action and an app's lifecycle hook. Any other delete
  follows the normal rules.
- **Apps.** `apps/launch_egress.py` reads every launch an app's code makes, at Python's own audit
  event for it, so whatever helper started it: each host its command line names gets an
  `egress_launch` row naming the app and the program, and a host neither the manifest declares for
  that program (`launches[].hosts`) nor the owner allowed is refused before the program starts.

It is defence in depth in front of the OS sandbox, which confines neither egress nor writes
([limitations §16](../security/limitations.md#16-where-a-shell-command-or-an-apps-program-reaches-is-read-from-its-command-line)).

## Browsing on the user's behalf (`browse/`)

The `browse` action provider has two execution targets. `gateway` drives the gateway's own
Chrome profile under the egress chokepoint above. `user_browser` drives the operator's OWN,
already-logged-in browser through a paired loopback extension (`browse/target.py`,
`dashboard/handlers/browse_connector.py`). Because that target acts as the fully-authenticated
user, its authorization is not the earned-autonomy ladder but a **per-task grant**, and these
rules are load-bearing controls, not UX:

- **Per-task grant, fail-closed.** Every `user_browser` task requires a fresh, explicit human
  grant naming the site scope it will touch, routed through the shipped `ApprovalGate`
  (`agents/native/approval.py`) before the browser is touched. No answer within 300s, no approval
  channel, or any gate error is a **REJECT** — the run never starts, never falls open, and never
  silently retargets the gateway profile (`browse/grant.py`).
- **Where the human answers.** The pending grant surfaces in the dashboard's **Browse live view**
  band — the same panel that shows the run step by step and carries the kill switch — naming the
  task, the hostnames it will touch, and the fact that not answering is a refusal. It reads
  `GET /api/browse/status` and answers `POST /api/browse/grants/{request_id}/{action}`, both of
  which resolve the ONE gate in `browse/grant.py`. This is deliberately **not** the native-session
  tool-approval gate behind `GET /api/approvals`: that one is keyed by tool + tool_input and its
  timeout is origin-aware, so routing a browse grant through it would let a different table
  silently redefine the 300s ceiling stated above. Two gates, because they gate two different
  things.
- **No-credential-access invariant.** The agent drives an already-authenticated browser; it never
  reads, stores, or transmits a password field's value, a 2FA code, or a cookie jar. The grant and
  revoke audit rows (`browser_grant`, `browser_revoked` in the SEL) carry only the task label, the
  host scope, and a reason — never a credential, cookie, or token. The live `browse_grant` WebSocket
  frame carries a bare COUNT for the same reason: the label and scope stay behind the
  owner-authenticated read rather than riding a broadcast an app-scoped socket could be permitted
  to see.
- **The request is audited, not just the decision.** A `browser_grant` row is written when the grant
  is **requested** (`outcome=needs_confirm`) and again when it resolves (`granted`/`rejected`). The
  request row is what makes an authorization attempt against the operator's own logged-in browser
  auditable even when nobody answers it or the run abandons it — a request whose only row was
  written after resolution left no trace at all in exactly that case.
- **A tab of the run's own.** A granted task works only in a tab the browser opens for it: the
  extension opens a new background tab in a tab group named after the task (in a browser without
  tab groups, an unfocused window of its own) and announces that tab's own page target, which the
  grant is then bound to (`browse/grant.py:open_run_tab`). Attaching the browser announces no
  page, so a page the user already has open is never a browse target, whichever tab has focus,
  and the tab's page target is set once and never re-pointed. If no tab of the run's own comes to
  be within 15 seconds, the task is refused (`ERR_BROWSE_RUN_TAB_UNAVAILABLE`) and never falls
  back to another page.
- **Close-to-kill and take-over.** Closing the task's group (or its window, or the tab) is a hard
  stop the run observes within one step; so is the browser disconnecting. Bringing the run's tab
  to the front is a take-over: the run pauses (parks, notes kept) before its next model call and
  says so, rather than act in a page the user is now using. This is distinct from the browse kill
  switch (`browse/killswitch.py`), which stops *all* unattended browse via a flag.
- **Honest limit — no IP pinning on a real browser.** A real browser does its own DNS and opens
  its own sockets, so `net.fetch`'s resolved-IP pinning does **not** apply to `user_browser`: every
  navigation is still pre-flighted through the egress guard, but that is validation only and stays
  rebind-vulnerable. This is inherent to driving any real browser and is stated here rather than
  implied away.
- **Not an anti-bot surface.** This target exists to let the agent act in the user's browser under
  explicit per-task permission. PersonalClaw does **not** describe, design, or expose anti-bot or
  CAPTCHA avoidance as a capability; any such effect is an incidental consequence of legitimate
  traffic from the user's own machine, never a feature.

### A background chore is a call of its own, offered no tools (`chores.run_chore`)

The chores that run behind the chat — a chat's title and tags, its organize proposal, its
follow-up chips, the home suggestions, a folder's icon, history compression, memory consolidation
and skill refinement, a Slack thread's title — each answer in text from what their prompt carries,
and that prompt quotes chats, pages and messages nobody vetted. Each is one call of its own to a
model on the Background chain (`chores.run_chore`, over `llm_helpers.one_shot_completion`): the
model is built for the call, sent the chore's own prompt, masked, and nothing of any other chore,
and it is offered no tools.

It runs on a model, never on an agent CLI. Every one-shot call is resolved by the bridge's one rule
for a call automation makes (`provider_bridge.resolve_metered_model`): the model bound for the use
case (the Background chain, else the Chat chain), else a configured model that names one of its
own. An agent CLI is never that model: it is a whole agent, with tools of its own, and a home may
run every chat on one. With no model chosen the chore is skipped before anything is built
(`chores.NoModelChosen`), and the surface it was for says so: the new-chat page and the dashboard
under their suggestions, a reply where its follow-ups would be, and an untitled chat's header under
its name (and in the answer to Regenerate title), each in the words "… need a model: choose one in
Settings → Models.", leading there. The same rule picks the model a judge grades on (best-of-n, an
evaluation, a lesson's replay). A call used to fall back to "the first registered provider" when
nothing was bound, and on a home whose only runtime was an agent CLI that started the CLI for every
chore.

A tool request on such a call is refused, with nobody asked, and audited as `rejected` with the
reason `reject_all_policy`: the one-shot call refuses every call its model asks about
(`llm_helpers.one_shot_completion`), and so does the default policy of
`llm_helpers.stream_and_collect`, the one loop every helper call streams through, which used to
approve every request nobody was asked about. A caller whose turn may use tools passes its own
policy: the heartbeat task (its SafetyProfile, `approval_policy_for_session`), a subagent's result
announced in its parent (`gateway.injection_approval_policy`) and a room member's turn (the room's
gate) do; the side chat and an agent's test run on its page refuse every call explicitly.

No session is kept for them, so what one chat's chore reads never
reaches another's: the chores used to share one long-lived session whose conversation carried
every chore of every chat, and a consolidation stored words read there as the user's own facts.
`tests/test_session_acquisition_census.py` fails a chore that takes a session. The one-shot
completions (inbox triage, digests, re-tagging, schedule parsing) are such calls too. A chore of an
Incognito or Temporary chat reaches no model but the chat's own: it runs on that model only in the
chat's own turn, and is refused anywhere else before anything is sent
([chat-sessions.md](chat-sessions.md)).

The prompt optimizer runs as the lite agent (`personalclaw-lite`), whose runtime is built with no
tool providers at all (`provider_bridge._build_native_runtime`): its model is offered no tools,
and a call it makes anyway names a tool that does not exist. Its session lasts one rewrite.

A heartbeat task is not a chore: the owner allowed it to run "with your agent's tools"
(`heartbeat.consent`), so it runs as their agent in a session of its own that ends with the task
(`cron:system:heartbeat-tasks:<run>`).

## Untrusted-content fencing

`security.py::fence_untrusted` wraps third-party text in
`<untrusted_content>` markers (escaping any embedded marker so content can't
break out), paired with a system-prompt note that fenced spans are data, not
instructions. Applied to web-search results, inbox content, and third-party
payloads; memory recall applies the same data-not-instructions framing to
recalled episodes (`dashboard/handlers/memory.py`; see
[knowledge-memory.md](knowledge-memory.md#recall--the-privacy-guard)).

The markers are for the model. A tool result keeps them where it is stored and
where the model reads it; where the dashboard shows it (a chat tool card, the
full-result view, the Tools page runner) `web/src/lib/untrustedFence.ts` takes
off the real markers and shows the text between them, rendered as text. A
marker that was part of the wrapped text was escaped by the fence, so it is
shown as the text it was.

## Stored and remote text on the page

Everything the dashboard shows that it did not write — a model's reply, a
tool's result, a knowledge item's body, an inbox message, an app's or a tool's
description, release notes — renders through ONE component,
`web/src/ui/Markdown.tsx`, on the dashboard's own origin. It renders Markdown
only:

- **Embedded HTML is shown as text.** Before anything parses it as HTML, each
  embedded fragment is either a run of attribute-free formatting tags (`<kbd>`,
  `<br>`, `<sub>`, `<details>` …, the `EMBEDDED_FORMATTING` list) or it becomes
  a text node. A form, a frame, a style block or an image tag in a body is words
  on the page, and a tag carrying any attribute is too.
- **A link opens only for http, https and mailto**, read with the browser's own
  URL parser; a relative link (a path on this gateway, an in-app route) is not
  a link.
- **An image loads only over https or from the artifact library's own route.**
- **A `<widget>` block runs in its sandboxed frame only in the agent's own chat
  reply** (the `widgets` prop). Anywhere else it is embedded HTML like any other.

`tests/test_rendering_registry_parity.py` censuses every other way the web app
turns a string into live markup — `dangerouslySetInnerHTML`, `innerHTML`, a
parsed document, a frame's `srcdoc` or an HTML blob — by file and count, with
why each is safe (highlight.js output; an `svg`/`document` artifact after the
fail-closed sanitizer in `ui/content/sanitize.ts`; the sandboxed widget and
artifact frames), and fails on a new one. The project's own copy — labels,
hints, errors — is JSX text and never passes through the renderer.

The page's Content-Security-Policy (`dashboard/server.py::dashboard_csp`) is
the layer behind that: `default-src 'self'`, no code, style or font from
another origin, `img-src` adding `data:`, `blob:` and `https:` (the images in a
rendered body), `frame-src 'self' blob:`, `frame-ancestors 'self'`,
`object-src 'none'`, `base-uri 'self'`, `form-action 'self'`, and a
`connect-src` of `'self'` plus the loopback WebSocket at the port the page was
served on (and the public host when `dashboard.public_url` is set).
`script-src` keeps `'unsafe-inline'` because widget frames — blob: documents —
inherit the page's policy and run inline scripts and handlers, and the sign-in
and pairing pages carry inline scripts; dropping it needs widgets served as
documents with a policy of their own.

## Supply chain (`supply_chain.py`)

`SkillScanner` gates both app installs and skill installs through
`install_guarded`:

- verdicts: clean / warning (consent required — 409) / **dangerous (terminal
  refusal, non-overridable)**;
- the integrity invariant: **scanned bytes == installed bytes** (no
  time-of-check/time-of-use window between scan and install). Staging leaves the same
  tooling out of both (`supply_chain.NEVER_INSTALLED_NAMES`: `.git`, `__pycache__`,
  virtualenvs), and the scanner skips nothing else, so no folder or file size installs
  unread; a file it cannot read is an `unscanned_file` finding;
- source trust tiers modulate strictness (a bundled skill's `curl` is not the
  same risk as a random repository's).

## Trust / YOLO state (`trust_mode.py`)

ONE process-global YOLO (auto-approve) state: config-permanent vs TTL'd
surface activation (`YOLO_CHANNEL_TTL_SECS`), with `on_disable` callbacks.
Config-driven YOLO is read back from `agent.yolo` while it is on, by `is_yolo_active()` and by
`yolo_from_config()` alike, so a channel asked "is YOLO permanent?" gets the config's answer, not
a cached one.
Dashboard and channel apps delegate to it — there is deliberately no second
implementation. Task-mode tool-gating postures are hard-enforced before approval
for the native runtime, and for every call an agent CLI asks about, YOLO or not;
what an agent CLI runs without asking reaches it only as framing (a documented
tradeoff — [limitations §1](../security/limitations.md#1-an-agent-cli-is-held-to-the-rails-only-for-the-calls-it-asks-about)).

## Audit — the Security Event Log (`sel.py`)

`SecurityEventLog` writes HMAC-chained events (key file `sel_hmac.key`) —
tamper-evident, append-only. Events carry caller, operation, outcome, and
`downstream_service` labels (the generic value is `"channel"`; no vendor
names). API denials, webhook auth failures, and app lifecycle events all log
here. The dashboard Security panel reads it.

**Events, not requests.** A refused authentication is a row, every time. A successful one is a
row the first time a session is seen in a 15-minute window, and the rest of that window is one
summary row counting the requests and the paths they reached (`token_auth._SuccessTally`, flushed
at shutdown), so every success is accounted for at a row per session per quarter hour. An
internal-secret grant is one tallied `internal_auth` family (it used to write two rows). On day 8
one idle Home tab grew the log ~5 MB an hour, 94% of it `dashboard.token_auth ok`.

**A tool call is one row, written when it is decided.** The card of a call arrives before any gate
has run (the native loop yields it and only then checks the agent's tool list, its deny-list, task
mode, tool grants and approval), so no runtime audits there. An asked call is audited where the answer lands:
`approved` or `rejected` by `you`, `expired` or `cancelled` by `nobody`, or `auto_approved` by the
grant that answered. A call nobody was asked about is audited at its result, from what the
runtime stamped on it (`llm.events.unasked_outcome`): `denied` by the gate that refused it,
`auto_approved` by the policy that waived its ask, `failed` or `cancelled` for one that never ran,
or `invoked` for a tool that asks nobody. `metadata.decided_by` says who decided, in every runtime
that hosts a turn: the chat (which a channel's turn also runs), the subagent manager (trigger
agents and every workflow stage), the background helper and the eval runner, whose allowlist
answers its asks (`eval_safe_tools`). Before this, the subagent manager and the background helper
wrote `auto_approved` for every call and the chat wrote `invoked`, so a call the deny-list refused
read as approved; the eval runner wrote `invoked` for every call as it appeared and then a second
row for one that asked.

**A tool call's row says what it ran.** Each writer hands the log the call's arguments
(`log_tool_invocation(tool_input=…)`), and the row's `resources` records the command of a shell
call (the platform's `bash`, or an agent CLI's shell call by its kind, its name or a `Running: `
title) and the path a file write names (the native `write_file`/`edit_file`, or an agent CLI's
call it reports as `edit`, `delete` or `move`): `audit_subject.subject_of`. A row's `resources`
(that subject, or what a row about no such call records), its `operation` (an agent CLI can title
a shell call with the command itself) and its `error` are stored masked the way a call's title is
masked where it is shown (exfiltration URLs, then credential shapes, withheld whole if the masker
fails), on one line with each control character a visible escape, and cut at 500 characters with
a marker saying how many were left out (`audit_subject.audit_text`). A command refused before it
ran keeps its command the same way (`command_audit`, `metadata.command`), and so does an action
the denylist held (`guardrails.denylist`). Settings → Security → Audit log shows the command or path
beside the tool on each row, and `personalclaw security events` prints it.
`tests/test_sel_subject_census.py` holds every row whose tool is named at run time to handing its
call's arguments over, so a new path that audits a shell call or a write cannot leave out what it
ran.

**An automation's action is one row when it did something, at the rung it ran at.** A trigger's
or hook's governed action that succeeded writes `guardrails.autonomy_executed`
(`guardrails.rungs.record_execution`): `caller` is `autonomy:<action type>`, and `resources` the
rung, the undo it kept (`undo=<record id> reversal=<handle>`, or `reversal=none`) and the trigger
or hook and provider that ran it. A run that had nothing to do (an `ActionResult` outcome of
`skip`: the heartbeat queue was empty, the workflow was already running) writes no row; its run
history records the no-op. An action that runs code is the exception (`ActionTypeSpec.runs_code`):
a script that answers `skip` still ran, and its own answer never keeps it out of the log. The rung
is the one Settings → Guardrails → Earned autonomy shows, because the panel asks the route the
seams use (`ladder._type_row`).

**"Runs with undo" is claimed only with an undo behind it.** An action type can be undone when
every provider it governs names the reversal handles it can take back (`rungs.can_be_undone`); a
type's providers must agree, because the rung is the type's. A run nobody watches is narrowed to
"runs with undo" only for such an action. An action that cannot be undone never runs at that
rung: where its own rung would put it there (a declaration, a grant, or the clamp that lands an
app's `autonomous` claim on that rung), it asks first, and the panel and the Inbox row say that
what it does cannot be taken back (`rungs.route_action_type`). A run is logged at the rung it
had: one that came back with no handle the reversal store kept is logged `rung=autonomous`, never
at the undo rung, so every row at that rung names an undo the panel's undo list offers. A
promotion never offers "runs with undo" to an action that cannot be undone
(`autonomy.promotion_eligibility`).

**Size and retention.** The live file rotates by size: the write that takes it past 16 MiB
archives it to `sel_archive/security_events.<UTC time>.jsonl` under a cross-process lock and starts
a fresh chain (`verify_integrity` tolerates the break at a rotation), and the rotation is itself
logged (`sel.rotated`). Retention is age: 365 days, applied by the remediation engine's SEL prune
to the live file and to whole archives, with a 512 MiB ceiling on the archive as a backstop
(oldest first); every archive removal is logged. The durability inventory lists `sel_archive/`
(`security_events_archive`), so snapshots carry the rotated trail. `POST /api/sel/rotate` does
the same rotation on demand, and keeps the live log if the archive cannot be written.

## Data-leaving-the-system rules

- Session-archive reads are redacted (`history.py` via
  `redact_credentials` / `redact_exfiltration_urls`).
- **A login in an address is masked whole, and only the login.** In a URL the credential runs to
  the last `@` before the host, so a password holding an `@`, a `:` or a `/` is masked entirely
  (`https://ada:p@ss@host/x` becomes `https://[REDACTED: url credential]@host/x`); where a raw
  character in a password cuts the address short, the mask runs on to the last `@` it can belong
  to. An scp-style `user:password@host:path`, which has no `://`, has its login masked the same
  way. Every mask built on `redact_credentials` carries it: the log sinks' `MaskingFormatter`,
  `redact_or_withhold`, `redact_for_display`, `mask_child_output`, and `strip_url_userinfo` for an
  address that has to keep working. The scan is its own module, `address_logins.py`, and
  `tests/test_redaction_cost.py` holds it to a plain reading of the rule, byte for byte.
- **A text its masker fails on is withheld, never passed on as it came**
  (`security.redact_or_withhold`). A masker that raised proves nothing about what the text holds,
  so the local-model health message, a run notification, a send-message hook's text, the run
  ledger's and a crash record's fields, the doctor's evidence, the trigger history, a skill draft
  and a proposal's text say `[redaction failed; text withheld]` in its place. The log sinks do
  the same: `MaskingFormatter` writes a record it cannot mask (or one whose arguments do not fit
  its message) as its time, level and logger with its words withheld, and the sinks' handlers
  (`WithholdingHandler`) name a record they cannot write without its words, where the standard
  library's handlers write its message and arguments to stderr as they came.
  The maskers that withheld before these did (a confirmation preview, a batch's recall view, the
  capture store and the capture proxy's failure line) say it in the same words. A web source's
  sanitizer is held to the same rule: markup it fails on is withheld as
  `[sanitizing failed; content withheld]` and never stored as the page sent it. The sanitizer is
  nh3 alone (`web/extract.py`): an install that cannot import it refuses to sanitize
  (`SanitizerUnavailable`) instead of cleaning markup with a weaker pass, so a web source
  withholds the field, and a fetch, a document made from HTML or a report whose text carries
  markup fails with the reason. The agent's tools report it as they report any failure:
  `web_fetch` and `web_extract` as a failed fetch, and `document_create` as a failed call, in
  the refusal's words.
  `tests/test_a_text_that_cannot_be_masked_is_withheld.py` holds every try in the tree that masks
  or sanitizes a text to returning a placeholder or raising when the masker does.
- Portability export (`portability.py`) always excludes credentials: `.env`,
  `sel_hmac.key`, `session_map.json` are on the exclusion list.
- **Download filenames are redacted, in one place** (`http_download.py`). A
  `Content-Disposition` filename is a copy of user text that leaves the machine by a
  second path — proxy logs and browser download history — and outlives deleting the
  file it named, so it is redacted even where the body deliberately is not. The project
  export is exactly that case: the archive carries secrets on purpose (it declares
  `X-PersonalClaw-Secrets-Expected`) while its *header* must not, and for a while it did
  because the name was interpolated raw. `attachment_disposition` is the only emitter —
  it redacts, emits RFC 6266's `filename*=UTF-8''` beside an ASCII `filename=` fallback,
  and closes header injection by rebuilding that fallback from an allowlist. A new
  download route calls it; the `content-disposition-header` duplication ratchet
  (`structural-baseline.json`, floor 0) reds if one formats the header itself instead.

## Memory privacy

Restricted sessions (temporary/incognito) gate memory reads/writes and lesson
capture — enforced in the after-turn path, session listing/search, and the
recall API — and keep nothing in long-term memory by any path: the stores
refuse every write made for one (`memory_writes.py`, failing closed on a mode it
cannot read), the agent's file tools and shell change nothing in the memory
folders for one, and for a temporary one read nothing there either (refused
before anyone is asked, and kept read-only, or unreadable, to its commands by the
OS sandbox; what that does not cover is
[limitations §19](../security/limitations.md#19-a-private-chat-is-kept-out-of-the-memory-folders-not-out-of-every-store)),
nothing in the artifact library changes for one: its artifact tools save,
change and remove nothing there, an artifact mentioned in it is not stamped with
it, and a run it starts publishes nothing there (`memory_reads.keeps_nothing`,
the check the library's routes make),
no background model is given anything of one
(`blocks_background_models`), and nothing of one reaches any model but the one
its turn runs on: not the embedding model (its memory is searched by keyword and
its tools are ranked by their words), a tool's model, a subagent's, the image
reader or a fallback (`model_may_read`, asked at every seam that reaches a
model, carried into every worker thread and into the tool process an agent CLI
runs). The work it starts away from its turn (a subagent, its own subagents, the
steps of a run it started) is handed that model with its mode and runs on it,
after a restart too, and a start that cannot is refused before anything is sent.
A side question asked beside it reads its conversation and runs as its own work,
as a turn does (`memory_writes.as_its_session`), held to the same answer. Nor
does its work leave anything behind that lasts after it and runs as work of its
own: a loop or project, an automation or scheduled task, a callback
(`lasting_work.py`, refused where the loop store, the loop manager, the trigger
tools and the callback store are reached, before anything is written). Nor a
record that other work reads later: a skill or a draft of one, a proposal for
review, a task, a task list or a project on the Tasks page (its overview and
ledgers included), an Inbox item, a change to a loop's spec or plan (refused, by
the same rule and in the same words, where the skill drafts, the proposal queue,
the task and project stores, the Inbox sink and the loop's spec and plan are
written). A Temporary or Incognito chat's workflow runs, and every chat's batch,
are its own: only that chat's work and you read them, another chat's agent reads
one as not found, and no chat's turn is told of another's (`workflows/chat_runs.py`).
So is what its work waits on in the Inbox, an ordinary chat's included (the approval its agent or
a subagent of it asks for, a batch's ask, a question put to you, what its own run waits on):
another chat's agent, a scheduled briefing and the Morning triage digest read none of it and count
none of it, while you read all of it (`inbox_reach.py`).
They end with the chat: the workflow supervisor stops each once its chat has
ended (a Temporary chat's session, an Incognito chat's deletion) and deletes it
with what it produced (`workflows/private_runs.py`).
Every one of these reads a session's mode through one reader
(`memory_writes.session_mode`): the live chat first, then the registry, the
transcript and a workflow step's run. So work a chat starts on its first turn,
before its transcript is written, keeps the chat's mode, and a mode nothing can
say (a record that cannot be read, a value this build does not know, a chat the
gateway no longer holds that nothing records) is taken as a Temporary chat's,
and what such work is refused says that the chat's setting cannot be read. What the person gives such a chat in a form its model cannot read (an
attached file, a shared screen) is read by the model set up for it, and the
chat's notice says so. Who may read memory at all is one answer
(`memory_reads.reach_of`) every reader asks: a Temporary chat's work reads none
(its subagents, their subagents and the steps of a run it started included), and
neither does an app's — a conversation it started, an agent run it asked for, an
agent its scheduled job started, an agent working for any of them — unless the app
holds the `memory` permission its install consent showed you. The same grant
governs what such work changes: without it the memory store refuses every change
made in the app's work (`memory_writes.check_memory_statement`), and with it each
record names the app as its source (`memory_writes.written_by`), so an app's
write never outranks one of yours. A turn someone other than you asked for (a
colleague in a shared thread, a correspondent, a program through the
OpenAI-compatible endpoint: `turn_source.asked_by`) changes none of your memory
on its own: the turn names who asked (`memory_writes.asked_for`, or
`turn_asked_by` for a channel that runs its turns itself), the requests its tools
make and the subagents it starts read it (`memory_writes.asker`), the stores
refuse their changes saying who asked, as do the file tools, the shell and the
gate an agent CLI's own tools ask (`screen_tool_call`), and what the memory tools ask for is held
for your own Allow (`dashboard/memory_holds`, through the approval registry, no
standing grant answering it). Work such a turn starts that outlives it (a
workflow run, a loop, a callback) records who asked on its own record
(`lasting_work.py`), so its steps, cycles and turns are held the same way after
the turn has ended and after a restart, as is the turn that hands a subagent's
report back to its chat; an automation is not made or changed on someone else's
say-so, and a loop takes the words of whoever asked for it alone. Details in
[chat-sessions.md](chat-sessions.md#session-model) and
[knowledge-memory.md](knowledge-memory.md#recall--the-privacy-guard).

Nor does any standing grant of yours answer for such work (`approval_grants`, rule 4). A chat's
Trust, Trust reads and YOLO, an agent's "Always allow", your Approval mode "Auto", your chat's Trust
as a subagent it started holds it, and the turn that announces a subagent's report in a chat you are
in are yours, for what you ask. In a turn someone else asked for, and in the work it starts, none of
them answers a call, on either runtime and in a channel that runs its turns itself:
`approval_grants.stands_for_work` is the one question every grant that answers a call asks, and the
policy a chat's native runtime is handed is read through it for whoever asked for the turn it runs
(`DashboardState.chat_policy`). The call is asked of you instead, on your own surfaces. Its card
names who asked (`asked_for`) and offers your answer for that call alone, a Trust or YOLO switch
answers none of those calls, a channel's prompt offers no Allow for this chat for them, and each
hold is audited (`approval.grant_held`, naming who asked) beside the decision row, which names them
too. What a call's tool declares (a read, a call whose work asks you itself), the operator's hook
settings and patterns, and a grant given for that one run (a loop's Mode or "This loop", a run's
own approval mode, a trigger's Allow) answer as before. Such work searches none of your chats
either: `chat_search`, `GET /api/sessions/recall` and the inbound door's `sessions_search` search
nothing for it, answering with a sentence that names who asked (`chat_recall.not_searched_for`).
