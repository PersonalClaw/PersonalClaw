import { useEffect, useRef, useState } from 'react'
import { CheckCircle2, AlertTriangle, ArrowRight, Plus, Trash2, RefreshCw, Check, X, Wand2 } from 'lucide-react'
import { api, type LexiconTerm, type LexiconCorrection } from '../../lib/api'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { PanelHeader, Section, Row, Field, Toggle, SavedToast } from './settingsUI'
import { FormSkeleton, ListSkeleton } from '../../ui/ListScaffold'

/** Speech & Transcription — provider/model-AGNOSTIC behavior for STT (transcription)
 *  + TTS (spoken replies), plus the Vocabulary & corrections section (the user-visible
 *  Lexicon, core LEX.6) that biases EVERY transcription — mic input AND knowledge
 *  audio/video ingestion. The MODEL for each use case is bound in Settings → Models
 *  (the single source of truth for every use-case→model binding); single-select use
 *  cases like stt/tts allow exactly one model there. This page owns only behavior:
 *  enable the feature, streaming (STT), speaking speed + voice persona (TTS), and the
 *  lexicon terms/learned corrections. It reads the bound model purely to show
 *  readiness and to know whether the hosted-voice persona applies — it never changes
 *  the binding. The legacy #/settings/vocabulary deep-link redirects here with
 *  ?section=vocabulary, which scrolls to the merged section. */
export function VoicePanel({ go, query }: { go?: (id: string) => void; query?: Record<string, string> }) {
  const [sttSettings, setSttSettings] = useState<Record<string, unknown> | null>(null)
  const [ttsSettings, setTtsSettings] = useState<Record<string, unknown> | null>(null)

  // Stale-while-revalidate + persist: paint instantly on revisit/reload from one
  // cached snapshot. `active` is read-only (the bound model is owned by Models);
  // the stt/tts settings are seeded into local state and mutated optimistically.
  const { data } = useCachedData('settings:voice', async () => {
    const [active, stt, tts] = await Promise.all([
      api.modelsActive().catch(() => ({} as Record<string, string[]>)),
      api.useCaseSettings('stt').catch(() => ({} as Record<string, unknown>)),
      api.useCaseSettings('tts').catch(() => ({} as Record<string, unknown>)),
    ])
    return { active, stt, tts }
  }, { persist: true })
  const active = data?.active ?? {}

  useEffect(() => {
    if (data) { setSttSettings(data.stt); setTtsSettings(data.tts) }
  }, [data])

  if (!data || !sttSettings || !ttsSettings) return <FormSkeleton sections={3} />

  return (
    <div>
      <PanelHeader title="Speech & Transcription" hint="How voice input, spoken replies, and transcription behave. The vocabulary below biases ALL transcription — microphone input and knowledge audio/video ingestion alike. The model for each use case is bound in Models — these are the provider-agnostic settings on top of it." />
      <UseCaseVoiceSection
        title="Speech-to-text" hint="Transcribe microphone input into the composer." useCase="stt"
        enableLabel="Enable speech-to-text" boundModel={(active['stt'] ?? [])[0] ?? ''}
        settings={sttSettings} setSettings={setSttSettings} go={go}
        extras={(s, save) => (
          <Row label="Streaming" hint="Transcribe incrementally as you speak (when supported).">
            <Toggle on={Boolean(s.streaming)} onChange={(v) => save({ streaming: v })} label="Streaming transcription" />
          </Row>
        )}
      />
      <UseCaseVoiceSection
        title="Text-to-speech" hint="Speak agent replies aloud." useCase="tts"
        enableLabel="Speak replies aloud" boundModel={(active['tts'] ?? [])[0] ?? ''}
        settings={ttsSettings} setSettings={setTtsSettings} go={go}
        extras={(s, save, boundModel) => {
          const speed = typeof s.speed === 'number' ? s.speed : 1.0
          const provider = boundModel.includes(':') ? boundModel.split(':', 1)[0] : ''
          const isRemoteVoice = !!provider && !PIPER_PROVIDERS.includes(provider)
          const speechVoice = typeof s.speech_voice === 'string' && s.speech_voice ? s.speech_voice : 'alloy'
          return (
            <>
              <Field label="Speaking speed" hint={`${speed.toFixed(2)}× — lower is faster.`}>
                <div className="flex items-center gap-3">
                  <span className="text-on-surface-low text-[0.7rem]">Fast</span>
                  <input type="range" min={0.6} max={1.6} step={0.05} value={speed}
                    onChange={(e) => setLocalSpeed(s, setTtsSettings, Number(e.target.value))}
                    onPointerUp={(e) => save({ speed: Number((e.target as HTMLInputElement).value) })}
                    // Keyboard adjustments never fire pointerup — persist those too.
                    onKeyUp={(e) => { if (RANGE_KEYS.has(e.key)) save({ speed: Number((e.target as HTMLInputElement).value) }) }}
                    className="flex-1 accent-[var(--color-primary)]" />
                  <span className="text-on-surface-low text-[0.7rem]">Slow</span>
                  <span className="w-10 text-right font-mono text-on-surface text-[0.72rem] tabular-nums">{speed.toFixed(2)}×</span>
                </div>
              </Field>
              {isRemoteVoice && (
                <Field label="Voice persona" hint="The hosted voice used by remote TTS models.">
                  <select value={speechVoice} onChange={(e) => save({ speech_voice: e.target.value })} className={selectCls}>
                    {SPEECH_VOICES.map((v) => <option key={v} value={v}>{v}</option>)}
                  </select>
                </Field>
              )}
            </>
          )
        }}
      />
      <VocabularySection scrollTo={query?.section === 'vocabulary'} />
    </div>
  )
}

// Provider names that drive the bundled local Piper backend (no hosted persona).
const PIPER_PROVIDERS = ['piper', 'piper-tts']
// Keys that move a range input's value (persist on keyup for keyboard users).
const RANGE_KEYS = new Set(['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown'])
// Built-in personas exposed by remote OpenAI-compatible TTS models.
const SPEECH_VOICES = ['alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer']

// live-update the speed value while dragging (persisted on pointer-up).
function setLocalSpeed(s: Record<string, unknown>, setter: (v: Record<string, unknown>) => void, speed: number) {
  setter({ ...s, speed })
}

function UseCaseVoiceSection({
  title, hint, useCase, enableLabel, boundModel, settings, setSettings, go, extras,
}: {
  title: string; hint: string; useCase: string; enableLabel: string
  boundModel: string
  settings: Record<string, unknown>; setSettings: (v: Record<string, unknown>) => void
  go?: (id: string) => void
  extras?: (settings: Record<string, unknown>, save: (patch: Record<string, unknown>) => void, boundModel: string) => React.ReactNode
}) {
  const [saved, setSaved] = useState(false)
  const flash = () => { setSaved(true); window.setTimeout(() => setSaved(false), 1500) }

  const enabled = Boolean(settings.enabled)
  const bound = !!boundModel
  // boundModel is a "provider:id" ref — show the model id without the provider prefix.
  const modelLabel = boundModel.includes(':') ? boundModel.split(':').slice(1).join(':') : boundModel

  const saveSettings = async (patch: Record<string, unknown>) => {
    const next = { ...settings, ...patch }
    setSettings(next)
    try { await api.saveUseCaseSettings(useCase, next); flash() } catch { /* keep optimistic */ }
  }

  return (
    <Section title={title} hint={hint}>
      <div className="rounded-lg bg-surface-container px-4 py-1">
        <Row label={enableLabel} hint={bound ? undefined : 'No model bound for this use case — bind one in Models to use this.'}>
          <div className="flex items-center gap-2">
            <AvailChip available={bound} okLabel="model bound" missLabel="no model" />
            <Toggle on={enabled} onChange={(v) => saveSettings({ enabled: v })} label={enableLabel} disabled={!bound} />
          </div>
        </Row>

        {/* The binding itself is owned by Models — show it read-only here. */}
        <Row label="Model" hint={`Bound to the ${useCase.toUpperCase()} use case — change it in Models.`}>
          {bound
            ? <span className="rounded-md bg-surface-high px-2 py-1 font-mono text-on-surface text-[0.78rem]">{modelLabel}</span>
            : <span className="text-on-surface-low text-[0.8rem] italic">none</span>}
        </Row>

        {enabled && bound && extras?.(settings, saveSettings, boundModel)}
      </div>

      <ManageLink kind={useCase.toUpperCase()} go={go} />
      <SavedToast show={saved} />
    </Section>
  )
}

const selectCls = 'h-9 w-full max-w-sm rounded-md bg-surface-high px-3 text-[0.8125rem] text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 cursor-pointer'

function AvailChip({ available, okLabel, missLabel }: { available: boolean; okLabel: string; missLabel: string }) {
  return (
    <span className="inline-flex items-center gap-1 text-[0.72rem]" style={{ color: available ? 'var(--color-success)' : 'var(--color-on-surface-low)' }}>
      {available ? <CheckCircle2 size={12} /> : <AlertTriangle size={12} />} {available ? okLabel : missLabel}
    </span>
  )
}

function ManageLink({ kind, go }: { kind: string; go?: (id: string) => void }) {
  if (!go) return null
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1">
      <button type="button" onClick={() => go('models')}
        className="inline-flex items-center gap-1 text-[0.78rem] text-primary hover:underline">
        Bind the {kind} model in Models <ArrowRight size={13} />
      </button>
      <button type="button" onClick={() => go('providers')}
        className="inline-flex items-center gap-1 text-[0.78rem] text-on-surface-low hover:text-on-surface hover:underline">
        Add or download models in Providers <ArrowRight size={13} />
      </button>
    </div>
  )
}

// ── Vocabulary & corrections (merged from the former VocabularyPanel) ────────────

const SOURCE_BADGE: Record<string, { label: string; cls: string }> = {
  graph: { label: 'graph', cls: 'bg-surface-high text-on-surface-low' },
  manual: { label: 'manual', cls: 'bg-primary/15 text-primary' },
  learned: { label: 'learned', cls: 'bg-ok/15' },
}

/** Vocabulary & corrections (core LEX.6) — the user-visible + editable Lexicon:
 *  terms (graph / manual / learned, source-badged), add/prune/delete a manual term,
 *  rebuild from the knowledge graph, and the learned-corrections list with a per-row
 *  "always fix" toggle. Reads /api/lexicon/* — the same store that biases EVERY
 *  transcription (mic STT + knowledge audio/video ingestion) + auto-corrects
 *  mis-heard terms. `scrollTo` (from the legacy #/settings/vocabulary redirect)
 *  scrolls the section into view once its data has painted. */
function VocabularySection({ scrollTo }: { scrollTo: boolean }) {
  const { data, refresh } = useCachedData('settings:lexicon', async () => {
    const [terms, corrections] = await Promise.all([
      api.lexiconTerms().catch(() => ({ terms: [] as LexiconTerm[], total: 0 })),
      api.lexiconCorrections().catch(() => ({ corrections: [] as LexiconCorrection[] })),
    ])
    return { terms: terms.terms, total: terms.total, corrections: corrections.corrections }
  }, { persist: true })

  const [adding, setAdding] = useState('')
  const [busy, setBusy] = useState(false)
  const reload = () => { invalidateCache('settings:lexicon'); refresh() }

  // Legacy #/settings/vocabulary deep-link → scroll here once (after first paint
  // with data, so the sections above have their final height).
  const anchor = useRef<HTMLDivElement | null>(null)
  const hasData = !!data
  useEffect(() => {
    if (scrollTo && hasData) anchor.current?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  }, [scrollTo, hasData])

  const addTerm = async () => {
    const v = adding.trim()
    if (!v || busy) return
    setBusy(true)
    try { await api.lexiconAddTerm(v); setAdding(''); reload() } finally { setBusy(false) }
  }
  const rebuild = async () => {
    if (busy) return
    setBusy(true)
    try { await api.lexiconRebuild(); reload() } finally { setBusy(false) }
  }

  return (
    <div ref={anchor} id="vocabulary" style={{ scrollMarginTop: '1rem' }}>
      <Section title="Vocabulary & corrections" hint="Your personal lexicon — the terms that bias every transcription (mic input and knowledge audio/video ingestion) toward how you actually spell things, and the learned fixes that auto-correct mis-heard words. Auto-built from your knowledge graph; add your own or prune wrong ones.">
        {!data ? <ListSkeleton rows={5} /> : (
          <>
            <div className="mb-3 flex items-center gap-2">
              <input
                value={adding} onChange={(e) => setAdding(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') addTerm() }}
                placeholder="Add a term (e.g. Kubernetes, K8s)…"
                className="flex-1 rounded-md border border-outline-variant/50 bg-surface-container px-3 py-2 text-[0.85rem] outline-none focus:border-primary" />
              <button type="button" onClick={addTerm} disabled={busy || !adding.trim()}
                className="inline-flex h-9 items-center gap-1.5 rounded-md bg-primary px-3 text-on-primary text-[0.8rem] disabled:opacity-40">
                <Plus size={15} /> Add
              </button>
              <button type="button" onClick={rebuild} disabled={busy} title="Resync from the knowledge graph"
                className="inline-flex h-9 items-center gap-1.5 rounded-md border border-outline-variant/50 px-3 text-on-surface-low text-[0.8rem] hover:text-on-surface disabled:opacity-40">
                <RefreshCw size={15} className={busy ? 'animate-spin' : ''} /> Rebuild
              </button>
            </div>
            <p className="mb-2 text-on-surface-low text-[0.78rem]">{data.total} in your lexicon.</p>
            {data.terms.length === 0 ? (
              <div className="rounded-lg border border-dashed border-outline-variant/50 bg-surface-container px-4 py-6 text-center text-on-surface-low text-[0.82rem]">
                No terms yet. <span className="text-on-surface">Rebuild</span> to seed from your knowledge graph, or add one above.
              </div>
            ) : (
              // A lexicon holds hundreds of terms — cap the list and scroll INSIDE it
              // so this section (below STT + TTS) never grows unboundedly. The section
              // header + add-term input stay above, always visible.
              <div className="max-h-[45vh] overflow-y-auto rounded-lg border border-outline-variant/30 px-3">
                <div className="flex flex-col divide-y divide-outline-variant/30">
                  {data.terms.map((t) => <TermRow key={t.id} term={t} onChanged={reload} />)}
                </div>
              </div>
            )}

            <h4 className="mt-6 mb-1 text-on-surface text-[0.875rem]" style={{ fontVariationSettings: '"wght" 600' }}>Learned corrections</h4>
            <p className="mb-2 text-on-surface-low text-[0.78rem]">Fixes captured from your transcript edits. Toggle “always” to auto-apply next time.</p>
            {data.corrections.length === 0 ? (
              <div className="rounded-lg border border-dashed border-outline-variant/50 bg-surface-container px-4 py-6 text-center text-on-surface-low text-[0.82rem]">
                No learned corrections yet. When you fix a mis-heard term in a transcript, it shows up here.
              </div>
            ) : (
              // Same unbounded-growth guard as the terms list, with its own (shorter) cap.
              <div className="max-h-[30vh] overflow-y-auto rounded-lg border border-outline-variant/30 px-3">
                <div className="flex flex-col divide-y divide-outline-variant/30">
                  {data.corrections.map((c) => <CorrectionRow key={c.id} corr={c} onChanged={reload} />)}
                </div>
              </div>
            )}
          </>
        )}
      </Section>
    </div>
  )
}

function TermRow({ term, onChanged }: { term: LexiconTerm; onChanged: () => void }) {
  const badge = SOURCE_BADGE[term.source] ?? SOURCE_BADGE.graph
  const [busy, setBusy] = useState(false)
  const act = async (fn: () => Promise<unknown>) => { setBusy(true); try { await fn(); onChanged() } finally { setBusy(false) } }
  return (
    <div className={`flex items-center gap-2 py-2 ${term.enabled ? '' : 'opacity-50'}`}>
      <span className="flex-1 truncate text-[0.85rem]">
        {term.canonical}
        {term.aliases.length > 0 && <span className="ml-1.5 text-on-surface-low text-[0.75rem]">({term.aliases.join(', ')})</span>}
      </span>
      <span className={`rounded px-1.5 py-0.5 text-[0.68rem] ${badge.cls}`}>{badge.label}</span>
      <button type="button" disabled={busy} title={term.enabled ? 'Disable (prune)' : 'Enable'}
        onClick={() => act(() => api.lexiconSetTermEnabled(term.id, !term.enabled))}
        className="inline-flex h-7 w-7 items-center justify-center rounded text-on-surface-low hover:text-on-surface disabled:opacity-40">
        {term.enabled ? <X size={14} /> : <Check size={14} />}
      </button>
      <button type="button" disabled={busy} title="Delete"
        onClick={() => act(() => api.lexiconDeleteTerm(term.id))}
        className="inline-flex h-7 w-7 items-center justify-center rounded text-on-surface-low hover:text-danger disabled:opacity-40">
        <Trash2 size={14} />
      </button>
    </div>
  )
}

function CorrectionRow({ corr, onChanged }: { corr: LexiconCorrection; onChanged: () => void }) {
  const [busy, setBusy] = useState(false)
  const toggle = async () => { setBusy(true); try { await api.lexiconSetCorrectionAuto(corr.id, !corr.auto_apply); onChanged() } finally { setBusy(false) } }
  return (
    <div className="flex items-center gap-2 py-2 text-[0.85rem]">
      <span className="flex-1 truncate">
        <span className="text-on-surface-low line-through">{corr.heard}</span>
        <span className="mx-1.5 text-on-surface-low">→</span>
        <span className="text-on-surface">{corr.meant}</span>
        <span className="ml-2 text-on-surface-low text-[0.72rem]">×{corr.count}</span>
      </span>
      <button type="button" onClick={toggle} disabled={busy}
        title={corr.auto_apply ? 'Auto-applied — click to make it a suggestion' : 'Always fix this automatically'}
        className={`inline-flex h-7 items-center gap-1 rounded px-2 text-[0.72rem] transition-colors disabled:opacity-40 ${
          corr.auto_apply ? 'bg-ok/15' : 'border border-outline-variant/50 text-on-surface-low hover:text-on-surface'}`}>
        <Wand2 size={12} /> {corr.auto_apply ? 'Always' : 'Suggest'}
      </button>
    </div>
  )
}
