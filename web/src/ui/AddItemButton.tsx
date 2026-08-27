import type { ReactNode } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import { cx } from './cx'
import { spring, expr } from '../design/motion'

/** The quiet "add another row" affordance beneath an editable list (workflow
 *  steps, prompt/snippet variables). A surface-container fill, medium radius, 36px
 *  tall, ink-var label with a leading glyph; hover lifts to surface-high. Deliberately
 *  rectangular + understated (not the pill CTA {@link Button}) so it aligns with the
 *  `rounded-md bg-surface-container` list rows it sits under. Five editors (WorkflowForm
 *  ×2, PromptForm, SnippetForm, PromptEditFields) rendered this exact markup inline;
 *  this is the single source. Children are the leading icon + label. Pass
 *  `className="self-start"` where the button must not stretch in its flex column.
 *
 *  Press springs in (expressiveness-scaled, yielding to reduced motion) — the same
 *  spring the rest of the button family uses. This button APPENDS A ROW, so the press
 *  acknowledgement is the only feedback between the click and the new row arriving. */
export function AddItemButton({ children, onClick, className }: {
  children: ReactNode
  onClick?: (e: React.MouseEvent<HTMLButtonElement>) => void
  className?: string
}) {
  const reduce = useReducedMotion()
  const pressScale = reduce ? 1 : 1 - expr(0.05, 0.4)
  return (
    <motion.button
      type="button"
      onClick={onClick}
      whileTap={{ scale: pressScale }}
      transition={spring.spatialFast}
      className={cx(
        'inline-flex items-center gap-xs rounded-md bg-surface-container px-m h-9',
        'text-on-surface-var text-[0.8125rem] hover:bg-surface-high transition-colors',
        className,
      )}
    >
      {children}
    </motion.button>
  )
}
