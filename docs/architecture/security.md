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

`auth/modes.py` defines four modes, selected via `PERSONALCLAW_AUTH_MODE`
(default `local_token`):

| Mode | Behavior |
|---|---|
| `none` | No token auth — **bind is forced to loopback** (an unauthenticated gateway must never leave the host). Dev convenience. |
| `local_token` | The default: token auth with a login page; static assets bypass the check (the real asset surface only — `dashboard/token_auth.py`). An opt-in, IP-gated local-network bypass exists. |
| `api_key` | Header key auth. |
| `oauth2` | OIDC via `auth/oidc.py` (loaded only in this mode). |

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

- `generate_token(user_id, ttl_seconds, app=...)` mints tokens with an
  optional **`app` claim**; app-scoped tokens bound a request to that app's
  declared permissions.
- App backends never see the owner's credential: the reverse proxy strips
  cookie + Authorization and injects a fresh 1-hour app-scoped token
  (see [app-platform.md](app-platform.md#the-reverse-proxy--token-model)).
- Session TTLs are capped (`MAX_SESSION_TTL_SECS`); nonces are registered and
  evicted.

### Webhook auth

`POST /api/hooks/agent` (`dashboard/handlers/hooks.py`) is
middleware-exempt; its **only** gate is `_verify_hook_token` — a
constant-time (`hmac.compare_digest`) check of the Bearer or
`x-personalclaw-token` header against `hooks.webhook_token` in config. No
configured token means every request is refused. Denials are logged to the
Security Event Log.

## Command screening (`security.py`)

- **Deny list** — `BUILTIN_DENIED_COMMAND_PATTERNS` (112 shell patterns) is
  merged with user-configured `security.denied_commands` **at read time**
  (`denied_command_patterns()`), so config edits apply immediately. This one
  source feeds both the native bash tool and the Security panel.
- **Suspicious-pattern watchers** — `SUSPICIOUS_BASH_PATTERNS` (52 patterns)
  flag rather than block.
- **Tool-name denies** — `BUILTIN_DENY_PATTERNS` (fnmatch over tool names)
  with a documented `_DENY_EXCEPTIONS` escape hatch.
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
gives a hook, cron-script or bash-action child a minimal base
(`PATH`, locale, home-equivalents, proxy/CA settings, and the three
`PERSONALCLAW_*` vars) plus whatever names the operator declared in
`sandbox.env_passthrough`. Nothing else from the gateway environment reaches
them, so a credential the gateway holds is not readable by `printenv`. The
sensitive-prefix list above is the floor: a declaration cannot pass
`AWS_SECRET*`, `AWS_SESSION*`, `SSH_AUTH_SOCK`, `GNUPGHOME` or `GIT_ASKPASS`.
Withheld names are listed in the debug log at each spawn, so a script that
needs one more variable is diagnosable rather than mysteriously broken.

### What the sandbox does and does not do

This is a **credential-hiding sandbox, not a confinement sandbox** — a precise distinction
that the rest of this section, and any public claim, must respect. The macOS Seatbelt profile
is allow-by-default (`(version 1)\n(allow default)`) with targeted `deny file-read*` rules over
credential paths (`~/.aws`, `~/.gnupg`, `~/.config/gcloud`, `~/.azure`, `~/.docker`, `~/.kube`,
`.npmrc`, `.pypirc`, `.netrc`, `.git-credentials`, `.personalclaw/.env`, plus `~/.ssh` in
`strict`); the Linux path is equivalent (bind-mount empty dirs over those paths). It raises the
cost of credential theft; it does not stop an agent from doing anything else.

| It **does** | It does **not** |
|---|---|
| Hide credential dirs/files from the agent child (macOS Seatbelt deny-reads; Linux bind-mounts) | Confine filesystem **writes** (except `~/.ssh` on macOS `strict`) |
| Scrub credential env vars from the child, every mode | Restrict **network / egress** from the child |
| Deny `~/.ssh` writes (macOS `strict` only) | Limit processes, CPU, or memory (no rlimits) |
| Path-allowlist a subagent's cwd (advisory — the prompt tells the agent its scope) | Provide a filesystem **jail** or a real execution boundary |
| Enforce app `api`/`storage`/`memory`/`cron` permissions server-side | Enforce the app `network` permission (declaration-only — see `docs/security/limitations.md`) |

The honest, complete statement of limitations lives in
[`../security/threat-model.md`](../security/threat-model.md) and
[`../security/limitations.md`](../security/limitations.md); this section is the architectural
summary, not a substitute for them.

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

## Egress chokepoint (`net/`)

`net/client.py` + `net/guard.py` + `net/policy.py` form the ONE outbound-HTTP
chokepoint:

- Named policies: `STRICT`, `CONNECTOR` (knowledge scraping), `WEBHOOK`
  (user-configured POSTs), `LOOPBACK_INTERNAL` (loopback only — **never
  widened** by config), `REGISTRY`/`LISTED` (exclusive allow-lists).
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

## Untrusted-content fencing

`security.py::fence_untrusted` wraps third-party text in
`<untrusted_content>` markers (escaping any embedded marker so content can't
break out), paired with a system-prompt note that fenced spans are data, not
instructions. Applied to web-search results, inbox content, and third-party
payloads; memory recall applies the same data-not-instructions framing to
recalled episodes (`dashboard/handlers/memory.py`; see
[knowledge-memory.md](knowledge-memory.md#recall--the-privacy-guard)).

## Supply chain (`supply_chain.py`)

`SkillScanner` gates both app installs and skill installs through
`install_guarded`:

- verdicts: clean / warning (consent required — 409) / **dangerous (terminal
  refusal, non-overridable)**;
- the integrity invariant: **scanned bytes == installed bytes** (no
  time-of-check/time-of-use window between scan and install);
- source trust tiers modulate strictness (a bundled skill's `curl` is not the
  same risk as a random repository's).

## Trust / YOLO state (`trust_mode.py`)

ONE process-global YOLO (auto-approve) state: config-permanent vs TTL'd
surface activation (`YOLO_CHANNEL_TTL_SECS`), with `on_disable` callbacks.
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

## Data-leaving-the-system rules

- Session-archive reads are redacted (`history.py` via
  `redact_credentials` / `redact_exfiltration_urls`).
- Portability export (`portability.py`) always excludes credentials: `.env`,
  `sel_hmac.key`, `session_map.json` are on the exclusion list.

## Memory privacy

Restricted sessions (temporary/incognito) gate memory reads/writes and lesson
capture — enforced in the after-turn path, session listing/search, and the
recall API. Details in
[chat-sessions.md](chat-sessions.md#session-model) and
[knowledge-memory.md](knowledge-memory.md#recall--the-privacy-guard).
