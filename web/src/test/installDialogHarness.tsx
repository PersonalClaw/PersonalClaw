import { useEffect } from 'react'
import { useAppInstall, type InstallTarget } from '../pages/apps/installConsent'
import type { AppInstallResult } from '../lib/api'

/** The one install-consent dialog, opened on `target` exactly as a Store surface opens it.
 *
 *  The review it renders is whatever the test's mocked `api.previewApp` answers, so a test
 *  states the SERVER'S review and asserts what the user is shown and what a click sends —
 *  through the shipped hook and dialog, not a stand-in for them.
 *
 *  Lives in `src/test/` beside the vitest setup file: it is test support, not app code, and
 *  nothing in the app imports it. */
export function InstallDialogHarness({ target, onInstalled }: {
  target: InstallTarget
  onInstalled?: (result: AppInstallResult) => void
}) {
  const install = useAppInstall({ onInstalled: (r) => onInstalled?.(r) })
  // Opened once, on mount — the harness IS the click that opens it.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { void install.begin(target) }, [])
  return install.dialog
}
