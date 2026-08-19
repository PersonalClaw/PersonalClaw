import { useEffect, useMemo, useState } from 'react'
import { ResultAnnouncement } from '../../ui/ListControls'
import {
  ChevronRight, Check, MessageSquare, Boxes, Mic, Volume2, Eye, ImagePlus,
  Ear, Music, ScanEye, Clapperboard, Users, Download, Code2, BrainCircuit,
  Moon, Network, RefreshCcw, ArrowUp, ArrowDown, X, AlertTriangle, Wrench,
  Trash2, Gavel, type LucideIcon,
} from 'lucide-react'
import { api, type AvailableModel, type JudgeBenchRecommendation, type ProviderHealth } from '../../lib/api'
import { humanBytes } from '../../lib/chunkedUpload'
import {
  occupantDetail, pressureDetail, pressureTone, reclaimableCount, sortOccupants,
} from '../../lib/residency'
import { IconButton } from '../../ui/IconButton'
import { Button } from '../../ui/Button'
import { Meter } from '../../ui/Meter'
import { SearchField } from '../../ui/SearchField'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { confirm } from '../../ui/dialog'
import { PanelHeader, Section, ToggleRow } from './settingsUI'
import { notify } from '../../app/appSdk'
import { FormSkeleton, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { fvs } from '../../design/fontWeight'
import { accentChip } from '../../design/accent'

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
  orchestration: { label: 'Orchestration', group: 'Chat routing', description: 'Supervising turns and subagents spawned without an explicit model.', chain: true, icon: Network, fallback: 'Chat' },
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
 *  Pure + exported for unit testing. */
export function capableModels(useCase: string, allModels: AvailableModel[], activeModels: string[]): AvailableModel[] {
  const capability = CHAT_SUBCATEGORIES.has(useCase) ? 'chat' : useCase
  const seen = new Set<string>()
  const out: AvailableModel[] = []
  for (const m of allModels) {
    if (!m.capabilities.includes(capability)) continue
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
        <span className="rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low text-[0.6875rem] uppercase tracking-wide"
          title="Deprecated — still bindable, but a newer model is preferred.">deprecated</span>
      )}
      {model.status === 'sunset' && (
        <span className="rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low text-[0.6875rem] uppercase tracking-wide"
          title="Sunset — hidden from new bindings; an existing binding keeps working.">sunset</span>
      )}
      {model.non_commercial && (
        <span className="inline-flex items-center gap-1 rounded-pill px-1.5 py-0.5 text-[0.6875rem]"
          style={{ background: 'color-mix(in srgb, var(--color-warning) 16%, transparent)', color: 'var(--color-warning)' }}
          title={`Non-commercial license${model.license ? ` (${model.license})` : ''} — for personal/research use only.`}>
          <AlertTriangle size={9} /> non-commercial
        </span>
      )}
      {model.integrity === 'truncated' && (
        <>
          <span className="inline-flex items-center gap-1 rounded-pill px-1.5 py-0.5 text-[0.6875rem]"
            style={{ background: 'color-mix(in srgb, var(--color-danger) 16%, transparent)', color: 'var(--color-danger)' }}
            title="Downloaded weights are incomplete — this model won't load. Repair to re-download.">
            truncated
          </span>
          <button type="button" onClick={onRepair} disabled={repairing}
            className="inline-flex items-center gap-1 rounded-pill px-1.5 py-0.5 text-[0.6875rem] transition-colors hover:bg-surface-high"
            style={{ background: 'var(--color-surface-high)', color: 'var(--color-on-surface)' }}
            title="Re-download this model's weights.">
            <Wrench size={9} /> {repairing ? 'repairing…' : 'Repair'}
          </button>
        </>
      )}
    </span>
  )
}

/** "Reclaim N GB" — surfaces the partial-download leftovers (cancelled/crashed fetches)
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
      title: `Reclaim ${humanBytes(totalBytes)}?`,
      body: 'Deletes partial-download leftovers (.part / .tmp / .incomplete files) from cancelled or interrupted fetches. Fully downloaded models are untouched.',
      confirmLabel: 'Reclaim',
    })
    if (!ok) return
    setBusy(true)
    try {
      await api.modelDownloadCleanup()
      await refreshCandidates()
      onReclaimed()
    } finally { setBusy(false) }
  }

  return (
    <Button variant="tonal" size="xs" loading={busy} onClick={reclaim}
      title="Delete partial-download leftovers from cancelled or interrupted fetches.">
      <Trash2 size={13} /> Reclaim {humanBytes(totalBytes)}
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
  const { data, refresh } = useCachedData('settings:models', async () => {
    const [rows, active] = await Promise.all([
      api.modelsAvailable().catch(() => [] as { name: string; models?: AvailableModel[] }[]),
      api.modelsActive().catch(() => ({} as Record<string, string[]>)),
    ])
    return { allModels: rows.flatMap((r) => r.models ?? []), active }
  }, { persist: true })
  // Per-provider breaker health for the chain-entry dots — refreshed on panel
  // mount (persist:false so a broken provider isn't shown green from cache).
  const { data: health } = useCachedData('settings:models-health', () =>
    api.modelsHealth().then((h) => h.providers).catch(() => [] as ProviderHealth[]), { persist: false })
  // The judge benchmark's tier recommendations (ES-4), so rebinding a judge to the cheapest
  // adequate tier is ONE action here rather than a hand-translation from a table on another
  // page. A failure or a 404 collapses to "no recommendation, no chip" — which is an honest
  // absence rather than a swallowed error, because the Learning page's Judge tiers panel is
  // the surface that owns reporting WHY there is none.
  const { data: judgeRecs } = useCachedData('settings:judge-bench-recs', () =>
    api.judgeBench().then((v) => v.recommendations).catch(() => [] as JudgeBenchRecommendation[]),
    { persist: false })
  const allModels = data?.allModels
  const active = data?.active ?? {}

  // A binding mutation invalidates the cached catalog so the next read revalidates
  // against the changed state instead of a stale snapshot.
  const reloadActive = () => { invalidateCache('settings:models'); refresh() }

  if (!allModels) return <ListSkeleton rows={6} />

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
          <div className="mb-3 rounded-lg border border-dashed border-outline-variant/50 bg-surface-container px-4 py-5 text-center text-on-surface-low text-[0.8125rem]">
            No models discovered. Add a backend in <span className="text-on-surface">Providers</span> and test its connection.
          </div>
        )}
        {USE_CASE_ORDER.map((uc, i) => {
          const meta = USE_CASE_META[uc]
          const prevGroup = i > 0 ? USE_CASE_META[USE_CASE_ORDER[i - 1]]?.group : undefined
          const showGroupHeader = meta?.group && meta.group !== prevGroup
          return (
            <div key={uc}>
              {showGroupHeader && <div className="mb-1.5 mt-3 px-1 text-on-surface-low text-[0.75rem] uppercase tracking-wide">{meta.group}</div>}
              <UseCaseRow useCase={uc} activeModels={active[uc] ?? []} allModels={allModels} health={health ?? []} judgeRec={(judgeRecs ?? []).find((r) => r.verdict === 'recommended' && r.use_case === uc)} onChanged={reloadActive} />
            </div>
          )
        })}
      </Section>
      <LoadedModelsSection />
      <PromptCacheSection />
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
  const { data, error: loadErr, refresh } = useCachedData('models:loaded', () =>
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
      invalidateCache('models:loaded')
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
          <ul className="mt-3 flex flex-col gap-0.5 text-on-surface-low text-[0.8125rem]">
            {notReady.map((p) => (
              <li key={p.provider}>
                {p.display_name}:{' '}
                {p.state === 'loading' ? 'loading a model now' : 'unavailable on this machine'}
              </li>
            ))}
          </ul>
        )}
        {rows.length === 0 ? (
          <div className="mt-3 text-on-surface-low text-[0.8125rem]">
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
                  <span className="truncate text-on-surface text-[0.8125rem]" style={fvs(500)}>
                    {row.model || row.provider}
                  </span>
                  <span className="truncate text-on-surface-low text-[0.75rem]">
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

/** Prompt caching (PROMPT-CACHE-SUBSTRATE §C6) — the one inference-behaviour switch that
 *  belongs beside the model bindings, since whether it does anything depends entirely on
 *  which provider a use-case is bound to. Default ON: caching is semantically transparent
 *  (the model sees the same tokens either way) and a provider that doesn't support it is a
 *  no-op. Off is the diagnosis position — it stops the cache marker, and deliberately does
 *  NOT change the served prompt's ORDERING, which is an unconditional repair. */
function PromptCacheSection() {
  const { data, error: loadErr, refresh } = useCachedData('settings:models-prompt-cache', () =>
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
      <div className="rounded-lg bg-surface-container px-4 py-1">
        <ToggleRow label="Prompt caching" cfg={cfg} field="prompt_cache_enabled" patch={patch}
          hint="Ask providers that support it to cache the stable prompt prefix, cutting cost and latency on multi-turn work. Providers without cache support are unaffected. Turn it off to rule caching out when debugging a provider — what the model is shown, and in what order, is identical either way." />
      </div>
    </Section>
  )
}

/** Breaker-state dot for one chain entry's provider: closed→green, half_open→amber,
 *  open→red (+ retry hint). No health row (provider never called) renders nothing —
 *  absence of data must not read as "healthy". */
function HealthDot({ provider, health }: { provider: string; health: ProviderHealth[] }) {
  const h = health.find((p) => p.name === provider)
  if (!h) return null
  const color = h.breaker_state === 'open' ? 'var(--color-danger)'
    : h.breaker_state === 'half_open' ? 'var(--color-warning)' : 'var(--color-ok)'
  const label = h.breaker_state === 'open'
    ? `${provider}: circuit open (${h.consecutive_failures} consecutive failures) — chain entries on this provider are skipped until it recovers`
    : h.breaker_state === 'half_open' ? `${provider}: recovering — next call probes it` : `${provider}: healthy`
  // role="img": the dot is the ONLY carrier of the breaker state (no text equivalent
  // beside it), and on a role-less span `aria-label` is a PROHIBITED attribute — the name
  // is discarded, so a screen-reader user gets a coloured dot and nothing else.
  return <span role="img" className="size-2 shrink-0 rounded-pill" style={{ background: color }} title={label} aria-label={label} />
}

function UseCaseRow({ useCase, activeModels, allModels, health, judgeRec, onChanged }: {
  useCase: string; activeModels: string[]; allModels: AvailableModel[]; health: ProviderHealth[]
  judgeRec?: JudgeBenchRecommendation; onChanged: () => void
}) {
  const [open, setOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [query, setQuery] = useState('')
  const [reindex, setReindex] = useState<import('../../lib/api').ReindexJob | null>(null)
  // The `provider:id` ref currently being re-downloaded (truncated → Repair), so its
  // button shows a pending state without blocking the rest of the list.
  const [repairing, setRepairing] = useState<string | null>(null)
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
      // A stream failure is NOT a re-index failure: the job keeps running server-side, we just lost
      // the progress feed. Closing silently froze this panel on its last percentage forever, so a
      // user could not tell "still working" from "we stopped hearing about it". Recorded on the job
      // itself, which this panel already renders — and the copy says what is actually known.
      es.onerror = () => {
        es.close()
        setReindex((r) => (r && r.status === 'running'
          ? { ...r, status: 'error', error: 'Lost the progress feed — the re-index may still be running in the background. Reload to check.' }
          : r))
      }
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
    // Chain use-cases APPEND a newly-picked model to the end of the chain (the
    // user then reorders); picking an already-chained model removes it.
    if (meta.chain) setActive(activeModels.includes(ref) ? activeModels.filter((m) => m !== ref) : [...activeModels, ref])
    else setActive(activeModels.includes(ref) ? [] : [ref])
  }
  const move = (i: number, dir: -1 | 1) => {
    const j = i + dir
    if (j < 0 || j >= activeModels.length) return
    const next = [...activeModels]
    ;[next[i], next[j]] = [next[j], next[i]]
    setActive(next)
  }
  // Repair a truncated model: re-run the same download the "not downloaded" path uses
  // (the runner overwrites the incomplete weights), then revalidate so the chip clears.
  const repair = async (m: AvailableModel) => {
    const ref = `${m.provider}:${m.id}`
    setRepairing(ref)
    try {
      await api.startModelDownload(m.provider, m.id)
      onChanged()
    } catch (e) {
      // No catch at all meant an unhandled rejection: the spinner stopped (the `finally` below) and
      // NOTHING else happened, so a failed repair was indistinguishable from a click that did not
      // register. Two siblings in this same file already report through `notify`.
      notify(`Couldn't re-download ${m.id}: ${String((e as Error)?.message || e)}`, 'error')
    } finally { setRepairing(null) }
  }

  return (
    <div className="mb-2 overflow-hidden rounded-lg bg-surface-container">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-high">
        <ChevronRight size={14} className="shrink-0 text-on-surface-low transition-transform" style={{ transform: open ? 'rotate(90deg)' : 'none', color: open ? 'var(--color-primary)' : undefined }} />
        <span className="grid size-7 shrink-0 place-items-center rounded-md"
          style={activeModels.length > 0
            ? accentChip
            : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}>
          <meta.icon size={14} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-on-surface text-[0.8125rem]" style={fvs(500)}>{meta.label}</div>
          <div className="mt-0.5 text-on-surface-low text-[0.75rem]">
            {activeModels.length > 0
              ? meta.chain && activeModels.length > 1
                ? `chain of ${activeModels.length}`
                : `${activeModels.length} active`
              : meta.fallback
                ? <span className="italic">uses your {meta.fallback} chain</span>
                : <span className="italic">none configured</span>}
          </div>
        </div>
        {capable.length > 0 && <span className="shrink-0 rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-low text-[0.75rem] tabular-nums">{capable.length} available</span>}
      </button>

      {open && (
        <div className="flex flex-col gap-3 border-t border-outline-variant/30 px-4 pb-4 pt-3">
          <p className="text-on-surface-low text-[0.8125rem]">{meta.description}</p>
          <div className="inline-flex w-fit items-center gap-1.5 rounded-md px-2 py-1 text-[0.75rem]"
            style={meta.chain ? accentChip : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}>
            <span className="size-1.5 rounded-pill" style={{ background: meta.chain ? 'var(--color-primary)' : 'var(--color-on-surface-low)' }} />
            {meta.chain ? 'Fallback chain — first is the default, later entries take over on failure' : 'Single-select — one model per use case'}
          </div>

          {/* ES-4's "one user action": the judge benchmark measured this axis and named the
              cheapest ADEQUATE tier, so binding it is a click rather than a hand-copy from the
              Learning page's table. It sets the recommended ref as the DEFAULT — position 0 of a
              chain, or the single selection — because "rebind the judge" means change what
              resolves, not append a fallback that never runs.
              The harness still only recommends: no code binds this without the click. */}
          {judgeRec && judgeRec.model_ref && (
            <div className="flex flex-wrap items-center gap-2 rounded-lg bg-surface px-2.5 py-2">
              <Gavel size={13} className="shrink-0 text-on-surface-low" />
              <span className="text-on-surface-low text-[0.75rem]">
                Judge benchmark: cheapest adequate tier is <span className="text-on-surface">{judgeRec.tier}</span>
                {' '}at {judgeRec.samples} sample{judgeRec.samples === 1 ? '' : 's'} — <span className="text-on-surface">{judgeRec.model_ref}</span>
              </span>
              {activeModels[0] === judgeRec.model_ref ? (
                <span className="inline-flex items-center gap-1 text-on-surface-low text-[0.75rem]">
                  <Check size={12} /> already the default
                </span>
              ) : (
                <Button size="sm" variant="tonal" disabled={saving}
                  onClick={() => setActive([judgeRec.model_ref, ...activeModels.filter((m) => m !== judgeRec.model_ref)])}>
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
                const sep = ref.indexOf(':')
                const provider = sep >= 0 ? ref.slice(0, sep) : ''
                const id = sep >= 0 ? ref.slice(sep + 1) : ref
                return (
                  <div key={ref} className="flex items-center gap-2 rounded-md bg-surface-container px-2.5 py-1.5">
                    <span className="w-16 shrink-0 text-on-surface-low text-[0.6875rem] uppercase tracking-wide">
                      {i === 0 ? 'default' : `fallback ${i}`}
                    </span>
                    <HealthDot provider={provider} health={health} />
                    <span className="min-w-0 flex-1 truncate font-mono text-on-surface text-[0.8125rem]">{id}</span>
                    {provider && <span className="shrink-0 rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low text-[0.75rem]">{provider}</span>}
                    {/* The reason names the BOUNDARY branch only: while `saving` the gate is
                        transient and the row's own save state carries it, so a reason there
                        would explain the wrong cause. */}
                    <IconButton icon={ArrowUp} label={`Move ${id} up`} size={24} iconSize={13}
                      disabled={saving || i === 0} onClick={() => move(i, -1)}
                      disabledReason={i === 0 ? 'Already the default' : undefined} />
                    <IconButton icon={ArrowDown} label={`Move ${id} down`} size={24} iconSize={13}
                      disabled={saving || i === activeModels.length - 1} onClick={() => move(i, 1)}
                      disabledReason={i === activeModels.length - 1 ? 'Already the last fallback' : undefined} />
                    <IconButton icon={X} label={`Remove ${id} from chain`} size={24} iconSize={13}
                      disabled={saving} onClick={() => setActive(activeModels.filter((m) => m !== ref))} />
                  </div>
                )
              })}
            </div>
          )}

          {useCase === 'embedding' && reindex && (
            <div className="rounded-md px-3 py-2 text-[0.75rem]"
              style={{ background: reindex.status === 'error' ? 'color-mix(in srgb, var(--color-danger) 10%, transparent)' : 'var(--color-surface-high)' }}>
              {/* "Re-index not started" is only true when the POST itself failed — that path sets
                  `id: ''`. A job with an id DID start (e.g. its progress feed dropped), so its message
                  speaks for itself rather than carrying a prefix that contradicts it. */}
              {reindex.status === 'error' ? (
                <span style={{ color: 'var(--color-danger)' }}>{reindex.id ? reindex.error : `Re-index not started: ${reindex.error}`}</span>
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
            <div className="rounded-lg border border-dashed border-outline-variant/50 px-3 py-3 text-on-surface-low text-[0.8125rem] italic">
              {meta.fallback ? (
                <>Already uses your <span className="text-on-surface not-italic font-medium">{meta.fallback}</span> chain by default — no dedicated {meta.label} model is required. Add a backend with a chat-capable model to override.</>
              ) : (
                <>No models with {meta.label} capability. Add a backend with compatible models first.</>
              )}
            </div>
          ) : (
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
                <div className="rounded-md border border-dashed border-outline-variant/50 px-3 py-3 text-on-surface-low text-[0.8125rem] italic">
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
                  // A row, not a bare button: the Repair affordance is itself a button and
                  // can't nest inside one. The toggle lives on the flex-1 inner button; the
                  // chips + Repair sit beside it as siblings.
                  <div key={ref}
                    className="flex items-center gap-2.5 rounded-md pr-3 transition-colors hover:bg-surface-high"
                    style={on ? { background: 'color-mix(in srgb, var(--color-primary) 12%, transparent)' } : undefined}>
                    <button type="button" onClick={() => toggle(ref)} disabled={saving}
                      className="flex min-w-0 flex-1 items-center gap-2.5 rounded-md px-3 py-2 text-left">
                      <span className="grid size-4 shrink-0 place-items-center rounded border"
                        style={on ? { background: 'var(--color-primary)', borderColor: 'var(--color-primary)' } : { borderColor: 'var(--color-outline-variant)' }}>
                        {on && <Check size={10} strokeWidth={3} className="text-on-primary" />}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-on-surface text-[0.8125rem] font-mono">{m.name}</span>
                    </button>
                    <ModelChips model={m} onRepair={() => repair(m)} repairing={repairing === ref} />
                    {on && notDownloaded && (
                      <span className="shrink-0 inline-flex items-center gap-1 rounded-pill px-1.5 py-0.5 text-[0.75rem]"
                        style={{ background: 'color-mix(in srgb, var(--color-warning) 16%, transparent)', color: 'var(--color-warning)' }}
                        title="Bound but not downloaded — download it in Providers to activate.">
                        <Download size={9} /> not downloaded
                      </span>
                    )}
                    <span className="shrink-0 rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low text-[0.75rem]">{m.provider}</span>
                  </div>
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
