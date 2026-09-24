/** Asking for the product tour, from anywhere.
 *
 *  Two callers, and they are in different worlds:
 *
 *   • **The onboarding done screen**, whose "Take the quick tour" runs `finish()` — the act
 *     that commits identity, flips `onboarded`, and therefore REPLACES the whole flow with the
 *     app shell. The button's own component is gone before the tour could mount, so it cannot
 *     render the tour itself; it can only leave a request behind for the shell that is about
 *     to exist. Same problem `exitTo.ts` solves for "land on this page instead", same shape.
 *
 *   • **The Discover hub's "Replay the tour" card**, which clicks inside an already-mounted
 *     shell — nothing is being replaced, so a request needs to reach a live listener.
 *
 *  One mechanism covers both: a pending flag plus an event. The done screen sets the flag
 *  before the shell exists and the shell CONSUMES it on mount; Discover sets it and the
 *  mounted listener consumes it in the same tick. Consuming is what stops a request from
 *  replaying — an unconsumed flag would start the tour again on the next thing that mounted.
 *
 *  Module state, not storage, deliberately: this is the whole of the tour's memory. Nothing
 *  about the tour is persisted and nothing is reported, so there is no progress to resume, no
 *  "seen it" record, and no request on any step. A flag in `localStorage` would resurrect a
 *  half-finished tour on the next load and would be a second thing to keep in step with the
 *  onboarding state on the server. */

const EVENT = 'ne:product-tour'

let pending = false

/** Ask for the tour. Safe before the shell exists — the flag is what the shell reads on
 *  mount — and safe while it is mounted, which is what the event is for. */
export function requestProductTour(): void {
  pending = true
  window.dispatchEvent(new CustomEvent(EVENT))
}

/** Take the pending request, if there is one. Consumes: a second caller gets `false`. */
export function consumeProductTourRequest(): boolean {
  const was = pending
  pending = false
  return was
}

/** Subscribe to requests made while already mounted. Same in-tab event contract as
 *  `navApps`/`navDisclosure`; deliberately NOT cross-tab, because a tour is a thing
 *  happening in ONE window. */
export function onProductTourRequest(cb: () => void): () => void {
  window.addEventListener(EVENT, cb)
  return () => window.removeEventListener(EVENT, cb)
}
