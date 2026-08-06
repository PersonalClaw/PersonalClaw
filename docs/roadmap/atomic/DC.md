# DESKTOP-CAPABILITIES — atomic plans

**Source plan:** [`DESKTOP-CAPABILITIES`](../plans/DESKTOP-CAPABILITIES.md)  
**Code:** `DC`  
**Source status:** proposed

6 atoms, none started (plan is DESIGNED, no shipped work). DC-2 is the independently-startable capability-bridge seam-owner; DC-3/4/5 hang off it; DC-1 carries the mac signing/updater pipeline; DC-5 and DC-6 carry the two cross-plan gates (INBOX-NOTIF, PLATFORM-REACH).

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `DC-1` | ⬜ | S1: Rebuild + signing + notarization + electron-updater + install-kind | `EXT:CI-RELEASE-ENGINEERING:signing/build stage in release.yml pipeline`, `EXT:DISTRIBUTION:release artifacts + detect_install_kind desktop branch` | CI mac-runner job builds `make desktop` -> electron-builder signed+notarized dmg/zip attached to the GitHub Release (spctl -a passes on a clean Mac); electron-updater checks Releases and prompts a one-version-behind install; shell sets PERSONALCLAW_INSTALL_KIND=desktop and the gateway Updates panel shows 'managed by the desktop app'; desktop/test/packaging.test.js green; V1 clean-machine install+update recorded. (Owner task 1 supplies the four Apple Developer signing secrets to the CI `release` environment.) |
| `DC-2` | ⬜ | S2: Typed capability bridge + gateway seam + Settings panel + app perm | — | window.pclawDesktop.capabilities probe/request/state works per-capability with unit tests over the state machine and contextIsolation asserted (renderer cannot reach ipc channels outside the namespace); shell POSTs a capability manifest to a new loopback /api/desktop/register (per-session shell_token; misuse -> 403 + SEL) and GET /api/desktop/state reflects availability (absent/'not connected' in a browser tab); Settings -> Security -> Desktop capabilities panel renders truthful per-capability state with request buttons; a fixture app with desktop:['native_notifications'] is enforced like api/events (missing cap -> 403 + SEL capability_denied) and shown on the install consent surface. (Owner task 2 approves the consent copy.) |
| `DC-3` | ⬜ | S3: Live audio — push-to-talk mic capture to STT | `DC-2` | global-hotkey push-to-talk (bridge global_hotkey cap, chord configurable in Settings) captures only while held/toggled with an always-on capturing indicator; renderer getUserMedia (TCC via bridge grant) chunk-uploads to existing /api/stt/transcribe and a spoken sentence lands in the composer at cursor <=2s after release on faster-whisper local; system-audio probe returns unavailable with reason and docs/guides/desktop.md states mic-only; deny-mic path degrades with an actionable prompt and an already-registered-chord conflict surfaces cleanly. (Owner tasks 3/4: mic-privacy sanity pass + default chord.) |
| `DC-4` | ⬜ | S4: Tray/menu-bar presence + login-item + graceful quit | `DC-2` | tray/menu-bar icon+menu shows pending-approvals count (click-through deep-links into the SPA), running loops, quick-capture note->inbox, open dashboard, quit; counts live-update over the loopback WS/API; login-item toggle (Settings via bridge) survives reboot; graceful gateway shutdown on quit leaves no orphan gateway (process table verified). AMBIENT-SURFACES menu-bar tiles render here only when that plan is available (non-blocking). |
| `DC-5` | ⬜ | S4: Native notifications as a plan-42 rules target | `DC-2`, `EXT:INBOX-NOTIFICATIONS-UNIFICATION:native notification target registered in the rules engine` | a notification rule with target `native` fires an Electron OS Notification when the desktop shell is connected and the tap focuses the relevant surface; falls back to dashboard toasts when the shell is not connected. |
| `DC-6` | ⬜ | S4: Windows/Linux electron-builder targets (PLATFORM-REACH-gated) | `DC-1`, `EXT:PLATFORM-REACH:non-mac backend proven on the target OS` | either Windows/Linux electron-builder targets ship with per-OS signing docs once PLATFORM-REACH's corresponding rung is proven, OR a dated DEFERRED note records the exact gate condition. |

## Atom scopes

### `DC-1` — S1: Rebuild + signing + notarization + electron-updater + install-kind

**Status:** todo

Session 1 — Rebuild + signing + updater (T1.1-T1.4, V1); Contract C4 (install-kind + updater)

**Done when:** CI mac-runner job builds `make desktop` -> electron-builder signed+notarized dmg/zip attached to the GitHub Release (spctl -a passes on a clean Mac); electron-updater checks Releases and prompts a one-version-behind install; shell sets PERSONALCLAW_INSTALL_KIND=desktop and the gateway Updates panel shows 'managed by the desktop app'; desktop/test/packaging.test.js green; V1 clean-machine install+update recorded. (Owner task 1 supplies the four Apple Developer signing secrets to the CI `release` environment.)

### `DC-2` — S2: Typed capability bridge + gateway seam + Settings panel + app perm

**Status:** todo

Session 2 — Capability bridge (T2.1-T2.4, V2); Contracts C1 (preload contextBridge), C2 (gateway desktop route), C3 (app manifest desktop perm)

**Done when:** window.pclawDesktop.capabilities probe/request/state works per-capability with unit tests over the state machine and contextIsolation asserted (renderer cannot reach ipc channels outside the namespace); shell POSTs a capability manifest to a new loopback /api/desktop/register (per-session shell_token; misuse -> 403 + SEL) and GET /api/desktop/state reflects availability (absent/'not connected' in a browser tab); Settings -> Security -> Desktop capabilities panel renders truthful per-capability state with request buttons; a fixture app with desktop:['native_notifications'] is enforced like api/events (missing cap -> 403 + SEL capability_denied) and shown on the install consent surface. (Owner task 2 approves the consent copy.)

### `DC-3` — S3: Live audio — push-to-talk mic capture to STT

**Status:** todo

Session 3 — Live audio (T3.1-T3.3, V3)

**Done when:** global-hotkey push-to-talk (bridge global_hotkey cap, chord configurable in Settings) captures only while held/toggled with an always-on capturing indicator; renderer getUserMedia (TCC via bridge grant) chunk-uploads to existing /api/stt/transcribe and a spoken sentence lands in the composer at cursor <=2s after release on faster-whisper local; system-audio probe returns unavailable with reason and docs/guides/desktop.md states mic-only; deny-mic path degrades with an actionable prompt and an already-registered-chord conflict surfaces cleanly. (Owner tasks 3/4: mic-privacy sanity pass + default chord.)

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

