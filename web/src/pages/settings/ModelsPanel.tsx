import { useMemo, useState } from 'react'
import {
  ChevronRight, Check, MessageSquare, Boxes, Mic, Volume2, Eye, ImagePlus,
  Ear, Music, ScanEye, Clapperboard, Search, X, Users, Download, type LucideIcon,
} from 'lucide-react'
import { api, type AvailableModel } from '../../lib/api'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { confirm } from '../../ui/dialog'
import { PanelHeader, Section } from './settingsUI'
import { ListSkeleton } from '../../ui/ListScaffold'

// Canonical use-cases (matches the backend's USE_CASES + MULTI_ACTIVE set).
// `multi`: several models can be active (routing pool); else single-select.
// `fallback` names the use-case this one INHERITS its binding from when no model
// is pinned here (mirrors backend parent_capability). Its presence changes the
// empty-picker state from a misleading "add a backend first" to an accurate
// "already uses your <fallback> model; pin one here only to override".
const USE_CASE_META: Record<string, { label: string; group?: string; description: string; multi: boolean; icon: LucideIcon; fallback?: string }> = {
  chat: { label: 'Chat', description: 'Conversational models for chat and agent interactions.', multi: true, icon: MessageSquare },
  embedding: { label: 'Embedding', description: 'Vector embedding models for knowledge and memory.', multi: false, icon: Boxes },
  stt: { label: 'Speech-to-text', description: 'Voice transcription models.', multi: false, icon: Mic },
  tts: { label: 'Text-to-speech', description: 'Voice synthesis models.', multi: false, icon: Volume2 },
  diarization: { label: 'Speaker diarization', description: 'Labels "who spoke when" in audio/video (speaker turns). Served by diarization providers (ONNX, pyannote).', multi: false, icon: Users },
  image_modality: { label: 'Image · Modality', group: 'Image', description: 'Models that understand images as input (vision / VLM).', multi: true, icon: Eye },
  image_gen: { label: 'Image · Generation', group: 'Image', description: 'Models that generate images from a prompt.', multi: false, icon: ImagePlus },
  audio_modality: { label: 'Audio · Modality', group: 'Audio', description: 'Models that understand audio as input.', multi: false, icon: Ear },
  audio_gen: { label: 'Audio · Generation', group: 'Audio', description: 'Models that generate audio, music, or sound effects.', multi: false, icon: Music },
  video_modality: { label: 'Video · Modality', group: 'Video', description: 'Models that understand video as input.', multi: false, icon: ScanEye },
  video_gen: { label: 'Video · Generation', group: 'Video', description: 'Models that generate video from a prompt.', multi: false, icon: Clapperboard },
  // NOTE: knowledge-ingestion (OCR/vision/classify/consolidation) has NO dedicated
  // use-case rows — each ingestion node resolves directly to the relevant default
  // binding (Image·Modality / Chat / Speech-to-text). There is no per-role override.
}
const USE_CASE_ORDER = ['chat', 'embedding', 'stt', 'tts', 'diarization', 'image_modality', 'image_gen', 'audio_modality', 'audio_gen', 'video_modality', 'video_gen']

/** The models a use-case can pick from: every catalog model declaring the capability
 *  (deduped by `provider:id`), PLUS a synthetic "unavailable" row for any ACTIVE binding
 *  whose model is absent from the catalog (e.g. an ollama model deleted or never pulled).
 *  Without the synthetic row the use-case reads "N active" but the bound model is invisible
 *  AND unremovable in the picker — a phantom binding the user can't clear. Synthetic rows
 *  carry `downloaded:false` so the not-downloaded chip renders; toggling one off unbinds it.
 *  Pure + exported for unit testing. */
export function capableModels(useCase: string, allModels: AvailableModel[], activeModels: string[]): AvailableModel[] {
  const seen = new Set<string>()
  const out: AvailableModel[] = []
  for (const m of allModels) {
    if (!m.capabilities.includes(useCase)) continue
    const ref = `${m.provider}:${m.id}`
    if (seen.has(ref)) continue
    seen.add(ref)
    out.push(m)
  }
  for (const ref of activeModels) {
    if (seen.has(ref)) continue
    seen.add(ref)
    const sep = ref.indexOf(':')
    const provider = sep >= 0 ? ref.slice(0, sep) : ''
    const id = sep >= 0 ? ref.slice(sep + 1) : ref
    out.push({ id, name: id, provider, capabilities: [useCase], downloaded: false } as AvailableModel)
  }
  return out
}

/** Models → assign discovered models to use-cases. Reads /api/models/available
 *  (all backends' models) + /api/models/active (current bindings); writes via
 *  PUT /api/models/active/{use_case}. Chat + Image·Modality are multi-select;
 *  the rest take one model. */
export function ModelsPanel() {
  // Stale-while-revalidate + sessionStorage persistence: the discovered-models
  // catalog and use-case bindings barely change, so on revisit (and after a full
  // reload) the page paints instantly from cache and revalidates in the background
  // — no "Loading…" flash. Both fetches batch into one cache key.
  const { data, refresh } = useCachedData('settings:models', async () => {
    const [rows, active] = await Promise.all([
      api.modelsAvailable().catch(() => [] as { name: string; models?: AvailableModel[] }[]),
      api.modelsActive().catch(() => ({} as Record<string, string[]>)),
    ])
    return { allModels: rows.flatMap((r) => r.models ?? []), active }
  }, { persist: true })
  const allModels = data?.allModels
  const active = data?.active ?? {}

  // A binding mutation invalidates the cached catalog so the next read revalidates
  // against the changed state instead of a stale snapshot.
  const reloadActive = () => { invalidateCache('settings:models'); refresh() }

  if (!allModels) return <ListSkeleton rows={6} />

  return (
    <div>
      <PanelHeader title="Models" hint="Assign discovered models to each use case. Chat and Image·Modality allow several active models; the rest take one. Modality means understanding that media as input; Generation means producing it." />
      <Section>
        {allModels.length === 0 && (
          <div className="mb-3 rounded-lg border border-dashed border-outline-variant/50 bg-surface-container px-4 py-5 text-center text-on-surface-low text-[0.82rem]">
            No models discovered. Add a backend in <span className="text-on-surface">Providers</span> and test its connection.
          </div>
        )}
        {USE_CASE_ORDER.map((uc, i) => {
          const meta = USE_CASE_META[uc]
          const prevGroup = i > 0 ? USE_CASE_META[USE_CASE_ORDER[i - 1]]?.group : undefined
          const showGroupHeader = meta?.group && meta.group !== prevGroup
          return (
            <div key={uc}>
              {showGroupHeader && <div className="mb-1.5 mt-3 px-1 text-on-surface-low text-[0.7rem] uppercase tracking-wide">{meta.group}</div>}
              <UseCaseRow useCase={uc} activeModels={active[uc] ?? []} allModels={allModels} onChanged={reloadActive} />
            </div>
          )
        })}
      </Section>
    </div>
  )
}

function UseCaseRow({ useCase, activeModels, allModels, onChanged }: {
  useCase: string; activeModels: string[]; allModels: AvailableModel[]; onChanged: () => void
}) {
  const [open, setOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [query, setQuery] = useState('')
  const [reindex, setReindex] = useState<import('../../lib/api').ReindexJob | null>(null)
  const meta = USE_CASE_META[useCase] ?? { label: useCase, description: '', multi: false, icon: Boxes }
  // Filter to models declaring this capability, then DEDUPE by the `provider:id`
  // ref. A model can legitimately surface from two discovery paths (e.g.
  // `OpenAI:gpt-image-1` appears via both the chat `/v1/models` sweep AND the
  // image_gen registry adapter), which would otherwise render two buttons with
  // the same React key (key-collision warning + a visible duplicate row).
  const capable = useMemo(() => capableModels(useCase, allModels, activeModels), [allModels, useCase, activeModels])
  // Filter by model name / id / provider. Active models always stay visible so a
  // narrowing search never hides the current selection.
  const matched = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return capable
    return capable.filter((m) => {
      const ref = `${m.provider}:${m.id}`
      return activeModels.includes(ref)
        || `${m.name} ${m.id} ${m.provider}`.toLowerCase().includes(q)
    })
  }, [capable, query, activeModels])
  // Float the SELECTED (active) models to the TOP so they're always at hand to
  // unselect — a stable partition (active first, each group keeping its original
  // order) so the list doesn't reshuffle on every toggle. (user request 2026-07-06)
  const filtered = useMemo(() => {
    const active: typeof matched = []
    const rest: typeof matched = []
    for (const m of matched) (activeModels.includes(`${m.provider}:${m.id}`) ? active : rest).push(m)
    return active.length ? [...active, ...rest] : matched
  }, [matched, activeModels])

  // Changing the embedding model invalidates every stored vector → warn, then
  // kick off a re-index of all knowledge + memory embeddings with live progress.
  const startReindex = () => {
    api.startEmbeddingReindex().then((job) => {
      setReindex(job)
      if (job.status !== 'running') return
      const es = new EventSource(api.embeddingReindexStreamUrl(job.id))
      const onFrame = (e: MessageEvent) => {
        try { const j = JSON.parse(e.data) as import('../../lib/api').ReindexJob; setReindex(j); if (j.status !== 'running') es.close() } catch { /* ignore */ }
      }
      for (const ev of ['snapshot', 'progress', 'done', 'error']) es.addEventListener(ev, onFrame as EventListener)
      es.onerror = () => es.close()
    }).catch((err) => {
      // 409 model_not_ready (or any failure): the change stands but vectors weren't
      // wiped — tell the user the index is stale until the model is ready.
      let msg = err instanceof Error ? err.message : String(err)
      try { msg = JSON.parse(msg).error || msg } catch { /* raw */ }
      setReindex({ id: '', model: '', status: 'error', phase: 'error', done: 0, total: 0, knowledge: 0, memory: 0, error: msg })
    })
  }

  const setActive = async (models: string[]) => {
    if (useCase === 'embedding') {
      const ok = await confirm({
        title: 'Change the embedding model?',
        body: 'Changing the embedding model will re-index ALL knowledge and memories. Existing embeddings are computed with the current model and are incompatible with a different one, so they must be regenerated.\n\nRe-indexing runs in the background and may take a while for large stores.',
        confirmLabel: 'Change & re-index',
      })
      if (!ok) return
    }
    setSaving(true)
    try {
      await api.setActiveModel(useCase, models)
      onChanged()
      if (useCase === 'embedding' && models.length > 0) startReindex()
    } finally { setSaving(false) }
  }
  const toggle = (ref: string) => {
    if (meta.multi) setActive(activeModels.includes(ref) ? activeModels.filter((m) => m !== ref) : [...activeModels, ref])
    else setActive(activeModels.includes(ref) ? [] : [ref])
  }

  return (
    <div className="mb-2 overflow-hidden rounded-lg bg-surface-container">
      <button type="button" onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-high">
        <ChevronRight size={14} className="shrink-0 text-on-surface-low transition-transform" style={{ transform: open ? 'rotate(90deg)' : 'none', color: open ? 'var(--color-primary)' : undefined }} />
        <span className="grid size-7 shrink-0 place-items-center rounded-md"
          style={activeModels.length > 0
            ? { background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)', color: 'var(--color-primary)' }
            : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}>
          <meta.icon size={14} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-on-surface text-[0.875rem]" style={{ fontVariationSettings: '"wght" 500' }}>{meta.label}</div>
          <div className="mt-0.5 text-on-surface-low text-[0.75rem]">
            {activeModels.length > 0 ? `${activeModels.length} active` : <span className="italic">none configured</span>}
          </div>
        </div>
        {capable.length > 0 && <span className="shrink-0 rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-low text-[0.68rem] tabular-nums">{capable.length} available</span>}
      </button>

      {open && (
        <div className="flex flex-col gap-3 border-t border-outline-variant/30 px-4 pb-4 pt-3">
          <p className="text-on-surface-low text-[0.8rem]">{meta.description}</p>
          <div className="inline-flex w-fit items-center gap-1.5 rounded-md px-2 py-1 text-[0.72rem]"
            style={meta.multi ? { background: 'color-mix(in srgb, var(--color-primary) 10%, transparent)', color: 'var(--color-primary)' } : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}>
            <span className="size-1.5 rounded-pill" style={{ background: meta.multi ? 'var(--color-primary)' : 'var(--color-on-surface-low)' }} />
            {meta.multi ? 'Multi-select — several models can be active' : 'Single-select — one model per use case'}
          </div>

          {useCase === 'embedding' && reindex && (
            <div className="rounded-md px-3 py-2 text-[0.78rem]"
              style={{ background: reindex.status === 'error' ? 'color-mix(in srgb, var(--color-danger) 10%, transparent)' : 'var(--color-surface-high)' }}>
              {reindex.status === 'error' ? (
                <span style={{ color: 'var(--color-danger)' }}>Re-index not started: {reindex.error}</span>
              ) : reindex.status === 'done' ? (
                <span style={{ color: 'var(--color-ok)' }}>Re-indexed {reindex.knowledge} knowledge + {reindex.memory} memory embeddings.</span>
              ) : (
                <div className="flex flex-col gap-1.5">
                  <span className="text-on-surface-var">Re-indexing embeddings — {reindex.phase}{reindex.total > 0 ? ` (${reindex.done}/${reindex.total})` : '…'}</span>
                  <div className="h-1.5 w-full overflow-hidden rounded-pill bg-surface-container">
                    <div className="h-full rounded-pill bg-primary transition-[width]" style={{ width: reindex.total > 0 ? `${Math.min(100, Math.round((reindex.done / reindex.total) * 100))}%` : '40%' }} />
                  </div>
                </div>
              )}
            </div>
          )}

          {capable.length === 0 ? (
            <div className="rounded-lg border border-dashed border-outline-variant/50 px-3 py-3 text-on-surface-low text-[0.8rem] italic">
              {meta.fallback ? (
                <>Already uses your <span className="text-on-surface not-italic font-medium">{meta.fallback}</span> model by default — no dedicated {meta.label} model is required. Add a backend with a {meta.label}-capable model to override.</>
              ) : (
                <>No models with {meta.label} capability. Add a backend with compatible models first.</>
              )}
            </div>
          ) : (
            <>
              {capable.length > 8 && (
                <div className="relative">
                  <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-low" />
                  <input
                    value={query} onChange={(e) => setQuery(e.target.value)}
                    placeholder={`Search ${capable.length} models — name or provider`}
                    className="h-9 w-full rounded-md bg-surface-high pl-9 pr-9 text-[0.8125rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50"
                  />
                  {query && (
                    <button type="button" onClick={() => setQuery('')} aria-label="Clear search"
                      className="absolute right-2 top-1/2 grid size-6 -translate-y-1/2 place-items-center rounded text-on-surface-low hover:text-on-surface">
                      <X size={13} />
                    </button>
                  )}
                </div>
              )}
              {filtered.length === 0 ? (
                <div className="rounded-md border border-dashed border-outline-variant/50 px-3 py-3 text-on-surface-low text-[0.8rem] italic">
                  No models match “{query}”.
                </div>
              ) : (
                <div className="-m-1 flex max-h-[300px] flex-col gap-0.5 overflow-y-auto p-1" style={{ opacity: saving ? 0.6 : 1 }}>
                  {filtered.map((m) => {
                const ref = `${m.provider}:${m.id}`
                const on = activeModels.includes(ref)
                // A LOCAL model (carries a `downloaded` flag) that's bound but NOT
                // downloaded won't actually run — surface it so "configured" never
                // silently means "inert" (e.g. after deleting a bound model's weights).
                const notDownloaded = m.downloaded === false
                return (
                  <button key={ref} type="button" onClick={() => toggle(ref)} disabled={saving}
                    className="flex items-center gap-2.5 rounded-md px-3 py-2 text-left transition-colors hover:bg-surface-high"
                    style={on ? { background: 'color-mix(in srgb, var(--color-primary) 12%, transparent)' } : undefined}>
                    <span className="grid size-4 shrink-0 place-items-center rounded border"
                      style={on ? { background: 'var(--color-primary)', borderColor: 'var(--color-primary)' } : { borderColor: 'var(--color-outline-variant)' }}>
                      {on && <Check size={10} strokeWidth={3} className="text-on-primary" />}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-on-surface text-[0.8rem] font-mono">{m.name}</span>
                    {on && notDownloaded && (
                      <span className="shrink-0 inline-flex items-center gap-1 rounded-pill px-1.5 py-0.5 text-[0.62rem]"
                        style={{ background: 'color-mix(in srgb, var(--color-warning) 16%, transparent)', color: 'var(--color-warning)' }}
                        title="Bound but not downloaded — download it in Providers to activate.">
                        <Download size={9} /> not downloaded
                      </span>
                    )}
                    <span className="shrink-0 rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low text-[0.65rem]">{m.provider}</span>
                  </button>
                )
                  })}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
