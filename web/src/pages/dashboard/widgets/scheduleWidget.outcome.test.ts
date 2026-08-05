/**
 * The Schedule widget must read the TYPED outcome, not `status` (S163).
 *
 * 🔴 THE DEFECT. `/api/triggers/history` returns `FireRecord` rows, and a FireRecord carries no
 * `status` field at all — its typed value is `outcome`
 * (`ran | skipped_gate | blocked_injection | deferred | refused | …`). The widget had its own local
 * three-branch mapper keyed on `status`, so every projected row arrived with `status: undefined`,
 * fell through to the default branch, and rendered as **"ran"** with an info dot.
 *
 * A quiet-hours SUPPRESSION displayed as "ran" is the feed reporting that the machine did work it
 * explicitly had not done — the opposite of §7 criterion 8's "zero silent drops", and worse than
 * showing nothing, because the user has no reason to look further.
 *
 * `statusMeta` (S137) already maps the whole vocabulary. A second local copy is how two surfaces
 * start disagreeing about what an outcome means, which is why this test pins the SHARED one.
 */
import { describe, it, expect } from 'vitest'
import { statusMeta } from '../../schedule/scheduleMeta'

describe('the Schedule widget outcome mapping', () => {
  it('does NOT render a suppressed fire as "ran"', () => {
    const m = statusMeta('skipped_gate')
    expect(m.label).not.toBe('ran')
    expect(m.label).toBe('gate')
  })

  it('gives a suppression a NEUTRAL tone, not ok-green and not danger', () => {
    // The automation is working as configured — quiet hours held it. A green tick would claim
    // success; a red badge would send the user hunting a fault that is not there.
    const m = statusMeta('skipped_gate')
    expect(m.tone).toBe('var(--color-on-surface-low)')
  })

  it('still renders a real success and a real failure distinctly', () => {
    expect(statusMeta('success').tone).toBe('var(--color-ok)')
    expect(statusMeta('failure').tone).toBe('var(--color-danger)')
  })

  it('renders the typed RAN outcome the projection actually emits', () => {
    // The feed's success value is `ran`, not `success` — a widget that only knew `success` would
    // label every genuine run "never run".
    expect(statusMeta('ran').label).not.toBe('never run')
  })

  it('keeps a blocked payload distinguishable from a skip', () => {
    expect(statusMeta('blocked_injection').label).toBe('blocked')
    expect(statusMeta('blocked_injection').tone).toBe('var(--color-danger)')
    expect(statusMeta('skipped_gate').tone).not.toBe(statusMeta('blocked_injection').tone)
  })

  it('falls back to `status` when a row has no typed outcome', () => {
    // The same widget also renders legacy ScheduleRun rows, which DO carry `status` and no
    // `outcome`. Both shapes must work: the widget reads `r.outcome ?? r.status`.
    expect(statusMeta(undefined as unknown as string).label).toBe('never run')
    expect(statusMeta('success').label).toBe('ok')
  })
})

describe('the completeness guard this pattern earned', () => {
  // The backend vocabulary is closed (`models.Outcome`), so it can be restated here and asserted
  // whole. S137 mapped part of it and S163 found three members still missing — including `failed`,
  // rendering as "never run". A per-value test catches the next addition instead of letting it
  // fall through the default branch, which is where every one of these bugs has lived.
  const OUTCOMES = [
    'ran', 'ran_late', 'skipped_overlap', 'skipped_budget', 'skipped_gate', 'skipped_noop',
    'skipped_triage', 'skipped_missed', 'deferred', 'refused', 'blocked_injection', 'failed',
  ]

  it('renders EVERY typed outcome as something other than "never run"', () => {
    const unmapped = OUTCOMES.filter((o) => statusMeta(o).label === 'never run')
    expect(unmapped).toEqual([])
  })

  it('keeps a FAILED fire visually distinct from a never-run one', () => {
    // The pair that matters most: before S163 both were grey "never run", so a broken automation
    // was indistinguishable from an idle one.
    expect(statusMeta('failed').tone).toBe('var(--color-danger)')
    expect(statusMeta(undefined as unknown as string).tone).not.toBe('var(--color-danger)')
  })
})
