# SSO / OIDC integration — design note

**Status: design note. No code ships with this document.** It maps the auth entry point that
exists today onto a provider-plugin boundary, fixes where SSO configuration lives, enumerates the
security controls it touches, and names the follow-on work. Every claim below cites the file and
line it was read from; where the as-built contradicts an assumption, the as-built wins and the
contradiction is stated.

Read [`security.md`](security.md) §"Auth modes" first — it already records that `oauth2` is
declared and unreachable, and that wiring the selector "carries one design question worth settling
deliberately rather than in passing" (`docs/architecture/security.md:44`). This note settles it.

---

## 1. What exists today

### 1.1 Three entry points, one validator

The gateway has **three ways to obtain a session and exactly one way to validate one.** That
asymmetry is deliberate and is the load-bearing fact of this whole design:

| Entry point | Mints | Where |
|---|---|---|
| Tokened URL (`?token=…`) | `generate_token()` | `src/personalclaw/dashboard/token_auth.py:497` |
| `personalclaw token` CLI | the same `generate_token()` | `src/personalclaw/dashboard/token_auth.py:497` |
| `POST /api/auth/login` (password) | the same `generate_token()` | `src/personalclaw/dashboard/handlers/auth.py:216` |

All three produce the identical artifact — a signed payload
`{sub, exp, session_exp, iat, nonce}` (`token_auth.py:537-543`) carried in the
`pc_token_{port}` cookie — and all three are validated by one middleware,
`token_auth_middleware` (`token_auth.py:802`). The login handler's own docstring states the rule
this note obeys:

> **This is one more ISSUER of the existing session token, not a second way to be authorized.**
> […] Downstream, the middleware cannot tell a login-minted session from a link-minted one —
> there is exactly one validation path, which is the point: a second path is a second place for
> an authorization bug to hide.
> — `src/personalclaw/dashboard/handlers/auth.py:3-8`

The cookie is set by one shared resolver so the two issuers cannot drift into two security
postures (`handlers/auth.py:230-247`, and its docstring at `:231-237` on why `secure_cookies()` is
shared rather than re-decided).

`/login`, `/api/auth/login` and `/api/auth/status` are the only paths exempt from token auth, and
the exemption is an explicit three-element set: `token_auth.py:363`.

### 1.2 The declared-but-unreachable fourth thing — and why it is not the answer

`AuthMode.OAUTH2` exists (`src/personalclaw/auth/modes.py:37`) and a complete OIDC JWT verifier
ships with it (`src/personalclaw/auth/oidc.py:33`, `cryptography` lazily imported at `:47-66`).
It is wired: `auth_middleware` branches on the mode at `token_auth.py:1247` and constructs an
`OidcVerifier` at `:1254`.

It is also **not selectable**. `SELECTABLE_MODES` contains only `none` and `local_token`
(`modes.py:46-49`); `oauth2` and `api_key` are in `UNSELECTABLE_MODES` (`modes.py:57`);
`AuthConfig.from_env()` returns `classify_auth_mode_request().effective`, which for `oauth2`
is `LOCAL_TOKEN` plus a warning (`modes.py:160-176`, `:136-139`). The only three runtime
construction sites are all `from_env()`: `dashboard/server.py:411`, `dashboard/server.py:2368`,
`dashboard/origin.py:275`. So the missing half is **selection, not verification.**

But selection alone would be the wrong fix, because of *what* the OAUTH2 branch does. It
**replaces the entire middleware** (`token_auth.py:1247-1282`) with a bearer-JWT validator:

- it reads only `Authorization: Bearer` (`:1268`) — browsers do not send that on navigations, so
  it cannot serve a dashboard SSO login at all;
- it sets `request["user"] = claims.get("sub", "")` (`:1278`) and **never sets `request["app"]`**,
  which `token_auth_middleware` does at `:1110`. `request["app"]` is what the app permission
  sandbox keys on — the identical defect class already documented for `AUTH_MODE=none` in
  `security.md:51-55`;
- it has no cookie, no CSRF/origin check, no IP binding (`token_auth.py:633`), and no SEL audit
  row on grant or deny — all of which the `local_token` path has;
- it removes the `?token=` escape hatch that `handlers/auth.py:11-13` deliberately preserves so a
  credential problem cannot brick the box.

In the vocabulary of §1.1, the OAUTH2 branch is a **second validation path**. That is precisely
the thing `handlers/auth.py:3-8` forbids. Therefore:

> **Design decision.** Browser SSO is a **fourth ISSUER** of the existing session token, not a
> fourth validator. The IdP proves who the operator is; `generate_token()` remains the only thing
> that mints a session and `token_auth_middleware` remains the only thing that validates one.

The fate of the existing OAUTH2 middleware branch is a separate ruling — see §4, control **C4**.

### 1.3 The two identity strings this must respect (issue #960)

Issue #960 reports that Settings → Account carries two fields one word apart. They are two
distinct concepts and both are already shipped:

| Concept | Storage | Code | UI |
|---|---|---|---|
| **Attribution username** ("Username") | `config.json` → `dashboard.username` | `identity.py:80 current_username()`, normalized on load by `loader.py:327 _slug_username`, field at `loader.py:1364`, loaded at `loader.py:3586` | `web/src/pages/settings/AccountPanel.tsx:109` (comment naming it the attribution handle at `:38`) |
| **Sign-in username** ("Sign-in username") | `~/.personalclaw/auth/credentials.json` (0600) | `auth/credentials.py:122 set_password`, `:153 verify_password`, `:116 has_credentials` | `web/src/pages/settings/AccountPanel.tsx:226` |

The semantics are already written down and they are not symmetric:

- The attribution username **is not a credential**: "Nothing authenticates against it and nothing
  authorizes on it. It answers 'who wrote this row', not 'who may'" (`identity.py:12-13`). And "a
  rename affects future writes only. Existing records keep the string they were written with —
  rewriting history to match a new name would silently falsify the record it exists to preserve"
  (`identity.py:14-17`).
- The sign-in username exists **only as a login subject**, and its own docstring already
  anticipates this note: a username exists "only so the login form has a subject and so it can
  later graduate into an SSO-provisioned one (TEAM-SHARED-ENTITIES' identity string)"
  (`auth/credentials.py:5-6`).

That sentence is the hook. SSO does not need a new identity field; it needs to populate the one
that was built to be populated this way.

> **Design decision (the clean break).** An IdP-verified subject lands on the **sign-in username
> side only**. The attribution username is never derived from, defaulted from, or overwritten by
> any IdP claim. There is no third identity string, no `sso_subject` parallel to `username`, and no
> second login path.

Two consequences worth stating because they are easy to get wrong:

- **The session subject is already the sign-in username.** `handlers/auth.py:216` mints with
  `generate_token(username.strip() or "owner", …)`. An SSO issuer therefore mints with the mapped
  sign-in username and the token payload's `sub` keeps meaning exactly what it means today.
- **The attribution username stays empty until the owner sets it.** `current_username()` returns
  `""` when unset and every write path degrades to "no attribution" (`identity.py:80-93`). Filling
  it from an IdP `preferred_username` would silently start stamping records with a string the owner
  never chose, and — per `identity.py:14-17` — could never be corrected retroactively. Not done.

#960's own suggested fix (rename "Username" → "Attribution handle") is orthogonal to this note and
does not block it: this design reuses the *fields*, whatever their labels end up being. It does
make the rename more valuable, because after SSO the sign-in side may be provisioned rather than
typed, and a label that says "attribution" survives that change while "Username" does not.

### 1.4 What the shipped verifier gives us, and two measured gaps

Adopted **unchanged** (`src/personalclaw/auth/oidc.py`):

- JWKS fetch with a 1-hour cache and stale-on-refresh-failure fallback (`:26`, `:77-94`);
- signing-key selection by `kid` with RSA / EC / `x5c` handling (`:96-161`);
- the algorithm allowlist — RS/ES 256/384/512, everything else refused, which is what rejects the
  `alg: none` and HMAC-confusion attacks (`:183-184`);
- `exp`, `nbf`, `iss` and `aud` validation (`:192-207`);
- lazy `cryptography` import so the dependency is never loaded outside this mode (`:47-66`), and
  the module-level ban on importing auth libraries in `modes.py:17-19`;
- the all-or-nothing return contract: `verify()` raises `OidcVerificationError` and "never returns
  partial claims" (`:165-170`).

Two gaps, **measured, not assumed** — both are why this is a spike and not a one-line selector fix:

1. **No token-endpoint exchange, and no `nonce` check.** `OidcVerifier.verify()` validates a JWT
   you already hold; nothing in the module performs an authorization-code exchange, and `nonce` is
   never read (`:192-208` checks `exp`/`nbf`/`iss`/`aud` and nothing else). A browser
   authorization-code flow needs both — the code→token POST, and the `nonce` echo check that binds
   the ID token to the authorization request. The verifier is the *back half* of the flow.
2. **The JWKS URI is hardcoded, not discovered.** `:71` builds
   `f"{issuer}/.well-known/jwks.json"`. There is no reference to
   `/.well-known/openid-configuration` anywhere in the module. Real IdPs publish their JWKS
   elsewhere (Okta at `/oauth2/v1/keys`, Entra at `/discovery/v2.0/keys`), so against most
   providers the current verifier fetches a 404 and raises. Discovery is required work, not polish.

A third, lower-severity observation: `_find_key` treats a missing `kid` as "any key matches" and
returns the first usable one (`:101-119`). Acceptable for a single-key IdP; worth tightening when
the flow is real.

---

## 2. The provider-plugin boundary

The point of a boundary here is that **OIDC and SAML differ entirely in the front half of the flow
and not at all in what the gateway needs out of it.** Both end with "this IdP asserts the operator
is *subject S*, issued by *issuer I*". Everything after that is shared.

```
                    ┌───────────────────────── provider plugin ─────────────────────────┐
GET /api/auth/       │  SsoProvider.authorize_url(state, nonce, redirect_uri) -> str    │
  sso/start ────────▶│    OidcProvider: authorization endpoint + PKCE challenge         │──▶ IdP
                     │    SamlProvider: AuthnRequest (later, same protocol)             │
                     └──────────────────────────────────────────────────────────────────┘
                     ┌──────────────────────────────────────────────────────────────────┐
GET /api/auth/       │  SsoProvider.exchange(code, state) -> SsoIdentity                │
  sso/callback ─────▶│    OidcProvider: token POST, then the SHIPPED OidcVerifier       │◀── IdP
                     │      (auth/oidc.py:165) + nonce echo check                       │
                     └───────────────────────────────┬──────────────────────────────────┘
                                                     │  SsoIdentity(subject, issuer, claims)
                                                     ▼
                       map subject ──▶ SIGN-IN USERNAME  (auth/credentials.json)
                                       ✗ never the attribution username (§1.3)
                                                     │
                                                     ▼
                       generate_token(sign_in_username, ttl)   token_auth.py:497
                       _set_session_cookie(...)                handlers/auth.py:230
                                                     │
                                                     ▼
                       token_auth_middleware — UNCHANGED       token_auth.py:802
```

**Where it lives.** `src/personalclaw/auth/sso/` — `protocol.py` (the `SsoProvider` protocol and
the frozen `SsoIdentity` dataclass), `oidc_provider.py` (wraps `auth/oidc.py`, adds discovery,
PKCE and the token POST), `registry.py` (name → provider). `auth/oidc.py` itself is not moved:
it is the verification primitive and keeps its current public surface.

**What the plugin may and may not do.** A provider returns a verified `SsoIdentity` and nothing
else. It does not read config, does not touch the credential store, does not mint a session, and
does not decide whether the subject is allowed in. Those four are the gateway's job, kept in one
place, for the same reason §1.1 keeps one validator.

**Routes.** `GET /api/auth/sso/start` and `GET /api/auth/sso/callback`, added to `_BYPASS_EXACT`
beside the existing three (`token_auth.py:363`) — they are how you *get* a session, so they cannot
require one, exactly as `/api/auth/login` cannot (`token_auth.py:373`). `GET`, not `POST`, because
the IdP redirects the browser back; that is what forces `state` + PKCE to carry the CSRF job the
origin check does for `/api/auth/login` (see control **C6**).

**Login-page integration, not a second login page.** `/login` already exists
(`server.py:443`, page at `handlers/auth.py`) and `_deny` already redirects page requests to it
when login is offered (`token_auth.py:1331-1337`). SSO adds a button to that page. It does not add
a page.

---

## 3. Configuration round-trip

Two stores, split on exactly one question: *is it a secret?*

### 3.1 Non-secret settings → `config.json` under `auth.*`

The `auth` section is the right home and already carries the precedent, including the reason
secrets are excluded from it:

> The credential itself is NOT here. The username/hash live in `auth/credentials.json` and the
> TOTP secret in the credential store, because `config.json` is a settings file people read, diff
> and paste into issues.
> — `src/personalclaw/config/safety.py:241-243`

New fields on `AuthConfigSection` (`config/safety.py:233-284`):

| Field | Type | Default |
|---|---|---|
| `sso_enabled` | `bool` | `False` |
| `sso_provider` | `str` | `"oidc"` |
| `sso_issuer` | `str` | `""` |
| `sso_client_id` | `str` | `""` |
| `sso_audience` | `str` | `""` |
| `sso_redirect_uri` | `str` | `""` |

Off by default, for the reason already recorded for `login_enabled`: "Login is **opt-in and off by
default**. That default is load-bearing" (`config/safety.py:236-239`).

The round-trip contract, wired the same five ways the existing `auth.*` fields are:

1. **dataclass + `_meta`** — `config/safety.py:233-284`, alongside `login_enabled:246`,
   `session_ttl:255`, `require_totp:263`, `lockout_threshold:271`, `lockout_window:278`.
2. **`load()`** — the explicit field-by-field `AuthConfigSection(...)` mapping at
   `config/loader.py:4110-4119`. An omission here is a silently dropped setting.
3. **`to_dict()`** — `"auth": asdict(self.auth)` at `config/loader.py:4306`; no per-field work.
4. **Write path** — new rows in the `_EDITABLE_CONFIG` PATCH allowlist beside
   `"auth.login_enabled"` at `dashboard/handlers/core.py:1112-1116`. That allowlist is the only
   typed, bounded, SEL-audited config mutator (`config/edit_spec.py:3-27`); anything else is one
   of the four dialects that module exists to have removed.
5. **Frontend control** — `web/src/pages/settings/AccountPanel.tsx`, in the same
   "Sign in from outside your network" section as the login toggle at `:261`
   (`api.patchConfig('auth.login_enabled', next)` at `:199` is the pattern to follow).

`test_config_roundtrip.py` catches a miss in 1–3.

### 3.2 The client secret → credential store only

The OIDC **client secret** never enters `config.json`. It goes through the credential store, which
is keychain-first and falls back to `~/.personalclaw/.env` at 0600, mirroring into `os.environ` for
the running gateway (`config/credentials.py:383-397 save_credential`, `:400-410 get_credential`).

Key name `PERSONALCLAW_SSO_CLIENT_SECRET`, following the shipped precedent
`TOTP_SECRET_KEY = "PERSONALCLAW_TOTP_SECRET"` (`auth/credentials.py:54`) and its stated reason:
the TOTP secret goes to the credential store "not into a JSON file that the snapshot/export set
might later sweep up" (`auth/credentials.py:17-18`). A client secret has the same property.

It is never returned by an API, never logged, and never included in a status payload — the same
three prohibitions `auth/credentials.py:15-16` states for the password plaintext. Note that
`handlers_system.py:713-714` already echoes `oauth2_issuer` into the auth-status body; the issuer
is not a secret and that is fine, but it is the boundary to be careful about — the client id may
join it, the secret may not.

### 3.3 Selection: from config, not only from env

`AuthConfig`'s per-mode fields already exist — `oauth2_issuer`, `oauth2_client_id`,
`oauth2_audience` (`modes.py:154-156`). The clean break is that `auth.sso_*` **populates those**
rather than a second parallel set being minted. Nothing new is declared on `AuthConfig`; a
`from_config()` (or an `AppConfig`-aware `from_env`) fills them.

One caveat, already flagged in `security.md:44-49` and confirmed here: `from_env()` is called
inside a request path at `dashboard/origin.py:275`, not only at boot. So a selector that raises on
a bad SSO configuration turns a misconfiguration into a 500 rather than a clean boot failure. The
refuse-at-startup-vs-fall-back question is control **C7**.

The interim legibility half — a requested-but-unhonored mode must not be silent — is already
shipped by SL-8 (`modes.py:109-139`, warning at `:174-175`) and is not re-solved here.

---

## 4. Security-control surfaces — every one is owner-escalation

Each row is a control this design touches. Per the plan's own framing (and the pattern set by
WIN-6 / Q9), **an implementation atom may not resolve any of these on its own**; each needs an
owner ruling first. The recommendation column is this note's proposal, not a decision.

| # | Control | Surface | Recommendation | Status |
|---|---|---|---|---|
| **C1** | **Session issuance** | A fourth caller of `generate_token()` (`token_auth.py:497`), minting the same cookie via `_set_session_cookie` (`handlers/auth.py:230`). TTL from `auth.session_ttl`, or from the IdP's token lifetime? | Reuse `auth.session_ttl` (`parse_config_duration`, `handlers/auth.py:215`). An IdP-controlled TTL hands session lifetime to an external party. | 🔴 OWNER-ESCALATION |
| **C2** | **Unauthenticated route surface** | Two new entries in `_BYPASS_EXACT` (`token_auth.py:363`), growing the pre-auth attack surface from 3 paths to 5. | Accept, with the same guards `/api/auth/login` carries: per-IP lockout (`handlers/auth.py:_record_failure`), SEL rows, and a single-use `state`. | 🔴 OWNER-ESCALATION |
| **C3** | **Credential-record shape and the escape hatch** | `has_credentials()` requires `password_hash` (`auth/credentials.py:116-119`), and `_login_offered()` requires it *and* `login_enabled` (`token_auth.py:1300-1318`). An SSO-only install has no password, so `/login` is not offered and `_deny` never redirects there (`:1331`). | Widen `has_credentials()` to "password **or** SSO subject", and keep `?token=` as the escape hatch unconditionally. The lockout-not-lockout reasoning at `token_auth.py:1303-1307` is the invariant to preserve. | 🔴 OWNER-ESCALATION |
| **C4** | **The existing `AuthMode.OAUTH2` middleware branch** | `token_auth.py:1247-1282` is a second *validation* path: no cookie, no CSRF, no IP binding, no SEL, and it never adopts `request["app"]` (`:1110`), so it bypasses the app permission sandbox — the defect class `security.md:51-55` already records for `AUTH_MODE=none`. | **Delete it.** Clean break: browser SSO is an issuer (§1.2), and a machine-to-machine bearer path is a separate product decision with its own atom. Keeping both is two validators. | 🔴 OWNER-ESCALATION |
| **C5** | **RBAC — there is none, and this is where one would leak in** | `auth/credentials.py:4` : "there is deliberately no user table, no roles, and no signup." The only principal scoping in a request is `request["app"]` (`token_auth.py:1110`) feeding `apps/permissions.py`. An IdP hands over `groups` / `roles` / `scope` claims for free. | **Drop every authorization claim at the boundary, explicitly.** `SsoIdentity` carries `subject` and `issuer`; `claims` is retained for audit only and no admission decision reads it. Adopting IdP roles invents the role system the product deliberately lacks. Say so in code, not by omission. | 🔴 OWNER-ESCALATION |
| **C6** | **CSRF / origin on the callback** | `/api/auth/login` is guarded by `check_origin` (`handlers/auth.py:141`, `dashboard/origin.py`). The SSO callback is a cross-site `GET` redirect *from the IdP*, so that check cannot apply unchanged. | Single-use, short-TTL, server-side `state` + PKCE `code_verifier` carries the CSRF job. Do **not** relax `check_origin` for existing routes to accommodate the new one. | 🔴 OWNER-ESCALATION |
| **C7** | **Fail-open vs fail-closed on a bad SSO config** | `from_env()` runs in a request path (`origin.py:275`), so raising is a 500, not a boot failure (`security.md:44-49`). | Validate at boot and refuse to *offer* SSO (leaving `local_token` in force and the `?token=` hatch open) rather than raising from a request. Fails closed on admission, open on availability. | 🔴 OWNER-ESCALATION |
| **C8** | **Outbound network from the gateway** | The verifier fetches JWKS over the network (`oidc.py:82 urllib.request.urlopen`), and the token exchange adds a second outbound POST. Both are new egress from an auth path. | Route through the existing egress policy surface (`security.egress`, `config/loader.py:4095-4103`) rather than raw `urllib`, and keep the timeout explicit as `oidc.py:82` already does. | 🔴 OWNER-ESCALATION |

**One requirement that is not an escalation.** Every new grant and deny emits a SEL row, following
`login_success` / `login_failed` / `login_locked_out` / `login_origin_rejected`
(`handlers/auth.py:141-227`). New operation names: `sso_start`, `sso_success`, `sso_failed`,
`sso_state_rejected`. The existing Tier-S error codes at `handlers/auth.py:49-58` are not
reworded; SSO adds its own (`auth_sso_not_enabled`, `auth_sso_state_invalid`,
`auth_sso_verification_failed`) and each needs a wire-error registry row.

---

## 5. Follow-on atom set

Named here for the PM to register. **This note registers nothing** — Chairman directive #31 forbids
growing the denominator, and registration is the PM's call. Ordering is dependency-real: each atom
is completable start-to-finish once the ones above it are done, and every one of them is fenced
behind at least one §4 escalation, so **none is startable until the corresponding ruling lands.**

| Proposed atom | Scope | Gated on |
|---|---|---|
| **SSO-1** — OIDC discovery + JWKS URI | `oidc.py` reads `{issuer}/.well-known/openid-configuration` and takes `jwks_uri`, `authorization_endpoint`, `token_endpoint` from it, replacing the hardcoded `:71`. Non-cheatable: a fixture IdP publishing a JWKS at a non-default path must verify. | none (pure defect fix, §1.4 gap 2) |
| **SSO-2** — the plugin boundary, no provider | `auth/sso/protocol.py`: `SsoProvider` protocol + frozen `SsoIdentity(subject, issuer, claims)`, plus a registry. No routes, no config, no session. Non-cheatable: a fixture provider satisfies the protocol without importing anything from `dashboard/`. | C5 (what `SsoIdentity` may carry) |
| **SSO-3** — config round-trip, no flow | The six `auth.sso_*` fields through all five wiring points of §3.1, plus `PERSONALCLAW_SSO_CLIENT_SECRET` through `save_credential`. Non-cheatable: `test_config_roundtrip` passes and a test asserts the secret is absent from `to_dict()` output and from the auth-status body. | none |
| **SSO-4** — `OidcProvider`: PKCE + token exchange + nonce | The front half §1.4 gap 1 names, wrapping the shipped verifier unchanged. Non-cheatable: an ID token with a mismatched `nonce` is refused, and a replayed `state` is refused. | C6 |
| **SSO-5** — the two routes as a fourth issuer | `/api/auth/sso/start` + `/api/auth/sso/callback`, `_BYPASS_EXACT` entries, SEL rows, error codes. Mints via the same `generate_token` + `_set_session_cookie`. Non-cheatable: a test asserts the middleware cannot distinguish an SSO-minted session from a link-minted one, and that no new validation path exists. | C1, C2, C6 |
| **SSO-6** — subject → sign-in username | `set_sso_subject()` beside `set_password` in `auth/credentials.py`; `has_credentials()` widened per C3. Non-cheatable: a test asserts `dashboard.username` (`identity.current_username()`) is **unchanged** across a full SSO login, and that `?token=` still works with no password configured. | C3 |
| **SSO-7** — delete the OAUTH2 middleware branch | Remove `token_auth.py:1247-1282` and `AuthMode.OAUTH2` from `UNSELECTABLE_MODES`; update `security.md`'s mode table. Non-cheatable: no second validation path remains; `test_sl8_unhonored_auth_mode_is_named.py` still passes. | C4 |
| **SSO-8** — login page affordance | An SSO button on `/login` when `sso_enabled` and configured. Non-cheatable: with SSO misconfigured the page still renders the password form and the paste-token gate is still reachable. | C7 |
| **SSO-9** — SAML provider | A second `SsoProvider` implementation, proving the boundary. Non-cheatable: no file under `auth/sso/` outside `saml_provider.py` changes. | SSO-2, SSO-5 |

**Not in scope for any of these.** A user table, roles, group mapping, signup, or SCIM
provisioning. `auth/credentials.py:3-6` calls itself "authentication, not multi-tenancy" and names
that its soul guardrail; C5 is where that guardrail is at risk and the recommendation is to hold it.
