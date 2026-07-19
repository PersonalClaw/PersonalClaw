# Plan: Desktop Capabilities — The Electron App as a Capability Surface

**Status:** DESIGNED — deepened 2026-07-18 with code recon (initial PROPOSED 2026-07-18; owner GO: live audio + OS capabilities for the platform *and* apps via the Electron app)
**Created:** 2026-07-18
**Wave:** 2/3. Not launch-gating — DISTRIBUTION carries the launch; the desktop ships when it is *better* than a browser tab.
**Depends on:** CI-RELEASE-ENGINEERING (build/signing in the pipeline), DISTRIBUTION (release artifacts + install-kind detection: the shell sets `PERSONALCLAW_INSTALL_KIND=desktop`). Coordinates with MULTIMODAL-IO (voice capture lands on this bridge; screen-context is its flagship consumer), AMBIENT-SURFACES (menu-bar companion rides the tray), INBOX-NOTIFICATIONS-UNIFICATION (native notifications as a rules target).
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

## Contracts & Interfaces (conventions per [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md))

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

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

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
