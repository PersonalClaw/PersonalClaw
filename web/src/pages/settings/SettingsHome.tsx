import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Eyebrow } from '../../ui/Eyebrow'
import { ResultAnnouncement } from '../../ui/ListControls'
import { SearchX } from 'lucide-react'
import { SearchField } from '../../ui/SearchField'
import { SETTINGS_WIDGETS, type SettingsWidget } from './settingsWidgets'
import { columnsFor, packRows } from './packRows'
import { SETTINGS_GROUPS, SETTINGS_PAGES, type SettingsPageDef } from './settingsPages'
import { SETTINGS_TERMS } from './settingsTerms'
import { BentoCard, BentoPartProvider } from './bento'
import { TextLink } from '../../ui/TextLink'

const GAP = 12        // px between cards, across and down
const GRID_MAX = 1200 // the grid's widest; past it, three columns of ~390 px stay centred

/** Settings home — one card per page (#5897), each surfacing the sections it holds and their quick
 *  controls; clicking opens the page. A full-width search filters the cards (every section's label,
 *  description, setting labels and the data its part surfaces) and highlights matches.
 *
 *  Layout (#5897): row bands. Each group is its heading and then its cards, in a grid whose column
 *  count follows the space it has (`columnsFor`) and whose spans `packRows` picks so every row ends
 *  at the right edge. It used to pack whole groups into columns, which left the columns over a
 *  thousand pixels apart; a group now reads left to right, top to bottom, in its own source order,
 *  which is also the order focus moves in. Cards in a row stretch to its tallest. */
export function SettingsHome({ go }: { go: (id: string) => void }) {
  const [query, setQuery] = useState('')
  const q = query.trim()

  // The pages by group, in the registry's order.
  const groups = useMemo(() => SETTINGS_GROUPS.map((g) => ({ title: g, items: SETTINGS_PAGES.filter((p) => p.group === g) })), [])

  // Each page reports whether it matches the query (its sections own the hooks producing their
  // search text) so we can show a global "no matches" state + hide empty groups.
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

/** One group: its heading, then its pages' cards in a row-band grid. The spans are packed over the
 *  cards a search leaves visible, so a filtered group still ends its rows even; hidden when none
 *  match. */
function Group({ title, items, query, go, onMatch, matches, columns }: {
  title: string; items: SettingsPageDef[]; query: string; go: (id: string) => void
  onMatch: (id: string, matched: boolean) => void; matches: Record<string, boolean>; columns: number
}) {
  const headingId = useId()
  // A card not yet reported counts as visible: it renders until its own cell says otherwise.
  const visible = items.filter((p) => query === '' || matches[p.id] !== false)
  const spans = packRows(visible.map((p) => p.span), columns)
  const spanOf = new Map(visible.map((p, i) => [p.id, spans[i]]))
  return (
    <section aria-labelledby={headingId} className="mb-l" style={visible.length ? undefined : { display: 'none' }}>
      <Eyebrow as="h2" id={headingId} className="mb-2 px-1">{title}</Eyebrow>
      <div className="grid" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`, gap: GAP }}>
        {items.map((p) => <Cell key={p.id} page={p} query={query} go={go} onMatch={onMatch} span={spanOf.get(p.id) ?? 1} />)}
      </div>
    </section>
  )
}

const WIDGETS = new Map(SETTINGS_WIDGETS.map((w) => [w.id, w]))
const widgetOf = (id: string): SettingsWidget => {
  const w = WIDGETS.get(id)
  if (!w) throw new Error(`Settings section ${id} has no widget`)
  return w
}

/** A page's cell: whether the page matches the query, reported up, and its card. A page matches by
 *  its own name or through any section it holds; each section computes its own search text (its own
 *  hook, in `SectionSearch`), so the hook count never changes with the query, and the card is ALWAYS
 *  rendered (a non-match is hidden with CSS) for the same reason. */
function Cell({ page, query, go, onMatch, span }: {
  page: SettingsPageDef; query: string; go: (id: string) => void; onMatch: (id: string, matched: boolean) => void; span: number
}) {
  const [hits, setHits] = useState<Record<string, boolean>>({})
  const report = useCallback((id: string, m: boolean) => setHits((p) => (p[id] === m ? p : { ...p, [id]: m })), [])
  const own = query !== '' && page.label.toLowerCase().includes(query.toLowerCase())
  const through = query === '' ? [] : page.sections.filter((id) => hits[id])
  const matched = query === '' || own || through.length > 0
  const reportRef = useRef(onMatch); reportRef.current = onMatch
  useEffect(() => { reportRef.current(page.id, matched) }, [matched, page.id])
  // A flex box so the card stretches to its row's height; `min-w-0` so a long value truncates
  // inside its column rather than widening it.
  return (
    <div className="flex min-w-0" style={matched ? { gridColumn: `span ${span}` } : { display: 'none' }}>
      {page.sections.map((id) => <SectionSearch key={id} widget={widgetOf(id)} query={query} onMatch={report} />)}
      <PageCard page={page} span={span} query={query} go={go} foundIn={own ? [] : through} />
    </div>
  )
}

/** One section's part of its page's search: its label, description, the labels of the settings its
 *  panel holds (`settingsTerms.ts`) and the data its part surfaces. Renders nothing. */
function SectionSearch({ widget, query, onMatch }: {
  widget: SettingsWidget; query: string; onMatch: (id: string, matched: boolean) => void
}) {
  const text = widget.useSearchText()
  const haystack = `${widget.label} ${widget.description} ${(SETTINGS_TERMS[widget.id] ?? []).join(' ')} ${text}`.toLowerCase()
  const matched = query !== '' && haystack.includes(query.toLowerCase())
  const reportRef = useRef(onMatch); reportRef.current = onMatch
  useEffect(() => { reportRef.current(widget.id, matched) }, [matched, widget.id])
  return null
}

/** A page's card: its title (an `h3`), then the parts it shows, each its section's tile drawn as a
 *  part (`BentoPartProvider`), side by side on a card two columns wide. Sections it does not show are
 *  named at its foot as links, and so is each section a search found the page through. */
function PageCard({ page, span, query, go, foundIn }: {
  page: SettingsPageDef; span: number; query: string; go: (id: string) => void; foundIn: string[]
}) {
  const parts = page.parts ?? page.sections
  const also = page.sections.filter((id) => !parts.includes(id))
  const icon = (WIDGETS.get(page.id) ?? widgetOf(page.sections[0])).icon
  // The part the card leads with goes untitled where the parts stack, since the card's own title
  // sits over it; every part after it keeps its title. A card of several parts two columns wide
  // titles them all, since they sit side by side. The lead part, not the part that shares the page's
  // id: Models & Providers leads with Providers, and its Models part kept no title at one column.
  const titled = (id: string) => parts.length > 1 && !(id === parts[0] && span < 2)
  // Each opens its section on the page, in one click from here.
  const links = (ids: string[]) => ids.map((id, i) => (
    <span key={id}>
      {i > 0 && ' · '}
      <TextLink size="xs" ink="emphasis" className="pointer-events-auto" onClick={(e) => { e.stopPropagation(); go(id) }}>
        {widgetOf(id).label}
      </TextLink>
    </span>
  ))
  const footer = (also.length > 0 || foundIn.length > 0) && (
    <div className="flex flex-col gap-0.5">
      {foundIn.length > 0 && <div>Found in {links(foundIn)}</div>}
      {also.length > 0 && <div>Also here: {links(also)}</div>}
    </div>
  )
  return (
    <BentoCard icon={icon} title={page.label} query={query} onClick={() => go(page.id)} heading footer={footer || undefined}>
      <div className={span >= 2 && parts.length > 1 ? 'grid grid-cols-2 gap-x-xl gap-y-l' : 'flex flex-col gap-l'}>
        {parts.map((id) => (
          <BentoPartProvider key={id} value={{ titled: titled(id) }}>{widgetOf(id).render(query, go)}</BentoPartProvider>
        ))}
      </div>
    </BentoCard>
  )
}
