import { useEffect, useState } from 'react'
import { Pencil, Trash2, Check, X, ExternalLink, Sparkles, Layers, Loader2, Pin, Archive, Download, Target, Maximize2, Wand2, ChevronDown, WifiOff, RefreshCw } from 'lucide-react'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { Markdown } from '../../ui/Markdown'
import { ChipInput } from '../tasks/formControls'
import type { KnowledgeItem, IntentOutcome, IntentOutcomeField } from '../../lib/api'
import { resolveType, insightRows, fmtBytes, relTime, GIST_LANGUAGES } from './knowledgeMeta'
import { getKnowledge, updateKnowledge, deleteKnowledge } from './knowledgeStore'
import { GistEditor } from './GistEditor'
import { confirm } from '../../ui/dialog'
import { api } from '../../lib/api'

/** Wrap gist code in a fenced markdown block so Markdown's CodeBlock highlights it.
 *  The fence is longer than any backtick run in the body (defensive against code
 *  that itself contains triple-backticks). */
function gistFence(code: string, lang?: string): string {
  const longest = Math.max(0, ...(code.match(/`+/g) || []).map((r) => r.length))
  const fence = '`'.repeat(Math.max(3, longest + 1))
  return `${fence}${(lang || '').trim()}\n${code}\n${fence}`
}

/** Knowledge item inspector: type-aware preview (markdown / link card / inline
 *  image·audio·video / code gist / doc), extracted content, AI insights,
 *  entities/relations/related, and per-type edit + delete. Works against both
 *  backend items and the local stub (knowledgeStore merges them). */
export function KnowledgeDetail({ item, onChanged, onDeleted, onTagClick, onShowDetails, detailsOpen, detailsCount, onHeader }: { item: KnowledgeItem; onChanged: () => void; onDeleted: () => void; onTagClick?: (tag: string) => void; onShowDetails?: () => void; detailsOpen?: boolean; detailsCount?: number; onHeader?: (parts: { wand: React.ReactNode; actions: React.ReactNode; editing: boolean } | null) => void }) {
  const [full, setFull] = useState<KnowledgeItem>(item)
  const [editing, setEditing] = useState(false)
  // Tag autocomplete, fetched lazily when the user first enters edit mode.
  const [knownTags, setKnownTags] = useState<string[]>([])
  // Reseed the draft from the freshest FULL item before editing — the list seeds a
  // truncated content preview (content_truncated), so editing must never start from
  // (and save) a clipped body. Fetch full content first, then open the editor.
  const startEdit = async () => {
    if (!knownTags.length) api.knowledgeTags().then(setKnownTags).catch(() => {})
    let src = full
    if (src.content_truncated || item.content_truncated) {
      const fresh = await getKnowledge(item.id)
      if (fresh) { src = fresh; setFull(fresh) }
    }
    setDraft({ title: src.title ?? '', content: src.content ?? '', summary: src.summary ?? '', tags: src.tags ?? [], item_type: src.item_type ?? src.type ?? 'note', gist_language: src.gist_language ?? '', url: src.url ?? '' })
    setEditing(true)
  }
  const [draft, setDraft] = useState({ title: item.title ?? '', content: item.content ?? '', summary: item.summary ?? '', tags: item.tags ?? [], item_type: item.item_type ?? item.type ?? 'note', gist_language: item.gist_language ?? '', url: item.url ?? '' })
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  const tm = resolveType(full)

  // Intents this item contributed to (bidirectional link, item side) — feeds the
  // Insights dock. The extracted-content pool / entities / relations / related items
  // live in the page's "More details" side panel (KnowledgeExtras), which fetches its
  // own copies, so this component no longer holds them.
  const [itemIntents, setItemIntents] = useState<IntentOutcome[]>([])
  // Live node-graph ingestion phase (#30): node_type → phase (running/done/skipped/failed).
  const [nodePhases, setNodePhases] = useState<Record<string, string>>({})
  const [procStatus, setProcStatus] = useState<string>(item.processing_status ?? '')
  // The ingestion node-graph SHAPE for this item's type → rendered as a mini-DAG.
  const [ingestGraph, setIngestGraph] = useState<import('../../lib/api').KnowledgeIngestGraph | null>(null)
  // Full-screen overlay: {title, node} of the preview/content being inspected large.
  const [fullscreen, setFullscreen] = useState<{ title: string; node: React.ReactNode } | null>(null)
  // Page mode: the bottom insights panel — collapsed shows only the summary; expand
  // for the key points / topics / action items / contributed-intents.
  const [insightsOpen, setInsightsOpen] = useState(false)
  // Edit mode: re-ingest the content on save (re-run the enrichment node-graph). On by
  // default — a content edit should normally refresh insights/entities/embedding.
  const [reingest, setReingest] = useState(true)

  useEffect(() => {
    let alive = true
    setEditing(false); setItemIntents([]); setNodePhases({}); setIngestGraph(null)
    getKnowledge(item.id).then((d) => { if (alive && d) { setFull(d); setProcStatus(d.processing_status ?? ''); setDraft({ title: d.title ?? '', content: d.content ?? '', summary: d.summary ?? '', tags: d.tags ?? [], item_type: d.item_type ?? d.type ?? 'note', gist_language: d.gist_language ?? '', url: d.url ?? '' }) } }).catch(() => setFull(item))
    api.knowledgeItemIntents(item.id).then((r) => { if (alive) setItemIntents(r.outcomes || []) }).catch(() => {})
    api.knowledgeItemGraph(item.id).then((g) => { if (alive) setIngestGraph(g) }).catch(() => {})
    return () => { alive = false }
  }, [item.id])

  // Subscribe to per-item ingestion progress SSE while the item is still processing,
  // so the transparency strip animates live and the pool refreshes on completion.
  useEffect(() => {
    if (procStatus !== 'queued' && procStatus !== 'processing') return
    const es = new EventSource(api.knowledgeIngestStreamUrl(item.id))
    const onNode = (e: MessageEvent) => {
      try { const d = JSON.parse(e.data); if (d.node) setNodePhases((p) => ({ ...p, [d.node]: d.phase })) } catch { /* ignore */ }
    }
    const onComplete = (e: MessageEvent) => {
      try { const d = JSON.parse(e.data); setProcStatus(d.status || 'done') } catch { setProcStatus('done') }
      // Refresh what this component shows (the item itself + its contributed-intents),
      // computed DURING enrichment, so a panel opened mid-processing fills in on finish.
      getKnowledge(item.id).then((d) => d && setFull(d)).catch(() => {})
      api.knowledgeItemIntents(item.id).then((r) => setItemIntents(r.outcomes || [])).catch(() => {})
      // Tell the parent (the dedicated page) enrichment finished so its own copies —
      // the list, and the page's "More details" pool/entities/relations/related —
      // refresh too; otherwise that side panel stays empty after a mid-processing open.
      onChanged()
      es.close()
    }
    es.addEventListener('node', onNode)
    es.addEventListener('ingest_complete', onComplete)
    es.addEventListener('ingest_failed', onComplete)
    es.onerror = () => es.close()
    return () => es.close()
  }, [item.id, procStatus])

  const insights = insightRows(full.insights)
  const [genning, setGenning] = useState(false)
  // Generated insights only make sense for items carrying authored/extracted text.
  const canGenerate = !!(full.content || '').trim()

  async function generateInsights() {
    setGenning(true); setErr('')
    try {
      const updated = await api.generateKnowledgeIntelligence(item.id)
      setFull(updated)
      // Regenerate now re-enqueues the FULL pipeline (queued) — drive the live
      // progress strip + poll by reflecting the new processing status.
      setProcStatus(updated.processing_status ?? 'queued')
      onChanged()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Insight generation failed') } finally { setGenning(false) }
  }

  async function save() {
    setSaving(true); setErr('')
    try {
      // Send only the fields the user actually changed. Sending everything would
      // needlessly re-enrich on an unchanged body, and would trip journal immutability
      // (the backend rejects a title/content key on a past-day journal even if its
      // value is identical) when the user only tweaked tags.
      const fields: Record<string, unknown> = {}
      if (draft.title !== (full.title ?? '')) fields.title = draft.title
      if (draft.content !== (full.content ?? '')) fields.content = draft.content
      if (draft.url !== (full.url ?? '')) fields.url = draft.url
      if (draft.summary !== (full.summary ?? '')) fields.summary = draft.summary
      if (draft.item_type !== (full.item_type ?? full.type)) { fields.type = draft.item_type; fields.item_type = draft.item_type }
      if ((draft.item_type === 'gist') && draft.gist_language !== (full.gist_language ?? '')) fields.gist_language = draft.gist_language
      // Tags: compare as ordered lists.
      if (JSON.stringify(draft.tags) !== JSON.stringify(full.tags ?? [])) fields.tags = draft.tags
      if (Object.keys(fields).length === 0) { setEditing(false); return }
      // Re-ingest control: when off, tell the backend NOT to re-run enrichment even
      // though content changed (a quick fix that shouldn't burn a model pass).
      const changedBody = 'content' in fields || 'url' in fields
      if (changedBody && !reingest) fields.reingest = false
      await updateKnowledge(item.id, fields)
      // Optimistic local merge must not include the transient reingest flag.
      const { reingest: _r, ...applied } = fields
      setFull((f) => ({ ...f, ...applied }))
      if (changedBody && reingest) setProcStatus('queued')  // reflect the re-enqueue
      onChanged(); setEditing(false)
    } catch (e) { setErr(e instanceof Error ? e.message : 'Save failed') } finally { setSaving(false) }
  }
  async function del() {
    if (!(await confirm({ title: `Delete "${full.title || 'this item'}"?`, body: 'This removes it from the knowledge base.', danger: true, confirmLabel: 'Delete' }))) return
    try { await deleteKnowledge(item.id); onDeleted() } catch { setErr('Delete failed') }
  }
  async function toggleFlag(flag: 'is_pinned' | 'is_archived') {
    const next = !full[flag]
    setFull((f) => ({ ...f, [flag]: next }))  // optimistic
    try { await updateKnowledge(item.id, { [flag]: next }); onChanged() }
    catch { setFull((f) => ({ ...f, [flag]: !next })); setErr('Update failed') }
  }
  // The user kept their own title, so the AI title was only stored as a backup. This
  // promotes it to the displayed title on demand (the magic-wand affordance).
  async function applyAiTitle() {
    const ai = (full.ai_title || '').trim()
    if (!ai) return
    const prev = full.title
    setFull((f) => ({ ...f, title: ai }))  // optimistic
    try { await updateKnowledge(item.id, { title: ai }); onChanged() }
    catch { setFull((f) => ({ ...f, title: prev })); setErr('Update failed') }
  }
  // The displayed title differs from the AI suggestion → offer the one-tap swap.
  const aiTitleAvailable = !!(full.ai_title && full.ai_title.trim() && full.ai_title.trim() !== (full.title || '').trim())

  // The action cluster — the ONE responsive header cluster (4-tier FULL→TEXT→ICON→
  // OVERFLOW). Edit mode swaps Edit for Cancel/Save; the curation controls (pin/
  // archive/delete/more) stay available throughout. Priorities drive the overflow
  // Header tenet: the side-panel opener (More details) is the RIGHTMOST control;
  // Delete sits LEFTMOST of the right-edge group; the rest in between. Priority
  // (not order) drives overflow: the primary action (Save when editing, else Edit)
  // is kept longest; the low ones (Delete, More details) fall into the auto-`…`
  // menu first as the header tightens.
  const actionCluster = (
    <HeaderActions>
      <HeaderControl icon={Trash2} label="Delete" priority="low" danger onClick={del} />
      {editing ? (
        <>
          <HeaderControl icon={X} label="Cancel" onClick={() => { setEditing(false); setErr('') }} />
          <HeaderControl icon={Check} label={saving ? 'Saving…' : 'Save'} variant="primary" priority="primary" onClick={save} disabled={saving} />
        </>
      ) : (
        <HeaderControl icon={Pencil} label="Edit" variant="primary" priority="primary" onClick={startEdit} />
      )}
      <HeaderControl icon={Pin} label={full.is_pinned ? 'Pinned' : 'Pin'} active={full.is_pinned} onClick={() => toggleFlag('is_pinned')} />
      <HeaderControl icon={Archive} label={full.is_archived ? 'Archived' : 'Archive'} active={full.is_archived} onClick={() => toggleFlag('is_archived')} />
      {onShowDetails && (
        <HeaderControl icon={Layers} label={`More details${detailsCount ? ` · ${detailsCount}` : ''}`} active={detailsOpen} priority="low" onClick={onShowDetails} />
      )}
    </HeaderActions>
  )
  const wandBtn = aiTitleAvailable ? (
    <button type="button" onClick={applyAiTitle} aria-label="Use the AI-suggested title"
      title={`Use AI title: "${full.ai_title}"`}
      className="grid size-6 shrink-0 place-items-center rounded text-primary/70 hover:bg-surface-high hover:text-primary transition-colors">
      <Wand2 size={14} />
    </button>
  ) : null

  // The dedicated page's header bar is the single home for the title + actions —
  // publish them up so there aren't two stacked headers; render nothing inline here.
  // The wand goes NEXT TO THE TITLE (left, via onHeader.wand); the action cluster right.
  useEffect(() => {
    if (!onHeader) return
    onHeader({ wand: wandBtn, actions: actionCluster, editing })
    return () => onHeader(null)
    // `draft`, `full` and `reingest` MUST be deps: the published Save button closes over
    // save(), which reads all three. Without them the header keeps a closure from
    // edit-open time where draft===full → save() sees zero changed fields and silently
    // discards the edit. (Deps are still hand-picked — actionCluster/wandBtn are fresh
    // objects every render; listing them would republish unconditionally and loop.)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [full, draft, reingest, aiTitleAvailable, detailsCount, detailsOpen, editing, saving])

  // Fleeting notes are content-only (auto-titled) and journals are date-driven — neither
  // exposes an editable title (matches the create flow + journal immutability).
  const titleEditable = draft.item_type !== 'fleeting' && draft.item_type !== 'journal'
  // A journal is immutable once its creation day has passed (the backend 403s a
  // content/title edit). Detect it so the editor signals this upfront — read-only body,
  // no failed-save surprise. Curation (tags/pin/archive) stays editable.
  // Compare in LOCAL time: created_at is stored local (datetime.now()) and the backend
  // checks against local today, so we must too — using toISOString() (UTC) here would
  // mis-lock a same-day journal in the evening of a behind-UTC timezone (UTC already
  // rolled to tomorrow) while the backend still accepts the edit.
  const localToday = (() => {
    const d = new Date()
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  })()
  const journalLocked = (full.item_type ?? full.type) === 'journal'
    && (full.created_at || '').slice(0, 10) !== localToday
  // Bottom edit bar: ONLY the re-ingest checkbox (Cancel/Save live in the header — the
  // single home for the item's actions; there's no second copy here). Re-ingest is
  // meaningful only when there's a re-processable body: text content or a bookmark URL
  // (re-scraped). Media/document bodies aren't user-edited here — then the bar is empty
  // and the shell omits it entirely (no empty strip).
  const reingestApplies = tm.group === 'text' || tm.key === 'bookmark'
  const editBar = reingestApplies ? (
    <label className="flex items-center gap-1.5 text-on-surface-var text-[0.8125rem] cursor-pointer select-none">
      <input type="checkbox" checked={reingest} onChange={(e) => setReingest(e.target.checked)} className="size-4 accent-[var(--color-primary)]" />
      Re-process on save
    </label>
  ) : null

  if (editing) {
    // Edit = the same full-size shell as preview: tags inline at top, a full-height
    // editable content panel in the middle, and the edit bar stuck at the bottom. The
    // title's edited via the header (Cancel/Save live there too). Summary is omitted —
    // it's AI-generated, not user-authored.
    return (
      <>
      <div className="flex h-full min-h-0 flex-col gap-l">
        {err && <p className="shrink-0 text-danger text-[0.8125rem]">{err}</p>}
        {titleEditable && (
          <input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} autoFocus placeholder="Title"
            className="shrink-0 w-full bg-transparent text-on-surface outline-none border-b border-outline-variant/40 pb-1.5 text-[1.0625rem] focus:border-primary" data-type="title-l" />
        )}
        {draft.item_type === 'gist' && (
          <div className="shrink-0 flex items-center gap-2">
            <span className="text-on-surface-low text-[0.7rem] uppercase tracking-wide">Language</span>
            <select value={draft.gist_language || ''} onChange={(e) => setDraft({ ...draft, gist_language: e.target.value })}
              className="h-8 appearance-none rounded-md bg-surface-container px-m text-on-surface text-[0.8125rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 [color-scheme:dark]">
              <option value="">(none)</option>
              {GIST_LANGUAGES.map((l) => <option key={l} value={l}>{l}</option>)}
            </select>
          </div>
        )}
        {/* Bookmark → its URL is the editable field (the page is re-scraped on save). */}
        {tm.key === 'bookmark' && (
          <div className="shrink-0 flex items-center gap-s rounded-md bg-surface-container px-m h-10">
            <ExternalLink size={15} className="shrink-0 text-on-surface-low" />
            <input value={draft.url} onChange={(e) => setDraft({ ...draft, url: e.target.value })} placeholder="https://…"
              className="flex-1 bg-transparent text-on-surface text-[0.9375rem] outline-none placeholder:text-on-surface-low" />
          </div>
        )}
        {/* Tags edited inline, in the same place the preview shows them. */}
        <div className="shrink-0"><ChipInput values={draft.tags} onChange={(v) => setDraft({ ...draft, tags: v })} placeholder="Add a tag, Enter" suggestions={knownTags} /></div>
        {/* The editable body — only TEXT types have a user-authored body. Bookmark's
            body is scraped (URL edited above); media/document content is an extracted
            artifact of the file (not editable) → show the read-only preview so the user
            still sees what they're titling/tagging. */}
        {journalLocked && <p className="shrink-0 text-on-surface-low text-[0.75rem]">This journal entry is immutable — its day has passed. You can still curate tags, pin, and archive.</p>}
        {draft.item_type === 'gist' ? (
          // Gists get the full Monaco code editor (syntax highlighting by language),
          // not a raw textarea — same editor surface as the Files page.
          <div className="min-h-0 flex-1 overflow-hidden rounded-lg border border-outline-variant/40 bg-surface-container">
            <GistEditor value={draft.content} onChange={(v) => setDraft({ ...draft, content: v })} language={draft.gist_language} />
          </div>
        ) : tm.group === 'text' ? (
          <div className="min-h-0 flex-1 overflow-hidden rounded-lg border border-outline-variant/40 bg-surface-container">
            <textarea value={draft.content} onChange={(e) => setDraft({ ...draft, content: e.target.value })} readOnly={journalLocked}
              placeholder="Markdown supported…"
              className={`h-full w-full resize-none bg-transparent px-m py-2 text-on-surface text-[0.875rem] leading-relaxed outline-none ${journalLocked ? 'opacity-60 cursor-not-allowed' : ''}`} />
          </div>
        ) : (
          <div className="relative flex min-h-0 flex-1 overflow-hidden">
            <Preview item={full} tm={tm} prominent />
          </div>
        )}
      </div>
      {editBar && <div className="-mx-l shrink-0 border-t border-outline-variant/40 bg-surface/95 px-l py-3">{editBar}</div>}
      </>
    )
  }

  return (
    <>
    {fullscreen && <FullscreenModal title={fullscreen.title} onClose={() => setFullscreen(null)}>{fullscreen.node}</FullscreenModal>}
    <div className="flex h-full min-h-0 flex-col gap-l">
      {/* The title + wand + action cluster live in the dedicated page's header bar
          (published via onHeader) — not inline here, so there's a single header. */}
      {err && <p className="text-danger text-[0.8125rem]">{err}</p>}

      {/* Metadata row: provider/size/shape/words/age on the left, the live ingestion
          status DAG floated to the right of the same row. */}
      <div className="flex shrink-0 items-start gap-x-m gap-y-1">
        <div className="flex flex-wrap items-center gap-x-m gap-y-1 text-on-surface-low text-[0.8125rem] min-w-0">
          {full.provider && full.provider !== 'native' && <span className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var text-[0.7rem]">{full.provider}</span>}
          {full.mime_type && <span className="font-mono text-[0.7rem]">{full.mime_type}</span>}
          {full.file_size != null && <span>{fmtBytes(full.file_size)}</span>}
          {full.file_metadata?.width && full.file_metadata?.height && <span>{full.file_metadata.width}×{full.file_metadata.height}</span>}
          {typeof full.file_metadata?.page_count === 'number' && <span>{full.file_metadata.page_count} pages</span>}
          {typeof full.file_metadata?.sheet_count === 'number' && <span>{full.file_metadata.sheet_count} sheet{full.file_metadata.sheet_count === 1 ? '' : 's'}</span>}
          {typeof full.file_metadata?.slide_count === 'number' && <span>{full.file_metadata.slide_count} slide{full.file_metadata.slide_count === 1 ? '' : 's'}</span>}
          {typeof full.file_metadata?.row_count === 'number' && full.file_metadata.row_count > 0 && <span>{full.file_metadata.row_count} row{full.file_metadata.row_count === 1 ? '' : 's'}</span>}
          {full.word_count != null && full.word_count > 0 && <span>{full.word_count} words</span>}
          {full.updated_at && <span title="Last updated">{relTime(full.updated_at)}</span>}
        </div>
        <div className="ml-auto shrink-0">
          <ProcessingStrip status={procStatus} nodePhases={nodePhases} error={full.processing_error} graph={ingestGraph} onRetry={generateInsights} retrying={genning} />
        </div>
      </div>

      {/* Tags row, with the Expand-to-fullscreen control floated to its right edge.
          Always rendered so Expand has a home even for a tag-less item. */}
      <div className="flex shrink-0 items-start gap-s">
        <div className="flex flex-1 flex-wrap gap-1.5">{(full.tags ?? []).map((t) => onTagClick
          ? <button key={t} type="button" onClick={() => onTagClick(t)} title={`Find items tagged "${t}"`} className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var text-[0.75rem] transition-colors hover:bg-surface-container hover:text-primary">{t}</button>
          : <span key={t} className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var text-[0.75rem]">{t}</span>)}</div>
        <ExpandButton onClick={() => setFullscreen({ title: full.title || full.url_title || 'Preview', node: <Preview item={full} tm={tm} fullscreen /> })} />
      </div>

      {/* The preview (with its own per-type frame) fills the space between the tags and
          the bottom Insights dock and scrolls inside. Expand lives in the metadata row
          above (a clear toolbar slot) — not floating over the preview, where it would
          collide with the content's own controls (e.g. a gist's copy button). The
          per-node pool / entities / relations / related live in the page's "More
          details" side panel (KnowledgeExtras), not inline. */}
      <Preview item={full} tm={tm} prominent />
      <InsightsDock
        open={insightsOpen} onToggle={() => setInsightsOpen((v) => !v)}
        summary={full.summary} insights={insights} intents={itemIntents}
        canGenerate={canGenerate} genning={genning} onGenerate={generateInsights}
        processing={procStatus === 'queued' || procStatus === 'processing'}
      />
    </div>
    </>
  )
}

/** Type-aware preview, in ONE uniform framed container across all 12 types: bookmark
 *  → link card; image/audio/video → inline player; file types → a download row; text
 *  /gist → a rendered body preview. Keeps the sidebar's at-a-glance view consistent. */
function Preview({ item, tm, fullscreen, prominent }: { item: KnowledgeItem; tm: ReturnType<typeof resolveType>; fullscreen?: boolean; prominent?: boolean }) {
  const baseFrame = 'rounded-lg border border-outline-variant/40 bg-surface-container overflow-hidden'
  // `prominent` (the dedicated page) lets a preview fill the full available column
  // height — but ONLY for types with growable content (image/video, long text/gist,
  // or a doc with extracted text). Compact cards (bookmark, audio player, a bare file
  // row) keep their natural height so they don't become a giant empty box. `fillFrame`
  // is the growing variant; `baseFrame` stays compact.
  const fillFrame = baseFrame + (prominent ? ' flex w-full min-h-0 flex-1 flex-col' : '')
  const hasFile = !!item.file_path
  const mediaMax = fullscreen ? 'max-h-[80vh]' : prominent ? 'min-h-0 flex-1' : 'max-h-80'
  const textMax = fullscreen ? '' : prominent ? 'min-h-0 flex-1 overflow-y-auto' : 'max-h-80 overflow-y-auto'

  // Bookmark / any item with a URL → compact link card (never fills).
  if (tm.key === 'bookmark' || item.url) {
    return (
      <div className={baseFrame + (prominent ? ' w-full self-start' : '')}>
        <a href={item.url} target="_blank" rel="noreferrer" className="flex items-center gap-s px-m py-3 hover:bg-surface-high transition-colors">
          <tm.icon size={18} className="shrink-0" style={{ color: tm.tone }} />
          <div className="flex-1 min-w-0">
            <div className="truncate text-on-surface text-[0.875rem]">{item.url_title || item.title || item.url}</div>
            {item.url && <div className="truncate text-on-surface-low text-[0.75rem]">{item.url}</div>}
          </div>
          <ExternalLink size={14} className="shrink-0 text-on-surface-low" />
        </a>
      </div>
    )
  }
  // Media with bytes → inline player/image, with a download row beneath. Image/video
  // fill the height; audio is a compact player + row (no tall empty box).
  if (hasFile && (tm.key === 'image' || tm.key === 'audio' || tm.key === 'video')) {
    const fills = tm.key === 'image' || tm.key === 'video'
    return (
      <div className={fills ? fillFrame : baseFrame + (prominent ? ' w-full self-start' : '')}>
        {tm.key === 'image' && <img src={api.knowledgeItemFileUrl(item.id)} alt={item.title} className={`${mediaMax} w-full object-contain bg-surface`} />}
        {tm.key === 'audio' && <div className="p-m"><audio src={api.knowledgeItemFileUrl(item.id)} controls className="w-full" /></div>}
        {tm.key === 'video' && <video src={api.knowledgeItemFileUrl(item.id)} controls className={`${mediaMax} w-full bg-black`} />}
        <FileRow item={item} tm={tm} />
      </div>
    )
  }
  // File types without an inline renderer (pdf/doc/sheet/slides) → a download row,
  // plus a preview of the extracted text when present (so the Preview tab is useful
  // without switching to Extracted just to glimpse the content). Fill only when there's
  // extracted text to grow into; otherwise the bare row stays compact.
  if (hasFile) {
    const hasText = !!(item.content || '').trim()
    return (
      <div className={hasText ? fillFrame : baseFrame + (prominent ? ' w-full self-start' : '')}>
        <FileRow item={item} tm={tm} />
        {hasText && (
          <div className={`border-t border-outline-variant/40 px-m py-2 text-on-surface-var text-[0.8125rem] leading-relaxed ${textMax}`}>
            <Markdown>{item.content!}</Markdown>
          </div>
        )}
      </div>
    )
  }
  // Gist → render through Markdown as a fenced code block: syntax highlighting,
  // copy button, and a language label, all reusing the chat's CodeBlock.
  if (item.content && tm.key === 'gist') {
    return (
      <div className={`${prominent ? fillFrame : baseFrame} px-m ${textMax}`}>
        <Markdown>{gistFence(item.content, item.gist_language)}</Markdown>
      </div>
    )
  }
  // Other text → rendered markdown body.
  if (item.content) {
    return (
      <div className={`${prominent ? fillFrame : baseFrame} px-m py-2 ${textMax}`}>
        <div className="text-on-surface-var text-[0.875rem] leading-relaxed"><Markdown>{item.content}</Markdown></div>
      </div>
    )
  }
  return null
}

/** A uniform file row: type icon + filename + size + download link. */
function FileRow({ item, tm }: { item: KnowledgeItem; tm: ReturnType<typeof resolveType> }) {
  return (
    <a href={api.knowledgeItemFileUrl(item.id)} target="_blank" rel="noreferrer" download={item.title}
      className="flex items-center gap-s px-m py-2.5 hover:bg-surface-high transition-colors text-on-surface-var text-[0.8125rem] border-t border-outline-variant/30 first:border-t-0">
      <tm.icon size={16} style={{ color: tm.tone }} className="shrink-0" />
      <span className="flex-1 truncate">{item.title}</span>
      {item.file_size != null && <span className="shrink-0 text-on-surface-low text-[0.7rem]">{fmtBytes(item.file_size)}</span>}
      <Download size={14} className="shrink-0 text-on-surface-low" />
    </a>
  )
}

type NodePhase = 'pending' | 'running' | 'done' | 'skipped' | 'failed'

/** Resolve each pipeline node's phase: live SSE events win; otherwise infer from the
 *  item's final status + the persisted skip/fail reason (so a reloaded item still
 *  shows an accurate per-node picture, not blanks). */
function resolveNodePhases(
  graph: import('../../lib/api').KnowledgeIngestGraph,
  live: Record<string, string>,
  status: string,
  error?: string,
): Record<string, NodePhase> {
  // Ground truth (when present): the per-node phase map persisted at ingest end.
  // A live SSE phase (this render is mid-processing) always wins over it; then the
  // persisted map; then — only for older items with no persisted map — a lossy
  // reconstruction from processing_error.
  const persisted = graph.node_phases || {}
  // processing_error carries EITHER a benign "Skipped (optional steps unavailable):
  // a, b" list OR a real node-failure list "node: reason; node2: reason" (backend
  // runner.py). Parse both (for 'partial' too) as the legacy fallback.
  const skipOnly = (error || '').startsWith('Skipped (optional steps unavailable):')
  const skipped = new Set(skipOnly ? error!.split(':').slice(1).join(':').split(',').map((s) => s.trim()) : [])
  const hasRealFailure = !skipOnly && !!error && (status === 'failed' || status === 'unreachable' || status === 'partial')
  const failed = new Set(hasRealFailure ? error!.split(';').map((s) => s.split(':')[0].trim()).filter(Boolean) : [])
  const out: Record<string, NodePhase> = {}
  for (const n of graph.nodes) {
    const nt = n.node_type
    const lv = live[nt]
    if (lv === 'running' || lv === 'done' || lv === 'skipped' || lv === 'failed') { out[nt] = lv as NodePhase; continue }
    const p = persisted[nt]
    if (p === 'done' || p === 'skipped' || p === 'failed') { out[nt] = p as NodePhase; continue }
    // Legacy fallback (no persisted phase for this node): reconstruct from error.
    if (failed.has(nt)) { out[nt] = 'failed'; continue }
    if (skipped.has(nt)) { out[nt] = 'skipped'; continue }
    if (Object.keys(persisted).length) out[nt] = 'pending'  // persisted map exists but omits this node → not reached
    else if (status === 'done' || status === 'partial') out[nt] = 'done'
    else out[nt] = 'pending'
  }
  return out
}

/** Assign each node a topological level (longest path from a root) for column layout.
 *  Loop back-edges are EXCLUDED — they'd make a loop target look like it depends on a
 *  later node and corrupt the levels. */
function dagLevels(graph: import('../../lib/api').KnowledgeIngestGraph): Map<string, number> {
  const preds = new Map<string, string[]>()
  for (const n of graph.nodes) preds.set(n.node_type, [])
  for (const e of graph.edges) { if (!e.loop) preds.get(e.to)?.push(e.from) }
  const level = new Map<string, number>()
  const visit = (nt: string, seen: Set<string>): number => {
    if (level.has(nt)) return level.get(nt)!
    if (seen.has(nt)) return 0
    seen.add(nt)
    const ps = preds.get(nt) || []
    const lv = ps.length ? Math.max(...ps.map((p) => visit(p, seen))) + 1 : 0
    level.set(nt, lv)
    return lv
  }
  for (const n of graph.nodes) visit(n.node_type, new Set())
  return level
}

/** Node-graph ingestion transparency: a status line + a mini-DAG of the pipeline,
 *  each node showing its step status (pending = outline, running = spinner, done =
 *  green check, skipped = dash, failed = ✕). Hidden when nothing notable to report. */
function ProcessingStrip({ status, nodePhases, error, graph, onRetry, retrying }: { status: string; nodePhases: Record<string, string>; error?: string; graph?: import('../../lib/api').KnowledgeIngestGraph | null; onRetry?: () => void; retrying?: boolean }) {
  const active = status === 'queued' || status === 'processing'
  // unreachable = the URL couldn't be fetched (retryable) — distinct from a hard failure.
  const unreachable = status === 'unreachable'
  // Done + clean + no graph → nothing to show (don't clutter a finished item).
  if (!active && status !== 'partial' && status !== 'failed' && !unreachable && !graph) return null
  const hasDag = !!graph && graph.nodes.length > 0
  // A Retry re-runs the whole pipeline — offered for a fetch failure (unreachable) or a
  // hard failure, both of which a later attempt may clear.
  const retryBtn = onRetry && (unreachable || status === 'failed') ? (
    <button type="button" onClick={onRetry} disabled={retrying}
      className="inline-flex items-center gap-1 rounded-pill bg-surface-high px-2 py-0.5 text-[0.7rem] text-on-surface-var transition-colors hover:text-on-surface disabled:opacity-60">
      <RefreshCw size={11} className={retrying ? 'animate-spin' : ''} /> {retrying ? 'Retrying…' : 'Retry'}
    </button>
  ) : null

  // Before the graph shape has loaded, fall back to a bare status pill.
  if (!hasDag) {
    if (unreachable) {
      return (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 py-1 text-[0.8125rem]">
          <span className="inline-flex items-center gap-1.5" style={{ color: 'var(--color-warning)' }}><WifiOff size={13} /> Unreachable</span>
          {retryBtn}
          {error && <span className="basis-full text-[0.75rem] text-on-surface-low">{error}</span>}
        </div>
      )
    }
    if (active) {
      return (
        <div className="flex items-center gap-1.5 py-1 text-[0.8125rem]" style={{ color: 'var(--color-primary)' }}>
          <Loader2 size={13} className="animate-spin" />
          <span>{status === 'queued' ? 'Queued…' : 'Processing…'}</span>
        </div>
      )
    }
    // failed/partial with no graph yet → reason + retry.
    return (
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 py-1 text-[0.8125rem]">
        {error && <span className="text-[0.75rem]" style={{ color: status === 'failed' ? 'var(--color-danger)' : 'var(--color-on-surface-low)' }}>{error}</span>}
        {retryBtn}
      </div>
    )
  }

  // The DAG itself is the indicator: the running step expands inline with a spinner +
  // its name; when finished, the last node stays expanded with a check + "Processed".
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 py-1 text-[0.8125rem]">
      <MiniDag graph={graph!} phases={resolveNodePhases(graph!, nodePhases, status, error)} status={status} />
      {retryBtn}
      {error && <span className="basis-full text-[0.75rem]" style={{ color: status === 'failed' ? 'var(--color-danger)' : 'var(--color-on-surface-low)' }}>{error}</span>}
    </div>
  )
}

/** Humanize a node_type for the expanded step label (e.g. document_read → "Document read"). */
function nodeLabel(nodeType: string): string {
  const s = nodeType.replace(/_/g, ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

const _dagDotColor = (p: NodePhase) => p === 'done' ? 'var(--color-success)'
  : p === 'failed' ? 'var(--color-danger)'
  : p === 'running' ? 'var(--color-primary)'
  : p === 'skipped' ? 'var(--color-on-surface-low)'
  : 'var(--color-outline-variant)'

/** A structural mini-DAG of the ingestion pipeline: nodes laid out in COLUMNS by
 *  topological level, parallel siblings STACKED vertically within a column (so the
 *  video graph's audio ∥ video branches read as parallel), columns joined by
 *  connectors, and any bounded loop back-edge (video_classify ⟲ frame_extract) shown
 *  as a ⟲ badge on the loop target with its max-iteration cap. Each node shows its
 *  phase (pending outline / running spinner / done check / skipped dash / failed ✕). */
function MiniDag({ graph, phases }: { graph: import('../../lib/api').KnowledgeIngestGraph; phases: Record<string, NodePhase>; status: string }) {
  const level = dagLevels(graph)
  const maxLevel = Math.max(0, ...[...level.values()])
  // Group node_types by level → columns; preserve input order within a column.
  const columns: { node_type: string; backend?: string; model_backed?: boolean }[][] = Array.from({ length: maxLevel + 1 }, () => [])
  for (const n of graph.nodes) columns[level.get(n.node_type) ?? 0].push(n)

  // Loop back-edges → a map of loop-TARGET node_type → {from, max_iters} for the ⟲ badge.
  const loopByTarget = new Map<string, { from: string; max: number }>()
  for (const e of graph.edges) if (e.loop) loopByTarget.set(e.to, { from: e.from, max: e.max_iters ?? 1 })

  const Dot = ({ nt }: { nt: string }) => {
    const ph = phases[nt] ?? 'pending'
    const c = _dagDotColor(ph)
    const loop = loopByTarget.get(nt)
    const stateWord = ph === 'running' ? 'processing' : ph
    return (
      <span className="inline-flex items-center gap-1" title={`${nt.replace(/_/g, ' ')}: ${ph}${loop ? ` (⟲ resamples up to ${loop.max}×)` : ''}`}>
        <span className="grid size-3.5 shrink-0 place-items-center rounded-full"
          style={{ border: `1.5px ${ph === 'pending' ? 'dashed' : 'solid'} ${c}`,
                   background: ph === 'done' ? 'color-mix(in srgb, var(--color-success) 22%, transparent)' : 'transparent' }}>
          {ph === 'running' && <Loader2 size={8} className="animate-spin" style={{ color: c }} />}
          {ph === 'done' && <Check size={8} strokeWidth={3} style={{ color: c }} />}
          {ph === 'failed' && <X size={8} style={{ color: c }} />}
        </span>
        <span className="text-[0.7rem] whitespace-nowrap" style={{ color: ph === 'pending' ? 'var(--color-on-surface-low)' : c }}>
          {nodeLabel(nt)}{loop && <RefreshCw size={9} className="ml-0.5 inline-block align-[-1px]" style={{ color: 'var(--color-primary)' }} />}
        </span>
        <span className="sr-only">{stateWord}</span>
      </span>
    )
  }

  return (
    <span className="inline-flex flex-wrap items-stretch gap-x-1 gap-y-1 align-middle">
      {columns.map((col, ci) => (
        <span key={ci} className="inline-flex items-center gap-1">
          {ci > 0 && <span className="h-px w-2.5 shrink-0 self-center" style={{ background: 'var(--color-outline-variant)' }} />}
          {/* a column: parallel siblings stacked vertically */}
          <span className="inline-flex flex-col justify-center gap-0.5">
            {col.map((n) => <Dot key={n.node_type} nt={n.node_type} />)}
          </span>
        </span>
      ))}
    </span>
  )
}

function Section({ label, icon: Icon, action, children }: { label: string; icon?: typeof Sparkles; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1.5 flex items-center gap-1.5 text-on-surface-low text-[0.7rem] uppercase tracking-wide">{Icon && <Icon size={12} />}{label}{action && <span className="ml-auto normal-case tracking-normal">{action}</span>}</div>
      {children}
    </div>
  )
}

/** Bottom-stuck, expandable Insights panel for the dedicated detail page. Collapsed it
 *  shows only the summary (one line) + a chevron; expanded it reveals the insight rows
 *  (key points / topics / action items) and the intents this item contributed to. */
function InsightsDock({ open, onToggle, summary, insights, intents, canGenerate, genning, onGenerate, processing }: {
  open: boolean; onToggle: () => void; summary?: string
  insights: Array<{ label: string; value: string }>
  intents: IntentOutcome[]
  canGenerate: boolean; genning: boolean; onGenerate: () => void; processing?: boolean
}) {
  const hasMore = insights.length > 0 || intents.length > 0
  // Nothing to dock and nothing to generate → render nothing.
  if (!summary && !hasMore && !canGenerate) return null
  // While the item is enriching, the live ProcessingStrip already shows progress —
  // don't offer a (redundant) Generate button; the insights will arrive on their own.
  const showGenerate = canGenerate && !processing
  return (
    // A panel rounded on the TOP edge only — it docks flush to the bottom of the column
    // (a drawer that rises from the bottom), so the lower corners stay square while the
    // top corners curve. rounded-t-lg uses the --radius-lg token (× the global
    // --radius-scale), matching the preview frame above + following the corner-roundness
    // config. In-flow (not sticky) so it RESERVES space; mt-auto pins it down.
    <div className="mt-auto shrink-0 rounded-t-lg border border-b-0 border-outline-variant/40 bg-surface-container overflow-hidden">
      <div className="px-m">
        {/* Collapsed header: a refined bar — a primary-tinted sparkles tile, a readable
            "Insights" title with a count pill, a muted one-line summary preview, then the
            Regenerate action + a chevron chip. More breathing room than a bare eyebrow so
            it reads as a real section header, not a thin strip. */}
        <button type="button" onClick={onToggle} disabled={!hasMore}
          className="group/dock flex w-full items-center gap-2.5 py-3 text-left disabled:cursor-default">
          <span className="grid size-7 shrink-0 place-items-center rounded-lg bg-primary/10">
            <Sparkles size={14} className={`text-primary ${genning || processing ? 'animate-pulse' : ''}`} />
          </span>
          <span className="shrink-0 text-on-surface text-[0.875rem]" style={{ fontVariationSettings: '"wght" 500' }}>Insights</span>
          {hasMore && (
            <span className="shrink-0 rounded-pill bg-surface-high px-1.5 text-on-surface-low text-[0.7rem] tabular-nums">{insights.length + intents.length}</span>
          )}
          {/* Persistent flex-1 slot: a one-line summary preview when collapsed (so the bar
              is informative at a glance), empty when open (the body shows the full text). */}
          <span className="min-w-0 flex-1 truncate text-[0.8125rem] text-on-surface-low">
            {open ? '' : (summary || (processing ? 'Enriching…' : hasMore ? 'Key points, topics & more' : 'No insights yet'))}
          </span>
          {showGenerate && (
            <span role="button" tabIndex={0} onClick={(e) => { e.stopPropagation(); onGenerate() }}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); onGenerate() } }}
              title="Reprocess this item — refreshes insights, entities, tags, and the embedding"
              className="shrink-0 inline-flex items-center gap-1 rounded-pill px-2.5 h-7 text-[0.75rem] text-primary hover:bg-primary/10 disabled:opacity-50 transition-colors">
              <Sparkles size={12} className={genning ? 'animate-pulse' : ''} /> {genning ? 'Reprocessing…' : insights.length > 0 || summary ? 'Regenerate' : 'Generate'}
            </span>
          )}
          {hasMore && (
            <span className="grid size-7 shrink-0 place-items-center rounded-lg text-on-surface-low transition-colors group-hover/dock:bg-surface-high group-hover/dock:text-on-surface">
              <ChevronDown size={16} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
            </span>
          )}
        </button>

        {open && (
          <div className="flex max-h-[45vh] flex-col gap-l overflow-y-auto border-t border-outline-variant/40 pb-m pt-m">
            {summary && <p className="text-on-surface-var text-[0.875rem] leading-relaxed">{summary}</p>}
            {insights.length > 0 && (
              <Section label="Highlights" icon={Sparkles}>
                <div className="flex flex-col gap-1.5">
                  {insights.map((r) => (
                    <div key={r.label} className="rounded-md bg-surface-high px-m py-1.5">
                      <div className="text-on-surface-low text-[0.65rem] uppercase tracking-wide">{r.label}</div>
                      <div className="text-on-surface-var text-[0.8125rem] mt-0.5">{r.value}</div>
                    </div>
                  ))}
                </div>
              </Section>
            )}
            {intents.length > 0 && (
              <Section label={`Contributed to · ${intents.length}`} icon={Target}>
                <div className="flex flex-col gap-1.5">
                  {intents.map((o) => (
                    <div key={o.id} className="rounded-md bg-surface-high px-m py-1.5">
                      <div className="flex items-center gap-1.5"><Target size={12} className="shrink-0 text-primary/80" /><span className="truncate text-on-surface text-[0.8125rem]">{o.intent_name || o.intent_id}</span></div>
                      {o.takeaway && <div className="mt-0.5 text-on-surface-var text-[0.8125rem]">{o.takeaway}</div>}
                      {/* The structured fields this item contributed (the point of a Tier-3
                          intent) — shown from the item's own page, mirroring the intents tab. */}
                      {(o.fields?.length ?? 0) > 0 && (
                        <div className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-m gap-y-0.5 text-[0.75rem]">
                          {o.fields!.map((f, i) => (
                            <div key={i} className="contents">
                              <span className="text-on-surface-low">{f.name}</span>
                              <OutcomeFieldValue field={f} />
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </Section>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

/** Render one intent-outcome field's value by its declared type (mirrors the intents-tab
 *  OutcomeCard): booleans as Yes/No, numbers right-aligned, urls as links, tags as pills. */
function OutcomeFieldValue({ field }: { field: IntentOutcomeField }) {
  const { type, value } = field
  if (value === null || value === undefined || value === '') return <span className="text-on-surface-low">—</span>
  if (type === 'boolean') return <span className="text-on-surface">{value ? 'Yes' : 'No'}</span>
  if (type === 'number') return <span className="text-on-surface tabular-nums">{String(value)}</span>
  if (type === 'url') return <a href={String(value)} target="_blank" rel="noreferrer" className="text-primary underline decoration-primary/40 break-all">{String(value)}</a>
  if (type === 'tags' && Array.isArray(value)) return <span className="flex flex-wrap gap-1">{value.map((t, i) => <span key={i} className="rounded-pill bg-surface-high px-2 h-5 inline-flex items-center text-on-surface-var text-[0.7rem]">{String(t)}</span>)}</span>
  return <span className="text-on-surface break-words">{String(value)}</span>
}

/** A full-screen overlay for inspecting a preview/extracted content at full size.
 *  Closes on Escape or backdrop/✕ click; the body scrolls. (Its content isn't
 *  URL-serializable, so unlike the More-details panel it isn't a history step — the three
 *  explicit dismiss affordances cover it; a popstate trap fought the hash router.) */
function FullscreenModal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-surface/95 backdrop-blur-sm" onClick={onClose}>
      <div className="flex items-center gap-s border-b border-outline-variant/40 px-l py-3">
        <span className="flex-1 truncate text-on-surface text-[0.9375rem]" style={{ fontVariationSettings: '"wght" 500' }}>{title}</span>
        <button type="button" onClick={onClose} aria-label="Close fullscreen"
          className="grid size-8 place-items-center rounded-pill text-on-surface-low hover:bg-surface-high hover:text-on-surface"><X size={18} /></button>
      </div>
      <div className="flex-1 overflow-auto p-l" onClick={(e) => e.stopPropagation()}>
        <div className="mx-auto" style={{ maxWidth: 'var(--content-width, 56rem)' }}>{children}</div>
      </div>
    </div>
  )
}

/** Small "expand to full screen" icon button. */
function ExpandButton({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} aria-label="Expand to full screen" title="Expand to full screen"
      className="grid size-6 place-items-center rounded text-on-surface-low hover:bg-surface-high hover:text-on-surface transition-colors">
      <Maximize2 size={13} />
    </button>
  )
}
