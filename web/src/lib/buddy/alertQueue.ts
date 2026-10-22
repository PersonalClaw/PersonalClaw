// ── The alert queue (`docs/design/buddy/state-machine.md`, "The alert queue") ─────────────────
//
// Alerts are shown one at a time, in the waiting list's one order, consequence then age, so the
// buddy agrees with every other surface (#3888). A shown alert stays until it is answered here,
// answered elsewhere, expires or is withdrawn; no timer ever dismisses it. Pure.

import type { Alert } from './inputs'

/** The waiting list's order: the greater consequence first, then the older. */
export function byWaitingOrder(a: Alert, b: Alert): number {
  return b.consequence - a.consequence || a.raisedAt - b.raisedAt
}

/** The queue with `alert` in its place, a re-read of one already there replacing it. */
export function queued(queue: readonly Alert[], alert: Alert): Alert[] {
  return [...queue.filter((a) => a.id !== alert.id), alert].sort(byWaitingOrder)
}

/** The queue without the alert `id`. */
export function dropped(queue: readonly Alert[], id: string): Alert[] {
  return queue.filter((a) => a.id !== id)
}
