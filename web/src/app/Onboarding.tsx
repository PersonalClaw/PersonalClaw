import { useCallback, useEffect, useRef, useState } from 'react'
import { withWeight } from '../design/fontWeight'
import { motion } from 'framer-motion'
import { ArrowLeft, User, Boxes, Rocket, Sparkles, Loader2, Check, Compass, Inbox, Waves, PanelLeft, FolderInput, RefreshCw } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { ClawMark } from '../ui/ClawMark'
import { DotGlow } from '../ui/DotGlow'
import { FormFooter } from '../ui/FormFooter'
import { LoadError, LoadingStatus } from '../ui/ListScaffold'
import { Button } from '../ui/Button'
import { TextLink } from '../ui/TextLink'
import { Toggle } from '../ui/Toggle'
import { ScalarControl } from '../ui/TokenControls'
import { TOKENS, type ScalarToken } from '../design/tokenRegistry'
import { spring, stagger, listItemEnter, prefersReducedMotion } from '../design/motion'
import { useIdentity, firstNameOf, suggestHandle, DEFAULT_USER_NAME } from './identity'
import { readNavDisclosure, setNavMode } from './navDisclosure'
import { APP_NAME } from './config'
import { notify } from './appSdk'
import { api, type OnboardingStatePatch } from '../lib/api'
import { chatModelSummary } from './onboarding/chatModelSummary'
import { checkChatModel } from './onboarding/checkChatModel'
import { StepRow, type StepState } from './onboarding/StepStack'
import { StepActions, StepActionsSlot } from './onboarding/StepActions'
import { EssentialsStep } from './onboarding/EssentialsStep'
import { ImportStep } from './onboarding/ImportStep'
import { TryOneStep } from './onboarding/TryOneStep'
import { setOnboardingExit } from './onboarding/exitTo'
import { requestProductTour } from './onboarding/tourLaunch'
import {
  ORDER as STEP_ORDER, TITLES as STEP_TITLES, SLUGS, STORED, type StepId,
  furthestOf, isUnlocked, nextOf, pathOf, previousOf, resolveStep, stepFromSlug, stepFromStored,
} from './onboarding/steps'

// Re-stated as local bindings because the live region and every row heading read them by these
// names; `onboarding/steps.ts` is the single definition. `stepProgressAnnounced.test.ts` rails the
// pair together so a title can never be hardcoded onto a row.
const ORDER = STEP_ORDER
const TITLES = STEP_TITLES

/** What a step actually produced for this user. Deliberately NOT derivable from position: the
 *  previous flow read `ORDER.indexOf(step)` and rendered every earlier row as complete, so a
 *  resumed run put a green check on "Bring your setup over" for someone who had never seen that
 *  screen. An outcome exists only where the user, or the server's own record, supplies one. */
type Outcome = 'done' | 'skipped'
interface StepRecord { outcome: Outcome; summary: string }

/** The name and handle the user has typed but not yet committed.
 *
 *  **Why it is persisted.** Identity is committed once, at the end, by `finish()` — that write is
 *  what flips `onboarded` and releases the route guard. So mid-flow the typed name lives only in
 *  React state, and a reload lost it: measured on a fresh home with the server reporting
 *  `step: "essentials"`, a refresh put the user back on "Step 1 of 5: Your name" with both fields
 *  empty. Resuming to the right step but silently forgetting the name would be worse, not better —
 *  `finish()` would then commit the fallback name for someone who never declined to give one.
 *
 *  **Why `sessionStorage`.** It is the exact scope of the problem: a refresh keeps it, a new tab
 *  does not. Persisting it any wider would resurrect one person's half-typed name into someone
 *  else's fresh first run, which is the failure `exitTo.ts` warns about for its own module state.
 *  It is NOT a second source of truth for identity — nothing reads it but this flow, and only
 *  `setName` writes identity. */
const DRAFT_KEY = 'onboarding-draft'

interface Draft {
  name: string
  handle: string
  /** Whether the operator has edited the handle field — INCLUDING clearing it. See `NameStep`. */
  handleTouched: boolean
  /** Whether the name step has been passed at least once. This is the flow's one hard gate. */
  passed: boolean
}

/** Read the draft, falling back to what this install already stores.
 *
 *  `passed` is honoured only alongside a non-empty name, so a truncated or hand-edited draft can
 *  never unlock the rest of the flow for a run that has no name to commit. */
function loadDraft(storedName: string, storedHandle: string): Draft {
  const fresh: Draft = {
    name: storedName, handle: storedHandle, handleTouched: storedHandle.length > 0, passed: false,
  }
  try {
    const raw = sessionStorage.getItem(DRAFT_KEY)
    if (!raw) return fresh
    const d = JSON.parse(raw) as Partial<Draft>
    const name = typeof d.name === 'string' ? d.name : fresh.name
    return {
      name,
      handle: typeof d.handle === 'string' ? d.handle : fresh.handle,
      handleTouched: typeof d.handleTouched === 'boolean' ? d.handleTouched : fresh.handleTouched,
      passed: d.passed === true && name.trim().length > 0,
    }
  } catch {
    return fresh
  }
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
export function Onboarding({ sub, navigate, deferred, onFinished }: {
  /** The hash sub-path — the step's slug. `''` on `#/onboarding`, which resolves to wherever
   *  this run belongs. */
  sub: string
  /** The shell's router. Steps are real history entries, which is what makes the browser's Back
   *  and Forward buttons walk the flow instead of fighting the route guard. */
  navigate: (path: string, opts?: { replace?: boolean }) => void
  /** The route the guard deferred to get the user here, or `''`. A fresh home pulls EVERY route to
   *  `#/onboarding`, and a silent yank reads as the app swallowing the click — so the flow says the
   *  redirect is a deferral, and `finish()` keeps the promise by landing them there.
   *
   *  A PROP, not a read of `exitTo`'s slot. Reading it here was the obvious shape and it never
   *  rendered: `App` returns this component during its FIRST render, and the guard that records the
   *  destination is an effect, which runs after. The guard owns the slot; it passes down what it
   *  put there. */
  deferred: string
  /** Called once the flow is over, whichever door was used. On a deliberate re-run this is what
   *  withdraws the request so the guard can put the user back; on a first run the `onboarded`
   *  flip does that and this is a no-op. `App` owns the move either way — one navigator. */
  onFinished: () => void
}) {
  const { name: storedName, setName, username: storedHandle } = useIdentity()
  /** Seeded from what this install already stores, then from a live draft.
   *
   *  The STORED name matters for a deliberate re-run: "Run setup again" no longer clears identity,
   *  so the field offers back the name this install has rather than an empty box that would commit
   *  a rename. The stored HANDLE is seeded as already-touched for the same reason — the suggestion
   *  may not overwrite a choice already made.
   *
   *  Reading the context values as INITIAL state is safe because `App` renders a spinner until
   *  `loaded`, so the identity fetch has resolved before this component first mounts. */
  const [draft, setDraft] = useState<Draft>(() => loadDraft(storedName, storedHandle))
  /** A deliberate re-run ("Run setup again"): this install already HAS a name, so it is past
   *  first run. Decided once, at mount — `App` renders the flow only after identity has loaded,
   *  and the only thing that changes the stored name mid-flow is `finish()` itself.
   *
   *  🔴 It decides what skipping may write. Settings → Account promises "your name, handle and
   *  everything already set up are kept", and the flow pre-fills both — but "Skip setup for now"
   *  committed `DEFAULT_USER_NAME` with an empty handle regardless, because the committed identity
   *  is derived from THIS run's name step, which a skip never passes. Measured: a re-run skipped
   *  from step 1 PUT `{"user_name":"Operator","username":""}` and Home greeted "Good morning,
   *  Operator". */
  const [rerun] = useState(() => storedName.trim().length > 0)
  /** The furthest step this run has stood on. Raised by every forward move and by the persisted
   *  high-water mark; never lowered, so going back cannot cost the user the steps they reached.
   *
   *  **Seeded from the address bar**, and that is load-bearing rather than an optimisation. The
   *  server's mark arrives asynchronously, so a reload of `#/onboarding/essentials` that started
   *  from `'name'` would resolve to the name step and the URL-correction effect below would
   *  REWRITE the very address that was asking to resume — measured: the announcement stayed on
   *  "Step 1 of 5" even after the mark landed, because `sub` had already been overwritten.
   *
   *  Seeding is also the more trustworthy record of the two. The flow only ever pushes a step it
   *  had already unlocked, so a sub-path naming one is this session's own evidence that the run
   *  stood there — whereas the server write is deliberately fire-and-forget, so a failed POST would
   *  otherwise cost the user their position. Gated on the draft having passed the name step, so an
   *  address alone can never open the flow for a run with no name to commit. */
  const [reached, setReached] = useState<StepId>(() => {
    const fromUrl = stepFromSlug(sub)
    return fromUrl && loadDraft(storedName, storedHandle).passed ? fromUrl : 'name'
  })
  const [readiness, setReadiness] = useState<import('../lib/api').OnboardingState | null>(null)
  /** `GET /api/onboarding` FAILED — the reason, or `''`. Distinct from `readiness === null`
   *  (still loading): a failed read is reported as one, never turned into a guess. */
  const [readError, setReadError] = useState('')
  const [readAttempt, setReadAttempt] = useState(0)
  /** Where the proof behind a re-entered run's chat-model line stands: `pending` while
   *  `checkChatModel` is out (the line claims nothing yet), `unknown` when the check could not
   *  run, `done` once there is an answer — a seeded record when it said ok, none otherwise. */
  const [modelSeed, setModelSeed] = useState<'pending' | 'done' | 'unknown'>('pending')
  /** What each step produced, for its collapsed row and the recap. A step absent from this map has
   *  NO recorded outcome and must render as not-yet-done — never as complete. */
  const [records, setRecords] = useState<Partial<Record<StepId, StepRecord>>>({})
  /** The done screen's rail choice, written once by `finish()` — see there. */
  // A re-run starts the switch at the rail this install already has, so finishing or skipping
  // without touching it keeps that rail. A first run starts it off: no record there resolves to
  // `expert` (the marker for an install onboarded before disclosure shipped), which is not a
  // choice this user made.
  const [showEverything, setShowEverything] = useState(() => rerun && readNavDisclosure().mode === 'expert')

  const namePassed = draft.passed && draft.name.trim().length > 0
  /** Which step is on screen: what the URL asks for, reconciled with what this run has reached.
   *  The URL is the position — that is what makes refresh, Back, Forward and a deep link all land
   *  in the same place — and `resolveStep` is total, so there is no input that renders nothing. */
  const step = resolveStep(stepFromSlug(sub), reached, namePassed)

  /** The committed halves of identity, DERIVED from the draft rather than captured into their own
   *  state. An untouched handle field shows a suggestion tracking the name as it is typed, so what
   *  lands is what the operator saw and accepted; and because the field is only reachable ON the
   *  name step, returning to it and editing re-commits — which is exactly what going back should
   *  mean. Two fewer state variables that could disagree with the fields. */
  const savedName = namePassed ? draft.name.trim() : ''
  const savedHandle = namePassed
    ? (draft.handleTouched ? draft.handle.trim() : suggestHandle(draft.name.trim()))
    : ''

  // the active step's row drives the 3D glow focus (like the composer in chat)
  // `HTMLLIElement` since the rows became real list items — tsc caught the mismatch, which is the
  // useful half of a typed ref: the glow tracks whatever element the row actually renders.
  const rowRefs = {
    name: useRef<HTMLLIElement>(null), import: useRef<HTMLLIElement>(null),
    essentials: useRef<HTMLLIElement>(null),
    try: useRef<HTMLLIElement>(null), ready: useRef<HTMLLIElement>(null),
  }
  const activeRef = rowRefs[step]

  /** The navigation bar's slot for the active step's own actions (`StepActions` portals into it).
   *  State rather than a ref, so the steps re-render into it once it exists. */
  const [actionSlot, setActionSlot] = useState<HTMLElement | null>(null)
  /** The one scroller. The bar is sticky at its foot, so it covers whatever scrolls beneath it —
   *  and `scroll-padding-bottom` is what the browser leaves clear when it scrolls a control into
   *  view (Tab, `focus()`, `scrollIntoView`). Kept equal to the bar's height, which grows when its
   *  buttons wrap at phone width, so a control a keyboard user moves to never lands behind the bar
   *  (WCAG 2.4.11). */
  const scrollRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const scroller = scrollRef.current
    const bar = scroller?.querySelector<HTMLElement>('[data-form-footer]')
    if (!scroller || !bar || typeof ResizeObserver === 'undefined') return
    const fit = () => { scroller.style.scrollPaddingBottom = `${bar.offsetHeight}px` }
    fit()
    const watch = new ResizeObserver(fit)
    watch.observe(bar)
    return () => watch.disconnect()
  }, [])

  const stateOf = (id: StepId): StepState => {
    if (id === step) return 'active'
    // The name step's outcome IS the gate: it has no summary of its own beyond the identity it
    // captured, so `namePassed` is the whole record.
    if (id === 'name') return namePassed ? 'done' : 'upcoming'
    const rec = records[id]
    return rec ? rec.outcome : 'upcoming'
  }

  /** Record first-run progress. Deliberately fire-and-forget: the flow's job is to get
   *  the user working, and a progress write that fails must cost them nothing. */
  const progress = useCallback((patch: OnboardingStatePatch) => {
    api.saveOnboardingState(patch).catch(() => { /* resume is a convenience, not a gate */ })
  }, [])

  // Persist the draft on every change, so a refresh mid-flow keeps what was typed.
  useEffect(() => {
    try { sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft)) } catch { /* storage may be denied */ }
  }, [draft])

  // Keep the address bar agreeing with what is rendered. `replace` because a corrected URL is not
  // a place the user navigated to — pushing it would make Back return them to the step the clamp
  // just refused, and each Back press would bounce them forward again (#306's lesson, same shape).
  useEffect(() => {
    if (sub !== SLUGS[step]) navigate(pathOf(step), { replace: true })
  }, [sub, step, navigate])

  /** 🔴 ARRIVING AT A STEP MOVES THE VIEW TO IT. The FOCUS is `StepStack`'s to move, not this
   *  effect's — see the trap below, which is why.
   *
   *  The view did not move: the scroll offset simply CARRIED OVER (`377 → 377` across step 3 → 4),
   *  so the new step opened at whatever offset the old one had been scrolled to. Clicking "Set up
   *  later" could look like nothing happened at all, which is the largest single contributor to the
   *  flow reading as unnavigable — the live region announced the change to assistive tech while the
   *  screen showed no evidence of it.
   *
   *  🪤 THIS EFFECT ALSO CALLED `row.focus()`, AND IT SILENTLY BEAT THE BETTER FIX. `StepRow` already
   *  moves focus to the newly-active step's `<h2>`, and React runs a CHILD's effects before its
   *  parent's — so on every advance both fired, in that order, and the row's focus landed second and
   *  won. Measured in the browser rail (`web/e2e/onboardingGeometry.spec.ts`) as two `focusin` events
   *  per advance — `h2 :: "Bring your setup over"` then `li :: "Bring your setup overStep 2 of
   *  5Already use another local agent tool?…"` — leaving `document.activeElement` on the `<li>`.
   *  Neither fix was wrong on its own; together, effect ORDER decided it rather than intent.
   *
   *  The row is also the worse destination on the merits. It is a `<li>` with no role and no
   *  accessible name, so what a screen reader reads on arrival is its whole text content — title,
   *  position, subtitle AND the entire expanded step body — where the heading announces exactly
   *  "heading level 2, Bring your setup over". So there is ONE focus mover, it lives beside the
   *  heading it targets, and this effect moves the view only.
   *
   *  Skipped on the FIRST render, deliberately: step 1 is active from the first paint, so there is no
   *  arrival to show, and scrolling during the commit that applies the name field's `autoFocus` would
   *  move the page out from under the caret. Only a CHANGE of step moves anything. */
  const arrivedRef = useRef<StepId | null>(null)
  useEffect(() => {
    const previous = arrivedRef.current
    arrivedRef.current = step
    if (previous === null || previous === step) return
    const row = rowRefs[step].current
    if (!row) return
    // Guarded the way `ui/SpotlightTour` guards it — jsdom has no implementation — and honouring the
    // reduced-motion preference for the same reason that surface does.
    if (typeof row.scrollIntoView === 'function') {
      row.scrollIntoView({ block: 'start', behavior: prefersReducedMotion() ? 'auto' : 'smooth' })
    }
  }, [step])  // eslint-disable-line react-hooks/exhaustive-deps -- rowRefs is a stable ref bag

  // ONE fetch, on mount — and again only on an explicit retry after it FAILED. The same payload
  // carries live model readiness (what the essentials step needs) AND the persisted high-water
  // mark (what a reloaded flow needs) — asking for it when the essentials step opens would
  // already be too late to know where to resume TO.
  useEffect(() => {
    let alive = true
    setReadError(''); setModelSeed('pending')
    api.onboarding().then((s) => {
      if (!alive) return
      setReadiness(s)
      setReached((r) => furthestOf(r, stepFromStored(s.step) ?? 'name'))
      // Seed ONLY outcomes the stored state PROVES. A step this home reached but recorded nothing
      // about stays absent, so it renders as not-yet-done and stays reachable — rather than
      // wearing a green check for a screen the user may never have seen. Anything this session
      // already recorded wins: the user may have just done the step.
      const tried = Object.values(s.first_success ?? {}).filter(Boolean).length
      if (tried > 0) setRecords((r) => ({ try: { outcome: 'done', summary: `${tried} of 3 tried` }, ...r }))
      if (s.needs_model) { setModelSeed('done'); return }
      // 🔴 `needs_model: false` IS A CLAIM, NOT PROOF. It is the no-instantiate readiness probe,
      // which can see a model that is not downloaded but can never see a provider that does not
      // ANSWER — an Ollama entry saved at an address nothing listens on reads "ready" to it. It
      // used to seed this record directly, so a reload said "Ready — using a configured
      // provider" in the collapsed step-3 row and the recap, and a later "Set up later" kept it
      // (a skip never erases a done step). The record is now the step's own proof,
      // `checkChatModel`: it builds what chat builds, then asks the provider that answers
      // whether it does.
      //
      // 🔴 THE SUMMARY IS THE LIVE BINDING, NOT `essentials.model`. That persisted field is the
      // **app** the lane installed (`ollama-models`), and this summary is rendered under the
      // words "Chat model" by both consumers below — the collapsed step-3 row and the recap — so
      // a re-entered first run told the user their chat model was an app name (#3528). The
      // verdict names the model from `active_models.json`, or '' for the implicit fallback, and
      // says whether it is the small bundled model; `chatModelSummary` is the SAME function the
      // step's own summary uses, so a first pass and a re-entered pass agree.
      void checkChatModel().then((v) => {
        if (!alive) return
        if (v.kind === 'ok') {
          setRecords((r) => ({ essentials: { outcome: 'done', summary: chatModelSummary(v.model, v.floor) }, ...r }))
        }
        setModelSeed(v.kind === 'unknown' ? 'unknown' : 'done')
      })
    }).catch((e: unknown) => {
      // 🔴 A FAILED READ IS NOT "NEEDS A MODEL". This used to fabricate
      // `{needs_model: true, …}` here, so an unreachable gateway put the user on the model
      // step as if nothing were set up, and the recap said "Chat model — set up later in
      // Settings" — both claims about a home that had not been read at all. The failure is
      // kept as what it is, the step says it could not read the state and offers the retry,
      // and the recap says the same.
      if (alive) setReadError(e instanceof Error && e.message ? e.message : String(e ?? 'the request failed'))
    })
    return () => { alive = false }
  }, [readAttempt])

  /** Move to *id*, pushing a history entry so Back returns to the step before it.
   *
   *  A FORWARD move raises the high-water mark and records it before navigating — both halves
   *  matter. Raising it first is what lets `resolveStep` admit the new step on the very next
   *  render (React commits the state update before the browser's `hashchange` task runs), and
   *  recording it is what a later reload resumes from. Going BACK writes nothing, so the mark
   *  keeps naming where the run actually got to. */
  const goTo = useCallback((id: StepId) => {
    const mark = furthestOf(reached, id)
    if (mark !== reached) {
      setReached(mark)
      progress({ step: STORED[mark] })
    }
    navigate(pathOf(id))
  }, [reached, navigate, progress])

  /** Leave a step, recording what it produced, and open the next one.
   *
   *  A skip never erases work already done: the essentials and try steps carry server-side
   *  evidence, so a user who succeeded, reloaded, then walked past the step keeps the outcome
   *  their own home recorded instead of being told "nothing tried yet". */
  const leave = useCallback((id: StepId, outcome: Outcome, summary: string) => {
    setRecords((r) => (outcome === 'skipped' && r[id]?.outcome === 'done' ? r : { ...r, [id]: { outcome, summary } }))
    const next = nextOf(id)
    if (next) goTo(next)
  }, [goTo])

  /** A row's "take me to this step" handler, or `undefined` when the flow has not unlocked it.
   *
   *  Withholding the handler is what makes `StepRow` render a quiet row instead of a button, so the
   *  affordance and the permission are one decision. The previous rule was "a row is clickable iff
   *  it is DONE", which is why going back broke going forward: rows ahead of the current step were
   *  never clickable even when the run had already stood on them, so the only route forward was to
   *  redo every step. Unlocked means "at or behind the high-water mark", which covers both a step
   *  already finished and one a resume jumped over. */
  const activate = (id: StepId) => (isUnlocked(id, reached, namePassed) ? () => goTo(id) : undefined)

  function commitName() {
    const n = draft.name.trim()
    if (!n) return
    setDraft({ ...draft, passed: true })
    // Forward to the next step, or straight to where an earlier visit got to — whichever is
    // further. The steps that get jumped over are left with NO recorded outcome, so they show as
    // unfinished and stay one click away rather than being stamped complete.
    goTo(furthestOf(nextOf('name') as StepId, reached))
  }
  /** End the flow — completing it, skipping it, or leaving for a destination.
   *
   *  🔴 **IDENTITY IS COMMITTED FIRST, AND A REFUSAL KEEPS THE USER HERE.** This used to fire the
   *  progress write, the rail marker and the draft deletion, then call `setName` without awaiting
   *  it — and `setName` swallowed its own failure. Measured on a fresh home with the gateway
   *  killed: clicking the flow's one advertised exit returned the user to step 1 with the typed
   *  name erased and `alerts: []` — no toast, no error, nothing that advanced. The single worst
   *  dead end on the screen, reachable from any failure of that PUT.
   *
   *  So the commit is the gate: nothing that CLAIMS the flow is over is written until the write
   *  that ends it has landed. On a refusal the user stays on the step they were on, with their
   *  draft, and is told — through the app toast, the mechanism `DoneScreen.toggleAutoUpdate` below
   *  already uses for exactly this. */
  async function finish() {
    // A re-run whose name step was never passed has nothing new to commit: the saved name and
    // handle ARE the user's answer, and overwriting them with the first-run fallback is the wipe
    // described on `rerun`. It also needs no commit to leave — `onFinished()` withdrawing the
    // re-run request is what releases the guard for an onboarded user.
    if (namePassed || !rerun) {
      try {
        // The handle rides along in the SAME write — one act commits identity, so the name and the
        // handle can never disagree about whether first run happened. It is passed explicitly
        // (rather than derived server-side from `user_name`) because only a surface that ASKED may
        // send one: see `setName` in app/identity.
        //
        // `savedHandle` is deliberately NOT defaulted the way the name is. Skipping setup from the
        // first step falls back to DEFAULT_USER_NAME for the name because the route guard needs a
        // non-empty one, but there is no equivalent need for a handle and `slugify_username` never
        // invents a fallback — so a skipped run commits '' and the records it writes stay
        // unattributed, which is the shipped promise.
        await setName(savedName || DEFAULT_USER_NAME, savedHandle)
      } catch (e: unknown) {
        let msg = e instanceof Error ? e.message : 'the request failed'
        try { msg = JSON.parse(msg).error || msg } catch { /* raw text */ }
        // 🪤 A `fetch` that never completes rejects with a TypeError whose message is the browser's
        // own "Failed to fetch" — measured verbatim on a killed gateway. `lib/errText` cannot help:
        // it turns a failed RESPONSE into a sentence, and here there is no response. A first-run user
        // reading "Failed to fetch" learns nothing they can act on; the local gateway being down is
        // both the likeliest cause and the one they can actually fix.
        if (e instanceof TypeError) msg = `${APP_NAME} didn't respond — check it is still running`
        notify(`Couldn't save your name: ${msg}. Setup is still open — nothing was lost.`, 'error')
        return
      }
    }
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
    // The draft is dropped only now that identity is committed — the one act that makes it
    // redundant. Dropping it before the write could fail was half of the erased-input defect
    // above; leaving it behind afterwards would re-offer a stale name to the NEXT run.
    try { sessionStorage.removeItem(DRAFT_KEY) } catch { /* storage may be denied */ }
    // Withdraw a deliberate re-run request, so the guard — the single owner of where this flow
    // sends a user — moves them out. On a first run the `onboarded` flip does that instead and
    // this changes nothing.
    onFinished()
  }
  /** Leave setup unfinished, from any step. The flow is guidance, never a gate, and the two
   *  in-step escapes ("Set up later", "Skip this") only move to the NEXT step — a user who
   *  wants the app rather than the tour needs one door out.
   *
   *  It runs the same `finish()` as completing the flow, which is what makes the landing a
   *  WORKING dashboard: the terminal step is recorded, the rail marker is written, and
   *  committing identity is what releases the route guard. Skipping a FIRST run from its first
   *  step has no name to commit, so identity falls back to `DEFAULT_USER_NAME` — the same word the
   *  Settings → Account field uses — and the link says so, because a visible default beats a
   *  silent rename. Skipping a RE-RUN commits nothing: the saved name and handle stay (`rerun`). */
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
    <div className="fixed inset-0 z-[var(--z-modal)] overflow-hidden" style={{ background: 'var(--color-canvas)' }}>
      <DotGlow intensity={1.15} composerRef={activeRef} />

      {/* 🔴 THE SCROLL BOX IS A PLAIN BLOCK, AND THE CENTRING LIVES ON A BOX INSIDE IT.
          Both halves are load-bearing; an earlier shape had them on the same element
          (`flex h-full items-center justify-center overflow-y-auto`) and lost content at both
          edges of a short viewport.

          · `items-center` on a scroller CLIPS THE START EDGE UNRECOVERABLY. When the flex item is
            taller than the box, `align-items: center` distributes the overflow to BOTH ends, and
            the part above the start edge cannot be scrolled to: `scrollTop` is clamped at 0 and
            `scrollHeight` does not count it. Measured on a fresh home at 1280×700, step 2
            ("Bring your setup over", the longest step): at `scrollTop: 0` — already the top of the
            range — the panel's top sat at **-96.5px** and the `<h1>` at **-201.5px**, so "Welcome
            to PersonalClaw" was simply gone. Scrolling to the bottom took the h1 to -326.
          · `min-h-full` on the COLUMN inside it, with the steps centred in a `flex-1` box above the
            navigation bar, gives the same centred look while there is room and degrades to
            top-aligned + fully scrollable when there is not: `justify-content` has no spare space
            to distribute once the content grows, so nothing is pushed past the start edge.

          The column is also what the bar is sticky WITHIN — a sticky box moves only inside its
          parent, and this one spans the whole scroll height, so the bar sits at the foot of the
          screen on a short step and stays there while a long one scrolls beneath it.

          `design/onboardingScrollable.test.tsx` is the rail; it also explains why jsdom cannot
          measure this and what it asserts instead. */}
      {/* Two widths, both centred on one axis: the steps read at 540px, and the navigation bar at
          the foot of the column may run to 760px. Back, the door out, a step's own alternative and
          its main action are four labelled buttons — "Back to bring your setup over", "Skip the rest
          of setup", "Set up later", "Continue" measure 727px together — so inside 540px the bar
          wrapped on every desktop step but the first, stranding the main action on a row of its
          own. On a phone both are the full width, and the bar wraps by design. */}
      <div ref={scrollRef} className="relative h-full overflow-y-auto px-l pt-3xl">
        <div className="mx-auto flex min-h-full w-full flex-col" style={{ maxWidth: 760 }}>
          <div className="mx-auto flex w-full flex-1 flex-col justify-center pb-2xl" style={{ maxWidth: 540 }}>
          <motion.div initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={spring.spatialSlow}
            className="relative w-full">
            {/* hero — ABOVE the stepper, IN FLOW.
                🪤 It used to be positioned out of flow (`absolute` against the panel's top edge),
                "so it doesn't affect the stepper's vertical centering; the STEPPER is what sits
                mid-screen". That bought the centring by putting the product's title outside the
                scrollable range: out of flow and above the flow origin, it could not be scrolled to
                at ANY viewport height once the panel filled the screen — it was the first thing
                lost in the measurement above. In flow, the hero and the stepper centre as one group
                (the stepper sits a little lower when there is room) and both scroll. */}
            <div className="mb-2xl flex flex-col items-center">
              <ClawMark size={52} animated blob />
              <h1 data-type="headline-m" className="mt-l text-on-surface text-center">{rerun ? 'Setup, again' : `Welcome to ${APP_NAME}`}</h1>
              <p className="mt-2 text-center text-on-surface-low text-[0.9375rem]" style={{ maxWidth: 360 }}>
                {rerun
                  ? 'Everything already set up is kept. Change what you like, and skip the rest.'
                  : 'Your self-hosted personal agent. A few moments to get set up.'}
              </p>
              {/* The deferred destination, said out loud. Without it the guard's redirect is
                  indistinguishable from the app losing the click — the user asked for a page and got
                  a different screen with no explanation. Naming the deferral makes it a promise, and
                  `finish()` keeps it by landing them there instead of on the dashboard. */}
              {deferred && (
                <p data-type="body-s" className="mt-s text-center text-on-surface-var" style={{ maxWidth: 360 }}>
                  Setup comes first. Finish or skip it and you&rsquo;ll go straight to the page you asked for.
                </p>
              )}
            </div>

            {/* vertical collapsing stepper — the centered focal element */}
            {/* Announces step progress to assistive tech (WCAG 4.1.3). Always mounted so the text
                change is observed; polite so it does not interrupt.
                🔑 WHAT IT CARRIES THAT THE FOCUS MOVE CANNOT. Advancing a step now moves focus to
                the new step's heading (`StepStack`), so "you have arrived somewhere" no longer needs
                a live region — but the heading it moves to is named for the step, not for its
                position, so it says "Bring your setup over" and never "Step 2 of 5". This region is
                the only place the POSITION is spoken, which is the half of the answer a first-run
                user actually wants.
                🪤 TWO EARLIER JUSTIFICATIONS DIED HERE, both by being true when written. First "the
                rows are not focusable" — false once a completed row's header became a real button.
                Then "a step CHANGE is not a focus change" — false once the heading took focus. Both
                would have argued for deleting a still-necessary region; the reason above is about
                what the region SAYS, which does not depend on how focus behaves. */}
            <p role="status" aria-live="polite" className="sr-only">
              {`Step ${ORDER.indexOf(step) + 1} of ${ORDER.length}: ${TITLES[step]}`}
            </p>
            {/* 🔑 A REAL <ol>. Five numbered steps were a stack of divs, so `aria-current="step"` on a
                row had no set to be current WITHIN, and a screen-reader user got no "list, 5 items" to
                orient by. The live region is deliberately OUTSIDE it: only `<li>` may be an `<ol>`
                child, and a `<p>` in there is invalid content an AT tree may drop — which would have
                silently removed the announcement this screen already relies on.
                Every step's own actions render in the navigation bar below, through this slot. */}
            <StepActionsSlot.Provider value={actionSlot}>
            <ol className="flex w-full list-none flex-col gap-2 p-0">
              <StepRow ref={rowRefs.name} index={ORDER.indexOf('name')} total={ORDER.length} icon={User} title={TITLES.name}
                /* NOT "Saved on the server, so it follows you across devices" — that is
                   `AccountPanel`'s sentence, where it is true because the panel writes on
                   change. HERE the write is deliberately the last thing the flow does
                   (`finish()`, see `commitName` above and OU-1's one-source-of-truth note),
                   so past-tense "Saved" was a claim about a write that had not happened, on
                   the product's very first screen. Measured on a fresh container: advancing
                   off this step issues NO write, `GET /api/onboarding` still answers
                   `step: "name"`, and a reload returned an empty field — after the screen had
                   already shown the name back as a completed step. Say WHEN the promise is
                   kept instead of implying it already was. */
                subtitle="How the system addresses you, plus the handle your records carry. Saved when you finish setup, so it then follows you across devices."
                state={stateOf('name')} doneSummary={savedName ? (savedHandle ? `${savedName} · @${savedHandle}` : savedName) : undefined}
                onActivate={activate('name')}>
                {/* An untouched handle field DISPLAYS the suggestion rather than storing it,
                    so it tracks the name as it is typed; the first edit (clearing included)
                    makes it the operator's and stops the tracking. */}
                <NameStep value={draft.name} onChange={(v) => setDraft({ ...draft, name: v })} onSubmit={commitName}
                  handle={draft.handleTouched ? draft.handle : suggestHandle(draft.name)}
                  onHandleChange={(v) => setDraft({ ...draft, handle: v, handleTouched: true })} />
              </StepRow>

              {/* PEP-5 — adopt another local agent tool's setup. It sits BEFORE essentials
                  because the work a user already did elsewhere is theirs before anything is
                  installed here, and because none of what it writes (memories, MCP entries,
                  skills) needs a model provider to land. */}
              <StepRow ref={rowRefs.import} index={ORDER.indexOf('import')} total={ORDER.length} icon={FolderInput} title={TITLES.import}
                subtitle="Already use another local agent tool? Bring its instructions, MCP servers and skills across."
                state={stateOf('import')} doneSummary={records.import?.summary}
                onActivate={activate('import')}>
                <ImportStep onDone={(s) => leave('import', 'done', s)} onSkip={() => leave('import', 'skipped', 'Skipped')} />
              </StepRow>

              <StepRow ref={rowRefs.essentials} index={ORDER.indexOf('essentials')} total={ORDER.length} icon={Boxes} title={TITLES.essentials}
                subtitle="Install what the agent needs to work. A model provider is required; the rest are optional."
                state={stateOf('essentials')} doneSummary={records.essentials?.summary}
                onActivate={activate('essentials')}>
                {readiness
                  ? <EssentialsStep readiness={readiness} onProgress={progress}
                      onDone={(s) => leave('essentials', 'done', s)}
                      onSkip={() => leave('essentials', 'skipped', 'Set up later')} />
                  : readError
                    // The step's lanes all start from this read (is a model already set up?), so
                    // with it failed there is nothing true to show them from — say that, offer
                    // the retry, and keep the way out. Never a guessed "set up a model".
                    ? <div className="flex flex-col gap-s" data-testid="onboarding-readiness-error">
                        <LoadError what="setup state" error={new Error(readError)}
                          onRetry={() => setReadAttempt((n) => n + 1)} />
                        <StepActions secondary={{ label: 'Set up later', onClick: () => leave('essentials', 'skipped', "Set up later — couldn't read what was set up") }} />
                      </div>
                    : <>
                        <div role="status" aria-busy="true" className="flex items-center py-2">
                          <LoadingStatus what="what's already set up" />
                          <Loader2 size={18} className="animate-spin text-on-surface-low" aria-hidden="true" />
                        </div>
                        <StepActions secondary={{ label: 'Set up later', onClick: () => leave('essentials', 'skipped', 'Set up later') }} />
                      </>}
              </StepRow>

              <StepRow ref={rowRefs.try} index={ORDER.indexOf('try')} total={ORDER.length} icon={Rocket} title={TITLES.try}
                subtitle="Watch it actually do something. Each one runs for real — and none of them is required."
                state={stateOf('try')} doneSummary={records.try?.summary}
                onActivate={activate('try')}>
                <TryOneStep onProgress={progress} onDone={(s) => leave('try', 'done', s)}
                  onSkip={() => leave('try', 'skipped', 'Skipped')} onExitTo={exitTo} />
              </StepRow>

              {/* 🔑 THE RECAP IS A REACHABLE ROW TOO. It used to be the one row with no `onActivate`,
                  on the reasoning that a final step can never be "done" — but that made it the row a
                  user could not get back to. A run that reached the recap and stepped back to review
                  something had no forward affordance at all and had to walk the remaining steps
                  again, which is the trap this whole change is about. */}
              <StepRow ref={rowRefs.ready} index={ORDER.indexOf('ready')} total={ORDER.length} icon={Sparkles} title={TITLES.ready}
                subtitle={`You're ready, ${firstNameOf(savedName)}.`}
                state={stateOf('ready')} onActivate={activate('ready')}>
                <DoneScreen name={savedName} model={records.essentials} tried={records.try} settled={readiness !== null} readFailed={!!readError} modelCheck={modelSeed}
                  showEverything={showEverything} onShowEverything={setShowEverything}
                  onFinish={finish} onTakeTour={takeTour} onExitTo={exitTo} />
              </StepRow>
            </ol>
            </StepActionsSlot.Provider>

            {/* What skipping costs, and how to come back — on every step but the last, where
                nothing is left to skip. It used to sit under the two doors it explains; the doors
                moved into the bar, and this stays with the content, where it is read rather than
                clicked. */}
            {step !== 'ready' && (
              <p data-type="caption" className="mx-auto mt-l text-center text-on-surface-low" style={{ maxWidth: 380 }}>
                {step === 'name'
                  ? rerun
                    ? 'Skipping keeps your name, your handle and everything already set up exactly as they are.'
                    : `Nothing is set up, and you'll be called "${DEFAULT_USER_NAME}" until you pick a name.`
                  : 'Whatever you have finished so far is kept.'}
                {' '}Pick setup back up any time: Settings &rarr; Account &rarr; Run setup again.
              </p>
            )}
          </motion.div>
          </div>

          {/* 🔑 THE FLOW'S NAVIGATION, IN ONE PLACE ON EVERY STEP. Back on the left, named for the
              step it returns to; on the right the door out of setup, then the step's own actions
              (`StepActions` renders them into the slot at the end), Continue last. The owner, on
              the build before: "I am expecting to see the buttons for Back to essential apps /
              Skip the rest of setup to be in an intuitive shell like place. Not tucked away at the
              bottom in an oddly aligned manner." They were two centred links under the whole step,
              and each step drew its own Continue at the end of its own content — on the model step
              a screen and a half down.

              It is `ui/FormFooter`, the design system's sticky action bar, not a new one: sticky
              at the foot of the scroller, bleeding to the column's edges so its buttons line up
              with the steps above, and in DOM order after the steps, so Tab reaches it last.

              Back is non-destructive by construction — it navigates, and nothing in `goTo` clears a
              draft, a record or the high-water mark. Skipping everything is quieter than a step's
              own skip ("Set up later"), which is the smaller and likelier choice.

              The bar is painted on `--color-surface`. The skip door used to be a link on the bare
              `--color-canvas` (rgb(240,244,248)), where the base accent measured **4.37:1** against
              a 4.5 floor at 13px/400 and had to take the emphasis ink; as buttons on the bar's
              surface they carry the on-surface ink, and no accent text is left on the canvas. */}
          <FormFooter>
            {/* On a phone the step's name is spoken but not drawn ("Back to bring your setup over"
                is 250px of a 358px bar), which keeps the bar to two rows there: the way back and the
                way out on top, the step's own actions beneath. */}
            {previousOf(step) && (
              <Button variant="ghost" size="md" className="mr-auto" onClick={() => goTo(previousOf(step) as StepId)}>
                <ArrowLeft size={16} aria-hidden="true" />
                {/* The space sits OUTSIDE the hidden span: inside it, the accessible-name computation
                    trims it away and the button is announced "Backto your name". */}
                <span>Back <span className="sr-only sm:not-sr-only">to {TITLES[previousOf(step) as StepId].toLowerCase()}</span></span>
              </Button>
            )}
            {step !== 'ready' && (
              <Button variant="ghost" size="md" onClick={skipSetup}>
                {step === 'name' ? 'Skip setup for now' : 'Skip the rest of setup'}
              </Button>
            )}
            <div ref={setActionSlot} className="contents" data-testid="onboarding-step-actions" />
          </FormFooter>
        </div>
      </div>
    </div>
  )
}

/** Step 1 — name + attribution handle (pill inputs with focus glow, Enter/arrow to advance).
 *
 *  The handle sits BESIDE the display name rather than in Settings only, because it is the
 *  one identity field that cannot be applied retroactively: a rename affects future writes
 *  only (TEAM-SHARED-ENTITIES §1), so every record created before the handle exists is
 *  permanently unattributed. First run is the only moment at which asking costs nothing.
 *
 *  It is a SUGGESTION, never a requirement — the Continue button stays gated on the name
 *  alone, and clearing the handle is a supported answer that means "keep my records
 *  unattributed". So this step gained a field without gaining a gate. */
function NameStep({ value, onChange, onSubmit, handle, onHandleChange }: {
  value: string
  onChange: (v: string) => void
  onSubmit: () => void
  handle: string
  onHandleChange: (v: string) => void
}) {
  return (
    <div className="flex flex-col gap-s">
      {/* Enter in either field submits; the step's Continue is the bar's, like every other step's.
          It used to be an arrow inside this pill — a second Continue, in a different place from
          the one on every step after it. */}
      <PillField value={value} onChange={onChange} onEnter={onSubmit} autoFocus
        ariaLabel="Your name" placeholder="Your name" />
      {/* The hint is wired with `aria-describedby` rather than left as adjacent prose: the
          rule it states (normalized, optional, not a login) is the whole reason an operator
          would leave this empty on purpose, and a screen-reader user who only hears the
          label "Username" has no way to reach it. */}
      <PillField value={handle} onChange={onHandleChange} onEnter={onSubmit}
        ariaLabel="Username" placeholder="your-handle" describedBy="onboarding-handle-hint" />
      <p id="onboarding-handle-hint" data-type="caption" className="px-m leading-relaxed" style={{ color: 'var(--color-on-surface-low)' }}>
        Optional. A short handle stamped onto things you create, so contributions stay
        attributable later — a label, not a login. Leave it empty to keep records
        unattributed.
      </p>
      <StepActions primary={{ label: 'Continue', onClick: onSubmit, disabled: !value.trim(), disabledReason: 'Enter your name first' }} />
    </div>
  )
}

/** The flow's pill-shaped text field — the identity step's own chrome, now that the step asks
 *  two questions instead of one.
 *
 *  Extracted rather than copied: the two fields sit directly above one another, so a second copy
 *  of the six-class wrapper plus the six-class input would be visible drift the moment either one
 *  was touched. It also keeps `primitiveAdoption`'s raw-input ratchet flat — one `<input>`, two
 *  uses — which is what that rail asks for (`ui/FilterChip`'s note records the same move).
 *
 *  NOT `ui/forms`' `TextInput`: this is a 17px pill on a glowing backdrop, and the shared family is
 *  a settings-row control (fixed sizes, `rounded-md`, its own surface tokens). Adopting it here would
 *  mean overriding all of it — the case `primitiveAdoption.baseline.json` already records twice.
 *  Local to this file for the same reason: the flow is its only caller, and `ui/` is for chrome more
 *  than one surface actually shares. */
function PillField({ value, onChange, onEnter, ariaLabel, placeholder, describedBy, autoFocus }: {
  value: string
  onChange: (v: string) => void
  onEnter: () => void
  ariaLabel: string
  placeholder: string
  describedBy?: string
  autoFocus?: boolean
}) {
  return (
    <div className="flex items-center gap-s rounded-pill bg-surface-high px-s py-1.5 ring-1 ring-outline/40 focus-within:ring-2 focus-within:ring-inset focus-within:ring-primary">
      <input autoFocus={autoFocus} value={value} onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') onEnter() }}
        aria-label={ariaLabel}
        aria-describedby={describedBy}
        placeholder={placeholder}
        className="min-w-0 flex-1 bg-transparent px-m text-on-surface text-[1.0625rem] placeholder:text-on-surface-low outline-none" />
    </div>
  )
}

/** The done screen — a recap of what this run actually did, then the four things worth
 *  knowing on day one, each with its real control rather than a sentence about one:
 *
 *   1. **the Inbox** is where work comes back to you (and you can land there instead);
 *   2. **Bounciness** — the live Settings → Design dial, so the app's feel reads as yours to
 *      set from the first minute rather than a taste you have to live with;
 *   3. **Show every surface** — the starter sidebar is a starting point, not a limit. The
 *      switch states intent; `finish()` performs the single write (see there);
 *   4. **what it does on its own** — auto-update pulls, rebuilds and restarts unattended,
 *      and the Store starts with one seeded community source. Both default on, both
 *      defensible for a tool that keeps itself healthy — but for a local-first product
 *      they must be TOLD at first run, not discovered. The update half hands over the
 *      real Settings → Updates switch; the Store half is a sentence plus the path to
 *      where source removal actually persists (the seed already ran at gateway start,
 *      so a toggle here would read as a live off-switch and retract nothing — exactly
 *      the control shape users mis-trust, per the Store-sources hint).
 *
 *  It teaches by handing over controls, which is why the dial and the switch are the SAME
 *  objects Settings owns — a copy here would be a second mechanism to keep in step.
 *
 *  It is also where the product tour starts (OU-10). The tour sits BESIDE "Start using"
 *  rather than replacing it: the recap above already hands over three controls, and a
 *  first-run screen whose only exit is a guided walk is a gate wearing an offer. Both
 *  buttons finish the flow; one of them then walks the app. */
function DoneScreen({ name, model, tried: triedRec, settled, readFailed, modelCheck, showEverything, onShowEverything, onFinish, onTakeTour, onExitTo }: {
  name: string
  /** What the essentials / try steps RECORDED, or `undefined` when they recorded nothing.
   *
   *  The recap used to ask `modelSummary !== 'Set up later'` — a sentinel comparison against the
   *  step's own copy, so renaming that string would have silently turned "not set up" into a
   *  claimed success. The outcome is now the thing being read, and the summary is only text. */
  model?: StepRecord
  tried?: StepRecord
  /** Whether `GET /api/onboarding` has settled — the read that seeds `model` and `tried` for a
   *  run this session did not walk.
   *
   *  🔴 UNTIL IT HAS, `undefined` MEANS "NOT READ YET", NOT "RECORDED NOTHING". A reload on the
   *  recap paints before that read lands, so reading `undefined` as an outcome painted
   *  `Chat model — set up later in Settings` for a home whose model was bound, then swapped in the
   *  real name a moment later. A line with no evidence yet names its subject and claims nothing. */
  settled: boolean
  /** That read FAILED. A line this session recorded nothing true for then says it could not
   *  read the state — never "set up later", which is a claim about a home nobody read. */
  readFailed: boolean
  /** The proof behind a re-entered run's chat-model line (`checkChatModel`): `pending` while it
   *  is out, so the line claims nothing yet, and `unknown` when it could not run, so the line
   *  says it could not tell rather than "set up later". */
  modelCheck: 'pending' | 'done' | 'unknown'
  showEverything: boolean
  onShowEverything: (v: boolean) => void
  onFinish: () => void
  onTakeTour: () => void
  onExitTo: (path: string) => void
}) {
  const chatReady = model?.outcome === 'done'
  const tried = triedRec?.outcome === 'done'
  /** A line is unknown only while the read is out AND this session recorded nothing for it. */
  const unread = (rec?: StepRecord) => !settled && !readFailed && rec === undefined
  /** The read failed and this session did not finish the step itself, so nothing is known. */
  const unknown = (rec?: StepRecord) => readFailed && rec?.outcome !== 'done'
  /** The chat-model line also waits for, and can be left unknown by, its own proof. */
  const modelUnread = unread(model) || (!readFailed && model === undefined && modelCheck === 'pending')
  const modelUnknown = unknown(model) || (modelCheck === 'unknown' && model?.outcome !== 'done')

  /** The autonomy pointer's facts, read when the ready step opens (this component mounts
   *  only then — StepRow renders children on the active step). `null` = still loading
   *  (disclosure copy shows, control withheld); `'failed'` = unreadable config, so the
   *  pointer offers the Settings path instead of a switch claiming a state it cannot
   *  know — a control showing a guessed value is worse than none. */
  const [autonomy, setAutonomy] = useState<{ autoUpdate: boolean; registrySeeded: boolean } | 'failed' | null>(null)
  useEffect(() => {
    let alive = true
    api.personalclawConfig().then((c) => {
      if (!alive) return
      const apps = (c.apps ?? {}) as { registry_source_enabled?: boolean }
      // `updates.auto` = 'staged' is the opt-in unattended-apply mode (RUM-5); anything else
      // (the 'off' default) is notify-only. Retired the legacy top-level `auto_update` bool.
      const updates = (c.updates ?? {}) as { auto?: string }
      setAutonomy({ autoUpdate: updates.auto === 'staged', registrySeeded: apps.registry_source_enabled !== false })
    }).catch(() => { if (alive) setAutonomy('failed') })
    return () => { alive = false }
  }, [])

  // The real Settings → Updates write, with that panel's exact remedy: flip optimistically,
  // and on a refused write TELL (app toast) rather than fight the control the user just
  // touched — see UpdatesPanel's reportSettingFailure note for why reverting is the wrong
  // move in this family.
  const toggleAutoUpdate = (v: boolean) => {
    setAutonomy((p) => (p && p !== 'failed' ? { ...p, autoUpdate: v } : p))
    api.setAutoUpdate(v).catch((e: unknown) => {
      let msg = e instanceof Error ? e.message : 'the request failed'
      try { msg = JSON.parse(msg).error || msg } catch { /* raw text */ }
      notify(`Couldn't ${v ? 'enable' : 'disable'} automatic updates: ${msg}`, 'error')
    })
  }

  return (
    <div className="flex flex-col gap-l">
      <motion.div className="flex flex-col gap-1.5"
        initial="initial" animate="animate" variants={{ animate: { transition: stagger(0.06) } }}>
        <motion.div variants={listItemEnter}><Recap ok label={`Hello, ${firstNameOf(name)}`} /></motion.div>
        <motion.div variants={listItemEnter}>{modelUnread
          ? <Recap ok={null} label="Chat model" />
          : modelUnknown
            ? <Recap ok={null} label="Chat model — couldn't read whether one is set up" />
            : <Recap ok={chatReady} label={chatReady ? `Chat model: ${model?.summary}` : 'Chat model — set up later in Settings'} />}</motion.div>
        <motion.div variants={listItemEnter}>{unread(triedRec)
          ? <Recap ok={null} label="First success" />
          : unknown(triedRec)
            ? <Recap ok={null} label="First success — couldn't read what was tried" />
            : <Recap ok={tried} label={tried ? `First success: ${triedRec?.summary}` : 'Nothing tried yet — the cards are in Discover'} />}</motion.div>
      </motion.div>

      <div className="flex flex-col gap-s">
        <p data-type="label-s" className="text-on-surface-low">Four things to know</p>
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
        <Pointer icon={RefreshCw} title="It keeps itself current on its own"
          body="When a new version ships, it installs and restarts unattended. This is the real switch from Settings → Updates.">
          <div className="flex flex-col gap-1.5">
            {autonomy === 'failed' ? (
              <TextLink size="sm" ink="emphasis" onClick={() => onExitTo('settings/updates')}>Manage updates in Settings</TextLink>
            ) : autonomy ? (
              <div className="flex items-center gap-2">
                <Toggle on={autonomy.autoUpdate} onChange={toggleAutoUpdate} label="Update automatically" />
                <span className="text-on-surface-var text-[0.8125rem]">Update automatically</span>
              </div>
            ) : null}
            {autonomy !== 'failed' && autonomy?.registrySeeded && (
              <p className="text-on-surface-low text-[0.8125rem]">
                App discovery starts with one community source; installing anything still runs the
                security scanner. <TextLink size="sm" ink="emphasis" onClick={() => onExitTo('apps')}>Review Store sources</TextLink>
              </p>
            )}
          </div>
        </Pointer>
      </div>

      {/* The flow's two last doors, in the bar with every step's: the tour is offered rather than
          imposed — it finishes setup either way, and every stop is skippable once it starts
          (Escape exits from any of them). */}
      <StepActions primary={{ label: `Start using ${APP_NAME}`, onClick: onFinish }}
        secondary={{ label: 'Take the quick tour', icon: Compass, onClick: onTakeTour }} />
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

/** One recap line. `ok: null` is a line still waiting on its evidence: the neutral badge carries
 *  the same spinner the essentials step shows while that read is out, and the label is only the
 *  subject, so the line makes no claim in either direction until the answer lands. */
function Recap({ ok, label }: { ok: boolean | null; label: string }) {
  return (
    <div className="flex items-center gap-2 text-[0.8125rem]">
      <span className="grid size-5 place-items-center rounded-full" style={{ background: ok ? 'var(--color-success)' : 'var(--color-surface-high)', color: ok ? 'var(--color-on-primary)' : 'var(--color-on-surface-low)' }}>
        {ok === null ? <Loader2 size={12} className="animate-spin" aria-hidden="true" /> : <Check size={12} />}
      </span>
      <span className="text-on-surface-var">{label}</span>
      {ok === null && <LoadingStatus />}
    </div>
  )
}
