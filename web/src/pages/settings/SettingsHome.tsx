import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Search, X, SearchX } from 'lucide-react'
import { SETTINGS_WIDGETS, type SettingsWidget } from './settingsWidgets'

const COL_MIN = 320   // min column width before adding another column
const COL_GAP = 12    // px gap between columns and between stacked cards

/** Settings home — a balanced bento where each subpage is one widget surfacing its
 *  most essential info; clicking opens the subpage. A full-width search filters the
 *  cards (label + description + the data each card surfaces) and highlights matches.
 *  Replaces the old left-nav + 4-card overview.
 *
 *  Layout: section blocks are packed into a width-derived number of fixed-width
 *  columns, shortest-column-first (largest blocks placed first) — so short sections
 *  tuck beneath tall ones and the columns end up near-equal height. Cards size to
 *  their own content, so nothing stretches when the page is wide. */
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

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full px-2xl" style={{ maxWidth: 'var(--content-width)' }}>
          {/* Search across all settings + the data on every card. Pinned to the top
              of the scroll area (its own surface bg masks cards sliding under it),
              with a constant gap below so the first row never crowds it. */}
          <div className="sticky top-0 z-20 -mx-2xl bg-canvas px-2xl pt-2xl pb-l">
            <div className="relative mx-auto w-full" style={{ maxWidth: 720 }}>
              <Search size={16} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-on-surface-low" />
              <input value={query} onChange={(e) => setQuery(e.target.value)} type="search" autoFocus
                placeholder="Search settings — name, description, or any value inside"
                aria-label="Search settings"
                onKeyDown={(e) => { if (e.key === 'Escape' && query) { e.preventDefault(); e.stopPropagation(); setQuery('') } }}
                className="h-11 w-full rounded-pill bg-surface-high pl-10 pr-10 text-[0.95rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
              {query && (
                <button type="button" onClick={() => setQuery('')} aria-label="Clear search"
                  className="absolute right-3 top-1/2 inline-flex size-6 -translate-y-1/2 items-center justify-center rounded-full text-on-surface-low hover:bg-surface-highest hover:text-on-surface"><X size={15} /></button>
              )}
            </div>
          </div>

          {!anyMatch && (
            <div className="flex flex-col items-center gap-2 py-2xl text-center text-on-surface-low">
              <SearchX size={28} className="opacity-50" />
              <p className="text-[0.9rem]">No settings match “{q}”.</p>
            </div>
          )}

          <div className="pb-2xl" style={anyMatch ? undefined : { display: 'none' }}>
            <BalancedColumns
              items={groups.map((g) => ({
                key: g.title,
                // Bigger blocks first → shortest-column packing balances best.
                weight: g.items.length,
                node: <Group title={g.title} items={g.items} query={q} go={go} onMatch={reportMatch} />,
              }))}
            />
          </div>
        </div>
      </div>
    </div>
  )
}

/** Packs blocks into N fixed-width columns, choosing the currently-shortest column
 *  for each (measured pixel heights). Placing the heaviest blocks first yields
 *  near-equal column heights — a true masonry, not greedy single-column fill. */
function BalancedColumns({ items }: { items: { key: string; weight: number; node: React.ReactNode }[] }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const blockRefs = useRef<Map<string, HTMLDivElement>>(new Map())
  const heights = useRef<Map<string, number>>(new Map())
  const [cols, setCols] = useState(1)
  const [layout, setLayout] = useState<string[][]>([])

  // Column count tracks the container width.
  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return
    const apply = () => {
      const w = el.clientWidth
      const n = Math.max(1, Math.min(items.length, Math.floor((w + COL_GAP) / (COL_MIN + COL_GAP))))
      setCols((prev) => (prev === n ? prev : n))
    }
    apply()
    const ro = new ResizeObserver(apply)
    ro.observe(el)
    return () => ro.disconnect()
  }, [items.length])

  // Pack order = heaviest first (stable: weight is the card count, not a live
  // height, so blocks don't reshuffle as async data loads in).
  const order = useMemo(
    () => items.map((it) => it.key).sort((a, b) => {
      const wa = items.find((x) => x.key === a)!.weight
      const wb = items.find((x) => x.key === b)!.weight
      return wb - wa
    }),
    [items],
  )

  // Re-pack after every commit: measure each block, place into the shortest column.
  // Guarded so we only setState when the assignment actually changes (no loop).
  useLayoutEffect(() => {
    for (const [k, el] of blockRefs.current) heights.current.set(k, el.offsetHeight)
    const colKeys: string[][] = Array.from({ length: cols }, () => [])
    const colH = new Array(cols).fill(0)
    for (const key of order) {
      let m = 0
      for (let i = 1; i < cols; i++) if (colH[i] < colH[m]) m = i
      colKeys[m].push(key)
      colH[m] += (heights.current.get(key) ?? 1) + COL_GAP
    }
    const same = layout.length === colKeys.length && colKeys.every((c, i) => {
      const p = layout[i]; return p && p.length === c.length && c.every((k, j) => p[j] === k)
    })
    if (!same) setLayout(colKeys)
  })

  const byKey = (k: string) => items.find((it) => it.key === k)!
  // Until the first measure resolves, render a single flow (also the cols===1 case).
  const columns = layout.length === cols && cols > 0 ? layout : [order]

  return (
    <div ref={containerRef} className="flex items-start" style={{ gap: COL_GAP }}>
      {columns.map((keys, ci) => (
        <div key={ci} className="flex min-w-0 flex-1 flex-col" style={{ gap: COL_GAP }}>
          {keys.map((k) => (
            <div key={k} ref={(el) => { if (el) blockRefs.current.set(k, el); else blockRefs.current.delete(k) }}>
              {byKey(k).node}
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

/** One category block: a heading + its widgets stacked. Hidden when every widget is
 *  filtered out (so the masonry re-packs around it). */
function Group({ title, items, query, go, onMatch }: {
  title: string; items: SettingsWidget[]; query: string; go: (id: string) => void
  onMatch: (id: string, matched: boolean) => void
}) {
  const [visible, setVisible] = useState<Record<string, boolean>>({})
  const anyVisible = query === '' || Object.values(visible).some(Boolean)
  const report = useCallback((id: string, m: boolean) => {
    setVisible((p) => (p[id] === m ? p : { ...p, [id]: m }))
    onMatch(id, m)
  }, [onMatch])
  return (
    <section style={anyVisible ? undefined : { display: 'none' }}>
      <h2 className="mb-2 px-1 text-on-surface-low text-[0.72rem] uppercase tracking-wide">{title}</h2>
      <div className="flex flex-col" style={{ gap: COL_GAP }}>
        {items.map((w) => <Cell key={w.id} widget={w} query={query} go={go} onMatch={report} />)}
      </div>
    </section>
  )
}

/** A bento cell: computes the widget's search text (its own cached hook), decides
 *  whether it matches the query, reports up (effect), and renders it. The widget's
 *  `render()` calls its own hooks, so we ALWAYS render it (and hide a non-match via
 *  CSS) — returning null on a filter miss would change the hook count and crash. */
function Cell({ widget, query, go, onMatch }: {
  widget: SettingsWidget; query: string; go: (id: string) => void; onMatch: (id: string, matched: boolean) => void
}) {
  const text = widget.useSearchText()
  const haystack = `${widget.label} ${widget.description} ${text}`.toLowerCase()
  const matched = query === '' || haystack.includes(query.toLowerCase())
  const reportRef = useRef(onMatch); reportRef.current = onMatch
  useEffect(() => { reportRef.current(widget.id, matched) }, [matched, widget.id])
  return <div style={matched ? undefined : { display: 'none' }}>{widget.render(query, go)}</div>
}
