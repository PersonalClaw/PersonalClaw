/**
 * Typed fire outcomes must RENDER as themselves.
 *
 * 🔴 THE DEFECT. The backend's outcome vocabulary grew — `blocked_injection`,
 * `skipped_*` (the archive split), `deferred` (the resource slots), `refused` (the kill
 * switch) — and `statusMeta` knew none of them. Every one fell through to the default branch and
 * rendered as **"never run"**.
 *
 * That is the worst possible label for each of them, and worst of all for a blocked attack: the user
 * reads "this automation has never run" when it in fact REFUSED a hostile payload — and because
 * `blocked_injection` never auto-retries, that row is the only record there will ever be.
 *
 * The typed outcome vocabulary exists so surfaces can switch on it instead of matching prose. A backend
 * vocabulary the frontend does not know is that contract half-kept.
 */
import { describe, it, expect } from 'vitest'
import { statusMeta } from './scheduleMeta'

describe('statusMeta — the typed outcome vocabulary', () => {
  it('does not render a BLOCKED payload as "never run"', () => {
    const m = statusMeta('blocked_injection')
    expect(m.label).toBe('blocked')
    expect(m.label).not.toBe('never run')
  })

  it('gives a blocked payload a DANGER tone', () => {
    // A screened injection is the one row a user must not scroll past.
    expect(statusMeta('blocked_injection').tone).toBe('var(--color-danger)')
  })

  it('renders a suppressed fire neutrally, not as an error', () => {
    // The automation is working as configured — quiet hours held it, or a slot was busy. A red badge
    // would send the user looking for a fault that is not there.
    const m = statusMeta('skipped_gate')
    expect(m.label).toBe('gate')
    expect(m.tone).toBe('var(--color-on-surface-low)')
  })

  it('humanises every skipped_* variant', () => {
    expect(statusMeta('skipped_overlap').label).toBe('overlap')
    expect(statusMeta('skipped_budget').label).toBe('budget')
    expect(statusMeta('skipped_noop').label).toBe('noop')
    expect(statusMeta('skipped_triage').label).toBe('triage')
  })

  it('renders deferred as postponed, not failed', () => {
    // A resource slot frees on its own; this fire is waiting, not broken.
    const m = statusMeta('deferred')
    expect(m.label).toBe('deferred')
    expect(m.tone).toBe('var(--color-info)')
  })

  it('renders a policy refusal distinctly from a failure', () => {
    // The kill switch, a capability fence, an unresolved secret — a DECISION, not a fault.
    const m = statusMeta('refused')
    expect(m.label).toBe('refused')
    expect(m.tone).not.toBe('var(--color-danger)')
  })

  it('renders a run a restart cut off as interrupted, not as a failure', () => {
    // Its own outcome in the runs feed (`Outcome.INTERRUPTED`), which fed it in as `failed`. Nothing
    // failed: it did not finish, and it waits on the review for you to run it again or dismiss it.
    const m = statusMeta('interrupted')
    expect(m.label).toBe('interrupted by a restart')
    expect(m.tone).toBe('var(--color-warning)')
    expect(m.tone).not.toBe(statusMeta('failed').tone)
  })

  it('renders a degraded run as degraded, not as a plain success', () => {
    // A digest whose synthesis was unavailable still arrived: it ran, at its reduced tier. Neither
    // the ok-green of a run that did all it is for nor the red of one that broke.
    const m = statusMeta('degraded')
    expect(m.label).toBe('degraded')
    expect(m.tone).toBe('var(--color-warning)')
    expect(m.tone).not.toBe(statusMeta('success').tone)
    expect(m.tone).not.toBe(statusMeta('failure').tone)
  })

  it('keeps the PRE-EXISTING labels unchanged', () => {
    // The shipped UI renders these; adding branches must not move them.
    expect(statusMeta('ok').label).toBe('ok')
    expect(statusMeta('success').label).toBe('ok')
    expect(statusMeta('error').label).toBe('error')
    expect(statusMeta('failure').label).toBe('error')
    expect(statusMeta('timeout').label).toBe('timed out')
    expect(statusMeta('launched').label).toBe('launched')
  })

  it('still says "never run" for a genuinely unrun trigger', () => {
    expect(statusMeta(null).label).toBe('never run')
    expect(statusMeta('').label).toBe('never run')
    expect(statusMeta(undefined).label).toBe('never run')
  })

  it('does not mistake an unknown outcome for a skip', () => {
    // `startsWith('skipped_')` must not swallow a future outcome that merely contains the word.
    expect(statusMeta('was_skipped').label).toBe('never run')
  })
})
