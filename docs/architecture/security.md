# Security Model

Defense in depth for a system that runs an autonomous agent on your machine:
authentication modes, command screening, an OS sandbox, one egress chokepoint,
scoped tokens, supply-chain gates, untrusted-content fencing, and a
tamper-evident audit log. Paths are relative to
`PersonalClaw/src/personalclaw/`.

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

OS-level child-process sandboxing for tool execution, including an
environment-variable denylist (credential env vars like `SLACK_BOT_TOKEN`
never reach a sandboxed child).

## Egress chokepoint (`net/`)

`net/client.py` + `net/guard.py` + `net/policy.py` form the ONE outbound-HTTP
chokepoint:

- Named policies: `STRICT`, `CONNECTOR` (knowledge scraping), `WEBHOOK`
  (user-configured POSTs), `LOOPBACK_INTERNAL` (loopback only — **never
  widened** by config).
- `egress_policy_for(base)` is the single config-layering seam: the Security
  panel's allow/deny hosts and `allow_private` are layered onto a base policy
  at the `web_fetch`/`web_extract`/render entry (`web/fetch.py`) and at
  webhook/knowledge-connector call sites (`knowledge/connectors/web_url.py`).
  Raw `net.fetch` stays config-free for fixed-posture internal callers.

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
