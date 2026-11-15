// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import type { ReactNode } from 'react'

// ── Home's section of what you keep on it says it is empty once, and only when it is ─────────────
//
// The tiles you keep on Home moved into the Overview's Shown on Home section, above the
// pins. The pins' own list said "No pinned artifacts. Pin one from its page to keep it here." when
// it held none, so with a tile there the section said it was empty under the tile. Now the
// sentence is said only when the section holds neither, and once.

const views = vi.hoisted(() => ({ tiles: [] as unknown[] }))
const pins = vi.hoisted(() => ({ rows: [] as { slug: string; pinned_at: number }[] }))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    dashboardViews: vi.fn(async () => [{ id: 'overview', name: 'Overview', nav_pinned: true, preset: true, tiles: views.tiles }]),
    artifact: vi.fn(async (slug: string) => ({ slug, name: 'Weekly metrics chart', kind: 'markdown', content: 'A tile' })),
    artifactExists: vi.fn(async () => true),
    artifacts: vi.fn(async () => [{ slug: 'report', name: 'Quarterly report', kind: 'markdown', version: 1 }]),
    pinnedArtifacts: vi.fn(async () => ({ pins: pins.rows })),
    refreshTile: vi.fn(async () => ({ refreshed: false, reason: 'manual', ok: true, nodes: [], row: {} })),
    tileLedgerHref: (viewId: string, ref: string) => `/api/dashboard/views/${viewId}/tiles/refresh?ref=${ref}`,
    computerUseLiveView: vi.fn(async () => ({ live: false, enabled: true, allowed_apps: [], ttl_secs: 30, snapshots: [], trail: [], feed: [] })),
    browseStatus: vi.fn(async () => ({ live: false, kill: { active: false, reason: '', started_at: '' }, expired: [], grants: [] })),
  },
}))
vi.mock('../../app/appSdk', () => ({ launchChat: () => {}, notify: () => {} }))
vi.mock('../../lib/useChatSocket', async (orig) => ({ ...(await orig<Record<string, unknown>>()), useChatSocket: () => {} }))
vi.mock('./DashboardLive', () => ({ DashboardLiveProvider: ({ children }: { children: ReactNode }) => <>{children}</> }))
vi.mock('./agents/AgentDay', () => ({ AgentDay: () => <h2>Your agents today</h2> }))
vi.mock('./widgets/Discover', () => ({ Discover: () => null }))
vi.mock('./widgets/ScheduleWidget', () => ({ ScheduleWidget: () => null }))
vi.mock('../../ui/surfaces/SurfaceOverlay', () => ({ SurfaceOverlay: () => null }))

const { HomeOverview } = await import('./DashboardPage')
const { resetDataStore } = await import('../../lib/data/store')
const { registerBuiltinContentTypes } = await import('../../ui/content/registerBuiltins')
registerBuiltinContentTypes()

const route = { sub: '', navigate: vi.fn(), navEpoch: 0, query: {}, setQuery: vi.fn() }
const EMPTY = /Nothing shown here yet\. Choose Show on Home on a page in your library\./
const TILE = { ref: 'artifact:metrics', size: 'm', order: 0, added_by: 'user', refresh: { mode: 'manual', ttl_secs: 0, skeleton: '', data: [] } }
const section = () => screen.getByRole('heading', { name: 'Shown on Home' }).closest('section')!

beforeEach(() => {
  resetDataStore(); sessionStorage.clear(); localStorage.clear()
  views.tiles = []
  pins.rows = []
})

describe("Home's section of what you keep on it", () => {
  it('holding neither a tile nor a pin, says so once', async () => {
    render(<HomeOverview {...route} />)
    expect(await within(section()).findByText(EMPTY)).toBeInTheDocument()
    expect(within(section()).getAllByText(EMPTY)).toHaveLength(1)
  })

  it('🔴 holding a tile and no pin, draws the tile and says nothing of being empty', async () => {
    views.tiles = [TILE]
    render(<HomeOverview {...route} />)
    await waitFor(() => expect(section().querySelector('[data-testid="pinned-tiles"]')).not.toBeNull())
    await waitFor(() => expect(within(section()).getByRole('button', { name: 'Remove from Home' })).toBeInTheDocument())
    expect(within(section()).queryByText(EMPTY)).toBeNull()
  })

  it('holding a pin and no tile, lists the pin and says nothing of being empty', async () => {
    pins.rows = [{ slug: 'report', pinned_at: 1 }]
    render(<HomeOverview {...route} />)
    expect(await within(section()).findByText('Quarterly report')).toBeInTheDocument()
    expect(within(section()).queryByText(EMPTY)).toBeNull()
    expect(section().querySelector('[data-testid="pinned-tiles"]')).toBeNull()
  })
})
