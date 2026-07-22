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
- **Command screening** (`security.py`): a deny list
  (`BUILTIN_DENIED_COMMAND_PATTERNS`, merged with user config at read time via
  `denied_command_patterns()`) and suspicious-pattern watchers
  (`SUSPICIOUS_BASH_PATTERNS`).
- **OS child sandbox** (`sandbox.py`) with a credential-env denylist so secrets
  never reach a sandboxed child.
- **Trust/YOLO state** (`trust_mode.py`): one process-global auto-approve state,
  config-permanent or TTL'd, with `on_disable` callbacks.

### 2. Core ↔ apps

Installed apps extend the gateway but must not reach the owner's full authority:

- **App-scoped tokens** (`dashboard/token_auth.py::generate_token` with an `app`
  claim) bound a request to that app's declared permissions; TTLs capped by
  `MAX_SESSION_TTL_SECS`.
- **Reverse-proxy credential stripping**
  (`dashboard/handlers/apps.py::api_app_proxy`): app backends never see the
  owner's cookie/Authorization — a fresh 1-hour app-scoped token is injected.
- **Permission middleware** holds in every auth mode — including `none`, where
  `dashboard/server.py`'s `_dev_user_middleware` re-adopts the app claim via
  `validate_token_with_app` so an app token only ever *narrows* reach.

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
- **Scanner verdicts** (`supply_chain.py`: `SkillScanner`, `Verdict`): `clean` /
  `warning` (consent required) / **`dangerous` (terminal, non-overridable)**;
  `TrustTier` modulates strictness.

### 5. System ↔ persisted / exported state

Data leaving the running system:

- **Tamper-evident audit** (`sel.py::SecurityEventLog`): HMAC-chained,
  append-only events (caller, operation, outcome).
- **Redacted archive reads** (`history.py`: `redact_credentials`,
  `redact_exfiltration_urls`).
- **Credential-excluding exports** (`portability.py`): `.env`, `sel_hmac.key`,
  and `session_map.json` are on the export exclusion list.
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
| **ASI03** Identity & privilege abuse | App-scoped tokens, reverse-proxy credential stripping, permission middleware (holds even in `none` mode) | `dashboard/handlers/apps.py::api_app_proxy`; `dashboard/token_auth.py`; `dashboard/server.py` (`_dev_user_middleware`) | enforced |
| **ASI04** Supply-chain & dependency risk | Quarantine → scan → consent → install; `dangerous` verdict terminal; scanned-tree == installed-tree | `apps/app_manager.py::install`; `supply_chain.py` (`SkillScanner`, `Verdict`) | enforced |
| **ASI05** Unauthorized code execution | Command screening + OS sandbox + credential-env denylist | `security.py`; `sandbox.py` | enforced |
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
- **An app's own outbound network traffic.** The `network` app permission is
  declaration-only (disclosed at install consent), not a gateway-enforced
  boundary — an app backend is its own OS process with its own network stack. See
  [limitations.md](limitations.md). What *is* enforced is the supply-chain gate on
  what you install and the app's gateway-mediated (`api`) reach.

Each of these has a rationale above; none is an accident. Gaps discovered while
maintaining this document are routed to the security-hardening track as
candidates, never patched inline in a docs change.
