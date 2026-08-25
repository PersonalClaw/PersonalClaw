# COMPANION-APPS

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/CA.md`](../atomic/CA.md) as 9 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Companion Apps — Native Clients Over a Local or Remote Gateway

**Status:** PROPOSED — created 2026-07-26 from owner request (companion apps for different platforms
that connect to a PersonalClaw gateway on the local network **or** a remote instance using whatever
auth we land on). Nothing built yet: no `dashboard/handlers/devices.py`, no `DevicesPanel.tsx`, no
`companion/` package, no mDNS dependency, no `companion` config section.
**Its S1 prerequisite is now satisfied (verified 2026-08-04)** — REMOTE-USER-AUTH shipped all four
sessions 2026-07-30, so the durable session store this plan's device sessions ride exists and the LAN
half (S1-S2) is startable; S3/S4's remote path is also unblocked by that plan's S4 (`Secure`/`wss`).
The rev-13 hub veto killed a MECHANISM, not this plan: "no multi-instance hub ever" designates
multi-gateway pairing + switching here as the sanctioned alternative, and the same amendment ADDS
T3.3 and T4.4. Also never formally inserted into the execution order (workspace `ROADMAP.md` §5).

---

## Context (code recon, 2026-07-26)

Verified against code + the two consuming plans — re-verify before editing; a moved cite is E1.

- **The desktop shell already connects to a local gateway** — `desktop/main.js` spawns
  `personalclaw gateway --port auto --json-ready --no-open`, scans stdout for
  `PERSONALCLAW_READY:{port,token,pid,home}` (`gateway.py:3107`), and loads
  `http://localhost:<port>` with `PERSONALCLAW_DEV_NO_AUTH=1` (loopback + auth-off). It has
  **no notion of connecting to a gateway it did not spawn** — no saved-endpoint list, no
  remote URL, no pairing. That "connect to a *different* gateway" capability is this plan's
  desktop contribution (additive; the spawn-local mode is unchanged and stays the default).
- **MOBILE-COMPANION's device-token/QR design is FOLDED IN HERE (`CA-3`, 2026-08-17)** — it
  used to add a `device` claim to `generate_token` plus an `entity_settings/devices.json`
  registry (its C1) and to define `POST /api/devices/pair/start|complete` itself (its C4).
  Both are now **references** to this plan: **this plan unifies that pairing with
  REMOTE-USER-AUTH's enrollment code so there is ONE pairing path** (a device session is a
  `sessions.json` row, C1 of REMOTE-USER-AUTH, with `device` set). The fold was not a plain
  deletion: the two constraints that plan carried and this one did not — the 30-day TTL and
  the `bind_ip` roaming-phone caveat — moved INTO C1 below, and its `caller="device:<name>"`
  audit attribution moved into the SEL line. This plan owns "device session + pairing";
  MOBILE-COMPANION owns the phone UI + push.
- **REMOTE-USER-AUTH provides the auth foundation** — its C1 durable `sessions.json` (device
  rows survive restart — the reason companion sessions can be long-lived), C3 `/api/auth/
  enroll/*` (the remote, no-password device-enrollment path), and C4 `session_ttl`. This plan
  **consumes** those; it does not touch `token_auth.py` minting (E4 if a task seems to need to).
- **No local-network discovery exists** — grep confirms no mDNS/Bonjour/zeroconf/SSDP anywhere;
  `.local_secret` is loopback-only. Discovery is greenfield here (a small, optional advertiser).
- **Origin/CSRF + WS** — `check_origin` (`origin.py:319`) already trusts loopback + the
  configured `dashboard.url`/`public_url` origin and allows `PERSONALCLAW_CORS_ORIGINS`; a
  native client connecting to a remote gateway rides the REMOTE-USER-AUTH `public_url` boundary
  (its S4) — this plan adds **no new origin exemption**. WS `/api/ws` auth is the session
  cookie/`?token=`; a native client presents its device session **via the cookie** — see C1's
  transport constraint, because the `?token=` path is the one that IP-binds.
- **Auth-mode reality (do not fight it):** only `none` (loopback-forced) and `local_token` are
  reachable (`auth/modes.py:49`; `server.py:1446` wires `token_auth_middleware` directly). A
  companion connecting to a LAN gateway needs that gateway reachable beyond loopback — which
  today means `PERSONALCLAW_BIND_HOST` + `local_token`, or a tunnel for remote. This plan's
  discovery + pairing assume `local_token`; it never asks the operator to run auth-off
  off-loopback (that is refused/loopback-forced by construction, and this plan warns on it).

## Design

Four capabilities, smallest-surface-first. S1–S2 deliver LAN use with no internet/account;
S3–S4 add remote + the native wrappers (which are the *existing* per-platform plans wearing
this contract).

- **S1 — The connectivity contract + Devices registry (gateway side).** Define the client
  connection model once: a saved **endpoint** = `{label, base_url, kind: "local"|"remote",
  device_session_ref}`. Gateway side, a thin **Devices** surface (Settings → Devices) lists
  paired device sessions (name, kind, last-seen, revoke) — reading REMOTE-USER-AUTH's
  `sessions.json` device rows. Pairing endpoints unify MOBILE-COMPANION's C4 + REMOTE-USER-AUTH
  C3 into one: `POST /api/devices/pair/start` (LAN, session-auth) → `{code}`; `POST
  /api/devices/pair/complete {code, device_name}` → a durable device session. This is the whole
  new backend footprint.
- **S2 — Local-network discovery (opt-in advertiser + client resolver).** An **optional**
  mDNS/DNS-SD advertiser (`_personalclaw._tcp`, advertising `{instance_name, port,
  requires_pairing}`) behind `companion.discovery_enabled` (default **off** — advertising your
  gateway's presence is a choice). A companion client resolves it to a `base_url` and begins
  pairing. Fully degradable: if discovery is off/unavailable, the user types the LAN URL by
  hand (or scans a QR the dashboard shows). No discovery payload ever contains a token or
  content — just enough to locate + start pairing.
- **S3 — Endpoint switching + reconnection (client-side contract).** The client behavior the
  wrappers share: hold a list of saved endpoints (one local, one remote is the common case),
  switch between them, and reconnect gracefully (the remote tunnel is flaky by nature) —
  degraded-connection UI reuses the platform-resilience degraded contract, not a new one. On a
  remote endpoint the client authenticates with its device session over TLS (`wss://`) per
  REMOTE-USER-AUTH S4; on a local endpoint, over the LAN. **Same served SPA either way** —
  switching endpoints reloads the same UI from a different origin.
- **S4 — Native wrappers per platform (the existing plans, wearing this contract).** No new
  wrapper is built *here*; instead: (a) **Desktop** (DESKTOP-CAPABILITIES) gains a "Connect to
  a gateway" mode — add a remote/LAN endpoint beside the spawn-local default, using S3;
  (b) **Mobile** (MOBILE-COMPANION) Capacitor shell + PWA consume S1–S3 for pairing + endpoint
  switching instead of its own device-token/QR mechanism; (c) any **future platform** (tablet,
  watch, TV, a second desktop OS) becomes "wrap the served UI + implement the S3 client
  contract," gated on PLATFORM-REACH for that OS. This session is coordination + the desktop
  connect-mode; the mobile wrapper stays in MOBILE-COMPANION.

## Contracts & Interfaces (conventions per [AGENTS.md](../../../AGENTS.md))

### C1 — Endpoint + device-session model (owned here; consumed by all wrappers)
```jsonc
// A companion client's saved endpoint (client-side storage, per platform):
{ "label": "Home laptop", "base_url": "http://claw.local:10000",
  "kind": "local" | "remote", "device_session_ref": "<nonce>" }
```
A **device session** is a REMOTE-USER-AUTH C1 `sessions.json` row with `device` set and
`issuer: "enroll" | "pair"` — this plan does NOT define a new token type. Long-lived TTL from
`auth.session_ttl` — whose default IS `30d` (`config/loader.py:4677`), which is exactly the
number MOBILE-COMPANION C1 asked for, so folding its TTL in costs nothing and adds no field.
Revocable from the Devices surface (revoke = flip `revoked` on the row). The fields the Devices
surface reads off the row: device name, `kind`, `issuer`, minted-at, last-seen-at (MOBILE-
COMPANION's `devices.json` shape, minus the file — the row already holds all five).

**Transport constraint (folded in from MOBILE-COMPANION C1's `bind_ip` finding — the one thing
that plan carried and this one did not).** A device session MUST be carried as the session
**cookie**, never through the `?token=` query-param exchange. Measured in
`src/personalclaw/dashboard/token_auth.py`, not assumed: the query-param path binds the token to
the first client IP it sees (`bind_token_ip`, `:582`, called at `:1055`) and denies on mismatch
(`check_token_ip`, `:587`, enforced at `:1041` behind a `not from_cookie` guard), while
cookie-borne requests skip the IP check entirely — "the cookie itself is the credential, and IP
validation behind a proxy is unreliable" (`:1038-1040`). A roaming phone changes IP between cell
and Wi-Fi, so a query-param
device session would die on every network change; a cookie-borne one is untouched. **This
resolves MOBILE-COMPANION's open design pivot with no `token_auth.py` change at all** — no
`device` claim, no unbinding, no weakened invariant, so no E4. It is what `MC-2`'s "a
roaming-IP phone keeps its device session valid per the plan-54 contract" refers to.
Consequence for T1.1: `pair/complete` sets the session cookie on the response; a native shell
(Capacitor/Electron WebView) holds it like any browser.

### C2 — Unified pairing (SUPERSEDES MOBILE-COMPANION C4; folds in REMOTE-USER-AUTH C3 enroll)
| Route | Auth | Purpose |
|---|---|---|
| `POST /api/devices/pair/start` | session (LAN or logged-in) | → `{pairing_url, code, expires_in}`; single-use, TTL 300s, SEL `device_pair_started` |
| `POST /api/devices/pair/complete` | none (exempt) | `{code, device_name}` → durable device session (C1); SEL `device_paired`; reuse rejected |
| `GET /api/devices` | session | list device sessions (name, kind, last_seen, issuer) |
| `POST /api/devices/{id}/revoke` | session | revoke a device session; SEL `device_revoked` |
QR pairing (dashboard shows a QR of `{pairing_url, code}`) is a **rendering of these routes**,
not a separate mechanism — MOBILE-COMPANION's QR screen scans it; the desktop connect dialog
can paste the code. Two shapes folded in from MOBILE-COMPANION C4 so its screen has nothing
left to invent: (a) `pairing_url` comes back from `pair/start` and is resolved **by the
gateway**, never composed in the browser — the dashboard may be open on loopback while the
scanning phone needs the LAN address, and a browser-composed URL would hand the phone
`127.0.0.1` (this is the same origin surface as the CA-5 rough edge in the Execution log);
(b) `device_name` on `pair/complete` is **optional** — omitted, the gateway derives a label
from the User-Agent/hostname, which is what C4's `{code}`-only body assumed. Error codes
(Tier-S): `device_pair_code_invalid`, `device_pair_expired`.

### C3 — Local-network discovery (`companion/discovery.py`, new, optional)
mDNS/DNS-SD service `_personalclaw._tcp.local`, TXT `{name, port, requires_pairing: "1",
schema: "1"}` — **never a token, never content**. Behind `companion.discovery_enabled`
(default off). Client resolver returns candidate `{name, base_url}` list. Degrades to
manual-URL entry when unavailable. Advertiser binds only when the gateway is already bound
beyond loopback (advertising a loopback-only gateway is pointless — no-op + log).

### C4 — Config (5-point §2.1 — new `companion` section)
`companion.discovery_enabled: bool=False`, `companion.instance_name: str=""` (defaults to the
machine hostname; the name shown in discovery + on device lists). `_meta` on each; wired
through `load()`/`to_dict()`/`_EDITABLE_CONFIG` (both are PATCH-editable) + a FE control in the
Devices panel. No secrets here (device sessions live in REMOTE-USER-AUTH's store).

### Integration points
- **Calls:** REMOTE-USER-AUTH's session store (C1) + enrollment path (C3), `atomic_write`,
  `config_dir()`, `sel()`, `check_origin` (existing — no new exemption).
- **Called by:** the MOBILE-COMPANION PWA + Capacitor wrapper (pairing + endpoint switch), the
  DESKTOP-CAPABILITIES Electron shell (connect-to-gateway mode), any future native client.
- **Depends on:** REMOTE-USER-AUTH S1 (durable store) + S4 (remote/TLS boundary) for the remote
  path; nothing for the LAN path beyond `local_token` reachable off-loopback.
- **Storage:** `companion` config section; device sessions are REMOTE-USER-AUTH rows (no new
  store); discovery holds no state.
- **SEL (§2.3):** `device_pair_started`, `device_paired`, `device_revoked`, `discovery_enabled`.
  Folded in from MOBILE-COMPANION C1: requests authenticated by a device session attribute to
  `caller="device:<name>"` in `log_api_access`, so the audit trail names the phone rather than a
  bare user id (that plan's `device_token_mint|revoke` operations are covered by the three
  `device_*` events above — one vocabulary, not two).

## Task breakdown (executor-ready — run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

**Change class B** (new durable pairing state, reusing REMOTE-USER-AUTH's store) — clean break
under the pre-1.0 banner. Sequenced strictly after REMOTE-USER-AUTH S1.

### Session 1 — Connectivity contract + Devices registry

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | Unified pairing routes (C2): `pair/start`, `pair/complete`, `GET /api/devices`, `revoke` — device sessions are REMOTE-USER-AUTH C1 rows with `device`/`issuer` set (no new token type); SEL on each | `dashboard/handlers/devices.py` (new), `server.py` wiring | pair start→complete yields a durable device session surviving a restart; reuse rejected; revoke kills it next request |
| T1.2 | Settings → Devices panel: list device sessions (name, kind, minted, last-seen, issuer) + revoke; "Pair a device" shows a QR of `{pairing_url, code}`. This is the ONLY Devices list — MOBILE-COMPANION T2.4 links here rather than building a second one | `web/src/pages/settings/DevicesPanel.tsx` | a device pairs from the QR end-to-end; revoke observed live |
| T1.3 | Reconcile MOBILE-COMPANION C1/C4: mark them consuming this contract (this plan owns "device session + pairing") | `docs/roadmap/plans/MOBILE-COMPANION.md` | the two plans reference one pairing mechanism; no duplicate device-token design remains |
| V1 | Validation: pair a second browser as a "device" over the LAN, see it in Devices, revoke it, confirm lockout | — | recorded |

### Session 2 — Local-network discovery

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | `companion` config section (C4) wired through all 5 points; `discovery_enabled` default off | `config/loader.py`, `handlers/core.py`, FE | `test_config_roundtrip` green; PATCH toggles discovery |
| T2.2 | Optional mDNS advertiser (C3): advertise `_personalclaw._tcp` with a token-free TXT record only when bound beyond loopback + `discovery_enabled`; clean start/stop with the gateway | `companion/discovery.py` (new) | a resolver on the LAN finds the instance by name; loopback-only gateway → no-op + log; TXT carries no secret (asserted) |
| T2.3 | Client resolver helper + graceful "no discovery → type the URL" fallback documented for wrappers | `companion/discovery.py`, `docs/guides/companion-apps.md` (new) | resolver returns candidates on a LAN fixture; fallback path documented |
| V2 | Validation: enable discovery, find the gateway from another device on the LAN, pair; disable discovery, confirm manual-URL still works | — | recorded |

### Session 3 — Endpoint switching + reconnection (client contract)

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | Define + document the shared client contract (C1 endpoints, switch, reconnect using the platform-resilience degraded contract — not a new one) so both wrappers implement it identically | `docs/guides/companion-apps.md`, a small shared TS helper if one fits in `web/src/lib/` | the contract is precise enough that desktop + mobile implement it without re-deciding; degraded UI reuses the existing contract |
| T3.2 | Remote-endpoint auth path: a native client presents its device session over `wss://` per REMOTE-USER-AUTH S4; verify origin/CSP allow it without a new exemption | client helper, docs | a native client reaches a remote gateway over the owner's tunnel using its device session (no new origin exemption added) |
| V3 | Validation: from one client, switch between a saved LAN endpoint and a remote endpoint; kill the remote tunnel mid-session and confirm graceful reconnect/degraded UI | — | recorded |

### Session 4 — Native wrappers wear the contract (coordination + desktop connect-mode)

| ID | Task | Files | Done when |
|---|---|---|---|
| T4.1 | **Desktop** connect-to-gateway mode: beside the spawn-local default, add a saved LAN/remote endpoint using S3; the spawn-local path is unchanged | `desktop/main.js` (+ a connect dialog), coordinated with DESKTOP-CAPABILITIES | the desktop app can connect to a gateway it did not spawn, local or remote, and stays on spawn-local by default |
| T4.2 | **Mobile** coordination: MOBILE-COMPANION's Capacitor/PWA pairing + endpoint switch consume C2/C1/S3 (its plan's tasks reference this contract) — no work duplicated here | `docs/roadmap/plans/MOBILE-COMPANION.md` task refs | MOBILE-COMPANION S4 pairing points at C2; no parallel device-token code |
| T4.3 | **Future-platform note:** document "wrap the served UI + implement the S3 client contract, gated on PLATFORM-REACH" as the recipe for any new platform | `docs/guides/companion-apps.md` | the recipe is written; no speculative per-platform code shipped |
| V4 | Validation: desktop app connects to the LAN gateway AND a remote gateway; a phone (via MOBILE-COMPANION) pairs + switches endpoints against the same contract | — | recorded |

## Success Criteria (adversarial / observable)

1. **One pairing mechanism:** device sessions are REMOTE-USER-AUTH `sessions.json` rows; MOBILE-COMPANION and the desktop connect-mode both use `pair/start`+`pair/complete` — grep shows no second device-token design surviving.
2. **LAN needs no internet/account:** with discovery on and login off, a second device on the LAN finds the gateway, pairs with a code, and drives the UI — no account, no internet.
3. **Remote rides the auth plan:** a native client reaches a remote gateway using its device session over `wss://` with **no new origin exemption** and **no cloud middle tier** in the path (verify the connection is client→owner-gateway only).
4. **No forked UI / no second API:** every companion renders the same served SPA; the only new backend routes are pairing + Devices + discovery config — asserted against the route table.
5. **Degradable discovery:** discovery off/unavailable never blocks use — manual URL + QR always work; the discovery TXT record contains no token or content (asserted).
6. **Revocation:** revoking a device session from the Devices panel locks that device out on its next request, across a restart.

## Owner tasks (real world)

1. **Confirm the supersession:** MOBILE-COMPANION's own device-token/QR design (C1/C4) is folded into this plan's unified pairing — approve that reconciliation (it removes duplication; the phone UI + push stay in MOBILE-COMPANION). **Executed by `CA-3` (2026-08-17); two folded shapes want a yes/no** — (a) `pair/start` now returns `pairing_url` (gateway-resolved) beside `{code, expires_in}`, and (b) `pair/complete`'s `device_name` is optional with a gateway-derived fallback. Both come from MOBILE-COMPANION C4 / `MC-8`; neither adds a route or a store. Reject either and only C2's response shape moves.
2. **Decide discovery default** — ships **off** (advertising your gateway is opt-in); confirm.
3. **Validation (V4):** connect the desktop app to both a LAN and a remote gateway, and pair a phone — on your own network + tunnel.
4. Per-platform store/enrollment costs (Apple/Google) are owned by MOBILE-COMPANION/DESKTOP-CAPABILITIES, not incurred here.

## Risks & open questions

| Risk | Mitigation |
|---|---|
| Duplicate device-token machinery vs MOBILE-COMPANION | This plan owns pairing + the device session; MOBILE-COMPANION consumes it (T1.3 reconciles, §5 updated). One mechanism by construction (Success Criterion 1) |
| Discovery leaks that a gateway exists / a token | Discovery is opt-in + off by default, advertises only a name/port (no token, no content), and only when already bound beyond loopback |
| A companion asks the operator to run auth-off off-loopback | Refused by construction (`AUTH_MODE=none` is loopback-forced); the plan assumes `local_token` for LAN and warns against unsafe binds |
| Remote path built before REMOTE-USER-AUTH's TLS boundary | S3/S4 hard-gated on REMOTE-USER-AUTH S4 (`public_url` + `Secure`/`wss`); the LAN path (S1–S2) needs only S1 |
| Scope creep into a full native app | Guardrail: no new product surface — same served SPA; wrappers live in the existing per-platform plans, gated on PLATFORM-REACH |
| **Open:** whether the shared client contract needs a tiny published TS module vs prose | T3.1 decides based on how much desktop + mobile actually share; default to prose + a minimal helper, not a framework |

## Amendment (2026-07-26 — gap analysis round 2, owner decisions)

**The sanctioned multi-instance story (owner decision — and a hub veto).** Honest recon: this is mostly a SHARPENING of scope the plan already carries, not an addition. S3/C1 already define a client-side list of saved endpoints ("one local, one remote is the common case") with switching + graceful reconnection, and T4.1 already gives desktop a connect-to-a-different-gateway mode. What the plan does NOT yet say — and this amendment pins down — is that N endpoints may be N **different gateways** (work brain / personal brain), that this is the ONLY sanctioned multi-instance mechanism, and what the pairing registry must therefore hold.

Owner rulings:

- **Multi-gateway pairing + switching is the story.** A native shell (desktop per DESKTOP-CAPABILITIES, mobile per MOBILE-COMPANION) holds N paired gateways, each its own C1 endpoint entry with its own `device_session_ref` (device sessions live per-gateway in THAT gateway's `sessions.json` — nothing federates), plus a **switcher** UI affordance the shells share.
- **No hub in core, ever. No gateway-to-gateway anything.** Gateways never discover, sync with, or proxy for each other; no shared identity, no cross-gateway search, no aggregated inbox in core or in the shells. A future "hub" could only ever be a third-party app running against gateways the user pairs it with — explicitly out of every first-party plan's scope.
- **One small contract addition (C1 sharpened, not replaced):** the client-side pairing registry becomes `{active: str, endpoints: [{id, label, base_url, kind, device_session_ref}]}` — a multi-entry list + an **active-gateway pointer**. Switching re-points `active` and reloads the same served SPA from the new origin (S3 semantics unchanged); every cache/WS/store in the shell is namespaced by endpoint `id` so two brains never bleed state. Per-gateway labels/instance names come from `companion.instance_name` (C4 — already designed).

### Session placement

No new session. S3 T3.1's client contract is WHERE the multi-entry registry + active pointer are specified (it was going to specify the endpoint list anyway); S4 T4.1/T4.2 gain the switcher acceptance bar. One added task row in S3 for the isolation guarantee.

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.3 | Multi-gateway registry in the client contract: `{active, endpoints[]}` shape, per-endpoint state namespacing (caches/WS/prefs keyed by endpoint id), switcher behavior spec (re-point + reload), and the written no-hub/no-gateway-to-gateway rule | `docs/guides/companion-apps.md` (T3.1's doc), the shared TS helper if T3.1 ships one | two paired gateways switchable from one client with zero state bleed (verify: distinct sessions/inbox/settings render per endpoint); the doc states the hub veto verbatim |
| T4.4 | Switcher acceptance on both wrappers: desktop connect dialog + mobile shell each list N gateways, show the active one, and switch cleanly (graceful reconnect per S3 on the target) | `desktop/main.js` connect dialog, MOBILE-COMPANION task refs | V4 extended: pair a "work" and a "personal" dev gateway, switch between them on desktop and on the phone; revoking one gateway's device session breaks only that entry |

## Execution log

- [2026-08-16][CA-5] DONE: S2 discovery — stdlib mDNS/DNS-SD advertiser + client resolver
  (`companion/discovery.py`), `GET /api/companion/discovery`, live-apply on the `companion.*`
  PATCH path, `personalclaw discover`, a Settings status row showing the broadcast record
  verbatim, and `docs/guides/companion-apps.md`. **No dependency added** — `SO_REUSEPORT`
  coexists with the host responder on 5353, so the reason `zeroconf` looked necessary did not
  hold. Verified against Apple's `mDNSResponder` (`dns-sd -B`/`-L` found it by name on the real
  LAN interface) and end to end on a live gateway: PATCH flipped advertising with no restart,
  `discover` printed the instance, and an `auth enroll` code redeemed at the discovered
  `base_url` returned a 30-day device session.
- [2026-08-16][CA-5] DEVIATION: the done_when's "QR pairing" clause is vacuous — no QR surface
  exists yet (it is a rendering of the pairing routes T1.1/T1.2 own). Degradability is asserted
  against the path that does exist (typed URL + `auth enroll`) and structurally, by proving
  `auth/enrollment.py` cannot import `companion`.
- [2026-08-17][CA-3] DONE: S1 supersession reconciliation — MOBILE-COMPANION's C1/C4 device-token
  + QR design is folded into this plan. Doc-only; **no code touched** (see the DISCOVERY below —
  there was nothing to touch). This plan now states the mechanism once (C1 device session + C2
  unified pairing); MOBILE-COMPANION §C1/§C4 **reference** it and their restated route shapes,
  `device` claim and `devices.json` registry are deleted rather than archived. Where the two
  designs disagreed, the constraint moved INTO the owner instead of being dropped:
  · **TTL** — MOBILE-COMPANION's hardcoded "30d" vs this plan's `auth.session_ttl`: measured
    identical, `auth.session_ttl` defaults to `"30d"` (`config/loader.py:4677`), so C1 now names
    the default and the fold costs nothing.
  · **Roaming IP** (the one constraint this plan did not carry) — folded into C1 as a transport
    constraint. MOBILE-COMPANION left it an open "design pivot" (mint unbound, or
    rebind-on-mismatch, E4 if it weakens an invariant); it is measured, so neither branch is
    needed: IP binding applies **only** to the `?token=` exchange (`bind_token_ip`,
    `token_auth.py:970`; `check_token_ip`, `:957`) and cookie-borne requests skip the check
    outright (`:954`). A cookie-borne device session roams for free with **zero**
    `token_auth.py` change. This is what `MC-2`'s "roaming-IP phone keeps its device session
    valid per the plan-54 contract" was pointing at — that reference previously dangled, because
    this plan's contract said nothing about IP binding.
  · **Audit attribution** — MOBILE-COMPANION's `caller="device:<name>"` in `log_api_access` had
    no counterpart here; folded into the SEL line. Its `device_token_mint|revoke` operation names
    are dropped in favour of the existing `device_*` events (one vocabulary).
  · **`minted` field** — MOBILE-COMPANION T2.4 listed it, T1.2 did not; added to T1.2 + C1.
  Two shapes are owner-review flagged (Owner task 1) because they change C2's response, not just
  prose: `pair/start` now returns a **gateway-resolved** `pairing_url` (the browser cannot
  compose it — the dashboard may be on loopback while the scanning phone needs the LAN address,
  the same origin surface as the CA-5 DISCOVERY below), and `pair/complete`'s `device_name` is
  optional with a derived fallback (what C4's `{code}`-only body assumed). Also normalised the
  spelling `pair_url` → `pairing_url`, matching MOBILE-COMPANION C4 and `MC-8`'s done_when (2 of
  3 prior uses). One clarifying edit outside the two plans: REMOTE-USER-AUTH's S1 bullet said
  "MOBILE-COMPANION device tokens", a stale attribution now that plan 54 owns them.
- [2026-08-17][CA-3] Success Criterion 1 evidence — `grep -rniE 'device[ _-]?token'
  docs/roadmap/plans/ src/ web/src` went **17 → 10** hits. All 10 survivors are the one
  mechanism talking about itself: explicit negations ("there is no separate device-token type",
  "instead of its own device-token/QR mechanism", "no parallel device-token code"), this plan's
  own supersession/risk/owner rows, Criterion 1's own sentence, and REMOTE-USER-AUTH resolving
  "device tokens are C1 rows with `device` set". Zero survivors specify a rival route, store,
  claim, TTL or revocation path. The design's fingerprints are gone rather than reworded:
  `device: str` (the claim) 1 → 0, `pair_url` 2 → 0; the 3 remaining `devices.json` /
  `entity_settings/devices` hits are all in sentences saying it does not exist.
- [2026-08-17][CA-3] DISCOVERY (no code change made, needs its own atom if acted on): the rival
  design was **never built**, so the fold has no code consequence — `grep -rniE
  'device[ _-]?token' src/ web/src` = 0 hits, no `devices.json` anywhere in `src/`, and
  `generate_token` is still `(user_id, ttl_seconds=3600, *, app="")` with no `device` claim
  (`token_auth.py:394`). Separately, and worth an atom of its own: `bind_token_ip`
  (`token_auth.py:510`) has **no non-test caller other than the middleware itself** — the only
  production call pair is `token_auth.py:957`/`:970` inside `token_auth_middleware`, so IP
  binding is reachable only on the query-param exchange. That is exactly what makes C1's
  cookie constraint free today, and exactly what would silently break if a future change bound
  cookie sessions too. Not this atom's scope (this atom's done_when is about the plans).
- [2026-08-16][CA-5] DISCOVERY (not fixed): redeeming a pairing code from a browser reached **by
  IP** is refused — `POST /api/auth/enroll/complete` with `Origin: http://<lan-ip>:<port>` → 403
  `CSRF check failed: request origin not allowed` (`build_allowed_origins` has the loopback names
  and the bare hostname, no LAN address). Reads from that origin pass. Left to T1.1 /
  REMOTE-USER-AUTH because C2 requires the pairing path work "with no new origin exemption";
  recorded as a known rough edge in the guide.
- [2026-08-18][CA-1] DONE: S1 backend — C2's four routes (`dashboard/handlers/devices.py`, new,
  wired next to the auth routes in `server.py`) over a widened REMOTE-USER-AUTH session row. New
  `auth/pairing.py` mints the code (8 chars, Crockford-ish, TTL 300s, ≤5 outstanding, SHA-256 at
  rest, constant-time compare, fail-closed, single-use consumed BEFORE the mint). `pair/complete`
  mints through the ordinary `generate_token` and then ANNOTATES the row it just wrote — **no new
  token type**, no `device` claim, no `token_auth.generate_token` change. All four done_when
  clauses were driven against a real gateway on an isolated `/private/tmp` home with **two real
  process restarts** (pids 89345 → 90256 → 90909), not asserted in-process:
  · the row is `{"exp":…, "issuer":"pair", "device":{id,name,kind,minted_at}}`, file mode 0600 —
    written from a body carrying ONLY the code, so the derived-name path is what was exercised
    (an iPhone User-Agent produced `name:"iPhone", kind:"mobile"`);
  · the device's unchanged cookie returned 200 from the NEW process after restart #1;
  · reuse → 401 `device_pair_code_invalid`; an aged record → 401 `device_pair_expired`;
  · after revoke the device's next request was 403 `token superseded` — live, and again after
    restart #2, while the OWNER's cookie survived that same restart (the positive control that
    rules out "the restart broke everything").
  Eight SEL events landed in `security_events.jsonl` across the drive, including every denial
  (`device_paired/denied` for invalid and for expired; a separate drive also confirmed
  `device_revoked/denied` for an unknown id); `caller_identity` is `owner` on the three
  session-auth routes and the client IP on the exempt one, and neither the code nor any live
  nonce appears anywhere in them. Real home untouched (110496 files before and after, 0
  modified in the window; probe controls 1 and 0).
- [2026-08-18][CA-1] DEVIATION: **revoke DELETES the row rather than flipping a `revoked` flag**
  on it (C1 says "flip `revoked`"). Measured reason: nothing reads such a flag —
  `TokenStateManager.is_nonce_valid` authorizes on *presence + expiry* of the nonce
  (`token_auth.py:129`), so a `revoked: true` row would keep authenticating and the flag would
  be an inert control that told the owner the device was locked out while it kept working. A
  reader would have to be added inside `is_nonce_valid`, which is the middleware hot path.
  Deleting the row revokes through the check that already exists. Cost of the deviation: no
  tombstone, so a revoked device disappears from the list instead of showing as revoked — if
  CA-2 wants a "Revoked" row, that is a `revoked` flag PLUS its reader, one atom, not a field.
- [2026-08-18][CA-1] DEVIATION: **`last_seen` is NOT shipped**, though C1/C2 and T1.2 list it.
  The only place a device is actually observed is where its request is authorized — the token
  middleware — so an honest `last_seen` is a throttled write on the request path (a per-request
  read-modify-write of `sessions.json` otherwise), which is a performance decision outside this
  atom's fence, not a field to declare. Set once at pairing it would read as fresh forever, and
  the owner would use it to decide a device is still in use. Shipped absent instead of wrong.
  **CA-2's done_when names a last-seen column, so CA-2 is short one field until that writer
  exists** — recipe: throttle on `record.last_seen`, write from `is_nonce_valid`'s success path.
- [2026-08-18][CA-1] DEVIATION: the session row's **shape is a clean break** — a row went from a
  bare `float` to `{"exp", "issuer", "device"}`, and an old-shape row is **discarded, not
  upgraded** (`_parse_record` returns `None`, one place, logged once with a count). Not laziness
  about a three-line branch: a row with no `issuer` is a live session the registry can neither
  describe nor revoke, which is the audit gap the record exists to close, so admitting one ships
  a device list that is silently incomplete. Cost is one `personalclaw token` re-mint, which is
  the pre-S1 behavior and inside the pre-1.0 banner. `load_sessions()` keeps its
  `{nonce: exp}` signature as the *projection* of the one shape, so `token_auth`'s two call
  sites are untouched; `save_sessions` is gone, replaced by `save_session_records`.
- [2026-08-18][CA-1] DEVIATION (fence): two small additions to `token_auth.py`, which CA-1's
  fence did not include. Both are unavoidable rather than convenient —
  `_BYPASS_EXACT.add("/api/devices/pair/complete")` (without it the route 401s forever, i.e.
  ships inert), and a public `revoke_nonce(nonce)` wrapper, because the registry holds a device
  id and never sees the device's token, so `revoke_token(token)` cannot serve it. The
  alternative was reaching into `token_auth._state` from a handler. `generate_token` itself is
  byte-identical.
- [2026-08-18][CA-1] DISCOVERY (not fixed, blocks CA-2): the CA-5 rough edge above is **still
  open and now applies to `pair/complete`**. `build_allowed_origins` carries the loopback names
  and the bare hostname, never a LAN address, so a phone that scans the QR and posts from
  `Origin: http://<lan-ip>:<port>` gets 403 before the code is ever read. C2 forbids a new
  origin exemption, so this is not fixable inside CA-1 by adding one — it needs
  `build_allowed_origins` to learn the bound LAN address (REMOTE-USER-AUTH's surface).
  **CA-2's "pairs from the shown QR end-to-end over the LAN" cannot pass until it is done.**
  Loopback and same-origin pairing work today, which is what CA-1's own done_when covers.
- [2026-08-18][CA-1] DISCOVERY (not fixed, wants its own atom — this is the sharpest edge CA-1
  found): **five further token mints silently unpair a device.** `MAX_CONCURRENT_NONCES = 5`
  (`token_auth.py:239`) and `register_nonce` evicts the oldest nonce past the cap
  (`:119`), and `generate_token` keeps the durable store in step by calling
  `forget_session(evicted)` (`:417`). Measured, not reasoned: pair a device, mint five more
  tokens, and `device_sessions()` goes 1 → 0 while the device's own token returns
  `token superseded`. The device disappears from the Devices list with no revoke and no event
  the owner would recognise — it looks exactly like the revoke path succeeding by itself. This
  was harmless while every session died at restart; a durable device session makes it
  user-visible. The fix is a policy call inside `register_nonce` (exempt rows with `device`
  set from eviction, or size the cap to devices + browsers) and is outside CA-1's fence, since
  it changes what eviction means for every session, not just paired ones.
- [2026-08-18][CA-1] DISCOVERY (not fixed): a paired device holds an ORDINARY session, so it has
  the owner's full authority — the drive confirmed a paired device can call `GET /api/devices`
  and could revoke its siblings. That follows directly from C1's "no new token type" and is not
  a bug in this atom, but it is the property a reader will assume away: pairing a phone is
  handing it the whole dashboard, not a scoped client. Scoping needs a capability on the row
  plus a middleware reader, i.e. its own atom (CA-7's remote-endpoint work is the natural home).
- [2026-08-18][CA-1] DISCOVERY (not fixed): `issuer` has exactly two producers — `"pair"` (this
  atom) and `"unknown"` (every ordinary mint). C1 specifies `"enroll" | "pair"`, but
  `/api/auth/enroll/complete` still mints through the bare `generate_token`, so an enrolled
  device lands as `unknown` and is invisible to the registry. Deliberately not fixed here:
  adding an `ISSUER_ENROLL` constant with no writer is an enum member nobody writes. The fix is
  one keyword argument at `handlers/auth.py:448`, plus deciding whether an enrolled device is a
  device row at all (it has no name and no kind to show).
- [2026-08-18][CA-1] Contract note: three C2 names were followed over the atom row's shorthand —
  `pairing_url` (not `pair_url`; CA-3 normalised it), `device_name` on the request body (optional,
  gateway-derived from the User-Agent when omitted), and the error code `device_pair_expired`
  (not `device_pair_code_expired`). The asymmetry with `device_pair_code_invalid` is C2's; CA-2
  maps these strings to copy, so it was matched rather than tidied. `minted_at` is the row's
  mint timestamp (C1's "minted-at", T1.2's "minted").
- [2026-08-19][CA-2] DONE: S1 frontend — Settings → Devices (`web/src/pages/settings/DevicesPanel.tsx`),
  the product's ONE device registry, plus the `last_seen` writer C1 deliberately deferred. The list
  carries name, kind, last-seen, issuer, paired-at and session expiry; revoke asks a DANGER dialog
  that NAMES the device and re-reads the list (a failed revoke is reported, never swallowed); pairing
  shows the `code` and `pairing_url` copyably with the expiry counting down. Registered in `SUBPAGES`,
  the axe manifest (`web/e2e/routes.ts`) and `SETTINGS_WIDGETS` — the Settings home renders only
  widgets with no fallback, so a card is what makes the panel reachable rather than URL-only.
- [2026-08-19][CA-2] DONE: `last_seen` shipped with its writer, honouring C1's constraint verbatim.
  `DeviceInfo.last_seen` round-trips through `to_dict`/`_parse_device`; `touch_device_last_seen()`
  is called from `TokenStateManager.is_nonce_valid` on BOTH success paths (in-memory hit and
  adopted-from-store — skipping the latter would make every device read "never seen" for the first
  request after a restart). Two throttle layers for two costs: an in-memory attempt map suppresses
  the file READ, and the store's own staleness check against `LAST_SEEN_THROTTLE_SECS = 60.0`
  suppresses the WRITE. 60s is a cost decision — the only reader renders relative minutes, so a
  minute of slack is below perceptible resolution while bounding the rate at one atomic rewrite per
  device per minute. The whole touch is wrapped so no failure can reach the auth decision, and a
  non-device session is a no-op. Falsified in six ways (both throttle layers, the best-effort guard,
  the parser backfill, the panel's "never" branch, the response field) — each mutation reds a named
  test.
- [2026-08-19][CA-2] DEVIATION: **"shows a QR" is UNMET for the IMAGE specifically.** There is no QR
  encoder in either ecosystem (`npm ls | grep qr` → nothing; python `qrcode`/`segno` absent), and
  adding one is a dependency decision, not this atom's call. The plan itself calls QR "a RENDERING of
  these routes, not a separate mechanism", and the repo's precedent is that TOTP enrollment ships no
  QR either (`AccountPanel` sends the owner to `personalclaw auth totp setup`). So the panel ships the
  `pairing_url` + `code` — which is what a second browser on the LAN actually needs, and the URL
  already contains the code — behind a LABELLED placeholder (`role="img"`, named "QR code not
  available — use the pairing link and code"). A silent omission would have read as a broken image;
  this reads as an absent one, and pairing still completes. This closes CA-5's vacuity DEVIATION above:
  the pairing surface now exists, minus the scannable rendering.
- [2026-08-19][CA-2] DISCOVERY (fixed here): `ui/Button` takes `ariaLabel`, NOT the hyphenated
  `aria-label`, and does not spread rest props — but TypeScript permits any hyphenated attribute on a
  JSX element WITHOUT type checking, so `aria-label="…"` on a `<Button>` typechecks cleanly and is
  silently DISCARDED. Five controls in the first draft of this panel shipped nameless and typecheck
  passed; only the RTL name queries caught it. Anything auditing accessible names by reading source
  will report these as named.
- [2026-08-19][CA-2] DISCOVERY (not fixed): FOUR settings panels are unreachable from the Settings
  home — `companion`, `ambient`, `sources`, `packs` have `SUBPAGES` entries but no `SETTINGS_WIDGETS`
  card, and `SettingsHome` renders ONLY the widget registry with no fallback list. They are reachable
  only by typing `#/settings/<id>`. Out of scope here (this atom added its own card so it is not a
  fifth), but it is a real discoverability gap, and no ratchet catches it — `settingsSubpageCoverage`
  compares `SUBPAGES` to the axe manifest, not to the widget registry.
- [2026-08-19][CA-2] DEVIATION: two ratchets fired on the new surface and were ROOT-CAUSED rather than
  relaxed. `emptyStateRollout`'s PEP-2 census demands a verdict per empty-state file — Devices is
  recorded `on-ramp` ("Pair your first device", the same `startPairing()` the section above calls),
  deliberately worded differently from that section's button because two controls sharing one
  accessible name make the action ambiguous to name-based navigation. `healthUnknown`'s "one per card"
  assertion counted `Couldn't check` FILE-WIDE and was pinned at 2, so a third card adopting the same
  correct copy read as a regression while saying nothing about the two cards it names; it is now
  scoped per card by brace-matching the `doctor` and `guardrails` entries — strictly stronger, since
  either named card dropping the state now fails BY NAME.

### 2026-08-19 — `CA-2` built and gated; **BLOCKED on an owner scope decision**, atom stays `todo`

The Devices panel, its four api-client methods and the `last_seen` writer `CA-1`'s note asked for are
built, tested and gated. Then the live drive found that the atom's own `done_when` cannot be reached,
for a reason outside this atom's surface.

**What was driven.** A token-authenticated gateway on an isolated home, bound to `0.0.0.0` via
`PERSONALCLAW_BIND_HOST` (the loopback invariant means `AUTH_MODE=none` would have forced 127.0.0.1
and disabled the very auth pairing exists to cross), with `dashboard.url` declared as the LAN address
so the LAN origin passes CSRF — `gateway.py:3374` passes `cfg.dashboard.url`, and
`dashboard.public_url` is a **different** field that does not widen the CSRF set.

- `POST /api/devices/pair/start` over the LAN, as the owner → `{"code": "9WWG-UEXG", "pairing_url":
  "http://192.168.86.33:10025/pair?code=9WWG-UEXG", "expires_in": 300}`. The mint path works.
- Opening that `pairing_url` in a **genuinely separate browser context** (own cookie jar, no token) —
  i.e. the situation a phone is in — renders **`403 — Token required`**, with the instruction
  *"Run `personalclaw token` in your terminal, then paste the URL below."*

**Two defects, and the second is the blocking one.**

1. **`/pair` is not in the auth bypass.** `_BYPASS_EXACT` (`token_auth.py:305-340`) exempts `/login`
   *the page* alongside `/api/auth/login`, and exempts `/api/auth/enroll/complete` and
   `/api/devices/pair/complete` "for the same reason … the device redeeming a code has no session
   yet — that is the point". The **page** a pairing device must open was never added, so the API is
   reachable and its entry point is not. The 403 body then tells the joining device to run a command
   on the host's terminal, which is advice only the owner's own machine can follow.
2. **There is no pairing screen at all.** No `/pair` route exists server-side and the SPA has no
   `pair` view: with a valid token `/pair?code=…` returns **200 with the ordinary SPA shell** (16,016
   bytes — the same as `/nonexistent-xyz`, i.e. the catch-all), and the SPA's hash router lands on the
   dashboard. So the URL `pair/start` hands out is a dead end in **both** states — 403 without a
   token, the wrong screen with one. Nothing in the repo redeems a pairing code from a browser.

**Why this is an owner decision rather than something to route around.** Closing `done_when` requires
building the joining device's redeem screen and exempting its route from token auth. That is (a) a
different surface from "Settings → Devices panel", which is what this atom and its task row `T1.2`
describe, and (b) **a new unauthenticated HTTP route**, which is a security-boundary change that
should be decided deliberately, not slipped into a panel atom. The existing exemptions each carry
compensating guards named in code (origin check, per-IP lockout, single-use hashed short-TTL code); a
`/pair` page exemption would need the same treatment and its own review.

Two coherent resolutions, for the owner to choose:

- **(a) Re-scope `CA-2`** to the owner-side panel (what T1.2 actually describes) and file the redeem
  screen + route exemption as its own atom, which is where `MOBILE-COMPANION`'s folded-in device-side
  design (`CA-3`) naturally lands. `CA-2`'s `done_when` then loses its "second browser pairs" clause.
- **(b) Grow `CA-2`** to include the redeem screen and the exemption, and re-drive.

Until then `CA-2` stays `todo` with the panel merged: it lists, revokes and mints pairing codes
correctly, and the mint's URL is honest about where it points — there is simply nothing serving it yet.

**Also unmet, separately (and not blocking):** "shows a QR" is unmet for the *image*. No QR encoder
exists in either ecosystem and none was added; the panel ships the `pairing_url` and `code` copyably
behind a labelled placeholder. The plan itself calls QR "a **rendering of these routes**, not a
separate mechanism", and TOTP enrollment sets the no-bundled-QR precedent (`AccountPanel` points at
`personalclaw auth totp setup`). Adding a QR dependency is a separate owner call.

**Gates:** `make lint` exit 0 · `test_device_pairing` + `test_session_store` + `test_token_auth`
**196 passed** (was 185) · web typecheck 0, full web suite **424 files / 4386 tests**, build 0. Six
falsifications, each restored from a file copy; I independently re-ran the `last_seen`-backfill one
(`assert device.last_seen == 0.0` reds when an absent stamp falls back to `minted_at`).

- [2026-08-21][CA-2] DONE: the owner scope decision above is taken as **(b) — grow `CA-2`** and the
  `done_when` is now MET end-to-end. The missing half was never the panel: it was that
  `pair/start`'s URL pointed at nothing. `GET /pair` now exists (`handlers/devices.py`, registered in
  `register_device_routes` beside the API routes it is the entry point for, and added to
  `token_auth._BYPASS_EXACT`), served as a standalone document exactly like `/login` — an SPA route
  cannot work here because every authenticated bundle fetch the SPA makes on boot 403s before a
  field renders. **Driven over the LAN, not asserted**: gateway pid 78163 on port 10041 bound
  `*:10041` via `PERSONALCLAW_BIND_HOST=0.0.0.0`, home self-reported as
  `/private/tmp/ca2-wt/.dev-home` in its own `PERSONALCLAW_READY` line (a foreign gateway was live
  on 10698 against the real home throughout, so the home was asserted from the process rather than
  assumed). Two independent Playwright browser contexts, cookie jars measured empty/`pc_token_10041`
  before pairing and holding **different** values after:
  · owner panel at `http://192.168.86.33:10041/#/settings/devices` minted
    `2XFF-H7HC` → `http://192.168.86.33:10041/pair?code=2XFF-H7HC` — the LAN address, not loopback;
  · the second context (iPhone UA, no session) opened that URL, got **"Pair this device"** with the
    code **pre-filled and equal to the panel's**, typed a name, and landed on `/#/dashboard`;
  · the device's own `GET /api/devices` returned **200**;
  · the owner's list showed **`Keyur iPhone` · Phone · Last seen just now · Paired with a code`**,
    i.e. all four columns, over `{"name":"Keyur iPhone","kind":"mobile","last_seen":1787345636.933632,
    "minted_at":1787345636.91012,"issuer":"pair"}` — `last_seen` and `minted_at` differ by 23ms, so
    the column is the throttled writer's value and NOT a backfill;
  · revoke → the device's next request **403 `token superseded`, live, no restart**, and its reload
    rendered the token gate, while the OWNER's cookie returned 200 through the same revoke (the
    positive control that rules out "the revoke broke everything").
  Also observed live: `/pair` with the owner's cookie **302s to `/`** while `/pair` with no cookie is
  200 (so the redirect is not unconditional), a reused code returns
  `{"error":{"code":"device_pair_code_invalid"}}`, and `GET /` with no token is **403** — the bypass
  is scoped to the page, not a blanket disable. Six SEL events landed
  (`device_pair_started`×4 ok, `device_paired` granted, `device_revoked` ok) and **neither live nonce
  nor the pairing code appears in `security_events.jsonl`** in either form, so CA-1's assertion is
  not regressed. Real home untouched: 110509 files before and after, and its `sessions.json` /
  `security_events.jsonl` mtimes predate the drive window while the dev home's are inside it.
- [2026-08-21][CA-2] DEVIATION: **exempting a PAGE from token auth is a security-boundary change, and
  it is bounded deliberately.** The prior session was right to stop here rather than slip it into a
  panel atom; taken as an owner decision, the compensating design is that the exemption buys
  *reachability only*. The document is a CONSTANT — the code is read from `location.search` in the
  browser and never interpolated server-side — so there is no injection surface and no secret on the
  page, and every grant still happens at `/api/devices/pair/complete` behind its own origin check,
  per-IP lockout and single-use hashed short-TTL code. Falsified: interpolating `?code=` into the
  served bytes reds `test_the_redeem_page_never_interpolates_the_query_string`.
- [2026-08-21][CA-2] DEVIATION: a browser that already holds a valid session is **redirected home**
  instead of being offered the form, which gives `handlers/auth.has_valid_session` its **first
  reader** — it had ZERO call sites before this. Not tidiness: redeeming a code in the owner's own
  browser overwrites its `pc_token_{port}` cookie, so the laptop silently becomes a `device` row
  while its previous session row stays behind unreachable, and with `MAX_CONCURRENT_NONCES = 5` a
  self-pair also spends an eviction slot (CA-1's eviction discovery makes that expensive). Falsified
  by replacing the call with `if False:` — `test_a_browser_that_already_has_a_session_is_sent_home`
  reds; observed live as a 302 with the owner cookie and 200 without.
- [2026-08-21][CA-2] DEVIATION: the two standalone pages now share ONE token block
  (`handlers/page_shell.py`: `PAGE_STYLE`, `LOGO_MARK`, `page_document`). `/login` and `/pair` are the
  only surfaces in the product that cannot inherit `web/`'s design system, and neither has a
  visual-regression test, so a second hand-written copy of ~55 lines of tokens would drift silently.
  The extraction is byte-verified: the recomposed `_LOGIN_HTML` is character-identical to the
  pre-change literal (8121 bytes both sides), so the login page did not change shape to gain a
  housemate. `test_the_two_standalone_pages_share_one_token_block` pins it with a vacuity floor.
- [2026-08-21][CA-2] DISCOVERY (fixed here): **both inline scripts read the error envelope in the
  wrong shape.** `json_error` emits `{"error": {"code", "message"}}` (PL-8), and the login page read
  `res.data.error` as a bare string — so every `MESSAGES[code]` lookup missed and the page could only
  ever say "Sign-in failed." / "Pairing failed.", never "Wrong username or password", "Too many
  attempts" or "Password sign-in is not enabled". Worse, the branch that reveals the 2FA field on an
  `auth_totp_required` response could never fire. PL-8 standardized the emitter and passed over two
  raw children that parse it by hand, which no type checker reads. Both reads now take `error.code`,
  and `test_the_page_reads_the_error_envelope_the_route_actually_emits` asserts it against a REAL
  `json_error` body rather than a string, so an envelope reshape reds rather than silently re-breaking
  the copy.
- [2026-08-21][CA-2] Decision, restated deliberately: **revoke stays DELETION, no `revoked`
  tombstone.** CA-1's reasoning holds and the drive confirms its consequence is the one the clause
  asks for — `is_nonce_valid` authorizes on presence + expiry, so deletion revokes through the check
  that already exists and the device's next request was refused live. A `revoked: true` flag with no
  reader inside `is_nonce_valid` would tell the owner a device is locked out while it kept
  authenticating, which is the inert-control shape this repo has shipped repeatedly. The cost is
  unchanged and still accepted: a revoked device disappears rather than showing as "Revoked". That is
  a `revoked` flag PLUS a middleware reader — its own atom, and it must not be taken as a field.
  Falsified in the direction that matters: leaving the row in place reds
  `test_clause_4_revoke_locks_the_device_out_across_a_restart` **on its live assertion**
  (`assert token_auth.validate_token(...)[0] is False`, "the in-memory half bit, with no restart
  involved"), not merely on the list being empty.
- [2026-08-21][CA-2] DEVIATION (carried, unchanged): **"shows a QR" is still UNMET for the IMAGE.**
  Re-measured, not assumed: `qrcode`, `segno` and `pyqrcode` are all absent from the Python
  environment and nothing in either `package.json` provides an encoder. Adding one is a dependency
  decision, and hand-rolling Reed-Solomon + masking to render a *wrong* QR would be worse than the
  labelled placeholder. The clause's substance is met by what the QR would have encoded: the drive's
  second browser paired from the `pairing_url` the panel shows, which already contains the code.
- [2026-08-21][CA-2] DISCOVERY (not fixed): **LAN pairing requires `dashboard.url` to name the LAN
  address**, and that is the only reason CA-1's "blocks CA-2" note needed no code change.
  `build_allowed_origins` takes a `dashboard_url` argument and adds its origin as-is, so setting
  `dashboard.url = http://<lan-ip>:<port>` puts the LAN origin in the CSRF set — no new exemption,
  which is what C2 forbade. Unset, `pair/complete` 403s `device_pair_origin_rejected`; the redeem page
  now maps that code to a sentence naming the fix rather than failing mutely. Making the gateway
  learn its own bound LAN address automatically would widen CSRF for every install that binds
  `0.0.0.0` — REMOTE-USER-AUTH's call, not this atom's.
- [2026-08-21][CA-2] DISCOVERY (not fixed): the login page's device-code form posts to
  `/api/auth/enroll/complete`, and `login_page` **redirects to `/` when `login_enabled` is false** —
  so on a default install that form is unreachable and there was no door at all for a pairing device.
  `/pair` is deliberately independent of `login_enabled` for exactly that reason. Whether the two
  code paths should converge on one redeem screen is CA-3's question, not this atom's.

**Gates (2026-08-21):** `make lint` exit 0 · targeted `pytest` over `test_device_pairing`,
`test_session_store`, `test_token_auth`, `test_auth_login`, `test_auth_exposure` · full `make test` ·
`python -m personalclaw.manifest_reference` regenerated with `PYTHONPATH` pointed at THIS worktree
(the venv is an editable install of the main checkout) and produced **no diff** — `/pair` is a UI page,
so it belongs in `MANIFEST_EXCLUDE`, not the API manifest. **No `web/` source changed**: the panel
merged complete in this atom's first half; what was missing was the server-side half. Seven
falsifications, each mutation re-read to confirm it landed and each restored from a file copy — the
manifest exclusion reds in BOTH directions (dropping it → "Non-/api routes registered but not in
MANIFEST_EXCLUDE: ['/pair']"; a bogus key → "MANIFEST_EXCLUDE lists paths that no longer register").

**ARCC was not queried (MCP server unavailable).** The `last_seen` writer is the only auth-path change
here: best-effort, throttled at 60 s, wrapped so a store failure cannot deny a valid session, and a
no-op for sessions with no device. No new route, exemption or credential surface ships in this atom.
- [2026-08-24][CA-2] DONE (ledger only): flipped `CA-2` to `done` in `dag.json` + `atomic/CA.md`, cited to
  **#1855** (the batch that landed the redeem screen; its member PR #1854 closed as superseded, and the
  panel half had landed earlier in #1752). No code change — the 2026-08-21 entry above already drove every
  `done_when` clause over the LAN. This entry exists because the implementing PRs shipped without the
  status flip, so the atom read `todo` with its deliverable fully on `main`: `GET /pair` in
  `handlers/devices.py` registered by `register_device_routes`, `/pair` in `token_auth._BYPASS_EXACT`,
  `DevicesPanel.tsx` + `devicesPanel.test.tsx`, and the `/api/devices` routes. Verified by code on
  `origin/main`, not by the branch subject — a subject match is a screen, not a verdict.

---

## Execution log — CA-6 (S3 shared client contract + multi-gateway registry)

- **CA-6 DONE.** `docs/guides/companion-apps.md` gains `## The shared client contract` (+199 lines) and
  `web/src/lib/endpoints.ts` (333) + `endpoints.test.ts` (431, 33 tests) ship the registry the shells
  import, so desktop (`CA-8`/T4.1) and mobile implement one contract instead of two.
- **MEASURED — the namespacing obligation is the SHELL's, not the SPA's, and this reshaped the atom.**
  A literal reading of T3.3's "caches/WS/prefs keyed by endpoint id" is a refactor of every web-storage
  site in the dashboard (**47 non-test files** under `web/src`, e.g. the `cache:` sessionStorage mirror at
  `web/src/lib/data/store.ts:50`). That would re-invent what the platform already gives:
  `desktop/main.js:768` does `wc.loadURL(backendUrl)` — the shell loads the SPA **from the gateway's own
  origin** — `:143` shows `backendUrl` is single-valued from that gateway's READY line, `grep -n partition
  desktop/main.js` returns nothing (default partition), `web/src/lib/api.ts` is root-relative with **no
  `base_url`/`API_BASE` concept at all**, and the socket is origin-relative
  (`web/src/lib/useChatSocket.ts:32`). So switching gateways IS loading another origin, and browser origin
  isolation already partitions every SPA store. The served SPA also cannot hold the registry — it is
  re-downloaded from whichever gateway is active. What spans all N gateways is the **shell's own single
  storage scope**, and that is the only place two brains can bleed. The doc says so with those anchors,
  because a reader who assumes otherwise either re-namespaces the SPA pointlessly or forgets the shell.
- **The key encoding is length-prefixed, and that is the substantive call.**
  `ep:${id.length}:${id}:${logicalKey}` rather than `id + ':' + key`, because the obvious form is **not
  injective**: `{id:'a', key:'b:c'}` and `{id:'a:b', key:'c'}` land in one slot — the exact bleed the module
  exists to prevent, hidden inside the prevention mechanism. An encoding *property* was chosen over a
  charset *rule* since the registry cannot police hand-edited or older-shell input; `parseEndpointKey` is
  the inverse and is what lets `clearEndpointState` sweep by owner. The endpoint `id` is opaque and minted
  at pair time, deliberately NOT the `base_url`: two rows can share a host and a URL changes (network move,
  port reassign, Tailscale rename) without the gateway becoming a different brain, so id-as-URL would
  silently orphan that endpoint's namespaced state on every such change. Unknown `kind` coerces to
  `remote`, the less privileged value. Parsing is total — corrupt JSON, a missing or dangling `active`,
  duplicate ids and a throwing `getItem` all resolve rather than raise, because a shell that dies on a bad
  registry cannot reach the switcher that would fix it.
- **Hub veto quoted verbatim**, machine-compared against this plan's amendment line after unwrapping the
  blockquote: identical word for word, only soft-wrapped to the guide's width.
- **CORRECTED — this plan's own C1 citations were stale and are fixed in the same PR.** The transport
  paragraph cited `bind_token_ip` at `:970`, `check_token_ip` at `:957` and the cookie comment at `:954`,
  and omitted the `dashboard/` path segment; those lines hold unrelated code. Measured:
  `src/personalclaw/dashboard/token_auth.py:582` and `:587`, called at `:1055`, enforced at `:1041` behind
  a `not from_cookie` guard, comment at `:1038-1040`. A contract doc whose upstream anchors do not resolve
  is the failure mode this atom exists to prevent, so the drift was fixed rather than inherited.
- **Gate:** web typecheck clean (`tsc --noEmit` and `tsconfig.sw.json`), 33/33 on `endpoints.test.ts`, the
  FULL web suite **475 files / 5013 tests**, `npm run build` (524 assets, sw.js emitted), and 20 passed on
  the two docs rails (`test_docs_lint_baseline.py`, `test_getting_started_walkthrough.py`). The full web
  suite was run rather than a guessed subset because ~120 suites under `web/src` scan the tree
  (`readdirSync`/`import.meta.glob`/`globSync`), not just the ~20 in `web/src/design/` — a subset would
  have left unrun legs. Falsifications: dropping the id from the key → **11 red** (zero-bleed first);
  naive `id + ':' + key` → **10 red**, failing on the collision itself
  (`expected 'ep:a:b:c' not to be 'ep:a:b:c'`), the second reproduced independently before pushing. Each
  mutation was restored from a file copy, never `git checkout`.
- **Rail honesty:** the docs-drift ratchet's green is not vacuous here — `companion-apps.md` has no
  baseline entry and `regressions()` scores an absent file as 0, so an injected dead link and a bad
  `.py` citation were both confirmed to red it before the real run.
- **DISCOVERY (not acted on) — `CA-7`'s and `CA-8`'s acceptance needs two live gateways.** This atom
  specifies the switch; the "two paired gateways switchable with zero state bleed" *observation* over real
  sessions/inbox/settings is T4.4's bar and lands with the desktop connect dialog, which is where a second
  endpoint can actually be paired. Nothing in the shells reads the registry yet — `desktop/main.js` still
  holds one `backendUrl` — so the helper is a contract with its first consumer still to come.

---

## Execution log — CA-7 (S3 remote-endpoint auth path over `wss`)

- **CA-7 PARTIAL — the atom stays `todo`.** The native-client half is built, tested and gated; the
  two `done_when` clauses that need two live gateways over a real tunnel are not observed here, for
  the same reason `CA-6`'s log already recorded against `CA-7`/`CA-8`.
- **T3.2's verb is *verify*, and the verification FAILED — that is the finding.** The row says
  "verify origin/CSP allow it without a new exemption", i.e. it expected the existing machinery to
  already admit a native client. Measured against `origin.py`/`ws.py` before writing anything, it
  did not, for **two independent reasons**:
  1. **A native client sends no `Origin` at all** (it has no document), and `_check_ws_origin` →
     `check_origin(require=True)` answered *False* for any non-loopback peer → `403 WebSocket
     origin not allowed`. The old docstring stated the intent out loud: *"including missing Origin
     (non-browser clients are not expected)"*.
  2. **A WebView over the tunnel is refused too**, and this one is a shipped user-visible defect
     rather than a missing feature. `dashboard.public_url` adds `wss://<host>` to the CSP
     (`server.py:_ws_csp_sources`, measured returning `' wss://pc.example.com
     https://pc.example.com'`) but **nothing** adds `https://<host>` to `allowed_origins`:
     `build_allowed_origins` takes `dashboard_url` — `cfg.dashboard.url`, passed at
     `gateway.py:3494` — and `public_url` is a different field. So the browser is *permitted* to
     open the socket and the gateway then refuses the upgrade, and every state-changing POST 403s
     via `csrf_middleware`. `remote-access.md` never mentions `dashboard.url`, and its own
     troubleshooting entry blamed a `public_url` mismatch for exactly this symptom.
- **Only (1) was fixed here; (2) was left to REMOTE-USER-AUTH, deliberately.** Closing (2) means
  widening `build_allowed_origins`, and `CA-2`'s log already ruled that widening the allowlist "is
  that plan's call, not this atom's" on the strength of the same "no new origin exemption" clause.
  Both halves are pinned by tests so neither reads as accidental, and both guides now document the
  workaround (set `dashboard.url` to the same public URL — measured to work: the origin appears in
  the set, behind the existing `_is_token_auth` invariant at `server.py:1977-1993`).
- **The mechanism, and why it is not an origin exemption.** `ws.py:_check_ws_origin` keeps the
  allowlist as the rule for anything presenting an `Origin` — that path is byte-identical — and
  admits an `Origin`-**less** upgrade only when the authorizing session carries a paired `device`
  row. `build_allowed_origins` is untouched, so the allowed set is byte-identical and a paired
  device gets **no** help forging an origin it does not have. `token_auth.py` publishes
  `request["session_nonce"]` (new `token_nonce()`, documented as valid-token-only since the
  middleware has just validated the same string) — that writer is the live call site, and without
  it the predicate is inert.
- **MEASURED — the refusal being relaxed protected nothing, and this is the load-bearing
  argument.** A non-browser caller that wants past `check_origin` today simply sends `Origin:
  http://localhost:10000`, which is in the set unconditionally. Probed on the same client: honest
  (no `Origin`) → **403**; lying (`Origin: http://localhost:10000`) → **101**. The rule therefore
  only ever constrained clients that *cannot* choose their headers — the honest ones — so keying
  admission on a paired device session (revocable per-device from Settings → Devices, `last_seen`
  stamped, revoked rows stop authenticating upstream) is strictly stronger than the header it
  replaces. Pinned by `test_the_refusal_it_replaces_was_bypassable_by_forging_an_origin`.
- **FAIL-CLOSED, asserted not assumed.** `_paired_device_session` answers `""` for an absent nonce,
  a non-string nonce, an unknown nonce, a non-device row and an unreadable registry; `token_nonce`
  answers `""` for anything undecodable. Each is a test, and the two guards were falsified: making
  the non-device branch return a device id → **3 red** (including the ordinary-session and
  unknown-nonce fail-closed legs); removing the `Origin`-absence precondition → **2 red** (the
  disallowed-origin and public-origin legs). Mutating the TS scheme map (`https:` → `ws:`) → **6
  red**. Every mutation was grepped back to confirm it applied and restored from a file copy.
- **No config field was added**, so there is no round-trip surface: the admission is keyed on
  existing *state* (a `sessions.json` device row), not a new knob. Pairing a device IS the owner's
  opt-in, which is why a `companion.allow_native_remote`-style flag would have been a knob nobody
  sets. `config/loader.py` is untouched by this atom.
- **Client helper + docs (T3.2's declared deliverables).** `endpointSocketUrl`/`endpointSocket` in
  `web/src/lib/endpoints.ts` map an endpoint's own scheme to `ws:`/`wss:` and refuse — never guess
  — an unparseable URL, a bare host, or a non-http scheme. Nothing consults `kind`: that field is a
  lifecycle fact, so a mislabelled row still dials correctly. The guide gains
  `### Reaching a remote gateway over wss://` covering the WebView-vs-native split (and why this is
  the one legitimate place a `base_url` is prepended to a path, which does not contradict C1's
  "load it as an origin" rule), the admission table, the no-middle-tier property, and the known
  rough edge.
- **CORRECTED — this atom's own commit staled C1's `token_auth.py` anchors, and they are fixed in
  the same change.** Adding `token_nonce()` shifted every citation in the guide's transport
  paragraph by +20 (`:582`→`:602`, `:587`→`:607`, `:1055`→`:1075`, `:1042`→`:1062`,
  `:1040-1041`→`:1059-1061`). `CA-6` fixed the same class of drift rather than inheriting it; the
  docs rails do not check line numbers, so this had to be caught by hand.
- **UNMET clauses (why the atom stays `todo`).** (a) The done-when is written as an *observation* —
  "a native client reaches a remote gateway **over the owner's tunnel**" — and nothing here drove a
  real tunnel with a real native shell; there is no native client in the repo to drive
  (`desktop/main.js` still holds one `backendUrl` and no shell reads the registry, per `CA-6`).
  (b) "killing the tunnel mid-session reconnects/degrades gracefully" is unobserved for the same
  reason; the SPA's capped-backoff contract is reused by reference, not re-verified. (c) The
  WebView/public-origin half is documented and pinned but not closed. `CA-8` is where a second
  endpoint can actually be paired and is the natural place to close (a) and (b).
- **Gate:** `make lint` clean (black 2029 files, isort, flake8, mypy 1001 sources); 22/22 on
  `tests/test_ca7_remote_wss_auth.py`; 20/20 on the two docs rails; web typecheck clean
  (`tsc --noEmit` + `tsconfig.sw.json`), the FULL web suite **478 files / 5050 tests**, and
  `npm run build` (524 assets, sw.js emitted). The full web suite was run rather than a subset
  because ~120 suites under `web/src` scan the tree.
