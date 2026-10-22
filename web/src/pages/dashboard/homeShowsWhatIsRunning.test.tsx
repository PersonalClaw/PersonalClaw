// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDataStore } from '../../lib/data'
import type { RunningList, RunningRow } from '../../lib/api'
import type { WsMessage } from '../../lib/useChatSocket'
import { clockTime } from '../../lib/epoch'

// ── Home's Running Now is the running list (#3889, #3813) ───────────────────────────────────────
//
// Running Now reads `GET /api/running`, the one list of live work every reader shares, and nothing
// of its own: each row shows the work's name, its exact phase as text, what it is doing, the agent
// doing it, its last update and a route that opens it. A blocked row says so, apart from working,
// and a row that waits on you says how much does. The just-finished tail is Recent and completed's.
//
// Home renders every state the list can be in: a large list, an empty one, the socket
// reconnecting, work a gateway restart interrupted, failed work and the just-finished tail. Each is
// fed here, and each assertion names what it fed, so an empty group cannot pass for any of them.

const running = vi.fn<() => Promise<RunningList>>()
const inboxOpen = vi.fn()
const status = vi.fn<() => Promise<{ start_time?: number; uptime_secs?: number }>>()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    waiting: () => Promise.resolve({ waiting: [], unreadable: [] }),
    inboxOpen: () => inboxOpen(),
    skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
    chatSessions: () => Promise.resolve([]),
    running: () => running(),
    status: () => status(),
  },
}))

// Every reader on the page shares the socket, so a frame, a reconnect and a link change reach each.
const readers = new Map<string, { onMessage: (m: WsMessage) => void; onReconnect: () => void; onStatus?: (up: boolean) => void }>()
const deliver = (m: WsMessage) => { for (const r of [...readers.values()]) r.onMessage(m) }
const reconnect = () => { for (const r of [...readers.values()]) r.onReconnect() }
const connected = (up: boolean) => { for (const r of [...readers.values()]) r.onStatus?.(up) }
vi.mock('../../lib/useChatSocket', () => ({
  useChatSocket: (onMessage: (m: WsMessage) => void, onReconnect: () => void, onStatus?: (up: boolean) => void) => {
    // One entry per call site: a re-render replaces its own, as the real hook keeps the latest.
    readers.set(onStatus ? 'running' : 'needs', { onMessage, onReconnect, onStatus })
  },
  refreshKinds: () => [],
}))
vi.mock('../../app/identity', () => ({ useIdentity: () => ({ name: '' }), firstNameOf: (n: string) => n }))
vi.mock('./PinnedTiles', () => ({ PinnedTiles: () => null }))
vi.mock('../../ui/surfaces/SurfaceOverlay', () => ({ SurfaceOverlay: () => null }))
vi.mock('./widgets/Suggestions', () => ({ Suggestions: () => null }))

import { HomeHarness } from '../../test/homeHarness'

const UPDATED = '2026-10-21T10:04:00Z'

function row(over: Partial<RunningRow> = {}): RunningRow {
  return {
    work: 'chat:c1', kind: 'chat', session: 'c1', phase: 'searching', line: 'Searching your knowledge',
    since: UPDATED, name: 'Plan the garden', agent: 'gardener', started_at: '2026-10-21T10:00:00Z',
    route: '#/chat/c1', blocked: false, waiting: [], ...over,
  }
}

const list = (live: RunningRow[] = [], finished: RunningRow[] = []): RunningList => ({ running: live, finished })

const working = () => screen.getByRole('region', { name: 'Running Now' })
const recent = () => screen.getByRole('region', { name: 'Recent and completed' })

async function mount() {
  render(<HomeHarness />)
  await waitFor(() => expect(running).toHaveBeenCalled())
}

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  running.mockResolvedValue(list())
  inboxOpen.mockResolvedValue([])
  status.mockResolvedValue({ start_time: 100 })
})

describe('each row says what the work is doing', () => {
  it('its name, its phase as text, its line, its agent, its last update and a route into it', async () => {
    running.mockResolvedValue(list([row()]))
    await mount()
    const lane = working()
    const card = await within(lane).findByRole('listitem', { name: /Plan the garden/ })
    expect(within(card).getByText('Plan the garden')).toBeTruthy()
    expect(within(card).getByText('Searching')).toBeTruthy()
    expect(within(card).getByText('Searching your knowledge')).toBeTruthy()
    expect(within(card).getByText(/gardener/)).toBeTruthy()
    expect(within(card).getByText(`Updated ${clockTime(UPDATED)}`)).toBeTruthy()
    expect(within(card).getByRole('link', { name: 'Open Plan the garden — chat' }).getAttribute('href')).toBe('#/chat/c1')
    expect(within(lane).getByText('1', { selector: '[data-lane-count]' })).toBeTruthy()
  })

  it('🔴 a queued helper reads blocked, with its reason, and is not counted working', async () => {
    running.mockResolvedValue(list([
      row(),
      row({ work: 'subagent:h4', kind: 'subagent', name: 'grep the tree', phase: 'queued', line: 'Waiting to start', blocked: true }),
    ]))
    await mount()
    const card = await within(working()).findByRole('listitem', { name: /grep the tree/ })
    expect(within(card).getByText('Blocked')).toBeTruthy()
    expect(within(card).getByText('Waiting to start')).toBeTruthy()
    expect(within(working()).getByText('1 blocked')).toBeTruthy()
  })

  it('a held run and a held loop say what holds them and what moves them again, and never read as running', async () => {
    // The running list's own words for work incident mode holds (`work_phase.HELD`).
    const held = 'Held until incident mode is off'
    running.mockResolvedValue(list([
      row({ work: 'workflow:r1', kind: 'workflow', session: '', name: 'nightly-digest', phase: 'queued', line: held, blocked: true }),
      row({ work: 'loop:l1', kind: 'loop', session: '', name: 'Fix the README', phase: 'queued', line: held, blocked: true }),
    ]))
    await mount()
    for (const name of [/nightly-digest/, /Fix the README/]) {
      const card = await within(working()).findByRole('listitem', { name })
      expect(within(card).getByText(held)).toBeTruthy()
      expect(within(card).getByText('Blocked')).toBeTruthy()
      expect(card.textContent ?? '', 'a held row reads as running').not.toMatch(/\b(Running|Thinking|Working)\b/)
    }
  })

  it('a row that waits on you says so', async () => {
    running.mockResolvedValue(list([row({ phase: 'approval', line: 'Needs your OK: shell', waiting: ['ask-1'] })]))
    await mount()
    const card = await within(working()).findByRole('listitem', { name: /Plan the garden/ })
    expect(within(card).getByText('1 waiting on you')).toBeTruthy()
  })

  it('a row the list moved is read again on the ring, never from the frame', async () => {
    running.mockResolvedValue(list([row()]))
    await mount()
    await within(working()).findByText('Searching your knowledge')
    running.mockResolvedValue(list([row({ phase: 'streaming', line: 'Writing' })]))
    act(() => deliver({ type: 'running_changed', data: { changed: [{ work: 'chat:c1', session: 'c1' }] } }))
    expect(await within(working()).findByText('Writing')).toBeTruthy()
  })
})

describe('every state of the list', () => {
  it('empty: it says nothing runs', async () => {
    await mount()
    expect(await within(working()).findByText('Nothing is running right now.')).toBeTruthy()
  })

  it('large: the count is all of it, the first rows show, and the rest are one press away', async () => {
    const many = Array.from({ length: 60 }, (_, i) => row({ work: `subagent:h${i}`, kind: 'subagent', name: `Helper ${i}` }))
    running.mockResolvedValue(list(many))
    await mount()
    await within(working()).findByText('Helper 0')
    expect(within(working()).getByText('60', { selector: '[data-lane-count]' })).toBeTruthy()
    expect(within(working()).queryByText('Helper 59')).toBeNull()
    const more = within(working()).getByRole('button', { name: 'Show all 60' })
    await userEvent.click(more)
    expect(within(working()).getByText('Helper 59')).toBeTruthy()
    // The button stays, so focus stays on it, and takes the lane back to its first rows.
    expect(document.activeElement).toBe(more)
    expect(more.textContent).toBe('Show the first 25')
  })

  it('reconnecting: it says the rows may be out of date until the socket is back', async () => {
    running.mockResolvedValue(list([row()]))
    await mount()
    await within(working()).findByText('Plan the garden')
    act(() => connected(false))
    expect(screen.getByRole('status').textContent).toMatch(/Reconnecting/)
    expect(within(working()).getByText('Plan the garden')).toBeTruthy()
    act(() => connected(true))
    expect(screen.queryByText(/Reconnecting/)).toBeNull()
  })

  it('failed and just finished: each ending says how it ended, when, and opens the work', async () => {
    const ended = '2026-10-21T10:06:00Z'
    running.mockResolvedValue(list([], [
      row({ work: 'workflow:r9', kind: 'workflow', session: '', name: 'nightly', phase: 'done', line: 'Done in 12 seconds', since: ended, route: '#/workflows/runs/r9' }),
      row({ work: 'subagent:h2', kind: 'subagent', name: 'fetch the page', phase: 'error', line: 'Stopped: the site refused', since: ended }),
    ]))
    await mount()
    const tail = await within(recent()).findByRole('list', { name: 'Just finished' })
    const done = within(tail).getByRole('listitem', { name: /nightly/ })
    expect(within(done).getByText('Done')).toBeTruthy()
    expect(within(done).getByText('Done in 12 seconds')).toBeTruthy()
    expect(within(done).getByText(`Ended ${clockTime(ended)}`)).toBeTruthy()
    expect(within(done).getByRole('link', { name: 'Open nightly — Done' }).getAttribute('href')).toBe('#/workflows/runs/r9')
    const failed = within(tail).getByRole('listitem', { name: /fetch the page/ })
    expect(within(failed).getByText('Stopped')).toBeTruthy()
    expect(within(failed).getByText('Stopped: the site refused')).toBeTruthy()
    // Nothing runs, and Running Now still says so above Recent and completed's tail.
    expect(within(working()).getByText('Nothing is running right now.')).toBeTruthy()
  })

  it('interrupted: work a restart cut off stays, said as interrupted, and opens the work', async () => {
    running.mockResolvedValue(list([row()]))
    await mount()
    await within(working()).findByText('Plan the garden')
    // The gateway restarted: the turn is gone from the list, and no ending of it was kept.
    status.mockResolvedValue({ start_time: 200 })
    running.mockResolvedValue(list())
    await act(async () => { reconnect() })
    const tail = await within(recent()).findByRole('list', { name: 'Just finished' })
    const cut = within(tail).getByRole('listitem', { name: /Plan the garden/ })
    expect(within(cut).getByText('Interrupted')).toBeTruthy()
    expect(within(cut).getByText('The gateway restarted while it ran.')).toBeTruthy()
    expect(within(cut).getByRole('link', { name: 'Open Plan the garden — Interrupted' }).getAttribute('href')).toBe('#/chat/c1')
  })

  it('🪤 VACUITY: a reconnect to the same gateway interrupts nothing', async () => {
    running.mockResolvedValue(list([row()]))
    await mount()
    await within(working()).findByText('Plan the garden')
    running.mockResolvedValue(list())
    await act(async () => { reconnect() })
    await waitFor(() => expect(within(working()).getByText('Nothing is running right now.')).toBeTruthy())
    expect(within(recent()).queryByText('Interrupted')).toBeNull()
  })
})

describe('what a restart cut off is said once, and only while it is true', () => {
  afterEach(() => { vi.useRealTimers() })

  const restart = async (startedAt: number, after: RunningList) => {
    status.mockResolvedValue({ start_time: startedAt })
    running.mockResolvedValue(after)
    await act(async () => { reconnect() })
  }
  const cutRows = () => within(recent()).queryAllByText('Interrupted')

  it('goes once its work is live again, and two restarts never say it twice', async () => {
    running.mockResolvedValue(list([row()]))
    await mount()
    await within(working()).findByText('Plan the garden')
    await restart(200, list())
    await waitFor(() => expect(cutRows()).toHaveLength(1))
    // A second restart before anything else moved: still one.
    await restart(300, list())
    await waitFor(() => expect(running).toHaveBeenCalledTimes(5))
    expect(cutRows()).toHaveLength(1)
    // Its chat runs again: it is not cut off any more, and no render shows it both running and
    // cut off. Each DOM change is checked as it lands, so the cut row has to go in the same commit
    // as the row that says it runs, not in one after it.
    const both: boolean[] = []
    const seen = new MutationObserver(() => {
      both.push(within(working()).queryByText('Thinking') !== null && cutRows().length > 0)
    })
    seen.observe(document.body, { childList: true, subtree: true, characterData: true })
    running.mockResolvedValue(list([row({ phase: 'thinking', line: 'Thinking' })]))
    act(() => deliver({ type: 'running_changed', data: { changed: [{ work: 'chat:c1', session: 'c1' }] } }))
    await within(working()).findByText('Thinking')
    await waitFor(() => expect(cutRows()).toHaveLength(0))
    seen.disconnect()
    expect(both).not.toContain(true)
  })

  it('a restart while the rows went unread since the last one says it once', async () => {
    running.mockResolvedValue(list([row()]))
    await mount()
    await within(working()).findByText('Plan the garden')
    // The page's re-reads after each restart fail, so the rows it last read still hold the chat,
    // while the restart check's own read of the list lands. Both are `api.running()`, so the page's
    // is told apart by where it is made from.
    const pageRead = () => !/useCutOff/.test(new Error().stack ?? '')
    running.mockImplementation(() => (pageRead() ? Promise.reject(new Error('gateway still starting')) : Promise.resolve(list())))
    status.mockResolvedValue({ start_time: 200 })
    await act(async () => { reconnect() })
    await waitFor(() => expect(cutRows()).toHaveLength(1))
    status.mockResolvedValue({ start_time: 300 })
    await act(async () => { reconnect() })
    // Home's own read (its YOLO line) is one of them: mount, then one per reconnect.
    await waitFor(() => expect(status).toHaveBeenCalledTimes(4))
    await waitFor(() => expect(running).toHaveBeenCalledTimes(5))
    expect(cutRows()).toHaveLength(1)
  })

  it('goes after as long as the gateway keeps an ending, ten minutes', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] })
    running.mockResolvedValue(list([row()]))
    await mount()
    await within(working()).findByText('Plan the garden')
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
    running.mockResolvedValue(list([row()]))
    await mount()
    await within(working()).findByText('Plan the garden')
    // The gateway goes, and is started again five minutes later.
    act(() => { connected(false) })
    act(() => { vi.advanceTimersByTime(5 * 60 * 1000) })
    await restart(Date.now() / 1000 - 10, list())
    await waitFor(() => expect(cutRows()).toHaveLength(1))
  })

  it('a start time it could not read first tells no restart, and the next one is told', async () => {
    status.mockRejectedValue(new Error('not yet'))
    running.mockResolvedValue(list([row()]))
    await mount()
    await within(working()).findByText('Plan the garden')
    await restart(200, list())
    // Home's own read (its YOLO line), the cut-off check's at mount, and the reconnect's.
    await waitFor(() => expect(status).toHaveBeenCalledTimes(3))
    await within(working()).findByText('Nothing is running right now.')
    expect(cutRows()).toHaveLength(0)
    running.mockResolvedValue(list([row()]))
    act(() => deliver({ type: 'running_changed', data: { changed: [] } }))
    await within(working()).findByText('Plan the garden')
    await restart(300, list())
    await waitFor(() => expect(cutRows()).toHaveLength(1))
  })

  it('a read that lands while the socket is down cannot hide what the restart cut off', async () => {
    running.mockResolvedValue(list([row()]))
    await mount()
    await within(working()).findByText('Plan the garden')
    act(() => connected(false))
    // The page reads again while the socket is down (an answer pressed, say), from the gateway
    // that has already restarted.
    running.mockResolvedValue(list())
    act(() => deliver({ type: 'running_changed', data: { changed: [] } }))
    await within(working()).findByText('Nothing is running right now.')
    act(() => connected(true))
    await restart(200, list())
    await waitFor(() => expect(cutRows()).toHaveLength(1))
  })
})

describe('a list that cannot be read', () => {
  it('says so in the lane, never that nothing runs', async () => {
    running.mockRejectedValue(new Error('gateway down'))
    render(<HomeHarness />)
    expect(await within(working()).findByText('Could not read what is running.')).toBeTruthy()
    expect(within(working()).queryByText('Nothing is running right now.')).toBeNull()
  })
})
