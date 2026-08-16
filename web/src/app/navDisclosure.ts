import { useCallback, useEffect, useState } from 'react'

/** Progressive disclosure over the rail (ONBOARDING-UX C4).
 *
 *  The rail carries 18 static destinations. That is the right surface area for someone who
 *  knows the product and a wall for someone who does not, so the rail has two states:
 *
 *    starter  — the five surfaces a first run actually needs, PLUS every surface this
 *               browser has since visited (see auto-pin below).
 *    expert   — everything, permanently.
 *
 *  **Hiding is never gating.** Disclosure governs the RAIL ONLY. Every route stays routable:
 *  a deep link, a CommandPalette "Go to", a Discover tip and an in-app link all render their
 *  surface whatever the mode is — and a visit to a hidden surface PINS it, so the rail grows
 *  with use instead of asking to be configured. `navDisclosure.test.tsx` reds if any of that
 *  stops being true.
 *
 *  ## Where this is stored, and why here
 *
 *  `localStorage['nav-disclosure']`, alongside `nav-collapsed`, `nav-width-v2` and `nav-apps`.
 *  Nav shape is a per-DEVICE preference in this app and `identity.tsx` says so in as many
 *  words ("Per-device prefs (theme, width, nav state) stay in localStorage; identity does
 *  not") — a 13" laptop and a 32" monitor want different rails, and the pin set is a record
 *  of what you reached from THIS browser. It is also why auto-pin survives a reload: the pin
 *  is written the moment the surface renders, not on some later save.
 *
 *  ## The upgrade marker IS the record's absence
 *
 *  C4 asks for an "onboarding-completed-before-this-version marker" so an existing install
 *  keeps its full rail. That marker needs no new field: `Onboarding`'s finish step writes
 *  `mode: 'starter'` (the one act that can only happen on a fresh install — it is what
 *  commits identity and flips `onboarded`), so **a stored record means "onboarded under this
 *  version" and no record means "onboarded before it"**. Absence therefore resolves to
 *  `expert`, which is also the safe direction to fail in: an unreadable or absent preference
 *  shows every surface rather than hiding surfaces someone has been using for months.
 */
export type NavMode = 'starter' | 'expert'

/** The starter rail: land, talk, see what needs you, get more, configure.
 *
 *  The Design's set is "Chat, Inbox, Apps, Settings"; `dashboard` (Home) is added because it
 *  is the app's LANDING route (`useHashRoute('dashboard')`) and a rail that omits the page it
 *  opens on is a defect rather than a decision. Recorded as a deviation in the plan's log. */
export const STARTER_NAV_IDS: readonly string[] = ['dashboard', 'chat', 'inbox', 'apps', 'settings']

export interface NavDisclosure {
  mode: NavMode
  /** Static rail ids revealed by having been visited. Order is arrival order; the rail
   *  renders them in its own NAV order regardless. */
  pinned: string[]
}

const KEY = 'nav-disclosure'
const EVENT = 'ne:nav-disclosure'

/** No record at all → show everything (see "the upgrade marker" above). */
const ABSENT: NavDisclosure = { mode: 'expert', pinned: [] }

export function readNavDisclosure(): NavDisclosure {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return { ...ABSENT }
    const v = JSON.parse(raw) as Partial<NavDisclosure>
    return {
      // A record that exists but names no mode is still a record — it was written by this
      // app, so it means "onboarded under this version" and starter is the honest read.
      mode: v?.mode === 'expert' ? 'expert' : 'starter',
      pinned: Array.isArray(v?.pinned) ? v.pinned.filter((x): x is string => typeof x === 'string') : [],
    }
  } catch { return { ...ABSENT } }
}

function write(next: NavDisclosure): void {
  try { localStorage.setItem(KEY, JSON.stringify(next)) } catch { /* private mode — the rail still works */ }
  window.dispatchEvent(new CustomEvent(EVENT))
}

/** Set the rail mode. Called by the Appearance toggle, the rail's own disclosure control,
 *  and once by `Onboarding`'s finish step (which is what marks a fresh install). */
export function setNavMode(mode: NavMode): void {
  write({ ...readNavDisclosure(), mode })
}

/** Reveal a surface permanently because it was reached. Idempotent. */
export function pinNavSurface(id: string): void {
  const cur = readNavDisclosure()
  if (cur.pinned.includes(id)) return
  write({ ...cur, pinned: [...cur.pinned, id] })
}

/** Subscribe to disclosure changes — same contract as `navApps`' (in-tab event plus the
 *  cross-tab `storage` event, so two windows of the same browser agree). */
export function onNavDisclosureChange(cb: () => void): () => void {
  window.addEventListener(EVENT, cb)
  const onStorage = (e: StorageEvent) => { if (e.key === KEY) cb() }
  window.addEventListener('storage', onStorage)
  return () => { window.removeEventListener(EVENT, cb); window.removeEventListener('storage', onStorage) }
}

/** Does this rail id show, given the mode and pin set? The single rule, so the rail's
 *  filter, the "how many more" count and the auto-pin trigger cannot disagree. */
export function isDisclosed(id: string, mode: NavMode, pinned: readonly string[]): boolean {
  if (mode === 'expert') return true
  // A contributed app's tile is ALREADY an explicit per-app pin ("Show in navigation" on the
  // app's detail panel, persisted in `nav-apps`), so disclosure has nothing to reveal for it —
  // hiding it would silently undo a choice the user made by hand.
  if (id.startsWith('app/')) return true
  if (STARTER_NAV_IDS.includes(id)) return true
  return pinned.includes(id)
}

/** How many of these ids the starter rail holds back — i.e. what "Everything" reveals.
 *  Computed against `'starter'` whatever the current mode is, so the control can say what
 *  collapsing would cost while the rail is expanded. */
export function undisclosedCount(ids: readonly string[], pinned: readonly string[]): number {
  return ids.filter((id) => !isDisclosed(id, 'starter', pinned)).length
}

/** The live disclosure state. Reads synchronously on mount (no probe, no flash) and
 *  re-reads on every change from this tab or another. */
export function useNavDisclosure(): NavDisclosure & { setMode: (m: NavMode) => void; pin: (id: string) => void } {
  const [state, setState] = useState<NavDisclosure>(readNavDisclosure)
  useEffect(() => onNavDisclosureChange(() => setState(readNavDisclosure())), [])
  const setMode = useCallback((m: NavMode) => setNavMode(m), [])
  const pin = useCallback((id: string) => pinNavSurface(id), [])
  return { ...state, setMode, pin }
}
