import type { CSSProperties } from 'react'

// Canonical, friendly labels for a unified Loop / Code-project status — the ONE
// source so the Code list, the in-chat progress card, the Projects linked-work rows,
// and any future surface read the same word for the same state. (These had drifted
// into 3 separate per-page maps: "Stalled" vs "Stagnant", "Analyzing" vs "Intake",
// "Complete" vs "Completed", "Needs you" vs "Needs input" — adding `stagnant` once
// meant touching each copy, and the linked-work pills showed raw snake_case for a
// while because they had no map at all.)
const LOOP_STATUS_LABEL: Record<string, string> = {
  intake: 'Analyzing', planning: 'Planning', review: 'Review', ready: 'Ready',
  running: 'Running', paused: 'Paused', needs_input: 'Needs you', blocked: 'Blocked',
  stagnant: 'Stalled', complete: 'Complete', failed: 'Failed', stopped: 'Stopped',
  // Synthetic (not a real backend status): a COMPLETE loop carrying an error_message
  // finished non-genuinely (budget/exhaustion). Callers map to it via effectiveLoopStatus.
  ended_early: 'Ended early',
}

/** Friendly label for a loop/code status; falls through to the raw value for any
 *  unmapped/future status (never blanks). */
export function loopStatusLabel(status: string): string {
  return LOOP_STATUS_LABEL[status] ?? status
}

/** The display status: a COMPLETE loop with an error_message finished non-genuinely
 *  (cycle budget ran out / exhausted with stages unfinished) — surface that as the
 *  synthetic `ended_early` so it doesn't read as an identical green "Complete". */
export function effectiveLoopStatus(status: string, errorMessage?: string | null): string {
  return status === 'complete' && errorMessage ? 'ended_early' : status
}

// Canonical theme color (a CSS var) per status — the ONE source so the pill/chip tone
// matches across the Code list, the in-chat card, and the Projects linked-work rows.
// (Was duplicated as statusPill/statusTone with drifted opacity + an ended_early gap,
// and the linked-work pills had NO tone at all → a failed/needs_input loop showed
// neutral grey there.) needs_input/review/ready = info (actionable / waits on the
// user); blocked/stagnant/ended_early = warn (stalled / non-genuine finish);
// running/intake/planning = primary (in flight); complete = ok; failed = danger.
const LOOP_STATUS_COLOR: Record<string, string> = {
  complete: 'var(--color-ok)', failed: 'var(--color-danger)',
  ended_early: 'var(--color-warn)', blocked: 'var(--color-warn)',
  stagnant: 'var(--color-warn)',
  running: 'var(--color-primary)', intake: 'var(--color-primary)', planning: 'var(--color-primary)',
  needs_input: 'var(--color-info)', review: 'var(--color-info)',
  ready: 'var(--color-info)',
}

/** Pill/chip style for a loop/code status: a tinted background + matching text color.
 *  Unmapped statuses (paused, stopped, …) get the neutral surface tone. `mix` is the
 *  background opacity % (default 16). */
export function loopStatusTone(status: string, mix = 16): CSSProperties {
  const c = LOOP_STATUS_COLOR[status]
  if (!c) return { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-var)' }
  return { background: `color-mix(in srgb, ${c} ${mix}%, transparent)`, color: c }
}
