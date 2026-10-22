/** The renderer's typed view of the desktop shell bridge.
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

/** The OS's answer about "open PersonalClaw at login".
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

/** What the shell says about a page it was asked to open in the system's default browser.
 *  `ok: false` is an ANSWER, not a throw: the shell opens only an http or https page, and says
 *  why when the browser did not open, so the caller can offer the link to open by hand. */
export interface SystemBrowserResult {
  ok: boolean
  reason?: string
}

/** Where the buddy's window sits on this machine. */
export interface BuddyPlace { mode: 'docked' | 'floating'; displayId?: number }

export interface DesktopBridge {
  onStatus?: (cb: (msg: string) => void) => () => void
  /** Push-to-talk. Note what is NOT here: no `start()`. The shell cannot open
   *  the microphone — it can only tell the renderer the chord fired. `setCapturing`
   *  runs the other way: the renderer reporting the stream it owns, which is what
   *  lights the menu-bar indicator. */
  pushToTalk?: {
    bind: (chord: string) => Promise<ChordBindResult>
    /** The microphone's state, with the capture source that holds it (`lib/pushToTalk`). */
    setCapturing: (on: boolean, source?: string) => Promise<boolean>
    on: (cb: (push: { action: 'toggle' | 'stop'; reason?: string }) => void) => () => void
    /** Whether the microphone is live in any window, and which sources hold it (#3833). */
    onListening?: (cb: (state: { on: boolean; sources: string[] }) => void) => () => void
  }
  /** Native OS notifications — the `native` delivery target. Deliberately not
   *  a general "notify the user" API: `show()` is called only for a gateway note whose rule
   *  named the target and whose `native.deliver` came back true, so the policy stays in the
   *  rules engine. `on()` fires when a banner is TAPPED — the shell has already focused the
   *  window, and the payload carries the route because the renderer owns the SPA's IA. */
  notifications?: {
    /** A buddy alert (#3897) names its kind and carries its still orb as a PNG data URL; the
     *  shell raises it silent, with that icon. */
    show: (note: { title: string; body: string; route: string; alert?: string; icon?: string }) => Promise<NativeNotifyResult>
    on: (cb: (payload: { route: string }) => void) => () => void
  }
  /** "Open at login". Deliberately NOT under `capabilities`: that vocabulary is
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
  /** The system's default browser, for a page that is not the gateway's. The shell sends every
   *  such page there anyway (a link, a window a page asks for, a navigation) and refuses to load
   *  one in its own windows, a blank window included; `open()` is that rule as a call that
   *  answers, for a page that has to know whether the browser opened. Optional like `loginItem`:
   *  a shell built before it existed has no such namespace. */
  systemBrowser?: {
    open: (url: string) => Promise<SystemBrowserResult>
  }
  /** This machine's buddy rows in Settings (#3901): where the buddy's window sits and whether it
   *  is kept out of screen capture. In the shell's store, not the gateway's config; absent in a
   *  shell from before them, so Settings shows these rows only when it is here. */
  buddy?: {
    getPlace: () => Promise<BuddyPlace>
    /** Back to the dock; resolves where it is now. */
    resetPlace: () => Promise<BuddyPlace>
    /** Null where this computer cannot keep a window out of a capture (Linux). */
    getHideFromCapture: () => Promise<boolean | null>
    /** Resolves the value that holds after the change; null where it cannot be set. */
    setHideFromCapture: (on: boolean) => Promise<boolean | null>
  }
  capabilities: {
    names: () => string[]
    probe: (cap: string) => Promise<DesktopCapabilityWire>
    snapshot: () => Promise<Record<string, DesktopCapabilityWire>>
    request: (cap: string) => Promise<DesktopGrantResult>
    on: (cap: string, cb: (state: DesktopCapabilityWire) => void) => () => void
  }
}

/** A rectangle in the buddy window's own points. */
export interface BuddyRect { x: number; y: number; width: number; height: number }

/** The buddy's room: `hidden` hides its window until the shell shows it again. */
export type BuddyPresentation = 'hidden' | 'peek' | 'compact' | 'expanded'

/** The doorbell's connection, as the shell tells the buddy's page (`desktop/doorbell.js`). */
export type BuddyConnection = 'up' | 'lost' | 'restarting'

/** Where the buddy's window is: a drag moving it, or where it settled (`desktop/buddyWindow.js`). */
export type BuddyPlacement = 'moving' | 'docked' | 'floating'

/** The buddy window's one fixed shape (#5216), in the window's own points: the orb's box, and the
 *  area its card may use beside it, on the side the shell chose where the orb settled. The card is
 *  anchored to the orb and grows away from it: down from the orb's top, or up from its bottom. */
export interface BuddyLayout {
  side: 'right' | 'left' | 'below' | 'above'
  opens: 'down' | 'up'
  orb: BuddyRect
  card: BuddyRect
}

/** `window.pclawBuddy`: only in the buddy's own window (`desktop/buddyPreload.js`, #3831), never
 *  beside `pclawDesktop`. Eight sends, which the shell checks each one again, and its pushes back
 *  (#3832, #5216): its doorbell's ring, which carries nothing, and connection; where the window is
 *  and its shape; and whether one of the app's own windows is full screen, the owner is away, or
 *  hid the buddy. */
export interface BuddyBridge {
  // The page is served by the gateway and can be newer than the shell that loads it, so each call
  // the shell added after the window's first seven is optional: a page reads it only where it is.
  /** macOS: the forwarded pointer is over the face or the open card (take clicks), or not. */
  pointerOver: (hit: boolean) => void
  /** Linux: the face's and the card's rectangles are the window's shape. */
  setRegions: (rects: BuddyRect[]) => void
  dragStart: () => void
  dragBy: (dx: number, dy: number) => void
  /** `instant` under reduced motion: the dock or the float lands without a move. */
  dragEnd: (instant: boolean) => void
  setPresentation: (presentation: BuddyPresentation) => void
  /** Open a route of this app (a chat, a run) in the main window, as a hash route. */
  openInApp: (route: string) => void
  /** Hear main ring the buddy open: a tap on an alert's notification (#3897). The ring carries
   *  nothing. Returns an unsubscribe. Absent from a shell built before it. */
  onOpenAlert?: (cb: () => void) => () => void
  /** The doorbell rang: something running or waiting changed, so read the lists again. Returns
   *  the way to stop hearing it. */
  onRing?: (cb: () => void) => () => void
  /** The doorbell's connection changed. */
  onConnection?: (cb: (state: BuddyConnection) => void) => () => void
  /** A drag moved the window (`moving`), or it settled, docked or floating; and, on each load,
   *  where it is. */
  onPlacement?: (cb: (placement: BuddyPlacement) => void) => () => void
  /** One of the app's own windows went full screen, or left it; and, on each load, whether one is. */
  onFullScreen?: (cb: (on: boolean) => void) => () => void
  /** The owner went away (the system's idle time, a locked screen, sleep) or came back; and, on
   *  each load, whether they are away. */
  onAway?: (cb: (away: boolean) => void) => () => void
  /** The owner hid the buddy for a while, or the hide ended; and, on each load, whether it holds. */
  onHidden?: (cb: (hidden: boolean) => void) => () => void
  /** The buddy's context menu, at the pointer: hide it for a while, or open PersonalClaw. */
  openMenu?: () => void
  /** The window's shape: on each load, and again when a drop or a display change moves the orb to
   *  another side of its card. Absent from a shell that sizes the window to the room instead. */
  onLayout?: (cb: (layout: BuddyLayout) => void) => () => void
}

declare global {
  interface Window {
    pclawDesktop?: DesktopBridge
    pclawBuddy?: BuddyBridge
  }
}

/** The bridge, or null in a browser tab. */
export function desktopBridge(): DesktopBridge | null {
  return (typeof window !== 'undefined' && window.pclawDesktop) || null
}

/** The buddy window's bridge, or null anywhere else (a browser tab, the main window). */
export function buddyBridge(): BuddyBridge | null {
  return (typeof window !== 'undefined' && window.pclawBuddy) || null
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

/** Whether the shell can open a page in the system browser and say if it did. Asked before
 *  anything is opened: a browser tab has to be opened inside a click, and the desktop app must
 *  not be asked for one. */
export function hasSystemBrowser(): boolean {
  return !!desktopBridge()?.systemBrowser
}

/** Open `url` in the system's default browser through the shell.
 *
 *  Null means there is nobody to ask (a browser tab, or a shell without the namespace). A thrown
 *  IPC error resolves to a refusal with its reason: an unanswered call is not an opened browser. */
export async function openInSystemBrowser(url: string): Promise<SystemBrowserResult | null> {
  const bridge = desktopBridge()
  if (!bridge?.systemBrowser) return null
  try {
    return await bridge.systemBrowser.open(url)
  } catch (e) {
    return { ok: false, reason: e instanceof Error ? e.message : 'the desktop app did not answer' }
  }
}
