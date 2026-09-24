import type { DashboardTile, TileNodeOutcome, TileRefreshRow } from '../../lib/api'

// ── Freshness + per-source status, derived from ONE ledger row ───────────────
// AMBIENT-SURFACES §2.4. The header's whole content comes from the tile's newest
// `tile_refreshed` row, so "the tile says fresh" and "the ledger says the fetch failed"
// cannot disagree — that divergence is how a silently-stale panel happens, which is the
// complaint this section exists to kill.
//
// Pure functions, in their own module, because the interesting cases are the ones a rendered
// component makes awkward to reach: never refreshed, refreshed-but-a-source-failed, and a
// binding whose node list has changed since the last row was written.

export type ChipTone = 'ok' | 'error' | 'pending'

export interface SourceChip {
  id: string
  tone: ChipTone
  /** What the dot says on hover. Never empty — a chip with no explanation is a dot the user
   *  cannot act on. */
  title: string
}

/** Is this tile bound to a data workflow at all? An unbound (manual) tile shows no chips —
 *  there is nothing to be stale about, and a permanently grey dot reads as broken. */
export function isLive(tile: DashboardTile): boolean {
  return tile.refresh?.mode === 'ttl' && Boolean(tile.refresh?.skeleton)
}

/** One chip per DATA NODE in the binding, in binding order.
 *
 *  Keyed off the BINDING, not off the row: a node added since the last refresh has no outcome
 *  yet and must render as pending rather than vanish. The opposite (iterating the row) would
 *  silently drop the new source from the header — the user would see three green dots and
 *  believe all three of their sources were healthy.
 */
export function sourceChips(tile: DashboardTile, row: TileRefreshRow | undefined): SourceChip[] {
  const nodes = tile.refresh?.data ?? []
  const byId = new Map<string, TileNodeOutcome>()
  for (const o of row?.nodes ?? []) byId.set(o.id, o)
  return nodes.map((n) => {
    const outcome = byId.get(n.id)
    if (!outcome) return { id: n.id, tone: 'pending' as ChipTone, title: `${n.id} — not refreshed yet` }
    if (outcome.ok) return { id: n.id, tone: 'ok' as ChipTone, title: `${n.id} — ok (${n.provider})` }
    return {
      id: n.id,
      tone: 'error' as ChipTone,
      title: `${n.id} — ${outcome.error || 'failed with no reason given'}`,
    }
  })
}

/** The header's cost line. Honest zero over an invented blank: a refresh that really cost
 *  nothing is the POINT of the layout/data split, so it is stated. */
export function costLabel(row: TileRefreshRow | undefined): string {
  if (!row || row.ts === undefined) return ''
  const tokens = row.tokens ?? 0
  const ms = row.duration_ms ?? 0
  return tokens === 0 ? `0 tokens · ${ms} ms` : `${tokens} tokens · ${ms} ms`
}

/** Did the last refresh fail? Drives the RED chip while the last-good body stays painted. */
export function lastRefreshFailed(row: TileRefreshRow | undefined): boolean {
  return Boolean(row && row.ts !== undefined && row.ok === false)
}
