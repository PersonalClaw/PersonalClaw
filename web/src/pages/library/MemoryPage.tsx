import { useCallback, useEffect, useMemo, useState } from 'react'
import type { RouteProps } from '../../app/useQueryState'
import { MEMORY_TABS, MemoryPanel, STUDIO_KIND_META, useMemoryTab, type MemoryTab, type StudioKind } from '../settings/MemoryPanel'
import { TopBar } from '../../ui/TopBar'
import { PageTitle } from '../../ui/PageTitle'
import { HeaderActions, HeaderSegmented } from '../../ui/HeaderActions'
import { HeaderFilter } from '../../ui/HeaderFilter'
import { HeaderSearch } from '../../ui/HeaderSearch'
import type { FilterSectionDef } from '../../ui/FilterMenu'

/** The kinds the Studio's filter offers, in its order. */
const KINDS: StudioKind[] = ['fact', 'episodic', 'lesson', 'entity', 'slot', 'doc']

/** Library › Memory (#3815), at `#/memory`: the memory studio and its tools, which were Settings ›
 *  Memory. Its header holds what the page's controls are: the view switch (Studio, Health, Recall,
 *  Inspect, Audit, Settings), and on the Studio its search and its kind filter, whose rows count what
 *  the explorer would show. Its views and the record it opens ride the address as they did (`?tab=`,
 *  `?sel=`, `?pref=`, `?partition=`), and the old Settings address carries them here
 *  (`routeRedirects.ts`). */
export function MemoryPage({ query, setQuery }: Pick<RouteProps, 'query' | 'setQuery'>) {
  const [tab, setTab] = useMemoryTab(query, setQuery)
  const [q, setQ] = useState('')
  const [kind, setKind] = useState<StudioKind | 'all'>('all')
  const [counts, setCounts] = useState<Record<StudioKind | 'all', number> | null>(null)
  // The explorer reports its counts after each render that recomputes them. It keeps the last ones
  // unless a number changed, or each report would render the page, and the explorer, again.
  const onCounts = useCallback((next: Record<StudioKind | 'all', number>) => {
    setCounts((prev) => (prev && (Object.keys(next) as (StudioKind | 'all')[]).every((k) => prev[k] === next[k]) ? prev : next))
  }, [])
  // Another memory is another set of records, so its search and filter start over.
  const partition = query.partition ?? ''
  useEffect(() => { setQ(''); setKind('all') }, [partition])

  const kindSections = useMemo<FilterSectionDef[]>(() => [{
    title: 'Kind', value: kind, defaultKey: 'all', onChange: (k) => setKind(k as StudioKind | 'all'), quiet: true,
    options: [
      { key: 'all', label: 'All kinds', count: counts?.all },
      ...KINDS.map((k) => ({ key: k, label: STUDIO_KIND_META[k].label, icon: STUDIO_KIND_META[k].icon, count: counts?.[k] })),
    ],
  }], [kind, counts])

  const studio = tab === 'studio'
  return (
    <div className="flex h-full min-w-0 flex-col">
      <TopBar left={<PageTitle>Memory</PageTitle>}
        right={<HeaderActions>
          {studio && <HeaderSearch value={q} onChange={setQ} placeholder="Search memories" />}
          {studio && <HeaderFilter sections={kindSections} />}
          <HeaderSegmented ariaLabel="Memory view" value={tab} onChange={(v) => setTab(v as MemoryTab)}
            options={MEMORY_TABS.map((t) => ({ key: t.id, label: t.label, icon: t.icon }))} />
        </HeaderActions>} />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto px-2xl py-l">
          <MemoryPanel query={query} setQuery={setQuery} studio={{ q, kind, onCounts }} />
        </div>
      </div>
    </div>
  )
}
