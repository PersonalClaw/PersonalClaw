import { useRef, type ReactNode } from 'react'
import { AnimatePresence, motion, useMotionValue, useMotionTemplate, useReducedMotion } from 'framer-motion'
import { Loader2 } from 'lucide-react'
import { cx } from './cx'
import { spring, bounce, expr, exprHeavy } from '../design/motion'
import { fvs } from '../design/fontWeight'

type Variant = 'primary' | 'tonal' | 'secondary' | 'ghost' | 'danger'
type Size = 'xs' | 'sm' | 'md' | 'lg'

const variants: Record<Variant, string> = {
  primary: 'bg-primary text-on-primary hover:bg-primary-emphasis',
  // tonal: the primary-tinted chip CTA (Material's tonal button) — the pattern
  // pages kept hand-rolling as `bg-primary/15 text-primary hover:bg-primary/25`.
  tonal: 'bg-primary/15 text-primary hover:bg-primary/25',
  secondary: 'bg-surface-high text-on-surface hover:bg-surface-highest',
  ghost: 'bg-transparent text-on-surface hover:bg-surface-high',
  danger: 'bg-danger text-on-danger hover:opacity-90',
}

const sizes: Record<Size, string> = {
  // xs: dense in-panel chrome (cockpit banners, error retries, inline strips) —
  // the tier whose absence made pages hand-roll <button>s.
  xs: 'h-7 px-m text-[0.8125rem]',
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
  loading = false, className, onClick, disabled, type = 'button', title, ariaExpanded, ariaPressed,
  disabledReason,
}: {
  children: ReactNode
  variant?: Variant
  size?: Size
  shape?: 'pill' | 'squircle'
  loading?: boolean
  className?: string
  onClick?: (e: React.MouseEvent<HTMLButtonElement>) => void
  disabled?: boolean
  type?: 'button' | 'submit'
  title?: string
  // A disclosure/toggle button announces its state to assistive tech. Optional so the common
  // action button carries no misleading `aria-expanded`; set it only when the button folds
  // content (e.g. the runs-inbox "Show N suppressed" archive toggle).
  ariaExpanded?: boolean
  // A button acting as a SELECTED/UNSELECTED choice (a list row that stays chosen, a view toggle)
  // must announce that state, or a screen-reader user hears an identical label for the row they are
  // on and the row they are not. Separate from `ariaExpanded` on purpose: expanded means "reveals
  // content", pressed means "is the current selection", and one attribute cannot carry both.
  ariaPressed?: boolean
  // WHY this button is unavailable, when `disabled` is true.
  //
  // A native `disabled` button is removed from the tab order entirely, so a keyboard user
  // cannot reach it to hear anything — they tab straight past the action they are looking for
  // with no way to learn what is missing. Measured on the New-project modal: the Create button
  // was `disabled`, `title: null`, `aria-describedby: null`, and `NOT focusable`.
  //
  // Given a reason, the button stays REACHABLE and announces it: `aria-disabled` (semantically
  // unavailable, still focusable) instead of the native attribute, with the click suppressed in
  // the handler. That is the standard trade — the native attribute is stronger protection but
  // silences the control, and a form submit that cannot explain itself is the worse failure.
  //
  // Omit it and nothing changes: `disabled` stays native, which is right for a button whose
  // unavailability is self-evident from context (a Delete that needs a selection).
  disabledReason?: string
}) {
  const reduce = useReducedMotion()
  const ref = useRef<HTMLButtonElement>(null)
  const isSolid = variant === 'primary' || variant === 'danger'
  const off = !!disabled || loading
  // Reachable-but-unavailable only when a reason exists to announce; otherwise stay native.
  const softOff = off && !!disabledReason && !loading

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
      title={softOff ? [title, disabledReason].filter(Boolean).join(' — ') : title}
      aria-expanded={ariaExpanded}
      aria-pressed={ariaPressed}
      // `loading` cross-fades the label to opacity 0 and swaps in an aria-hidden spinner, so
      // sighted users see the action is in flight and everyone else got NO signal at all —
      // the button just went quiet and disabled while keeping its original name. `aria-busy`
      // is the state that says "working"; measured 0 buttons in the app carrying it before.
      aria-busy={loading || undefined}
      // The reason rides `title`, NOT an aria-describedby target inside the button.
      // Measured: an sr-only <span> in the button body is CONCATENATED into the accessible
      // name — "Create project" became "Create projectEnter a name first", so the action
      // stopped being findable by its own name. A describedby target outside the button would
      // need a wrapper element at 100+ call sites. `title` is already the kit's convention
      // (ruled cycle 37) and is both the sighted tooltip and the AT description.
      // aria-disabled when there is a reason to announce (keeps the tab stop), native
      // `disabled` otherwise. Both paths must refuse the click.
      aria-disabled={softOff || undefined}
      onClick={softOff ? (e) => e.preventDefault() : onClick}
      disabled={softOff ? undefined : off}
      onPointerMove={onMove}
      onPointerLeave={onLeave}
      whileTap={off ? undefined : { scale: pressScale, transition: spring.spatialFast }}
      whileHover={off ? undefined : { scale: hoverScale, transition: bounce.subtle }}
      style={fvs(470)}
      className={cx(
        // whitespace-nowrap + shrink-0: a labelled pill must never wrap its text
        // or be squeezed below its content in a tight flex row.
        'relative inline-flex shrink-0 items-center justify-center gap-s overflow-hidden whitespace-nowrap font-[450] select-none',
        shape === 'squircle' ? 'squircle' : 'rounded-pill',
        'transition-colors duration-100 ease-[cubic-bezier(0.2,0,0,1)]',
        // aria-disabled needs the same dimming as the native attribute, and must not
        // swallow pointer events (a hover has to reveal the title/tooltip).
        'disabled:opacity-40 disabled:pointer-events-none aria-disabled:opacity-40 aria-disabled:cursor-not-allowed',
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
