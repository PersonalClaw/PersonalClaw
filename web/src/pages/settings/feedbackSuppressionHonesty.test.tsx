import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import type { FeedbackProducerRow } from '../../lib/api'
import { FeedbackPanel } from './FeedbackPanel'

// ── A pill that says "Stopped surfacing" must describe something that stopped ─────────────────────
//
// Feedback-Signal computes a withholding set over all six `PRODUCER_KINDS`, but only a kind with a
// surfacing gate can act on it: `skills.surfacing` withholds a matched skill whose identity is
// `("skill_synthesis", <key>)`. The other five — prompt, loop_judge, workflow_surfacing,
// routing_pair, app — keep surfacing and earn the retire PROPOSAL only.
//
// `GET /api/feedback/producers` used to set `suppressed: true` for any below-threshold producer of
// ANY kind, and this panel renders that as a red pill titled "Stopped surfacing". So for five of six
// kinds the panel asserted an effect that never happened — and `feedbackCountsNamed.test.tsx`'s
// fixture used a `prompt` row with `suppressed: true` as its worked example, so the impossible shape
// was written into the tests too.
//
// These assertions are about what the USER is told. The route-shape half is pinned in
// `tests/test_feedback_suppression_enforcement.py`.

const feedbackProducers = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    feedbackProducers: (...a: unknown[]) => feedbackProducers(...a),
    feedbackSnooze: vi.fn(),
    feedbackClear: vi.fn(),
  },
}))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))

function rows(...producers: FeedbackProducerRow[]) {
  feedbackProducers.mockResolvedValue({ producers, min_n: 5, window_days: 90 })
}

const UNENFORCED: FeedbackProducerRow = {
  producer_kind: 'prompt', producer_id: 'task-inbox-classify',
  ups: 3, downs: 6, n: 9, accuracy: 0.333, proposal_only: true,
}
const ENFORCED: FeedbackProducerRow = {
  producer_kind: 'skill_synthesis', producer_id: 'some-skill',
  ups: 1, downs: 8, n: 9, accuracy: 0.111, suppressed: true,
}

describe('the panel only claims "stopped surfacing" where something stops', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  it('a below-threshold producer with NO surfacing gate reads "retire proposed", never "suppressed"', async () => {
    rows(UNENFORCED)
    render(<FeedbackPanel />)
    await waitFor(() => expect(screen.getByText('task-inbox-classify')).toBeTruthy())
    expect(screen.getByText('retire proposed')).toBeTruthy()
    expect(
      screen.queryByText('suppressed'),
      'a prompt producer is still injected verbatim — calling it suppressed tells the user its ' +
        'output stopped, which is the one thing that did not happen',
    ).toBeNull()
  })

  it('the honest pill explains WHY it still runs', async () => {
    // The actionable half is that a proposal is waiting, not that a number is low. Without the
    // reason the user reads "retire proposed" as a synonym for suppressed and learns nothing.
    rows(UNENFORCED)
    render(<FeedbackPanel />)
    const pill = await waitFor(() => screen.getByText('retire proposed'))
    expect(pill.getAttribute('title') ?? '').toMatch(/no surfacing gate|still runs/i)
  })

  it('an ENFORCED kind below threshold DOES read "suppressed"', async () => {
    // The other direction, and the more dangerous one: under-reporting a real withholding leaves
    // the user unable to explain why a skill stopped appearing.
    rows(ENFORCED)
    render(<FeedbackPanel />)
    await waitFor(() => expect(screen.getByText('some-skill')).toBeTruthy())
    expect(screen.getByText('suppressed')).toBeTruthy()
    expect(screen.queryByText('retire proposed')).toBeNull()
  })

  it('the two states never render on one row', async () => {
    // Both pills at once would be self-contradictory chrome; the route guarantees exactly one, and
    // this asserts the panel does not manufacture the other from a stale field.
    rows({ ...UNENFORCED, suppressed: false, proposal_only: true })
    render(<FeedbackPanel />)
    await waitFor(() => expect(screen.getByText('task-inbox-classify')).toBeTruthy())
    expect(screen.queryAllByText(/^(suppressed|retire proposed)$/)).toHaveLength(1)
  })

  it('a healthy producer claims neither', async () => {
    rows({ ...UNENFORCED, accuracy: 0.95, proposal_only: undefined, suppressed: false })
    render(<FeedbackPanel />)
    await waitFor(() => expect(screen.getByText('task-inbox-classify')).toBeTruthy())
    expect(screen.queryByText('suppressed')).toBeNull()
    expect(screen.queryByText('retire proposed')).toBeNull()
  })
})
