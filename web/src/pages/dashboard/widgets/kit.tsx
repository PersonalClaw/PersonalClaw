import { type ReactNode } from 'react'
import { motion } from 'framer-motion'
import type { LucideIcon } from 'lucide-react'
import { cx } from '../../../ui/cx'
import { spring } from '../../../design/motion'

/** Calm "all clear" empty state — a compact, top-aligned strip (icon + line +
 *  optional inline action). Deliberately NOT full-height-centered: dashboard
 *  sections sit side by side in grid rows, and a stretched empty widget next to
 *  a full sibling read as a conspicuous void. The dashed hairline marks the
 *  slot as intentionally empty without adding card chrome. */
export function EmptyState({ icon: Icon, children, action }: { icon: LucideIcon; children: ReactNode; action?: ReactNode }) {
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
  onClick, children, tone = 'default', title,
}: {
  onClick: () => void
  children: ReactNode
  tone?: 'default' | 'primary' | 'ok' | 'danger'
  title?: string
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
      whileTap={{ scale: 0.92 }}
      transition={spring.spatialFast}
      onClick={onClick}
      className={cx('inline-flex items-center gap-xs rounded-pill px-m py-xs transition-colors', toneCls)}
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
