# REMOTE-USER-AUTH — atomic plans

**Source plan:** [`REMOTE-USER-AUTH`](../plans/REMOTE-USER-AUTH.md)  
**Code:** `RUA`  
**Source status:** done



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `RUA-1` | ✅ | Durable session foundation: persist HMAC signing key + move nonce/binding registry to auth/sessions.json (survive restart, fail-closed) | — | A token minted before a gateway restart validates (HTTP 200) after it; signing_key + sessions.json both 0600; revoke_all_sessions()/personalclaw logout kills sessions durably across a restart; corrupt sessions.json forces re-auth and never falls open; generate_token/validate_token signatures unchanged. Verified across 3 boots; test_session_store.py 34 cases; make test 9439 passed. |
| `RUA-2` | ✅ | Owner credential (argon2id) + auth config section + personalclaw auth CLI + deploy-env bootstrap seed | — | argon2id set_password/verify_password round-trips with constant-time (timing-equalized) unknown-user path and no hash in logs; auth section wired through all 5 config points (test_config_roundtrip green; login_enabled/require_totp/lockout knobs PATCH-editable, password never is); CLI auth set-password/enable/disable/status prompted no-echo, refused on non-TTY; PERSONALCLAW_LOGIN_USER/PASSWORD seeds credential once at first boot, no-ops on re-run, does not enable login. login_enabled defaults off. 115 new tests green. |
| `RUA-3` | ✅ | Login front door: POST /api/auth/login\|logout minting the one session token, per-IP lockout, /login redirect, Settings Account panel | `RUA-1`, `RUA-2` | POST /api/auth/login verifies argon2 then mints via the same generate_token/pc_token cookie (one validation path, indistinguishable to middleware); logout revokes the nonce in-memory AND durably; per-IP lockout returns 429 with Retry-After and refuses the correct password while locked; when login_enabled AND a credential exists, expired/absent session serves /login (302) while /api/* still gets 403 JSON; loopback ?token= escape hatch stays reachable even when locked out or credentials.json is corrupt (paste-token gate, no redirect loop); Settings > Account sets credentials over the LAN. Validated across 3 boots; test_auth_login.py 45 cases; make test 9701 + 302 vitest. |
| `RUA-4` | ✅ | Public-exposure hardening (Secure cookie / wss CSP / trusted-proxy headers) + TOTP at login + single-use remote enrollment codes + remote-access guide | `RUA-3` | With dashboard.public_url set: session cookie carries Secure; HttpOnly; SameSite=Lax; wss://<host> allowed in WS CSP; X-Forwarded-Proto/-For trusted only from configured trusted_proxies (spoofed header from untrusted peer ignored, mutation-verified). require_totp refuses password-only login with auth_totp_required and accepts with a live code. auth enroll issues an 8-char single-use code (300s TTL, max 5 outstanding, SHA-256 at rest 0600, constant-time) redeemable once from a second device yielding a persistent issuer:enroll session; reuse rejected. docs/guides/remote-access.md walks tunnel->password->public_url->2FA->phone pairing with a 'what this does not protect you from' section. Storage decision recorded (credentials.json in snapshot set; signing_key/sessions.json/enroll_codes.json excluded). Validated across 4 boots; test_auth_exposure.py 51 cases; make test 9752. |

## Atom scopes

### `RUA-1` — Durable session foundation: persist HMAC signing key + move nonce/binding registry to auth/sessions.json (survive restart, fail-closed)

**Status:** done

Design S1; Contracts C1 (auth/session_store.py); Session 1 tasks T1.1-T1.3 + V1

**Done when:** A token minted before a gateway restart validates (HTTP 200) after it; signing_key + sessions.json both 0600; revoke_all_sessions()/personalclaw logout kills sessions durably across a restart; corrupt sessions.json forces re-auth and never falls open; generate_token/validate_token signatures unchanged. Verified across 3 boots; test_session_store.py 34 cases; make test 9439 passed.

### `RUA-2` — Owner credential (argon2id) + auth config section + personalclaw auth CLI + deploy-env bootstrap seed

**Status:** done

Design S2; Contracts C2 (auth/credentials.py), C4 (auth config section), C5 (CLI); Session 2 tasks T2.1-T2.4 + V2

**Done when:** argon2id set_password/verify_password round-trips with constant-time (timing-equalized) unknown-user path and no hash in logs; auth section wired through all 5 config points (test_config_roundtrip green; login_enabled/require_totp/lockout knobs PATCH-editable, password never is); CLI auth set-password/enable/disable/status prompted no-echo, refused on non-TTY; PERSONALCLAW_LOGIN_USER/PASSWORD seeds credential once at first boot, no-ops on re-run, does not enable login. login_enabled defaults off. 115 new tests green.

### `RUA-3` — Login front door: POST /api/auth/login|logout minting the one session token, per-IP lockout, /login redirect, Settings Account panel

**Status:** done

Design S3 (Option C); Contracts C3 (login/session HTTP surface + error codes); Session 3 tasks T3.1-T3.4 + V3

**Done when:** POST /api/auth/login verifies argon2 then mints via the same generate_token/pc_token cookie (one validation path, indistinguishable to middleware); logout revokes the nonce in-memory AND durably; per-IP lockout returns 429 with Retry-After and refuses the correct password while locked; when login_enabled AND a credential exists, expired/absent session serves /login (302) while /api/* still gets 403 JSON; loopback ?token= escape hatch stays reachable even when locked out or credentials.json is corrupt (paste-token gate, no redirect loop); Settings > Account sets credentials over the LAN. Validated across 3 boots; test_auth_login.py 45 cases; make test 9701 + 302 vitest.

### `RUA-4` — Public-exposure hardening (Secure cookie / wss CSP / trusted-proxy headers) + TOTP at login + single-use remote enrollment codes + remote-access guide

**Status:** done

Design S4; Contracts C3 (enroll routes) + integration points (exposure boundary); Session 4 tasks T4.1-T4.4 + V4

**Done when:** With dashboard.public_url set: session cookie carries Secure; HttpOnly; SameSite=Lax; wss://<host> allowed in WS CSP; X-Forwarded-Proto/-For trusted only from configured trusted_proxies (spoofed header from untrusted peer ignored, mutation-verified). require_totp refuses password-only login with auth_totp_required and accepts with a live code. auth enroll issues an 8-char single-use code (300s TTL, max 5 outstanding, SHA-256 at rest 0600, constant-time) redeemable once from a second device yielding a persistent issuer:enroll session; reuse rejected. docs/guides/remote-access.md walks tunnel->password->public_url->2FA->phone pairing with a 'what this does not protect you from' section. Storage decision recorded (credentials.json in snapshot set; signing_key/sessions.json/enroll_codes.json excluded). Validated across 4 boots; test_auth_exposure.py 51 cases; make test 9752.

