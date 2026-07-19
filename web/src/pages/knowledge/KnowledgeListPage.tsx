import { Fragment, useEffect, useMemo, useState } from 'react'
import { BookOpen, Plus, Search, Database, Sparkles, Network, Library, Trash2, Target, X, Pin, Archive, Play, FileText, Loader2, CircleAlert, Boxes, WifiOff } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { WorkbenchLayout } from '../../ui/WorkbenchLayout'
import { Button } from '../../ui/Button'
import { EmptyState, ListRow, ListSkeleton } from '../../ui/ListScaffold'
import { SidePanel } from '../../ui/SidePanel'
import { ListControls } from '../../ui/ListControls'
import { IconButton } from '../../ui/IconButton'
import { ContextMenu, type ContextMenuItem } from '../../ui/motion'
import { Segmented } from '../tasks/formControls'
import { api, type KnowledgeIntent, type IntentOutcome, type KnowledgeItem } from '../../lib/api'
import { resolveType, relTime, fmtBytes, typeLabel } from './knowledgeMeta'
import { listKnowledge, knowledgeStats, getKnowledge } from './knowledgeStore'
import { KnowledgeDetail } from './KnowledgeDetail'
import { KnowledgeGraph } from './KnowledgeGraph'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { useCachedData } from '../../lib/useCachedData'

type View = 'library' | 'graph' | 'intents'

function StatChip({ icon: Icon, label, value }: { icon: typeof Database; label: string; value: number | string }) {
  return (
    <div className="flex items-center gap-s rounded-lg bg-surface-container px-m py-2">
      <Icon size={15} className="text-primary shrink-0" />
      <span className="text-on-surface text-[0.9375rem] tabular-nums" style={{ fontVariationSettings: '"wght" 500' }}>{value}</span>
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
        <span className="text-on-surface text-[0.9375rem] tabular-nums" style={{ fontVariationSettings: '"wght" 500' }}>{stale}</span>
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
        <span className="text-on-surface text-[0.9375rem] tabular-nums" style={{ fontVariationSettings: '"wght" 500' }}>{embedded}/{stats.items}</span>
        <span className="text-on-surface-low text-[0.75rem]">{busy ? 'embedding…' : 'embed rest'}</span>
      </button>
    )
  }
  return (
    <div className="flex items-center gap-s rounded-lg bg-surface-container px-m py-2" title={`All items embedded for semantic search (model: ${e.model})`}>
      <Boxes size={15} className="text-primary shrink-0" />
      <span className="text-on-surface text-[0.9375rem] tabular-nums" style={{ fontVariationSettings: '"wght" 500' }}>{embedded}</span>
      <span className="text-on-surface-low text-[0.75rem]">embedded</span>
    </div>
  )
}

export function KnowledgeListPage({ onCreate, onOpenItem, query, setQuery }: { onCreate: () => void; onOpenItem: (id: string) => void } & Pick<RouteProps, 'query' | 'setQuery'>) {
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
    ? { id: '', goal: '', enabled: true, enabled_for: [], propose_skill: false }
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
  // Stale-while-revalidate: revisiting Knowledge shows the last items instantly and
  // refetches in the background (no "Loading…" flash except the genuine first load).
  // Type is filtered CLIENT-side (like provider/tag) so the full item set
  // stays loaded — otherwise selecting a type would leave only that type present and
  // the type chips (gated on >1 present) would vanish, trapping the user.
  const itemsKey = `knowledge:items:${submitted}:${showArchived ? 'arch' : ''}`
  const { data: itemsData, loading: itemsLoading, refresh: refreshItems } =
    useCachedData(itemsKey, () => listKnowledge({ q: submitted || undefined, includeArchived: showArchived }))
  const { data: statsData, refresh: refreshStats } = useCachedData('knowledge:stats', () => knowledgeStats())
  const items = itemsData ?? null
  const stats = statsData ?? null
  const load = () => { refreshItems(); refreshStats() }

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
      (!tagFilter || (it.tags ?? []).includes(tagFilter))),
    [items, typeFilter, providerFilter, tagFilter],
  )
  const empty = stats && stats.items === 0

  return (
    <WorkbenchLayout
      scroll={view !== 'graph'}
      controls={view === 'library'
        ? <ListControls search={{ value: q, onChange: setQ, placeholder: 'Search knowledge', label: 'Search knowledge' }} />
        : undefined}
      topBar={
        <TopBar
          keepCornerPadding
          left={<span data-type="title-l" className="text-on-surface">Knowledge</span>}
          right={
            <div className="flex items-center gap-s">
              <Segmented options={[{ key: 'library', label: 'Library', icon: Library }, { key: 'graph', label: 'Graph', icon: Network }, { key: 'intents', label: 'Intents', icon: Target }]} value={view} onChange={(v) => setView(v as View)} />
              {view === 'library' && (items?.length ?? 0) > 0 && (
                <IconButton icon={Sparkles} size={40}
                  label="Regenerate intelligence (items missing insights)"
                  onClick={regenning ? undefined : regenerate}
                  className={regenning ? 'animate-pulse pointer-events-none opacity-50' : ''} />
              )}
              {view === 'intents'
                ? <Button size="sm" className="h-10" onClick={() => setSelectedIntent({ id: '', goal: '', enabled: true, enabled_for: [], propose_skill: false })}><Plus size={16} /> New intent</Button>
                : <Button size="sm" className="h-10" onClick={onCreate}><Plus size={16} /> Add knowledge</Button>}
            </div>
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
          <KnowledgeGraph selectedId={selectedEntity} onSelect={setSelectedEntity} />
        </div>
      )}

      {view !== 'graph' && (
      <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
        {items === null ? (itemsLoading ? <ListSkeleton /> : null) : empty ? (
              <EmptyState icon={BookOpen} title="Knowledge base is empty" hint="Add notes, code gists, bookmarks, documents, images, audio, and video. Content is extracted, entities surfaced, and everything indexed for agents to retrieve." action={{ label: 'Add knowledge', onClick: onCreate, icon: Plus }} />
            ) : view === 'intents' ? (
              <IntentsView selectedId={selectedIntent?.id ?? null} onSelect={setSelectedIntent} reloadKey={intentsReloadKey} />
            ) : (
              <>
                <div className="mb-l flex flex-wrap gap-1.5">
                  {typesPresent.length > 1 && <FilterChip active={typeFilter === ''} onClick={() => setTypeFilter('')}>All types</FilterChip>}
                  {typesPresent.length > 1 && typesPresent.map((t) => { const tm = resolveType({ type: t as never }); return <FilterChip key={t} active={typeFilter === t} onClick={() => setTypeFilter(t)} tone={tm.tone}><tm.icon size={12} /> {tm.label}</FilterChip> })}
                  {providersPresent.length > 1 && <FilterChip active={providerFilter === ''} onClick={() => setProviderFilter('')}><Database size={12} /> All providers</FilterChip>}
                  {providersPresent.length > 1 && providersPresent.map((p) => <FilterChip key={p} active={providerFilter === p} onClick={() => setProviderFilter(p)}>{p === 'native' ? 'PersonalClaw' : p}</FilterChip>)}
                  <FilterChip active={showArchived} onClick={() => setShowArchived((v) => !v)}><Archive size={12} /> {showArchived ? 'Showing archived' : 'Show archived'}</FilterChip>
                  {tagFilter && <FilterChip active onClick={() => setTagFilter('')}># {tagFilter} <X size={11} /></FilterChip>}
                </div>
                {(shown?.length ?? 0) === 0 ? (
                  <EmptyState icon={Search} title="No matching items" hint="Try a different search or filter." />
                ) : (
                  <div className="flex flex-col gap-s">
                    {shown!.map((it, i) => {
                      const tm = resolveType(it)
                      // Right-click / long-press → scoped actions. This surface only
                      // opens an item (no delete/archive is wired here), so it's a
                      // single-item menu — still worth it for discoverability, and it
                      // calls the SAME handler as the row click.
                      const menuItems: ContextMenuItem[] = [
                        { icon: <FileText size={15} />, label: 'Peek', onSelect: () => setItemTok(it.id) },
                        { icon: <Library size={15} />, label: 'Open full page', onSelect: () => onOpenItem(it.id) },
                      ]
                      return (
                        <ContextMenu key={it.id} items={menuItems}>
                        <ListRow index={i} accent={tm.tone} onClick={() => setItemTok(peekId === it.id ? '' : it.id)}>
                          {tm.key === 'image' && it.file_path
                            ? <img src={api.knowledgeItemThumbnailUrl(it.id)} alt="" className="shrink-0 size-10 rounded-lg object-cover bg-surface-container" onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none' }} />
                            : <span className="shrink-0 inline-flex size-10 items-center justify-center rounded-lg" style={{ background: `color-mix(in srgb, ${tm.tone} 16%, transparent)` }}><tm.icon size={19} style={{ color: tm.tone }} /></span>}
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-s">
                              {it.is_pinned && <Pin size={12} className="shrink-0 text-primary" style={{ fill: 'currentColor' }} />}
                              <span className="truncate text-on-surface text-[0.9375rem]" style={{ fontVariationSettings: '"wght" 500' }}>{it.title || it.url_title || '(untitled)'}</span>
                              {(it.processing_status === 'queued' || it.processing_status === 'processing') && (
                                <span className="shrink-0 inline-flex items-center gap-1 rounded-pill bg-surface-high px-1.5 text-primary text-[0.65rem]"><Loader2 size={10} className="animate-spin" /> Enriching</span>
                              )}
                              {it.processing_status === 'failed' && (
                                <span className="shrink-0 inline-flex items-center gap-1 rounded-pill bg-surface-high px-1.5 text-danger text-[0.65rem]" title={it.processing_error || 'Enrichment failed'}><CircleAlert size={10} /> Failed</span>
                              )}
                              {/* Unreachable = the URL couldn't be fetched (network/DNS/timeout/HTTP error) —
                                  the link is saved; it's retryable, NOT an unexpected failure. */}
                              {it.processing_status === 'unreachable' && (
                                <span className="shrink-0 inline-flex items-center gap-1 rounded-pill bg-surface-high px-1.5 text-[0.65rem]" style={{ color: 'var(--color-warning)' }} title={`${it.processing_error || "Couldn't reach the site"} — open to retry`}><WifiOff size={10} /> Unreachable</span>
                              )}
                              {/* A genuine partial (e.g. insights model unavailable) is actionable — flag it
                                  so it's not mistaken for a fully-processed item. Benign skips (optional
                                  media steps with no model) are left unbadged. */}
                              {it.processing_status === 'partial' && !(it.processing_error || '').startsWith('Skipped (optional steps unavailable):') && (
                                <span className="shrink-0 inline-flex items-center gap-1 rounded-pill bg-surface-high px-1.5 text-[0.65rem]" style={{ color: 'var(--color-warning)' }} title={`${it.processing_error || 'Enrichment incomplete'} — open to regenerate`}><CircleAlert size={10} /> Incomplete</span>
                              )}
                              {it.is_archived && <span className="shrink-0 rounded-pill bg-surface-high px-1.5 text-on-surface-low text-[0.65rem]">archived</span>}
                              {it._match_type && <span className="shrink-0 rounded-pill bg-surface-high px-1.5 text-on-surface-low text-[0.65rem]">{it._match_type}</span>}
                            </div>
                            <div className="mt-0.5 flex flex-wrap items-center gap-x-m gap-y-0.5 text-on-surface-low text-[0.8125rem]">
                              <span style={{ color: tm.tone }}>{typeLabel(it)}</span>
                              {it.provider && it.provider !== 'native' && <span className="rounded-pill bg-surface-high px-1.5 text-on-surface-var text-[0.65rem]">{it.provider}</span>}
                              {it.file_size != null && it.file_size > 0 && <span>· {fmtBytes(it.file_size)}</span>}
                              {(it.summary || it.content) && <span className="truncate">· {it.summary || it.content}</span>}
                            </div>
                          </div>
                          {(it.tags?.length ?? 0) > 0 && <div className="hidden md:flex shrink-0 gap-1">{it.tags!.slice(0, 2).map((t) => <button key={t} type="button" onClick={(e) => { e.stopPropagation(); setTagFilter(t) }} title={`Filter by "${t}"`} className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var text-[0.7rem] transition-colors hover:bg-surface-container hover:text-primary">{t}</button>)}</div>}
                          {it.updated_at && <span className="hidden sm:block shrink-0 text-on-surface-low text-[0.75rem]">{relTime(it.updated_at)}</span>}
                        </ListRow>
                        </ContextMenu>
                      )
                    })}
                  </div>
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
function IntentsView({ selectedId, onSelect, reloadKey }: {
  selectedId: string | null
  onSelect: (intent: KnowledgeIntent | null) => void
  reloadKey: number
}) {
  const [intents, setIntents] = useState<KnowledgeIntent[] | null>(null)
  const load = () => api.knowledgeIntents().then((r) => setIntents(r.intents)).catch(() => setIntents([]))
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
  if (intents === null) return <ListSkeleton rows={3} />
  return (
    <div className="flex flex-col gap-s">
      <p className="text-on-surface-low text-[0.8125rem]">Tell PersonalClaw what to watch for in plain language. As you save items, it gathers what matches — with the specifics extracted as structured fields. Click an intent to see everything it found, or add one with “New intent”.</p>
      {intents.length === 0 && <EmptyState icon={Target} title="No intents yet" hint='e.g. "anything that could improve my homelab", "ideas that help me learn agentic engineering", or "hints on how I should invest".' />}
      {intents.map((it) => (
        <ListRow key={it.id} index={0} accent={it.id === selectedId ? 'var(--color-primary)' : undefined} onClick={() => onSelect(it)}>
          <Target size={15} className="shrink-0 text-primary/80" />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span className="truncate text-on-surface text-[0.9375rem]">{it.goal || it.id}</span>
              {!it.enabled && <span className="rounded-pill bg-surface-high px-1.5 text-on-surface-low text-[0.65rem]">off</span>}
              {it.propose_skill && <span className="rounded-pill bg-surface-high px-1.5 text-primary/80 text-[0.65rem]">proposes skill</span>}
            </div>
            <div className="truncate text-on-surface-low text-[0.75rem]">
              {(it.outcome_count ?? 0) > 0 ? `${it.outcome_count} gathered` : 'nothing gathered yet'}
              {(it.enabled_for?.length ?? 0) > 0 && ` · ${it.enabled_for!.join('/')}`}
            </div>
          </div>
          <span onClick={(e) => e.stopPropagation()}>
            <Button size="sm" variant="ghost" onClick={() => api.deleteKnowledgeIntent(it.id).then(load)}><Trash2 size={14} /></Button>
          </span>
        </ListRow>
      ))}
    </div>
  )
}

/** Render one outcome's typed fields type-aware (number/date/url/boolean/tags/string). */
function OutcomeFieldValue({ field }: { field: { type: string; value: unknown } }) {
  const { type, value } = field
  if (value === null || value === undefined || value === '') return <span className="text-on-surface-low">—</span>
  if (type === 'boolean') return <span className="text-on-surface">{value ? 'Yes' : 'No'}</span>
  if (type === 'number') return <span className="text-on-surface tabular-nums">{String(value)}</span>
  if (type === 'url') return <a href={String(value)} target="_blank" rel="noreferrer" className="text-primary underline decoration-primary/40 break-all">{String(value)}</a>
  if (type === 'tags' && Array.isArray(value)) return <span className="flex flex-wrap gap-1">{value.map((t, i) => <span key={i} className="rounded-pill bg-surface-high px-2 h-5 inline-flex items-center text-on-surface-var text-[0.7rem]">{String(t)}</span>)}</span>
  return <span className="text-on-surface break-words">{String(value)}</span>
}

function OutcomeCard({ o, onOpenItem }: { o: IntentOutcome; onOpenItem: (id: string) => void }) {
  return (
    <div className="rounded-lg border border-outline-variant/40 bg-surface-container p-m flex flex-col gap-s">
      {o.takeaway && <p className="text-on-surface text-[0.875rem]">{o.takeaway}</p>}
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
        {intent.propose_skill && (
          <span title={hasOutcomes ? 'Synthesize a reusable skill from what this intent has gathered' : 'Gather some matches first'}>
            <Button size="sm" variant="secondary" onClick={generateSkill} disabled={genning || !hasOutcomes}>
              <Sparkles size={14} className={genning ? 'animate-pulse' : ''} /> {genning ? 'Generating…' : 'Generate skill'}
            </Button>
          </span>
        )}
        <span onClick={(e) => e.stopPropagation()}>
          <Button size="sm" variant="ghost" onClick={() => api.deleteKnowledgeIntent(intent.id).then(() => { onChanged(); onClose() })}><Trash2 size={14} /> Delete</Button>
        </span>
      </div>
      {note && <p className="text-on-surface-low text-[0.8125rem]">{note}</p>}
      <div className="text-on-surface-low text-[0.7rem] uppercase tracking-wide">Gathered ({outcomes?.length ?? 0})</div>
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
          <div className="text-on-surface-low text-[0.7rem] uppercase tracking-wide">Connected to</div>
          <div className="flex flex-col gap-1">
            {related!.map((r, i) => (
              <button key={i} type="button" onClick={() => onSelectEntity?.(r.name)}
                className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-surface-high">
                <Network size={13} className="shrink-0 text-primary/70" />
                <span className="truncate text-on-surface text-[0.8125rem]">{r.name}</span>
                <span className="ml-auto shrink-0 text-on-surface-low text-[0.7rem]">{r.outgoing ? '' : '← '}{r.relation_type}{r.outgoing ? ' →' : ''}</span>
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="flex flex-col gap-s">
        <div className="text-on-surface-low text-[0.7rem] uppercase tracking-wide">Mentioned in</div>
        {items === undefined ? (loading ? <ListSkeleton rows={3} /> : null)
          : items.length === 0 ? <p className="text-on-surface-low text-[0.8125rem]">No items reference this entity.</p>
          : items.map((it, i) => {
              const tm = resolveType(it)
              return (
                <ListRow key={it.id} index={i} accent={tm.tone} onClick={() => onOpenItem(it.id)}>
                  <tm.icon size={16} style={{ color: tm.tone }} className="shrink-0" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-on-surface text-[0.875rem]">{it.title || it.url_title || '(untitled)'}</div>
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
        <button type="button" onClick={onClose} className="text-on-surface-low hover:text-on-surface"><X size={16} /></button>
      </div>
      <div className="flex flex-col gap-1.5">
        <label className="text-on-surface-low text-[0.7rem] uppercase tracking-wide">What do you want to track?</label>
        <textarea aria-label="What do you want to track?" value={goal} onChange={(e) => setGoal(e.target.value)} rows={4} autoFocus
          placeholder={'e.g. "anything that could improve my homelab self-hosted setup"'}
          className="rounded-md bg-surface p-3 text-[0.875rem] text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 resize-none" />
        <p className="text-on-surface-low text-[0.75rem]">Plain language. As items are saved, PersonalClaw decides what's relevant and pulls out the useful specifics for you — no need to define fields.</p>
      </div>
      <div className="flex flex-col gap-1.5">
        <label className="text-on-surface-low text-[0.7rem] uppercase tracking-wide">Limit to types (optional)</label>
        <input aria-label="Limit to types (optional)" value={enabledFor} onChange={(e) => setEnabledFor(e.target.value)} placeholder="comma-separated, blank = all types"
          className="h-9 rounded-md bg-surface px-3 text-[0.8125rem] text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
      </div>
      <label className="flex items-start gap-2 text-on-surface-var text-[0.8125rem]">
        <input type="checkbox" className="mt-0.5" checked={proposeSkill} onChange={(e) => setProposeSkill(e.target.checked)} />
        <span>Offer to build a skill from this intent — adds a “Generate skill” action that distills what it has gathered into a reusable skill.</span>
      </label>
      {err && <p className="text-danger text-[0.8125rem]">{err}</p>}
      <div className="flex justify-end gap-s"><Button size="sm" variant="ghost" onClick={onClose}>Cancel</Button><Button size="sm" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save intent'}</Button></div>
    </div>
  )
}

function FilterChip({ active, onClick, tone, children }: { active: boolean; onClick: () => void; tone?: string; children: React.ReactNode }) {
  return (
    <button type="button" onClick={onClick}
      className="inline-flex items-center gap-1 rounded-pill px-m h-8 text-[0.8125rem] transition-colors"
      style={active ? { background: `color-mix(in srgb, ${tone ?? 'var(--color-primary)'} 20%, transparent)`, color: tone ?? 'var(--color-primary)' } : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-var)' }}>
      {children}
    </button>
  )
}
