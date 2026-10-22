// ── Which work is in focus (`docs/design/buddy/state-machine.md`, "Which work is in focus") ─────
//
// 1. While an alert shows, its work is in focus.
// 2. Otherwise the work already in focus keeps it for as long as it is live: focus never jumps
//    between two pieces of running work.
// 3. When the focused work ends, focus moves to the live work that started earliest.
// 4. Every other live piece of work is "in sight", in start order.
//
// Pure: the live list is the running list's rows in start order.

import type { Work } from './inputs'

/** The work focus moves to when the work in focus ends, or none is: the one that started earliest. */
export function earliest(live: readonly Work[]): Work | null {
  return live.reduce<Work | null>((first, w) => (!first || w.startedAt < first.startedAt ? w : first), null)
}

/** The focus after the live list changes: kept while the focused work is live, else the earliest. */
export function keptFocus(focus: string | null, live: readonly Work[]): string | null {
  if (focus && live.some((w) => w.id === focus)) return focus
  return earliest(live)?.id ?? null
}

/** The live work in sight beside the focused one, in start order. */
export function inSight(live: readonly Work[], focus: string | null): Work[] {
  return [...live].filter((w) => w.id !== focus).sort((a, b) => a.startedAt - b.startedAt)
}
