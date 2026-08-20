import { useCallback, useEffect, useState } from 'react'
import { MoreRow } from '../../ui/MoreRow'
import { ArrowLeft, Network, Layers, Highlighter, Copy } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { PageTitle } from '../../ui/PageTitle'
import { WorkbenchLayout } from '../../ui/WorkbenchLayout'
import { LoadError } from '../../ui/ListScaffold'
import { SidePanel } from '../../ui/SidePanel'
import { IconButton } from '../../ui/IconButton'
import { Markdown } from '../../ui/Markdown'
import { KnowledgeDetail } from './KnowledgeDetail'
import { AnnotationList } from './ReadingView'
import { DuplicateList } from './DuplicateList'
import { KnowledgeEgoGraph, type KnowledgeGraphPayload } from './KnowledgeEgoGraph'
import { Button } from '../../ui/Button'
import { resolveType, typeLabel } from './knowledgeMeta'
import { api, type KnowledgeAnnotation, type KnowledgeDuplicate, type KnowledgeItem, type ExtractedContent, ApiError } from '../../lib/api'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'

/** Accent ink for a `knowledgeMeta` tone painted as TEXT on the **canvas** — the ground
 *  cycle 147 named. `--color-primary` measures **4.37:1** on `--color-canvas` in light
 *  against a 4.5 floor, so the three primary-toned kinds (Note / Fleeting note / Journal)
 *  failed AA in the breadcrumb trail below; every other tone on that ground measures
 *  5.71-5.83 and passes, so only this one is remapped. `primary-emphasis` is the mode-aware
 *  legible sibling — further from the ground in BOTH modes (light `#c8452e`→`#a33922` = 6.0:1,
 *  dark `#ff6b5b`→`#ff9a86` = 9.33:1) — and is the token cycles 147/155/158 already settled
 *  for this same failure on the canvas, `surface-high` and `surface-low`.
 *
 *  The REGISTRY is deliberately left alone: `knowledgeMeta`'s tone also inks icons in
 *  `ArtifactCard`, `ArtifactViewer` and `KnowledgeDetail`, which carry a 3:1 non-text floor
 *  and already pass — cycle 155 checked that icon and left it for exactly this reason. */
const canvasInk = (tone: string) => (tone === 'var(--color-primary)' ? 'var(--color-primary-emphasis)' : tone)

/** The dedicated, full-screen Knowledge item page (`#/knowledge/item/<id>`).
 *  Mirrors the app's header-bar philosophy: a back button + "Knowledge" breadcrumb
 *  in the TopBar, the item detail as the centered body, and the per-node extracted
 *  content / entities / relations / related items behind a "More details" side panel
 *  (what used to be the panel's "Extracted" tab). */
export function KnowledgeDetailPage({ id, onBack, onOpenItem, query, setQuery }: {
  id: string
  onBack: () => void
  onOpenItem: (id: string) => void
  query: RouteProps['query']
  setQuery: RouteProps['setQuery']
}) {
  const [item, setItem] = useState<KnowledgeItem | null>(null)
  const [missing, setMissing] = useState(false)
  // A failure that is NOT a 404 — the item may well exist; the server just could not say.
  const [loadErr, setLoadErr] = useState<unknown>(null)
  // The "More details" panel open-state lives in the URL (?details=1) so it's a navigable
  // history step — the browser Back button closes the panel rather than leaving the page,
  // matching the app-wide "every open/close panel is a navigable link" guidance.
  const [detailsParam, setDetailsParam] = useQueryParam(query, setQuery, 'details', '')
  const showDetails = detailsParam === '1'
  const setShowDetails = (v: boolean) => setDetailsParam(v ? '1' : '')
  // Reading mode lives in the URL for the same reason the panel does: it's a navigable
  // step, so Back leaves the reader rather than the item, and a reading link is sharable.
  const [readParam, setReadParam] = useQueryParam(query, setQuery, 'read', '')
  const reading = readParam === '1'
  const toggleReading = useCallback(() => setReadParam(reading ? '' : '1'), [reading, setReadParam])
  // The "more details" payload (counts drive the toggle badge).
  const [pool, setPool] = useState<ExtractedContent[]>([])
  const [related, setRelated] = useState<KnowledgeItem[]>([])
  // The item's reading highlights are owned HERE, not inside the reader: the More-details
  // panel lists the same rows, so one fetch feeds both and a delete in the panel re-paints
  // the prose. Two independent copies would let the two surfaces disagree.
  const [annotations, setAnnotations] = useState<KnowledgeAnnotation[]>([])
  // Near-duplicate candidates (T3.2). The ERROR is kept beside the list on purpose: for almost
  // every item the honest answer is an empty list, so a failed lookup that fell back to `[]`
  // would render as "this item is clean" and there would be no way to tell the two apart. The
  // panel therefore mounts the section when there are candidates OR the lookup failed.
  const [duplicates, setDuplicates] = useState<KnowledgeDuplicate[]>([])
  const [duplicatesErr, setDuplicatesErr] = useState<unknown>(null)
  const [reloadKey, setReloadKey] = useState(0)
  // The detail's title-wand + action cluster, lifted into THIS page's header bar so
  // there's a single header (no stacked page-header + in-body title row). The wand sits
  // next to the title (left); the action cluster on the right.
  const [header, setHeader] = useState<{ wand: React.ReactNode; actions: React.ReactNode; editing: boolean } | null>(null)

  useEffect(() => {
    let alive = true
    setItem(null); setMissing(false); setLoadErr(null)
    // 🪤 `getKnowledge` collapses EVERY failure into `null` (`catch { return null }`), so a 500 and a
    // real 404 were indistinguishable here — and this page then told the user their item "no longer
    // exists". Call the API directly so `ApiError.status` survives: only a 404 is a deletion; anything
    // else is a load failure the user can retry. The repo already draws this line in
    // `ProjectsSection`'s `dirErrorMessage` (404 → gone, else → couldn't read).
    api.knowledgeItem(id)
      .then((d) => { if (!alive) return; if (d) setItem(d); else setMissing(true) })
      .catch((e) => {
        if (!alive) return
        if (e instanceof ApiError && e.status === 404) setMissing(true)
        else setLoadErr(e)
      })
    api.knowledgeExtracted(id).then((d) => { if (alive) setPool(d.contents || []) }).catch(() => {})
    api.knowledgeItemRelated(id).then((r) => { if (alive) setRelated(r) }).catch(() => {})
    return () => { alive = false }
  }, [id, reloadKey])

  // Highlights reload on their own key so keeping one doesn't re-fetch the whole item
  // (and re-mount the article the reader is scrolled into).
  const [annotationKey, setAnnotationKey] = useState(0)
  const reloadAnnotations = useCallback(() => setAnnotationKey((k) => k + 1), [])
  useEffect(() => {
    let alive = true
    api.knowledgeAnnotations(id).then((a) => { if (alive) setAnnotations(a) }).catch(() => {})
    return () => { alive = false }
  }, [id, annotationKey])

  const removeAnnotation = useCallback(async (annotationId: string) => {
    await api.deleteKnowledgeAnnotation(annotationId).catch(() => {})
    reloadAnnotations()
  }, [reloadAnnotations])

  // Duplicates get their own key so "Try again" after a failed lookup re-runs ONE request
  // rather than re-reading the whole item and remounting the body.
  const [duplicateKey, setDuplicateKey] = useState(0)
  const reloadDuplicates = useCallback(() => setDuplicateKey((k) => k + 1), [])
  useEffect(() => {
    let alive = true
    // 🔴 NO `.catch(() => [])`. The rejection is STORED, not substituted: see `duplicatesErr`.
    api.knowledgeDuplicates(id)
      .then((d) => { if (alive) { setDuplicates(d); setDuplicatesErr(null) } })
      .catch((e) => { if (alive) { setDuplicates([]); setDuplicatesErr(e) } })
    return () => { alive = false }
  }, [id, duplicateKey])

  // A merge rewrote BOTH sides: the survivor inherited the loser's collections/tags/mentions/
  // highlights (so the item, its related list and its highlights are stale) and the loser no
  // longer exists (so the candidate list is stale). Both keys move, or the panel keeps offering
  // a merge into an item that is already gone.
  const afterMerge = useCallback(() => {
    setReloadKey((k) => k + 1)
    setAnnotationKey((k) => k + 1)
    reloadDuplicates()
  }, [reloadDuplicates])

  const detailsCount =
    pool.length + (item?.entities?.length ?? 0) + (item?.relations?.length ?? 0) + related.length
    + annotations.length + duplicates.length
  const tm = item ? resolveType(item) : null

  return (
    <WorkbenchLayout
      scroll={false}
      topBar={
        <TopBar
          keepCornerPadding
          contentAligned
          left={
            <div className="flex items-center gap-s min-w-0">
              <IconButton icon={ArrowLeft} label="Back to Knowledge" size={40} onClick={onBack} />
              {/* While EDITING, the wide action cluster (Cancel/Save/Pin/…) leaves no room
                  for the breadcrumb, and the title is edited inline in the body anyway —
                  so collapse the trail to just the back arrow. In view mode show the full
                  "Knowledge / <Type> <Title>" breadcrumb; the title truncates if long. */}
              {!header?.editing && (
                // One clipping group holds the whole "Knowledge / <type> <title>" trail.
                // The TITLE truncates first under width pressure (it's the min-w-0 flex
                // child, and it's also shown in full on the body card below); the breadcrumb
                // chrome stays fixed. Because the GROUP is overflow-hidden, if the slot ever
                // gets narrower than even the chrome, the chrome clips at the group's edge
                // instead of painting OVER the action cluster (the overlap bug). This is
                // container-relative — it tracks the header's flex width, not the viewport.
                <div className="flex items-center gap-s min-w-0 overflow-hidden">
                  <button type="button" onClick={onBack} className="text-on-surface-low hover:text-on-surface text-[0.9375rem] transition-colors whitespace-nowrap shrink-0">Knowledge</button>
                  <span className="text-on-surface-low shrink-0">/</span>
                  {/* `canvasInk`, not `tm.tone`: this segment is 13px accent text on the CANVAS.
                      The icon travels with the label so the segment stays ONE ink — it passes at
                      either shade (3:1 non-text floor), and splitting them would read as two
                      colours for one breadcrumb crumb. */}
                  {tm && item && <span className="shrink-0 inline-flex items-center gap-1.5 text-[0.8125rem] whitespace-nowrap" style={{ color: canvasInk(tm.tone) }}><tm.icon size={16} /> {typeLabel(item)}</span>}
                  {/* `PageTitle`, not the bare span it replaced: this route's PATH identifies the
                      entity (`#/knowledge/item/<id>`), so by the rule cycle 162 settled — a
                      destination is named by its identity, not by its category — the item's name is
                      this page's `h1`. Measured before: ZERO headings of any level on the whole
                      surface, so a screen-reader user skipping by heading landed on nothing and had
                      to read the DOM in order to orient. `PageTitle` is this span with the tag it
                      should have had (same `data-type="title-l"`), so nothing moves. */}
                  <PageTitle className="truncate min-w-0">{item?.title || item?.url_title || (missing ? 'Not found' : loadErr ? "Couldn't load" : 'Loading…')}</PageTitle>
                </div>
              )}
              {/* Magic-wand sits NEXT TO the title (not floating to the right edge). */}
              {header?.wand}
            </div>
          }
          right={header?.actions}
        />
      }
      panel={
        showDetails && item ? (
          <SidePanel fillHeight storeKey="knowledge-extras-w" icon={<Layers size={18} className="text-primary" />} title="More details" onClose={() => setShowDetails(false)}>
            <KnowledgeExtras item={item} pool={pool} related={related} onOpenItem={onOpenItem}
              annotations={annotations} onRemoveAnnotation={removeAnnotation}
              duplicates={duplicates} duplicatesError={duplicatesErr}
              onRetryDuplicates={reloadDuplicates} onMerged={afterMerge} />
          </SidePanel>
        ) : undefined
      }
    >
      {/* w-full is load-bearing: the WorkbenchLayout body is a flex COLUMN (scroll=false),
          where `mx-auto` (margin-inline:auto) cancels the default align-self:stretch, so
          without w-full the column collapses to its content's intrinsic width — looking
          cramped even at the 'full' (100%) width preset. w-full makes it fill up to
          max-width (the toggle), then mx-auto centers any remainder. */}
      <div className="mx-auto flex h-full min-h-0 w-full flex-col px-l pt-l" style={{ maxWidth: 'var(--content-width)' }}>
        {loadErr ? (
          // Not "no longer exists": the load failed, so offer the retry rather than assert a deletion.
          <LoadError what="knowledge item" error={loadErr} onRetry={() => setReloadKey((k) => k + 1)} />
        ) : missing ? (
          <div className="grid h-full place-items-center text-on-surface-low text-[0.8125rem]">This knowledge item no longer exists.</div>
        ) : item ? (
          <KnowledgeDetail
            item={item}
            detailsCount={detailsCount}
            detailsOpen={showDetails}
            onShowDetails={() => setShowDetails(!showDetails)}
            onHeader={setHeader}
            onChanged={() => setReloadKey((k) => k + 1)}
            onDeleted={onBack}
            onTagClick={() => onBack()}
            reading={reading}
            onToggleReading={toggleReading}
            annotations={annotations}
            onAnnotationsChanged={reloadAnnotations}
            // KL-16: the reader keeps the dock's attention sections instead of replacing
            // them. Passed as a NODE rather than imported by `ReadingView`, because this
            // module already imports `KnowledgeDetail` → `ReadingView`, and an import back
            // the other way would close that into a cycle. It also keeps the rail's data
            // (related items, annotation removal, item routing) owned here, where it is
            // fetched, rather than re-fetched a second time inside the reader.
            insightRail={hasReaderInsights(item, related, annotations) ? (
              <ReaderInsights item={item} related={related} annotations={annotations}
                onRemoveAnnotation={removeAnnotation} onOpenItem={onOpenItem} />
            ) : undefined}
          />
        ) : (
          <div className="grid h-40 place-items-center text-on-surface-low text-[0.8125rem]">Loading…</div>
        )}
      </div>
    </WorkbenchLayout>
  )
}

/** The per-item "more details" content: full content, the extracted-content pool,
 *  entities, relations, and related items — the dedicated page's side-panel body. */
function KnowledgeExtras({ item, pool, related, onOpenItem, annotations, onRemoveAnnotation,
  duplicates, duplicatesError, onRetryDuplicates, onMerged }: {
  item: KnowledgeItem
  pool: ExtractedContent[]
  related: KnowledgeItem[]
  onOpenItem: (id: string) => void
  annotations: KnowledgeAnnotation[]
  onRemoveAnnotation: (id: string) => void
  duplicates: KnowledgeDuplicate[]
  duplicatesError: unknown
  onRetryDuplicates: () => void
  onMerged: () => void
}) {
  const entities = item.entities ?? []
  const relations = item.relations ?? []
  // A FAILED duplicates lookup is content: it has to keep the panel out of its "nothing here
  // yet" state, or the one surface that knows the check broke is the one that renders instead.
  const showDuplicates = duplicates.length > 0 || !!duplicatesError
  if (pool.length === 0 && entities.length === 0 && relations.length === 0 && related.length === 0
    && annotations.length === 0 && !showDuplicates && !item.content) {
    return <p className="text-on-surface-low text-[0.8125rem]">No extracted content, entities, or related items yet.</p>
  }
  return (
    <div className="flex flex-col gap-l">
      {/* Highlights lead: they are the only thing here the USER wrote, and they belong on
          the item whether or not the reader happens to be open. */}
      <HighlightsSection annotations={annotations} onRemove={onRemoveAnnotation} />
      {/* Duplicates sit SECOND — above the read-only sections — because this is the only
          section here that asks the user to DO something, and library hygiene decays the longer
          two copies of a document coexist. The count is omitted when the lookup failed: "· 0"
          beside an error would assert exactly the thing the error says is unknown. */}
      {showDuplicates && (
        <Section label={`Possible duplicates${duplicates.length ? ` · ${duplicates.length}` : ''}`} icon={Copy}>
          <DuplicateList item={item} duplicates={duplicates} error={duplicatesError}
            onRetry={onRetryDuplicates} onOpenItem={onOpenItem} onMerged={onMerged} />
        </Section>
      )}
      {pool.length > 0 && (
        <Section label={`Extracted content · ${pool.length}`} icon={Layers}>
          <div className="flex flex-col gap-1.5">
            {pool.map((ec) => (
              <details key={ec.id} className="rounded-md bg-surface-container px-m py-1.5">
                <summary className="flex items-center gap-2 cursor-pointer text-[0.8125rem] text-on-surface-var">
                  <span className="font-mono text-[0.75rem] text-on-surface-low">{ec.node_type}</span>
                  {ec.backend && <span className="text-on-surface-low text-[0.75rem]">· {ec.backend}</span>}
                  <span className="ml-auto text-on-surface-low text-[0.75rem]">{(ec.text || '').length} chars</span>
                </summary>
                {ec.text && <div className="mt-1.5 max-h-72 overflow-y-auto text-on-surface-var text-[0.8125rem] leading-relaxed"><Markdown>{ec.text}</Markdown></div>}
              </details>
            ))}
          </div>
        </Section>
      )}
      <EntitiesSection entities={entities} />
      {relations.length > 0 && (
        <Section label={`Relations · ${relations.length}`}>
          <div className="flex flex-col gap-1">
            {relations.slice(0, 30).map((r) => (
              <div key={r.id} className="text-on-surface-var text-[0.8125rem]"><span className="text-on-surface">{r.source_name}</span> <span className="text-on-surface-low">{r.relation_type}</span> <span className="text-on-surface">{r.target_name}</span></div>
            ))}
            <MoreRow total={relations.length} shown={30} />
          </div>
        </Section>
      )}
      <RelatedSection related={related} onOpenItem={onOpenItem} />
    </div>
  )
}

// ── The three dock sections the reader's insight rail also carries ───────────────────
//
// KL-16's clause: reading mode "no longer REPLACES the insights dock — related items,
// entities and highlights ride a rail". Those three sections therefore have TWO homes: the
// "More details" side panel (KnowledgeExtras, above) and the reader's rail (ReaderInsights,
// below). They are extracted as components rather than duplicated into a second reader-only
// component, so a change to how a related item or an entity chip reads lands on both
// surfaces at once.
//
// 🔑 Extracted as THREE components rather than one block, because the dock's ordering is
// deliberate and interleaved (highlights, then duplicates, then extracted content, then
// entities, then relations, then related). One combined component could not be dropped into
// that sequence without reordering it.

/** The item's highlights — the only thing on the item the USER wrote. */
export function HighlightsSection({ annotations, onRemove }: {
  annotations: KnowledgeAnnotation[]
  onRemove: (id: string) => void
}) {
  if (annotations.length === 0) return null
  return (
    <Section label={`Highlights · ${annotations.length}`} icon={Highlighter}>
      <AnnotationList annotations={annotations} onDelete={onRemove} />
    </Section>
  )
}

/** The entities extracted from the item, as chips. */
export function EntitiesSection({ entities }: { entities: NonNullable<KnowledgeItem['entities']> }) {
  if (entities.length === 0) return null
  return (
    <Section label={`Entities · ${entities.length}`} icon={Network}>
      <div className="flex flex-wrap gap-1.5">
        {entities.slice(0, 60).map((e) => (
          <span key={e.id} className="inline-flex items-center gap-1 rounded-pill bg-surface-container px-2 h-6 text-on-surface-var text-[0.75rem]" title={e.entity_type}>{e.name}{e.entity_type && <span className="text-on-surface-low">· {e.entity_type}</span>}</span>
        ))}
        <MoreRow total={entities.length} shown={60} className="px-1" />
      </div>
    </Section>
  )
}

/** Items this one shares entities with, each a route to that item. */
export function RelatedSection({ related, onOpenItem }: {
  related: KnowledgeItem[]
  onOpenItem: (id: string) => void
}) {
  if (related.length === 0) return null
  return (
    <Section label={`Related · ${related.length}`} icon={Network}>
      <div className="flex flex-col gap-1">
        {related.slice(0, 15).map((r) => (
          <button key={r.id} type="button" onClick={() => onOpenItem(r.id)}
            className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-surface-high">
            <span className="truncate text-on-surface text-[0.8125rem]">{r.title || '(untitled)'}</span>
            {/* The badge names whatever CHOSE this ordering. KL-13 replaced an unthresholded
                shared-entity count with a cosine similarity edge above a real floor, so a
                "3 shared" chip would no longer explain why this row sits where it does. The
                tooltip carries the rest — which passage matched, and the entity overlap the
                score no longer ranks by — so the number stays accountable. Falls back to the
                old chip when a response predates the edge table. */}
            {typeof r.score === 'number' ? (
              <span className="ml-auto shrink-0 text-on-surface-low text-[0.75rem]"
                title={[
                  `${Math.round(r.score * 100)}% similar`,
                  typeof r.chunk_index === 'number' && typeof r.neighbour_chunk_index === 'number'
                    ? `matched section ${r.chunk_index + 1} of this item against section ${r.neighbour_chunk_index + 1} of the other`
                    : '',
                  typeof r.shared_entities === 'number' ? `${r.shared_entities} shared entities` : '',
                ].filter(Boolean).join(' · ')}>
                {Math.round(r.score * 100)}%
              </span>
            ) : typeof r.shared_entities === 'number' ? (
              <span className="ml-auto shrink-0 text-on-surface-low text-[0.75rem]">{r.shared_entities} shared</span>
            ) : null}
          </button>
        ))}
        <MoreRow total={related.length} shown={15} />
      </div>
    </Section>
  )
}

/** True when the reader's insight rail would have something to say.
 *
 *  Exported and asked BEFORE the rail is built, because in a wide reader pane the rail is
 *  always on screen — an empty column beside the article is a worse outcome than no column,
 *  and `ReadingView` cannot inspect a React node to find out. So the decision is made here,
 *  where the data is, and travels as the presence or absence of the node itself. */
export function hasReaderInsights(item: KnowledgeItem, related: KnowledgeItem[], annotations: KnowledgeAnnotation[]): boolean {
  return annotations.length > 0 || (item.entities ?? []).length > 0 || related.length > 0
}

/** The reader's insight rail body: the same highlights / entities / related sections the
 *  More-details dock shows, so opening the reader no longer costs the reader access to them. */
export function ReaderInsights({ item, related, annotations, onRemoveAnnotation, onOpenItem }: {
  item: KnowledgeItem
  related: KnowledgeItem[]
  annotations: KnowledgeAnnotation[]
  onRemoveAnnotation: (id: string) => void
  onOpenItem: (id: string) => void
}) {
  return (
    <>
      <HighlightsSection annotations={annotations} onRemove={onRemoveAnnotation} />
      <EntitiesSection entities={item.entities ?? []} />
      <RelatedSection related={related} onOpenItem={onOpenItem} />
      <EgoGraphSection item={item} onOpenItem={onOpenItem} />
    </>
  )
}

/** KL-17's ego view, in the reading rail. Centred on the item's best-connected entity, because
 *  the graph's nodes ARE entities — an item is not a node in it, so "this document's
 *  neighbourhood" is honestly "the neighbourhood of what this document is about".
 *
 *  🔴 Loads the graph ONLY when opened. The payload is the whole positioned graph (every node,
 *  edges thinned) and the projection behind it is a whole-library computation; paying for that on
 *  every reader open would tax the common case to serve the rare one. A collapsed section that
 *  has never been expanded issues no request at all. */
function EgoGraphSection({ item, onOpenItem }: {
  item: KnowledgeItem
  onOpenItem: (id: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<KnowledgeGraphPayload | null>(null)
  const [err, setErr] = useState<unknown>(null)

  // The focus: the item's entity with the most connections in the graph once it arrives, and
  // before that its first entity. Falls back to '' so the view renders its own not-in-the-graph
  // state rather than this section inventing an empty one.
  const entities = item.entities ?? []
  const focusId = (() => {
    if (!entities.length) return ''
    if (!data) return String(entities[0].id ?? '')
    const degree = new Map<string, number>()
    for (const e of data.edges) {
      degree.set(e.source, (degree.get(e.source) ?? 0) + 1)
      degree.set(e.target, (degree.get(e.target) ?? 0) + 1)
    }
    const ids = entities.map((e) => String(e.id ?? '')).filter(Boolean)
    return ids.slice().sort((a, b) => (degree.get(b) ?? 0) - (degree.get(a) ?? 0) || a.localeCompare(b))[0] ?? ''
  })()

  useEffect(() => {
    if (!open || data || err) return
    let alive = true
    api.knowledgeGraph()
      .then((d) => { if (alive) setData(d as KnowledgeGraphPayload) })
      .catch((e) => { if (alive) setErr(e) })
    return () => { alive = false }
  }, [open, data, err])

  if (!entities.length) return null
  return (
    <Section label="Neighbourhood" icon={Network}>
      <Button variant="ghost" size="sm" ariaExpanded={open} onClick={() => setOpen((v) => !v)}>
        {open ? 'Hide the graph' : 'Show this in the graph'}
      </Button>
      {open && (
        err
          ? <LoadError what="knowledge graph" error={err} onRetry={() => setErr(null)} />
          : data
          ? <div className="mt-s"><KnowledgeEgoGraph data={data} focusId={focusId} onSelect={onOpenItem} /></div>
          : <div data-type="body-s" className="px-s py-m text-on-surface-low">Loading the graph…</div>
      )}
    </Section>
  )
}

function Section({ label, icon: Icon, children }: { label: string; icon?: typeof Network; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1.5 flex items-center gap-1.5 text-on-surface-low text-[0.75rem] uppercase tracking-wide">{Icon && <Icon size={12} />}{label}</div>
      {children}
    </div>
  )
}
