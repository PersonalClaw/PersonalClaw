# Plan: Remote User Authentication — Log In From the Internet Without Being Home

**Status:** PROPOSED — created 2026-07-25 from owner request (self-hosting exposed via the
owner's own tunnel — cloudflared / Tailscale / Traefik — wanting to reach the dashboard
from the internet without being on the home network to mint a token).
**Created:** 2026-07-25
**Wave:** 1 — a completable floor with no hard dependency; **it is the prerequisite for the
remote half of COMPANION-APPS + MOBILE-COMPANION** (their device tokens and pairing ride the
durable session store this plan builds). Land the S1 foundation before those consume it.
**Depends on:** nothing hard. **Coordinates with:** EXTERNAL-ACCESS (shares the "the public
URL is a security boundary" doctrine + the `public_url`/`allow_remote` boundary — this plan
owns the **human dashboard** login; EXTERNAL-ACCESS owns **inbound API/agent** bearers — one
"this instance is internet-exposed" signal, two surfaces); TEAM-SHARED-ENTITIES (its
`dashboard.username` owner-identity string is reused as the login identity when present — an
attribution string that graduates into a credential subject here, exactly its "eventually
provisioned by SSO/enterprise login" note); MOBILE-COMPANION + COMPANION-APPS (both consume
the session/device store, §C3).
**Scope:** add a real **user login** as an *additional front door that mints the existing
session token* — settable at deploy / via CLI / via the WebUI on the local network — so a
browser reaching an internet-exposed gateway logs in for a fresh cookie-borne token, and on
expiry logs in again. Underneath it: persist the signing key + a durable session store so
tokens survive a restart (they don't today). **Soul guardrail:** this adds *authentication*,
not multi-tenancy — **one owner, one credential set**. The zero-account local flow stays the
**default and is never removed**: the `?token=` link, `personalclaw token`, the desktop
sidecar (`PERSONALCLAW_DEV_NO_AUTH` on loopback), and CLI/MCP machine auth are all untouched.
Login is **opt-in**, layered on `local_token` — it is one more *issuer* of the one session
token, never a parallel token system and never a replacement (Option C, not Option D). No
cloud middle tier ever holds the credential. A broken login config must **never brick the
box** — the loopback token path always remains as the local escape hatch.

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
