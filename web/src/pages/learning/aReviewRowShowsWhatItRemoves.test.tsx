import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { invalidateKeys } from '../../lib/data'
import { LearningPage } from './LearningPage'
import { ApiError } from '../../lib/api'
import type { LearningInbox, LearningRow, StagingWeek } from '../../lib/api'

// ── A proposal's excerpt is shown as the text it is, line by line ─────────────────────────────────
//
// The review of what may have been learned from text she did not type lists each item and the text
// it matches in the proposal's excerpt, the part of the row the Learning page shows. The store
// fences every excerpt for a model (`<untrusted_content …>`), and the page printed it as it was:
// the markers around the text, and the lines run together into one.

const row = (over: Partial<LearningRow> = {}): LearningRow => ({
  id: 'ablation.correction-heuristic', kind: 'retirement',
  title: 'Retire correction-heuristic — ablation measured no delta',
  provenance: 'inferred', source_cadence: 'ablation', source_excerpt: '',
  evidence_refs: ['ablation:ablation-20260817T120000Z', 'matrix:ablation-20260817T120000Z'],
  evidence_strength: 'ablation', reinforcements: 3, confidence: 0.7,
  manifest_valid: true, manifest_issues: [], risk_tier: 'review',
  status: 'pending', renderable: true, bulk_acceptable: true,
  gate: {
    state: 'ungated', reason: 'no gate run yet', before: null, after: null, delta: null,
    regressed: false, scenarios: 0, halted: false, dollars_est: 0, unpriced_attempts: 0, spend_observed: false,
    pin: {}, ran_at: '',
  },
  replay: {
    state: 'unreplayed', reason: 'no replay run yet', verdict: 'unmeasured',
    candidate_mean: null, baseline_mean: null, cases: 0, scored: 0, rejected: 0, tool_free: 0,
    deferred: false, provenance: [], ran_at: '',
  },
  ...over,
})

const inboxOf = (rows: LearningRow[]): LearningInbox => ({
  rows, total: rows.length, by_kind: {}, by_tier: {},
  flagged: 0, unrenderable: [], bulk_acceptable: rows.length,
})

const WEEK: StagingWeek = {
  days: 7, buckets: [], silent_days: [], error_days: [], produced_total: 0, cost_usd: 0,
}

const learningProposals = vi.fn<() => Promise<LearningInbox>>()
const learningStagingWeek = vi.fn<() => Promise<StagingWeek>>()
const learningHealth = vi.fn<() => Promise<never>>()
const judgeBench = vi.fn<() => Promise<{ ran: false }>>()
const evalStudies = vi.fn<() => Promise<never>>()
const retrievalBench = vi.fn<() => Promise<{ ran: false }>>()
const ablation = vi.fn<() => Promise<{ ran: false }>>()

// 🪤 PARTIAL mock, via `importOriginal`: the REAL `ApiError` and answer guards are kept. The eval
// panels branch on `isSwitchedOff`/`isNotRun`, so a factory that returned only `api` made the mocked
// module throw "No export is defined" from inside the render — and a double that rejected where the
// wire answers `{"ran": false}` would render the generic failure instead of the state under test.
vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      // LearningPage now also reads attention accounting; empty is its ordinary
      // state, and an omitted stub would throw inside a passive effect (see note above).
      workflowAttention: () => Promise.resolve({ scopes: [] }),
      evalFieldMetrics: () => Promise.resolve({ subjects: [] }),
      learningProposals: () => learningProposals(),
      learningStagingWeek: () => learningStagingWeek(),
      learningHealth: () => learningHealth(),
      acceptLearningProposal: () => Promise.resolve({ ok: true }),
      rejectLearningProposal: () => Promise.resolve(undefined),
      judgeBench: () => judgeBench(),
      evalStudies: () => evalStudies(),
      retrievalBench: () => retrievalBench(),
      ablation: () => ablation(),
      // The identity report is fetched by the same LearningPage this file renders. A partial
      // `api` mock does not fail on the missing key — it throws `api.identityReport is not a
      // function` from inside the render, so BOTH call-site tests below died before asserting
      // anything. Neither PR could see it alone: LV-4 added the call, this file mocks only its own
      // reads, and the break exists only in the union. Rejecting is the honest stub — the page must
      // paint the ablation grade whether or not the identity report resolves, and if it ever grows a
      // dependency on that payload these tests should say so rather than silently pass.
      identityReport: () => Promise.reject(new Error('not under test')),
      // The skill-impact benchmark, and the SECOND instance of the paragraph above — same
      // shape, one PR later: LV-7 added the read to `LearningPage`, this file mocks only its own,
      // and `api.learningBenchmark is not a function` killed both call-site tests in the union
      // alone. Answering the wire's `{"ran": false}` rather than rejecting, because
      // `BenchmarkPanel` renders its never-run state from that value and a rejection would quietly
      // render the generic failure instead.
      learningBenchmark: () => Promise.resolve({ ran: false }),
    },
  }
})


const EXCERPT =
  '<untrusted_content source=retirement-evidence>\n' +
  '- Glossary: Yesterday — the last working day before today (the same as in the saved prompt @standup)\n' +
  'Accept removes them. Reject keeps them, and they are not offered again.\n' +
  '</untrusted_content>'

describe('the review row on the Learning page', () => {
  beforeEach(() => {
    invalidateKeys('', true)
    sessionStorage.clear()
    vi.clearAllMocks()
    learningStagingWeek.mockResolvedValue(WEEK)
    learningHealth.mockRejectedValue(new Error('not under test'))
    judgeBench.mockResolvedValue({ ran: false })
    evalStudies.mockRejectedValue(new ApiError('No study is registered under that id.', 404, 'study_absent'))
    retrievalBench.mockResolvedValue({ ran: false })
    ablation.mockResolvedValue({ ran: false })
  })

  it('lists what Accept removes, without the markers a model reads, a line each', async () => {
    learningProposals.mockResolvedValue(inboxOf([row({
      id: 'retirement-654e4706a174',
      title: "Check what may have been learned from text you didn't type",
      source_cadence: 'cleanup', evidence_strength: 'correlated',
      evidence_refs: ['glossary:Yesterday — the last working day before today'],
      source_excerpt: EXCERPT,
    })]))
    render(<LearningPage navigate={() => {}} />)

    const excerpt = await screen.findByText(/Glossary: Yesterday — the last working day before today/)
    expect(excerpt.textContent).not.toMatch(/untrusted_content/)
    expect(excerpt.textContent).toContain('(the same as in the saved prompt @standup)\nAccept removes them.')
    expect(excerpt.className).toMatch(/whitespace-pre-line/)
    expect(screen.getByRole('button', { name: /Accept/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Reject/ })).toBeTruthy()
  })
})
