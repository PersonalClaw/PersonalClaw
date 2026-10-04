# Security Limitations — What We Do Not (Yet) Enforce

PersonalClaw's strongest claim is that its controls are *enforced at the point of
execution*, not merely requested in a prompt. Honesty requires naming the places
where that is not (yet) literally true. These are deliberate, documented
tradeoffs — not oversights — and each is stated here in the same terms the
internal architecture uses, without softening.

This page is referenced from the public
[threat model](threat-model.md) and from `SECURITY.md`. Verified against the
codebase at the commit that introduced this file.

## 1. An agent CLI is held to the rails only for the calls it asks about

Task modes (`agent` / `ask` / `plan` / `build`) decide *which tools may run*. For
the **native runtime**, this gate is hard-enforced: `task_modes.py` is enforced
in `_guard_and_invoke` **before approval is consulted, so a Trust/YOLO
auto-approve can never bypass a task-mode restriction**
(`src/personalclaw/task_modes.py`).

An **agent CLI** (an external CLI agent driven over the Agent Client Protocol) runs
its own tools and decides itself which of its calls to ask the host about.
PersonalClaw tells every agent CLI it runs the mode in which it asks, in a chat you
watch and in unattended work alike (`acp/permission_authority.py`: `default`, or
the CLI's own most restrictive spelling of it). Each call the CLI asks about meets
the rails before anything can approve it: the task mode, the deny-list and the
screen that keeps what only you may change out of reach, the hooks, an automation's
capability class, and, in work nobody watches, the run's bounds (§16). Only then do
Trust, YOLO or a run's standing grant answer it, and in work nobody watches a call
nothing approves is refused at once, with its reason, instead of waiting for a
person who is not there.

What the CLI runs **without asking** never reaches those rails: what its own
settings let it run, and the tools it never asks about at all, which PersonalClaw
measures and declares for each CLI ([ACP parity](../agents/acp-parity.md)).
PersonalClaw learns of such a call once it has run: its card says it ran without
asking you, the audit log records it as `ungated`, and under `ask` or `plan` a call
that may have changed something stops the turn. For those calls the task mode and
the capability class reach the CLI only as framing.

The one exception is your choice for a single loop. An Unattended loop that runs on
an agent CLI can let that CLI approve its own calls instead of asking: a switch on
the loop's page, off by default, turned on only once you confirm what it does,
audited each time it changes, and offered only for a CLI whose unasked calls
PersonalClaw has measured. That loop's CLI then asks about none of its calls, so the
rails above reach them only as framing. An operator ceiling that narrows `tools`, or
says `"approval": "ask"`, takes the permission away again, and every call then
reaches the host's gate.

**What this means for you:** an agent CLI's own settings decide what it runs without
asking, so keep them as tight as you want its work to be (Claude Code's allowed
tools, for one), and leave a loop's "approve its own calls" off unless you want that
loop's CLI to answer for itself. A native agent keeps every call on the hard rail.

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
(`/api/onboarding/import`, which copies their MCP servers, skills, agents, prompts,
instructions, memories, conversations and denied commands in).
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
`agent` permission, at the tier it declares (`text`, `read` or `tools`). A lesson is memory that
every agent is handed as a rule, so `/api/lessons` needs the `memory` grant, the same as
`/api/memory`.

Your conversations are in the same class, because a message in one of them is an
instruction your agent carries out. An app may not send into one of your chats, or edit,
regenerate, resume, stop, retitle, rebind or fork one, or answer an approval in one; it may
not set the task mode of your chats, share one, carry one into your channel DM, or delete
one from your history.
It may not speak in a room (a line there is written as yours, and every member answers
it), answer an agent's question in your inbox, approve a proposal, write an inbox note in
your name, or post through the schedules' delivery door (`/api/send-message`), which
speaks as your agent. An app may hold conversations of its own: it starts one, and only
that one is its to reach. Its turns need its `agent` permission at the `tools` tier and run
under it, never under your approval switches (YOLO, Trust, Trust reads, an agent's "always
allow"): no tier approves a call, so each one that needs approval asks you, and an app never
answers an approval raised in its own conversation. Its background agent tasks are held the
same way, and what one finishes goes back to the app alone, never into a turn of your own
agent. It reaches you through a proposal, which the inbox labels with the app's name, and
`/api/reveal` opens only a file in its own data folder. The exception is the relay you install
for approvals: an app that declares `/api/approvals` (the menu-bar companion does) can approve
or reject your pending approvals one at a time, and the gateway cannot tell whether an answer it
relays was yours.

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

What reached you is yours the same way. An app reads the notifications it raised and the
ones about a conversation it started, in `GET /api/notifications` and on its websocket, and
nothing else in your log: not what your automations, loops, inbox, channels or other apps
raised, and not your notification settings or rules. Its session list says nothing about
whether your tool calls run without asking.

Each app's provider is that app's. A provider's settings say where it connects and which of
its credentials it connects with, so an app that could change another app's could point it
at a server of its choosing, and that app would send its own key there. Under
`/api/providers` an app reaches its own provider and no other: another app's settings, its
instances (adding, changing, removing or testing one) and its availability check are
refused, and so are their reads, since the settings say where the provider connects even
with the keys masked. Its list of providers holds only its own. The MCP Tool Servers card
(`/api/providers/mcp-tools`) is not an ordinary provider: its instances are every MCP server
the gateway launches, so no app reaches it, reads included, as with `/api/mcp`. An app's own
settings resolve only keys stored under that app, so an app pointing its own provider
somewhere sends only its own key there.

Your models are yours. A model provider says where your model calls go and which of your
keys goes with them, and a binding says which model each use runs on. Your chats and agents
send your chat model what you say, and its answers decide the tool calls your agent makes.
So under `/api/model-providers` and `/api/models` an app may not add, change, test or remove
a model provider, bind a use to a model, change the routing table or a use's settings, set
or clear your Hugging Face token, download, install, delete or unload a model, or re-embed
what you stored. It may not read them either: your providers, your bindings, your routing
table and your usage are yours. The onboarding wizard's one-click bind of a local model
(`/api/onboarding/local-model/bind`) is refused for the same reason: it adds a model provider
and can move your chats onto it. The rest of your first-run setup (`/api/onboarding`) is yours too.
An app may not read it, since it names the model your chats are bound to, move its progress, or
have the gateway look for model servers on this machine or sweep your network for them.

Every write route in these families, and every read in your conversation families, your
notification log, your providers, your models and your first-run setup, has to be declared one
way or the other: one that is not is refused to every app until someone declares it, and
`tests/test_security_posture_rail.py` fails the build on it.

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

pip itself runs in the child allowlist (`sandbox.py::build_child_env` with `installer="pip"`),
not in the gateway's environment. That matters because pip runs each package's build code with
whatever pip has, and the gateway's environment holds every secret saved in PersonalClaw. pip gets
`PATH`, the home, locale, the proxy and certificate settings, and its own `PIP_*` settings
except the four that would move the install (`PIP_TARGET`, `PIP_PREFIX`, `PIP_ROOT`,
`PIP_USER`). A login in any of those values (`http://ada:pw@proxy:3128`, an index URL with a
token) is taken out. An install that then fails because the proxy or index wanted it says which
setting lost its login. Adding that name under Settings → Security → Child environment
passthrough passes it as it is, to every process PersonalClaw starts.

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
  a file, it replied with a Python snippet for you to run, and nothing was written. So no
  loop is started on it, nor a workflow run whose steps work with tools, nor a subagent sent
  to read or change things: each is refused before it starts, naming the model and where to
  choose one that uses tools, and Settings → Models does not bind it for Loops.
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
in an MCP server's `env` and `headers`, a credential in an MCP server's arguments or URL that
PersonalClaw can place (below), and the webhook token (`hooks.webhook_token`, which
`POST /api/hooks/agent` checks) are kept in the credential store: the OS keychain, or the
`.env` file in the PersonalClaw home (`~/.personalclaw/.env` unless `PERSONALCLAW_HOME` names
another) at mode 0600. The file that configures them holds a `{{secret:…}}`
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
- **A token in an MCP server's arguments or URL that PersonalClaw cannot place.** A credential in
  the arguments or the URL is kept in the credential store, `mcp.json` holding a reference in its
  place, when where it sits says what it is: the value of a flag named for one (`--api-key …`,
  `--api-token=…`), a header's value (`--header "Authorization: …"`), the login in an address
  (`https://user:pw@…`), a query value named for one or shaped like one (`?token=…`), a part of an
  address's path shaped like a token, or an argument in a format only credentials have (a provider
  key). A token standing alone as an argument, with no flag naming it and in no format PersonalClaw
  knows, stays in `mcp.json` as written and travels with an export: it cannot be told from a
  package or project name that looks the same. So does anything in the command itself. Every page
  that shows a server masks all of these with one mask — the Tools page's edit form, the MCP Tool
  Servers card in Settings → Providers, the import list and the question Allow asks — and the list
  of configured servers leaves them out; a save keeps a masked value as it was, or replaces it with
  what you type over the mask. Put such a token after a flag that names it, in an environment
  variable or in a header instead. On a machine whose store lacks a stored value (a restore onto
  another machine), the server does not start, and the Tools page says which value to type in.
- **A sync's older copies.** Each sync sends your records to the store you chose as one whole
  copy (encrypted for a bucket or a shared folder), and a token in a file a sync carries — one in
  an MCP server's arguments or URL that PersonalClaw cannot place (above), or one sent before
  PersonalClaw kept such tokens in the credential store — is in every copy sent while it was there.
  On a transport that removes old copies (Folder Sync, S3 Sync), the store keeps this machine's
  newest copy, and the one before it until a newer copy has stood 15 minutes
  (`durability/published.py`): the first sync after you remove the token sends a copy without it,
  and a sync at least 15 minutes after that removes the last copy with it — within about 40
  minutes at the default 15-minute window, and within twice the window when you set it longer.
  Git Sync and Rsync Sync keep every copy they are sent, and the service a synced folder goes
  through, or a bucket with versioning on, may keep a removed file in its own history.
- **What work a secret was filled into did with it.** What an automation's action or a workflow
  step prints, returns or fails with is masked of each `{{secret:NAME}}` value filled into it,
  however short, before its run history, its step's output and ledger, the note that reports it
  or Run now's answer keeps or shows it, and so is what the work writes itself as it runs: the
  `command_refused` row of a command refused before it ran, the `egress_fetch` row of a request,
  and the gateway log (`filled_secrets`). A value the work changed on the way (encoded, cut short,
  split across lines) is not recognised, and what the work does with a value is its own: a file it
  writes, a message it sends or a request it makes carries what it put there. Records written
  before this was masked are rewritten when the gateway starts, for a value of eight characters
  or more: a run's when its definition names a secret, and every automation's history and last
  error. A note, an audit row or a log line written before keeps what it said.
- **Claude Code's own config.** Putting an MCP server into Claude Code's scope
  (`POST /api/mcp/apply` with `ccGlobal`) writes it into Claude Code's `.claude.json` (in your
  home directory, or in `$CLAUDE_CONFIG_DIR` when that is set) with its values, because Claude
  Code reads only its own file. That copy is outside PersonalClaw's home, snapshots and exports,
  under Claude Code's own file permissions. PersonalClaw writes no other copy: a session restart
  or an MCP sync leaves `~/.mcp.json` alone.

**What this means for you:** after upgrading, treat snapshots and exports made before it as
holding your tokens. Delete them, or change any token that has left your hands in one.

## 7. An app's own code runs as you

Everything §2 describes bounds an app's **token**: what the app may reach by asking the
gateway. None of it bounds the app's **code**. An app can bring six kinds, and all six
run under your own account:

- provider modules, imported into the gateway's own process
  (`providers/loader.py::_load_ext_module`), or run as a child of it when the manifest says
  `execution: sidecar`, with the engine packages (`dependencies.sidecarDependencies`) that
  **Install engine** puts in that child's own Python environment, `apps/<app>/venv`
  (`local_models/sidecar.py::SidecarInstall`);
- a backend, started as a process on this machine (`apps/backend_runtime.py`);
- the MCP servers in its manifest's `mcpServers`, each a command the gateway launches, once you
  allow it on the Tools page, with the child allowlist and the `env` the manifest declares for it,
  whose secret references
  resolve only the app's own secrets (`apps/mcp_bridge.py`, `mcp_discovery.py::stdio_spawn_env`,
  `config/secret_refs.py`);
- setup hooks (`setup.onInstall` and the rest), shell commands run at install, update,
  enable, disable and uninstall (`apps/app_manager.py::_run_hook`);
- CLI steps (`cli.setup`, `cli.doctor`), imported and run when you run `personalclaw setup`
  or `personalclaw doctor` (`app_cli.py`);
- a connector pack's source parsers (`sources`), scripts the gateway runs on what the pack's
  sources fetch, fenced off from the network but not from your files
  (`knowledge_providers/pack_parse.py`).

The Python packages an app installs load into the gateway's process too (§3). Its engine
packages do not: they install only when you choose Install engine, into the app's own
environment, and run in its child process. A sidecar is a crash boundary, not a sandbox, so
that child has your files and your network as well.

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
is not available refuses to launch instead of falling back to the host. Everything the
gateway starts for an app begins from one allowlist (`sandbox.py::build_child_env`): the pip
that installs its packages, the venv and pip of its engine, the npm that installs an ACP
adapter, its setup hooks, backend, worker, sidecar and MCP servers. None of them inherits a
credential you did not pass through by name in `sandbox.env_passthrough`, and a proxy address
reaches them without its login. A backend and an MCP server both run under the
resource-ceiling shim (`sandbox.py::spawn_shim_argv`).

**What is not enforced:** anything about the code itself. A backend that names no
sandbox, a provider module, an MCP server, a setup hook and a CLI step have your files and
your network. A provider module runs inside the gateway, and a CLI step inside `personalclaw
setup` or `doctor`, so both see the environment of the process they run in. A child that a
provider module starts inherits it too unless the provider passes the child allowlist
(`personalclaw.sdk.util.child_process_env`); the first-party apps that start a program built
by someone else (Piper's synthesis, the skills search's `npx`) do. The ones that start your own
authenticated tools with a fixed command (`gh`, `glab`, a sync app's `git` or `rsync` to your
remote, an ops runbook's action) keep it, because those tools sign in from it. An ACP agent an
app registers starts from the same allowlist, plus the variables its app declares for it and
the session it answers for (`acp/transport.py`). An agent CLI that signs in to its model
provider from an environment variable, such as an API key, gets that variable only if you pass
its name through in `sandbox.env_passthrough`; a sign-in the CLI keeps in its own config folder
needs nothing passed. A source parser has your files and no network.

**What the consent surface tells you:** the install dialog reads `apps/disclosure.describe`
and has a row titled *What it runs on this machine*. It leads with the gateway's own
sentence saying which of the app's code runs as you, that it can read and change your
files, your PersonalClaw settings included, and that the permissions listed beside it
limit what the app asks the gateway for, not what that code does
(`apps/disclosure._runs_as_you`). Below it the row names every kind: the server process it
starts, and the sandbox tier it runs in when it names one; each provider module, with the
entry point the gateway loads and whether it runs inside the gateway or as a child of it;
the engine packages Install engine would put in its own environment, verbatim;
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

## 8. Signing in to a remote MCP server needs a browser on this machine, or HTTPS

A remote MCP server that signs in with OAuth sends your browser to its authorization server, which
sends it back to PersonalClaw. The MCP spec allows only a loopback or an HTTPS address for that, so
the sign-in works from a browser on the machine PersonalClaw runs on (it comes back to
`http://127.0.0.1:<port>`), or from anywhere when the dashboard is served over HTTPS. A dashboard
opened from another machine over plain `http://` is refused with a sentence that says so
(`mcp_sign_in_needs_local_address`).

- **Signing out does not revoke the grant.** Sign out deletes the tokens PersonalClaw holds for the
  server. Your account at the authorization server may still list PersonalClaw as allowed until you
  remove it there.
- **An authorization server that does not advertise PKCE with S256 is refused.** The MCP spec
  requires it, and PersonalClaw signs in no other way.
- **No client ID metadata document.** PersonalClaw has no HTTPS address of its own to publish one
  at, so an authorization server that offers neither dynamic registration nor another way needs the
  client ID of an app you registered there yourself.
- **A sign-in in progress lives in the gateway's memory.** A restart while you are on the
  authorization server's page drops it, and you start again from the Tools page.

## 9. Your Allow for an MCP server covers what it runs, not what that loads

A server runs only once you allowed its definition (`mcp_grants.py`): how it is reached, its
command, arguments and folder, the names of the variables it sets, and a remote server's address
and header names. That is what you are shown, so that is what a yes is to.

- **The code behind the command is not in it.** A command that fetches code when it starts runs
  whatever that fetch returns: `npx some-package` downloads the package's latest version each
  time, so your yes to the command is not a yes to one version of the code. Pin a version, or point
  the command at a program on your disk. An app update keeps the yes for a server whose definition
  it leaves unchanged, since the Store's update consent is where you are asked about the new code.
- **Values are not in it.** Replacing a secret under the same name asks nothing: nobody is shown
  a secret, so no yes could be to one.
- **Another tool's own servers are its business.** PersonalClaw never hands Claude Code a server
  that waits (`/api/mcp/apply`'s `ccGlobal`), but a server you configure in Claude Code or Codex
  runs there under that tool's rules, whatever PersonalClaw's list says.
- **So is what the folder it works in names.** An agent CLI also reads the settings of the folder
  it is pointed at, such as a repository's own: Claude Code's `.claude/settings.json`,
  `.claude/settings.local.json` and `.mcp.json`, Codex's `.codex/` in a project you trust, Gemini
  CLI's `.gemini/settings.json`, kiro-cli's `.kiro/`. The rules, hooks and servers there apply under
  the CLI's rules, and Claude Code shows no trust prompt for them when PersonalClaw starts it.
  Install consent says which programs an app starts with a folder's settings. Claude Code with
  *Isolated Claude settings* on loads none of them.
- **On Linux, a missing `mcp.json` can be created by the agent's shell.** The read-only bind needs
  a file to bind, so a home with no `mcp.json` yet leaves the path writable to the agent's shell,
  as it does for `config.json`. A definition written there waits for your Allow like any other.

## 10. A process running as you can answer as you

Only you answer an approval (`approval_answer`; [security.md](../architecture/security.md#who-answers-an-approval-approval_answerpy)).
An app's token, an agent's tool, a trigger, a workflow run and a control-bridge client are
refused, and so is the party that asked. What the gateway checks is the credential a request
presents. It cannot see past that credential to the process holding it.

- **Your sign-in can be minted by any process running as you.** `personalclaw token` prints a link
  that signs in as you. It signs the link with `session_key` in your PersonalClaw home, a file every
  process under your account can read. The agent's file tools and its shell's screen refuse that
  file in the home in use and in the default one (`security.HOME_SECRET_FILE_BASENAMES`; what the
  shell's screen cannot see is §13). Nothing stops a shell from running
  `personalclaw token`, which reads the key inside its own process. So could a local program that
  talks to the control bridge, and so could an app's code (§7).
- **What that credential then answers as is you.** The refusals above stop every path that presents
  what it is: an app's token, the internal secret, a bridge bearer, a trigger. They do not stop a
  process that takes your own credential.
- **What limits it today:** an agent has to leave its tools' contract to do this. It must run the
  CLI in a shell and then call the dashboard's API with the link, which is a deliberate act. It is
  not a door left open for a model that follows its tools' descriptions.

## 11. What reaches a model is masked by shape, and an agent CLI's own tools are outside it

Every tool answer, every stored text a prompt is built from, a spawned agent's task and an
attached file's text are masked before an agent's model is handed them
([threat model §5](threat-model.md#5-system--persisted--exported-state)). The mask is the one your
views show, and it has these edges:

- **It finds a secret by its shape or its field's name.** A provider key, a token, a URL's login
  or a webhook is masked wherever it appears; a password written into a note as a plain word, or a
  code in a sentence, reaches the model as written. The one exception is `bash`: it masks every
  value it handed the command it ran, whatever its shape: each `{{secret:NAME}}` value it filled
  in, however short, and the credentials the gateway's environment holds, one you stored in
  Settings → Secrets whatever its length and one only its variable's name calls a credential from
  eight characters, since a value that short there (`none`, `1`) is a setting more often than a
  secret. It masks a value as written and as a JSON string writes it, not one the command changed
  (encoded, cut short, split), and a short value masks every place its text appears, ordinary
  words included.
- **An ACP agent CLI's own tools are outside it.** Claude Code's `Read` or `Bash`, and any other
  CLI's own file and shell tools, read inside the CLI and send what they read to that CLI's
  provider without passing PersonalClaw. Only PersonalClaw's own tools, which the CLI reaches over
  MCP, are masked for it. Such a CLI also has no way to fill in a `{{secret:NAME}}` itself.
- **A one-shot model call is scanned, not masked.** A workflow's infer, judge and visualize
  steps, a knowledge item's digest and a sync-conflict merge you review have no tools to read
  with. They pass the outbound scan at the model-call guard instead: at `guardrails.scan_mode`'s
  default, `redact`, a credential in the prompt is replaced before it leaves; `block` refuses the
  call and `warn` sends it. A model that runs on this machine keeps `warn` whatever the setting
  says, so its prompt is scanned and then sent as written. The guard asks that of the model each
  call is for (`llm.registry.served_on_this_machine`): a model runs here when it runs inside the
  gateway (the bundled offline model), or when a model server that runs its models where it is
  (Ollama, vLLM) is at `localhost`, a loopback address or `0.0.0.0` and does not pass that model on.
  A model your Ollama answers from Ollama's cloud (its tag says `cloud`, or the server names the
  host it answers it from) is passed on, so its prompt is scanned like a hosted provider's, as is
  every model of a server on another machine and of an OpenAI-compatible endpoint, which can pass
  a prompt on to a cloud service from any address.
  Masking these would put a marker
  into answers that are written back, such as a merge you accept. PersonalClaw's own chores (a
  title, follow-ups, memory consolidation) are one-shot calls too, and they are masked as well as
  scanned: each prompt is composed from stored text that no person typed this turn
  (`chores.run_chore`).
- **Pixels are not masked.** An image you attach, and a screen frame for computer use, go to a
  vision model as they are, and a key visible in them goes with them.
- **What you type this turn goes as typed.** A key pasted into a message reaches the model; it is
  masked when that turn is read back later (history, a compaction, a search).
- **A hidden value can only stay where it is.** The agent's file tools refuse a change that would
  move, copy or rewrite a value it was shown as a marker. A tool outside PersonalClaw's own stores
  (an MCP server that writes a file on a remote service) cannot put a value back, so a marker the
  agent passes it arrives as text.
- **An automation's prompt keeps a reference as its name.** An agent task, a saved prompt's
  variables and a workflow's stage, infer and visualize steps hand the model `{{secret:NAME}}`,
  not the value, and an agent uses it in a command. A prompt written to hand the model the value
  itself no longer does.

**What this means for you:** store a credential in Settings → Secrets and refer to it as
`{{secret:NAME}}` where it is used, rather than writing it into a note, a prompt or a file an agent
will read. Work that needs a stored credential inside a command runs on the native runtime, whose
`bash` fills the reference in.

## 12. A git driver a repository assigns to its own files still runs

PersonalClaw runs git inside repositories an agent can write: the workspace's, a loop's worktree,
the state history, the updater's checkout. Every such git runs with settings that stop the
repository's own configuration from running a program: hooks, a file-system monitor, an external
diff, its ssh command and credential helpers, and the rest
([security.md](../architecture/security.md#sandbox-sandboxpy)). A git older than 2.12 ignores some
of those settings, so PersonalClaw refuses to run it and says which version it needs. Two kinds of
program are outside what a setting can reach:

- **A filter or merge driver.** A repository can define a driver (`filter.<name>.clean`, `.smudge`
  or `.process`, `merge.<name>.driver`) and assign it to its own files in `.gitattributes` or
  `.git/info/attributes`. Git names the driver by the repository's own word, so no fixed setting
  stops every one. When PersonalClaw's git adds, checks out, merges or reports the status of such a
  file, the driver runs.
- **The command a remote host is asked to run.** Over ssh, git asks the server to run
  `git-upload-pack` or `git-receive-pack`, or the command a repository set for that remote
  (`remote.<name>.uploadpack` or `.receivepack`). The server runs it as the account the owner's key
  signs in to.

**What limits it today:** a driver runs with the child environment, so none of the gateway's
secrets reach it. A git host lets that account run git's own commands and nothing else.

**What this means for you:** an agent's shell that gets past the screen of git's settings (§22: a
script, or a program that writes them itself) can define a driver in the workspace's repository,
and the program it names runs when PersonalClaw's git next touches a file it is assigned to. A
key that signs in to a machine where it has a full shell account can run more than git there.

## 13. The agent's shell is screened, not fenced, from your credential files

Before the agent's shell runs a command, PersonalClaw reads it and refuses it
(`security.is_sensitive_bash_command`) when it:

- **names a file only its owner reads**, whatever it does with it: PersonalClaw's credential store
  (`.env`) and its session key, session table, `auth/`, `credentials/` and governance files, in the
  home in use and in the default `~/.personalclaw`; its security-log key, loopback secret and
  telemetry salt, wherever they sit; and the sign-in another tool keeps: Codex's, Claude Code's,
  Gemini CLI's, the GitHub and GitLab CLIs', Hugging Face's, and any an agent or provider app
  declares from its vendor's documentation, each
  found where its tool looks for it (a folder `CODEX_HOME`, `GH_CONFIG_DIR` or the tool's other
  variable moved included). The path is read the way the shell would find it: from the folder the
  command runs in and from every folder a `cd` in it moves to, through a link, a glob or a brace
  list, with the homes written as a shell or a one-line script writes them (`$PERSONALCLAW_HOME`,
  `~`, `process.env.HOME + '/…'`).
- **returns a credential folder under your home**, named from there: `~/.ssh`, `~/.aws`, `~/.gnupg`
  and the rest. A command that only uses one, such as `ssh -i ~/.ssh/key`, runs.
- **shows what one of those folders holds**: it names the folder itself, whatever it does with it
  (`ls ~/.aws`, `find ~/.ssh`, a `cd` into it), or a glob the shell expands to the folder or into
  it (`ls ~/.a*`, `ls ~/.ssh/*`). A folder that only shares a name with one (`~/src/ssh-helper`)
  is not one.
- **asks a signed-in tool to print its credential**: token-printing auth commands from the GitHub,
  GitLab, Google Cloud, AWS, Azure and macOS credential CLIs are refused. Their ordinary status,
  identity and account-listing commands still run.

The agent's file tools and the dashboard refuse all of these files. The shell's screen is defence in
depth, not a fence:

- **A command that reads a whole folder holding one reaches it.** `grep -r … ~` or `tar c ~` names
  your home, not the file in it.
- **A path built while the command runs is not seen.** A variable the command sets, a command
  substitution, or strings a script joins as it runs never spell the file in the text.
- **A credential-printing command the baseline does not name is not inferred from its output.**
  The screen refuses known command forms before they run; it does not run an unknown command and
  decide afterward whether its output was a credential.
- **The OS sandbox hides the credential store at its `cc` and `strict` levels only.** The native
  agent's shell runs at the standard level, which hides no single file, and where the operating
  system offers no sandbox none runs. An agent CLI's own sign-in stays readable inside its sandbox,
  because the CLI reads it to sign in.
- **An agent CLI's own shell and file tools are screened only when the CLI asks first.** One it
  runs without asking reads inside the CLI (§1, §11).

**What this means for you:** keep credentials in Settings → Secrets rather than in files an agent
works on: a command gets one as `{{secret:NAME}}` and the agent never holds it. With **Store
credentials in the OS keychain** on (Settings → Security → Credential storage), the store keeps
them in the keychain rather than in `.env`. Treat an agent's shell as able to read what your own
account can when it sets out to.

## 14. A shell command read as a read is read from its text

Trust reads, Ask and Plan mode run a shell command without asking only when PersonalClaw reads it as
a read (`command_effects`): every program in it is one it knows, run in a form that only reads, with
every option and subcommand it uses known to read. Anything else is not a read: an unknown program,
option or subcommand, a redirect into a file, an expansion (`$…`, a backtick), a subshell or a group.
Sending stderr to `/dev/null`, joining it to stdout (`2>&1`) and a leading `cd <folder> &&` change
nothing and are allowed. The approval prompt shows what the reading established: "Reads only",
"Writes files", "Uses the network", "Runs a command" for a part it could not vouch for, and the
risk **Not checked** for such a command rather than Safe or Destructive.

What the reading does not see:

- **A program reads its own configuration.** `git status` and `git log` honour the repository's
  `.git/config`, which can name a program git runs (an fsmonitor, a diff driver): reading a
  repository that someone else prepared runs what it configures, as any git you run there would.
- **The shell is yours.** The command runs in a login shell, so a function or an alias your
  profile defines under a program's name runs instead of the program.

**What this means for you:** Trust reads is for reading your own files and repositories. Turn it
off for a chat that works in a folder or a repository you did not create.

## 15. The shell denylist reads a command's text

Settings → Security → Shell denylist refuses a command whose text matches one of its patterns,
on every path PersonalClaw runs a command for the agent or an automation: the agent's shell (and
Tools → Try it and a script's tool call, which reach it), a command an agent CLI asks to run, a
loop's or a workflow's check, a workflow step or teardown, a bash action however it started, and an
app's setup hook. A refused command is refused before anyone is asked to approve it, and a run
nobody watches says so where you look: a loop pauses with the rule as its question, a workflow's
gate fails with it, and a trigger's run history records it.

What the patterns do not see:

- **What a command runs without naming it.** A script file (`python3 probe.py`), a library an
  interpreter imports, or a command a script builds as it runs is not in the text a pattern reads.
- **An agent CLI's own tools when the CLI does not ask first.** The host screens what a CLI asks
  it to run; a command the CLI runs without asking stays inside the CLI (§1, §11).

**What this means for you:** a pattern keeps the commands it names from running. To keep a tool
away from the agent entirely, do not install it or give it credentials where PersonalClaw runs,
and keep approvals on for work that might reach for it.

## 16. Where a shell command or an app's program reaches is read from its command line

The agent's shell, and every program an app's code starts, is held to two bounds read from the
command it runs (`run_bounds`, `apps/launch_egress.py`):

- **The network.** A command that reaches the network is checked by the hosts it names: a URL's
  host, ssh's `user@host`, and a package manager's registry when its command line names no other
  (`pip`, `uv`, `npm`, `npx`, `yarn` and the rest). Only a host on Allowed hosts (Settings → Security
  → Network egress), and not on Denied hosts, goes ahead without a person saying yes. Any other host,
  or a command that reaches one it does not name (a git remote by name, a URL in a variable, a
  proxy or a registry set in a variable for it), is refused in an unattended run (a loop, a
  schedule, a subagent nobody watches) and asked about in an attended one, with the host on the
  card, whatever Trust, YOLO or a standing grant would have said. In a run whose egress tier is not
  `all`, a command that goes ahead still runs with no network at all, whatever it names (§18).
  A program an app's code starts may also reach a host its manifest declares for that program, which
  its install review names; every such launch leaves an egress row in the audit log, and one toward
  any other host is stopped before it starts.
- **Writes.** An unattended run writes only inside the folder it works in, the folders it was given
  and its own temporary folder (its shell's `TMPDIR`, where `mktemp -d` makes one). A native file
  write, or a shell command's redirect, `-o`, `mkdir`, `cp`, `mv` or `rm` target outside them is
  refused.

What the reading does not see:

- **A program's own requests.** `npx` fetches the package it is told to, and that package's code
  reaches whatever it reaches; a tool reads its own configuration (`.npmrc`, `pip.conf`, `~/.ssh/config`)
  for where to go. A script the command runs (`python probe.py`) reaches and writes what it likes.
- **A command it cannot read.** A command substitution, a subshell, a background job, or a program
  the reading does not know (a script, `make`) establishes nothing it reaches or writes, so neither
  bound holds it: the run's approval mode decides it, as it decides any other call.
- **An agent CLI that does not ask.** An agent CLI an unattended run starts runs its own tools without
  asking PersonalClaw, so they are not read (§1, §11).
- **An app's own code.** An app's provider runs inside the gateway, so its own Python reaches the
  network without starting a program, and a launch it hands to another thread as a bare function
  (`asyncio.to_thread(subprocess.run, …)`) has none of the app's code on that thread to say whose it
  is, so it is read as PersonalClaw's own. Install consent, the security scan and the review of what
  an app's code does are what hold an app's code itself.

**What this means for you:** list on Allowed hosts the hosts you want an unattended run to reach on
its own, such as your package registry, and the hosts you set an app to reach: Git Sync's
repository (its clone and connection check name it) and Rsync Sync's server are named on the
command lines those apps start, so each is stopped, with a sentence saying how to allow it, until
its host is on that list. The bounds stop a command that says where it goes; they are not a
network fence around one that does not (in a run whose egress tier is not `all`, the OS sandbox is:
§18).

## 17. The content scan reads what comes in from outside, but not all of it

Every file you upload is scanned before anything is made from it, sent in one request or in parts
(`uploads/content_scan.py`): a chat attachment, a file uploaded to a folder, a Knowledge file, a
file dropped into a workflow run, an artifact's new bytes, a pinned screen frame, a project archive
and a backup, a file you drop in the memory vault's `raw/` folder when a sync takes it into
Knowledge, and a file in a folder Knowledge watches when a poll takes it in (one either refuses
becomes a failed Knowledge item that says why). The scan reads a file by its bytes, whatever its
name says it is, with the scanner's destructive-script rules and its prose rules (injection
phrases, invisible characters), and refuses a file they call dangerous: an SVG drawing is text and
is read; an ordinary picture, recording, video or archive is binary and is not. A scan that could
not run refuses the upload as well.

Text from outside that Knowledge keeps with no file is read by the same rules before it is stored
(`knowledge/text_items.py`): a watched feed's or page's entries, what an app's source hands in (a
repository's files, a shared store's items), the page or paper a bookmark fetches, a web watch's
new items, a note an app, the agent or a workflow writes or edits, an artifact one of them saves or
edits whose text Knowledge's search keeps (a page, a document, markdown, text, JSON or CSV: its
name, its description and its words, `knowledge/artifact_ingest.py`), and an edit made to a page of
the knowledge vault, which is a file any program on the machine can write. So is the text of a file
an artifact points at, before Knowledge's copy of the artifact keeps it, whoever saved the
artifact. What the scan refuses, or could not check, is not kept: a source's entry becomes a failed
item that says why and keeps no text (one the scan could not check is read again when the source
next offers it), an app, the agent's tool or a workflow's step is told and nothing is written, the
vault leaves the note as it was and says why in the page, and Knowledge's copy of an artifact that
points at a file keeps no text and says why (the artifact is the file, as it was). Each refusal is
a row in the security event log, and the gateway log names the source and the item. A source's
text is read when it is new or when it changed, not each time the source offers it again. A note
you write yourself in Knowledge, and an artifact you save or edit in Artifacts, is kept as you
wrote it, as your chat messages are: the scan is for text from someone else.

What a reader makes of an upload is scanned too, by the same rules, before a model is handed it or
it is kept for one: the text of a chat or an Inbox attachment, of a Knowledge document or code
file, and of a document the agent opens with `read_file`. A PDF's or an Office document's text,
which sits in compressed parts of the file, and text beside a stray NUL byte are read there. Text
that fails the scan, or that it could not check, is withheld: the chat's preview, the Knowledge
item's status or the agent's tool says why, and the model is told it was not given the text. No
file is read as text that is not text: a zip or a program is not handed to a model as text. And
the text a model is handed of an attachment, a referenced Knowledge item or a document reaches it
fenced as quoted data, which the agent's rules say is never an instruction.

What the scan does not see:

- **The middle of a large file, or of a long text.** A file, or a text a reader makes, of up to
  512 KB is read whole; a larger one only as its first and its last 256 KB. A chat attachment
  hands the model no more of a long text than the first 256 KB the scan read; a Knowledge item
  and a document read with `read_file` keep the rest as well.
- **Text in a binary window of a file the agent reads itself.** A 256 KB window of a file that
  holds a NUL byte is read as binary and is not scanned, because random runs of binary bytes read
  as false alarms. A text file kept in a folder, which the agent opens with `read_file` as it opens
  any file in your folders, is not scanned again when it is read, so text beside a stray NUL byte
  in it is not checked.
- **What a model reads off a picture, a recording or a scanned page.** OCR, an image's
  description and a transcript are a model's reading of pixels or sound, and are not scanned; they
  are fenced as data like any attachment's text.
- **An archive's files.** An archive is scanned as the file it is: the files a project or backup
  import writes out of it are not scanned one by one, nor the artifacts and the library entries a
  backup brings.
- **An instruction written as prose.** The scan refuses what the scanner calls dangerous. An
  instruction to the model written as ordinary prose is only a warning there, and passes; the
  fence around a file's text is what tells the model it is not an instruction.
- **Where an item came from.** The scan reads an item's title and its text. Its link, a source's
  name for it and a file's name are kept as where it came from, and a refused item is named by
  them.
- **Text an earlier version took in.** An entry a source took in, or a note or an artifact an app
  or the agent wrote, with an earlier version that did not scan it is left as it was, and so is
  Knowledge's copy of that artifact. A feed's or a page's entry is taken once, when it is first
  seen, so it is not read again; an app's source's item is read again when the source says it
  changed.
- **What is written as you.** A note you write in Knowledge is not scanned, and neither is one a
  program writes with your session (§10), an artifact you save or edit in Artifacts, or a chat you
  share as one. An artifact that points at a file is the exception: its text is the file's, which
  any program can write, so Knowledge's copy of it is scanned whoever saved it.
- **An artifact Knowledge does not keep.** A widget's, a component's, a drawing's or an
  infographic's code, and a generated document, sheet, deck or PDF, are not read when the agent or
  an app saves one: Knowledge's search keeps none of their text. Bytes uploaded into an artifact
  are scanned as any upload is.

**What this means for you:** the scan keeps a destructive script or hidden reversed text out of
your chats and your library, whether it sits in a text file, in a document's text, in a feed or a
page you watch, or in a note or an artifact an app or the agent writes; it is not a reading of
every document. Treat a file, a feed or a page from someone else as you would their message.

## 18. A run's egress tier holds where its requests ask the guard

A run's safety profile says how far its requests may reach, its egress tier: nowhere (`off`), only
the hosts on Allowed hosts (`listed`), those and the common package registries (`registry`), or any
public host (`all`). The operator ceiling (`governance/ceiling.json`) can narrow it for every run
on the machine, and is what sets it today. The tier is read for every request the egress guard is
asked about (`net.policy.egress_policy_for_run`, from `net.guard.evaluate`): a page the agent
fetches or renders, a search, an image or a video a provider's answer points at, a webhook an app
sends, the browser's navigations and every other request an app makes through the SDK.

An automation's action is held to it too. A trigger's fire, its Run now, a webhook's or a view's
fire, a hook and a workflow step hold what their action reaches to the run the action is dispatched
for (`net.policy.egress_held_to`, under the identity an unattended run of it is judged by), so a
webhook, an A2A call or a fetch an automation sends keeps to the tier as a chat's request does; an
agent the action starts is held to its own run. So is a remote MCP server: its connection's start,
a turn's listing of its tools and every tool call ask the guard about the server's URL first, for
the run the call is made for (`net.policy.MCP_SERVER`). A run whose tier is `off` reaches no server
and is offered none of a server's tools it would have to reach to list, a `listed` run reaches one
only when its host is on Allowed hosts, and your Denied hosts and the cloud metadata service are
refused for every call; a server on your own machine or network stays reachable otherwise, since
you configured it. The hosts the agent's shell and the programs an app's code starts name are held
to the tier as well (§16).

In a run whose tier is `off` each of those requests is refused before its host is looked up, the
agent is told "egress is off for this run", and the refusal is in the audit log. A request made for
no run (your own action in the app, such as a provider's Test or the Tools page's look at a server,
or a background job) keeps to your Network egress settings alone.

A command the run starts is held to the tier where it is launched. It is a program of its own, and
nothing it reaches asks the guard, so the OS sandbox it runs in (`sandbox.wrap_argv`) is what holds
it: a bash or a script action, a loop's or a workflow's check, a workflow's setup and teardown
steps, an effect's teardown, the agent's shell, and a program an app starts in the host sandbox
(`personalclaw.sdk.util.sandbox_wrap_argv`). A sandbox can take a program's network away but
cannot keep it to a list of hosts, so only a run whose tier is `all` launches its commands with the
network. Under `off`, and under `listed` and `registry` as well, a command runs with no network at
all, this machine's own services included, even a host on Allowed hosts: on Linux in a network
namespace of its own with nothing in it, on macOS under a profile that denies every network
operation, a local socket's included. Each such launch is an `egress_launch` row in the audit log,
and a command that fails there says, after its own error, that it ran with no network and why.
Where the sandbox cannot take the network away (it is set to off, or the machine offers none that
can, such as a Linux host or a container that refuses unprivileged user and network namespaces),
the command is not run: it is refused in a sentence that says so and where the tier is set, with a
`command_refused` row in the audit log. A command started for no run keeps the network.

What the tier does not reach:

- **A program a command can ask to reach the network for it.** The command has no network, but a
  program running outside its sandbox may, and can be asked over its own channel: on Linux, a
  container engine's socket, or the system's name resolver where it answers over a local socket (a
  name the command looks up is then still sent out as a query); on either system, a browser asked
  to open a page. Do not give a run whose commands should reach nothing such a program to talk to.
- **An agent CLI's own process.** An agent CLI reaches its model itself, as PersonalClaw's own
  agent does, so the run it works for does not take its network away. A CLI's built-in web search or
  fetch, and a command it runs without asking PersonalClaw, run inside the CLI and reach what they
  reach (§11); the tools PersonalClaw serves it are held to the tier.
- **A request made on a thread its code starts for itself.** Work handed to PersonalClaw's own
  worker threads carries the run (the gateway's `run_in_executor` pool and `asyncio.to_thread`
  alike), but a thread code starts by hand, or a pool of its own, does not: a request an app makes
  through the SDK from one is held to your Network egress settings alone.
- **An app's own HTTP client, and what an MCP server reaches itself.** A request an app makes
  without the SDK (§2) asks no guard. A remote MCP server's connection is asked about the server's
  own host, the one host it reaches, but the server then reaches whatever it reaches, as a stdio
  server, a program on this machine, does (§9). Once a connection is open (your own look on the
  Tools page opened it, or a run its tier lets reach the server), any run is shown the server's tools
  and only its calls are refused. A sign-in's renewal, which the connection sends on its own, keeps
  to your settings alone, and the connection looks the server's name up again for itself, so it is
  not held to the address the guard checked.
- **What a page in the browser loads on its own.** Its images, scripts and frames are not asked of
  the guard; only the navigation is (`net.policy.BROWSE`), and so is a redirect the page takes,
  judged for the run that opened it.

**What this means for you:** a tier set in the ceiling holds for everything the agent, your
automations and your apps reach through PersonalClaw's own requests, for every call to a remote
MCP server, and for every command a run starts. Under `off`, `listed` or `registry` a run's
commands reach no network at all: a package install, a `curl`, or a test suite that starts a server
of its own and talks to it does not work there, while the agent's own fetch and search keep to
what the tier allows. Set `all` for runs whose commands need the network. The tier is not a fence
around the machine: an agent CLI and what it runs itself, an app's own code, and a program running
outside the command keep their own reach, and an app you do not trust with the network should not
be installed where its code runs as you (§7).

## 19. A private chat is kept out of the memory folders, not out of every store

In an Incognito or Temporary chat, in the work of an app you did not give your memory, and in a turn
someone other than you asked for (a colleague in a shared thread), the agent changes nothing in the
memory folders: `workspace/memory` in the home (preferences.md, projects.md and the daily history)
and `workspace/_ext` (each working folder's memory, its documents and its database). In a Temporary
chat, and in the work of such an app, it reads nothing there either: a Temporary chat starts blank.
An Incognito chat reads its memory, as its notice says, and so does a turn someone else asked for.

- `write_file` and `edit_file` refuse the change before you are asked to approve it, and tell the
  agent nothing was written and why, and an artifact that shows a memory document (one you saved
  from Files) changes nothing in it either. Where the work reads no memory, `read_file`,
  `list_dir`, `glob`, `grep` and `repo_map` refuse a path there and leave the folders out of what
  they list and find, saying why, and nothing there is read through an artifact's `content_file`,
  a file `notify_attachment` sends, or an artifact that shows the file.
- The agent's shell refuses a command that names a path there and does more than read it, before
  you are asked; where the work reads no memory, it refuses any command that names a path there.
- The OS sandbox keeps those folders read-only to every command started for such work, whatever its
  text says, and unreadable to every command started for work that reads no memory: the native
  agent's shell, and an agent CLI started for that chat or in that turn.
- An agent CLI's own file and shell tools: a call there that the CLI asks about before it runs is
  refused where it asks, by the shell's own screen, before you are asked or a chat's Trust or a
  standing grant could approve it: a change in such work, and a read too where the work reads no
  memory.

What this does not hold:

- **Where no OS sandbox runs.** With the sandbox off, or on a system that offers none, only the
  reading of the command's text holds, and a path a command builds while it runs is not seen.
- **An agent CLI that was already running.** A CLI that serves several chats on one process is
  fenced or not by the chat it was started for, and one the session warm pool starts ahead of any
  chat is not fenced. In a private chat on such a process, a change its own tools ask about is
  refused, and so is a read in a Temporary chat, but a tool it runs without asking first is
  screened by nothing (§1, §11).
- **A turn is not a process.** Who asked for a turn changes from one turn to the next, and an agent
  CLI's fence is set when its process starts: one started in a colleague's turn keeps the memory
  folders read-only for as long as it runs, your own later turns on it included, and one started in
  your turn holds a colleague's turn only where the CLI asks before a call runs.
- **PersonalClaw's databases.** A command that opens one itself is not fenced: `memory.db` at the
  top of the home is not refused either, and the databases in the workspace (the knowledge
  library's, the vocabulary's, and each working folder's memory database and learning log) are
  refused only by their names in the command's text (§13 explains why a text screen is not a
  fence). The stores refuse a change only when it is made through PersonalClaw.
- **A two-way vault.** With the memory or knowledge vault set to two-way, a page a command edits is
  read back into memory or knowledge by the next sync, as your edit. A vault, or any other copy of
  your memory, kept in a folder the agent's tools reach is read as any file there is.

**What this means for you:** neither a private chat's agent nor a turn someone else asked for can
save to your memory by writing its files, and a Temporary chat's cannot read your memory from them.
Keep the sandbox on, run private chats on the native agent or on an agent CLI that runs one chat per
process, run shared threads on the native agent or on an agent CLI that asks before its tools run,
and keep the vaults one-way, and out of the folders the agent's tools reach, if such work's commands
could reach them.

## 20. Work someone else asked for is held by its own record, not by everything handed to it

A turn someone other than you asked for (a colleague in a shared thread) changes none of your memory
on its own, and neither does the work it starts that lasts after it: a workflow run, a loop and a
callback record who asked when they are made, and their steps, cycles and turns are held to that for
as long as they last, after a restart too (`lasting_work.py`). An automation is not made or changed
on someone else's say-so, and a loop takes the words of whoever asked for it alone.

What this does not hold:

- **Words handed to work you asked for by other doors.** An answer a colleague's turn gives to a
  question your run is waiting on, or an edit it makes to the plan of a loop of yours before it
  starts, goes into your work: the work's record says you asked for it, so what it does next is
  judged as yours.
- **A grant given for that one run.** None of your standing grants answers the work's calls (§23),
  but a grant the run itself was given does: a loop made Unattended in a colleague's turn runs its
  calls on its own grant, as one you made Unattended would, once you allowed the call that
  started it.

**What this means for you:** answer your runs' questions yourself, and check the Mode of a loop
someone else asked for before it runs.

## 21. A restore, an import or an export looks for links, then writes or reads

A restore, an import (of an export archive, a project archive or a pack) and a sync never write
through a link the home holds, and a sync's export and the backup export never read through one
(`durability/home_paths.py`): each looks at every folder on the way to the path it is about to
write or read, and at what is there, and leaves the item as it is when it finds a link. It looks,
and then it writes or reads. A process writing in the home at that moment, such as an app's backend
in its `data/` folder or a command of the agent's in the workspace, could make a folder a link
between the two, and the write or the read would follow it.

- **What holds:** a link that is there when the door reaches the item, however it got there and
  wherever it leads. A whole file an archive or a project archive brings is written as a new file
  and renamed into place, so a link made at its own name in between is replaced, never written
  through. A lock is opened in one call that refuses a link at its name, so a lock is never
  opened through one, raced in or not.
- **What does not:** a folder made a link in between; a database or a log merged in place, a file
  an export reads, and a pack's skill files, each opened by its path once it was looked at. A
  lock's folder is its store's, where the store writes its files: a store its owner keeps behind a
  link is locked there, as it is written there.

**What this means for you:** a link put in the home ahead of time is refused; one raced in while the
restore, the import or the export runs is not. Stop the gateway, and with it the apps' backends and
the agent's commands, before a replace restore, as the restore asks you to.

## 22. What git and your shell run as you: refused on every write path, fenced only in part

Your own git and your own shell run what some files hold, as you and outside any sandbox: a
repository's git settings and hook scripts (`.git/config`, `.git/hooks/`, `.gitmodules` and the
rest), your own git settings (`~/.gitconfig`), and your shell's startup files (`~/.zshrc`,
`~/.bashrc`, `~/.profile` and the rest:
[security.md](../architecture/security.md#what-runs-as-the-owner-is-owner-only-owner_onlypy) lists
them). An agent changes none of them on the paths PersonalClaw runs for it, in any approval mode,
Trust, YOLO and a standing grant included, and it is told why in the words its refusal of
PersonalClaw's own files uses:

- `write_file` and `edit_file` refuse the change before you are asked, wherever their folders reach
  one: a repository in the workspace, or your home folder when an allowed working directory covers
  it.
- An agent CLI's own write, edit or patch that it asks about first is refused where it asks, read on
  the files the call names, a patch's changes included.
- A file-backed artifact cannot point at one, so no save of it writes one, and an automation cannot
  name one as a file it changes.
- The agent's shell refuses a command that names one and does more than read it, and a `git config`
  that sets, unsets or edits a setting at any scope (`git -c name=value`, which sets one for a
  single command, still runs). Reading them, and running a startup file in the agent's own shell
  (`source ~/.zshrc`), run as before.

What the OS sandbox holds of them, in its `auto` mode:

- **macOS** denies writes to your own git settings and your shell's startup files in your home
  folder (`~/.gitconfig`, `~/.zshrc` and the rest), whatever the command says. One that is a link
  into a dotfiles folder is held at both its names, and that folder cannot be moved aside.
- **Linux** holds none of them: holding a file in your home folder would mean making the whole
  folder read-only to the agent's shell.
- **Neither** fences a repository's settings and hooks, nor the settings kept in `~/.config`
  (`~/.config/git/config`, fish's): git writes a repository's itself in the agent's ordinary work
  (`git init`, `git clone`, `git remote add`, `git push -u`, a branch that tracks another), and a
  kernel rule cannot tell that from a planted setting; and holding `~/.config` would mean refusing
  to make it where a program's first run needs it.

With the sandbox off, or on a system that offers none, the screen alone holds the shell.

What the screen does not see:

- **git's own commands that record a setting as part of their work**: `git remote add`,
  `git push -u`, `git submodule add`, a branch that tracks another, and `git clone` or `git init`
  making a new repository's.
- **A program that changes them itself**: a script, an installer that adds its line to your
  startup files (a language toolchain's, a version manager's), or a hook manager's install step
  (`pre-commit install`, a package's install script that sets `core.hooksPath`).
- **A path built while the command runs**, as §13 says of credentials.
- **A repository an agent makes.** A folder it turns into one, with `git init`, a clone, a `.git`
  it writes where there was none or the files of a bare repository, holds what the agent wrote,
  and git reads it like any other. So does a git folder put in place of a repository's own after
  deleting it.
- **A file they read in turn**: a file your git settings include (`include.path`), a hooks folder
  inside the project that `core.hooksPath` names (`.husky/`, `.githooks/`), a hook manager's own
  settings (`.pre-commit-config.yaml`), a plugin your shell's startup file sources, and the startup
  files of a shell not listed there.
- **An agent CLI's tools that do not ask first** (§1, §11).

Editor and task-runner settings in a repository are not in the rule: `.vscode/settings.json` and
`.vscode/tasks.json`, `.idea/`, a `Makefile`, `package.json` scripts, a `justfile` or
`Taskfile.yml`, `.envrc`, `.mise.toml` and the like. They are the project's own files, which you ask
an agent to change and which show in `git status` when the repository tracks them, and the tools
that run commands from them either ask you first (an editor's trust prompt for a folder and its
automatic tasks, `direnv allow`, `mise trust`) or run them when you run that tool.

**What this means for you:** an agent's shell in a Trust or YOLO chat can still change these files
through git itself, a program or a script, and on Linux nothing under the screen stops it. Before
you run git in a repository an agent worked in unattended or with Trust or YOLO, look at what it
changed: `git config --list --show-origin --show-scope` and a listing of `.git/hooks` read them
without running anything. Set `safe.bareRepository` to `explicit` in your own git settings, so a
folder that only looks like a bare repository is not read as one, and treat a repository an agent
made, or one someone else prepared, as one a stranger sent you.

## 23. A turn someone else asked for takes none of your grants, but it still reads

A turn someone other than you asked for (a colleague's message in a shared thread, a correspondent's,
a program's) is answered by none of your standing grants: your Trust, Trust reads and YOLO, an
agent's "Always allow" and your Approval mode "Auto" answer only what you ask for, on either runtime,
in a channel that runs its turns itself, and in the work such a turn starts (`approval_grants`, rule
4). Its calls are asked of you, their card naming who asked, and it searches none of your chats:
`chat_search` and the inbound door's `sessions_search` answer it with a sentence that names who
asked.

What this does not hold:

- **What a call's tool declares.** A call whose tool declares that it only reads asks nobody, in
  their turn as in yours: a file read, a memory recall, a list of your tasks, a connector's read of
  an account of yours. What it finds can be answered into the shared thread.
- **What the turn is handed.** The turn's context carries your memory as any turn of the chat does
  (saved facts, lessons, standing instructions, what it recalls of earlier conversations), and
  `memory_recall` answers it. Only the transcripts of your other chats are withheld.
- **The operator's hook settings and patterns**, which answer their calls as before, and a grant
  given for that one run (§20).
- **Where the call is asked.** It is asked where the chat's calls are asked: in a shared thread,
  the thread's own prompt, which your colleague sees and only you can answer.

**What this means for you:** a shared thread is a conversation your colleagues have with what the
agent reads of yours. Connect a shared channel to an agent whose memory and read-only tools you are
content to have used for them, and answer the calls they ask for yourself.

## Why these are listed, not fixed

Per the project's lifecycle discipline, a control *gap* discovered while writing
documentation is recorded as a candidate for the security-hardening track — never
patched inline in a docs change. Every item above has a named future direction
(extending the hard rail to ACP protocol paths for #1; OS-level app isolation for
#2; out-of-process providers for the residual half of #3; a distinct origin for app
UI, with the SDK crossing it as a message channel, for #4; checking a hand-copied
weight's sha256 when it loads, for the gap in #5; per-app OS isolation for every kind of app
code, which today only a backend that names a sandbox tier has, for #7; a session signing key the
agent's shell cannot read, for #10; masking an agent CLI's requests in the capture proxy its
model calls can already be pointed at (`inbound/capture_proxy.py`), for #11; running the git of a
repository an agent can write under that agent's own sandbox, for #12; hiding the credential store
from the agent's shell at every sandbox level, for #13; running a command Trust reads approves
inside a read-only sandbox with the program's own configuration ignored, for #14; a list of the
programs a run may start, enforced by its sandbox rather than read from the command's text, for
#15; a network and a write fence around an unattended run's shell and an app's programs,
enforced by the OS rather than read from a command line, for #16; reading the whole of a long
text a reader makes of a document, and an archive's files one by one as an import writes them
out, for #17; a proxy the guard answers for, through which a run's commands reach the hosts its
tier lists, and a network fence, enforced by the OS, around an app's own requests, an MCP server's
program and an agent CLI's own tools, for #18; a fence, enforced by the OS, around every store of
long-term memory a private chat's commands could reach, an agent CLI's process kept to one private
chat and to one person's turns, and a vault that reads back no edit made by such a chat's commands, for #19;
the same record on every door that hands words to lasting work, and a Mode only you may loosen on
work someone else asked for, for #20; writing each file a restore, an import or a sync brings,
and reading each file an export sends, through folders opened without following a link, from the
home down, for #21; a fence that holds a repository's git settings and hook scripts against the
agent's shell while git's own commands still record what they must, and one that holds your own
files on Linux, for #22). This page will
shrink as those land.
The rest of #5 will not: a small model is the point of a floor, and the remedy for its
limits is to bind a real one.
