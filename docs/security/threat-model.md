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
  store and keys, in the home in use and the default one, or another tool's sign-in, and one
  returning a credential folder under `$HOME`. The first is found the way the command's shell
  would find it (`command_paths.named_paths`, the reading the owner-only fence uses too).
  Defence in depth: [limitations §13](limitations.md#13-the-agents-shell-is-screened-not-fenced-from-your-credential-files).
- **OS child sandbox** (`sandbox.py`) with a credential-env denylist so secrets
  never reach a sandboxed child.
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
  and moves your chats onto it. The rest of the first-run setup (`/api/onboarding`) is a
  family of its own, owner-only the same way, reads included: its status read and model
  check name your chat binding, its local-model probe and LAN sweep have the gateway look
  for model servers on your machine and network, and its state is your setup's progress.
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
  (`workflows/supervisor_policy.py::POLICY_OVERRIDE_SECURITY`).
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
  payloads.
- **Webhook auth** (`dashboard/handlers/hooks.py::_verify_hook_token`): a
  constant-time (`hmac.compare_digest`) token check; no configured token means
  every request is refused, and denials log to the Security Event Log.
- **Egress chokepoint** (`net/client.py` + `net/guard.py` + `net/policy.py`): the
  single outbound-HTTP seam with named policies, layered by
  `net/policy.py::egress_policy_for`.
- **What a channel is handed is masked, once, by core** (`channel_delivery.py::MaskedDelivery`).
  A channel app sends what it is handed to a service outside the machine, so every channel's
  delivery handle is registered behind the mask, and every path to a channel (a reply, an owner
  notification, a rich payload's strings, an automation's result, an approval's title, purpose and
  input, a chat mirror, a stream's progress) hands it text masked with `redact_for_display`. A new
  sending method in the `ChannelDelivery` protocol is masked or listed as sending no text, or
  `tests/test_channels_are_handed_masked_text.py` fails. What an app sends on its own paths, text it
  builds or relays without core, is the app's to mask.

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
  turn. PersonalClaw's own chores run in the background session (a title, follow-ups,
  suggestions, memory consolidation), which no person types into, so every prompt it is handed
  is masked there (`NativeAgentRuntime.stream`), and consolidation's writes put each hidden value
  back from the fact it read (`memory_formation._as_stored`, `history._kept_lines`). A one-shot
  model call with no agent behind it (a workflow's infer, judge and visualize steps, a knowledge
  digest) passes the outbound scan at the model-call guard instead (`guardrails.scan_mode`). The
  mask is idempotent (a value that is already a mask is not taken for a credential), so a read
  already masked for a UI passes unchanged. A projection or a `tool_result_get` slice is cut from
  the masked text (`project_and_retain`), so it never splits a key. The agent's file tools keep a
  hidden value where it stands (`masked_edit`, `keep_masked_lines`): an edit keeps every stored
  byte outside the text it replaces, and a change that would move, copy or rewrite a hidden value
  is refused. A tool that needs a credential takes `{{secret:NAME}}`: `bash` fills in a credential
  the owner stored in Settings → Secrets as the command runs (never the gateway's environment,
  never a setting's own key) and masks every value it handed the command out of what the command
  prints (`redact_known_values`). Text handed to a model keeps the reference as the name: a
  trigger whose action is a model turn (`ActionProvider.hands_config_to_a_model`) and a workflow's
  stage, infer and visualize steps do not fill it in, so the agent's tools do. What this cannot
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
  machine.
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
  create them 0600. A file written some other way (a log, a lock, a database) keeps the umask
  mode inside the 0700 home.
- **Credential-free snapshots** (`durability/inventory.py`, `credential=True`): `.env`,
  `.env.pre-keychain`, `.local_secret`, and an older release's `credentials.json` (kept only
  while the Doctor lists a value in it the boot move could not settle) never enter a snapshot,
  and a per-app `.app_secret` enters neither a snapshot nor an export. No settings file an archive
  carries holds a stored value (`tests/test_export_carries_no_credential_store_value.py`
  searches every member for every value the store holds). What a reference cannot cover (copies
  made before the upgrade, a value with a NUL character, Claude Code's own config) is in
  [limitations.md §6](limitations.md).
- **One home, and read-only places outside it only by choice** (`outside_home.py`): PersonalClaw
  installs, writes and deletes inside its home. Skills install into `<home>/skills`, the workspace
  defaults to `<home>/workspace`, and neither a session restart nor an MCP sync writes
  `~/.mcp.json`. A place outside the home that is worth reading (the skills folder AI tools share,
  the machine-wide Hugging Face folder with its `huggingface-cli login` token, a subscription
  provider's sign-in) is declared in that module, is off until the owner turns it on in Settings →
  Security → Outside PersonalClaw's home (`security.outside_home`, a loosening the PATCH asks to
  confirm), and is only read. Some libraries a feature loads would write outside the home, or
  report on their use, by themselves, so every `personalclaw` command, and every child it starts,
  tells each one not to with the library's own setting (`library_env.py`): the Hugging Face
  library reads no token it finds by itself (`HF_HUB_DISABLE_IMPLICIT_TOKEN`), keeps its Xet
  transfer cache in the home whatever folder a download was told to fill (`HF_XET_CACHE`), and
  fetches and keeps no list of AI tools in the shared folder and sends no usage pings
  (`HF_HUB_DISABLE_TELEMETRY`); onnxruntime starts none of its maker's telemetry, the device
  identifier and machine description it would keep under the user's home and upload
  (`ORT_DISABLE_TELEMETRY`); and the code map's grammars download into the home
  (`TREE_SITTER_LANGUAGE_PACK_CACHE_DIR`). An app's download passes the token PersonalClaw
  resolved, or none.
  An ACP adapter an agent app needs is npm-installed only as you install or enable the app, never
  at a gateway start (`acp/cli_resolve.py`). Deleting a skill that lives outside the home is refused (409). The
  Files page has no root for the home itself, whose `config.json`, `mcp.json` and automations it
  used to let you edit, only for the work folders inside it.
  `tests/test_personalclaw_stays_inside_its_home.py` fails on code that names a location in the
  user's real home anywhere but that module and a reviewed list of guards and owner-driven actions
  (the service installer, the Claude Code importer, the terminal, the folder picker).
- **A path another machine or an archive names goes nowhere but where it may be written**
  (`record_ids.is_path_in_store`: the name's shape, and the path once every symlink is followed).
  A sync pull resolves every path a peer names before anything of its change is written: each
  object's key, each path its export's manifest declares, the file each of its rows stands for,
  and its machine id, which names its folder of the remote. One outside the export it came in or
  the store it names is refused, nothing of that change is taken in, and the sync report names the
  path (`durability/pull_engine.py`). A transport whose remote is a folder on this machine (Folder
  Sync's shared folder, Git Sync's clone) holds its keys to the same rule, since whoever else
  writes that folder can put a link to any file of this machine's in it: a key outside the folder,
  or one that leads out of it through a link, is neither read nor written, and the transport says
  which (`sync_transports.base.KeysRefused`). The sync refuses the peer's change such a key is in,
  whole, fails a cycle whose registry, salt or push is refused, and names the keys in its report
  either way. A pack whose name or component id would build a path outside
  its store is refused before any of it is parsed or written, and every path the pack layout
  builds is checked again (`packs/import_.py::component_path`). A snapshot's tar refuses `..`,
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
- **Memory privacy** (`session_restrictions.py`): temporary/incognito sessions
  gate memory reads/writes.

## OWASP Agentic Security (ASI) Top-10 mapping

Status legend: **enforced** (a resolvable control gates it) · **in progress
(plan N)** (control is designed, not yet landed) · **documented limitation** (a
deliberate, disclosed gap — see [limitations.md](limitations.md)). A row may claim
`enforced` only with a resolvable `file:path` citation.

| ASI category | Control | Code citation (`file:path`) | Status |
|---|---|---|---|
| **ASI01** Agent goal / instruction manipulation | Untrusted-content fencing, approval modes, and data-not-instructions framing on recalled memory; an app token cannot write your agents, skills, prompts or routing notes, or post into your chats, rooms or inbox answers | `security.py::fence_untrusted`; `dashboard/handlers/memory.py` (recall framing); `apps/permissions.py` (`ROUTE_AUTHZ`, `OWNER_ONLY_API_PATHS["/api/onboarding/import"]`, `OWNER_ONLY_API_PATHS["/api/send-message"]`) | enforced |
| **ASI02** Tool misuse | Command deny/suspicious patterns, task-mode gating, OS child sandbox | `security.py` (`BUILTIN_DENIED_COMMAND_PATTERNS`, `SUSPICIOUS_BASH_PATTERNS`); `task_modes.py`; `sandbox.py` | enforced |
| **ASI03** Identity & privilege abuse | App-scoped tokens, reverse-proxy credential stripping, permission middleware (holds even in `none` mode), an owner-only registry plus per-route declarations that refuse an undeclared write, settings scoped to the fields a manifest declares, conversations held to the app that started them (reads and socket frames included) and run under its own `agent` grant, notifications held to the app that raised them, each provider held to its own app, your model providers, model bindings and first-run setup the owner's | `dashboard/handlers/apps.py::api_app_proxy`; `dashboard/token_auth.py`; `dashboard/server.py` (`_dev_user_middleware`, `app_permission_middleware`, `_ownership_denial`); `apps/permissions.py` (`OWNER_ONLY_API_PATHS`, `ROUTE_AUTHZ`, `undeclared_security_route`, `app_conversation_auto_approves`); `dashboard/ws_state.py` (`frame_subject`, `_own_notifications`); `dashboard/state.py::notification_reaches` | enforced |
| **ASI04** Supply-chain & dependency risk | Quarantine → scan → consent → install; `dangerous` verdict terminal; scanned-tree == installed-tree (tooling left out of both, nothing skipped in what remains, an unreadable file disclosed); staging never follows a link out of the bundle; a registry listing names a public `https://` repo, checked again at every connection its fetch makes | `apps/app_manager.py::install`; `apps/staging.py`; `supply_chain.py` (`SkillScanner`, `Verdict`, `never_installed`); `apps/catalog.py::listing_repo_refusal`; `net/git.py::run_git_guarded` | enforced |
| **ASI05** Unauthorized code execution | Command screening + OS sandbox + credential-env denylist; an app token cannot define an MCP server or an automation, and the owner confirms an automation step that approves its own tool calls | `security.py`; `sandbox.py`; `apps/permissions.py` (`OWNER_ONLY_API_PATHS["/api/mcp"]`, `ROUTE_AUTHZ`); `automation_posture.py` | enforced *(an installed app's own code runs as you: documented limitation, [limitations.md](limitations.md) §7)* |
| **ASI06** Memory & context poisoning | Fenced recall, propose-only (never live-write) learning, temporary/incognito session modes; an app writes memory, lessons included, only with its `memory` grant | `dashboard/handlers/memory.py`; `after_turn_review.py` (propose-only queue); `session_restrictions.py`; `apps/permissions.py::MEMORY_API_PATHS` | enforced |
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
