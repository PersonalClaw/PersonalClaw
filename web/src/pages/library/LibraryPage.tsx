import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { FilePlus, Gauge, LayoutGrid, Link2, List, ListChecks, NotebookPen, RefreshCw, Repeat, ScanSearch, Search, Server, Upload } from 'lucide-react'
import { api, type KnowledgeStats, type LibraryItem } from '../../lib/api'
import { invalidateKeys, useQuery } from '../../lib/data'
import { useEmbeddingReindex } from '../../lib/useEmbeddingReindex'
import { DiscoverTip } from '../discover/DiscoverTip'
import { notify } from '../../app/appSdk'
import { reportActionFailure } from '../../app/reportingWrite'
import { confirm } from '../../ui/dialog'
import { SetupPane } from './setup/SetupPane'
import { ComparePane } from './ComparePane'
import { deleteCollection, newCollection, renameCollection } from './libraryCollections'
import { TagsPane } from './TagsPane'
import { CollectionPicker } from './CollectionPicker'
import { PowerUserTabs, panelId, tabId } from './PowerUserTabs'
import { PowerTabPanel } from './PowerTabPanel'
import { isPowerTab, powerUserPlace, usePowerUserView, type PowerTab } from './powerUserView'
import { LibraryBulkBar } from './LibraryBulkBar'
import { LibraryAddForm, LibraryAddMenu, uploadFiles, type AddForm } from '../../ui/LibraryAddMenu'
import type { RouteProps } from '../../app/useQueryState'
import { useGatewayConnection } from '../../app/gatewayConnection'
import { useIsMobile } from '../../app/useIsMobile'
import { TopBar } from '../../ui/TopBar'
import { PageTitle } from '../../ui/PageTitle'
import { HeaderActions, HeaderControl, HeaderSegmented } from '../../ui/HeaderActions'
import { HeaderSearch } from '../../ui/HeaderSearch'
import { HeaderFilter } from '../../ui/HeaderFilter'
import { ListStatus } from '../../ui/ListControls'
import { EmptyState, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { PartialNotice } from '../../ui/PartialNotice'
import { Button } from '../../ui/Button'
import { TextLink } from '../../ui/TextLink'
import { TOUCH_TARGET } from '../../ui/Toggle'
import type { ContextMenuItem } from '../../ui/motion'
import { LibraryList, LibraryGrid } from './LibraryList'
import { librarySections } from './libraryFilter'
import { countLine, failedStoreNotice, n, noRows, partialWords, type LineAction } from './libraryWords'
import { isNarrowed, itemRefOf, LIBRARY_SORT_KEY, libraryParamsOf, mappedQuery, readLibraryCollections, readLibraryProjects, readLibraryStats, useLibrary, useLibraryContext, useWriteUps } from './useLibrary'
import { fileInto, moreRows, LAST_PROJECT_KEY, readLast, useInChatGroups, type ActionInputs } from './libraryActions'
import { LibraryPreview } from '../../ui/LibraryPreview'
import { ProjectPickerDialog } from '../../ui/ProjectPicker'
import { useSyncedDraft } from '../../ui/forms'
import { ChatPicker } from '../chat/ChatPicker'
import { useThreads } from '../../lib/useThreads'
import { useDefaultProject } from '../../lib/defaultProject'
import { chatLaunchPath } from '../../app/linkState'
import { chipRefOf } from './libraryActions'
import { refKind, titleOf } from './libraryMeta'

/** The device's own choices for the page: List or Grid, and Sort by. */
const LAYOUT_KEY = 'library:layout'
const SORT_KEY = LIBRARY_SORT_KEY
const stored = (key: string) => { try { return localStorage.getItem(key) ?? '' } catch { return '' } }
const keep = (key: string, value: string) => { try { if (value) localStorage.setItem(key, value); else localStorage.removeItem(key) } catch { /* private mode */ } }

/** How long the search waits after the last key before it reads. */
export const SEARCH_SETTLE_MS = 250

/** The Power user view's detail on the Items count line: how much can be found by meaning, and how
 *  much is still being read, by the list's own state facet (never the names the graph has not
 *  taken); a part that would read 0 is left out. */
function detailParts(stats: KnowledgeStats, state: Record<string, number> | undefined): string[] {
  const reading = state?.being_read ?? 0
  return [
    stats.embeddings.enabled && stats.embeddings.embedded_items ? `${n(stats.embeddings.embedded_items)} can be found by meaning` : '',
    reading ? `${n(reading)} still being read` : '',
  ].filter(Boolean)
}

/** The Power user view switch's tooltip. */
const POWER_TIP = 'Adds the graph, counts and other details. Everything else stays the same.'

/** Library: one list of everything you keep beside a preview, the header's Search,
 *  Filter, List or Grid, the "…" and Add, and the count line under the title. What the list shows
 *  is the address's (`useLibrary.libraryParamsOf`); the preview is `?item=<ref>`. */
export function LibraryPage({ navigate, query: raw, setQuery }: RouteProps) {
  // An old Knowledge or Artifacts address's keys, read as the library's own.
  const query = useMemo(() => mappedQuery(raw), [raw])
  const power = usePowerUserView()
  const phone = useIsMobile()
  const connection = useGatewayConnection()
  const offline = connection.state === 'disconnected'
  const [layoutPref, setLayoutPref] = useState<'list' | 'grid'>(() => (stored(LAYOUT_KEY) === 'grid' ? 'grid' : 'list'))
  const layout: 'list' | 'grid' = query.layout === 'grid' ? 'grid' : query.layout === 'list' ? 'list' : layoutPref
  const setLayout = useCallback((next: 'list' | 'grid') => {
    setLayoutPref(next); keep(LAYOUT_KEY, next === 'grid' ? 'grid' : '')
    setQuery({ layout: next === 'grid' ? 'grid' : null }, { replace: true })
  }, [setQuery])
  useEffect(() => { if (query.sort) keep(SORT_KEY, query.sort === 'activity' ? '' : query.sort) }, [query.sort])

  const params = useMemo(() => libraryParamsOf(query, stored(SORT_KEY)), [query])
  const read = useLibrary(params)
  const { data, loading, error, refresh, stale } = read
  const ctx = useLibraryContext(query.project && query.project !== 'none' ? query.project: undefined)
  const { data: projects } = useQuery('projects:list', readLibraryProjects)
  const { data: collections, error: collectionsErr } = useQuery('knowledge:collections', readLibraryCollections)
  const { data: stats } = useQuery('knowledge:stats', readLibraryStats)
  // The one re-index: Library settings' Re-index and the "…" row's, each the same job.
  const reindex = useEmbeddingReindex({ onSettled: () => invalidateKeys('knowledge:stats') })
  const reindexing = reindex.job?.status === 'running'
  const unembedded = stats?.embeddings.enabled
    ? stats.embeddings.stale_items ?? Math.max(0, stats.items - (stats.embeddings.embedded_items ?? 0)) : 0

  // The search: its own field's words, read after the settle, held in the address (replace).
  const [words, setWords] = useSyncedDraft(query.q ?? '')
  const settle = useRef<number | undefined>(undefined)
  const onSearch = (next: string) => {
    setWords(next)
    window.clearTimeout(settle.current)
    // On another Power user view tab, a search opens Items with it: it searches Items' rows.
    const elsewhere = isPowerTab(raw.view) && raw.view !== 'items' && !!next.trim()
    settle.current = window.setTimeout(() => setQuery({ q: next.trim() ? next: null, ...(elsewhere ? { view: 'items' } : {}) }, { replace: true }), SEARCH_SETTLE_MS)
  }
  useEffect(() => () => window.clearTimeout(settle.current), [])

  const push = useCallback((patch: Record<string, string | null>) => setQuery(patch), [setQuery])
  const selected = itemRefOf(query.item)
  const select = useCallback((item: LibraryItem) => {
    if (refKind(item.ref) === '') return
    setQuery({ item: item.ref }, phone ? undefined: { replace: true })
  }, [phone, setQuery])

  const [open, setOpen] = useState<ReadonlySet<string>>(new Set())
  const [bulk, setBulk] = useState(false)
  const [checked, setChecked] = useState<ReadonlySet<string>>(new Set())
  const onCheck = (ref: string, on: boolean) => setChecked((cur) => { const next = new Set(cur); if (on) next.add(ref); else next.delete(ref); return next })

  const rows = data?.items ?? []
  const narrowed = isNarrowed(query)
  const firstRun = !!data && data.total === 0 && data.complete && !narrowed
  const projectName = (projects ?? []).find((p) => p.id === query.project)?.name ?? ''
  const collection = (collections ?? []).find((c) => c.id === query.collection)
  const wordsOnly = !!stats && !stats.embeddings?.enabled
  // A library with nothing in it yet draws no count line: "0 items" above "Your library is empty" says
  // the empty state's words again. A search or a filter that narrows it to nothing keeps its line.
  const line = data && !firstRun ? countLine({
    query, data, projectName,
    collection: collection ? { name: collection.name, smart: collection.kind === 'smart' ? collection.query ?? '' : null } : query.collection ? { name: query.collection, smart: null } : undefined,
    wordsOnly,
  }) : null

  const clearFilters = () => setQuery({ kind: null, made: null, project: null, store: null, collection: null, tag: null, state: null, entity: null, type: null, provider: null })
  const lineAction = (key: LineAction) => () => {
    if (key === 'clear') clearFilters()
    else if (key === 'clear-search') setQuery({ q: null })
    else if (key === 'open-project') navigate(`projects/${encodeURIComponent(query.project)}`)
    else if (key === 'set-up-meaning') navigate('settings/models')
    else if (key === 'rename-collection' && collection) void renameCollection(collection)
    else if (key === 'delete-collection' && collection) {
      void deleteCollection(collection, collection.item_count ?? data?.total ?? 0).then((gone) => { if (gone) setQuery({ collection: null }) })
    }
  }

  const sections = librarySections({
    query, data, projects: projects ?? [], collections: collections ?? [], push, phone, layout, setLayout,
    onNewCollection: () => void newCollection().then((id) => { if (id) setQuery({ collection: id }) }),
    onManageTags: () => setQuery({ manage: 'tags' }),
  })

  const fileInput = useRef<HTMLInputElement>(null)
  // The project the list is filtered to: what Add, an upload and a drop file into.
  const into = query.project && projectName ? { id: query.project, name: projectName } : null
  const [dropping, setDropping] = useState(false)
  const dragDepth = useRef(0)
  const emptyHome = firstRun
  const nothingToRead = emptyHome ? 'There is nothing to read yet.' : undefined
  /** Read missed items, or every item again (`scope: "all"`, which asks first). */
  const readAgain = async (scope: 'missing' | 'all') => {
    // A refusal (no model to read with, say) is said in the gateway's own words.
    const res = await api.regenerateKnowledgeIntelligence(scope)
      .catch(reportActionFailure(scope === 'all' ? 'process everything again' : 'read missed items'))
    if (!res) return
    const { queued } = res
    notify(queued ? `Reading ${n(queued)} ${queued === 1 ? 'item' : 'items'} again. You can keep working.` : 'Nothing was waiting to be read.', 'success')
    invalidateKeys('knowledge', true)
  }
  const processAll = async () => {
    const yes = await confirm({
      title: 'Process everything again?',
      body: 'This reads every item again for its tags, topics, the graph and search. Your intents check every item again too. Archived items are left out. It uses your model for a while. You can keep working.',
      confirmLabel: 'Process everything again',
    })
    if (yes) await readAgain('all')
  }
  // `?setup=`: what adds on its own, and its forms, in the pane.
  // The old Intents view (`view=intents`, with `intent` and `edit`) is the set-up panel, or that
  // intent's form, drawn in place at its own address: never rewritten.
  const setup = query.setup ?? (query.view === 'intents' ? (query.intent ? `intent:${query.intent}` : 'all') : '')
  const setupNav = useMemo(() => ({
    form: (v: string) => setQuery({ setup: v }),
    item: (ref: string) => setQuery({ setup: null, item: ref }),
  }), [setQuery])
  const closeSetup = () => setQuery({ setup: null, ...(query.view === 'intents' ? { view: null, intent: null } : {}) })

  const writeUps = useWriteUps()
  const { groups: threadGroups } = useThreads()
  const { defaultProjectId } = useDefaultProject()
  const [pickingChat, setPickingChat] = useState<LibraryItem | null>(null)
  const [pickingProject, setPickingProject] = useState<LibraryItem | null>(null)
  const [pickingCollection, setPickingCollection] = useState<LibraryItem | null>(null)
  const actions: ActionInputs = {
    navigate: (path) => navigate(path),
    writeUps,
    ...(query.project && query.project !== 'none' ? { project: query.project } : {}),
    projects: (projects ?? []).map((p) => ({ id: p.id, name: p.name })),
    chatTitle: (key) => threadGroups.flatMap((g) => g.threads).find((t) => t.id === key || t.key === key)?.title ?? '',
    pickChat: setPickingChat,
    pickProject: setPickingProject,
    collection: collection && collection.kind === 'manual' ? { id: collection.id, name: collection.name } : null,
    offReason: offline ? "Can't reach PersonalClaw." : undefined,
    pickCollection: setPickingCollection,
    offersSkill: (id) => !!ctx.intents?.get(id)?.proposesSkill,
    foundBy: (id) => ctx.intents?.get(id)?.count,
  }
  // A row's menu: Use in chat's rows at the top, then More's.
  const menuOf = (item: LibraryItem): ContextMenuItem[] => [
    ...useInChatGroups(item, actions).flatMap((g) => g.rows.map((r) => ({ icon: <r.icon size={15} />, label: r.label, hint: r.hint, disabled: r.disabled, onSelect: r.onSelect }))),
    ...moreRows(item, actions),
  ]
  // The Power user view: on by the stored setting, or for this visit when the address names one of
  // its tabs (`powerUserView.ts`).
  const place = powerUserPlace(raw, power.stored, narrowed)
  const powerOn = !!place.tab
  const onItems = !place.tab || place.tab === 'items'
  const previewed = selected ? rows.find((r) => r.ref === selected) : undefined
  const adding = /^(link|note|audio):new$/.exec(query.open ?? '')?.[1] as AddForm | undefined
  const setupPane = adding ? (
    <LibraryAddForm key={adding} form={adding} project={into} onClose={() => setQuery({ open: null })}
      onAdded={(ref) => setQuery({ open: null, item: ref || null })} />
  ) : setup ? <SetupPane setup={setup} nav={setupNav} onClose={closeSetup} detail={powerOn} />
    // Manage tags: the tag tree in the pane, each count opening the list filtered by it.
    : query.manage === 'tags' ? <TagsPane onClose={() => setQuery({ manage: null })} onFilter={(tag) => setQuery({ manage: null, tag })} />
    // Compare: two items that disagree, reached from either one's notice line.
    : query.compare && selected ? (
      <ComparePane cid={query.compare} fromTitle={previewed ? titleOf(previewed) : ''} onBack={() => setQuery({ compare: null })}
        onOpen={(id) => navigate(`knowledge/item/${encodeURIComponent(id)}`)} />
    ) : null

  const togglePower = async () => {
    const next = !powerOn
    // The setting moves first (at once, optimistically), so the address below never reads as a
    // visit. Turning it on opens the Graph; turning it off returns to the list with what was selected.
    const saved = power.save(next)
    setQuery(next ? { view: 'graph', entity: null } : { view: null, entity: null })
    await saved
  }
  const openTab = (tab: PowerTab) => setQuery({ view: tab })

  const header = (
    <TopBar
      keepCornerPadding
      left={<PageTitle>Library</PageTitle>}
      right={(
        <HeaderActions overflowClassName={TOUCH_TARGET} overflowWidth={360} overflowPhoneSheet>
          {(!emptyHome || !!query.q) && <HeaderSearch value={words} onChange={onSearch} placeholder="Search your library" />}
          {!emptyHome && onItems && <HeaderFilter sections={sections} prefix="Filter" panel phoneSheet foot={data ? `Show ${n(data.total)} items` : undefined} />}
          {!emptyHome && !phone && onItems && (
            <HeaderSegmented ariaLabel="Show as" markCurrent value={layout} onChange={(k) => setLayout(k === 'grid' ? 'grid' : 'list')}
              options={[{ key: 'list', label: 'List', icon: List }, { key: 'grid', label: 'Grid', icon: LayoutGrid }]} />
          )}
          <HeaderControl icon={Gauge} label="Power user view" title={POWER_TIP} asSwitch keep="row" active={powerOn} onClick={() => void togglePower()} />
          {/* On a phone the gauge stands alone; the "…" holds its words and its switch. */}
          {phone && <HeaderControl icon={Gauge} label="Power user view" hint="Adds the graph, counts and other details." asSwitch inOverflow active={powerOn} onClick={() => void togglePower()} group="view" />}
          {!emptyHome && rows.length > 0 && onItems && (
            <HeaderControl icon={ListChecks} label="Select items" onClick={() => { setBulk(true); setChecked(new Set()) }} inOverflow group="list" />
          )}
          <HeaderControl icon={Repeat} label="Feeds, intents and reports" hint="See and change what adds on its own." onClick={() => setQuery({ setup: 'all' })} inOverflow group="list" />
          {(unembedded > 0 || reindexing) && (
            <HeaderControl icon={ScanSearch} label="Re-index search by meaning"
              hint={reindexing ? `Re-indexing · ${n(reindex.job!.done)} of ${n(reindex.job!.total)}` : `${n(unembedded)} ${unembedded === 1 ? 'item is' : 'items are'} waiting.`}
              disabled={reindex.starting || reindexing || offline} disabledReason={reindex.starting ? 'Starting…' : reindexing ? "It's re-indexing now." : offline ? "Can't reach PersonalClaw." : undefined}
              onClick={reindex.start} inOverflow group="upkeep" />
          )}
          <HeaderControl icon={RefreshCw} label="Read missed items" hint={nothingToRead ? undefined: 'For items added while no model could read them.'}
            disabled={!!nothingToRead || offline} disabledReason={nothingToRead ?? (offline ? "Can't reach PersonalClaw." : undefined)}
            onClick={() => void readAgain('missing')} inOverflow group="upkeep" />
          <HeaderControl icon={Server} label="Process everything again…" hint={nothingToRead ? undefined: 'Uses your model for a while. You can keep working.'}
            disabled={!!nothingToRead || offline} disabledReason={nothingToRead ?? (offline ? "Can't reach PersonalClaw." : undefined)}
            onClick={() => void processAll()} inOverflow group="upkeep" />
          <LibraryAddMenu project={null} className={TOUCH_TARGET} phoneSheet disabled={offline} disabledReason="Can't reach PersonalClaw."
            onOpen={(form) => setQuery({ open: `${form}:new`, setup: null })} onSetup={(form) => setQuery({ setup: `${form}:new`, open: null })} />
        </HeaderActions>
      )}
    />
  )

  const body = (() => {
    if (loading && !data) return <ListSkeleton rows={8} what="library" />
    if (error && !data) return <LoadError what="library" error={error} onRetry={refresh} />
    if (!data) return null
    if (firstRun) {
      return (
        <EmptyState icon={FilePlus} title="Your library is empty"
          hint="Files, links and notes you add show up here. So do the pages and documents your chats make."
          action={{ label: 'Upload files', icon: Upload, onClick: () => fileInput.current?.click(), className: TOUCH_TARGET }}
          footer={(
            <div className="flex flex-col items-center gap-m">
              <div className="flex flex-wrap justify-center gap-s">
                <Button variant="secondary" onClick={() => setQuery({ open: 'link:new' })} className={TOUCH_TARGET}><Link2 size={16} /> Add a link</Button>
                <Button variant="secondary" onClick={() => setQuery({ open: 'note:new' })} className={TOUCH_TARGET}><NotebookPen size={16} /> Write a note</Button>
              </div>
              <p data-type="caption" className="text-on-surface-low">Or drop files anywhere on this page.</p>
              <DiscoverTip here="library" />
            </div>
          )} />
      )
    }
    if (rows.length === 0) {
      const none = noRows(query, projectName)
      const act = (key: string) => () => {
        if (key === 'clear-search') setQuery({ q: null })
        else if (key === 'search-everything') clearFilters()
        else if (key === 'clear-filters') clearFilters()
        else if (key === 'open-project') navigate(`projects/${encodeURIComponent(query.project)}`)
      }
      return (
        <EmptyState icon={Search} title={none.title}
          footer={(
            <div className="flex flex-col items-center gap-s">
              <div className="flex flex-wrap justify-center gap-s">
                {none.actions.map((a) => <Button key={a.key} variant="secondary" onClick={act(a.key)} className={TOUCH_TARGET}>{a.label}</Button>)}
              </div>
              {none.tip && <p data-type="caption" className="text-on-surface-low">{none.tip}</p>}
            </div>
          )} />
      )
    }
    return layout === 'grid'
      ? <LibraryGrid rows={rows} selected={selected} ctx={ctx} onOpen={select} />
      : (
        <LibraryList rows={rows} sort={data.sort} searching={!!query.q?.trim()} open={open}
          onShowAll={(t) => setOpen((cur) => new Set([...cur, t]))} selected={selected} ctx={ctx} onOpen={select}
          onFilterProject={(id) => push({ project: id })} menuOf={menuOf} bulk={bulk} checked={checked} onCheck={onCheck} />
      )
  })()

  const failed = data ? failedStoreNotice(data.failed) : ''
  // The list and its pane: the regular view, and the Power user view's Items tab.
  const listAndPane = (
      <div className="flex min-h-0 flex-1">
        <section role="region" aria-label="Library items" className="flex min-h-0 w-full flex-col md:w-[440px] md:shrink-0">
          {bulk && (
            <LibraryBulkBar checked={checked} rows={rows} total={rows.length} collections={collections ?? []}
              collectionsReason={collections ? undefined : collectionsErr ? "Couldn't load your collections." : 'Your collections are loading.'}
              collection={collection && collection.kind !== 'smart' ? { id: collection.id, name: collection.name } : null}
              offReason={offline ? "Can't reach PersonalClaw." : undefined}
              onSelectAll={() => setChecked(new Set(rows.map((r) => r.ref)))}
              onClear={() => { setBulk(false); setChecked(new Set()) }} />
          )}
          <div className="px-l pb-s">
            {data && (
              <ListStatus results={{ count: data.total, noun: 'items', active: narrowed }} stale={stale}
                lead={line?.lead} line={line ? (powerOn && !narrowed && stats ? [line.line, ...detailParts(stats, data.facets?.state)].filter(Boolean).join(' · ') : line.line) : undefined}
                actions={line?.actions.map((a) => ({ label: a.label, onSelect: lineAction(a.key) }))} />
            )}
            {failed && (
              <p data-type="body-s" className="mt-s text-on-surface-var">{failed} <TextLink onClick={refresh}>Try again</TextLink></p>
            )}
            {data && !data.complete && !data.failed.length && (
              <PartialNotice complete={false} shown={rows.length} total={data.total} what="items"
                detail={`${partialWords(data.sort, rows.length, data.total)} Search or filter to reach the rest.`} />
            )}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {/* A phone opens the item full screen (push, so Back returns to the row). */}
            {phone && setupPane ? setupPane
              : phone && previewed ? <LibraryPreview key={previewed.ref} item={previewed} actions={actions} phoneSheet foot detail={powerOn} /> : body}
          </div>
        </section>
        {!phone && (
          <div className="m-m hidden min-w-0 flex-1 overflow-y-auto rounded-xl bg-surface-low md:block">
            {setupPane}
            {!setupPane && !selected && <p data-type="body-m" className="p-xl text-on-surface-low">Pick an item to see it here.</p>}
            {!setupPane && selected && previewed && <LibraryPreview key={previewed.ref} item={previewed} actions={actions} phoneSheet detail={powerOn} />}
            {!setupPane && selected && data && !previewed && !loading && (
              <p data-type="body-m" className="p-xl text-on-surface-low">Nothing in your library has this address.</p>
            )}
          </div>
        )}
      </div>
  )
  return (
    // The whole page is a drop target: files dropped anywhere are added, into the project the
    // list is filtered to when there is one.
    <div className="relative flex h-full flex-col overflow-hidden"
      onDragEnter={(e) => { if (!offline && e.dataTransfer.types.includes('Files')) { dragDepth.current++; setDropping(true) } }}
      onDragOver={(e) => { if (dropping) e.preventDefault() }}
      onDragLeave={() => { if (dragDepth.current > 0 && --dragDepth.current === 0) setDropping(false) }}
      onDrop={(e) => {
        if (!dropping) return
        e.preventDefault(); dragDepth.current = 0; setDropping(false)
        void uploadFiles([...e.dataTransfer.files], into)
      }}>
      {header}
      {/* Raw: no primitive draws a file picker. Forwarded to by the empty state's Upload files, the keyboard-reachable control. */}
      <input ref={fileInput} type="file" multiple className="hidden" tabIndex={-1} aria-hidden
        onChange={(e) => { const files = [...(e.target.files ?? [])]; e.target.value = ''; void uploadFiles(files, into) }} />
      {dropping && (
        <div aria-hidden className="pointer-events-none absolute inset-m z-20 flex flex-col items-center justify-center gap-xs rounded-xl border-2 border-dashed border-primary bg-surface/90">
          <p data-type="title-m" className="text-on-surface">{into ? `Drop to add to ${into.name}` : 'Drop to add to your library'}</p>
          <p data-type="body-s" className="text-on-surface-var">Files stay on this computer.</p>
        </div>
      )}
      {place.tab ? (
      <div className={`flex min-h-0 flex-1 ${phone ? 'flex-col' : ''}`}>
        <PowerUserTabs tab={place.tab} onOpen={openTab} visit={place.visit} onKeepOn={() => void power.save(true)} strip={phone} />
        <div role="tabpanel" id={panelId(place.tab)} aria-labelledby={tabId(place.tab)} className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto">
          {place.tab === 'items' ? listAndPane : (
            <PowerTabPanel tab={place.tab} entity={query.entity} stats={stats} navigate={navigate}
              onEntity={(entity) => setQuery({ entity }, { replace: true })}
              onShowEntity={(entity, item) => setQuery({ view: 'items', entity, item: item ?? null })} onFilterTag={(tag) => setQuery({ view: 'items', tag })}
              openTab={openTab} filterItems={(state) => setQuery({ view: 'items', state })} processAll={() => void processAll()} reindex={reindex} />
          )}
        </div>
      </div>
      ) : listAndPane}
      {pickingChat && (
        <ChatPicker onClose={() => setPickingChat(null)}
          onPick={(key) => { const chip = chipRefOf(pickingChat, writeUps); if (chip) navigate(chatLaunchPath({ session: key, attach: [chip] })) }} />
      )}
      {pickingCollection && (
        <CollectionPicker item={pickingCollection} collections={collections ?? []} phoneSheet onClose={() => setPickingCollection(null)} />
      )}
      {pickingProject && (
        <ProjectPickerDialog itemTitle={pickingProject.title} current={pickingProject.project} lastOpened={readLast(LAST_PROJECT_KEY)}
          defaultProject={defaultProjectId} phoneSheet onClose={() => setPickingProject(null)}
          onPick={(to) => void fileInto(pickingProject, to, actions)} />
      )}
    </div>
  )
}
