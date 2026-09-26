import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import type { WsMessage } from '../../lib/useChatSocket'

// ── The Triggers page follows the trigger store ─────────────────────────────────────────────
//
// Measured on day 8: the status strip said "6 triggers" over a Triggers page that listed 5. The
// sixth was a loop's auto-nudge, written to the store after the page had loaded, and only the
// schedules on this page ever re-read, so the page kept showing the store as it was when it opened
// while the strip counted it as it is. The gateway now says `crons` whenever the trigger store
// changes (a store write by anyone, found by the clock loop's tick), and every source re-reads.
//
// And the two store kinds the chat's `automation_create` makes that no page listed (`event`,
// `manual`) are listed and labelled for what they are.

type Row = Record<string, unknown>

const frames: Array<(m: WsMessage) => void> = []
let storeRows: Row[] = []
const storeTriggers = vi.fn(() => Promise.resolve(storeRows))

function mockApi() {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      schedules: () => Promise.resolve({ jobs: [] }),
      hooks: () => Promise.resolve([]),
      storeTriggers: () => storeTriggers(),
      eventTriggers: () => Promise.resolve([]),
      actionProviders: () => Promise.resolve([]),
      autonomyLadder: () => Promise.reject(new Error('no ladder in this test')),
      triggerVariables: () => Promise.resolve({ lifecycle: [], schedule: [], event: [] }),
    },
  }))
  vi.doMock('../../lib/useChatSocket', () => ({
    useChatSocket: (onMessage: (m: WsMessage) => void) => { frames.push(onMessage) },
  }))
}

function storeRow(kind: string, name: string): Row {
  return {
    kind: 'store', store_kind: kind, id: `store:${kind}:${name}`, raw_id: `${kind}:${name}`, name,
    enabled: true, spec: {}, action: { provider: 'notify', config: {} }, health: 'healthy',
    state: 'active', run_count: 0, last_error: '', broken: [], warnings: [],
  }
}

async function mount() {
  const { TriggersSection } = await import('./TriggersSection')
  render(<TriggersSection sub="" navigate={vi.fn()} navEpoch={0} query={{}} setQuery={() => {}} />)
}

function say(m: WsMessage) {
  act(() => { for (const onMessage of [...frames]) onMessage(m) })
}

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  frames.length = 0
  storeRows = []
  storeTriggers.mockClear()
  mockApi()
})

describe('the Triggers page follows the trigger store', () => {
  it('lists a trigger written elsewhere once the gateway says the store changed', async () => {
    storeRows = [storeRow('file', 'Summarize notes')]
    await mount()
    await waitFor(() => expect(screen.getByText('Summarize notes')).toBeInTheDocument())

    // A loop starts and writes its auto-nudge; the page has already loaded.
    storeRows = [...storeRows, storeRow('idle', 'Auto-nudge — loop-1')]
    say({ type: 'refresh', data: { kinds: ['crons'] } } as WsMessage)
    await waitFor(() => expect(screen.getByText('Auto-nudge — loop-1')).toBeInTheDocument())
  })

  it('does not re-read for a refresh about something else', async () => {
    await mount()
    await waitFor(() => expect(storeTriggers).toHaveBeenCalled())
    const reads = storeTriggers.mock.calls.length
    say({ type: 'refresh', data: { kinds: ['history'] } } as WsMessage)
    say({ type: 'sessions', data: {} } as WsMessage)
    expect(storeTriggers.mock.calls.length).toBe(reads)
  })

  it('labels the event and manual automations the chat makes for what they are', async () => {
    storeRows = [storeRow('event', 'When I end a session'), storeRow('manual', 'Tidy downloads')]
    await mount()
    await waitFor(() => expect(screen.getByText('Tidy downloads')).toBeInTheDocument())
    expect(screen.getByText('On an event')).toBeInTheDocument()
    expect(screen.getByText('When you run it')).toBeInTheDocument()
  })
})
