import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'

// ── A data-event trigger is a row in the one trigger store ─────────────────────────────────────
//
// Measured on main, driving a real gateway: the Data-event form wrote a SECOND store with its own
// engine, whose fires left no run record, while the `kind: "event"` rows the chat's
// `automation_create` wrote into the trigger store listed as "On an event", opened on "When it
// runs: event" and "Firing on its own" — and never fired. An event trigger lives in the one store
// now and fires through the store dispatch, so the page reads it from the store list and opens it in
// the store inspector (run history, Run now, delete), described by the pattern it listens for.

type Row = Record<string, unknown>

let storeRows: Row[] = []

function mockApi() {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      schedules: () => Promise.resolve({ jobs: [] }),
      hooks: () => Promise.resolve([]),
      storeTriggers: () => Promise.resolve(storeRows),
      actionProviders: () => Promise.resolve([]),
      autonomyLadder: () => Promise.reject(new Error('no ladder in this test')),
      triggerVariables: () => Promise.resolve({ lifecycle: [], schedule: [], event: [], app_sources: [] }),
      triggerHistory: () => Promise.resolve({
        runs: [{ run_id: 'fire-1', job_id: 'event:acme-watch', trigger: 'ok', status: 'success', started_at: 1_790_000_000, finished_at: 1_790_000_001, duration_ms: 1000 }],
        total: 1,
      }),
    },
  }))
  vi.doMock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
}

function eventRow(overrides: Row = {}): Row {
  return {
    kind: 'store', store_kind: 'event', id: 'store:event:acme-watch', raw_id: 'event:acme-watch',
    name: 'Acme watch', enabled: true,
    spec: { source: 'memory', pattern: 'MemoryKeyPattern', key_glob: 'project.acme.*' },
    action: { provider: 'notify', config: { title_template: 'Acme changed: $key' } },
    health: 'ok', state: 'active', run_count: 1, last_error: '', broken: [], warnings: [],
    ...overrides,
  }
}

function manualRow(): Row {
  return {
    kind: 'store', store_kind: 'manual', id: 'store:manual:tidy', raw_id: 'manual:tidy', name: 'Tidy downloads',
    enabled: true, spec: {}, action: { provider: 'notify', config: {} }, health: 'ok',
    state: 'active', run_count: 0, last_error: '', broken: [], warnings: [],
  }
}

async function mount(query: Record<string, string> = {}) {
  const { TriggersSection } = await import('./TriggersSection')
  render(<TriggersSection sub="" navigate={vi.fn()} navEpoch={0} query={query} setQuery={() => {}} />)
}

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  storeRows = []
  mockApi()
})

describe('an event trigger in the one store', () => {
  it('lists under Data events, described by the pattern it listens for', async () => {
    storeRows = [eventRow(), manualRow()]
    await mount()
    await waitFor(() => expect(screen.getByText('Acme watch')).toBeInTheDocument())
    expect(screen.getByText('Memory write to a key')).toBeInTheDocument()
    expect(screen.queryByText('On an event')).not.toBeInTheDocument()
  })

  it('opens in the store inspector: the pattern, its matcher, its run history and Run now', async () => {
    storeRows = [eventRow()]
    await mount({ open: 'store:event:acme-watch' })
    const when = await waitFor(() => screen.getByText('When it runs').parentElement as HTMLElement)
    expect(within(when).getByText('Memory write to a key')).toBeInTheDocument()
    expect(within(when).getByText('project.acme.*')).toBeInTheDocument()
    expect(screen.queryByText('event')).not.toBeInTheDocument()
    const what = screen.getByText('What it runs').parentElement as HTMLElement
    expect(within(what).getByText('Notify')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: /Run now/ })).toBeInTheDocument())
    expect(screen.queryByText('No runs recorded yet.')).not.toBeInTheDocument()
  })

  it('opens from the link its own notification carries (the store id)', async () => {
    // `delivery.status_url` mints `#/triggers?open=<store id>` — `event:acme-watch` here.
    storeRows = [eventRow()]
    await mount({ open: 'event:acme-watch' })
    await waitFor(() => expect(screen.getByText('When it runs')).toBeInTheDocument())
  })

  it('a manual trigger that has been run says when, not "never"', async () => {
    // Measured on main: Run now recorded the run (the inspector listed it), and the list row still
    // read "never", because a store row carried no last-run field and `run_count` — the fire meter a
    // Run button deliberately does not spend — stayed 0.
    storeRows = [{ ...manualRow(), last_run_ts: Date.now() / 1000 - 300, last_run_status: 'success' }]
    await mount()
    await waitFor(() => expect(screen.getByText('Tidy downloads')).toBeInTheDocument())
    expect(screen.getByText('5m ago')).toBeInTheDocument()
    expect(screen.queryByText('never')).not.toBeInTheDocument()
  })

  it('a manual trigger does not claim to fire on its own', async () => {
    storeRows = [manualRow()]
    await mount({ open: 'store:manual:tidy' })
    const when = await waitFor(() => screen.getByText('When it runs').parentElement as HTMLElement)
    expect(screen.queryByText('Firing on its own')).not.toBeInTheDocument()
    expect(screen.getByText('Runs only when you run it — it never fires on its own')).toBeInTheDocument()
    expect(within(when).getByText('Only when you run it')).toBeInTheDocument()
    // Its Enabled switch would change nothing — a manual trigger never fires on its own.
    expect(screen.queryByRole('switch', { name: 'Enabled' })).not.toBeInTheDocument()
  })
})
