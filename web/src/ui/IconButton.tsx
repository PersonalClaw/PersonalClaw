import { motion, AnimatePresence, useReducedMotion } from 'framer-motion'
import type { LucideIcon } from 'lucide-react'
import { cx } from './cx'
import { spring, bounce, expr, exprHeavy } from '../design/motion'

/** Round icon button — pill hit area, rounded outline icon (ROND feel).
 *  Redesign-v2: expressiveness-scaled press/hover + a soft hover halo (bold only)
 *  so the button "lights up" under the cursor. Keeps the icon-morph (`iconKey`)
 *  and success `bloom` moments. Yields to reduced-motion; halo drops below the
 *  heavy-effect threshold. */
export function IconButton({
  icon: Icon, label, onClick, active, filled, size = 40, className, disabled, iconKey, bloom,
}: {
  icon: LucideIcon
  label: string
  onClick?: (e: React.MouseEvent) => void
  active?: boolean
  filled?: boolean
  size?: number
  className?: string
  // Dim + block the button (no hover, not-allowed cursor) when its action is
  // currently unavailable — so a gated icon button reads as inert instead of a
  // silent dead-click. onClick is suppressed regardless of what's passed.
  disabled?: boolean
  // When set, the icon cross-fades/scales in whenever iconKey changes (a shape
  // morph, e.g. send arrow → success check). Without it the icon swaps instantly.
  iconKey?: string
  // A one-shot success bloom: the button pops with a playful overshoot when it
  // mounts in this state (e.g. the send→check confirmation). Scales with the
  // user's bounciness setting via the bounce tier.
  bloom?: boolean
}) {
  const reduce = useReducedMotion()
  const pressScale = reduce ? 1 : 1 - expr(0.08, 0.5)
  const hoverScale = reduce ? 1 : 1 + expr(0.06, 0.35)
  // Soft hover halo — non-filled + enabled buttons at bold intensity only (filled
  // buttons carry their own emphasis; refined mode stays flat).
  const showHalo = !disabled && !filled && !reduce && exprHeavy(0.5)
  return (
    <motion.button
      type="button"
      aria-label={label}
      aria-disabled={disabled || undefined}
      title={label}
      onClick={disabled ? undefined : onClick}
      whileTap={disabled ? undefined : { scale: pressScale }}
      whileHover={disabled ? undefined : { scale: hoverScale }}
      animate={bloom ? { scale: [1, 1.18, 1] } : undefined}
      transition={bloom ? bounce.playful : spring.spatialFast}
      className={cx(
        'group relative inline-flex items-center justify-center rounded-pill transition-colors duration-100',
        disabled
          ? 'text-on-surface-var opacity-40 cursor-not-allowed'
          : filled
            ? 'bg-primary text-on-primary hover:bg-primary-emphasis'
            : active
              ? 'bg-surface-high text-on-surface'
              : 'text-on-surface-var hover:bg-surface-high hover:text-on-surface',
        className,
      )}
      style={{ width: size, height: size }}
    >
      {/* Hover halo — soft radial that fades in on hover (bold intensity only). */}
      {showHalo && (
        <span
          aria-hidden
          className="pointer-events-none absolute inset-0 rounded-pill opacity-0 transition-opacity duration-150 group-hover:opacity-100"
          style={{ background: 'radial-gradient(circle at center, color-mix(in srgb, var(--color-primary) 16%, transparent), transparent 70%)' }}
        />
      )}
      {iconKey ? (
        <AnimatePresence mode="wait" initial={false}>
          <motion.span
            key={iconKey}
            initial={{ scale: 0.4, opacity: 0, rotate: -30 }}
            animate={{ scale: 1, opacity: 1, rotate: 0 }}
            exit={{ scale: 0.4, opacity: 0, rotate: 30 }}
            transition={bounce.subtle}
            className="relative inline-flex"
          >
            <Icon size={20} strokeWidth={2} absoluteStrokeWidth />
          </motion.span>
        </AnimatePresence>
      ) : (
        <Icon size={20} strokeWidth={2} absoluteStrokeWidth className="relative" />
      )}
    </motion.button>
  )
}
