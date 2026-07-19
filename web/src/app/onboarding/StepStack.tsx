import { forwardRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Check } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { spring } from '../../design/motion'

export type StepState = 'upcoming' | 'active' | 'done'

/** A vertically-stacked onboarding step row. Active rows expand to reveal their
 *  body; done rows collapse to a compact green summary; upcoming rows are quiet.
 *  The row that's `active` forwards its ref so the DotGlow can track it. */
export const StepRow = forwardRef<HTMLDivElement, {
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

  return (
    <motion.div
      ref={ref}
      layout
      transition={spring.spatialDefault}
      onClick={!active && done && onActivate ? onActivate : undefined}
      className="overflow-hidden"
      style={{
        // match the DotGlow bloom's corner radius exactly so the glow halo hugs
        // the active card's edges (the glow uses --radius-xli).
        borderRadius: 'var(--radius-xli)',
        background: active ? 'var(--color-surface-container)' : 'transparent',
        border: `1px solid ${active ? 'var(--color-outline)' : 'transparent'}`,
        boxShadow: active ? 'var(--shadow-rest)' : 'none',
        cursor: done && onActivate ? 'pointer' : 'default',
      }}
    >
      <motion.div layout="position" className="flex items-center gap-m px-l py-m">
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
            <span className="text-on-surface" style={{ fontVariationSettings: '"wght" 600', fontSize: active ? '1.0625rem' : '0.9375rem' }}>{title}</span>
            {!active && <span className="text-on-surface-low text-[0.75rem]">Step {index + 1}</span>}
          </div>
          <AnimatePresence initial={false} mode="wait">
            {active && subtitle
              ? <motion.p key="sub" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="mt-0.5 text-on-surface-low text-[0.875rem]">{subtitle}</motion.p>
              : done && doneSummary
                ? <motion.p key="done" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mt-0.5 text-[0.8125rem]" style={{ color: green }}>{doneSummary}</motion.p>
                : null}
          </AnimatePresence>
        </div>
      </motion.div>

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
    </motion.div>
  )
})
