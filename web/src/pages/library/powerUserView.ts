import { api, type LibrarySettings } from '../../lib/api'
import { invalidateKeys, useMutation, useQuery, writeQuery } from '../../lib/data'
import { peekQuery } from '../../lib/data/store'
import { reportActionFailure } from '../../app/reportingWrite'

// ── The Power user view's setting ─────────────────────────────────────────────────────────────────
//
// Remembered on the gateway (`GET` and `PUT /api/library/settings`), so it is the same on every
// browser, the desktop app and the phone, and read with `persist` so it paints without a flash.
// Pressing the switch, or Keep it on in the visit state, are the only things that write it.

export const LIBRARY_SETTINGS_KEY = 'knowledge:library-settings'

/** The power-user tabs, in the tab list's order, Library settings pinned last. */
export const POWER_TABS = ['graph', 'items', 'tags', 'conflicts', 'decisions', 'settings'] as const
export type PowerTab = (typeof POWER_TABS)[number]

export const isPowerTab = (v: string | undefined): v is PowerTab => (POWER_TABS as readonly string[]).includes(v ?? '')

export function usePowerUserView(): {
  /** The stored setting. */
  stored: boolean
  /** The read has answered, with a value or a failure. */
  settled: boolean
  error: unknown
  /** Write the setting. */
  save: (on: boolean) => Promise<unknown>
} {
  const { data, error } = useQuery(LIBRARY_SETTINGS_KEY, () => api.librarySettings(), { persist: true })
  const { mutate } = useMutation({
    run: (on: boolean) => api.saveLibrarySettings({ power_user_view: on }),
    invalidates: [LIBRARY_SETTINGS_KEY],
    onError: reportActionFailure('change the Power user view'),
  })
  return {
    stored: !!data?.power_user_view,
    settled: data !== undefined || !!error,
    error: data === undefined ? error : null,
    save: (on: boolean) => {
      // The switch moves at once; the read that follows the write confirms it.
      writeQuery(LIBRARY_SETTINGS_KEY, { power_user_view: on })
      return mutate(on)
    },
  }
}

/** Where the page is, from the address and the stored setting: the tab it shows (none in the
 *  regular view), and whether the view is on for this visit only, because the address names a tab
 *  while the setting is off. Any library filter key opens Items. */
export function powerUserPlace(query: Record<string, string>, stored: boolean, filtered: boolean): { tab: PowerTab | null; visit: boolean } {
  const named = isPowerTab(query.view) ? query.view : null
  if (named) return { tab: named, visit: !stored }
  if (!stored) return { tab: null, visit: false }
  return { tab: filtered || query.q?.trim() ? 'items' : 'graph', visit: false }
}

/** The stored setting as this browser last read it, without a read of its own: the palette's row
 *  says Turn on or Turn off from it. Unread, it is off, the setting's default. */
export function peekPowerUserView(): boolean {
  return !!peekQuery<LibrarySettings>(LIBRARY_SETTINGS_KEY)?.power_user_view
}

/** Write the setting from outside the page (the palette's row): at once, then confirmed by a read. */
export async function savePowerUserView(on: boolean): Promise<void> {
  writeQuery(LIBRARY_SETTINGS_KEY, { power_user_view: on })
  await api.saveLibrarySettings({ power_user_view: on }).catch(reportActionFailure('change the Power user view'))
  invalidateKeys(LIBRARY_SETTINGS_KEY)
}
