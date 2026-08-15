import { type ReactNode, useId } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Search, X } from 'lucide-react'
import { physics } from '../design/motion'
import { cx } from './cx'

// ── The canonical compound-search field (design-system consistency, S2/S3) ──
// A search/filter input carries MORE chrome than the plain leading-icon TextInput
// (ui/forms.tsx): a leading magnifier AND a trailing affordance — a clear-X, a
// keyboard hint, or a spinner. The app hand-rolled this a dozen times with four
// different clear-button styles, three radii, and a five-step height/text spread.
// This primitive is the one home for that role. It is *codification, not redesign*
// (the plan's soul): every value below is a shape the app already ships; the clear-X
// converges on the owner-chosen spring-pop circle, and the geometry inherits the
// same blessed `left-3` / `pl-9` inset the TextInput leading-icon uses.
//
// Two structural variants, because the app ships two genuinely different search
// shells and folding one onto the other would BE a redesign:
//
//  • overlay (default) — the dominant list/page search: a relative box with the
//      magnifier absolutely pinned at left-3, the input carrying pl-9 pr-9, and the
//      trailing control absolutely pinned at right-2.5. Owns type="search",
//      Escape-to-clear, and the size→height/radius scale.
//  • inline — the ⌘K/⌘P palette shell: a rounded flex row (bg lives on the parent
//      container the caller styles) whose input is bg-transparent and whose leading
//      icon + trailing slot are flex children. Used where the field IS the row
//      (CommandPalette, PromptPalette, WorkspacePicker, CodeCockpit quick-open).
//
// The clear-X is built in for both variants (the overwhelmingly common affordance);
// `trailingSlot` adds a variant-specific extra (a `<kbd>esc</kbd>`, a spinner) that
// sits after it. Behavioral props are grown in lockstep with real adopters.

type SearchSize = 'sm' | 'md' | 'lg'

// overlay height + text + radius, matched to the shapes already shipping:
//   sm → h-8  / 0.8125rem / rounded-md   (dense in-panel filters)
//   md → h-9  / 0.8125rem / rounded-md   (side-panel / toolbar filters)
//   lg → h-10 / 0.9375rem / rounded-pill (the prominent list/page search bars) ← DEFAULT
// A pill at lg (the hero bars) and a soft md corner below it is exactly the split
// the app already draws; the scale just names it. text tracks the field family's
// blessed steps (0.8125 / 0.9375rem) — off-ramp sizes (0.75/0.78/0.85/0.95rem) are
// drift these adopters normalize ONTO the scale.
const OVERLAY_SIZE: Record<SearchSize, string> = {
  sm: 'h-8 text-[0.8125rem] rounded-md',
  md: 'h-9 text-[0.8125rem] rounded-md',
  lg: 'h-10 text-[0.9375rem] rounded-pill',
}
type SearchSurface = 'high' | 'container' | 'base'
const SEARCH_SURFACE: Record<SearchSurface, string> = {
  high: 'bg-surface-high',
  container: 'bg-surface-container',
  base: 'bg-surface',
}
// The inline variant carries no height (the caller's row sets padding), so `size`
// here selects only the text step — again the blessed steps: lg for the big ⌘K
// palette (0.9375rem), sm/md for the denser filter rows (0.8125rem). Off-ramp
// inline sizes (0.78/0.875rem) normalize onto these, mirroring the overlay scale.
const INLINE_TEXT: Record<SearchSize, string> = {
  sm: 'text-[0.8125rem]',
  md: 'text-[0.8125rem]',
  lg: 'text-[0.9375rem]',
}
// `type="search"` makes Chromium auto-render a native ::-webkit-search-cancel-button
// glyph once there's a value. This field owns its clear affordance (the spring-pop
// ClearButton for clearable fields, nothing for the opt-out palettes), so the native
// one is redundant chrome — it double-renders beside the overlay clear-X and gives the
// clearable={false} palettes a clear button they explicitly declined. Suppress it so
// every search field's clear behavior is exactly what this primitive draws.
const INPUT_CHROME = 'text-on-surface placeholder:text-on-surface-low outline-none [&::-webkit-search-cancel-button]:hidden'
// The focus ring is OVERLAY-ONLY. Overlay fields are the visible box, so the
// inset ring names their focus (matching ui/forms TextInput). The inline input
// is a transparent flex child of a caller-styled palette row whose focus is
// carried by the modal context — the shipped palettes drew no per-input ring,
// and an inset rectangle inside the round row would be new chrome (a redesign
// of a hero surface), so inline keeps outline-none with no ring.
const OVERLAY_FOCUS = 'focus:ring-2 focus:ring-inset focus:ring-primary/50'

/** A spring-pop clear-X (the owner-chosen canonical affordance): a soft circle that
 *  scale-pops in on the first keystroke and out on clear, with a whileTap squish.
 *  Rendered only while there's a value to clear. Shared by both variants; `dense`
 *  shrinks the hit area to sit inside a compact inline palette row. */
function ClearButton({ show, onClear, label, dense }: { show: boolean; onClear: () => void; label: string; dense?: boolean }) {
  return (
    <AnimatePresence>
      {show && (
        <motion.button type="button" onClick={onClear} aria-label={label}
          initial={{ scale: 0, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0, opacity: 0 }}
          transition={physics.snappy} whileTap={{ scale: 0.88 }}
          className={cx('inline-flex shrink-0 items-center justify-center rounded-full text-on-surface-low hover:bg-surface-highest hover:text-on-surface', dense ? 'size-5' : 'size-6')}>
          <X size={dense ? 13 : 14} />
        </motion.button>
      )}
    </AnimatePresence>
  )
}

interface SearchFieldProps {
  value: string
  onChange: (v: string) => void
  /** Forwarded to the input for a field that DRIVES a listbox elsewhere (the command palette, the
   *  composer menus): the field keeps focus while an option is "active", so assistive tech needs the
   *  relationship spelled out. Follows `ui/composer/MarkdownInput`'s documented choice — haspopup +
   *  controls + activedescendant, deliberately NOT `role="combobox"`. */
  ariaHasPopup?: 'listbox'
  ariaControls?: string
  ariaActiveDescendant?: string
  placeholder?: string
  /** Accessible name. Falls back to the placeholder when omitted — a search field
   *  outside a labeled Field must still name itself. */
  ariaLabel?: string
  autoFocus?: boolean
  /** A stable form `name` (also used as the id). Defaults to a generated one so the
   *  browser doesn't autofill a transient filter and each field is uniquely targetable. */
  name?: string
  /** Extra key handling layered on top of the built-in Escape-to-clear (e.g. Enter
   *  picks the first match, arrows navigate a result list). Runs first; if it calls
   *  preventDefault the built-in Escape handler is skipped. */
  onKeyDown?: (e: React.KeyboardEvent<HTMLInputElement>) => void
  /** Variant-specific trailing chrome shown AFTER the clear-X (a `<kbd>`, a spinner). */
  trailingSlot?: ReactNode
  /** Whether to render the built-in clear-X (default true). The ⌘K/⌘P command
   *  palettes clear by convention (Esc/backspace), not a button — they opt OUT so
   *  the primitive doesn't add chrome those hero surfaces never had. */
  clearable?: boolean
  /** overlay (default): absolute-pinned icon + clear over a solid field.
   *  inline: transparent input as a flex child of a caller-styled row. */
  variant?: 'overlay' | 'inline'
  // ── overlay-only ──
  size?: SearchSize
  surface?: SearchSurface
  // ── inline-only ──
  /** Leading magnifier glyph size for the inline variant (the palettes ship 13–17px
   *  to match their row density). Ignored by overlay (fixed 14). Defaults to 14. */
  inlineIconSize?: number
  /** Escape-to-clear on the inline variant (overlay always clears on Escape). The
   *  palettes each own their Escape (close the modal); opt in only where clearing wins. */
  clearOnEscape?: boolean
  /** Ref to the underlying input — palettes focus/select it on a shortcut. */
  inputRef?: React.Ref<HTMLInputElement>
  /** Passed through to the input (palettes disable autocorrect/-capitalize/spellcheck). */
  spellCheck?: boolean
  autoCapitalize?: string
  autoCorrect?: string
  /** Called on focus (CodeCockpit reopens its result list). */
  onFocus?: () => void
}

/** The one compound-search field. See the module header for the two variants. */
export function SearchField({
  value, onChange, placeholder, ariaLabel, autoFocus, name, onKeyDown, trailingSlot, clearable = true,
  variant = 'overlay', size = 'lg', surface = 'high',
  inlineIconSize = 14, clearOnEscape, inputRef, spellCheck, autoCapitalize, autoCorrect, onFocus,
  ariaHasPopup, ariaControls, ariaActiveDescendant,
}: SearchFieldProps) {
  const autoName = useId()
  const fieldName = name ?? `search-${autoName}`
  const label = ariaLabel ?? placeholder ?? 'Search'

  // Escape clears when there's a value to clear — overlay always, inline on opt-in.
  // A caller onKeyDown runs first and may preventDefault to keep its own Escape.
  const escapeClears = variant === 'overlay' || clearOnEscape
  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    onKeyDown?.(e)
    if (e.defaultPrevented) return
    if (e.key === 'Escape' && escapeClears && value) { e.preventDefault(); e.stopPropagation(); onChange('') }
  }

  // Chrome-invariant input props shared by both variants; each variant supplies the
  // layout-specific className.
  const inputProps = {
    ref: inputRef, value, onChange: (e: React.ChangeEvent<HTMLInputElement>) => onChange(e.target.value),
    onKeyDown: handleKeyDown, onFocus, type: 'search' as const, name: fieldName, id: fieldName,
    'aria-label': label, placeholder, autoFocus, spellCheck, autoCapitalize, autoCorrect,
    'aria-haspopup': ariaHasPopup, 'aria-controls': ariaControls, 'aria-activedescendant': ariaActiveDescendant,
  }
  const clearLabel = `Clear ${label.toLowerCase()}`

  if (variant === 'inline') {
    // Transparent input as a flex child; the caller styles the surrounding row
    // (bg, radius, padding) so the field slots into a bespoke palette shell.
    return (
      <>
        <Search size={inlineIconSize} className="pointer-events-none shrink-0 text-on-surface-low" />
        <input {...inputProps} className={cx('min-w-0 flex-1 bg-transparent', INPUT_CHROME, INLINE_TEXT[size])} />
        {clearable && <ClearButton show={!!value} onClear={() => onChange('')} label={clearLabel} dense />}
        {trailingSlot}
      </>
    )
  }

  // overlay: magnifier pinned left-3, input pl-9 pr-9, clear pinned right-2.5.
  return (
    <div className="relative w-full">
      <Search size={14} className="pointer-events-none absolute left-3 top-1/2 z-10 -translate-y-1/2 text-on-surface-low" />
      <input {...inputProps} className={cx('w-full pl-9 pr-9', INPUT_CHROME, OVERLAY_FOCUS, OVERLAY_SIZE[size], SEARCH_SURFACE[surface])} />
      <div className="absolute right-2.5 top-1/2 flex -translate-y-1/2 items-center gap-1">
        {clearable && <ClearButton show={!!value} onClear={() => onChange('')} label={clearLabel} />}
        {trailingSlot}
      </div>
    </div>
  )
}
