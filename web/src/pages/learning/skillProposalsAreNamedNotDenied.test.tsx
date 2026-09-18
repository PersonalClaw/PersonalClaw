/** The Learning empty state names pending skill proposals instead of denying them (#321).
 *
 *  It read: *"Nothing to review — proposals appear here when the system notices a pattern worth
 *  offering."* A skill refinement synthesized from the user's own sessions is precisely "a pattern
 *  worth offering", so the copy described the very items it omitted.
 *
 *  Measured on `origin/main` with ONE pending proposal on disk: `learning_report._gather_proposals`
 *  — the identity report, lower on THIS SAME PAGE — returned `[{label: 'delegation (refine)'}]`
 *  while `GET /api/learning/proposals` returned `total: 0`. The page contradicted itself, a state
 *  created by Learning-Visibility S4 landing: it bridged the store into the identity report and
 *  left this panel behind.
 *
 *  A COUNT and a POINTER, never rows. `skills/.proposals` is a different store with its own review
 *  UI, accept/reject endpoints and inbox routing; listing them here would make Learning a third
 *  owner of skill-proposal review. Bridging the stores belongs to Learning-Visibility, which owns it.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { invalidateKeys } from '../../lib/data'
import { LearningPage } from './LearningPage'
import { ApiError } from '../../lib/api'
import type { LearningInbox, LearningRow } from '../../lib/api'

const EMPTY_INBOX: LearningInbox = {
  rows: [], total: 0, by_kind: {}, by_tier: {}, flagged: 0, unrenderable: [], bulk_acceptable: 0,
}

const learningProposals = vi.fn<() => Promise<LearningInbox>>()

// PARTIAL mock via `importOriginal`, for the reason `weekNeverRan.test.tsx` records: the side
// panels branch on the REAL `hasApiCode`, so a factory returning only `api` throws from inside the
// render and every assertion below would die before it ran.
vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      workflowAttention: () => Promise.resolve({ scopes: [] }),
      learningProposals: () => learningProposals(),
      learningStagingWeek: () => Promise.reject(new Error('not under test')),
      learningHealth: () => Promise.reject(new Error('not under test')),
      acceptLearningProposal: () => Promise.resolve({ ok: true }),
      rejectLearningProposal: () => Promise.resolve(undefined),
      judgeBench: () => Promise.reject(new ApiError('No judge benchmark has run yet.', 404, 'judge_bench_absent')),
      evalStudies: () => Promise.reject(new ApiError('No study is registered under that id.', 404, 'study_absent')),
      retrievalBench: () => Promise.reject(new ApiError('No retrieval benchmark has run yet.', 404, 'retrieval_absent')),
      ablation: () => Promise.reject(new ApiError('No ablation has run yet.', 404, 'ablation_absent')),
      identityReport: () => Promise.reject(new Error('not under test')),
      // FieldMetricsPanel fetches on mount, so an absent stub throws from inside the render and
      // kills every assertion below — the same reason the seven sibling suites stub it too.
      evalFieldMetrics: () => Promise.resolve({ subjects: [] }),
      learningBenchmark: () => Promise.reject(
        new ApiError('No skill-impact benchmark has run yet.', 404, 'learning_benchmark_absent'),
      ),
    },
  }
})

const inbox = (over: Partial<LearningInbox> = {}): LearningInbox => ({ ...EMPTY_INBOX, ...over })

describe('the Learning empty state and pending skill proposals', () => {
  beforeEach(() => {
    invalidateKeys('', true)
    sessionStorage.clear()
    vi.clearAllMocks()
  })

  it('🔑 says how many are waiting instead of "Nothing to review"', async () => {
    learningProposals.mockResolvedValue(inbox({ skill_proposals_pending: 4 }))
    render(<LearningPage navigate={() => {}} />)

    expect(await screen.findByText(/4 skill proposals awaiting review/i)).toBeTruthy()
    // The denial must be GONE, not merely accompanied.
    expect(screen.queryByText('Nothing to review')).toBeNull()
  })

  it('singular for one — "1 skill proposals" is how a count reads as machine-generated', async () => {
    learningProposals.mockResolvedValue(inbox({ skill_proposals_pending: 1 }))
    render(<LearningPage navigate={() => {}} />)

    expect(await screen.findByText(/1 skill proposal awaiting review/i)).toBeTruthy()
    expect(screen.queryByText(/1 skill proposals/i)).toBeNull()
  })

  it('the action routes to the surface that owns them', async () => {
    const navigate = vi.fn()
    learningProposals.mockResolvedValue(inbox({ skill_proposals_pending: 2 }))
    render(<LearningPage navigate={navigate} />)

    await userEvent.click(await screen.findByRole('button', { name: /review in skill proposals/i }))
    expect(navigate).toHaveBeenCalledWith('skills?mode=proposals')
  })

  it('distinguishes the two queues, so quiet-learning is not read as broken-learning', async () => {
    learningProposals.mockResolvedValue(inbox({ skill_proposals_pending: 3 }))
    render(<LearningPage navigate={() => {}} />)

    expect(await screen.findByText(/ambient lesson pipeline has filed nothing/i)).toBeTruthy()
  })

  // ── the floors ─────────────────────────────────────────────────────────────────────────────

  it('🪤 vacuity floor — the original empty state still renders when there is genuinely nothing', async () => {
    // A fix that showed the pointer unconditionally would pass every assertion above.
    learningProposals.mockResolvedValue(inbox({ skill_proposals_pending: 0 }))
    render(<LearningPage navigate={() => {}} />)

    expect(await screen.findByText('Nothing to review')).toBeTruthy()
    expect(screen.queryByText(/awaiting review/i)).toBeNull()
  })

  it('an older gateway that OMITS the field reads as "none pending"', async () => {
    // `?? 0`. Asserted with the field absent entirely rather than set to 0, because absent is the
    // shape a pre-fix gateway actually sends — and a missing count must not claim proposals exist.
    learningProposals.mockResolvedValue(EMPTY_INBOX)
    render(<LearningPage navigate={() => {}} />)

    expect(await screen.findByText('Nothing to review')).toBeTruthy()
  })

  it('rows win over the pointer — a non-empty queue is not an empty state at all', async () => {
    // A faithful row, shaped like `evidenceGrade.test.tsx`'s fixture: a fabricated one rendered
    // nothing, so the assertion below would have passed because the PAGE was empty rather than
    // because the pointer was correctly suppressed.
    const realRow: LearningRow = {
      id: 'lesson.prefers-terse', kind: 'lesson',
      title: 'a real learning proposal', provenance: 'inferred',
      source_cadence: 'per_turn', source_excerpt: '',
      evidence_refs: [], evidence_strength: '', reinforcements: 1, confidence: 0.6,
      manifest_valid: true, manifest_issues: [], risk_tier: 'low',
      status: 'pending', renderable: true, bulk_acceptable: true,
      gate: {
        state: 'ungated', reason: 'no gate run yet', before: null, after: null, delta: null,
        regressed: false, scenarios: 0, halted: false, dollars_est: 0, spend_observed: false,
        pin: {}, ran_at: '',
      },
      replay: {
        state: 'unreplayed', reason: 'no replay run yet', verdict: 'unmeasured',
        candidate_mean: null, baseline_mean: null, cases: 0, scored: 0, rejected: 0, tool_free: 0,
        deferred: false, provenance: [], ran_at: '',
      },
    }
    learningProposals.mockResolvedValue(inbox({ rows: [realRow], total: 1, skill_proposals_pending: 9 }))
    render(<LearningPage navigate={() => {}} />)

    expect(await screen.findByText('a real learning proposal')).toBeTruthy()
    expect(screen.queryByText(/9 skill proposals/i)).toBeNull()
  })
})
