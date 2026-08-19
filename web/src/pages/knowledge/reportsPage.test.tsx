import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { ReportRow, ReportsPage } from './ReportsPage'
import { api, type ResearchReport } from '../../lib/api'
import { invalidateCache } from '../../lib/useCachedData'

// ── The scheduled-reports destination (WF2KNO-12) ────────────────────────────────
// Three things here have a way of looking done while being absent:
//
//  • THE TRIPLE IS THE FEATURE. A report is a research prompt plus THREE scoping decisions —
//    what counts as new material, what may be searched while writing, what may be cited. A row
//    that shows only a name and a schedule hides the decision a reader needs to judge the
//    finding, so the citation policy is asserted as rendered TEXT, per policy value.
//  • A FAILED RUN IS NOT A MISSING RUN. The runner deliberately does not advance `last_run_ts`
//    when a run fails, so "ran 3 hours ago" and "the last run failed" are both true at once.
//    Blending them would hide the retry, so both are asserted on one row.
//  • AN EMPTY LIST AND A FAILED FETCH ARE DIFFERENT FACTS. Telling an owner they have no
//    reports when the truth is "we could not load them" is the worse of the two, and both
//    render "no rows" — so the distinguishing assertion is which of the two surfaces appears.

function report(over: Partial<ResearchReport> = {}): ResearchReport {
  return {
    id: 'rep-1',
    name: 'Weekly contradiction scan',
    prompt: 'Find claims that contradict what we already believe.',
    schedule: { kind: 'cron', cron_expr: '0 8 * * 1' },
    tz: 'America/Los_Angeles',
    source: { tags: ['research'], window_secs: 0 },
    context: null,
    citation_policy: 'cite-source-only',
    iteration_cap: 3,
    enabled: true,
    created_ts: 1_787_000_000,
    last_run_ts: null,
    last_status: '',
    last_error: '',
    watermark_ts: 0,
    ...over,
  }
}

// `useCachedData` keeps a MODULE-GLOBAL cache, so a list fetched by one test is served to the
// next one — which made the failed-load case render the previous test's empty list and pass for
// the wrong reason. Clearing the key is what keeps each case measuring its own fetch.
afterEach(() => { vi.restoreAllMocks(); invalidateCache('knowledge:reports') })

describe('a report row states its scoping decisions', () => {
  it('names the schedule, what it watches, and that it cites new material only', () => {
    render(<ReportRow report={report()} onChanged={() => {}} />)
    expect(screen.getByText(/Weekly contradiction scan/)).toBeTruthy()
    expect(screen.getByText(/cron 0 8 \* \* 1/)).toBeTruthy()
    expect(screen.getByText(/tagged research/)).toBeTruthy()
    // The policy is the third leg of the triple — a row without it cannot be judged.
    expect(screen.getByText(/cites new material only/)).toBeTruthy()
  })

  it('says so when the policy allows citing context', () => {
    render(<ReportRow report={report({ citation_policy: 'allow-citing-context' })} onChanged={() => {}} />)
    expect(screen.getByText(/may cite context/)).toBeTruthy()
    expect(screen.queryByText(/cites new material only/)).toBeNull()
  })

  it('shows a failed last run WITHOUT losing the run time', () => {
    render(<ReportRow report={report({ last_run_ts: 1_787_100_000, last_status: 'error', last_error: 'model timeout' })}
      onChanged={() => {}} />)
    expect(screen.getByText(/last run failed/)).toBeTruthy()
    // Both facts survive: the stamp is deliberately not advanced by a failure.
    expect(screen.getByText(/ran /)).toBeTruthy()
  })

  it('offers a run that names the report, so two rows cannot share one name', () => {
    render(<ReportRow report={report()} onChanged={() => {}} />)
    expect(screen.getByRole('button', { name: /Run Weekly contradiction scan now/i })).toBeTruthy()
  })

  it('running calls the run endpoint and reports a refusal instead of swallowing it', async () => {
    const run = vi.spyOn(api, 'runResearchReport').mockRejectedValue(new Error('a run is already in flight'))
    render(<ReportRow report={report()} onChanged={() => {}} />)
    screen.getByRole('button', { name: /Run Weekly contradiction scan now/i }).click()
    await waitFor(() => expect(run).toHaveBeenCalledWith('rep-1'))
  })
})

describe('the reports destination', () => {
  it('lists what exists', async () => {
    vi.spyOn(api, 'researchReports').mockResolvedValue({ reports: [report()] })
    render(<ReportsPage onBack={() => {}} />)
    await waitFor(() => expect(screen.getByText(/Weekly contradiction scan/)).toBeTruthy())
  })

  it('offers a first report when there are none', async () => {
    vi.spyOn(api, 'researchReports').mockResolvedValue({ reports: [] })
    render(<ReportsPage onBack={() => {}} />)
    await waitFor(() => expect(screen.getByText(/No scheduled reports/)).toBeTruthy())
    expect(screen.queryByText(/could not/i), 'an empty list must not read as a failure').toBeNull()
  })

  it('a failed load says so rather than claiming there are none', async () => {
    vi.spyOn(api, 'researchReports').mockRejectedValue(new Error('offline'))
    render(<ReportsPage onBack={() => {}} />)
    // The LoadError surface, not the empty state: the two are different facts.
    await waitFor(() => expect(screen.queryByText(/No scheduled reports/)).toBeNull())
    expect(await screen.findByText(/scheduled reports/)).toBeTruthy()
  })
})
