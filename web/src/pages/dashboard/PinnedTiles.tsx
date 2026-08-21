import { useCallback, useEffect, useMemo, useState } from 'react'
import { reportActionFailure } from '../../app/reportingWrite'
import { Check, X, RefreshCw, Sparkles } from 'lucide-react'
import { api, type DashboardView, type DashboardTile, type Artifact, type TileRefreshRow } from '../../lib/api'
import { useQuery, invalidateKeys } from '../../lib/data'
import { useVisiblePoll } from '../../lib/useVisiblePoll'
import { relPast } from '../schedule/scheduleMeta'
import { WidgetFrame } from '../../ui/widget/WidgetFrame'
import { LiquidShape } from '../../ui/motion'
import { SquareIconButton } from '../../ui/SquareIconButton'
import { costLabel, isLive, lastRefreshFailed, sourceChips } from './tileFreshness'

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
  const { data: views, refresh } = useQuery<DashboardView[]>(
    VIEWS_CACHE_KEY, () => api.dashboardViews().catch(() => [] as DashboardView[]), { persist: true },
  )
  const tiles = useMemo(() => artifactTiles(views), [views])

  const resolve = useCallback((ref: string, keep: boolean) => {
    api.resolveTile('overview', { ref, keep }).then(() => {
      invalidateKeys(VIEWS_CACHE_KEY)
      refresh()
    }).catch(() => {})
  }, [refresh])

  // Empty registry ⇒ render nothing (the page stays byte-identical to today).
  if (tiles.length === 0) return null

  return (
    <section className="flex min-w-0 flex-col gap-s" data-testid="pinned-tiles">
      <div className="flex items-center gap-s">
        <Sparkles size={14} className="shrink-0 text-on-surface-low" />
        {/* h2 to match the dashboard's other sections — see the note on DashboardPage's
            `Section`. This one is its own component, so it has to be kept in step by hand. */}
        <h2 data-type="label-l" className="text-on-surface-var">Pinned</h2>
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

// ── Freshness + per-source chips (§2.4) ──────────────────────────────────────
// One dot per data node, its title carrying the source's own error. `title` is the hover
// affordance AND the accessible name here on purpose: a coloured dot with no text is invisible
// to a screen reader, and colour alone would carry the ok/error distinction.
const TONE_CLASS = {
  ok: 'bg-success',
  error: 'bg-error',
  pending: 'bg-outline',
} as const

function FreshnessBar({ tile, row }: { tile: DashboardTile; row: TileRefreshRow | undefined }) {
  const chips = sourceChips(tile, row)
  const failed = lastRefreshFailed(row)
  const cost = costLabel(row)
  return (
    <div className="flex min-w-0 items-center gap-xs" data-testid="tile-freshness">
      <a
        href={api.tileLedgerHref('overview', tile.ref)}
        target="_blank"
        rel="noreferrer"
        data-type="label-s"
        data-testid="tile-ledger-link"
        title={
          row?.ts
            ? `Refreshed ${row.ts}${cost ? ` · ${cost}` : ''}${row.error ? ` · ${row.error}` : ''} — open the ledger row`
            : 'Never refreshed — open the ledger row'
        }
        className={`truncate underline decoration-dotted underline-offset-2 ${failed ? 'text-error' : 'text-on-surface-low'}`}
      >
        {row?.ts ? relPast(row.ts) : 'never refreshed'}
      </a>
      {chips.map((c) => (
        <span
          key={c.id}
          role="img"
          aria-label={c.title}
          title={c.title}
          data-testid={`tile-source-${c.tone}`}
          className={`size-2 shrink-0 rounded-full ${TONE_CLASS[c.tone]}`}
        />
      ))}
    </div>
  )
}

/** Shape-character amplitude for the tile's composure silhouette (FLUID-MOTION §S2
 *  T2.2 / atom FM-4). A PLAIN number on purpose: `LiquidShape` multiplies through
 *  `expr()` itself, so passing `expr(1)` here would scale the expressiveness knob
 *  twice. Small, because this is decoration beside a title, not a hero graphic.
 *  Exported so the call-site test can render the primitive standalone at the SAME
 *  amplitude and pin the state→silhouette mapping by exact geometry (same reason
 *  `artifactTiles` above is exported). */
export const TILE_COMPOSURE_INTENSITY = 0.5

// ── One tile: an artifact rendered live, with a header + (for proposals) chips ──
function PinnedTile({ tile, onResolve }: { tile: DashboardTile; onResolve: (ref: string, keep: boolean) => void }) {
  const slug = tile.ref.slice('artifact:'.length)
  const isProposal = tile.added_by === 'agent'
  const live = isLive(tile)

  // Cached SWR paint of the artifact body — instant on revisit, revalidates behind.
  // `revalidating` is the in-flight leg and it is what makes the composure silhouette below
  // depict something a user can actually see happen: MEASURED, body-presence alone never
  // transitions in practice — on localhost the artifact resolves before the silhouette
  // first mounts (0 of 276 sampled frames had the tile in its loading state), so the blob
  // would be a shape that is always already settled. Revalidation keeps the cached body
  // painted while it is true, so the two together read as "settled, and not
  // currently being re-read".
  const { data: artifact, refresh, revalidating: reReading } = useQuery<Artifact | null>(
    `dashboard:tile:${slug}`, () => api.artifact(slug).catch(() => null), { persist: true },
  )
  const [reloadKey, setReloadKey] = useState(0)
  useEffect(() => { if (reloadKey) refresh() }, [reloadKey, refresh])

  // The tile's newest ledger row — the only source of the freshness stamp and the chips.
  const [row, setRow] = useState<TileRefreshRow | undefined>(undefined)

  // The unattended leg: ask the gateway to refresh. It is TTL-GATED there, so this poll is a
  // "is it due?" check rather than a fetch — a tile with a 15-minute TTL polled every minute
  // still re-runs its data workflow four times an hour, not sixty. It also carries the tile's
  // CURRENT row back on a within-TTL answer, which is why one call feeds both the cadence and
  // the header. A manual tile polls NOTHING: it has no bound workflow and no ledger row.
  const tick = useCallback(() => {
    api.refreshTile('overview', { ref: tile.ref })
      .then((r) => { if (r.row?.ts) setRow(r.row); if (r.refreshed) refresh() })
      .catch(() => {})
  }, [tile.ref, refresh])
  useVisiblePoll(tick, live ? 60_000 : null)

  // The button FORCES past the TTL for a live tile (a human asked) and is a plain reload for a
  // static one.
  const onRefreshClick = useCallback(() => {
    if (!live) { setReloadKey((k) => k + 1); return }
    // A human asked, so a failure is theirs to know about. The 60s `tick` above deliberately keeps its
    // silent catch: nobody asked for it, and one toast per failed poll would be a defect of its own.
    api.refreshTile('overview', { ref: tile.ref, force: true })
      .then((r) => { if (r.row?.ts) setRow(r.row); refresh() })
      .catch(reportActionFailure('refresh this tile'))
  }, [live, tile.ref, refresh])

  // The body the tile currently has, if any. ONE expression feeds both the composure
  // silhouette in the header and the frame at the bottom, so the two cannot disagree
  // about whether this tile has settled.
  const body = artifact?.content

  return (
    <div className="flex min-w-0 flex-col gap-xs rounded-lg border border-outline-variant/40 bg-surface-low/60 p-s">
      <div className="flex items-center gap-s">
        {/* The tile's COMPOSURE as a silhouette: `blob` (unsettled, organic) while the
            body has not painted yet, morphing to `squircle` (settled, deliberate) once
            it has — the primitive's own vocabulary for loading→loaded. `active` is
            therefore read as "settled", which is the polarity that lets `from`/`to`
            stay in the primitive's stated direction (`from` = unsettled resting form)
            instead of inverting it at the call site.

            Settled means "there is a body AND nothing is re-reading it". Body-presence
            alone was the first version and it was MEASURED not to transition at all: on
            localhost the artifact resolves before the silhouette first mounts (0 of 276
            sampled frames caught the tile in its loading state), so the blob was a shape
            that had always already settled. the in-flight read is what a user can see happen —
            press Refresh and the silhouette unsettles while the read is in flight.

            Hosted HERE, in the always-rendered header row, and deliberately NOT inside
            the body ternary below: a morph is only observable if its host survives the
            state change, and that ternary swaps its whole subtree at exactly the moment
            the state flips — a silhouette placed inside it would be created
            already-settled and would never animate. `pinnedTileLiquid.test.tsx` fails
            if it is moved there.

            Decorative by the primitive's contract (aria-hidden + pointer-events-none,
            both applied by the primitive). The state it depicts is carried in TEXT by
            this header's own `FreshnessBar` — its labelled source chips and ledger
            stamp — and by the "Loading tile…" line below, so the silhouette is never
            the only place a user could learn something. */}
        <LiquidShape
          from="blob"
          to="squircle"
          active={Boolean(body) && !reReading}
          intensity={TILE_COMPOSURE_INTENSITY}
          className="size-4 shrink-0"
        />
        <span data-type="label-m" className="min-w-0 flex-1 truncate text-on-surface-var">
          {artifact?.name || slug}
        </span>
        {live && <FreshnessBar tile={tile} row={row} />}
        {isProposal && (
          <span data-type="label-s" className="inline-flex items-center gap-1 rounded-pill bg-primary-container px-2 py-0.5 text-on-primary-container">
            <Sparkles size={11} /> Proposed
          </span>
        )}
        <SquareIconButton label="Refresh tile" onClick={onRefreshClick}>
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
      {body
        ? <WidgetFrame html={body} title={artifact?.name || slug} slug={slug} />
        : <div data-type="body-s" className="px-s py-l text-center text-on-surface-low">Loading tile…</div>}
    </div>
  )
}
