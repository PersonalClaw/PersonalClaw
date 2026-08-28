/** The renderer's typed view of the desktop shell bridge (DC-2 C1).
 *
 *  `window.pclawDesktop` exists ONLY inside the Electron shell — `preload.js` exposes
 *  it over `contextBridge` with `contextIsolation: true`. In a browser tab it is
 *  undefined, and every helper here returns a value that says so rather than throwing
 *  or pretending. One module owns the `declare global`, so no surface hand-rolls an
 *  `as any` cast to reach the shell.
 *
 *  There is deliberately no token accessor: the gateway `shell_token` lives in the
 *  Electron MAIN process, so page JS has no path to it even if a page is compromised.
 */
import type { DesktopCapabilityWire } from './api'

export interface DesktopGrantResult {
  granted: boolean
  state: DesktopCapabilityWire['granted']
  /** True only on the one transition that can raise an OS dialog
   *  (`not-determined` → granted/denied), so a caller can tell "you were asked and
   *  said no" from "the OS will not ask again". */
  prompted: boolean
  reason: string
}

/** The result of asking the shell to bind a global chord. `conflict` separates "another
 *  app already owns that chord" from "that is not a valid chord" — they need different
 *  sentences, and collapsing them is how a user retypes a perfectly good shortcut. */
export interface ChordBindResult {
  ok: boolean
  chord: string
  conflict: boolean
  reason: string
}

/** What the shell says about one raised banner. `ok: false` is an ANSWER, not a throw —
 *  the caller keeps the in-app bell rather than losing the note (`reason` says why: the OS
 *  refused, or the note had no title). */
export interface NativeNotifyResult {
  ok: boolean
  route: string
  reason?: string
}

/** The OS's answer about "open PersonalClaw at login" (DC-4).
 *
 *  The truth lives in the OS, not in `config.json`: the shell reads it back out of
 *  `app.getLoginItemSettings()` on every call, and the user can remove the registration in
 *  System Settings → General → Login Items while PersonalClaw is not even running. A
 *  mirrored config field would be a second source for one fact and would go stale the
 *  first time that happened, so there is deliberately no config key behind this.
 *
 *  `supported: false` is a PLATFORM statement, not a failure — Electron implements login
 *  items on macOS and Windows only. `describes` names exactly what the toggle touches, so
 *  Settings can say it before the user flips a persistent change to their machine. */
export interface LoginItemState {
  enabled: boolean
  supported: boolean
  describes: string
}

/** The result of writing the login item. `ok: true, changed: false` is the idempotent
 *  no-op (it was already in the requested state, so nothing was written and no second
 *  entry could be minted). `ok: false` carries `reason` and is an ANSWER — the caller
 *  renders the OS's refusal instead of leaving a toggle that claims a registration that
 *  does not exist. `enabled` is always what the OS reports AFTER the write, never what was
 *  asked for. */
export interface LoginItemResult {
  ok: boolean
  enabled: boolean
  changed: boolean
  supported: boolean
  reason?: string
}

export interface DesktopBridge {
  onStatus?: (cb: (msg: string) => void) => () => void
  /** Push-to-talk (DC-3). Note what is NOT here: no `start()`. The shell cannot open
   *  the microphone — it can only tell the renderer the chord fired. `setCapturing`
   *  runs the other way: the renderer reporting the stream it owns, which is what
   *  lights the menu-bar indicator. */
  pushToTalk?: {
    bind: (chord: string) => Promise<ChordBindResult>
    setCapturing: (on: boolean) => Promise<boolean>
    on: (cb: (push: { action: 'toggle' | 'stop'; reason?: string }) => void) => () => void
  }
  /** Native OS notifications (DC-5) — plan-42's `native` delivery target. Deliberately not
   *  a general "notify the user" API: `show()` is called only for a gateway note whose rule
   *  named the target and whose `native.deliver` came back true, so the policy stays in the
   *  rules engine. `on()` fires when a banner is TAPPED — the shell has already focused the
   *  window, and the payload carries the route because the renderer owns the SPA's IA. */
  notifications?: {
    show: (note: { title: string; body: string; route: string }) => Promise<NativeNotifyResult>
    on: (cb: (payload: { route: string }) => void) => () => void
  }
  /** "Open at login" (DC-4). Deliberately NOT under `capabilities`: that vocabulary is
   *  ratcheted to probe/request/snapshot and answers "may we?", while this answers
   *  "should we?" — a preference, not an OS permission.
   *
   *  Optional like the rest, and for a second reason beyond the browser: a shell built
   *  before this namespace existed exposes no `loginItem`, and a Settings surface that
   *  assumed it would call through `undefined`. Absent here reads the same as absent
   *  bridge — say so, do not offer the control. */
  loginItem?: {
    get: () => Promise<LoginItemState>
    set: (enabled: boolean) => Promise<LoginItemResult>
  }
  capabilities: {
    names: () => string[]
    probe: (cap: string) => Promise<DesktopCapabilityWire>
    snapshot: () => Promise<Record<string, DesktopCapabilityWire>>
    request: (cap: string) => Promise<DesktopGrantResult>
    on: (cap: string, cb: (state: DesktopCapabilityWire) => void) => () => void
  }
}

declare global {
  interface Window {
    pclawDesktop?: DesktopBridge
  }
}

/** The bridge, or null in a browser tab. */
export function desktopBridge(): DesktopBridge | null {
  return (typeof window !== 'undefined' && window.pclawDesktop) || null
}

/** Ask the OS for a capability through the shell. Null when there is no shell — the
 *  caller renders "not connected" rather than a failed grant. */
export async function requestDesktopCapability(cap: string): Promise<DesktopGrantResult | null> {
  const bridge = desktopBridge()
  if (!bridge) return null
  try {
    return await bridge.capabilities.request(cap)
  } catch (e) {
    return { granted: false, state: 'unavailable', prompted: false, reason: e instanceof Error ? e.message : 'The desktop shell did not answer' }
  }
}

/** Read the login-item registration from the OS through the shell.
 *
 *  Null means "there is nobody to ask" — a browser tab, or a shell too old to carry the
 *  namespace. The caller states that rather than rendering a toggle it cannot honour.
 *  A thrown IPC error also resolves to null: an unanswered read is not a "disabled"
 *  answer, and reporting it as one is how a control comes to lie. */
export async function getLoginItem(): Promise<LoginItemState | null> {
  const bridge = desktopBridge()
  if (!bridge?.loginItem) return null
  try {
    return await bridge.loginItem.get()
  } catch {
    return null
  }
}

/** Register or un-register the login item through the shell. The SAME registration the
 *  menu-bar item's "Open at Login" checkbox drives — one mechanism, two surfaces.
 *
 *  Null means there was nobody to ask (see `getLoginItem`). Anything else is the OS's own
 *  answer read back after the write, so a caller can never be told "enabled" when nothing
 *  was registered. */
export async function setLoginItem(enabled: boolean): Promise<LoginItemResult | null> {
  const bridge = desktopBridge()
  if (!bridge?.loginItem) return null
  try {
    return await bridge.loginItem.set(enabled)
  } catch (e) {
    return {
      ok: false,
      enabled: !enabled,
      changed: false,
      supported: true,
      reason: e instanceof Error ? e.message : 'The desktop app did not answer',
    }
  }
}
