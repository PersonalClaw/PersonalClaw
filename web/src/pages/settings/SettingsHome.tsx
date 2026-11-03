import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Eyebrow } from '../../ui/Eyebrow'
import { ResultAnnouncement } from '../../ui/ListControls'
import { SearchX } from 'lucide-react'
import { SearchField } from '../../ui/SearchField'
import { SETTINGS_WIDGETS, type SettingsWidget } from './settingsWidgets'
import { columnsFor, packRows } from './packRows'

const GAP = 12        // px between cards, across and down
const GRID_MAX = 1200 // the grid's widest; past it, three columns of ~390 px stay centred

/** Settings home — one card per subpage, each surfacing its most essential info; clicking opens the
 *  subpage. A full-width search filters the cards (label + description + the data each card
 *  surfaces) and highlights matches.
 *
 *  Layout (#5897): row bands. Each group is its heading and then its cards, in a grid whose column
 *  count follows the space it has (`columnsFor`) and whose spans `packRows` picks so every row ends
 *  at the right edge. It used to pack whole groups into columns, which left the columns over a
 *  thousand pixels apart; a group now reads left to right, top to bottom, in its own source order,
 *  which is also the order focus moves in. Cards in a row stretch to its tallest. */
export function SettingsHome({ go }: { go: (id: string) => void }) {
  const [query, setQuery] = useState('')
  const q = query.trim()

  // Group the widgets by category, preserving registry order.
  const groups = useMemo(() => {
    const order: string[] = []
    const byGroup: Record<string, SettingsWidget[]> = {}
    for (const w of SETTINGS_WIDGETS) {
      if (!byGroup[w.group]) { byGroup[w.group] = []; order.push(w.group) }
      byGroup[w.group].push(w)
    }
    return order.map((g) => ({ title: g, items: byGroup[g] }))
  }, [])

  // Each widget reports whether it matches the query (it owns the hook producing
  // its search text) so we can show a global "no matches" state + hide empty groups.
  const [matches, setMatches] = useState<Record<string, boolean>>({})
  const anyMatch = q === '' || Object.values(matches).some(Boolean)
  const reportMatch = useCallback((id: string, m: boolean) => {
    setMatches((prev) => (prev[id] === m ? prev : { ...prev, [id]: m }))
  }, [])
  const gridRef = useRef<HTMLDivElement>(null)
  const columns = useColumns(gridRef)

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div ref={gridRef} className="mx-auto w-full px-2xl" style={{ maxWidth: `calc(${GRID_MAX}px + 2 * var(--spacing-2xl))` }}>
          {/* Search across all settings + the data on every card. Pinned to the top
              of the scroll area (its own surface bg masks cards sliding under it),
              with a constant gap below so the first row never crowds it. */}
          <div className="sticky top-0 z-20 -mx-2xl bg-canvas px-2xl pt-2xl pb-l">
            {/* `data-tour="settings"` — the product tour's last stop.
                On the SEARCH, not on the page: the bento is as tall and wide as the viewport,
                so ringing the whole thing dims nothing and the card has nowhere to sit that
                is not on top of it. The search is also what the stop's copy is about. */}
            <div data-tour="settings" className="mx-auto w-full" style={{ maxWidth: 720 }}>
              <SearchField value={query} onChange={setQuery} autoFocus
                placeholder="Search settings — name, description, or any value inside"
                ariaLabel="Search settings" />
              {/* 🔑 This page holds no filtered array — each widget owns the hook producing its own
                  search text and reports whether it matched. So the count comes from that SAME
                  `matches` map that decides what stays on screen (and that `anyMatch` reads), which is
                  why it cannot disagree with the page: both are the one source. */}
              <ResultAnnouncement count={Object.values(matches).filter(Boolean).length} noun="settings"
                active={q !== ''} />
            </div>
          </div>

          {!anyMatch && (
            <div className="flex flex-col items-center gap-2 py-2xl text-center text-on-surface-low">
              <SearchX size={28} className="opacity-50" />
              <p data-type="body-m">No settings match “{q}”.</p>
            </div>
          )}

          <div className="pb-2xl" style={anyMatch ? undefined : { display: 'none' }}>
            {groups.map((g) => (
              <Group key={g.title} title={g.title} items={g.items} query={q} go={go} onMatch={reportMatch}
                matches={matches} columns={columns} />
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

/** The column count for the grid inside `ref`, tracking its width. The ref sits on the page's own
 *  content box (search and grid), which never hides, so a search that hides every card does not
 *  read as a zero-width grid. */
function useColumns(ref: React.RefObject<HTMLDivElement | null>): number {
  const [columns, setColumns] = useState(1)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const apply = () => {
      const pad = parseFloat(getComputedStyle(el).paddingLeft || '0') + parseFloat(getComputedStyle(el).paddingRight || '0')
      const n = columnsFor(el.clientWidth - pad)
      setColumns((prev) => (prev === n ? prev : n))
    }
    apply()
    const ro = new ResizeObserver(apply)
    ro.observe(el)
    return () => ro.disconnect()
  }, [ref])
  return columns
}

/** The span a card asks for. Every card asks for one column until a card says otherwise. */
const SPAN = (_w: SettingsWidget) => 1

/** One group: its heading, then its cards in a row-band grid. The spans are packed over the cards a
 *  search leaves visible, so a filtered group still ends its rows even; hidden when none match. */
function Group({ title, items, query, go, onMatch, matches, columns }: {
  title: string; items: SettingsWidget[]; query: string; go: (id: string) => void
  onMatch: (id: string, matched: boolean) => void; matches: Record<string, boolean>; columns: number
}) {
  const headingId = useId()
  // A card not yet reported counts as visible: it renders until its own cell says otherwise.
  const visible = items.filter((w) => query === '' || matches[w.id] !== false)
  const spans = packRows(visible.map(SPAN), columns)
  const spanOf = new Map(visible.map((w, i) => [w.id, spans[i]]))
  return (
    <section aria-labelledby={headingId} className="mb-l" style={visible.length ? undefined : { display: 'none' }}>
      <Eyebrow as="h2" id={headingId} className="mb-2 px-1">{title}</Eyebrow>
      <div className="grid" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`, gap: GAP }}>
        {items.map((w) => <Cell key={w.id} widget={w} query={query} go={go} onMatch={onMatch} span={spanOf.get(w.id) ?? 1} />)}
      </div>
    </section>
  )
}

/** A bento cell: computes the widget's search text (its own cached hook), decides
 *  whether it matches the query, reports up (effect), and renders it. The widget's
 *  `render()` calls its own hooks, so we ALWAYS render it (and hide a non-match via
 *  CSS) — returning null on a filter miss would change the hook count and crash. */
function Cell({ widget, query, go, onMatch, span }: {
  widget: SettingsWidget; query: string; go: (id: string) => void; onMatch: (id: string, matched: boolean) => void; span: number
}) {
  const text = widget.useSearchText()
  const haystack = `${widget.label} ${widget.description} ${text}`.toLowerCase()
  const matched = query === '' || haystack.includes(query.toLowerCase())
  const reportRef = useRef(onMatch); reportRef.current = onMatch
  useEffect(() => { reportRef.current(widget.id, matched) }, [matched, widget.id])
  // A flex box so the card stretches to its row's height; `min-w-0` so a long value truncates
  // inside its column rather than widening it.
  return <div className="flex min-w-0" style={matched ? { gridColumn: `span ${span}` } : { display: 'none' }}>{widget.render(query, go)}</div>
}
