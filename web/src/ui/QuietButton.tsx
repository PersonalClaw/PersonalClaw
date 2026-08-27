import type { ReactNode } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import { cx } from './cx'
import { spring, expr } from '../design/motion'

/** The quiet compact inline action in a content-viewer toolbar — a dimmed
 *  ink-low label + leading glyph, medium radius, 28px tall; hover fills to
 *  surface-high and brightens the ink. Deliberately smaller, dimmer, and
 *  square-cornered vs the pill ghost {@link Button} (h-7/text-[0.75rem]/
 *  text-on-surface-low, not h-8+/pill/text-on-surface) so it recedes into a
 *  header action row rather than reading as a CTA. Four toolbar actions —
 *  ArtifactViewer's "Source file" + "Download", FileViewer's "Artifact",
 *  LoopCockpitPage's findings-log "Download" — rendered this exact markup
 *  inline; this is the single source. Children are the leading glyph + label
 *  (they carry the accessible name); `title` adds the supplementary tooltip
 *  three of the sites pass.
 *
 *  `disabled`/`disabledReason` are the SAME treatment {@link SquareIconButton}
 *  ships — `aria-disabled` (never the native attribute) so the tab stop
 *  survives, the reason riding `title`, the click suppressed in the handler —
 *  not a fourth spelling of the idea. Press springs in
 *  (expressiveness-scaled, yielding to reduced motion), matching the rest of
 *  the button family. */
export function QuietButton({
  children, onClick, onDoubleClick, title, ariaExpanded, disabled, disabledReason, className,
}: {
  children: ReactNode
  onClick?: (e: React.MouseEvent<HTMLButtonElement>) => void
  onDoubleClick?: (e: React.MouseEvent<HTMLButtonElement>) => void
  title?: string
  /** When this quiet action is a DISCLOSURE, its open state — same prop name `Button` already uses, so
   *  the two siblings answer the question the same way rather than each inventing a spelling.
   *
   *  Six of its call sites are disclosures and every one was silent: ChatPage's "View / Hide", the
   *  artifact viewer's "Compare versions / Close compare", and `WorkflowRunDetail`'s four panel toggles
   *  (workspace, outbox, introspect, steer). Each swaps its own label, which tells a user what the NEXT
   *  click does — but not whether the panel is open right now, which is what `aria-expanded` carries.
   *  Omit it for a plain quiet action (Download, Source file) and no state is claimed. */
  ariaExpanded?: boolean
  /** Action currently unavailable: 40% opacity, not-allowed, onClick/onDoubleClick suppressed,
   *  press feedback dropped.
   *
   *  This tier had NO disabled state at all, and the sharp consequence was measured on
   *  `pages/workflows/OutboxPanel.tsx`: its "Choose files" button forwards to a file-picker input
   *  five lines below that is itself `disabled={dropBusy}`, so for the whole hand-over window the
   *  button stayed fully lit and fully clickable while every click did nothing and announced nothing.
   *  A label swapping to "Handing over…" is not a disabled state — it says what is happening, not that
   *  the control is inert.
   *
   *  🪤 The sentence above deliberately says "a file-picker input" rather than spelling the element:
   *  `design/filePickerReachable.test.ts` scans `.tsx` for that tag and does NOT strip comments, so
   *  naming it here enrols this primitive in the file-picker census as a pointer-only picker. Measured
   *  — it went red on exactly that.
   *
   *  Mapped to `aria-disabled`, never the native attribute (the same trade `SquareIconButton` and
   *  `Button` make): the control stays focusable so a keyboard user can reach it and hear why. */
  disabled?: boolean
  /** WHY it is unavailable, when `disabled` is true. Rides `title` (appended after an em dash, after
   *  any `title` the caller passes), matching `SquareIconButton.disabledReason` and
   *  `Button.disabledReason` — an sr-only span inside the button would be CONCATENATED into the
   *  accessible name, so the action would stop being findable by its own name.
   *
   *  Omit it when the gate is self-evident; a compound gate should pass it only for the branch it
   *  actually describes. */
  disabledReason?: string
  className?: string
}) {
  const reduce = useReducedMotion()
  const pressScale = reduce || disabled ? 1 : 1 - expr(0.05, 0.4)
  return (
    <motion.button
      type="button"
      onClick={disabled ? undefined : onClick}
      onDoubleClick={disabled ? undefined : onDoubleClick}
      title={disabled && disabledReason ? [title, disabledReason].filter(Boolean).join(' — ') : title}
      aria-disabled={disabled || undefined}
      aria-expanded={ariaExpanded}
      whileTap={disabled ? undefined : { scale: pressScale }}
      transition={spring.spatialFast}
      className={cx(
        'inline-flex items-center gap-xs rounded-md px-s h-7 text-[0.75rem]',
        disabled
          ? 'text-on-surface-low opacity-40 cursor-not-allowed'
          : 'text-on-surface-low hover:bg-surface-high hover:text-on-surface',
        className,
      )}
    >
      {children}
    </motion.button>
  )
}
