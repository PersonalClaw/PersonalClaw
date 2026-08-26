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
- **Device pairing is plan 54's, not this plan's** (`CA-3` fold, 2026-08-17): the original recon here found `token_auth.py::generate_token(user_id, ttl_seconds, app="")` (`:394`) with `MAX_SESSION_TTL_SECS = 1 year`, a nonce registry + eviction, and the **caveat that mattered** — `bind_ip(token, ip, …)` (`:169`) pins a token to one client IP while a roaming phone changes IPs. That finding was correct and it survives; its **conclusion now lives in COMPANION-APPS §C1** as a transport constraint on the mechanism that plan owns. The bind semantics the bullet asked S2 to verify are now measured: only the `?token=` query-param exchange binds (`:970`) and checks (`:957`); cookie-borne requests skip the IP check entirely (`:954`), so a cookie-borne device session roams for free and **nothing here extends `token_auth.py`**. See §C1 below.
- Approval answer route: `POST /api/chat/sessions/{session}/approve`. Loop controls exist behind the loops handlers (exact routes to be mapped in S2 — the loops pages drive them today). Notifications/inbox APIs per plan 42.
- Remote access today: none documented; auth modes support token URLs (`personalclaw token`).

## Design

- **S1 — remote access first** (valuable standalone): Tailscale-first docs (gateway joins the tailnet; phone joins; token-auth'd dashboard over it — works with `AUTH_MODE=local_token` today), Cloudflare Tunnel alternative, explicit anti-pattern warnings (no raw port-forward; `none`-mode is loopback-forced anyway); `doctor` reachability probe (detect tailnet interface, print the phone-usable URL via `personalclaw token`).
- **S2-3 — PWA tier:** manifest + installability + service worker (app-shell caching only — API responses are never cached: stale approval data is dangerous); a **Companion route** (`#/companion`): approvals front and center (decision-brief cards from plan 43 T3), running loops with pause/nudge/stop, tasks/inbox lists (read + resolve), recent notifications; **web push** where supported (VAPID keys generated locally, subscription stored per device; push payloads content-free: `{kind, item_id}` → the app fetches details over the VPN link on tap) + **ntfy/UnifiedPush** documented as the fully-self-hosted push backbone; `push` becomes a real target in plan 42's rules engine.
- **S4-6 — wrapper tier:** Capacitor shell around the Companion route (store presence + reliable platform push); pairing = the dashboard's QR, which **renders** COMPANION-APPS §C2's `/api/devices/pair/*` (the Devices list + revocation are plan 54's Devices surface — not a second one here); push routed via ntfy apps (first-class) or an opt-in relay (content-free pings; relay code open-source in the org, deployable by anyone — the hosted instance is a convenience, not a dependency); iOS/Android store packaging.

## Contracts & Interfaces (conventions per [AGENTS.md](../../../AGENTS.md))

### C1 — Device session — **DEFINED IN COMPANION-APPS §C1/C2 (plan 54) on REMOTE-USER-AUTH §C1 (plan 53); consumed here**
> **Rev-11 reconciliation (2026-07-26), executed by `CA-3` (2026-08-17):** device sessions and
> pairing are **owned once** by COMPANION-APPS (the connectivity contract) on REMOTE-USER-AUTH's
> durable session store. A device session is an `auth/sessions.json` row with `device`/`issuer`
> set — there is no separate device-token type, no `device` claim on `generate_token`, and no
> `devices.json` registry. **Read plan 54 §C1/C2 for the mechanism; it is not restated here.**
>
> The parallel design this section used to carry is **deleted, not archived** (clean break). Its
> three live constraints were folded into the owner instead of left duplicated:
> * **TTL 30d** → plan 54 §C1's `auth.session_ttl`, whose default IS `30d`
>   (`config/loader.py:4677`) — the same number, one source.
> * **The `bind_ip` roaming pivot** → plan 54 §C1's transport constraint: a device session rides
>   the session **cookie**, which skips the IP check (`token_auth.py:954`); only the `?token=`
>   exchange binds (`:970`) and rejects on mismatch (`:957`). Measured, so the "pivot" needs no
>   decision and no auth change.
> * **`caller="device:<name>"` audit attribution** → plan 54's SEL line, alongside
>   `device_pair_started`/`device_paired`/`device_revoked` (one event vocabulary, not two).
>
> What this plan still owns: the **phone UI + push**. Its device-auth tasks (T2.3/T2.4 below,
> atom `MC-2`) *consume* plan 54 §C1/C2 and touch `token_auth.py` **not at all** — E4 if a task
> appears to need to.

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

### C4 — QR pairing screen (wrapper tier) — **routes DEFINED IN COMPANION-APPS §C2 (plan 54); rendered here**
> **Rev-11 reconciliation (2026-07-26), executed by `CA-3` (2026-08-17):** `POST
> /api/devices/pair/start|complete` are defined **only** in COMPANION-APPS §C2. The route shapes
> that used to be restated here are **deleted** — a second copy of a contract is a second
> contract. What this plan builds is the screen (atom `MC-8`): render a QR of the
> `{pairing_url, code}` that `pair/start` returns, let the app scan it, POST the code to
> `pair/complete`, get a device session (§C1). Single-use + TTL, the `pairing_url` resolution
> rule, the optional `device_name`, and the Tier-S error codes all live in plan 54 §C2; errors
> surface through the §2.2 envelope like everything else.

### Integration points
- **Calls:** COMPANION-APPS §C2's pairing routes (§C1/§C4 — this plan calls them; it never mints a token and never touches the token registry), the existing approval/loop/inbox/notification endpoints (§C2), plan-42 rules engine (`push` target registration), `save_credential` (VAPID), `sel()`.
- **Called by:** the PWA + the Capacitor wrapper (both render the same served `#/companion`).
- **Depends on:** plan 42 (push target must exist), EXTERNAL-ACCESS/VPN for off-LAN reach (docs), CHANNEL (channels are the chat-on-phone answer — this is control-surface only).
- **Storage:** none for device state — device sessions are rows in REMOTE-USER-AUTH's `auth/sessions.json` (plan 54 §C1); VAPID keys in credential store; push subscriptions per device.

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
| T2.3 | Consume plan 54's device session: pair/enroll the phone per COMPANION-APPS §C2 and prove a roaming-IP phone keeps its session (it rides the cookie, which skips the IP check per §C1's transport constraint) — **no new claim, no `token_auth.py` change; E4 if it appears to need one** | companion client code, tests | roaming-IP fixture keeps the device session valid; the diff touches no auth-minting code |
| T2.4 | Devices list: consumed from COMPANION-APPS T1.2 (Settings → Devices — name, kind, minted, last-seen, issuer + revoke). This plan builds **no second list**; it links to that one from the companion surface | companion nav/link only | a revoke performed in plan 54's panel kills the device session on the phone's next request |
| V2 | Validation: from the phone — approve a real tool call, pause/nudge a loop, resolve an inbox item; revoke the device and observe lockout | — | all hold |

### Session 3 — Installability + push (PWA part 2)

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | Manifest (icons from the claw mark, standalone display, start_url `#/companion`) + service worker (app-shell precache ONLY; network-first for everything; explicit no-cache for `/api/`) | `web/public/manifest.webmanifest`, `web/src/sw.ts`, vite wiring | Lighthouse installability passes; API responses never served from cache (test with offline toggle) |
| T3.2 | Web push: VAPID keypair generation (`personalclaw push init` CLI; keys in credential store), subscription endpoint + per-device storage, content-free payload sender wired as plan 42's `push` target | backend push module (new, small), CLI, rules-engine target registration | push arrives on a subscribed phone for an `immediate`+push rule; payload contains ids only (inspect) |
| T3.3 | ntfy/UnifiedPush path: docs + a delivery adapter (POST to user-configured ntfy topic URL, content-free) as an alternative push target | adapter + `docs/guides/remote-access.md` section | self-hosted ntfy receives pings; tap-through opens companion (deep link) |
| V3 | Validation: install to home screen; background push → tap → approval resolved in <30s round-trip on cell data | — | timed + recorded |

### Sessions 4-6 — Wrapper tier (Wave 3)

> **The wrapper tier defines no connectivity of its own** (`CA-9` coordination, 2026-08-25). The
> shell's connection state, its endpoint switching and its reconnect behaviour are COMPANION-APPS
> §C1 + its S3 client contract, written out as the eight-item wrapper list in
> [docs/guides/companion-apps.md](../../guides/companion-apps.md) and shipped as
> `web/src/lib/endpoints.ts` (plan 54 `CA-6`). This plan's S4 tasks *implement that list* on
> Capacitor; they do not restate it, extend it, or hold a second registry shape. The same rule
> that governs pairing (§C1/§C4: read plan 54, mint nothing) governs the endpoint half — E4 if a
> task here appears to need its own connection model.

| ID | Task | Files | Done when |
|---|---|---|---|
| T4.1 | Capacitor shell: wraps the served companion URL, native safe-areas, no forked UI. Connection state is COMPANION-APPS §C1's endpoint registry (`{active, endpoints[]}` per plan 54 S3/T3.3, already shipped in `web/src/lib/endpoints.ts`) — **not** a single stored gateway URL and not a second registry shape; the device session in each entry is the one plan-54 pairing returned | new `mobile/` dir in core repo (or org repo — decision recorded) | shell builds for iOS+Android; renders the live companion from the registry's `active` endpoint |
| T4.2 | QR pairing screen **and endpoint switching**, both consumed from plan 54: Settings → Devices → "Pair phone" renders a QR of COMPANION-APPS §C2's `{pairing_url, code}` → app scans → `pair/complete` → device session (§C1); the paired gateway then becomes a §C1 registry entry (`device_session_ref`) and the switcher is plan 54's S3 spec — re-point `active`, reload the same served SPA, per-endpoint state namespaced by endpoint `id`. **Routes and switch semantics consumed from plan 54, not defined here** | app pairing + switcher screens (the routes are plan 54 T1.1/T1.2; the registry/switch contract is plan 54 T3.1/T3.3) | pair from QR end to end against plan 54's routes; a second paired gateway is switchable from the shell with no state bleed; single-use + TTL, the registry shape and the no-hub rule verified in plan 54, not re-specified here |
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

### 2026-08-15 — `MC-4` (S3 T3.1 manifest + service worker) — **DONE**

The companion is installable and the service worker **cannot** cache an API response.
`web/public/manifest.webmanifest` (`display: standalone`, `start_url` `/#/companion`, `scope: /`,
claw-mark icons) + `web/src/sw.ts`, bundled by `web/scripts/buildServiceWorker.mjs` (esbuild, from a
Vite `closeBundle` hook) to the dist **ROOT** as `sw.js`. Root placement is not tidiness: a worker's
scope is its path, so one emitted into `dist/assets/` could only ever control `/assets/`. New gateway
routes `/manifest.webmanifest` and `/sw.js` plus an `/icons` static mount, and `/icons/` joined
`spa_fallback`'s exclusion tuple.

**§C2's service-worker rule, as implemented.** All policy is one pure module
(`web/src/app/swPolicy.ts`) with `sw.ts` as thin plumbing, so the rule that matters is unit-testable
and cannot drift across the several places a response could enter a cache. `mayCache()` is the ONE
gate — consulted before every read and every write — and `strategyFor()` is defined in terms of it, so
a path cannot be assigned a caching strategy without also being cacheable. The default is
**fail-closed**: an unrecognised path is `network-only`, so a gateway route added later is not
silently cached because nobody deny-listed it. `/api` resolves to `network-only`, which returns from
the fetch handler **without** calling `respondWith` — the browser then performs the fetch itself, so
the response never enters worker JavaScript, a stronger guarantee than `respondWith(fetch(request))`
and one that also leaves streamed uploads alone. Precache is the app shell ONLY; hashed `/assets/*`
are runtime `cache-first` on the strength of being content-addressed.

**DEVIATION — the done-when's instrument no longer exists.** Lighthouse **12** removed the PWA
category and all of its audits. A real 12.8.2 run against this build (`npx lighthouse@12`, system
Chrome, headless) completed rc 0 and reported categories `performance, accessibility, best-practices,
seo` — `installable-manifest`, `service-worker`, `maskable-icon`, `apple-touch-icon`, `splash-screen`
and `themed-omnibox` are all absent from `audits`. So installability is asserted criterion by
criterion in `web/src/app/manifest.test.ts` (including each PNG's real IHDR dimensions against its
declared `sizes`), and in `web/e2e/pwa.spec.ts` against the manifest **as Chrome fetched it** through
CDP `Page.getAppManifest`.

**DISCOVERY — `Page.getInstallabilityErrors` is INERT.** It reads like the perfect replacement for the
removed audit, and it was measured before being trusted: it returns `[]` for `display: "browser"` AND
`[]` for a manifest that is not even valid JSON. An assertion on it can never fail. It was written,
found decorative, and removed. `Page.getAppManifest` was measured the same way and does have teeth
(an invalid manifest yields `Line: 1, column: 3, Syntax error.`), so the rail rests on that.

**DISCOVERY — Chromium keeps the fragment in a cache key but ignores it when matching.** After a
reload the shell slot is keyed `…/#/onboarding`, so `cache.keys()` contains no bare `/`, while
`cache.match('/')` still resolves because the matching algorithm excludes fragments. A key-string
assertion therefore reads as a broken precache when the precache is fine; the spec asserts by lookup.
Two harness bugs of the same family were caught and fixed in the spec itself: a `goto` differing only
in its hash is a SAME-DOCUMENT navigation, so it neither takes control of a client nor fetches
anything — used for the offline step it would have made the vacuity floor itself vacuous.

**Update strategy — no `skipWaiting()`, no `clients.claim()`,** ratcheted by a source assertion rather
than left as a comment (the ratchet strips comments first, with a vacuity floor, because `sw.ts`
documents at length why it does *not* call them and a raw scan matched the prose).

**Security decision — the PWA stays BEHIND auth.** `tests/test_token_auth.py` already carried
`test_retired_pwa_paths_require_auth`, asserting `/sw.js` and friends must NOT bypass the session.
That ratchet was honoured rather than relaxed: nothing was added to `_BYPASS_PREFIXES`/`_BYPASS_EXACT`,
and a new `test_live_pwa_paths_require_auth` locks `/manifest.webmanifest`, `/sw.js` and `/icons/*`
the same way. The cost is real — browsers fetch a manifest with credentials omitted — and is paid by
`crossorigin="use-credentials"` on the link, asserted in `manifest.test.ts` because losing it fails
silently. The handlers also return a 404 **response** rather than raising, since `spa_fallback` turns a
raised `HTTPNotFound` into index.html and HTML served for `/sw.js` fails registration on a MIME check.

**Root-caused, not baselined:** the three new non-`/api` routes reddened
`test_api_manifest_drift.py::test_every_non_api_route_is_excluded` AND
`test_agent_reference.py::test_checked_in_reference_matches_a_fresh_render` — the offline reference is
rendered from the manifest, so one omission surfaced twice. Fixed by adding `/icons`,
`/manifest.webmanifest` and `/sw.js` to `MANIFEST_EXCLUDE` with reasons; no generated file was bumped.

**Falsification** (every mutation restored): `/api/approvals` into the precache list → 4 red, incl.
`mayCache` still refusing it, which is the defence-in-depth working; `display: browser` → installable-
display red, and Chrome's own `data` showed `"display": "browser"`; `skipWaiting()` added → update-
strategy rail red; `crossorigin` removed → red; a 192-declared icon pointed at the 512 raster →
`expected { width: 512, height: 512 } to deeply equal { width: 192, height: 192 }`; and the behavioural
one — API guard removed from `mayCache` + rebuild → red at three independent layers, ending with
`offline /api resolved instead of failing: {"ok":true,"status":200,"body":"{\"call\":1,\"secret\":\"payload-1\"}"}`.

**Gate:** `make lint` rc 0 (black/isort/flake8 clean, mypy 871 files) · `npm run typecheck --workspace
web` clean (now two programs — `src/sw.ts` needs the `webworker` lib, which cannot share a program
with `dom`, so it has `tsconfig.sw.json`) · full `npm test --workspace web` **267 files / 2665 tests
passed** (up from 264 / 2625) · `npm run build --workspace web` rc 0 · `web/e2e/pwa.spec.ts` 2 passed ·
`pytest` on the affected + required files (`test_pwa_file_symlink`, `test_token_auth`,
`test_frontend_dist_resolve`, `test_api_manifest_drift`, `test_agent_reference`, `test_auth_exposure`)
**186 passed**.

**Known constraint, documented not hidden:** service workers need a secure context, so a gateway
reached over plain http at a LAN address gets no install and no offline. `registerServiceWorker()`
prints one line naming that reason instead of leaving an install affordance that never appears.

### 2026-08-25 — `MC-2` (S2 T2.3 + T2.4) — **DONE**, and it was a rail job, not a build job

**The producer already had live consumers.** The decisive check first, because a shipped mechanism
nothing calls is this repo's recurring defect and it greps identically to a wired one:
`register_device_routes` is IMPORTED **and CALLED** at `dashboard/server.py:411-413`, registering all
four C2 routes plus `GET /pair` (`handlers/devices.py:496-505`); `api.ts:3832-3837`'s three client
methods are called from `web/src/pages/settings/DevicesPanel.tsx:87/118/138`; and that panel is
mounted as a real tab at `SettingsPage.tsx:83`. `touch_device_last_seen` — the writer behind the
`last_seen` column, the field most likely to be a reader-of-an-unwritten-key — is called from
`token_auth.py:221` on both authorized paths. So MC-2's UI clause was already MET by `CA-2` (merged
2026-08-21), including `minted`: `DevicesPanel.tsx:318` renders `Paired {relPast(d.minted_at)}` and
`devicesPanel.test.tsx:90` asserts `/^Paired \d+[mhd] ago/`. **No second list was built** (T2.4's
"this plan builds no second list").

**What was genuinely missing was the two clauses a PHONE depends on — both of which live in the auth
middleware, not in the store `CA-2`'s tests exercise.** New file
`tests/test_mc2_device_session_consumption.py` (5 tests) drives the real
`token_auth.token_auth_middleware` over a real `TestServer`, with `aiohttp.DummyCookieJar` so a
credential travels only when a leg names it — no leg can pass by inheriting an earlier cookie.

- **T2.3 roaming IP.** Pair a device, then hit a protected route with its cookie from `203.0.113.7`
  and again from `198.51.100.22`: both 200, same `session_nonce`, one device still in the registry.
  The **vacuity floor** is a separate test that primes the *same credential* through `?token=` and
  gets `403 {"error": "IP mismatch"}` from the moved address — so "the phone still gets in" cannot
  read as a pass on a build whose IP binding was deleted. This closes the gap `CA-3`'s log filed as
  "worth an atom of its own": the constraint is free today and would break silently.
- **T2.4 revocation.** `CA-2` asserts revoke through `validate_token`; a phone never calls
  `validate_token`, it makes a request, and between the two sit the bypass lists, the cookie branch
  and the adopt-from-store path. Now asserted as a request: device in (200) → owner revokes (200,
  `revoked: 1`) → **the very next cookie-borne request is 403**, `GET /api/devices` is `[]`, and it
  stays 403 after `clear_all()` + `reset_secret_cache()`.
- **"No new claim added to `token_auth.py`"** is ratcheted as a shape rather than as a point-in-time
  grep: `generate_token`'s parameters are exactly `(user_id, ttl_seconds, app)`, the minted payload's
  claim set is exactly `{sub, exp, session_exp, iat, nonce}`, the `device_id` appears nowhere in it,
  and the device identity is asserted to live in the session-store row instead. **`token_auth.py` and
  `session_store.py` are untouched by this diff** (`git diff --stat` = one new test file).

**Falsification** (each mutation grepped back to confirm it applied, then restored from a file copy
at the literal path; tree verified clean after):
1. `token_auth.py:1072` `if not from_cookie and not check_token_ip(...)` → `if not check_token_ip(...)`
   — i.e. bind cookie sessions too, the exact future regression. `test_a_device_session_roams_between_client_ips`
   red: *"a device session must ride the cookie, not the address"*; the other 4 stayed green.
2. `handlers/devices.py:488-490` dropped `revoke_nonce(nonce)`, keeping `forget_session(nonce)` —
   `test_revoke_refuses_the_devices_next_http_request` red with `assert 200 == 403` while
   `test_a_revoked_device_stays_refused_across_a_restart` **PASSED**, which is the point: the two legs
   measure different halves (in-memory vs durable), so neither is redundant.

**Gate:** `make lint` rc 0 (black 2059 files, isort, flake8, mypy 1012 files) · `pytest --no-cov` on
`test_mc2_device_session_consumption` + `test_device_pairing` + `test_session_store` +
`test_token_auth` **207 passed** (was 202) · `scripts/gate_report.py` **6/6 PASS** · probe sweep 16
repo-wide with **0 diff-introduced**, `git status --porcelain` empty. **The web legs were not run and
did not need to be: the diff contains zero `web/` files.** Clause 2's evidence is the merged
`devicesPanel.test.tsx` plus `CA-2`'s recorded LAN drive, not a re-run here.

**Unmet, and deliberately left:** T2.4's *Files* column also asks for a "companion nav/link only" —
a link from `#/companion` to Settings → Devices, so the phone can reach the one list instead of
growing a second. It is **not** part of `MC-2`'s `done_when` (all four clauses above are met without
it), it lives in `web/src/pages/companion/CompanionPage.tsx` which was outside this session's fence,
and it belongs with the surface that adds the companion's other sections (`MC-6`). Recorded here so
it is not lost. Separately: `role="list"` was NOT added to the Devices rows — the list is named by
its `Section` `<h2>` ("Paired devices (N)"), which is the house idiom, and a census found `role="list"`
in **0 of the ~60 settings panels** (only `ui/WindowedList.tsx`), so adding it would invent a pattern
rather than match one.
