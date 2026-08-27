import { useEffect, useRef, useState } from 'react'
import { notify } from '../../app/appSdk'
import { unavailableWhen } from '../../ui/unavailable'
import { CheckCircle2, AlertTriangle, ArrowRight, Plus, Trash2, RefreshCw, Check, X, Wand2 } from 'lucide-react'
import { api, type LexiconTerm, type LexiconCorrection } from '../../lib/api'
import { useQuery, invalidateKeys } from '../../lib/data'
import { PanelHeader, Section, RowGroup, Row, Field, Toggle, SavedToast, ToggleRow } from './settingsUI'
import { FormSkeleton, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { ChipInput } from '../../ui/forms'
import { SquareIconButton } from '../../ui/SquareIconButton'
import { TextLink } from '../../ui/TextLink'
import { fvs } from '../../design/fontWeight'
import { bindChord, chordFromEvent, formatChord, DEFAULT_PUSH_TO_TALK_CHORD } from '../../lib/pushToTalk'
import { desktopBridge } from '../../lib/desktopBridge'
import { ShortcutRecorder } from '../../ui/ShortcutRecorder'

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
  const { data, error: loadErr, refresh } = useQuery('settings:voice', async () => {
    const [active, stt, tts] = await Promise.all([
      // `modelsActive` KEEPS its fallback: it only shows readiness ("model bound" / "no model"), so losing
      // it degrades a chip rather than inventing your settings.
      api.modelsActive().catch(() => ({} as Record<string, string[]>)),
      // 🔴 These two ARE the panel. `.catch(() => ({}))` made a failed read resolve as an empty settings
      // object, so every control rendered at its fallback — indistinguishable from "this is what you
      // saved" — and each one PUTs on change. Measured on `#/settings/voice` with the use-case GETs at
      // 500: **2 switches and 1 input rendered, no error anywhere**. Same defect cycle 124 fixed in three
      // sibling panels; this is the fourth.
      api.useCaseSettings('stt'),
      api.useCaseSettings('tts'),
    ])
    return { active, stt, tts }
  }, { persist: true })
  const active = data?.active ?? {}

  useEffect(() => {
    if (data) { setSttSettings(data.stt); setTtsSettings(data.tts) }
  }, [data])

  // Error first: `data` is undefined for loading AND for failure, so a later test never runs.
  if (!data && loadErr) return <LoadError what="speech settings" error={loadErr} onRetry={refresh} />
  if (!data || !sttSettings || !ttsSettings) return <FormSkeleton sections={3} what="speech settings" />

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
                  <span className="text-on-surface-low text-[0.75rem]">Fast</span>
                  <input type="range" min={0.6} max={1.6} step={0.05} value={speed}
                    onChange={(e) => setLocalSpeed(s, setTtsSettings, Number(e.target.value))}
                    onPointerUp={(e) => save({ speed: Number((e.target as HTMLInputElement).value) })}
                    // Keyboard adjustments never fire pointerup — persist those too.
                    onKeyUp={(e) => { if (RANGE_KEYS.has(e.key)) save({ speed: Number((e.target as HTMLInputElement).value) }) }}
                    className="flex-1 accent-[var(--color-primary)]" />
                  <span className="text-on-surface-low text-[0.75rem]">Slow</span>
                  <span className="w-10 text-right font-mono text-on-surface text-[0.75rem] tabular-nums">{speed.toFixed(2)}×</span>
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
      <HandsFreeSection />
      <VocabularySection scrollTo={query?.section === 'vocabulary'} />
    </div>
  )
}

/** Hands-free voice loop (MULTIMODAL-IO §4.5) + the desktop push-to-talk chord
 *  (DESKTOP-CAPABILITIES S3) — the `voice.*` config fields.
 *
 *  They are comfort knobs, not safety guards: turning one off makes the loop
 *  noisier (more echo, code read aloud), never less safe. Each control patches one
 *  config path and flashes its own "Saved ✓". The phrase lists are what the composer's
 *  hands-free toggle gates on, so an empty list would make the mode deaf — the backend
 *  falls back to the shipped defaults rather than accepting one. */
function HandsFreeSection() {
  const { data, error, refresh } = useQuery('settings:voice-loop', async () => {
    const cfg = await api.personalclawConfig()
    return (cfg.voice ?? {}) as Record<string, unknown>
  }, { persist: true })
  const [cfg, setCfg] = useState<Record<string, unknown> | null>(null)
  useEffect(() => { if (data) setCfg(data) }, [data])

  // Error first: `data` is undefined for loading AND for failure. A silent fallback
  // here would render every switch at its default — indistinguishable from "this is
  // what you saved", on controls that PATCH on change.
  if (!data && error) return <LoadError what="hands-free voice settings" error={error} onRetry={refresh} />
  if (!cfg) return <FormSkeleton sections={1} what="hands-free voice settings" />

  const patch = (key: string, value: unknown, onSaved: () => void) => {
    const prev = cfg
    setCfg({ ...cfg, [key]: value })
    api.patchConfig(`voice.${key}`, value).then(onSaved).catch(() => setCfg(prev))
  }
  const phrases = (key: string) => {
    const v = cfg[key]
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
  }

  return (
    <Section title="Hands-free voice" hint="Keep listening and send only when you say a confirmation phrase. The mic button stays push-to-talk; these settings shape the hands-free loop beside it.">
      <RowGroup>
        <ChordRow
          value={typeof cfg.push_to_talk_chord === 'string' && cfg.push_to_talk_chord
            ? cfg.push_to_talk_chord
            : DEFAULT_PUSH_TO_TALK_CHORD}
          onChange={(v, cb) => patch('push_to_talk_chord', v, cb)} />
        <PhraseRow label="Confirmation phrases"
          hint="Dictation accumulates in the composer until one of these ends what you just said — so a half-finished thought is never sent."
          values={phrases('confirmation_phrases')} onChange={(v, cb) => patch('confirmation_phrases', v, cb)} />
        <PhraseRow label="Exit phrases" hint="Saying one of these throws the accumulated dictation away."
          values={phrases('exit_phrases')} onChange={(v, cb) => patch('exit_phrases', v, cb)} />
        <ToggleRow label="Mute while speaking" hint="Release the microphone and discard what it captured while a reply plays aloud. This is what stops the assistant hearing itself."
          cfg={cfg} field="duplex_mute_enabled" patch={patch} />
        <ToggleRow label="Echo filter" hint="Also drop any transcription that repeats three consecutive words the assistant just spoke — the backstop for speaker bleed."
          cfg={cfg} field="echo_filter_enabled" patch={patch} />
        <ToggleRow label="Clean text before speaking" hint="Strip code blocks, shorten URLs to their domain and paths to their filename, and drop CLI flags. The transcript always keeps the full text."
          cfg={cfg} field="clean_for_speech_enabled" patch={patch} />
        <ToggleRow label="Voice-origin disclaimer" hint="Tell the model a message was dictated so it self-corrects misheard words instead of confidently misreading them."
          cfg={cfg} field="voice_disclaimer_enabled" patch={patch} />
      </RowGroup>
    </Section>
  )
}

/** The desktop push-to-talk chord (DESKTOP-CAPABILITIES S3).
 *
 *  A RECORDER, not a text field: the stored value is an Electron accelerator string
 *  (`CommandOrControl+Shift+Space`), which nobody should have to type or spell. Click,
 *  press the combination, done — and what is displayed back is `⌘⇧Space`.
 *
 *  Two failure modes get their own sentences, because they need different actions:
 *
 *  - **Only modifiers pressed** — the recorder keeps listening rather than storing half a
 *    chord. A bare key is likewise never recorded: the shell refuses to bind one (it
 *    would be taken from every app on the machine), so offering it here would be
 *    offering a shortcut that cannot be saved.
 *  - **Already owned by another app** — the shell answers with the conflict, and the
 *    previous chord is KEPT. Discovering a clash at the next launch, with the old
 *    shortcut already thrown away, is the outcome this avoids.
 *
 *  In a browser tab there is no shell to bind anything, so the row saves the preference
 *  and says plainly that the desktop app is what applies it. Hiding the control would
 *  make the setting undiscoverable for anyone configuring before installing the app.
 */
function ChordRow({ value, onChange }: { value: string; onChange: (next: string, onSaved: () => void) => void }) {
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const flash = () => { setSaved(true); window.setTimeout(() => setSaved(false), 1500) }
  const shell = !!desktopBridge()

  const record = async (chord: string) => {
    if (chord === value) { setError(''); return }
    if (shell) {
      // Bind FIRST, save second: a chord that cannot be bound must not become the
      // stored value, or the setting would claim a shortcut that does nothing.
      const r = await bindChord(chord)
      if (!r.ok) { setError(r.reason); return }
    }
    setError('')
    onChange(chord, flash)
  }

  return (
    <Field label="Push-to-talk shortcut"
      hint={shell
        ? 'Press it to start capturing your microphone, press it again to stop and transcribe into the composer at your cursor. It works while other apps have focus, so a capture indicator stays in the menu bar the whole time.'
        : 'Used by the desktop app for global push-to-talk. A browser tab has no global shortcuts, so this is saved for when you run the desktop app.'}>
      <div className="flex flex-wrap items-center gap-2">
        <ShortcutRecorder label="Push-to-talk shortcut" value={value}
          format={formatChord} parse={chordFromEvent} onRecord={record} />
        {value !== DEFAULT_PUSH_TO_TALK_CHORD && (
          <TextLink size="xs" onClick={async () => {
            if (shell) {
              const r = await bindChord(DEFAULT_PUSH_TO_TALK_CHORD)
              if (!r.ok) { setError(r.reason); return }
            }
            setError('')
            onChange(DEFAULT_PUSH_TO_TALK_CHORD, flash)
          }}>Reset to default</TextLink>
        )}
        <SavedToast show={saved} />
      </div>
      {error && (
        // The conflict/refusal sentence from the shell, verbatim: it names the chord and
        // what to do, which a generic "couldn't save" would not.
        <p role="alert" className="mt-1.5 text-[0.75rem]" style={{ color: 'var(--color-error)' }}>{error}</p>
      )}
    </Field>
  )
}

/** One editable phrase list. Chips, not a comma-separated string: a phrase can
 *  contain a comma, and the chip form makes each phrase individually removable. */
function PhraseRow({ label, hint, values, onChange }: {
  label: string
  hint: string
  values: string[]
  onChange: (next: string[], onSaved: () => void) => void
}) {
  const [saved, setSaved] = useState(false)
  const flash = () => { setSaved(true); window.setTimeout(() => setSaved(false), 1500) }
  return (
    <Field label={label} hint={hint}>
      <div className="flex items-center gap-2">
        <ChipInput values={values} onChange={(v) => onChange(v, flash)} max={20} placeholder="Add a phrase…" ariaLabel={label} />
        <SavedToast show={saved} />
      </div>
    </Field>
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
    const prev = settings
    const next = { ...settings, ...patch }
    setSettings(next)
    try {
      await api.saveUseCaseSettings(useCase, next)
      flash()
    } catch (e) {
      // `keep optimistic` left the toggle showing a value the server had REFUSED — a claim that
      // survived until the next reload, with no flash and no error to explain it.
      //
      // This section receives `settings`/`setSettings` as props and owns no read of its own, so it
      // ROLLS BACK to the pre-patch value rather than reconciling by re-reading (the hub tiles'
      // `mutate` does the latter because it holds the cache keys). Both forms are already in this
      // codebase — `WidgetFrame.pin` rolls back, `PinnedArtifacts.unpin` reconciles — and the one
      // that is wrong is keeping a value the server refused.
      setSettings(prev)
      notify(`Couldn't save this speech setting: ${String((e as Error)?.message || e)}`, 'error')
    }
  }

  return (
    <Section title={title} hint={hint}>
      <RowGroup>
        <Row label={enableLabel} hint={bound ? undefined : 'No model bound for this use case — bind one in Models to use this.'}>
          <div className="flex items-center gap-2">
            <AvailChip available={bound} okLabel="model bound" missLabel="no model" />
            <Toggle on={enabled} onChange={(v) => saveSettings({ enabled: v })} label={enableLabel} disabled={!bound}
              disabledReason="No model is bound for this use case — bind one in Models first" />
          </div>
        </Row>

        {/* The binding itself is owned by Models — show it read-only here. */}
        <Row label="Model" hint={`Bound to the ${useCase.toUpperCase()} use case — change it in Models.`}>
          {bound
            ? <span className="rounded-md bg-surface-high px-2 py-1 font-mono text-on-surface text-[0.75rem]">{modelLabel}</span>
            : <span className="text-on-surface-low text-[0.8125rem] italic">none</span>}
        </Row>

        {enabled && bound && extras?.(settings, saveSettings, boundModel)}
      </RowGroup>

      <ManageLink kind={useCase.toUpperCase()} go={go} />
      <SavedToast show={saved} />
    </Section>
  )
}

const selectCls = 'h-9 w-full max-w-sm rounded-md bg-surface-high px-3 text-[0.8125rem] text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 cursor-pointer'

function AvailChip({ available, okLabel, missLabel }: { available: boolean; okLabel: string; missLabel: string }) {
  return (
    <span className="inline-flex items-center gap-1 text-[0.75rem]" style={{ color: available ? 'var(--color-success)' : 'var(--color-on-surface-low)' }}>
      {available ? <CheckCircle2 size={12} /> : <AlertTriangle size={12} />} {available ? okLabel : missLabel}
    </span>
  )
}

// `gap-y-3`, not `gap-y-1`: `TextLink` grows its hit box with `py-1 -my-1`, so it bleeds 4px past its
// layout box on BOTH sides to keep a line's rhythm. When this row wraps — it does at 390px, where the two
// links no longer fit side by side — a 4px `gap-y-1` minus that 8px of bleed left the two 26px targets
// OVERLAPPING by 4px (measured: first bottom=530.5, second top=526.5), which is the SC 2.5.8 failure axe
// reports as serious. They pass on SIZE alone (26px ≥ 24px) once they stop overlapping, so 12px of gap
// (12 − 8 = 4px of real separation) is enough. Desktop is untouched: `gap-y` only applies between
// wrapped lines, and at 1440px these sit on one.
function ManageLink({ kind, go }: { kind: string; go?: (id: string) => void }) {
  if (!go) return null
  // `ink="emphasis"` on both: this row is a bare `<div>` with no background, so the links inherit
  // `--color-canvas`, where the base accent measures **4.37:1** at this `xs` size (12px) against a 4.5
  // floor — light mode, unchanged at 390px. An app-wide census of every RENDERED accent link (55
  // surfaces, populated home, backdrop read off each node) found 25 of them: 20 on `--color-surface`
  // passing at 4.83, one already on the emphasis shade, and these four — the component mounts twice,
  // for STT and TTS — as the only failures. The emphasis shade measures 6.0 in coral and passes in all
  // 12 schemes. Dark was already fine at 6.85.
  //
  // 🪤 This comment lives ABOVE the `return`, not inside it: a `{/* … */}` as the first child of a
  // `return (` is a SECOND child where one expression is allowed, and the parse error it throws is
  // reported against an unrelated line. Third time this session.
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-3">
      <TextLink onClick={() => go('models')} icon={ArrowRight} iconPosition="trailing" size="xs" ink="emphasis">
        Bind the {kind} model in Models
      </TextLink>
      {/* 🔑 ITS OWN SIBLING WAS THE ANSWER. This row already ships a `TextLink` two lines up — same job
          (navigate to another settings sub-view), same `xs` size, same trailing arrow — and it measured
          181.13×26.00 while this hand-rolled twin measured 224.73×**18.00**. So the fix is not a colour
          judgement, it is convergence onto what the row already renders: the primitive carries cycle
          115's `py-1 -my-1` hit box, so adopting it fixes SC 2.5.8 and deletes the drift in one move. */}
      <TextLink onClick={() => go('providers')} icon={ArrowRight} iconPosition="trailing" size="xs" ink="emphasis">
        Add or download models in Providers
      </TextLink>
    </div>
  )
}

// ── Vocabulary & corrections (merged from the former VocabularyPanel) ────────────

const SOURCE_BADGE: Record<string, { label: string; cls: string }> = {
  graph: { label: 'graph', cls: 'bg-surface-high text-on-surface-low' },
  manual: { label: 'manual', cls: 'bg-primary-container text-on-primary-container' },
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
  const { data, refresh } = useQuery('settings:lexicon', async () => {
    const [terms, corrections] = await Promise.all([
      api.lexiconTerms().catch(() => ({ terms: [] as LexiconTerm[], total: 0 })),
      api.lexiconCorrections().catch(() => ({ corrections: [] as LexiconCorrection[] })),
    ])
    return { terms: terms.terms, total: terms.total, corrections: corrections.corrections }
  }, { persist: true })

  const [adding, setAdding] = useState('')
  const [busy, setBusy] = useState(false)
  const reload = () => { invalidateKeys('settings:lexicon'); refresh() }

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
                aria-label="Add a vocabulary term"
                onKeyDown={(e) => { if (e.key === 'Enter') addTerm() }}
                placeholder="Add a term (e.g. Kubernetes, K8s)…"
                className="flex-1 rounded-md border border-outline-variant/50 bg-surface-container px-3 py-2 text-[0.8125rem] outline-none focus:border-primary" />
              <button type="button" onClick={addTerm}
                {...unavailableWhen(!adding.trim(), 'Enter a term first', { busy })}
                className="inline-flex h-9 items-center gap-1.5 rounded-md bg-primary px-3 text-on-primary text-[0.8125rem] disabled:opacity-40 aria-disabled:opacity-40 aria-disabled:cursor-not-allowed">
                <Plus size={15} /> Add
              </button>
              <button type="button" onClick={rebuild} disabled={busy} title="Resync from the knowledge graph"
                className="inline-flex h-9 items-center gap-1.5 rounded-md border border-outline-variant/50 px-3 text-on-surface-low text-[0.8125rem] hover:text-on-surface disabled:opacity-40">
                <RefreshCw size={15} className={busy ? 'animate-spin' : ''} /> Rebuild
              </button>
            </div>
            <p className="mb-2 text-on-surface-low text-[0.75rem]">{data.total} in your lexicon.</p>
            {data.terms.length === 0 ? (
              <div className="rounded-lg border border-dashed border-outline-variant/50 bg-surface-container px-4 py-6 text-center text-on-surface-low text-[0.8125rem]">
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

            {/* 🔴 `h3`, not `h4`. This sits inside `<Section title="Vocabulary & corrections">`, which
                renders an `h2` under the panel's `h1`, so a level-4 heading skipped level 3. Measured in
                the live DOM: `H1 > H2 > H2 > H2 > H2 > H4`. The size comes from the class, so this is
                pixel-identical — the same rule `settingsUI`'s `Section` states for itself.
                🪤 `ux-audit` did NOT report this one, and providers' skip on the same run: it evaluates
                after the panel's skeleton clears but BEFORE this nested, separately-fetched block
                renders, so the h4 is not in the DOM it measures. A static rail catches it instead. */}
            <h3 className="mt-6 mb-1 text-on-surface text-[0.8125rem]" style={fvs(600)}>Learned corrections</h3>
            <p className="mb-2 text-on-surface-low text-[0.75rem]">Fixes captured from your transcript edits. Toggle “always” to auto-apply next time.</p>
            {data.corrections.length === 0 ? (
              <div className="rounded-lg border border-dashed border-outline-variant/50 bg-surface-container px-4 py-6 text-center text-on-surface-low text-[0.8125rem]">
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
      <span className="flex-1 truncate text-[0.8125rem]">
        {term.canonical}
        {term.aliases.length > 0 && <span className="ml-1.5 text-on-surface-low text-[0.75rem]">({term.aliases.join(', ')})</span>}
      </span>
      <span className={`rounded px-1.5 py-0.5 text-[0.75rem] ${badge.cls}`}>{badge.label}</span>
      <button type="button" disabled={busy} title={term.enabled ? 'Disable (prune)' : 'Enable'}
        onClick={() => act(() => api.lexiconSetTermEnabled(term.id, !term.enabled))}
        className="inline-flex h-7 w-7 items-center justify-center rounded text-on-surface-low hover:text-on-surface disabled:opacity-40">
        {term.enabled ? <X size={14} /> : <Check size={14} />}
      </button>
      <SquareIconButton icon={Trash2} tone="danger" label="Delete" disabled={busy}
        onClick={() => act(() => api.lexiconDeleteTerm(term.id))} />
    </div>
  )
}

function CorrectionRow({ corr, onChanged }: { corr: LexiconCorrection; onChanged: () => void }) {
  const [busy, setBusy] = useState(false)
  const toggle = async () => { setBusy(true); try { await api.lexiconSetCorrectionAuto(corr.id, !corr.auto_apply); onChanged() } finally { setBusy(false) } }
  return (
    <div className="flex items-center gap-2 py-2 text-[0.8125rem]">
      <span className="flex-1 truncate">
        <span className="text-on-surface-low line-through">{corr.heard}</span>
        <span className="mx-1.5 text-on-surface-low">→</span>
        <span className="text-on-surface">{corr.meant}</span>
        <span className="ml-2 text-on-surface-low text-[0.75rem]">×{corr.count}</span>
      </span>
      <button type="button" onClick={toggle} disabled={busy}
        title={corr.auto_apply ? 'Auto-applied — click to make it a suggestion' : 'Always fix this automatically'}
        className={`inline-flex h-7 items-center gap-1 rounded px-2 text-[0.75rem] transition-colors disabled:opacity-40 ${
          corr.auto_apply ? 'bg-ok/15' : 'border border-outline-variant/50 text-on-surface-low hover:text-on-surface'}`}>
        <Wand2 size={12} /> {corr.auto_apply ? 'Always' : 'Suggest'}
      </button>
    </div>
  )
}
