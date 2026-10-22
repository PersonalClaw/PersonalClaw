// @vitest-environment jsdom
// @module-tag tree-scan
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { join } from 'node:path'
import { resetDataStore } from '../../lib/data'
import type { WsMessage } from '../../lib/useChatSocket'
import { readSource } from '../../test/sourceTree'
import { HomeHarness, runningRow, thread, waitingItem, waitingList } from '../../test/homeHarness'

// ── Home is the attention stack (#3813) ─────────────────────────────────────────────────────────
//
// Three groups, in order: Needs You (the waiting list, by consequence then age), Running Now (the
// running list, each row's phase and last update) and Recent and completed. No idle group. And
// every state the surface can be in says what it is: empty, overloaded, offline, a concurrent
// answer, an expired ask, a failed read, and the read that recovers.

const waiting = vi.fn()
const inboxOpen = vi.fn()
const running = vi.fn()
const chatSessions = vi.fn()
const resolveApproval = vi.fn()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    waiting: () => waiting(),
    inboxOpen: () => inboxOpen(),
    skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
    status: () => Promise.resolve({}),
    running: () => running(),
    chatSessions: () => chatSessions(),
    resolveApproval: (...a: unknown[]) => resolveApproval(...a),
  },
}))

let statusOf: ((up: boolean) => void) | undefined
const frames: Array<(m: WsMessage) => void> = []
vi.mock('../../lib/useChatSocket', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatSocket: (onMessage: (m: WsMessage) => void, _onReconnect: unknown, onStatus?: (up: boolean) => void) => {
    frames.push(onMessage)
    if (onStatus) statusOf = onStatus
  },
}))
vi.mock('../../app/identity', () => ({ useIdentity: () => ({ name: 'Sam Rivera' }), firstNameOf: (n: string) => n.split(' ')[0] }))
vi.mock('./PinnedTiles', () => ({ PinnedTiles: () => <div data-testid="pinned-tiles" /> }))
vi.mock('../../ui/surfaces/SurfaceOverlay', () => ({ SurfaceOverlay: ({ surface }: { surface: string }) => <div data-testid={`overlay-${surface}`} /> }))
vi.mock('./widgets/Suggestions', () => ({ Suggestions: () => <div data-testid="suggestions" /> }))
vi.mock('./DashboardPage', () => ({ HomeOverview: () => <div data-testid="home-overview" /> }))

const ApiErrorOf = async () => (await import('../../lib/api')).ApiError

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  sessionStorage.clear()
  frames.length = 0
  statusOf = undefined
  waiting.mockResolvedValue(waitingList())
  inboxOpen.mockResolvedValue([])
  running.mockResolvedValue({ running: [], finished: [] })
  chatSessions.mockResolvedValue([])
  resolveApproval.mockResolvedValue({ ok: true })
})
afterEach(() => { vi.restoreAllMocks() })

/** jsdom's own box, taken before any test sizes one. */
const BOX = HTMLElement.prototype.getBoundingClientRect

const group = (name: string) => screen.getByRole('region', { name })
const needsYou = () => group('Needs You')

describe('the three groups', () => {
  it('render in order under the page title, and there is no idle group', async () => {
    render(<HomeHarness />)
    await screen.findByText('Nothing is waiting on you.')
    const headings = screen.getAllByRole('heading').map((h) => h.textContent)
    expect(headings[0]).toMatch(/^Good \w+, Sam$/)
    expect(headings.slice(1)).toEqual(['Needs You', 'Running Now', 'Recent and completed'])
    expect(screen.queryByRole('heading', { name: 'Idle' })).toBeNull()
  })

  it('each says what its emptiness means, and the all-clear offers suggestions', async () => {
    render(<HomeHarness />)
    expect(await within(needsYou()).findByText('Nothing is waiting on you.')).toBeInTheDocument()
    expect(within(group('Running Now')).getByText('Nothing is running right now.')).toBeInTheDocument()
    expect(await within(group('Recent and completed')).findByText(/Nothing has finished lately/)).toBeInTheDocument()
    expect(screen.getByTestId('suggestions')).toBeInTheDocument()
  })

  it('keep what was pinned and the dashboard overlays under them', async () => {
    render(<HomeHarness />)
    await screen.findByText('Nothing is waiting on you.')
    expect(screen.getByTestId('pinned-tiles')).toBeInTheDocument()
    expect(screen.getByTestId('overlay-dashboard')).toBeInTheDocument()
  })

  it('Needs You keeps the order the list serves: consequence, then age', async () => {
    // The low-risk ask is older; the broad-reach approval is newer. The list serves the approval
    // first, and Home never sorts the list again.
    waiting.mockResolvedValue(waitingList([
      waitingItem({ id: 'approval:new', consequence: 'destructive', risk: 'destructive', age_secs: 5,
        brief: { tool: 'rm -rf build', source_label: 'loop “Tidy”' },
        answer: { method: 'POST', path: '/api/approvals/new/{answer}', takes: ['approve', 'reject'], body: {} } }),
      waitingItem({ id: 'question:old', kind: 'question', consequence: 'asks', age_secs: 9000,
        brief: { questions: [{ question: 'Which branch?' }], asked_by: 'Release notes' } }),
    ]))
    render(<HomeHarness />)
    await within(needsYou()).findByText('Which branch?')
    const text = needsYou().textContent ?? ''
    expect(text.indexOf('rm -rf build')).toBeGreaterThan(-1)
    expect(text.indexOf('rm -rf build')).toBeLessThan(text.indexOf('Which branch?'))
    expect(within(needsYou()).getByText('2')).toHaveAttribute('data-group-count')
  })

  it('Recent and completed lists your threads, newest first, and leaves out a chat that runs', async () => {
    chatSessions.mockResolvedValue([
      thread('c-old', 'Old plan', { last_ts: '2026-10-20T09:00:00Z' }),
      thread('c-new', 'Release notes', { last_ts: '2026-10-21T09:00:00Z' }),
      thread('c-live', 'Live one'),
    ])
    running.mockResolvedValue({ running: [runningRow({ work: 'chat:c-live', session: 'c-live', name: 'Live one' })], finished: [] })
    render(<HomeHarness />)
    const list = await within(group('Recent and completed')).findByRole('list', { name: 'Your threads' })
    const rows = within(list).getAllByRole('listitem').map((li) => li.textContent)
    expect(rows[0]).toContain('Release notes')
    expect(rows[1]).toContain('Old plan')
    expect(rows.join(' ')).not.toContain('Live one')
  })
})

describe('every state', () => {
  it('overloaded: the count is all of it, the first 25 show, and the rest are one press away', async () => {
    waiting.mockResolvedValue(waitingList(Array.from({ length: 30 }, (_, i) => waitingItem({
      id: `question:q${i}`, kind: 'question', consequence: 'asks', brief: { questions: [{ question: `Question ${i}` }] },
    }))))
    render(<HomeHarness />)
    await within(needsYou()).findByText('Question 0')
    expect(within(needsYou()).getByText('30')).toHaveAttribute('data-group-count')
    expect(within(needsYou()).queryByText('Question 29')).toBeNull()
    await userEvent.click(within(needsYou()).getByRole('button', { name: 'Show all 30' }))
    expect(within(needsYou()).getByText('Question 29')).toBeInTheDocument()
  })

  it('offline: while the socket is down it says what shows may be out of date', async () => {
    render(<HomeHarness />)
    await screen.findByText('Nothing is waiting on you.')
    act(() => { statusOf?.(true) })
    act(() => { statusOf?.(false) })
    expect(await screen.findByText(/Reconnecting… what shows here may be out of date/)).toBeInTheDocument()
  })

  it('concurrent: an ask answered somewhere else says so, and offers nothing to retry', async () => {
    const ApiError = await ApiErrorOf()
    resolveApproval.mockRejectedValue(new ApiError('not found', 404))
    waiting.mockResolvedValue(waitingList([waitingItem()]))
    render(<HomeHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Allow/ }))
    expect(await within(needsYou()).findByText('It is no longer waiting: it was answered or ended somewhere else.')).toBeInTheDocument()
    expect(within(needsYou()).queryByRole('button', { name: /^Allow/ })).toBeNull()
  })

  it('expired: an ask held past its expiry can no longer be answered here, and says when it stopped', async () => {
    waiting.mockResolvedValue(waitingList([waitingItem({
      id: 'question:late', kind: 'question', consequence: 'asks', expires_at: '2020-01-01T00:00:00Z',
      brief: { questions: [{ question: 'Too late?' }] },
    }), waitingItem({ id: 'approval:late', expires_at: '2020-01-01T00:00:00Z' })]))
    render(<HomeHarness />)
    const said = await within(needsYou()).findAllByText(/stopped waiting at .+, so it can no longer be answered here/)
    expect(said).toHaveLength(2)
    expect(within(needsYou()).queryByRole('button', { name: /^Allow|^Deny/ })).toBeNull()
  })

  it('failed: a read that fails says so, and never that nothing is waiting', async () => {
    waiting.mockRejectedValue(new Error('gateway unreachable'))
    render(<HomeHarness />)
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Could not read what is waiting on you: gateway unreachable')
    expect(within(needsYou()).getByText('Could not read what is waiting on you.')).toBeInTheDocument()
    expect(screen.queryByText('Nothing is waiting on you.')).toBeNull()
  })

  it('recovered: Try again reads it again, and the groups come back', async () => {
    waiting.mockRejectedValueOnce(new Error('gateway unreachable'))
    waiting.mockResolvedValue(waitingList([waitingItem()]))
    render(<HomeHarness />)
    await userEvent.click(within(await screen.findByRole('alert')).getByRole('button', { name: 'Try again' }))
    expect(await within(needsYou()).findByRole('group', { name: 'Permission needed to run shell.run' })).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText(/Could not read what is waiting on you/)).toBeNull())
  })

  it('a source the gateway could not read is named, and the rest is not called all clear', async () => {
    waiting.mockResolvedValue(waitingList([], ['gate', 'approval']))
    render(<HomeHarness />)
    expect(await within(needsYou()).findByText(/Could not read workflow runs, approvals: what they hold may be waiting on you/)).toBeInTheDocument()
    expect(within(needsYou()).getByText('Nothing else is waiting on you.')).toBeInTheDocument()
    expect(screen.queryByText('Nothing is waiting on you.')).toBeNull()
    expect(screen.queryByTestId('suggestions')).toBeNull()
  })

  it('a frame that something waits re-reads the list, and the new ask shows', async () => {
    render(<HomeHarness />)
    await screen.findByText('Nothing is waiting on you.')
    waiting.mockResolvedValue(waitingList([waitingItem()]))
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      act(() => { for (const f of [...frames]) f({ type: 'approval', data: {} }) })
      await act(async () => { vi.advanceTimersByTime(350) })
    } finally {
      vi.useRealTimers()
    }
    expect(await within(needsYou()).findByRole('group', { name: 'Permission needed to run shell.run' })).toBeInTheDocument()
  })
})

describe('the selected item’s detail', () => {
  /** The tab's own box, `width` px wide; every other element keeps jsdom's empty box. */
  function room(width: number) {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      if (!this.matches('[data-capability-layout]')) return BOX.call(this)
      return { x: 0, y: 0, top: 0, left: 0, width, height: 800, right: width, bottom: 800, toJSON: () => ({}) } as DOMRect
    })
  }

  it('wide: opens beside the groups, which stay, and the address names it', async () => {
    room(1200)
    running.mockResolvedValue({ running: [runningRow({ name: 'Plan the garden', line: 'Reading the plan' })], finished: [] })
    let address: Record<string, string> = {}
    render(<HomeHarness onQuery={(q) => { address = q }} />)
    const row = await within(group('Running Now')).findByRole('listitem', { name: /Plan the garden/ })
    await userEvent.click(within(row).getByRole('button', { name: 'Show the details of Plan the garden' }))
    const pane = await screen.findByRole('region', { name: 'Plan the garden' })
    expect(within(pane).getByText('Reading the plan')).toBeInTheDocument()
    expect(group('Running Now')).toBeVisible()
    expect(address.open).toBe('run:chat:chat-1')
    expect(within(pane).getByRole('button', { name: 'Close' })).toBeInTheDocument()
  })

  it('compact: opens in place of the groups, and Back returns to them', async () => {
    room(600)
    waiting.mockResolvedValue(waitingList([waitingItem()]))
    render(<HomeHarness query={{ open: 'waiting:approval:appr-1' }} />)
    const pane = await screen.findByRole('region', { name: 'shell.run' })
    expect(within(pane).getByRole('button', { name: /^Allow/ })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Needs You' })).toBeNull()
    await userEvent.click(within(pane).getByRole('button', { name: 'Back to Home' }))
    expect(await screen.findByRole('region', { name: 'Needs You' })).toBeInTheDocument()
  })
})

describe('the header', () => {
  it('starts a new thread, and its View switches to Overview and back', async () => {
    const navigate = vi.fn()
    render(<HomeHarness navigate={navigate} />)
    await screen.findByText('Nothing is waiting on you.')
    await userEvent.click(screen.getByRole('button', { name: /New thread/ }))
    expect(navigate).toHaveBeenCalledWith('chat/new')
    await userEvent.click(screen.getByRole('radio', { name: 'Overview' }))
    expect(await screen.findByTestId('home-overview')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Needs You' })).toBeNull()
    await userEvent.click(screen.getByRole('radio', { name: 'Attention' }))
    expect(await screen.findByRole('region', { name: 'Needs You' })).toBeInTheDocument()
  })
})

describe('the route is Home', () => {
  const app = () => readSource(join(process.cwd(), 'src/app/App.tsx'))

  it('dispatches both the old home and the Mission Control address to Home', () => {
    const src = app()
    expect(src).toMatch(/lazy\(\(\) => import\('\.\.\/pages\/dashboard\/Home'\)/)
    expect(src).toMatch(/case 'dashboard': return <Home \{\.\.\.r\} \/>/)
    expect(src).toMatch(/case 'mission-control': return <Home \{\.\.\.r\} \/>/)
  })

  it('keeps mission-control routable, and reachable from the palette', () => {
    const src = app()
    expect(src).toMatch(/const ROUTABLE = new Set\(\[[^\]]*'mission-control'/)
    expect(src).toMatch(/id: 'go:mission-control'[^\n]*navigate\('mission-control'\)/)
  })
})

describe('GROUP_REFS, the one reconciliation point with views_store', () => {
  it('covers every group exactly once, in order', async () => {
    const { GROUPS, GROUP_REFS, groupForRef } = await import('./Home')
    expect(Object.keys(GROUP_REFS)).toEqual([...GROUPS])
    expect(new Set(Object.values(GROUP_REFS)).size).toBe(GROUPS.length)
    for (const g of GROUPS) expect(groupForRef(GROUP_REFS[g])).toBe(g)
    expect(groupForRef('core:lane-idle')).toBeNull()
  })
})
