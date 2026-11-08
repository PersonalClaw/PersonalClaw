import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { PinnedTiles, artifactTiles } from './PinnedTiles'
import type { DashboardView, DashboardTile } from '../../lib/api'
import { invalidateKeys } from '../../lib/data'
import { MD_BLOCK, rendersBlock } from '../../test/markdownSinks'
import { registerBuiltinContentTypes } from '../../ui/content/registerBuiltins'

// The tile a mounted band shows: its kind and body are each test's.
const pinned = vi.hoisted(() => ({ kind: 'markdown', content: '' }))
vi.mock('../../app/appSdk', () => ({ launchChat: () => {}, notify: () => {} }))
vi.mock('../../lib/api', () => ({
  api: {
    dashboardViews: vi.fn(async () => [{ id: 'overview', tiles: [{ ref: 'artifact:notes', size: 'm', order: 0, added_by: 'user',
      refresh: { mode: 'manual', ttl_secs: 0, skeleton: '', data: [] } }] }]),
    artifact: vi.fn(async () => ({ slug: 'notes', name: 'Notes', kind: pinned.kind, content: pinned.content })),
    artifactExists: vi.fn(async () => true),
    createArtifact: vi.fn(async () => ({})),
    deleteArtifact: vi.fn(async () => ({})),
    pinTile: vi.fn(async () => ({})),
    resolveTile: vi.fn(async () => ({})),
    refreshTile: vi.fn(async () => ({ refreshed: false, reason: 'manual', ok: true, nodes: [], row: {} })),
    tileLedgerHref: (viewId: string, ref: string) => `/api/dashboard/views/${viewId}/tiles/refresh?ref=${ref}`,
  },
}))
// The app registers its content types at startup (`main.tsx`); the band reads them through that registry.
registerBuiltinContentTypes()
if (typeof URL.createObjectURL !== 'function') {
  URL.createObjectURL = () => 'blob:pinned-tiles-test'
  URL.revokeObjectURL = () => {}
}

// ── The byte-identical-safety property ───────────────────────────────────────
// artifactTiles() is what PinnedTiles renders. An EMPTY registry (or a preset with
// only core widgets) must yield ZERO artifact tiles, so the band renders null and the
// dashboard is byte-identical to today's fixed layout. The tile schema carries a ref +
// size + order + added_by and NEVER a coordinate.

// The tile's `refresh` is a DATA seam (where a tile's content comes from), never a spatial one.
const tile = (ref: string, order = 0, added_by: 'user' | 'agent' = 'user'): DashboardTile =>
  ({ ref, size: 'm', order, added_by, refresh: { mode: 'manual', ttl_secs: 0, skeleton: '', data: [] } })

const overview = (tiles: DashboardTile[]): DashboardView =>
  ({ id: 'overview', name: 'Overview', nav_pinned: true, preset: true, tiles })

describe('artifactTiles — the additive-over-fixed-layout contract', () => {
  it('an empty registry yields no tiles (dashboard stays byte-identical to today)', () => {
    expect(artifactTiles(undefined)).toEqual([])
    expect(artifactTiles([])).toEqual([])
  })

  it('a preset with only core widgets yields no tiles (core stays hard-imported)', () => {
    const coreOnly = overview([tile('core:hero-pulse'), tile('core:tasks', 1)])
    expect(artifactTiles([coreOnly])).toEqual([])
  })

  it('returns only artifact:<slug> tiles, sorted by order', () => {
    const v = overview([
      tile('core:hero-pulse'),
      tile('artifact:b', 2),
      tile('artifact:a', 1),
    ])
    expect(artifactTiles([v]).map((t) => t.ref)).toEqual(['artifact:a', 'artifact:b'])
  })

  it('agent-proposed rows are surfaced (so the band can render an accept/dismiss chip)', () => {
    const v = overview([tile('artifact:proposed', 0, 'agent')])
    const tiles = artifactTiles([v])
    expect(tiles).toHaveLength(1)
    expect(tiles[0].added_by).toBe('agent')
  })
})

describe('the tile schema carries no coordinates (the retired grid stays retired)', () => {
  it('a tile has exactly ref/size/order/added_by/refresh — no x/y/w/h', () => {
    const t = tile('artifact:x')
    expect(Object.keys(t).sort()).toEqual(['added_by', 'order', 'ref', 'refresh', 'size'])
    for (const banned of ['x', 'y', 'w', 'h', 'col', 'row', 'width', 'height']) {
      expect(banned in t).toBe(false)
      expect(banned in t.refresh).toBe(false)
    }
  })
})

/** The one tile's body: everything under its header row (the band's own "Pinned" heading is ours). */
async function tileBody(): Promise<HTMLElement> {
  const refresh = await screen.findByLabelText('Refresh tile')
  return refresh.closest('div.rounded-lg')!.lastElementChild as HTMLElement
}

describe("a pinned artifact's body renders in the form its kind holds", () => {
  beforeEach(() => {
    invalidateKeys('dashboard:views')
    invalidateKeys('dashboard:tile:notes')
  })

  it('a markdown artifact renders through the content registry, with no frame', async () => {
    Object.assign(pinned, { kind: 'markdown', content: MD_BLOCK })
    render(<PinnedTiles />)
    await waitFor(async () => expect((await tileBody()).querySelector('h2')).not.toBeNull())
    const body = await tileBody()
    expect(rendersBlock(body)).toEqual([])
    expect(body.querySelector('iframe')).toBeNull()
  })

  it('an HTML artifact stays in its sandboxed frame, and nothing in it is rendered as markdown', async () => {
    Object.assign(pinned, { kind: 'html', content: MD_BLOCK })
    render(<PinnedTiles />)
    await waitFor(async () => expect((await tileBody()).querySelector('iframe')).not.toBeNull())
    expect((await tileBody()).querySelector('h1, h2, h3, ul, strong')).toBeNull()
  })
})
