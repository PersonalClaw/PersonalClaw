import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { invalidateKeys } from '../../lib/data'
import { clockTime } from '../../lib/epoch'
import type { Loop, RunningList, RunningRow } from '../../lib/api'
import type { WsMessage } from '../../lib/useChatSocket'

/** The test environment's jsdom, which Vitest puts on the global. */
declare const jsdom: { reconfigure(settings: { url: string }): void }

// ── The phone's Running section is the running list (#3889) ─────────────────────────────────────
//
// `#/companion`'s Running section read the loops and listed the steerable ones, and nothing else:
// a chat's turn, a helper, a workflow run, an automation's fire and a webhook's turn never showed,
// and a loop's row said its status and its cycle, not what it was doing. It reads `GET /api/running`
// now, the list Mission Control, Agent World and the menu bar read, and each row says the work's
// name, its exact phase, what it is doing and when it last changed, and opens the work. A loop's
// row keeps its steering, and a paused loop, which runs nothing and so is on no list of live work,
// is still offered to resume. The section renders every state of the list, each fed here.

const running = vi.fn<() => Promise<RunningList>>()
const status = vi.fn<() => Promise<{ start_time?: number; uptime_secs?: number }>>()
const uLoops = vi.fn<() => Promise<Loop[]>>()
const uLoopAction = vi.fn()

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      running: () => running(),
      status: () => status(),
      uLoops: () => uLoops(),
      uLoopAction: (id: string, action: string) => uLoopAction(id, action),
      uLoopNudge: () => Promise.resolve({}),
    },
  }
})

/** Every handler each subscriber registered on the socket: the lanes and the restart watch. */
const subs: Array<{ onMessage: (m: WsMessage) => void; onReconnect: (() => void) | null; onStatus?: (up: boolean) => void }> = []
vi.mock('../../lib/useChatSocket', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatSocket: (onMessage: (m: WsMessage) => void, onReconnect: (() => void) | null, onStatus?: (up: boolean) => void) => {
    subs.push({ onMessage, onReconnect, onStatus })
  },
}))

import { RunningSection } from './CompanionSections'

const UPDATED = '2026-10-21T10:04:00Z'

function row(over: Partial<RunningRow> = {}): RunningRow {
  return {
    work: 'chat:c1', kind: 'chat', session: 'c1', phase: 'searching', line: 'Looking through your notes',
    since: UPDATED, name: 'Plan the garden', agent: 'gardener', started_at: '2026-10-21T10:00:00Z',
    route: '#/chat/c1', blocked: false, waiting: [], ...over,
  }
}
const list = (live: RunningRow[] = [], finished: RunningRow[] = []): RunningList => ({ running: live, finished })
const loop = (over: Partial<Loop> = {}): Loop => ({
  id: '0a1b2c3d', kind: 'goal', name: 'Nightly sweep', task: 'Sweep the inbox', execution: 'solo', agent: 'default',
  model: 'm', attended: false, max_cycles: 10, idle_secs: 60, success_criteria: null, status: 'running',
  total_cycles: 3, error_message: null, created_at: 1, started_at: 2, completed_at: null, kind_config: {}, ...over,
} as Loop)

const section = () => screen.getByRole('region', { name: /^Running/ })
const card = (name: RegExp) => within(section()).findByRole('listitem', { name })
const say = (m: WsMessage) => act(() => { for (const s of [...subs]) s.onMessage(m) })
const reconnect = async () => { await act(async () => { for (const s of [...subs]) s.onReconnect?.() }) }
const connected = (up: boolean) => act(() => { for (const s of [...subs]) s.onStatus?.(up) })

async function mount() {
  render(<RunningSection />)
  await waitFor(() => expect(running).toHaveBeenCalled())
}

beforeEach(() => {
  subs.length = 0
  for (const fn of [running, status, uLoops, uLoopAction]) fn.mockReset()
  for (const k of ['running-companion', 'loops-companion']) invalidateKeys(k)
  running.mockResolvedValue(list())
  status.mockResolvedValue({ start_time: 100 })
  uLoops.mockResolvedValue([])
})
afterEach(cleanup)

describe('each row says what the work is doing, and opens it', () => {
  it('its name, its exact phase, its line, its agent, its last update and a route into it', async () => {
    running.mockResolvedValue(list([row()]))
    await mount()
    const c = await card(/Plan the garden/)
    expect(within(c).getByText('Plan the garden')).toBeTruthy()
    expect(within(c).getByText('Searching: Looking through your notes')).toBeTruthy()
    expect(within(c).getByText(`gardener · Updated ${clockTime(UPDATED)}`)).toBeTruthy()
    expect(within(c).getByRole('link', { name: 'Open Plan the garden — chat' }).getAttribute('href')).toBe('#/chat/c1')
  })

  it('every kind of work is listed, not only loops', async () => {
    running.mockResolvedValue(list([
      row(),
      row({ work: 'subagent:h1', kind: 'subagent', name: 'grep the tree', phase: 'tool', line: 'Running shell' }),
      row({ work: 'workflow:r1', kind: 'workflow', session: '', name: 'nightly', phase: 'thinking', line: 'Running', route: '#/workflows/runs/r1' }),
    ]))
    await mount()
    for (const name of [/Plan the garden/, /grep the tree/, /nightly/]) expect(await card(name)).toBeTruthy()
    expect(within(section()).getByRole('heading').textContent).toBe('Running (3)')
  })

  it('🔴 a queued helper reads blocked, with its reason', async () => {
    running.mockResolvedValue(list([row({ work: 'subagent:h4', kind: 'subagent', name: 'grep the tree', phase: 'queued', line: 'Waiting to start', blocked: true })]))
    await mount()
    expect(within(await card(/grep the tree/)).getByText('Blocked, Waiting to start')).toBeTruthy()
  })

  it('a row that waits on you says so', async () => {
    running.mockResolvedValue(list([row({ phase: 'approval', line: 'Needs your OK: shell', waiting: ['ask-1'] })]))
    await mount()
    expect(within(await card(/Plan the garden/)).getByText(/1 waiting on you/)).toBeTruthy()
  })

  it("a loop's row keeps its steering, and a paused loop on no list of live work is still offered", async () => {
    running.mockResolvedValue(list([row({ work: 'loop:0a1b2c3d', kind: 'loop', session: '', name: 'Nightly sweep', phase: 'thinking', line: 'Running', route: '#/loops/0a1b2c3d' })]))
    uLoops.mockResolvedValue([loop(), loop({ id: 'p1', name: 'Weekly digest', status: 'paused' })])
    uLoopAction.mockResolvedValue({})
    await mount()
    const live = await card(/Nightly sweep/)
    expect(within(live).getByText('Thinking: Running')).toBeTruthy()
    await userEvent.click(within(live).getByRole('button', { name: 'Pause Nightly sweep' }))
    expect(uLoopAction).toHaveBeenCalledWith('0a1b2c3d', 'pause')
    const paused = await card(/Weekly digest/)
    expect(within(paused).getByRole('button', { name: 'Resume Weekly digest' })).toBeTruthy()
  })

  it("a run-backed loop's row is joined to its loop by its run's id", async () => {
    running.mockResolvedValue(list([row({ work: 'workflow:r7', kind: 'loop', session: '', name: 'Nightly sweep', phase: 'thinking', line: 'Running', route: '#/loops/r7' })]))
    uLoops.mockResolvedValue([loop({ id: 'r7' })])
    await mount()
    const live = await card(/Nightly sweep/)
    expect(within(live).getByRole('button', { name: 'Pause Nightly sweep' })).toBeTruthy()
    // Joined, so the loop is not listed a second time under the rows.
    expect(within(section()).getAllByRole('listitem', { name: /Nightly sweep/ })).toHaveLength(1)
  })

  it('the heading counts what runs, and a long list never pushes a paused loop out of sight', async () => {
    running.mockResolvedValue(list(Array.from({ length: 9 }, (_, i) => row({ work: `subagent:h${i}`, kind: 'subagent', name: `Helper ${i}` }))))
    uLoops.mockResolvedValue([loop({ id: 'p1', name: 'Weekly digest', status: 'paused' })])
    await mount()
    const others = await within(section()).findByRole('list', { name: 'Other loops you can steer' })
    expect(within(others).getByRole('button', { name: 'Resume Weekly digest' })).toBeTruthy()
    expect(within(section()).getAllByRole('heading')[0].textContent).toBe('Running (9)')
  })

  it('a running list that cannot be read still offers every steerable loop under its error', async () => {
    running.mockRejectedValue(new Error('gateway down'))
    uLoops.mockResolvedValue([loop(), loop({ id: 'p1', name: 'Weekly digest', status: 'paused' })])
    render(<RunningSection />)
    expect(await within(section()).findByRole('alert')).toBeTruthy()
    const loops = await within(section()).findByRole('list', { name: 'Loops you can steer' })
    expect(within(loops).getByRole('button', { name: 'Pause Nightly sweep' })).toBeTruthy()
    expect(within(loops).getByRole('button', { name: 'Resume Weekly digest' })).toBeTruthy()
  })

  it('a list of loops that cannot be read costs a loop its controls, never the rows', async () => {
    running.mockResolvedValue(list([row(), row({ work: 'loop:0a1b2c3d', kind: 'loop', session: '', name: 'Nightly sweep', phase: 'thinking', line: 'Running', route: '#/loops/0a1b2c3d' })]))
    uLoops.mockRejectedValue(new Error('loops unreachable'))
    await mount()
    const live = await card(/Nightly sweep/)
    expect(await card(/Plan the garden/)).toBeTruthy()
    expect(within(live).queryByRole('button', { name: 'Pause Nightly sweep' })).toBeNull()
    expect((await within(section()).findByRole('alert')).textContent).toMatch(/Couldn't load your loops.*loops unreachable/)
  })

  it('work no page opens has no link, rather than one to somewhere its name does not say', async () => {
    running.mockResolvedValue(list([row({ work: 'webhook:w1', kind: 'webhook', session: 'webhook:w1', name: 'Deploy hook', route: '' })]))
    await mount()
    const c = await card(/Deploy hook/)
    expect(within(c).queryByRole('link')).toBeNull()
    expect(within(c).queryByText('Open')).toBeNull()
  })

  it('a row the list moved is read again on its ring', async () => {
    running.mockResolvedValue(list([row()]))
    await mount()
    await card(/Plan the garden/)
    running.mockResolvedValue(list([row({ phase: 'streaming', line: 'Writing' })]))
    say({ type: 'running_changed', data: { changed: [{ work: 'chat:c1', session: 'c1' }] } })
    // The row's phase, and its buddy line in its new phase's template (#3898).
    expect((await within(section()).findAllByText('Writing')).length).toBeGreaterThan(0)
    expect(await within(section()).findByText('Plan the garden: writing')).toBeTruthy()
  })
})

describe('every state of the list', () => {
  it('empty: it says nothing runs', async () => {
    await mount()
    expect(await within(section()).findByText('Nothing running')).toBeTruthy()
  })

  it('large: it says how much it is not showing', async () => {
    running.mockResolvedValue(list(Array.from({ length: 9 }, (_, i) => row({ work: `subagent:h${i}`, kind: 'subagent', name: `Helper ${i}` }))))
    await mount()
    expect(await within(section()).findByText(/Showing 6 of 9/)).toBeTruthy()
    expect(within(section()).queryByText('Helper 8')).toBeNull()
  })

  it('a list that cannot be read is said so, never as nothing running', async () => {
    running.mockRejectedValue(new Error('gateway down'))
    render(<RunningSection />)
    expect(await within(section()).findByRole('alert')).toBeTruthy()
    expect(within(section()).queryByText('Nothing running')).toBeNull()
  })

  it('reconnecting: it says the rows may be out of date until the socket is back', async () => {
    running.mockResolvedValue(list([row()]))
    await mount()
    await card(/Plan the garden/)
    // The region is there before the drop, so the words that arrive in it are announced.
    expect(within(section()).getByRole('status').textContent).toBe('')
    connected(false)
    expect(within(section()).getByRole('status').textContent).toMatch(/Reconnecting/)
    connected(true)
    expect(within(section()).queryByText(/Reconnecting/)).toBeNull()
  })

  it('failed and just finished: each ending says how it ended, when, and opens the work', async () => {
    const ended = '2026-10-21T10:06:00Z'
    running.mockResolvedValue(list([], [
      row({ work: 'workflow:r9', kind: 'workflow', session: '', name: 'nightly', phase: 'done', line: 'Done in 12 seconds', since: ended, route: '#/workflows/runs/r9' }),
      row({ work: 'subagent:h2', kind: 'subagent', name: 'fetch the page', phase: 'error', line: 'Stopped: the site refused', since: ended }),
    ]))
    await mount()
    const tail = await within(section()).findByRole('list', { name: 'Just finished' })
    const done = within(tail).getByRole('listitem', { name: /nightly/ })
    // A line that starts with its phase's word says the word once.
    expect(within(done).getByText('Done in 12 seconds')).toBeTruthy()
    expect(within(done).getByText(`Ended ${clockTime(ended)}`)).toBeTruthy()
    expect(within(done).getByRole('link', { name: 'Open nightly — Done' }).getAttribute('href')).toBe('#/workflows/runs/r9')
    expect(within(within(tail).getByRole('listitem', { name: /fetch the page/ })).getByText('Stopped: the site refused')).toBeTruthy()
  })

  it('a long tail says how much it is not showing', async () => {
    running.mockResolvedValue(list([], Array.from({ length: 8 }, (_, i) => row({ work: `subagent:e${i}`, kind: 'subagent', name: `Ended ${i}`, phase: 'done', line: 'Done' }))))
    await mount()
    const tail = await within(section()).findByRole('list', { name: 'Just finished' })
    expect(within(tail).getAllByRole('listitem')).toHaveLength(6)
    expect(within(section()).getByText(/Showing 6 of 8/)).toBeTruthy()
  })

  it('interrupted: work a restart cut off stays, said as interrupted, and opens the work', async () => {
    running.mockResolvedValue(list([row()]))
    await mount()
    await card(/Plan the garden/)
    status.mockResolvedValue({ start_time: 200 })
    running.mockResolvedValue(list())
    await reconnect()
    const tail = await within(section()).findByRole('list', { name: 'Just finished' })
    const cut = within(tail).getByRole('listitem', { name: /Plan the garden/ })
    expect(within(cut).getByText('Interrupted: The gateway restarted while it ran.')).toBeTruthy()
    expect(within(cut).getByRole('link', { name: 'Open Plan the garden — Interrupted' }).getAttribute('href')).toBe('#/chat/c1')
  })

  it('a cut-off row the gateway has an ending for is said once, by its ending', async () => {
    running.mockResolvedValue(list([row()]))
    await mount()
    await card(/Plan the garden/)
    status.mockResolvedValue({ start_time: 200 })
    running.mockResolvedValue(list())
    await reconnect()
    await within(section()).findByText('Interrupted: The gateway restarted while it ran.')
    // Not even for the one render before the restart watch reads the ending: every paint the
    // section makes is looked at.
    let twice = false
    const paints = new MutationObserver(() => {
      const t = section().textContent ?? ''
      if (t.includes('Stopped: the model refused') && t.includes('Interrupted')) twice = true
    })
    paints.observe(section(), { subtree: true, childList: true, characterData: true })
    running.mockResolvedValue(list([], [row({ phase: 'error', line: 'Stopped: the model refused' })]))
    say({ type: 'running_changed', data: { changed: [{ work: 'chat:c1', session: 'c1' }] } })
    expect(await within(section()).findByText('Stopped: the model refused')).toBeTruthy()
    paints.disconnect()
    expect(twice).toBe(false)
    expect(within(section()).queryByText(/Interrupted/)).toBeNull()
  })
})

describe('a phone that slept through a restart says nothing it cannot know', () => {
  afterEach(() => { vi.useRealTimers() })

  const restartAt = async (startedMs: number) => {
    status.mockResolvedValue({ start_time: startedMs / 1000 })
    running.mockResolvedValue(list())
    connected(false)
    await reconnect()
    connected(true)
    // Every handler the section's renders registered is told, as the socket tells each; let the
    // start-time reads and what they start land.
    await waitFor(() => expect(status.mock.calls.length).toBeGreaterThan(1))
    for (let i = 0; i < 5; i++) await act(async () => {})
  }

  it('awake: a restart right after its rows were current says what it cut off', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] })
    status.mockResolvedValue({ start_time: Date.now() / 1000 - 3600 })
    running.mockResolvedValue(list([row()]))
    await mount()
    await card(/Plan the garden/)
    act(() => { vi.advanceTimersByTime(60_000) })
    await restartAt(Date.now() + 20_000)
    expect(await within(section()).findByText('Interrupted: The gateway restarted while it ran.')).toBeTruthy()
  })

  it('🔴 asleep: rows hours old do not read as cut off by a restart in the night', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] })
    status.mockResolvedValue({ start_time: Date.now() / 1000 - 3600 })
    running.mockResolvedValue(list([row()]))
    await mount()
    await card(/Plan the garden/)
    // The phone sleeps eight hours: its clock jumps and no tick ran in between. It wakes, its
    // clock ticks, and the gateway it finds started five hours in.
    const slept = Date.now()
    vi.setSystemTime(slept + 8 * 3600_000)
    act(() => { vi.advanceTimersByTime(15_000) })
    await restartAt(slept + 5 * 3600_000)
    expect(within(section()).queryByText(/Interrupted/)).toBeNull()
  })

  it('awake for the drop, then asleep while the gateway was down: what it cut off is still told', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] })
    status.mockResolvedValue({ start_time: Date.now() / 1000 - 3600 })
    running.mockResolvedValue(list([row()]))
    await mount()
    await card(/Plan the garden/)
    connected(false)
    // The gateway is down; the machine sleeps an hour and the gateway starts as it wakes.
    vi.setSystemTime(Date.now() + 3600_000)
    act(() => { vi.advanceTimersByTime(15_000) })
    status.mockResolvedValue({ start_time: Date.now() / 1000, uptime_secs: 0 })
    running.mockResolvedValue(list())
    await reconnect()
    connected(true)
    expect(await within(section()).findByText('Interrupted: The gateway restarted while it ran.')).toBeTruthy()
  })

  it('a read that fails as the gateway goes is the gateway going, not a list gone stale', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] })
    status.mockResolvedValue({ start_time: Date.now() / 1000 - 3600, uptime_secs: 3600 })
    running.mockResolvedValue(list([row()]))
    await mount()
    await card(/Plan the garden/)
    running.mockRejectedValue(new Error('connection refused'))
    say({ type: 'running_changed', data: { changed: [{ work: 'chat:c1', session: 'c1' }] } })
    await act(async () => { await new Promise((r) => setTimeout(r, 600)) })
    act(() => { vi.advanceTimersByTime(5_000) })
    connected(false)
    act(() => { vi.advanceTimersByTime(5 * 60 * 1000) })
    status.mockResolvedValue({ start_time: Date.now() / 1000, uptime_secs: 0 })
    running.mockResolvedValue(list())
    await reconnect()
    connected(true)
    expect(await within(section()).findByText('Interrupted: The gateway restarted while it ran.')).toBeTruthy()
  })

  it('🔴 on the gateway\'s machine, rows whose reads kept failing are not current at the drop', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] })
    status.mockResolvedValue({ start_time: Date.now() / 1000 - 3600, uptime_secs: 3600 })
    running.mockResolvedValue(list([row()]))
    await mount()
    await card(/Plan the garden/)
    const reads = running.mock.calls.length
    running.mockRejectedValue(new Error('list unreadable'))
    say({ type: 'running_changed', data: { changed: [{ work: 'chat:c1', session: 'c1' }] } })
    // The lane's burst window is a real timeout; `waitFor` polls on the faked interval, so wait it out.
    await act(async () => { await new Promise((r) => setTimeout(r, 600)) })
    expect(running.mock.calls.length).toBeGreaterThan(reads)
    act(() => { vi.advanceTimersByTime(60 * 60 * 1000) })
    connected(false)
    await restartAt(Date.now() - 10_000)
    expect(within(section()).queryByText(/Interrupted/)).toBeNull()
  })
})

describe('a phone away from the gateway tells only a restart its rows can bound', () => {
  // A page served from another machine: a network stands between it and the gateway, so a drop
  // may be the network's, and the gateway may have gone on and ended work before it restarted.
  // The page's own address moves there: a Location cannot be replaced, in a browser or in the VM
  // context this file runs in (vitest.config.ts), so jsdom is told the page's address instead.
  const home = location.href
  beforeEach(() => { jsdom.reconfigure({ url: 'https://gateway.example/#/companion' }) })
  afterEach(() => { jsdom.reconfigure({ url: home }); vi.useRealTimers() })

  /** Restart on a gateway that has been up `uptime` seconds, whose own clock reads `skew` ms off. */
  const restartUp = async (uptime: number, skew = 0) => {
    status.mockResolvedValue({ start_time: (Date.now() + skew) / 1000 - uptime, uptime_secs: uptime })
    running.mockResolvedValue(list())
    await reconnect()
    connected(true)
    await waitFor(() => expect(status.mock.calls.length).toBeGreaterThan(1))
    for (let i = 0; i < 5; i++) await act(async () => {})
  }
  const mountUp = async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] })
    status.mockResolvedValue({ start_time: Date.now() / 1000 - 3600, uptime_secs: 3600 })
    running.mockResolvedValue(list([row()]))
    await mount()
    await card(/Plan the garden/)
  }

  it('a restart just after the drop is told, whatever the gateway host\'s clock reads', async () => {
    await mountUp()
    act(() => { vi.advanceTimersByTime(60_000) })
    connected(false)
    act(() => { vi.advanceTimersByTime(30_000) })
    await restartUp(10, 10 * 60 * 1000)
    expect(await within(section()).findByText('Interrupted: The gateway restarted while it ran.')).toBeTruthy()
  })

  it('a restart long after the drop is not guessed at', async () => {
    await mountUp()
    connected(false)
    act(() => { vi.advanceTimersByTime(30 * 60 * 1000) })
    await restartUp(10)
    expect(within(section()).queryByText(/Interrupted/)).toBeNull()
  })

  it('🔴 rows whose reads kept failing are not current however long the socket stayed up', async () => {
    await mountUp()
    running.mockRejectedValue(new Error('list unreadable'))
    const reads = running.mock.calls.length
    say({ type: 'running_changed', data: { changed: [{ work: 'chat:c1', session: 'c1' }] } })
    // The lane's burst window is a real timeout; `waitFor` polls on the faked interval, so wait it out.
    await act(async () => { await new Promise((r) => setTimeout(r, 600)) })
    expect(running.mock.calls.length).toBeGreaterThan(reads)
    act(() => { vi.advanceTimersByTime(60 * 60 * 1000) })
    connected(false)
    await restartUp(10)
    expect(within(section()).queryByText(/Interrupted/)).toBeNull()
  })
})
