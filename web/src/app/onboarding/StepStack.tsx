import { forwardRef, useContext, useEffect, useRef } from 'react'
import { withWeight } from '../../design/fontWeight'
import { motion, AnimatePresence } from 'framer-motion'
import { Check, Minus } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { spring } from '../../design/motion'
import { StepActionsSlot } from './StepActions'

/** What a row says about its step.
 *
 *  `skipped` is a distinct state, not a flavour of `done`. The flow previously rendered a
 *  walked-past step as `done` with the summary "Skipped" — a green check beside the word Skipped,
 *  a contradiction the eye resolves in favour of the check. A skip is a real and legitimate
 *  outcome, so it gets its own quiet mark; `upcoming` stays reserved for a step with NO recorded
 *  outcome at all, which is the state the flow must never dress up as complete. */
export type StepState = 'upcoming' | 'active' | 'done' | 'skipped'

/** A vertically-stacked onboarding step row. Active rows expand to reveal their body; settled rows
 *  collapse to a compact summary — green for done, quiet for skipped; upcoming rows say nothing.
 *  The row that's `active` forwards its ref so the DotGlow can track it. */
export const StepRow = forwardRef<HTMLLIElement, {
  index: number
  /** How many steps there are, so the ACTIVE row can answer "how much is left" in words rather
   *  than only in the screen-reader announcement. Optional: the row primitive is also rendered
   *  directly by its own tests, where the set size is not the subject. */
  total?: number
  icon: LucideIcon
  title: string
  subtitle?: string
  state: StepState
  doneSummary?: string
  onActivate?: () => void
  children?: React.ReactNode
}>(function StepRow({ index, total, icon: Icon, title, subtitle, state, doneSummary, onActivate, children }, ref) {
  const done = state === 'done'
  const skipped = state === 'skipped'
  const settled = done || skipped
  const active = state === 'active'
  const green = 'var(--color-success)'
  // Whether this row is a "go back to this step" target — which decides the ELEMENT its header
  // renders as, below, and its cursor. ONE predicate for both, so they cannot drift apart again.
  //
  // 🔑 IT NO LONGER ASKS WHETHER THE STEP IS DONE. That was the mechanism behind "going back
  // destroys going forward": a run that had reached step 5 and returned to step 1 re-derived steps
  // 2-5 as upcoming, so every header stopped being a button and the only route forward was to walk
  // all four again — measured on a fresh home, the page left with just Continue and Skip. Whether a
  // step is REACHABLE is the caller's judgement, because only it knows the high-water mark, and it
  // states that judgement by passing or withholding `onActivate`. This row honours it.
  const revisitable = !active && !!onActivate
  const Header = revisitable ? motion.button : motion.div

  // 🔴 ADVANCING A STEP USED TO DROP FOCUS ON THE FLOOR, on the first screen of the product. The
  // control that had it — the name field, or its Continue arrow — lives in the step BODY, and the
  // body unmounts as the step collapses. Focus then falls to `<body>`, so a keyboard user's next Tab
  // starts from the top of the document and a screen-reader user is told nothing about where they
  // now are: `Onboarding`'s live region announces the step, but an announcement is not a position.
  // WCAG 2.4.3.
  //
  // So the newly-active step takes focus, on its HEADING — the destination the user was moved to,
  // which is also what puts the new step on screen (`focus()` scrolls its scroller).
  //
  // 🪤 ONLY ON THE TRANSITION, never on mount. Step 1 is active from the first render and its name
  // field carries `autoFocus`; focusing the heading unconditionally would race that and land the
  // user on a heading instead of the field they have to type in. Comparing against the PREVIOUS
  // value is what distinguishes "became active" from "was rendered active", and an initial-value
  // ref makes the first pass a no-op by construction rather than by a flag someone can forget to
  // clear.
  const heading = useRef<HTMLHeadingElement>(null)
  const wasActive = useRef(active)
  // The shell's action bar, handed on to this step's body only while it is the step on screen. A
  // body that is still animating shut after the user moved on stays mounted for that animation,
  // and without this its Continue would sit in the bar beside the next step's.
  const bar = useContext(StepActionsSlot)
  const bodyBar = active || bar === undefined ? bar : null
  useEffect(() => {
    if (active && !wasActive.current) heading.current?.focus()
    wasActive.current = active
  }, [active])

  return (
    <motion.li
      ref={ref}
      layout
      // 🪤 `aria-current` NEEDS A SET CONTEXT TO BE EXPOSED, and this was on a role-less
      // `motion.div`. `stepProgressAnnounced.test.ts` asserts the attribute is PRESENT and gated to
      // the active row — both true — but never that it can reach anyone: `aria-current="step"` is
      // defined for an item within a set, so on a generic div its exposure is inconsistent at best.
      // The stack is a real `<ol>` now and this is a real `<li>`, which is also what the visible
      // design always was: five numbered steps.
      aria-current={active ? 'step' : undefined}
      // 🪤 THE ROW IS NOT A FOCUS TARGET, and it briefly carried `tabIndex={active ? -1 : undefined}`
      // so that the flow could focus it on a step change. Two independent fixes for the same
      // dropped-focus defect then existed at once — this row's heading below, and `Onboarding`'s
      // arrival effect focusing the row — and the parent's won by effect order alone, because React
      // runs a child's effects first. The heading is the right destination: this `<li>` has no role
      // and no accessible name, so focusing it makes a screen reader read the row's entire text
      // content, expanded step body included, instead of "heading level 2, <the step>". The `<li>`
      // therefore has no focusable state and needs no inset focus ring of its own.
      transition={spring.spatialDefault}
      className="list-none overflow-hidden"
      style={{
        // match the DotGlow bloom's corner radius exactly so the glow halo hugs
        // the active card's edges (the glow uses --radius-xli).
        borderRadius: 'var(--radius-xli)',
        background: active ? 'var(--color-surface-container)' : 'transparent',
        border: `1px solid ${active ? 'var(--color-outline)' : 'transparent'}`,
        boxShadow: active ? 'var(--shadow-rest)' : 'none',
      }}
    >
      {/* 🔴 THE HEADER IS A REAL BUTTON WHEN IT IS CLICKABLE. A completed row carried `onClick` and
          `cursor: pointer` on a plain `div` — no `tabIndex`, no `role`, no key handler — so a mouse
          user could return to any finished step and a keyboard user could not (WCAG 2.1.1), on the
          FIRST screen of the product. Four of the five rows are given `onActivate`, so this was the
          normal path, not an edge.
          🪤 The previous audit of this file recorded "the rows are `<div>`s (not focusable)" as a
          REASON the live region was needed — it read the missing tab stop as a fact to work around
          rather than as the bug sitting next to the click handler.
          A real `<button>` is used rather than `role="button"` + `tabIndex` + a key handler, because
          hand-rolling those is how three of them end up subtly different — this repo's own
          `rawSoftOffContract` records that lesson. */}
      <Header
        {...(revisitable
          ? {
            type: 'button' as const,
            onClick: onActivate,
            // The visible text is the step's NAME; the button's job is to go BACK to it, and that
            // verb appears nowhere on screen. Naming it explicitly is the one case where an
            // aria-label earning its keep beats plain children.
            'aria-label': `Go back to step ${index + 1}: ${title}`,
          }
          : {})}
        layout="position"
        // 🔴 THE RING HAD TO BE INSET, and this is the half a DOM check cannot see. Making the header
        // focusable is worthless if the focus indicator is invisible (WCAG 2.4.7), and it WAS: the
        // global `:focus-visible` rule computed `outline: 2px solid` correctly — `matches(':focus-visible')`
        // returned true after a real Tab — while the `<li>` above carries `overflow-hidden` and this
        // button fills it edge to edge, so an outward-drawn outline lay entirely outside the clip and
        // was discarded. Two screenshots showed a focused row with no ring before I looked at WHY.
        // `-outline-offset-2` draws the same ring just inside the box, where the clip cannot reach it.
        // 🪤 The trap generalises: any focusable element that fills an `overflow-hidden` parent needs
        // an inset ring, and no accessibility-tree or computed-style assertion catches it — only
        // pixels do.
        // …and the ring needs the ROW'S RADIUS, or its four corners are clipped by the same
        // rounded `overflow-hidden` box and it reads as a broken rectangle rather than a ring.
        // Caught in the screenshot after the inset fix: straight edges present, corners missing.
        style={{ borderRadius: 'var(--radius-xli)' }}
        className={`flex w-full items-center gap-m px-l py-m text-left focus-visible:-outline-offset-2 ${revisitable ? 'cursor-pointer' : 'cursor-default'}`}
      >
        {/* node */}
        <span
          className="grid size-9 shrink-0 place-items-center rounded-full transition-colors"
          style={{
            background: done ? green : active ? 'var(--color-primary)' : 'var(--color-surface-high)',
            color: done || active ? 'var(--color-on-primary)' : 'var(--color-on-surface-low)',
          }}
        >
          <AnimatePresence mode="wait" initial={false}>
            {done
              ? <motion.span key="check" initial={{ scale: 0, rotate: -30 }} animate={{ scale: 1, rotate: 0 }} transition={spring.spatialFast}><Check size={18} /></motion.span>
              : skipped
                // A dash, on the quiet ground: settled, and visibly NOT the same thing as done.
                ? <motion.span key="skip" initial={{ scale: 0, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={spring.spatialFast}><Minus size={18} /></motion.span>
                : <motion.span key="icon" initial={{ scale: 0.6, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}><Icon size={17} /></motion.span>}
          </AnimatePresence>
        </span>

        {/* title / summary */}
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-s">
            {/* 🔑 THE CURRENT STEP'S TITLE IS A REAL HEADING, and it is the only one. First run had
                exactly one `<h1>` ("Welcome to …") and no `<h2>` at all, so the five steps were
                invisible to heading navigation — on the screen whose complaint was that it is hard
                to navigate. It is also the focus destination the effect above needs, which is why
                the two arrived together: a heading nobody can move to is half the fix.
                🪤 ONE at a time, on the ACTIVE row, deliberately — not five. The active row's header
                is a `<div>`, so an `<h2>` nests validly; a DONE row's header is a `<button>`, and
                `<h2>` inside `<button>` is invalid content (a button takes phrasing content, a
                heading is flow content). Five headings would therefore need the APG accordion shape
                (`<h2><button>`), whose accessible name is then the whole row — title, step number
                AND subtitle — which trades a missing heading for an unreadable one. The set
                semantics the five rows need is already carried properly: a real `<ol>` of `<li>`s
                with `aria-current="step"`, plus the live region that speaks "Step N of M".
                `tabIndex={-1}` makes it a programmatic focus target WITHOUT adding a tab stop: the
                heading is a destination, not a control, so it must not join the tab ring.
                `-outline-offset-2` for the same reason the header below carries it — the `<li>` is
                `overflow-hidden`, so an outward-drawn focus ring is clipped away entirely and the
                user is moved somewhere with no sign they were moved. */}
            {active
              ? <h2 ref={heading} tabIndex={-1} className="text-on-surface focus-visible:-outline-offset-2"
                  style={withWeight({ fontSize: '1.0625rem' }, 600)}>{title}</h2>
              : <span className="text-on-surface" style={withWeight({ fontSize: '0.9375rem' }, 600)}>{title}</span>}
            {/* 🔑 UNGATED ON PURPOSE — `!active &&` hid the number on exactly the row the user is
                standing on, so the visible numbering always had a hole where the answer mattered
                most: first load read "Your name · Step 2 · Step 3 · Step 4 · Step 5", and at step 3
                it read "Step 2 · Essential apps · Step 4". "How far through am I" is a question
                about the CURRENT step.
                It was also a mismatch between the two channels: `stepProgressAnnounced.test.ts`
                already has this screen announce `Step N of M: <title>` to assistive tech through a
                live region, so a screen-reader user was told the position while the eye was not.
                Showing it makes the visible label agree with what is already spoken. */}
            {/* 🔑 THE ACTIVE ROW CARRIES THE TOTAL. The visible label used to read a bare "Step 3"
                while the screen-reader announcement said "Step 3 of 5" — so "how much is left" was
                answered for one channel and not the other, on the screen where a stranger most
                wants to know. The collapsed rows stay bare: repeating "of 5" five times down the
                stack is noise, and the question is about where you are STANDING. */}
            <span className="text-on-surface-low text-[0.75rem]">
              Step {index + 1}{active && total ? ` of ${total}` : ''}
            </span>
          </div>
          <AnimatePresence initial={false} mode="wait">
            {active && subtitle
              ? <motion.p key="sub" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="mt-0.5 text-on-surface-low text-[0.8125rem]">{subtitle}</motion.p>
              : settled && doneSummary
                ? <motion.p key="settled" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mt-0.5 text-[0.8125rem]"
                    style={{ color: done ? green : 'var(--color-on-surface-low)' }}>{doneSummary}</motion.p>
                : null}
          </AnimatePresence>
        </div>
      </Header>

      {/* expanding body — only when active.
          The action-bar slot is provided OUTSIDE the presence, on purpose: a body animating shut is
          rendered from the element it had when it was last active, frozen, so a provider inside it
          would keep handing that body the bar. Out here the provider re-renders with the row, and
          the frozen body's actions read `null` the moment the row stops being the step on screen. */}
      <StepActionsSlot.Provider value={bodyBar}>
      <AnimatePresence initial={false}>
        {active && children && (
          <motion.div
            key="body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ height: spring.spatialDefault, opacity: { duration: 0.18 } }}
          >
            {/* 🔑 THE HANGING INDENT IS A DESKTOP AFFORDANCE AND A PHONE TAX. 4.75rem lines the step
                body up under its title, past the 36px status node — worth it at 1024px and up, and
                76px of a 342px card at 390px, i.e. 22% of the screen spent on alignment. Measured at
                390×844, `pl-[4.75rem]` → `pl-l`: the step body's content box goes 264px → 324px, the
                name field 204px → 264px, and the handle hint wraps over 4 lines instead of 5. Below
                `sm` the body takes the card's own inset instead; the title is still directly above
                it, so nothing becomes ambiguous — it just stops paying for a rule the screen has no
                room for. */}
            <div className="px-l pb-l pl-l sm:pl-[4.75rem]">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
      </StepActionsSlot.Provider>
    </motion.li>
  )
})
