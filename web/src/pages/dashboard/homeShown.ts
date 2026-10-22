/** Which groups Home shows, kept per browser like the sidebar's state: all three, or one of them,
 *  so a narrow window can show the one it is for. A view choice only: the toolbar's badge counts
 *  everything that waits on you whatever this hides. */
export const SHOWN_KEY = 'home-groups'

/** The stored choice, or `all` for none, one this build does not know, or an origin without storage. */
export function readShown<G extends string>(groups: readonly G[]): 'all' | G {
  try {
    const v = localStorage.getItem(SHOWN_KEY)
    return v && (groups as readonly string[]).includes(v) ? v as G : 'all'
  } catch {
    return 'all'
  }
}

export function writeShown(v: string) {
  try {
    if (v === 'all') localStorage.removeItem(SHOWN_KEY)
    else localStorage.setItem(SHOWN_KEY, v)
  } catch { /* an opaque origin keeps it for this visit only */ }
}
