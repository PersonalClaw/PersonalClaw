/** The rig spike's harness route (#3887): `#/buddy-rig`, in a build made with `VITE_BUDDY_RIG=1`.
 *
 *  The entry (`main.tsx`) mounts the harness in place of the app when the build carries it and the
 *  page opens on this hash, so the scratch measurement window and the owner's side-by-side watch see
 *  the two candidates with nothing else drawn. A release build leaves the flag unset and carries no
 *  part of the harness (`route.test.ts`).
 *
 *  `?view=` picks what the stage shows: `both` side by side (the default, for watching), one
 *  candidate (`in-place` or `layered`, for measuring), `none` (the harness with no candidate, the
 *  baseline the measurement subtracts to find each candidate's own chunks) or `sheet` (the contact
 *  sheet: every pose at every size, still). `?size=` is the orb's size on the stage, and `?fps=` the
 *  most frames a second the candidates paint their dots at: the buddy's 30 unless it says `60`, or
 *  `0` for every frame the display draws, as the spike's first runs did. */

import { BUDDY_FPS, BUDDY_SIZES, type BuddySize } from './poses'

export const BUDDY_RIG_ROUTE = 'buddy-rig'

/** The two ways to extend the agent orb's drawing (`docs/design/buddy/rig.md`, "Candidates"). */
export type RigCandidate = 'in-place' | 'layered'
export const RIG_CANDIDATES: readonly RigCandidate[] = ['in-place', 'layered']

export type RigView = RigCandidate | 'both' | 'none' | 'sheet'
const VIEWS: readonly RigView[] = [...RIG_CANDIDATES, 'both', 'none', 'sheet']

function split(hash: string): { path: string; query: URLSearchParams } {
  const h = hash.replace(/^#\/?/, '')
  const q = h.indexOf('?')
  return { path: q >= 0 ? h.slice(0, q) : h, query: new URLSearchParams(q >= 0 ? h.slice(q + 1) : '') }
}

/** Whether `hash` opens the harness. */
export function isBuddyRigHash(hash: string): boolean {
  return split(hash).path === BUDDY_RIG_ROUTE
}

/** The rates the measurement can ask for: the buddy's own, twice it, and 0 for no cap at all. */
const RATES = [BUDDY_FPS, 60, 0] as const

/** What the harness's stage shows for `hash`: an unknown view, size or rate is the default, and a
 *  rate of `null` means every frame the display draws. */
export function rigViewOf(hash: string): { view: RigView; size: BuddySize; fps: number | null } {
  const { query } = split(hash)
  const view = VIEWS.find((v) => v === query.get('view')) ?? 'both'
  const size = BUDDY_SIZES.find((s) => String(s) === query.get('size')) ?? 48
  const rate = RATES.find((r) => String(r) === query.get('fps')) ?? BUDDY_FPS
  return { view, size, fps: rate === 0 ? null : rate }
}
