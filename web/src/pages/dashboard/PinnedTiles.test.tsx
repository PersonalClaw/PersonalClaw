import { describe, it, expect } from 'vitest'
import { artifactTiles } from './PinnedTiles'
import type { DashboardView, DashboardTile } from '../../lib/api'

// ── AMBIENT-SURFACES AS-1: the byte-identical-safety property ────────────────
// artifactTiles() is what PinnedTiles renders. An EMPTY registry (or a preset with
// only core widgets) must yield ZERO artifact tiles, so the band renders null and the
// dashboard is byte-identical to today's fixed layout. The tile schema carries a ref +
// size + order + added_by and NEVER a coordinate.

// AS-2 added `refresh` — a DATA seam (where a tile's content comes from), never a spatial one.
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
