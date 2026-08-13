import { type ReactNode } from 'react'
import { motion } from 'framer-motion'
import type { LucideIcon } from 'lucide-react'
import { cx } from '../../../ui/cx'
import { spring } from '../../../design/motion'

/** Calm "all clear" dashboard-slot empty state — a compact, top-aligned strip
 *  (icon + line + optional inline action). Deliberately NOT the full-height
 *  centered `EmptyState` (ui/ListScaffold): dashboard sections sit side by side
 *  in grid rows, and a stretched empty widget next to a full sibling reads as a
 *  conspicuous void. The dashed hairline marks the slot as intentionally empty
 *  without adding card chrome. See docs/design/patterns.md — the two empty-state
 *  patterns are distinct on purpose (page-empty vs slot-empty). */
export function SlotEmptyState({ icon: Icon, children, action }: { icon: LucideIcon; children: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex items-center gap-s self-start rounded-lg border border-dashed border-outline-variant/50 px-m py-s">
      <Icon size={15} className="shrink-0 text-on-surface-low opacity-70" />
      <p data-type="body-m" className="min-w-0 text-on-surface-low">{children}</p>
      {action && <div className="flex shrink-0 items-center" onClick={(e) => e.stopPropagation()}>{action}</div>}
    </div>
  )
}

/** A row in a widget list — a tappable surface with spring press feedback + a
 *  hover lift, matching the app's pressable idiom. Optional trailing actions. */
export function WidgetRow({
  onClick, children, actions, className,
}: {
  onClick?: () => void
  children: ReactNode
  actions?: ReactNode
  className?: string
}) {
  return (
    <motion.div
      layout
      transition={spring.spatialDefault}
      whileHover={onClick ? { y: -1 } : undefined}
      className={cx(
        'flex items-center gap-s rounded-lg bg-surface-low px-m py-s',
        onClick && 'cursor-pointer transition-colors hover:bg-surface-high',
        className,
      )}
      onClick={onClick}
    >
      <div className="min-w-0 flex-1">{children}</div>
      {actions && <div className="flex shrink-0 items-center gap-xs" onClick={(e) => e.stopPropagation()}>{actions}</div>}
    </motion.div>
  )
}

/** A small pill button for inline row actions (approve/dismiss/complete). Tone
 *  drives the accent; ghost by default. */
export function RowAction({
  onClick, children, tone = 'default', title, ariaLabel,
}: {
  onClick: () => void
  children: ReactNode
  tone?: 'default' | 'primary' | 'ok' | 'danger'
  title?: string
  /** The accessible name, composed with the ROW'S SUBJECT — "Reply: Skill: refine-a-skill".
   *
   *  Required for any action rendered inside a per-row `.map`, because `title` alone names the verb
   *  and every row repeats it. Measured on `#/dashboard`: **8× "Reply", 8× "Dismiss", 6× "Mark
   *  complete"**, each acting on a different item, so a screen-reader user listing the controls hears
   *  the same three words over and over with nothing to choose between them (WCAG 4.1.2). Note the
   *  visible text stays the verb — on screen the subject is the row you are looking at.
   *
   *  The kit already composes names this way elsewhere: `ui/Toaster` (`Dismiss: ${message}`),
   *  `ui/forms` (`Remove ${value}`), `ui/WidthPill` (`Content width: ${label}`), and FileTree /
   *  AppsSection (`Actions for ${name}`). Omit it only for a SINGLETON action (one per widget, no
   *  row to disambiguate). */
  ariaLabel?: string
}) {
  const toneCls = {
    default: 'text-on-surface-var hover:bg-surface-highest hover:text-on-surface',
    primary: 'text-primary hover:bg-primary-container/40',
    ok: 'text-ok hover:bg-ok/15',
    danger: 'text-danger hover:bg-danger/15',
  }[tone]
  return (
    <motion.button
      type="button"
      title={title}
      aria-label={ariaLabel}
      whileTap={{ scale: 0.92 }}
      transition={spring.spatialFast}
      onClick={onClick}
      // 24px HIT BOX, 22px painted. Measured on `#/dashboard`: `px-m py-xs` rendered **38×22**, and
      // sibling actions sit `gap-xs` (4px) apart — so SC 2.5.8's undersized-target exception cannot
      // rescue it either, the 24px circles overlap. `min-h-6` raises the box and `-my-px` returns the
      // 2px to the layout, so the row keeps its height: the fix is the hit box, not the design. Same
      // resolution as the `sm` Toggle's 36×20 → 36×24.
      className={cx('inline-flex min-h-6 -my-px items-center gap-xs rounded-pill px-m py-xs transition-colors', toneCls)}
      data-type="label-m"
    >
      {children}
    </motion.button>
  )
}

/** A tiny status dot, colored by a CSS var. */
export function StatusDot({ color, pulse }: { color: string; pulse?: boolean }) {
  return (
    <span className="relative inline-flex shrink-0" style={{ width: 8, height: 8 }}>
      {pulse && <span className="status-pulse absolute inset-0 rounded-pill" style={{ background: color }} />}
      <span className="relative inline-block rounded-pill" style={{ width: 8, height: 8, background: color }} />
    </span>
  )
}
