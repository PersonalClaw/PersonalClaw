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
  integrity](#baseline-denylist-integrity-anti-drift-and-anti-llm-tamper-not-anti-owner))
  and suspicious-pattern watchers (`SUSPICIOUS_BASH_PATTERNS`).
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

- **App-scoped tokens** (`dashboard/token_auth.py::generate_token` with an `app`
  claim) bound a request to that app's declared permissions; TTLs capped by
  `MAX_SESSION_TTL_SECS`.
- **Reverse-proxy credential stripping**
  (`dashboard/handlers/apps.py::api_app_proxy`): app backends never see the
  owner's cookie/Authorization — a fresh 1-hour app-scoped token is injected.
- **Permission middleware** holds in every auth mode — including `none`, where
  `dashboard/server.py`'s `_dev_user_middleware` re-adopts the app claim via
  `validate_token_with_app` so an app token only ever *narrows* reach. The internal
  routes' cookie/`?token=` fallback records the claim too
  (`dashboard/token_auth.py::_extract_and_validate_token`): it used to validate an app
  token and drop its identity, so `/api/tools/invoke` reached the handler as the owner.
- **The owner's security posture is not an app's to change.** A config field whose
  `_EDITABLE_CONFIG` entry declares a `SecurityControl` (`config/edit_spec.py`) refuses an
  app-scoped write `403`, in either direction, with a Security Event Log row naming the
  app and the field; so does an agent's `approval_mode` and a standing approval verb
  (`yolo`, `trust_agent`). The routes that exist only to change the posture are in the
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
  (`dashboard/handlers/files.py::_dashboard_roots`), since `config.json` and `mcp.json`
  live there.
- **A new route fails closed.** Every write route under a family that decides what runs
  as you, whether it asks first, or who may reach you (`SECURITY_ROUTE_FAMILIES`) is
  either owner-only or declared `AppMay` with its reason. An undeclared one is refused to
  every app token at runtime (`undeclared_security_write`), and
  `tests/test_security_posture_rail.py` fails the build on it.
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
  processes, `config/loader.py`), and its setup hooks are shell commands. That code can
  read and write every file in your PersonalClaw home: `config.json` with every security
  setting, `mcp.json`, the credential files (`.env`, `credentials.json`), and
  `session_key`, the key that signs every session token, yours included. The home's
  0600/0700 modes (§5) keep other accounts out, not code running as you. The one
  exception is a backend that names a sandbox tier (`backend.sandbox`), which launches
  inside that tier. An app that ships code therefore needs no API call to relax your
  posture. Install consent names its server process, its install hook and its MCP server
  commands (`apps/disclosure.py::describe`), but not its provider modules or its other
  hooks, and it does not say the code runs as you. The supply-chain gate (§4) is the
  control that vets it. See [limitations.md](limitations.md) §7.

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

*Inbound MCP and external remote access (fail-closed inbound, fencing at
ingestion) are owned by MCP-READONLY-INBOUND and EXTERNAL-ACCESS — not yet
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
- **Scanner verdicts** (`supply_chain.py`: `SkillScanner`, `Verdict`): `clean` /
  `warning` (consent required) / **`dangerous` (terminal, non-overridable)**;
  `TrustTier` modulates strictness.

### 5. System ↔ persisted / exported state

Data leaving the running system:

- **Tamper-evident audit** (`sel.py::SecurityEventLog`): HMAC-chained,
  append-only events (caller, operation, outcome).
- **Redacted archive reads** (`security.py`: `redact_credentials`,
  `redact_exfiltration_urls`).
- **Credential-excluding exports** (`portability.py`): `.env`, `sel_hmac.key`,
  and `session_map.json` are on the export exclusion list.
- **Secret settings held by reference** (`config/secret_refs.py`): a provider key, every
  app setting declared `x-meta.sensitive`, every MCP server `env` and `headers` value (bar the
  `env` variables a server marks plain) and the webhook token live in the credential store;
  `config.json`, an app's `data/config.json`, provider instance records, `mcp.json` and the
  agent config carry a `{{secret:…}}` reference, resolved where the value is used: an MCP
  server's at spawn, the webhook token when a request is checked. Every path that adds or changes
  an MCP server writes through `secret_refs.write_mcp_document`. Deleting a provider, removing an MCP
  server on the Tools page, or either removal rung of an app, deletes what it owned.
- **Private home** (`atomic_write.py`): a file the atomic writers put under the home —
  `atomic_write`, and `agent._atomic_json_write` for `mcp.json` and the agent config — is 0600
  in a 0700 directory, and a wider mode is refused. `config.json`, an app's `data/config.json`,
  provider instance records, `mcp.json`, the agent config, `.env`, `credentials.json` and
  `auth/` are all written that way; `.local_secret`, `telemetry_salt` and `.app_secret` have
  writers of their own that create them 0600. A file written some other way (a log, a lock, a
  database) keeps the umask mode inside the 0700 home.
- **Credential-free snapshots** (`durability/inventory.py`, `credential=True`): `.env`,
  `.env.pre-keychain`, `credentials.json` and `.local_secret` never enter a snapshot, and a
  per-app `.app_secret` enters neither a snapshot nor an export. No settings file an archive
  carries holds a stored value (`tests/test_export_carries_no_credential_store_value.py`
  searches every member for every value the store holds). What a reference cannot cover (copies
  made before the upgrade, a multi-line value, Claude Code's own config) is in
  [limitations.md §6](limitations.md).
- **Memory privacy** (`session_restrictions.py`): temporary/incognito sessions
  gate memory reads/writes.

## OWASP Agentic Security (ASI) Top-10 mapping

Status legend: **enforced** (a resolvable control gates it) · **in progress
(plan N)** (control is designed, not yet landed) · **documented limitation** (a
deliberate, disclosed gap — see [limitations.md](limitations.md)). A row may claim
`enforced` only with a resolvable `file:path` citation.

| ASI category | Control | Code citation (`file:path`) | Status |
|---|---|---|---|
| **ASI01** Agent goal / instruction manipulation | Untrusted-content fencing, approval modes, and data-not-instructions framing on recalled memory | `security.py::fence_untrusted`; `dashboard/handlers/memory.py` (recall framing) | enforced |
| **ASI02** Tool misuse | Command deny/suspicious patterns, task-mode gating, OS child sandbox | `security.py` (`BUILTIN_DENIED_COMMAND_PATTERNS`, `SUSPICIOUS_BASH_PATTERNS`); `task_modes.py`; `sandbox.py` | enforced |
| **ASI03** Identity & privilege abuse | App-scoped tokens, reverse-proxy credential stripping, permission middleware (holds even in `none` mode), an owner-only registry plus per-route declarations that refuse an undeclared write, settings scoped to the fields a manifest declares | `dashboard/handlers/apps.py::api_app_proxy`; `dashboard/token_auth.py`; `dashboard/server.py` (`_dev_user_middleware`, `app_permission_middleware`); `apps/permissions.py` (`OWNER_ONLY_API_PATHS`, `ROUTE_AUTHZ`, `undeclared_security_write`) | enforced |
| **ASI04** Supply-chain & dependency risk | Quarantine → scan → consent → install; `dangerous` verdict terminal; scanned-tree == installed-tree; staging never follows a link out of the bundle | `apps/app_manager.py::install`; `apps/staging.py`; `supply_chain.py` (`SkillScanner`, `Verdict`) | enforced |
| **ASI05** Unauthorized code execution | Command screening + OS sandbox + credential-env denylist; an app token cannot define an MCP server or an automation, and the owner confirms an automation step that approves its own tool calls | `security.py`; `sandbox.py`; `apps/permissions.py` (`OWNER_ONLY_API_PATHS["/api/mcp"]`, `ROUTE_AUTHZ`); `automation_posture.py` | enforced *(an installed app's own code runs as you: documented limitation, [limitations.md](limitations.md) §7)* |
| **ASI06** Memory & context poisoning | Fenced recall, propose-only (never live-write) learning, temporary/incognito session modes | `dashboard/handlers/memory.py`; `after_turn_review.py` (propose-only queue); `session_restrictions.py` | enforced |
| **ASI07** Insecure inter-agent / inbound comms | Fail-closed inbound surface + fencing at ingestion | *(owned by MCP-READONLY-INBOUND + EXTERNAL-ACCESS)* | in progress (plans 41, 24) |
| **ASI08** Cascading failures / denial-of-wallet | Circuit breakers, budgets, spend caps | *(owned by AUTONOMY-GUARDRAILS)* | in progress (plan 9) |
| **ASI09** Trust exploitation / social engineering | Approval surfaces, expiring YOLO with `on_disable` callbacks, consent-gated installs | `trust_mode.py`; `apps/app_manager.py::install` | enforced |
| **ASI10** Rogue / runaway agents | Tamper-evident audit log + YOLO kill/disable (auto-approve is revocable, firing disable callbacks) | `sel.py::SecurityEventLog`; `trust_mode.py` (`on_disable`) | enforced *(incident-flag on breaker trip: in progress, plan 9)* |

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
