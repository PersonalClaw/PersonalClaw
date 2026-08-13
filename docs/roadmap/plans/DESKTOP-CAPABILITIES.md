# DESKTOP-CAPABILITIES

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/DC.md`](../atomic/DC.md) as 6 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Desktop Capabilities — The Electron App as a Capability Surface

**Status:** DESIGNED — deepened 2026-07-18 with code recon (initial PROPOSED 2026-07-18; owner GO: live audio + OS capabilities for the platform *and* apps via the Electron app)
**Created:** 2026-07-18
**Wave:** 2/3. Not launch-gating — DISTRIBUTION carries the launch; the desktop ships when it is *better* than a browser tab.
**Depends on:** CI-RELEASE-ENGINEERING (build/signing in the pipeline), DISTRIBUTION (release artifacts + install-kind detection: the shell sets `PERSONALCLAW_INSTALL_KIND=desktop`). Coordinates with MULTIMODAL-IO (voice capture lands on this bridge; screen-context is its flagship consumer), AMBIENT-SURFACES (menu-bar companion rides the tray), INBOX-NOTIFICATIONS-UNIFICATION (native notifications as a rules target), **COMPANION-APPS (plan 54 — its "connect to a gateway you did not spawn: LAN or remote" mode is added here in that plan's S4/T4.1, consuming plan 54's client contract; the spawn-local default is unchanged)**.

> **Rev-11 note (2026-07-26):** a code audit confirmed the desktop shell is **already Electron**
> (`desktop/main.js`, Electron ^43) — there is **no Tauri anywhere in the repo**, so the
> "Tauri→Electron migration" some backlog framing implied is a no-op: this plan (committed to
> Electron, "no forked frontend, no second API") stands as-is. The real host-capability gap the
> owner cares about — **mic/live audio capture** — is this plan's **Session 3** (unbuilt), not a
> re-platform.
**Scope:** complete the Electron app and make it the **OS-capability surface**: live/system audio capture, global hotkeys, native notifications, tray/menu-bar presence, consent-gated screen capture, login-item lifecycle — exposed to core and, permission-gated, to apps. **Soul guardrail:** the desktop hosts the same SPA and the same gateway — no forked frontend, no second API. OS capabilities enter through ONE typed bridge with per-capability consent mirroring the app-platform's permission voice; nothing is silently granted; **no always-on/ambient capture ships in this plan.**

---

## Context (code recon, 2026-07-18)

- **The shell is further along than assumed:** `desktop/main.js` already implements login-shell PATH resolution, backend spawn (`personalclaw gateway --port auto --json-ready --no-open`) via `findPersonalclawBin` (bundled `backend-dist` with PATH fallback — dependency-injected and unit-tested), 2-min readiness wait on the `--json-ready` line, loading screen via `preload.js`'s minimal `contextBridge` (`electronAPI.onStatus`) — **the bridge pattern to extend exists**. `Tray, Menu, nativeImage, nativeTheme` are already imported in main.js (presence work has a head start — verify how much is wired).
- **Build chain exists:** `make pyinstaller` (spec excludes torch/faiss by design — local-model extras degrade with UI guidance) → `make desktop` (stages `backend-dist`, npm install) → `make desktop-dist` (`electron-builder --mac` → dmg). `backend-dist` was deleted pre-split (PUBLICATION follow-up → this plan's S1). No signing/notarization config in-repo; dist target macOS-only.
- Desktop tests exist (`desktop/test/`: context-menu, find-bin, packaging).

## Design

- **S1:** fresh backend bundle in CI (mac runner: `make desktop` → electron-builder with signing + notarization via CI secrets) attached to GitHub Releases; auto-update via electron-updater against Releases (the desktop's DISTRIBUTION §C kind: shell sets `PERSONALCLAW_INSTALL_KIND=desktop`; the in-app Updates panel shows "managed by the desktop app").
- **S2 — the capability bridge:** `preload.js` grows a namespaced typed API (`window.pclawDesktop.capabilities`) — registry `{audio_capture, global_hotkey, native_notifications, tray, screen_capture, login_item}`; each: `probe()` (availability + OS-permission state via macOS TCC queries), `request()` (triggers the OS prompt through Electron's `systemPreferences`/`desktopCapturer` paths), `state` events. Gateway-side: a `desktop` provider seam — the shell registers itself with the gateway on boot (loopback call carrying a capability manifest + a shell token); capability state surfaces in **Settings → Security → Desktop capabilities** (grant list in the app-permission consent voice; SEL events on grant/use). Apps consume via a manifest permission (`desktop: ["audio_capture"]`) enforced like `api`/`events` — gateway mediates every app→bridge call (apps never talk to Electron directly).
- **S3 — live audio:** push-to-talk global hotkey → mic capture in the renderer (getUserMedia, TCC-prompted via the bridge) → existing `/api/stt/transcribe` (bound STT provider — faster-whisper local by default) → composer insertion or voice-chat surface; system-audio capture only where the OS allows it natively (macOS: screen-capture-audio path; documented honestly, likely deferred) — mic is the S3 deliverable.
- **S4 — presence + platforms:** tray/menu-bar companion (pending-approvals count, running loops, quick capture — AMBIENT-SURFACES' menu-bar item lands here against its tile registry when available); native notifications as a plan-42 rules target (`native` on desktop replaces `dashboard` toasts when focused-away); login-item toggle; **then** Windows/Linux electron-builder targets *only after* PLATFORM-REACH proves the backend there (desktop follows platform support, never leads it).

## Contracts & Interfaces (conventions per [AGENTS.md](../../../AGENTS.md))

### C1 — Capability bridge (`desktop/preload.js` contextBridge — EXTENDS the existing `electronAPI` pattern, verified `preload.js`)

```typescript
window.pclawDesktop = {
  capabilities: {
    // caps: "audio_capture"|"global_hotkey"|"native_notifications"|"tray"|"screen_capture"|"login_item"
    probe(cap: string): Promise<{ available: boolean; granted: 'granted'|'denied'|'not-determined'|'unavailable'; reason?: string }>,
    request(cap: string): Promise<{ granted: boolean }>,   // triggers OS/TCC prompt via systemPreferences
    on(cap: string, cb: (state) => void): () => void,
  }
}
```
`contextIsolation` stays on; the renderer reaches ONLY this namespace (asserted in desktop tests). Main-process handlers in `desktop/capabilities.js` (new, split from main.js).

### C2 — Gateway desktop seam (`dashboard/handlers/desktop.py`, new thin handler)
Shell registers on boot: `POST /api/desktop/register {capabilities: {...state}, shell_token}` (loopback; shell_token minted per session, misuse → 403 + SEL). State lands in `DashboardState`; `GET /api/desktop/state` reflects it (absent/"not connected" in a browser tab). App→bridge calls are gateway-mediated (apps never touch Electron IPC).

### C3 — App manifest `desktop` permission (via plan 32's manifest-field pattern, §3.8)
```jsonc
{ "permissions": { "desktop": ["native_notifications", "audio_capture"] } }
```
Enforced in `apps/permissions.py` exactly like `api`/`events`: an app calling a bridge-backed route without the declared cap → 403 + SEL `capability_denied`. Shown on the install consent surface.

### C4 — Install-kind + updater
Shell sets `PERSONALCLAW_INSTALL_KIND=desktop` in the spawn env (consumed by DISTRIBUTION C1 `detect_install_kind` → `desktop_delegate` branch). electron-updater against GitHub Releases; user-initiated apply only.

### Integration points
- **Calls:** existing `desktop/main.js` spawn machinery + `Tray`/`Menu` (already imported — extend), `systemPreferences`/`desktopCapturer` (Electron), `/api/stt/transcribe` (audio → STT, S3), plan-42 rules engine (`native` notification target), `sel()`.
- **Called by:** apps declaring `desktop:` perm (gateway-mediated); AMBIENT-SURFACES (20) menu-bar tiles render in the tray this plan owns.
- **Depends on:** plan 33 (signing in CI), plan 34 (`detect_install_kind` desktop branch), plan 39 (non-mac targets gate on its rungs), plan 42 (`native` target).
- **Storage:** capability grants surfaced in Settings → Security; SEL events on grant/use.

## Task breakdown (executor-ready — run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

### Session 1 — Rebuild + signing + updater

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | CI job (macos runner, release.yml): `make desktop` → electron-builder signed+notarized dmg/zip → attach to the GitHub Release; secrets consumed from the `release` environment (owner task 1 provides them) | `.github/workflows/release.yml`, `desktop/package.json` build config (hardenedRuntime, entitlements incl. microphone for S3, notarize) | rc release carries a notarized dmg; `spctl -a` passes on a clean Mac |
| T1.2 | electron-updater wired against Releases (check on launch + daily; user-initiated apply; no silent installs) | `desktop/main.js`, package.json publish config | one-version-behind install prompts and updates |
| T1.3 | `PERSONALCLAW_INSTALL_KIND=desktop` in the spawn env; gateway Updates panel renders the desktop-managed state (DISTRIBUTION T4.x coordination — verify its instructions-payload branch handles `desktop`) | `desktop/main.js`, updates handler | panel shows "updates managed by the desktop app" under the shell |
| T1.4 | Packaging test refresh: `desktop/test/packaging.test.js` asserts bundle staging + version stamping against the new pipeline | desktop tests | `node --test` green |
| V1 | Validation on a clean macOS machine/VM: install dmg → first run (Gatekeeper clean) → onboarding → chat; update from a previous rc | — | recorded |

### Session 2 — Capability bridge

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | Bridge API in preload (typed, namespaced, per-capability probe/request/state; deny-by-default) + main-process handlers (TCC state via `systemPreferences.getMediaAccessStatus` etc.) | `desktop/preload.js`, `desktop/main.js` (+ split `desktop/capabilities.js`), desktop tests with injected stubs | unit tests per capability state machine; renderer cannot reach ipc channels outside the namespace (contextIsolation asserted) |
| T2.2 | Shell↔gateway registration: on ready, POST a capability manifest to a new loopback gateway route (shell token minted per session; gateway stores desktop state in DashboardState) | `desktop/main.js`, new `dashboard/handlers/desktop.py` (thin), state wiring | gateway `/api/status` (or a `desktop/state` route) reflects capability availability; token misuse rejected + SEL-logged |
| T2.3 | Settings → Security → Desktop capabilities panel: per-capability state (unavailable / not-granted / granted) with request buttons routed through the bridge; consent copy in the app-permission voice (copy-sensitive — reuse phrasing patterns) | `web/src/pages/settings/` new panel | states render truthfully on desktop; panel absent (or "desktop app not connected") in a browser tab |
| T2.4 | App-facing permission: manifest `desktop: [caps]` parsed + enforced (gateway mediates app calls to bridge-backed routes exactly like `api` prefixes); consent surface shows it at install | `apps/manifest.py`, `apps/permissions.py`, install consent UI | fixture app with `desktop: ["native_notifications"]` can fire one, an app without it gets 403 + SEL entry |
| V2 | Validation: grant/deny each capability from Settings; verify TCC prompts appear exactly once per grant; SEL trail complete | — | holds |

### Session 3 — Live audio (the owner's headline)

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | Push-to-talk: global hotkey registration (bridge capability; default chord configurable in Settings) toggling capture state with tray/menu-bar indication | `desktop/capabilities.js`, settings wiring | hotkey captures only while held/toggled; visible indicator always on while capturing |
| T3.2 | Mic capture → STT: renderer getUserMedia (TCC via bridge grant) → chunked upload to `/api/stt/transcribe` (bound provider; verify the route's streaming/chunk contract — record in log) → composer insertion at cursor (chat) with an interim-text affordance | renderer capture module (`web/src/` voice util or desktop-injected), composer integration | spoken sentence lands in the composer ≤2s after release on faster-whisper local; mic indicator truthfulness verified |
| T3.3 | Honest system-audio note: probe + documented deferral (macOS system audio requires screen-capture entitlement paths); `docs/guides/desktop.md` states mic-only for now | guide + probe stub returning `unavailable` with reason | doc + probe agree; no half-shipped system audio |
| V3 | Validation: full voice round-trip in chat; deny-mic path degrades with the actionable prompt; hotkey conflict case (already-registered chord) surfaces cleanly | — | holds |

### Session 4 — Presence + platforms

| ID | Task | Files | Done when |
|---|---|---|---|
| T4.1 | Tray/menu-bar companion: icon + menu (pending approvals count with click-through, running loops, quick-capture note→inbox, open dashboard, quit); wired to gateway state over the loopback WS/API (verify what `Tray` wiring already exists in main.js — extend, don't duplicate) | `desktop/main.js`/`capabilities.js` | counts live-update; click-throughs deep-link into the SPA |
| T4.2 | Native notifications as a plan-42 target: `native` target routed to Electron Notification when the desktop shell is connected; falls back to dashboard toasts otherwise | notification target registration (plan 42's rules engine), shell handler | a rule with target `native` fires an OS notification; tap focuses the relevant surface |
| T4.3 | Login item toggle (Settings, via bridge) + graceful gateway shutdown on quit (verify current quit path kills the child cleanly — PPID-reaping interplay) | `desktop/main.js`, settings | login-item survives reboot (manual check); no orphan gateway after quit (process table verified) |
| T4.4 | Windows/Linux targets — **gated**: only if PLATFORM-REACH's corresponding rung is proven; then electron-builder targets + per-OS signing docs; else record DEFERRED with the gate condition | `desktop/package.json`, docs | either shipped-with-proof or a dated deferral note |
| V4 | Validation: a day of desktop dogfood (owner task 3) — tray counts honest, notifications sane, quit/restart clean | — | recorded |

## Owner tasks (real world)

1. **Apple Developer Program** ($99/yr) — needed for signing + notarization (S1): create the Developer ID Application cert, an app-specific password / App Store Connect API key for `notarytool`, and hand the four values to CI as `release`-environment secrets (names listed in the workflow file). ~1 hour first time.
2. **Approve the consent copy** for capability grants (S2 — security-voice surfaces).
3. **Desktop dogfood day** (V4) and the mic-privacy sanity pass (S3): confirm the capture indicator behavior matches your expectations before any release.
4. Decide the **default push-to-talk chord** (S3) — trivial but personal.

## Risks & open questions

- **Notarization pipeline flakiness** is a known industry papercut — retries + `notarytool` (not legacy altool) + a documented manual fallback in the runbook.
- **Bundle size** (PyInstaller + Electron): measure in S1 and record; torch-class exclusions keep it sane; if >400MB, note options (no action without measurement — bottleneck-gated).
- **Open:** whether quick-capture from the tray writes an inbox item or a chat message — default: inbox `system` item (plan 42), revisit after dogfood.

## Execution log

Format: one line per task/event — `DONE` / `DEVIATION` / `DISCOVERY` / `BLOCKED` — under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md).

### Session 2 — Capability bridge (2026-08-13)

- **DECISION (namespace — ONE bridge, not two).** C1 says "extends the existing `electronAPI` pattern" while the atom's `done_when` names `window.pclawDesktop`. Keeping both would have been the dual-mechanism drift this repo has paid for three times recently (two spring vocabularies, two approval renderers, two verdict enums): the next person adding a capability would have to guess which namespace owns it, and a security review would have two surfaces to audit. So `electronAPI` is **deleted**, `onStatus` moved into `window.pclawDesktop` alongside `capabilities`, and `loading.html` migrated in the same change (clean break under the pre-1.0 banner — the shell's bridge is not persisted state, so there is nothing to migrate). `desktop/test/capabilities.test.js` pins it: exactly one `exposeInMainWorld` call, and `loading.html` must not mention the retired name.
- **DECISION (shell_token — no new secret on disk).** `POST /api/desktop/register` is gated on loopback + the EXISTING `$PERSONALCLAW_HOME/.local_secret` (the rail `GET /api/token/local` already uses) and returns an in-memory `secrets.token_urlsafe(32)` that authorizes only `/api/desktop` writes. Rationale: any process that can read the home can already mint a full API token, so a second secret FILE would add something to back up and leak without moving the boundary — while a per-session in-memory token is strictly narrower than the local secret, dies with the process, rotates on re-register (so a stale shell stops writing), and adds no `durability/inventory.py` entry. It is never written to disk, never logged, never in an error body, and never handed to a renderer (the preload exposes probe/request/on and no token accessor).
- **DONE T2.1** — `desktop/capabilities.js` (new, split from main.js): 6 capabilities x 5 grant states over injected OS handles. `probe()` never throws (an OS failure degrades to `unavailable` + reason); `request()` prompts ONLY on the `not-determined` → granted/denied transition and returns `prompted` so "one TCC dialog per grant" is an assertion; `denied`/`restricted` return the System-Settings route without re-asking; disclosure-only capabilities (screen recording, notification authorization — macOS exposes no prompt/no readable state) report `requestable: false` with a reason instead of offering a dead control; non-darwin reports `unavailable`, never an optimistic guess. Preload validates the closed vocabulary and the MAIN process validates it again (a compromised renderer can pass any string over IPC). 33 `node --test` cases; rails assert `contextIsolation: true` / `nodeIntegration: false` on every window, exactly one exposed namespace, no IPC channel outside `IPC_CHANNELS`, and that the shell token never reaches a log line, a renderer send, or disk.
- **DONE T2.2** — `dashboard/desktop_registry.py` + `dashboard/handlers/desktop.py` (new, thin) on `DashboardState.desktop`: `POST /api/desktop/{register,state,unregister}` (loopback checked BEFORE the credential so a remote caller cannot use the route as a token oracle) and `GET /api/desktop/{state,capabilities/{cap}}`. Fail closed everywhere: unregistered → `{connected: false, capabilities: {}}`; an unparseable capability entry normalizes to `unavailable`/not-requestable; an unknown capability name is dropped rather than stored; no local secret minted → 503 rather than a loopback-only fallback. Every rejection emits a SEL row (`desktop.register`, `desktop.state.push`, `desktop.unregister`, `desktop.capability_denied`) whose `resources` names the failure class and never any part of a presented credential. `main.js` registers after the gateway's READY line and unregisters on `before-quit`; a grant pushes a refreshed manifest so the panel needs no polling.
- **DONE T2.3** — Settings → Security → **Desktop capabilities**. Renders only what the shell reported: a browser tab shows "Desktop app not connected" with zero grant buttons and zero capability names (`connected: false` wins even over a stale non-empty map). A grant button appears only where the shell said `requestable`, and its accessible name carries the capability ("Allow microphone") so six rows are not six identically-named controls. An unmapped capability name is humanized and still rendered — dropping it would make the UI less honest than the API.
- **DONE T2.4** — App-manifest `desktop: [caps]`: `Permissions` (+`to_dict`/`from_dict`) → `PermissionChecker.can_use_desktop` → the desktop handler → `AppPermissionsWire` → `PermissionList`. EXACT-match only, no `*` wildcard (the vocabulary is closed, and "everything native this host can do" is not a grant a user should click through), deny-by-default, and the Store discloses both the declared capabilities (enforced bullets) and the deny-by-default case (caption) — the APE-12 lesson applied.
- **DEVIATION (T2.4 first call site).** The task's "fixture app with `desktop: ["native_notifications"]` can fire one" cannot land in S2: no bridge-backed notification route exists until S4/plan-42 registers the `native` target. Enforcement therefore lands on the gateway-mediated capability READ (`GET /api/desktop/capabilities/{cap}` → 403 + SEL `desktop.capability_denied` for an undeclared capability), so the gate ships ahead of its first USE site by design. S3/S4 must route their new use routes through `can_use_desktop` rather than re-deriving a check.
- **DISCOVERY (a non-loopback POST never reaches this handler in a real deployment).** Driving `POST /api/desktop/register` from the machine's LAN IP against a `0.0.0.0`-bound gateway is refused earlier by the origin/CSRF guard (403 "request origin not allowed"), and with an Origin the guard accepts, by the token's IP binding (403 "IP mismatch"). The handler's own loopback rail is therefore a third, independent layer that is unreachable over the wire on a correctly configured gateway — which is exactly why it is covered by a unit test that clones the request with a non-loopback peer, and why disabling it must turn that test red (verified: it does).
- **DISCOVERY (a coral "Granted" read as "Denied").** The first panel build styled `granted` with `text-primary`; in this theme the brand primary is a coral almost identical to `text-error`, so a granted capability and a denied one rendered the same colour. Caught by looking at the real page, not by any test. Now `text-success` (measured live: `rgb(14,188,95)` vs `rgb(246,108,102)`).
- **REMAINING (V2 + owner task 2).** Electron was never launched during this session, so V2's on-device walk-through — real TCC dialogs, "exactly once per grant" on a device, the grant→push→panel loop through the actual shell — is unproven; the state machine's once-only property is proven by unit test only. Owner task 2 (approve the consent copy in the panel and the Store bullet) is also outstanding. `DC-2` therefore stays `todo` in `dag.json` with those two named remainders even though T2.1–T2.4 are complete and gated.
