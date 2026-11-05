import { api, type InstallKind, type UpdateApplyAnswer, type UpdateCheck } from '../lib/api'
import { fetchKey, peekEntry } from '../lib/data/store'
import { useQuery } from '../lib/data/useQuery'

/** The update check's one cache key, which Settings › Updates reads too. */
const UPDATE_CHECK_KEY = 'settings:update-check'

/** The installs PersonalClaw does not update in place. `/api/update` answers them with how they
 *  are updated (`status: 'instructions'`: pull the new image, or install the new app from the
 *  releases page), and nothing restarts. */
export const UPDATED_OUTSIDE: ReadonlySet<InstallKind> = new Set(['container', 'desktop'])

/** How this PersonalClaw was installed, from the update check Settings › Updates reads (one cache
 *  key); undefined while it loads or when it cannot be read. */
export function useInstallKind(): InstallKind | undefined {
  return useQuery(UPDATE_CHECK_KEY, () => api.updateCheck(), { persist: true }).data?.kind
}

/** How this PersonalClaw was installed, read when an update is asked for on a surface that does not
 *  otherwise read it (Home): the update check's answer as it was last read, at any age, since an
 *  install's kind does not change while it runs, else one read of it. Undefined when it cannot be
 *  read: the update then asks first, as on an install that restarts. */
export async function readInstallKind(): Promise<InstallKind | undefined> {
  const known = peekEntry<UpdateCheck>(UPDATE_CHECK_KEY)?.value?.kind
  if (known) return known
  try {
    return (await fetchKey(UPDATE_CHECK_KEY, () => api.updateCheck(), true)).kind
  } catch {
    return undefined
  }
}

/** Whether *kind* is an install PersonalClaw does not update in place (`UPDATED_OUTSIDE`). */
export function updatedOutside(kind: InstallKind | undefined): boolean {
  return kind !== undefined && UPDATED_OUTSIDE.has(kind)
}

/** Ask the gateway to update this install.
 *
 *  An update that starts answers `null`: the shell's UpdateProgressOverlay shows its progress. A
 *  container or desktop install answers with how it is updated instead, which this returns for
 *  the caller to say where it was asked for (the System popover inline, Home in a dialog), since a
 *  toast is gone before its link can be followed. Dropped, the press did nothing at all. A start
 *  the gateway refuses (an update already running, a checkout with local changes) pushes no
 *  progress, so its reason is a toast, and this answers `null`. */
export async function applyUpdate(): Promise<UpdateApplyAnswer | null> {
  try {
    const answer = await api.applyUpdate()
    return answer?.status === 'instructions' ? answer : null
  } catch (e) {
    const msg = e instanceof Error && e.message ? e.message : 'Request failed'
    window.dispatchEvent(new CustomEvent('ne:toast', { detail: { level: 'error', message: `Update blocked: ${msg}` } }))
    return null
  }
}
