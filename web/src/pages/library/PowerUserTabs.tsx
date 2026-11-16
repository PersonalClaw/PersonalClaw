import { useRef, type KeyboardEvent } from 'react'
import { BookOpen, Gavel, Network, Scale, Settings2, Tag as TagIcon, type LucideIcon } from 'lucide-react'
import { api } from '../../lib/api'
import { useQuery } from '../../lib/data'
import { Button } from '../../ui/Button'
import { CURRENT_OUTLINE } from '../../ui/RowHitTarget'
import { TOUCH_TARGET } from '../../ui/Toggle'
import { n } from './libraryWords'
import type { PowerTab } from './powerUserView'

// ── The Power user view's tab list ────────────────────────────────────────────────────────────────
//
// A vertical tab list at the left of the body: Graph, Items, Tags, Conflicts, Decisions, then Library
// settings pinned to its foot under a hairline. Each tab says one fact that is never a 0; a tab with
// nothing to say says nothing. The arrows move along the list, Enter or Space opens a tab, and each
// panel is named by its tab. The current tab shows under forced colours.
//
// On a phone (`strip`) the same list is a strip above the panel, which then has the page's whole
// width: beside it, the 184 px column left each panel about 205 px at 390, and cut the Graph's
// heading to "Gra…" and an item's title to a few letters. The strip scrolls sideways and never cuts a
// tab's name; Left and Right move along it, in the same order, with Library settings last beyond a
// hairline. A scroller clips what its children draw past it, so a strip tab's focus ring is drawn
// inside the tab, never cut at the strip's edges.

const TABS: { key: PowerTab; label: string; icon: LucideIcon }[] = [
  { key: 'graph', label: 'Graph', icon: Network },
  { key: 'items', label: 'Items', icon: BookOpen },
  { key: 'tags', label: 'Tags', icon: TagIcon },
  { key: 'conflicts', label: 'Conflicts', icon: Scale },
  { key: 'decisions', label: 'Decisions', icon: Gavel },
  { key: 'settings', label: 'Library settings', icon: Settings2 },
]

export const tabLabel = (t: PowerTab) => TABS.find((x) => x.key === t)!.label
export const tabId = (t: PowerTab) => `lib-tab-${t}`
export const panelId = (t: PowerTab) => `lib-panel-${t}`

/** Each tab's fact, from the reads the page already keeps; `''` for one with nothing to say, so no
 *  tab ever reads 0. */
export function useTabFacts(): Record<PowerTab, string> {
  const stats = useQuery('knowledge:stats', () => api.knowledgeStats()).data
  const tags = useQuery('knowledge:tag-tree', () => api.knowledgeTagTree()).data
  const conflicts = useQuery('knowledge:conflicts:500', () => api.knowledgeConflicts(500)).data
  // What is still being read, from the library's own state facet (as Library settings counts it),
  // never the names the graph has not taken.
  // A read that failed says nothing, never a count it could not take.
  const libraryRead = useQuery('knowledge:library:{}', () => api.library({}))
  const reading = libraryRead.error ? 0 : libraryRead.data?.facets?.state?.being_read ?? 0
  const undecided = new Set((conflicts?.conflicts ?? []).filter((c) => !c.choice).map((c) => c.id)).size
  const say = (count: number | undefined, words: (c: number) => string) => (count ? words(count) : '')
  return {
    graph: say(stats?.entities, (c) => `${n(c)} people, places and ideas`),
    items: say(stats?.items, n),
    tags: say(tags?.length, n),
    conflicts: say(undecided, (c) => `${n(c)} to decide`),
    decisions: '',
    settings: say(reading, (c) => `${n(c)} still being read`),
  }
}

export function PowerUserTabs({ tab, onOpen, visit, onKeepOn, strip = false }: {
  tab: PowerTab
  onOpen: (tab: PowerTab) => void
  /** On for this visit only: the address names a tab while the setting is off. */
  visit: boolean
  onKeepOn: () => void
  /** A phone's: a strip above the panel, in place of the column beside it. */
  strip?: boolean
}) {
  const facts = useTabFacts()
  const list = useRef<HTMLDivElement>(null)
  const move = (e: KeyboardEvent<HTMLButtonElement>) => {
    const tabs = [...(list.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]') ?? [])]
    const at = tabs.indexOf(e.currentTarget)
    const [next, back] = strip ? ['ArrowRight', 'ArrowLeft'] : ['ArrowDown', 'ArrowUp']
    const to = e.key === next ? at + 1 : e.key === back ? at - 1 : e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1 : -2
    if (to === -2) return
    e.preventDefault()
    const target = tabs[(to + tabs.length) % tabs.length]
    target?.focus()
    // Focus leaves a tab the strip shows only in part where it is (Chromium scrolls it no further), so
    // the strip brings the whole tab, and its ring, into view.
    if (strip) target?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' })
  }
  const row = (t: (typeof TABS)[number]) => {
    const current = t.key === tab
    return (
      <button key={t.key} type="button" role="tab" id={tabId(t.key)} aria-selected={current} aria-controls={panelId(t.key)}
        tabIndex={current ? 0 : -1} onKeyDown={move} onClick={() => onOpen(t.key)}
        className={`flex items-start gap-s rounded-lg px-m py-s text-left max-lg:min-h-11 ${strip ? 'shrink-0 whitespace-nowrap focus-visible:outline-offset-[-2px]' : 'w-full'} ${current ? `bg-surface-high ${CURRENT_OUTLINE}` : 'hover:bg-surface-high'}`}>
        <t.icon size={16} aria-hidden className="mt-0.5 shrink-0 text-on-surface-var" />
        <span className={strip ? 'shrink-0' : 'min-w-0 flex-1'}>
          <span data-type="label-l" className="block text-on-surface">{t.label}</span>
          {facts[t.key] && <span data-type="caption" className="block text-on-surface-low">{facts[t.key]}</span>}
        </span>
      </button>
    )
  }
  const keepOn = visit && (
    <div className={`flex gap-xs rounded-lg bg-surface-high px-m py-s ${strip ? 'items-center justify-between' : 'flex-col'}`}>
      <p data-type="caption" className="text-on-surface">On for this visit. Your setting is off.</p>
      <Button variant="secondary" size="sm" onClick={onKeepOn} className={`${TOUCH_TARGET} ${strip ? 'shrink-0' : ''}`}>Keep it on</Button>
    </div>
  )
  if (strip) return (
    <div className="flex shrink-0 flex-col gap-xs border-b border-outline-variant/40 px-s py-xs">
      <div ref={list} role="tablist" aria-orientation="horizontal" aria-label="Library views" className="flex gap-0.5 overflow-x-auto">
        {TABS.filter((t) => t.key !== 'settings').map(row)}
        <div className="ml-xs flex shrink-0 border-l border-outline-variant/40 pl-xs">{row(TABS[TABS.length - 1])}</div>
      </div>
      {keepOn}
    </div>
  )
  return (
    <div className="flex w-[184px] shrink-0 flex-col gap-xs border-r border-outline-variant/40 px-s py-m">
      <div ref={list} role="tablist" aria-orientation="vertical" aria-label="Library views" className="flex min-h-0 flex-1 flex-col gap-0.5">
        {TABS.filter((t) => t.key !== 'settings').map(row)}
        <div className="mt-auto border-t border-outline-variant/40 pt-xs">{row(TABS[TABS.length - 1])}</div>
      </div>
      {keepOn}
    </div>
  )
}
