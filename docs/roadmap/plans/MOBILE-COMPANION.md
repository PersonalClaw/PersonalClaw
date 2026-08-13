# MOBILE-COMPANION

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/MC.md`](../atomic/MC.md) as 10 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Mobile Companion — Monitor, Track, and Approve From the Phone

**Status:** DESIGNED — deepened 2026-07-18 with code recon (initial PROPOSED 2026-07-18; owner scope: monitor states + track ongoing tasks; investigation sharpening: **approvals are the killer feature** — approval latency caps autonomy)
**Created:** 2026-07-18
**Wave:** 2 (S1-3: remote-access story + PWA tier) + 3 (S4-6: wrapper + platform push). Stage gate: the PWA must prove the surface before any store app wraps it.
**Depends on:** INBOX-NOTIFICATIONS-UNIFICATION (rules decide what reaches the phone; the `push` target activates here), CHANNEL-EXPANSION (channels are chat-on-phone; this is the *control surface*, not a chat app), EXTERNAL-ACCESS (future hardened non-VPN access — until then, **VPN-overlay only**), **COMPANION-APPS (plan 54 — owns device sessions + unified pairing + endpoint switching; this plan consumes them, see C1/C4 reconciliation) + REMOTE-USER-AUTH (plan 53 — the durable session store device sessions live in)**.
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

## Contracts & Interfaces (conventions per [AGENTS.md](../../../AGENTS.md))

### C1 — Device token — **SUPERSEDED by COMPANION-APPS §C1/C2 (plan 54) + REMOTE-USER-AUTH §C1 (plan 53)**
> **Rev-11 reconciliation (2026-07-26):** device sessions and pairing are now **owned once** by
> COMPANION-APPS (the connectivity contract) on REMOTE-USER-AUTH's durable session store. A
> "device token" is a `sessions.json` row with `device`/`issuer` set — this plan **consumes**
> that contract instead of extending `generate_token` itself. The original design below is kept
> for historical context; when this plan reaches execution, its device-auth tasks reference
> plan 54 §C1/C2 rather than touching `token_auth.py`. This plan still owns the **phone UI +
> push**, not the token/pairing mechanism.

Original design (superseded): EXTENDS `token_auth.py::generate_token(user_id, ttl_seconds=3600, *, app="")` (verified `:257`) — add a `device: str = ""` claim. **The `bind_ip` behavior (verified `:362`) is the design pivot** — T2.3 reads it first and chooses: device tokens are minted **without IP binding** (roaming phones) OR with rebind-on-mismatch. Whichever, it is the **minimal** change consistent with the model (E4 if it needs weakening an auth invariant). Default TTL 30d; SEL on mint/revoke (`log_api_access(caller="device:<name>", operation="device_token_mint|revoke", …)`). Devices registry `~/.personalclaw/entity_settings/devices.json`: `{"<device_id>": {"name","minted_at","last_seen_at","token_nonce"}}`.

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

### C4 — QR pairing (wrapper tier) — **SUPERSEDED by COMPANION-APPS §C2 (plan 54)**
> **Rev-11 reconciliation (2026-07-26):** the `POST /api/devices/pair/start|complete` routes are
> now owned by COMPANION-APPS §C2 (unified pairing). This plan's QR screen **renders** those
> routes; it does not define them. The shape below matches the owner contract and is retained
> for reference.

`POST /api/devices/pair/start` → `{pairing_url, code}` (code single-use, TTL 300s, SEL-logged); app scans → `POST /api/devices/pair/complete {code}` → device session (COMPANION-APPS §C1). Errors use §2.2 envelope.

### Integration points
- **Calls:** `generate_token`/token registry (§C1), the existing approval/loop/inbox/notification endpoints (§C2), plan-42 rules engine (`push` target registration), `save_credential` (VAPID), `sel()`.
- **Called by:** the PWA + the Capacitor wrapper (both render the same served `#/companion`).
- **Depends on:** plan 42 (push target must exist), EXTERNAL-ACCESS/VPN for off-LAN reach (docs), CHANNEL (channels are the chat-on-phone answer — this is control-surface only).
- **Storage:** `devices.json`; VAPID keys in credential store; push subscriptions per device.

## Task breakdown (executor-ready — run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

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

## Amendment (2026-07-26 — sibling-platform gap analysis, owner greenlight)

**Approve-from-phone is milestone one.** Sibling evidence confirms this plan's own investigation note ("approvals are the killer feature — approval latency caps autonomy"): the first shipped slice must be the complete loop *push notification on pending approval → open PWA → approve/reject with the approval card's full context* — not a four-section companion page that happens to include approvals. This is a **reordering and sharpening of existing scope, not an expansion**: every ingredient is already in S1-S3 (the `#/companion` route, `POST /api/chat/sessions/{session}/approve` at `server.py:667`, `GET /api/approvals` + `POST /api/approvals/{id}/{action}` at `server.py:941-942`, C3 push, plan 42's `push` target). The change: S2 ships approvals-only; S3 wires push to approvals FIRST; loops/tasks/inbox sections move to a new S3.5 that can slip without delaying the milestone.

### Contract-level design (sharpened, not new)

- **Milestone-1 definition of done:** phone locked in a pocket → an unattended run hits a tool approval → content-free push `{kind: "approval", item_id}` (C3, unchanged) → tap opens `#/companion` scrolled to that approval → the card shows FULL context (tool name, arguments, session/agent, the plan-43 decision-brief when it exists; raw fallback until then — do not block on plan 43) → approve/reject → the paused run proceeds. Target round-trip <30s on cell data (the existing V3 number, now attached to the milestone).
- **Approval card context source:** the same `GET /api/approvals` rows the dashboard Action Center renders — the companion adds no backend; if a context field is missing on the phone it is missing on the desktop too (fix at the shared endpoint, once).
- **Per-category sounds/badges ride plan 42, not new machinery:** the notification-rules store (plan 42 C2) — not this plan — carries any per-(source,kind) `sound`/badge preference; the service worker maps the push payload's `kind` to the platform notification options (`tag` for coalescing, `badge` count from inbox PENDING per plan 42 S5). This plan contributes only the SW-side mapping; the rules schema field itself is a one-line addition proposed to plan 42 (record as a coordination note there). No sound/badge config UI is built here.

### Session placement (reorder; count 6 → unchanged, boundaries move)

- **S1** unchanged (remote access — the transport the milestone needs).
- **S2 — Approvals-first companion:** T2.1 narrows to the Approvals section only (full-context cards); T2.2 narrows to the approve/reject wiring; T2.3/T2.4 (device sessions/devices list — already superseded to plan 54's contract) unchanged.
- **S3 — Push-to-approval (milestone 1 completes here):** T3.1 (manifest/SW) + T3.2 (web push) + tap-through deep-link to the specific approval; V3 is the milestone validation.
- **S3.5 — the rest of the companion (new session, absorbing deferred S2 scope):** Running loops (pause/nudge/stop), tasks/inbox, recent notifications; plus the SW sound/badge mapping (needs plan 42's field).

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1r | Rescope S2 to approvals-only: `#/companion` renders full-context approval cards from `GET /api/approvals`; approve/reject round-trip; other sections stubbed behind S3.5 | `web/src/pages/companion/`, router | phone viewport: a real pending approval renders with tool+args+session context and resolves; no other section ships |
| T3.4 | Push→approval deep link: SW notification click opens `#/companion?approval=<id>` scrolled/highlighted; payload stays content-free `{kind, item_id}` | `web/src/sw.ts`, companion route | locked-phone push → tap → correct card focused → approve → run proceeds; <30s on cell data, timed |
| T3.5.1 | S3.5: loops/tasks/inbox/notifications sections (former T2.1/T2.2 scope) + SW per-kind sound/badge mapping reading plan 42's rules field | companion components, `sw.ts` | sections work per original S2 Done-whens; distinct sound fires for a kind configured in the rules UI |

## Execution log

### 2026-08-13 — `MC-3` (S2 T2.1r + T2.2 approvals half) — **DONE**

`#/companion` ships approvals-first. A full-screen, no-NavRail hash route
(`web/src/pages/companion/CompanionPage.tsx`) renders one card per `GET /api/approvals` row with the
whole decision on screen — tool, arguments UNTRUNCATED (a structured `tool_input` is pretty-printed
rather than stringified), purpose, session, requesting source, and how long it has been waiting.
Approve/reject round-trip live; Running/Inbox/Recent are named as not-yet-built, not rendered as empty
data. Registered in `App.tsx` next to the `#/onboarding` early return and deliberately kept OUT of
`NAV`/`ROUTABLE`: in `NAV` it would demand an e2e route-manifest entry (`routeManifestParity`) and put a
phone surface in the desktop rail; in `ROUTABLE` it would render inside the shell WITH the rail.
Not gated on `useIsMobile` — that is a `max-width` media query, not a touch test, so gating would only
make the route undebuggable from a desktop browser.

**RESOLVED ROUTE MAP (T2.2, approvals half) — and a correction to §C2.**

| Action | Endpoint actually wired | Handler |
|---|---|---|
| list pending approvals | `GET /api/approvals` | `handlers/sessions.py::api_approvals` → `list(state._pending_approvals.values())`; registered `dashboard/server.py:1097` |
| answer one | `POST /api/approvals/{id}/{action}` (`action` ∈ `approve`/`reject`) | `handlers/sessions.py::api_approval_resolve` → `state.resolve_approval(id, approved)`; registered `dashboard/server.py:1098`; 400 on a bad action, 404 when unknown/expired |

**DEVIATION from §C2's "answer approval" row, which named
`POST /api/chat/sessions/{session}/approve` (and cited a stale `server.py:667`; it is at `server.py:854`).**
That is the CHAT page's resolver, not this queue's, and wiring it here would have been wrong twice over:

1. It requires a `{session}` path segment and 404s on an unknown session name. The rows
   `GET /api/approvals` serves are written only by `DashboardState.request_approval`, called from
   `gateway.py:495`/`:541` — the gateway tool gate — and those carry `session: ""` for a cron fire or a
   channel-originated call. A phone answering one of those has no session to address.
2. It carries chat-only trust scopes (`trust`, `trust_agent`, `trust_reads`) that persist an agent
   profile's `approval_mode`. A phone approval is a single decision, not a standing grant.

`state.resolve_approval` is the SUPERSET resolver: it answers state-level futures first and then scans
every session's `_approval_futures`, so `POST /api/approvals/{id}/{action}` resolves both origins. The
amendment above already cites this pair (its `server.py:941-942` is likewise stale); §C2's table row is
the outlier and should be read as corrected by this log. Proven by driving it, not by reading it: with
two approvals seeded into a real `DashboardState` on an isolated home, Allow returned the awaited future
`True` and Deny returned `False` — the exact values `gateway.py` consumes to let a tool run or refuse it
— each with a matching `approval_decision` SEL event (`outcome=approved` / `outcome=rejected`).

**DISCOVERY — one renderer, not two.** An approval card already existed
(`pages/chat/ApprovalCard.tsx`). Rather than fork a second one for the phone, its chrome was extracted to
`web/src/ui/ApprovalPrompt.tsx` (warn-tinted shell, the `role=group` name + inner `role=alert`
announcement, tool/argument line, action row) with two densities — `compact` for the chat column,
`roomy` for the phone (full arguments, a metadata block, 44px targets). The chat card now renders it and
keeps only what is genuinely chat's: the transcript segment, the settled-outcome collapse, the risk chip,
the trust vocabulary. Its existing test (`approvalOutcome.test.tsx`, which pins the four scope labels)
passes unchanged, so the extraction is behaviour-preserving. Net effect: a wording or announcement fix to
the permission prompt can no longer land on one surface only.

**DISCOVERY — found by DRIVING, not reading: the optimistic hide had no reconciliation.** Answered rows
were hidden by an id set that was never pruned against the server, so a re-listed id stayed hidden
forever — the live gateway served two pending approvals while the phone read
"Nothing waiting on you". On an approvals surface a silently hidden prompt is a denial the user never
made. The fetched list is now authoritative: every fetch drops the hidden ids whose POST has SETTLED
(ids still in flight stay hidden, which is the whole point of the optimistic hide), so an approval the
server still lists comes back. Pinned by a test.

**Failure honesty.** A first fetch that fails renders the `LoadError` primitive (announced `role=alert`,
retryable) — never the "nothing waiting on you" empty state, which on this surface would be the most
dangerous possible lie. A failed *resolve* restores the card and toasts the gateway's own message. Both
paths are tested against a COLD `sessionStorage` (a warm cache masks the error branch entirely) and both
were falsified: breaking the resolve call reddened 4 tests, removing the error branch reddened 1.

**Deferred, per the atom's own scope:** the plan-43 decision-brief does not exist upstream yet, so the
raw-argument fallback is what ships (the done-when directs exactly this — do not block on plan 43). PWA
manifest/service worker is `MC-4`; push and `#/companion?approval=<id>` are `MC-5`; the
loops/tasks/inbox/notifications sections are `MC-6`.

**Gate:** `make lint` rc 0 · `npm run typecheck:web` clean · full `npx vitest run` 216 files / 2107 tests
passed · `npm run build` ok · full `pytest -q --timeout=600` 19001 passed / 30 skipped / 12 xfailed
(baseline, no python touched). `web/src/design/primitiveAdoption.baseline.json` `rawButton` ratcheted
274 → 272 (the extraction removed the chat card's hand-rolled pill; the other −1 was pre-existing slack
between the baseline and the live count on the parent commit).

**Live validation** (isolated home `/private/tmp/mc3-live/home`, real `start_dashboard`, loopback-only,
never `~/.personalclaw`): 390×844, no nav rail, no horizontal overflow, zero console errors. Six controls,
all named in the accessibility tree and all in the Tab order: `Refresh approvals` (42×42),
`Allow Bash` / `Deny Bash` / `Allow WebFetch` / `Deny WebFetch` (44×104 each — a thumb target),
`Open the full dashboard`. A keyboard-only approve (focus + Enter) resolved the backend future.
