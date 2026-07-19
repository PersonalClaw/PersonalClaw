import { useMemo, useState } from 'react'
import { Plus, FileText, Puzzle, Maximize2, User, Cog } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { WorkbenchLayout } from '../../ui/WorkbenchLayout'
import { HeaderActions, HeaderControl, HeaderSegmented } from '../../ui/HeaderActions'
import { ListControls } from '../../ui/ListControls'
import { FilterMenu, type FilterSectionDef } from '../../ui/FilterMenu'
import { EmptyState, ListRow, ListSkeleton } from '../../ui/ListScaffold'
import { SidePanel } from '../../ui/SidePanel'
import { ContextMenu, type ContextMenuItem, Disintegrate } from '../../ui/motion'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { api, type PromptItem, type PromptSnippet } from '../../lib/api'
import { promptVars, sourceTone, sourceLabel } from './promptMeta'
import { PromptDetail } from './PromptDetail'
import { SnippetDetail } from './SnippetDetail'
import { useQueryParam, useEditFlag, type RouteProps } from '../../app/useQueryState'

type Tab = 'system' | 'user' | 'snippets'
const TABS: { key: Tab; label: string; icon: typeof User }[] = [
  { key: 'user', label: 'User', icon: User },
  { key: 'system', label: 'System', icon: Cog },
  { key: 'snippets', label: 'Snippets', icon: Puzzle },
]

type SortKey = 'name' | 'updated' | 'source' | 'vars'
const SORTS: { key: SortKey; label: string }[] = [
  { key: 'name', label: 'Name (A–Z)' },
  { key: 'updated', label: 'Recently updated' },
  { key: 'source', label: 'Source' },
  { key: 'vars', label: 'Variable count' },
]
const SOURCES = [
  { key: 'all', label: 'All sources' },
  { key: 'user', label: 'User' },
  { key: 'bundled', label: 'Bundled' },
  { key: 'marketplace', label: 'Marketplace' },
]

// One row type covers both prompts and snippets for sorting/filtering (snippets
// have no `kind`; everything else is shared).
type Row = PromptItem & PromptSnippet

function applyView(rows: Row[], q: string, sort: SortKey, source: string): Row[] {
  const n = q.trim().toLowerCase()
  let out = n
    ? rows.filter((r) => `${r.name} ${r.title ?? ''} ${r.description ?? ''} ${(r.tags ?? []).join(' ')}`.toLowerCase().includes(n))
    : rows.slice()
  if (source !== 'all') out = out.filter((r) => (r.source || 'user') === source)
  const byName = (a: Row, b: Row) => (a.title || a.name).localeCompare(b.title || b.name)
  out.sort((a, b) => {
    switch (sort) {
      case 'updated': return (b.updated_at ?? 0) - (a.updated_at ?? 0) || byName(a, b)
      case 'source': return (a.source || 'user').localeCompare(b.source || 'user') || byName(a, b)
      case 'vars': return (b.variables?.length ?? 0) - (a.variables?.length ?? 0) || byName(a, b)
      default: return byName(a, b)
    }
  })
  return out
}

/** A thin bar at the top of the side-panel detail that promotes opening the same
 *  record on its dedicated full page (view + edit, consistent with the create page). */
function OpenFullPageBar({ onOpen }: { onOpen: () => void }) {
  return (
    <button type="button" onClick={onOpen}
      className="mb-l inline-flex items-center gap-1.5 self-start rounded-md px-2 py-1 text-[0.8125rem] text-on-surface-low transition-colors hover:bg-surface-high hover:text-on-surface">
      <Maximize2 size={13} /> Open full page
    </button>
  )
}

export function PromptsListPage({ onCreate, onOpen, navigate, query, setQuery }: {
  onCreate: (tab: Tab) => void
  onOpen: (tab: Tab, name: string, opts?: { edit?: boolean }) => void
} & Pick<RouteProps, 'navigate' | 'query' | 'setQuery'>) {
  const { data: items, refresh } = useCachedData<PromptItem[]>('prompts', () => api.prompts().catch(() => []), { persist: true })
  const { data: snippets, refresh: refreshSnips } = useCachedData<PromptSnippet[]>('prompt-snippets', () => api.snippets().catch(() => []), { persist: true })
  const [q, setQ] = useQueryParam(query, setQuery, 'q', '', { replace: true })
  const [tabRaw, setTabRaw] = useQueryParam(query, setQuery, 'tab', 'user', { replace: true })
  const tab = (TABS.some((t) => t.key === tabRaw) ? tabRaw : 'user') as Tab
  const setTab = (t: Tab) => setTabRaw(t)
  const [openNameRaw, setOpenName] = useQueryParam(query, setQuery, 'open', '')
  const openName = openNameRaw || null
  const [editing, setEditing] = useEditFlag(query, setQuery)
  const [sortRaw, setSort] = useQueryParam(query, setQuery, 'sort', 'name', { replace: true })
  const sort = (SORTS.some((s) => s.key === sortRaw) ? sortRaw : 'name') as SortKey
  const [source, setSource] = useQueryParam(query, setQuery, 'src', 'all', { replace: true })

  const load = () => { invalidateCache('prompts'); invalidateCache('prompt-snippets'); refresh(); refreshSnips() }
  const isSnips = tab === 'snippets'

  // A just-deleted record's row DISINTEGRATES in place before the list reloads —
  // the deletion is confirmed server-side by the time the detail panel calls
  // onDeleted, so we play the effect on the still-rendered row, then reload when it
  // settles (onDone) so the gap closes into the disintegration rather than a
  // jarring instant drop. Keyed by name (list is name-unique).
  const [deletingName, setDeletingName] = useState<string | null>(null)
  const onDeleted = (name: string) => { setOpenName(''); setDeletingName(name) }
  const finishDelete = () => { setDeletingName(null); load() }

  // Active-tab rows (prompts of the tab's kind, or snippets) through search/sort/filter.
  const rows = useMemo<Row[] | null>(() => {
    if (isSnips) return snippets ? applyView(snippets as Row[], q, sort, source) : null
    if (!items) return null
    const byKind = (items as Row[]).filter((p) => (p.kind ?? 'user') === tab)
    return applyView(byKind, q, sort, source)
  }, [isSnips, items, snippets, q, sort, source, tab])

  const openPrompt = items?.find((p) => p.name === openName) ?? null
  const openSnip = snippets?.find((s) => s.name === openName) ?? null
  const loading = rows === null
  const count = rows?.length ?? 0
  const anyItems = isSnips ? (snippets === undefined || (snippets?.length ?? 0) > 0) : (items === undefined || (items?.length ?? 0) > 0)

  const filterSections: FilterSectionDef[] = [
    { title: 'Sort by', value: sort, defaultKey: 'name', onChange: (k) => setSort(k), options: SORTS.map((s) => ({ key: s.key, label: s.label })) },
    { title: 'Source', value: source, defaultKey: 'all', onChange: (k) => setSource(k), options: SOURCES.map((s) => ({ key: s.key, label: s.label })) },
  ]

  return (
    <WorkbenchLayout
      topBar={
        <TopBar
          keepCornerPadding
          left={<span data-type="title-l" className="text-on-surface">Prompts</span>}
          right={
            // Structural view switch + primary action in the 4-tier cluster (degrade
            // together, no clip on mobile). Search / sort / source-filter live on the
            // page (controls bar).
            <HeaderActions>
              <HeaderSegmented ariaLabel="Prompt kind" value={tab} onChange={(v) => { setTab(v as Tab); setQuery({ open: null, edit: null }) }} options={TABS} />
              <HeaderControl icon={Plus} label={isSnips ? 'New snippet' : 'New prompt'} variant="primary" priority="primary" onClick={() => onCreate(tab)} />
            </HeaderActions>
          }
        />
      }
      controls={anyItems
        ? <ListControls
            search={{ value: q, onChange: setQ, placeholder: isSnips ? 'Search snippets' : 'Search prompts', label: 'Search' }}>
            <FilterMenu sections={filterSections} label="Sort & filter" />
          </ListControls>
        : undefined}
      panel={
        isSnips
          ? (openSnip && (
              <SidePanel key={openSnip.name} fillHeight storeKey="prompt-panel-w" icon={<Puzzle size={18} style={{ color: sourceTone(openSnip.source) }} />} title={openSnip.name} onClose={() => setQuery({ open: null, edit: null })}>
                <OpenFullPageBar onOpen={() => onOpen('snippets', openSnip.name)} />
                <SnippetDetail snippet={openSnip} editing={editing} onEditingChange={setEditing} onSaved={() => load()} onDeleted={() => onDeleted(openSnip.name)} />
              </SidePanel>
            ))
          : (openPrompt && (
              <SidePanel key={openPrompt.name} fillHeight storeKey="prompt-panel-w" icon={<FileText size={18} style={{ color: sourceTone(openPrompt.source) }} />} title={openPrompt.name} onClose={() => setQuery({ open: null, edit: null })}>
                <OpenFullPageBar onOpen={() => onOpen((openPrompt.kind ?? 'user') as Tab, openPrompt.name)} />
                <PromptDetail prompt={openPrompt} editing={editing} onEditingChange={setEditing} onSaved={() => load()} onDeleted={() => onDeleted(openPrompt.name)} onNavigate={navigate} />
              </SidePanel>
            ))
      }
    >
      <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
        {loading ? <ListSkeleton rows={6} /> : count === 0 ? (
          isSnips ? (
            <EmptyState icon={Puzzle} title={q ? 'No matching snippets' : 'No snippets'} hint={q ? 'Try a different term.' : 'Snippets are reusable fragments other prompts include with {{> name}}. Their variables merge into the including prompt.'} action={!q ? { label: 'New snippet', onClick: () => onCreate('snippets'), icon: Plus } : undefined} />
          ) : (
            <EmptyState icon={FileText} title={q ? 'No matching prompts' : `No ${tab} prompts`} hint={q ? 'Try a different term.' : tab === 'system' ? 'System prompts are bound to a use-case (chat / background / code / goal loop) and injected as the agent system prompt.' : 'User prompts are invoked in chat with filled-in {{variables}}.'} action={!q ? { label: 'New prompt', onClick: () => onCreate(tab), icon: Plus } : undefined} />
          )
        ) : (
          <div className="flex flex-col gap-s">
            {rows!.map((r, i) => {
              const Icon = isSnips ? Puzzle : FileText
              const vars = promptVars(r)
              // Right-click / long-press → scoped actions (the shared ContextMenu
              // primitive). Both reuse handlers this surface already invokes: the
              // row-click side-panel open, and the dedicated full-page open the
              // panel's OpenFullPageBar uses (tab-resolved per snippet vs kind).
              const menuItems: ContextMenuItem[] = [
                { icon: <Icon size={15} />, label: 'Open', onSelect: () => setQuery({ open: r.name, edit: null }) },
                { icon: <Maximize2 size={15} />, label: 'Open full page', onSelect: () => onOpen(isSnips ? 'snippets' : ((r.kind ?? 'user') as Tab), r.name) },
              ]
              return (
                <Disintegrate key={r.name} active={deletingName === r.name} onDone={finishDelete}>
                <ContextMenu items={menuItems}>
                <ListRow index={i} accent={sourceTone(r.source)} onClick={() => setQuery({ open: r.name, edit: null })}>
                  <span className="shrink-0 inline-flex size-10 items-center justify-center rounded-lg" style={{ background: `color-mix(in srgb, ${sourceTone(r.source)} 16%, transparent)` }}><Icon size={19} style={{ color: sourceTone(r.source) }} /></span>
                  <div className="flex-1 min-w-0">
                    <span className="truncate text-on-surface text-[0.9375rem] font-mono" style={{ fontVariationSettings: '"wght" 500' }}>{r.name}</span>
                    <div className="mt-0.5 flex flex-wrap items-center gap-x-m gap-y-0.5 text-on-surface-low text-[0.8125rem]">
                      <span>{sourceLabel(r.source)}</span>
                      {vars.length > 0 && <span>{vars.length} var{vars.length > 1 ? 's' : ''}</span>}
                      {r.description && <span className="truncate">· {r.description}</span>}
                    </div>
                  </div>
                  {(r.tags?.length ?? 0) > 0 && <div className="hidden md:flex shrink-0 gap-1">{r.tags!.slice(0, 2).map((t) => <span key={t} className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var text-[0.7rem]">{t}</span>)}</div>}
                </ListRow>
                </ContextMenu>
                </Disintegrate>
              )
            })}
          </div>
        )}
      </div>
    </WorkbenchLayout>
  )
}
