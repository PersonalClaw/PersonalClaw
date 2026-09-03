import type { ReactNode, MouseEvent } from 'react'
import type { LucideIcon } from 'lucide-react'
import { cx } from './cx'

type Size = 'xs' | 'sm' | 'inherit'
const SIZE: Record<Size, string> = {
  xs: 'text-[0.75rem]',      // dense chrome (queue chips, VoicePanel hints, FilterMenu "Clear")
  sm: 'text-[0.8125rem]',    // standalone links (Show more, View all loops, external "Open")
  inherit: '',               // inline inside a running sentence — take the paragraph's size
}

// ── The ink is decided by the GROUND, which is why it is a prop ────────────────────────────────────
//
// The accent ink passes or fails AA depending only on what is painted behind it. Computed across the
// curated scheme set (`design/schemes.ts`), light mode, small text:
//
//   ground                        `primary`                      `primary-emphasis`
//   --color-surface (white)       4.83  passes all 12            passes
//   --color-canvas                4.37  FAILS 7 of 12            4.82 worst, coral 6.0 — passes all 12
//   --color-surface-low           4.46  FAILS 6 of 12            4.92 worst — passes all 12
//   --color-surface-high          4.26  FAILS 10 of 12           4.70 worst — passes all 12
//
// So `primary` is only safe on `--color-surface`. Anywhere else, pass `ink="emphasis"` — the shade the
// design system already ships for exactly this (`accentOnCanvas.test.ts`, 10+ call sites). It is a prop
// rather than a new default because most links here DO sit on a surface and pass, and re-inking all of
// them would pre-empt the owner's standing "coral as accent text" decision.
type Ink = 'primary' | 'emphasis'
const INK: Record<Ink, string> = {
  primary: 'text-primary',                    // only correct on `--color-surface`
  emphasis: 'text-primary-emphasis',          // every other ground
}

/** The inline text-link idiom — a coral `text-primary` label that underlines on
 *  hover, used for in-sentence navigations ("Browse the Store"), quiet inline
 *  actions ("Remove from queue", "View all loops"), and the occasional real
 *  `<a>` (an external task URL, a memory deep-link). ~16 sites across the app
 *  hand-rolled `text-primary hover:underline` with drifting size classes,
 *  element types, icon slots, and margins; this is the single source.
 *
 *  Renders a `<button type="button">` by default, or an `<a>` when `href` is set
 *  (`external` adds `target=_blank rel="noopener noreferrer"` for off-app URLs;
 *  omit it for in-app hash links). An `icon` opts the row into
 *  `inline-flex items-center gap-1` and sits before the label, or after it when
 *  `iconPosition="trailing"` — plain (icon-less) links stay bare inline so they
 *  flow inside running text without a baseline shift. `size` defaults to
 *  `inherit` (the in-sentence case); pass `xs`/`sm` for standalone links. Extra
 *  layout (`ml-auto`, `mt-1.5`, `normal-case`) rides through `className`.
 *
 *  `ink` defaults to `primary`, which is only AA-safe on `--color-surface`; pass
 *  `ink="emphasis"` when the link is painted on any other ground (see the table
 *  above). Do NOT push the ink through `className` — two colour utilities on one
 *  element resolve by stylesheet order, not by the order you wrote them, so it
 *  works or does not depending on Tailwind's output. */
export function TextLink({
  children, href, external = false, onClick, icon: Icon, iconPosition = 'leading',
  iconSize = 13, size = 'inherit', ink = 'primary', disabled = false, title, className,
  'aria-label': ariaLabel,
}: {
  children: ReactNode
  href?: string
  external?: boolean
  onClick?: (e: MouseEvent<HTMLElement>) => void
  icon?: LucideIcon
  iconPosition?: 'leading' | 'trailing'
  iconSize?: number
  size?: Size
  ink?: Ink
  disabled?: boolean
  title?: string
  className?: string
  /** Accessible name when the visible label alone does not say what the link
   *  opens ("open" beside a row) — same passthrough precedent as forms' id. */
  'aria-label'?: string
}) {
  // `py-0.5 -my-0.5`: measured 10 of these at **20px tall** inside `#/tasks`' clickable rows, where
  // SC 2.5.8's spacing exception cannot apply — a link nested in a larger target can never clear it.
  // Vertical PADDING (not a min-height) grows the hit box to 24px without changing the display type:
  // switching to `inline-flex` made the element an atomic inline box and re-rounded the text baseline,
  // which moved 0.83% of the pixels on `#/tasks` for no reason. The negative margin hands the 4px
  // back, so the line keeps its rhythm and every other TextLink surface stays byte-identical.
  //
  // `py-1` (4px a side) rather than `py-0.5`: an inline box's rect is the union of its line boxes, and
  // a 19.99px line box plus 2+2 measured **23.99** — passing to the eye, failing the 24px floor. Real
  // headroom beats a value that is only exactly right when the font rounds kindly.
  const cls = cx(
    INK[ink],
    'hover:underline disabled:opacity-50 py-1 -my-1',
    Icon && 'inline-flex items-center gap-1',
    SIZE[size],
    className,
  )
  const body = Icon
    ? (iconPosition === 'trailing'
        ? <>{children} <Icon size={iconSize} /></>
        : <><Icon size={iconSize} /> {children}</>)
    : children

  if (href !== undefined) {
    return (
      <a href={href} onClick={onClick} title={title} aria-label={ariaLabel} className={cls}
        {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}>
        {body}
      </a>
    )
  }
  return (
    <button type="button" onClick={onClick} disabled={disabled} title={title} aria-label={ariaLabel} className={cls}>
      {body}
    </button>
  )
}
