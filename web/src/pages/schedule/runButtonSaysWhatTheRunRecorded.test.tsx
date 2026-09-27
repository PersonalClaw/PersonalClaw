import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ScheduleJob, Trigger as WireTrigger, TriggerReviewCard } from '../../lib/api'

// ── A Run button says what its run recorded, the way the run's history row says it ───────────────
//
// Measured on a gateway: a browse trigger at a sign-in page, Run now. The run recorded `waiting` and
// its history row read "waiting for you", while the button flashed "Run finished" — the schedule
// panel took its flash from the trigger's HEALTH rollup, which says how the automation has been
// going and nothing about this run. The automation panel said "Ran", and the restart review's card
// said the automation "ran now". `/run` now answers the status its run recorded, and every Run
// button says it in the row's own words.

const { API } = vi.hoisted(() => ({
  API: {
    runSchedule: vi.fn(),
    runStoreTrigger: vi.fn(),
    decideTriggerReview: vi.fn(),
    triggerHistory: vi.fn(() => Promise.resolve({ runs: [], total: 0, supported: true })),
    triggerRunDetail: vi.fn(),
    channels: vi.fn(() => Promise.resolve([])),
  },
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: API,
}))

const { ScheduleDetail } = await import('./ScheduleDetail')
const { StoreTriggerDetail } = await import('../triggers/StoreTriggerDetail')
const { TriggerReview } = await import('../triggers/TriggerReview')
const { runFlashMeta } = await import('./scheduleMeta')

const WAITING_LINE = 'Waiting for you. Sign in to bank.example, then confirm — the browse run resumes with that session.'

function job(over: Partial<ScheduleJob> = {}): ScheduleJob {
  return {
    id: 'clock:balance', name: 'Check my balance', message: '', enabled: true,
    schedule: 'every day at 09:00', every_secs: null, cron_expr: '0 9 * * *',
    action: { provider: 'browse', config: { goal: 'read my balance' } },
    last_run_ts: null, last_status: 'ok', run_count: 0,
    next_run_ts: null, is_running: false, warnings: [], broken: [],
    ...over,
  }
}

const props = { providers: [], onSaved: vi.fn(), onDeleted: vi.fn(), onChanged: vi.fn(), editing: false, onEditingChange: vi.fn() }

/** Press Run now on the schedule panel, then hand it the row the list's next poll would: the run
 *  landed, so `last_run_ts` moved. What the button reads then is its flash. */
async function pressAndLand(status: string | undefined): Promise<HTMLElement> {
  API.runSchedule.mockResolvedValue({ ok: true, name: 'Check my balance', result: 'ran', status })
  const view = render(<ScheduleDetail job={job()} {...props} />)
  fireEvent.click(screen.getByRole('button', { name: /Run now/ }))
  await screen.findByText('Running…')
  view.rerender(<ScheduleDetail job={job({ last_run_ts: 1790367812.7 })} {...props} />)
  return await screen.findByText(/Run finished|Waiting for you|Launched|Queued/)
}

beforeEach(() => {
  cleanup()
  for (const fn of Object.values(API)) fn.mockClear()
})

describe('the schedule panel’s Run button', () => {
  it('🔴 says a run that stopped for you is waiting, as its history row does', async () => {
    const flash = await pressAndLand('waiting')
    expect(flash.textContent).toBe('Waiting for you')
    expect(screen.queryByText('Run finished')).toBeNull()
  })

  it('says a run that did its work finished', async () => {
    expect((await pressAndLand('success')).textContent).toBe('Run finished')
  })

  it('says a run that only started a workflow launched it, and did not finish it', async () => {
    expect((await pressAndLand('launched')).textContent).toBe('Launched')
  })

  it('reads the run, not the trigger’s health: a failing automation’s good run finished', async () => {
    // The flash used to read `last_status` — the health rollup — so this is the case it could not
    // tell apart from the run.
    API.runSchedule.mockResolvedValue({ ok: true, result: 'ran', status: 'success' })
    const view = render(<ScheduleDetail job={job({ last_status: 'failing' })} {...props} />)
    fireEvent.click(screen.getByRole('button', { name: /Run now/ }))
    await screen.findByText('Running…')
    view.rerender(<ScheduleDetail job={job({ last_status: 'failing', last_run_ts: 1790367812.7 })} {...props} />)
    expect((await screen.findByText(/Run finished|Waiting for you/)).textContent).toBe('Run finished')
  })
})

describe('the automation panel’s Run button', () => {
  const row = (): WireTrigger => ({
    kind: 'store', id: 'store:manual:balance', raw_id: 'manual:balance',
    name: 'Check my balance', enabled: true, action: { provider: 'browse', config: {} },
    store_kind: 'manual', spec: {}, broken: [], run_count: 0,
  })

  it('🔴 says a run that stopped for you is waiting, not "Ran"', async () => {
    API.runStoreTrigger.mockResolvedValue({ ok: true, result: WAITING_LINE, status: 'waiting' })
    render(<StoreTriggerDetail trigger={row()} onChanged={vi.fn()} onDeleted={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /Run now/ }))
    expect(await screen.findByText('Waiting for you')).toBeTruthy()
    expect(screen.queryByText('Ran')).toBeNull()
  })

  it('says a run that did its work finished', async () => {
    API.runStoreTrigger.mockResolvedValue({ ok: true, result: 'ran', status: 'success' })
    render(<StoreTriggerDetail trigger={row()} onChanged={vi.fn()} onDeleted={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /Run now/ }))
    expect(await screen.findByText('Run finished')).toBeTruthy()
  })
})

describe('the restart review’s Run now', () => {
  const CARD: TriggerReviewCard = {
    trigger_id: 'clock:balance', kind: 'missed', count: 1, latest: Date.now() / 1000 - 3600,
    oldest: Date.now() / 1000 - 3600, reason: '', count_is_floor: false, name: 'Check my balance',
    open_id: 'schedule:clock:balance',
  }

  it('🔴 says a run that stopped for you is waiting, in its row’s words, not that it ran', async () => {
    const toasts: string[] = []
    const heard = (e: Event) => toasts.push((e as CustomEvent).detail.message)
    window.addEventListener('ne:toast', heard)
    API.decideTriggerReview.mockResolvedValue({ ok: true, outcome: 'ran_late', result: WAITING_LINE, status: 'waiting' })
    render(<TriggerReview cards={[CARD]} onRetry={vi.fn()} onDecided={vi.fn()} onOpen={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /Run now/ }))
    await vi.waitFor(() => expect(toasts).toContain(`Check my balance: ${WAITING_LINE}`))
    expect(toasts.some((t) => t.includes('ran now'))).toBe(false)
    window.removeEventListener('ne:toast', heard)
  })
})

describe('runFlashMeta', () => {
  it('uses the history row’s own word for every run that did not finish its work', () => {
    expect(runFlashMeta('waiting').label).toBe('Waiting for you')
    expect(runFlashMeta('launched').label).toBe('Launched')
    expect(runFlashMeta('queued').label).toBe('Queued')
  })

  it('says "Run finished" for a run that did its work, late or with nothing to do', () => {
    for (const status of ['success', 'ran_late', 'skipped_noop', '', undefined]) {
      expect(runFlashMeta(status).label, String(status)).toBe('Run finished')
    }
  })
})
