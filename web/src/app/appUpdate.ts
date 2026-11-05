import { useCallback, useEffect, useState } from 'react'
import { desktopBridge, type AppUpdateStart, type AppUpdateState } from '../lib/desktopBridge'

/** The desktop app's own update, as a page of the app holds it. */
export interface AppUpdate {
  /** Where it stands, or null until the app first says. */
  state: AppUpdateState | null
  /** Check now, whatever the automatic checks are set to. */
  check: () => Promise<void>
  /** Update: download and check *version*, the one on offer, in the background. */
  download: (version: string) => Promise<AppUpdateStart>
  /** Restart to finish the update to *version*, the one ready, once the confirm named it. */
  finish: (version: string) => Promise<AppUpdateStart>
}

const NO_ANSWER = 'The app did not answer. Try again, or install from the releases page.'

/** The desktop app's own update (`desktop/appUpdate.js`), followed as it moves on: null outside the
 *  app's windows on this computer's gateway (a browser tab, a paired gateway's page, a shell from
 *  before it), where nothing about this app can be asked. */
export function useAppUpdate(): AppUpdate | null {
  // Read once: the bridge does not come or go while a page is open.
  const [bridge] = useState(() => desktopBridge()?.appUpdate ?? null)
  const [state, setState] = useState<AppUpdateState | null>(null)
  useEffect(() => {
    if (!bridge) return
    let live = true
    bridge.status().then((s) => { if (live) setState(s) }).catch(() => { /* its next push says where it stands */ })
    const off = bridge.on((s) => { if (live) setState(s) })
    return () => { live = false; off() }
  }, [bridge])
  const check = useCallback(async () => {
    if (!bridge) return
    try { setState(await bridge.check()) } catch { /* the state it pushes says what happened */ }
  }, [bridge])
  const download = useCallback(async (version: string): Promise<AppUpdateStart> => {
    if (!bridge) return { ok: false, sentence: NO_ANSWER }
    try { return await bridge.download(version) } catch { return { ok: false, sentence: NO_ANSWER } }
  }, [bridge])
  const finish = useCallback(async (version: string): Promise<AppUpdateStart> => {
    if (!bridge) return { ok: false, sentence: NO_ANSWER }
    try { return await bridge.finish(version) } catch { return { ok: false, sentence: NO_ANSWER } }
  }, [bridge])
  return bridge ? { state, check, download, finish } : null
}
