import { useEffect, useMemo, useRef, useState } from 'react'
import { ResultAnnouncement } from '../../ui/ListControls'
import {
  Check, MessageSquare, Boxes, Mic, Volume2, Eye, ImagePlus,
  Ear, Music, ScanEye, Clapperboard, Users, Download, Code2, BrainCircuit,
  Moon, Network, RefreshCcw, ArrowUp, ArrowDown, X, AlertTriangle, Wrench,
  Trash2, Gavel, FlaskConical, KeyRound, type LucideIcon,
} from 'lucide-react'
import {
  api, isLiveDownload, isNotRun, isSwitchedOff, type AvailableModel, type DownloadJob, type JudgeBenchRecommendation,
  type ModelConnection, type ProviderHealth, type ProviderModels, type HfTokenSource, type LocalModelHealth,
  type LocalModelSelftest,
} from '../../lib/api'
import { BundledDownloadProgress, modelBytes } from '../chat/bundledModelDownload'
import { namesModel, splitModelRef } from '../../lib/modelRef'
import {
  occupantDetail, pressureDetail, pressureTone, reclaimableCount, sortOccupants,
} from '../../lib/residency'
import { IconButton } from '../../ui/IconButton'
import { Button } from '../../ui/Button'
import { Meter } from '../../ui/Meter'
import { WavyProgress } from '../../ui/WavyProgress'
import { SearchField } from '../../ui/SearchField'
import { TextInput } from '../../ui/forms'
import { StatusPill } from '../../ui/StatusPill'
import { useQuery, invalidateKeys } from '../../lib/data'
import type { Rebase, Revisioned } from '../../lib/staleWrite'
import { useStaleWriteGuard } from '../../lib/useStaleWriteGuard'
import { useEmbeddingReindex } from '../../lib/useEmbeddingReindex'
import { StaleWriteNotice } from '../../ui/StaleWriteNotice'
import { confirm } from '../../ui/dialog'
import { PanelHeader, Section, RowGroup, ToggleRow, NumberRow } from './settingsUI'
import { notify } from '../../app/appSdk'
import { FormSkeleton, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { fvs } from '../../design/fontWeight'
import { accentChip } from '../../design/accent'
import { ERROR_SURFACE_PAINT } from '../../design/errorTreatments'
import { DisclosureCard } from '../../ui/DisclosureCard'
import { BUSY_REASON } from '../../ui/unavailable'
import { reportingWrite } from '../../app/reportingWrite'
import { DownloadFailure, InlineModelDownload, isDownloadable, modelLabel, useRowDownload } from './InlineModelDownload'
import { HELD_CHANGE_REASON } from '../../lib/staleWrite'

/** The `/api/models/available` rows whose type is the use case they serve: one per image- or
 *  video-generation provider. */
const MEDIA_ROW_TYPES: ReadonlySet<string> = new Set(['image_gen', 'video_gen'])
/** A media provider that can't generate right now, and the reason it gives. */
interface UnavailableProvider { name: string; useCase: string; error: string }

// Canonical use-cases (matches the backend's USE_CASES vocabulary).
// `chain`: the binding is an ordered fallback CHAIN (position 0 = default,
// later entries tried when an earlier provider's breaker is open or its build
// fails) with a reorderable editor; else single-select.
// `fallback` names the use-case this one INHERITS its binding from when no model
// is pinned here (mirrors backend parent_capability). Its presence changes the
// empty-picker state from a misleading "add a backend first" to an accurate
// "already uses your <fallback> chain; pin one here only to override".
const USE_CASE_META: Record<string, { label: string; group?: string; description: string; chain: boolean; icon: LucideIcon; fallback?: string }> = {
  chat: { label: 'Chat', description: 'Conversational models for chat and agent interactions. Order matters: the first model is the default; later ones are fallbacks used when an earlier provider is down.', chain: true, icon: MessageSquare },
  code_tools: { label: 'Code & tools', group: 'Chat routing', description: 'Native agent turns that lean on tool use and code work.', chain: true, icon: Code2, fallback: 'Chat' },
  reasoning: { label: 'Reasoning', group: 'Chat routing', description: 'One-shot judgment calls — web-page extraction and other guarded single completions.', chain: true, icon: BrainCircuit, fallback: 'Chat' },
  background: { label: 'Background', group: 'Chat routing', description: 'Housekeeping chores — session titles, tags, suggestions, digests, consolidation. Bind a cheap or local model here so chores stop burning your main chat model.', chain: true, icon: Moon, fallback: 'Chat' },
  orchestration: { label: 'Orchestration', group: 'Chat routing', description: 'Supervising turns, webhook agent turns, and subagents spawned without an explicit model.', chain: true, icon: Network, fallback: 'Chat' },
  loops: { label: 'Loops', group: 'Chat routing', description: 'Autonomous goal-loop workers, gates and judges — long-horizon work that benefits from a long-context model.', chain: true, icon: RefreshCcw, fallback: 'Chat' },
  embedding: { label: 'Embedding', group: 'Capabilities', description: 'Vector embedding models for knowledge and memory.', chain: false, icon: Boxes },
  stt: { label: 'Speech-to-text', group: 'Capabilities', description: 'Voice transcription models.', chain: false, icon: Mic },
  tts: { label: 'Text-to-speech', group: 'Capabilities', description: 'Voice synthesis models.', chain: false, icon: Volume2 },
  diarization: { label: 'Speaker diarization', group: 'Capabilities', description: 'Labels "who spoke when" in audio/video (speaker turns). Served by diarization providers (ONNX, pyannote).', chain: false, icon: Users },
  image_modality: { label: 'Image · Modality', group: 'Image', description: 'Models that understand images as input (vision / VLM).', chain: true, icon: Eye },
  image_gen: { label: 'Image · Generation', group: 'Image', description: 'Models that generate images from a prompt.', chain: false, icon: ImagePlus },
  audio_modality: { label: 'Audio · Modality', group: 'Audio', description: 'Models that understand audio as input.', chain: false, icon: Ear },
  audio_gen: { label: 'Audio · Generation', group: 'Audio', description: 'Models that generate audio, music, or sound effects.', chain: false, icon: Music },
  video_modality: { label: 'Video · Modality', group: 'Video', description: 'Models that understand video as input.', chain: false, icon: ScanEye },
  video_gen: { label: 'Video · Generation', group: 'Video', description: 'Models that generate video from a prompt.', chain: false, icon: Clapperboard },
  // NOTE: knowledge-ingestion (OCR/vision/classify/consolidation) has NO dedicated
  // use-case rows — each ingestion node resolves directly to the relevant default
  // binding (Image·Modality / Chat / Speech-to-text). There is no per-role override.
}
const USE_CASE_ORDER = [
  'chat', 'code_tools', 'reasoning', 'background', 'orchestration', 'loops',
  'embedding', 'stt', 'tts', 'diarization',
  'image_modality', 'image_gen', 'audio_modality', 'audio_gen', 'video_modality', 'video_gen',
]

// Chat sub-categories (mirrors backend CHAT_SUBCATEGORIES): models never declare
// these as capabilities — their pickable pool is the CHAT-capable catalog.
const CHAT_SUBCATEGORIES = new Set(['code_tools', 'reasoning', 'background', 'orchestration', 'loops'])

/** The models a use-case can pick from: every catalog model declaring the capability
 *  (a chat SUB-CATEGORY draws from the chat pool — models never declare "code_tools"),
 *  deduped by `provider:id`, PLUS a synthetic "unavailable" row for any ACTIVE binding
 *  whose model is absent from the catalog (e.g. an ollama model deleted or never pulled).
 *  Without the synthetic row the use-case reads "N active" but the bound model is invisible
 *  AND unremovable in the picker — a phantom binding the user can't clear. Synthetic rows
 *  carry `downloaded:false` so the not-downloaded chip renders; toggling one off unbinds it.
 *
 *  A row or binding that names no model (`namesModel`) is none to pick: toggling it would bind
 *  `"provider:"`, which chose no model and which the gateway refuses.
 *  Pure + exported for unit testing. */
export function capableModels(useCase: string, allModels: AvailableModel[], activeModels: string[]): AvailableModel[] {
  const capability = CHAT_SUBCATEGORIES.has(useCase) ? 'chat' : useCase
  const seen = new Set<string>()
  const out: AvailableModel[] = []
  for (const m of allModels) {
    if (!m.capabilities.includes(capability)) continue
    const ref = `${m.provider}:${m.id}`
    if (seen.has(ref) || !namesModel(ref)) continue
    seen.add(ref)
    out.push(m)
  }
  for (const ref of activeModels) {
    if (seen.has(ref) || !namesModel(ref)) continue
    seen.add(ref)
    const { provider, model: id } = splitModelRef(ref)
    out.push({ id, name: id, provider, capabilities: [useCase], downloaded: false } as AvailableModel)
  }
  return out
}

/** What the page learned about one provider when it read its models: whether they could be
 *  listed (`error`), the connection its last test measured, and the ids it lists. Read once for
 *  every row, so a chain entry can say its instance is down or its model is gone. */
export interface ProviderListing {
  error: string
  connection?: ModelConnection
  /** The provider has a local download card (an Ollama instance, a bundled runtime): it lists
   *  every model it has, so a bound model it does not list is gone from it. */
  local: boolean
  /** An array, not a Set: the panel's read is persisted to session storage as JSON. */
  listed: readonly string[]
}

/** The listings of every provider the models read returned, by name. A media row (image or video
 *  generation) is left out: why a provider cannot GENERATE says nothing about its chat models, and
 *  it has its own notice (`unavailable`). */
export function providerListings(rows: ProviderModels[]): Record<string, ProviderListing> {
  const out: Record<string, ProviderListing> = {}
  for (const r of rows) {
    if (MEDIA_ROW_TYPES.has(r.type)) continue
    const held = out[r.name]
    out[r.name] = {
      error: held?.error || r.error || '',
      connection: held?.connection ?? r.connection,
      local: !!held?.local || !!r.local,
      listed: [...(held?.listed ?? []), ...(r.models ?? []).map((m) => m.id)],
    }
  }
  return out
}

/** Why *provider* could not be asked for its models: its measured connection failed (a refused
 *  key named as one), or listing them failed — in the words its test or its listing used. Null
 *  when it answered. Its models are then MISSING from the list because it did not answer, not
 *  because they are gone or not downloaded, so no row may say either of those about them. */
export function listingFailure(
  provider: string, listing: ProviderListing | undefined,
): { tone: 'danger'; label: string; detail: string } | null {
  const conn = listing?.connection
  if (conn?.state === 'failed') {
    return conn.rejected_credential
      ? { tone: 'danger', label: 'key rejected', detail: conn.detail || `${provider} refused its key.` }
      : { tone: 'danger', label: 'not answering', detail: conn.detail || listing?.error || `${provider} did not answer its connection test.` }
  }
  if (listing?.error) return { tone: 'danger', label: 'not answering', detail: listing.error }
  return null
}

/** Why one chain entry cannot run right now, in words — or null when nothing says so.
 *
 *  A chain entry showed only its provider's breaker as a coloured dot, whose one text was a
 *  tooltip: with its instance down the entry read "recovering — next call probes it", and one
 *  whose model had vanished from its instance showed nothing at all. First match wins:
 *
 *  1. the instance's measured connection failed, or its models could not be listed → it is not
 *     answering (or its key was refused), in the words its test or its listing used;
 *  2. its breaker has tripped (`open`, or `half_open`: reached only from open, and left only by a
 *     call that succeeds) → its calls are failing;
 *  3. a LOCAL provider that answered does not list the model → the model is gone from it (the
 *     Doctor prunes such a binding). A hosted provider can serve a model it does not list, so it
 *     is never judged this way — the same line the Doctor's check draws;
 *  4. a local model it lists is not downloaded → it will not run until it is.
 *
 *  Pure + exported for unit testing. */
export function chainEntryStatus(
  ref: string, listing: ProviderListing | undefined, health: ProviderHealth | undefined,
  model: AvailableModel | undefined,
): { tone: 'danger' | 'warn'; label: string; detail: string } | null {
  const { provider, model: id } = splitModelRef(ref)
  const down = listingFailure(provider, listing)
  if (down) return down
  if (health && (health.breaker_state === 'open' || health.breaker_state === 'half_open')) {
    const n = health.consecutive_failures
    const calls = `${provider}: its last ${n} call${n === 1 ? '' : 's'} failed`
    return {
      tone: 'danger', label: 'failing',
      detail: health.breaker_state === 'open'
        ? `${calls}. It is skipped until it answers again, and the next entry in this chain runs instead.`
        : `${calls}. The next call to it tests whether it answers again.`,
    }
  }
  if (listing?.local && !listing.listed.includes(id)) {
    return {
      tone: 'danger', label: 'unavailable',
      detail: `${provider} no longer lists ${id}, so it cannot run. Remove it here, or prune it from Settings → Doctor.`,
    }
  }
  if (listing?.local && model?.downloaded === false) {
    return { tone: 'warn', label: 'not downloaded', detail: `${id} is not on ${provider} yet, so it cannot run until it is downloaded.` }
  }
  return null
}

/** The contract chips a model row shows (LMMV §2.2/§2.3), as pure data so the mapping
 *  is unit-testable independently of rendering:
 *   - `deprecated`/`sunset` status → an informational chip (the model stays bindable).
 *   - a non-commercial license → a warning chip surfaced AT BIND TIME (Success Criterion 7).
 *   - `integrity: "truncated"` → a danger chip whose row offers Repair (re-download).
 *  A hosted/remote model (no catalog fields) yields no chips. */
export type ChipKind = 'status' | 'non-commercial' | 'truncated'
export function modelChips(m: AvailableModel): ChipKind[] {
  const chips: ChipKind[] = []
  if (m.status === 'deprecated' || m.status === 'sunset') chips.push('status')
  if (m.non_commercial) chips.push('non-commercial')
  if (m.integrity === 'truncated') chips.push('truncated')
  return chips
}

/** The contract chips (+ a Repair button when truncated) for one model row. Kept beside
 *  the provider chip in the row; renders nothing for a model carrying no catalog fields. */
function ModelChips({ model, onRepair, repairing }: {
  model: AvailableModel; onRepair: () => void; repairing: boolean
}) {
  const chips = modelChips(model)
  if (chips.length === 0) return null
  return (
    <span className="flex shrink-0 items-center gap-1">
      {model.status === 'deprecated' && (
        <span data-type="caption" className="rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low uppercase tracking-wide"
          title="Deprecated — still bindable, but a newer model is preferred.">deprecated</span>
      )}
      {model.status === 'sunset' && (
        <span data-type="caption" className="rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low uppercase tracking-wide"
          title="Sunset — hidden from new bindings; an existing binding keeps working.">sunset</span>
      )}
      {model.non_commercial && (
        <span data-type="caption" className="inline-flex items-center gap-1 rounded-pill px-1.5 py-0.5"
          style={{ background: 'color-mix(in srgb, var(--color-warning) 16%, transparent)', color: 'var(--color-warning)' }}
          title={`Non-commercial license${model.license ? ` (${model.license})` : ''} — for personal/research use only.`}>
          <AlertTriangle size={9} /> non-commercial
        </span>
      )}
      {model.integrity === 'truncated' && (
        <>
          <span data-type="caption" className="inline-flex items-center gap-1 rounded-pill px-1.5 py-0.5"
            style={{ background: 'color-mix(in srgb, var(--color-danger) 16%, transparent)', color: 'var(--color-danger)' }}
            title="Downloaded weights are incomplete — this model won't load. Repair to re-download.">
            truncated
          </span>
          <button type="button" onClick={onRepair} disabled={repairing}
            data-type="caption" className="inline-flex items-center gap-1 rounded-pill px-1.5 py-0.5 transition-colors hover:bg-surface-high"
            style={{ background: 'var(--color-surface-high)', color: 'var(--color-on-surface)' }}
            title="Re-download this model's weights.">
            <Wrench size={9} /> {repairing ? 'repairing…' : 'Repair'}
          </button>
        </>
      )}
    </span>
  )
}

/** "Reclaim N GiB" — surfaces the partial-download leftovers (cancelled/crashed fetches)
 *  that otherwise sit invisible across every local provider's cache root, and unlinks
 *  them on confirm. Renders nothing when there's nothing to reclaim, so a clean install
 *  shows no affordance. `onReclaimed` lets the caller revalidate the model list after a
 *  sweep (a repaired/removed partial changes a row's downloaded state). */
function ReclaimButton({ onReclaimed }: { onReclaimed: () => void }) {
  const [totalBytes, setTotalBytes] = useState(0)
  const [busy, setBusy] = useState(false)

  const refreshCandidates = () =>
    api.modelDownloadCleanupCandidates()
      .then((r) => setTotalBytes(r.total_bytes))
      .catch(() => setTotalBytes(0))

  useEffect(() => { refreshCandidates() }, [])

  if (totalBytes <= 0) return null

  const reclaim = async () => {
    const ok = await confirm({
      title: `Reclaim ${modelBytes(totalBytes)}?`,
      body: 'Deletes partial-download leftovers (.part / .tmp / .incomplete files) from cancelled or interrupted fetches. Fully downloaded models are untouched.',
      confirmLabel: 'Reclaim',
    })
    if (!ok) return
    setBusy(true)
    try {
      // Was a bare `try/finally`: a refused cleanup rejected unhandled and the button simply
      // stopped spinning, which is what success looks like too.
      if (!(await reportingWrite('reclaim that space', () => api.modelDownloadCleanup()))) return
      await refreshCandidates()
      onReclaimed()
    } finally { setBusy(false) }
  }

  return (
    <Button variant="tonal" size="xs" loading={busy} onClick={reclaim}
      title="Delete partial-download leftovers from cancelled or interrupted fetches.">
      <Trash2 size={13} /> Reclaim {modelBytes(totalBytes)}
    </Button>
  )
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
  //
  // 🔴 NEITHER READ SWALLOWS ITS REJECTION, AND THE SITE BINDS `error`. Both halves were wrong
  // together, which is what made this panel the last surface in the first-run set with no terminal
  // state: `api.modelsAvailable().catch(() => [])` + `api.modelsActive().catch(() => ({}))` made
  // `error` structurally unreachable, and the call site read only `data` — so a read that failed
  // painted "No models discovered" (a confident lie about a page that never loaded) and a read that
  // never settled left `<ListSkeleton>` up forever. Measured on a fresh home with `/api` held
  // open: `#/settings/models` was still on "Loading…" with `aria-busy=1` and no Retry at 9.5s,
  // while Inbox, Apps, Settings-home and Providers had all reached a legible error. Both models
  // reads are REQUIRED to render a binding, so failing the panel is the honest outcome; the two
  // optional enrichments below (`health`, `judgeRecs`) keep their catches on purpose and say why.
  const { data, error: modelsErr, refresh } = useQuery('settings:models', async () => {
    const [rows, active] = await Promise.all([
      api.modelsAvailable(),
      // Each chain WITH its revision: a row saves its whole chain, over the copy it painted.
      api.activeChains(),
    ])
    // `local` marks a provider the local-model registry lists — one that can DOWNLOAD a model it
    // does not have yet — so a row can offer its Download right where it is chosen.
    // `unavailable`: an image or video provider that can't generate right now, with why. Its row
    // lists no models, so flattening the rows into models alone dropped it from the page.
    const unavailable = rows
      .filter((r) => r.error && MEDIA_ROW_TYPES.has(r.type))
      .map((r): UnavailableProvider => ({ name: r.name, useCase: r.type, error: r.error ?? '' }))
    return {
      allModels: rows.flatMap((r) => r.models ?? []), active, localProviders: rows.filter((r) => r.local).map((r) => r.name),
      unavailable, listings: providerListings(rows),
    }
  }, { persist: true })
  // Per-provider breaker health for the chain-entry dots — refreshed on panel
  // mount (persist:false so a broken provider isn't shown green from cache).
  const { data: health } = useQuery('settings:models-health', () =>
    api.modelsHealth().then((h) => h.providers).catch(() => [] as ProviderHealth[]), { persist: false })
  // The judge benchmark's tier recommendations, so rebinding a judge to the cheapest
  // adequate tier is ONE action here rather than a hand-translation from a table on another
  // page. Evals off (`{"enabled": false}`), no benchmark yet (`{"ran": false}`) and a failure all
  // collapse to "no recommendation, no chip" — which is an honest absence rather than a swallowed
  // error, because the Learning page's Judge tiers panel is the surface that owns reporting WHY
  // there is none. The first two are decided 200s: "no benchmark yet" used to be a 404, so with
  // evals on every visit here logged a failed request until someone ran the CLI benchmark.
  const { data: judgeRecs } = useQuery('settings:judge-bench-recs', () =>
    api.judgeBench()
      .then((v) => (isSwitchedOff(v) || isNotRun(v) ? [] : v.recommendations))
      .catch(() => [] as JudgeBenchRecommendation[]),
    { persist: false })
  // 🔑 The downloads the gateway holds, read ONCE for every row, fresh on each open (see
  // `useRowDownload`): after a reload, a Repair or a Download still running shows its progress
  // again, and one that failed says why. An enrichment like the two above: a failed read leaves
  // every row as it would be with nothing running.
  const [downloads, setDownloads] = useState<ReadonlyMap<string, DownloadJob>>(NO_DOWNLOADS)
  useEffect(() => {
    let alive = true
    api.modelDownloads().then((all) => { if (alive) setDownloads(latestDownloads(all)) }).catch(() => { /* none re-attach */ })
    return () => { alive = false }
  }, [])
  const allModels = data?.allModels
  const active = data?.active ?? {}
  const localProviders = useMemo(() => new Set(data?.localProviders ?? []), [data?.localProviders])

  // A binding mutation invalidates the cached catalog so the next read revalidates
  // against the changed state instead of a stale snapshot.
  const reloadActive = () => { invalidateKeys('settings:models'); refresh() }

  if (!allModels && modelsErr) return <LoadError what="models" error={modelsErr} onRetry={refresh} />
  if (!allModels) return <ListSkeleton rows={6} what="models" />

  return (
    <div>
      <div className="flex items-start justify-between gap-3">
        <PanelHeader title="Models" hint="Assign discovered models to each use case. Chat and its routing sub-categories store an ordered fallback chain — the first model is the default; later ones take over when an earlier provider is down. Modality means understanding that media as input; Generation means producing it." />
        <div className="shrink-0 pt-1"><ReclaimButton onReclaimed={reloadActive} /></div>
      </div>
      {/* 🔴 Titled: this is the panel's primary group and it was the only one without a heading,
          while "Prompt caching" further down had one. Measured on `#/settings/models`: 16 controls
          — every use-case row — belonged to no section, so the heading outline jumped from "Models"
          straight to "Prompt caching" and skipped the thing the panel is for. The uppercase group
          labels inside (`meta.group`) stay plain `<div>`s: they are a visual grouping of rows within
          this one section, and promoting them is its own change. */}
      <Section title="Model bindings" hint="One model — or an ordered fallback chain — per use case.">
        {allModels.length === 0 && (
          <div data-type="body-s" className="mb-3 rounded-lg border border-dashed border-outline-variant/50 bg-surface-container px-4 py-5 text-center text-on-surface-low">
            No models discovered. Add a backend in <span className="text-on-surface">Providers</span> and test its connection.
          </div>
        )}
        {USE_CASE_ORDER.map((uc, i) => {
          const meta = USE_CASE_META[uc]
          const prevGroup = i > 0 ? USE_CASE_META[USE_CASE_ORDER[i - 1]]?.group : undefined
          const showGroupHeader = meta?.group && meta.group !== prevGroup
          return (
            <div key={uc}>
              {showGroupHeader && <div data-type="caption" className="mb-1.5 mt-3 px-1 text-on-surface-low uppercase tracking-wide">{meta.group}</div>}
              <UseCaseRow useCase={uc} chain={active[uc] ?? NO_CHAIN} allModels={allModels} localProviders={localProviders} downloads={downloads} health={health ?? []} judgeRec={(judgeRecs ?? []).find((r) => r.verdict === 'recommended' && r.use_case === uc)}
                listings={data?.listings ?? NO_LISTINGS}
                unavailable={(data?.unavailable ?? []).filter((p) => p.useCase === uc)} onChanged={reloadActive} />
            </div>
          )
        })}
      </Section>
      <HfTokenSection />
      <LoadedModelsSection />
      <LocalRuntimeSection />
      <PromptCacheSection />
    </div>
  )
}

/** The `local_models.*` config section — how the local inference runtime is bounded and how the
 *  catalog is filtered.
 *
 *  All six were PATCH-editable, bounded, `_meta`-labelled and read (`fit.py`, `sidecar.py`,
 *  `residency.py`, `hf_token.py`, `model_downloads.py`) with NO control anywhere in `web/` — the
 *  section reads the values (`lib/residency.ts` applies `pressure_warn_pct` to the loaded-models
 *  bar) and had no way to write any of them. It lives HERE because this panel already owns the
 *  local stack: the loaded-models bar those two memory knobs govern, and the HuggingFace token
 *  whose check interval is one of them, are both directly above. */
function LocalRuntimeSection() {
  const [cfg, setCfg] = useState<Record<string, unknown> | null>(null)
  const { data, error: loadErr, refresh } = useQuery('settings:local-models', () =>
    api.personalclawConfig().then((c) => (c.local_models ?? {}) as Record<string, unknown>),
    { persist: true },
  )
  useEffect(() => { if (data) setCfg(data) }, [data])

  if (!data && loadErr) return <LoadError what="local-model settings" error={loadErr} onRetry={refresh} />
  if (!data || !cfg) return <FormSkeleton sections={1} what="local-model settings" />

  const patch = (key: string, value: unknown, onSaved?: () => void, label?: string) => {
    const prev = cfg[key]
    setCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`local_models.${key}`, value).then(() => {
      onSaved?.()
      // `hide_unrunnable_models` and `memory_reserve_gb` both change the model-fit verdict this
      // panel's catalog is filtered by, so the list must revalidate rather than keep a snapshot
      // taken under the old thresholds.
      invalidateKeys('settings:models')
    }).catch((e) => {
      setCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  return (
    <Section title="Local runtime" hint="Memory headroom for local inference, what the browse list shows, and the timeouts around a model sidecar.">
      <RowGroup>
        <ToggleRow label="Hide models this device cannot run" cfg={cfg} field="hide_unrunnable_models" patch={patch}
          hint="Keep models that do not fit this machine's memory out of the browse list. On by default; turn it off to see the whole catalog." />
        <NumberRow label="Memory reserve (GiB)" cfg={cfg} field="memory_reserve_gb" min={0} max={64} step={0.5} patch={patch}
          hint="Memory held back for your OS and the inference runtime, subtracted before any model-fit verdict. Raise it if models fit on paper but your machine struggles — verdicts get more cautious. It never blocks anything." />
        <NumberRow label="Memory pressure warning (%)" cfg={cfg} field="pressure_warn_pct" min={1} max={100} patch={patch}
          hint="Percent of system RAM in use at which the loaded-models bar above warns. Advisory only — nothing is unloaded for you." />
        <NumberRow label="Sidecar restart limit" cfg={cfg} field="sidecar_restart_max" min={0} max={20} patch={patch}
          hint="How many times in a row a crashed model sidecar is respawned before the runner gives up and reports the failure instead." />
        <NumberRow label="Model selftest timeout (seconds)" cfg={cfg} field="selftest_timeout_s" min={5} max={600} patch={patch}
          hint="How long a per-capability selftest may run before it is stopped and reported as timed out. A selftest runs a real inference on click, so this bounds a model that hangs while loading." />
        <NumberRow label="HuggingFace token check interval (seconds)" cfg={cfg} field="whoami_ttl_s" min={0} max={86400} step={60} patch={patch}
          hint="How long a HuggingFace token's validity is cached after a successful check, so listing models does not re-call HuggingFace every time. 0 re-checks on every read." />
      </RowGroup>
    </Section>
  )
}

/** HuggingFace token cascade (LMMV §5). Shows the three sources — the saved credential, the
 *  process environment, and a `huggingface-cli login` file — each with HuggingFace's own
 *  whoami verdict (valid + username, or not), a MASKED preview (the value never crosses the
 *  wire), and an "active" badge on the first valid one. The set/clear field writes SOURCE 1
 *  (the credential store); a set/clear is SEL-audited server-side and re-checks the whole
 *  cascade. Invalidates the models cache too, so a gated model's pre-warn chip clears the
 *  moment a valid token lands. */
const HF_SOURCE_LABEL: Record<HfTokenSource['source'], string> = {
  credential_store: 'Saved token',
  env: 'Environment (HF_TOKEN)',
  hf_cli_file: 'huggingface-cli login',
}

function HfTokenSection() {
  const { data, error: loadErr, refresh } = useQuery('settings:hf-token', () =>
    api.hfTokenStatus().then((d) => d.sources), { persist: false })
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)

  // A failed read must not render as "no token configured" — an unreachable gateway and a
  // genuinely empty cascade look identical, and one is a lie about the machine's credentials.
  if (!data && loadErr) return <LoadError what="HuggingFace token" error={loadErr} onRetry={refresh} />
  if (!data) return <FormSkeleton sections={1} what="HuggingFace token" />

  const stored = data.find((s) => s.source === 'credential_store')

  const afterWrite = () => {
    // Repaint the cascade AND the models list — a gated model's `token_ready` pre-warn is
    // computed from exactly this token, so both must revalidate against the new state.
    invalidateKeys('settings:hf-token'); invalidateKeys('settings:models'); refresh()
  }
  const save = async () => {
    if (!value.trim()) return
    setBusy(true)
    try {
      if (!(await reportingWrite('save the HuggingFace token', () => api.setHfToken(value.trim())))) return
      setValue('')
      afterWrite()
    } finally { setBusy(false) }
  }
  const clear = async () => {
    const ok = await confirm({
      title: 'Clear the saved HuggingFace token?',
      body: 'Removes it from the credential store. Gated models will need a token again before they can download.',
      confirmLabel: 'Clear',
    })
    if (!ok) return
    setBusy(true)
    try {
      if (!(await reportingWrite('clear the HuggingFace token', () => api.clearHfToken()))) return
      afterWrite()
    } finally { setBusy(false) }
  }

  return (
    <Section title="HuggingFace token" hint="Gated models (e.g. pyannote diarization) need a HuggingFace token with the model's license accepted. PersonalClaw checks each source below and uses the first one HuggingFace confirms is valid.">
      <div className="flex flex-col gap-2 rounded-lg bg-surface-container px-4 py-3">
        {data.map((s) => (
          <div key={s.source} className="flex items-center gap-2">
            <span data-type="label-s" className="w-44 shrink-0 text-on-surface">{HF_SOURCE_LABEL[s.source] ?? s.source}</span>
            {!s.present ? (
              <span data-type="caption" className="text-on-surface-low">{s.note || 'not set'}</span>
            ) : (
              <span className="flex min-w-0 flex-wrap items-center gap-2">
                <span data-type="caption" className="font-mono text-on-surface-low">{s.masked}</span>
                {s.valid ? (
                  <span data-type="caption" className="inline-flex items-center gap-1" style={{ color: 'var(--color-ok)' }}>
                    <Check size={11} /> {s.username ? `valid — ${s.username}` : 'valid'}
                  </span>
                ) : (
                  <span data-type="caption" className="inline-flex items-center gap-1" style={{ color: 'var(--color-warning)' }}>
                    <AlertTriangle size={11} /> not valid
                  </span>
                )}
                {s.active && (
                  <span data-type="caption" className="rounded-pill px-1.5 py-0.5" style={accentChip}>active</span>
                )}
              </span>
            )}
          </div>
        ))}
        <div className="mt-1 flex items-center gap-2">
          <div className="min-w-0 flex-1">
            <TextInput type="password" value={value} onChange={setValue}
              placeholder="hf_…  paste a token to save"
              ariaLabel="HuggingFace token" size="md" mono />
          </div>
          <Button variant="tonal" size="sm" loading={busy} disabled={!value.trim()} disabledReason="Paste a token first" onClick={save}>Save</Button>
          {stored?.present && <Button variant="tonal" size="sm" loading={busy} onClick={clear}>Clear</Button>}
        </div>
      </div>
    </Section>
  )
}

/** A per-model "Test" affordance (LMMV §6): runs the provider's health check + a real
 *  per-capability inference on click and shows the TYPED result inline. User-click only — a
 *  selftest can page a model into RAM, so it is never fired automatically. Rendered under a
 *  downloaded LOCAL model row; a broken runtime contract surfaces its typed reason here. */
function ModelTestButton({ provider, model }: { provider: string; model: string }) {
  const [busy, setBusy] = useState(false)
  const [health, setHealth] = useState<LocalModelHealth | null>(null)
  const [result, setResult] = useState<LocalModelSelftest | null>(null)
  const [err, setErr] = useState('')

  const run = async () => {
    setBusy(true); setErr('')
    try {
      const [h, s] = await Promise.all([
        api.localModelHealth(provider).catch(() => null),
        api.localModelSelftest(provider, model),
      ])
      setHealth(h); setResult(s)
    } catch (e) {
      setErr(String((e as Error)?.message || e))
    } finally { setBusy(false) }
  }

  const caps = result ? Object.entries(result.capabilities) : []
  return (
    <div className="flex flex-col gap-1 px-3 pb-2">
      <Button variant="secondary" size="xs" className="w-fit" onClick={run}
        loading={busy} loadingLabel="testing…"
        title="Run a real inference to check this model actually works on this machine.">
        <FlaskConical size={10} /> Test
      </Button>
      {err && <span data-type="caption" style={{ color: 'var(--color-danger)' }}>{err}</span>}
      {health && !health.ok && (
        <span data-type="caption" style={{ color: 'var(--color-warning)' }}>Provider: {health.message}</span>
      )}
      {result && (caps.length === 0 ? (
        <span data-type="caption" className="text-on-surface-low">{result.detail}</span>
      ) : (
        <div className="flex flex-col gap-0.5">
          {/* A failure shows what went wrong (the provider's sentence, which wraps) and its typed
              reason after it. The reason used to stand in for the sentence, so the next step a
              provider named was never shown. */}
          {caps.map(([cap, r]) => (
            <span key={cap} data-type="caption" className="flex items-start gap-1"
              style={{ color: r.ok ? 'var(--color-ok)' : 'var(--color-danger)' }}>
              {r.ok ? <Check size={10} className="mt-0.5 shrink-0" /> : <X size={10} className="mt-0.5 shrink-0" />}
              <span className="min-w-0 break-words">
                {cap}: {r.detail} ({!r.ok && r.reason ? `${r.reason}, ` : ''}{r.duration_ms} ms)
              </span>
            </span>
          ))}
        </div>
      ))}
    </div>
  )
}

/** Loaded models + memory pressure (LMMV §7) — "what is occupying my RAM right now".
 *
 *  Answers the question no surface answered before: a model stays resident after its
 *  binding moves elsewhere, and a sidecar adds a whole child process. Rows are ordered
 *  reclaimable-first (see lib/residency), because the row a user can act on is the one
 *  still in memory with nothing bound to it. Unload is idempotent server-side and the
 *  reply carries a fresh pressure snapshot, so the bar moves as proof rather than the UI
 *  claiming the memory went. */
function LoadedModelsSection() {
  const { data, error: loadErr, refresh } = useQuery('models:loaded', () =>
    api.modelsLoaded(), { persist: false },
  )
  const [busy, setBusy] = useState('')

  // A failed read must not render as "nothing is loaded" — an empty list and an unreachable
  // gateway look identical, and one of them is a lie about the machine's memory.
  if (!data && loadErr) return <LoadError what="loaded models" error={loadErr} onRetry={refresh} />
  if (!data) return <FormSkeleton sections={1} what="loaded models" />

  const rows = sortOccupants(data.loaded)
  const reclaimable = reclaimableCount(rows)
  // A provider paging a multi-gigabyte model in from disk is `loading`, not hung. Saying so
  // is the whole reason ensure_ready() reports a state instead of a bare boolean — without
  // this line the payload would carry the answer and the screen would stay silent.
  const notReady = data.providers.filter((p) => p.state !== 'ready')

  const unload = async (provider: string) => {
    const ok = await confirm({
      title: `Unload ${provider}?`,
      body: 'Frees the memory this provider holds. The next request loads the model again, which takes as long as the first load did.',
      confirmLabel: 'Unload',
    })
    if (!ok) return
    setBusy(provider)
    try {
      await api.unloadModelProvider(provider)
      // `refresh()` refetches THIS surface's key. The dashboard's "on this machine" widget makes the
      // byte-identical read and can also unload, so each left the other's cached copy describing memory
      // that is no longer held — visible on its next mount until the revalidation lands. One shared key
      // means there is only one answer to be wrong.
      invalidateKeys('models:loaded')
      refresh()
    } catch (e) {
      notify(`Couldn't unload ${provider}: ${String((e as Error)?.message || e)}`, 'error')
    } finally {
      setBusy('')
    }
  }

  return (
    <Section
      title="On this machine"
      hint={
        reclaimable > 0
          ? `${reclaimable} resident model${reclaimable === 1 ? '' : 's'} no longer bound to a use case — unloading frees its memory until something needs it again.`
          : 'Models currently held in memory, and how much of this machine they are using.'
      }
    >
      <div className="rounded-lg bg-surface-container px-4 py-3">
        <Meter
          label="System memory in use"
          pct={data.pressure.used_pct}
          tone={pressureTone(data.pressure)}
          detail={pressureDetail(data.pressure)}
        />
        {notReady.length > 0 && (
          <ul data-type="body-s" className="mt-3 flex flex-col gap-0.5 text-on-surface-low">
            {notReady.map((p) => (
              <li key={p.provider}>
                {p.display_name}:{' '}
                {p.state === 'loading' ? 'loading a model now' : 'unavailable on this machine'}
              </li>
            ))}
          </ul>
        )}
        {rows.length === 0 ? (
          <div data-type="body-s" className="mt-3 text-on-surface-low">
            No models are loaded right now. One loads on its first use.
          </div>
        ) : (
          <div className="mt-3 flex flex-col gap-1.5">
            {rows.map((row) => (
              <div
                key={`${row.provider}:${row.model}`}
                className="flex items-center gap-2 rounded-md bg-surface-high px-2.5 py-1.5"
              >
                <span className="flex min-w-0 flex-1 flex-col">
                  <span data-type="label-s" className="truncate text-on-surface" style={fvs(500)}>
                    {row.model || row.provider}
                  </span>
                  <span data-type="caption" className="truncate text-on-surface-low">
                    {row.provider} · {occupantDetail(row)}
                  </span>
                </span>
                <Button
                  variant="tonal"
                  size="xs"
                  loading={busy === row.provider}
                  onClick={() => unload(row.provider)}
                  ariaLabel={`Unload ${row.model || row.provider}`}
                  title="Free the memory this provider holds"
                >
                  Unload
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>
    </Section>
  )
}

/** Prompt caching — the one inference-behaviour switch that
 *  belongs beside the model bindings, since whether it does anything depends entirely on
 *  which provider a use-case is bound to. Default ON: caching is semantically transparent
 *  (the model sees the same tokens either way) and a provider that doesn't support it is a
 *  no-op. Off is the diagnosis position — it stops the cache marker, and deliberately does
 *  NOT change the served prompt's ORDERING, which is an unconditional repair. */
function PromptCacheSection() {
  const { data, error: loadErr, refresh } = useQuery('settings:models-prompt-cache', () =>
    api.personalclawConfig().then((c) => (c.agent ?? {}) as Record<string, unknown>),
    { persist: true },
  )
  const [cfg, setCfg] = useState<Record<string, unknown> | null>(null)
  useEffect(() => { if (data) setCfg(data) }, [data])

  // A failed read must not render the switch at its fallback — an unloaded `false` would
  // be indistinguishable from "you turned caching off".
  if (!data && loadErr) return <LoadError what="prompt-cache setting" error={loadErr} onRetry={refresh} />
  if (!data || !cfg) return <FormSkeleton sections={1} what="prompt-cache setting" />

  // Optimistic single-field PATCH; a rejected save rolls back and surfaces the error.
  const patch = (key: string, value: boolean, onSaved: () => void) => {
    const prev = cfg[key]
    setCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`agent.${key}`, value).then(onSaved).catch((e) => {
      setCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save prompt caching: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  return (
    <Section title="Prompt caching" hint="Reuse the stable part of the prompt across turns on providers that support it.">
      <RowGroup>
        <ToggleRow label="Prompt caching" cfg={cfg} field="prompt_cache_enabled" patch={patch}
          hint="Ask providers that support it to cache the stable prompt prefix, cutting cost and latency on multi-turn work. Providers without cache support are unaffected. Turn it off to rule caching out when debugging a provider — what the model is shown, and in what order, is identical either way." />
      </RowGroup>
    </Section>
  )
}

/** Breaker-state dot for one chain entry's provider: closed→green, half_open→amber,
 *  open→red. No health row (provider never called) renders nothing — absence of data must not
 *  read as "healthy". The entry's status pill (`chainEntryStatus`) says the same in words.
 *
 *  `half_open` said "recovering — next call probes it". It is not recovering: it is reached only
 *  from `open`, once the wait has passed with nothing calling it, and only a call that succeeds
 *  closes it — so an instance that was still down read as coming back. */
function HealthDot({ provider, health }: { provider: string; health: ProviderHealth[] }) {
  const h = health.find((p) => p.name === provider)
  if (!h) return null
  const color = h.breaker_state === 'open' ? 'var(--color-danger)'
    : h.breaker_state === 'half_open' ? 'var(--color-warning)' : 'var(--color-ok)'
  const failed = `its last ${h.consecutive_failures} call${h.consecutive_failures === 1 ? '' : 's'} failed`
  const label = h.breaker_state === 'open'
    ? `${provider}: failing — ${failed}; chain entries on it are skipped until it answers again`
    : h.breaker_state === 'half_open' ? `${provider}: failing — ${failed}; the next call tests it again` : `${provider}: healthy`
  // role="img": the dot is the ONLY carrier of the breaker state (no text equivalent
  // beside it), and on a role-less span `aria-label` is a PROHIBITED attribute — the name
  // is discarded, so a screen-reader user gets a coloured dot and nothing else.
  return <span role="img" className="size-2 shrink-0 rounded-pill" style={{ background: color }} title={label} aria-label={label} />
}

/** A use case with nothing bound, for one the read did not list: its empty chain has no revision,
 *  so a save from it names none and is refused (`428`) rather than taken as a blind overwrite. */
const NO_CHAIN: Revisioned<string[]> = { value: [], revision: '' }
/** No provider listings: a read cached before the page kept them, or none at all. Nothing is then
 *  claimed about any chain entry beyond its breaker. */
const NO_LISTINGS: Record<string, ProviderListing> = {}

/** The key a row finds its model's download by. */
const downloadKey = (provider: string, model: string) => `${provider}\u0000${model}`
const NO_DOWNLOADS: ReadonlyMap<string, DownloadJob> = new Map()

/** The job each model's row re-attaches to: the one running, else the newest. A model can hold
 *  an earlier job that ended beside the one running now (at most one runs: the gateway hands a
 *  second request for it the same job), and the list is in the order the jobs started. */
export function latestDownloads(all: DownloadJob[]): ReadonlyMap<string, DownloadJob> {
  const out = new Map<string, DownloadJob>()
  for (const job of all) {
    const key = downloadKey(job.provider, job.model)
    const held = out.get(key)
    if (!held || !isLiveDownload(held)) out.set(key, job)
  }
  return out
}

/** The confirm before an Embedding save. A change re-indexes; a clear re-indexes nothing — the
 *  save starts the re-index only once a model is bound (`onSaved`) — so it says what clearing does
 *  instead. It used to ask "Change & re-index" for a clear as well, and nothing re-indexed. */
const EMBEDDING_CHANGE = {
  title: 'Change the embedding model?',
  body: 'The knowledge and memories this model has not embedded are re-embedded with it. Until then, search finds them by keyword: an embedding from another model cannot be compared with this model\'s.\n\nRe-indexing runs in the background and may take a while for large stores.',
  confirmLabel: 'Change & re-index',
}
const EMBEDDING_CLEAR = {
  title: 'Stop using an embedding model?',
  body: 'Memory and knowledge search will match by keyword instead of by meaning until you choose an embedding model again. The embeddings already stored are kept, and choosing a model re-indexes them.',
  confirmLabel: 'Clear',
}

/** Why a chain control is unavailable while a refused change waits in the notice above it. */

// Each chain edit as an OPERATION, so a refused save re-applies onto the chain as stored now —
// the same edit, found by model rather than by position, wherever another tab moved it.
/** Add `ref` at the end, unless it is already bound. */
const appendRef = (ref: string): Rebase<string[]> => (theirs) => (theirs.includes(ref) ? theirs : [...theirs, ref])
/** Unbind `ref`. Already gone elsewhere is the same outcome. */
const withoutRef = (ref: string): Rebase<string[]> => (theirs) => theirs.filter((m) => m !== ref)
/** Make `ref` the default, keeping every other bound model after it. */
const asDefault = (ref: string): Rebase<string[]> => (theirs) => [ref, ...theirs.filter((m) => m !== ref)]
/** Move `ref` one place earlier (`-1`) or later (`1`) — `null` when it is no longer bound, or there
 *  is no place to move it to, so the notice says the change cannot be re-applied on its own. */
const moveRef = (ref: string, dir: -1 | 1): Rebase<string[]> => (theirs) => {
  const i = theirs.indexOf(ref)
  const j = i + dir
  if (i < 0 || j < 0 || j >= theirs.length) return null
  const next = [...theirs]
  ;[next[i], next[j]] = [next[j], next[i]]
  return next
}

/** One model in a use case's picker: its toggle and chips, and what it is doing right here — the
 *  Test of a downloaded local model, the Download of a chosen one this machine does not have, and
 *  the Repair of one whose weights are incomplete.
 *
 *  🔑 A REPAIR SHOWS ITS PROGRESS WHERE IT WAS PRESSED. It used to post the download and re-read
 *  the page, so the row looked idle while the weights came down again: no progress, no Cancel, and
 *  the job's warning (the free space could not be checked, #3715) drawn only on Settings →
 *  Providers, the one other place that job appears. It now runs through the row machine a chosen
 *  model's Download uses (`useRowDownload`), so it draws the same progress row, warning included,
 *  and a refusal lands under it. The page re-reads when the download lands and the chip can clear.
 *
 *  A column, not a bare button: the Repair, Test and Download affordances are buttons themselves
 *  and cannot nest inside the toggle. */
function ModelRow({ model: m, on, saving, held, localProviders, listed, providerDown, onToggle, onChanged, onDownloaded }: {
  model: AvailableModel; on: boolean
  /** Its provider did not answer when the page read its models (`listingFailure`), or null. */
  providerDown: { label: string; detail: string } | null
  /** The chain is being saved. */
  saving: boolean
  /** A refused save of the chain is waiting for the user (`StaleWriteNotice`). */
  held: boolean
  localProviders: ReadonlySet<string>
  /** The latest job the page's download list holds for this model (`latestDownloads`). */
  listed?: DownloadJob
  onToggle: () => void
  /** Re-read the page: a repair landed. */
  onChanged: () => void
  /** A chosen model's download landed. */
  onDownloaded: () => void
}) {
  // A Repair still running after a reload is found again, so its progress and how it ended show
  // here. The Repair takes the listed job only for a model on disk: a job running for one is a
  // Repair, even while its row does not read `truncated` (an unfinished fetch beside the weights
  // explains the shortfall), and one that ended counts only while the weights are still short. A
  // model not on disk has its job drawn by its Download (`InlineModelDownload`), and two trackers
  // would draw it twice.
  const repairJob = listed && m.downloaded === true && (isLiveDownload(listed) || m.integrity === 'truncated') ? listed : undefined
  const repair = useRowDownload(m, onChanged, repairJob)
  // A Repair that fails leaves the weights short, and the page may have been read while its fetch
  // ran, when nothing looked short: read it again, so the row says truncated and offers Repair
  // beside why the last one failed.
  const reread = useRef(onChanged)
  reread.current = onChanged
  useEffect(() => { if (repair.failed) reread.current() }, [repair.failed])
  // The status opens under the chip that was pressed, so a Repair pressed at the window's bottom
  // edge drew its progress below the fold (measured in the drive: the row at y=907 of a 900px
  // viewport). `block: 'nearest'`: already on screen, nothing moves; off it, the smallest scroll
  // shows it.
  const status = useRef<HTMLDivElement>(null)
  const showing = repair.running !== null || repair.failed !== ''
  useEffect(() => { if (showing) status.current?.scrollIntoView?.({ block: 'nearest' }) }, [showing])
  // A LOCAL model (carries a `downloaded` flag) that's bound but NOT
  // downloaded won't actually run — surface it so "configured" never
  // silently means "inert" (e.g. after deleting a bound model's weights).
  //
  // 🔴 Not when its provider did not answer. A bound model is missing from the list of an
  // instance that could not be listed, so it read "not downloaded … not on this machine yet" with
  // a Download button, while the model sat on an instance that was merely down. That row says the
  // instance is not answering instead, and offers nothing it could not do.
  const notDownloaded = m.downloaded === false && providerDown === null
  // …and one this machine can fetch gets its Download right here (`InlineModelDownload`).
  const downloadable = providerDown === null && isDownloadable(m, localProviders)
  // A local model carries a `downloaded` flag; a hosted/remote model does not. Only a
  // present LOCAL model can run a real-inference selftest here.
  const isLocal = m.downloaded !== undefined
  // Gated pre-warn (LMMV §5): the server set `token_ready:false` on a gated row when no
  // valid HF token is configured, so we warn BEFORE the user clicks Download. Absent =
  // the cascade couldn't answer → no nag.
  const needsToken = m.gated === true && m.token_ready === false
  return (
    <div className="flex flex-col rounded-md transition-colors hover:bg-surface-high"
      style={on ? { background: 'color-mix(in srgb, var(--color-primary) 12%, transparent)' } : undefined}>
      <div className="flex items-center gap-2.5 pr-3">
        {/* Natively disabled only while the chain saves. A held change is a state the user resolves
            in the notice above, so the toggle keeps its tab stop and says so, with the sentence
            every held-change lock in this card uses. */}
        <button type="button" onClick={held ? undefined : onToggle} disabled={saving}
          aria-busy={saving || undefined} aria-disabled={(held && !saving) || undefined}
          title={held ? HELD_CHANGE_REASON : undefined}
          className="flex min-w-0 flex-1 items-center gap-2.5 rounded-md px-3 py-2 text-left aria-disabled:opacity-40">
          <span className="grid size-4 shrink-0 place-items-center rounded border"
            style={on ? { background: 'var(--color-primary)', borderColor: 'var(--color-primary)' } : { borderColor: 'var(--color-outline-variant)' }}>
            {on && <Check size={10} strokeWidth={3} className="text-on-primary" />}
          </span>
          <span data-type="body-s" className="min-w-0 flex-1 truncate text-on-surface font-mono"
            title={modelLabel(m) !== m.name ? m.name : undefined}>{modelLabel(m)}</span>
        </button>
        <ModelChips model={m} onRepair={() => { void repair.begin() }} repairing={repair.starting || repair.running !== null} />
        {needsToken && (
          <StatusPill tone="warn" className="shrink-0 inline-flex items-center gap-1"
            role="img" aria-label="This gated model needs a valid HuggingFace token"
            title="This gated model needs a valid HuggingFace token — add one under “HuggingFace token” below before downloading.">
            <KeyRound size={9} /> needs token
          </StatusPill>
        )}
        {on && providerDown && (
          <StatusPill tone="danger" className="shrink-0 inline-flex items-center gap-xs" title={providerDown.detail}>
            <AlertTriangle size={9} aria-hidden /> {providerDown.label}
            <span className="sr-only">: {providerDown.detail}</span>
          </StatusPill>
        )}
        {on && notDownloaded && (
          <span data-type="caption" className="shrink-0 inline-flex items-center gap-1 rounded-pill px-1.5 py-0.5"
            style={{ background: 'color-mix(in srgb, var(--color-warning) 16%, transparent)', color: 'var(--color-warning)' }}
            title={downloadable
              ? 'Bound but not on this machine yet — download it below to use it.'
              : 'Bound but not downloaded — download it in Providers to activate.'}>
            <Download size={9} /> not downloaded
          </span>
        )}
        <span data-type="caption" className="shrink-0 rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low">{m.provider}</span>
      </div>
      {showing && (
        <div ref={status} data-testid="model-repair" className="flex flex-col gap-xs px-m pb-s">
          {repair.running && <BundledDownloadProgress offer={repair.offer} job={repair.running} onCancel={repair.stop} />}
          {repair.failed && <DownloadFailure text={repair.failed} />}
        </div>
      )}
      {isLocal && m.downloaded === true && <ModelTestButton provider={m.provider} model={m.id} />}
      {on && downloadable && <InlineModelDownload model={m} listed={listed} onDownloaded={onDownloaded} />}
    </div>
  )
}

function UseCaseRow({ useCase, chain, allModels, localProviders, downloads, health, listings, judgeRec, unavailable, onChanged }: {
  /** The use case's chain as the panel read it, with the revision of exactly that chain. */
  useCase: string; chain: Revisioned<string[]>; allModels: AvailableModel[]
  /** What each provider's models read said (`providerListings`), for each chain entry's status. */
  listings: Record<string, ProviderListing>
  /** This use case's providers that can't generate right now, each with the reason it gives. */
  unavailable: UnavailableProvider[]
  /** Providers that can download a model they do not have yet (see `isDownloadable`). */
  localProviders: ReadonlySet<string>
  /** Each model's latest download job, from the panel's one read of the list (`latestDownloads`). */
  downloads: ReadonlyMap<string, DownloadJob>
  health: ProviderHealth[]
  judgeRec?: JudgeBenchRecommendation; onChanged: () => void
}) {
  // No `open` flag here: `DisclosureCard` owns the disclosure state, which is the only thing this
  // component ever used it for.
  const [saving, setSaving] = useState(false)
  const [query, setQuery] = useState('')
  // The one embedding re-index, followed wherever it was started: this row's save, or the
  // gateway itself (at its start, or after a binding made elsewhere). Only the Embedding row
  // reads it.
  const { job: reindex, start: startReindex } = useEmbeddingReindex({ enabled: useCase === 'embedding' })
  // The bindings that name a model. One stored before the gateway refused `"provider:"` chose
  // none: it is not shown, and the next change here writes the chain without it.
  const activeModels = useMemo(() => chain.value.filter(namesModel), [chain.value])
  const meta = USE_CASE_META[useCase] ?? { label: useCase, description: '', chain: false, icon: Boxes }
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

  // 🔴 THE CHAIN IS SAVED WHOLE, over the revision this row read it at. Every edit here used to
  // send this row's copy with one change spliced in — so a panel opened before another tab,
  // onboarding or a provider's removal changed this use case put its old chain straight back.
  // A stale copy is now refused, and the edit — an operation on the chain, never the chain — is
  // re-applied on top of what is stored (`ui/StaleWriteNotice`).
  const guard = useStaleWriteGuard<string[]>({
    read: () => api.activeChain(useCase),
    write: (next, base) => api.setActiveModel(useCase, next, base),
    onSaved: (saved) => {
      onChanged()
      // The re-index the embedding confirm warned about, once a model is bound — on the first
      // save or on a re-applied one alike.
      if (useCase === 'embedding' && saved.length > 0) startReindex()
    },
    onDiscard: onChanged,
  })
  // While a refused change waits for the user, a second edit would be built from the same stale
  // copy — and replace the change the notice is holding.
  const conflicted = guard.conflict !== null

  const setActive = async (op: Rebase<string[]>) => {
    if (useCase === 'embedding') {
      const ok = await confirm((op(activeModels) ?? activeModels).length === 0 ? EMBEDDING_CLEAR : EMBEDDING_CHANGE)
      if (!ok) return
    }
    setSaving(true)
    try {
      // The embedding branch above warns this re-indexes ALL knowledge and memories. A silent
      // failure there is the worst case in this file: nothing changes, nothing is said, and the
      // re-index the user was warned about never starts — so `startReindex()` runs from the
      // guard's `onSaved`, only once a save has landed. A stale copy is not a failure: the notice
      // below keeps the change for the user to re-apply or drop.
      await reportingWrite('change the model', () => guard.apply(chain, (theirs) => op(theirs.filter(namesModel))))
    } finally { setSaving(false) }
  }
  const toggle = (ref: string) => {
    // Chain use-cases APPEND a newly-picked model to the end of the chain (the
    // user then reorders); picking an already-chained model removes it.
    const bound = activeModels.includes(ref)
    if (meta.chain) setActive(bound ? withoutRef(ref) : appendRef(ref))
    else setActive(bound ? withoutRef(ref) : () => [ref])
  }
  const move = (i: number, dir: -1 | 1) => {
    const ref = activeModels[i]
    if (ref === undefined || i + dir < 0 || i + dir >= activeModels.length) return
    setActive(moveRef(ref, dir))
  }

  return (
    <DisclosureCard icon={meta.icon} label={meta.label} active={activeModels.length > 0} count={capable.length}
      subtitle={activeModels.length > 0
        ? meta.chain && activeModels.length > 1
          ? `chain of ${activeModels.length}`
          : `${activeModels.length} active`
        : meta.fallback
          ? <span className="italic">uses your {meta.fallback} chain</span>
          : <span className="italic">none configured</span>}>
      {/* BODY ONLY. The card shell — wrapper, disclosure header, chevron, accent icon chip,
          label/subtitle stack, count pill, bordered body — was byte-identical to `SearchPanel`'s and
          now lives once in `ui/DisclosureCard`, along with the clipped-focus-ring fix both copies
          needed and the `aria-controls` neither of them had. */}
      <p data-type="body-s" className="text-on-surface-low">{meta.description}</p>
      <div data-type="caption" className="inline-flex w-fit items-center gap-1.5 rounded-md px-2 py-1"
        style={meta.chain ? accentChip : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}>
        <span className="size-1.5 rounded-pill" style={{ background: meta.chain ? 'var(--color-primary)' : 'var(--color-on-surface-low)' }} />
        {meta.chain ? 'Fallback chain — first is the default, later entries take over on failure' : 'Single-select — one model per use case'}
      </div>
      <StaleWriteNotice guard={guard} what={`The models for ${meta.label}`} />

      {/* The "one user action": the judge benchmark measured this axis and named the
          cheapest ADEQUATE tier, so binding it is a click rather than a hand-copy from the
          Learning page's table. It sets the recommended ref as the DEFAULT — position 0 of a
          chain, or the single selection — because "rebind the judge" means change what
          resolves, not append a fallback that never runs.
          The harness still only recommends: no code binds this without the click. */}
      {judgeRec && judgeRec.model_ref && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg bg-surface px-2.5 py-2">
          <Gavel size={13} className="shrink-0 text-on-surface-low" />
          <span data-type="caption" className="text-on-surface-low">
            Judge benchmark: cheapest adequate tier is <span className="text-on-surface">{judgeRec.tier}</span>
            {' '}at {judgeRec.samples} sample{judgeRec.samples === 1 ? '' : 's'} — <span className="text-on-surface">{judgeRec.model_ref}</span>
          </span>
          {activeModels[0] === judgeRec.model_ref ? (
            <span data-type="caption" className="inline-flex items-center gap-1 text-on-surface-low">
              <Check size={12} /> already the default
            </span>
          ) : (
            <Button size="sm" variant="tonal" disabled={saving || conflicted}
              disabledReason={conflicted ? HELD_CHANGE_REASON : BUSY_REASON}
              onClick={() => setActive(asDefault(judgeRec.model_ref))}>
              Bind as default
            </Button>
          )}
        </div>
      )}

      {/* The ordered chain editor: position 0 is the default; reorder with the
          arrow buttons (keyboard-accessible), remove with ×. Each entry carries
          its provider's breaker-health dot. */}
      {meta.chain && activeModels.length > 0 && (
        <div className="flex flex-col gap-1 rounded-lg bg-surface p-2">
          {activeModels.map((ref, i) => {
            const { provider, model: id } = splitModelRef(ref)
            // The model's name where the catalog has one (`SmolLM2-135M-Instruct`), not its file id.
            const known = capable.find((m) => m.provider === provider && m.id === id)
            const named = known ? modelLabel(known) : id
            const status = chainEntryStatus(ref, listings[provider], health.find((h) => h.name === provider), known)
            return (
              <div key={ref} className="flex items-center gap-2 rounded-md bg-surface-container px-2.5 py-1.5">
                <span data-type="caption" className="w-16 shrink-0 text-on-surface-low uppercase tracking-wide">
                  {i === 0 ? 'default' : `fallback ${i}`}
                </span>
                <HealthDot provider={provider} health={health} />
                <span data-type="body-s" className="min-w-0 flex-1 truncate font-mono text-on-surface" title={named !== id ? id : undefined}>{named}</span>
                {/* Said in words beside the entry, not only in a dot's tooltip: an instance that is
                    down, or a model gone from it, is what this chain most needs to tell you. */}
                {status && (
                  <StatusPill tone={status.tone} groundedOn="var(--color-surface-container)"
                    className="shrink-0 inline-flex items-center gap-xs" title={status.detail}>
                    <AlertTriangle size={9} aria-hidden /> {status.label}
                    <span className="sr-only">: {status.detail}</span>
                  </StatusPill>
                )}
                {provider && <span data-type="caption" className="shrink-0 rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low">{provider}</span>}
                {/* Two different claims, so two different props. The BOUNDARY (`i === 0`,
                    last row) is genuine unavailability and keeps `disabled` + the reason that
                    names it. `saving` is the chain PUT in flight, so it is `loading`: OR-ing
                    it into `disabled` made all three arrows announce "unavailable" for the
                    length of a request they had just started. */}
                <IconButton icon={ArrowUp} label={`Move ${id} up`} size={24} iconSize={13}
                  disabled={i === 0 || conflicted} loading={saving} onClick={() => move(i, -1)}
                  disabledReason={conflicted ? HELD_CHANGE_REASON : i === 0 ? 'Already the default' : undefined} />
                <IconButton icon={ArrowDown} label={`Move ${id} down`} size={24} iconSize={13}
                  disabled={i === activeModels.length - 1 || conflicted} loading={saving} onClick={() => move(i, 1)}
                  disabledReason={conflicted ? HELD_CHANGE_REASON : i === activeModels.length - 1 ? 'Already the last fallback' : undefined} />
                <IconButton icon={X} label={`Remove ${id} from chain`} size={24} iconSize={13}
                  disabled={conflicted} disabledReason={conflicted ? HELD_CHANGE_REASON : undefined}
                  loading={saving} onClick={() => setActive(withoutRef(ref))} />
              </div>
            )
          })}
        </div>
      )}

      {useCase === 'embedding' && reindex && (
        <div data-type="caption" className="rounded-md px-3 py-2"
          style={{ background: reindex.status === 'error' ? ERROR_SURFACE_PAINT.background : 'var(--color-surface-high)' }}>
          {/* "Re-index not started" is only true when the POST itself failed — that path sets
              `id: ''`. A job with an id DID start (e.g. its progress feed dropped), so its message
              speaks for itself rather than carrying a prefix that contradicts it. */}
          {reindex.status === 'error' ? (
            <span style={{ color: ERROR_SURFACE_PAINT.color }}>{reindex.id ? reindex.error : `Re-index not started: ${reindex.error}`}</span>
          ) : reindex.status === 'done' ? (
            <span style={{ color: 'var(--color-ok)' }}>Re-indexed {reindex.knowledge} knowledge + {reindex.chunks ?? 0} passage + {reindex.memory} memory embeddings.</span>
          ) : (
            <div className="flex flex-col gap-1.5">
              <span className="text-on-surface-var">Re-indexing embeddings — {reindex.phase}{reindex.total > 0 ? ` (${reindex.done}/${reindex.total})` : '…'}</span>
              {/* 🔴 A bar must never invent a fill it cannot compute. While a phase has no
                  total yet (`total === 0`) this parked the bar at a hardcoded 40% — a
                  fabricated claim that the job is nearly half done, and one that then
                  JUMPED backwards the moment a real total arrived. Determinate only when
                  there is a denominator; otherwise the indeterminate wave, which is the
                  one primitive that already expresses "running, extent unknown" (Meter
                  deliberately has no indeterminate mode). The wave is aria-hidden on
                  purpose — the sentence above it already reports the phase, so a second
                  valueless progressbar would only repeat it. */}
              {reindex.total > 0 ? (
                <div className="h-1.5 w-full overflow-hidden rounded-pill bg-surface-container">
                  <div className="h-full rounded-pill bg-primary transition-[width]" style={{ width: `${Math.min(100, Math.round((reindex.done / reindex.total) * 100))}%` }} />
                </div>
              ) : (
                <WavyProgress width={140} />
              )}
            </div>
          )}
        </div>
      )}

      {unavailable.length > 0 && (
        <ul className="flex flex-col gap-xs" aria-label={`${meta.label} providers that can’t be used right now`}>
          {unavailable.map((p) => (
            <li key={p.name} data-type="caption" className="flex items-start gap-1.5 rounded-md bg-surface px-2.5 py-1.5 text-on-surface-low">
              <AlertTriangle size={12} className="mt-0.5 shrink-0" style={{ color: 'var(--color-warning)' }} aria-hidden />
              <span className="min-w-0 break-words"><span className="fw-500 text-on-surface">{p.name}:</span> {p.error}</span>
            </li>
          ))}
        </ul>
      )}

      {/* With a provider above that says why it can't be used, "add a backend first" would send
          the user to add one they already have. */}
      {capable.length === 0 ? (unavailable.length === 0 && (
        <div data-type="body-s" className="rounded-lg border border-dashed border-outline-variant/50 px-3 py-3 text-on-surface-low italic">
          {meta.fallback ? (
            <>Already uses your <span className="text-on-surface not-italic fw-500">{meta.fallback}</span> chain by default — no dedicated {meta.label} model is required. Add a backend with a chat-capable model to override.</>
          ) : (
            <>No models with {meta.label} capability. Add a backend with compatible models first.</>
          )}
        </div>
      )) : (
        <>
          {capable.length > 8 && (
            <>
              <SearchField value={query} onChange={setQuery} size="md"
                placeholder={`Search ${capable.length} models — name or provider`}
                ariaLabel="Search models" />
              {/* `filtered` is `matched` re-ordered (active models floated to the top), so it is
                  the array the body maps and the one the count must come from. */}
              <ResultAnnouncement count={filtered.length} noun="models" active={!!query.trim()} />
            </>
          )}
          {filtered.length === 0 ? (
            <div data-type="body-s" className="rounded-md border border-dashed border-outline-variant/50 px-3 py-3 text-on-surface-low italic">
              No models match “{query}”.
            </div>
          ) : (
            <div className="-m-1 flex max-h-[300px] flex-col gap-0.5 overflow-y-auto p-1" style={{ opacity: saving ? 0.6 : 1 }}>
              {filtered.map((m) => {
                const ref = `${m.provider}:${m.id}`
                // A chosen model's download finishing is the binding taking effect — and for
                // Embedding, the re-index the choice asked for, which could not start while the
                // model was missing.
                const downloaded = () => { onChanged(); if (useCase === 'embedding') startReindex() }
                return (
                  <ModelRow key={ref} model={m} on={activeModels.includes(ref)}
                    providerDown={listingFailure(m.provider, listings[m.provider])}
                    saving={saving} held={conflicted} localProviders={localProviders}
                    listed={downloads.get(downloadKey(m.provider, m.id))}
                    onToggle={() => toggle(ref)} onChanged={onChanged} onDownloaded={downloaded} />
                )
              })}
            </div>
          )}
        </>
      )}
    </DisclosureCard>
  )
}
