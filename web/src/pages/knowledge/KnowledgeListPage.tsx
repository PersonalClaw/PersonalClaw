import { Fragment, useEffect, useMemo, useState } from 'react'
import { reportingWrite } from '../../app/reportingWrite'
import { BookOpen, FileClock, Plus, Search, Database, Sparkles, Network, Library, Trash2, Target, X, Pin, Star, Archive, Play, FileText, Loader2, CircleAlert, Boxes, WifiOff, Layers, Scale, Tag as TagIcon, Rss, ExternalLink } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { fvs } from '../../design/fontWeight'
import { WorkbenchLayout } from '../../ui/WorkbenchLayout'
import { Button } from '../../ui/Button'
import { EmptyState, ListRow, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { WindowedList } from '../../ui/WindowedList'
import { Checkbox, FieldError } from '../../ui/forms'
import { TagManager } from './TagManager'
import { ConflictPanel } from './ConflictPanel'
import { SidePanel } from '../../ui/SidePanel'
import { ListControls } from '../../ui/ListControls'
import { HeaderActions, HeaderControl, HeaderSegmented } from '../../ui/HeaderActions'
import { ContextMenu, type ContextMenuItem } from '../../ui/motion'
import { api, type KnowledgeIntent, type IntentOutcome, type KnowledgeItem, type KnowledgeCollection, type KnowledgeBulkOp } from '../../lib/api'
import { resolveType, relTime, fmtBytes, typeLabel, isArtifactItem } from './knowledgeMeta'
import { listKnowledge, knowledgeStats, getKnowledge } from './knowledgeStore'
import { KnowledgeDetail, OutcomeFieldValue } from './KnowledgeDetail'
import { KnowledgeGraph } from './KnowledgeGraph'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { rowSubject } from '../../lib/rowSubject'
import { confirm, confirmDelete, promptInput } from '../../ui/dialog'
import { PageTitle } from '../../ui/PageTitle'
import { notify } from '../../app/appSdk'

type View = 'library' | 'graph' | 'intents' | 'tags' | 'conflicts'

function StatChip({ icon: Icon, label, value }: { icon: typeof Database; label: string; value: number | string }) {
  return (
    <div className="flex items-center gap-s rounded-lg bg-surface-container px-m py-2">
      <Icon size={15} className="text-primary shrink-0" />
      <span className="text-on-surface text-[0.9375rem] tabular-nums" style={fvs(500)}>{value}</span>
      <span className="text-on-surface-low text-[0.75rem]">{label}</span>
    </div>
  )
}

/** Embedding-coverage chip: surfaces semantic-search readiness. Off → muted hint;
 *  on with stragglers → a one-click backfill button; fully covered → quiet status. */
function EmbeddingChip({ stats, busy, onBackfill }: { stats: import('../../lib/api').KnowledgeStats; busy: boolean; onBackfill: (rebuild?: boolean) => void }) {
  const e = stats.embeddings
  if (!e?.enabled) {
    return (
      <div className="flex items-center gap-s rounded-lg bg-surface-container px-m py-2 text-on-surface-low" title="No embedding model active — search is keyword + entity-graph only. Set one in Settings › AI & Models.">
        <Boxes size={15} className="shrink-0" />
        <span className="text-[0.75rem]">semantic search off</span>
      </div>
    )
  }
  const embedded = e.embedded_items ?? 0
  const stale = e.stale_items ?? 0
  const behind = Math.max(0, stats.items - embedded)
  // Stale vectors (embedded under a previous model — now vector-dead) need a full
  // re-embed (rebuild=true), so they take priority over plain stragglers.
  if (stale > 0) {
    return (
      <button type="button" onClick={() => onBackfill(true)} disabled={busy}
        title={`${stale} item${stale === 1 ? '' : 's'} embedded with a previous model — click to re-embed all with ${e.model} (semantic search ignores stale vectors until then)`}
        className="flex items-center gap-s rounded-lg bg-surface-container px-m py-2 transition-colors hover:bg-surface-high disabled:opacity-60">
        <Boxes size={15} className={`shrink-0 ${busy ? 'animate-pulse text-primary' : 'text-warning'}`} />
        <span className="text-on-surface text-[0.9375rem] tabular-nums" style={fvs(500)}>{stale}</span>
        <span className="text-on-surface-low text-[0.75rem]">{busy ? 'embedding…' : 'stale — re-embed'}</span>
      </button>
    )
  }
  if (behind > 0) {
    return (
      <button type="button" onClick={() => onBackfill(false)} disabled={busy}
        title={`${behind} item${behind === 1 ? '' : 's'} not yet embedded — click to backfill (model: ${e.model})`}
        className="flex items-center gap-s rounded-lg bg-surface-container px-m py-2 transition-colors hover:bg-surface-high disabled:opacity-60">
        <Boxes size={15} className={`shrink-0 ${busy ? 'animate-pulse text-primary' : 'text-warning'}`} />
        <span className="text-on-surface text-[0.9375rem] tabular-nums" style={fvs(500)}>{embedded}/{stats.items}</span>
        <span className="text-on-surface-low text-[0.75rem]">{busy ? 'embedding…' : 'embed rest'}</span>
      </button>
    )
  }
  return (
    <div className="flex items-center gap-s rounded-lg bg-surface-container px-m py-2" title={`All items embedded for semantic search (model: ${e.model})`}>
      <Boxes size={15} className="text-primary shrink-0" />
      <span className="text-on-surface text-[0.9375rem] tabular-nums" style={fvs(500)}>{embedded}</span>
      <span className="text-on-surface-low text-[0.75rem]">embedded</span>
    </div>
  )
}

export function KnowledgeListPage({ onCreate, onOpenItem, onOpenSources, onOpenReports, query, setQuery }: { onCreate: () => void; onOpenItem: (id: string) => void; onOpenSources: () => void; onOpenReports: () => void } & Pick<RouteProps, 'query' | 'setQuery'>) {
  const [viewRaw, setView] = useQueryParam(query, setQuery, 'view', 'library', { replace: true })
  const view = viewRaw as View
  // search: the submitted query lives in the URL (?q); the input box is local
  // and seeded from it (search-on-submit, mirroring the old two-step).
  const [submitted, setSubmitted] = useQueryParam(query, setQuery, 'q', '', { replace: true })
  const [q, setQ] = useState(submitted)
  // The header search filters live now (no submit button): debounce the local box
  // into the URL-backed `submitted` query that drives the cached fetch.
  useEffect(() => {
    if (q === submitted) return
    const t = setTimeout(() => setSubmitted(q), 300)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q])
  const [typeFilter, setTypeFilter] = useQueryParam(query, setQuery, 'type', '')
  const [providerFilter, setProviderFilter] = useQueryParam(query, setQuery, 'provider', '')
  const [tagFilter, setTagFilter] = useQueryParam(query, setQuery, 'tag', '', { replace: true })
  // Graph tab: a clicked entity opens in the sidebar (with its items). Intents tab:
  // a clicked intent opens in the sidebar for view/edit. Library tab: the item.
  // Both are URL-backed (push, so Back closes / refresh restores):
  //  ?entity=<name>  — a graph entity (a plain name; complete in the URL).
  //  ?intent=<id>|__new__ — a Tier-3 intent; the id is authoritative, the object is
  //    resolved by IntentsView (which owns the list) into `selectedIntentObj`.
  const [entityTok, setEntityTok] = useQueryParam(query, setQuery, 'entity', '')
  const selectedEntity = entityTok || null
  const setSelectedEntity = (name: string | null) => setEntityTok(name || '')
  // Library tab: a clicked item PEEKS in the standard right side panel first
  // (?item=<id>, push — Back closes / refresh restores); the panel's expand
  // control is the road to the dedicated full page (#/knowledge/item/<id>).
  const [itemTok, setItemTok] = useQueryParam(query, setQuery, 'item', '')
  const peekId = itemTok || null
  const [intentTok, setIntentTok] = useQueryParam(query, setQuery, 'intent', '')
  // The resolved intent object for the open panel: a fresh blank for `__new__`, else
  // whatever IntentsView reports for the URL id (kept here so the panel renders even
  // on a deep-link/refresh once the list resolves it).
  const [resolvedIntent, setResolvedIntent] = useState<KnowledgeIntent | null>(null)
  const selectedIntent: KnowledgeIntent | null = intentTok === '__new__'
    ? blankIntent()
    : (intentTok ? resolvedIntent : null)
  const setSelectedIntent = (it: KnowledgeIntent | null) => {
    setResolvedIntent(it && it.id ? it : null)
    setIntentTok(it ? (it.id || '__new__') : '')
  }
  const [intentsReloadKey, setIntentsReloadKey] = useState(0)
  const refreshIntents = () => setIntentsReloadKey((k) => k + 1)

  // Resolve the peeked item (full body) whenever ?item changes; the list rows only
  // carry truncated previews, and the peek panel renders the real KnowledgeDetail.
  const [peekItem, setPeekItem] = useState<KnowledgeItem | null>(null)
  useEffect(() => {
    if (!peekId) { setPeekItem(null); return }
    let alive = true
    getKnowledge(peekId).then((d) => { if (alive) setPeekItem(d ?? null) }).catch(() => alive && setPeekItem(null))
    return () => { alive = false }
  }, [peekId])

  const [showArchived, setShowArchived] = useState(false)
  // Curation filter (KNOWLEDGE-LIBRARY S2, T2.1): '' | 'unread' | 'reading' | 'read'
  // | 'favorites'. Client-side like the type/provider/tag filters, so the full item set
  // stays loaded and the chips can gate themselves on what's actually present.
  // Without this, favoriting was WRITE-ONLY — you could star an item and then had no
  // way to see your stars.
  const [curationFilter, setCurationFilter] = useState('')
  // Multi-select for bulk curation (KNOWLEDGE-LIBRARY S2, T2.3). Deliberately NOT
  // URL-backed: a transient selection isn't meaningfully deep-linkable, and restoring
  // one on reload would re-arm a destructive-feeling state the user didn't ask for
  // (the same call ChatPage's session selection makes).
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [bulkBusy, setBulkBusy] = useState(false)
  const [bulkNote, setBulkNote] = useState('')
  const selecting = selected.size > 0
  const clearSelection = () => setSelected(new Set())
  const toggleSelected = (id: string) => setSelected((prev) => {
    const next = new Set(prev)
    next.has(id) ? next.delete(id) : next.add(id)
    return next
  })
  // Stale-while-revalidate: revisiting Knowledge shows the last items instantly and
  // refetches in the background (no "Loading…" flash except the genuine first load).
  // Type is filtered CLIENT-side (like provider/tag) so the full item set
  // stays loaded — otherwise selecting a type would leave only that type present and
  // the type chips (gated on >1 present) would vanish, trapping the user.
  // Collections (KNOWLEDGE-LIBRARY S1). URL-backed so a shelf is deep-linkable.
  const [collectionTok, setCollectionTok] = useQueryParam(query, setQuery, 'collection', '', { replace: true })
  const { data: collectionsData, refresh: refreshCollections } =
    useCachedData<KnowledgeCollection[]>('knowledge:collections', () => api.knowledgeCollections().catch(() => []))
  const collections = collectionsData ?? []
  const activeCollection = collectionTok ? collections.find((c) => c.id === collectionTok) ?? null : null

  // A selected shelf REPLACES the item source rather than filtering the loaded list:
  // a smart shelf's membership comes from a server-side query, so it isn't derivable
  // from whatever the library happened to have fetched.
  const itemsKey = collectionTok
    ? `knowledge:collection-items:${collectionTok}`
    : `knowledge:items:${submitted}:${showArchived ? 'arch' : ''}`
  // No `.catch(() => [])` on the shelf branch. Swallowing the rejection handed the hook an EMPTY
  // LIST, which this page cannot tell apart from a shelf that really has nothing in it — and the
  // search branch, which never caught, had the opposite half of the same bug: its failure fell to
  // `items === null` with `itemsLoading` false and rendered a BLANK region, no error text anywhere.
  // One `error` read answers both: the library says the read failed, and offers a retry.
  const { data: itemsData, error: itemsErr, loading: itemsLoading, refresh: refreshItems } =
    useCachedData(itemsKey, () => (collectionTok
      ? api.knowledgeCollectionItems(collectionTok, 200).then((r) => r.items)
      : listKnowledge({ q: submitted || undefined, includeArchived: showArchived })))
  const { data: statsData, refresh: refreshStats } = useCachedData('knowledge:stats', () => knowledgeStats())
  const items = itemsData ?? null
  const stats = statsData ?? null
  const load = () => { refreshItems(); refreshStats(); refreshCollections() }

  /** Apply one curation op to the selection. Reports per-item outcomes rather than a
   *  bare ok: a selection can go stale between the click and the request, and
   *  "38 shelved · 2 not found" is a useful answer where a wholesale failure is not. */
  const runBulk = async (op: KnowledgeBulkOp, args: Record<string, unknown> = {}, verb = 'Updated') => {
    if (!selected.size || bulkBusy) return
    setBulkBusy(true); setBulkNote('')
    try {
      const res = await api.knowledgeBulk(op, [...selected], args)
      const parts = [`${verb} ${res.changed.length}`]
      if (res.unchanged.length) parts.push(`${res.unchanged.length} already set`)
      if (res.missing.length) parts.push(`${res.missing.length} not found`)
      setBulkNote(parts.join(' · '))
      clearSelection()
      load()
    } catch (e) {
      // The endpoint refuses argument problems with a typed code — surface the real
      // reason instead of a generic failure, since "smart shelves resolve from their
      // query" is actionable and "bulk failed" is not.
      const msg = String((e as Error)?.message || e)
      setBulkNote(msg.includes('smart_collection_immutable')
        ? "A smart shelf fills itself from its query — items can't be added by hand."
        : 'Bulk action failed — nothing was changed.')
    } finally {
      setBulkBusy(false)
    }
  }

  // Shelf management. A smart shelf is created by naming a query, which is why the
  // prompt asks for one rather than offering a kind toggle with an empty box — a
  // smart shelf without a query matches nothing and reads as broken.
  async function createCollection() {
    const name = await promptInput({ title: 'New shelf', label: 'Shelf name', placeholder: 'e.g. Reading list', confirmLabel: 'Create' })
    if (!name) return
    const q = await promptInput({
      title: 'Smart shelf?',
      label: 'Search that fills it (leave empty for a manual shelf)',
      placeholder: 'e.g. rust ownership',
      confirmLabel: 'Create shelf',
    })
    try {
      const res = await api.createKnowledgeCollection(q ? { name, kind: 'smart', query: q } : { name })
      invalidateCache('knowledge:collections')
      refreshCollections()
      setCollectionTok(res.collection.id)
    } catch { /* the rail just doesn't gain a shelf */ }
  }

  // 🪤 EVERY WRITE ON THIS PAGE IS DATA-DRIVEN: nothing flips locally, the row re-renders from a
  // refetch. So a swallowed rejection did not leave a lying control — it left NOTHING. The shelf did
  // not gain the item, the read-state pill did not move, no message appeared, and the refetch ran
  // anyway, re-rendering the same state so the click read as "nothing happened, twice". That is the
  // contract `tools/toggleFailureReported` named; `app/reportingWrite` is its one implementation, and
  // it returns the outcome precisely so the refetch can be skipped when the write never landed.
  async function shelveItem(c: KnowledgeCollection, it: KnowledgeItem) {
    const ok = await reportingWrite(`add "${it.title || 'this item'}" to "${c.name}"`,
      () => api.addToKnowledgeCollection(c.id, [it.id]))
    if (!ok) return
    invalidateCache('knowledge:collections')
    refreshCollections()
  }

  async function unshelveItem(c: KnowledgeCollection, it: KnowledgeItem) {
    const ok = await reportingWrite(`remove "${it.title || 'this item'}" from "${c.name}"`,
      () => api.removeFromKnowledgeCollection(c.id, it.id))
    if (!ok) return
    invalidateCache(itemsKey)
    invalidateCache('knowledge:collections')
    refreshItems(); refreshCollections()
  }

  async function cycleReadState(it: KnowledgeItem) {
    const next = it.read_state === 'reading' ? 'read' : it.read_state === 'read' ? 'unread' : 'reading'
    const ok = await reportingWrite(`mark "${it.title || 'this item'}" as ${next}`,
      () => api.setKnowledgeReadState(it.id, next))
    if (!ok) return
    invalidateCache(itemsKey)
    refreshItems()
  }

  async function toggleFavorite(it: KnowledgeItem) {
    const ok = await reportingWrite(
      `${it.favorited ? 'unfavourite' : 'favourite'} "${it.title || 'this item'}"`,
      () => api.setKnowledgeFavorited(it.id, !it.favorited))
    if (!ok) return
    invalidateCache(itemsKey)
    refreshItems()
  }

  async function renameCollection(c: KnowledgeCollection) {
    const name = await promptInput({ title: 'Rename shelf', label: 'Shelf name', initial: c.name, confirmLabel: 'Rename' })
    if (!name || name === c.name) return
    const ok = await reportingWrite(`rename "${c.name}"`,
      () => api.updateKnowledgeCollection(c.id, { name }))
    if (!ok) return
    invalidateCache('knowledge:collections')
    refreshCollections()
  }

  async function removeCollection(c: KnowledgeCollection) {
    // Deleting a shelf keeps its items — say so, because "delete" next to a list of
    // documents reads as destructive and this one isn't.
    const ok = await confirm({
      title: `Delete "${c.name}"?`,
      body: 'The shelf goes away. The items on it stay in your library.',
      confirmLabel: 'Delete shelf',
      danger: true,
    })
    if (!ok) return
    // The shelf used to vanish, then reappear on the refetch with nothing said. On failure keep the
    // shelf selected (`setCollectionTok('')` is the success step) so the message names something the
    // user can still see.
    try { await api.deleteKnowledgeCollection(c.id) }
    catch (e) {
      notify(`Couldn't delete the shelf "${c.name}": ${String((e as Error)?.message || e)}`, 'error')
      invalidateCache('knowledge:collections'); refreshCollections()
      return
    }
    invalidateCache('knowledge:collections')
    refreshCollections()
    setCollectionTok('')
  }

  // Re-run the ingestion node-graph over items that never got enriched (e.g. created
  // while the model was unavailable). Refreshes the list so badges update as they drain.
  const [regenning, setRegenning] = useState(false)
  const regenerate = async () => {
    setRegenning(true)
    try { await api.regenerateKnowledgeIntelligence('missing') } catch { /* surfaced by reload */ }
    finally { setRegenning(false); load() }
  }

  // Backfill embeddings for items indexed before a model was available (semantic
  // search only covers embedded items). One click → embed the stragglers.
  const [embedding, setEmbedding] = useState(false)
  // rebuild=true re-embeds EVERY item (needed when vectors are stale after an embedding-
  // model switch); rebuild=false only fills in never-embedded stragglers.
  const backfillEmbeddings = async (rebuild = false) => {
    setEmbedding(true)
    try { await api.generateKnowledgeEmbeddings(rebuild) } catch { /* surfaced by reload */ }
    finally { setEmbedding(false); refreshStats() }
  }

  // Create-fast/enrich-async: items land in the list immediately and enrich in the
  // background. While any item is still processing, poll so its title/tags/summary
  // and badge update on the card without the user manually refreshing.
  const anyProcessing = useMemo(
    () => (items ?? []).some((it) => it.processing_status === 'queued' || it.processing_status === 'processing'),
    [items],
  )
  useEffect(() => {
    if (!anyProcessing) return
    // Refresh items AND stats while enriching — enrichment grows entities/relations/
    // embedded counts, so the stat chips should track it, not freeze until reload.
    const t = setInterval(() => { refreshItems(); refreshStats() }, 3000)
    return () => clearInterval(t)
  }, [anyProcessing, refreshItems, refreshStats])

  const typesPresent = useMemo(() => {
    const set = new Set<string>()
    for (const it of items ?? []) set.add(resolveType(it).key)
    return [...set]
  }, [items])
  const providersPresent = useMemo(() => {
    const set = new Set<string>()
    for (const it of items ?? []) set.add(it.provider || 'native')
    return [...set]
  }, [items])
  // Provider + tag are client-side filtered (the list endpoint isn't provider-aware);
  // type filtering stays server-side via the query param.
  const shown = useMemo(
    () => (items ?? []).filter((it) =>
      (!typeFilter || resolveType(it).key === typeFilter) &&
      (!providerFilter || (it.provider || 'native') === providerFilter) &&
      (!tagFilter || (it.tags ?? []).includes(tagFilter)) &&
      // A NULL read_state normalizes to 'unread' server-side, but be tolerant here too
      // so a pre-curation item can't slip past the unread filter it belongs in.
      (!curationFilter || (curationFilter === 'favorites'
        ? !!it.favorited
        : (it.read_state || 'unread') === curationFilter))),
    [items, typeFilter, providerFilter, tagFilter, curationFilter],
  )
  // Chips appear only when the state they filter is actually present — an always-on
  // "Favorites (0)" chip is a dead end that teaches the user nothing.
  const curationCounts = useMemo(() => {
    const base = items ?? []
    return {
      unread: base.filter((i) => (i.read_state || 'unread') === 'unread').length,
      reading: base.filter((i) => i.read_state === 'reading').length,
      read: base.filter((i) => i.read_state === 'read').length,
      favorites: base.filter((i) => !!i.favorited).length,
    }
  }, [items])
  const empty = stats && stats.items === 0

  return (
    <WorkbenchLayout
      scroll={view !== 'graph'}
      controls={view === 'library'
        ? <ListControls search={{ value: q, onChange: setQ, placeholder: 'Search knowledge', label: 'Search knowledge' }}
            results={{ count: (items ?? []).length, noun: 'items', active: !!submitted }} />
        : undefined}
      topBar={
        <TopBar
          keepCornerPadding
          left={<PageTitle>Knowledge</PageTitle>}
          // The ONE responsive header cluster (`HeaderActions`), like the other 26 header
          // right-slots in the app. This page was hand-rolling a plain `flex` div with a bare
          // `Segmented` + `IconButton` + `Button`, so nothing degraded: the slot measured
          // **651px inside a 155px content box** at 390px — 496px of overflow, with Conflicts,
          // Regenerate and "Add knowledge" off-screen and the "Knowledge" title squeezed to
          // zero width. Still 364px over at 834px. Through the cluster the row sheds
          // label → icon → `…` menu and the 5-option strip collapses to a single pill.
          right={
            <HeaderActions>
              <HeaderSegmented ariaLabel="Knowledge view" value={view} onChange={(v) => setView(v as View)}
                options={[{ key: 'library', label: 'Library', icon: Library }, { key: 'graph', label: 'Graph', icon: Network }, { key: 'intents', label: 'Intents', icon: Target }, { key: 'tags', label: 'Tags', icon: TagIcon }, { key: 'conflicts', label: 'Conflicts', icon: Scale }]} />
              {view === 'library' && (items?.length ?? 0) > 0 && (
                // `priority="low"` so this sheds into the `…` menu before the primary action —
                // it is a maintenance nicety, not the reason anyone opens the page.
                <HeaderControl icon={Sparkles} label="Regenerate intelligence" priority="low"
                  hint="Re-derive insights for items missing them"
                  disabled={regenning} onClick={regenning ? undefined : regenerate} />
              )}
              {/* The way IN to watched sources (WATCHED-SOURCES §2.4). It lives beside the
                  primary action rather than in the `view` strip because Sources is its own
                  destination (`#/knowledge/sources`) with a create flow of its own, not a
                  fifth lens on the same item list — and everything WS-2..WS-5 built was
                  unreachable until something pointed here. `priority="low"` so it sheds into
                  the `…` menu before "Add knowledge". */}
              <HeaderControl icon={Rss} label="Sources" priority="low"
                hint="Pages, feeds and folders that fill your library on their own"
                onClick={onOpenSources} />
                {/* The way IN to scheduled reports (WF2KNO-12). Same reasoning as Sources: its
                    own destination with its own create flow, and everything the runner writes
                    stays unreachable-by-configuration until something points here. */}
                <HeaderControl icon={FileClock} label="Reports" priority="low"
                  hint="Watch part of your library on a schedule and write up what is new"
                  onClick={onOpenReports} />
              {view === 'intents'
                ? <HeaderControl icon={Plus} label="New intent" variant="primary" priority="primary"
                    onClick={() => setSelectedIntent(blankIntent())} />
                : <HeaderControl icon={Plus} label="Add knowledge" variant="primary" priority="primary"
                    onClick={onCreate} />}
            </HeaderActions>
          }
        />
      }
      panel={
        view === 'library' && peekId ? (
          // Item PEEK: the standard right side panel, expand → the dedicated page.
          <SidePanel key={peekId} fillHeight storeKey="knowledge-panel-w" urlKey={{ key: 'item', setQuery }}
            icon={peekItem ? (() => { const tm = resolveType(peekItem); return <tm.icon size={18} style={{ color: tm.tone }} /> })() : <FileText size={18} className="text-primary" />}
            title={peekItem?.title || peekItem?.url_title || 'Knowledge item'}
            onExpand={() => onOpenItem(peekId)}
            onClose={() => setItemTok('')}>
            {peekItem ? (
              <div className="h-full min-h-[60vh]">
                <KnowledgeDetail
                  item={peekItem}
                  onChanged={() => { getKnowledge(peekId).then((d) => setPeekItem(d ?? null)).catch(() => {}); load() }}
                  onDeleted={() => { setItemTok(''); load() }}
                  onTagClick={(t) => { setItemTok(''); setTagFilter(t) }}
                />
              </div>
            ) : (
              <ListSkeleton rows={6} />
            )}
          </SidePanel>
        ) : view === 'graph' && selectedEntity ? (
          <SidePanel key={selectedEntity} fillHeight storeKey="knowledge-panel-w" urlKey={{ key: 'entity', setQuery }} icon={<Sparkles size={18} className="text-primary" />} title={selectedEntity} onClose={() => setSelectedEntity(null)}>
            <EntityDetail name={selectedEntity} onOpenItem={(id) => onOpenItem(id)} onSelectEntity={setSelectedEntity} />
          </SidePanel>
        ) : view === 'intents' && selectedIntent ? (
          <SidePanel key={selectedIntent.id || '__new__'} fillHeight storeKey="knowledge-panel-w" urlKey={{ key: 'intent', setQuery }} icon={<Target size={18} className="text-primary" />} title={selectedIntent.id ? (selectedIntent.goal || selectedIntent.id) : 'New intent'} onClose={() => setSelectedIntent(null)}>
            {selectedIntent.id
              ? <IntentDetail intent={selectedIntent} onChanged={refreshIntents} onClose={() => setSelectedIntent(null)} onOpenItem={(id) => onOpenItem(id)} />
              : <IntentEditor intent={selectedIntent} onClose={() => setSelectedIntent(null)} onSaved={() => { setSelectedIntent(null); refreshIntents() }} />}
          </SidePanel>
        ) : null
      }
    >
      {stats && (
        <div className="mx-auto w-full px-l pt-l" style={{ maxWidth: 'var(--content-width)' }}>
          <div className="flex flex-wrap items-center gap-s">
            <StatChip icon={Database} label="items" value={stats.items} />
            <StatChip icon={Sparkles} label="entities" value={stats.entities} />
            <StatChip icon={Network} label="relations" value={stats.relations} />
            <EmbeddingChip stats={stats} busy={embedding} onBackfill={backfillEmbeddings} />
          </div>
        </div>
      )}

      {/* Graph view is full-bleed: it fills the remaining workbench height and width
          (with its own zoom/pan), laying bare against the page background. */}
      {view === 'graph' && !empty && (
        <div className="flex-1 min-h-0 px-l pb-l pt-m">
          {/* `regenerate` is handed down because its own header control is `view === 'library'`-only:
              from the Graph tab the one action that turns items into entities is off screen, so the
              graph's empty state carries it. */}
          <KnowledgeGraph selectedId={selectedEntity} onSelect={setSelectedEntity}
            onRegenerate={regenerate} regenerating={regenning} />
        </div>
      )}

      {/* 🪤 `|| empty` is load-bearing: with 0 items the graph above is gated off by `!empty`, and
          this block used to be gated off by `view !== 'graph'`, so the Graph tab rendered NOTHING
          below the stat chips — measured at 116 characters of panel against the Library's 311. The
          shared "Knowledge base is empty" state below is the right answer for every view, graph
          included; it was simply unreachable from one of them. */}
      {(view !== 'graph' || empty) && (
      <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
        {itemsData === undefined && itemsErr ? (
              <LoadError what={collectionTok ? 'shelf items' : 'knowledge items'} error={itemsErr} onRetry={load} />
            ) : items === null ? (itemsLoading ? <ListSkeleton what={collectionTok ? 'shelf items' : 'knowledge items'} /> : null) : empty ? (
              <EmptyState icon={BookOpen} title="Knowledge base is empty" hint="Add notes, code gists, bookmarks, documents, images, audio, and video. Content is extracted, entities surfaced, and everything indexed for agents to retrieve." action={{ label: 'Add knowledge', onClick: onCreate, icon: Plus }} />
            ) : view === 'conflicts' ? (
              <ConflictPanel />
            ) : view === 'tags' ? (
              <TagManager onChanged={load} />
            ) : view === 'intents' ? (
              <IntentsView selectedId={selectedIntent?.id ?? null} onSelect={setSelectedIntent} reloadKey={intentsReloadKey} />
            ) : (
              <>
                <div className="mb-l flex flex-wrap gap-1.5">
                  {typesPresent.length > 1 && <FilterChip active={typeFilter === ''} onClick={() => setTypeFilter('')}>All types</FilterChip>}
                  {typesPresent.length > 1 && typesPresent.map((t) => { const tm = resolveType({ type: t as never }); return <FilterChip key={t} active={typeFilter === t} onClick={() => setTypeFilter(t)} tone={tm.tone}><tm.icon size={12} /> {tm.label}</FilterChip> })}
                  {providersPresent.length > 1 && <FilterChip active={providerFilter === ''} onClick={() => setProviderFilter('')}><Database size={12} /> All providers</FilterChip>}
                  {providersPresent.length > 1 && providersPresent.map((p) => <FilterChip key={p} active={providerFilter === p} onClick={() => setProviderFilter(p)}>{p === 'native' ? 'PersonalClaw' : p}</FilterChip>)}
                  {/* Curation chips: only for states actually present, and only once
                      the library is big enough for filtering to be the point. */}
                  {(items?.length ?? 0) > 1 && curationCounts.reading > 0 && (
                    <FilterChip active={curationFilter === 'reading'} onClick={() => setCurationFilter(curationFilter === 'reading' ? '' : 'reading')}>
                      <BookOpen size={12} /> Reading {curationCounts.reading}
                    </FilterChip>
                  )}
                  {(items?.length ?? 0) > 1 && curationCounts.unread > 0 && curationCounts.unread !== (items?.length ?? 0) && (
                    <FilterChip active={curationFilter === 'unread'} onClick={() => setCurationFilter(curationFilter === 'unread' ? '' : 'unread')}>
                      Unread {curationCounts.unread}
                    </FilterChip>
                  )}
                  {(items?.length ?? 0) > 1 && curationCounts.read > 0 && (
                    <FilterChip active={curationFilter === 'read'} onClick={() => setCurationFilter(curationFilter === 'read' ? '' : 'read')}>
                      Read {curationCounts.read}
                    </FilterChip>
                  )}
                  {curationCounts.favorites > 0 && (
                    <FilterChip active={curationFilter === 'favorites'} onClick={() => setCurationFilter(curationFilter === 'favorites' ? '' : 'favorites')} tone="var(--color-primary)">
                      <Star size={12} /> Favorites {curationCounts.favorites}
                    </FilterChip>
                  )}
                  <FilterChip active={showArchived} onClick={() => setShowArchived((v) => !v)}><Archive size={12} /> {showArchived ? 'Showing archived' : 'Show archived'}</FilterChip>
                  {tagFilter && <FilterChip active onClick={() => setTagFilter('')}># {tagFilter} <X size={11} /></FilterChip>}
                </div>
                {/* Collections rail. A shelf REPLACES the item source (a smart shelf's
                    membership is server-resolved), so selecting one also parks the
                    search/type chips above — they describe the library, not the shelf. */}
                <div className="mb-m flex flex-wrap items-center gap-1.5">
                  <FilterChip active={!collectionTok} onClick={() => setCollectionTok('')}>
                    <Library size={12} /> All items
                  </FilterChip>
                  {collections.map((c) => (
                    <FilterChip key={c.id} active={collectionTok === c.id} onClick={() => setCollectionTok(c.id)}>
                      {c.kind === 'smart' ? <Sparkles size={12} /> : <Layers size={12} />}
                      {' '}{c.name}
                      {c.kind === 'manual' && typeof c.item_count === 'number' ? ` ${c.item_count}` : ''}
                    </FilterChip>
                  ))}
                  {/* Reuses the rail's own chip rather than a bespoke button — it
                      lives in the chip row and should read as one of them. */}
                  <FilterChip active={false} onClick={createCollection}>
                    <Plus size={12} /> New shelf
                  </FilterChip>
                </div>
                {activeCollection && (
                  <div className="mb-m flex flex-wrap items-center gap-2">
                    <span data-type="label-l" className="text-on-surface">{activeCollection.name}</span>
                    <span className="text-on-surface-low text-[0.75rem]">
                      {activeCollection.kind === 'smart'
                        ? `Smart shelf — everything matching "${activeCollection.query}", kept current automatically.`
                        : 'Manual shelf — the items you put here.'}
                    </span>
                    <Button variant="ghost" size="xs" onClick={() => renameCollection(activeCollection)}>Rename</Button>
                    <Button variant="ghost" size="xs" onClick={() => removeCollection(activeCollection)}>Delete shelf</Button>
                  </div>
                )}
                {selecting && (
                  <div className="mb-m flex flex-wrap items-center gap-2 rounded-lg bg-surface-container px-3 py-2">
                    <span className="text-on-surface text-[0.8125rem]" style={fvs(500)}>
                      {selected.size} selected
                    </span>
                    <Button variant="tonal" size="xs" disabled={bulkBusy}
                      onClick={() => runBulk('read_state', { state: 'read' }, 'Marked read')}>
                      Mark read
                    </Button>
                    <Button variant="tonal" size="xs" disabled={bulkBusy}
                      onClick={() => runBulk('read_state', { state: 'unread' }, 'Marked unread')}>
                      Mark unread
                    </Button>
                    <Button variant="tonal" size="xs" disabled={bulkBusy}
                      onClick={() => runBulk('favorite', { value: true }, 'Favorited')}>
                      Favorite
                    </Button>
                    {/* Only MANUAL shelves: a smart shelf resolves membership from its
                        query, so adding by hand would be a write its own reads ignore. */}
                    {collections.filter((c) => c.kind === 'manual').map((c) => (
                      <Button key={c.id} variant="tonal" size="xs" disabled={bulkBusy}
                        onClick={() => runBulk('collect', { collection_id: c.id }, `Added to ${c.name}:`)}>
                        Add to {c.name}
                      </Button>
                    ))}
                    <Button variant="tonal" size="xs" disabled={bulkBusy}
                      onClick={() => runBulk(showArchived ? 'restore' : 'archive', {}, showArchived ? 'Restored' : 'Archived')}>
                      {showArchived ? 'Restore' : 'Archive'}
                    </Button>
                    <Button variant="secondary" size="xs" onClick={clearSelection} className="ml-auto">
                      Clear
                    </Button>
                  </div>
                )}
                {/* Outcome note lives OUTSIDE the bar so it survives the bar unmounting
                    when the selection clears on success. */}
                {bulkNote && !selecting && (
                  <div role="status" className="mb-m text-on-surface-var text-[0.8125rem]">{bulkNote}</div>
                )}
                {(shown?.length ?? 0) === 0 ? (
                  <EmptyState icon={Search} title="No matching items" hint="Try a different search or filter." />
                ) : (
                  // DSC-13: the library is the surface the atom names first, and the one
                  // whose rows were MEASURED variable — 34-76px across a real 5,000-item
                  // store (median 76), because the badge row and the wrapping meta line are
                  // both conditional. The list endpoint caps `limit` at 100 server-side and
                  // this client never asks for page 2, so a full page windows today and a
                  // future pagination change needs no second thought here.
                  <WindowedList
                    items={shown!}
                    rowKey={(it) => it.id}
                    rowHeights="variable"
                    estimateRowHeight={76}
                    gap={8}
                    noun="items"
                    findHint="use the Search knowledge field above, which searches contents as well as titles."
                    anchorKey={peekId ?? undefined}
                    className="flex flex-col gap-s"
                  >
                    {(it, i, listCtx) => {
                      const tm = resolveType(it)
                      // Right-click / long-press → scoped actions. This surface only
                      // opens an item (no delete/archive is wired here), so it's a
                      // single-item menu — still worth it for discoverability, and it
                      // calls the SAME handler as the row click.
                      const manualShelves = collections.filter((c) => c.kind === 'manual')
                      const menuItems: ContextMenuItem[] = [
                        { icon: <FileText size={15} />, label: 'Peek', onSelect: () => setItemTok(it.id) },
                        { icon: <Library size={15} />, label: 'Open full page', onSelect: () => onOpenItem(it.id) },
                        // Read state as an explicit cycle rather than a toggle — the
                        // middle state is the one a reading list exists for.
                        {
                          icon: <BookOpen size={15} />,
                          label: it.read_state === 'reading' ? 'Mark as read'
                            : it.read_state === 'read' ? 'Mark as unread' : 'Mark as reading',
                          onSelect: () => cycleReadState(it),
                        },
                        {
                          icon: <Star size={15} />,
                          label: it.favorited ? 'Remove favorite' : 'Favorite',
                          onSelect: () => toggleFavorite(it),
                        },
                        // Only MANUAL shelves are offered: a smart shelf's contents come
                        // from its query, so "add to" there would be silently ignored.
                        ...manualShelves
                          .filter((c) => c.id !== collectionTok)
                          .map((c) => ({
                            icon: <Layers size={15} />,
                            label: `Add to ${c.name}`,
                            onSelect: () => shelveItem(c, it),
                          })),
                        ...(activeCollection && activeCollection.kind === 'manual'
                          ? [{
                            icon: <X size={15} />,
                            label: `Remove from ${activeCollection.name}`,
                            onSelect: () => unshelveItem(activeCollection, it),
                          }]
                          : []),
                      ]
                      return (
                        <ContextMenu key={it.id} items={menuItems}>
                        {/* index=0 while windowed — see ui/WindowedList's ctx.windowed doc. */}
                        <ListRow index={listCtx.windowed ? 0 : i} accent={tm.tone} onClick={() => setItemTok(peekId === it.id ? '' : it.id)} label={it.title || it.url_title || '(untitled)'}>
                          {/* Selection tick. Hidden until hover or an active selection so
                              the list stays calm when nobody is curating; a wrapper stops
                              the click from also opening the item. */}
                          <span onClick={(e) => e.stopPropagation()}
                            className={`shrink-0 transition-opacity ${selecting ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 focus-within:opacity-100'}`}>
                            <Checkbox checked={selected.has(it.id)} onChange={() => toggleSelected(it.id)}
                              ariaLabel={`Select ${it.title || it.url_title || 'item'}`} />
                          </span>
                          {tm.key === 'image' && it.file_path
                            ? <img src={api.knowledgeItemThumbnailUrl(it.id)} alt="" className="shrink-0 size-10 rounded-lg object-cover bg-surface-container" onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none' }} />
                            : <span className="shrink-0 inline-flex size-10 items-center justify-center rounded-lg" style={{ background: `color-mix(in srgb, ${tm.tone} 16%, transparent)` }}><tm.icon size={19} style={{ color: tm.tone }} /></span>}
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-s">
                              {it.is_pinned && <Pin size={12} className="shrink-0 text-primary" style={{ fill: 'currentColor' }} />}
                              {/* Favorite gets its OWN glyph. It shared the Pin icon
                                  before, which made two deliberately distinct concepts
                                  (pin = float to the top of the list; favorite = a
                                  personal mark) indistinguishable on the row. */}
                              {it.favorited && <Star size={12} className="shrink-0 text-primary" style={{ fill: 'currentColor' }} aria-label="Favorite" />}
                              {/* Unread is the DEFAULT state, so it gets no marker —
                                  badging every fresh item would make the list noise.
                                  Only the two states a reader deliberately set show. */}
                              {it.read_state === 'reading' && (
                                <span className="shrink-0 inline-flex items-center gap-1 rounded-pill bg-surface-high px-1.5 text-[0.75rem] text-primary-emphasis" title="You're partway through this">
                                  <BookOpen size={10} /> reading
                                </span>
                              )}
                              <span className={`truncate text-[0.9375rem] ${it.read_state === 'read' ? 'text-on-surface-var' : 'text-on-surface'}`} style={fvs(it.read_state === 'read' ? 400 : 500)}>{it.title || it.url_title || '(untitled)'}</span>
                              {(it.processing_status === 'queued' || it.processing_status === 'processing') && (
                                <span className="shrink-0 inline-flex items-center gap-1 rounded-pill bg-surface-high px-1.5 text-primary-emphasis text-[0.75rem]"><Loader2 size={10} className="animate-spin" /> Enriching</span>
                              )}
                              {it.processing_status === 'failed' && (
                                <span className="shrink-0 inline-flex items-center gap-1 rounded-pill bg-surface-high px-1.5 text-danger text-[0.75rem]" title={it.processing_error || 'Enrichment failed'}><CircleAlert size={10} /> Failed</span>
                              )}
                              {/* Unreachable = the URL couldn't be fetched (network/DNS/timeout/HTTP error) —
                                  the link is saved; it's retryable, NOT an unexpected failure. */}
                              {it.processing_status === 'unreachable' && (
                                <span className="shrink-0 inline-flex items-center gap-1 rounded-pill bg-surface-high px-1.5 text-[0.75rem]" style={{ color: 'var(--color-warning)' }} title={`${it.processing_error || "Couldn't reach the site"} — open to retry`}><WifiOff size={10} /> Unreachable</span>
                              )}
                              {/* A genuine partial (e.g. insights model unavailable) is actionable — flag it
                                  so it's not mistaken for a fully-processed item. Benign skips (optional
                                  media steps with no model) are left unbadged. */}
                              {it.processing_status === 'partial' && !(it.processing_error || '').startsWith('Skipped (optional steps unavailable):') && (
                                <span className="shrink-0 inline-flex items-center gap-1 rounded-pill bg-surface-high px-1.5 text-[0.75rem]" style={{ color: 'var(--color-warning)' }} title={`${it.processing_error || 'Enrichment incomplete'} — open to regenerate`}><CircleAlert size={10} /> Incomplete</span>
                              )}
                              {it.is_archived && <span className="shrink-0 rounded-pill bg-surface-high px-1.5 text-on-surface-low text-[0.75rem]">Archived</span>}
                              {it._match_type && <span className="shrink-0 rounded-pill bg-surface-high px-1.5 text-on-surface-low text-[0.75rem]">{it._match_type}</span>}
                            </div>
                            <div className="mt-0.5 flex flex-wrap items-center gap-x-m gap-y-0.5 text-on-surface-low text-[0.8125rem]">
                              <span style={{ color: tm.tone }}>{typeLabel(it)}</span>
                              {/* PEP-7: a mirrored artifact's provenance is already the type label
                                  ("Artifact", its own icon + tone), so the generic provider pill is
                                  suppressed for it — "Artifact" beside a lowercase "artifacts" pill
                                  is the same fact twice in two vocabularies. */}
                              {it.provider && it.provider !== 'native' && !isArtifactItem(it) && <span className="rounded-pill bg-surface-high px-1.5 text-on-surface-var text-[0.75rem]">{it.provider}</span>}
                              {/* The way BACK to the real thing. A mirror is a search surface, so a
                                  hit that could only ever show extracted text would be a dead end —
                                  the artifact itself has the versions, the preview and the editor.
                                  An anchor (not the row's click) because the row peeks, and both
                                  behaviours have to remain reachable. */}
                              {isArtifactItem(it) && !!it.guid && (
                                <a href={`#/artifacts/${encodeURIComponent(it.guid)}`} onClick={(e) => e.stopPropagation()}
                                  className="inline-flex items-center gap-1 rounded-pill bg-surface-high px-1.5 text-[0.75rem] text-primary-emphasis transition-colors hover:bg-surface-container">
                                  <ExternalLink size={10} aria-hidden /> Open artifact
                                </a>
                              )}
                              {it.file_size != null && it.file_size > 0 && <span>· {fmtBytes(it.file_size)}</span>}
                              {/* No `· ` prefix: this span is `truncate` (white-space:nowrap) inside a
                                  `flex-wrap` row, so its intrinsic width always exceeds the space left on
                                  the label's line and it wraps to a line of its OWN before truncating.
                                  A leading separator there separates nothing — measured 26 of 26 rows at
                                  both 1440×900 and 390×844. The `file_size` dot above is short enough to
                                  stay on the label's line, so it keeps its separator. */}
                              {(it.summary || it.content) && <span className="truncate">{it.summary || it.content}</span>}
                            </div>
                          </div>
                          {(it.tags?.length ?? 0) > 0 && <div className="hidden md:flex shrink-0 gap-1">{it.tags!.slice(0, 2).map((t) => <button key={t} type="button" onClick={(e) => { e.stopPropagation(); setTagFilter(t) }} title={`Filter by "${t}"`} className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var text-[0.75rem] transition-colors hover:bg-surface-container hover:text-primary">{t}</button>)}</div>}
                          {it.updated_at && <span className="hidden sm:block shrink-0 text-on-surface-low text-[0.75rem]">{relTime(it.updated_at)}</span>}
                        </ListRow>
                        </ContextMenu>
                      )
                    }}
                  </WindowedList>
                )}
          </>
        )}
      </div>
      )}
    </WorkbenchLayout>
  )
}

/** Tier-3 intents: state a standing interest in plain language ("anything that helps
 *  my homelab"); the system decides per-item relevance and gathers typed-field
 *  outcomes. Click one to see everything it has gathered. */
/** Ask before deleting an intent, and say what goes with it.
 *
 *  Both delete controls — the row's icon button and the detail panel's — fired straight into
 *  `api.deleteKnowledgeIntent` on one click, with no confirmation anywhere. `confirmDelete` is this
 *  app's dominant form for that (fourteen callers: schedules, artifacts, providers, memories, tasks,
 *  triggers, workflow definitions…), and the file already applies the same discipline to shelves a few
 *  hundred lines up.
 *
 *  The body is not boilerplate. Deleting a shelf keeps its items, so `removeCollection` says so; an
 *  intent is the opposite — `delete_intent` cascades into `delete_intent_outcomes`, and an outcome is
 *  stored BY VALUE precisely so it "survives source-item deletion". So this is the only copy of what
 *  the intent gathered, and re-adding the intent does not bring it back. Whoever presses Delete on a
 *  row reading "12 gathered" should know that before, not after.
 *
 *  Module scope because the two controls live in two components; one sentence, said once. */
async function confirmIntentDelete(goal: string, gathered: number): Promise<boolean> {
  return confirmDelete('intent', rowSubject([goal], 40), {
    body: gathered > 0
      ? `Everything it gathered goes with it — ${gathered} ${gathered === 1 ? 'match' : 'matches'}, kept by value, so re-adding the intent will not bring them back.`
      : 'It has gathered nothing yet, so only the intent itself goes.',
  })
}

/** The blank intent a create flow opens on — ONE definition, shared by the header's
 *  "New intent" control and the empty state's on-ramp (PEP-2).
 *
 *  Written as a function rather than a constant because the object is handed to
 *  `setSelectedIntent` and then edited by `IntentEditor`; a shared frozen literal would
 *  make two create attempts share one draft. The empty `id` is load-bearing — it is what
 *  the panel branches on to render `IntentEditor` instead of `IntentDetail`. */
function blankIntent(): KnowledgeIntent {
  return { id: '', goal: '', enabled: true, enabled_for: [], propose_skill: false }
}

function IntentsView({ selectedId, onSelect, reloadKey }: {
  selectedId: string | null
  onSelect: (intent: KnowledgeIntent | null) => void
  reloadKey: number
}) {
  const [intents, setIntents] = useState<KnowledgeIntent[] | null>(null)
  // 🔴 THE HONESTY PRECONDITION for the CTA below. This loader used to `.catch(() => setIntents([]))`,
  // the harsher swallow: the rejection became the same empty array a fresh install produces, so a
  // failed `GET /api/knowledge/intents` rendered "No intents yet" — and, once the empty state grew a
  // create button, would have pitched "New intent" to a user whose intents merely could not be read.
  // Capturing it is what lets the error branch run FIRST. (Per-site rather than an
  // `ui/loadErrorState.test.tsx` ADOPTERS row: that rail's no-swallow check is FILE-scoped, and this
  // 1000-line page carries several deliberate decoration-read fallbacks it would flag.)
  const [intentsErr, setIntentsErr] = useState<unknown>(null)
  const load = () => api.knowledgeIntents()
    .then((r) => { setIntentsErr(null); setIntents(r.intents) })
    .catch((e) => { setIntentsErr(e); setIntents([]) })
  useEffect(() => { load() }, [reloadKey])
  // Deep-link / refresh restore: when the URL names ?intent=<id> but the parent has
  // no resolved object yet, hand it the matching intent from the loaded list so the
  // panel opens. (onSelect no-ops for __new__ / a stale id not in the list.)
  useEffect(() => {
    if (!selectedId || selectedId === '__new__' || !intents) return
    const match = intents.find((it) => it.id === selectedId)
    if (match) onSelect(match)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, intents])
  // The failed read is answered FIRST and on its own, ahead of both the skeleton and the list: a
  // rejection leaves `intents` as `[]`, which the empty branch below would otherwise render as
  // "No intents yet" — now under a create CTA, which is the lie made actionable. The teaching
  // paragraph is skipped too; there is nothing to teach about a list nobody could read.
  if (intentsErr) return <LoadError what="intents" error={intentsErr} onRetry={load} />
  if (intents === null) return <ListSkeleton rows={3} what="intents" />
  return (
    <div className="flex flex-col gap-s">
      <p className="text-on-surface-low text-[0.8125rem]">Tell PersonalClaw what to watch for in plain language. As you save items, it gathers what matches — with the specifics extracted as structured fields. Click an intent to see everything it found, or add one with “New intent”.</p>
      {intents.length === 0 && (
        // PEP-2: the empty state carries the SAME create seed the header's "New intent" control
        // uses — `blankIntent()`, one definition of the blank shape, so the two cannot drift into
        // two create paths. Before this the surface told the user the control's name in prose and
        // left them to find it in the top bar.
        <EmptyState icon={Target} title="No intents yet"
          hint='e.g. "anything that could improve my homelab", "ideas that help me learn agentic engineering", or "hints on how I should invest".'
          action={{ label: 'New intent', onClick: () => onSelect(blankIntent()), icon: Plus }} />
      )}
      {intents.map((it) => (
        <ListRow key={it.id} index={0} accent={it.id === selectedId ? 'var(--color-primary)' : undefined} onClick={() => onSelect(it)} label={it.goal || it.id}>
          <Target size={15} className="shrink-0 text-primary/80" />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              {/* 🪤 AN INTENT'S GOAL IS A SENTENCE THE USER WROTE, and `truncate` was eating most of
                  it with no way back. Measured at 390px on four real intents: two truncate, and the
                  longest showed **233px of the 651px it needs — 36% of what the user typed**. Nothing
                  truncates at 1440px, which is why a desktop sweep sees nothing here.
                  The asymmetry is the same one the tag row had: `ListRow`'s `label` already carries
                  the FULL goal, so assistive tech was the only reader getting the whole sentence while
                  a sighted phone user got a third of it. `title` on the truncating element is this
                  app's idiom for that (19 elements carry it; SystemWidget, RoutingPanel and the tag
                  row among them). */}
              <span className="truncate text-on-surface text-[0.9375rem]" title={it.goal || it.id}>{it.goal || it.id}</span>
              {!it.enabled && <span className="rounded-pill bg-surface-high px-1.5 text-on-surface-low text-[0.75rem]">off</span>}
              {it.propose_skill && <span className="rounded-pill bg-surface-high px-1.5 text-primary-emphasis text-[0.75rem]">proposes skill</span>}
            </div>
            <div className="truncate text-on-surface-low text-[0.75rem]">
              {(it.outcome_count ?? 0) > 0 ? `${it.outcome_count} gathered` : 'nothing gathered yet'}
              {(it.enabled_for?.length ?? 0) > 0 && ` · ${it.enabled_for!.join('/')}`}
            </div>
          </div>
          <span onClick={(e) => e.stopPropagation()}>
            {/* An icon-only DESTRUCTIVE control had no accessible name at all: axe `button-name`
                [critical] at both themes, and a screen-reader user heard "button" beside every intent.
                Named after its row the way `RowAction` does, through the shared cap so an intent whose
                goal is a sentence cannot turn the name into a paragraph (cycle 142's rule). */}
            <Button size="sm" variant="ghost" ariaLabel={`Delete intent: ${rowSubject([it.goal || it.id], 40)}`}
              onClick={async () => {
                if (!(await confirmIntentDelete(it.goal || it.id, it.outcome_count ?? 0))) return
                await api.deleteKnowledgeIntent(it.id)
                load()
              }}><Trash2 size={14} /></Button>
          </span>
        </ListRow>
      ))}
    </div>
  )
}

/** Render one outcome's typed fields type-aware (number/date/url/boolean/tags/string). */
function OutcomeCard({ o, onOpenItem }: { o: IntentOutcome; onOpenItem: (id: string) => void }) {
  return (
    <div className="rounded-lg border border-outline-variant/40 bg-surface-container p-m flex flex-col gap-s">
      {o.takeaway && <p className="text-on-surface text-[0.8125rem]">{o.takeaway}</p>}
      {(o.fields?.length ?? 0) > 0 && (
        <div className="grid grid-cols-[auto_1fr] gap-x-m gap-y-1 text-[0.8125rem]">
          {o.fields!.map((f, i) => (
            <Fragment key={i}>
              <span className="text-on-surface-low">{f.name}</span>
              <OutcomeFieldValue field={f} />
            </Fragment>
          ))}
        </div>
      )}
      <button type="button" onClick={() => o.item_id && onOpenItem(o.item_id)} disabled={!o.item_id}
        className="self-start inline-flex items-center gap-1 text-[0.75rem] text-on-surface-low hover:text-primary disabled:hover:text-on-surface-low disabled:opacity-70">
        <FileText size={12} />
        {o.item_id ? (o.item_title || 'source item') : `${o.item_title || 'source item'} (removed — insight kept)`}
      </button>
    </div>
  )
}

/** Intents-tab sidebar: an intent's gathered outcomes + a retroactive-run action. */
function IntentDetail({ intent, onChanged, onClose, onOpenItem }: {
  intent: KnowledgeIntent
  onChanged: () => void
  onClose: () => void
  onOpenItem: (id: string) => void
}) {
  const [outcomes, setOutcomes] = useState<IntentOutcome[] | null>(null)
  const [running, setRunning] = useState(false)
  const [genning, setGenning] = useState(false)
  const [note, setNote] = useState('')
  const load = () => api.knowledgeIntentOutcomes(intent.id).then((r) => setOutcomes(r.outcomes)).catch(() => setOutcomes([]))
  useEffect(() => { load() /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [intent.id])

  const run = async () => {
    setRunning(true); setNote('')
    try {
      const r = await api.runKnowledgeIntent(intent.id)
      setOutcomes(r.outcomes)
      // Report new-vs-already-matched honestly: a re-run that re-confirms existing
      // matches shouldn't claim them as "new". When the model couldn't evaluate some
      // items (e.g. a cold pool), say so rather than implying nothing matched.
      const errSuffix = r.errors ? ` (${r.errors} couldn't be evaluated — try again in a moment)` : ''
      setNote(
        r.new > 0 ? `Found ${r.new} new match${r.new === 1 ? '' : 'es'}.${errSuffix}`
        : r.matched > 0 ? `No new matches — ${r.matched} existing still match.${errSuffix}`
        : r.errors ? `Couldn't evaluate ${r.errors} item${r.errors === 1 ? '' : 's'} — the model may still be warming up. Try again in a moment.`
        : 'No matches in your existing items.')
      onChanged()
    } catch { setNote('Run failed.') } finally { setRunning(false) }
  }

  const generateSkill = async () => {
    setGenning(true); setNote('')
    try {
      const r = await api.generateSkillFromIntent(intent.id)
      setNote(`Created skill "${r.skill}" from ${outcomes?.length ?? 0} gathered item${(outcomes?.length ?? 0) === 1 ? '' : 's'}. Find it under Skills.`)
    } catch (e) { setNote(e instanceof Error ? e.message : 'Skill generation failed.') } finally { setGenning(false) }
  }

  const hasOutcomes = (outcomes?.length ?? 0) > 0

  return (
    <div className="flex flex-col gap-m p-l">
      <p className="text-on-surface text-[0.9375rem]">{intent.goal}</p>
      <div className="flex flex-wrap items-center gap-s">
        <Button size="sm" variant="secondary" onClick={run} disabled={running}><Play size={14} className={running ? 'animate-pulse' : ''} /> {running ? 'Running…' : 'Run on existing items'}</Button>
        {/* The blocked reason used to live on a WRAPPING span's title, where a hover finds it and
            a keyboard user never can — the button inside stayed natively disabled and out of the
            tab order. Both strings now ride the button: `title` explains the action,
            `disabledReason` explains the block, and Button joins them when it is soft-off. */}
        {intent.propose_skill && (
          <Button size="sm" variant="secondary" onClick={generateSkill}
            title="Synthesize a reusable skill from what this intent has gathered"
            disabled={genning || !hasOutcomes} disabledReason={!hasOutcomes && !genning ? 'Gather some matches first' : undefined}>
            <Sparkles size={14} className={genning ? 'animate-pulse' : ''} /> {genning ? 'Generating…' : 'Generate skill'}
          </Button>
        )}
        <span onClick={(e) => e.stopPropagation()}>
          <Button size="sm" variant="ghost" onClick={async () => {
            if (!(await confirmIntentDelete(intent.goal || intent.id, outcomes?.length ?? 0))) return
            await api.deleteKnowledgeIntent(intent.id)
            onChanged(); onClose()
          }}><Trash2 size={14} /> Delete</Button>
        </span>
      </div>
      {note && <p className="text-on-surface-low text-[0.8125rem]">{note}</p>}
      <div className="text-on-surface-low text-[0.75rem] uppercase tracking-wide">Gathered ({outcomes?.length ?? 0})</div>
      {outcomes === null ? <ListSkeleton rows={3} />
        : outcomes.length === 0 ? <p className="text-on-surface-low text-[0.8125rem]">Nothing gathered yet. Save items relevant to this intent, or run it on what you already have.</p>
        : <div className="flex flex-col gap-s">{outcomes.map((o) => <OutcomeCard key={o.id} o={o} onOpenItem={onOpenItem} />)}</div>}
    </div>
  )
}

/** Graph-tab sidebar: an entity + the knowledge items that mention it (clickable). */
function EntityDetail({ name, onOpenItem, onSelectEntity }: { name: string; onOpenItem: (id: string) => void; onSelectEntity?: (name: string) => void }) {
  const { data: items, loading } = useCachedData(`knowledge:entity-items:${name}`, () => api.knowledgeEntityItems(name))
  const { data: related } = useCachedData(`knowledge:entity-related:${name}`, () => api.knowledgeEntityRelated(name).then((r) => r.related))
  return (
    <div className="flex flex-col gap-l p-l">
      {(related?.length ?? 0) > 0 && (
        <div className="flex flex-col gap-s">
          <div className="text-on-surface-low text-[0.75rem] uppercase tracking-wide">Connected to</div>
          <div className="flex flex-col gap-1">
            {related!.map((r, i) => (
              <button key={i} type="button" onClick={() => onSelectEntity?.(r.name)}
                className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-surface-high">
                <Network size={13} className="shrink-0 text-primary/70" />
                <span className="truncate text-on-surface text-[0.8125rem]">{r.name}</span>
                <span className="ml-auto shrink-0 text-on-surface-low text-[0.75rem]">{r.outgoing ? '' : '← '}{r.relation_type}{r.outgoing ? ' →' : ''}</span>
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="flex flex-col gap-s">
        <div className="text-on-surface-low text-[0.75rem] uppercase tracking-wide">Mentioned in</div>
        {items === undefined ? (loading ? <ListSkeleton rows={3} /> : null)
          : items.length === 0 ? <p className="text-on-surface-low text-[0.8125rem]">No items reference this entity.</p>
          : items.map((it, i) => {
              const tm = resolveType(it)
              return (
                <ListRow key={it.id} index={i} accent={tm.tone} onClick={() => onOpenItem(it.id)} label={it.title || it.url_title || '(untitled)'}>
                  <tm.icon size={16} style={{ color: tm.tone }} className="shrink-0" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-on-surface text-[0.8125rem]">{it.title || it.url_title || '(untitled)'}</div>
                    <div className="truncate text-on-surface-low text-[0.75rem]" style={{ color: tm.tone }}>{typeLabel(it)}</div>
                  </div>
                </ListRow>
              )
            })}
      </div>
    </div>
  )
}

/** Natural-language intent composer — the user writes ONE sentence; everything else
 *  (relevance, the fields to extract) is the LLM's job at ingest time. */
function IntentEditor({ intent, onClose, onSaved }: { intent: KnowledgeIntent; onClose: () => void; onSaved: () => void }) {
  const [goal, setGoal] = useState(intent.goal ?? '')
  const [enabledFor, setEnabledFor] = useState((intent.enabled_for ?? []).join(', '))
  const [proposeSkill, setProposeSkill] = useState(!!intent.propose_skill)
  const [err, setErr] = useState('')
  const [saving, setSaving] = useState(false)

  async function save() {
    setErr('')
    const g = goal.trim()
    if (!g) { setErr('Describe what you want to track.'); return }
    setSaving(true)
    try {
      await api.upsertKnowledgeIntent({
        // New intents omit id — the backend derives the slug from the goal (single
        // source of truth). Edits keep their existing id.
        id: intent.id || undefined, goal: g, enabled: true, propose_skill: proposeSkill,
        enabled_for: enabledFor.split(',').map((s) => s.trim()).filter(Boolean),
      })
      onSaved()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Save failed') } finally { setSaving(false) }
  }

  return (
    <div className="p-l flex flex-col gap-m">
      <div className="flex items-center justify-between">
        <span className="text-on-surface text-[0.9375rem]">New intent</span>
        {/* Icon-only close: with no name it announced as bare "button". A CONSTANT name is right
            here (one per panel), unlike the per-item buttons this sweep also found. */}
        <button type="button" aria-label="Close the intent editor" onClick={onClose} className="text-on-surface-low hover:text-on-surface"><X size={16} /></button>
      </div>
      <div className="flex flex-col gap-1.5">
        <label className="text-on-surface-low text-[0.75rem] uppercase tracking-wide">What do you want to track?</label>
        <textarea aria-label="What do you want to track?" value={goal} onChange={(e) => setGoal(e.target.value)} rows={4} autoFocus
          placeholder={'e.g. "anything that could improve my homelab self-hosted setup"'}
          className="rounded-md bg-surface p-3 text-[0.8125rem] text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 resize-none" />
        <p className="text-on-surface-low text-[0.75rem]">Plain language. As items are saved, PersonalClaw decides what's relevant and pulls out the useful specifics for you — no need to define fields.</p>
      </div>
      <div className="flex flex-col gap-1.5">
        <label className="text-on-surface-low text-[0.75rem] uppercase tracking-wide">Limit to types (optional)</label>
        <input aria-label="Limit to types (optional)" value={enabledFor} onChange={(e) => setEnabledFor(e.target.value)} placeholder="comma-separated, blank = all types"
          className="h-9 rounded-md bg-surface px-3 text-[0.8125rem] text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
      </div>
      <label className="flex items-start gap-2 text-on-surface-var text-[0.8125rem]">
        <input type="checkbox" className="mt-0.5" checked={proposeSkill} onChange={(e) => setProposeSkill(e.target.checked)} />
        <span>Offer to build a skill from this intent — adds a “Generate skill” action that distills what it has gathered into a reusable skill.</span>
      </label>
      {err && <FieldError>{err}</FieldError>}
      <div className="flex justify-end gap-s"><Button size="sm" variant="ghost" onClick={onClose}>Cancel</Button><Button size="sm" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save intent'}</Button></div>
    </div>
  )
}

/** One filter/collection chip. Selected chips carry the accent; the rest are neutral.
 *
 *  🪤 A SELECTED CHIP PUT THE ACCENT IN BOTH THE TEXT AND THE BACKGROUND, which is what broke it:
 *  coral text on a 20% coral tint measured **3.33:1** in light mode — below the 4.5 floor and
 *  reported `[serious] color-contrast` by axe. The tint raises the backdrop's luminance toward the
 *  text it sits under, so the two converge. Dark mode was never affected (6.99:1) because there the
 *  tint darkens the backdrop *away* from the light accent.
 *
 *  The selected state now uses the token pair the design system provides for exactly this — an
 *  accent-tinted container with ink chosen to sit on it (`--color-primary-container` /
 *  `--color-on-primary-container`, 13.1:1 in light, 10.43:1 in dark) — instead of hand-rolling a
 *  `color-mix` tint and reusing the accent as ink.
 *
 *  A per-type `tone` (the Note/Bookmark/Gist hues) is left EXACTLY as it was. There is no
 *  `<tone>-container` sibling to pair it with, and putting a type's hue on the coral container
 *  would be both a new contrast risk and visually wrong. Those chips do not render selected on this
 *  surface in any state measured here; if one ever fails, it needs its own container value, not a
 *  guess made from this one. */
function FilterChip({ active, onClick, tone, children }: { active: boolean; onClick: () => void; tone?: string; children: React.ReactNode }) {
  const selected = tone
    // Untouched: a type-toned chip keeps its own tint + ink.
    ? { background: `color-mix(in srgb, ${tone} 20%, transparent)`, color: tone }
    : { background: 'var(--color-primary-container)', color: 'var(--color-on-primary-container)' }
  return (
    <button type="button" onClick={onClick} aria-pressed={active}
      className="inline-flex items-center gap-1 rounded-pill px-m h-8 text-[0.8125rem] transition-colors"
      style={active ? selected : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-var)' }}>
      {children}
    </button>
  )
}
