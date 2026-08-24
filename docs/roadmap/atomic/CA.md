# COMPANION-APPS — atomic plans

**Source plan:** [`COMPANION-APPS`](../plans/COMPANION-APPS.md)  
**Code:** `CA`  
**Source status:** proposed



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `CA-1` | ✅ | S1 backend: unified pairing routes (C2) + Devices registry over REMOTE-USER-AUTH session rows | `EXT:REMOTE-USER-AUTH:durable sessions.json store + enroll path (C1/C3) — already shipped 2026-07-30` | pair/start->complete yields a durable device session (a sessions.json row with device/issuer set, no new token type) that survives a gateway restart; code reuse rejected (device_pair_code_invalid/expired); revoke locks the device out on its next request across a restart; SEL event on each route |
| `CA-2` | ✅ | S1 frontend: Settings → Devices panel (list, revoke, QR pairing) | `CA-1` | a second browser pairs as a device from the shown QR end-to-end over the LAN, appears in the Devices list (name, kind, last-seen, issuer), and revoking it is observed to lock it out live |
| `CA-3` | ✅ | S1 supersession reconciliation: fold MOBILE-COMPANION C1/C4 into this pairing contract | — | the two plans reference one pairing mechanism; grep shows no second/parallel device-token design surviving (Success Criterion 1) |
| `CA-4` | ✅ | S2 config: new `companion` section wired through all 5 config points | — | test_config_roundtrip green; PATCH toggles discovery_enabled and sets instance_name; both fields round-trip through load()/to_dict() |
| `CA-5` | ✅ | S2 discovery: optional mDNS advertiser + client resolver + guide | `CA-4` | a resolver on the LAN finds the instance by name and can begin pairing; loopback-only gateway is a no-op + log; TXT record asserted to carry no token/content; disabling discovery leaves manual-URL + QR pairing working (degradable, Success Criterion 5) |
| `CA-6` | ⬜ | S3 shared client contract doc + multi-gateway registry (amendment T3.3) | `EXT:PLATFORM-RESILIENCE:degraded-connection contract to reuse` | contract is precise enough that desktop + mobile implement it without re-deciding; two paired gateways are switchable from one client with zero state bleed (distinct sessions/inbox/settings per endpoint id); the doc states the hub veto verbatim; degraded UI reuses the existing contract |
| `CA-7` | ⬜ | S3 remote-endpoint auth path over wss (no new origin exemption) | `CA-6`, `EXT:REMOTE-USER-AUTH:S4 remote/TLS boundary (public_url, Secure/wss) — already shipped 2026-07-30` | a native client reaches a remote gateway over the owner's tunnel using its device session with no new origin exemption and no cloud middle tier in the path (client->owner-gateway only, Success Criterion 3); killing the tunnel mid-session reconnects/degrades gracefully |
| `CA-8` | ⬜ | S4 desktop connect-to-gateway mode + multi-gateway switcher (T4.1 + amendment T4.4) | `CA-6`, `CA-7`, `EXT:DESKTOP-CAPABILITIES:Electron shell connect-mode coordination` | the desktop app connects to a gateway it did not spawn (LAN or remote) while defaulting to spawn-local; the connect dialog lists N paired gateways, shows the active one, switches cleanly with graceful reconnect per S3; revoking one gateway's device session breaks only that entry |
| `CA-9` | ⬜ | S4 coordination docs: mobile task refs + future-platform recipe | `CA-6`, `EXT:MOBILE-COMPANION:PWA/Capacitor consumes pairing + endpoint switch` | MOBILE-COMPANION S4 pairing references C2 with no parallel device-token code; the future-platform recipe is written with no speculative per-platform code shipped |

## Atom scopes

### `CA-1` — S1 backend: unified pairing routes (C2) + Devices registry over REMOTE-USER-AUTH session rows

**Status:** done

Session 1 T1.1; Contracts C2 (POST /api/devices/pair/start|complete, GET /api/devices, POST /api/devices/{id}/revoke), C1 device-session model; new dashboard/handlers/devices.py + server.py wiring; SEL device_pair_started/device_paired/device_revoked

**Done when:** pair/start->complete yields a durable device session (a sessions.json row with device/issuer set, no new token type) that survives a gateway restart; code reuse rejected (device_pair_code_invalid/expired); revoke locks the device out on its next request across a restart; SEL event on each route

### `CA-2` — S1 frontend: Settings → Devices panel (list, revoke, QR pairing)

**Status:** todo

Session 1 T1.2 + V1; new web/src/pages/settings/DevicesPanel.tsx; QR renders {pair_url, code} from C2 routes

**Done when:** a second browser pairs as a device from the shown QR end-to-end over the LAN, appears in the Devices list (name, kind, last-seen, issuer), and revoking it is observed to lock it out live

### `CA-3` — S1 supersession reconciliation: fold MOBILE-COMPANION C1/C4 into this pairing contract

**Status:** todo

Session 1 T1.3; edits docs/roadmap/plans/MOBILE-COMPANION.md (this plan owns device session + pairing)

**Done when:** the two plans reference one pairing mechanism; grep shows no second/parallel device-token design surviving (Success Criterion 1)

### `CA-4` — S2 config: new `companion` section wired through all 5 config points

**Status:** done

Session 2 T2.1; Contracts C4; config/loader.py dataclass+_meta+load()+to_dict(), _EDITABLE_CONFIG PATCH allowlist in dashboard/handlers/core.py, FE control; companion.discovery_enabled (default off) + companion.instance_name

**Done when:** test_config_roundtrip green; PATCH toggles discovery_enabled and sets instance_name; both fields round-trip through load()/to_dict()

**DONE (2026-08-15):** new `CompanionConfig` dataclass (`config/loader.py`) with `discovery_enabled: bool` (default **off** — announcing on the LAN is an opt-in) + `instance_name: str`, wired through all five points: dataclass+`_meta`, `AppConfig.companion`, `load()` (guarded `companion` read + explicit construction), `to_dict()`, the `_EDITABLE_CONFIG` PATCH allowlist (`companion.discovery_enabled`/`instance_name`), and a FE control — a new **Companion apps** settings panel (`CompanionPanel.tsx`, registered in `SettingsPage`) with a LAN-discovery toggle + instance-name field. `companion` added to `test_config_roundtrip`'s exhaustive leaf-walk; dedicated round-trip + allowlist + default-off tests added. Falsified: breaking the `load()` mapping → the round-trip reverts (`assert False is True`); removing the allowlist entry → the write-path test reds.

### `CA-5` — S2 discovery: optional mDNS advertiser + client resolver + guide

**Status:** done

Session 2 T2.2, T2.3 + V2; Contracts C3; new companion/discovery.py (advertise _personalclaw._tcp with token-free TXT only when bound beyond loopback + discovery_enabled) + client resolver + new docs/guides/companion-apps.md fallback section

**Done when:** a resolver on the LAN finds the instance by name and can begin pairing; loopback-only gateway is a no-op + log; TXT record asserted to carry no token/content; disabling discovery leaves manual-URL + QR pairing working (degradable, Success Criterion 5)

**DONE (2026-08-16):** new `personalclaw/companion/discovery.py` — an mDNS/DNS-SD advertiser and
client resolver for `_personalclaw._tcp.local.`, **written on the standard library** (a hand-rolled
DNS wire codec over a multicast UDP socket). No third-party responder was added: `SO_REUSEPORT`
lets the socket coexist with the host's own responder on port 5353, which was the only reason a
dependency looked necessary. Ships with `GET /api/companion/discovery` (the LIVE advertiser state,
not the config flag), a live-apply hook on the `companion.*` PATCH path so the toggle needs no
restart, `personalclaw discover` as the resolver's real caller, a Settings → Companion apps status
row that shows the broadcast record verbatim, and `docs/guides/companion-apps.md`.

*The security properties, asserted rather than asserted-about:* the TXT record is built from a
closed four-key set that `build_txt`/`encode_txt` cannot exceed, and a test serializes a real
announcement with a real `.local_secret`, session token and enroll code on disk and asserts none
of them appear in the packet bytes. A loopback-only bind is a no-op with a log line naming the fix
(`PERSONALCLAW_BIND_HOST`), and an unreadable config **fails closed** — this surface broadcasts, so
a broken read is not permission to announce. Discovery never imports `personalclaw.auth`, and
`auth/enrollment.py` never imports `companion` (both asserted by AST), so discovery can neither
carry a credential nor become a precondition for pairing.

*Observed for real (not test-only):* Apple's own `mDNSResponder`, via `dns-sd -B`/`-L`, discovered
`CA5 Probe Box` by name on the real LAN interface and printed the TXT verbatim
(`name=… port=10166 requires_pairing=1 schema=1`); the shipped resolver returned
`base_url http://192.168.86.33:10166` over real multicast; and against a live gateway on :10166 the
PATCH flipped `advertising` false→true with **no restart**, `personalclaw discover` printed the
instance, and redeeming an `auth enroll` code at the discovered gateway returned 200 with a
30-day device session. NOT observed: resolution from a **second host** (no second machine
available) — same-host multicast only, by two independent resolvers.

*Falsified:* putting the local secret into the TXT keys → `AssertionError: a credential reached the
wire: s3cr3t-l…` plus two more reds; making the loopback branch advertise anyway →
`AssertionError: assert True is False`.

**DEVIATION — the done_when's "QR pairing" clause is vacuous today.** There is no QR surface in the
repo (`grep -i qrcode` → zero hits): QR is a *rendering* of the pairing routes that `CA-1`/`CA-2`
own, both still todo. So degradability is asserted against the pairing path that DOES exist — the
typed LAN URL plus `personalclaw auth enroll` — and structurally, by proving `enrollment` cannot
import `companion`. When `CA-2` ships the QR screen it inherits that property rather than needing
a new one.

**DISCOVERY (not fixed here — belongs to `CA-1`/REMOTE-USER-AUTH).** Redeeming a pairing code from a
browser that reached the gateway **by IP** is refused: `POST /api/auth/enroll/complete` with
`Origin: http://192.168.86.33:10166` → 403 `CSRF check failed: request origin not allowed`, because
`build_allowed_origins` covers the loopback names and the bare machine hostname but no LAN address.
Reads from that origin are fine (200); only the state-changing call is rejected. Left alone
deliberately: the plan requires the pairing path work "with no new origin exemption", so widening
the allowlist is that plan's call, not this atom's. Documented as a known rough edge in the guide.
Also measured and NOT a defect: a token already used from another address is refused with
`IP mismatch` — a fresh `personalclaw token` used first on the other device works.

### `CA-6` — S3 shared client contract doc + multi-gateway registry (amendment T3.3)

**Status:** todo

Session 3 T3.1 + Amendment T3.3; docs/guides/companion-apps.md + optional minimal shared TS helper in web/src/lib/; C1 endpoint model, {active, endpoints[]} registry, per-endpoint state namespacing, switcher spec, verbatim no-hub / no-gateway-to-gateway rule; reuses (not re-invents) the platform-resilience degraded-connection contract

**Done when:** contract is precise enough that desktop + mobile implement it without re-deciding; two paired gateways are switchable from one client with zero state bleed (distinct sessions/inbox/settings per endpoint id); the doc states the hub veto verbatim; degraded UI reuses the existing contract

### `CA-7` — S3 remote-endpoint auth path over wss (no new origin exemption)

**Status:** todo

Session 3 T3.2 + V3; client helper + docs; native client presents its device session over wss:// per REMOTE-USER-AUTH S4; verify check_origin/CSP allow it with no new exemption

**Done when:** a native client reaches a remote gateway over the owner's tunnel using its device session with no new origin exemption and no cloud middle tier in the path (client->owner-gateway only, Success Criterion 3); killing the tunnel mid-session reconnects/degrades gracefully

### `CA-8` — S4 desktop connect-to-gateway mode + multi-gateway switcher (T4.1 + amendment T4.4)

**Status:** todo

Session 4 T4.1, T4.4 + V4; desktop/main.js + a connect dialog, coordinated with DESKTOP-CAPABILITIES; spawn-local stays the default and unchanged

**Done when:** the desktop app connects to a gateway it did not spawn (LAN or remote) while defaulting to spawn-local; the connect dialog lists N paired gateways, shows the active one, switches cleanly with graceful reconnect per S3; revoking one gateway's device session breaks only that entry

### `CA-9` — S4 coordination docs: mobile task refs + future-platform recipe

**Status:** todo

Session 4 T4.2, T4.3; MOBILE-COMPANION.md task refs point pairing/endpoint-switch at C2/C1/S3 (no duplicated device-token code); docs/guides/companion-apps.md future-platform recipe (wrap served UI + implement S3 client contract, gated on PLATFORM-REACH)

**Done when:** MOBILE-COMPANION S4 pairing references C2 with no parallel device-token code; the future-platform recipe is written with no speculative per-platform code shipped

