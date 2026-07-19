# Plan: Mobile Companion — Monitor, Track, and Approve From the Phone

**Status:** DESIGNED — deepened 2026-07-18 with code recon (initial PROPOSED 2026-07-18; owner scope: monitor states + track ongoing tasks; investigation sharpening: **approvals are the killer feature** — approval latency caps autonomy)
**Created:** 2026-07-18
**Wave:** 2 (S1-3: remote-access story + PWA tier) + 3 (S4-6: wrapper + platform push). Stage gate: the PWA must prove the surface before any store app wraps it.
**Depends on:** INBOX-NOTIFICATIONS-UNIFICATION (rules decide what reaches the phone; the `push` target activates here), CHANNEL-EXPANSION (channels are chat-on-phone; this is the *control surface*, not a chat app), EXTERNAL-ACCESS (future hardened non-VPN access — until then, **VPN-overlay only**).
**Scope:** a phone surface for pending approvals, running loops (pause/nudge/stop), tasks/inbox, and notifications. **Soul guardrail:** the phone talks to the user's own gateway — **no cloud middle tier holding state or credentials**. The only permissible hosted component is an opt-in dumb push relay carrying content-free wake-up pings (item ids, never content); self-hosted push (ntfy/UnifiedPush) is the first-class path. The companion view is a phone-shaped subset — not a 20-surface dashboard shrink.

---

## Context (code recon, 2026-07-18)

- **No PWA substrate exists:** `web/public/` holds only `claw.svg` + fonts — no manifest, no service worker. `useIsMobile.ts` exists (responsive hooks in place).
- **Token machinery fits device pairing:** `token_auth.py::generate_token(user_id, ttl_seconds, app="")` with `MAX_SESSION_TTL_SECS = 1 year`; nonce registry + eviction. **Caveat found:** `bind_ip(token, ip, …)` — tokens appear IP-bound; a roaming phone changes IPs. S2 must verify bind semantics (bind-on-first-use? per-request rebind? reject-on-mismatch?) and design device tokens accordingly (likely: a `device` claim minted without IP binding, or rebind-allowed) — **E4-adjacent: change only what the task specifies after reading the code.**
- Approval answer route: `POST /api/chat/sessions/{session}/approve`. Loop controls exist behind the loops handlers (exact routes to be mapped in S2 — the loops pages drive them today). Notifications/inbox APIs per plan 42.
- Remote access today: none documented; auth modes support token URLs (`personalclaw token`).

## Design

- **S1 — remote access first** (valuable standalone): Tailscale-first docs (gateway joins the tailnet; phone joins; token-auth'd dashboard over it — works with `AUTH_MODE=local_token` today), Cloudflare Tunnel alternative, explicit anti-pattern warnings (no raw port-forward; `none`-mode is loopback-forced anyway); `doctor` reachability probe (detect tailnet interface, print the phone-usable URL via `personalclaw token`).
- **S2-3 — PWA tier:** manifest + installability + service worker (app-shell caching only — API responses are never cached: stale approval data is dangerous); a **Companion route** (`#/companion`): approvals front and center (decision-brief cards from plan 43 T3), running loops with pause/nudge/stop, tasks/inbox lists (read + resolve), recent notifications; **web push** where supported (VAPID keys generated locally, subscription stored per device; push payloads content-free: `{kind, item_id}` → the app fetches details over the VPN link on tap) + **ntfy/UnifiedPush** documented as the fully-self-hosted push backbone; `push` becomes a real target in plan 42's rules engine.
- **S4-6 — wrapper tier:** Capacitor shell around the Companion route (store presence + reliable platform push); pairing = QR from the dashboard (URL + scoped device token; revocation via existing token machinery + a Devices list in Settings); push routed via ntfy apps (first-class) or an opt-in relay (content-free pings; relay code open-source in the org, deployable by anyone — the hosted instance is a convenience, not a dependency); iOS/Android store packaging.

## Contracts & Interfaces (conventions per [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md))

### C1 — Device token (EXTENDS `token_auth.py::generate_token(user_id, ttl_seconds=3600, *, app="")`, verified `:257`)
Add a `device: str = ""` claim. **The `bind_ip` behavior (verified `:362`) is the design pivot** — T2.3 reads it first and chooses: device tokens are minted **without IP binding** (roaming phones) OR with rebind-on-mismatch. Whichever, it is the **minimal** change consistent with the model (E4 if it needs weakening an auth invariant). Default TTL 30d; SEL on mint/revoke (`log_api_access(caller="device:<name>", operation="device_token_mint|revoke", …)`). Devices registry `~/.personalclaw/entity_settings/devices.json`: `{"<device_id>": {"name","minted_at","last_seen_at","token_nonce"}}`.

### C2 — Companion route API map (all EXISTING endpoints — the companion view is a client, adds no backend except push)

| Action | Endpoint (verified) |
|---|---|
| answer approval | `POST /api/chat/sessions/{session}/approve` (`server.py:667`) |
| loop pause/stop/etc | `POST /api/loop/{id}/action` → `api_loop_action` (`loop_routes.py:373`) |
| loop nudge | `POST /api/loop/{id}/nudge` → `api_loop_nudge` (`loop_routes.py:464`) |
| inbox list/resolve | plan 42 inbox API |
| notifications | existing notifications API |

Companion route `#/companion` (frontend only; URL doctrine). **Service worker: app-shell precache ONLY; `/api/*` is network-first, never cached** (stale approval data is dangerous — §2.7 fail-closed for correctness).

### C3 — Push (activates plan 42's `push` target)

```python
# backend push module (new, small)
def push_init() -> tuple[str,str]: ...      # VAPID keypair → credential store PERSONALCLAW_VAPID_{PUBLIC,PRIVATE}
def subscribe(device_id: str, subscription: dict) -> None: ...   # W3C PushSubscription JSON, per-device
def send_push(device_id: str, payload: dict) -> None: ...        # payload = {"kind":..,"item_id":..} CONTENT-FREE
```
Plan 42 rules-engine `push` target calls `send_push` with `{kind, item_id}` only — the app fetches details over the VPN link on tap. ntfy/UnifiedPush alternative: POST content-free ping to a user-configured topic URL. Config (5-point, §2.1): `mobile.push_backend: "webpush"|"ntfy"|"none"`, `mobile.ntfy_topic_url: str`.

### C4 — QR pairing (wrapper tier)
`POST /api/devices/pair/start` → `{pairing_url, code}` (code single-use, TTL 300s, SEL-logged); app scans → `POST /api/devices/pair/complete {code}` → device token (C1). Errors use §2.2 envelope.

### Integration points
- **Calls:** `generate_token`/token registry (§C1), the existing approval/loop/inbox/notification endpoints (§C2), plan-42 rules engine (`push` target registration), `save_credential` (VAPID), `sel()`.
- **Called by:** the PWA + the Capacitor wrapper (both render the same served `#/companion`).
- **Depends on:** plan 42 (push target must exist), EXTERNAL-ACCESS/VPN for off-LAN reach (docs), CHANNEL (channels are the chat-on-phone answer — this is control-surface only).
- **Storage:** `devices.json`; VAPID keys in credential store; push subscriptions per device.

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

### Session 1 — Remote access story

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | `docs/guides/remote-access.md`: Tailscale walkthrough (install, join, `personalclaw token` URL, auth-mode notes), Cloudflare Tunnel alt, anti-patterns section (why not port-forward; what `bypass_local_networks` does and when NOT to set it) | new guide | a reader reaches their dashboard from a phone on cell data via tailnet following it verbatim (owner task 1 validates) |
| T1.2 | `doctor` reachability: detect tailscale interface/hostname, print the phone-ready tokenized URL; warn when bind host exposes beyond loopback without auth | `cli_doctor.py` | tailnet fixture prints URL; misconfig fixture warns |
| V1 | Validation: owner's phone on cell data reaches the dashboard read-write via tailnet; nothing listens on public interfaces (verify with `ss`/scan) | — | confirmed + recorded |

### Session 2 — Companion view (PWA part 1)

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | Route + IA: `#/companion` with four stacked sections (Approvals, Running, Inbox, Recent) using existing shell primitives + `useIsMobile`; large touch targets; no sidebar | `web/src/pages/companion/` new components, router registration | renders on a phone viewport; URL doctrine holds |
| T2.2 | Map + wire the control endpoints: approvals (`.../approve`), loop pause/nudge/stop (locate the loops handlers' routes — record the route map in the Execution log), task state transitions, inbox resolve (plan 42 API) | companion components | every action round-trips against a dev gateway; optimistic UI reverts on failure |
| T2.3 | Device-token semantics: read `bind_ip` behavior; design + implement the device-token path per findings (likely `generate_token(..., device=name)` unbound or rebind-allowed; TTL 30d default; SEL event on mint/revoke) — **minimal change consistent with the existing model; E4 if it requires weakening any auth invariant** | `token_auth.py` (surgical), tests | roaming-IP fixture keeps the device session valid per the chosen design; findings + choice in Execution log |
| T2.4 | Devices list in Settings (name, minted, last-seen, revoke) reading the token registry | Settings panel component + small API | revoke kills the device session on next request |
| V2 | Validation: from the phone — approve a real tool call, pause/nudge a loop, resolve an inbox item; revoke the device and observe lockout | — | all hold |

### Session 3 — Installability + push (PWA part 2)

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | Manifest (icons from the claw mark, standalone display, start_url `#/companion`) + service worker (app-shell precache ONLY; network-first for everything; explicit no-cache for `/api/`) | `web/public/manifest.webmanifest`, `web/src/sw.ts`, vite wiring | Lighthouse installability passes; API responses never served from cache (test with offline toggle) |
| T3.2 | Web push: VAPID keypair generation (`personalclaw push init` CLI; keys in credential store), subscription endpoint + per-device storage, content-free payload sender wired as plan 42's `push` target | backend push module (new, small), CLI, rules-engine target registration | push arrives on a subscribed phone for an `immediate`+push rule; payload contains ids only (inspect) |
| T3.3 | ntfy/UnifiedPush path: docs + a delivery adapter (POST to user-configured ntfy topic URL, content-free) as an alternative push target | adapter + `docs/guides/remote-access.md` section | self-hosted ntfy receives pings; tap-through opens companion (deep link) |
| V3 | Validation: install to home screen; background push → tap → approval resolved in <30s round-trip on cell data | — | timed + recorded |

### Sessions 4-6 — Wrapper tier (Wave 3)

| ID | Task | Files | Done when |
|---|---|---|---|
| T4.1 | Capacitor shell: wraps the served companion URL (config: gateway URL + device token from pairing), native safe-areas, no forked UI | new `mobile/` dir in core repo (or org repo — decision recorded) | shell builds for iOS+Android; renders the live companion |
| T4.2 | QR pairing: dashboard Settings → Devices → "Pair phone" renders QR {url, one-time pairing code} → app scans → exchanges for a device token (single-use, TTL 5min, SEL-logged) | Settings component, pairing endpoint, app pairing screen | pair from QR end to end; code single-use verified |
| T4.3 | Platform push: ntfy app integration documented as default; optional relay: open-source `push-relay` (stateless, content-free, org repo) + APNs/FCM wiring in the shell for relay users | relay repo content, shell push registration | both paths deliver; relay logs contain no content (audit fixture) |
| T4.4 | Store packaging: icons/splash from brand assets, privacy declarations (no data collection — truthfully), build docs; TestFlight/internal-track builds | shell config + `docs/maintainers/mobile-release.md` | installable builds produced via documented steps (owner runs store submissions — owner tasks 3-4) |
| V4-6 | Validation: full field week — owner daily-driving approvals from the wrapper app; friction list triaged | — | week recorded; fix-now items closed |

## Owner tasks (real world)

1. **Tailscale account + install** on your server and phone (free tier suffices; ~15 min) — S1 validation.
2. **Decide the push default** for docs: self-hosted ntfy (fully sovereign, one more service) vs the hosted relay convenience (content-free pings only) — the guide leads with your choice.
3. **Apple Developer Program** ($99/yr) + **Google Play Console** ($25 once) enrollments — only when S4 starts; TestFlight/internal tracks first; store review copy will need your name/address (Apple requirement).
4. **Store submissions** (assisted: executor prepares assets/copy; you click through the consoles and answer review questions).
5. **Field week** (V4-6): daily-drive the app for a week and keep the friction list honest.

## Risks & open questions

- **IP-bound tokens vs roaming phones** is the one real unknown (T2.3 resolves it surgically); worst case the device claim mints unbound tokens with shorter TTL + SEL visibility — still within the existing model.
- **iOS web-push limitations** (requires installed PWA; feature-gated by iOS version) — the wrapper tier exists precisely for reliable iOS push; PWA push documented as best-effort on iOS.
- **Open:** whether the companion should also render a minimal chat composer ("quick ask") — deferred; channels cover phone chat (revisit after field week evidence).
