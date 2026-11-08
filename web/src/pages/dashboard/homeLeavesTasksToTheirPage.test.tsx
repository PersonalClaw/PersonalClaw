import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import type { BrowseStatus, ComputerUseLiveView } from '../../lib/api'

// ── Home shows the agents, not their tasks ──────────────────────────────────────────────────────
//
// Tasks are what agents keep their goals in and track them by. The owner works with the agents, not
// with their tasks, so Home's Overview has no Tasks band: tasks keep their own page (`#/tasks`, and
// each project's). The Overview's bands run, top to bottom: Your agents today, Pinned artifacts,
// the live views while something is live, Discover, and Recent activity last.
//
// The bands' contents are stubs: what is pinned is which bands the Overview draws, and in what order.

const liveView = vi.fn<() => Promise<ComputerUseLiveView>>()
const browseStatus = vi.fn<() => Promise<BrowseStatus>>()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    computerUseLiveView: () => liveView(),
    browseStatus: () => browseStatus(),
  },
}))
vi.mock('../../lib/useChatSocket', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatSocket: () => {},
}))
vi.mock('../../lib/useVisiblePoll', async () => {
  const { useEffect } = await import('react')
  return { useVisiblePoll: (fn: () => void) => { useEffect(() => { fn() }, []) } }
})
vi.mock('./DashboardLive', () => ({ DashboardLiveProvider: ({ children }: { children: ReactNode }) => <>{children}</> }))
// Your agents today holds its own heading (`AgentDay`'s `Section`); the stand-in keeps just that.
vi.mock('./agents/AgentDay', () => ({ AgentDay: () => <h2>Your agents today</h2> }))
vi.mock('./widgets/Discover', () => ({ Discover: () => null }))
vi.mock('./widgets/PinnedArtifacts', () => ({ PinnedArtifacts: () => null }))
vi.mock('./widgets/ScheduleWidget', () => ({ ScheduleWidget: () => null }))

const { HomeOverview } = await import('./DashboardPage')
const { resetDataStore } = await import('../../lib/data/store')

const QUIET_DESKTOP: ComputerUseLiveView = {
  live: false, enabled: true, allowed_apps: ['TextEdit'], ttl_secs: 30, snapshots: [], trail: [], feed: [],
}
const IDLE_BROWSER: BrowseStatus = { live: false, kill: { active: false, reason: '', started_at: '' }, expired: [], grants: [] }

const route = { sub: '', navigate: vi.fn(), navEpoch: 0, query: {}, setQuery: vi.fn() }
const bands = () => screen.queryAllByRole('heading').map((h) => h.textContent?.trim() ?? '')

beforeEach(() => {
  resetDataStore(); sessionStorage.clear(); localStorage.clear()
  liveView.mockResolvedValue(QUIET_DESKTOP)
  browseStatus.mockResolvedValue(IDLE_BROWSER)
})

describe("Home's Overview", () => {
  it('🔴 runs Your agents today, Pinned artifacts, Discover, then Recent activity, with no Tasks band', async () => {
    render(<HomeOverview {...route} />)
    await waitFor(() => expect(browseStatus).toHaveBeenCalled())

    expect(bands()).toEqual(['Your agents today', 'Pinned artifacts', 'Discover', 'Recent activity'])
    expect(screen.queryByRole('button', { name: /new task/i })).toBeNull()
    expect(document.querySelector('a[href^="#/tasks"]')).toBeNull()
  })

  it('🔴 keeps Discover last but for Recent activity while a live view shows', async () => {
    liveView.mockResolvedValue({ ...QUIET_DESKTOP, live: true })
    browseStatus.mockResolvedValue({ ...IDLE_BROWSER, live: true })
    render(<HomeOverview {...route} />)
    await screen.findByRole('heading', { name: 'Browse live view' })
    await screen.findByRole('heading', { name: 'Desktop live view' })

    expect(bands()).toEqual([
      'Your agents today', 'Pinned artifacts', 'Desktop live view', 'Browse live view', 'Discover', 'Recent activity',
    ])
  })
})
