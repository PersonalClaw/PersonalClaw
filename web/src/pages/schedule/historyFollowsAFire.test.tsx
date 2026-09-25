import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import type { ScheduleJob, ScheduleRun, Trigger as WireTrigger } from '../../lib/api'

// ── A fire that lands while the panel is open shows up in its history ────────────────────────────
//
// Measured on a live gateway: a notify trigger firing every 60s, its panel open. The fire landed and
// "Last run" read "ok · just now" (the list poll carries `last_run_ts`), while the History section
// directly beneath it still read "No runs recorded yet." The history re-read only on a counter that
// Run now bumped, so a run the SCHEDULER made never reached it until the panel was closed and opened.
// Both panels share `RunHistory`, and both are pinned here.

const { API } = vi.hoisted(() => ({
  API: {
    triggerHistory: vi.fn(),
    triggerRunDetail: vi.fn(),
  },
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: API,
}))

const { ScheduleDetail } = await import('./ScheduleDetail')
const { StoreTriggerDetail } = await import('../triggers/StoreTriggerDetail')

const FIRE: ScheduleRun = {
  run_id: 'fire-1790367812735', job_id: 'clock:standup-nudge', trigger: 'ok',
  started_at: 1790367812.7, finished_at: 1790367812.7, duration_ms: 0, status: 'success', summary: '', error: '',
}

function job(over: Partial<ScheduleJob> = {}): ScheduleJob {
  return {
    id: 'clock:standup-nudge', name: 'Standup nudge', message: '', enabled: true,
    schedule: 'every 60s', every_secs: 60, cron_expr: null,
    action: { provider: 'notify', config: { title_template: 'Standup nudge: review Q4 tasks' } },
    last_run_ts: null, last_status: null, run_count: 0,
    next_run_ts: null, is_running: false, warnings: [], broken: [],
    ...over,
  }
}

const storeRow = (over: Partial<WireTrigger> = {}): WireTrigger => ({
  kind: 'store', id: 'store:file:notes', raw_id: 'file:notes',
  name: 'Summarize notes', enabled: true, action: { provider: 'notify', config: {} },
  store_kind: 'file', spec: { paths: ['~/notes/**'] }, broken: [], run_count: 0,
  ...over,
})

beforeEach(() => {
  API.triggerHistory.mockReset().mockResolvedValue({ runs: [], total: 0, supported: true })
  API.triggerRunDetail.mockReset()
})

describe('the run history follows a fire the scheduler made', () => {
  it('a schedule panel re-reads its history when `last_run_ts` moves', async () => {
    const props = { providers: [], onSaved: vi.fn(), onDeleted: vi.fn(), onChanged: vi.fn(), editing: false, onEditingChange: vi.fn() }
    const view = render(<ScheduleDetail job={job()} {...props} />)
    await screen.findByText('No runs recorded yet.')

    // The scheduler fires; the list's next poll hands the panel the new `last_run_ts`.
    API.triggerHistory.mockResolvedValue({ runs: [FIRE], total: 1, supported: true })
    view.rerender(<ScheduleDetail job={job({ last_run_ts: 1790367812.7, last_status: 'ok', run_count: 1 })} {...props} />)

    await screen.findByText('History · 1')
    expect(screen.queryByText('No runs recorded yet.')).toBeNull()
    expect(API.triggerHistory).toHaveBeenCalledTimes(2)
  })

  it('an automation panel re-reads its history when `run_count` moves', async () => {
    const view = render(<StoreTriggerDetail trigger={storeRow()} onChanged={vi.fn()} onDeleted={vi.fn()} />)
    await screen.findByText('No runs recorded yet.')

    API.triggerHistory.mockResolvedValue({ runs: [{ ...FIRE, job_id: 'file:notes' }], total: 1, supported: true })
    view.rerender(<StoreTriggerDetail trigger={storeRow({ run_count: 1 })} onChanged={vi.fn()} onDeleted={vi.fn()} />)

    await screen.findByText('History · 1')
    expect(API.triggerHistory).toHaveBeenCalledTimes(2)
  })

  it('a re-render that moved nothing does not re-read', async () => {
    const props = { providers: [], onSaved: vi.fn(), onDeleted: vi.fn(), onChanged: vi.fn(), editing: false, onEditingChange: vi.fn() }
    const view = render(<ScheduleDetail job={job({ last_run_ts: 1790367812.7 })} {...props} />)
    await screen.findByText('No runs recorded yet.')
    view.rerender(<ScheduleDetail job={job({ last_run_ts: 1790367812.7, next_run_ts: 1790367872.7 })} {...props} />)
    await waitFor(() => expect(API.triggerHistory).toHaveBeenCalledTimes(1))
  })
})
