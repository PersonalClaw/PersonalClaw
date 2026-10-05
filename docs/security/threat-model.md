# PersonalClaw Threat Model

PersonalClaw runs an autonomous agent on the owner's own machine. This document
makes its security posture externally checkable: the trust boundaries it defends,
the controls that guard each one (with code citations), a mapping to the OWASP
Agentic Security (ASI) Top-10, and an honest statement of what it deliberately
does **not** defend against.

Every "enforced" claim below cites a resolvable module in
`PersonalClaw/src/personalclaw/`. The architecture narrative behind these
controls is [`docs/architecture/security.md`](../architecture/security.md); the
current limitations are [`limitations.md`](limitations.md).

**Verified against:** `main` at the commit introducing this file. Controls evolve;
if a citation below no longer resolves, treat the row as unverified and file an
issue.

## Trust boundaries

PersonalClaw defends five boundaries. Each is a place where something less trusted
meets something more trusted, with named controls at the crossing.

### 1. Owner ↔ agent ↔ tools

The owner directs the agent; the agent invokes tools that act on the machine. The
crossing is gated so the agent cannot act outside the owner's chosen posture:

- **Task modes** (`task_modes.py`) decide *which* tools may run per session
  (`agent`/`ask`/`plan`/`build`), hard-enforced in the native runtime's
  `_guard_and_invoke` **before** approval is consulted.
- **Command screening** (`security.py`): a deny list (the packaged baseline in
  `baseline_denylist.json`, re-asserted and merged with user config at read time
  via `denied_command_patterns()` — see [Baseline denylist
  integrity](#baseline-denylist-integrity-anti-drift-and-anti-llm-tamper-not-anti-owner)),
  suspicious-pattern watchers (`SUSPICIOUS_BASH_PATTERNS`), and a credential screen
  (`is_sensitive_bash_command`) that refuses a command naming PersonalClaw's own credential
  store and keys, in the home in use and the default one, or another tool's sign-in, one
  returning a credential folder under `$HOME`, and one naming such a folder itself (a listing).
  The first and the last are found the way the command's shell
  would find it (`command_paths.named_paths`, the reading the owner-only fence uses too).
  Defence in depth: [limitations §13](limitations.md#13-the-agents-shell-is-screened-not-fenced-from-your-credential-files).
- **OS child sandbox** (`sandbox.py`) with a credential-env denylist so secrets
  never reach a sandboxed child.
- **What runs as the owner** (`owner_only.py`): PersonalClaw's own files in its home, git's
  own settings and hook scripts (a repository's `.git/config`, `.git/hooks/`, `.gitmodules`, and
  the owner's `~/.gitconfig`) and the owner's shell startup files (`~/.zshrc`, `~/.bashrc`,
  `~/.profile` and the rest). No approval mode, Trust, YOLO or standing grant lets an agent
  change one: the file tools, an agent CLI's own write, edit and patch, file-backed artifacts and
  automations refuse it, and the shell's screen refuses a command that names one or a
  `git config` that sets a value. Fenced by the OS sandbox only in part:
  [limitations §22](limitations.md#22-what-git-and-your-shell-run-as-you-refused-on-every-write-path-fenced-only-in-part).
- **Trust/YOLO state** (`trust_mode.py`): one process-global auto-approve state,
  config-permanent or TTL'd, with `on_disable` callbacks.

#### Baseline denylist integrity: anti-drift and anti-LLM-tamper, not anti-owner

The always-on bash denied-command patterns ship as packaged data
(`personalclaw/baseline_denylist.json` — `{version, sha256, patterns[]}`), verified at
import, re-asserted on every `denied_command_patterns()` read, and re-verified by the
`security.baseline_denylist` doctor probe. `GET /api/security/denied-commands` returns
that state as a `baseline` block (`version`, `sha256`, enforced `count`, `verified`), and
Settings → Security renders it beside the read-only pattern list.

**What the digest does catch:**

- On-disk corruption or a partial write. The module raises at import rather than coming
  up with a shorter — or empty — denylist.
- An edit that changed the patterns but not the `sha256` shipped beside them: the same
  hard failure at import.
- Mutation of the in-memory list inside a running process (a stray `.clear()`, a
  monkeypatch, a `sitecustomize`). The next read heals it from the verified snapshot and
  audits `baseline_denylist_reasserted` with the patterns that came back.
- A *self-consistent* rewrite of the packaged file, patterns **and** digest changed
  together. Because the fingerprint is held in memory from import onward, the file is
  never consulted for content again: the divergence is audited as
  `baseline_denylist_tamper_attempt` and the verified baseline stays in force.

**What it does not do:**

- It does not stop the owner. Anyone who can edit the installed package *before the
  process starts* owns the baseline — a different `patterns[]` with a matching `sha256`
  verifies happily, because at that point it *is* what shipped.
- It is therefore not a tamper-**proof** control, and no surface may present it as one.
  The panel says the baseline "matches what shipped", or that the packaged file no longer
  matches — never "secure" and never "tamper-proof".

The threat this closes is drift inside a running process, most concretely an agent asked
to relax its own guardrails: the model can write files and run commands, so it can reach
the list, but it cannot make a shortened list stick and it cannot make the shortening
silent. The threat it does not close is the owner reconfiguring their own machine, which
is a decision rather than an attack — the same posture as the auto-approve bullet under
[What we deliberately don't defend
against](#what-we-deliberately-dont-defend-against).

### 2. Core ↔ apps

An installed app reaches your machine two ways: through its app-scoped token, and through
its own code. The controls below bound the token, so an app's requests never reach the
owner's full authority. They do not bound the code, which runs as you — the last bullet
says what that means.

- **App-scoped tokens** (`dashboard/token_auth.py::app_session_token`, a token with an
  `app` claim) bound a request to that app's declared permissions; they last an hour, and
  count against a limit of their own per app, so no app can sign the owner's devices out.
- **Reverse-proxy credential stripping**
  (`dashboard/handlers/apps.py::api_app_proxy`): app backends never see the
  owner's cookie/Authorization — the app's own 1-hour app-scoped token is injected (the
  same one while it has more than half its hour left, not a new session per request).
- **Permission middleware** holds in every auth mode — including `none`, where
  `dashboard/server.py`'s `_dev_user_middleware` re-adopts the app claim via
  `validate_token_with_app` so an app token only ever *narrows* reach. The internal
  routes' session fallback records the claim too — every auth path picks its credential
  through `dashboard/token_auth.py::_select_request_credentials` — where it used to
  validate an app token and drop its identity, so `/api/tools/invoke` reached the
  handler as the owner.
- **Reverse-proxy query stripping** (`api_app_proxy`): the proxy drops the credential query
  parameters (`?token=`, `?app_token=`) before forwarding, for the reason it drops the
  cookie and `Authorization` — a backend must never hold a token it could replay.
- **The owner's security posture is not an app's to change.** A config field whose
  `_EDITABLE_CONFIG` entry declares a `SecurityControl` (`config/edit_spec.py`) refuses an
  app-scoped write `403`, in either direction, with a Security Event Log row naming the
  app and the field; so does a standing approval verb (`yolo`, `trust_agent`). The routes
  that exist only to change the posture are in the
  owner-only registry (`apps/permissions.py::OWNER_ONLY_API_PATHS`). The owner's own write
  that loosens a security setting needs `"confirm": true` — a record that the owner was
  asked, not authorization.
- **What runs as you is yours to define.** An app token cannot define an MCP server:
  `/api/mcp` is owner-only, reads included, because a server entry is a command the
  gateway launches and a remote server's headers carry its bearer token. Nor can it
  create, edit, arm or run an automation by hand, save a workflow, start or steer a run,
  or make any goal-loop write. Installing, enabling or updating an app or a pack, adding a
  Store source, and importing or restoring a backup are owner-only for the same reason:
  each puts code or configuration in place that later runs as you. An app declares its
  MCP servers and its scheduled jobs in its manifest, where install consent lists them.
  Subtrees are rows in `OWNER_ONLY_API_PATHS`; routes that share a family with an app's
  legitimate business are declared one by one in `apps/permissions.py::ROUTE_AUTHZ`.
- **An MCP server runs only once you allowed what it runs.** A server started with a command
  is a program run as you, and one at a URL gets whatever your agents send its tools, so the
  gateway starts and connects to none until you allowed its definition (`mcp_grants.py`): how
  it is reached, its command, arguments and folder, the names of the variables it sets, and a
  remote server's address and header names. Values are not in the seal; nobody is shown them.
  The Tools page's Add and Edit and the MCP Tool Servers card say exactly what will run and
  save nothing until you agree (`400 confirmation_required`). Every other way a definition
  arrives (Import from Claude Code or Codex, bringing a setup over, a pack's connector, an
  app's manifest, a restore, a hand edit of `mcp.json`) writes one that waits for Allow on the
  Tools page, which asks the same question first, and a change to anything in the seal waits
  again. `mcp.json` is an owner-only path (`owner_only.py`), so an agent's shell and write
  tools cannot change it, and the loopback internal secret reaches no `/api/mcp` route. Nor is
  a server that waits handed to Claude Code, which would start it (`/api/mcp/apply`'s
  `ccGlobal`).
- **What your agents are told is yours.** An agent carries out its instructions with your
  tools under your approval settings, so an app token cannot write them: creating,
  editing, syncing or deleting an agent (its system prompt, tools, skills, model and approval
  mode alike), writing an agent definition or activating one, installing, writing, accepting
  or removing a skill, writing a prompt or a snippet, rebinding the system prompt your chats,
  unattended runs and judges start from, launching a prompt template (it starts a goal loop),
  and rewriting the orchestrator's routing notes are all owner-only, and importing another
  agent tool's setup is in the owner-only registry. An app ships skills in its manifest, where
  install consent lists them. A lesson is memory every agent is handed as a rule, so
  `/api/lessons` needs the `memory` grant like `/api/memory`
  (`apps/permissions.py::MEMORY_API_PATHS`).
- **Your conversations are yours.** A message in one of your chats, your line in a room and
  your answer to an agent's question are instructions your agent carries out with your tools,
  so an app token cannot write any of them: sending into, editing, regenerating, resuming,
  steering or deleting one of your chats, answering an approval in one, setting your chats'
  task mode, speaking in a room, replying through the inbox or approving a proposal, and the
  schedules' delivery door (`/api/send-message`), which speaks as your agent. A conversation
  records the app that started it (`_ChatSession.created_by_app`, persisted), and the
  `ROUTE_AUTHZ` rows that carry `owns` hold an app to its own; the old per-handler check
  compared an origin tag an app could share by its name. An app's own conversation needs its
  `agent` grant and runs under it, never under your approval switches, the operator ceiling
  still bounds it, and the app never answers an approval that conversation raised
  (`permissions.app_conversation_auto_approves`). It reaches you through a proposal the
  inbox labels with its name. The one app door to an approval is the relay an app declares
  as `/api/approvals` (the menu-bar companion): it answers approve or reject once, and the
  gateway cannot tell whether that answer was yours. Reading is held the same way: the
  conversation families declare their reads route by route (`READ_DECLARED_FAMILIES`), a
  read of one conversation reaches only one the app started, lists and searches answer with
  the app's own, rooms and the inbox are yours, and the websocket carries a frame about a
  conversation to an app only when it started that conversation, and an inbox item only when
  the app raised it (`dashboard/ws_state.py::frame_subject`). Your notification log is held to
  the same rule: an app reads a notification, from `GET /api/notifications` or its socket, only
  when it raised it or it is about a conversation the app started
  (`DashboardState.notification_reaches`), and the session list carries no YOLO flag.
- **Your access, and who else has any, is yours.** Demoting an autonomy grant, undoing
  an automation's action, signing out a device, revoking a chat sender, and connecting
  or disconnecting a chat channel refuse an app token. Two levers stay with apps on
  purpose: `POST /api/incident`, which stops unattended work and grants nothing, and
  `POST /api/devices/pair/complete`, which only redeems a pairing code you minted.
- **An app reads only the settings it declared.** `GET /api/config/personalclaw` hands
  an app the fields its manifest lists in `permissions.config` and nothing else, and a
  write to any other field answers `403 config_field_not_declared`
  (`dashboard/handlers/core.py`). Install consent shows the list, and a manifest that
  names a security setting fails to install (`apps/manifest.py::_config_permission_errors`).
  `/api/apps/{name}/config` reaches only the calling app's own settings, and the file
  explorer hides the PersonalClaw home from an app token
  (`file_roots.py::dashboard_roots`), since `config.json` and `mcp.json`
  live there. It refuses an app like every other app refusal, `403` with a Security Event
  Log row naming the app and the path (`files.py::_app_path_refusal`).
- **A provider is its app's.** A provider's settings say where it connects and which of its
  credentials it connects with, so an app that could write another app's could point that
  app at a server of its choosing and have it send its own key there. Every
  `/api/providers/{name}` route is held to the calling app itself before its handler runs
  (`ROUTE_AUTHZ` rows carrying `owns` that names the app; `/api/apps/{name}/config` is held
  the same way): another app's settings, instances, instance test and availability check
  are refused, reads included, and the provider list answers an app with its own providers
  only. The MCP Tool Servers card (`/api/providers/mcp-tools`) is every server the gateway
  launches, so it is owner-only like `/api/mcp`, reads included. An app's own settings
  resolve only its own keys (`config/secret_refs.py`). A change to a switched-off app's
  settings or instances is saved and loads nothing until the app is switched on, and neither
  starts an app this core cannot host (`providers/routes.py::apply_saved_settings`).
- **Your models are yours.** A model provider says where your model calls go and which of
  your keys goes with them, and a binding says which model each use runs on; the model your
  chats run on decides the tool calls your agent makes. An app that could write either could
  send your chats to a server of its choosing, or answer them itself. Every route under
  `/api/model-providers` and `/api/models` is owner-only for an app, reads included
  (`ROUTE_AUTHZ` `OwnerOnly` rows; both are `SECURITY_ROUTE_FAMILIES` and
  `READ_DECLARED_FAMILIES`), and so is the onboarding wizard's one-click bind
  (`OWNER_ONLY_API_PATHS["/api/onboarding/local-model/bind"]`), which adds a model provider
  and can move your chats onto it. The rest of the first-run setup (`/api/onboarding`) is a
  family of its own, owner-only the same way, reads included: its status read and model
  check name your chat binding, its local-model probe and LAN sweep have the gateway look
  for model servers on your machine and network, and its state is your setup's progress.
- **A repair to your setup is yours, and so is updating the code that runs it.** A Doctor
  fix changes your setup once you confirm it (which models your uses run on, PersonalClaw's
  server in your agent's config, the files the dashboard is served from), so an app that
  sent the confirmation would have confirmed for you; the Doctor's maintenance run deletes
  what is past its retention, your security log's entries among it. An update replaces the
  code this gateway runs and restarts it. `/api/doctor` and `/api/update` are
  `SECURITY_ROUTE_FAMILIES` whose writes are `ROUTE_AUTHZ` `OwnerOnly` rows, apart from the
  Doctor's two simulators, which write nothing and are `AppMay`; their reads stay the
  allowlist's, so an app you granted the Doctor still reads its report. Taking or declining
  the newer version of a skill that comes with PersonalClaw is the owner's the same way.
- **Which of your tools your agents may call is yours, and so is a project's overview.** A
  tool or a tool provider you switched off is left out of a native agent's tools and refused
  to a direct call (`tool_providers/tool_prefs.py`), so an app that switched it back on would
  undo your choice, as one that switched an MCP server's tool would (`/api/mcp` is
  owner-only). A project's overview is given to every session in the project as what the
  project now knows, unfenced, so an app that wrote it would be writing what those sessions
  are told. `/api/tools` and `/api/legibility` are `SECURITY_ROUTE_FAMILIES`: both tool
  switches and the overview write are `ROUTE_AUTHZ` `OwnerOnly` rows; `POST /api/tools/invoke`
  is `AppMay`, its handler running only a tool the app's manifest declares in
  `permissions.mcpTools`; hiding a Discover tip and bringing the hidden ones back are `AppMay`,
  since they enable and configure nothing; the reads stay the allowlist's.
- **What a project gives its sessions is yours.** Its brief is put before every chat and loop
  in it as the project's goal, and an agent that loads the project's context (`get_context`) is
  given the brief and the instructions as its rules, unfenced; its folder is where those
  sessions work, read and run commands, and an agent CLI there follows the instruction files it
  finds. An app that wrote any of them would be writing what those sessions are told.
  `/api/projects` is a `SECURITY_ROUTE_FAMILIES` root: `POST /api/projects` is `AppMay`, so an
  app still makes a project under a name, and its `owner_only_fields` refuse an app's request
  that names the brief, the instructions or the folder, whatever the value, before the handler
  runs; every other write is a `ROUTE_AUTHZ` `OwnerOnly` row (changing, deleting or importing a
  project, the default project, a Work-board claim and its release, and writing PersonalClaw's
  block into the instruction files in its folder); the reads stay the allowlist's. A chat in
  the project is given the overview and the ledgers kept in the project's own folder in the
  home, so that folder is no explorer root for an app, as a loop's is not
  (`file_roots.dashboard_roots`): no explorer write and no file-backed artifact of an app
  reaches them.
- **A new route fails closed.** Every write route under a family that decides what runs
  as you, whether it asks first, or who may reach you (`SECURITY_ROUTE_FAMILIES`), and every
  read under your conversation families, your notification log, your providers, your models
  and your first-run setup (`READ_DECLARED_FAMILIES`), is either owner-only or declared
  `AppMay` with its reason. An undeclared one is refused to every app token at runtime
  (`undeclared_security_route`), and `tests/test_security_posture_rail.py` fails the build on
  it.
- **An automation that approves itself asks you first.** The owner's own write that makes
  a trigger's or a workflow step's agent approve its own tool calls (`approval_mode:
  auto`) or gives it write access (`capability: mutating`) needs `"confirm": true`
  (`automation_posture.py`). So does an agent sync that folds a looser `approval_mode` in
  from disk, and a run override that raises `max_cycles`
  (`workflows/supervisor_policy.py::POLICY_OVERRIDE_SECURITY`). The hook setting that starts
  subagents without asking stands in for none of these: it starts a step's agent and approves
  none of its calls. An agent's `subagent_run`
  batch with a task that may change things never sends that confirm: the gateway asks you once,
  naming each task and what it may change, and saves and starts it only on your own Allow
  (`workflows/batch_start.py`). So does an agent's `workflow_author` save of a step that would do
  more than the same step does now (`workflows/definition_ask.py`), and every other door that
  writes a definition goes through the same screen (`workflows.service._write_definition`): an
  accepted refiner diff, a prompt card, a pack's template (which arrives asking), and an edit of a
  running workflow (an agent's is refused). No standing grant answers either ask, and a session
  with nobody to ask (an Unattended loop's) is refused. A request to `POST /api/spawn` cannot set
  a subagent's approval mode at all. The `subagent_run` call itself asks nobody: what it starts
  asks (one task's start, or the batch's one ask), so the call is not a second question. A batch
  that only reads starts on what starts its chat's subagents (its Trust, YOLO) or on your answer
  to its one ask; a waiting batch's record (`workflows/batches/`) only ever asks again.
- **A chat's subagents are that chat's** (`subagent_reach.py`). `subagent_list` and
  `subagent_status` answer a call with the subagents of the chat its work is for, as its sign-in
  proves it (an agent's tools name their chat with the internal credential; an app's token is the
  app's own work), a nested subagent and a batch's tasks counting as their chat's. That holds while
  the gateway holds a subagent, for the folder a restart leaves of one, and for the report kept in
  its chat. Another chat's subagent reads as not found, in the words an id that never existed does;
  a call whose chat cannot be known reads none. Your own sign-in reads every one.
- **A chat's own workflow runs are that chat's** (`workflows/chat_runs.py`), by the same rule: a
  batch (which saves no workflow definition, so nothing lists, reads, starts or deletes it by name)
  and every run a Temporary or Incognito chat starts. A native agent's workflow tools, the run
  routes the tool server an agent CLI calls, the runs list, the store's repair and the runs a chat's
  turn is told of answer another chat's own run as no run at all, in the words an id that never
  existed does, with an audit row; the spec an inspect call echoes follows only a run its caller
  reads. Your own sign-in reads every one.
- **A chat's requests in the Inbox are that chat's** (`inbox_reach.py`), by the same rule: an item
  raised for a chat's work (the approval its agent or one of its subagents waits on, a batch's ask
  naming each task, a question its agent put to you, the note a call of its left when nobody
  answered, a proposal drawn from it, what one of its own runs waits on) is read by that chat's
  work and by you. `inbox_list` gives any other caller its own chat's items and those about no chat
  (your mail and channels, proposals, notices, what a run of yours waits on), and counts only
  those, in words that do not change with what it left out, wherever it runs: a chat's agent, its
  subagents and workflow steps, a scheduled run, and `POST /api/tools/invoke`, which a scheduled
  script, an app and an agent CLI's tool server reach (that tool server has no Inbox tool of its
  own). The Morning triage digest is no chat's work: a chat's item reaches neither its model nor
  its run's record, which every agent's workflow tools read. An item whose chat cannot be told is
  yours alone, and your Inbox page and a tool you run from your own pages read every one.
- **The app's own code is outside all of this.** An app's provider module is imported
  into the gateway's process, its backend is a process under your account, each MCP
  server in its manifest is a command the gateway launches with the gateway's own
  environment (which carries the stored credentials PersonalClaw exports for its child
  processes, `config/loader.py`), its setup hooks are shell commands, its CLI steps run
  inside `personalclaw setup` and `personalclaw doctor`, and a connector pack's source
  parsers run on what its sources fetch. That code can
  read and write every file in your PersonalClaw home: `config.json` with every security
  setting, `mcp.json`, the credential file (`.env`), and
  `session_key`, the key that signs every session token, yours included. The home's
  0600/0700 modes (§5) keep other accounts out, not code running as you. The one
  exception is a backend that names a sandbox tier (`backend.sandbox`), which launches
  inside that tier. An app that ships code therefore needs no API call to relax your
  posture. Install consent names every kind of it — the server process, each provider
  module, every lifecycle hook, the CLI steps, each source parser and each MCP server
  command — and leads with the gateway's sentence saying that code runs as you and that the
  permissions do not bound it (`apps/disclosure.py::describe`). Saying so is disclosure,
  not containment: the supply-chain gate (§4) is the control that vets it. See
  [limitations.md](limitations.md) §7.

### 3. Gateway ↔ channels / inbound

Content and requests arriving from outside the owner's trust boundary:

- **Untrusted-content fencing** (`security.py::fence_untrusted`) wraps
  third-party text in `<untrusted_content>` markers with a data-not-instructions
  system note; applied to web-search results, inbox content, and third-party
  payloads. The note is one of the platform's safety rules (the `safety-rules`
  snippet), which every agent is handed whatever its own prompt says
  (`prompt_providers/runtime.py::with_safety_rules`; a path that starts an agent
  without them fails `tests/test_agent_safety_rules_census.py`).
- **One door for text from outside** (`outside_text.py::admit`): the injection
  screen reads the text, then it is fenced with its source, and text the screen
  refuses is kept as nothing. The doors that take it: a stored trigger's fire
  (its payload's words, its `$CONTEXT` line and what started the run,
  `triggers/fire_facts.hand_on`), a lifecycle trigger's words and what its
  action prints (`hooks.hand_on`, `hooks.take_in`), a pasted prompt card, a
  callback's saved context, a scheduled run's result opened as a chat, a group
  channel's recent messages and a thread's first post, a line someone else sent
  in a conversation's history, a refiner's evidence, and a helper's report, to
  the chat it reports to and through `subagent_status` (`subagent_report.py`).
  `tests/test_outside_text_doors_census.py` lists every place core fences text
  for a model; the doors that still fence without the screen (a page the agent
  fetches or browses, a file attached in a chat, an Inbox message, what a
  room's members said, a workflow step's output, among others) are listed
  there, and that list may only shrink.
- **Injection screen** (`triggers/screen.py::screen`) reads that text before a
  model does, at every door `outside_text.admit` serves (above). It refuses text
  addressed to the model that tells it to drop what it was told, hands it a new
  purpose, speaks as its system turn, gives whoever reads it a side task, or
  hides one of those behind encoding or invisible characters, and it fences text
  that gives the model a role or asks for its configuration. It passes ordinary
  content: code, Markdown tables, shell pipelines, stack traces, diffs, logs,
  commit messages and release notes
  (`tests/test_the_injection_screen_passes_ordinary_text_and_refuses_a_take_over.py`).
  Text quoting a take-over phrase word for word is refused like the phrase. It
  is a filter, not a proof: the fence and a trigger's frozen capability set hold
  when it misses.
- **Webhook auth** (`inbound/webhook.py`): the webhook's two doors take requests
  only from programs on this machine and each its own token, compared in constant
  time — a sender token made for one automation at
  `/api/triggers/<id>/fire` (`dashboard/handlers/trigger_runs.py::api_trigger_fire`),
  the owner's webhook token at `/api/hooks/agent`
  (`dashboard/handlers/hooks.py::_hook_token_refusal`). Neither takes a dashboard
  session or the internal credential; no configured token means every request is
  refused; every request is an inbound-audit row, and every refusal and accepted
  call a Security Event Log entry.
- **Egress chokepoint** (`net/client.py` + `net/guard.py` + `net/policy.py`): the
  single outbound-HTTP seam with named policies, layered by
  `net/policy.py::egress_policy_for` and narrowed, for every request it judges, by the egress
  tier of the run the call is made for (`egress_policy_for_run`: a run whose tier is off
  reaches nothing, whichever app, tool or automation makes the request; what it does not reach is
  [limitations §18](limitations.md#18-a-runs-egress-tier-holds-where-its-requests-ask-the-guard)).
  A command a run starts asks no guard, so the tier holds for it where the OS sandbox launches it
  (`sandbox.wrap_argv`, `net/policy.py::no_network_for_commands`): unless the tier is `all`, the
  command runs with no network at all (a network namespace of its own on Linux, a profile that
  denies the network on macOS), and one the sandbox cannot do that for is refused, not run.
  Downloads go through it too, each request and each
  redirect hop asked before it is sent and audited: the code map fetches a grammar bundle
  through `net.fetch` and hands the language pack a manifest naming only files on this machine
  (`codegraph/grammars.py`); in every PersonalClaw process the Hugging Face library is given
  clients that ask the guard first (`net/libraries.py`); and a download too large to buffer
  streams through `net.open_url` (the bundled chat model's weights, an app's model files).
  Neither of the last two holds the connection to the address the guard checked, as `fetch`
  does, and a process that does not import PersonalClaw (an app's sidecar) gets the libraries'
  settings, not their guarded clients.
- **What a channel is handed is masked, once, by core** (`channel_delivery.py::MaskedDelivery`).
  A channel app sends what it is handed to a service outside the machine, so every channel's
  delivery handle is registered behind the mask, and every path to a channel (a reply, an owner
  notification, a rich payload's strings, an automation's result, an approval's title, purpose and
  input, a chat mirror, a stream's progress) hands it text masked with `redact_for_display`. A new
  sending method in the `ChannelDelivery` protocol is masked or listed as sending no text, or
  `tests/test_channels_are_handed_masked_text.py` fails. What an app sends on its own paths, text it
  builds or relays without core, is the app's to mask.
- **A channel's own turn runs a call unasked only on core's answer** (`chat_trust.chat_grant`). A
  channel app that runs a conversation itself (Slack's threads) asks core, at each call, who
  approves it without asking: the decision a chat makes for a call put to its gate, with every
  grant held to the allowed hosts, to the protected folders and to the operator ceiling
  (`approval_grants.stands_for_call`). Under `{"approval": {"value": "ask"}}` an operator's hook
  pattern, the chat's Trust and YOLO approve nothing there, a command reaching a host off the
  allowed hosts, or deleting the owner's home folder, the filesystem root or the folder the
  conversation runs in, is always asked about,
  and no grant answers a call the hook chain refuses: the channel refuses that call itself, before
  it asks core or anyone (`screen_tool_call`, read on the command that would run), and then one
  the operator's blocking hooks refuse, bound to the agent the conversation runs as, at the step
  every path asks them (`ask_pre_tool_hooks`). A call nobody approves is asked on the channel's
  own prompt. The app keeps no pattern, setting or approval mode of its own that
  approves a call.

*Inbound MCP and external remote access (fail-closed inbound, fencing at
ingestion) are owned — not yet
landed; see the ASI07 row.*

### 4. Install pipeline ↔ sources

Installable content (apps, skills) from arbitrary sources:

- **Quarantine → scan → consent → install** (`apps/app_manager.py::install`):
  content is staged in quarantine, scanned there, and only moved into place if it
  passes — so the scanned bytes are the installed bytes (no time-of-check/
  time-of-use gap).
- **Staging never follows a link** (`apps/staging.py`): the quarantine copy is made
  from one survey of the whole bundle, so a symlink or a hard link cannot pull a file
  from outside the bundle into the installed app. A link to one of the bundle's own
  files stays a link, and anything else is refused, naming the path.
- **What installs is what was scanned** (`supply_chain.py::never_installed`): staging
  leaves tooling out of the bundle at any depth (`.git`/`.hg`/`.svn`, `__pycache__`, and
  the virtualenvs `.venv`/`venv`/`.tox`), so it is neither scanned, nor in the consent
  digest, nor installed; skills follow the same rule. The scanner skips nothing in what
  remains (`node_modules` included) and discloses a file it cannot read as an
  `unscanned_file` finding. It used to skip those folders and every file over 512 KB
  while staging installed them, so code hid there unread, including bytecode the
  interpreter runs in place of the source the scan read.
- **Scanner verdicts** (`supply_chain.py`: `SkillScanner`, `Verdict`): `clean` /
  `warning` (consent required) / **`dangerous` (terminal, non-overridable)**;
  `TrustTier` modulates strictness.
- **Where the bytes come from**: a registry index is untrusted, so a listing's `repo`
  must be an `https://` URL (`apps/catalog.py::listing_repo_refusal`), so an index cannot
  point a Store card at a folder on this machine. Nor at this computer, a private network
  or the cloud metadata service: the Store shows such a listing refused, with the reason,
  when its address is written as a literal; an install resolves the host and refuses a
  forbidden answer before fetching (`apps/source.py::resolve`); and the fetch runs through
  `net/git.py::run_git_guarded`, which judges every host git connects to, so a name that
  rebinds after the check, or a redirect, cannot reach one either. The owner's own choices
  stay reachable: the host of a registry source they added, their egress allow-list, and
  anything typed into Install from URL, which is not a listing.
- **An app's `data/` is read as the app's** (`apps/manager.py::read_app_owned_text`):
  the gateway's reads of files an app can write never follow a link, so an app cannot
  plant `data/config.json -> <another file>` and be handed that file.

### 5. System ↔ persisted / exported state

Data leaving the running system:

- **Tamper-evident audit** (`sel.py::SecurityEventLog`): HMAC-chained,
  append-only events (caller, operation, outcome).
- **Redacted archive reads** (`security.py`: `redact_credentials`,
  `redact_exfiltration_urls`).
- **Every read masks the same way, and a masked value is never saved back over the real one.**
  A read that hands text to you, an app, a channel or the agent masks it with `redact_for_display`
  (`redact_values_for_display` for a structured value), so no read shows what another read of the
  same thing masks: a file in Files; an artifact, its name, tags and text preview included; a loop
  before launch, its plan, its command chip and its name wherever it appears; an automation's name,
  prompt, command, action and last error on the Automations page, the week grid, a dry run,
  `/cron list`, `personalclaw cron list`, the agent's automation tools and an investigation; an MCP
  server's command, arguments, URL and error; an inbox draft and a notification; a prompt or
  snippet; a memory fact, its key included, the memory history and graph; a lesson; and what the
  agent's `artifact_get` and `knowledge_get` return. A workflow run's list, status, live events and
  step outputs are masked with the journal's redactor, the one its journal and inspect drawer use.
  Each save restores every marker it gets back from the value stored when it writes
  (`keep_masked_spans`, `keep_masked_values`), read with nothing awaited before the write, so a
  marker left where it was keeps the value it stood for, even one another save changed meanwhile,
  and the plaintext never crosses the wire. A masked name (a tag, a fact's key, a lesson's rule)
  names the stored one it masks (`stored_name`). A save whose markers can no longer be placed, or a
  masked name that names nothing or more than one thing, is refused (`409`) rather than stored.
  Exports and snapshots keep the stored values, so a restore restores them. A marker that is
  already part of the stored text, like a `CLAUDE.md` imported redacted, stays text. A structured
  secret shown as `••••••••` works the same way: an app, provider or MCP save that sends the mask
  back keeps the stored value, and the credential store refuses the mask as a value
  (`secret_refs._move_into_store`).
- **What an agent's model is handed is masked, and a credential it needs is named, not shown**
  (`security.redact_for_model`, the display mask). A tool's answer becomes model context at one
  place per runtime, and each masks it: the native loop's `format_tool_result`, for every provider
  it calls (the platform file and shell tools, the in-process categories, an app's tools, a remote
  MCP server's); the MCP server an ACP agent's calls to PersonalClaw's tools reach
  (`mcp_shared.run_mcp_stdio_loop`); and the one wrapper every inbound surface answers another
  agent through (a tool result, an A2A artifact, a bridge answer), which a webhook's body also
  passes on its way into a trigger (`inbound/framing.fence_payload`). So a tool is masked
  without opting in, `workflow_status`, `workflow_output`, `memory_recall` and
  `triage_rules_list` among them. A prompt is masked where it is
  assembled (`context._Parts.add`: memory, lessons, history, skills, channel history, episodic
  recall, active workflows), and so is what a chat turn puts in front of the request, which goes
  as typed: a stopped turn read back, an app's background context, a subagent's failure notice,
  the project's record, a loop's current phase and a hook's output
  (`chat_runner._ahead_of_the_request`), and a webhook callback's saved context. A
  spawned agent's task, a heartbeat task, an attached file's extracted text and a compressed
  thread's summary are masked too; the only text sent as written is what the person typed this
  turn. PersonalClaw's own chores (a title, follow-ups, suggestions, memory consolidation) are
  each a call of their own that no person types into, so every chore's prompt is masked before
  a model reads it (`chores.run_chore`), and consolidation's writes put each hidden value back
  from the fact it read (`memory_formation._as_stored`, `history._kept_lines`). A one-shot
  model call with no agent behind it (a workflow's infer, judge and visualize steps, a knowledge
  digest) passes the outbound scan at the model-call guard instead (`guardrails.scan_mode`). The
  mask is idempotent (a value that is already a mask is not taken for a credential), so a read
  already masked for a UI passes unchanged. A projection or a `tool_result_get` slice is cut from
  the masked text (`project_and_retain`), so it never splits a key. The agent's file tools keep a
  hidden value where it stands (`masked_edit`, `keep_masked_lines`): an edit keeps every stored
  byte outside the text it replaces, and a change that would move, copy or rewrite a hidden value
  is refused. A tool that needs a credential takes `{{secret:NAME}}`: `bash` fills in a credential
  the owner stored in Settings → Secrets as the command runs — the call's project's own secret
  first, then the global one (`llm.credentials.resolve_secret`; never the gateway's environment,
  never a setting's own key, never a project's secret by its stored key) — and masks every value
  it handed the command out of what the command prints (`redact_known_values`), however short. An
  automation's action and a workflow step mask each value their dispatch filled in out of what they
  return the same way, before a run history, a step's output or ledger, a note or Run now's answer
  keeps or shows it, and out of the audit rows and log lines the work writes as it runs
  (`filled_secrets`). Text handed to a model keeps the reference as the name: a
  trigger whose action is a model turn (`ActionProvider.hands_config_to_a_model`) and a workflow's
  stage, infer and visualize steps do not fill it in, so the agent's tools do. A workflow run is
  handed a reference in its inputs as the reference, by an automation's Run workflow action and by
  a step that starts a run, so no record of the run holds the value; the run fills only the
  references the one who started it wrote, where a step uses them (`workflows/input_secrets.py`),
  and text that reads as a reference in an input anyone else supplied stays text. What this cannot
  cover is in [limitations §11](limitations.md#11-what-reaches-a-model-is-masked-by-shape-and-an-agent-clis-own-tools-are-outside-it).
- **Credential-excluding exports** (`portability.py`): `.env`, `sel_hmac.key`,
  and `session_map.json` are on the export exclusion list.
- **Secret settings held by reference** (`config/secret_refs.py`): a provider key, every
  app setting declared `x-meta.sensitive`, every MCP server `env` and `headers` value (bar the
  `env` variables a server marks plain) and the webhook token live in the credential store;
  `config.json`, an app's `data/config.json`, provider instance records, `mcp.json` and the
  agent config carry a `{{secret:…}}` reference, resolved where the value is used: an MCP
  server's at spawn, the webhook token when a request is checked. Every path that adds or changes
  an MCP server writes through `secret_refs.write_mcp_document`. Deleting a provider, removing an MCP
  server (the Tools page and the provider card share one delete, `secret_refs.remove_mcp_servers`,
  which takes it out of both documents), or either removal rung of an app, deletes what it owned.
  An owned secret is never put in the gateway's environment, so no child process, an MCP server
  included, inherits another record's value: `AppConfig.load_credentials` and the CLI's `.env`
  loader (`cli.main`) both skip `PCSECRET_` keys. `GET /api/mcp/importable` sends another tool's
  variable and header names, never their values, and each server's command name, arguments and
  URL with every credential in them masked (`mcp_discovery.masked_args` / `masked_url`). For a
  settings file of that tool it could not read, it sends the file's path and why, never the file's
  text (`onboarding_import.floors.why_unreadable`);
  `GET /api/mcp` sends no server's definition at all. A remote server's headers are resolved from
  the store when the native client connects, against the server's own owner (below), and sent on
  each request, never written anywhere.
- **A remote MCP server's OAuth sign-in** (`mcp_oauth.py`). Its tokens and any client secret live in
  the credential store under the server's own owner; its spec's `signIn` holds references and what
  the grant is for, and never reaches another tool's copy. The connection reads the token from the
  store at each request and renews it there, and never sends it to a URL outside the resource it was
  issued for (RFC 8707 `resource`): an edit that changes the server's address drops the sign-in, and
  a hand edit that does so makes the connection refuse to send it. A renewal the authorization server
  refuses deletes the tokens, with a security-log row and a notification. The sign-in is the
  authorization code flow with PKCE (S256 only) and a single-use 256-bit `state`, and an answer's
  `iss` must name the authorization server the sign-in started with (RFC 9207). Its callback,
  `GET /api/mcp/oauth/callback`, is reachable without a dashboard session (`token_auth._BYPASS_EXACT`),
  because the browser comes back to `127.0.0.1` and a dashboard opened at `localhost` has no cookie
  there. That opens nothing: a request is matched only to a sign-in the owner started from the Tools
  page, by its `state`, within ten minutes, and its code is exchanged only with that sign-in's PKCE
  verifier, which never leaves the gateway's memory. Every request a sign-in makes goes through the
  egress guard (`net.policy.MCP_SIGN_IN`, no redirects): only the server's own host may be private,
  and the authorization server must be on HTTPS unless the server itself is plain HTTP on this
  machine. The connection itself asks the guard about the server's URL before every use, for the
  run the call is made for (`net.policy.MCP_SERVER`): the owner's Denied hosts and the metadata
  service are refused, and a run's egress tier holds for every call to a remote server.
- **A reference resolves only against its own owner** (`SecretOwner.holds`): an app's settings,
  its instances and its `{app}:{server}` MCP servers resolve only that app's keys; core's
  settings resolve every key no app holds, the Secrets-panel vault included. A settings file is
  text an app can write, so a reference naming another owner's key is refused where it is used
  (`ForeignSecretReference`, and a `denied` security-log row naming the app and the key) and
  where it is saved (400). There is no grant: an app that needs a key has it stored under itself.
- **Private home** (`atomic_write.py`): a file the atomic writers put under the home —
  `atomic_write`, and `atomic_json_write` for `mcp.json` and the agent config — is 0600
  in a 0700 directory, and a wider mode is refused. `config.json`, an app's `data/config.json`,
  provider instance records, `mcp.json`, the agent config, `.env` and `auth/` are all written
  that way; `.local_secret`, `telemetry_salt` and `.app_secret` have writers of their own that
  create them 0600. A database is private from its first byte too: every store opens its
  database through `sqlite_compat.connect` or `connect_shared`, which make the file 0600 in a
  0700 folder before SQLite first opens it (`atomic_write.make_private_database`), so the
  journal, write-ahead log and shared-memory files SQLite makes beside it with the database
  file's own mode are 0600 as well, and one an earlier version left readable is tightened when
  it next opens (`tests/test_a_database_is_private_from_its_first_byte.py`). A file written
  some other way (a log, a lock) keeps the umask mode inside the 0700 home.
- **Private archives and exports** (`atomic_write.private_file`): a snapshot, the manifest beside
  it, a `backup export` folder and its manifest, a project export, a memory export and a pack are
  0600 from their first byte, in folders made 0700, wherever they are written: the bytes go to a
  temp file beside the destination that is created 0600, and the finished file is renamed into
  place. A snapshot's members are stamped 0600 (folders 0700), so a restore's staging copy, the
  files a restore puts back and a `tar -x` by hand are private too, and a restore writes the audit
  key back 0600 from its first byte. `tests/test_archive_writer_census.py` holds every archive
  writer to it.
- **Credential-free snapshots** (`durability/inventory.py`, `credential=True`): `.env`,
  `.env.pre-keychain`, `.local_secret`, and an older release's `credentials.json` (kept only
  while the Doctor lists a value in it the boot move could not settle) never enter a snapshot,
  and a per-app `.app_secret` enters neither a snapshot nor an export. No settings file an archive
  carries holds a stored value (`tests/test_export_carries_no_credential_store_value.py`
  searches every member for every value the store holds). What a reference cannot cover (copies
  made before the upgrade, a value with a NUL character, Claude Code's own config) is in
  [limitations.md §6](limitations.md).
- **Each home's own keychain namespace** (`config/credentials.py`, `keychain_service`): the OS
  keychain is the machine's, so every keychain read, write, delete and index entry names the home's
  own service there: `personalclaw` for the default home, `personalclaw-<id>` for any other, the id
  minted once and kept in the home, never derived from its path. A dev, scratch or test home never
  lists, reads, mirrors into its children's environment, overwrites or deletes the default home's
  secrets. `tests/test_keychain_namespace_census.py` fails a keychain call that names any other
  service, and an id file that holds no id turns the keychain off for that home rather than falling
  back to another's name.
- **One home, and read-only places outside it only by choice** (`outside_home.py`): PersonalClaw
  installs, writes and deletes inside its home. Skills install into `<home>/skills`, the workspace
  defaults to `<home>/workspace`, and neither a session restart nor an MCP sync writes
  `~/.mcp.json`. A place outside the home that is worth reading (the skills folder AI tools share,
  the machine-wide Hugging Face folder with its `huggingface-cli login` token, a subscription
  provider's sign-in, another agent tool's setup) is declared in that module, is off until the owner
  turns it on in Settings → Security → Outside PersonalClaw's home (`security.outside_home`, a
  loosening the PATCH asks to confirm), and is only read. Another agent tool's setup (Claude Code's
  `~/.claude`, `~/.claude.json` and the projects it lists; Codex's `~/.codex`) is also read for one
  request when the owner presses **Look in** it on the Tools page or in onboarding's Bring your setup
  over (`?look_in=setup:<tool>`): every reader of it asks `outside_home.readable`, and a page load,
  a background refresh or an import that names no tool reads only the setups turned on
  (`tests/test_another_tools_setup_is_read_only_when_you_ask.py` watches the reads themselves). Some libraries a feature loads would write outside the home, or
  report on their use, by themselves, so every `personalclaw` command, and every child it starts,
  tells each one not to with the library's own setting (`library_env.py`): the Hugging Face
  library reads no token it finds by itself (`HF_HUB_DISABLE_IMPLICIT_TOKEN`), downloads over its
  HTTP client rather than its Xet transfer client, which opens connections of its own
  (`HF_HUB_DISABLE_XET`), and fetches and keeps no list of AI tools in the shared folder and sends
  no usage pings (`HF_HUB_DISABLE_TELEMETRY`); onnxruntime starts none of its maker's telemetry,
  the device identifier and machine description it would keep under the user's home and upload
  (`ORT_DISABLE_TELEMETRY`); and the language pack behind the code map keeps its grammars in the
  home and downloads none itself (`TREE_SITTER_LANGUAGE_PACK_CACHE_DIR`, and
  `TREE_SITTER_LANGUAGE_PACK_MANIFEST_URL` naming a file there). An app's download passes the
  token PersonalClaw resolved, or none.
  An ACP adapter an agent app needs is npm-installed only as you install or enable the app, never
  at a gateway start (`acp/cli_resolve.py`). Deleting a skill that lives outside the home is refused (409). The
  Files page has no root for the home itself, whose `config.json`, `mcp.json` and automations it
  used to let you edit, only for the work folders inside it.
  `tests/test_personalclaw_stays_inside_its_home.py` fails on code that names a location in the
  user's real home anywhere but that module and a reviewed list of guards and owner-driven actions
  (the service installer, the Claude Code importer, the terminal, the folder picker).
- **A path another machine or an archive names goes nowhere but where it may be written**
  (`record_ids.is_path_in_store`: the name's shape, and the path once every symlink is followed;
  for a file of a store, the name's shape, and no link on the way, below). A sync pull resolves
  every path a peer names before anything of its change is written: each object's key, each path
  its export's manifest declares, the file each of its rows stands for, and its machine id, which
  names its folder of the remote. One outside the export it came in or the store it names is
  refused, nothing of that change is taken in, and the sync report names the path
  (`durability/pull_engine.py`). **Nothing a restore, an import, a pack's install or a sync
  brings is written through a link the home holds, nothing an export sends is read through one,
  and no lock is opened through one** (`durability/home_paths.py`): where the home has a symbolic
  link at the path an item would be written to or read from, at a folder on the way to it, or
  beside a database where SQLite keeps its log, or a file there with another name (a hard link),
  the item is left as it is. It is not written, nothing is read through the link, a replace does
  not move it aside, and the result names the link: a restore's last line and the parts it left
  unchanged, an import's summary, a project archive's or a pack's refused import (a pack lands
  whole or not at all, and its uninstall removes nothing through one), the conflict review's
  refusal, and the sync report, whose pull is held until the link is gone. Every write those doors
  make takes its path from `home_paths.home_path`. A sync's export and the backup export read the
  home by `home_paths.export_path`: a store behind a link, and a file or a folder of a store that
  is one, stay out of the copy and are named among the files it could not carry, and none of
  their records reads as deleted; the machine id every copy names is read the same way, and a link
  at it refuses the export whole. Every lock in the home is opened by `home_paths.open_lock`,
  which never empties its file and refuses a link at its name; a request stopped by one is
  answered `409 link_in_the_way`, naming it. `tests/test_home_path_census.py` fails a write, an
  export's read or a lock that takes its path from anywhere else. A replace restore used to move a
  store aside, leave a link where it found one, and copy the snapshot's file to its path, wherever
  the link led; a project import and a pack's install wrote through a folder that was a link; an
  export sent what a linked store, or a linked machine id, led to the other machines; and a store's
  lock emptied the file a link at its name led to. A transport whose remote is a folder on this
  machine (Folder Sync's shared folder, Git Sync's clone) holds its keys to the same rule, since
  whoever else writes that folder can put a link to any file of this machine's in it: a key outside
  the folder,
  or one that leads out of it through a link, is neither read, written nor removed, and the
  transport says which (`sync_transports.base.KeysRefused`). A sync removes only this machine's
  own copies that a newer one replaced (`durability/published.py`), and Folder Sync removes each
  through the folders on its way opened without following a link, so one made a link since its key
  was looked at leads nowhere. The sync refuses the peer's change such a key is in,
  whole, fails a cycle whose registry, salt or push is refused, and names the keys in its report
  either way. A pack whose name or component id would build a path outside
  its store is refused before any of it is parsed or written, and every path the pack layout
  builds is checked again, with no link the home holds on the way
  (`packs/import_.py::component_path`). A snapshot's tar refuses `..`,
  absolute names and links as it is extracted (`snapshot._data_filter`), and an import's zip
  extracts no member that climbs out (`portability.apply_import_zip`). A pulled key used to be
  joined onto the pull's scratch folder as it came, so `../` in one wrote anywhere this machine's
  user may, and a pack's prompt id of `../../../name` wrote its file outside the home.
- **A file-backed artifact points only where those surfaces reach** (`artifacts/source_files.py`).
  Its `source_path` is a live pointer: every read of the artifact reads the file, and a save that
  carries a body, or a revert, writes it. A create with no body starts as the file and never writes
  it, and like a save it names the revision of the copy it read (`If-Match`), so a pointer at an
  existing file is taken only by a caller who has read it. The pointer must pass the file
  explorer's own check (`file_roots.admit`: symlinks and `..` resolved, no credential or secret
  file) against the explorer's roots, or, for the owner, a loop's own folder, where an unbound
  loop keeps the deliverable its completion graduates. Anything else is refused when it is set
  (`400`, or `403` for an app), before the file is opened, so the answer says nothing about what
  the file holds.
  Every read and write checks it again, so a pointer recorded earlier, or one whose file was later
  swapped for a symlink out, touches nothing.
- **A loop's own folder is the owner's** (`file_roots.all_dashboard_roots`). Files shows it when the
  loop keeps its deliverable there or is a code loop with no workspace, so a graduated deliverable's
  Source file opens, and never to an app: the folder holds the brief the loop's worker reads every
  cycle. The watchdog and the judge read a deliverable only when it resolves inside the loop's
  workspace or its own folder (`loop.files.file_inside`: symlinks and `..` resolved), so a
  `primary_deliverable` named out of either reads nothing.
- **A CSV PersonalClaw writes holds no cell a spreadsheet would evaluate**
  (`documents/writers/csv_writer.py`, `artifacts/native.py`). A spreadsheet program opening a CSV
  reads a cell whose text begins with `=`, `+`, `-` or `@` as a formula (some drop a leading tab or
  carriage return first, and a byte-order mark that opens the file is dropped before the first cell
  is read), and a CSV's cells come from wherever the agent, an app or a workflow found them. So the
  csv writer, which `sheet_create` and an app's `get_writer("csv")` render through, writes a cell
  whose text begins with any of those six characters with a single quote in front of it, and the
  spreadsheet shows it as text. The artifact store keeps the text of every CSV artifact by the same
  rule (`render_csv_text`), whoever saves it: the agent's `artifact_save` and `artifact_update`, a
  workflow's `publish:` and its `artifact-update` step, an app's request, your own edit in the
  Artifacts editor, and a revert to an earlier version. The rule sits in the store rather than at
  those doors because the agent's tools and a workflow's steps reach the store directly. Text that
  is a number holds nothing to compute and is written as it is: an optional sign, an optional
  currency sign, then only digits, commas and periods, an optional exponent, and an optional percent
  or currency sign at the end (`-20`, `-1,234.56`, `-$45.20`, `-12.5%`, `+1.5e3`), so a negative
  number stays a number; a date, and a cell of dashes alone (a table's "none"), are written as they
  are too. A formula cell is written as its text by the same rule, since a CSV cannot say that a
  cell is one: a formula belongs in an xlsx. CSV text the `csv` module cannot read (a field longer
  than 131,072 characters) is refused with its reason, and nothing is written. A CSV artifact saved
  before this rule keeps its text until it is next written. A file the agent writes with its file
  tools is a file in your folders, not a CSV PersonalClaw makes, and is kept as written.
- **The agent writes an artifact's next version only over the version it read**
  (`artifacts/bases.py`, `mcp_artifacts.py`, `artifacts/native.py`). A write that replaces an
  artifact's whole text is made from a copy of it, and an edit you saved after the agent read
  that copy, in the Artifacts editor or the document editor, an app's write, or the agent's in
  another chat, would otherwise be undone without a word. So `artifact_get` hands the agent the
  text of every kind (a Word document as the markdown `document_create` takes, a deck as the
  outline `deck_create` takes, a workbook as the JSON `sheet_create` takes, a PDF as its pages'
  text), masked and fenced as data, in parts of 40,000 characters, with its version and its base
  (`v3-1a2b3c4d5e6f7a8b`: the version and the revision of the text it showed). `artifact_update`,
  `artifact_save` on an existing slug, and the document, sheet and deck tools naming an existing
  artifact by slug or by name take that base, and the store compares it under its lock, the
  revision for text (a plain Save changes the text and keeps the version number) and the version
  for a document's file (every write of one cuts a version). A base older than the live version is
  refused with both versions and who made the newer one, a write with no base is refused with the
  read to make first, and nothing is written; the refusal comes before anyone is asked to approve
  the call. A new artifact needs no base. A marker the agent writes back for a value its reading
  masked is put back from the version it read, as a text artifact's save puts it back. A
  workflow's step writes what its run made from no copy of the artifact, so it names no base:
  instead the store reads the live version inside the write. And a current base proves only that
  the agent read your edit, not that what it wrote kept it, while a chat's Trust can approve the
  write with nobody looking: so whoever writes over text another writer left that no version
  holds (your plain Save, an app's), the agent, an app or a workflow's step, the store first keeps
  it as its own version, credited to whoever made it, and the agent's reply names that version.
  Revert brings it back. Your own editor's saves name the revision or the version they opened,
  so a save over a version the agent wrote meanwhile is refused and your draft stays on the page,
  and a plain Save of yours replaces what the editor showed you and keeps the version number.
- **Memory privacy** (`session_restrictions.py`, `memory_writes.py`):
  temporary/incognito sessions gate memory reads/writes; the memory, knowledge
  and vocabulary stores refuse every write made for one, by any path; the
  agent's file tools and shell change nothing in the memory folders for one,
  refused before anyone is asked and fenced read-only by the OS sandbox around
  its commands, and for a temporary one read nothing there either, fenced
  unreadable (what that leaves open:
  [limitations §19](limitations.md#19-a-private-chat-is-kept-out-of-the-memory-folders-not-out-of-every-store));
  nothing in the artifact library changes for one, through its agent's artifact
  tools, a mention or a run it starts, as the library's routes hold it; no
  background model (titles, tags, follow-ups, a condensed history, suggestions)
  is given anything of one; and nothing of one reaches any model but the one its
  turn runs on (the embedding model, so its tools are ranked by their words, a
  tool's or a subagent's model, the image reader, a fallback, an agent CLI's tool
  process, a side question asked beside it: `memory_writes.model_may_read`;
  the work it starts away from its turn, its subagents and the steps of a run it
  started, is handed that model with its mode, after a restart too, the mode
  read by one reader with the live chat first, so a chat's first-turn work keeps
  it before its transcript is written, and a mode nothing can say is taken as a
  Temporary chat's), except what the person gives the chat in a form its model
  cannot read (an
  attached file, a shared screen), which the model set up for it reads. Its work
  leaves nothing behind that lasts after it and runs as work of its own, on its
  own model: no loop or project is made, started or steered for it, no
  automation, scheduled task or lifecycle trigger made or changed, no callback
  registered (`lasting_work.py`). Nor in a record that other work reads later: no
  skill drafted or kept, no proposal filed for review, no task, task list or
  project made or changed on the Tasks page, no Inbox item posted, no loop's spec
  or plan changed; and a Temporary chat's workflow runs are stopped and deleted
  once it has ended, an Incognito chat's once it is deleted
  (`workflows/private_runs.py`). Whether
  work may read memory at all is one answer, `memory_reads.reach_of`: a
  Temporary chat's work reads none (its subagents, theirs, and the steps of a run
  it started included), and neither does an app's (a conversation it started, an
  agent run it asked for, an agent its scheduled job started, an agent working for
  any of them) unless the app holds the `memory` permission — otherwise an app's
  own conversation, or its job, would be a second door to the memory its token is
  refused. The same grant governs writes: the memory store refuses every change an
  app's work makes without it (`memory_writes.check_memory_statement`), and with
  it records the app as the source (`memory_writes.written_by`), so an app cannot
  write as you. Nor can anyone else in a conversation with you: a turn someone
  other than you asked for (`turn_source.asked_by`) changes none of your memory on
  its own (`memory_writes.asker`, read by the stores, the turn's own learning,
  every request its tools make and the gate an agent CLI's own tools ask), and
  what its memory tools ask for waits for your own Allow (`dashboard/memory_holds.py`).
  Nor through work that turn starts that outlives it: a workflow run, a loop and
  a callback record who asked on their own record and are held to it for as long
  as they last, and an automation is not made on their say-so (`lasting_work.py`).

## OWASP Agentic Security (ASI) Top-10 mapping

Status legend: **enforced** (a resolvable control gates it) · **in progress
(plan N)** (control is designed, not yet landed) · **documented limitation** (a
deliberate, disclosed gap — see [limitations.md](limitations.md)). A row may claim
`enforced` only with a resolvable `file:path` citation.

| ASI category | Control | Code citation (`file:path`) | Status |
|---|---|---|---|
| **ASI01** Agent goal / instruction manipulation | Untrusted-content fencing, approval modes, and data-not-instructions framing on recalled memory; an app token cannot write your agents, skills, prompts, routing notes, or a project's overview, brief, instructions or folder, or post into your chats, rooms or inbox answers | `security.py::fence_untrusted`; `dashboard/handlers/memory.py` (recall framing); `apps/permissions.py` (`ROUTE_AUTHZ`, `OWNER_ONLY_API_PATHS["/api/onboarding/import"]`, `OWNER_ONLY_API_PATHS["/api/send-message"]`) | enforced |
| **ASI02** Tool misuse | Command deny/suspicious patterns, task-mode gating, OS child sandbox | `security.py` (`BUILTIN_DENIED_COMMAND_PATTERNS`, `SUSPICIOUS_BASH_PATTERNS`); `task_modes.py`; `sandbox.py` | enforced |
| **ASI03** Identity & privilege abuse | App-scoped tokens, reverse-proxy credential stripping, permission middleware (holds even in `none` mode), an owner-only registry plus per-route declarations that refuse an undeclared write, settings scoped to the fields a manifest declares, conversations held to the app that started them (reads and socket frames included) and run under its own `agent` grant, notifications held to the app that raised them, each provider held to its own app, your model providers, model bindings, first-run setup, Doctor fixes and maintenance, updates and tool switches the owner's | `dashboard/handlers/apps.py::api_app_proxy`; `dashboard/token_auth.py`; `dashboard/server.py` (`_dev_user_middleware`, `app_permission_middleware`, `_ownership_denial`); `apps/permissions.py` (`OWNER_ONLY_API_PATHS`, `ROUTE_AUTHZ`, `undeclared_security_route`, `app_conversation_auto_approves`); `dashboard/ws_state.py` (`frame_subject`, `_own_notifications`); `dashboard/state.py::notification_reaches` | enforced |
| **ASI04** Supply-chain & dependency risk | Quarantine → scan → consent → install; `dangerous` verdict terminal; scanned-tree == installed-tree (tooling left out of both, nothing skipped in what remains, an unreadable file disclosed); staging never follows a link out of the bundle; a registry listing names a public `https://` repo, checked again at every connection its fetch makes | `apps/app_manager.py::install`; `apps/staging.py`; `supply_chain.py` (`SkillScanner`, `Verdict`, `never_installed`); `apps/catalog.py::listing_repo_refusal`; `net/git.py::run_git_guarded` | enforced |
| **ASI05** Unauthorized code execution | Command screening + OS sandbox + credential-env denylist; an app token cannot define an MCP server or an automation, and the owner confirms an automation step that approves its own tool calls | `security.py`; `sandbox.py`; `apps/permissions.py` (`OWNER_ONLY_API_PATHS["/api/mcp"]`, `ROUTE_AUTHZ`); `automation_posture.py` | enforced *(an installed app's own code runs as you: documented limitation, [limitations.md](limitations.md) §7)* |
| **ASI06** Memory & context poisoning | Fenced recall; learning reads only the words a person typed, never a fenced span or the text the platform put around them (a saved prompt's, a file's, a theme's persona, an automation's message), and what it took from such text before is retracted or offered for review; the corrections, preferences and glossary lines learned from her words are written live, skills and self-model principles only proposed; nothing learned replaces, retires or rewrites a lesson or fact she set herself, and a learned lesson that would is kept out while she is asked which to keep; temporary/incognito session modes; an app writes memory, lessons included, only with its `memory` grant | `dashboard/handlers/memory.py`; `own_words.py`; `learning/hygiene.py`; `learning/composed_text.py`; `learning/lesson_conflicts.py`; `vector_memory.py::only_a_person_replaces`; `after_turn_review.py`; `session_restrictions.py`; `apps/permissions.py::MEMORY_API_PATHS` | enforced |
| **ASI07** Insecure inter-agent / inbound comms | Fail-closed inbound surface + fencing at ingestion | *(owned)* | in progress (plans 41, 24) |
| **ASI08** Cascading failures / denial-of-wallet | Circuit breakers, budgets, spend caps | *(owned)* | in progress |
| **ASI09** Trust exploitation / social engineering | Approval surfaces, expiring YOLO with `on_disable` callbacks, consent-gated installs | `trust_mode.py`; `apps/app_manager.py::install` | enforced |
| **ASI10** Rogue / runaway agents | Tamper-evident audit log + YOLO kill/disable (auto-approve is revocable, firing disable callbacks) | `sel.py::SecurityEventLog`; `trust_mode.py` (`on_disable`) | enforced *(incident-flag on breaker trip: in progress)* |

## What we deliberately don't defend against

PersonalClaw is a single-owner, self-hosted tool. Some things are out of scope by
design, not by omission — stating them keeps the in-scope claims credible.

- **Physical access to the machine.** If someone has your unlocked device, they
  have your agent. PersonalClaw is not a defense against local physical access.
- **A compromised host OS or OS account.** The controls above assume the machine
  itself, and the account PersonalClaw runs under, are trustworthy. Root on the
  box, a compromised user account, or malware already on the host are outside the
  model — they sit *below* the boundaries PersonalClaw defends.
- **The owner's own auto-approve (YOLO) choices.** PersonalClaw lets its owner
  lower their own guardrails. Choosing auto-approve, or running an external ACP
  agent under YOLO (where gating rides system-prompt framing, not rails — see
  [limitations.md](limitations.md)), is an owner decision, not a vulnerability.
- **Tampering with the installed package before startup.** The baseline denylist's
  digest proves the patterns in force are the ones that shipped *with this process*; it
  cannot prove which patterns were shipped. Whoever can edit the installed package
  before PersonalClaw starts sets the baseline. That control is anti-drift and
  anti-LLM-tamper, not anti-owner — see [Baseline denylist
  integrity](#baseline-denylist-integrity-anti-drift-and-anti-llm-tamper-not-anti-owner).
- **An app's own outbound network traffic.** The `network` app permission is
  declaration-only (disclosed at install consent), not a gateway-enforced
  boundary — an app backend is its own OS process with its own network stack. See
  [limitations.md](limitations.md). What *is* enforced is the supply-chain gate on
  what you install and the app's gateway-mediated (`api`) reach.
- **What an installed app's FRONTEND does in the dashboard page.** An app's UI bundle
  is imported into the dashboard's own origin (no iframe, sharing the host React
  instance), so it has the host `document`, `localStorage`, the owner's cookie and
  authenticated same-origin `/api/*` reach. The `api` allowlist binds the requests the
  app's backend and its SDK client make, not its page code: a bare `fetch` from app UI
  carries no app identity, so `app_permission_middleware` treats it as the owner. Telling
  the two apart requires a separate origin for app UI, so this is disclosed — at install
  consent and in [limitations.md](limitations.md) §4 — rather than enforced. The
  control is the supply-chain gate on what you install.
- **What an installed app's own code does on your machine.** The permissions bind the
  app's token, and its code does not need the token. A backend on the host, a provider
  module, an MCP server command or a setup hook runs under your account, so it can edit
  `config.json` on disk, add a server to `mcp.json`, or read `session_key` and sign itself
  any token it likes. Confining that code takes per-app OS isolation, which today exists
  only for a backend that names a sandbox tier. So this is disclosed rather than enforced:
  install consent names the app's server process, install hook and MCP server commands, and
  [limitations.md](limitations.md) §7 says what that code can do. The control is the
  supply-chain gate on what you install.

Each of these has a rationale above; none is an accident. Gaps discovered while
maintaining this document are routed to the security-hardening track as
candidates, never patched inline in a docs change.
