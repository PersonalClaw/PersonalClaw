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
- **MOBILE-COMPANION already designs device tokens + QR pairing** — its C1 adds a `device`
  claim to `generate_token` and a `devices.json` registry; C4 is `POST /api/devices/pair/
  start|complete` (single-use code, TTL 300s). **This plan unifies that pairing with
  REMOTE-USER-AUTH's enrollment code so there is ONE pairing path** (a device session is a
  `sessions.json` row, C1 of REMOTE-USER-AUTH, with `device` set), and MOBILE-COMPANION's C1/C4
  reference this plan rather than defining a parallel mechanism. Recorded as a supersession
  (this plan owns "device session + pairing"; MOBILE-COMPANION owns
  the phone UI + push).
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
  cookie/`?token=`; a native client presents its device session the same way.
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
`auth.session_ttl`; revocable from the Devices surface (revoke = flip `revoked` on the row).

### C2 — Unified pairing (SUPERSEDES MOBILE-COMPANION C4; folds in REMOTE-USER-AUTH C3 enroll)
| Route | Auth | Purpose |
|---|---|---|
| `POST /api/devices/pair/start` | session (LAN or logged-in) | → `{code, expires_in}`; single-use, TTL 300s, SEL `device_pair_started` |
| `POST /api/devices/pair/complete` | none (exempt) | `{code, device_name}` → durable device session (C1); SEL `device_paired`; reuse rejected |
| `GET /api/devices` | session | list device sessions (name, kind, last_seen, issuer) |
| `POST /api/devices/{id}/revoke` | session | revoke a device session; SEL `device_revoked` |
QR pairing (dashboard shows a QR of `{pair_url, code}`) is a **rendering of these routes**, not
a separate mechanism — MOBILE-COMPANION's QR screen scans it; the desktop connect dialog can
paste the code. Error codes (Tier-S): `device_pair_code_invalid`, `device_pair_expired`.

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

## Task breakdown (executor-ready — run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

**Change class B** (new durable pairing state, reusing REMOTE-USER-AUTH's store) — clean break
under the pre-1.0 banner. Sequenced strictly after REMOTE-USER-AUTH S1.

### Session 1 — Connectivity contract + Devices registry

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | Unified pairing routes (C2): `pair/start`, `pair/complete`, `GET /api/devices`, `revoke` — device sessions are REMOTE-USER-AUTH C1 rows with `device`/`issuer` set (no new token type); SEL on each | `dashboard/handlers/devices.py` (new), `server.py` wiring | pair start→complete yields a durable device session surviving a restart; reuse rejected; revoke kills it next request |
| T1.2 | Settings → Devices panel: list device sessions (name, kind, last-seen, issuer) + revoke; "Pair a device" shows a QR of `{pair_url, code}` | `web/src/pages/settings/DevicesPanel.tsx` | a device pairs from the QR end-to-end; revoke observed live |
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

1. **Confirm the supersession:** MOBILE-COMPANION's own device-token/QR design (C1/C4) is folded into this plan's unified pairing — approve that reconciliation (it removes duplication; the phone UI + push stay in MOBILE-COMPANION).
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
