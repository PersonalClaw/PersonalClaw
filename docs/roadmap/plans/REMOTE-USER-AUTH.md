# REMOTE-USER-AUTH

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/RUA.md`](../atomic/RUA.md) as 4 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Remote User Authentication — Log In From the Internet Without Being Home

**Status:** DONE — all four sessions shipped 2026-07-30 (created 2026-07-25 from owner request:
self-hosting exposed via the owner's own tunnel — cloudflared / Tailscale / Traefik — wanting to reach
the dashboard from the internet without being on the home network to mint a token). S1 durable session
foundation (persisted signing key + `sessions.json`, so a restart no longer logs you out), S2 owner
credential (argon2id) + the `auth` config section + the `personalclaw auth` CLI + deploy seed, S3 the
login front door (`POST /api/auth/login`, lockout, `/login`, Settings → Account), S4 public-exposure
hardening (`Secure` cookie, trusted-proxy forwarded headers) + TOTP at login + device enrollment +
`docs/guides/remote-access.md`.
**Recorded tail** (see the Execution log): the TOTP **QR image** in Settings (the secret and
`otpauth://` URI are surfaced, so paste enrollment works today) and passkey/WebAuthn, which this plan
already scoped as future extensions rather than v1. Status corrected 2026-08-04 by code audit (this
line had read PROPOSED). Note: this plan was executed although workspace `ROADMAP.md` §5 records it as
"not auto-inserted" into the execution order — a sequencing discrepancy, not a scope one.

---

## Context (code recon, 2026-07-25)

Verified against code — re-verify before editing; a cited line that has moved is escalation
**E1**, not license to guess.

- **AuthMode + AuthConfig** — `auth/modes.py`: `AuthMode(str, Enum){NONE, LOCAL_TOKEN,
  API_KEY, OAUTH2}` (`:22`); `AuthConfig` (`:31`, frozen dataclass) defaults
  `mode=LOCAL_TOKEN`, `bind_host="127.0.0.1"`, `csrf_required=True`. **`from_env()` (`:49`)
  only special-cases `"none"`; everything else falls through to `LOCAL_TOKEN`** — so
  `api_key`/`oauth2` are unreachable via env. `effective_bind(auth_cfg)` (`:65`) returns
  loopback iff mode is `NONE`.
- **The mode-dispatcher is dead code:** `auth_middleware(auth_cfg, …)` in
  `dashboard/token_auth.py:748` fully implements all four modes (incl. an `auth/oidc.py`
  RS256/ES256 JWKS verifier), **but is never called** — `dashboard/server.py:1446` wires
  `token_auth_middleware(...)` directly, gated by `_no_auth = (mode == NONE)` (`:1407`). So
  in practice only `none` (skip) and `local_token` (default) are reachable. **This plan does
  NOT resurrect the mode-dispatcher** — login is a feature flag on top of `local_token` that
  mints the same token, keeping ONE validation path.
- **Token minting is opaque HMAC, NOT JWT** — `token_auth.py`: `generate_token(user_id,
  ttl_seconds, *, app="")` (`:257`), `_sign` = `hmac.new(_SECRET, payload, sha256)` (`:253`),
  `validate_token(...)` (`:300`) compares via `hmac.compare_digest` and **checks the nonce is
  still in the live set**. **`_SECRET = os.urandom(32)` is module-level (`:41`) — ephemeral,
  regenerated every process start** → all tokens invalidate on gateway restart. TTLs:
  `LINK_WINDOW_SECS = 24h` (`:156`), `MAX_SESSION_TTL_SECS = 365d` (`:160`).
- **Session state is in-memory only** — `TokenStateManager` (`:44`, `threading.Lock`):
  `_nonces` (OrderedDict, `MAX_CONCURRENT_NONCES=5` `:146`, oldest evicted), `_ip_bindings`,
  `_consumed`. All lost on restart. `revoke_all_sessions()` (`:388`) clears it (this is
  `personalclaw logout`). **Together with the ephemeral `_SECRET`, this is the root cause of
  the owner's pain: a fresh token minted locally dies the moment the gateway restarts, and
  can only be re-minted with local access to `.local_secret`.**
- **Middleware** — `token_auth_middleware` (`:416`): credential from `?token=` else cookie
  `pc_token_{port}` (port-specific, `:657`); on first `?token=` use it validates `exp`,
  IP-binds, and sets the HttpOnly `SameSite=Lax` cookie — **`secure=False`** (`:730`, plain
  HTTP/loopback assumption). Deny → `/api/*` 403 JSON else the inline **`_403_HTML`
  paste-token gate** (`:162`). Loopback is NOT exempt (deliberate anti-port-forward).
  Escape hatches: `PERSONALCLAW_DEV_NO_AUTH=1` (`:474`, pass-through-all — the desktop shell
  uses this), `PERSONALCLAW_BYPASS_LOCAL_NETWORKS=1` (`:482`, private-IP skip).
- **Bind logic** — `dashboard/server.py:1508-1514`: loopback unless `PERSONALCLAW_BIND_HOST`
  set or `not local_only`; `AUTH_MODE=none` forces loopback (`:1512`). Security invariant
  (`:1484-1500`): if `dashboard.url` is set, the stack MUST contain a `_is_token_auth=True`
  middleware or the gateway refuses to start.
- **CSRF + origin** — `csrf_middleware` (`server.py:1332`) requires `check_origin`
  (`origin.py:319`) on unsafe methods; WS `/api/ws` authenticates via the same middleware
  (`?token=`/cookie) + `_check_ws_origin`; CSP restricts WS to `ws://localhost:*` — **no
  `wss://`** (`server.py:1274`, cleartext assumption). `_resolved_client_ip`
  (`token_auth.py:444`) trusts `X-Real-IP` only from a loopback/private TCP peer.
- **No user/account/password model exists.** `CRED_OWNER_ID`/`dashboard.user_name` are a
  channel handle + a display string. `CredentialStore` (`llm/credentials.py:80`) holds
  *provider* API keys, not human login. `save_credential(key, value)` → `.env`, 0600
  (`config/loader.py:234`). This plan introduces the identity/credential primitive from
  scratch, forward-compatible with TEAM-SHARED-ENTITIES' `dashboard.username`.
- `personalclaw token` (`cli_server.py:77`) reads `.local_secret`, GETs `/api/token/local`
  (loopback + `X-Local-Secret`, `handlers/core.py:797`), prints `…?token=…`. `--json-ready`
  prints `PERSONALCLAW_READY:{port,token,pid,home}` (`gateway.py:3107`).

## Design

The order is deliberate: **make the token durable first (S1), then give it a login front
door (S2–S3), then make it safe to expose (S4).** S1 is strictly-better on its own (restart
no longer logs you out); each later session is opt-in.

- **S1 — Durable session foundation (the standalone win).** Persist the HMAC signing key at
  `config_dir()/auth/signing_key` (0600, generated once, loaded on boot) so tokens survive a
  restart, and move the session/nonce registry to a durable store
  `config_dir()/auth/sessions.json` (0600) so a valid cookie keeps working across restarts.
  **No change to how tokens are obtained** — the `?token=` link, `personalclaw token`, and
  the desktop sidecar behave identically; they just stop dying on restart. This is the
  foundation MOBILE-COMPANION device tokens + COMPANION-APPS pairing build on. Fail-closed:
  a corrupt `sessions.json` refuses those sessions (re-auth), it does **not** fall open;
  a corrupt/absent `signing_key` regenerates + logs (one round of forced re-login, same as
  today's every-restart behavior — no regression).
- **S2 — Owner credential + CLI + deploy bootstrap.** A single owner credential:
  `{username, password_hash (argon2id)}`. Settable three ways: (a) **deploy** — bootstrap env
  `PERSONALCLAW_LOGIN_USER` + `PERSONALCLAW_LOGIN_PASSWORD` consumed once at first boot to
  seed the credential then cleared from memory (documented for container/systemd deploys);
  (b) **CLI** — `personalclaw auth set-password [--user NAME]` (prompts, never echoes);
  (c) **WebUI on the LAN** — Settings → Account → Login credentials (reachable via the
  existing token flow on the local network). `auth.login_enabled` config flag (default
  **off**). Username reuses `dashboard.username` (TEAM-SHARED-ENTITIES) when set.
- **S3 — Login front door (Option C).** A `/login` page + `POST /api/auth/login
  {username, password, totp?}` that, on success, **mints the existing session token** via
  `generate_token` and sets the `pc_token_{port}` cookie — the same token the `?token=` link
  produces, validated by the same middleware. When `login_enabled`, an expired/absent session
  redirects to `/login` instead of the paste-token gate; on cookie expiry the user simply
  logs in again for a fresh token (the owner's exact ask). `POST /api/auth/logout` clears +
  revokes. Login attempts are rate-limited with lockout, fail-closed, SEL-audited.
- **S4 — Public-exposure hardening + optional 2FA + remote enrollment.** When the instance is
  internet-exposed (a `dashboard.public_url` is configured — the same boundary EXTERNAL-ACCESS
  uses): set `Secure` on the session cookie, allow `wss://<public-host>` in the WS CSP, and
  trust `X-Forwarded-Proto`/`X-Forwarded-For` **only from a configured trusted proxy** (the
  tunnel). Optional TOTP (`auth.require_totp`); passkey/WebAuthn noted as a future extension,
  not built here. A **remote enrollment code** (`personalclaw auth enroll` → short-lived code
  shown locally → redeemed once from any device over the tunnel → a persistent, revocable
  device session) — this is the no-password path and the seam COMPANION-APPS pairing consumes.
  Docs: `docs/guides/remote-access.md` gains the login path beside MOBILE-COMPANION's
  Tailscale/Cloudflare walkthrough.

## Contracts & Interfaces (conventions per [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md))

### C1 — Persistent signing key + durable session store (`auth/session_store.py`, new)
Replaces the ephemeral `_SECRET` and in-memory `TokenStateManager` state with durable
equivalents; the `generate_token`/`validate_token` **signatures are unchanged** (surgical —
they read the key + registry from the store instead of module globals). E4 if a task needs to
weaken an auth invariant to do this — stop and record.
```python
# auth/signing_key: 32 random bytes, atomic_write_bytes(..., mode=0o600), loaded once at boot.
# auth/sessions.json (0600, atomic_write): the durable registry, superseding _nonces/_ip_bindings.
{
  "<nonce>": {
    "user": "<username|local-app>", "device": "",       # device set by COMPANION/MOBILE pairing
    "issuer": "link" | "login" | "enroll" | "local",     # how this session was minted
    "minted_at": "<iso>", "last_seen_at": "<iso>",
    "session_exp": "<iso>", "ip_binding": "<ip|>",        # binding semantics preserved from today
    "revoked": false
  }
}
```
Fail-closed: unreadable/corrupt store → those sessions are invalid (re-auth), never fall open.

### C2 — Owner credential (`auth/credentials.py`, new; storage 0600)
`config_dir()/auth/credentials.json` (0600, atomic_write): `{username, password_hash,
algo: "argon2id", updated_at, totp_enabled: bool}`. **argon2id** hashing (add `argon2-cffi`
to deps). The **TOTP secret is a secret** → credential store `save_credential(
"PERSONALCLAW_TOTP_SECRET", …)` (§2.5), never the JSON file. `set_password(user, plaintext)`,
`verify_password(user, plaintext) -> bool` (constant-time), `verify_totp(code) -> bool`.

### C3 — Login/session HTTP surface (new routes; error envelope §2.2)
| Route | Auth | Purpose |
|---|---|---|
| `GET /login` | none (exempt) | login page (replaces the paste-token gate when `login_enabled`) |
| `POST /api/auth/login` | none (exempt) | `{username, password, totp?}` → mints session token, sets cookie; rate-limited + lockout |
| `POST /api/auth/logout` | session | clears cookie + revokes the nonce |
| `POST /api/auth/enroll/start` | session (LAN) | → `{code}` single-use, TTL 300s, SEL-logged |
| `POST /api/auth/enroll/complete` | none (exempt) | `{code}` → persistent device session (C1 `issuer:"enroll"`) |
Stable snake error codes (Tier-S, never reworded): `auth_invalid_credentials`,
`auth_totp_required`, `auth_locked_out`, `auth_not_enabled`, `auth_enroll_code_invalid`.

### C4 — Config (5-point wiring §2.1 — new `auth` section beside `SecurityConfig`)
`auth.login_enabled: bool=False`, `auth.session_ttl: str="30d"`, `auth.require_totp:
bool=False`, `auth.lockout_threshold: int=5`, `auth.lockout_window: str="15m"`. Each with
`_meta(label, help)`; wired through `load()`, `to_dict()`, `_EDITABLE_CONFIG` (the runtime
subset — **`login_enabled`, `require_totp`, lockout knobs are PATCH-editable; passwords and
`public_url` are NOT** — credential lifecycle is the CLI/Settings-create flow), and a FE
Account panel. Password hash / TOTP secret live in C2, never in `config.json`.

### C5 — CLI (`personalclaw auth …`, two-level per §3.10)
`auth set-password [--user NAME]` (prompted, no echo), `auth enable|disable`, `auth status`,
`auth totp setup|disable`, `auth enroll` (prints an enrollment code), `auth revoke <nonce|--all>`.

### Integration points
- **Calls:** `generate_token`/`validate_token` (unchanged signatures, C1-backed store),
  `save_credential` (TOTP), `atomic_write`/`atomic_write_bytes` (§3.1), `config_dir()`,
  `sel()` (§C-SEL), `check_origin` (existing CSRF guard).
- **Called by:** the browser login page; COMPANION-APPS + MOBILE-COMPANION (the enrollment
  code path + the durable session store — device tokens are C1 rows with `device` set).
- **Depends on:** nothing hard; coordinates with EXTERNAL-ACCESS's `public_url` boundary.
- **Storage:** `auth/signing_key` (0600), `auth/sessions.json` (0600), `auth/credentials.json`
  (0600); TOTP secret in `.env`. All three join the snapshot/export set **except the signing
  key + sessions** (transient/security — mirror EXTERNAL-ACCESS's exclusion of `.env`-adjacent
  transient state; record the decision in the Execution log).
- **SEL (§2.3):** `login_success`, `login_failed`, `login_locked_out`, `password_set`,
  `session_revoked`, `enroll_code_issued`, `enroll_completed`, `signing_key_generated`.

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

**Change class B** (new durable auth state) touching a **security control** (E4-adjacent).
Under the pre-1.0 banner this executes as a clean break (no lifecycle gate/migration) — advise
`personalclaw snapshot` in release notes. Every session ends with the standing DoD.

### Session 1 — Durable session foundation

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | Persist the HMAC signing key: generate-once `auth/signing_key` (0600) loaded at boot, replacing module-level `_SECRET`; keep `generate_token`/`validate_token` signatures identical | `dashboard/token_auth.py`, new `auth/session_store.py` | tokens minted before a restart still validate after it (test); key file is 0600 |
| T1.2 | Move the nonce/binding registry from in-memory `TokenStateManager` to `auth/sessions.json` (0600, atomic_write); preserve current IP-binding semantics exactly; fail-closed on corrupt store | `dashboard/token_auth.py`, `auth/session_store.py` | a cookie session survives a restart; corrupt-store fixture → re-auth (not fall-open); binding regression tests green |
| T1.3 | `revoke_all_sessions()`/`personalclaw logout` operate on the durable store; SEL `session_revoked` | `token_auth.py`, `cli_server.py` | logout kills sessions across a restart; SEL line present |
| V1 | Validation: mint a token, restart the gateway, confirm the browser session persists; revoke, confirm lockout; verify file modes | — | recorded |

### Session 2 — Owner credential + CLI + deploy bootstrap

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | `auth/credentials.py`: argon2id `set_password`/`verify_password` (constant-time), `credentials.json` (0600); add `argon2-cffi` dep | `auth/credentials.py`, `pyproject.toml` | set→verify round-trips; wrong password rejected; hash never logged |
| T2.2 | `auth` config section (C4) wired through all 5 points; `login_enabled` default off | `config/loader.py`, `dashboard/handlers/core.py`, `web/src/lib/api.ts` | `test_config_roundtrip` green; PATCH toggles `login_enabled` |
| T2.3 | CLI `personalclaw auth set-password/enable/disable/status` (two-level, prompted no-echo) | `cli.py`, `cli_server.py` | commands set/report credential state; password never appears in argv/history |
| T2.4 | Deploy bootstrap: `PERSONALCLAW_LOGIN_USER`+`PERSONALCLAW_LOGIN_PASSWORD` seed the credential once at first boot then clear from memory; documented for container/systemd | gateway boot path, `docs/guides/containers.md` | fresh home + env → credential seeded; env absent → no-op; re-run doesn't re-seed |
| V2 | Validation: set a password three ways (deploy env, CLI, and confirm the LAN Settings path lands in S3); `auth status` reflects each | — | recorded |

### Session 3 — Login front door (Option C)

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | `POST /api/auth/login` mints the existing session token on argon2 verify + sets `pc_token_{port}` cookie; `POST /api/auth/logout` clears+revokes; error envelopes per C3 | `dashboard/handlers/auth.py` (new), `server.py` route wiring | login → authenticated cookie session; logout → 403 on next request |
| T3.2 | Rate-limit + lockout on `/api/auth/login` (threshold/window from C4; fail-closed; SEL `login_failed`/`login_locked_out`) | `handlers/auth.py` | N failures → lockout with `Retry-After`; SEL trail complete |
| T3.3 | `/login` page + middleware redirect: when `login_enabled`, an expired/absent session serves `/login` (not the paste-token gate); the `?token=` + loopback paths are unchanged | `web/src/pages/Login.tsx` (or served template), `token_auth.py` deny path | expired cookie on an exposed instance lands on `/login`; local `?token=` still works |
| T3.4 | Settings → Account: set/change login credentials + enable login, reachable over the LAN via the existing token session | `web/src/pages/settings/AccountPanel.tsx` | credentials set from the browser on the LAN; guarded copy in the security voice |
| V3 | Validation: enable login, log in from a browser, let the cookie expire (short TTL fixture), re-login; confirm the loopback escape hatch still works with login enabled | — | recorded |

### Session 4 — Public-exposure hardening + 2FA + remote enrollment

| ID | Task | Files | Done when |
|---|---|---|---|
| T4.1 | When `dashboard.public_url` set: `Secure` cookie, `wss://<public-host>` in the WS CSP, `X-Forwarded-Proto`/`-For` trusted **only** from a configured trusted proxy | `token_auth.py`, `server.py` CSP, `origin.py` | over TLS the cookie carries `Secure`; WS connects via `wss://`; forwarded headers ignored from untrusted peers (test) |
| T4.2 | Optional TOTP (`auth.require_totp`): `auth totp setup` (secret → credential store, QR in Settings), verified at login | `auth/credentials.py`, `handlers/auth.py`, Account panel | with TOTP on, login requires a valid code; `auth_totp_required` returned when missing |
| T4.3 | Remote enrollment: `auth enroll` code → `POST /api/auth/enroll/complete` → persistent device session (C1 `issuer:"enroll"`); single-use, TTL 300s, SEL-logged | `handlers/auth.py`, `cli.py` | code redeemed once from a second device yields a working session; reuse rejected |
| T4.4 | Docs: `docs/guides/remote-access.md` login section (tunnel + TLS termination + login), threat notes; coordinate with MOBILE-COMPANION's Tailscale walkthrough | guide | a reader exposes the dashboard via their tunnel and logs in from cell data following it verbatim (owner V4) |
| V4 | Validation: from off-network over the owner's tunnel — log in (with TOTP), confirm `Secure`/`wss`, redeem an enrollment code on a phone, revoke it | — | recorded |

## Success Criteria (adversarial / observable)

1. **Durability:** a browser session and a device session both survive a gateway restart (they do not today); a corrupt `sessions.json` forces re-auth and never falls open.
2. **One token model:** a login-minted cookie and a `?token=`-minted cookie are indistinguishable to the middleware — there is exactly one validation path; disabling login leaves today's behavior byte-identical.
3. **Local escape hatch intact:** with `login_enabled` and a *corrupt credentials file*, the owner can still reach the box via the loopback `?token=`/CLI path and fix it — the login layer cannot brick a local install.
4. **Exposure hardening:** over a TLS-terminating tunnel the session cookie is `Secure`, WS uses `wss://`, and forwarded headers from a non-trusted peer are ignored (spoofed `X-Forwarded-For` cannot bypass IP binding).
5. **Lockout:** N failed logins within the window lock out with `Retry-After`; every attempt is SEL-audited; no password or hash ever appears in logs, argv, or API responses.
6. **Remote-first:** the owner, off their home network, reaches an exposed dashboard and obtains a fresh working token via login — without any local access to `.local_secret` (the whole point of the plan).

## Owner tasks (real world)

1. **Decide the default posture** — login ships **off**; confirm that (a local-only install should never be forced to set a password).
2. **Set a strong password** (S2) and decide whether **TOTP is required** for your exposed instance (S4).
3. **TLS termination** — your tunnel/reverse proxy (cloudflared / Tailscale Serve / Traefik) must terminate TLS and set a configured trusted-proxy identity; provide `dashboard.public_url`. The plan assumes no in-process TLS (unchanged from today).
4. **Validation (V4):** exercise login from off your home network over your own tunnel, including a phone enrollment.

## Risks & open questions

| Risk | Mitigation |
|---|---|
| Persisting the signing key / sessions weakens an auth invariant | C1 keeps `generate_token`/`validate_token` signatures identical + preserves IP-binding semantics; any needed weakening is **E4** — stop and record, don't improvise |
| A login layer bricks a local box if its config corrupts | Success Criterion 3 is a hard test: the loopback `?token=`/CLI path is always available and never gated by login |
| Cleartext cookie leaks on a misconfigured public bind | `Secure` cookie + `wss` CSP activate off `public_url`; docs make TLS termination a precondition; no auto-bind to a public interface without the operator setting `PERSONALCLAW_BIND_HOST` |
| Overlap with EXTERNAL-ACCESS / MOBILE-COMPANION auth | This plan owns the **human dashboard** login + the durable session store; EXTERNAL-ACCESS owns **inbound API/agent** bearers; MOBILE-COMPANION/COMPANION-APPS **consume** the session store — coordinated, not duplicated (contract index updated in INTEGRATION-ARCHITECTURE §5) |
| **Open:** passkey/WebAuthn | Noted as a future extension on the same login surface; not built here (password + optional TOTP is the v1) |
| **Open:** multi-user | Deliberately out of scope — one owner credential. When TEAM-SHARED-ENTITIES tailors to a team, the username graduates to an SSO-provisioned subject (its stated future), reusing this login surface |

## Execution log

- 2026-07-30 — **DONE (S1: the durable session foundation).**

  **The bug, precisely.** `token_auth._SECRET = os.urandom(32)` ran at MODULE SCOPE and the
  valid-nonce set lived in memory, so **every gateway restart invalidated every token**. Locally
  that means re-running `personalclaw token` after each restart; off-network it means you are
  locked out, because minting a fresh URL requires being on the machine. Confirmed by grep in a
  prior session; now fixed and verified against a real restart.

  **Both halves were required — a persisted key alone would not have fixed it.** With only the
  key, a pre-restart token verifies its signature and is then refused for having no session
  record: the same lockout with a more confusing reason. So `session_store.py` persists **two**
  things — the 0600 signing key (`session_key`) and the session records (`sessions.json`) — and
  `is_nonce_valid` consults the durable store before refusing, adopting a hit into memory so
  eviction ordering treats a restored session like any other.

  **A security hole the existing suite caught, and the E4 rule earned its keep.**
  `test_token_rejected_when_no_nonces_registered` failed: `revoke_all_sessions()` cleared memory
  only, so with my durable store a revoked token would be rejected until the next restart and
  then **accepted again**. A revoke that un-revokes itself on reboot is worse than none, because
  you would believe you had cut access off. Per the contract I did NOT edit the assertion — I
  fixed the code to clear both stores. **All 208 pre-existing auth tests pass with zero edits.**

  **Owner TTL ruling applied.** `DEFAULT_BROWSER_SESSION_TTL_SECS = 30 days` for the two gateway
  startup mints (a human opens those URLs); the 1-year `MAX_SESSION_TTL_SECS` cap stays reachable
  **only** when a caller asks explicitly, which is the `personalclaw token` / automation case.
  Rationale in-code: sessions were minted at the 1-year cap precisely BECAUSE they were ephemeral
  — a restart wiped them, so the number never applied. Now that they survive, a 1-year browser
  cookie would outlive its reason and a stolen one would stay good for a year.

  **A bug of mine, and the lesson.** `use_ephemeral_secret(None)` overloaded `None` to mean both
  "generate one for me" and "turn this off", so a test calling it to DISABLE ephemeral mode
  silently enabled it — and the headline "token survives a restart" test failed with "invalid
  signature", pointing at the persistence code when the fault was in the toggle. Split into
  `use_ephemeral_secret()` / `use_persistent_secret()`, with a test pinning the distinction.

  **Fail-closed, deliberately against the house default.** `load_or_create_key()` raises rather
  than falling back to an ephemeral key: a silent fallback looks identical to working until the
  next restart logs everyone out again — the exact bug being fixed. Ephemeral signing is now an
  explicit opt-in. The key is 0600 from creation and re-tightened on read (a key another local
  account can read is one it can mint a session with); `sessions.json` is 0600 too, since it
  names live nonces. `session_stats()` reports counts only — a nonce in a status payload is a
  credential.

  **ARCC was NOT queried — the MCP server is unavailable in this session.** Standard practice
  applied: fail-closed posture, least privilege on both files (0600), no secret in any log or
  status payload, the existing auth suite used unedited as the regression lock, and revocation +
  rotation both verified to survive a restart.

  **Validated as a user** on an isolated dev home (port 10747, never :10000) across **three real
  gateway boots**: a token minted before the restart returned **HTTP 200 after it** (previously a
  guaranteed 401); `session_key` and `sessions.json` both present at 0600; `revoke_all_sessions()`
  → the token 403s and **stayed 403 after another restart**; `rotate_key()` changed the key.
  Confirmed both TTL paths through the real code: a browser mint is **30 days**, an explicit CLI
  mint is **365**. (`personalclaw token` reported 0.83 days because its own `--ttl` default is
  20h — an explicit request, not the browser default.) **0 tracebacks across all three boots.**

  **Gates:** `make lint` clean (mypy 555 files) · `make test` **9439 passed, 0 failed**.
  Tests: `tests/test_session_store.py`, 34 cases.

  **NOT in this session** (S2-S4): the owner credential (`personalclaw auth set-password`), the
  login front door that mints the session into a cookie, and the public-exposure hardening
  (Secure cookie / `wss` / trusted-proxy / TOTP). S1 stands alone and is strictly better without
  them: restart no longer logs you out.

- 2026-07-30 — **DONE (S2: the owner credential, its config section, the CLI, the deploy seed).**

  **`argon2-cffi` is a CORE dependency, not an extra.** A password verifier that only exists on
  some installs makes `auth.login_enabled` a setting that silently cannot work — and the failure
  would land on an internet-exposed box, which is the worst place to discover a missing wheel.
  It clears the same bar `reportlab`/`tree-sitter` already did: prebuilt wheels, no compiler, no
  torch weight. `uv.lock` re-locked in the same commit (CI runs `uv sync --locked`).

  **Timing equalization, because "no such user" must not be faster than "wrong password".**
  `verify_password` runs the argon2 verify even when there is no stored credential or the
  username does not match, against a module-level dummy hash of a random value. Measured live:
  wrong-password 28ms vs unknown-user 24ms (1.17×), where an early return would have been ~0ms
  against ~25ms — i.e. a trivially observable username oracle. Pinned by two tests that observe
  the verify actually running (a call record, not a wall-clock threshold, which would flake on a
  loaded CI box) and assert WHICH hash it ran against.

  **Fail-closed reads.** An unreadable, non-dict, or hash-less `credentials.json` means "no
  credential configured", never "allow" — five tests cover the mangled-file shapes, including a
  hand-edited record with the hash removed, which must not become a passwordless login.

  **The rotation-safety property in the deploy seed (T2.4).** `bootstrap_from_env()` is a no-op
  when a credential already exists, so a unit file or `.env` that keeps `PERSONALCLAW_LOGIN_*`
  set cannot silently reset the password to the deploy-time one on every restart — which would
  quietly undo a rotation and is the kind of bug nobody notices until they are locked out. It is
  also non-fatal (a too-short password logs and continues; a gateway that will not boot is worse
  than one you must set a password on) and it does NOT enable login — enrolling a credential and
  opening a front door stay separate decisions.

  **DEVIATION — TOTP primitives landed early.** T4.2 is an S4 row, but T2.3's `auth totp setup`
  had to either work or not exist. `auth/totp.py` is stdlib-only (`hmac`/`base64`), pinned to all
  six **RFC 6238 Appendix B** SHA-1 vectors. Not hand-rolled crypto in the forbidden sense: it
  calls `hmac.new()` exactly as the RFC specifies, and a dependency on the credential path buys
  nothing here. Password hashing is the opposite case and gets `argon2-cffi`. S4 wires it into
  the login flow; the enrollment/verify/skew behavior is already tested (35 cases).

  **DEVIATION — `parse_config_duration` is a second function, not a widened `parse_duration`.**
  The existing one serves `personalclaw token --ttl`, where an unrecognised unit must be a hard
  error the user sees immediately; reading `30d` as something else there would mint a token with
  the wrong lifetime. Config is the opposite posture: a hand-edited typo takes the documented
  default rather than bricking the box. Widening the original would have forced editing its
  existing "`30d` is invalid" assertion — an E4 stop — so it is left byte-identical and pinned by
  a test that says so.

  **Found and fixed a live doc bug in T2.4's path.** `docs/guides/containers.md` told headless
  users to set `PERSONALCLAW_AUTH_MODE=api_key` with `PERSONALCLAW_API_KEY`. `AuthConfig.from_env`
  honors only `none` — that setting does *nothing*, so anyone following the guide believed they
  had configured auth they did not have. Replaced with the owner-login flow and an explicit note
  that `api_key` is not wired.

  **The credential is not reachable through the config surface.** `login_enabled`/`require_totp`/
  the lockout knobs are PATCH-editable (turning login off, or loosening a lockout you tripped,
  should not need a restart); the password never is, and a test asserts no key matching
  password/credential/hash/secret can enter `_EDITABLE_CONFIG` — plus one that fails when a NEW
  `auth` field is added without deciding either way. Setting a password is CLI-only, prompted via
  `getpass`, and refused on a non-TTY so a piped secret from a shell history or CI log cannot
  become the login.

  **ARCC was NOT queried — the MCP server is unavailable in this session.** Standard practice
  applied: argon2id at the RFC 9106-informed profile, per-credential salt, 0600 on the credential
  file, fail-closed reads, no plaintext or encoded hash in any log/status/argv path (asserted),
  timing-equalized verification, TOTP secret held in the credential store rather than beside the
  hash, and a 12-character floor.

  **Gates:** `make lint` clean (mypy 560 files) · 115 new tests green
  (`test_auth_credentials.py` 35, `test_auth_totp.py` 35, `test_auth_config_and_cli.py` 45) ·
  `test_config_roundtrip` + the 201-test auth/CLI/loader contract set green with **zero edits to
  existing assertions**.

  **NOT in this session** (S3-S4): the `/login` page and `POST /api/auth/login` that mint the
  session into a cookie, rate-limit + lockout enforcement, Settings → Account, and the
  public-exposure hardening (Secure cookie / `wss` CSP / trusted-proxy headers / enrollment
  codes). S2 stands alone: a credential exists, is reachable from the CLI, and no surface offers
  it yet — `login_enabled` defaults off, so behavior is unchanged for every existing install.

- 2026-07-30 — **DONE (S3: the login front door — routes, lockout, `/login`, Account panel).**

  **One ISSUER, not a second authorization path.** `POST /api/auth/login` verifies argon2 and then
  calls the same `generate_token` the `?token=` link and `personalclaw token` already call, setting
  the same `pc_token_{port}` cookie with the same flags. The middleware cannot tell the two apart —
  which is Success Criterion 2, and is asserted by validating a login-minted token through the
  ordinary `validate_token` path. No new validator exists to drift out of step with the old one.

  **The anti-brick property is enforced by CODE, not by documentation.** `_login_offered()` requires
  BOTH `login_enabled` AND an actually-configured credential before any page redirects to `/login`,
  and any exception falls back to the paste-token gate. So enabling login and then losing/corrupting
  the credential file leaves the gate — the escape hatch — reachable, rather than bouncing every
  page to a form nobody can pass. Verified live on a fresh gateway with a corrupted
  `credentials.json`: the page served **403 with the gate** and login refused fail-closed.

  **`revoke_token()` added, because "logout" was otherwise theatre.** Only `revoke_all_sessions`
  existed. Clearing the cookie alone leaves the token live for anyone holding a copy (a synced
  browser profile, a proxy log), so logout now revokes the nonce in memory **and** in the durable
  store. The durable half is the security half — the same class of bug S1 fixed for revoke-all: an
  in-memory-only drop would be re-accepted after a restart.

  **Lockout is per-IP, fail-OPEN on bookkeeping, and refuses the correct password too.** A lockout
  that let a correct guess through would be decorative. The failure table is in memory (persisting
  it would give an unauthenticated endpoint a write primitive) and capped at 4096 IPs so address
  rotation cannot grow it. `X-Forwarded-For` is deliberately IGNORED for the client key: an
  untrusted peer can forge it, which would let an attacker both reset their own counter and lock out
  an arbitrary victim. S4 (T4.1) introduces trusted-proxy handling.

  **No enumeration.** Wrong username and wrong password both return `auth_invalid_credentials` and
  both pay the argon2 cost (S2's timing equalization). `auth_not_enabled` is deliberately distinct —
  "this door does not exist here" is the owner's own configuration, not a secret, and conflating it
  would make a misconfiguration indistinguishable from a typo. TOTP is checked AFTER the password so
  a valid-password/missing-code case is not separable by timing.

  **Exemptions are exactly three**, pinned by a test: `/login`, `/api/auth/login`,
  `/api/auth/status`. Logout, the session view and the password setter stay behind the middleware,
  verified by driving them through the REAL middleware rather than only inspecting the list.
  `/api/auth/status` returns two booleans and never the username or whether a credential exists.

  **DEVIATION — `POST /api/auth/password` accepts a password in a request body.** This looks like it
  contradicts S2's "CLI-only" rule, and the distinction is deliberate: it sits BEHIND the session
  middleware and the CSRF origin check, so it cannot be reached without an existing valid session.
  T3.4 asks for setting credentials from the browser on the LAN; the alternative is that a user who
  only ever reaches their box through a browser can never set a password at all. What stays true is
  that no UNAUTHENTICATED path accepts a password, and the PATCH allowlist still refuses anything
  password-shaped.

  **Found and fixed a collision of my own making.** My new handler was also named `api_auth_status`,
  colliding with the pre-existing `handlers_system.api_auth_status` behind `/api/auth-status` in the
  build-time reference generator (which keys on function name) — the generated docs silently replaced
  the older route's description with mine. Renamed to `api_login_status`; both routes now document
  correctly. Two repo guards earned their keep: the reference-drift test and
  `test_api_manifest_drift`, which required `/login` to be explicitly declared UI-transport rather
  than left as an undocumented surface.

  **ARCC was NOT queried — the MCP server is unavailable in this session.** Standard practice
  applied: HttpOnly + SameSite=Lax cookie (asserted), CSRF origin check on every mutating auth
  route, fail-closed verification, no enumeration oracle, per-IP rate limit with `Retry-After`,
  durable revocation, SEL trail on `login_success`/`login_failed`/`login_locked_out`/
  `session_revoked`/`password_set`, and no password or hash in any response, log, or status payload.

  **Validated as a user** on an isolated dev home across **three real gateway boots** (ports
  10853/10854, never :10000): unauthenticated page → **302 to /login** while an API request still
  got **JSON 403**; wrong password → 401 `auth_invalid_credentials`; correct password → cookie that
  authorized both a page (200) and `/api/sessions` (200); logout → the same cookie **stopped
  working** (302/403); 3 failures → **429 with `Retry-After: 899`** and the
  correct password also refused; with login enabled AND locked out, the local `?token=` path still
  returned **200** (the escape hatch); a corrupted credential file → the **paste-token gate**, not a
  redirect loop; and a login session **survived a gateway restart**. **0 tracebacks across all
  three boots.**

  **Gates:** `make lint` clean (mypy 561 files) · `make test` **9701 passed, 0 failed** · web
  typecheck + **302 vitest** + build green. Tests: `tests/test_auth_login.py`, 45 cases.

  **NOT in this session** (S4): `Secure` cookie + `wss` CSP + trusted-proxy forwarded headers off
  `dashboard.public_url`, the TOTP QR in Settings, remote enrollment codes
  (`/api/auth/enroll/start|complete`), and `docs/guides/remote-access.md`.

- 2026-07-30 — **DONE (S4: exposure hardening + TOTP at login + device enrollment + the guide).**

  **DEVIATION — `dashboard.public_url` did not exist; the plan named a field that was never built.**
  Only `inbound.public_url` (MCP-READONLY-INBOUND) existed. Added `dashboard.public_url` +
  `dashboard.trusted_proxies`, and put the resolution in ONE place (`dashboard/exposure.py`) that
  prefers the dashboard field and falls back to the inbound one — because the plan's own
  coordination note says there is one "this instance is exposed" signal serving two surfaces. An
  operator who already declared exposure for the inbound surface should not have to say it twice
  and silently keep an unhardened dashboard.

  **`dashboard.url` is deliberately NOT the signal**, though it already exists and looks apt. It
  means "a URL to put in links", and people legitimately set it to a LAN/`http://` address.
  Deriving `Secure` from it would set a flag that makes the cookie undeliverable over plain http —
  the user would be silently unable to log in with nothing pointing at the cause. Pinned by a test.

  **The forwarded-header rule was the real vulnerability.** `_resolved_client_ip` trusted
  `X-Real-IP` based on the SHAPE of the peer address (`10.`/`172.1x`/`192.168.`). On an exposed
  box every container neighbour, LAN device and SSRF-able local service sits on a private address,
  so any of them could set that header and move a session's IP binding. Now: exposed ⇒ only a peer
  in `trusted_proxies` is believed (empty = trust nothing); NOT exposed ⇒ **behavior unchanged**,
  because breaking every compose/nginx user to harden the few would be a regression paid by
  everyone. **Mutation-verified**: reverting the hardening makes
  `test_forwarded_header_ignored_from_an_untrusted_peer_when_exposed` fail (403 vs 200), so the
  test detects the actual vulnerability rather than merely covering the line.

  **`Secure` is shared, not duplicated.** Both mint paths (middleware + login handler) call one
  `secure_cookies()`, so a login cookie cannot end up with a different posture than a link cookie.
  An `http://` public URL deliberately does NOT get `Secure` — insecure by nature, but not broken;
  the guide says plainly that TLS termination is the precondition.

  **Enrollment codes bound the blast radius of an 8-character string:** single-use (consumed and
  persisted BEFORE the session is minted, so a race cannot redeem twice), 300s TTL, at most 5
  outstanding (an attacker cannot widen the guess space by asking for thousands), SHA-256 at rest
  in a 0600 file (reading it yields nothing redeemable), constant-time compared, fail-closed on an
  unreadable store, and wrong codes count toward the same lockout as passwords. Alphabet excludes
  I/O/0/1 because the code is read off one screen and typed into another.

  **A REAL BUG found only by live validation, and it was pre-existing on `main`.**
  `personalclaw auth revoke --all` printed success while the revoked session **kept working**.
  Two faults: (1) my CLI cleared the on-disk store while the running gateway held the nonces in
  memory — a two-process state bug no in-process test can see; (2) the root cause — **`/api/logout`
  was missing from `_BYPASS_EXACT`**, so the dashboard middleware demanded a session token before
  `api_logout`'s own loopback + `X-Local-Secret` check could run. Every `personalclaw logout` has
  been returning 403 while the CLI reported "✅ All dashboard sessions revoked." A
  revoke-everything command that could never revoke anything.

  Fixed by routing the CLI through the running gateway (reusing the existing rail, no new
  authenticated surface) and adding `/api/logout` beside `/api/token/local` — the precedent for a
  route that authenticates ITSELF. Exempting it opens nothing; it lets the request reach the check
  that guards it, asserted by a test that drives the middleware and then the handler. Both
  regression tests are **mutation-verified**. I first tried `internal_paths`, which was the wrong
  lever (that branch wants `X-Internal-Secret`) and reverted it.

  **DEVIATION — `auth revoke <nonce>` is not built; only `--all`.** A per-nonce revoke means
  printing live nonces so the user can choose one, and a nonce in a terminal or shell history is a
  credential. Re-authenticating is cheap; leaking a session identifier to save a step is not.

  **T4.4 guide** (`docs/guides/remote-access.md`, linked from the README) walks a reader from
  tunnel → password → `public_url` → 2FA → phone pairing, and has an explicit **"what this does not
  protect you from"** section (weak passwords, compromised devices, the tunnel provider, the
  agent's own reach, plain-http exposure). Also fixed `containers.md`, which recommended
  `PERSONALCLAW_AUTH_MODE=api_key` — a mode `AuthConfig.from_env` does not honor, so readers
  believed they had configured auth they did not have.

  **ARCC was NOT queried — the MCP server is unavailable in this session.** Standard practice
  applied: default-deny trusted proxies, no shape-based trust of attacker-controlled headers,
  `Secure`+`HttpOnly`+`SameSite=Lax` cookies on an exposed instance, single-use hashed short-lived
  codes, rate limiting on the pre-auth redemption endpoint, 0600 on every new file, fail-closed
  reads throughout, and no secret in any log, status payload or response.

  **Validated as a user** across **four gateway boots** on an isolated dev home (port 10861, never
  :10000), configured as exposed: the session cookie carried **`Secure; HttpOnly; SameSite=Lax`**;
  the CSP contained **`wss://pc.example.com https://pc.example.com`**; a CLI-minted code
  (`FYHW-PB9C`) paired a device (200) and was then **refused on reuse**; the code store held **no
  plaintext** at 0600; `require_totp` refused a password-only login with `auth_totp_required` and
  accepted it with a live code; and after the fix `auth revoke --all` **killed the live session**
  (302) with `personalclaw logout` working again. **0 tracebacks.**

  **Gates:** `make lint` clean (mypy 563 files) · `make test` **9752 passed, 0 failed** · web
  typecheck + **302 vitest** green. Tests: `tests/test_auth_exposure.py`, 51 cases.

  **Storage decision (plan's request to record):** `auth/credentials.json` joins the
  snapshot/export set; `auth/signing_key`, `auth/sessions.json` and `auth/enroll_codes.json` are
  **excluded** — a signing key or live nonce in a portable archive is a credential in a backup,
  and enrollment codes are 5-minute artifacts that would be expired garbage on restore.

  **Remaining in this plan:** the TOTP **QR image** in Settings (the secret + `otpauth://` URI are
  both surfaced, so enrollment works today by paste; a rendered QR needs a frontend qr dependency
  — deferred as a taste/dependency call, not a blocker), and passkey/WebAuthn, which the plan
  already lists as a future extension rather than v1 scope.
