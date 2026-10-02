import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ScheduleJob } from '../../lib/api'
import { resetDataStore } from '../../lib/data'

// ── What a replace restore paused, and Resume ─────────────────────────────────────────────────────
//
// A replace restore wrote every automation back switched on and armed, so a copy restored onto a
// second machine ran each brief and digest beside the original, which still ran them. The restore
// now holds each one, switched off, with where its snapshot came from (`restore_hold`), and sends
// you here to resume them: one Resume all above the list that says why they wait, a badge on each
// held row, and a Resume on each one's panel. The last `describe` is the vacuity leg: with nothing
// held there is no notice, so the positive legs cannot be met by a page that always shows one.

type Row = Record<string, unknown>

const { API } = vi.hoisted(() => ({
  API: {
    schedules: vi.fn(() => Promise.resolve({ jobs: [] as unknown[] })),
    hooks: vi.fn(() => Promise.resolve([])),
    storeTriggers: vi.fn(() => Promise.resolve([] as Row[])),
    callbacks: vi.fn(() => Promise.resolve([])),
    triggerReview: vi.fn(() => Promise.resolve([])),
    actionProviders: vi.fn(() => Promise.resolve([])),
    autonomyLadder: vi.fn(() => Promise.reject(new Error('no ladder in this test'))),
    triggerVariables: vi.fn(() => Promise.resolve({ lifecycle: [], schedule: [], event: [], app_sources: [] })),
    triggerHistory: vi.fn(() => Promise.resolve({ runs: [], total: 0 })),
    toggleStoreTrigger: vi.fn(() => Promise.resolve({ ok: true })),
    enableSchedule: vi.fn(() => Promise.resolve({ ok: true })),
    resumeRestoredTriggers: vi.fn(() => Promise.resolve({ ok: true, resumed: [] as unknown[], still_held: [] as unknown[] })),
    channels: vi.fn(() => Promise.resolve([])),
  },
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: API,
}))
vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

function job(over: Partial<ScheduleJob> = {}): ScheduleJob {
  return {
    id: 'clock:morning-brief', name: 'Morning brief', message: '', enabled: false,
    schedule: 'At 07:00', every_secs: null, cron_expr: '0 7 * * *',
    action: { provider: 'notify', config: { title_template: 'Morning brief' } },
    last_run_ts: null, last_run_status: null, last_status: 'ok', run_count: 12,
    next_run_ts: null, is_running: false, warnings: [], broken: [], needs_grant: [],
    restore_hold: 'another_home',
    ...over,
  } as ScheduleJob
}

function storeRow(over: Row = {}): Row {
  return {
    kind: 'store', store_kind: 'event', id: 'store:event:new-mail', raw_id: 'event:new-mail',
    name: 'New mail', enabled: false, created_by: 'user', needs_review: false, needs_grant: [],
    spec: { source: 'inbox', pattern: 'InboxMessage' },
    action: { provider: 'notify', config: { title_template: 'New mail' } },
    health: 'ok', state: 'active', run_count: 4, last_error: '', broken: [], warnings: [],
    restore_hold: 'another_home',
    ...over,
  }
}

async function mountTriggers(query: Record<string, string> = {}) {
  const { TriggersSection } = await import('./TriggersSection')
  render(<TriggersSection sub="" navigate={vi.fn()} navEpoch={0} query={query} setQuery={() => {}} />)
}

const toasts: string[] = []
window.addEventListener('ne:toast', (e) => toasts.push((e as CustomEvent).detail.message))

// `useQuery` keeps a module-global cache, so a list one test fetched would be painted in the next
// while it re-read: each test measures its own rows only with the cache emptied after the last.
afterEach(() => { resetDataStore() })

beforeEach(() => {
  sessionStorage.clear()
  toasts.length = 0
  API.toggleStoreTrigger.mockClear()
  API.enableSchedule.mockClear()
  API.resumeRestoredTriggers.mockClear()
  API.schedules.mockImplementation(() => Promise.resolve({ jobs: [
    job(),
    job({ id: 'system:source-digest', name: 'Morning source digest', cron_expr: '0 8 * * *' }),
    // The owner's own pause: not the restore's, so not counted and not badged as one.
    job({ id: 'clock:weekly-review', name: 'Weekly review', restore_hold: '' }),
  ] }))
  API.storeTriggers.mockImplementation(() => Promise.resolve([storeRow()]))
})

describe('the notice above the Triggers list', () => {
  it('says how many came back paused, why, and when resuming them is safe', async () => {
    await mountTriggers()
    const notice = await screen.findByTestId('restore-hold')
    expect(within(notice).getByRole('heading', { name: 'Paused by the restore' })).toBeInTheDocument()
    expect(notice).toHaveTextContent(
      '3 automations came back paused when this home was restored from a snapshot of another PersonalClaw home. '
      + 'If that home is still running, the same automations run there too: resume them here once it is retired.',
    )
  })

  it('Resume all resumes them, says so, and reads the lists again', async () => {
    API.resumeRestoredTriggers.mockImplementationOnce(() => Promise.resolve({
      ok: true,
      resumed: [{ id: 'clock:morning-brief', name: 'Morning brief' }, { id: 'event:new-mail', name: 'New mail' }],
      still_held: [{ id: 'system:source-digest', name: 'Morning source digest', reason: 'its action needs your yes' }],
    }))
    await mountTriggers()
    const notice = await screen.findByTestId('restore-hold')
    const reads = API.schedules.mock.calls.length
    fireEvent.click(within(notice).getByRole('button', { name: /Resume all/ }))
    await waitFor(() => expect(API.resumeRestoredTriggers).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(toasts).toContain('Resumed 2 automations.'))
    expect(toasts).toContain('Morning source digest stays paused: its action needs your yes')
    await waitFor(() => expect(API.schedules.mock.calls.length).toBeGreaterThan(reads))
  })

  it("a home restored from its own snapshot is told nothing else runs them", async () => {
    API.schedules.mockImplementation(() => Promise.resolve({ jobs: [job({ restore_hold: 'this_home' })] }))
    API.storeTriggers.mockImplementation(() => Promise.resolve([]))
    await mountTriggers()
    expect(await screen.findByTestId('restore-hold')).toHaveTextContent(
      '1 automation came back paused when this home was restored from its own snapshot. Nothing else runs it: resume it when you are ready.',
    )
  })

  it('a snapshot that does not say where it comes from is read the careful way', async () => {
    API.schedules.mockImplementation(() => Promise.resolve({ jobs: [job({ restore_hold: 'this_home' }), job({ id: 'clock:b', restore_hold: 'unknown' })] }))
    API.storeTriggers.mockImplementation(() => Promise.resolve([]))
    await mountTriggers()
    expect(await screen.findByTestId('restore-hold')).toHaveTextContent(
      'restored from a snapshot that does not say which PersonalClaw home it comes from. If that home is still running',
    )
  })
})

describe('each held automation', () => {
  it('is badged in the list as paused by the restore, and the owner-paused one as disabled', async () => {
    await mountTriggers()
    await waitFor(() => expect(screen.getByText('Weekly review')).toBeInTheDocument())
    expect(screen.getAllByText('· paused by the restore')).toHaveLength(3)
    expect(screen.getAllByText('· disabled')).toHaveLength(1)
  })

  it("opens saying why it waits, and its Resume sends its switch on", async () => {
    await mountTriggers({ open: 'store:event:new-mail' })
    await waitFor(() => expect(screen.getByText('Paused by the restore — it does not run until you resume it')).toBeInTheDocument())
    expect(screen.getByRole('note')).toHaveTextContent(
      'This home was restored from a snapshot of another PersonalClaw home. If that home is still running, this automation runs there too: resume it here once that home is retired.',
    )
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
    await waitFor(() => expect(API.toggleStoreTrigger).toHaveBeenCalledWith('event:new-mail', true))
  })

  it("a held schedule's panel resumes it the same way", async () => {
    const { ScheduleDetail } = await import('../schedule/ScheduleDetail')
    render(
      <ScheduleDetail job={job()} onSaved={vi.fn()} onDeleted={vi.fn()} onChanged={vi.fn()}
        editing={false} onEditingChange={vi.fn()} />,
    )
    expect(await screen.findByText('Paused by the restore')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
    await waitFor(() => expect(API.enableSchedule).toHaveBeenCalledWith('clock:morning-brief', true))
  })
})

describe('with nothing held', () => {
  it('there is no notice and no badge', async () => {
    API.schedules.mockImplementation(() => Promise.resolve({ jobs: [job({ restore_hold: '', enabled: true })] }))
    API.storeTriggers.mockImplementation(() => Promise.resolve([storeRow({ restore_hold: '', enabled: true })]))
    await mountTriggers()
    await waitFor(() => expect(screen.getByText('Morning brief')).toBeInTheDocument())
    expect(screen.queryByTestId('restore-hold')).not.toBeInTheDocument()
    expect(screen.queryByText('· paused by the restore')).not.toBeInTheDocument()
  })

  it('a row the server sends switched on never reads as held', async () => {
    API.schedules.mockImplementation(() => Promise.resolve({ jobs: [job({ enabled: true })] }))
    API.storeTriggers.mockImplementation(() => Promise.resolve([]))
    await mountTriggers()
    await waitFor(() => expect(screen.getByText('Morning brief')).toBeInTheDocument())
    expect(screen.queryByTestId('restore-hold')).not.toBeInTheDocument()
  })
})
