import type { CSSProperties } from 'react'

// ── ONE loop-status registry ───────────────────────────────────────────────────
//
// The single source for how a unified Loop / Code-project status is NAMED and COLOURED,
// so the Loops list, the Code list, the in-chat progress card, the dashboard Active Work
// widget, the Projects linked-work rows and any future surface read the same word and the
// same tone for the same state.
//
// This file already consolidated three per-page maps once. A FOURTH then shipped beside it
// (`pages/loops/loopStatusMeta.ts`) with exactly the drift the old comment here named as
// fixed: "Stalled" vs "Stagnant", "Analyzing" vs "Intake", "Complete" vs "Completed",
// "Needs you" vs "Needs input" — plus a second, contradictory colour language (`running`
// green here vs primary there, `complete` primary there vs green here). PP-16 retires it:
// one table, three accessors, no second registry.
//
// The words, where a state exists on both work-unit nouns, are the ones `workflowMeta`
// already ships for the same wire value — `complete` → "Completed" (the locked
// terminal-label ruling, railed by `pages/workflows/terminalSuccessLabel.test.ts`; this
// registry was the file that ruling's rail could not see) and `needs_input` → "Needs you".
//
// The tones say one thing each: primary = in flight, ok = finished well, info = it is your
// turn (actionable, waits on the user), warn = stalled or a non-genuine finish, danger =
// failed. `paused`/`stopped` carry NO accent on purpose — a state the user deliberately
// parked must not borrow an attention colour.
export interface LoopStatusLook {
  /** The word a user reads for this state. */
  label: string
  /** The accent CSS var — `''` for the deliberately toneless states. */
  accent: string
}

/** The colour a toneless status resolves to when a caller needs a concrete accent
 *  (a dot, a ring): low-emphasis on-surface, never an attention hue. */
const NEUTRAL_ACCENT = 'var(--color-on-surface-low)'

//: Keyed by `LoopStatus` (backend) PLUS the synthetic `ended_early` — a `complete` loop
//: carrying an `error_message` finished non-genuinely (budget exhausted, DoD unmet).
//: `tests/test_loop_status_vocabulary.py` rails this table against the backend enum in
//: both drift directions, so a new status cannot ship unnamed here.
const LOOP_STATUS: Record<string, LoopStatusLook> = {
  intake: { label: 'Analyzing', accent: 'var(--color-primary)' },
  planning: { label: 'Planning', accent: 'var(--color-primary)' },
  review: { label: 'Review', accent: 'var(--color-info)' },
  ready: { label: 'Ready', accent: 'var(--color-info)' },
  running: { label: 'Running', accent: 'var(--color-primary)' },
  paused: { label: 'Paused', accent: '' },
  stagnant: { label: 'Stalled', accent: 'var(--color-warn)' },
  blocked: { label: 'Blocked', accent: 'var(--color-warn)' },
  needs_input: { label: 'Needs you', accent: 'var(--color-info)' },
  complete: { label: 'Completed', accent: 'var(--color-ok)' },
  failed: { label: 'Failed', accent: 'var(--color-danger)' },
  stopped: { label: 'Stopped', accent: '' },
  ended_early: { label: 'Ended early', accent: 'var(--color-warn)' },
}

/** Label + accent for a loop/code status. An unmapped/future status keeps its raw wire
 *  value as the label (never blanks, never guesses a different state's word) and no accent. */
export function loopStatusLook(status: string): LoopStatusLook {
  return LOOP_STATUS[status] ?? { label: status, accent: '' }
}

/** Friendly label for a loop/code status; falls through to the raw value for any
 *  unmapped/future status (never blanks). */
export function loopStatusLabel(status: string): string {
  return loopStatusLook(status).label
}

/** A concrete accent colour for a dot / ring / text tint. Toneless and unmapped statuses
 *  resolve to the neutral low-emphasis colour. */
export function loopStatusColor(status: string): string {
  return loopStatusLook(status).accent || NEUTRAL_ACCENT
}

/** The display status: a COMPLETE loop with an error_message finished non-genuinely
 *  (cycle budget ran out / exhausted with stages unfinished) — surface that as the
 *  synthetic `ended_early` so it doesn't read as an identical green "Complete". */
export function effectiveLoopStatus(status: string, errorMessage?: string | null): string {
  return status === 'complete' && errorMessage ? 'ended_early' : status
}

/** Pill/chip style for a loop/code status: a tinted background + matching text color.
 *  Toneless statuses (paused, stopped, …) get the neutral surface tone. `mix` is the
 *  background opacity % (default 16). */
export function loopStatusTone(status: string, mix = 16): CSSProperties {
  const c = LOOP_STATUS[status]?.accent
  if (!c) return { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-var)' }
  return { background: `color-mix(in srgb, ${c} ${mix}%, transparent)`, color: c }
}

/** The statuses that count as "an active loop" — in flight, or awaiting the user but
 *  resumable. Mirrors the backend `loop.loop:ACTIVE_STATUSES` exactly (railed by
 *  `tests/test_loop_status_vocabulary.py`), and is the ONE set every surface counts or
 *  filters by: the nav badge, the dashboard hero + Active Work widget, and a cockpit
 *  deciding whether to hold its live stream open. Four hand-written copies drifted here —
 *  one of them silently omitted `blocked`, so a blocked design loop read as finished.
 *  `failed` is deliberately absent (resumable, but no worker is armed). */
export const ACTIVE_LOOP_STATUSES: ReadonlySet<string> = new Set([
  'running', 'paused', 'stagnant', 'blocked', 'needs_input',
])

/** The pre-launch statuses whose spec is still editable — no worker has run yet. Mirrors the
 *  backend `loop.loop:PRELAUNCH_STATUSES` (railed alongside the active set). A list filter that
 *  means "work I am shepherding" is ACTIVE ∪ PRELAUNCH; a lifecycle affordance is not — the
 *  backend refuses `stop` on a pre-launch loop with a 409. */
export const PRELAUNCH_LOOP_STATUSES: ReadonlySet<string> = new Set([
  'intake', 'planning', 'review', 'ready',
])

/** The four lifecycle actions `PATCH /api/loops/{id}` accepts. Mirrors the KEYS of the backend
 *  `loop.loop:ACTION_SOURCE_STATES` (railed by `tests/test_loop_action_guard_mirror.py`). This
 *  union was retyped inline six times before it had a name — once on `api.ts:uLoopAction` and
 *  once more inside every surface's own `act()`. */
export type LoopAction = 'start' | 'pause' | 'resume' | 'stop'

/** Which statuses each action may be invoked FROM — the frontend half of the lifecycle transition
 *  guard the backend enforces. Mirrors `loop.loop:ACTION_SOURCE_STATES` exactly, railed by
 *  `tests/test_loop_action_guard_mirror.py`, which asserts EQUALITY per action rather than a
 *  subset: a subset rail is precisely what let one state go missing from every surface at once.
 *
 *  Render an affordance only when its action's set holds the loop's status —
 *  `LOOP_ACTION_SOURCE_STATUSES.resume.has(loop.status)`. Offering one the backend refuses buys
 *  the user a 409 (`Cannot resume a loop in 'complete' state`); withholding one it accepts
 *  strands the loop with no way forward.
 *
 *  Six hand-written `resume` guards drifted here: three surfaces offered three states, two
 *  offered four, and one — written as a chained `===` so no array-literal search found it —
 *  offered five. Five of the six omitted `blocked`, so a blocked loop could not be resumed
 *  anywhere in the UI even though the backend accepted it, and the same `failed` loop showed
 *  Resume on a cockpit and nothing in the list. `start` drifted the same way: one cockpit
 *  offered `ready` alone while the backend also accepts `review`.
 *
 *  `stop` is ACTIVE_LOOP_STATUSES BY REFERENCE, never a second copy of those strings — the
 *  backend's own `stop` row is literally `ACTIVE_STATUSES`. The rail asserts this row stays a
 *  reference, so the two cannot drift apart even by one careless edit. */
export const LOOP_ACTION_SOURCE_STATUSES: Readonly<Record<LoopAction, ReadonlySet<string>>> = {
  start: new Set(['ready', 'review']),
  pause: new Set(['running']),
  resume: new Set(['paused', 'stagnant', 'blocked', 'needs_input', 'failed']),
  stop: ACTIVE_LOOP_STATUSES,
}
