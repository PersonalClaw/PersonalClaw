# Security Limitations — What We Do Not (Yet) Enforce

PersonalClaw's strongest claim is that its controls are *enforced at the point of
execution*, not merely requested in a prompt. Honesty requires naming the places
where that is not (yet) literally true. These are deliberate, documented
tradeoffs — not oversights — and each is stated here in the same terms the
internal architecture uses, without softening.

This page is referenced from the public
[threat model](threat-model.md) and from `SECURITY.md`. Verified against the
codebase at the commit that introduced this file.

## 1. ACP agents under auto-approve (YOLO) rely on system-prompt framing, not rails

Task modes (`agent` / `ask` / `plan` / `build`) decide *which tools may run*. For
the **native runtime**, this gate is hard-enforced: `task_modes.py` is enforced
in `_guard_and_invoke` **before approval is consulted, so a Trust/YOLO
auto-approve can never bypass a task-mode restriction**
(`src/personalclaw/task_modes.py`).

For **ACP agents** (external CLI agents driven over the Agent Client Protocol),
the same module is applied in the dashboard's permission handler as
"belt-and-suspenders for ACP runtimes that gate via their own protocol path"
(`task_modes.py`). But an ACP agent running under YOLO ultimately gates through
its own protocol path, and the architecture states the tradeoff plainly:

> Task-mode tool-gating postures are hard-enforced at the permission prompt for
> the native runtime; ACP agents under YOLO rely on system-prompt framing (a
> documented tradeoff — `task_modes.py`).
> — [`docs/architecture/security.md`](../architecture/security.md#trust--yolo-state-trust_modepy)

**What this means for you:** if you enable auto-approve (YOLO) *and* run an
external ACP agent, that agent's tool use is bounded by prompt framing rather than
by the same hard rail the native runtime enforces. Running a trusted native agent,
or leaving approval prompts on, keeps the hard rail in force.

## 2. The app `network` permission is declaration-only

An app manifest declares a permission scope (`api` / `events` / `mcpTools` /
`storage` / `network` / `memory` / `cron`). Most of these are enforced
server-side by the gateway. **`network` is not**, by design:

> `can_use_network` — **DECLARATION-ONLY (unenforced by design)**, and the consent
> surface says so rather than implying otherwise. There is no per-app egress
> chokepoint to enforce at: an app's provider code is imported **in-process** by the
> gateway, so its own `httpx`/`requests` calls are the gateway's egress, and an app
> with a backend owns a separate OS process with its own network stack … So the flag
> is DISCLOSURE, and the Store discloses it as such … Treat `network: true` as an
> honest declaration, not a boundary.
> — `src/personalclaw/apps/permissions.py`

The consent surface states the non-enforcement outright. The Store shows the
network claim **outside** the list of permissions the gateway enforces, labelled
advisory, with the text *"PersonalClaw does not confine an app's outbound traffic:
this app's code can reach the network either way. The declaration is disclosure, not
containment."* It is shown whether or not the app declares `network`, so an app that
declares `network: false` is not presented as one the platform has blocked. An app's
*gateway-mediated* reach is separately bounded by its `api` permission; what is
**not** bounded is the app's own outbound traffic.

That `api` bound has two halves, and the second is worth knowing because a path
prefix says nothing about power. The allowlist half is what the app declares and
what the Store shows you. The other half is a closed **owner-only** registry
(`apps/permissions.OWNER_ONLY_API_PATHS`) of capabilities no declaration reaches at
all, not even `"*"`: the terminal and its sessions, computer-use, the credential
store and secrets vault, the security audit log and SEL rotation, your login
password and second factor, gateway restart, and local token minting. Your security
posture is in the same class: the chat approval mode (auto-approve-everything),
resuming after an incident stop, project trust, autonomy promotions, standing
approve/deny rules, device pairing codes, external-access clients, and the agent's
runtime config (`allowedTools`, the servers it launches). So is code that runs as you,
and your own access: the MCP servers the gateway launches (`/api/mcp`, reads included,
since a remote server's headers hold its bearer token), your backups (export, import and
restore), who may message your agent from a chat channel, taking back an autonomy grant or
undoing what an automation did, and bringing your setup over from other agent tools
(`/api/onboarding/import`, which copies their MCP servers, skills and instructions in).
Holding any of those would make every other
line in a manifest moot, so there is nothing to scope — and before the registry existed,
an app declaring `/api/ws` (the event socket) prefix-matched `/api/ws/terminal/{id}` and
got an interactive shell running as you. A manifest that names one of these paths now
fails to install. None of this touches your own access to those surfaces; the refusal
applies only to requests carrying an app identity.

Some route families mix your business with an app's, so they are declared route by
route instead (`apps/permissions.ROUTE_AUTHZ`). An app may fire a webhook trigger with a
client token you minted, cancel or pause a run, stop a background agent, redeem a pairing
code you minted, and stop all unattended work with `POST /api/incident`. It may not
define, edit, arm or run an automation, save a workflow, start or steer a run, start a
goal loop, install, enable or update an app or a pack, connect or disconnect a chat
channel, or sign out one of your devices. Its scheduled work is the `crons` its manifest
declares, which install consent lists.

What your agents are told is in the same class, because an agent carries out its
instructions with your tools under your approval settings. An app may not create, edit,
sync or delete one of your agents (its system prompt, tools, skills, model or approval
mode), write an agent definition or make one of them your agent, install, write, accept
or remove a skill, write a prompt or a snippet, change which system prompt your chats,
unattended runs and judges start from, launch a prompt template (which starts a goal
loop), or rewrite the routing notes your orchestrator reads. It may still read those, check
a skill's integrity, and decline a proposed skill. An app ships its skills in its
manifest, which install consent lists by name, and runs agent work through its own
`agent` permission. A lesson is memory that every agent is handed as a rule, so
`/api/lessons` needs the `memory` grant, the same as `/api/memory`.

Your conversations are in the same class, because a message in one of them is an
instruction your agent carries out. An app may not send into one of your chats, or edit,
regenerate, resume, stop, retitle, rebind or fork one, or answer an approval in one; it may
not set the task mode of your chats, share one, carry one into your channel DM, or delete
one from your history.
It may not speak in a room (a line there is written as yours, and every member answers
it), answer an agent's question in your inbox, approve a proposal, write an inbox note in
your name, or post through the schedules' delivery door (`/api/send-message`), which
speaks as your agent. An app may hold conversations of its own: it starts one, and only
that one is its to reach. Its turns need its `agent` permission and run under that grant,
never under your approval switches (YOLO, Trust, Trust reads, an agent's "always allow").
The operator ceiling still bounds it, and an app never answers an approval raised in its
own conversation. It reaches you through a proposal, which the inbox labels with the
app's name, and `/api/reveal` opens only a file in its own data folder. The exception is
the relay you install for approvals: an app that declares `/api/approvals` (the menu-bar
companion does) can approve or reject your pending approvals one at a time, and the
gateway cannot tell whether an answer it relays was yours.

What your conversations say is yours to read, too. An app reads only the conversations it
started: a transcript, its map, a tool's full output, an export, a draft skill or a
background agent's result from any other is refused. Your chat list, your history and a
search over them answer an app with its own conversations only. Your rooms, your inbox,
your chat folders and tags and the transcripts older versions archived are not an app's
to read at all. Its websocket carries a frame about a conversation only if the app started
it, and an inbox item only if the app raised it (its own proposal). The approval relay is the
one exception there too: it hears your approvals, which it already reads through
`/api/approvals`. An app also may not clear your notifications, mark
them read or change what reaches you.

Every write route in these families, and every read in your conversation families, has to
be declared one way or the other: one that is not is refused to every app until someone
declares it, and `tests/test_security_posture_rail.py` fails the build on it.

The security settings that live in `config.json` are refused field by field instead,
because `/api/config` also carries ordinary settings an app may legitimately write: an
app-scoped `PATCH` of a field that is a security setting (YOLO, the approval mode,
sign-in and 2FA, egress, the keychain, sandbox ceilings, guardrail budgets, external
access, sync) answers `403`, in either direction, as does an app answering a pending
approval with a standing grant such as `yolo`.
Every other setting is the app's only if its manifest names it in `permissions.config`,
the list install consent shows: `GET /api/config/personalclaw` hands an app those fields
and nothing else, and a write to any other answers `403 config_field_not_declared`.
Every such refusal leaves a Security Event Log row naming the app and the field. The file
explorer shows an app no folder that holds your PersonalClaw home, and refuses an app
the same way: `403`, with a row naming the app and the path it asked for.

**What this means for you:** treat an installed app's `network: true` as a stated
intent you are consenting to, the same way you would trust any program you choose
to run — not as a sandbox that prevents the app from talking to the network. The
supply-chain scanner (quarantine → scan → consent → install, with `dangerous`
terminal) is the control that vets what you install; the `network` flag is
disclosure, not containment.

## 3. App Python dependencies load into the gateway's own process

An app may declare `dependencies.pythonDependencies` in its manifest, and the
installer pip-installs them into `<home>/app-python` — one directory every app
shares, on the same volume as the rest of your data — which the gateway loads into
its **own process**, after its own packages (`apps/app_python.py`). Core ships lean
deliberately (heavy provider and ML libraries are not core dependencies), so this is
how an app brings what it needs. Nothing is installed into the environment the
gateway runs from: in the container image that environment is read-only to the
gateway's user, and an install there would not survive the next `docker run`.

**What is enforced:** an app can add packages, but it cannot change or shadow one the
gateway uses.

- The directory is appended to the import path after the gateway's own entries, so a
  module the gateway already provides always wins the import.
- pip resolves with every distribution the gateway can import pinned, so a
  dependency — direct or transitive — that needs another version of one of them fails
  resolution instead of being installed, and nothing outside `app-python` is ever
  uninstalled or replaced. (Measured without the pins: pip resolved a conflicting
  `urllib3` by uninstalling the base environment's copy.)
- Every installed app's requirements resolve in the same pip run, so the version of a
  package two apps share is one both accept — or the install is refused, naming the
  conflict. One interpreter can hold only one version of a module, so this is the
  honest form of isolation between apps, not a weaker one.
- An update installs exactly the versions its new manifest pins, older or newer. pip
  runs from the gateway's environment and will not uninstall anything outside it, so it
  writes the new version over the old copy in `app-python`. The installer then removes
  the old copy itself, by that copy's own file list, deleting nothing outside
  `app-python` and no file the new version lists.

Before pip runs, `app_manager._reject_core_dependency_conflicts` also refuses any
declared requirement that names a core-declared dependency unless the version already
installed satisfies it — the gateway's copy loads first, so such a pin could never
take effect. The check is fail-closed: an unparseable requirement, or
a core-owned name whose installed version cannot be read, denies rather than
installs. Requirements for libraries core does not own are unaffected — that is 20
of the 22 first-party apps that declare dependencies, so the check has something to
say about **two** of them: `design-critique` pins `Pillow` and `diarization-onnx`
pins `numpy`, both core-declared, and both are admitted only while the installed
version already satisfies the pin. The provider SDKs like `openai` and `anthropic`
are *extras*, not core dependencies, so every app declaring one of those is
unaffected. (Ratio measured 2026-09-07 against `PersonalClawApps` `f623b66`, over
the 22 manifests declaring `dependencies.pythonDependencies`, compared against
core's `pyproject.toml` `[project].dependencies`. It is a claim about another
repository at a moment in time: re-derive it, do not trust it.)

**What is not enforced:** the packages an app adds are importable by everything in
the gateway's process — PersonalClaw itself and every other app. Isolating app
dependencies properly requires out-of-process providers — today an app's provider
code is imported in-process, so there is no import boundary to scope a path to. That
is a platform-seam change, recorded as such rather than approximated here. And the
pins protect against a *resolution* moving the gateway's packages, not against the
code itself: an app's dependency runs with the gateway's own access once imported, so
on an install where the environment is writable by your user it could rewrite it,
as any program you run could. The container image's environment is read-only to the
gateway's user.

**What the consent surface tells you:** the declared specifiers, verbatim, on the
install-consent screen itself — `anthropic>=0.20`, not "this app installs some
packages". `app_manager.describe_python_dependencies` classifies each against the
same core pin set the guard above gates on, so a package core does not own reads as
new code the gateway will load, while a core-owned pin (`Pillow>=10,<13`) reads
as "the version you already have must satisfy this, or the install is refused". An
app declaring none shows nothing at all. This section documenting the behaviour is
not a substitute for that: a user consenting in a modal does not read a threat
model, so the duty belongs to the surface where consent is given.

When core's own pin set cannot be read — which is also when the guard refuses the
install outright — every specifier degrades to the *new code* reading rather than
disappearing. Over-disclosing a package is safe; under-disclosing one is not.

**What this means for you:** an installed app can add libraries to the gateway's
process, so install apps you trust — the supply-chain scanner (quarantine →
scan → consent → install, with `dangerous` terminal) is the control that vets them.
What an app's install cannot do is change the version of a library the gateway
depends on.

## 4. An app's frontend bundle runs in the dashboard's own page

An app with a UI ships an ESM bundle. The host **fetches it, rewrites its bare
import specifiers to host-provided blob shims, and `import()`s the result into the
dashboard page** (`web/src/app/appSdk.tsx::loadContributedModule`), then mounts its
exported `mount` function in the host React tree
(`web/src/pages/apps/ContributedPage.tsx`, via `createRoot`). There is no iframe on
that path — `grep -c iframe` over `web/src/pages/apps/*.tsx` is `0` in every file.
The architecture doc states the posture plainly:

> **The gate is a declaration, not a sandbox.** … a contributed page already runs in
> the host React tree (`ContributedPage` mounts it with `createRoot`, no iframe) with
> the host `window`, so an undeclared app is not *prevented* from reaching the same
> components. What the block buys is legibility.
> — [`docs/architecture/app-platform.md`](../architecture/app-platform.md)

So an installed app's UI code has the dashboard's `document`, its `localStorage`, the
owner's session cookie, and authenticated same-origin reach to every `/api/*` route —
the same authority the dashboard itself has. Sharing the host's single React instance
is what the rewrite exists to do, and it is what makes this same-origin by
construction.

**What is enforced:** every call an app makes through the SDK client
(`createAppApi` / `useAppApi`) carries a short-lived app-scoped token, and
`app_permission_middleware` refuses a path the manifest did not declare — 403 + a
Security Event Log row, before the handler runs. An app's **backend** is handed no other
credential, so every request it makes through the gateway is bound by that allowlist
(and by the owner-only registry in §2). That bounds the backend's requests, not the
backend: its code runs as you and can act on your home without asking the gateway (§7).

**What is not enforced:** the bundle is under no obligation to use that client. A bare
`fetch('/api/…')` from app UI code carries the owner's cookie and *no* app identity, so
`app_permission_middleware` — which acts only on requests that carry one — passes it as
an owner request. That reaches the owner-only surfaces too: the terminal, the credential
store, the audit log. The manifest's `api` allowlist therefore bounds the app's backend
and its SDK calls, **not** its page code. `ui.components` (the `generative-component`
capability) is loaded by the shell for every *enabled* declaring app, so that module runs
without the user ever opening the app's page.

This is not closable from the server side: a same-origin request from an app's bundle is
indistinguishable from the dashboard's own. It needs a distinct **origin** for app UI. A
`sandbox` attribute alone cannot deliver it while the bundle shares the host's React
instance, and for the same reason `ChatEmbed`'s frame
(`allow-scripts allow-same-origin`) is not an isolation boundary either — it is a
separate document, not a separate origin.

**What this means for you:** an installed app's UI is host code, so treat installing a
UI-bearing app the way you would treat running any program as yourself — the
supply-chain gate (quarantine → scan → consent → install, with `dangerous` terminal) is
the control that vets it. Install consent says so at the moment you decide: an app that
ships a UI carries an advisory row naming the host-page reach, beside the permissions
the gateway does enforce. An app that ships no UI runs no code in your browser at all,
and the same row says that too.

## 5. The bundled default model is a floor, not an assistant

A fresh install can chat before you set anything up because PersonalClaw offers one small
model you can download: `SmolLM2-135M-Instruct`, in the Q8_0 GGUF build
`unsloth/SmolLM2-135M-Instruct-GGUF`, under Apache-2.0. The native `bundled-chat` app runs
it inside the gateway, on your CPU, with numpy
(`src/personalclaw/apps/native/bundled-chat/provider.py`). The model, its licence, its
pinned source and its sha256 are recorded in
[`bundled-model-signoff.txt`](../../src/personalclaw/apps/native/bundled-chat/bundled-model-signoff.txt).

It is there so a new install is not a dead end. At 135 million parameters it can greet you
and answer a simple factual question, and it gets unreliable quickly after that. Whenever it
is the model answering — because onboarding made it your chat model when you downloaded it
there, or because nothing else is bound — the chat screen says so.

**What it does not get:**

- Tools. The provider declares `supports_tools = False`, so the agent loop runs it with an
  empty toolset. It cannot read or write a file, run a command, search the web or call an
  app, and no approval prompt appears because there is nothing to approve. Asked to create
  a file, it replied with a Python snippet for you to run, and nothing was written.
- The context PersonalClaw builds. For other models, each turn is assembled into one
  prompt: the agent's instructions, your memory and preferences, the skills chosen for the
  turn, today's date, then your message. On a fresh home that came to about 20,000
  characters. A model this small, handed that much, continues the instructions instead of
  following them, so the app declares that it takes your message alone (`request_only` in
  `provider.py`) and PersonalClaw sends it nothing else. On the measured first turn the
  model saw 18 tokens. It does not know your name, your notes or the date, and it does not
  know it is PersonalClaw either. Asked "What can you do for me?", it offered to help with
  health questions.
- Room. It reads 4,096 tokens at a time (the **Prompt budget** setting; the weight itself
  accepts 8,192, and the setting cannot go higher). That includes its reply, which stops at
  320 tokens (**Maximum reply length**), so about 3,770 tokens are left for the
  conversation, and older turns are dropped first. A message that does not fit on its own is
  refused before the model reads it, with a sentence giving the limit in tokens and roughly
  in characters. For plain English prose at the defaults that was about 3,765 tokens, or
  roughly 15,000 characters. A reply that stops at the maximum length is marked **Cut off**.

**What it did with real requests**, measured on the shipped weight through the
dashboard's chat route:

1. "What is the capital of France?" got Paris.
2. Asked for today's date, it said 2019.
3. Asked to search the web for news about the Mars rover, it wrote a confident summary from
   memory with invented facts, among them a Curiosity launch on a SpaceX Falcon Heavy.
4. Asked for three things in one message (list three fruits, say which has the most vitamin
   C, write a two-line poem about it), it listed three fruits and did neither of the other
   two.
5. Told a name and a city and then asked for them back, it repeated its previous reply word
   for word.

In none of these did it say it could not do something or did not know, so treat whatever it
tells you as unverified.

**It also answers for the rest of PersonalClaw.** While it is your chat model, or with nothing
else bound, anything that asks for a chat model gets it — background work borrows the chat
model, and with nothing bound the implicit fallback
(`providers/provider_bridge.py::_resolve_from_config_registry`) picks it. That includes the jobs
that run after each reply to name the chat, tag it and suggest follow-ups, so every reply is
followed by more work for it on your CPU. In the same measurement, none of its four
follow-up suggestion replies came back in a form the chat could use. Goal loops and
workflows resolve their model the same way, so with nothing else bound they get this model
too.

**What it costs:** it loads on your first message and stays loaded. The weights take 538 MB
as float32, and loading them added 0.7 to 0.8 GiB to the resident size of the process that
holds them (measured on an Apple silicon Mac). Reading a long prompt takes more on top of
that: in a process holding only the model, a full 4,096-token prompt peaked at 1.3 GB
resident and took 21 seconds, and an 8,192-token one peaked at 1.8 GB and took 62 seconds.

**What is enforced:** the download never starts by itself. It is offered with its size
(138 MiB) in onboarding, on the chat screen and in **Settings → Providers**, and runs only
when you ask for it. It comes over https from a pinned revision on Hugging Face and is
checked against the sha256 in the sign-off record before it is installed. The 150 MiB ceiling
is enforced while the bytes arrive: a source that announces a bigger file is refused before
anything is written, and a transfer that passes the ceiling is stopped there. A transfer that
fails, is cancelled, passes the ceiling or does not match leaves nothing behind that
PersonalClaw would load. The record's licence must be on an allowlist
(Apache-2.0 or MIT); anything else is refused by name.

**What is not enforced:** the sha256 check guards the download, not the file on disk. If you
copy the weight into `$PERSONALCLAW_HOME/models/bundled-chat/` yourself, which is how you set
up a machine that has no network, PersonalClaw checks only that the file is at least the
signed-off size before it loads it. Compare its sha256 with the record's first, or run
`PERSONALCLAW_HOME=<home> python scripts/fetch_bundled_model.py --check` from a checkout.

**What this means for you:** use it to see chat working and to look around. For real work,
bind a model under **Settings → Models**. A local [Ollama](https://ollama.com) needs no key
and keeps working offline; every other provider is an app in the Store
([Getting started §3](../guides/getting-started.md#3-configure-a-model-provider)). Whatever
you bind wins, because this model answers only when nothing else does, and there is no
config to clean up afterwards. To stop it answering at all, delete it under **Settings →
Providers**, or turn off **Answer when nothing else is bound** in its settings there. That
switch takes effect when you save it; it decides what answers when no chat model is chosen,
so if onboarding made this your chat model, choose another in **Settings → Models** too.

## 6. Where a stored secret can still appear in plaintext

A provider's API key, every app setting its manifest declares `x-meta.sensitive`, every value
in an MCP server's `env` and `headers`, and the webhook token (`hooks.webhook_token`, which
`POST /api/hooks/agent` checks) are kept in the credential store: the OS keychain, or
`~/.personalclaw/.env` at mode 0600. The file that configures them holds a `{{secret:…}}`
reference, resolved where the value is used (`src/personalclaw/config/secret_refs.py`), and only
against the credentials of that file's owner: an app cannot name another app's key, a provider's,
or a Secrets-panel credential in its settings and receive it. An MCP
server variable you mark plain (the Add form's **Plain values**) stays readable in `mcp.json`
and travels with an export. One named like a token, secret, password or API key is stored
whatever you mark. Snapshots and exports carry the references and never the store, so
restoring onto another machine means entering those secrets again.

A secret still appears in plaintext in these places:

- **Copies made before you upgraded.** The first start after the upgrade moves every plaintext
  value it finds in those files into the store. A copy made earlier keeps its own: an older
  snapshot or export, a `pre-restore-*` directory, the time-travel history of `config.json`
  (`state-history/`, which never leaves the machine), and an audit-log row an earlier
  `personalclaw config set` wrote with the value in it. The audit log travels in an export,
  and its rows are chained, so they are not rewritten.
- **A value with a NUL character**, which no environment variable or keychain entry can hold.
  Adding a server with one is refused; one that reaches `mcp.json` another way (an import, a
  hand edit) stays there and travels with it, and the gateway logs which variable it left. A
  multi-line value, such as a PEM key, is stored like any other: `.env` keeps it on one line,
  quoted.
- **A value typed into `mcp.json` or `config.json` by hand** (`personalclaw config edit`, an
  editor) stays in the file until the gateway next starts and moves it.
- **A token in an MCP server's arguments or URL.** Only `env` and `headers` values are stored.
  A key passed as an argument (`--api-key …`) or carried in the URL (`?token=…`, `https://user:pw@…`)
  stays in `mcp.json` as written, travels with an export, and shows in the server's edit form. The
  import list and the list of configured servers mask or leave out both, but the file keeps them.
  Put a token in an environment variable or a header instead.
- **Claude Code's own config.** Putting an MCP server into Claude Code's scope
  (`POST /api/mcp/apply` with `ccGlobal`) writes it into Claude Code's `.claude.json` (in your
  home directory, or in `$CLAUDE_CONFIG_DIR` when that is set) with its values, because Claude
  Code reads only its own file. That copy is outside PersonalClaw's home, snapshots and exports,
  under Claude Code's own file permissions. The copy PersonalClaw makes by itself when sessions
  restart (`~/.mcp.json`) carries only the plain values.

**What this means for you:** after upgrading, treat snapshots and exports made before it as
holding your tokens. Delete them, or change any token that has left your hands in one.

## 7. An app's own code runs as you

Everything §2 describes bounds an app's **token**: what the app may reach by asking the
gateway. None of it bounds the app's **code**. An app can bring six kinds, and all six
run under your own account:

- provider modules, imported into the gateway's own process
  (`providers/loader.py::_load_ext_module`), or run as a child of it when the manifest says
  `execution: sidecar`;
- a backend, started as a process on this machine (`apps/backend_runtime.py`);
- the MCP servers in its manifest's `mcpServers`, each a command the gateway launches with
  the gateway's own environment, which carries the stored credentials PersonalClaw exports
  for its child processes (`apps/mcp_bridge.py`, `mcp_client.py`, `config/loader.py`);
- setup hooks (`setup.onInstall` and the rest), shell commands run at install, update,
  enable, disable and uninstall (`apps/app_manager.py::_run_hook`);
- CLI steps (`cli.setup`, `cli.doctor`), imported and run when you run `personalclaw setup`
  or `personalclaw doctor` (`app_cli.py`);
- a connector pack's source parsers (`sources`), scripts the gateway runs on what the pack's
  sources fetch, fenced off from the network but not from your files
  (`knowledge_providers/pack_parse.py`).

The Python packages an app installs load into the gateway's process too (§3).

That code can read and write every file in your PersonalClaw home. It can switch YOLO on
by editing `config.json`, with no `PATCH /api/config/personalclaw` to refuse; it can add
a server to `mcp.json`; it can read the credential files; and it can read `session_key`,
the key that signs every session token, and sign one as you. The home's private file
modes keep other accounts on the machine out, not your own processes. None of the
refusals in §2 applies, because none of this goes through the API.

**What is enforced:** an app cannot add code or instructions through the gateway after you
install it. Its MCP servers, its scheduled jobs and the skills it gives your agents come
from the manifest you consented to, and defining any of them through the API is owner-only
(§2), as is writing your agents and the system prompts they start from. A backend that names a sandbox tier
(`backend.sandbox`, such as `docker`) launches inside that tier rather than on the host,
with its `network` permission deciding its egress and its `storage` permission its one
writable folder (`apps/backend_runtime.py::build_backend_sandbox_spec`). A named tier that
is not available refuses to launch instead of falling back to the host. A backend's
environment is an allowlist, so it inherits no credential you did not pass through by
name in `sandbox.env_passthrough` (`sandbox.py::build_child_env`), and a backend and an
MCP server both run under the resource-ceiling shim (`sandbox.py::spawn_shim_argv`).

**What is not enforced:** anything about the code itself. A backend that names no
sandbox, a provider module, an MCP server, a setup hook and a CLI step have your files and
your network, and an MCP server and a setup hook also get the gateway's full environment.
A source parser has your files and no network.

**What the consent surface tells you:** the install dialog reads `apps/disclosure.describe`
and has a row titled *What it runs on this machine*. It leads with the gateway's own
sentence saying which of the app's code runs as you, that it can read and change your
files, your PersonalClaw settings included, and that the permissions listed beside it
limit what the app asks the gateway for, not what that code does
(`apps/disclosure._runs_as_you`). Below it the row names every kind: the server process it
starts, and the sandbox tier it runs in when it names one; each provider module, with the
entry point the gateway loads and whether it runs inside the gateway or as a child of it;
the shell command of each lifecycle hook, verbatim, with when it runs (the install or
update now, switching the app on, switching it off, removing it); the steps it adds to
`personalclaw setup` and `personalclaw doctor`; each source parser; and each MCP server
with the command line or URL it launches. A row titled *What it teaches your agents* lists
the skills it installs by name. The Store card reads the same projection, and an update
that adds any of it asks again. The network row says the app's code can reach the network
whatever it declares.

**What this means for you:** installing an app that brings code is running a program
as yourself. The supply-chain scanner (quarantine → scan → consent → install, with
`dangerous` terminal) is the control that vets it, and the permissions describe what
the app's token may do once it runs, not a box around it.

## Why these are listed, not fixed

Per the project's lifecycle discipline, a control *gap* discovered while writing
documentation is recorded as a candidate for the security-hardening track — never
patched inline in a docs change. Every item above has a named future direction
(extending the hard rail to ACP protocol paths for #1; OS-level app isolation for
#2; out-of-process providers for the residual half of #3; a distinct origin for app
UI, with the SDK crossing it as a message channel, for #4; checking a hand-copied
weight's sha256 when it loads, for the gap in #5; per-app OS isolation for every kind of app
code, which today only a backend that names a sandbox tier has, for #7). This page will shrink
as those land.
The rest of #5 will not: a small model is the point of a floor, and the remedy for its
limits is to bind a real one.
