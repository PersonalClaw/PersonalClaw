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
  icon: Icon, label, title, onClick, active, filled, size = 40, iconSize = 20, className, disabled, disabledReason, iconKey, bloom,
}: {
  icon: LucideIcon
  label: string
  /** Tooltip override — defaults to `label`, exactly as `ui/SquareIconButton` already does.
   *
   *  Needed when the NAME has to carry a row's subject and the tooltip should stay short: on
   *  `#/notifications` the name is `Delete: <notification title>` (83 rows, otherwise 83 identical
   *  names in the AX tree) while the hover hint stays "Delete". */
  title?: string
  onClick?: (e: React.MouseEvent) => void
  active?: boolean
  filled?: boolean
  size?: number
  /** Glyph size within the hit area (default 20). Dense toolbars/inline chrome
   *  use smaller glyphs (12–16) in a compact hit area — the prop lets those
   *  sites adopt the primitive instead of hand-rolling a <button>. */
  iconSize?: number
  className?: string
  // Dim + block the button (no hover, not-allowed cursor) when its action is
  // currently unavailable — so a gated icon button reads as inert instead of a
  // silent dead-click. onClick is suppressed regardless of what's passed.
  disabled?: boolean
  // WHY it is unavailable, when `disabled` is true. This primitive already keeps its tab
  // stop (it maps `disabled` to `aria-disabled`, never the native attribute), so a keyboard
  // user CAN land on it — and until now heard only the label. An icon-only button has no
  // visible text to carry the reason either, so without this a gated one is doubly mute.
  //
  // Rides `title`, appended after an em dash — matching `Button.disabledReason`. `title` is
  // the accessible DESCRIPTION here (the name comes from `aria-label`), so the reason is
  // announced without polluting the name the action is findable by.
  //
  // Omit it when the gate is self-evident or transient (an in-flight save already reads as
  // busy); a compound gate should pass it only for the branch it actually describes.
  disabledReason?: string
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
      // Same contract as `HeaderControl` below/above: `active` drove a tint only. The composer's optimize
      // and mic buttons are its two `active` callers, and "recording" vs "not recording" is exactly the
      // state a screen-reader user cannot infer from a tint. `undefined` unless a caller opts in.
      aria-pressed={active}
      title={disabled && disabledReason ? `${title ?? label} — ${disabledReason}` : (title ?? label)}
      onClick={disabled ? undefined : onClick}
      whileTap={disabled ? undefined : { scale: pressScale }}
      whileHover={disabled ? undefined : { scale: hoverScale }}
      animate={bloom ? { scale: [1, 1.18, 1] } : undefined}
      transition={bloom ? bounce.playful : spring.spatialFast}
      className={cx(
        // `shrink-0`: the size below is set via inline `width`/`height`, and an inline width is NOT a
        // floor for a flex child. Measured at 390px on the settings sub-routes, where this button sits in
        // a breadcrumb row, `size={36}` rendered **20x36** — under the 24px SC 2.5.8 minimum, with the
        // next target 4px away so the undersized-target spacing exception does not apply either. Same
        // shape as the `Segmented` tab fix: a declared size means nothing until something says it cannot
        // be taken away. Census at 390px across 12 surfaces: 2 squeezed sites, both this control.
        'group relative inline-flex shrink-0 items-center justify-center rounded-pill transition-colors duration-100',
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
            <Icon size={iconSize} strokeWidth={2} absoluteStrokeWidth />
          </motion.span>
        </AnimatePresence>
      ) : (
        <Icon size={iconSize} strokeWidth={2} absoluteStrokeWidth className="relative" />
      )}
    </motion.button>
  )
}
