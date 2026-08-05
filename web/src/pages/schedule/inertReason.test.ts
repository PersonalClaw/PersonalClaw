/**
 * A suppression's REASON must not render as an error (S172).
 *
 * 🔴 THE DEFECT, created by S171's own fix. That session began PERSISTING a suppressed fire's row so
 * criterion 8's "zero silent drops" became real — and the reason lands in `ScheduleRun.error`, which
 * `RunTrace` renders inside a danger-tinted box. Measured:
 *
 *     quiet-hours skip   dot=gate     tone=--color-on-surface-low   dangerBox=true
 *     budget skip        dot=budget   tone=--color-on-surface-low   dangerBox=true
 *     a REAL failure     dot=error    tone=--color-danger           dangerBox=true
 *
 * The row contradicted itself: a neutral grey "gate" dot beside its reason in RED, identical to a real
 * `ConnectionError`. The alarming half is the one a user reacts to, so an automation working exactly as
 * configured read as broken — sending the user hunting a fault that is not there.
 *
 * This is the shape S171's own log warned about one layer down: a visibility fix landing a value on a
 * surface that was not built to receive it.
 */
import { describe, it, expect } from 'vitest'
import { isInertOutcome, statusMeta } from './scheduleMeta'

describe('isInertOutcome', () => {
  it('covers every member of the backend INERT_OUTCOMES set', () => {
    // Mirrors `models.INERT_OUTCOMES` — all six share the `skipped_` prefix (verified against the
    // Python set), which is why the predicate is derived rather than a hand-copied list.
    for (const o of [
      'skipped_overlap',
      'skipped_budget',
      'skipped_gate',
      'skipped_noop',
      'skipped_triage',
      'skipped_missed',
    ]) {
      expect(isInertOutcome(o)).toBe(true)
    }
  })

  it('does NOT treat a real failure as inert', () => {
    // The pair that matters: `failed` and `failure` must keep the danger styling.
    for (const o of ['failed', 'failure', 'error', 'timeout', 'blocked_injection', 'refused']) {
      expect(isInertOutcome(o)).toBe(false)
    }
  })

  it('does not treat a successful run as inert', () => {
    for (const o of ['ran', 'ran_late', 'success', 'ok', 'launched', 'deferred']) {
      expect(isInertOutcome(o)).toBe(false)
    }
  })

  it('survives an absent outcome', () => {
    // A legacy `ScheduleRun` carries `status` and no `outcome`; neither may crash the renderer, and
    // "unknown" must fall to NOT-inert so a real error is never quietly de-emphasised.
    expect(isInertOutcome(undefined)).toBe(false)
    expect(isInertOutcome(null)).toBe(false)
    expect(isInertOutcome('')).toBe(false)
  })

  it('is not fooled by a name that merely contains the word', () => {
    // Anchored at the start, like `statusMeta`'s own prefix branch — `was_skipped` is not a
    // suppression and must not be de-emphasised.
    expect(isInertOutcome('was_skipped')).toBe(false)
    expect(isInertOutcome('not_skipped_gate')).toBe(false)
  })
})

describe('the dot and the reason must AGREE', () => {
  // The defect was an internal contradiction, so the test asserts consistency rather than one styling.
  const neutral = 'var(--color-on-surface-low)'

  it('a suppression is neutral in BOTH the dot and the reason box', () => {
    for (const o of ['skipped_gate', 'skipped_budget', 'skipped_overlap']) {
      expect(statusMeta(o).tone).toBe(neutral)
      expect(isInertOutcome(o)).toBe(true) // → the reason box renders neutral too
    }
  })

  it('a real failure is danger in BOTH', () => {
    expect(statusMeta('failure').tone).toBe('var(--color-danger)')
    expect(isInertOutcome('failure')).toBe(false)
  })
})
