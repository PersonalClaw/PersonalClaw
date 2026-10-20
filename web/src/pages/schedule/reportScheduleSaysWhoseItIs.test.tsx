import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { api, type ScheduleJob } from '../../lib/api'

// ── An automation that is a report's schedule says so, where it is edited and where it is deleted ──
//
// A scheduled report keeps its time on the report and fires through an automation on this page. The
// two are one schedule: moving the time or flipping the switch here moves the report, its name is
// the report's (a rename here is refused), and deleting it leaves the report unscheduled. The panel
// read like any other automation's, so nothing here said any of that, and the delete dialog's "This
// cannot be undone" read as if the report itself were going. The row names its report
// (`report_id`, from the server's own rule), and the panel says what that means.

const confirmDelete = vi.fn<(entity: string, name?: string, opts?: { body?: string }) => Promise<boolean>>()
vi.mock('../../ui/dialog', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  confirmDelete: (...args: [string, string?, { body?: string }?]) => confirmDelete(...args),
}))

const { ScheduleDetail } = await import('./ScheduleDetail')

function job(over: Partial<ScheduleJob> = {}): ScheduleJob {
  return {
    id: 'schedule:report-schedule:rpt-weekly', name: 'Research report: Weekly releases', message: '',
    enabled: true, schedule: 'At 8:00 AM EDT, only on Monday', cron_expr: '0 8 * * 1', every_secs: null,
    action: { provider: 'knowledge-report', config: { report_id: 'rpt-weekly' } },
    report_id: 'rpt-weekly', timezone: 'America/Toronto',
    last_run_ts: null, last_run_status: '', last_status: 'ok', run_count: 0,
    next_run_ts: null, is_running: false, warnings: [], broken: [], revision: 'r1',
    ...over,
  }
}

function mount(j: ScheduleJob, editing = false) {
  render(
    <ScheduleDetail job={j} onSaved={vi.fn()} onDeleted={vi.fn()} onChanged={vi.fn()}
      editing={editing} onEditingChange={vi.fn()} />,
  )
}

const NOTE = /This is the schedule of a report/

beforeEach(() => {
  confirmDelete.mockReset()
  vi.spyOn(api, 'triggerHistory').mockResolvedValue({ runs: [], total: 0 })
  vi.spyOn(api, 'channels').mockResolvedValue([])
})

describe("a report's schedule on the Triggers page", () => {
  it('says whose schedule it is, and where the report itself is renamed or deleted', () => {
    mount(job())
    expect(screen.getByText(NOTE)).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Knowledge › Reports' }).getAttribute('href')).toBe('#/knowledge/reports')
  })

  it('says it in the edit form too, where a rename is refused', () => {
    mount(job(), true)
    expect(screen.getByText(NOTE)).toBeTruthy()
  })

  it('asks before deleting it in words that say the report stays', async () => {
    confirmDelete.mockResolvedValue(false)
    mount(job())
    fireEvent.click(screen.getByRole('button', { name: /delete/i }))
    await waitFor(() => expect(confirmDelete).toHaveBeenCalled())
    const [, name, opts] = confirmDelete.mock.calls[0]
    expect(name).toBe('Research report: Weekly releases')
    expect(opts?.body).toMatch(/^The report stays, with no schedule: it runs when you press Run now\./)
    expect(opts?.body).toContain('Its run history is removed too.')
  })

  it('an automation that is no report’s schedule says none of it', async () => {
    confirmDelete.mockResolvedValue(false)
    // Runs the same action, but it is an automation of its own: the server names no report.
    mount(job({ id: 'schedule:clock:also-runs-it', name: 'Also runs it', report_id: null }))
    expect(screen.queryByText(NOTE)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /delete/i }))
    await waitFor(() => expect(confirmDelete).toHaveBeenCalled())
    expect(confirmDelete.mock.calls[0][2]?.body).toBe('Its run history is removed too. This cannot be undone.')
  })
})
