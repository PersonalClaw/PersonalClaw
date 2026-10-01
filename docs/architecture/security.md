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
`_dev_user_middleware`) re-implements claim adoption in none-mode: it extracts
the Bearer/`?app_token=` token, validates it (`validate_token_with_app`), and
sets `request["app"]` so `app_permission_middleware` and the WS event filter
scope the request. The app token only *narrows* the dev owner's reach — the
permission model holds in every auth mode.

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
itself, and in the `mcp-core` server an agent CLI runs), a scheduled script's `ctx.notify` and
`ctx.call_tool`, and `personalclaw auth rotate-key`. They carry the gateway's internal credential,
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
- **A credential this gateway did not issue** is refused with `403 internal_secret_invalid`, whose
  sentence says so. **A caller with none to read sends nothing:** a tool's result names the home it
  looked in (`mcp_core._internal_secret`), and a scheduled script's call back returns
  `{"ok": false, "error": {"code": "internal_secret_unavailable", …}}` while the rest of the script
  runs.

### Webhook auth

`POST /api/hooks/agent` (`dashboard/handlers/hooks.py`) is one of the internal
credential's operations: from this machine it takes the internal secret (a local
relay presents it; no PersonalClaw process calls the route) or an owner session, and
from anywhere else it is refused. Past that, `_verify_hook_token` is a
constant-time (`hmac.compare_digest`) check of the Bearer or
`x-personalclaw-token` header against `hooks.webhook_token` in config — a
`{{secret:…}}` reference there, resolved from the credential store at the
check (`config/secret_refs.py`). No configured token, or a reference the store
cannot answer, means every request is refused. And a session key a callback
the agent registered names (`webhook_callbacks.py`) starts a turn only once the
owner allowed that callback: until then the answer is `403 not_allowed`.
Denials are logged to the Security Event Log.

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
  (`denied_command_patterns()`), so config edits apply immediately. This one
  source feeds both the native bash tool and the Security panel.
- **Suspicious-pattern watchers** — `SUSPICIOUS_BASH_PATTERNS` (52 patterns)
  flag rather than block.
- **Tool-name denies** — `BUILTIN_DENY_PATTERNS` (fnmatch over tool names)
  with a documented `_DENY_EXCEPTIONS` escape hatch.
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
cost of credential theft. The one place it confines writes is the home's owner-only paths (below);
it does not stop an agent from doing anything else.

| It **does** | It does **not** |
|---|---|
| Hide credential dirs/files from the agent child (macOS Seatbelt deny-reads; Linux bind-mounts) | Confine filesystem **writes** (except `~/.ssh` on macOS `strict`, and the owner-only paths) |
| Refuse writes to the owner-only paths at every level (macOS deny-writes; Linux holds the home's entries — below) | |
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
  every entry already in it except these is bound back writable. A bind on one file holds that
  inode — it cannot hold a name that does not exist yet, and the kernel dissolves it when the file
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
  is on). Read only, and only the files that source takes in (`dir_source.takes`, the rule its own
  scan uses: its file patterns, nothing hidden, nothing below the top when it is not recursive).
  `knowledge_search` and `knowledge_get` name a note's file, so the agent can open it.

Everywhere else is refused, read or change, with a sentence that says which folders the tools
reach and where the owner adds one. An entry naming the filesystem root or a system folder is not a
place, and neither is a source its own provider would refuse to poll. A folder taken out of the
setting, or a source paused or pointed elsewhere, is out of reach at the next call.
`file_scope.refusal` answers from a call's arguments, the session's folder and the settings alone,
so the decision can be made before an approval is asked for. Each turn the native loop tells the
model which folders it reaches beyond the workspace (the `[file places]` note), and when it has the
knowledge tools, to search the library first.

Inside every place the check the Files view and `/api/file-read` make still holds
(`file_roots.Admission`): symlinks and `..` resolved, so a link or a climb out of a place reaches
nothing; the PersonalClaw home reached only through a place inside it (`file_roots.within`); no
protected credential location (`~/.ssh`, `~/.aws`, the keychain, the home's own `.env`, `auth/`,
`governance/`); and no PersonalClaw key, `.env`, `sessions.json`, `session_key`, `*.key`, `*.pem`
or `*.secret` file, nor any alias of one. A path that starts with `~/` names the owner's home, as
the owner writes it, and meets every one of these checks as any absolute path does (`~name` stays
a plain name). A `glob` or `grep` pattern is relative to the folder searched (the workspace, or the
call's `path`); one that is absolute, starts at `~` or climbs with `..` is refused as a whole, and
every match is checked one by one, so a listing, a search or a map leaves out what the tools could
not open. `code_map` indexes the workspace, an allowed working directory or a folder inside one,
never a knowledge source's folder, whose other files the tools do not read; its index skips the
same files (`codegraph.CodeGraphIndex`).

A file the agent names in a chat opens where the owner reads the chat: the Files view's read
surfaces (`/api/file-read`, `/api/file-raw`, `/api/file-watch`) also admit what the agent's file
tools may read (`files._agent_readable_path`, the same scope), for the owner's own requests and
never an app's, and never for a write. A mention that names a note by its file alone resolves to
the one watched-folder note with that file (`dir_source.note_file`); a name two notes share
resolves to neither.

PersonalClaw's own stores inside the workspace (every state-inventory entry inside its `workspace`
entry: the knowledge library's database and stored documents, the lexicon) are not files to the
agent. The file tools refuse them and leave them out of every listing, and the tool-call screen
(`hooks.HookManager.on_tool_call`) refuses a shell command that names one before any approval, with
the tool to use instead (`file_scope.store_named_in`): a raw read hands the model pages of a
database past the masking its own tools apply, and a write breaks the store.

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
  Trust reads' to approve. No tool switches between reading and changing on an argument: a listing
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
  anything read-only, so `readOnlyHint` counts only for a server listed in
  `security.mcp_read_only_servers` — per server, set on the Tools page, which asks first, and
  refused to an app. Its `destructiveHint` counts from anyone, because it only adds a question.
- **A shell call's command decides** (`is_read_only_bash`, an allowlist): for a declared call only
  the platform's `bash` — a name the registry reserves — is a shell; for an ACP CLI's call, one it
  reports as `execute`, a shell tool's name, or a `Running: ` title.
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
  spawn's own `approval_mode: "auto"`, the global Auto-approve setting, the hook settings and
  patterns, a listed source, the `--approval` flag, a remembered or policy-approved workflow gate,
  a workflow step's start its owner allowed before a restart cut the step off (for the same
  request, within the step's time limit, `approval_grants.APPROVED_BEFORE_RESUME`),
  the triage digest's auto-execution, a subagent's announce turn, an app's conversation, an
  unattended ACP CLI approving its own calls, a session policy that never asks, and the eval
  runner's allowlist of read-only tools. Under
  `{"approval": {"value": "ask"}}` none of them stands, and each refusal is audited
  (`approval.grant_refused`, naming the grant). A switch the owner presses (the chat's mode
  pill, a card's wider scope) is refused with `409 approval_grant_refused`, whose message names
  the file and says a restart applies a change to it. An operator's hook pattern is checked at
  the `hook_based` level, which a `hook_based` ceiling still permits. A grant also no longer makes
  a run headless: an agent whose grant the ceiling refused asks you for each call, through the
  same relay as any other.
- **The `tools` scope and a spawn's capability class hold under a grant.** Both are enforced where
  the host is asked about a call, and a runtime that answers its own asks (the native one, while
  a grant stands) asked about none: a read-only research run's write tools ran. The native
  runtime now asks the spawn's tool grants before its own approval
  (`NativeAgentRuntime.set_tool_grants`), and an unattended ACP CLI may approve its own calls only
  when the ceiling leaves the tools unrestricted. What that leaves for an ACP CLI is in
  [limitations §1](../security/limitations.md#1-acp-agents-under-auto-approve-yolo-rely-on-system-prompt-framing-not-rails).
- **A workflow stage is a leaf to its own tools on every runtime.** A stage runs with its lineage
  and posture — its run, its depth, whether it may write (`engine.leaf_spawn_env`) — and
  `mcp_shared.leaf_tool_denial` refuses an orchestration tool to every leaf and holds a research
  stage's in-process tools to `read`. An agent CLI's tool server gets the lineage as its
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
- `egress_policy_for_profile(base, tier)` narrows a surface policy by the RUN's
  `SafetyProfile.egress_tier` — tightest wins, and caps only tighten. `off`
  returns `None` and the caller refuses. Live at `web/fetch.py::web_fetch` (the
  agent's primary fetch surface) and `triggers/web_poll.py` (watched-source
  polls, plain + headless tier).

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
- **Close-to-kill.** The task runs in a tab group named after the task; the user closing it is a
  hard stop the run observes within one step. This is distinct from the browse kill switch
  (`browse/killswitch.py`), which stops *all* unattended browse via a flag.
- **Honest limit — no IP pinning on a real browser.** A real browser does its own DNS and opens
  its own sockets, so `net.fetch`'s resolved-IP pinning does **not** apply to `user_browser`: every
  navigation is still pre-flighted through the egress guard, but that is validation only and stays
  rebind-vulnerable. This is inherent to driving any real browser and is stated here rather than
  implied away.
- **Not an anti-bot surface.** This target exists to let the agent act in the user's browser under
  explicit per-task permission. PersonalClaw does **not** describe, design, or expose anti-bot or
  CAPTCHA avoidance as a capability; any such effect is an incidental consequence of legitimate
  traffic from the user's own machine, never a feature.

### A background chore runs with no tools (`provider_bridge._build_native_runtime`)

The chores that run behind the chat — a chat's title and tags, its follow-up chips, the home
suggestions, a folder's icon, history compression, memory consolidation and skill refinement, the
prompt optimizer, a Slack thread's title — each answer in text from what their prompt carries, and
that prompt quotes chats, pages and messages nobody vetted. They run as the lite agent
(`personalclaw-lite`), whose runtime is built with no tool providers at all: its model is offered
no tools, and a call it makes anyway names a tool that does not exist. The shared background
session (`_bg`) is the lite agent whoever reaches it first and whatever agent it names
(`SessionManager.get_or_create`). The one-shot completions (inbox triage, digests, re-tagging,
schedule parsing) call the model directly and never carried tools.

A heartbeat task is not a chore: the owner allowed it to run "with your agent's tools"
(`heartbeat.consent`), so it runs as their agent in a session of its own that ends with the task
(`cron:system:heartbeat-tasks:<run>`), never in the chores' session.

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
implementation. Task-mode tool-gating postures are hard-enforced at the
permission prompt for the native runtime; ACP agents under YOLO rely on
system-prompt framing (a documented tradeoff — `task_modes.py`).

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
has run (the native loop yields it and only then checks its deny-list, task mode, tool grants
and approval), so no runtime audits there. An asked call is audited where the answer lands:
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
recall API. Details in
[chat-sessions.md](chat-sessions.md#session-model) and
[knowledge-memory.md](knowledge-memory.md#recall--the-privacy-guard).
