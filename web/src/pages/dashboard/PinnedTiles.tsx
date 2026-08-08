import { useCallback, useEffect, useMemo, useState } from 'react'
import { Check, X, RefreshCw, Sparkles } from 'lucide-react'
import { api, type DashboardView, type DashboardTile, type Artifact } from '../../lib/api'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { WidgetFrame } from '../../ui/widget/WidgetFrame'
import { SquareIconButton } from '../../ui/SquareIconButton'

// ── The Pinned tiles band (AMBIENT-SURFACES §1 / A2-1) ──────────────────────
// The ADDITIVE half of the dashboard-as-views registry: the Overview preset's
// CORE widgets are the fixed DashboardPage layout (rendered by DashboardPage
// itself); this band renders only the `artifact:<slug>` tiles overlaid on the
// active view. So an EMPTY registry renders NOTHING here and the page is
// byte-identical to today — the critical safety property. First-party `core:`
// tiles are never rendered here (they stay hard imports in DashboardPage).
//
// Agent-proposed tiles (added_by:agent) render with an accept/dismiss chip:
// propose-don't-pin — the agent never silently rearranges the user's home.

const VIEWS_CACHE_KEY = 'dashboard:views'

/** Only the artifact tiles of the Overview view, in order. Exported for the
 *  byte-identical-safety test: an empty registry yields NO tiles, so the band
 *  renders null and the page equals today's fixed layout. */
export function artifactTiles(views: DashboardView[] | undefined): DashboardTile[] {
  const overview = views?.find((v) => v.id === 'overview')
  if (!overview) return []
  return overview.tiles
    .filter((t) => t.ref.startsWith('artifact:'))
    .sort((a, b) => a.order - b.order)
}

export function PinnedTiles() {
  // SWR paint through the shared cache (clause 5): the band paints its last-known
  // tiles instantly on revisit, no blank flash, and revalidates in the background.
  const { data: views, refresh } = useCachedData<DashboardView[]>(
    VIEWS_CACHE_KEY, () => api.dashboardViews().catch(() => [] as DashboardView[]), { persist: true },
  )
  const tiles = useMemo(() => artifactTiles(views), [views])

  const resolve = useCallback((ref: string, keep: boolean) => {
    api.resolveTile('overview', { ref, keep }).then(() => {
      invalidateCache(VIEWS_CACHE_KEY)
      refresh()
    }).catch(() => {})
  }, [refresh])

  // Empty registry ⇒ render nothing (the page stays byte-identical to today).
  if (tiles.length === 0) return null

  return (
    <section className="flex min-w-0 flex-col gap-s" data-testid="pinned-tiles">
      <div className="flex items-center gap-s">
        <Sparkles size={14} className="shrink-0 text-on-surface-low" />
        <h3 data-type="label-l" className="text-on-surface-var">Pinned</h3>
        <span className="h-px flex-1 bg-outline-variant/40" />
      </div>
      <div className="grid grid-cols-1 gap-l lg:grid-cols-2">
        {tiles.map((t) => (
          <PinnedTile key={t.ref} tile={t} onResolve={resolve} />
        ))}
      </div>
    </section>
  )
}

// ── One tile: an artifact rendered live, with a header + (for proposals) chips ──
function PinnedTile({ tile, onResolve }: { tile: DashboardTile; onResolve: (ref: string, keep: boolean) => void }) {
  const slug = tile.ref.slice('artifact:'.length)
  const isProposal = tile.added_by === 'agent'

  // Cached SWR paint of the artifact body — instant on revisit, revalidates behind.
  const { data: artifact, refresh } = useCachedData<Artifact | null>(
    `dashboard:tile:${slug}`, () => api.artifact(slug).catch(() => null), { persist: true },
  )
  const [reloadKey, setReloadKey] = useState(0)
  useEffect(() => { if (reloadKey) refresh() }, [reloadKey, refresh])

  return (
    <div className="flex min-w-0 flex-col gap-xs rounded-lg border border-outline-variant/40 bg-surface-low/60 p-s">
      <div className="flex items-center gap-s">
        <span data-type="label-m" className="min-w-0 flex-1 truncate text-on-surface-var">
          {artifact?.name || slug}
        </span>
        {isProposal && (
          <span data-type="label-s" className="inline-flex items-center gap-1 rounded-pill bg-primary/15 px-2 py-0.5 text-primary">
            <Sparkles size={11} /> Proposed
          </span>
        )}
        <SquareIconButton label="Refresh tile" onClick={() => setReloadKey((k) => k + 1)}>
          <RefreshCw size={12} />
        </SquareIconButton>
        {isProposal ? (
          <>
            <SquareIconButton label="Accept — keep on dashboard" onClick={() => onResolve(tile.ref, true)}>
              <Check size={13} />
            </SquareIconButton>
            <SquareIconButton label="Dismiss proposal" onClick={() => onResolve(tile.ref, false)}>
              <X size={13} />
            </SquareIconButton>
          </>
        ) : (
          <SquareIconButton label="Unpin from dashboard" onClick={() => onResolve(tile.ref, false)}>
            <X size={13} />
          </SquareIconButton>
        )}
      </div>
      {artifact?.content
        ? <WidgetFrame html={artifact.content} title={artifact.name || slug} slug={slug} />
        : <div data-type="body-s" className="px-s py-l text-center text-on-surface-low">Loading tile…</div>}
    </div>
  )
}
