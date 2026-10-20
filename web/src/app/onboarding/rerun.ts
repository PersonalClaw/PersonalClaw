/** Asking to run first-run setup again, from a shell that is already past it.
 *
 *  **The defect this replaces.** Settings → Account's only re-entry door was "Restart onboarding",
 *  and it worked by CLEARING the operator's name — `onboarded` is derived from that name being
 *  non-empty, so wiping it was what made the route guard show the flow again. A user who skipped
 *  setup and wanted to finish it therefore had to destroy their identity to get back in, and the
 *  flow then re-asked for a name it already knew. That is a destructive door, and it was the only
 *  one: nothing on any surface pointed at setup, and `#/onboarding` typed by hand was bounced
 *  straight back to the dashboard by the guard.
 *
 *  **Why a request rather than a navigation.** `App.tsx`'s guard is the single owner of where a
 *  user goes with respect to this flow, and it decides from `onboarded`. A deliberate visit cannot
 *  be expressed in that variable — the user IS onboarded and is not becoming un-onboarded — so it
 *  needs a second input. Handing the guard a request keeps one navigator: the caller states intent,
 *  the guard performs the move, and the flow's `finish()` withdraws the request, which is the state
 *  change that lets the guard put the user back. Same shape as `exitTo.ts` (a destination handed to
 *  the guard rather than raced against it) and `tourLaunch.ts` (a request left for a surface the
 *  caller cannot reach).
 *
 *  **Event only, no pending flag.** Unlike `tourLaunch`, every caller here clicks inside an already
 *  mounted shell — Settings and the dashboard both render below `App` — so there is no "set it
 *  before the listener exists" case to serve. A flag would be an unreachable branch. */

const EVENT = 'ne:setup-rerun'

/** Ask the shell to open first-run setup again, keeping the name, handle and everything already
 *  set up. Nothing is cleared; the flow pre-fills what this install already has. */
export function requestSetupRerun(): void {
  window.dispatchEvent(new CustomEvent(EVENT))
}

/** Subscribe to re-entry requests. Deliberately NOT cross-tab: setup is a thing happening in one
 *  window, and a second tab jumping into the flow because this one asked would be a surprise. */
export function onSetupRerun(cb: () => void): () => void {
  window.addEventListener(EVENT, cb)
  return () => window.removeEventListener(EVENT, cb)
}
