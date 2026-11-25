/** An older desktop app on a newer gateway checks for its own update at once.
 *
 *  The app checks at once when it finds the gateway it uses on a newer version than itself
 *  (`desktop/appUpdate.js` `noteGateway`), an automatic check: with automatic update checks off it
 *  does not. An app from before that rule (a shell whose update state has no `ready`, 0.3.7) waits
 *  for its next daily check instead, on a gateway already ahead of it, so this asks it for that
 *  check, once a gateway version a window, while the gateway still serves that app's windows
 *  (`dashboard/pair_refusals.MIN_DESKTOP_VERSION`). Once that floor passes 0.3.7, this goes with it
 *  (`appCheckOnGatewayAhead.test.tsx` fails until it does). Nothing outside the app's windows. */
import { useEffect } from 'react'
import type { AppUpdateState } from '../lib/desktopBridge'
import { compareVersions } from '../lib/versions'
import { useAppUpdate, type AppUpdate } from './appUpdate'
import { useUpdateCheck } from './applyUpdate'

/** The newest desktop app whose updater does not check on a newer gateway by itself. */
export const OLD_SHELL_UNTIL = '0.3.7'

/** Whether *state* is from an app before the two-step update, which has no such check of its own. */
export function isOldShell(state: AppUpdateState): boolean {
  return !('ready' in state) && compareVersions(state.current, OLD_SHELL_UNTIL) !== 1
}

/** The gateway versions this window has asked the app to check for. */
const asked = new Set<string>()

/** For tests: forget what was asked. */
export function resetAppCheckOnGatewayAhead(): void {
  asked.clear()
}

function Check({ update, state }: { update: AppUpdate; state: AppUpdateState }) {
  const known = useUpdateCheck()
  const gateway = known?.current ?? ''
  const checksOn = known?.check_enabled !== false
  const settled = state.phase === 'idle' || state.phase === 'current'
  useEffect(() => {
    if (!gateway || !known || !checksOn || !settled) return
    if (compareVersions(gateway, state.current) !== 1 || asked.has(gateway)) return
    asked.add(gateway)
    void update.check()
  }, [gateway, known, checksOn, settled, state.current, update])
  return null
}

export function AppCheckOnGatewayAhead() {
  const update = useAppUpdate()
  const state = update?.state
  return update && state && isOldShell(state) ? <Check update={update} state={state} /> : null
}
