import type { OnboardingStep } from '../../lib/api'

/** The first-run flow's step machine — the order, the titles, the URL spelling, the persisted
 *  spelling, and the rules that decide which step a given request resolves to.
 *
 *  **Why this is a module and not inline state.** The flow used to keep its position in a bare
 *  `useState<StepId>('name')` and derive every row's appearance from `ORDER.indexOf(step)`. That
 *  produced three defects a stranger hits in the first minute, all measured on a fresh home:
 *
 *   1. **Going back destroyed going forward.** From step 3, returning to step 1 re-derived steps
 *      2–5 as `upcoming`, which removed every row header button — the whole page was left with
 *      `Continue` and `Skip setup`, so the only way forward was to walk all four steps again.
 *   2. **The browser's Back button was a bounce.** Steps had no URL, and the route guard's
 *      redirect was a PUSH, so Back landed on `#/dashboard` and was immediately re-pushed to
 *      `#/onboarding`. `history.length` stayed at 3 and the user could not leave.
 *   3. **A jumped-over step rendered as complete.** Index-derived state marks every row before the
 *      current one `done`, so a resumed run showed a green check on "Bring your setup over" for a
 *      user who had never seen that screen. A step whose outcome is unknown must read unknown.
 *
 *  So position is now a URL segment (`#/onboarding/<slug>`), progress is what the server recorded,
 *  and the two are reconciled by `resolveStep` — one pure function, tested directly, rather than a
 *  derivation spread across a component. */

export type StepId = 'name' | 'import' | 'essentials' | 'try' | 'ready'

/** The five steps, in order. The FIRST is the only hard gate; the last is the recap. */
export const ORDER: StepId[] = ['name', 'import', 'essentials', 'try', 'ready']

/** One source for each step's title — used by the `StepRow` headings AND by the live region that
 *  announces progress, so the spoken step name can never drift from the visible one. */
export const TITLES: Record<StepId, string> = {
  name: 'Your name', import: 'Bring your setup over', essentials: 'Essential apps',
  try: 'Try one', ready: 'All set',
}

/** The step's spelling in the URL — `#/onboarding/<slug>`.
 *
 *  Deliberately equal to the `StepId` for every step: a second vocabulary would be a mapping to
 *  keep in step for no gain, and the ids are already URL-safe words. The map exists anyway so the
 *  URL is a DECLARED surface — a reader asking "what can follow `#/onboarding/`" gets an answer
 *  here rather than having to trust that the ids happen to be safe. */
export const SLUGS: Record<StepId, string> = {
  name: 'name', import: 'import', essentials: 'essentials', try: 'try', ready: 'ready',
}

/** The step's spelling in `entity_settings/onboarding.json` (`STEPS` in `onboarding.py`).
 *
 *  Only `try` differs: the persisted vocabulary calls that step `first_success`, which is the name
 *  of the thing it produces rather than of the step, and renaming a stored value is a migration
 *  this buys nothing by taking. Every other step maps to itself. */
export const STORED: Record<StepId, OnboardingStep> = {
  name: 'name', import: 'import', essentials: 'essentials', try: 'first_success', ready: 'ready',
}

const BY_SLUG = new Map(ORDER.map((id) => [SLUGS[id], id] as const))
const BY_STORED = new Map(ORDER.map((id) => [STORED[id], id] as const))

/** Position in `ORDER`. `-1` for anything that is not a step, so callers can compare safely. */
export function stepIndex(id: StepId): number {
  return ORDER.indexOf(id)
}

/** The step a URL sub-path names, or `null` when the sub-path is absent or not a step.
 *
 *  `null` is not an error: `#/onboarding` with no sub-path is the ordinary entry point, and the
 *  guard's redirect produces exactly that. It means "wherever this run belongs", which
 *  `resolveStep` answers from the recorded progress. */
export function stepFromSlug(sub: string | undefined): StepId | null {
  if (!sub) return null
  return BY_SLUG.get(sub.split('/')[0]) ?? null
}

/** The hash path for a step, ready for `navigate()`. */
export function pathOf(id: StepId): string {
  return `onboarding/${SLUGS[id]}`
}

/** The step a persisted resume point names, or `null`.
 *
 *  `done` resolves to `null` rather than to a step, because a home that finished the flow and is
 *  running it again asked to REDO it — dropping such a user on the recap would skip the very steps
 *  they came back for. An unrecognised value (a newer client's, or a corrupt file the server's
 *  sanitizer already rejected) resolves to `null` for the same reason a missing one does: start at
 *  the beginning rather than guess. */
export function stepFromStored(stored: OnboardingStep | undefined): StepId | null {
  if (!stored || stored === 'done') return null
  return BY_STORED.get(stored) ?? null
}

/** The later of two steps. Used to keep the recorded high-water mark monotonic — going BACK must
 *  never lower it, or a reload would cost the user the progress they had already made. */
export function furthestOf(a: StepId, b: StepId): StepId {
  return stepIndex(b) > stepIndex(a) ? b : a
}

/** The step before this one, or `null` for the first. This is what the visible Back control moves
 *  to, and it is the reason "back" is a property of the machine rather than of a row's click
 *  handler: the previous step exists whether or not its row happens to be rendered as revisitable. */
export function previousOf(id: StepId): StepId | null {
  const i = stepIndex(id)
  return i > 0 ? ORDER[i - 1] : null
}

/** The step after this one, or `null` for the recap (whose "next" is leaving the flow). */
export function nextOf(id: StepId): StepId | null {
  const i = stepIndex(id)
  return i >= 0 && i < ORDER.length - 1 ? ORDER[i + 1] : null
}

/** Whether the flow will let the user stand on *id*.
 *
 *  Two rules, and both are gates rather than decorations:
 *
 *   · **The name step is the one hard gate.** Until it is passed there is no committed identity,
 *     and `finish()` would commit the fallback name for someone who never declined to give one.
 *     So every step is locked behind it — including a deep link that names one.
 *   · **Nothing ahead of the high-water mark.** `reached` is what this home has recorded, so a
 *     hand-typed `#/onboarding/ready` on a fresh install is refused rather than honoured. Skipping
 *     forward would show a recap of work that never happened.
 *
 *  Everything at or behind the mark IS unlocked, which is the half the old flow got wrong: a step
 *  the user jumped over on a resume is theirs to go back and do. */
export function isUnlocked(id: StepId, reached: StepId, namePassed: boolean): boolean {
  if (id === 'name') return true
  if (!namePassed) return false
  return stepIndex(id) <= stepIndex(reached)
}

/** Which step to render, given what the URL asks for and what the run has recorded.
 *
 *  This is the single reconciliation point between the two, and it is total: every input resolves
 *  to a real step, so there is no state in which the flow renders nothing. The caller's job is to
 *  make the URL agree with the answer (with `replace`, so a corrected address does not become a
 *  history entry the user can go Back into). */
export function resolveStep(requested: StepId | null, reached: StepId, namePassed: boolean): StepId {
  if (!namePassed) return 'name'
  if (!requested) return reached
  return isUnlocked(requested, reached, namePassed) ? requested : reached
}
