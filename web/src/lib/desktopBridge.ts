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
