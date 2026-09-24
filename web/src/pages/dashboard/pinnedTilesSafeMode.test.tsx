/** AMBIENT-SURFACES §6 / AS-6 — the tile band under safe mode: INERT LINKS.
 *
 *  §6 names "tiles rendered as inert links" as part of the recovery route. The band itself
 *  is L0 code, so the route was never compromised — but a tile's BODY is a generated
 *  artifact that may be a genui tree, it polls a refresh endpoint on a timer, and it
 *  carries controls that re-fire a workflow server-side. None of that belongs on the
 *  surface someone reached BECAUSE the app was misbehaving.
 *
 *  Driven through the RENDERED band, because the claim is about what a user sees and about
 *  what does NOT happen: the assertion that carries the whole test is that `api.artifact`
 *  and `api.refreshTile` are never called. A safe-mode branch that still fetched would
 *  look identical in the DOM.
 *
 *  Both legs, always: safe mode off renders the live tile and DOES call those two, which is
 *  what proves the safe-mode leg above measured the mode rather than a broken mock. */
import { render, act, screen } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { PinnedTiles } from './PinnedTiles'
import { invalidateKeys } from '../../lib/data'

const BODY = '<div>sales: 42</div>'
const artifact = vi.fn(async () => ({ slug: 'sales', name: 'Sales', content: BODY }))
const refreshTile = vi.fn(async () => ({ refreshed: false, reason: 'within_ttl', ok: true, nodes: [], row: {} }))

vi.mock('../../app/appSdk', () => ({ launchChat: () => {}, notify: () => {} }))

/** Safe mode is driven through the READER the component actually calls, not through
 *  `window.location.hash`.
 *
 *  Two reasons, in order. (1) `tests/test_url_navigation_doctrine.py` bans
 *  `location.hash =` under `web/src/pages` so that `web/src/app/useHashRoute.ts` stays the
 *  single owner of hash/history mechanics — a page test that mutates global URL state is
 *  exactly what that doctrine is about, and the fix is to stop needing the URL, not to
 *  exempt the file. (2) It is the more honest unit: `PinnedTiles` reads `safeMode()` and
 *  nothing else, and which of the three levers (server latch, `<meta>`, hash query) turned
 *  the mode on is `ui/surfaces/layers.ts`'s claim, already pinned by `layers.test.tsx`.
 *
 *  `setServerSafeSurfaces(true)` was rejected: it is deliberately ONE-WAY (a later `false`
 *  cannot clear it), so the latch would leak out of the safe-mode leg and silently make the
 *  live control leg below safe too — the control leg is what proves this one measured the
 *  mode at all.
 *
 *  `maxSurfaceLayer` is overridden from the SAME flag so the mock cannot describe a state
 *  the real module could never be in (safe mode with a non-core layer ceiling). */
const surfaces = vi.hoisted(() => ({ safe: false }))
vi.mock('../../ui/surfaces/layers', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../ui/surfaces/layers')>()
  return {
    ...real,
    safeMode: () => surfaces.safe,
    maxSurfaceLayer: () => (surfaces.safe ? real.LAYER_CORE : real.LAYER_USER),
  }
})

vi.mock('../../lib/api', () => ({
  api: {
    dashboardViews: vi.fn(async () => [
      {
        id: 'overview',
        tiles: [
          {
            ref: 'artifact:sales',
            size: 'm',
            order: 0,
            added_by: 'user',
            // `isLive` needs mode:'ttl' AND a skeleton — a tile without one polls nothing, and
            // the `refreshTile` negative below would then be unmeasured on BOTH legs.
            refresh: { mode: 'ttl', ttl_secs: 900, skeleton: 'sales-skeleton', data: [{ id: 'h', provider: 'knowledge-health', config: {} }] },
          },
        ],
      },
    ]),
    artifact: (...a: unknown[]) => artifact(...(a as [])),
    artifactExists: vi.fn(async () => true),
    createArtifact: vi.fn(async () => ({})),
    deleteArtifact: vi.fn(async () => ({})),
    pinTile: vi.fn(async () => ({})),
    resolveTile: vi.fn(async () => ({})),
    refreshTile: (...a: unknown[]) => refreshTile(...(a as [])),
    tileLedgerHref: (viewId: string, ref: string) => `/api/dashboard/views/${viewId}/tiles/refresh?ref=${ref}`,
  },
}))

// jsdom has no blob URLs and the LIVE leg renders a real `WidgetFrame` (which is the point —
// the inert leg is only meaningful against a tile that really paints its body). Same shim the
// widget suites use.
if (typeof URL.createObjectURL !== 'function') {
  URL.createObjectURL = () => 'blob:pinned-tiles-safe-mode-test'
  URL.revokeObjectURL = () => {}
}

beforeEach(() => {
  invalidateKeys('dashboard:views')
  invalidateKeys('dashboard:tile:sales')
  artifact.mockClear()
  refreshTile.mockClear()
  surfaces.safe = false
})

async function paint() {
  render(<PinnedTiles />)
  await act(async () => { await Promise.resolve() })
  await act(async () => { await Promise.resolve() })
}

describe('the pinned-tile band in safe mode', () => {
  it('renders each tile as an inert link and fetches NOTHING', async () => {
    surfaces.safe = true
    await paint()

    const link = screen.getByTestId('pinned-tile-inert')
    expect(link.tagName).toBe('A')
    expect(link.getAttribute('href')).toBe('#/artifacts/sales')

    // 🪤 The load-bearing half. The DOM alone cannot tell an inert tile from a live one
    // whose body has not arrived; the absence of these two calls can.
    expect(artifact).not.toHaveBeenCalled()
    expect(refreshTile).not.toHaveBeenCalled()

    // No re-fire / unpin / refresh controls reachable from the recovery surface.
    expect(screen.queryByLabelText('Refresh tile')).toBeNull()
    expect(screen.queryByLabelText('Unpin from dashboard')).toBeNull()
    // Said out loud, so a user does not read the missing controls as more breakage.
    expect(screen.getByTestId('pinned-tiles-safe-note').textContent).toContain('links only')
  })

  it('renders the LIVE tile with safe mode off, and does call both (the control leg)', async () => {
    await paint()

    expect(screen.queryByTestId('pinned-tile-inert')).toBeNull()
    expect(screen.getByLabelText('Refresh tile')).toBeInTheDocument()
    expect(artifact).toHaveBeenCalled()
    expect(refreshTile).toHaveBeenCalled()
  })
})
