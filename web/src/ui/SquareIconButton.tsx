import { motion, AnimatePresence, useReducedMotion } from 'framer-motion'
import { Loader2, type LucideIcon } from 'lucide-react'
import { cx } from './cx'
import { spring, physics, expr } from '../design/motion'

/** Dense square icon button — the compact sibling of the round {@link IconButton}.
 *  A 28px (size-7) `rounded-md` hit area with a small glyph, for tight action
 *  clusters in list rows, card headers, and content toolbars where the 40px round
 *  pill is too large. Idle at ink-low; hover fills surface-high and brightens to
 *  ink; the `on` (selected/toggled) state carries the coral tint. Press springs in
 *  (expressiveness-scaled), yielding to reduced-motion — matching the animated
 *  icon-button doctrine.
 *
 *  Pass either an `icon` component or `children` (for glyphs that swap on state,
 *  e.g. spinner⇄wifi or a rotating chevron). `on` means selected; `disabled` means
 *  the action is currently unavailable (dim + inert) — kept distinct so a busy
 *  button reads as inert rather than a dead-click. `tone="danger"` is the
 *  destructive (delete/remove) variant: idle at ink-low, hover tints the glyph
 *  red with no fill — the restrained treatment those buttons already ship. */
export function SquareIconButton({
  icon: Icon, children, label, title, onClick, on, ariaExpanded, disabled, disabledReason, loading = false,
  tone = 'neutral', iconSize = 14, className,
}: {
  icon?: LucideIcon
  children?: React.ReactNode
  label: string
  /** Tooltip override — defaults to `label`. Use when the hover hint should differ
   *  from the accessible name (e.g. a disabled button explaining *why* it's gated). */
  title?: string
  onClick?: (e: React.MouseEvent) => void
  /** Selected/toggled — carries the coral tint (bg + text) and announces `aria-pressed`.
   *  For a control that REVEALS adjacent content, pass `ariaExpanded` instead: a disclosure and a
   *  toggle answer different questions, and this primitive only ever had the second one. */
  on?: boolean
  /** This button reveals adjacent content, and the content is currently shown.
   *
   *  🔑 SAME SPELLING AS `Button` AND `QuietButton`, deliberately — `ariaExpanded`, not a third name for
   *  the same question. Cycle 129 gave this primitive `on` → `aria-pressed` and measured 18 nodes gaining
   *  it on `#/settings/providers`; what it did not do (it did do it for `QuietButton`) was classify the
   *  callers. Three of them are disclosures — `ProviderCard`'s Configure, `WidgetFrame`'s iteration rail,
   *  `MultiInstanceCard`'s Edit — and two are true toggles (Bookmark, Pin), which keep `on`.
   *
   *  Passing this SUPPRESSES `aria-pressed`: a control that claims both states claims one of them
   *  falsely. The coral tint follows either, so nothing moves visually. */
  ariaExpanded?: boolean
  /** Action currently unavailable: 40% opacity, not-allowed, onClick suppressed. */
  disabled?: boolean
  /** WHY it is unavailable, when `disabled` is true. This primitive already keeps its tab
   *  stop (it maps `disabled` to `aria-disabled`, never the native attribute), so a keyboard
   *  user CAN land on it — and until now heard only the label. An icon-only button has no
   *  visible text to carry the reason either, so without this a gated one is doubly mute.
   *
   *  Rides `title` (appended after an em dash, after any `title` override), matching
   *  `Button.disabledReason`: `title` is the accessible DESCRIPTION here since the name comes
   *  from `aria-label`, so the reason is announced without polluting the name.
   *
   *  Omit it when the gate is self-evident or transient; a compound gate should pass it only
   *  for the branch it actually describes. */
  disabledReason?: string
  /** The action is IN FLIGHT — the opposite claim from `disabled`, which says "unavailable".
   *
   *  Measured before this prop existed: every async site in this tier passed `disabled={busy}`,
   *  so mid-flight the button dimmed to 40% with `cursor: not-allowed` and announced
   *  `aria-disabled` — and five of them then hand-rolled a spinner *inside* the dim
   *  (`{testing ? <Loader2 className="animate-spin"/> : <Wifi/>}`), which is the tell: the
   *  primitive was missing the state, so each caller invented half of it.
   *
   *  What it does: `aria-busy`, the glyph cross-fades under a centered spinner (`Button`'s
   *  treatment), the click is refused by the same `off = disabled || loading` guard, and the
   *  press spring stands down. It deliberately does NOT dim or set `aria-disabled` — a working
   *  button is not an unavailable one. Pass your idle glyph as usual; the spinner is the
   *  primitive's, so callers stop shipping their own. */
  loading?: boolean
  /** `danger` = destructive action: hover tints the glyph red (no fill). Ignored
   *  while `on` (a selected destructive button is not a pattern the app has). */
  tone?: 'neutral' | 'danger'
  /** Glyph size for the `icon` form (default 14). `children` size themselves. */
  iconSize?: number
  className?: string
}) {
  const reduce = useReducedMotion()
  // One guard for both inert reasons, exactly as `Button` spells it — otherwise `loading` would
  // trade a false "unavailable" for a double-fire.
  const off = !!disabled || loading
  const pressScale = reduce || off ? 1 : 1 - expr(0.1, 0.5)
  // The tint means "this is the live one" for both questions; the ARIA state is the one that differs.
  const lit = on || ariaExpanded
  return (
    <motion.button
      type="button"
      aria-label={label}
      aria-disabled={disabled || undefined}
      // `on` means "selected/toggled" and drove only the coral tint — the fifth primitive in this family
      // (after Button, HeaderControl, IconButton, FilterChip). Two callers pass it: the Edit buttons on
      // `#/settings/providers` and each multi-instance card, where "editing" vs "not editing" was a tint
      // and nothing else. `undefined` unless a caller opts in, so a plain icon action claims no state.
      aria-pressed={ariaExpanded === undefined ? on : undefined}
      aria-expanded={ariaExpanded}
      // "Working", not "unavailable". It is the only signal a screen-reader user gets: the spinner
      // is aria-hidden and the name deliberately never changes, so the action stays findable.
      aria-busy={loading || undefined}
      title={disabled && disabledReason ? `${title ?? label} — ${disabledReason}` : (title ?? label)}
      onClick={off ? undefined : onClick}
      whileTap={off ? undefined : { scale: pressScale }}
      transition={spring.spatialFast}
      className={cx(
        // `relative`: the loading spinner is an `absolute inset-0` overlay, and without a
        // positioned ancestor here it would centre itself on the nearest one — the row.
        'relative grid size-7 place-items-center rounded-md transition-colors',
        disabled
          ? 'text-on-surface-low opacity-40 cursor-not-allowed'
          : lit
            ? 'text-primary'
            : tone === 'danger'
              ? 'text-on-surface-low hover:text-danger'
              : 'text-on-surface-low hover:bg-surface-high hover:text-on-surface',
        // In flight keeps its full ink and colour; only the cursor changes. Dimming here is
        // exactly what made a working button read as dead.
        loading && !disabled && 'cursor-progress',
        className,
      )}
      style={lit && !disabled ? { background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' } : undefined}
    >
      {/* Glyph — cross-fades out under the spinner while loading (`Button`'s treatment). Always
          mounted, never keyed on `loading`, so the two directions cross-fade instead of remounting. */}
      <motion.span
        className="relative inline-flex"
        animate={{ opacity: loading ? 0 : 1, scale: loading ? 0.6 : 1 }}
        transition={spring.effects}
      >
        {Icon ? <Icon size={iconSize} /> : children}
      </motion.span>
      <AnimatePresence>
        {loading && (
          <motion.span
            aria-hidden
            className="absolute inset-0 grid place-items-center"
            initial={{ opacity: 0, scale: 0.6 }}
            animate={{ opacity: 1, scale: 1, transition: physics.snappy }}
            exit={{ opacity: 0, scale: 0.6, transition: spring.effects }}
          >
            <Loader2 size={iconSize} className="animate-spin" />
          </motion.span>
        )}
      </AnimatePresence>
    </motion.button>
  )
}
