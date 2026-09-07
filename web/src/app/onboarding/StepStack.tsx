import { forwardRef } from 'react'
import { withWeight } from '../../design/fontWeight'
import { motion, AnimatePresence } from 'framer-motion'
import { Check } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { spring } from '../../design/motion'

export type StepState = 'upcoming' | 'active' | 'done'

/** A vertically-stacked onboarding step row. Active rows expand to reveal their
 *  body; done rows collapse to a compact green summary; upcoming rows are quiet.
 *  The row that's `active` forwards its ref so the DotGlow can track it. */
export const StepRow = forwardRef<HTMLLIElement, {
  index: number
  icon: LucideIcon
  title: string
  subtitle?: string
  state: StepState
  doneSummary?: string
  onActivate?: () => void
  children?: React.ReactNode
}>(function StepRow({ index, icon: Icon, title, subtitle, state, doneSummary, onActivate, children }, ref) {
  const done = state === 'done'
  const active = state === 'active'
  const green = 'var(--color-success)'
  // Whether this row is a "go back to this step" target — which decides the ELEMENT its header
  // renders as, below. Previously this predicate only chose an `onClick`.
  const revisitable = !active && done && !!onActivate
  const Header = revisitable ? motion.button : motion.div

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
              : <motion.span key="icon" initial={{ scale: 0.6, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}><Icon size={17} /></motion.span>}
          </AnimatePresence>
        </span>

        {/* title / summary */}
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-s">
            <span className="text-on-surface" style={withWeight({ fontSize: active ? '1.0625rem' : '0.9375rem' }, 600)}>{title}</span>
            {/* 🔑 UNGATED ON PURPOSE — `!active &&` hid the number on exactly the row the user is
                standing on, so the visible numbering always had a hole where the answer mattered
                most: first load read "Your name · Step 2 · Step 3 · Step 4 · Step 5", and at step 3
                it read "Step 2 · Essential apps · Step 4". "How far through am I" is a question
                about the CURRENT step.
                It was also a mismatch between the two channels: `stepProgressAnnounced.test.ts`
                already has this screen announce `Step N of M: <title>` to assistive tech through a
                live region, so a screen-reader user was told the position while the eye was not.
                Showing it makes the visible label agree with what is already spoken. */}
            <span className="text-on-surface-low text-[0.75rem]">Step {index + 1}</span>
          </div>
          <AnimatePresence initial={false} mode="wait">
            {active && subtitle
              ? <motion.p key="sub" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="mt-0.5 text-on-surface-low text-[0.8125rem]">{subtitle}</motion.p>
              : done && doneSummary
                ? <motion.p key="done" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mt-0.5 text-[0.8125rem]" style={{ color: green }}>{doneSummary}</motion.p>
                : null}
          </AnimatePresence>
        </div>
      </Header>

      {/* expanding body — only when active */}
      <AnimatePresence initial={false}>
        {active && children && (
          <motion.div
            key="body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ height: spring.spatialDefault, opacity: { duration: 0.18 } }}
          >
            <div className="px-l pb-l pl-[4.75rem]">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.li>
  )
})
