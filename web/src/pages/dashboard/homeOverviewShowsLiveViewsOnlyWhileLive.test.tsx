import { describe, expect, it, vi, beforeEach } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import type { WsMessage } from '../../lib/useChatSocket'
import type { BrowseStatus, ComputerUseLiveView } from '../../lib/api'

// ── Home's Overview shows its live views only while something is live ──────────────────────────
//
// The Overview drew a Desktop live view band and a Browse live view band on every install, all
// the time: an Armed/Off pill, two toggles and an empty feed for a desktop nobody drove, an Idle
// pill and a Stop button for a browser nobody browsed with. Each band now shows only while what it
// shows is happening:
//
//  · Desktop live view while the agent drives the desktop: the gateway's `live` (an action or a
//    walk within the last 30 s), read when the Overview opens and again at each `computer_use`
//    hint the gateway rings when an action lands;
//  · Browse live view while a browse takes steps: the status read's `live`, and each `browse_step`
//    frame at once, for 30 s after the last one.
//
// The bands' neighbours are stubs: what is pinned is whether each band is drawn.

const liveView = vi.fn<() => Promise<ComputerUseLiveView>>()
const browseStatus = vi.fn<() => Promise<BrowseStatus>>()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    computerUseLiveView: () => liveView(),
    browseStatus: () => browseStatus(),
  },
}))

// One subscription per handler, as the real socket keeps: the hook runs on every render, and a list
// would hand one frame to the same handler once per render.
const sockets = new Set<(m: WsMessage) => void>()
vi.mock('../../lib/useChatSocket', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatSocket: (onMessage: (m: WsMessage) => void) => { sockets.add(onMessage) },
}))
// The real hook reads once on mount, then on an interval while the tab is visible; an interval in
// jsdom is noise, so this stand-in keeps the mount read alone.
vi.mock('../../lib/useVisiblePoll', async () => {
  const { useEffect } = await import('react')
  return { useVisiblePoll: (fn: () => void) => { useEffect(() => { fn() }, []) } }
})

vi.mock('./DashboardLive', () => ({ DashboardLiveProvider: ({ children }: { children: ReactNode }) => <>{children}</> }))
vi.mock('./agents/AgentDay', () => ({ AgentDay: () => null }))
vi.mock('./widgets/TasksWidget', () => ({ TasksWidget: () => null }))
vi.mock('./widgets/Discover', () => ({ Discover: () => null }))
vi.mock('./widgets/PinnedArtifacts', () => ({ PinnedArtifacts: () => null }))
vi.mock('./widgets/ScheduleWidget', () => ({ ScheduleWidget: () => null }))

const { HomeOverview } = await import('./DashboardPage')
const { resetDataStore } = await import('../../lib/data/store')

const QUIET_DESKTOP: ComputerUseLiveView = {
  live: false, enabled: true, allowed_apps: ['TextEdit'], ttl_secs: 30, snapshots: [], trail: [], feed: [],
}
const DRIVEN: ComputerUseLiveView = {
  ...QUIET_DESKTOP,
  live: true,
  trail: [{ seq: 1, ts: 1000, tool: 'computer_click', app: 'TextEdit', method: 'ax_press', x: 125, y: 210, label: 'Save · AXButton' }],
  feed: [{ timestamp: new Date().toISOString(), operation: 'computer_click', outcome: 'approved', error: '', source: 'dashboard', caller_identity: 'dashboard:abc', app: 'TextEdit' }],
}
const IDLE_BROWSER: BrowseStatus = { live: false, kill: { active: false, reason: '', started_at: '' }, expired: [], grants: [] }

const route = { sub: '', navigate: vi.fn(), navEpoch: 0, query: {}, setQuery: vi.fn() }
const heading = (name: string) => screen.queryByRole('heading', { name })
const deliver = (m: WsMessage) => act(() => { for (const on of sockets) on(m) })

beforeEach(() => {
  resetDataStore(); sessionStorage.clear(); localStorage.clear()
  sockets.clear()
  liveView.mockResolvedValue(QUIET_DESKTOP)
  browseStatus.mockResolvedValue(IDLE_BROWSER)
})

describe('the Desktop live view', () => {
  it('🔴 is not drawn while nothing drives the desktop, nor any switch or pill of it', async () => {
    render(<HomeOverview {...route} />)
    await waitFor(() => expect(liveView).toHaveBeenCalled())

    expect(heading('Desktop live view')).toBeNull()
    expect(screen.queryByRole('switch')).toBeNull()
    expect(screen.queryByText('Armed')).toBeNull()
  })

  it('shows while the agent drives the desktop, wireframe and cursor together', async () => {
    liveView.mockResolvedValue(DRIVEN)
    render(<HomeOverview {...route} />)

    expect(await screen.findByRole('heading', { name: 'Desktop live view' })).toBeTruthy()
    expect(screen.getByRole('img', { name: /fake cursor over save/i })).toBeTruthy()
    expect(screen.queryByRole('switch')).toBeNull()
  })

  it('comes in when the gateway rings that the desktop is in use', async () => {
    render(<HomeOverview {...route} />)
    await waitFor(() => expect(liveView).toHaveBeenCalledTimes(1))
    expect(heading('Desktop live view')).toBeNull()

    liveView.mockResolvedValue(DRIVEN)
    deliver({ type: 'refresh', data: { kinds: ['computer_use'] } } as WsMessage)

    expect(await screen.findByRole('heading', { name: 'Desktop live view' })).toBeTruthy()
  })
})

describe('the Browse live view', () => {
  it('🔴 is not drawn while no browse runs, nor its Stop', async () => {
    render(<HomeOverview {...route} />)
    await waitFor(() => expect(browseStatus).toHaveBeenCalled())

    expect(heading('Browse live view')).toBeNull()
    expect(screen.queryByRole('button', { name: /Stop all browsing/ })).toBeNull()
  })

  it('shows while a browse takes steps, and its steps with it', async () => {
    render(<HomeOverview {...route} />)
    await waitFor(() => expect(browseStatus).toHaveBeenCalled())
    expect(heading('Browse live view')).toBeNull()

    deliver({
      type: 'browse_step',
      data: { run_id: 'run-1', step_n: 3, url: 'https://example.com/pricing', action: 'CLICK a1b2 (Pricing)', screenshot: '', note: '' },
    } as WsMessage)

    expect(await screen.findByRole('heading', { name: 'Browse live view' })).toBeTruthy()
    expect(screen.getByText('https://example.com/pricing')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Stop/ })).toBeNull()
  })

  it('shows on a page opened mid-browse, from the status read', async () => {
    browseStatus.mockResolvedValue({ ...IDLE_BROWSER, live: true })
    render(<HomeOverview {...route} />)

    expect(await screen.findByRole('heading', { name: 'Browse live view' })).toBeTruthy()
  })
})
