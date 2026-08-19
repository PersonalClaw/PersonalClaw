import { motion, useReducedMotion } from 'framer-motion'
import type { LucideIcon } from 'lucide-react'
import { cx } from './cx'
import { spring, expr } from '../design/motion'

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
  icon: Icon, children, label, title, onClick, on, ariaExpanded, disabled, disabledReason,
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
  /** `danger` = destructive action: hover tints the glyph red (no fill). Ignored
   *  while `on` (a selected destructive button is not a pattern the app has). */
  tone?: 'neutral' | 'danger'
  /** Glyph size for the `icon` form (default 14). `children` size themselves. */
  iconSize?: number
  className?: string
}) {
  const reduce = useReducedMotion()
  const pressScale = reduce || disabled ? 1 : 1 - expr(0.1, 0.5)
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
      title={disabled && disabledReason ? `${title ?? label} — ${disabledReason}` : (title ?? label)}
      onClick={disabled ? undefined : onClick}
      whileTap={disabled ? undefined : { scale: pressScale }}
      transition={spring.spatialFast}
      className={cx(
        'grid size-7 place-items-center rounded-md transition-colors',
        disabled
          ? 'text-on-surface-low opacity-40 cursor-not-allowed'
          : lit
            ? 'text-primary'
            : tone === 'danger'
              ? 'text-on-surface-low hover:text-danger'
              : 'text-on-surface-low hover:bg-surface-high hover:text-on-surface',
        className,
      )}
      style={lit && !disabled ? { background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' } : undefined}
    >
      {Icon ? <Icon size={iconSize} /> : children}
    </motion.button>
  )
}
