import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ReportRow, ReportsPage } from './ReportsPage'
import { api, type ResearchReport } from '../../lib/api'
import { invalidateKeys } from '../../lib/data'

// ── The scheduled-reports destination ────────────────────────────────────────────
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
    last_result: '',
    watermark_ts: 0,
    schedule_shown: {
      words: 'At 8:00 AM PDT, only on Monday', timezone: 'America/Los_Angeles', next_run_at: '2026-10-05T15:00:00+00:00',
    },
    sources_shown: 'Reads what is new in your knowledge tagged research each time it runs. It does not search the web.',
    ...over,
  }
}

/** The toasts a test raised (`notify` dispatches `ne:toast` for the shell to render). */
function toasts(): Array<{ message: string; level: string }> {
  const seen: Array<{ message: string; level: string }> = []
  window.addEventListener('ne:toast', (e) => seen.push((e as CustomEvent).detail))
  return seen
}

// `useQuery` keeps a MODULE-GLOBAL cache, so a list fetched by one test is served to the
// next one — which made the failed-load case render the previous test's empty list and pass for
// the wrong reason. Clearing the key is what keeps each case measuring its own fetch.
afterEach(() => { vi.restoreAllMocks(); invalidateKeys('knowledge:reports') })

describe('a report row states its scoping decisions', () => {
  it('names the schedule, what it reads, that it does not search the web, and its citations', () => {
    render(<ReportRow report={report()} onChanged={() => {}} />)
    expect(screen.getByText(/Weekly contradiction scan/)).toBeTruthy()
    expect(screen.getByText(/At 8:00 AM PDT, only on Monday/)).toBeTruthy()
    // The server's own sentence for what it reads, worded from the scope a run reads.
    expect(screen.getByText(
      'Reads what is new in your knowledge tagged research each time it runs. It does not search the web.',
    )).toBeTruthy()
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
    expect(screen.getByText(/Last run /)).toBeTruthy()
  })

  it('🔑 says what the last run found — a run that found nothing new says so', () => {
    render(<ReportRow report={report({
      last_run_ts: Date.now() / 1000 - 120,
      last_status: 'nothing_new',
      last_result: 'Found no new material in your knowledge tagged research since its previous run.',
    })} onChanged={() => {}} />)
    expect(screen.getByText(
      'Last run 2m ago · Found no new material in your knowledge tagged research since its previous run.',
    )).toBeTruthy()
  })

  it('a report that has never run says so', () => {
    render(<ReportRow report={report()} onChanged={() => {}} />)
    expect(screen.getByText('Never run yet.')).toBeTruthy()
  })

  it('offers a run that names the report, so two rows cannot share one name', () => {
    render(<ReportRow report={report()} onChanged={() => {}} />)
    expect(screen.getByRole('button', { name: /Run Weekly contradiction scan now/i })).toBeTruthy()
  })

  it('running calls the run endpoint and reports a refusal instead of swallowing it', async () => {
    const seen = toasts()
    const run = vi.spyOn(api, 'runResearchReport').mockRejectedValue(new Error('a run is already in flight'))
    render(<ReportRow report={report()} onChanged={() => {}} />)
    screen.getByRole('button', { name: /Run Weekly contradiction scan now/i }).click()
    await waitFor(() => expect(run).toHaveBeenCalledWith('rep-1'))
    await waitFor(() => expect(seen).toContainEqual({ message: 'a run is already in flight', level: 'error' }))
  })

  it('🔑 Run now says what the run found, not that it started', async () => {
    const seen = toasts()
    vi.spyOn(api, 'runResearchReport').mockResolvedValue({
      ok: true, report_id: 'rep-1', outcome: 'nothing_new',
      result: 'Found no new material in your knowledge tagged research since its previous run.',
    })
    const onChanged = vi.fn()
    render(<ReportRow report={report()} onChanged={onChanged} />)
    screen.getByRole('button', { name: /Run Weekly contradiction scan now/i }).click()
    await waitFor(() => expect(seen).toContainEqual({
      message: 'Weekly contradiction scan: Found no new material in your knowledge tagged research since its previous run.',
      level: 'info',
    }))
    expect(seen.some((t) => /started/.test(t.message))).toBe(false)
    // The card re-reads, so it says the same as the toast did.
    expect(onChanged).toHaveBeenCalled()
  })

  it('a run that failed says it failed, in its own words', async () => {
    const seen = toasts()
    vi.spyOn(api, 'runResearchReport').mockResolvedValue({
      ok: false, report_id: 'rep-1', outcome: 'failed', result: 'knowledge-report: rep-1 failed: the model timed out',
    })
    render(<ReportRow report={report()} onChanged={() => {}} />)
    screen.getByRole('button', { name: /Run Weekly contradiction scan now/i }).click()
    await waitFor(() => expect(seen).toContainEqual({
      message: 'Weekly contradiction scan: knowledge-report: rep-1 failed: the model timed out', level: 'error',
    }))
  })
})

// ── What a report can read, said where it is made ───────────────────────────────────────────────
//
// A report was made with the prompt "What shipped this week, with links" and read only the
// library, which nothing on the page said: the form promised "anything new" and never that the
// web is not one of its sources. It says so first now, with the way to bring a site in.
describe('the report form', () => {
  it('🔑 says a report reads only your knowledge and does not search the web', async () => {
    vi.spyOn(api, 'researchReports').mockResolvedValue({ reports: [] })
    render(<ReportsPage onBack={() => {}} />)
    // The header's New report and the empty list's both open the same form.
    fireEvent.click((await screen.findAllByRole('button', { name: 'New report' }))[0])
    const said = screen.getByText(/A report reads only your knowledge/)
    expect(said.textContent).toMatch(/It does\s+not search the web\./)
    const link = screen.getByRole('link', { name: /watch\s+it as a source/ })
    expect(link.getAttribute('href')).toBe('#/knowledge/sources')
  })

  it('🔑 edits a report in place, and sends only what changed', async () => {
    const update = vi.spyOn(api, 'updateResearchReport').mockResolvedValue(report())
    const onChanged = vi.fn()
    render(<ReportRow report={report()} onChanged={onChanged} />)
    fireEvent.click(screen.getByRole('button', { name: 'Edit Weekly contradiction scan' }))
    fireEvent.change(screen.getByDisplayValue('0 8 * * 1'), { target: { value: '28 3 * * 5' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save report' }))
    await waitFor(() => expect(update).toHaveBeenCalledWith('rep-1', {
      schedule: { kind: 'cron', cron_expr: '28 3 * * 5' },
    }))
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })
})

// ── A schedule is said in words, with its zone and its next run ─────────────────────────────────
//
// A report set for Monday 08:00 Toronto read "cron 0 8 * * 1" here, while the Triggers page said
// "At 8:00 AM EDT, only on Monday … in 5d" of the very same schedule. The row now says it the way
// the Triggers page does, names the zone, and says when it next runs, in that zone.
describe('a report row says when it runs', () => {
  const TORONTO = {
    words: 'At 8:00 AM EDT, only on Monday', timezone: 'America/Toronto', next_run_at: '2026-10-05T12:00:00+00:00',
  }
  afterEach(() => { vi.useRealTimers() })

  it('🔑 in words, with the zone it runs in and its next run in that zone', () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-10-01T12:00:00Z'))
    render(<ReportRow report={report({ tz: 'America/Toronto', schedule_shown: TORONTO })} onChanged={() => {}} />)
    const line = screen.getByText(/At 8:00 AM EDT, only on Monday/)
    expect(line.textContent).toBe(
      'At 8:00 AM EDT, only on Monday (America/Toronto) · next run Mon, Oct 5, 8:00 AM EDT, in 4d')
    expect(screen.queryByText(/cron/)).toBeNull()
  })

  it('a paused report says it does not run, not when it would', () => {
    render(<ReportRow report={report({ enabled: false, schedule_shown: { ...TORONTO, next_run_at: '' } })}
      onChanged={() => {}} />)
    expect(screen.getByText(/At 8:00 AM EDT, only on Monday \(America\/Toronto\) · paused, so it does not run/))
      .toBeTruthy()
    expect(screen.queryByText(/next run/)).toBeNull()
  })

  it('a report with no schedule says how it runs', () => {
    render(<ReportRow report={report({ schedule_shown: { words: '', timezone: '', next_run_at: '' } })}
      onChanged={() => {}} />)
    expect(screen.getByText('No schedule: it runs when you press Run now')).toBeTruthy()
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
