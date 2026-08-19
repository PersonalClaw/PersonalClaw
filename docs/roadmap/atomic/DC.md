# DESKTOP-CAPABILITIES — atomic plans

**Source plan:** [`DESKTOP-CAPABILITIES`](../plans/DESKTOP-CAPABILITIES.md)  
**Code:** `DC`  
**Source status:** proposed

6 atoms: 1 done, 5 todo. **DC-2's implementation (T2.1-T2.4) landed 2026-08-13 and DC-3's (T3.1-T3.3) on 2026-08-16**; DC-2 closed with #1286, while DC-3 stays `todo` for its on-device walk-through (V3 — a launched Electron shell on a real Mac) and its owner tasks — DC-4/DC-5 can build against their contracts now. DC-2 is the independently-startable capability-bridge seam-owner; DC-3/4/5 hang off it; DC-1 carries the mac signing/updater pipeline; DC-5 and DC-6 carry the two cross-plan gates (INBOX-NOTIF, PLATFORM-REACH).

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `DC-1` | ⬜ | S1: Rebuild + signing + notarization + electron-updater + install-kind | `EXT:CI-RELEASE-ENGINEERING:signing/build stage in release.yml pipeline`, `EXT:DISTRIBUTION:release artifacts + detect_install_kind desktop branch` | CI mac-runner job builds `make desktop` -> electron-builder signed+notarized dmg/zip attached to the GitHub Release (spctl -a passes on a clean Mac); electron-updater checks Releases and prompts a one-version-behind install; shell sets PERSONALCLAW_INSTALL_KIND=desktop and the gateway Updates panel shows 'managed by the desktop app'; desktop/test/packaging.test.js green; V1 clean-machine install+update recorded. (Owner task 1 supplies the four Apple Developer signing secrets to the CI `release` environment.) |
| `DC-2` | ✅ | S2: Typed capability bridge + gateway seam + Settings panel + app perm | — | window.pclawDesktop.capabilities probe/request/state works per-capability with unit tests over the state machine and contextIsolation asserted (renderer cannot reach ipc channels outside the namespace); shell POSTs a capability manifest to a new loopback /api/desktop/register (per-session shell_token; misuse -> 403 + SEL) and GET /api/desktop/state reflects availability (absent/'not connected' in a browser tab); Settings -> Security -> Desktop capabilities panel renders truthful per-capability state with request buttons; a fixture app with desktop:['native_notifications'] is enforced like api/events (missing cap -> 403 + SEL capability_denied) and shown on the install consent surface. (Owner task 2 approves the consent copy.) |
| `DC-3` | 🟡 impl landed | S3: Live audio — push-to-talk mic capture to STT | `DC-2` | global-hotkey push-to-talk (bridge global_hotkey cap, chord configurable in Settings) captures only while held/toggled with an always-on capturing indicator; renderer getUserMedia (TCC via bridge grant) chunk-uploads to existing /api/stt/transcribe and a spoken sentence lands in the composer at cursor <=2s after release on faster-whisper local; system-audio probe returns unavailable with reason and docs/guides/desktop.md states mic-only; deny-mic path degrades with an actionable prompt and an already-registered-chord conflict surfaces cleanly. (Owner tasks 3/4: mic-privacy sanity pass + default chord.) |
| `DC-4` | ⬜ | S4: Tray/menu-bar presence + login-item + graceful quit | `DC-2` | tray/menu-bar icon+menu shows pending-approvals count (click-through deep-links into the SPA), running loops, quick-capture note->inbox, open dashboard, quit; counts live-update over the loopback WS/API; login-item toggle (Settings via bridge) survives reboot; graceful gateway shutdown on quit leaves no orphan gateway (process table verified). AMBIENT-SURFACES menu-bar tiles render here only when that plan is available (non-blocking). |
| `DC-5` | ⬜ | S4: Native notifications as a plan-42 rules target | `DC-2`, `EXT:INBOX-NOTIFICATIONS-UNIFICATION:native notification target registered in the rules engine` | a notification rule with target `native` fires an Electron OS Notification when the desktop shell is connected and the tap focuses the relevant surface; falls back to dashboard toasts when the shell is not connected. |
| `DC-6` | ⬜ | S4: Windows/Linux electron-builder targets (PLATFORM-REACH-gated) | `DC-1`, `EXT:PLATFORM-REACH:non-mac backend proven on the target OS` | either Windows/Linux electron-builder targets ship with per-OS signing docs once PLATFORM-REACH's corresponding rung is proven, OR a dated DEFERRED note records the exact gate condition. |

## Atom scopes

### `DC-1` — S1: Rebuild + signing + notarization + electron-updater + install-kind

**Status:** todo

Session 1 — Rebuild + signing + updater (T1.1-T1.4, V1); Contract C4 (install-kind + updater)

**Done when:** CI mac-runner job builds `make desktop` -> electron-builder signed+notarized dmg/zip attached to the GitHub Release (spctl -a passes on a clean Mac); electron-updater checks Releases and prompts a one-version-behind install; shell sets PERSONALCLAW_INSTALL_KIND=desktop and the gateway Updates panel shows 'managed by the desktop app'; desktop/test/packaging.test.js green; V1 clean-machine install+update recorded. (Owner task 1 supplies the four Apple Developer signing secrets to the CI `release` environment.)

### `DC-2` — S2: Typed capability bridge + gateway seam + Settings panel + app perm

**Status:** todo — implementation landed 2026-08-13, two non-code remainders

**Landed:**
- `desktop/capabilities.js` (new) — the capability state machine over injectable OS handles:
  6 capabilities x 5 grant states, `probe`/`snapshot`/`request`, `request()` prompting ONLY from
  `not-determined`, disclosure-only capabilities (screen recording, notification authorization)
  reporting where to grant instead of offering a dead control. 33 `node --test` cases.
- `desktop/preload.js` — collapsed to the ONE `window.pclawDesktop` namespace (`onStatus` +
  `capabilities`); the old `electronAPI` bridge is DELETED and `loading.html` moved with it.
  Rails assert `contextIsolation: true`/`nodeIntegration: false` on every window and that the
  renderer reaches no IPC channel outside `IPC_CHANNELS`.
- `dashboard/desktop_registry.py` + `dashboard/handlers/desktop.py` (new) — `POST /api/desktop/
  register` (loopback + `X-Local-Secret` -> in-memory per-session `shell_token`, rotated on
  re-register), `POST /api/desktop/state`, `POST /api/desktop/unregister`, `GET /api/desktop/state`,
  `GET /api/desktop/capabilities/{cap}`. Every rejection: 403 + a SEL row
  (`desktop.register` / `.state.push` / `.unregister` / `.capability_denied`).
- Settings -> Security -> **Desktop capabilities** panel — renders only what the shell reported;
  a browser tab gets "Desktop app not connected" and zero grant buttons.
- App-manifest `desktop: [caps]` — exact-match, deny-by-default, through
  `Permissions` -> `AppPermissionsWire` -> `PermissionList` (enforced bullets + the
  deny-by-default caption).

**Remaining:**
- **V2 on-device walk-through.** Electron was not launched during implementation, so the real TCC
  prompt path (and the once-per-grant property) is proven by the state-machine unit tests, not on
  a device. Needs a built shell — pairs naturally with DC-1's dmg.
- **Owner task 2** — approve the consent copy in the panel and the Store bullet.

**DEVIATION (T2.4).** The plan's "fixture app can fire a native notification" is enforced at the
gateway-mediated capability READ (`GET /api/desktop/capabilities/{cap}`); no bridge-backed USE
route exists until DC-3/DC-5 build one, so the gate ships ahead of its first call site.

Session 2 — Capability bridge (T2.1-T2.4, V2); Contracts C1 (preload contextBridge), C2 (gateway desktop route), C3 (app manifest desktop perm)

**Done when:** window.pclawDesktop.capabilities probe/request/state works per-capability with unit tests over the state machine and contextIsolation asserted (renderer cannot reach ipc channels outside the namespace); shell POSTs a capability manifest to a new loopback /api/desktop/register (per-session shell_token; misuse -> 403 + SEL) and GET /api/desktop/state reflects availability (absent/'not connected' in a browser tab); Settings -> Security -> Desktop capabilities panel renders truthful per-capability state with request buttons; a fixture app with desktop:['native_notifications'] is enforced like api/events (missing cap -> 403 + SEL capability_denied) and shown on the install consent surface. (Owner task 2 approves the consent copy.)

### `DC-3` — S3: Live audio — push-to-talk mic capture to STT

**Status:** todo

Session 3 — Live audio (T3.1-T3.3, V3)

**Done when:** global-hotkey push-to-talk (bridge global_hotkey cap, chord configurable in Settings) captures only while held/toggled with an always-on capturing indicator; renderer getUserMedia (TCC via bridge grant) chunk-uploads to existing /api/stt/transcribe and a spoken sentence lands in the composer at cursor <=2s after release on faster-whisper local; system-audio probe returns unavailable with reason and docs/guides/desktop.md states mic-only; deny-mic path degrades with an actionable prompt and an already-registered-chord conflict surfaces cleanly. (Owner tasks 3/4: mic-privacy sanity pass + default chord.)

**Implemented (2026-08-16) — status stays `todo` for V3.**

- `desktop/pushToTalk.js` (new) — chord grammar + bind/conflict state machine over an injected
  `globalShortcut`. `bind()` releases ours first (so re-saving the same chord is not a conflict with
  itself) and separates **conflict** ("another app owns it") from **invalid**, because the two need
  different sentences in Settings. A bare modifier-less chord is REFUSED: it would be taken from
  every app on the machine. 35 `node --test` cases.
- **The shell never opens the microphone.** It forwards the press; the renderer owns the stream. So
  the menu-bar indicator is driven by the renderer's `setCapturing` report, never by "we sent a
  press" — an indicator bound to intent would stay lit when a denied mic meant no stream ever
  opened. A source rail asserts the module contains no capture API at all.
- `desktop/preload.js` — `pclawDesktop.pushToTalk` = `bind`/`setCapturing`/`on`, and deliberately
  NO `start()`: nothing on the bridge can open the microphone.
- `desktop/main.js` — the menu-bar item becomes `● Listening` while capturing (title, not just a
  tooltip: an indicator you must hover to discover is not an indicator), and `before-quit` unbinds
  the chord and clears the indicator.
- `web/src/lib/pushToTalk.ts` (new) — the renderer seam: subscribes to presses, reports the real
  capture state back, binds the configured chord, and asks for the mic grant through the bridge
  BEFORE opening a stream. Inert in a browser tab.
- `web/src/ui/MicCaptureChip.tsx` + `ShortcutRecorder.tsx` (new, both with doc objects) — the
  in-app indicator, and the click-then-press chord control (extracted to `ui/` rather than bumping
  the primitive-adoption ratchet: the keyboard semantics are the reusable part).
- `voice.push_to_talk_chord` — five-point round trip + `config-baseline.json` +
  `docs/reference/configuration.md` (which had no `voice.*` section at all; all seven fields are
  now documented).
- `system_audio` capability — probes `unavailable` WITH the Screen-Recording reason on EVERY
  platform, checked before the platform test so it never reads as an unfinished port.
  `docs/guides/desktop.md` (new) says mic-only in prose, and a test asserts doc and probe agree.

**Remaining:**
- **V3 on-device round-trip.** Electron was never launched, so THREE legs are unexercised: that
  macOS delivers the chord (`globalShortcut` is stubbed), the real TCC prompt, and a real
  microphone (jsdom has neither `getUserMedia` nor `MediaRecorder`, so both are fakes). The
  renderer path itself IS driven end to end, including that every track is stopped on release.
- **Owner tasks 3 and 4** — the mic-privacy sanity pass, and approving the default chord.

**DEVIATION (T3.1 — the chord toggles, it does not read key-release).** Electron's
`globalShortcut` delivers one callback per press and exposes no global key-up event, so "captures
only while held" is unreachable without an accessibility-class input tap this plan deliberately
does not request. The `done_when` says "held/**toggled**", so the toggled half ships, labelled
honestly in the guide and in Settings. A toggle has a failure mode a hold does not — press it and
walk away — so a 2-minute capture ceiling asks the renderer to stop, bounding a forgotten toggle.

### `DC-4` — S4: Tray/menu-bar presence + login-item + graceful quit

**Status:** todo

Session 4 — Presence + platforms, T4.1 (tray/menu companion) + T4.3 (login item + quit); coordinates with AMBIENT-SURFACES tile registry when available

**Done when:** tray/menu-bar icon+menu shows pending-approvals count (click-through deep-links into the SPA), running loops, quick-capture note->inbox, open dashboard, quit; counts live-update over the loopback WS/API; login-item toggle (Settings via bridge) survives reboot; graceful gateway shutdown on quit leaves no orphan gateway (process table verified). AMBIENT-SURFACES menu-bar tiles render here only when that plan is available (non-blocking).

### `DC-5` — S4: Native notifications as a plan-42 rules target

**Status:** todo

Session 4 — Presence + platforms, T4.2 (native notifications target); Integration points ('native' notification target)

**Done when:** a notification rule with target `native` fires an Electron OS Notification when the desktop shell is connected and the tap focuses the relevant surface; falls back to dashboard toasts when the shell is not connected.

### `DC-6` — S4: Windows/Linux electron-builder targets (PLATFORM-REACH-gated)

**Status:** todo

Session 4 — Presence + platforms, T4.4 (Windows/Linux targets, gated); design S4 ('desktop follows platform support, never leads it')

**Done when:** either Windows/Linux electron-builder targets ship with per-OS signing docs once PLATFORM-REACH's corresponding rung is proven, OR a dated DEFERRED note records the exact gate condition.

