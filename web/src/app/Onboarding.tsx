import { useCallback, useEffect, useRef, useState } from 'react'
import { unavailableWhen } from '../ui/unavailable'
import { withWeight } from '../design/fontWeight'
import { motion } from 'framer-motion'
import { ArrowRight, User, Boxes, Rocket, Sparkles, Loader2, Check, Compass, Inbox, Waves, PanelLeft, FolderInput } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { ClawMark } from '../ui/ClawMark'
import { DotGlow } from '../ui/DotGlow'
import { LoadingStatus } from '../ui/ListScaffold'
import { Button } from '../ui/Button'
import { TextLink } from '../ui/TextLink'
import { Toggle } from '../ui/Toggle'
import { ScalarControl } from '../ui/TokenControls'
import { TOKENS, type ScalarToken } from '../design/tokenRegistry'
import { spring, stagger, listItemEnter } from '../design/motion'
import { useIdentity, firstNameOf, DEFAULT_USER_NAME } from './identity'
import { setNavMode } from './navDisclosure'
import { APP_NAME } from './config'
import { api, type OnboardingState, type OnboardingStatePatch } from '../lib/api'
import { StepRow, type StepState } from './onboarding/StepStack'
import { EssentialsStep } from './onboarding/EssentialsStep'
import { ImportStep } from './onboarding/ImportStep'
import { TryOneStep } from './onboarding/TryOneStep'
import { setOnboardingExit } from './onboarding/exitTo'
import { requestProductTour } from './onboarding/tourLaunch'

type StepId = 'name' | 'import' | 'essentials' | 'try' | 'ready'
const ORDER: StepId[] = ['name', 'import', 'essentials', 'try', 'ready']
// One source for each step's title — used by both the StepRow headings and the live region that
// announces progress, so the spoken step name can never drift from the visible one.
const TITLES: Record<StepId, string> = {
  name: 'Your name', import: 'Bring your setup over', essentials: 'Essential apps',
  try: 'Try one', ready: 'All set',
}

/** Where a re-entered flow picks up, from the persisted resume point (`STEPS` in
 *  `onboarding.py`, written by every transition below).
 *
 *  Two of the four stored values are NOT resume targets:
 *   • `name` is where the stack starts anyway;
 *   • `done` means a previous run finished. Re-entering after that is a fresh run
 *     ("Restart onboarding" in Settings → Account, or a finish whose identity write never
 *     landed), and dropping such a user on the recap would skip the very steps they asked
 *     to redo.
 *
 *  The name step itself always runs, because the name is deliberately NOT part of this
 *  state (OU-1: identity lives on the server and `onboarded` is derived from it, so storing
 *  a second copy here would create a second source of truth) and it is committed only at
 *  the end. Re-typing one field is the honest cost of that; fabricating a name for someone
 *  who typed one before the reload is not. Everything the earlier visit actually did —
 *  installed apps, bound model, completed cards — is what resume restores. */
function resumeTarget(state: OnboardingState): StepId | null {
  if (state.step === 'essentials') return 'essentials'
  if (state.step === 'first_success') return 'try'
  return null
}

/** The Motion group's Bounciness dial, straight out of the token registry — the done screen
 *  shows the REAL Settings → Design control, not a lookalike bound to the same variable. */
const BOUNCINESS = TOKENS.find((t) => t.varName === '--bounciness') as ScalarToken | undefined

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
  /** What the import step brought over, for its collapsed row. '' until it is left. */
  const [importSummary, setImportSummary] = useState<string>('')
  /** The step a re-entered flow jumps to once the name is in, or null for a first visit. */
  const [resume, setResume] = useState<StepId | null>(null)
  /** How many "try one" cards this home has ALREADY completed, per the persisted flags. */
  const [triedFloor, setTriedFloor] = useState(0)
  /** The done screen's rail choice, written once by `finish()` — see there. */
  const [showEverything, setShowEverything] = useState(false)

  // the active step's row drives the 3D glow focus (like the composer in chat)
  const rowRefs = {
    name: useRef<HTMLDivElement>(null), import: useRef<HTMLDivElement>(null),
    essentials: useRef<HTMLDivElement>(null),
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

  // ONE fetch, on mount. The same payload carries live model readiness (what the essentials
  // step needs) AND the persisted resume point (what a reloaded flow needs) — asking for it
  // when the essentials step opens would already be too late to know where to resume TO.
  useEffect(() => {
    let alive = true
    api.onboarding().then((s) => {
      if (!alive) return
      setReadiness(s)
      setResume(resumeTarget(s))
      // Past the essentials step, its collapsed row states what is already set up. The claim
      // is checked against `needs_model` — the LIVE resolution probe — so a run whose provider
      // was uninstalled since does not keep promising a model it no longer has.
      if (s.step === 'first_success') {
        setModelDone(s.needs_model ? 'Set up later' : s.essentials?.model || 'Ready to chat')
      }
      setTriedFloor(Object.values(s.first_success ?? {}).filter(Boolean).length)
    }).catch(() => {
      if (alive) setReadiness({ needs_model: true, has_model_provider: false, has_chat_binding: false })
    })
    return () => { alive = false }
  }, [])

  function commitName() {
    const n = name.trim()
    if (!n) return
    // Resume is honoured HERE and nowhere else: a fetch that lands after the user has already
    // moved on must never yank them forward mid-step. It also never walks the stored point
    // BACKWARDS — recording `essentials` for a run already at `first_success` would lose a
    // step of progress on the next reload.
    const target = resume ?? 'import'
    setSavedName(n); setStep(target)
    // The import step is deliberately NOT a stored resume point (`STEPS` has no id for it,
    // exactly as it has none between `first_success` and `done`). It does not need one: item
    // identity is a fingerprint and the importer keeps a ledger of what it wrote, so a run
    // that reloads there redoes an idempotent step and sees its own earlier work marked
    // `already imported`. Inventing a fifth stored value to save re-reading one screen would
    // buy nothing and add a value every older client would have to tolerate. So only a
    // RESUMED run — one whose earlier visit got past import — records a point here.
    if (target === 'essentials') progress({ step: 'essentials' })
    else if (target === 'try') progress({ step: 'first_success' })
  }
  /** Leave the import step. Recording `essentials` here is what makes a later reload resume
   *  PAST import rather than re-offering it: the point moves forward only once the user has
   *  actually finished with it (imported, or skipped it on purpose). */
  function leaveImport(summary: string) {
    setImportSummary(summary); setStep('essentials'); progress({ step: 'essentials' })
  }
  function leaveEssentials(summary: string) {
    setModelDone(summary); setStep('try'); progress({ step: 'first_success' })
  }
  /** The try-one step is the LAST persisted resume point (`STEPS` has no id between
   *  `first_success` and `done`), so moving to the recap writes nothing new — a user
   *  who reloads on the recap still resumes at the step they have not finished. */
  function leaveTryOne(summary: string) {
    // A resumed visit starts with idle cards: only the FLAGS survive a reload, not the
    // outcomes the cards rendered. So a user who succeeded, reloaded, then walked past the
    // step would be told "nothing tried yet" about a first success their own home recorded.
    setTriedSummary(summary === 'Skipped' && triedFloor > 0 ? `${triedFloor} of 3 tried` : summary)
    setStep('ready')
  }
  function finish() {
    progress({ step: 'done' })
    // The fresh-install marker for the rail (ONBOARDING-UX C4). This is the ONE act that can
    // only happen on a fresh install — it is what commits identity and flips `onboarded` — so
    // writing the disclosure record here is what tells the shell "onboarded under this
    // version, start on the starter rail". An install that has no record was onboarded before
    // this shipped and keeps its full rail; see app/navDisclosure.ts. Pins are left alone, so
    // restarting onboarding never takes away a surface you had already reached.
    //
    // The done screen's "Show every surface" switch resolves into this SAME write rather than
    // setting the mode itself: one act decides the rail, so the marker and the user's choice
    // can never disagree, and abandoning the flow leaves no record behind.
    setNavMode(showEverything ? 'expert' : 'starter')
    // commit identity LAST so the gate (`onboarded`) flips only on completion
    setName(savedName || DEFAULT_USER_NAME)
  }
  /** Leave setup unfinished, from any step. The flow is guidance, never a gate, and the two
   *  in-step escapes ("Set up later", "Skip this") only move to the NEXT step — a user who
   *  wants the app rather than the tour needs one door out.
   *
   *  It runs the same `finish()` as completing the flow, which is what makes the landing a
   *  WORKING dashboard: the terminal step is recorded, the rail marker is written, and
   *  committing identity is what releases the route guard. Skipping from the first step has no
   *  name to commit, so identity falls back to `DEFAULT_USER_NAME` — the same word the
   *  Settings → Account field uses — and the link says so, because a visible default beats a
   *  silent rename. */
  function skipSetup() {
    finish()
  }
  /** Finish, then walk the app (OU-10 / ruling b). It cannot render the tour itself: the
   *  very act that ends the flow — `finish()` committing identity — is what replaces this
   *  component with the app shell, so the request is left for the shell that is about to
   *  mount. `tourLaunch.ts` explains the seam; it is the same shape as `exitTo`. */
  function takeTour() {
    requestProductTour()
    finish()
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
            <StepRow ref={rowRefs.name} index={ORDER.indexOf('name')} icon={User} title={TITLES.name}
              subtitle="How the system addresses you. Saved on the server, so it follows you across devices."
              state={stateOf('name')} doneSummary={savedName ? `${savedName}` : undefined}
              onActivate={() => setStep('name')}>
              <NameStep value={name} onChange={setNameDraft} onSubmit={commitName} />
            </StepRow>

            {/* PEP-5 — adopt another local agent tool's setup. It sits BEFORE essentials
                because the work a user already did elsewhere is theirs before anything is
                installed here, and because none of what it writes (memories, MCP entries,
                skills) needs a model provider to land. */}
            <StepRow ref={rowRefs.import} index={ORDER.indexOf('import')} icon={FolderInput} title={TITLES.import}
              subtitle="Already use another local agent tool? Bring its instructions, MCP servers and skills across."
              state={stateOf('import')} doneSummary={importSummary || undefined}
              onActivate={() => setStep('import')}>
              <ImportStep onDone={leaveImport} onSkip={() => leaveImport('Skipped')} />
            </StepRow>

            <StepRow ref={rowRefs.essentials} index={ORDER.indexOf('essentials')} icon={Boxes} title={TITLES.essentials}
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

            <StepRow ref={rowRefs.try} index={ORDER.indexOf('try')} icon={Rocket} title={TITLES.try}
              subtitle="Watch it actually do something. Each one runs for real — and none of them is required."
              state={stateOf('try')} doneSummary={triedSummary || undefined}
              onActivate={() => setStep('try')}>
              <TryOneStep onProgress={progress} onDone={leaveTryOne}
                onSkip={() => leaveTryOne('Skipped')} onExitTo={exitTo} />
            </StepRow>

            <StepRow ref={rowRefs.ready} index={ORDER.indexOf('ready')} icon={Sparkles} title={TITLES.ready}
              subtitle={`You're ready, ${firstNameOf(savedName)}.`}
              state={stateOf('ready')}>
              <DoneScreen name={savedName} modelSummary={modelDone} triedSummary={triedSummary}
                showEverything={showEverything} onShowEverything={setShowEverything}
                onFinish={finish} onTakeTour={takeTour} onExitTo={exitTo} />
            </StepRow>
          </div>

          {/* The one door out, on every step but the last — where "Start using" IS the door.
              Guidance never gates: this is what makes "skip at any step" land somewhere real.

              `ink="emphasis"` because this link sits OUTSIDE the step card, on `--color-canvas`
              (measured off the node: rgb(240,244,248)). There, the base accent is **4.37:1** against a
              4.5 floor at 13px/400 — axe and ux-audit agreeing, the same number the canvas ground has
              carried since the accent-on-canvas family was named. The emphasis shade measures 6.0 in
              coral and passes in all 12 schemes. Its three siblings inside the card keep the base ink
              and pass at 4.83, because they are painted on `--color-surface`: the ground decides. */}
          {step !== 'ready' && (
            <div className="mt-l flex justify-center">
              <TextLink size="sm" ink="emphasis" onClick={skipSetup}>
                {step === 'name'
                  ? `Skip setup — start as ${DEFAULT_USER_NAME}, rename yourself in Settings`
                  : 'Skip setup and go to the dashboard'}
              </TextLink>
            </div>
          )}
        </motion.div>
      </div>
    </div>
  )
}

/** Step 1 — name (pill input with focus glow, Enter/arrow to advance). */
function NameStep({ value, onChange, onSubmit }: { value: string; onChange: (v: string) => void; onSubmit: () => void }) {
  return (
    <div className="flex items-center gap-s rounded-pill bg-surface-high px-s py-1.5 ring-1 ring-outline/40 focus-within:ring-2 focus-within:ring-inset focus-within:ring-primary">
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

/** The done screen — a recap of what this run actually did, then the three things worth
 *  knowing on day one, each with its real control rather than a sentence about one:
 *
 *   1. **the Inbox** is where work comes back to you (and you can land there instead);
 *   2. **Bounciness** — the live Settings → Design dial, so the app's feel reads as yours to
 *      set from the first minute rather than a taste you have to live with;
 *   3. **Show every surface** — the starter sidebar is a starting point, not a limit. The
 *      switch states intent; `finish()` performs the single write (see there).
 *
 *  It teaches by handing over controls, which is why the dial and the switch are the SAME
 *  objects Settings owns — a copy here would be a second mechanism to keep in step.
 *
 *  It is also where the product tour starts (OU-10). The tour sits BESIDE "Start using"
 *  rather than replacing it: the recap above already hands over three controls, and a
 *  first-run screen whose only exit is a guided walk is a gate wearing an offer. Both
 *  buttons finish the flow; one of them then walks the app. */
function DoneScreen({ name, modelSummary, triedSummary, showEverything, onShowEverything, onFinish, onTakeTour, onExitTo }: {
  name: string; modelSummary: string; triedSummary: string
  showEverything: boolean
  onShowEverything: (v: boolean) => void
  onFinish: () => void
  onTakeTour: () => void
  onExitTo: (path: string) => void
}) {
  const chatReady = modelSummary && modelSummary !== 'Set up later'
  const tried = triedSummary && triedSummary !== 'Skipped'
  return (
    <div className="flex flex-col gap-l">
      <motion.div className="flex flex-col gap-1.5"
        initial="initial" animate="animate" variants={{ animate: { transition: stagger(0.06) } }}>
        <motion.div variants={listItemEnter}><Recap ok label={`Hello, ${firstNameOf(name)}`} /></motion.div>
        <motion.div variants={listItemEnter}><Recap ok={!!chatReady} label={chatReady ? `Chat model: ${modelSummary}` : 'Chat model — set up later in Settings'} /></motion.div>
        <motion.div variants={listItemEnter}><Recap ok={!!tried} label={tried ? `First success: ${triedSummary}` : 'Nothing tried yet — the cards are in Discover'} /></motion.div>
      </motion.div>

      <div className="flex flex-col gap-s">
        <p data-type="label-s" className="text-on-surface-low">Three things to know</p>
        <Pointer icon={Inbox} title="Work comes back to you in the Inbox"
          body="Approvals, reminders and finished runs queue up there instead of chasing you across the app.">
          {/* `Pointer` paints `bg-surface-high`, where the base accent is the WORST of the four grounds:
              4.26:1 in coral at this 13px size, failing in 10 of 12 schemes. Emphasis measures 5.86.
              Not driven — the `ready` step needs a completed flow — but the ground is declared on the
              parent rather than assumed, and the same computation reproduces the driven 4.37 exactly on
              the skip link below. */}
          <TextLink size="sm" ink="emphasis" onClick={() => onExitTo('inbox')}>Open the Inbox instead</TextLink>
        </Pointer>
        <Pointer icon={Waves} title="How much the interface moves is a dial"
          body="Every animation scales with it — all the way down to none. This is the real control from Settings → Design.">
          {BOUNCINESS && <ScalarControl token={BOUNCINESS} />}
        </Pointer>
        <Pointer icon={PanelLeft} title="The sidebar starts short and grows"
          body={showEverything
            ? 'It will list every destination from the start. You can shorten it again in Settings → Design.'
            : 'Five essentials now; any other surface joins it the first time you open one. Nothing is locked away.'}>
          {/* The switch's own words, visible: its accessible name is "Show every surface" and a
              sighted user gets the same phrase rather than a bare toggle under a paragraph. */}
          <div className="flex items-center gap-2">
            <Toggle on={showEverything} onChange={onShowEverything} label="Show every surface" />
            <span className="text-on-surface-var text-[0.8125rem]">Show every surface</span>
          </div>
        </Pointer>
      </div>

      <div className="flex flex-wrap items-center gap-s">
        <motion.button whileTap={{ scale: 0.98 }} transition={spring.spatialFast} onClick={onFinish} type="button"
          className="inline-flex items-center justify-center gap-1.5 rounded-pill px-5 h-11 text-[0.9375rem]"
          style={withWeight({ background: 'var(--color-primary)', color: 'var(--color-on-primary)' }, 500)}>
          Start using {APP_NAME} <ArrowRight size={17} />
        </motion.button>
        {/* The tour, offered rather than imposed — it finishes setup either way, and every
            stop is skippable once it starts (Escape exits from any of them). */}
        <Button variant="secondary" size="lg" onClick={onTakeTour}>
          <Compass size={17} /> Take the quick tour
        </Button>
      </div>
    </div>
  )
}

/** One done-screen pointer: an icon, a claim, a line of why, and the control it is about. */
function Pointer({ icon: Icon, title, body, children }: {
  icon: LucideIcon; title: string; body: string; children: React.ReactNode
}) {
  return (
    <div className="flex items-start gap-2 rounded-lg bg-surface-high p-3">
      <span className="mt-0.5 inline-flex size-7 shrink-0 items-center justify-center rounded-lg"
        style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' }}>
        <Icon size={15} className="text-primary" aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-on-surface text-[0.8125rem]" style={withWeight({}, 600)}>{title}</p>
        <p className="mt-0.5 text-on-surface-low text-[0.8125rem]">{body}</p>
        <div className="mt-1.5">{children}</div>
      </div>
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
