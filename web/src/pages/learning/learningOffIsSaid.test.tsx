import { describe, it, expect, beforeEach, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { invalidateKeys } from '../../lib/data'
import { LearningPage } from './LearningPage'
import type { LearningInbox, StagingWeek } from '../../lib/api'

// ── Learning switched off is one sentence and a way back on ──────────────────────────────────────
//
// With `learning.enabled` off, the four reads the Learning page makes for its own panels — the
// proposals, the capture week, the health panel and the identity report — used to answer 404. The
// page drew each as a red "Couldn't load your …" alert whose Retry could never succeed, because a
// switch that is off does not flip when the fetch is repeated, and the switch had no control
// anywhere: the only way back on was editing `config.json`.
//
// They now answer the decided `{"enabled": false}`, and the page says it once, with a button that
// is the PATCH itself. The eval panels below are a different switch and keep their own answers.

const OFF = { enabled: false }
const EMPTY_INBOX: LearningInbox = {
  rows: [], total: 0, by_kind: {}, by_tier: {}, flagged: 0, unrenderable: [], bulk_acceptable: 0,
}
const WEEK: StagingWeek = {
  days: 7, buckets: [], silent_days: [], error_days: [], produced_total: 0, cost_usd: 0, first_pass_day: '',
}

let learningOn = false
const patchConfig = vi.fn()
const learningProposals = vi.fn()

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      workflowAttention: () => Promise.resolve({ scopes: [] }),
      evalFieldMetrics: () => Promise.resolve({ subjects: [] }),
      learningProposals: () => learningProposals(),
      learningStagingWeek: () => Promise.resolve(learningOn ? WEEK : OFF),
      // One switch, so all four follow it. Once on, these two are not under test.
      learningHealth: () => (learningOn ? Promise.reject(new Error('not under test')) : Promise.resolve(OFF)),
      identityReport: () => (learningOn ? Promise.reject(new Error('not under test')) : Promise.resolve(OFF)),
      judgeBench: () => Promise.resolve({ ran: false }),
      evalStudies: () => Promise.resolve({ studies: [] }),
      retrievalBench: () => Promise.resolve({ ran: false }),
      ablation: () => Promise.resolve({ ran: false }),
      learningBenchmark: () => Promise.resolve({ ran: false }),
      patchConfig: (...a: unknown[]) => patchConfig(...a),
    },
  }
})

describe('the Learning page with learning switched off', () => {
  beforeEach(() => {
    invalidateKeys('', true)
    sessionStorage.clear()
    vi.clearAllMocks()
    learningOn = false
    learningProposals.mockImplementation(() => Promise.resolve(learningOn ? EMPTY_INBOX : OFF))
    patchConfig.mockImplementation(() => { learningOn = true; return Promise.resolve({}) })
  })

  it('says it once, with the way back on — no load failures, no empty inbox', async () => {
    render(<LearningPage navigate={() => {}} />)
    expect(await screen.findByRole('heading', { name: 'Learning is off' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Turn learning on' })).toBeTruthy()
    for (const what of ['proposals', 'capture week', 'learning health', 'identity report']) {
      expect(screen.queryByText(new RegExp(`Couldn't load your ${what}`)), `${what} is off, not failed`).toBeNull()
    }
    expect(screen.queryByText('Nothing to review'), 'off is not "you have none"').toBeNull()
    // The eval panels are another switch, and still answer for themselves.
    expect(screen.getByText(/No benchmark has run yet/)).toBeTruthy()
  })

  it('turns learning on from the page, and the panels it covers come back', async () => {
    render(<LearningPage navigate={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Turn learning on' }))
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('learning.enabled', true))
    expect(await screen.findByText('Nothing to review')).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Learning is off' })).toBeNull()
  })

  it('a refused switch is said beside the button', async () => {
    patchConfig.mockRejectedValue(new Error('config.json is read-only'))
    render(<LearningPage navigate={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Turn learning on' }))
    expect(await screen.findByText("Couldn't turn learning on: config.json is read-only")).toBeTruthy()
  })
})
