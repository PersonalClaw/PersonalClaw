/** A scheduled report says what it reads, what it may cite and when it runs.
 *
 *  The old Reports page held three things that look done while being absent, and they live on in the
 *  library's set-up (#6101):
 *   · THE TRIPLE IS THE FEATURE. A report is a prompt plus three scoping decisions: what counts as new
 *     material, what may be searched while writing, what may be cited. The Power user view's lines on
 *     its set-up row say what it reads (the gateway's own sentence) and its citation rule.
 *   · AN EDIT SENDS ONLY WHAT CHANGED, so a schedule the form can't show (an interval, a one-off set
 *     elsewhere) is never rewritten by a save that left it alone.
 *   · A SCHEDULE IS SAID IN WORDS, with its zone and its next run in that zone. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { answers, resetGateway } from '../../../test/libraryHarness'
import type { ResearchReport } from '../../../lib/api'
import { blank, changed, editable, nextRun } from './reportDraft'

vi.mock('../../../lib/api', async (orig) => {
  const { gatewayApi } = await import('../../../test/libraryHarness')
  return { ...(await orig<typeof import('../../../lib/api')>()), api: gatewayApi() }
})
const { SetupPanel } = await import('./SetupPanel')
const { resetDataStore } = await import('../../../lib/data/store')

function report(over: Partial<ResearchReport> = {}): ResearchReport {
  return {
    id: 'rep-1', name: 'Weekly contradiction scan', prompt: 'Find claims that contradict what we already believe.',
    schedule: { kind: 'cron', cron_expr: '0 8 * * 1' }, tz: 'America/Los_Angeles',
    source: { tags: ['research'], window_secs: 0 }, context: null, citation_policy: 'cite-source-only',
    iteration_cap: 3, enabled: true, created_ts: 1_787_000_000, last_run_ts: null, last_status: '',
    last_error: '', last_result: '', watermark_ts: 0,
    schedule_shown: { words: 'At 8:00 AM PDT, only on Monday', timezone: 'America/Los_Angeles', next_run_at: '2026-10-05T15:00:00+00:00' },
    sources_shown: 'Reads what is new in your knowledge tagged research each time it runs. It does not search the web.',
    ...over,
  } as ResearchReport
}

beforeEach(() => {
  resetDataStore(); resetGateway()
  answers.knowledgeIntents = { intents: [] }
  answers.knowledgeSources = { sources: [], kinds: [] }
})
afterEach(() => { cleanup(); vi.useRealTimers() })

describe("a report's set-up row states its scoping decisions in the Power user view", () => {
  const mount = (r: ResearchReport, detail = true) => {
    answers.researchReports = { reports: [r] }
    render(<SetupPanel nav={{ form: vi.fn(), item: vi.fn() }} onClose={vi.fn()} detail={detail} />)
  }

  it('🔑 says what it reads, in the gateway\'s own sentence, and that it cites only what is new', async () => {
    mount(report())
    expect(await screen.findByText('Reads what is new in your knowledge tagged research each time it runs. It does not search the web.')).toBeTruthy()
    expect(screen.getByText('Cites only what is new since its last run')).toBeTruthy()
  })

  it('says so when it may cite what it was given to read', async () => {
    mount(report({ citation_policy: 'allow-citing-context' }))
    expect(await screen.findByText('May also cite what it was given to read')).toBeTruthy()
    expect(screen.queryByText('Cites only what is new since its last run')).toBeNull()
  })

  it('🔴 says neither in the regular view', async () => {
    mount(report(), false)
    await screen.findByText('Weekly contradiction scan')
    expect(screen.queryByText(/Reads what is new|Cites only/)).toBeNull()
  })
})

describe("a report's draft", () => {
  it('🔑 an edit sends only what changed', () => {
    const was = editable(report())
    expect(changed({ ...was, schedule: { kind: 'cron', cron_expr: '28 3 * * 5' } }, was))
      .toEqual({ schedule: { kind: 'cron', cron_expr: '28 3 * * 5' } })
    expect(changed(was, was), 'an untouched draft sends nothing').toEqual({})
  })

  it('a blank one cites only the material it was asked to watch', () => {
    // The narrower policy is the one a reader can trust without knowing the configuration.
    expect(blank()).toMatchObject({ citation_policy: 'cite-source-only', enabled: true })
  })

  it('🔑 its next run is said in words, in the zone it runs in, with how far off it is', () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-10-01T12:00:00Z'))
    expect(nextRun('2026-10-05T12:00:00+00:00', 'America/Toronto')).toBe('Mon, Oct 5, 8:00 AM EDT, in 4d')
  })

  it('a stamp it cannot read says nothing', () => {
    expect(nextRun('not a time', 'America/Toronto')).toBe('')
  })
})
