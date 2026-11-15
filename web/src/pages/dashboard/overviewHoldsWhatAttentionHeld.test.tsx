// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'

// ── Home's Overview holds what its Attention view held ───────────────────────────────────────────
//
// Home's Attention view drew two things that were not asks: the tiles you keep on Home, with Keep
// and Dismiss on the ones an agent proposed, and your own overlays for the `dashboard` surface, last.
// Attention became Needs you, which holds only what waits; so both moved to the Overview, the tiles
// under "Your agents today" in the Home section, with every control they had, and the overlays at
// the end. Pin to Home pinned onto Overview's tiles all along, yet Overview never drew them.

const resolveTile = vi.fn(async () => ({}))
const tile = (ref: string, order: number, added_by: 'user' | 'agent') =>
  ({ ref, size: 'm', order, added_by, refresh: { mode: 'manual', ttl_secs: 0, skeleton: '', data: [] } })

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    dashboardViews: vi.fn(async () => [{ id: 'overview', name: 'Overview', nav_pinned: true, preset: true,
      tiles: [tile('artifact:metrics', 0, 'user'), tile('artifact:budget', 1, 'agent')] }]),
    artifact: vi.fn(async (slug: string) => ({ slug, name: slug === 'metrics' ? 'Weekly metrics chart' : 'Grocery budget tracker', kind: 'markdown', content: 'A tile' })),
    artifactExists: vi.fn(async () => true),
    resolveTile: (...a: unknown[]) => resolveTile(...(a as [])),
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
vi.mock('./widgets/PinnedArtifacts', () => ({ PinnedArtifacts: () => null }))
vi.mock('./widgets/ScheduleWidget', () => ({ ScheduleWidget: () => null }))
vi.mock('../../ui/surfaces/SurfaceOverlay', () => ({ SurfaceOverlay: ({ surface }: { surface: string }) => <div data-testid={`overlay-${surface}`} /> }))

const { HomeOverview } = await import('./DashboardPage')
const { resetDataStore } = await import('../../lib/data/store')
const { registerBuiltinContentTypes } = await import('../../ui/content/registerBuiltins')
registerBuiltinContentTypes()

const route = { sub: '', navigate: vi.fn(), navEpoch: 0, query: {}, setQuery: vi.fn() }

beforeEach(() => { resetDataStore(); sessionStorage.clear(); localStorage.clear(); resolveTile.mockClear() })

describe("the Overview holds what Home's Attention view held", () => {
  it('draws the tiles you keep on Home under Your agents today, in the Home section', async () => {
    render(<HomeOverview {...route} />)
    const tiles = await screen.findByTestId('pinned-tiles')
    const agents = screen.getByRole('heading', { name: 'Your agents today' })
    expect(agents.compareDocumentPosition(tiles) & Node.DOCUMENT_POSITION_FOLLOWING, 'the tiles come after the band').toBeTruthy()
    const section = screen.getByRole('heading', { name: 'Shown on Home' }).closest('section')!
    expect(section.contains(tiles)).toBe(true)
  })

  it('keeps each tile’s controls: Keep on Home and Dismiss on a proposed one, Remove from Home on yours', async () => {
    render(<HomeOverview {...route} />)
    const tiles = await screen.findByTestId('pinned-tiles')
    await waitFor(() => expect(within(tiles).getAllByRole('button', { name: 'Refresh tile' })).toHaveLength(2))
    expect(within(tiles).getAllByRole('button', { name: 'Remove from Home' })).toHaveLength(1)
    await userEvent.click(within(tiles).getByRole('button', { name: 'Keep on Home' }))
    expect(resolveTile).toHaveBeenCalledWith('overview', { ref: 'artifact:budget', keep: true })
    await userEvent.click(within(tiles).getByRole('button', { name: 'Dismiss proposal' }))
    expect(resolveTile).toHaveBeenCalledWith('overview', { ref: 'artifact:budget', keep: false })
    // The tips are the app's own, never a native title.
    for (const b of within(tiles).getAllByRole('button')) expect(b.hasAttribute('title'), b.getAttribute('aria-label') ?? '').toBe(false)
  })

  it('draws your own overlays for Home last', async () => {
    const { container } = render(<HomeOverview {...route} />)
    await screen.findByTestId('pinned-tiles')
    const overlay = screen.getByTestId('overlay-dashboard')
    const column = overlay.parentElement!
    expect(column.lastElementChild).toBe(overlay)
    expect(container.querySelectorAll('[data-testid^="overlay-"]')).toHaveLength(1)
  })
})
