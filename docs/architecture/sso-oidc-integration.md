# SSO / OIDC integration — design note

**Status: design note. No code ships with this document.** It maps the auth entry point that
exists today onto a provider-plugin boundary, fixes where SSO configuration lives, enumerates the
security controls it touches, and names the follow-on work. Every claim below cites the code it
was read from by name — `path::symbol`, a function, class, constant or field — and
`tests/test_sso_design_note_citations.py` checks that each name still exists, so a rename or a
deletion reds a test instead of leaving the note pointing somewhere else. (It used to cite line
numbers, and most of them had drifted.) Where the as-built contradicts an assumption, the as-built
wins and the contradiction is stated.

Read [`security.md`](security.md#auth-modes) §"Auth modes" first. The gateway has two auth modes,
`none` and `local_token`; an `oauth2` mode that no configuration could select was deleted along
with its verifier. This note is how SSO would be added without bringing back a mode.

---

## 1. What exists today

### 1.1 Seven doors, one mint, one validator

The gateway has **several ways to obtain a session and exactly one way to validate one.** That
asymmetry is deliberate and is the load-bearing fact of this whole design. Every door mints
through one function, `src/personalclaw/dashboard/token_auth.py::mint_session`, naming itself:

| Door (`issuer`) | Who opens it | Where it mints |
|---|---|---|
| `startup`, `ready` | the link the gateway prints (at a terminal) and opens when it starts, which replaces the unopened one of the start before; the `--json-ready` token a test harness opens | `src/personalclaw/gateway.py::mint_startup_token` |
| `token` | `personalclaw token`, `personalclaw run` and scripts, through `GET /api/token/local` | `src/personalclaw/dashboard/handlers/core.py::api_token_local` |
| `token` | a channel app's "open the dashboard" link, for its owner alone — `personalclaw.sdk.channel.owner_sign_in_token` | `src/personalclaw/dashboard/token_auth.py::owner_sign_in_token` |
| `app` | the one app-scoped token an app's page, its proxy and an agent's call to it share | `src/personalclaw/dashboard/token_auth.py::app_session_token` |
| `login` | `POST /api/auth/login` (password) | `src/personalclaw/dashboard/handlers/auth.py::api_auth_login` |
| `enroll` | a device code typed on the sign-in page | `src/personalclaw/dashboard/handlers/auth.py::api_auth_enroll_complete` |
| `pair` | Settings → Devices → **Pair a device** | `src/personalclaw/dashboard/handlers/devices.py::api_devices_pair_complete` |

The door names are constants beside `src/personalclaw/dashboard/session_store.py::ISSUER_LOGIN`,
and the door decides which limit a session counts against
(`src/personalclaw/dashboard/session_store.py::pool_of`), so Settings → Devices can say how each
device signed in. A browser sign-in — startup link, password, device code, pairing — lasts
`auth.session_ttl` (`src/personalclaw/dashboard/token_auth.py::browser_session_ttl`), and nothing
lasts longer than 90 days (`src/personalclaw/auth/lifetimes.py::MAX_LIFETIME_SECS`): `mint_session`
refuses a longer lifetime rather than shortening it.

Every door produces the identical artifact — a signed payload
`{sub, exp, session_exp, iat, nonce}`, plus `app` on an app-scoped token (built in
`mint_session`) — carried in the `pc_token_{port}` cookie, a `?token=` link or an
`Authorization: Bearer` header, and all of them are validated by one middleware,
`src/personalclaw/dashboard/token_auth.py::token_auth_middleware`, through
`src/personalclaw/dashboard/token_auth.py::validate_token`. The login module's docstring
(`src/personalclaw/dashboard/handlers/auth.py`) states the rule this note obeys:

> **This is one more ISSUER of the existing session token, not a second way to be authorized.**
> […] Downstream, the middleware validates a login-minted session exactly like a link-minted one —
> there is exactly one validation path, which is the point: a second path is a second place for an
> authorization bug to hide.

The cookie a password or device-code sign-in sets comes from
`src/personalclaw/dashboard/handlers/auth.py::_set_session_cookie`, which uses the same name, the
same flags and the same `Secure` resolver
(`src/personalclaw/dashboard/token_auth.py::secure_cookies`) as the cookie the middleware sets for
a `?token=` link; its docstring says why that one resolver is shared rather than re-decided.

The paths exempt from token auth are one explicit set,
`src/personalclaw/dashboard/token_auth.py::_BYPASS_EXACT`, each entry written beside the reason it
opens nothing. Three are the login front door — `/login`, `/api/auth/login` and
`/api/auth/status`. The others that are exempt because they are how a device *gets* a session are
device-code and pairing completion, the pairing page, and the MCP sign-in callback
`/api/mcp/oauth/callback` — the closest precedent for an SSO callback (§2).

### 1.2 The deleted `oauth2` mode — and why it was not the answer

An `AuthMode.OAUTH2` used to exist, with a complete OIDC JWT verifier and a branch of
`src/personalclaw/dashboard/token_auth.py::auth_middleware` that used it, but no configuration
could select it: `src/personalclaw/auth/modes.py::SELECTABLE_MODES` held only `none` and
`local_token`, as it still does. It was deleted rather than wired, because selecting it would have
been the wrong fix. The branch **replaced the entire middleware** with a bearer-JWT validator:

- it read only `Authorization: Bearer` — browsers do not send that on navigations, so it could
  not serve a dashboard SSO login at all;
- it set `request["user"]` from the JWT's `sub` and **never set `request["app"]`**, which
  `src/personalclaw/dashboard/token_auth.py::token_auth_middleware._adopt` does on every path.
  `request["app"]` is what the app permission sandbox
  (`src/personalclaw/dashboard/server.py::app_permission_middleware`) keys on — the identical
  defect class [`security.md`](security.md) §"The `AUTH_MODE=none` sandbox fix" records;
- it had no cookie, no CSRF/origin check, no IP binding
  (`src/personalclaw/dashboard/token_auth.py::bind_token_ip`), and no SEL audit row on grant or
  deny — all of which the `local_token` path has;
- its refusal was a bare `{"error": "Unauthorized"}`, where every refusal of a browser's sign-in
  on the `local_token` path carries a sentence saying why and how to sign in
  (`src/personalclaw/dashboard/token_auth.py::refusal_notice`);
- it removed the `?token=` escape hatch that the login module deliberately preserves ("Login never
  becomes the only way in") so a credential problem cannot brick the box.

In the vocabulary of §1.1 it was a **second validation path**. That is precisely the thing the
login module's rule forbids. Therefore:

> **Design decision.** Browser SSO is one more **ISSUER** of the existing session token — a new
> door — not a second validator and not a mode. The IdP proves who the operator is;
> `mint_session()` remains the only thing that mints a session and `token_auth_middleware`
> remains the only thing that validates one.

The mode switch itself stays what it is today: `src/personalclaw/auth/modes.py::AuthConfig.from_env`
returns `src/personalclaw/auth/modes.py::classify_auth_mode_request`'s `effective` mode, and the
only three runtime construction sites are all `from_env()`:
`src/personalclaw/dashboard/server.py::start_dashboard`,
`src/personalclaw/dashboard/api_server.py::start_api_server` and
`src/personalclaw/dashboard/origin.py::auth_is_off`.

### 1.3 The two identity strings this must respect (issue #960)

Issue #960 reports that Settings → Account carries two fields one word apart. They are two
distinct concepts and both are already shipped:

| Concept | Storage | Code | UI |
|---|---|---|---|
| **Attribution username** ("Username") | `config.json` → `dashboard.username` | `src/personalclaw/identity.py::current_username`, normalized on load by `src/personalclaw/config/loader.py::_slug_username` in `src/personalclaw/config/loader.py::AppConfig.load_with_migration_state`; the field is `src/personalclaw/config/loader.py::DashboardConfig.username` | `web/src/pages/settings/AccountPanel.tsx::saveHandle`, in `web/src/pages/settings/AccountPanel.tsx::AccountPanel` (its hint: "It's a label, not a login") |
| **Sign-in username** ("Sign-in username") | `~/.personalclaw/auth/credentials.json` (0600) | `src/personalclaw/auth/credentials.py::set_password`, `src/personalclaw/auth/credentials.py::verify_password`, `src/personalclaw/auth/credentials.py::has_credentials` | `web/src/pages/settings/AccountPanel.tsx::LoginSection` |

The semantics are already written down and they are not symmetric:

- The attribution username **is not a credential**: "Nothing authenticates against it and nothing
  authorizes on it. It answers 'who wrote this row', not 'who may'" (the module docstring of
  `src/personalclaw/identity.py`). And "a rename affects future writes only. Existing records keep
  the string they were written with — rewriting history to match a new name would silently falsify
  the record it exists to preserve" (the same docstring).
- The sign-in username exists **only as a login subject**, and its own module docstring already
  anticipates this note: a username exists "only so the login form has a subject and so it can
  later graduate into an SSO-provisioned one (TEAM-SHARED-ENTITIES' identity string)"
  (`src/personalclaw/auth/credentials.py`).

That sentence is the hook. SSO does not need a new identity field; it needs to populate the one
that was built to be populated this way.

> **Design decision (the clean break).** An IdP-verified subject lands on the **sign-in username
> side only**. The attribution username is never derived from, defaulted from, or overwritten by
> any IdP claim. There is no third identity string, no `sso_subject` parallel to `username`, and no
> second login path.

Two consequences worth stating because they are easy to get wrong:

- **The session subject is already the sign-in username.**
  `src/personalclaw/dashboard/handlers/auth.py::api_auth_login` mints with
  `mint_session(username.strip() or "owner", …, issuer=ISSUER_LOGIN, …)`. An SSO issuer therefore
  mints with the mapped sign-in username and the token payload's `sub` keeps meaning exactly what
  it means today.
- **The attribution username stays empty until the owner sets it.** `current_username()` returns
  `""` when unset and every write path degrades to "no attribution"
  (`src/personalclaw/identity.py::current_username`). Filling it from an IdP `preferred_username`
  would silently start stamping records with a string the owner never chose, and — per the rename
  rule above — could never be corrected retroactively. Not done.

#960's own suggested fix (rename "Username" → "Attribution handle") is orthogonal to this note and
does not block it: this design reuses the *fields*, whatever their labels end up being. It does
make the rename more valuable, because after SSO the sign-in side may be provisioned rather than
typed, and a label that says "attribution" survives that change while "Username" does not.

### 1.4 What an OIDC provider needs, and what the gateway already has

**The back half: an ID-token verifier.** None ships. The deleted `oauth2` mode's verifier showed
the shape one needs, and the two places it fell short, both measured before it was deleted:

- a JWKS fetch with a short cache and a stale-on-refresh-failure fallback, and signing-key
  selection by `kid` (RSA, EC and `x5c`) that does **not** treat a missing `kid` as "any key";
- an algorithm allowlist — RS/ES 256/384/512, everything else refused, which is what rejects the
  `alg: none` and HMAC-confusion attacks — and `exp`, `nbf`, `iss` and `aud` validation;
- an all-or-nothing contract: it raises, and never returns partial claims;
- **what it lacked:** a `nonce` check, which binds the ID token to the authorization request, and
  discovery. It built the JWKS URI as `{issuer}/.well-known/jwks.json`, which real IdPs do not
  publish there (Okta serves `/oauth2/v1/keys`, Entra `/discovery/v2.0/keys`), so against most
  providers it would have fetched a 404 and refused everyone.

**The front half exists in the gateway already.** A remote MCP server's OAuth sign-in
(`src/personalclaw/mcp_oauth.py`) discovers an authorization server's endpoints through RFC 8414
and OpenID configuration (`src/personalclaw/mcp_oauth.py::_server_metadata_urls`), runs the code
flow with PKCE S256 and a single-use 256-bit `state`
(`src/personalclaw/mcp_oauth.py::start_sign_in`), exchanges the code with the verifier
(`src/personalclaw/mcp_oauth.py::finish_sign_in`), and sends every request through the egress
guard under its own policy (`src/personalclaw/net/policy.py::mcp_sign_in_egress_policy`). It is
not an identity login — it obtains an access token for a resource and verifies no ID token, so the
verifier and the `nonce` check are still new — but it is the precedent SSO-1 and SSO-4 should
build on, not a second implementation of the same flow beside it.

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
  sso/callback ─────▶│    OidcProvider: token POST, then the ID-token verifier          │◀── IdP
                     │      (SSO-1) + nonce echo check                                  │
                     └───────────────────────────────┬──────────────────────────────────┘
                                                     │  SsoIdentity(subject, issuer, claims)
                                                     ▼
                       map subject ──▶ SIGN-IN USERNAME  (auth/credentials.json)
                                       ✗ never the attribution username (§1.3)
                                                     │
                                                     ▼
                       mint_session(sign_in_username, ttl, issuer="sso")   token_auth.py
                       _set_session_cookie(...)                            handlers/auth.py
                                                     │
                                                     ▼
                       token_auth_middleware — UNCHANGED                   token_auth.py
```

**Where it lives.** `src/personalclaw/auth/sso/` — `protocol.py` (the `SsoProvider` protocol and
the frozen `SsoIdentity` dataclass), `oidc_provider.py` (discovery, PKCE, the token POST and the
ID-token verifier of §1.4), `registry.py` (name → provider).

**What the plugin may and may not do.** A provider returns a verified `SsoIdentity` and nothing
else. It does not read config, does not touch the credential store, does not mint a session, and
does not decide whether the subject is allowed in. Those four are the gateway's job, kept in one
place, for the same reason §1.1 keeps one validator.

**A door of its own.** The SSO sign-in mints through `mint_session` naming a new door (`sso`,
beside `session_store.py::ISSUER_LOGIN`), so Settings → Devices says how the browser signed in and
the session counts against the browser limit like a password sign-in does.

**Routes.** `GET /api/auth/sso/start` and `GET /api/auth/sso/callback`, added to
`src/personalclaw/dashboard/token_auth.py::_BYPASS_EXACT` beside the login trio — they are how you
*get* a session, so they cannot require one, exactly as `/api/auth/login` cannot. `GET`, not
`POST`, because the IdP redirects the browser back; that is what forces `state` + PKCE to carry
the CSRF job the origin check does for `/api/auth/login` (see control **C6**). The MCP sign-in
callback (`src/personalclaw/dashboard/handlers/mcp.py::api_mcp_oauth_callback`) is already exactly
this shape: exempt, matched only to a sign-in the owner started by its single-use `state`, within
ten minutes, with the code exchanged only with that sign-in's PKCE verifier.

**Login-page integration, not a second login page.** `/login` already exists (registered in
`src/personalclaw/dashboard/routes.py::register_dashboard_routes`, page
`src/personalclaw/dashboard/handlers/auth.py::login_page`), and
`src/personalclaw/dashboard/token_auth.py::_deny` already redirects page requests to it when login
is offered (`src/personalclaw/dashboard/token_auth.py::_login_offered`). SSO adds a button to that
page. It does not add a page.

---

## 3. Configuration round-trip

Two stores, split on exactly one question: *is it a secret?*

### 3.1 Non-secret settings → `config.json` under `auth.*`

The `auth` section is the right home and already carries the precedent, including the reason
secrets are excluded from it:

> The credential itself is NOT here. The username/hash live in `auth/credentials.json` and the
> TOTP secret in the credential store, because `config.json` is a settings file people read, diff
> and paste into issues.
> — `src/personalclaw/config/safety.py::AuthConfigSection`

New fields on `AuthConfigSection`:

| Field | Type | Default |
|---|---|---|
| `sso_enabled` | `bool` | `False` |
| `sso_provider` | `str` | `"oidc"` |
| `sso_issuer` | `str` | `""` |
| `sso_client_id` | `str` | `""` |
| `sso_audience` | `str` | `""` |
| `sso_redirect_uri` | `str` | `""` |

Off by default, for the reason already recorded for `login_enabled`: "Login is **opt-in and off by
default**. That default is load-bearing" (the `AuthConfigSection` docstring).

The round-trip contract, wired the same five ways the existing `auth.*` fields are:

1. **dataclass + `_meta`** — `AuthConfigSection`, alongside
   `src/personalclaw/config/safety.py::AuthConfigSection.login_enabled`,
   `src/personalclaw/config/safety.py::AuthConfigSection.session_ttl`,
   `src/personalclaw/config/safety.py::AuthConfigSection.require_totp`,
   `src/personalclaw/config/safety.py::AuthConfigSection.lockout_threshold` and
   `src/personalclaw/config/safety.py::AuthConfigSection.lockout_window`.
2. **`load()`** — the explicit field-by-field `AuthConfigSection(...)` mapping in
   `src/personalclaw/config/loader.py::AppConfig.load_with_migration_state`. An omission here is a
   silently dropped setting.
3. **`to_dict()`** — `"auth": asdict(self.auth)` in
   `src/personalclaw/config/loader.py::AppConfig.to_dict`; no per-field work.
4. **Write path** — new rows in the PATCH allowlist
   `src/personalclaw/config/editable.py::_EDITABLE_CONFIG`, beside `"auth.login_enabled"` and
   `"auth.session_ttl"` (which declares the 90-day limit as `max_secs`, so a longer value is
   refused with the sentence that says why). That allowlist is the only typed, bounded,
   SEL-audited config mutator — `src/personalclaw/config/edit_spec.py::coerce_edit_value`, and the
   module docstring of `src/personalclaw/config/edit_spec.py` on the four dialects it exists to
   have removed.
5. **Frontend control** — `web/src/pages/settings/AccountPanel.tsx::LoginSection`, the "Sign in
   from outside your network" section the login toggle lives in
   (`web/src/pages/settings/AccountPanel.tsx::toggleLogin`, which calls
   `api.patchConfig('auth.login_enabled', next)`, is the pattern to follow).

`test_config_roundtrip.py` catches a miss in 1–3.

### 3.2 The client secret → credential store only

The OIDC **client secret** never enters `config.json`. It goes through the credential store, which
is keychain-first and falls back to `~/.personalclaw/.env` at 0600, mirroring into `os.environ` for
the running gateway (`src/personalclaw/config/credentials.py::save_credential`,
`src/personalclaw/config/credentials.py::get_credential`).

Key name `PERSONALCLAW_SSO_CLIENT_SECRET`, following the shipped precedent
`TOTP_SECRET_KEY = "PERSONALCLAW_TOTP_SECRET"`
(`src/personalclaw/auth/credentials.py::TOTP_SECRET_KEY`) and its stated reason: the TOTP secret
goes to the credential store "not into a JSON file that the snapshot/export set might later sweep
up" (the module docstring of `src/personalclaw/auth/credentials.py`). A client secret has the same
property.

It is never logged, never put in argv, never returned by an API, and never included in a status
payload — the prohibitions that docstring states for the password plaintext. The auth-status body
(`src/personalclaw/dashboard/handlers_system.py::api_auth_status`) is the boundary to be careful
about: the issuer and the client id may appear in it, the secret may not.

### 3.3 Selection: from config, not from the auth mode

SSO is not a mode (§1.2), so nothing is added to `src/personalclaw/auth/modes.py::AuthConfig`: the
provider reads `auth.sso_*` the way the login door reads `auth.login_enabled`. (The deleted
`oauth2` mode carried `AuthConfig` fields for an issuer, a client id and an audience that nothing
ever filled; they went with it.)

One caveat, confirmed here: `from_env()` is also called inside a request path —
`src/personalclaw/dashboard/origin.py::auth_is_off`, which the Doctor's remote-reachability probe
(`src/personalclaw/resilience/doctor.py::_probe_remote_reachability`) calls — not only at boot. So
a selector that raises on a bad SSO configuration turns a misconfiguration into a failed request
rather than a clean boot failure. The refuse-at-startup-vs-fall-back question is control **C7**.

The legibility half — a value that is not a mode must not be ignored silently — is shipped by
SL-8 (`classify_auth_mode_request`, and the warning `AuthConfig.from_env` logs) and is not
re-solved here.

---

## 4. Security-control surfaces — every one is owner-escalation

Each row is a control this design touches. Per the plan's own framing (and the pattern set by
WIN-6 / Q9), **an implementation change may not resolve any of these on its own**; each needs an
owner ruling first. The recommendation column is this note's proposal, not a decision.

| # | Control | Surface | Recommendation | Status |
|---|---|---|---|---|
| **C1** | **Session issuance** | One more door into `src/personalclaw/dashboard/token_auth.py::mint_session`, setting the same cookie through `src/personalclaw/dashboard/handlers/auth.py::_set_session_cookie`. TTL from `auth.session_ttl`, or from the IdP's token lifetime? | Reuse `auth.session_ttl` (`src/personalclaw/dashboard/token_auth.py::browser_session_ttl`), which the 90-day limit already bounds. An IdP-controlled TTL hands session lifetime to an external party — and one could ask for longer than the limit. | 🔴 OWNER-ESCALATION |
| **C2** | **Unauthenticated route surface** | Two new entries in `src/personalclaw/dashboard/token_auth.py::_BYPASS_EXACT`, growing the pre-auth attack surface by two paths; every existing entry carries the reason it opens nothing, and these would need the same. | Accept, with the same guards `/api/auth/login` carries: per-IP lockout (`src/personalclaw/dashboard/handlers/auth.py::_record_failure`), SEL rows, and a single-use `state` as the MCP sign-in callback has. | 🔴 OWNER-ESCALATION |
| **C3** | **Credential-record shape and the escape hatch** | `src/personalclaw/auth/credentials.py::has_credentials` requires `password_hash`, and `src/personalclaw/dashboard/token_auth.py::_login_offered` requires it *and* `login_enabled`. An SSO-only install has no password, so `/login` is not offered and `_deny` never redirects there. | Widen `has_credentials()` to "password **or** SSO subject", and keep `?token=` as the escape hatch unconditionally. The lockout-not-lockout reasoning in `_login_offered`'s docstring is the invariant to preserve. | 🔴 OWNER-ESCALATION |
| **C4** | **The `AuthMode.OAUTH2` middleware branch** | It was a second *validation* path: no cookie, no CSRF, no IP binding, no SEL, a bare `Unauthorized` refusal, and it never adopted `request["app"]` (`token_auth_middleware._adopt` does), so it bypassed the app permission sandbox — the defect class `security.md` §"The `AUTH_MODE=none` sandbox fix" records. | Delete it. Browser SSO is an issuer (§1.2), and a machine-to-machine bearer path is a separate product decision with its own change. | ✅ DONE — deleted with the mode and its verifier |
| **C5** | **RBAC — there is none, and this is where one would leak in** | The module docstring of `src/personalclaw/auth/credentials.py`: "there is deliberately no user table, no roles, and no signup." The only principal scoping in a request is `request["app"]` (`token_auth_middleware._adopt`) feeding `src/personalclaw/apps/permissions.py::app_request_denial`. An IdP hands over `groups` / `roles` / `scope` claims for free. | **Drop every authorization claim at the boundary, explicitly.** `SsoIdentity` carries `subject` and `issuer`; `claims` is retained for audit only and no admission decision reads it. Adopting IdP roles invents the role system the product deliberately lacks. Say so in code, not by omission. | 🔴 OWNER-ESCALATION |
| **C6** | **CSRF / origin on the callback** | `/api/auth/login` is guarded by `src/personalclaw/dashboard/origin.py::check_origin` (called first in `api_auth_login`). The SSO callback is a cross-site `GET` redirect *from the IdP*, so that check cannot apply unchanged. | Single-use, short-TTL, server-side `state` + PKCE `code_verifier` carries the CSRF job, as it already does for `/api/mcp/oauth/callback`. Do **not** relax `check_origin` for existing routes to accommodate the new one. | 🔴 OWNER-ESCALATION |
| **C7** | **Fail-open vs fail-closed on a bad SSO config** | `from_env()` runs in a request path (`src/personalclaw/dashboard/origin.py::auth_is_off`), so raising fails a request, not the boot (`security.md` §"Auth modes"). | Validate at boot and refuse to *offer* SSO (leaving `local_token` in force and the `?token=` hatch open) rather than raising from a request. Fails closed on admission, open on availability. | 🔴 OWNER-ESCALATION |
| **C8** | **Outbound network from the gateway** | An OIDC provider fetches the IdP's discovery document and JWKS and posts the token exchange. All three are new egress from an auth path. | Route them through the egress chokepoint (`src/personalclaw/net/client.py::fetch`) under a named policy, as `src/personalclaw/net/policy.py::mcp_sign_in_egress_policy` does for the MCP sign-in, never a raw `urllib`, with explicit timeouts. | 🔴 OWNER-ESCALATION |

**One requirement that is not an escalation.** Every new grant and deny emits a SEL row, following
`login_success` / `login_failed` / `login_locked_out` / `login_origin_rejected`
(`src/personalclaw/dashboard/handlers/auth.py::api_auth_login`). New operation names: `sso_start`,
`sso_success`, `sso_failed`, `sso_state_rejected`. The existing Tier-S error codes beside
`src/personalclaw/dashboard/handlers/auth.py::ERR_INVALID` are not reworded; SSO adds its own
(`auth_sso_not_enabled`, `auth_sso_state_invalid`, `auth_sso_verification_failed`) and each needs a
row in the wire-error registry, `src/personalclaw/http_errors.py::HTTP_ERROR_CODES`.

---

## 5. The work this would take, in order

**Nothing below is scheduled.** This section exists so that a reader can see the size and the
shape of the change rather than guess at it, and so that a contributor who wants to argue for
it has something concrete to argue about. The ordering is dependency-real: each step is
completable start-to-finish once the ones above it are done, and every one is gated behind at
least one §4 owner decision, so **none of it is startable until that decision is made.** To
propose it, open an issue — see [CONTRIBUTING](../../CONTRIBUTING.md#the-model).

| Step | Scope | Gated on |
|---|---|---|
| **SSO-1** — the ID-token verifier, with discovery | A verifier with the §1.4 shape — allowlisted algorithms, `exp`/`nbf`/`iss`/`aud`, `kid` selection that refuses a missing `kid` against a multi-key JWKS, all-or-nothing — that takes `jwks_uri`, `authorization_endpoint` and `token_endpoint` from `{issuer}/.well-known/openid-configuration`, reusing the discovery `mcp_oauth.py` already does rather than writing a second one. Non-cheatable: a fixture IdP publishing its JWKS at a non-default path verifies, and an `alg: none` token is refused. | none |
| **SSO-2** — the plugin boundary, no provider | `auth/sso/protocol.py`: `SsoProvider` protocol + frozen `SsoIdentity(subject, issuer, claims)`, plus a registry. No routes, no config, no session. Non-cheatable: a fixture provider satisfies the protocol without importing anything from `dashboard/`. | C5 (what `SsoIdentity` may carry) |
| **SSO-3** — config round-trip, no flow | The six `auth.sso_*` fields through all five wiring points of §3.1, plus `PERSONALCLAW_SSO_CLIENT_SECRET` through `save_credential`. Non-cheatable: `test_config_roundtrip` passes and a test asserts the secret is absent from `to_dict()` output and from the auth-status body. | none |
| **SSO-4** — `OidcProvider`: PKCE + token exchange + nonce | The front half, around SSO-1's verifier. Non-cheatable: an ID token with a mismatched `nonce` is refused, and a replayed `state` is refused. | SSO-1, C6 |
| **SSO-5** — the two routes as a new door | `/api/auth/sso/start` + `/api/auth/sso/callback`, `_BYPASS_EXACT` entries, SEL rows, error codes, and an `sso` issuer. Mints via the same `mint_session` + `_set_session_cookie`. Non-cheatable: a test asserts the middleware cannot distinguish an SSO-minted session from a link-minted one, and that no new validation path exists. | C1, C2, C6 |
| **SSO-6** — subject → sign-in username | `set_sso_subject()` beside `set_password` in `auth/credentials.py`; `has_credentials()` widened per C3. Non-cheatable: a test asserts `dashboard.username` (`identity.current_username()`) is **unchanged** across a full SSO login, and that `?token=` still works with no password configured. | C3 |
| ~~**SSO-7** — delete the OAUTH2 middleware branch~~ | Done: `AuthMode.OAUTH2`, its middleware branch and its verifier were deleted, and `test_unhonored_auth_mode_is_named.py` requires every `AuthMode` to be selectable. | — |
| **SSO-8** — login page affordance | An SSO button on `/login` when `sso_enabled` and configured. Non-cheatable: with SSO misconfigured the page still renders the password form and the paste-token gate is still reachable. | C7 |
| **SSO-9** — SAML provider | A second `SsoProvider` implementation, proving the boundary. Non-cheatable: no file under `auth/sso/` outside `saml_provider.py` changes. | SSO-2, SSO-5 |

**Not in scope for any of these.** A user table, roles, group mapping, signup, or SCIM
provisioning. The credential module's docstring (`src/personalclaw/auth/credentials.py`) calls
itself "authentication, not multi-tenancy" and names that its soul guardrail; C5 is where that
guardrail is at risk and the recommendation is to hold it.
