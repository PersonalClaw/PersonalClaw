import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { invalidateKeys } from '../../lib/data'
import { LearningPage } from './LearningPage'
import { ApiError } from '../../lib/api'
import type { LearningInbox, StagingWeek } from '../../lib/api'

// ── LEARN-1: never-ran is not a warning ────────────────────────────────────────────────────────
//
// On an instance where capture has NEVER run, the week header rendered an amber "7 silent" chip —
// while the page's own zero-states ("no capture pass has run", "not measured yet — nothing has
// run") say the same fact quietly. The header dressed a first-run state as a problem.
//
// `has_ever_run` is the backend's UNBOUNDED answer, not a window proxy: an all-silent week is
// also what a ran-then-died instance looks like, and THAT one must keep the chip. So the gate is
// strict `=== false` — a stale cached payload without the field defaults to the warning, because
// hiding a real silent week is the worse failure.

const week = (over: Partial<StagingWeek> = {}): StagingWeek => ({
  days: 7,
  buckets: [],
  silent_days: ['2026-08-29', '2026-08-30', '2026-08-31', '2026-09-01', '2026-09-02', '2026-09-03', '2026-09-04'],
  error_days: [],
  produced_total: 0,
  cost_usd: 0,
  ...over,
})

const EMPTY_INBOX: LearningInbox = {
  rows: [], total: 0, by_kind: {}, by_tier: {}, flagged: 0, unrenderable: [], bulk_acceptable: 0,
}

const learningStagingWeek = vi.fn<() => Promise<StagingWeek>>()

// PARTIAL mock via `importOriginal`, for the reason `evidenceGrade.test.tsx` records: the side
// panels branch on the REAL `hasApiCode`, so a factory returning only `api` throws from inside
// the render and every assertion below would die before it ran.
vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      workflowAttention: () => Promise.resolve({ scopes: [] }),
      evalFieldMetrics: () => Promise.resolve({ subjects: [] }),
      learningProposals: () => Promise.resolve(EMPTY_INBOX),
      learningStagingWeek: () => learningStagingWeek(),
      learningHealth: () => Promise.reject(new Error('not under test')),
      acceptLearningProposal: () => Promise.resolve({ ok: true }),
      rejectLearningProposal: () => Promise.resolve(undefined),
      judgeBench: () => Promise.reject(new ApiError('No judge benchmark has run yet.', 404, 'judge_bench_absent')),
      evalStudies: () => Promise.reject(new ApiError('No study is registered under that id.', 404, 'study_absent')),
      retrievalBench: () => Promise.reject(new ApiError('No retrieval benchmark has run yet.', 404, 'retrieval_absent')),
      ablation: () => Promise.reject(new ApiError('No ablation has run yet.', 404, 'ablation_absent')),
      identityReport: () => Promise.reject(new Error('not under test')),
      learningBenchmark: () => Promise.reject(
        new ApiError('No skill-impact benchmark has run yet.', 404, 'learning_benchmark_absent'),
      ),
    },
  }
})

describe('the silent-days chip waits for a first run', () => {
  beforeEach(() => {
    invalidateKeys('', true)
    sessionStorage.clear()
    vi.clearAllMocks()
  })

  it('renders never-ran as the quiet zero-state, not as an amber warning', async () => {
    learningStagingWeek.mockResolvedValue(week({ has_ever_run: false }))
    render(<LearningPage />)

    expect(await screen.findByText(/no capture pass has run yet/)).toBeTruthy()
    // The chip's whole form — count, "silent", warning styling — must be absent, because
    // the per-row zero-states beside it state the same fact without alarm.
    expect(screen.queryByText(/7 silent/)).toBeNull()
  })

  it('keeps the chip for ran-then-died — silent days AFTER a first run are the signal', async () => {
    learningStagingWeek.mockResolvedValue(week({ has_ever_run: true }))
    render(<LearningPage />)

    expect(await screen.findByText(/7 silent/)).toBeTruthy()
    expect(screen.queryByText(/no capture pass has run yet/)).toBeNull()
  })

  it('defaults a MISSING field to showing the chip, so a stale payload cannot calm a real gap', async () => {
    // An older cached response has no `has_ever_run` at all. Suppressing on absence would hide
    // the one warning this panel exists to raise, on exactly the installs most likely to be stale.
    learningStagingWeek.mockResolvedValue(week())
    render(<LearningPage />)

    expect(await screen.findByText(/7 silent/)).toBeTruthy()
    expect(screen.queryByText(/no capture pass has run yet/)).toBeNull()
  })
})
