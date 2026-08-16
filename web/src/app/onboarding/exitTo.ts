/** Where the app should land when onboarding finishes, if not the dashboard.
 *
 *  **Why this exists.** While `onboarded` is false, `App.tsx`'s route guard pulls EVERY route back
 *  to `#/onboarding` — that is what makes onboarding a gate rather than a page. So a link out of
 *  the flow cannot simply navigate: it would be bounced back before the browser painted. And it
 *  cannot navigate after committing the name either, because the same guard fires on the
 *  `onboarded` flip and sends the user to the dashboard; whichever of the two navigations wins is
 *  a race.
 *
 *  So the destination is handed to the GUARD instead of raced against it. The flow sets it, then
 *  commits the name; the single guard branch that already runs on the `onboarded` flip reads it
 *  and navigates. One decision, one owner, and the guard stays the only thing that decides where
 *  a non-onboarded user goes.
 *
 *  This is what makes OU-3's failure-path Settings deep-link real: when a try-one card's real call
 *  fails, "open provider settings" has to actually leave the flow and land on that panel, or it is
 *  an inert control on the one surface where a stuck user needs a way out.
 *
 *  **Read is NOT a consume, and that distinction was a live bug.** The first version cleared on
 *  read. Driving it against a real gateway landed on `#/dashboard` instead of
 *  `#/settings/providers`: the guard effect runs more than once after the `onboarded` flip while
 *  `route` is STILL `'onboarding'`, because `navigate` sets `location.hash` and `route` only
 *  catches up when the browser's async `hashchange` fires. The first run consumed the destination
 *  and navigated correctly; a second run — same effect, stale `route` — read `''` and navigated to
 *  the default, overwriting the hash. Clearing on read made the guard's own re-entrancy silently
 *  undo it, and it reproduced every time while the unit test asserting read-and-clear stayed green.
 *
 *  So `peekOnboardingExit` is idempotent: repeated guard runs all resolve to the SAME destination.
 *  `clearOnboardingExit` is called once the route has actually left onboarding — the point at which
 *  the handoff is provably finished.
 *
 *  Module state, not `sessionStorage`: the value is consumed in the same tick by the same page
 *  load, and persisting it would resurrect a stale destination on the next fresh onboarding. */

let pending = ''

/** Remember a hash path (no leading `#/`) for the guard to use once onboarding ends. */
export function setOnboardingExit(path: string): void {
  pending = path.replace(/^#?\/?/, '')
}

/** The pending destination, WITHOUT clearing it — `''` when there is none, which is the guard's
 *  signal to keep its dashboard default. Idempotent on purpose: see the note above. */
export function peekOnboardingExit(): string {
  return pending
}

/** Drop the pending destination. Called by the guard once the route has left onboarding, so a
 *  one-time exit cannot redirect a later visit. */
export function clearOnboardingExit(): void {
  pending = ''
}
