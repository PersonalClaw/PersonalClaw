import { useCallback, useEffect, useRef, useState } from 'react'
import { unavailableWhen } from '../ui/unavailable'
import { withWeight } from '../design/fontWeight'
import { motion } from 'framer-motion'
import { ArrowRight, User, Boxes, Rocket, Sparkles, Loader2, Check } from 'lucide-react'
import { ClawMark } from '../ui/ClawMark'
import { DotGlow } from '../ui/DotGlow'
import { LoadingStatus } from '../ui/ListScaffold'
import { spring, stagger, listItemEnter } from '../design/motion'
import { useIdentity, firstNameOf } from './identity'
import { APP_NAME } from './config'
import { api, type OnboardingState, type OnboardingStatePatch } from '../lib/api'
import { StepRow, type StepState } from './onboarding/StepStack'
import { EssentialsStep } from './onboarding/EssentialsStep'
import { TryOneStep } from './onboarding/TryOneStep'
import { setOnboardingExit } from './onboarding/exitTo'

type StepId = 'name' | 'essentials' | 'try' | 'ready'
const ORDER: StepId[] = ['name', 'essentials', 'try', 'ready']
// One source for each step's title — used by both the StepRow headings and the live region that
// announces progress, so the spoken step name can never drift from the visible one.
const TITLES: Record<StepId, string> = {
  name: 'Your name', essentials: 'Essential apps', try: 'Try one', ready: 'All set',
}

/** First-run welcome — a full-screen branded moment over the chat 3D dot-wave.
 *  A vertically-stacked stepper: each step expands when active and collapses to
 *  a green "done" row. The DotGlow focus follows the active row down the page.
 *  Shown only until a name is set (name is the only hard gate).
 *
 *  The middle step is the essential-apps step (OU-2): the flow's first real act,
 *  where a fresh install installs a model provider — required — plus optional
 *  search / speech / channel apps, and binds a chat model, all in-flow.
 *
 *  Each transition persists its resume point through `POST /api/onboarding/state`
 *  (`step`), and the essentials step persists which lanes it filled (`essentials`).
 *  Those writes are what OU-4's resume reads; the progress POST is fire-and-forget on
 *  purpose — a failed write must never block a user's first run. */
export function Onboarding() {
  const { setName } = useIdentity()
  const [step, setStep] = useState<StepId>('name')
  const [name, setNameDraft] = useState('')
  const [savedName, setSavedName] = useState('')
  const [readiness, setReadiness] = useState<OnboardingState | null>(null)
  const [modelDone, setModelDone] = useState<string>('')  // '' = not resolved, else summary
  const [triedSummary, setTriedSummary] = useState<string>('')

  // the active step's row drives the 3D glow focus (like the composer in chat)
  const rowRefs = {
    name: useRef<HTMLDivElement>(null), essentials: useRef<HTMLDivElement>(null),
    try: useRef<HTMLDivElement>(null), ready: useRef<HTMLDivElement>(null),
  }
  const activeRef = rowRefs[step]

  const stateOf = (id: StepId): StepState => {
    const si = ORDER.indexOf(step), ii = ORDER.indexOf(id)
    if (id === step) return 'active'
    return ii < si ? 'done' : 'upcoming'
  }

  /** Record first-run progress. Deliberately fire-and-forget: the flow's job is to get
   *  the user working, and a progress write that fails must cost them nothing. */
  const progress = useCallback((patch: OnboardingStatePatch) => {
    api.saveOnboardingState(patch).catch(() => { /* resume is a convenience, not a gate */ })
  }, [])

  // fetch readiness when entering the essentials step
  useEffect(() => {
    if (step === 'essentials' && !readiness) api.onboarding().then(setReadiness).catch(() => setReadiness({ needs_model: true, has_model_provider: false, has_chat_binding: false }))
  }, [step, readiness])

  function commitName() {
    const n = name.trim()
    if (!n) return
    setSavedName(n); setStep('essentials'); progress({ step: 'essentials' })
  }
  function leaveEssentials(summary: string) {
    setModelDone(summary); setStep('try'); progress({ step: 'first_success' })
  }
  /** The try-one step is the LAST persisted resume point (`STEPS` has no id between
   *  `first_success` and `done`), so moving to the recap writes nothing new — a user
   *  who reloads on the recap still resumes at the step they have not finished. */
  function leaveTryOne(summary: string) {
    setTriedSummary(summary); setStep('ready')
  }
  function finish() {
    progress({ step: 'done' })
    // commit identity LAST so the gate (`onboarded`) flips only on completion
    setName(savedName || 'Operator')
  }
  /** Leave the flow for a real destination — a try-one card's outcome link, or the
   *  Settings deep-link on its failure path. The route guard holds a non-onboarded
   *  user on `#/onboarding`, so the destination is handed to the guard and the name
   *  commit is what releases it; `exitTo.ts` explains why navigating instead races. */
  function exitTo(path: string) {
    setOnboardingExit(path)
    finish()
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
            {/* Announces step progress to assistive tech. The rows are not focusable and the step
                title is not in any focused control's accessible name, so without this a screen-reader
                user is never told they advanced (WCAG 4.1.3). Always mounted so the text change is
                observed; polite so it does not interrupt. */}
            <p role="status" aria-live="polite" className="sr-only">
              {`Step ${ORDER.indexOf(step) + 1} of ${ORDER.length}: ${TITLES[step]}`}
            </p>
            <StepRow ref={rowRefs.name} index={0} icon={User} title={TITLES.name}
              subtitle="How the system addresses you. Saved on the server, so it follows you across devices."
              state={stateOf('name')} doneSummary={savedName ? `${savedName}` : undefined}
              onActivate={() => setStep('name')}>
              <NameStep value={name} onChange={setNameDraft} onSubmit={commitName} />
            </StepRow>

            <StepRow ref={rowRefs.essentials} index={1} icon={Boxes} title={TITLES.essentials}
              subtitle="Install what the agent needs to work. A model provider is required; the rest are optional."
              state={stateOf('essentials')} doneSummary={modelDone || undefined}
              onActivate={() => setStep('essentials')}>
              {readiness
                ? <EssentialsStep readiness={readiness} onProgress={progress}
                    onDone={leaveEssentials}
                    onSkip={() => leaveEssentials('Set up later')} />
                : <div role="status" aria-busy="true" className="flex items-center py-2">
                    <LoadingStatus what="what's already set up" />
                    <Loader2 size={18} className="animate-spin text-on-surface-low" aria-hidden="true" />
                  </div>}
            </StepRow>

            <StepRow ref={rowRefs.try} index={2} icon={Rocket} title={TITLES.try}
              subtitle="Watch it actually do something. Each one runs for real — and none of them is required."
              state={stateOf('try')} doneSummary={triedSummary || undefined}
              onActivate={() => setStep('try')}>
              <TryOneStep onProgress={progress} onDone={leaveTryOne}
                onSkip={() => leaveTryOne('Skipped')} onExitTo={exitTo} />
            </StepRow>

            <StepRow ref={rowRefs.ready} index={3} icon={Sparkles} title={TITLES.ready}
              subtitle={`You're ready, ${firstNameOf(savedName)}.`}
              state={stateOf('ready')}>
              <ReadyStep name={savedName} modelSummary={modelDone} triedSummary={triedSummary} onFinish={finish} />
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
        aria-label="Your name"
        placeholder="Your name"
        className="min-w-0 flex-1 bg-transparent px-m text-on-surface text-[1.0625rem] placeholder:text-on-surface-low outline-none" />
      <motion.button whileTap={{ scale: 0.96 }} transition={spring.spatialFast} onClick={onSubmit} type="button"
        {...unavailableWhen(!value.trim(), 'Enter your name first')}
        className="inline-flex size-9 shrink-0 items-center justify-center rounded-pill disabled:opacity-40 aria-disabled:opacity-40 aria-disabled:cursor-not-allowed"
        style={{ background: 'var(--color-primary)', color: 'var(--color-on-primary)' }} aria-label="Continue">
        <ArrowRight size={17} />
      </motion.button>
    </div>
  )
}

/** Final step — recap + launch. */
function ReadyStep({ name, modelSummary, triedSummary, onFinish }: {
  name: string; modelSummary: string; triedSummary: string; onFinish: () => void
}) {
  const chatReady = modelSummary && modelSummary !== 'Set up later'
  const tried = triedSummary && triedSummary !== 'Skipped'
  return (
    <div className="flex flex-col gap-m">
      <motion.div className="flex flex-col gap-1.5"
        initial="initial" animate="animate" variants={{ animate: { transition: stagger(0.06) } }}>
        <motion.div variants={listItemEnter}><Recap ok label={`Hello, ${firstNameOf(name)}`} /></motion.div>
        <motion.div variants={listItemEnter}><Recap ok={!!chatReady} label={chatReady ? `Chat model: ${modelSummary}` : 'Chat model — set up later in Settings'} /></motion.div>
        <motion.div variants={listItemEnter}><Recap ok={!!tried} label={tried ? `First success: ${triedSummary}` : 'Nothing tried yet — the cards are in Discover'} /></motion.div>
      </motion.div>
      <motion.button whileTap={{ scale: 0.98 }} transition={spring.spatialFast} onClick={onFinish} type="button"
        className="inline-flex items-center justify-center gap-1.5 self-start rounded-pill px-5 h-11 text-[0.9375rem]"
        style={withWeight({ background: 'var(--color-primary)', color: 'var(--color-on-primary)' }, 500)}>
        Start using {APP_NAME} <ArrowRight size={17} />
      </motion.button>
    </div>
  )
}

function Recap({ ok, label }: { ok: boolean; label: string }) {
  return (
    <div className="flex items-center gap-2 text-[0.8125rem]">
      <span className="grid size-5 place-items-center rounded-full" style={{ background: ok ? 'var(--color-success)' : 'var(--color-surface-high)', color: ok ? 'var(--color-on-primary)' : 'var(--color-on-surface-low)' }}><Check size={12} /></span>
      <span className="text-on-surface-var">{label}</span>
    </div>
  )
}
