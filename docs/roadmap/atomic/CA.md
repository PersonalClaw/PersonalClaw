# COMPANION-APPS — atomic plans

**Source plan:** [`COMPANION-APPS`](../plans/COMPANION-APPS.md)  
**Code:** `CA`  
**Source status:** proposed



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `CA-1` | ⬜ | S1 backend: unified pairing routes (C2) + Devices registry over REMOTE-USER-AUTH session rows | `EXT:REMOTE-USER-AUTH:durable sessions.json store + enroll path (C1/C3) — already shipped 2026-07-30` | pair/start->complete yields a durable device session (a sessions.json row with device/issuer set, no new token type) that survives a gateway restart; code reuse rejected (device_pair_code_invalid/expired); revoke locks the device out on its next request across a restart; SEL event on each route |
| `CA-2` | ⬜ | S1 frontend: Settings → Devices panel (list, revoke, QR pairing) | `CA-1` | a second browser pairs as a device from the shown QR end-to-end over the LAN, appears in the Devices list (name, kind, last-seen, issuer), and revoking it is observed to lock it out live |
| `CA-3` | ⬜ | S1 supersession reconciliation: fold MOBILE-COMPANION C1/C4 into this pairing contract | — | the two plans reference one pairing mechanism; grep shows no second/parallel device-token design surviving (Success Criterion 1) |
| `CA-4` | ⬜ | S2 config: new `companion` section wired through all 5 config points | — | test_config_roundtrip green; PATCH toggles discovery_enabled and sets instance_name; both fields round-trip through load()/to_dict() |
| `CA-5` | ⬜ | S2 discovery: optional mDNS advertiser + client resolver + guide | `CA-4` | a resolver on the LAN finds the instance by name and can begin pairing; loopback-only gateway is a no-op + log; TXT record asserted to carry no token/content; disabling discovery leaves manual-URL + QR pairing working (degradable, Success Criterion 5) |
| `CA-6` | ⬜ | S3 shared client contract doc + multi-gateway registry (amendment T3.3) | `EXT:PLATFORM-RESILIENCE:degraded-connection contract to reuse` | contract is precise enough that desktop + mobile implement it without re-deciding; two paired gateways are switchable from one client with zero state bleed (distinct sessions/inbox/settings per endpoint id); the doc states the hub veto verbatim; degraded UI reuses the existing contract |
| `CA-7` | ⬜ | S3 remote-endpoint auth path over wss (no new origin exemption) | `CA-6`, `EXT:REMOTE-USER-AUTH:S4 remote/TLS boundary (public_url, Secure/wss) — already shipped 2026-07-30` | a native client reaches a remote gateway over the owner's tunnel using its device session with no new origin exemption and no cloud middle tier in the path (client->owner-gateway only, Success Criterion 3); killing the tunnel mid-session reconnects/degrades gracefully |
| `CA-8` | ⬜ | S4 desktop connect-to-gateway mode + multi-gateway switcher (T4.1 + amendment T4.4) | `CA-6`, `CA-7`, `EXT:DESKTOP-CAPABILITIES:Electron shell connect-mode coordination` | the desktop app connects to a gateway it did not spawn (LAN or remote) while defaulting to spawn-local; the connect dialog lists N paired gateways, shows the active one, switches cleanly with graceful reconnect per S3; revoking one gateway's device session breaks only that entry |
| `CA-9` | ⬜ | S4 coordination docs: mobile task refs + future-platform recipe | `CA-6`, `EXT:MOBILE-COMPANION:PWA/Capacitor consumes pairing + endpoint switch` | MOBILE-COMPANION S4 pairing references C2 with no parallel device-token code; the future-platform recipe is written with no speculative per-platform code shipped |

## Atom scopes

### `CA-1` — S1 backend: unified pairing routes (C2) + Devices registry over REMOTE-USER-AUTH session rows

**Status:** todo

Session 1 T1.1; Contracts C2 (POST /api/devices/pair/start|complete, GET /api/devices, POST /api/devices/{id}/revoke), C1 device-session model; new dashboard/handlers/devices.py + server.py wiring; SEL device_pair_started/device_paired/device_revoked

**Done when:** pair/start->complete yields a durable device session (a sessions.json row with device/issuer set, no new token type) that survives a gateway restart; code reuse rejected (device_pair_code_invalid/expired); revoke locks the device out on its next request across a restart; SEL event on each route

### `CA-2` — S1 frontend: Settings → Devices panel (list, revoke, QR pairing)

**Status:** todo

Session 1 T1.2 + V1; new web/src/pages/settings/DevicesPanel.tsx; QR renders {pair_url, code} from C2 routes

**Done when:** a second browser pairs as a device from the shown QR end-to-end over the LAN, appears in the Devices list (name, kind, last-seen, issuer), and revoking it is observed to lock it out live

### `CA-3` — S1 supersession reconciliation: fold MOBILE-COMPANION C1/C4 into this pairing contract

**Status:** todo

Session 1 T1.3; edits docs/roadmap/plans/MOBILE-COMPANION.md + INTEGRATION-ARCHITECTURE.md §5 (this plan owns device session + pairing)

**Done when:** the two plans reference one pairing mechanism; grep shows no second/parallel device-token design surviving (Success Criterion 1)

### `CA-4` — S2 config: new `companion` section wired through all 5 config points

**Status:** todo

Session 2 T2.1; Contracts C4; config/loader.py dataclass+_meta+load()+to_dict(), _EDITABLE_CONFIG PATCH allowlist in dashboard/handlers/core.py, FE control; companion.discovery_enabled (default off) + companion.instance_name

**Done when:** test_config_roundtrip green; PATCH toggles discovery_enabled and sets instance_name; both fields round-trip through load()/to_dict()

### `CA-5` — S2 discovery: optional mDNS advertiser + client resolver + guide

**Status:** todo

Session 2 T2.2, T2.3 + V2; Contracts C3; new companion/discovery.py (advertise _personalclaw._tcp with token-free TXT only when bound beyond loopback + discovery_enabled) + client resolver + new docs/guides/companion-apps.md fallback section

**Done when:** a resolver on the LAN finds the instance by name and can begin pairing; loopback-only gateway is a no-op + log; TXT record asserted to carry no token/content; disabling discovery leaves manual-URL + QR pairing working (degradable, Success Criterion 5)

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

