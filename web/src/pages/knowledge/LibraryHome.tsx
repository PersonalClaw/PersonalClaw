import { useMemo, useState } from 'react'
import { BookOpen, Check, Clock, Layers, Star } from 'lucide-react'
import { ProgressRing } from '../../ui/ProgressRing'
import { Button } from '../../ui/Button'
import { IconButton } from '../../ui/IconButton'
import { InlineError } from '../../ui/InlineError'
import { ListRow, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { api, type KnowledgeItem, type KnowledgeLibraryHome } from '../../lib/api'
import { useQuery, invalidateKeys } from '../../lib/data'
import { relTime, typeLabel } from './knowledgeMeta'
import { clearReadingPosition, readingPositions } from './readingPosition'

/** The library HOME (KNOWLEDGE-LIBRARY S3, T3.3) — the `#/knowledge?view=home` lens.
 *
 *  Four shelves, answering the four questions a library gets asked before any search does:
 *  what is on my shelves, what am I part-way through, what did I just add, and what did I keep.
 *  It is a GLANCE surface: each shelf carries eight rows and hands off to the Library lens
 *  (which paginates, filters and searches properly) rather than growing into a second one.
 *
 *  🔑 ONE READ, so "empty" and "broken" cannot be the same pixels. `GET /api/knowledge/
 *  library-home` returns all four shelves, so a failure is ONE failure this surface can name —
 *  four independent fetches would put three populated shelves beside a fourth that is blank for
 *  a reason the user has no way to see. Every shelf that can be empty says so IN ITS OWN WORDS;
 *  a read that failed says "Couldn't load your library" and offers a retry; a read that failed
 *  over a cached copy says THAT, above the shelves. None of the three is a blank region.
 *  (`api.knowledgeLibraryHome` deliberately carries no `.catch(() => …)` for the same reason —
 *  see its comment. A library with nothing in it at all is answered one level up, by the page's
 *  own "Knowledge base is empty" state, rather than by four empty sentences stacked.)
 *
 *  🪤 COMPOSABLE, STANDALONE TODAY. The atom asks for a surface that consumes AMBIENT-SURFACES'
 *  tile registry "if landed, standalone otherwise". No registry exists on `main` (no
 *  `tileRegistry`/`AmbientTile` symbol anywhere in `web/src` or core), so this is the standalone
 *  form — and `Shelf` is the seam: each shelf is a self-contained titled section over one array,
 *  so registering the four as tiles later is a wiring change here, not a rewrite of the rows.
 */
export function LibraryHome({ onOpenItem, onOpenReader, onOpenCollection, onShowCuration }: {
  /** Open an item (the library's own row destination). */
  onOpenItem: (id: string) => void
  /** Open the item's READING view — where the persisted position is resumed. */
  onOpenReader: (id: string) => void
  /** Open a shelf in the Library lens. */
  onOpenCollection: (id: string) => void
  /** Switch to the Library lens with a curation filter pre-applied — the "see all" road out of
   *  a shelf that only shows its first eight. */
  onShowCuration: (filter: 'favorites' | 'reading') => void
}) {
  const { data, error, loading, refresh } = useQuery<KnowledgeLibraryHome>(
    'knowledge:library-home', () => api.knowledgeLibraryHome())
  // A write that FAILED must not read as one that worked: `markRead` reports instead of
  // swallowing, and leaves the row (and its resume point) exactly where they were.
  const [writeErr, setWriteErr] = useState('')

  // Reading POSITIONS are per-device (see readingPosition.ts) while shelf MEMBERSHIP is server
  // truth, so the two are joined here rather than in the payload. Re-read whenever the shelves
  // change: coming back from the reader is a refetch, and the position moved while you read.
  const positions = useMemo(() => readingPositions(), [data])

  if (loading && !data) return <ListSkeleton rows={5} what="library" />
  if (!data) return <LoadError what="library" error={error} onRetry={refresh} />

  // Most-recently-READ first, which is not an order the server can produce: it sorts the shelf by
  // the item's `updated_at`, and "where I left off last" is a local fact. Items with no local
  // position keep the server's order, after the ones that have one.
  const reading = [...data.continue_reading].sort((a, b) =>
    (positions[b.id]?.ts ?? 0) - (positions[a.id]?.ts ?? 0))

  const markRead = async (it: KnowledgeItem) => {
    const name = it.title || it.url_title || 'that item'
    setWriteErr('')
    try {
      await api.setKnowledgeReadState(it.id, 'read')
    } catch {
      setWriteErr(`Couldn't mark “${name}” read — it is still where you left it.`)
      return
    }
    // Finishing an article is the ONE way off the continue-reading shelf (the reader does not
    // auto-complete — scrolling to the bottom to check a reference is not being done). The resume
    // point goes with it, or the row would come back mid-article if it is ever re-opened.
    clearReadingPosition(it.id)
    // The Library lens holds the same rows with the old read_state, so invalidate by PREFIX: its
    // key carries the active query and shelf, and a home that only refreshed itself would send
    // the user to a Library tab still showing "reading".
    invalidateKeys('knowledge:items', true)
    refresh()
  }

  return (
    <div className="flex min-w-0 flex-col gap-2xl">
      {/* A stale-but-painted surface still says the last read failed. Without this, an error with
          a cached value renders as a perfectly confident home. */}
      {error ? (
        <InlineError icon onRetry={refresh}>Couldn't refresh your library — showing the last copy.</InlineError>
      ) : null}
      {writeErr ? <InlineError icon onDismiss={() => setWriteErr('')}>{writeErr}</InlineError> : null}

      <Shelf id="home-collections" icon={Layers} title="Shelves" count={data.collections.length}
        empty="No shelves yet — group items into one from the Library lens's shelf rail.">
        <ul className="flex flex-wrap gap-s">
          {data.collections.map((c) => (
            <li key={c.id}>
              {/* The count rides IN the name, not only beside it: "Recipes, 12 items" is what the
                  shelf IS, and a number announced as loose text after a button belongs to nothing.
                  `count_capped` renders "200+" — the resolve cap is reported, never passed off as
                  a total. */}
              <Button variant="secondary" size="xs" onClick={() => onOpenCollection(c.id)}
                ariaLabel={`${c.name || 'Untitled shelf'}, ${c.count}${c.count_capped ? ' or more' : ''} item${c.count === 1 && !c.count_capped ? '' : 's'}`}>
                {c.name || 'Untitled shelf'}
                <span className="text-on-surface-low tabular-nums">{c.count}{c.count_capped ? '+' : ''}</span>
              </Button>
            </li>
          ))}
        </ul>
      </Shelf>

      <Shelf id="home-reading" icon={BookOpen} title="Continue reading" count={reading.length}
        empty="Nothing in progress. Open something and it will wait here at the paragraph you stopped on."
        action={reading.length > 0 ? { label: 'View all in progress', onClick: () => onShowCuration('reading') } : undefined}>
        <ItemRows items={reading} onOpen={onOpenItem} subtitle={(it) => {
          const pos = positions[it.id]
          return pos ? `${Math.round(pos.pct * 100)}% in · ${typeLabel(it)}` : `Not started · ${typeLabel(it)}`
        }} lead={(it) => (
          <ProgressRing pct={positions[it.id]?.pct ?? 0} tone="var(--color-primary)" size={26}
            label={`Reading progress on ${it.title || it.url_title || 'untitled item'}: ${Math.round((positions[it.id]?.pct ?? 0) * 100)}%`} />
        )} actions={(it) => (
          <>
            <IconButton icon={BookOpen} size={32} iconSize={16}
              label={`Resume reading: ${it.title || it.url_title || 'untitled item'}`} title="Resume reading"
              onClick={(e) => { e.stopPropagation(); onOpenReader(it.id) }} />
            <IconButton icon={Check} size={32} iconSize={16}
              label={`Mark read: ${it.title || it.url_title || 'untitled item'}`} title="Mark read"
              onClick={(e) => { e.stopPropagation(); void markRead(it) }} />
          </>
        )} />
      </Shelf>

      <Shelf id="home-recent" icon={Clock} title="Recently added" count={data.recently_added.length}
        empty="Nothing added yet — anything you add, upload or let a watched source pull in lands here first.">
        <ItemRows items={data.recently_added} onOpen={onOpenItem}
          subtitle={(it) => [typeLabel(it), relTime(it.created_at)].filter(Boolean).join(' · ')} />
      </Shelf>

      <Shelf id="home-favorites" icon={Star} title="Favorites" count={data.favorites.length}
        empty="No favorites yet — star an item and it lands here."
        action={data.favorites.length > 0 ? { label: 'View all favorites', onClick: () => onShowCuration('favorites') } : undefined}>
        <ItemRows items={data.favorites} onOpen={onOpenItem}
          subtitle={(it) => [typeLabel(it), relTime(it.updated_at)].filter(Boolean).join(' · ')} />
      </Shelf>
    </div>
  )
}

/** One titled shelf. The section idiom of `pages/dashboard/MissionControl`'s lanes —
 *  `<section aria-labelledby>` + an `h2` heading row carrying the count as TEXT + a hairline —
 *  because a shelf is the same shape of thing and inventing a second one would be drift.
 *
 *  The EMPTY sentence is required, not optional: at least one of these four shelves is empty on
 *  any given day, and a blank region under a heading is the failure this prop exists to prevent.
 */
function Shelf({ id, icon: Icon, title, count, empty, action, children }: {
  id: string
  icon: typeof Star
  title: string
  count: number
  empty: string
  action?: { label: string; onClick: () => void }
  children?: React.ReactNode
}) {
  return (
    <section aria-labelledby={`${id}-heading`} className="flex min-w-0 flex-col gap-s">
      <div className="flex items-center gap-s">
        <Icon size={15} className="text-primary shrink-0" aria-hidden />
        {/* h2 to match this repo's section idiom (MissionControl's lanes, DashboardPage's
            `Section`): the live tree stays H1 › H2 … — flat but skip-free, so heading-order
            holds. h3 would mean joining `discoverHeadingLevel`'s closed inventory. */}
        <h2 id={`${id}-heading`} data-type="label-l" className="text-on-surface-var">{title}</h2>
        <span data-type="label-s" className="text-on-surface-low tabular-nums">{count}</span>
        <span className="h-px flex-1 bg-outline-variant/40" />
        {action && (
          <Button variant="ghost-accent" size="xs" onClick={action.onClick}>{action.label}</Button>
        )}
      </div>
      {count === 0 ? <p data-type="body-s" className="text-on-surface-low">{empty}</p> : children}
    </section>
  )
}

/** A shelf's rows. `ui/ListRow` owns the tab stop, the accessible name and the hover/press
 *  motion, so a shelf row behaves exactly like a library row — and its nested action buttons
 *  stay real siblings of the row's overlay button rather than children of it
 *  (`nested-interactive`), which is why each one calls `stopPropagation` for itself. */
function ItemRows({ items, onOpen, subtitle, lead, actions }: {
  items: KnowledgeItem[]
  onOpen: (id: string) => void
  subtitle: (it: KnowledgeItem) => string
  lead?: (it: KnowledgeItem) => React.ReactNode
  actions?: (it: KnowledgeItem) => React.ReactNode
}) {
  return (
    <ul className="flex min-w-0 flex-col gap-s">
      {items.map((it, i) => {
        const name = it.title || it.url_title || '(untitled)'
        return (
          <li key={it.id} className="min-w-0">
            <ListRow index={i} label={name} onClick={() => onOpen(it.id)}>
              {lead?.(it)}
              <div className="min-w-0 flex-1">
                <p className="truncate text-on-surface" data-type="label-m">{name}</p>
                <p className="truncate text-on-surface-low" data-type="body-s">{subtitle(it)}</p>
              </div>
              {actions && <div className="flex shrink-0 items-center gap-1">{actions(it)}</div>}
            </ListRow>
          </li>
        )
      })}
    </ul>
  )
}
