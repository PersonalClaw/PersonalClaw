// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { resetDataStore } from '../../../lib/data'
import type { RunningList, RunningRow } from '../../../lib/api'
import type { WsMessage } from '../../../lib/useChatSocket'

// ── What a gateway restart cut off is said on the band, once, and only while it is true ──────────
//
// Home's band is where running work shows, so it is where work a restart cut off is told: the
// gateway kept no ending of it, so the page tells it from the running list it last read and the
// gateway's start (`useCutOff`), as "Interrupted at 10:42: The gateway restarted while it ran.",
// counted in the head as "1 cut off by a restart". It goes once its work is live again, after ten
// minutes, and two restarts never say it twice. A reconnect to the same gateway cuts off nothing.

const running = vi.fn<() => Promise<RunningList>>()
const status = vi.fn<() => Promise<{ start_time?: number; uptime_secs?: number }>>()

vi.mock('../../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    running: () => running(),
    status: () => status(),
    chatSessions: () => Promise.resolve([]),
    uLoops: () => Promise.resolve([]),
    approvals: () => Promise.resolve([]),
    waiting: () => Promise.resolve({ waiting: [], unreadable: [] }),
    inboxOpen: () => Promise.resolve([]),
    skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
    agentsDay: (since: string, until: string) => Promise.resolve({ since, until, agents: [], cut: 0 }),
    spawnedAgents: () => Promise.resolve([]),
    triggersWeek: () => Promise.resolve({ start: '', end: '', occurrences: [] }),
    agents: () => Promise.resolve({ agents: [], default_agent: 'PersonalClaw' }),
    usageBudget: () => Promise.resolve(null),
  },
}))

// Every reader on the band shares the socket, so a frame, a reconnect and a link change reach each.
type Reader = { onMessage: (m: WsMessage) => void; onReconnect?: () => void; onStatus?: (up: boolean) => void }
const readers = new Map<string, Reader>()
const deliver = (m: WsMessage) => { for (const r of [...readers.values()]) r.onMessage(m) }
const reconnect = () => { for (const r of [...readers.values()]) r.onReconnect?.() }
const connected = (up: boolean) => { for (const r of [...readers.values()]) r.onStatus?.(up) }
vi.mock('../../../lib/useChatSocket', () => ({
  useChatSocket: (onMessage: Reader['onMessage'], onReconnect?: Reader['onReconnect'], onStatus?: Reader['onStatus']) => {
    // One entry per call site: a re-render replaces its own, as the real hook keeps the latest.
    readers.set(`${onMessage.toString()}${onStatus ? '+status' : ''}`, { onMessage, onReconnect, onStatus })
  },
  refreshKinds: () => [],
}))

import { AgentDay } from './AgentDay'

function row(over: Partial<RunningRow> = {}): RunningRow {
  return {
    work: 'chat:c1', kind: 'chat', session: 'c1', phase: 'searching', line: 'Searching your knowledge',
    since: null, name: 'Plan the garden', agent: 'gardener', started_at: null,
    route: '#/chat/c1', blocked: false, waiting: [], project_id: '', loop_kind: '', ...over,
  }
}
const list = (live: RunningRow[] = []): RunningList => ({ running: live, finished: [] })

/** The band's rows that say a restart cut them off. */
const cutRows = () => [...document.querySelectorAll('li[aria-label*="Interrupted at"]')]
const workingRow = () => document.querySelector('li[aria-label^="gardener, working"]')

async function mount() {
  render(<AgentDay />)
  await waitFor(() => expect(workingRow()).not.toBeNull())
}

const restart = async (startedAt: number, after: RunningList) => {
  status.mockResolvedValue({ start_time: startedAt })
  running.mockResolvedValue(after)
  await act(async () => { reconnect() })
}

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  readers.clear()
  running.mockResolvedValue(list([row()]))
  status.mockResolvedValue({ start_time: 100 })
})
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('work a restart cut off', () => {
  it('is said Interrupted on its agent’s row, with when and why, opens the work, and the head counts it', async () => {
    await mount()
    await restart(200, list())
    await waitFor(() => expect(cutRows()).toHaveLength(1))
    const [cut] = cutRows()
    expect(cut.getAttribute('aria-label')).toMatch(/^gardener, interrupted: Interrupted at \d\d:\d\d: The gateway restarted while it ran\.\.?$/)
    expect(cut.querySelector('a')?.getAttribute('href')).toBe('#/chat/c1')
    expect(screen.getByText(/1 cut off by a restart/)).toBeInTheDocument()
  })

  it('🪤 VACUITY: a reconnect to the same gateway cuts off nothing', async () => {
    await mount()
    running.mockResolvedValue(list())
    await act(async () => { reconnect() })
    await waitFor(() => expect(workingRow()).toBeNull())
    expect(cutRows()).toHaveLength(0)
    expect(screen.queryByText(/cut off by a restart/)).toBeNull()
  })
})

describe('what a restart cut off is said once, and only while it is true', () => {
  it('goes once its work is live again, and two restarts never say it twice', async () => {
    await mount()
    await restart(200, list())
    await waitFor(() => expect(cutRows()).toHaveLength(1))
    // A second restart before anything else moved: still one.
    await restart(300, list())
    await waitFor(() => expect(running).toHaveBeenCalledTimes(5))
    expect(cutRows()).toHaveLength(1)
    // Its chat runs again: it is not cut off any more, and no render shows it both running and
    // cut off. Each DOM change is checked as it lands, so the cut has to go in the same commit as
    // the row that says it runs, not in one after it.
    const both: boolean[] = []
    const seen = new MutationObserver(() => { both.push(workingRow() !== null && cutRows().length > 0) })
    seen.observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true })
    running.mockResolvedValue(list([row({ phase: 'thinking', line: 'Thinking' })]))
    act(() => deliver({ type: 'running_changed', data: { changed: [{ work: 'chat:c1', session: 'c1' }] } }))
    await waitFor(() => expect(workingRow()).not.toBeNull())
    await waitFor(() => expect(cutRows()).toHaveLength(0))
    seen.disconnect()
    expect(both).not.toContain(true)
  })

  it('a restart while the rows went unread since the last one says it once', async () => {
    await mount()
    // The band's re-reads after each restart fail, so the rows it last read still hold the chat,
    // while the restart check's own read of the list lands. Both are `api.running()`, so the band's
    // is told apart by where it is made from.
    const bandRead = () => !/useCutOff/.test(new Error().stack ?? '')
    running.mockImplementation(() => (bandRead() ? Promise.reject(new Error('gateway still starting')) : Promise.resolve(list())))
    status.mockResolvedValue({ start_time: 200 })
    await act(async () => { reconnect() })
    await waitFor(() => expect(cutRows()).toHaveLength(1))
    status.mockResolvedValue({ start_time: 300 })
    await act(async () => { reconnect() })
    // The cut-off check's own read at mount, then one per reconnect.
    await waitFor(() => expect(status).toHaveBeenCalledTimes(3))
    await waitFor(() => expect(running).toHaveBeenCalledTimes(5))
    expect(cutRows()).toHaveLength(1)
  })

  it('goes after as long as the gateway keeps an ending, ten minutes', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] })
    await mount()
    await restart(200, list())
    await waitFor(() => expect(cutRows()).toHaveLength(1))
    act(() => { vi.advanceTimersByTime(9 * 60 * 1000) })
    expect(cutRows()).toHaveLength(1)
    act(() => { vi.advanceTimersByTime(2 * 60 * 1000) })
    expect(cutRows()).toHaveLength(0)
  })

  it('a desktop awake for the drop tells what a restart cut off, however long the gateway was down', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] })
    status.mockResolvedValue({ start_time: Date.now() / 1000 - 3600, uptime_secs: 3600 })
    await mount()
    // The gateway goes, and is started again five minutes later.
    act(() => { connected(false) })
    act(() => { vi.advanceTimersByTime(5 * 60 * 1000) })
    await restart(Date.now() / 1000 - 10, list())
    await waitFor(() => expect(cutRows()).toHaveLength(1))
  })

  it('a start time it could not read first tells no restart, and the next one is told', async () => {
    status.mockRejectedValue(new Error('not yet'))
    await mount()
    await restart(200, list())
    // The cut-off check's read at mount, and the reconnect's.
    await waitFor(() => expect(status).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(workingRow()).toBeNull())
    expect(cutRows()).toHaveLength(0)
    running.mockResolvedValue(list([row()]))
    act(() => deliver({ type: 'running_changed', data: { changed: [] } }))
    await waitFor(() => expect(workingRow()).not.toBeNull())
    await restart(300, list())
    await waitFor(() => expect(cutRows()).toHaveLength(1))
  })

  it('a read that lands while the socket is down cannot hide what the restart cut off', async () => {
    await mount()
    act(() => connected(false))
    // The band reads again while the socket is down (an answer pressed, say), from the gateway
    // that has already restarted.
    running.mockResolvedValue(list())
    act(() => deliver({ type: 'running_changed', data: { changed: [] } }))
    await waitFor(() => expect(workingRow()).toBeNull())
    act(() => connected(true))
    await restart(200, list())
    await waitFor(() => expect(cutRows()).toHaveLength(1))
  })
})
