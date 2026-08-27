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

### 2026-08-26 — `MC-6` (S3.5 T3.5.1) — **PARTIAL**: the four sections are DONE, the SW sound/badge mapping is not

**Clause 1 — the loops/tasks/inbox/notifications sections — landed.** `#/companion` is now the
whole attention path in one column: **Approvals → Running → Tasks → Inbox → Recent**, in that
order, asserted by test. The order is the priority order and is not cosmetic — a blocked run is
the only row on this page another person is waiting on, so it stays at the top however much the
companion grows. `MC-3`'s "Not on the phone yet" stub list is **deleted**, not hidden behind a
flag, and the test that asserted its presence was replaced (not weakened — its subject no longer
exists) by one asserting approvals stay first.

**RESOLVED ROUTE MAP (T2.2, the half `MC-3` deferred).** Every action is the owning surface's own
route. Nothing was invented, reshaped, or wrapped — `PP-16` is unifying Loops into WorkflowRuns
concurrently, so the loop API is consumed strictly read-only.

| Action | Endpoint wired | Client |
|---|---|---|
| list steerable loops | `GET /api/loops` | `api.uLoops()`, filtered to `running/paused/needs_input/stagnant/blocked` |
| pause / resume / stop | `PATCH /api/loops/{id}` `{action}` | `api.uLoopAction(id, action)` |
| nudge | `POST /api/loops/{id}/nudge` `{text}` | `api.uLoopNudge(id, text)` |
| list open tasks | `GET /api/tasks?status=…&limit=20`, **twice** | `api.tasks({status})` per open status |
| task state transition | `PUT /api/tasks/{id}` `{status}` | `api.updateTask` |
| list pending inbox | `GET /api/inbox/pending` | `api.inboxPending()` |
| inbox resolve | `PUT /api/inbox/{id}` `{status: handled\|dismissed}` | `api.updateInboxItem` — plan 42's own lifecycle vocabulary (`PENDING → SEEN → HANDLED \| DISMISSED`); no second notion of "dealt with" was minted |
| recent notifications | `GET /api/notifications` | `api.notifications()` |
| mark one read | `POST /api/notifications/ack` `{ts}` | `api.ackNotification` — keyed on `ts` because the log has no id and every ack/unack/delete route takes the timestamp |

**Tasks is read TWICE on purpose.** `GET /api/tasks` takes a single `status` plus a `limit`, so one
unfiltered read lets a project's DONE history fill the window — and the phone would then say "no
open tasks" while open tasks existed. Both legs share one query key, so a failure in either paints
ONE `LoadError`, not two.

**DISCOVERY — the optimistic contract is TWO mechanisms, and the first draft could not tell them
apart.** T2.2 asks for "optimistic UI reverts on failure". `useCompanionAction` owns both halves
once, rather than four sections re-deriving them:

1. **REVERT on failure** — the patch is withdrawn immediately and the gateway's own sentence is
   toasted.
2. **RECONCILE against the server** — on every fetch, every patch whose POST has SETTLED is
   dropped, so the fetched list is authoritative. This is `MC-3`'s trap generalized: its
   optimistic hide was never pruned, so a queue the backend was still serving rendered as
   "nothing waiting on you". Four more sections of the same shape is four more chances to remake
   it.

Then the measurement that changed the test file: **deleting the revert entirely left all 33 tests
GREEN.** The post-action collection bust triggers a refetch and the reconcile drops the patch
anyway, so the three "REVERTS" tests only proved that *one of the two* worked. The revert's real
job is LATENCY — on cell data the refetch is exactly what is slow, and until it lands a withdrawn
action must not sit on screen looking like it succeeded. So a fourth test holds the refetch open
(`data` unchanged ⇒ the reconcile cannot fire) and asserts the row is already back, with a
vacuity assertion that the refetch is genuinely still in flight.

**DISCOVERY — a reader-shaped cache key, and a rail that a constant hides from.** The four keys
were first written `companion:loops|tasks|inbox|notifications`, which is exactly the defect
`web/src/lib/splitCollectionBusts.test.ts` exists for: a key named after its READER sits in a
namespace the collection's own invalidation can never reach. `#/tasks` busts with
`invalidateKeys('tasks', true)`, so `companion:tasks` would have been dropped by nothing, ever.
Two consequences were fixed, not one:

* The keys became `loops-companion` / `tasks-companion` / `inbox-companion` /
  `notifications-companion` — hyphen-suffixed so they sit in the COLLECTION's namespace (the same
  shape as the existing `tasks-all`), each declared in `lib/data/keys.ts` with the policy of the
  collection it projects.
* Every action busts the **collection prefix** instead of calling its own `refresh()`. That is
  the direction that actually matters: a task finished on the phone must staleten `#/tasks` and
  the dependency picker's `persist: true` copy, or the next desktop mount repaints the row as
  still open. Nothing tested that, so a test now writes sibling keys, drives the action, and
  asserts both are dropped while an unrelated collection survives (the vacuity floor).

🪤 **And the near-miss worth recording: hoisting those keys to module constants made the rail stop
seeing them.** That census matches a LITERAL first argument to `useQuery`, so with the keys behind
`TASKS_KEY` etc., reverting one to `companion:tasks` left the whole suite **green** — the "fix"
would have consisted of hiding from the rail that caught the mistake. The literals are written
inline at each call site for that reason, with the reason in the source.

**Gate:** `make lint` rc 0 (black/isort/flake8 clean, mypy 1043 files) · `npm run typecheck:web`
clean (app + `tsconfig.sw.json`) · full `npm run test:web` **506 files / 5422 tests passed** ·
`npm run build` rc 0 · `scripts/gate_report.py` **6/6 PASS** · `tests/test_structural_baseline.py`
**31 passed** · `config/loader.py` **5900 lines, unchanged** (no config field: the per-kind sound
belongs in `entity_settings/notification_rules.json`, and that clause is deferred anyway).
Zero Python files changed. Probe sweep: 0 diff-introduced hits.

Four house rails caught real work and were satisfied rather than exempted: the split-collection
census (above), `ui/disabledReason*` (the Send-nudge button was disabled with no stated reason),
`pages/emptyStateRollout` (the new file needed a PEP-2 verdict — `derived`, because all four
sections are projections of collections owned by `#/loops`, `#/tasks`, `#/inbox`, `#/notifications`
and the on-ramps belong to those surfaces), and `lib/data/dataLayerAdoption` §2 (every namespace
declared).

**Clause 2 — the SW per-kind sound/badge mapping — UNMET, and it is a dependency gate, not a code
gap.** The done-when's call site is `MC-5`'s: `web/src/sw.ts`'s `push` handler and
`web/src/app/pushPolicy.ts`'s `notificationFor()`, which compose the whole notification from the
payload's `kind`. Both exist only on the unmerged `feature-mc5-push-approval` (`dbc96b5f`) and are
**absent at this atom's branch point** (`908c7ed1`). Building them here would ship a second push
handler and a second notification composer into the same file `MC-5` already edits — the duplicate
mechanism the tenets forbid — and adding the rules field with no reader would ship an inert
control. So neither half was started. Two measured findings for whoever executes it:

* **plan 42's rules field has no `sound` key.** `notification_rules.Rule` is
  `mode`/`targets`/`conditions`/`verify`; `MC-5` extended that module (`ensure_target`) without
  adding one. So the `EXT:` dep is genuinely unbuilt upstream and clause 2 owes the field itself
  — dataclass + `_coerce_rule` + `rules_document()` + the `PUT /api/notifications/rules` guard +
  a control in `NotificationRulesMatrix.tsx`. It is `entity_settings/notification_rules.json`
  state, **not** `config.json`, so the config round-trip contract does not apply and
  `config/loader.py` does not grow.
* **the sound vocabulary already exists and must be consumed, not invented.**
  `web/src/design/soundCues.ts` is a closed set of synthesised earcons (zero audio assets, ratcheted
  by `design/noAudioAssets.test.ts`) with cue POINTS split from VOICES precisely so a caller can
  re-voice one. A per-kind rule should name a voice from that set. Note the platform constraint:
  a service worker cannot play audio, so "a distinct sound fires" means `silent`/`vibrate` on the
  notification plus a message to an open client that plays the voice — worth stating in the atom
  rather than discovering late.

### 2026-08-26 — `MC-8` (S4 T4.2, QR half) — **PARTIAL**: the screen ships and its payload is proven scannable; the shell leg is device-gated

**Premise check first, and it moved the scope.** The pairing routes were already there and already
consumed: `POST /api/devices/pair/start|complete` at `handlers/devices.py:159/228`, registered at
`:498-505` together with the `GET /pair` door the URL points at, and `api.ts::devicePairStart` called
from `DevicesPanel.tsx`. So `MC-8` invented nothing. What was missing was exactly one thing, and
`CA-2` had recorded it twice as a deliberate gap — 2026-08-19 and again 2026-08-21:

> **"shows a QR" is still UNMET for the IMAGE.** … `qrcode`, `segno` and `pyqrcode` are all absent
> from the Python side … adding one is a dependency decision, and hand-rolling Reed-Solomon +
> masking to render a *wrong* QR would be worse than the labelled placeholder.

That is an owner call, and `MC-8` is where it gets made. **The call: encode it in the repo, and
answer the "wrong QR" risk with a decoder instead of with confidence.** No dependency was added to
either ecosystem. The reasoning is not thrift — the npm packages that do this arrive with a
transitive tree (image writers, arg parsers, terminal renderers) for a product whose posture is a
legible supply chain, and the requirement is one pure function over a fixed published specification
that cannot drift out from under us.

**What shipped.** `web/src/lib/qr.ts` (444 lines, half of it the reasoning) encodes byte mode at error-correction level M,
versions 1-40 — one mode and one level, so there is no `ecl` argument for a call site to get wrong.
`web/src/pages/settings/PairingQr.tsx` renders the matrix as ONE `<path>` of 1×1 squares inside a
`viewBox` that includes the mandatory 4-module quiet zone, on a fixed light plate: **contrast
polarity is part of the barcode format, not a surface style**, so this is the one thing on the page
that deliberately does not follow the colour scheme. `DevicesPanel.tsx` replaces its labelled
placeholder with it and keeps the code and the link beside it, because a camera that will not focus
must not be the only way in.

**Proof that the symbol is a symbol, in three independent layers** (`web/src/lib/qr.test.ts`, 13
tests) — none of which re-reads the encoder:

1. **A DECODER.** `readBack()` recovers the payload from the DRAWN MATRIX with its own function-module
   map (built from a literal alignment-position table, *not* from `qr.ts`, so the two cannot agree
   about a wrong layout), its own un-mask, zigzag walk, de-interleave and byte-mode parse. Asserted
   over 11 payload sizes chosen to cross every structural boundary in versions 1-10 — no alignment
   pattern (v1), two blocks (v4-5), four blocks and the arrival of the version field (v6-8), five
   blocks with UNEQUAL lengths and the 16-bit character count (v9-10) — plus a non-ASCII host name,
   because the payload is UTF-8 bytes and not characters.
2. **A SYNDROME CHECK** on the codewords that decoder pulled back out: every block must evaluate to
   zero at α^0…α^(n-1). A wrong remainder, a mis-split block or a mis-ordered interleave cannot
   satisfy that by accident.
3. **BCH by division, not by comparison.** Each of the 8 format fields is checked to divide by 0x537
   with level-M data bits, and all 34 version fields by 0x1F25 — brute-forced against every legal
   alternative, so nothing passes by being compared to a copy of itself. The second format copy is
   asserted identical to the first.

**And then the leg that actually settles it: the OPERATING SYSTEM read it.** The rendered symbol was
rasterized to PNG and handed to `VNDetectBarcodesRequest` — Apple's Vision barcode detector, the
same decoder an iPhone camera runs. Three symbols, three exact payloads returned, including a
64-character host name (v6) and a 213-byte payload (v10).

**Then the exchange, end to end, against a REAL gateway** (isolated `PERSONALCLAW_HOME`, removed
after; the real `~/.personalclaw` was confirmed untouched):

| Leg | Result |
|---|---|
| `POST /api/devices/pair/start` as the owner | `{"code": "UD33-49ET", "pairing_url": "http://127.0.0.1:56515/pair?code=UD33-49ET", "expires_in": 300}` |
| encode that URL → PNG → **Vision decoder** | `http://127.0.0.1:56515/pair?code=UD33-49ET` — byte-identical |
| `GET /pair?code=…` with NO session (what the scan opens) | `200`, the redeem form |
| `POST /api/devices/pair/complete` with the code **read off the scan** | `200`, `device_id fddcb13ef94e108f`, `Set-Cookie pc_token_…` |
| `GET /api/devices` as the owner | one row, `issuer: "pair"` |
| the same scanned code, again | `401 device_pair_code_invalid` — single-use, verified through the scanned payload |
| `GET /pair` holding a session (already paired) | `302 → /`, so a self-pair cannot silently overwrite the owner's own session |
| the plaintext code in `gateway.log` | **0 occurrences** |

**Driven as a user, in the real dashboard.** The gateway was started from THIS worktree's code
(`PYTHONPATH` at the worktree `src`, `src/personalclaw/static/dist → web/dist`, isolated
`PERSONALCLAW_HOME`, removed after — the first attempt silently served the MAIN checkout's SPA
through the shared `.venv`, which rendered the OLD placeholder and is exactly how a frontend change
gets validated against someone else's bundle). Settings → Devices → **Pair a device**:

* The QR renders at **176×176** beside the code and the link, and **the browser's rendered pixels
  were handed back to the OS decoder** — `VNBarcodeSymbologyQR http://127.0.0.1:10847/pair?code=3PAS-ZL6Y`,
  the exact string the route had just minted. Not the unit test's matrix: the screenshot.
* **Expiry, driven.** With the clock pushed past the deadline, the QR became "Nothing left to scan.",
  the code and the link were gone, and the row read "This code has expired — generate another." Three
  places, three different sentences, nothing redeemable left on screen.
* **Polarity is scheme-independent, measured rather than argued.** `getComputedStyle` on the plate and
  the module path: `rgb(255,255,255)` / `rgb(0,0,0)` under `data-mode="dark"` **and** under
  `data-mode="light"`. That is the point of not using tokens here — a QR that inverted with the theme
  would stop being a QR for any scanner that does not try both polarities.
* **Zero console errors and zero page errors** across the run.

**The security half, which is a change in behaviour and not just an image.** An expired payload is now
**withdrawn at the presenting end**, not dimmed: the QR, the code, the link and both copy buttons all
go, replaced by a sentence saying the gateway refuses an expired code. Leaving them on screen hands
the owner a string guaranteed to fail on the far device, where the failure reads as "pairing is
broken" rather than "that code ran out". Two other refusals are legible for the same reason a blank
square is not: `pairing_url: ""` (the gateway could not resolve its own address — the code still
works, so it stays) and a payload too long for version 40. The **vacuity leg** for all three is the
same component drawing a real symbol on the accepting path, asserted in `devicesPanel.test.tsx`.

**DISCOVERY — the QR payload assertion was pointed at a URL the gateway cannot emit.**
`devicesPanel.test.tsx`'s fixture read `http://…/#/pair?code=…`. The gateway composes
`{base}/pair?code=…` (`_pair_base_url`, `handlers/devices.py:182`), and the `#` form would not work
if it did: `/pair` is a standalone document whose script reads the code out of `location.search`, and
a code parked behind a `#` lands in the fragment. Corrected — a fixture that cannot happen is a QR
payload nobody ever checked.

**DISCOVERY — "TTL 5min verified" was verified by a tautology.** The only assertion on the window was
`data["expires_in"] == pairing.PAIR_CODE_TTL_SECS`, which holds for any value the constant has,
including an hour. A pairing code's whole security argument is the SIZE of the window (32^8 over
300s, ≤5 live), so the number is the property. `test_the_pairing_ttl_is_five_minutes_and_the_deadline_honours_it`
now pins 300 **and** that the minted deadline is actually that far out.

**DISCOVERY — a global census rail false-positived, and its author had asked for exactly this case.**
`pages/knowledge/graphMarkContrast.test.ts` enrols any `.tsx` emitting a node mark *and* an edge mark,
and its own note said: *"If a future icon-bearing file false-positives, the failure names the file and
the condition can be added then, against a real case."* `PairingQr.tsx` is that case — a `<rect>`
plate plus a `<path>` of modules is node∧edge to a text scan and a graph to nobody, and SC 1.4.11 has
nothing to say about a monochrome barcode whose polarity is fixed by the format. **Not exempted:** the
census now requires the marks to be emitted ONE PER DATUM (inside a `.map(` body), which is what
separates a graph from a bitmap and which all three hand-verified graphs satisfy
(`graph.edges.map → <line>`, `nodes.map → <circle>`/`<rect>`). The new condition carries its own
**load-bearing proof** — a test that shows the barcode satisfies the OLD signal and has no iteration
at all, so the knob cannot become decoration that silently excludes a real graph. **This rail is the
one file in the diff a design-system session might also be holding.** Note this was invisible to every
path-scoped run and only reds unscoped.

**Falsification** (each mutation `git grep`-ed back to confirm it APPLIED — a no-op mutation proves
nothing — then the red observed, then restored from a file copy at the literal path, never
`git checkout`):

1. `lib/qr.ts:414` `push(0b0100, 4)` → `push(0b0010, 4)`: declare alphanumeric mode while emitting
   bytes. This is the encoder bug that still draws a plausible, well-formed symbol, so no amount of
   looking at it helps. **3 of 13 red**, all three round-trip tests, on the DECODER:
   `expected '<mode 2>' to be 'http://192.168.1.5:10000/pair?code=…'`.
2. `lib/qr.ts:112` `if (j + 1 < degree) result[j] ^= result[j + 1]` DELETED from `rsDivisor`: a wrong
   generator polynomial, so every ECC byte is wrong and every module is still drawn. **3 of 13 red**,
   and this is the pair that matters: the failing assertion is
   `every block must be a valid RS codeword: expected false to be true`, which sits AFTER the text
   assertion in each test — so the payload round-tripped fine and only the syndrome layer could see
   it. Mutations 1 and 2 fail on disjoint layers in both directions, which is the evidence that
   neither is redundant.
3. `PairingQr.tsx:49` `if (expired)` → `if (false && expired)`: present a scannable dead payload.
   **1 of 21 red** in `devicesPanel.test.tsx` — `expected SVGSVGElement to be null`, i.e. the QR came
   back — while the accepting-path test stayed green, so the guard distinguishes the two states
   rather than hiding the surface.
4. `DevicesPanel.tsx:206` `{expired ? (` → `{false && expired ? (`: the OTHER half of the withdrawal,
   which lives in the panel and not in the component. **1 of 21 red**:
   `the code is gone: expected <code …> to be null`. Two mutations because the withdrawal is two
   decisions in two files, and mutation 3 leaves this one passing.
5. `DevicesPanel.tsx:200` `url={pairing.pairing_url}` → `url={pairing.code}`: encode the bare
   eight-character code, which a phone camera resolves to a string with nowhere to go — and which is
   indistinguishable from correct on a screenshot. **2 of 21 red**: the payload assertion
   (`the QR encodes the pairing URL`, with its `not.toBe(qrPath(encodeQr(code)))` counter-leg holding,
   so the assertion is on the payload and not on "some path was drawn") AND the no-address test, since
   a code-encoding QR keeps drawing when `pairing_url` is empty.
6. **The rail I sharpened, falsified as a rail.** `KnowledgeGraph.tsx:400`'s node
   `stroke={active ? 'var(--color-primary)' : 'var(--color-on-surface-low)'}` → two CSS colour names.
   **1 of 20 red** in `graphMarkContrast.test.ts`:
   `the entity mark still names the neutral outright: expected +0 to be 1`. So the per-datum condition
   did not stop a real graph from being enrolled or its clauses from firing — the guard still guards.

**Gate** (from the worktree root, `PERSONALCLAW_HOME` unset): `make lint` rc 0 (black 2145 files,
isort, flake8, mypy 1059 files) · `pytest --no-cov tests/test_device_pairing.py
tests/test_structural_baseline.py tests/test_mc2_device_session_consumption.py
tests/test_companion_single_pairing_mechanism.py` **108 passed** (was 107; +1 is the TTL rail) ·
`scripts/gate_report.py` **6/6 PASS** · `npm ci` + `npm run typecheck:web` clean + **UNSCOPED**
`npm run test:web` **506 files / 5418 tests passed** (+2 from this atom; the unscoped run is what
caught the census false positive below — every path-scoped run stayed green) ·
`npm run build` clean. `config/loader.py` is **5900 lines, unchanged by this diff** — no config field
was needed. `docs/design/consistency-audit.json` regenerates on a suite run and was reverted, not
committed. No new route, so `src/personalclaw/reference/routes.md` is unchanged.

**PARTIAL — the one leg a human must do, and it is the plan's recorded environment gate.** The
`done_when` says *"the shell scans and exchanges it for a device session end to end"*. **The shell
does not exist yet** — that is `MC-7` (the Capacitor wrapper), still `todo` — and a camera scan needs
a second physical device, which is the same gate `MC-2`/`CA-2` already carry. What was driven is
everything one machine allows, with the scan performed by the platform's real barcode decoder rather
than simulated. **To close the clause, the owner needs to:** open `Settings → Devices → Pair a
device` on the desktop dashboard bound to a LAN address (not loopback — `_pair_base_url` returns
whatever `Host` it was reached on, so a loopback dashboard hands out a `127.0.0.1` URL the phone
cannot resolve), point a phone camera at the QR, and confirm the phone lands on `/pair`, pairs, and
appears in the Devices list. Nothing in the code path is waiting on that; it is the physical
confirmation of a path already proven leg by leg.

### 2026-08-27 — `MC-7` (S4 T4.1) — **PARTIAL**: the shell is complete; both native builds are environment-gated

`mobile/` now exists and wraps the **served** `#/companion` route. Every clause of the `done_when`
is met except `builds for iOS+Android and renders the live companion`, which cannot be satisfied on
this machine by any amount of work — see the gate below. Row marked 🟡.

**Repo-location decision: the shell lives in the CORE repo, at `mobile/`.** The companion is a
served route of *this* gateway, and the shell is a thin wrapper over that route's URL — so a
separate repository would put a release boundary between a URL and the thing serving it. The
companion route, the pairing routes (`/pair`, `/api/devices/pair/*`), the session-cookie contract
and the endpoint registry all live here and move together; splitting would mean a cross-repo
version matrix for a change as small as renaming a hash route, and the first drift would be silent
because nothing would build both sides at once. Concretely it earns the place three ways a sibling
repo could not: `tests/test_mobile_shell.py` holds the shell's route string against the route
`web/src/app/App.tsx` registers and its registry vocabulary against `web/src/lib/endpoints.ts`;
`tests/test_ci_tier_enforcement.py` forces the tier to stay wired; and one root `npm ci` installs it.

**How "no forked UI" is actually enforced, not just intended.** `mobile/www/` is a bootstrap screen
and nothing else — one address field. The shell computes `<origin>/#/companion` and calls
`location.replace`. Four rails in `tests/test_mobile_shell.py` keep it that way: no `.tsx/.jsx/.vue`
file may be tracked under `mobile/`, no file there may import `web/src` (which would also break the
app outright — `www/` is copied verbatim into the native project with no bundler), the shell's
`COMPANION_ROUTE` must equal a route `App.tsx` registers, and `server.url`/`server.hostname` must
stay **absent** from `capacitor.config.json`. That last one is the real design constraint: Capacitor's
usual "wrap a remote site" recipe bakes the URL at build time, which for per-owner private addresses
means one store build per owner. The bootstrap document exists only to make the address runtime state.

**DISCOVERY — `web/src/lib/endpoints.ts` had ZERO production importers, and this atom is its first
consumer.** That module declares the companion-shell endpoint registry (`companion:endpoints`,
`{active, endpoints[]}`, rows `{id, label, base_url, kind, device_session_ref}`) and names its
consumers explicitly: *"what desktop (T4.1) and mobile import so that neither re-decides the key
format — two shells that disagree about the format are two shells that cannot share a registry."*
`grep` for importers outside its own test found **nothing**, so the format was declared and
unconsumed — exactly the state in which a second shell quietly invents its own key. The first draft
of this shell did precisely that (`personalclaw.gatewayUrl` in `localStorage`); it was rewritten onto
the declared contract before commit. `mobile/www/shell/registry.mjs` is a **parity rail, not an
import**: `endpoints.ts` is TypeScript inside the `web` Vite bundle and the bootstrap has no build
step at all, so the two cannot share code any more than `desktop/`'s modules can import core's
Python — and this repo's existing answer to that shape is a vocabulary rail
(`tests/test_desktop_seam.py`). The rail asserts the storage key, both field vocabularies and the
id alphabet/prefix still match `endpoints.ts` character for character. The N-gateway switcher,
per-endpoint storage namespacing and `endpointSocketUrl` are deliberately **not** reimplemented;
whichever atom grows the shell to N gateways should do it by importing `endpoints.ts` through a real
build.

**The device session needed no new contract, and the shell holds no credential.**
`POST /api/devices/pair/complete` answers with an httponly `Set-Cookie` (`pc_token_{port}`,
SameSite=Lax, `token_auth.py:1130`), so the session lives in the WebView's own cookie jar where
script cannot read it. The shell therefore does **not** redeem a scanned code itself — it hands the
WebView to the gateway's own `/pair` page so the exchange happens in the jar the companion will read
from. A native redemption would hold a session the WebView could not use *and* would be a second
device-session mechanism beside the one on `main`. A rail asserts no `fetch(... pair/complete ...)`
exists under `mobile/` (matching the call, not the string — the docstrings discuss the route on
purpose). This also confirms `endpoints.ts`'s note that the URL carries no credential and the
`?token=` query parameter stays forbidden.

**Finding for `MC-8`: `device_session_ref` is not fillable by the shell.** The field wants the nonce
naming a `sessions.json` device row, but `pair/complete`'s response body returns `device_id`, `name`,
`kind`, `expires_in` — not the nonce — and the mapping (`nonces_for_device`) is server-side only.
Since the shell hands redemption to the served `/pair` page, it never sees that body either. Rows
this shell creates carry `device_session_ref: ''`, and it is never overwritten on a row found. Filling
it needs either a route that returns the nonce for the *calling* session or a served-page write.

**Safe areas are two mechanisms, because there are two documents.** Measured first: `web/src` contains
**zero** `env(safe-area-inset-*)` and `web/index.html` has no `viewport-fit=cover`, so nothing in the
served document insets itself. So (1) the **served companion** is inset natively, by
`android.adjustMarginsForEdgeToEdge: "force"` (Android draws edge-to-edge from API 35) and
`ios.contentInset: "always"`; and (2) the **bootstrap screen** — the shell's own document, and the only
one a CI test can observe — resolves the four `env()` values into `--pc-safe-*` in `shell.css` (script
cannot read `env()` directly) and `safeArea.mjs` reads them back and writes them onto the layout
element as padding, re-applying on rotation and resize. That split is what makes it falsifiable rather
than declarative. A rail also fails loudly if `web/src` ever grows its own inset handling, since the
native keys would then be double-insetting. **Neither native key can be proven by any test in this
repo** — they are read by native code.

**Navigation is fenced to the private network, not `*`.** `server.allowNavigation` lists localhost,
`*.local`, RFC1918 and `*.ts.net`; Capacitor keeps navigation to those hosts in the WebView and kicks
everything else to the system browser, so a link in rendered content cannot steer the app onto an
arbitrary origin wearing the app's chrome. The 172 block is spelled out one octet at a time because a
glob cannot say "16 through 31" and `172.*` would hand the shell most of a public /8; Tailscale is
matched by MagicDNS name rather than its `100.64/10` CGNAT range for the same reason. The list is
duplicated between `network.mjs` (which the shell validates against) and `capacitor.config.json` (which
native code reads) — necessarily, since one is JS and one is native config — so a rail asserts set
equality, and a public reverse-proxy host has to be added to both, deliberately, in a diff.

**BLOCKED clause — what a human must install and run.** Measured on the development host:
`xcodebuild` is a Command Line Tools stub (`xcode-select -p` → `/Library/Developer/CommandLineTools`,
no `/Applications/Xcode*.app`), `pod` is absent, and there is no Android SDK (`ANDROID_HOME` unset,
no `~/Library/Android/sdk`, no `sdkmanager`, no `gradle`). So **neither** native build is runnable
here, and neither is a CI tier — a rail asserts `cap build` never appears in `ci.yml`, because putting
it there would need Xcode and the Android SDK on a runner. To close the clause:

1. Install Xcode (full) + CocoaPods, and the Android SDK with `ANDROID_HOME` set (JDK 17+).
2. From the repo root: `npm ci`, then `npm run add:ios --workspace mobile` and
   `npm run add:android --workspace mobile` (these generate the gitignored `mobile/ios` and
   `mobile/android` template projects), then `npm run sync --workspace mobile`.
3. `npm run open:ios --workspace mobile` / `open:android`, and Run on a device or simulator.
4. Start a gateway on the LAN (`make serve` binds `0.0.0.0`), read its address off
   `personalclaw status`, type it into the bootstrap screen, and confirm the live companion renders
   **inside** the safe area on a notched device — that last part is the half no rail can assert.

**Footprint, stated rather than imposed.** `mobile/` is a third npm workspace member, so the root
`package-lock.json` grew by **575 lines / 93 packages / 27 MB / ~12 s** of `npm ci` — all of it
`@capacitor/{core,cli,ios,android}` and the CLI's Ionic / `native-run` / `xml2js` dependencies. The
104 deleted lockfile lines are pure de-nesting: hoisting a top-level `semver` and `lru-cache` let npm
drop nested copies under `@electron/get`, `node-gyp`, `jsdom` and five others, so the tree got
slightly *less* duplicated. Nothing else changed version. Workspace membership was the deliberate
choice over a standalone `mobile/package-lock.json`, because `tests/test_ci_tier_enforcement.py` only
sees workspace members — keeping the shell outside the graph would have kept 37 `node --test` cases
out of every gate, which is the exact gap that rail was written to close. 27 MB is a rounding error
beside `desktop/`'s Electron, so the trade reads clearly in favour of the rail; if the owner disagrees,
the alternative is dropping `mobile` from `workspaces`, giving it its own lockfile, and adding a
`cd mobile && npm ci && npm test` CI step plus an exemption in that rail.

`src/personalclaw/config/loader.py` is **untouched: 5900 lines before and after.** The shell needs no
config field — the gateway URL is per-device runtime state in the shell's own storage, not gateway
config, and the endpoint registry is the contract that already owns it.
