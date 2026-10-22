/** The Automations list shows the route's named order, under its groups' headings (#3816).
 *
 *  `GET /api/triggers` states the order once (`triggers/listing.py`, held by
 *  `tests/test_automations_are_listed_needs_you_then_next_run_then_name.py`): Needs you, then Next
 *  to run, then Not on a clock, each row with its group and its place in the whole list. The page
 *  lays the rows out as served, under the three headings, an empty group left out, and moves no
 *  row: the same rows read again in another order lay out the same. Each row says where it
 *  reports, and the one it last ran says how that went.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import type { CallbackRow, ScheduleJob, Trigger as WireTrigger } from '../../lib/api'

const { STATE } = vi.hoisted(() => ({
  STATE: { jobs: [] as unknown[], stores: [] as unknown[], callbacks: [] as unknown[], reads: 0 },
}))

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {}, refreshKinds: () => [] }))
vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    automations: () => {
      STATE.reads += 1
      return Promise.resolve({ jobs: STATE.jobs, hooks: [], stores: STATE.stores, callbacks: STATE.callbacks, server_tz: 'UTC', unreadable: [] })
    },
    triggerReview: () => Promise.resolve([]),
    actionProviders: () => Promise.resolve([]),
    autonomyLadder: () => Promise.reject(new Error('no ladder in this test')),
    triggerVariables: () => Promise.resolve({ lifecycle: [], schedule: [], event: [] }),
  },
}))

const { TriggersSection } = await import('../triggers/TriggersSection')
const { invalidateKeys } = await import('../../lib/data')

const NOW = Math.floor(Date.now() / 1000)

const job = (id: string, name: string, over: Partial<ScheduleJob> = {}): ScheduleJob => ({
  id, name, message: '', enabled: true, schedule: 'every hour', action: { provider: 'invoke-agent', config: {} },
  state: 'active', ...over,
} as ScheduleJob)
const store = (raw: string, name: string, over: Partial<WireTrigger> = {}): WireTrigger => ({
  kind: 'store', id: `store:${raw}`, raw_id: raw, name, enabled: true, store_kind: raw.split(':')[0],
  action: { provider: 'notify', config: {} }, spec: {}, state: 'active', ...over,
} as WireTrigger)

/** As the route serves them: in its order, each with its group and rank. */
function served() {
  STATE.jobs = [
    job('backup', 'Backup', { group: 'needs_you', rank: 1, last_run_status: 'failure', last_run_ts: NOW - 3600, next_run_ts: NOW + 30, reports_to: { kind: 'channel', channel: 'slack' } }),
    job('brief', 'Morning brief', { group: 'next_to_run', rank: 2, next_run_ts: NOW + 600, last_run_status: 'success', last_run_ts: NOW - 86400, reports_to: { kind: 'thread', session: 'chat-1', title: 'Planning' } }),
    job('weekly', 'Weekly review', { group: 'next_to_run', rank: 3, next_run_ts: NOW + 7200, reports_to: { kind: 'inbox' } }),
    job('archive', 'Archive', { group: 'not_on_a_clock', rank: 4, enabled: false, reports_to: { kind: 'none' } }),
  ]
  STATE.stores = [
    store('file:notes', 'Notes watcher', { group: 'not_on_a_clock', rank: 6 }),
    store('event:mail', 'inbox mail', { group: 'not_on_a_clock', rank: 5, store_kind: 'event', spec: { pattern: 'InboxMessage' } }),
  ]
  STATE.callbacks = [{
    kind: 'callback', id: 'callback:cb1', raw_id: 'cb1', name: 'cb1', enabled: false, created_by: 'agent',
    needs_grant: ['Run an agent turn'], context_summary: 'deploy finished', session_key: 'chat-2', url: '', reach: '',
    registered_at: NOW - 7000, seal: 's', group: 'needs_you', rank: 0, reports_to: { kind: 'thread', session: 'chat-2' },
  } satisfies CallbackRow]
}

const mount = () => render(<TriggersSection sub="" navigate={vi.fn()} navEpoch={0} query={{}} setQuery={() => {}} />)

/** Each group's heading, and the row names under it, top to bottom. */
function laidOut(): Array<[string, string[]]> {
  return screen.getAllByRole('heading', { level: 2 }).map((h) => {
    const section = h.closest('section') as HTMLElement
    const rows = within(section).getAllByRole('button').map((b) => b.getAttribute('aria-label') ?? '').filter(Boolean)
    return [h.textContent ?? '', rows]
  })
}

const EXPECTED: Array<[string, string[]]> = [
  ['Needs you', ['cb1', 'Backup']],
  ['Next to run', ['Morning brief', 'Weekly review']],
  ['Not on a clock', ['Archive', 'inbox mail', 'Notes watcher']],
]

beforeEach(() => {
  sessionStorage.clear()
  invalidateKeys('automations:list')
  STATE.reads = 0
  served()
})
afterEach(() => cleanup())

describe('the Automations list shows the order the route states', () => {
  it('lays the rows out as served, under the three headings', async () => {
    mount()
    await waitFor(() => expect(screen.getByText('Weekly review')).toBeInTheDocument())
    expect(laidOut()).toEqual(EXPECTED)
  })

  it('a refresh with the same rows in another order changes nothing on screen', async () => {
    mount()
    await waitFor(() => expect(screen.getByText('Weekly review')).toBeInTheDocument())
    const before = laidOut()
    STATE.jobs = [...STATE.jobs].reverse()
    STATE.stores = [...STATE.stores].reverse()
    const reads = STATE.reads
    // The page read again, as its poll and a `crons` push make it: the same rows, another order.
    cleanup()
    invalidateKeys('automations:list')
    mount()
    await waitFor(() => expect(STATE.reads).toBeGreaterThan(reads))
    await waitFor(() => expect(screen.getByText('Weekly review')).toBeInTheDocument())
    expect(laidOut()).toEqual(before)
  })

  it('leaves out a group with no rows, heading and all', async () => {
    STATE.jobs = (STATE.jobs as ScheduleJob[]).filter((j) => j.group !== 'next_to_run')
    mount()
    await waitFor(() => expect(screen.getByText('Backup')).toBeInTheDocument())
    expect(laidOut().map(([h]) => h)).toEqual(['Needs you', 'Not on a clock'])
    expect(screen.queryByRole('heading', { name: 'Next to run' })).toBeNull()
  })

  it('each row says where it reports', async () => {
    mount()
    await waitFor(() => expect(screen.getByText('Weekly review')).toBeInTheDocument())
    // The row's button is a zero-content overlay (`ListRow`); what the row says is its card's.
    const row = (name: string) => screen.getByRole('button', { name }).parentElement as HTMLElement
    expect(row('Morning brief').textContent).toContain('Reports to “Planning”')
    expect(row('cb1').textContent).toContain('Reports to its thread')
    expect(row('Backup').textContent).toContain('Reports to Slack')
    expect(row('Weekly review').textContent).toContain('Reports to the Inbox')
    expect(row('Archive').textContent).toContain('Silent')
    expect(row('Notes watcher').textContent).not.toContain('Reports to')
  })

  it('a row the route places nowhere still lists, in the order it came, under no heading', async () => {
    // An older gateway sends no group: the page shows its rows as sent rather than inventing one.
    STATE.jobs = [job('b', 'Second'), job('a', 'First')]
    STATE.stores = []
    STATE.callbacks = []
    mount()
    await waitFor(() => expect(screen.getByText('First')).toBeInTheDocument())
    expect(screen.queryAllByRole('heading', { level: 2 })).toEqual([])
    const names = screen.getAllByRole('button').map((b) => b.getAttribute('aria-label')).filter((n) => n === 'First' || n === 'Second')
    expect(names).toEqual(['Second', 'First'])
  })
})
