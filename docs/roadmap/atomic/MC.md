# MOBILE-COMPANION — atomic plans

**Source plan:** [`MOBILE-COMPANION`](../plans/MOBILE-COMPANION.md)  
**Code:** `MC`  
**Source status:** proposed

Decomposed MOBILE-COMPANION into 10 todo atoms along the 2026-07-26 Amendment's session placement (push-to-approval is milestone 1). Nothing has shipped (DESIGNED, no execution log). Device sessions/tokens (MC-2) and QR pairing (MC-8) are SUPERSEDED — they consume COMPANION-APPS (plan 54) + REMOTE-USER-AUTH (plan 53) contracts rather than editing token_auth.py. Push atoms (MC-5/6/9) edge on INBOX-NOTIFICATIONS-UNIFICATION (plan 42) push target/rules. The pre-existing docs/guides/remote-access.md is plan 53's artifact, not MC-1 done-work.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `MC-1` | ✅ | S1 remote-access story: Tailscale-first guide + doctor reachability probe | — | A reader on cell data reaches their dashboard via tailnet following docs/guides/remote-access.md verbatim; `doctor` detects the tailscale interface and prints the phone-ready tokenized URL, and warns when the bind host exposes beyond loopback without auth (tailnet + misconfig fixtures pass). |
| `MC-2` | ⬜ | S2 device-session consumption + Devices list in Settings | `EXT:COMPANION-APPS:device-session model + unified pairing contract`, `EXT:REMOTE-USER-AUTH:durable session store (auth/sessions.json)` | A roaming-IP phone keeps its device session valid per the plan-54 contract; the Settings > Devices list renders name/minted/last-seen and Revoke; revoking kills the device session on the next request. No new claim added to token_auth.py — the device session comes from plan 54. |
| `MC-3` | ✅ | S2 approvals-first companion route + approve/reject wiring | — | On a phone viewport, `#/companion` renders full-context approval cards from GET /api/approvals (tool name, arguments, session/agent; plan-43 decision-brief when present, raw fallback until then — do not block on plan 43) and approve/reject round-trips against a dev gateway; all other sections stubbed behind S3.5; URL doctrine holds. |
| `MC-4` | ✅ | S3 PWA installability: manifest + service worker (app-shell precache, /api never cached) | `MC-3` | Lighthouse installability passes with manifest (claw-mark icons, standalone, start_url #/companion) and sw.ts precaching app-shell only; API responses are never served from cache (verified with offline toggle). |
| `MC-5` | ⬜ | S3 push-to-approval milestone 1: web push + ntfy adapter + deep link to the approval | `MC-3`, `MC-4`, `MC-2`, `EXT:INBOX-NOTIFICATIONS-UNIFICATION:rules-engine push target must exist` | VAPID keypair generated via `personalclaw push init` (keys in credential store), per-device subscription endpoint, and a content-free {kind,item_id} sender are wired as plan-42's `push` target; ntfy topic-URL adapter is an alternative backend (config mobile.push_backend/ntfy_topic_url); a locked-phone push → tap opens #/companion?approval=<id> with the correct card focused → approve → the paused run proceeds, <30s on cell data (timed); payload inspection shows ids only. |
| `MC-6` | ⬜ | S3.5 rest of companion: loops/tasks/inbox/notifications sections + SW sound/badge mapping | `MC-3`, `MC-4`, `EXT:INBOX-NOTIFICATIONS-UNIFICATION:per-(source,kind) sound/badge rules field + inbox resolve API` | Companion adds Running-loops (pause/nudge/stop via loop_routes), tasks, inbox-resolve, and recent-notifications sections working per the original S2 done-whens; the SW maps a push payload's `kind` to per-kind sound/badge using plan-42's rules field (a distinct sound fires for a kind configured in the rules UI). |
| `MC-7` | ⬜ | S4 Capacitor shell wrapping the served companion route | `MC-3`, `MC-2` | A Capacitor shell (new mobile/ dir; repo-location decision recorded) wraps the served companion URL with config for gateway URL + device session and native safe-areas, no forked UI; builds for iOS+Android and renders the live companion. |
| `MC-8` | ⬜ | S4 QR pairing screen (renders COMPANION-APPS pairing routes) | `MC-7`, `MC-2`, `EXT:COMPANION-APPS:unified pairing routes /api/devices/pair/start|complete` | Settings > Devices > Pair phone renders a QR of {pairing_url, one-time code}; the shell scans and exchanges it for a device session end to end; code single-use (TTL 5min) verified. Pairing routes are consumed from plan 54, not defined here. |
| `MC-9` | ⬜ | S4 platform push: ntfy default + open-source content-free relay + APNs/FCM shell wiring | `MC-7`, `MC-5` | ntfy-app integration works as the documented default; the optional stateless open-source push-relay (org repo) plus APNs/FCM wiring in the shell also delivers; an audit fixture confirms relay logs contain no content (ids-only pings). |
| `MC-10` | ⬜ | S4 store packaging + mobile-release docs | `MC-7`, `MC-9` | Icons/splash from brand assets, truthful no-data-collection privacy declarations, and docs/maintainers/mobile-release.md produce installable TestFlight/internal-track builds via the documented steps (owner performs the actual store submissions). |

## Atom scopes

### `MC-1` — S1 remote-access story: Tailscale-first guide + doctor reachability probe

**Status:** todo

Session 1 — Remote access story (T1.1 remote-access guide, T1.2 doctor probe, V1)

**Done when:** A reader on cell data reaches their dashboard via tailnet following docs/guides/remote-access.md verbatim; `doctor` detects the tailscale interface and prints the phone-ready tokenized URL, and warns when the bind host exposes beyond loopback without auth (tailnet + misconfig fixtures pass).

### `MC-2` — S2 device-session consumption + Devices list in Settings

**Status:** todo

Session 2 T2.3/T2.4 + C1 Device token (SUPERSEDED — consumes COMPANION-APPS §C1/C2 device sessions on REMOTE-USER-AUTH's durable store, not token_auth.py)

**Done when:** A roaming-IP phone keeps its device session valid per the plan-54 contract; the Settings > Devices list renders name/minted/last-seen and Revoke; revoking kills the device session on the next request. No new claim added to token_auth.py — the device session comes from plan 54.

### `MC-3` — S2 approvals-first companion route + approve/reject wiring

**Status:** done

Amendment §Session placement S2 — Approvals-first companion (T2.1r) + C2 Companion route API map

**Done when:** On a phone viewport, `#/companion` renders full-context approval cards from GET /api/approvals (tool name, arguments, session/agent; plan-43 decision-brief when present, raw fallback until then — do not block on plan 43) and approve/reject round-trips against a dev gateway; all other sections stubbed behind S3.5; URL doctrine holds. — DONE 2026-08-13.

**Landed:** `#/companion`, a full-screen no-NavRail hash route (`web/src/pages/companion/CompanionPage.tsx`,
registered in `App.tsx` beside the `#/onboarding` early return, deliberately outside `NAV`/`ROUTABLE`).
Full-context approval cards from `GET /api/approvals` — tool, UNTRUNCATED arguments (JSON pretty-printed),
purpose, session, requesting source, time waited — resolving through `POST /api/approvals/{id}/{action}`
(the resolved route map is recorded in the plan's Execution log; the plan's C2 line named the chat route,
which is wrong for this queue). The card chrome was EXTRACTED to `web/src/ui/ApprovalPrompt.tsx` and the
in-chat card now renders it too, so there is one permission-prompt renderer, not two. A failed first
fetch renders `LoadError` (announced, retryable) instead of the "nothing waiting" empty state; a failed
resolve puts the card back and toasts. Running/Inbox/Recent are named as not-yet-built under a
"Not on the phone yet" heading — never as empty data.

**Remains (later atoms, not gaps here):** PWA manifest + service worker (`MC-4`), push and the
`?approval=<id>` deep link (`MC-5`), the loops/tasks/inbox/notifications sections (`MC-6`). The
plan-43 decision-brief is still absent upstream, so the raw-argument fallback is what ships — as the
done-when directs.

### `MC-4` — S3 PWA installability: manifest + service worker (app-shell precache, /api never cached)

**Status:** done

Session 3 T3.1 + C2 service-worker rule (network-first; explicit no-cache for /api/*)

**Done when:** Lighthouse installability passes with manifest (claw-mark icons, standalone, start_url #/companion) and sw.ts precaching app-shell only; API responses are never served from cache (verified with offline toggle). — DONE 2026-08-15.

**Landed.** The companion installs to a home screen, and the service worker cannot cache an API
response. `web/public/manifest.webmanifest` (`display: standalone`, `start_url` `/#/companion`,
`scope: /`) plus `web/src/sw.ts`, bundled to the dist **root** as `sw.js` by
`web/scripts/buildServiceWorker.mjs` from a Vite `closeBundle` hook — a worker emitted into
`dist/assets/` would be scoped to `/assets/` and could not control the SPA. Gateway routes
`/manifest.webmanifest`, `/sw.js` and a `/icons` static mount; `/icons/` was added to
`spa_fallback`'s exclusions so a missing icon 404s instead of coming back as index.html.

**The `/api`-never-cached rule is one gate, proven behaviourally.** All caching policy lives in
`web/src/app/swPolicy.ts`; `mayCache()` is the single predicate consulted before every cache read
and every write, and `strategyFor()` is defined in terms of it so the two cannot disagree. The
default is **fail-closed** — an unrecognised path is `network-only`, so a route added to the gateway
tomorrow is not silently cached. `/api` resolves to `network-only`, which returns from the fetch
handler **without** calling `respondWith`: the browser performs the fetch itself, so an API response
never enters worker JavaScript at all. Precache is the shell only (index.html, favicon, manifest,
icons, the one preloaded font); hashed `/assets/*` are runtime `cache-first` because they are
content-addressed and immutable.

Proven in `web/e2e/pwa.spec.ts` against the real build: install the worker, read `/api/ping` twice
(a counter, so a replay is unmistakable), inspect the real Cache Storage, then take the origin
genuinely offline by **shutting the server down** — no network emulation to get subtly wrong. Offline,
a navigation still renders the shell from cache (the vacuity floor: without it, `/api` "failing"
would prove nothing) while `fetch('/api/ping')` fails in the same instant. Falsified by removing the
API guard and rebuilding: the test reported the leak verbatim — `offline /api resolved instead of
failing: {"ok":true,"status":200,"body":"{\"call\":1,\"secret\":\"payload-1\"}"}`.

**Update strategy: no `skipWaiting()`, no `clients.claim()`** (ratcheted by a source assertion, not
just a comment). Navigations are network-first, so a reachable gateway always serves the freshest
`index.html` and a stale shell cannot pin an old bundle — `skipWaiting` would buy nothing while
actively breaking live tabs, since `App.tsx` lazy-loads nearly every route and claiming clients while
purging the old cache swaps the asset cache out mid-import. The gateway serves this same SPA to the
desktop app, so a wrong update strategy here is every user's bug, not a phone bug.

**DEVIATION — "Lighthouse installability" no longer exists.** Lighthouse **12** removed the PWA
category and every audit in it; a 12.8.2 run against this build reports only performance /
accessibility / best-practices / seo, with no `installable-manifest`, `service-worker` or
`maskable-icon`. The criteria are asserted directly instead (`web/src/app/manifest.test.ts`, incl.
each PNG's real IHDR dimensions vs its declared `sizes`) plus, in the e2e spec, the manifest **as
Chrome fetched it** via `Page.getAppManifest`. `Page.getInstallabilityErrors` was measured and found
**INERT** — it returns `[]` for an unparseable manifest and for `display: "browser"` — so it is
deliberately not asserted on; `getAppManifest` was measured to have teeth (`Line: 1, column: 3,
Syntax error.`) and carries the rail.

**Security posture.** The PWA files stay **behind session auth** — none was added to `token_auth`'s
bypass sets, honouring the existing `test_retired_pwa_paths_require_auth` ratchet, and
`test_live_pwa_paths_require_auth` now locks the live paths the same way. The cost is that browsers
fetch a manifest with credentials omitted, so `index.html` declares the link with
`crossorigin="use-credentials"`. Only the authenticated owner can install the companion.

**Known constraint (not a gap here):** service workers require a secure context, so a gateway reached
over plain http at a LAN address cannot install or work offline — `localhost` or a TLS tunnel (MC-1's
remote-access story) is required. `registerServiceWorker()` says so in one console line rather than
leaving an install button that silently never appears.

**Remains (later atoms):** `push`/`notificationclick` handlers and the `?approval=<id>` deep link
(`MC-5`), per-kind sound/badge mapping (`MC-6`) — deliberately absent, not stubbed.

### `MC-5` — S3 push-to-approval milestone 1: web push + ntfy adapter + deep link to the approval

**Status:** todo

Amendment §S3 Push-to-approval (T3.2 web push, T3.3 ntfy/UnifiedPush adapter, T3.4 SW deep link, V3) + C3 Push

**Done when:** VAPID keypair generated via `personalclaw push init` (keys in credential store), per-device subscription endpoint, and a content-free {kind,item_id} sender are wired as plan-42's `push` target; ntfy topic-URL adapter is an alternative backend (config mobile.push_backend/ntfy_topic_url); a locked-phone push → tap opens #/companion?approval=<id> with the correct card focused → approve → the paused run proceeds, <30s on cell data (timed); payload inspection shows ids only.

### `MC-6` — S3.5 rest of companion: loops/tasks/inbox/notifications sections + SW sound/badge mapping

**Status:** todo

Amendment §S3.5 (T3.5.1) — former S2 T2.1/T2.2 breadth

**Done when:** Companion adds Running-loops (pause/nudge/stop via loop_routes), tasks, inbox-resolve, and recent-notifications sections working per the original S2 done-whens; the SW maps a push payload's `kind` to per-kind sound/badge using plan-42's rules field (a distinct sound fires for a kind configured in the rules UI).

### `MC-7` — S4 Capacitor shell wrapping the served companion route

**Status:** todo

Sessions 4-6 Wrapper tier — T4.1 Capacitor shell

**Done when:** A Capacitor shell (new mobile/ dir; repo-location decision recorded) wraps the served companion URL with config for gateway URL + device session and native safe-areas, no forked UI; builds for iOS+Android and renders the live companion.

### `MC-8` — S4 QR pairing screen (renders COMPANION-APPS pairing routes)

**Status:** todo

Sessions 4-6 — T4.2 QR pairing + C4 (SUPERSEDED — renders plan-54 §C2 /api/devices/pair/* rather than defining them)

**Done when:** Settings > Devices > Pair phone renders a QR of {pairing_url, one-time code}; the shell scans and exchanges it for a device session end to end; code single-use (TTL 5min) verified. Pairing routes are consumed from plan 54, not defined here.

### `MC-9` — S4 platform push: ntfy default + open-source content-free relay + APNs/FCM shell wiring

**Status:** todo

Sessions 4-6 — T4.3 Platform push

**Done when:** ntfy-app integration works as the documented default; the optional stateless open-source push-relay (org repo) plus APNs/FCM wiring in the shell also delivers; an audit fixture confirms relay logs contain no content (ids-only pings).

### `MC-10` — S4 store packaging + mobile-release docs

**Status:** todo

Sessions 4-6 — T4.4 Store packaging + Owner tasks 3-4

**Done when:** Icons/splash from brand assets, truthful no-data-collection privacy declarations, and docs/maintainers/mobile-release.md produce installable TestFlight/internal-track builds via the documented steps (owner performs the actual store submissions).

