/** A report's Run now says what the run found, in the run's own words.
 *
 *  The old Reports page learned this the hard way: a toast that said only that a run started told
 *  nothing about a run that found nothing new, or failed. The library's Run now (the preview's bar,
 *  the set-up row) says the run's own sentence when it ends, a failed run's own words, and a refusal
 *  in the gateway's. */
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { answers, calls, resetGateway } from '../../test/libraryHarness'

vi.mock('../../lib/api', async (orig) => {
  const { gatewayApi } = await import('../../test/libraryHarness')
  return { ...(await orig<typeof import('../../lib/api')>()), api: gatewayApi() }
})
const { ApiError } = await import('../../lib/api')
const { runReport, SetupPanel } = await import('./setup/SetupPanel')
const { resetDataStore } = await import('../../lib/data/store')

const REPORT = { id: 'rpt-1a2b3c4d', name: 'Weekly perf digest' }
let toasts: { message: string; level: string }[] = []
const onToast = (e: Event) => {
  const d = (e as CustomEvent).detail ?? {}
  toasts.push({ message: String(d.message ?? ''), level: String(d.level ?? '') })
}
beforeEach(() => { resetGateway(); toasts = []; window.addEventListener('ne:toast', onToast) })
afterEach(() => { cleanup(); window.removeEventListener('ne:toast', onToast) })

describe("a report's run says what it found", () => {
  it('🔑 a run that found nothing new says so, in its own sentence', async () => {
    answers.runResearchReport = { ok: true, report_id: REPORT.id, outcome: 'nothing_new', result: 'Found no new material since its previous run.' }
    await runReport(REPORT as never)
    expect(calls.runResearchReport).toEqual([[REPORT.id]])
    expect(toasts.at(-1)).toEqual({ message: 'Weekly perf digest: Found no new material since its previous run.', level: 'info' })
  })

  it('🔑 a run that failed says so in its own words', async () => {
    answers.runResearchReport = { ok: false, report_id: REPORT.id, outcome: 'failed', result: 'the model timed out' }
    await runReport(REPORT as never)
    expect(toasts.at(-1)).toEqual({ message: 'Weekly perf digest: the model timed out', level: 'error' })
  })

  it("🔑 a refusal is said in the gateway's words", async () => {
    answers.runResearchReport = new ApiError('It is already writing one.', 409, 'already_running')
    await runReport(REPORT as never)
    expect(toasts.at(-1)?.message).toContain('It is already writing one.')
    expect(toasts.at(-1)?.level).toBe('error')
  })

  it('a run that wrote one says what it wrote', async () => {
    answers.runResearchReport = { ok: true, report_id: REPORT.id, outcome: 'wrote', result: 'Wrote a new report from 3 new items.' }
    await runReport(REPORT as never)
    expect(toasts.at(-1)).toEqual({ message: 'Weekly perf digest: Wrote a new report from 3 new items.', level: 'success' })
  })
})

describe('a report a restore holds says it waits for a Resume', () => {
  it("🔑 its set-up row says it is paused by the restore, and where it's resumed", async () => {
    resetDataStore()
    answers.knowledgeIntents = { intents: [] }
    answers.knowledgeSources = { sources: [], kinds: [] }
    answers.researchReports = { reports: [{
      ...REPORT, enabled: true, schedule: { kind: 'cron', cron_expr: '0 8 * * 1' }, source: { tags: [], window_secs: 0 },
      schedule_shown: { words: 'Every Monday at 8:00', timezone: '', next_run_at: '', restore_hold: 'another_home' },
    }] }
    render(<SetupPanel nav={{ form: vi.fn(), item: vi.fn() }} onClose={vi.fn()} />)
    expect(await screen.findByText(/Every Monday at 8:00 · paused by the restore: resume it on the Triggers page/)).toBeTruthy()
  })
})
