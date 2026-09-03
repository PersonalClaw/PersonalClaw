import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

// A schedule the store loaded leniently despite a malformed field (S87) carries its parse errors
// as `broken`, exactly like a store trigger. The list's "· needs attention" flag used to be gated
// to `kind === 'store'`, so a broken SCHEDULE listed as if healthy — the automation silently would
// not fire and the user had no way to see why. This drives the real `TriggersSection` with a broken
// schedule row and asserts the flag now shows; the vacuity leg asserts a healthy schedule stays
// quiet, so the positive cannot be satisfied by a page that flags every row.

const SCHED = (over: Record<string, unknown> = {}) => ({
  kind: 'schedule',
  id: 'schedule:clock:nightly-digest',
  raw_id: 'clock:nightly-digest',
  name: 'Nightly digest',
  enabled: true,
  schedule: 'every day at 09:00',
  action: { provider: 'run-prompt', config: {} },
  last_run_ts: null,
  last_run_status: '',
  last_status: 'ok',
  run_count: 0,
  next_run_ts: null,
  author: '',
  read_only: false,
  broken: [],
  ...over,
})

const { STATE } = vi.hoisted(() => ({ STATE: { jobs: [] as unknown[] } }))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    schedules: () => Promise.resolve({ jobs: STATE.jobs }),
    hooks: () => Promise.resolve([]),
    storeTriggers: () => Promise.resolve([]),
    eventTriggers: () => Promise.resolve([]),
    actionProviders: () => Promise.resolve([]),
    autonomyLadder: () => Promise.reject(new Error('no ladder in this test')),
    triggerVariables: () => Promise.resolve({ lifecycle: [], schedule: [], event: [] }),
  },
}))

const { TriggersSection } = await import('./TriggersSection')

const mount = () =>
  render(<TriggersSection sub="" navigate={vi.fn()} navEpoch={0} query={{}} setQuery={() => {}} />)

beforeEach(() => { sessionStorage.clear() })

describe('a broken schedule flags needs-attention on the list', () => {
  it('shows "needs attention" for a schedule carrying parse errors', async () => {
    STATE.jobs = [SCHED({ broken: ['unparseable cron expr'] })]
    mount()
    await waitFor(() => expect(screen.getByText('Nightly digest')).toBeInTheDocument())
    // The flag is a SCHEDULE row's — the gate used to require kind==='store', so this row
    // (kind 'schedule') listed as if healthy despite the store keeping it only leniently.
    expect(screen.getByText(/needs attention/)).toBeInTheDocument()
  })

  it('stays quiet for a healthy schedule (vacuity leg)', async () => {
    STATE.jobs = [SCHED({ broken: [] })]
    mount()
    await waitFor(() => expect(screen.getByText('Nightly digest')).toBeInTheDocument())
    expect(screen.queryByText(/needs attention/)).toBeNull()
  })
})
