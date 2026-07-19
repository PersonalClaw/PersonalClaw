import { useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { ArrowRight, User, Cpu, Sparkles, Loader2, ExternalLink, Check } from 'lucide-react'
import { ClawMark } from '../ui/ClawMark'
import { DotGlow } from '../ui/DotGlow'
import { spring, stagger, listItemEnter } from '../design/motion'
import { useIdentity, firstNameOf } from './identity'
import { APP_NAME } from './config'
import { api, type OnboardingState, type ChatModelOption } from '../lib/api'
import { StepRow, type StepState } from './onboarding/StepStack'

type StepId = 'name' | 'model' | 'ready'
const ORDER: StepId[] = ['name', 'model', 'ready']

/** First-run welcome — a full-screen branded moment over the chat 3D dot-wave.
 *  A vertically-stacked stepper: each step expands when active and collapses to
 *  a green "done" row. The DotGlow focus follows the active row down the page.
 *  Shown only until a name is set; the model step is a fix-or-skip readiness
 *  check (name is the only hard gate). */
export function Onboarding() {
  const { setName } = useIdentity()
  const [step, setStep] = useState<StepId>('name')
  const [name, setNameDraft] = useState('')
  const [savedName, setSavedName] = useState('')
  const [readiness, setReadiness] = useState<OnboardingState | null>(null)
  const [modelDone, setModelDone] = useState<string>('')  // '' = not resolved, else summary

  // the active step's row drives the 3D glow focus (like the composer in chat)
  const rowRefs = { name: useRef<HTMLDivElement>(null), model: useRef<HTMLDivElement>(null), ready: useRef<HTMLDivElement>(null) }
  const activeRef = rowRefs[step]

  const stateOf = (id: StepId): StepState => {
    const si = ORDER.indexOf(step), ii = ORDER.indexOf(id)
    if (id === step) return 'active'
    return ii < si ? 'done' : 'upcoming'
  }

  // fetch readiness when entering the model step
  useEffect(() => {
    if (step === 'model' && !readiness) api.onboarding().then(setReadiness).catch(() => setReadiness({ needs_model: true, has_model_provider: false, has_chat_binding: false }))
  }, [step, readiness])

  function commitName() {
    const n = name.trim()
    if (!n) return
    setSavedName(n); setStep('model')
  }
  function finish() {
    // commit identity LAST so the gate (`onboarded`) flips only on completion
    setName(savedName || 'Operator')
  }

  return (
    <div className="fixed inset-0 z-[100] overflow-hidden" style={{ background: 'var(--color-canvas)' }}>
      <DotGlow intensity={1.15} composerRef={activeRef} />

      <div className="relative flex h-full items-center justify-center overflow-y-auto px-l py-3xl">
        <motion.div initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={spring.spatialSlow}
          className="relative w-full" style={{ maxWidth: 540 }}>
          {/* hero — floats ABOVE the stepper (absolute, so it doesn't affect the
              stepper's vertical centering; the STEPPER is what sits mid-screen) */}
          <div className="absolute bottom-full left-0 right-0 mb-2xl flex flex-col items-center">
            <ClawMark size={52} animated blob />
            <h1 data-type="headline-m" className="mt-l text-on-surface text-center">Welcome to {APP_NAME}</h1>
            <p className="mt-2 text-center text-on-surface-low text-[0.9375rem]" style={{ maxWidth: 360 }}>Your self-hosted personal agent. A few moments to get set up.</p>
          </div>

          {/* vertical collapsing stepper — the centered focal element */}
          <div className="flex w-full flex-col gap-2">
            <StepRow ref={rowRefs.name} index={0} icon={User} title="Your name"
              subtitle="How the system addresses you. Saved on the server, so it follows you across devices."
              state={stateOf('name')} doneSummary={savedName ? `${savedName}` : undefined}
              onActivate={() => setStep('name')}>
              <NameStep value={name} onChange={setNameDraft} onSubmit={commitName} />
            </StepRow>

            <StepRow ref={rowRefs.model} index={1} icon={Cpu} title="Chat model"
              subtitle="Confirm the agent has a model to think with."
              state={stateOf('model')} doneSummary={modelDone || undefined}
              onActivate={() => setStep('model')}>
              <ModelStep readiness={readiness}
                onResolved={(summary) => { setModelDone(summary); setStep('ready') }}
                onSkip={() => { setModelDone('Set up later'); setStep('ready') }}
                onOpenSettings={finish} />
            </StepRow>

            <StepRow ref={rowRefs.ready} index={2} icon={Sparkles} title="All set"
              subtitle={`You're ready, ${firstNameOf(savedName)}.`}
              state={stateOf('ready')}>
              <ReadyStep name={savedName} modelSummary={modelDone} onFinish={finish} />
            </StepRow>
          </div>
        </motion.div>
      </div>
    </div>
  )
}

/** Step 1 — name (pill input with focus glow, Enter/arrow to advance). */
function NameStep({ value, onChange, onSubmit }: { value: string; onChange: (v: string) => void; onSubmit: () => void }) {
  return (
    <div className="flex items-center gap-s rounded-pill bg-surface-high px-s py-1.5 ring-1 ring-outline/40 focus-within:ring-2 focus-within:ring-inset focus-within:ring-primary/50">
      <input autoFocus value={value} onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') onSubmit() }}
        placeholder="Your name"
        className="min-w-0 flex-1 bg-transparent px-m text-on-surface text-[1rem] placeholder:text-on-surface-low outline-none" />
      <motion.button whileTap={{ scale: 0.96 }} transition={spring.spatialFast} onClick={onSubmit} type="button"
        disabled={!value.trim()}
        className="inline-flex size-9 shrink-0 items-center justify-center rounded-pill disabled:opacity-40"
        style={{ background: 'var(--color-primary)', color: 'var(--color-on-primary)' }} aria-label="Continue">
        <ArrowRight size={17} />
      </motion.button>
    </div>
  )
}

/** Step 2 — readiness. Real fix: bind a chat model in-flow when none is bound. */
function ModelStep({ readiness, onResolved, onSkip, onOpenSettings }: { readiness: OnboardingState | null; onResolved: (summary: string) => void; onSkip: () => void; onOpenSettings: () => void }) {
  const [models, setModels] = useState<ChatModelOption[] | null>(null)
  const [binding, setBinding] = useState<string>('')

  // already good? `needs_model` is the backend's single source of truth (a
  // dry-run of real chat resolution) — the coarser has_* flags only pick which
  // fix-path UI to show below.
  useEffect(() => {
    if (readiness && !readiness.needs_model) onResolved('Ready to chat')
  }, [readiness]) // eslint-disable-line react-hooks/exhaustive-deps

  // provider present but chat can't resolve → offer the bindable models
  useEffect(() => {
    if (readiness && readiness.needs_model && readiness.has_model_provider) {
      api.chatModels().then(setModels).catch(() => setModels([]))
    }
  }, [readiness])

  if (!readiness) return <Centered><Loader2 size={18} className="animate-spin text-on-surface-low" /></Centered>

  if (!readiness.needs_model) {
    return <p className="inline-flex items-center gap-1.5 text-[0.875rem]" style={{ color: 'var(--color-success)' }}><Check size={15} /> A chat model is configured — you're ready.</p>
  }

  // No provider at all → can't bind here; point to Settings, allow skip.
  if (!readiness.has_model_provider) {
    return (
      <div className="flex flex-col gap-m">
        <p className="text-on-surface-var text-[0.875rem] leading-relaxed">No model provider is connected yet, so chat can't run. Connect one in <span className="text-on-surface">Settings → Providers</span> after setup — it only takes a moment.</p>
        <div className="flex items-center gap-s">
          {/* Commit the name BEFORE navigating: the App.tsx guard redirects back
              to onboarding while `onboarded` is false, so without the commit
              this link is a no-op loop. The name step is already complete here. */}
          <a href="#/settings" onClick={onOpenSettings} className="inline-flex items-center gap-1.5 rounded-md px-3 h-9 text-[0.8125rem]" style={{ background: 'var(--color-primary)', color: 'var(--color-on-primary)' }}><ExternalLink size={14} /> Open Settings</a>
          <button type="button" onClick={onSkip} className="text-on-surface-low text-[0.8125rem] hover:text-on-surface">Set up later</button>
        </div>
      </div>
    )
  }

  // Provider present, no binding → bind one in-flow.
  async function bind(m: ChatModelOption) {
    setBinding(m.name)
    // active_models.json holds canonical `provider:model` refs (what ModelsPanel
    // writes) — NOT the display `name`, which the discovery fallback builds as
    // `provider/model`. Build the ref from the entry's own parts.
    const ref = m.provider ? `${m.provider}:${m.model_id}` : m.model_id
    try { await api.setActiveModel('chat', [ref]); onResolved(`${m.model_id}`) }
    catch { setBinding('') }
  }
  return (
    <div className="flex flex-col gap-m">
      <p className="text-on-surface-var text-[0.875rem]">Pick the model the agent should chat with:</p>
      {models === null ? <Centered><Loader2 size={16} className="animate-spin text-on-surface-low" /></Centered>
        : models.length === 0 ? <p className="text-on-surface-low text-[0.8125rem]">No chat-capable models found. <button onClick={onSkip} className="text-primary hover:underline">Set up later</button></p>
        : (
          <motion.div className="flex flex-col gap-1.5"
            initial="initial" animate="animate" variants={{ animate: { transition: stagger() } }}>
            {models.map((m) => (
              <motion.button key={m.name} variants={listItemEnter} type="button" onClick={() => bind(m)} disabled={!!binding}
                className="flex items-center gap-2 rounded-lg bg-surface-high px-3 py-2.5 text-left transition-colors hover:bg-surface-highest disabled:opacity-50">
                <Cpu size={15} className="shrink-0 text-primary" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-on-surface text-[0.875rem]">{m.model_id}</div>
                  <div className="text-on-surface-low text-[0.7rem]">{m.provider}</div>
                </div>
                {binding === m.name && <Loader2 size={15} className="shrink-0 animate-spin text-on-surface-low" />}
              </motion.button>
            ))}
            <button type="button" onClick={onSkip} className="mt-1 self-start text-on-surface-low text-[0.8125rem] hover:text-on-surface">Set up later</button>
          </motion.div>
        )}
    </div>
  )
}

/** Step 3 — recap + launch. */
function ReadyStep({ name, modelSummary, onFinish }: { name: string; modelSummary: string; onFinish: () => void }) {
  const chatReady = modelSummary && modelSummary !== 'Set up later'
  return (
    <div className="flex flex-col gap-m">
      <motion.div className="flex flex-col gap-1.5"
        initial="initial" animate="animate" variants={{ animate: { transition: stagger(0.06) } }}>
        <motion.div variants={listItemEnter}><Recap ok label={`Hello, ${firstNameOf(name)}`} /></motion.div>
        <motion.div variants={listItemEnter}><Recap ok={!!chatReady} label={chatReady ? `Chat model: ${modelSummary}` : 'Chat model — set up later in Settings'} /></motion.div>
      </motion.div>
      <motion.button whileTap={{ scale: 0.98 }} transition={spring.spatialFast} onClick={onFinish} type="button"
        className="inline-flex items-center justify-center gap-1.5 self-start rounded-pill px-5 h-11 text-[0.9375rem]"
        style={{ background: 'var(--color-primary)', color: 'var(--color-on-primary)', fontVariationSettings: '"wght" 500' }}>
        Start using {APP_NAME} <ArrowRight size={17} />
      </motion.button>
    </div>
  )
}

function Recap({ ok, label }: { ok: boolean; label: string }) {
  return (
    <div className="flex items-center gap-2 text-[0.875rem]">
      <span className="grid size-5 place-items-center rounded-full" style={{ background: ok ? 'var(--color-success)' : 'var(--color-surface-high)', color: ok ? 'var(--color-on-primary)' : 'var(--color-on-surface-low)' }}><Check size={12} /></span>
      <span className="text-on-surface-var">{label}</span>
    </div>
  )
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="flex items-center py-2">{children}</div>
}
