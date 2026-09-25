import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { invalidateKeys } from '../../lib/data'
import { LearningPage } from './LearningPage'
import type { LearningInbox, StagingWeek } from '../../lib/api'

// ── Day-7 validation: every visit to #/learning on a default install made 6 × 404 ───────────────
//
// `evals.enabled` ships off, and the six eval report reads answered that with 404 `evals_disabled`
// — one "Failed to load resource" console error per panel on every visit, for a feature nobody had
// turned on. They now answer the decided `200 {"enabled": false}` (`handlers/evals.py:_off`), the
// shape every other switched-off read in the gateway uses. This drives the whole page through that
// answer: all six panels must say "off" — including the two whose payload LearningPage unwraps
// (`studies`, `subjects`), which is where a value-shaped off state could silently vanish.

const OFF = { enabled: false as const }

const EMPTY_INBOX: LearningInbox = {
  rows: [], total: 0, by_kind: {}, by_tier: {}, flagged: 0, unrenderable: [], bulk_acceptable: 0,
}
const WEEK: StagingWeek = {
  days: 7, buckets: [], silent_days: [], error_days: [], produced_total: 0, cost_usd: 0, first_pass_day: '',
}

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      workflowAttention: () => Promise.resolve({ scopes: [] }),
      learningProposals: () => Promise.resolve(EMPTY_INBOX),
      learningStagingWeek: () => Promise.resolve(WEEK),
      learningHealth: () => Promise.reject(new Error('not under test')),
      identityReport: () => Promise.reject(new Error('not under test')),
      // The six report reads, exactly as a default install answers them now.
      judgeBench: () => Promise.resolve(OFF),
      evalStudies: () => Promise.resolve(OFF),
      retrievalBench: () => Promise.resolve(OFF),
      ablation: () => Promise.resolve(OFF),
      learningBenchmark: () => Promise.resolve(OFF),
      evalFieldMetrics: () => Promise.resolve(OFF),
    },
  }
})

beforeEach(() => {
  invalidateKeys('', true)
  sessionStorage.clear()
})

describe('#/learning with evals off: six decided answers, no failures', () => {
  it('renders the off notice in every eval panel — and no load error', async () => {
    render(<LearningPage navigate={() => {}} />)
    await screen.findByText(/no judge benchmark can run/)
    for (const what of ['judge benchmark', 'study', 'retrieval benchmark', 'ablation', 'benchmark', 'lab-vs-field table']) {
      expect(screen.getByText(new RegExp(`no ${what} can run`)), what).toBeTruthy()
    }
    expect(screen.getAllByRole('link', { name: 'Evals enabled in Settings → Evaluations' })).toHaveLength(6)
    // The answer is decided, so nothing on the page may read as a failed load.
    expect(screen.queryByText(/Couldn't load your (judge benchmark|studies|retrieval benchmark|ablation report|skill-impact benchmark|lab vs field)/)).toBeNull()
  })
})
