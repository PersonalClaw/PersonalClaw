import { type ReactNode, useEffect, useState } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import { expr, exprHeavy } from '../../design/motion'

/** A theme-tinted destructive-delete effect (P18b). Wrap the thing being deleted;
 *  flip `active` true to play it, then `onDone` fires when it settles so the caller
 *  can drop the item from state. It is the destructive counterpart to the app's
 *  additive motion — where a create springs in, a delete DISINTEGRATES:
 *
 *   • BOLD (exprHeavy): a `--color-negative`-tinted wash sweeps across via a clip
 *     mask while the content fragments — a slight scatter (rotate + drift, scaled by
 *     `expr()`) and blur — then collapses its height so the list closes the gap.
 *   • REFINED (below the exprHeavy gate): no scatter/blur — just the danger-tinted
 *     fade + height collapse (the refined tier DROPS the heavy effect, per the
 *     expressiveness contract, rather than shrinking it).
 *   • REDUCED-MOTION: instant — `onDone` on the next tick, no animation at all
 *     (the global CSS rule kills CSS transitions; JS/Motion must self-gate).
 *
 *  NO WebGL, no gooey filter (the deleted-primitives lesson) — pure Motion +
 *  CSS mask. Tint is always `var(--color-negative)`; offsets ride `expr()` (no raw
 *  px), so it honors the theme + the expressiveness knob. */
export function Disintegrate({
  active,
  onDone,
  children,
  className,
}: {
  /** Flip true to run the delete animation. */
  active: boolean
  /** Called once the effect settles (or immediately, reduced-motion) — drop the item. */
  onDone?: () => void
  children: ReactNode
  className?: string
}) {
  const reduce = useReducedMotion()
  const heavy = exprHeavy()
  // Reduced-motion: no animation — resolve on the next tick so callers can treat
  // `onDone` uniformly (always async) instead of branching on the motion mode.
  const [done, setDone] = useState(false)
  useEffect(() => {
    if (!active || !reduce || done) return
    const id = setTimeout(() => { setDone(true); onDone?.() }, 0)
    return () => clearTimeout(id)
  }, [active, reduce, done, onDone])

  if (reduce) {
    // Instant removal: once resolved, render nothing; until then, the content as-is.
    return done ? null : <div className={className}>{children}</div>
  }

  // Scatter amplitude is gated to the BOLD tier and scaled by the knob; refined
  // keeps only the tinted fade + collapse.
  const rotate = heavy ? expr(3, 0) : 0            // degrees
  const drift = heavy ? expr(8, 0) : 0             // % of own height, downward
  const blur = heavy ? expr(2, 0) : 0              // px of blur, bold-only

  // Blur is animated ONLY in the bold tier and ONLY with a plain monotonic tween —
  // never a spring. A spring settling on `filter` undershoots below its target, and
  // `blur()` rejects negative values (browser warns + drops the frame). Omitting the
  // key entirely when blur is 0 means the refined tier never touches `filter` at all.
  const animateProps = active
    ? {
        opacity: 0,
        // A downward drift + slight rotate = the fragments falling away (bold);
        // refined leaves these at 0 so it's a clean tinted fade.
        y: `${drift}%`,
        rotate,
        height: 0,
        marginTop: 0,
        marginBottom: 0,
        ...(blur > 0 ? { filter: `blur(${blur}px)` } : {}),
      }
    : { opacity: 1, y: 0, rotate: 0, height: 'auto', ...(heavy ? { filter: 'blur(0px)' } : {}) }
  return (
    <motion.div
      className={className}
      style={{ overflow: 'hidden', position: 'relative' }}
      animate={animateProps}
      transition={{
        // Everything rides a non-overshooting tween so nothing (least of all blur)
        // can undershoot its target; the whole effect is a directed dissolve, not a
        // springy wobble.
        duration: active ? 0.34 * (heavy ? 1 : 0.7) : 0.18,
        ease: [0.4, 0, 0.2, 1],
      }}
      onAnimationComplete={() => { if (active) onDone?.() }}
    >
      {children}
      {/* The danger wash — a `--color-negative`-tinted overlay that fades IN as the
          content leaves, reading as "burning away" in the theme's own danger tone.
          Pointer-events-none so it never blocks the settling row. */}
      <motion.span
        aria-hidden
        className="pointer-events-none absolute inset-0"
        initial={{ opacity: 0 }}
        animate={{ opacity: active ? expr(0.5, 0.4) : 0 }}
        transition={{ duration: 0.2 }}
        style={{
          background: 'linear-gradient(90deg, transparent, color-mix(in srgb, var(--color-negative) 55%, transparent))',
          // Bold tier gets a soft mask sweep so the wash reads as a directional
          // dissolve, not a flat tint; refined just fades the whole overlay. The
          // mask uses the `black`/`transparent` alpha keywords (a mask channel, not
          // a theme color — so no token needed / no hex).
          WebkitMaskImage: heavy
            ? 'linear-gradient(90deg, black 40%, transparent)'
            : undefined,
          maskImage: heavy ? 'linear-gradient(90deg, black 40%, transparent)' : undefined,
        }}
      />
    </motion.div>
  )
}
