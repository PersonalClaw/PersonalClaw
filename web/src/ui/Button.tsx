import { useRef, type ReactNode } from 'react'
import { AnimatePresence, motion, useMotionValue, useMotionTemplate, useReducedMotion } from 'framer-motion'
import { Loader2 } from 'lucide-react'
import { cx } from './cx'
import { spring, bounce, expr, exprHeavy } from '../design/motion'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
type Size = 'sm' | 'md' | 'lg'

const variants: Record<Variant, string> = {
  primary: 'bg-primary text-on-primary hover:bg-primary-emphasis',
  secondary: 'bg-surface-high text-on-surface hover:bg-surface-highest',
  ghost: 'bg-transparent text-on-surface hover:bg-surface-high',
  danger: 'bg-danger text-on-danger hover:opacity-90',
}

const sizes: Record<Size, string> = {
  sm: 'h-8 px-l text-[0.8125rem]',
  md: 'h-10 px-xl text-[0.9375rem]',
  lg: 'h-12 px-2xl text-[1.0625rem]',
}

/** The one shared button. Redesign-v2: bold, physical, expressiveness-scaled.
 *  Signature moments (all scale through the global `--expressiveness` knob and
 *  fully yield to reduced-motion):
 *   • spring press-in whose depth grows with expressiveness,
 *   • hover-lift,
 *   • a liquid pointer-tracking sheen on solid (primary/danger) buttons — a
 *     radial highlight following the cursor; dropped below the heavy-effect
 *     threshold so "refined" stays flat,
 *   • a `loading` state that cross-fades the label out for a centered spinner
 *     while preserving the button's width (no layout jump).
 *  Pill by default; `shape="squircle"` opts into the superellipse corner. No
 *  hardcoded colors/px — all via tokens. */
export function Button({
  children, variant = 'primary', size = 'md', shape = 'pill',
  loading = false, className, onClick, disabled, type = 'button',
}: {
  children: ReactNode
  variant?: Variant
  size?: Size
  shape?: 'pill' | 'squircle'
  loading?: boolean
  className?: string
  onClick?: () => void
  disabled?: boolean
  type?: 'button' | 'submit'
}) {
  const reduce = useReducedMotion()
  const ref = useRef<HTMLButtonElement>(null)
  const isSolid = variant === 'primary' || variant === 'danger'
  const off = !!disabled || loading

  // Pointer-tracked sheen origin (0..100% within the button) — Motion values so
  // the highlight follows the cursor with no per-move React re-render.
  const mx = useMotionValue(50)
  const my = useMotionValue(50)
  const sheen = useMotionTemplate`radial-gradient(circle at ${mx}% ${my}%, color-mix(in srgb, var(--color-on-primary) 22%, transparent), transparent 60%)`

  const onMove = (e: React.PointerEvent<HTMLButtonElement>) => {
    if (reduce) return
    const r = ref.current?.getBoundingClientRect()
    if (!r) return
    mx.set(((e.clientX - r.left) / r.width) * 100)
    my.set(((e.clientY - r.top) / r.height) * 100)
  }
  const onLeave = () => { mx.set(50); my.set(50) }

  // Press/hover depth scale with expressiveness (floor keeps a hint when refined).
  const pressScale = reduce ? 1 : 1 - expr(0.05, 0.4)
  const hoverScale = reduce ? 1 : 1 + expr(0.025, 0.4)
  const showSheen = isSolid && !reduce && exprHeavy(0.45) && !off

  return (
    <motion.button
      ref={ref}
      type={type}
      onClick={onClick}
      disabled={off}
      onPointerMove={onMove}
      onPointerLeave={onLeave}
      whileTap={off ? undefined : { scale: pressScale, transition: spring.spatialFast }}
      whileHover={off ? undefined : { scale: hoverScale, transition: bounce.subtle }}
      style={{ fontVariationSettings: '"wght" 470' }}
      className={cx(
        // whitespace-nowrap + shrink-0: a labelled pill must never wrap its text
        // or be squeezed below its content in a tight flex row.
        'relative inline-flex shrink-0 items-center justify-center gap-s overflow-hidden whitespace-nowrap font-[450] select-none',
        shape === 'squircle' ? 'squircle' : 'rounded-pill',
        'transition-colors duration-100 ease-[cubic-bezier(0.2,0,0,1)]',
        'disabled:opacity-40 disabled:pointer-events-none',
        variants[variant], sizes[size], className,
      )}
    >
      {/* Liquid pointer-tracking sheen (solid buttons, bold intensity only). */}
      {showSheen && (
        <motion.span aria-hidden className="pointer-events-none absolute inset-0" style={{ background: sheen, opacity: 0.9 }} />
      )}
      {/* Label — cross-fades out under a spinner while loading; width preserved. */}
      <motion.span
        className="relative inline-flex items-center gap-s"
        animate={{ opacity: loading ? 0 : 1, y: loading ? -4 : 0 }}
        transition={spring.effects}
      >
        {children}
      </motion.span>
      <AnimatePresence>
        {loading && (
          <motion.span
            aria-hidden
            className="absolute inset-0 grid place-items-center"
            initial={{ opacity: 0, scale: 0.6 }}
            animate={{ opacity: 1, scale: 1, transition: bounce.subtle }}
            exit={{ opacity: 0, scale: 0.6, transition: spring.effects }}
          >
            <Loader2 size={16} className="animate-spin" />
          </motion.span>
        )}
      </AnimatePresence>
    </motion.button>
  )
}
