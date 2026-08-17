import { describe, it, expect } from 'vitest'
import { costLabel, isLive, lastRefreshFailed, sourceChips } from './tileFreshness'
import type { DashboardTile, TileRefreshRow } from '../../lib/api'

// ── AMBIENT-SURFACES AS-2 §2.4: the header derives from ONE ledger row ───────
// The cases that matter are the ones a rendered component makes awkward to reach: never
// refreshed, refreshed-but-a-source-failed, and a binding whose node list moved on since the
// last row was written.

const tile = (over: Partial<DashboardTile['refresh']> = {}): DashboardTile => ({
  ref: 'artifact:sales',
  size: 'm',
  order: 0,
  added_by: 'user',
  refresh: {
    mode: 'ttl',
    ttl_secs: 900,
    skeleton: 'sales-skeleton',
    data: [{ id: 'health', provider: 'knowledge-health', config: {} }],
    ...over,
  },
})

const row = (over: Partial<TileRefreshRow> = {}): TileRefreshRow => ({
  kind: 'tile_refreshed',
  event_id: 'overview__sales-evt-1',
  ts: '2026-08-17T09:00:00Z',
  ok: true,
  tokens: 0,
  cost_usd: 0,
  duration_ms: 12,
  nodes: [{ id: 'health', provider: 'knowledge-health', ok: true, error: '', duration_ms: 3 }],
  ...over,
})

describe('isLive — only a bound tile claims freshness', () => {
  it('a manual tile is not live (a permanently grey dot reads as broken)', () => {
    expect(isLive(tile({ mode: 'manual' }))).toBe(false)
  })

  it('a ttl tile with no skeleton is not live — there is nothing to render', () => {
    expect(isLive(tile({ skeleton: '' }))).toBe(false)
  })

  it('a ttl tile over a skeleton is live', () => {
    expect(isLive(tile())).toBe(true)
  })
})

describe('sourceChips — one dot per DATA NODE, keyed off the binding', () => {
  it('an ok outcome is green and names its provider', () => {
    const chips = sourceChips(tile(), row())
    expect(chips).toHaveLength(1)
    expect(chips[0].tone).toBe('ok')
    expect(chips[0].title).toContain('knowledge-health')
  })

  it('a failed source is red and carries its own error on hover', () => {
    const chips = sourceChips(
      tile(),
      row({
        ok: false,
        nodes: [{ id: 'health', provider: 'knowledge-health', ok: false, error: 'store unreachable', duration_ms: 1 }],
      }),
    )
    expect(chips[0].tone).toBe('error')
    expect(chips[0].title).toContain('store unreachable')
  })

  it('a failed source with no message still explains itself', () => {
    const chips = sourceChips(
      tile(),
      row({ nodes: [{ id: 'health', provider: 'x', ok: false, error: '', duration_ms: 0 }] }),
    )
    expect(chips[0].title).toContain('failed with no reason given')
  })

  it('a node added since the last refresh renders PENDING, never vanishes', () => {
    // Iterating the ROW instead of the binding would drop this source from the header and the
    // user would read one green dot as "all my sources are healthy".
    const t = tile({
      data: [
        { id: 'health', provider: 'knowledge-health', config: {} },
        { id: 'brand-new', provider: 'knowledge-retrieve', config: {} },
      ],
    })
    const chips = sourceChips(t, row())
    expect(chips.map((c) => c.id)).toEqual(['health', 'brand-new'])
    expect(chips[1].tone).toBe('pending')
  })

  it('with no row at all every source is pending (never refreshed)', () => {
    expect(sourceChips(tile(), undefined).map((c) => c.tone)).toEqual(['pending'])
  })

  it('every chip has a non-empty title (a dot with no explanation is unactionable)', () => {
    const rows = [undefined, row(), row({ nodes: [] })]
    for (const r of rows) {
      for (const chip of sourceChips(tile(), r)) expect(chip.title.length).toBeGreaterThan(0)
    }
  })
})

describe('costLabel — honest zero over an invented blank', () => {
  it('states a zero-token refresh rather than hiding it', () => {
    expect(costLabel(row())).toBe('0 tokens · 12 ms')
  })

  it('renders nothing when there is no row to report', () => {
    expect(costLabel(undefined)).toBe('')
  })

  it('reports a non-zero cost when one is recorded', () => {
    expect(costLabel(row({ tokens: 240, duration_ms: 900 }))).toBe('240 tokens · 900 ms')
  })
})

describe('lastRefreshFailed — the red chip over a last-good body', () => {
  it('a failed row reddens the chip', () => {
    expect(lastRefreshFailed(row({ ok: false }))).toBe(true)
  })

  it('never refreshed is NOT a failure (nothing has gone wrong yet)', () => {
    expect(lastRefreshFailed(undefined)).toBe(false)
  })

  it('an ok row is not a failure', () => {
    expect(lastRefreshFailed(row())).toBe(false)
  })
})
