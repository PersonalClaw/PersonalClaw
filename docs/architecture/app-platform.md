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
| Native (32) | `apps/native/` in-package | seeded on first run, locked on (e.g. `native-agents`, `personalclaw-memory`, `ollama-models`, `bundled-chat`, the action bundles); may own its provider code — see [the native capability contract](#the-native-capability-contract-appsnative_contractpy) |
| First-party (68) | workspace `apps/` | Slack channel, hosted model providers, speech, Minutes/Growth dashboards |
| Third-party | user sources → `~/.personalclaw/apps/` | fixtures at `third-party-apps/` (`hello-search`, `demo-dashboard`) |

The gateway loads **installed copies** at `~/.personalclaw/apps/<name>/`.
Editing the repo `apps/` tree does nothing to a running gateway until you push
it via `POST /api/apps/{name}/update`, which runs the new code at once (see
[Unload and load](#unload-and-load-appsapp_runtimepy)). App sources for the Store are managed at
`/api/apps/sources` (`dashboard/handlers/apps.py`).

## Install lifecycle (`apps/app_manager.py`)

Install is: **copy → stage in quarantine → validate manifest → scan staged
content → platform gate → consent → pip deps → `setup.onInstall` hook (bounded
subprocess, 60s cap) → register providers/prompts/MCP servers/crons → start
backend**.

- **Quarantine first** — staged under `~/.personalclaw/apps/.quarantine/`;
  dangerous content never touches the live tree.
- **Staging never follows a link** (`apps/staging.py`) — install, update and the
  install preview copy the bundle from one survey of the whole tree, taken before
  anything is written, and every later gate reads that copy. A link to one of the
  bundle's own files is kept as that link. Anything that brings in bytes from
  elsewhere, or could, is refused with the offending path named: an absolute link,
  a link that leaves the bundle for even one step, a link to a folder (the scan does
  not descend into one), a link to nothing, a loop, a hard link to an outside file, a
  pipe, socket or device, a link into or out of `data/`, and `data` or
  `installed.json` as a link. A `repo#subdirectory` pointer must stay inside its
  clone. The gateway's own copies of an app's `data/` (update, keep-data uninstall,
  restore) copy links as links, and the gateway's own reads of files there (the
  app's `data/config.json`, for the config API, provider settings and the boot-time
  secret move) never follow a link (`apps/manager.read_app_owned_text`).
- **Tooling is never installed** — staging leaves out every entry named in
  `supply_chain.NEVER_INSTALLED_NAMES`, at any depth and in any letter case: `.git`,
  `.hg`, `.svn` (version-control metadata, whose tools run what it names),
  `__pycache__` (bytecode the interpreter would run instead of the scanned source)
  and `.venv`, `venv`, `.tox` (virtualenvs; the platform installs
  `pythonDependencies` itself, and a sidecar's engine into `apps/<app>/venv`). What is left out is not scanned, not in the consent
  digest and not installed; a link into it is refused. Everything else is the app,
  `node_modules` included: a JavaScript app's dependencies are code it runs, so they
  install and are scanned like any other file. Skills follow the same rule
  (`skills.marketplace.install_scanned`).
- **The scan** is the shared `SkillScanner` (`supply_chain.py`), and it reads the
  whole staged tree: no folder is skipped, every file a rule reads is read whole (up
  to 16 MB), and a file it cannot read is an `unscanned_file` finding in the review,
  never a silent pass. A *dangerous*
  verdict (or an invalid signature) is a terminal refusal, **non-overridable**.
  Each finding carries whether the code it sits in can run (`reachability`) and
  whether anything the app runs loads its file at all (`runtime` — the install
  dialog groups an app's own test files apart from the code it runs).
- **A registry listing names a remote repository** — a `repo` in a source's
  `app-registry.json` must be a plain `https://` URL (no credentials, no port), the
  form the published registry requires. A listing naming a local path, `file://`,
  `ssh`, `git@…` or `http://` is not listed. Installing a folder on this machine is
  the owner's own act (Install from URL, or adding the folder as a source).
- **Every install waits for consent** — a clean scan is not consent.
  `POST /api/apps/preview {source}` stages the source and returns what installing
  it grants and runs (`apps/disclosure.describe`: permissions, scheduled jobs and
  whether each is switched on, Python packages, the engine packages Install engine would
  put in a sidecar's environment, dashboard code, its own server process, the install
  hook, MCP servers, the programs it starts and the npm packages installed for it, and where
  outside its own folder it writes), what it needs that PersonalClaw does not install, the scan, and a
  `consent` digest of the staged bytes. `POST /api/apps {source, consent}` commits only if the bytes still
  have that digest; anything else — `confirm: true` included — answers 409 with
  the review. The install invariant is scanned-bytes == reviewed-bytes ==
  installed-bytes (no swap-after-scan window, and none after consent either).
- **An app this core cannot host is refused at every door**
  (`apps/core_version.check_core_compatibility`): the review, install, update, enable and
  gateway start, and the Store card carries the same verdict (`coreCompatibility`). It
  refuses an app whose `minPersonalClawVersion` is above this core, and one that names a
  core feature in `requiresCoreFeatures` this core does not offer (`apps/core_features`,
  published as `personalclaw.sdk.features`). Every core built between two releases reads the
  same version, so a floor cannot tell such cores apart and a feature can; a feature also
  holds in a source checkout, whose version does not parse. The refusal names the version
  and each feature this core lacks, and says to update PersonalClaw.
- **Update** is atomic with rollback: the previous install is preserved at
  `~/.personalclaw/apps/.{name}.rollback` for the duration. An update that changes
  what the app gets (compared with the installed copy's disclosure), or scans with
  warnings, needs the same `consent` digest (`POST /api/apps/preview {source, name}`);
  one that changes none of it needs none.
- **An update keeps the app's state** — what its folder holds that no bundle ships:
  `data/` (`sdk.util.app_data_dir`), copied into the new version before the swap so a
  failed update gives the old version its data exactly as it left it, and `venv/`, the
  Python environment a sidecar app's child runs in (`sdk.sidecar.sidecar_venv_dir`),
  moved across by rename after the swap and before `onUpdate` runs, however large the
  engine in it. A failed update hands back exactly what it moved; a crash in between is
  finished by `recover_interrupted_updates` at the next start. Everything else in the
  folder is the old version's files and goes with them. Removal takes `venv/` with the
  app's files; the keep-data rung keeps `data/` alone.
- **Finding an update** (`apps/catalog.updates_available`) — `/api/apps` marks an app
  whose source offers a newer version: a configured local source, by the app's name,
  and for an app installed from the Store, the pointer `installed.json` recorded
  (`url#app`, a registry listing's repo, or a single-app repository), read from the
  Store's own discovery caches. The Store's catalog read refreshes those under its
  budget and failure backoff, and the Apps page re-reads the list when that read
  lands. Nothing polls, and `/api/apps` never touches the network. The Update dialog
  starts from where the newer version was found, else from where the app was
  installed from (`updateSource`).
- **Removal** distinguishes deactivate (providers deregistered, files kept)
  from force-uninstall.

## What an app needs, and its engine

- **What it starts, installs and writes outside PersonalClaw** — three manifest declarations
  install consent shows under what the app runs (`apps/disclosure.describe`), each of which an
  update that adds or widens one asks for again:
  - `launches: [{program, why, inherits, inheritsWhile, hosts}]` (`manifest.LaunchedProgram`) — each
    program on this machine it starts by name, outside PersonalClaw: an agent app's own CLI, a
    tool. `inherits` is what it runs with, from a closed list. Three are the owner's: `sign-in`
    (the account the program is signed in to), `settings` (its own configuration folder) and
    `auto-approve-rules` (rules there that let it act without asking — consent says that what
    they allow, it does without asking here first). The fourth, `folder-settings`, is the
    program's settings in the folder it works in, such as a repository's own: they can add rules
    of that kind and commands for it to run, and consent names them apart, because they come
    from whoever wrote the folder. `inheritsWhile: {setting, value}` names a boolean setting of
    the app's provider when that decides it (Claude Code runs with all four only while *Isolated
    Claude settings* is off); consent words it with the setting's label and where it starts.
    `hosts` are the hosts the program reaches on the network (host names; a bare domain covers its
    subdomains), which consent names. The app's code may start the program toward those and the
    owner's Allowed hosts only: a launch whose command line names another host is refused before
    it starts, and every launch that reaches the network leaves an `egress_launch` audit row naming
    the app, the program and the host (`apps/launch_egress.py`, read at Python's own audit event
    for a process launch). A declared npm package covers the npm registry for `npm`, `npx`, `pnpm`,
    `yarn` and `bun`, and so does the package an `npx` entry names. An `npx` entry must name, in
    `npmPackage`, the npm package npx downloads and runs (no version: consent says each start
    fetches its newest version, runs it as the owner, and runs npm's install scripts). A program the
    owner chooses, such as a runbook action they wrote, is `program: "*"`: consent says "the
    programs you name for it", and the entry names no program core may start for the app.
  - An agent app passes its CLI per-session options with `register_acp_cli_entry(session_meta=…)`:
    a JSON object core adds as the `_meta` of every `session/new` and `session/load` it sends
    from the app's entry (the runtime, a resumed session, the readiness probe and a pooled
    connection). Claude Code uses it to keep an isolated session to its own config: the folder's
    settings are not loaded either.
  - `dependencies.npmPackages` — the npm packages core may install for the app into
    `<home>/acp-adapters` as it is installed or switched on (an agent app's ACP adapter), and
    that a runtime may otherwise fetch with `npx`. Consent says npm runs their install scripts. A
    package the app's own code runs with `npx` is not one of these; its `launches` entry names it.
  - `writes: [{path, why}]` (`manifest.ExternalWrite`) — each place outside the app's own folder
    its code writes: a path inside the PersonalClaw folder, or `~/…` in the home folder.

  Core does two of these things for an app itself, and holds both to the declaration
  (`apps/declared.py`, which reads the calling app off the stack the way `app_code` takes its
  registrations back): `provision_acp_adapter` installs no package the calling app's manifest
  does not list, and `register_acp_cli_entry` refuses (its provider card saying why) an agent
  CLI from an app that lists no program, whose adapter's engine (`requires_executable`) it does
  not list, or whose adapter runs through `npx` as a package it does not list. A registration
  core makes itself is not held to a manifest. What an app's own code starts is its own: that
  code runs as the owner, which consent's *runs as you* sentence says.
- **Prerequisites** — `requires: [{name, why, how}]` (`manifest.Prerequisite`) lists what the
  app needs on this machine that PersonalClaw does not install: the ComfyUI server Local Image
  Generation sends its work to, a program a tool runs. Each is a name, what the app uses it
  for and what to do to have it, within the lengths the dialog shows. Install consent leads
  with them (*What it needs that PersonalClaw doesn't install*), the Store card says
  "Needs ComfyUI", and an update that adds one asks again. Once the app is installed,
  `GET /api/apps` carries them on its row (`requires`) and the app's panel in the Library
  shows the same row consent did.
- **An engine** — a provider with `execution: "sidecar"` runs in a child process with a Python
  environment of its own, `apps/<app>/venv` (`local_models/sidecar.py`). Its engine is
  `dependencies.sidecarDependencies`: PEP 508 requirements (an option is an install error, and
  pip's options end with `--` before them), installed into that environment and never into
  `app-python`, and only when the owner presses **Install engine** on the app's card in
  Settings → Providers or on its Configure page. The install is a job
  (`POST /api/models/sidecar/{app}/install`): the environment, then pip, each step skipped
  when already done, so a stopped install resumes. The environment is made with no pip of its
  own (`python -m venv --without-pip`), and the gateway's own pip installs the engine into it
  (`pip --python <its interpreter>`): making it with pip asks `ensurepip`, which a Debian or
  Ubuntu system Python has only with `python3-venv` installed. pip's output reaches the job's
  log tail as it is written, the pip step may run for `DEPS_TIMEOUT_SECS` (two hours) before it
  is stopped, and cancelling (`DELETE /api/models/downloads/{id}`) kills pip and what it started. A
  finished install re-measures the app's availability. While it runs, an update or a removal
  of the app answers 409 `engine_installing`: pip is writing into the folder those replace or
  delete. **Remove engine** deletes an environment PersonalClaw made, never one someone else
  did.
- **One call in a child of its own** — `sdk.sidecar.run_once(app, worker, method, payload)` runs
  one `call(method, payload)` of an app's worker module in a child that exits once it has
  answered, for an in-process provider whose engine holds Python's interpreter lock while it
  works. In any thread of the gateway such a call stops the event loop, so every request waits
  for it: Diarization (ONNX)'s sherpa-onnx held the lock for the whole of a two-minute
  diarization, and Diarization (pyannote)'s clustering (scipy) holds it for a second or more at
  a time on a long recording. Both run their engine this way. The child is the sidecar child
  under the app's own `venv` when it has one, else
  under the gateway's interpreter with `app-python` appended to its import path, after the
  interpreter's own packages; it gets the child allowlist environment, the `tool` ceiling and a
  process group of its own. It has no deadline of its own (the caller's bound governs), and a
  cancelled call kills the child and what it started. The two speak one JSON line each way.
  What the child writes to stderr reaches the gateway's log masked, as a backend's does, and a
  call it died in names its last lines.
  Measured on this rule: faster-whisper, RapidOCR and torch release the lock in their heavy
  kernels (an event loop beside a 200-second transcription went at most 60 ms without a turn),
  so they stay in-process.
- **Backups leave the engine behind.** `apps/<app>/venv` is gigabytes built for this machine's
  OS, CPU and Python, so the `apps` inventory entry declares `*/venv` `derived_within`: a
  snapshot, an export and the hourly shard export leave it out. A restore or an import never
  plants one an older archive still carries, since that copy came without its interpreter but
  with its package receipt, and Install engine would then skip pip. The restore names each app
  it brought back whose engine is not installed here (`snapshot._engines_not_here`), and the
  app offers Install engine. A merge into a home that has the engine keeps it.

## Unload and load (`apps/app_runtime.py`)

Every lifecycle step that starts or stops an app goes through one pair, so an update, an
uninstall and a reinstall all leave exactly the version on disk running:

- **`load(*manifests)`** — install, enable, the end of an update, and gateway startup: first each
  app's code and registrations (its providers, which imports its code from its files now, its
  prompts, skills and proposal kinds), then what each runs beside the gateway (its MCP servers,
  backend and background worker). Startup loads every enabled app in the one call, so every app's
  code is registered before any app's process starts — a backend that launches through another
  app's sandbox tier finds the tier.
- **`unload(name, manifest)`** — disable, the three uninstall rungs, and the start of an update:
  stop and hold the backend and the worker (neither watchdog starts a held app, so no process
  comes back from the old files before the swap), drop the MCP servers and close the processes
  they spawned, disable the providers (a channel's receiver stops with them, and the unload waits
  for it), take back what the app's code registered, remove its prompts, skills and proposal
  kinds, and forget its availability answers.
- **`reload(name, manifest)`** — the two in turn, for an app that is already running: enabling an
  app that is on (a provider whose start failed, switched on again), and an app whose missing
  Python packages the boot-time repair just reinstalled.

**Startup** (`providers/loader.py::load_all_extensions`) recovers an interrupted update, puts the
app packages on the import path, seeds the native apps, and then hands every installed app to
`app_runtime.start_installed`: an enabled one is loaded as above, a disabled one is listed with its
providers off, and an enabled one this core cannot host (`minPersonalClawVersion`,
`requiresCoreFeatures`) is refused as
an enable refuses it — listed, its providers saying why, its backend and worker held so no
watchdog starts them. An app's MCP servers are written at every load, startup included; what the
owner set on one (`disabled`, `disabledTools`, `autoApprove`) is kept. A process that is not the
gateway (a CLI command that resolves a model) makes the same walk and loads each enabled app's
code and registrations only. A start-up that fails, or is stopped before the gateway installs its
signal handlers (a Ctrl-C during the update check), stops the backends and workers it already
started (`gateway.run_gateway`), as the gateway's own stop does.

**Settings → Providers** switches a provider by switching its app. A provider has no on/off of its
own: its switch sends the Apps page's own `POST /api/apps/{name}/disable` and `…/enable` (the web
client's `setActivation`), so the app's code leaves the process and comes back from its files, the
two pages never disagree about whether the app is on, and only the owner can switch an app's code
on (both routes are owner-only). Switching on a provider whose start failed enables an app that is
already on, which reloads it. Saving a provider's settings or changing one of its instances rebuilds
it when its app is on and retries it when its start failed
(`providers/routes.py::apply_saved_settings`). The change is saved either way, but an app that is
off stays off until you switch it on, and one this core cannot host stays refused.

**Taking the code back** (`personalclaw/app_code.py`). The loader claims an app's directory
before it runs any of its code. Each registry app code can write to through the SDK — model types
and catalogs, media catalogs and scanners, subscription sources, `acp:` runtime entries, sidecar
runners, trust-mode callbacks, skills marketplaces — records how to take an entry back
(`tests/test_sdk_registration_takeback_census.py` holds every `register*` the SDK publishes to it,
and names the ones that are not an app's to contribute), and an entry is the app's when
the app's code made the call; a core module that registers its own type while the app's import
pulls it in stays core's. `release(name)` runs those take-backs, removes every module loaded from
the app's directory from `sys.modules` (and its cached bytecode, since the next version's file
reuses the path), and reports what Python cannot take back; the unload adds what it cannot
stop:

| Left in the process | Why it stays | What the owner sees |
|---|---|---|
| a compiled extension module the app loaded | Python cannot unload one | a restart reason naming it |
| a thread still running the app's code | nothing can stop an arbitrary thread | a restart reason naming it |
| a task suspended in the app's code | it would resume the old code | a restart reason naming it |
| a package in `app-python` the gateway had loaded, replaced by the update | an interpreter keeps the version it imported first | a restart reason naming the packages |
| a process of the gateway's tree still running the app's files — above all an MCP server an agent CLI (an ACP session) started for itself | the agent starts the servers its own configuration names when its session starts and keeps them for the session's life; the gateway does not own them | a restart reason: the agent session keeps it until that session restarts |

A restart reason is the update's `restart_reason` (with `restart_required`), the toast that
reports the update, and the app panel's "Restart the gateway to finish" notice
(`GET /api/apps` → `restartReason`) — kept in memory only, since a restart is what clears it. The
Library re-reads the list when its socket reopens, so the notice goes when the restart is done. A
chat turn already in flight finishes on the provider instance it started with; the next one
builds from the new code.

**The restart itself** is the gateway's own full stop, then a re-execution in place (`os.execve`,
same PID; `restart_request.py`) — so its first step, `stop_processes()`, stops every app backend
and worker (watchdogs first), and every other step of a stop runs too. A process left running
would stay a child of the new image, which neither supervises it nor reaps it at boot (only a
process whose parent died counts as an orphan there).

**UI bundles** are served with `Cache-Control: no-cache`, and every bundle URL carries the app's
`uiRevision` (a digest of the bundles its manifest declares), so a tab that already imported the
old module imports the new one.

## Permissions (`apps/permissions.py`)

The manifest's `permissions` block is enforced, with one documented exception
(`network`, which the consent surface marks advisory):

| Permission | Enforcement |
|---|---|
| `api` | prefix-allowlist middleware over gateway API paths — pathname only, query string stripped (server and SDK agree on this). Two halves no declaration widens: the owner-only registry (`OWNER_ONLY_API_PATHS`, whole subtrees such as `/api/mcp` and `/api/terminal`), and `ROUTE_AUTHZ`, which declares each write route in a security family (`SECURITY_ROUTE_FAMILIES`: automations, apps, packs, agents and agent definitions, skills, prompts and snippets, the orchestrator's routing notes, config, devices, channels…) `OwnerOnly` or `AppMay` with a reason. Every write to an agent, a skill or a prompt is `OwnerOnly` — they are the instructions your agents carry out with your tools — so an app ships skills in its manifest's `skills` and runs agent work through its `agent` grant. Your conversations are families too (chat, sessions, rooms, inbox, reveal): an `AppMay` row that carries `owns` names where the route addresses a conversation, and the middleware refuses one the calling app did not start (`_ChatSession.created_by_app`); a row marked `agent_work` names the `agent` tier its work needs (a turn in the app's own conversation needs `tools`, summarising links the app sends needs `text`), and an app whose tier does not cover it is refused. Those families declare their READS route by route too (`READ_DECLARED_FAMILIES`): a read of one conversation carries `owns`, a list or a search answers an app with its own conversations only, and rooms and the inbox are the owner's. Your notification log is declared the same way: `GET /api/notifications` answers an app with the notifications it raised and those about a conversation it started (`DashboardState.notification_reaches`), and your notification settings and rules are the owner's. What you dictate is a family too (`/api/lexicon`): every write but the graph resync is `OwnerOnly`, because a term or a correction rewrites your transcripts, and its reads stay the allowlist's. Each app's provider is its own (`/api/providers`): every `/api/providers/{name}` row carries `owns` naming the app, so an app reaches its own provider's settings, instances and availability check and no other's, reads included; the list answers it with its own providers; `/api/providers/mcp-tools`, whose instances are every MCP server the gateway launches, is owner-only; and `/api/apps/{name}/config` is held to the calling app the same way. Your models are yours (`/api/model-providers`, `/api/models`): every route in both is `OwnerOnly`, reads included, because a model provider says where your model calls go and with which key, and a binding says which model each use runs on, your chats included; the onboarding wizard's one-click bind (`/api/onboarding/local-model/bind`), which adds a model provider and can move your chats onto it, is owner-only too. So is the rest of your first-run setup (`/api/onboarding`), reads included: its status and model check name your chat binding, its local-model probe and LAN sweep look for model servers on your machine and network, and its state is your setup's progress. A write route in any family, or a read in a declared-read family, that has no declaration is refused to every app (`undeclared_security_route`); `HEAD` answers from the `GET` row |
| `config` | the exact settings (`voice.echo_filter_enabled`) `/api/config` reaches for this app. A second declaration on top of `api`, which must still name `/api/config` for the route. `GET /api/config/personalclaw` returns these fields and nothing else, and a write to any other answers `403 config_field_not_declared`. Deny by default. Naming a security setting is an install error (`manifest._config_permission_errors`) |
| `events` | WebSocket fan-out filter — an app's socket only receives event types it declared, a frame about a conversation only when the app started that conversation, and an inbox item only when the app raised it (`dashboard/ws_state.py::frame_subject`); its session list holds its own rows, and a notification frame (the note, or `notification_logged`/`_removed`/`_ack`/`_unack` naming notes by `ts`) reaches it only about a notification it raised or one about a conversation it started. The envelope is `type` and `data`, so nothing on it says whether your tool calls run without asking. An app that may read `/api/approvals` (the approval relay) also hears the approval frames for yours |
| `eventSubscriptions` | which **platform** events (`apps/app_events.py`: `session.created`, `knowledge.ingested`, `task.completed`) are delivered to the app. A DIFFERENT axis from `events` above, deliberately: `events` is the WS type allowlist, these are core-emitted facts, and holding one grants nothing about the other. `app_events.emit` is the only delivery path and is the whole gate — deny by default and **exact name only** (no prefix, no `*`), so a typo denies rather than widens. Delivered into the app's broker-owned inbox (the `appMessaging` queue, sender `@platform`, which no app can be named), drained over `GET /api/apps/message`. Payloads carry identifiers only, never prose: a subscription grants timing, not content an app's `api` scope may not cover. |
| `mcpTools` | which MCP tools the app may invoke |
| `memory` | one boolean grant on `/api/memory/*` and `/api/lessons` (`permissions.MEMORY_API_PATHS` — a lesson is a memory record every agent is handed as a rule), refused unless declared. It is also what lets the app's WORK read and change your memory, one grant for both as its install consent says ("Read and change your memory"): a conversation the app started, an agent run it asked for, an agent its scheduled job started (judged by the job's id it carries, `app_crons.app_of`) and every agent working for any of them are assembled with your memory, and may recall it, only when the app holds it (`memory_reads.reach_of`); otherwise their memory tools answer why, and `get_context` leaves its memory tier out for them and for the app's own requests. Without it the same work saves, forgets and rules nothing (`memory_remember`, `memory_forget`, `triage_rules` answer why), the after-turn review learns nothing from the app's conversation, its consolidation never runs and a workflow run it started teaches nothing; the memory store refuses any change such work makes by any other path (`memory_writes.check_memory_statement`). With it, everything the app's work and the app's own requests write names the app as its source (`app:<name>`, `memory_writes.written_by`), so none of it reads as yours: not a fact you set or a lesson you taught, which outrank every other source. NOT a tier: this was `"app-scoped"`/`"shared"` until #3501 and `app-scoped` granted nothing on any path — the checker only answered True for it when asked about the app-scoped scope, and the gateway's single call site asked about `"shared"`. Deleted rather than implemented: core has no per-app memory partition (`memory_record.MemoryScope` is `session\|workspace\|agent\|global`), so the schema was offering a choice that did nothing on a *permission* the user approves at install. A leftover string value is an install error, never reinterpreted. |
| `cron` | whether manifest crons register, with the app's agent tier: each job's agent runs at it (`agent`, below), and a manifest that declares jobs, or this permission, with no tier is refused at install (`app_crons.schedules`). A job's agent is the app's work: what it may read and change of your memory is the `memory` permission's (above) |
| `storage` | a private DATA_DIR handed to the backend; the one folder `/api/reveal` shows or opens for the app |
| `agent` | a TIER, not a boolean (`apps/agent_tiers.AGENT_TIERS`): `text` — the model is handed the task the app sends and nothing else of yours (no memory, lessons or history is assembled ahead of it), on the worker built with no tools, and any call is refused; `read` — an agent offered and allowed only read-only tools, run only on PersonalClaw's own agent, which the host can hold to them (an agent CLI runs its own tools, so a `read` task there is refused before it starts); `tools` — an agent with your tools. `POST /api/apps/{name}/agent-run` runs a task at the CALLING app's tier, or a narrower `tier` the task asks for; a wider one is refused `403 agent_tier_exceeded` and nothing runs, and a `text` task names no agent. An app's scheduled job runs at the app's tier, the one it holds when the job fires (`app_crons.start_job`, [Crons](#crons)). No tier approves a call: the task starts on the app's install consent (`approval_grants.APP`), and every call that needs approval asks you, whatever your YOLO, Trust or Approval mode say (`subagent_tier`), as does every call of an agent its conversation or its run spawns (`memory_reads.reach_of`'s `app`), and a finished task's result is the app's to read, announced to no agent of yours; a turn in the app's own conversation, which needs `tools`, asks the same way (`chat_runner.started_by_app`). Install consent words each tier on its own (`AGENT_TIER_SENTENCE`), so an update that changes the tier asks again. `true` names no tier: it is an install error, and an app installed declaring it runs no agent work until updated |
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
- **what it prints reaches the log** (`child_output`): its stdout (at INFO) and
  stderr (at WARNING, so it shows at the default level) are relayed line by line
  into the gateway's log sinks, tagged `app <name> backend (pid N)`, masked by
  shape and, for every credential in the environment it was handed (its proxy
  secret among them), by value, with control characters written as visible
  escapes. The log takes a burst of 200 lines from one process and then 2 a
  second; what it does not take is counted in a note. A thread per process drains
  its pipes as fast as it writes, so a chatty backend never waits on a full pipe.
  Its last 40 lines and how it ended are kept, and while it is not running because
  that run ended on its own, its panel under Apps and the Doctor's apps check show
  them. A background worker and an engine are relayed the same way;
- `PERSONALCLAW_SKIP_APP_BACKENDS=1` disables backend spawning (test
  isolation).

### The backend environment — an allowlist, not an inheritance

A backend does **not** inherit the gateway's environment. It receives
`sandbox.build_child_env(site="app-backend")`: the `CHILD_ENV_BASE_NAMES`
allowlist (`PATH`, `HOME`, `TMPDIR`, `XDG_*`, locale/`TZ`, proxy + CA vars,
`PYTHONPATH`, `PYTHONPYCACHEPREFIX`, and the three `PERSONALCLAW_HOME`/`_WORKSPACE`/`_PORT` vars) plus
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
backend. The pre-change copy delivered ~130
variables the backend had no declared need for, including `SSH_AUTH_SOCK`, AWS
region/SDK vars and the operator's git identity.

**If a backend needs one more variable,** the operator declares it by name in
`sandbox.env_passthrough` (`config.json`). That is an operator surface on
purpose — it is not reachable from a manifest or a trigger payload, because an
app-declared name would be an exfiltration channel. Note that the declaration is
**global**, not per-site: a name declared there reaches every child site (cron,
bash action, app backend). The same allowlist is where every other process the
gateway starts for an app begins: the pip that installs its `pythonDependencies`,
its engine's venv and pip, the npm that installs an ACP adapter, its setup hooks,
worker, sidecar and MCP servers. Withheld names are logged at DEBUG against the
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
- the app's **1-hour app-scoped Bearer token** (`token_auth.app_session_token(user,
  name)`, `APP_TOKEN_TTL_SECS = 3600`) plus `X-PersonalClaw-App` are
  injected, so the backend has an identity bounded to its own declared
  permissions. It is the same token the app's SDK holds, reused while it has more than
  half its hour left: every mint is a session, and a fresh one per proxied request used to
  sign the owner's other devices out.

That boundary is about what the proxy hands a backend. A backend on the host is still a
process under the owner's account, so it can read the home directly, `session_key` (the
token-signing key) included. A backend that names a sandbox tier runs under that tier's
confinement instead — see
[security/limitations.md §7](../security/limitations.md#7-an-apps-own-code-runs-as-you).

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

Every request the gateway sends a backend carries an HMAC signature the backend
verifies **fail-closed**: what the proxy forwards, and what an agent's or a trigger's
`call_app_route` sends (`tool_providers/app_routes.py`). Both sign through one signer,
`apps/app_secret.proxy_signature`, over the contract in `personalclaw/proxy_signature.py`,
and neither sends a request unsigned when the app has no secret:

- **Header:** `X-PersonalClaw-Proxy: <ts>:<hmac_hex>`.
- **Signed message:** `<ts>:<METHOD>:<raw_path?query>:<sha256_hex(body)>` —
  binding the method, the exact wire path (aiohttp's `request.raw_path`), and a
  hash of the body, so none can be altered in transit.
- **Window:** `ts` is an integer unix second; a signature more than **±60s**
  from now is refused (replay protection). The compare is constant-time
  (`hmac.compare_digest`).
- **Secret:** a per-app 256-bit key at `apps_dir()/<app>/.app_secret`, minted
  0600 afresh every time the backend starts and injected into the child via
  `PERSONALCLAW_APP_SECRET`, so it lasts as long as that backend: a copy stops
  working the next time it starts (`apps/app_secret.mint_app_secret`). It is
  never logged.
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

- **Python**: `sdk/` is THE stable app-facing import surface —
  apps import core **only** via `personalclaw.sdk.*`
  (boundary-lint-enforced by `tests/test_apps_import_boundary.py`). Modules
  cover models, channels, tools, search, memory, knowledge, STT/TTS,
  credentials, settings (`ProviderSettings` — each app's persisted store),
  security helpers, `provider_helpers.register_branded_app` for
  protocol-thin branded model apps, and `testing.keychain_off` for an app's
  test harness, which keeps the test process out of the machine's OS keychain
  (one keychain serves every home, so a scratch `PERSONALCLAW_HOME` does not).
  `testing.neutral_git_env` and `testing.refuse_git_helpers` keep a suite's git
  off the machine's credential helpers, a Mac's keychain one among them: every
  test's git reads no system configuration and a global file of its own, and a
  git that could still sign in with a helper of the machine's is refused before
  it starts, failing the test that started it (core's suite and the apps
  repository's both install it).
  `testing.refuse_ports` refuses a suite's connections to a local model
  server's port at any address, unless the test process itself serves that
  port, and with `loopback=True` to any port on the machine the process has not
  opened itself, so a test reaches only the servers it started; the test that
  asked fails by name (core's suite installs both rules, the apps repository's
  the first).
  `testing.launch_acp_entry` launches an ACP entry's command with the environment
  a spawn from that entry gets, so an ACP app's tests check what its CLI is
  handed with a stub in the CLI's place, never the CLI itself.
  `features` names the contracts this build offers apps (`CORE_FEATURES`,
  `core_has`): an app declares the ones it relies on in `requiresCoreFeatures`,
  which install, update and enable check (above), and asks `core_has` about one it
  can do without.
- **Its signatures are a reviewed contract**: every name each `sdk` module publishes
  is recorded in `src/personalclaw/sdk/signatures.json`
  (`scripts/sdk_signature_snapshot.py`), `tests/test_sdk_signature_snapshot.py`
  fails until a change is regenerated into it, and writing it refuses a parameter
  that changed type in place (an old caller would still bind). CI's `apps-contract`
  job runs the first-party apps' SDK contract checks against a change that touches
  the SDK (`scripts/apps_sdk_contract.py`). The rule for contributors:
  [CONTRIBUTING.md](../../CONTRIBUTING.md#sdk-changes).
- **Frontend**: `web/src/app/appSdk.tsx` — a contributed UI gets
  `createAppApi` / `createAppEvents` and mounts via `mount(el, ctx)`; the host
  resolves bare `react` / `@personalclaw/app-sdk` imports so app UIs don't
  bundle their own React.

### Saving a whole document from an app page

A route that replaces a whole document — an app's own settings (`PUT /api/apps/<name>/config`),
a task, a knowledge item's body — refuses a write that does not name the copy it was built from
([the contract](../reference/api-overview.md#request-and-response-conventions)). Its read
reports a `revision`; keep it beside the value, and hand it back on the save:

```js
import { createAppApi, isStaleWrite } from '@personalclaw/app-sdk'

const api = createAppApi(ctx)
const read = await api.get(`/api/apps/${ctx.name}/config`)   // { config, revision, … }
try {
  await api.put(`/api/apps/${ctx.name}/config`, { ...read.config, collection: 'journal' },
    { basedOn: read.revision })
} catch (e) {
  if (isStaleWrite(e)) {
    // 409: the settings changed since this page read them (another tab, Settings → Providers,
    // your own backend). Nothing was saved. Read again, re-apply the edit, save over the new
    // revision — or show the user both and let them choose.
  } else throw e
}
```

`post`, `put` and `patch` take the same `{ basedOn }`; the client sends it as `If-Match`, the
one header an app sets. A write that names no revision is refused with `428
revision_required`. Every refusal rejects with the gateway's `status`, `code` and sentence
(`e.message`), the same `ApiError` core's own pages get.

### The UI SDK's gated subpaths

Two subpaths sit beside the base module, each unlocked by one entry in the manifest's
top-level `uiCapabilities` list (closed vocabulary: `UI_CAPABILITIES` in
`apps/manifest.py`, mirrored by the `UiCapability` union in `appSdk.tsx` — a test pins
the two together):

| Import | Declaration | Exports |
|---|---|---|
| `@personalclaw/app-sdk/ui` | `shell-primitives` | `Button`, `Surface`, `useTheme`, `readAppTheme` |
| `@personalclaw/app-sdk/genui` | `generative-widget` | `GenerativeWidget` |
| `@personalclaw/app-sdk/genui` | `generative-component` | `registerComponent`, `unregisterComponents` |

`Button`/`Surface` are the host's OWN components by identity, not copies, so a page built
from them renders markup identical to a native page's — which is what
`web/src/app/appSdkUi.test.tsx` asserts, by loading a fixture bundle through
`ContributedPage` and diffing its output against the same page written natively. `useTheme`
/`readAppTheme` return the resolved token contract (`colors` + spreadable `cssVars`), so an
app never names a host CSS variable directly.

`GenerativeWidget` takes a genui DSL body and hands it to the HOST renderer, which validates
every line against the host's own component registry (`ui/genui/registry.ts`). Authoring
surface for the DSL is `library.prompt()`.

**Contributing a component TYPE** is the separate reading APE-11 deferred, and AMBIENT-SURFACES
AS-6 landed it under `generative-component` — a distinct declaration, because supplying a DSL
body and extending the component vocabulary are different trust edges and one must not grant
the other. An app declares `ui.components` (a module whose `register(sdk, ctx)` export calls
`sdk.registerComponent(ctx, def)`); the SHELL loads it for every enabled declaring app, so a
chat-born widget can name the component without the user ever opening that app's page. Four
properties keep the safety model intact:

- **additive only** — an app registration is an L1 layer entry that may add a name, never
  shadow a core one (refused at register time, so model-authored `Table(…)` always reaches the
  core `Table`);
- **host-validated** — the component declares its args to the host registry and is validated
  by it like any core component;
- **error-boundaried** — it renders inside a `LayerBoundary`, so a throwing app component
  cannot blank the surface it was composed into;
- **removed on disable** — the same sync pass that loads a module drops the components of any
  app that is no longer enabled or no longer declares the capability, and they leave
  `library.prompt()` with it.

**The gate is a declaration, not a sandbox.** `resolvableAppSpecs()` omits an undeclared
subpath from the bundle's import rewrite, so its bare import fails to resolve — but a
contributed page already runs in the host React tree (`ContributedPage` mounts it with
`createRoot`, no iframe) with the host `window`, so an undeclared app is not *prevented*
from reaching the same components. What the block buys is legibility: the app states which
host surfaces it builds on, in one place the host can read. Same posture as
`permissions.network` — advisory, and never presented as enforced.

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
breaking `isinstance` across two reads). The cache is for one version: a step that changes the
app's files unloads it first ([Unload and load](#unload-and-load-appsapp_runtimepy)), so the
next load runs what is on disk.

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

**The second bundle that owns code: `apps/native/ollama-models/`** — the local Ollama model
provider, bundled so that a fresh install has a chat
**and** embedding provider without a credential and without cloning a second repository. It
is the contract's first non-`tool` user, and it demonstrates the two things the exemplar
could not:

- **A `model` provider needs no core edit at all**, not even the residual metadata one. It
  registers its own type at module import —
  `get_default_registry().register_type(...)`, reached through the published
  `personalclaw.sdk.model` re-export — and core's generic `ModelTypeHandler` does the rest.
  There is no `llm/*.py` self-register and no registry special case; `TOOL_META` is a
  tool-surface map, so a model bundle never touches it.
- **"Only core's own dependencies" is a hard constraint, not a preference.**
  `seed_builtin_apps()` never calls `_install_python_deps()` (only `install()` and
  `update()` do), so a bundled app that declares a dependency gets a tile that is dead on
  arrival with nothing logged. `ollama-models` was chosen precisely because its two
  third-party imports, `httpx` and `aiohttp`, are already core runtime dependencies.
  `tests/test_native_ollama_bundle.py` rails both halves across **every** bundle.

**The third: `apps/native/mcp-tools/`** — the adapter that hands agents the tools of the MCP
servers in `mcp.json`, moved in from the Store. The gateway already connects to every one of
those servers, and this provider is the only surface through which an agent can call them
(`mcp/<server>/<tool>`, rule 2 of `tool_providers/registry.py`), so as a Store install it
left a server the user added or imported running and useless until they found a second thing
to install. It meets the bundling bar the other two set: SDK imports only, no dependencies
(`mcp` is a core one), and no vendor logic, since MCP is a protocol. It also adds no outbound
reach to an install. With no server configured it serves no tools, and every tool it does
serve is one the user connected, asking before it runs at the risk the server declares.
`tests/test_native_mcp_tools_bundle.py` pins all three claims.

Note also that the packaged files of a bundled app are re-synced into an existing install on
every boot (`app_manager._resync_native_bundle`), not just its `app.json`: seeding is
once-only and a native app is locked against the `POST /api/apps/{name}/update` push path,
so without that resync a provider fix shipped in a new wheel would reach a fresh home and
never an upgraded one. `data/` and `installed.json` are never touched. The same holds on
the boot where an app a user installed from the Store becomes a bundled one: the packaged
files replace the store copy then, not a boot later, and the install record is rewritten
to say the app is built in (`app_manager._adopt_as_builtin`), keeping the user's `data/`
and on/off state.

## Crons

Manifest-declared crons are reconciled by `apps/app_crons.py` on every
lifecycle transition (install/enable/disable/uninstall). A job is registered
only for an app that holds the `cron` permission and an agent tier
(`app_crons.schedules`, the predicate install consent reads), named by its app
(`<display name>: <job>`), with delivery `none`: it has no conversation to post
to, and nothing reads a job's `silent` or `persistent_session` field.

Each fire runs the job's agent at the agent tier the app holds then, as the
app's work (`app_crons.start_job`): `text` hands its model the job's message
alone, with no tools; `read` gives it read-only tools and no message to send;
`tools` gives it your tools. It carries the app's name, so it starts on the
app's install consent and approves none of its calls: each call that needs
approval asks you in the Inbox, whatever your YOLO, Trust or Approval mode say.
It reports to the app (`app:<name>`), so no turn of your own agent is handed
its result. Its run history says how each run went, and its completion and
failure notes go out as any trigger's agent's do. Nothing of the job's step
decides any of that: its action carries no approval or write-access posture,
reconciliation takes one off a row registered with one, and no edit gives it
one (`app_crons.posture_refusal`). See
[tasks-triggers.md](tasks-triggers.md#app-manifest-crons).

## MCP bridge

An app may ship its **own MCP server(s)** under `manifest.mcpServers`
(distinct from MCP servers it merely depends on). `apps/mcp_bridge.py` writes
them into the live MCP store (`~/.personalclaw/mcp.json`) on enable/install
and removes them on disable/uninstall, closing their live connections too: an update keeps the
same command and arguments, so a connection left open would keep the old server process
answering. Entries are namespaced
`{app}:{server}` so apps can't collide on a server key and deregistration
removes exactly this app's servers. App-shipped stdio servers run with
`cwd=<app dir>` (`mcp_stdio.py`, which starts every stdio server, for the probe and an agent's
connection alike). Every start of a server is recorded in one place (`mcp_discovery.note_start`, in
the words of `mcp_status.py`), which the Tools page card, the Settings test and an agent's failed
call all read: a server that exited before it answered says so with its exit code and the line of
its error output that says why, and one that fails to start three times in a row is not started
again until its owner presses Retry or its definition changes. A start is never cut off part-way:
a program that has not said a word when its connection stops waiting (a package runner such as
`npx` installing what it runs the first time) is left to finish, for up to ten minutes
(`mcp_stdio.FINISH_SECS`), and its card says it is still starting. A start of the same program
meanwhile waits for it rather than installing beside it, and once it ends the server is probed
again; a probe that connects keeps nothing of the one before. Stopping a package runner in the
middle of an install left its cache half-written, and some never repair that themselves.

The manifest is the only way an app gets an MCP server. `/api/mcp` is owner-only for
app tokens, reads included (`apps/permissions.OWNER_ONLY_API_PATHS`): a server entry is a
command the gateway launches as the owner, with the gateway's environment, and a remote
server's headers carry its bearer token. So the servers an app runs through the gateway are
the ones install (or update) consent listed from `disclosure.describe`, and an installed app
cannot add another through the API. Its own code still can, by editing `mcp.json` as you
([security/limitations.md §7](../security/limitations.md#7-an-apps-own-code-runs-as-you)).

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

A tool provider is registered under its app's name, and every tool it lists passes the tool
seam before a model request carries it: a schema outside the portable profile is repaired or
left out, with one log line naming the app and the tool — see
[tool-schema-wire.md](tool-schema-wire.md).

**A tool name has one provider** (`tool_providers/registry.py`). An agent's call goes to the
provider its name maps to, under the approval card that tool asks with, so no provider may take
a name another holds:

1. the platform's names (`bash`, `read_file`, … `PLATFORM_TOOL_NAMES`) and its provider name
   `personalclaw-filesystem` are its own;
2. a name under `mcp/` is the MCP Tool Servers app's (`mcp/<server>/<tool>`), and a server's
   name cannot contain `/`, which would make its tools' names another server's
   (`mcp_discovery.server_name_problem`: import refuses one, and one already in `mcp.json` is not
   started);
3. a provider core ships (its own factories, `app-routes`, a `builtin`-tier app, by the tier core
   recorded, never the manifest's own `native` flag) outranks an installed app's;
4. otherwise the provider that claimed the name first keeps it. A provider claims its names when
   the registry reads its tool list: at enable, and on every read after, since a list is live.

A registration that breaks the rule is refused whole: the provider leaves the surface and serves
nothing, its Settings → Providers card is off with the sentence under it, the change that
registered it answers with that sentence, and the security log has an `outcome=refused` row. A
provider name is checked the same way, when it registers. An app that registers its provider
again (a re-enable, an update's new code) replaces its own, and its new list is the one read.
Pick tool names prefixed with your app's own (`notes_search`, not `search`).

**Availability is measured out of process.** A provider module may export
`availability() -> (bool, str)` — "can this provider run on this machine?". The gateway never
calls it: `providers/availability.py` runs every hook in a child process
(`personalclaw availability-probe <app>…`, killed at a 180 s deadline) at boot, when a
15-minute-old answer is read, and when the user presses **Check again**
(`POST /api/providers/{name}/availability`). `GET /api/providers` only reads the cached answer,
`availability: {state: checking | available | unavailable | unknown, reason, checkedAt}`.
Measured before this existed: one hook imported torch, and the list and `/api/healthz` both
passed 120 s. A hook must still be cheap — answer from metadata, never import the library it
checks for; the rule and the check to build it from are `personalclaw.sdk.availability`.

## Related docs

- What belongs in an app vs core: [provider-boundary.md](provider-boundary.md)
- The scanner and install-integrity invariants: [security.md](security.md)
- Channel apps specifically: [inbox-channels.md](inbox-channels.md)
