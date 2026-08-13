import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { withWeight } from '../design/fontWeight'
import { motion, useReducedMotion } from 'framer-motion'
import { ChevronDown } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { spring, physics, expr } from '../design/motion'
import { fvs } from '../design/fontWeight'
import { Popover, MenuRow } from './Popover'
import { menuCursorKeydown, useMenuCursor } from '../lib/useMenuCursor'
import { useFieldLabelId } from './forms'

export interface SegOption { key: string; label?: string; tone?: string; icon?: LucideIcon; title?: string }

/** The ONE canonical segmented single-select for the whole app — a slider-style
 *  pill group. Use this for every "pick one of N" choice (filters, view
 *  switches, mode toggles, tab strips) so they look identical everywhere.
 *
 *  Selected styling: solid `--color-primary` by default (clear, high-contrast),
 *  or the option's own `tone` when supplied (for semantic coloring like task
 *  status). Inner option height is h-8 to line up with `Button size="sm"` and
 *  search inputs in a header row. `iconOnly` renders square icon buttons (for a
 *  compact view-switch); otherwise icon + label. */
export function Segmented({ options, value, onChange, iconOnly = false, ariaLabel, disabled = false, size = 'md', collapse }: {
  options: SegOption[]
  value: string
  onChange: (k: string) => void
  iconOnly?: boolean
  ariaLabel?: string
  disabled?: boolean
  /** 'sm' renders a compact, low-key strip (shorter, smaller text, muted
   *  surface) for inconspicuous secondary controls. */
  size?: 'md' | 'sm'
  /** Responsive behavior when the full strip is wider than its container:
   *  - unset (default): no measuring, always the inline strip (all existing
   *    callers are unaffected — the liquid layoutId indicator is untouched).
   *  - 'scroll': keep the strip, let it scroll horizontally on overflow.
   *  - 'menu': below the fit threshold, collapse to a single pill showing the
   *    active option that opens the full list in a Popover. */
  collapse?: 'scroll' | 'menu'
}) {
  const sm = size === 'sm'
  const reduce = useReducedMotion()
  // Claim the enclosing `Field`'s label, the same contract `TextInput` already honours: an explicit
  // `ariaLabel` always WINS, otherwise the published label id names the group. Measured before this —
  // a DOM census of 14 routes found **7 unnamed tablists** (9 named), so a screen-reader user heard
  // "Critical / High / Medium / Low" with no statement of WHAT was being chosen. Six of those sit
  // inside a `Field` whose visible label already says it ("Status", "Priority", "When", "Runs",
  // "Approval mode", "Trigger kind") — the label existed, the group just never claimed it.
  const fieldLabelId = useFieldLabelId()
  const claimsFieldLabel = !!fieldLabelId && !ariaLabel
  // Responsive collapse (collapse='menu'): measure the natural strip width against
  // the container and flip to the compact pill when it can't fit. A hidden probe
  // renders the real strip off-flow so we get its true intrinsic width regardless
  // of the current mode; a ResizeObserver on the container re-evaluates on resize.
  const wrapRef = useRef<HTMLDivElement>(null)
  const probeRef = useRef<HTMLDivElement>(null)
  const [collapsed, setCollapsed] = useState(false)
  useLayoutEffect(() => {
    if (collapse !== 'menu') { setCollapsed(false); return }
    const wrap = wrapRef.current
    const parent = wrap?.parentElement
    if (!wrap || !parent) return
    const measure = () => {
      const need = probeRef.current?.scrollWidth ?? 0
      if (need <= 0) return
      // AVAILABLE width = the wrap's own laid-out width PLUS the parent's remaining
      // free space (parent.clientWidth − sum of all children's laid-out widths). We
      // must add the wrap's current width back because when collapsed the wrap has
      // shrunk to the pill, so "free space" alone understates the room the strip
      // could reclaim. Measuring wrap.clientWidth ALONE was the bug: it tracks the
      // collapsed content, so the control could never see enough room to re-expand —
      // a one-way latch that stuck the dial collapsed even on a wide desktop.
      let childrenWidth = 0
      for (const c of Array.from(parent.children)) childrenWidth += (c as HTMLElement).getBoundingClientRect().width
      // free can be NEGATIVE when the row already overflows — don't clamp it, or an
      // overflowing expanded strip would wrongly stay expanded. avail then correctly
      // drops below `need` and collapses. (childrenWidth includes the wrap, so this
      // reduces to parent.clientWidth − siblings = the wrap's true available slot,
      // identical whether currently collapsed or expanded → no latch, reversible.)
      const free = parent.clientWidth - childrenWidth
      const avail = wrap.getBoundingClientRect().width + free
      setCollapsed(need > avail + 1)  // +1px slack so sub-pixel rounding doesn't flap
    }
    measure()
    // Observe BOTH the wrap and its parent (the flex row that actually constrains it),
    // so a resize either way re-evaluates — widening re-expands the strip, not just
    // narrowing collapsing it.
    const ro = new ResizeObserver(measure)
    ro.observe(wrap)
    ro.observe(parent)
    return () => ro.disconnect()
  }, [collapse, options, iconOnly, sm])
  // The sliding indicator carries personality: at bold expressiveness it settles
  // with a little overshoot/squish (physics.snappy); refined/reduced → a clean
  // snappy spring with no wobble. Tab press-scale also scales with the knob.
  const indicatorSpring = reduce || expr(1, 0) < 0.4 ? spring.spatialFast : physics.snappy
  const pressScale = reduce ? 1 : 1 - expr(0.06, 0.4)
  // A per-instance layoutId so the sliding active-indicator is scoped to THIS
  // segmented group (two on a page must not share/steal one indicator).
  const groupId = useId()
  // WAI-ARIA tablist keyboard nav: ←/→ (and Home/End) move selection between
  // tabs, with a roving tabindex (only the active tab is in the tab order). A
  // role="tablist" sets that expectation for screen-reader users, so honor it.
  const move = (e: React.KeyboardEvent, idx: number) => {
    let next = -1
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = (idx + 1) % options.length
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = (idx - 1 + options.length) % options.length
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = options.length - 1
    else return
    e.preventDefault()
    const o = options[next]
    if (o) {
      onChange(o.key)
      const el = e.currentTarget.parentElement?.children[next] as HTMLElement | undefined
      el?.focus()
    }
  }
  // Tabs are `shrink-0`. `size-8` / `px-m` set a tab's size but NOT its floor, so in a constrained
  // slot the flex parent squeezed them: measured on the #/skills header at 390px, where this control
  // deliberately collapses to icon-only, the two icon tabs rendered **15.3 x 32** instead of 32 x 32 —
  // under the 24px SC 2.5.8 minimum, with the 15px glyph filling the box edge to edge. Overflow is the
  // job of `collapse` ('scroll' / 'menu'), not of silently crushing every target: a strip that cannot
  // fit should scroll or fold, and a tab that is 15px wide is neither legible nor tappable.
  const strip = (
    <div role="tablist"
      aria-labelledby={claimsFieldLabel ? fieldLabelId : undefined}
      aria-label={claimsFieldLabel ? undefined : ariaLabel}
      className={`inline-flex items-center gap-0.5 rounded-pill ${sm ? 'p-0.5 bg-surface-container/60' : 'p-1 bg-surface-container'} ${disabled ? 'opacity-50 pointer-events-none' : ''}`}>
      {options.map((o, idx) => {
        const on = o.key === value
        const Icon = o.icon
        // The active fill is a shared-layout pill that SLIDES + squishes between
        // options (layoutId) instead of each button toggling its own background —
        // the liquid active-indicator. Text/icon color still flips per option.
        const activeBg = o.tone ? `color-mix(in srgb, ${o.tone} 20%, transparent)` : 'var(--color-primary)'
        const fg = on
          ? (o.tone ? o.tone : 'var(--color-on-primary)')
          : 'var(--color-on-surface-low)'
        return (
          <motion.button key={o.key} type="button" role="tab" aria-selected={on} disabled={disabled}
            tabIndex={on ? 0 : -1} onKeyDown={(e) => move(e, idx)}
            whileTap={disabled ? undefined : { scale: pressScale }}
            transition={spring.spatialFast}
            onClick={() => onChange(o.key)} title={o.title ?? o.label}
            className={`relative inline-flex shrink-0 items-center justify-center gap-1.5 rounded-pill transition-colors whitespace-nowrap ${iconOnly ? (sm ? 'size-6' : 'size-8') : (sm ? 'h-6 px-2.5 text-[0.75rem]' : 'h-8 px-m text-[0.8125rem]')}`}
            style={on ? withWeight({ color: fg }, 550) : { color: fg }}>
            {on && (
              <motion.span
                layoutId={`seg-${groupId}`}
                transition={indicatorSpring}
                className="absolute inset-0 rounded-pill"
                style={{ background: activeBg }}
              />
            )}
            <span className="relative z-10 inline-flex items-center gap-1.5">
              {Icon && <Icon size={sm ? 12 : 15} className="shrink-0" />}
              {!iconOnly && o.label}
            </span>
          </motion.button>
        )
      })}
    </div>
  )

  // Default (no collapse): the bare strip, exactly as before — no wrapper, no
  // measuring, so every existing consumer + the liquid indicator are untouched.
  if (!collapse) return strip

  // collapse='scroll': keep the strip but let it scroll horizontally when it
  // exceeds the container instead of wrapping/squishing.
  if (collapse === 'scroll') {
    return <div className="max-w-full overflow-x-auto no-scrollbar">{strip}</div>
  }

  // collapse='menu': render the strip when it fits; below the threshold swap to a
  // compact pill that opens the options in a Popover. A hidden probe (the real
  // strip, off-flow) always measures the intrinsic width for the fit decision.
  return (
    <div ref={wrapRef} className="relative min-w-0">
      <div ref={probeRef} aria-hidden className="pointer-events-none invisible absolute -z-10 whitespace-nowrap">{strip}</div>
      {collapsed ? (
        <CollapsedSegmented options={options} value={value} onChange={onChange} sm={sm} disabled={disabled} ariaLabel={ariaLabel} iconOnly={iconOnly} />
      ) : strip}
    </div>
  )
}

/** The collapsed form of a `collapse='menu'` Segmented — a single pill showing the
 *  active option, opening the full option list in a Popover. Used only below the
 *  fit threshold; the expanded strip (with its liquid layoutId indicator) is the
 *  normal presentation. */
function CollapsedSegmented({ options, value, onChange, sm, disabled, ariaLabel, iconOnly = false }: {
  options: SegOption[]; value: string; onChange: (k: string) => void
  sm: boolean; disabled: boolean; ariaLabel?: string
  /** Drop the pill's own label + chevron too, leaving just the active option's glyph.
   *  The collapsed pill is the LAST rung of the ladder, so when the caller is already
   *  at its icon-only density this has to shrink with it: labelled, the pill is ~119px,
   *  which still overflowed a phone header's ~42px control rail and rendered as a
   *  clipped "☰ Li…". Icon-only it is ~32-40px and fits. The name survives on
   *  `aria-label` (already set) and the popover still lists every option with its
   *  full label, so nothing becomes unnameable. */
  iconOnly?: boolean
}) {
  const active = options.find((o) => o.key === value) ?? options[0]
  const ActiveIcon = active?.icon
  // Without a glyph there would be nothing left to render, so keep the label as the
  // fallback even when the caller asked for icon-only.
  const bare = iconOnly && !!ActiveIcon
  return (
    <Popover
      placement="bottom"
      // 🔴 PORTAL, or this menu is INVISIBLE. Measured on `#/knowledge` at 430px with the list open:
      // `document.elementFromPoint` at the listbox's own centre returned the page's search INPUT, not
      // the menu — an `absolute z-30` flyout inside the header loses to the page body, which paints
      // over the header row. `ui/HeaderActions`' mode pill portals for exactly this reason and says
      // so; this sibling did not, so its options were operable by keyboard and unseeable by everyone.
      portal
      trigger={(open, toggle) => (
        <button type="button" onClick={toggle} disabled={disabled}
          aria-label={ariaLabel ?? active?.label ?? active?.key} aria-expanded={open}
          // Matches `ProjectPicker`, the sibling whose popup is also a listbox: the trigger says
          // a listbox is coming, not just that something expanded.
          aria-haspopup="listbox"
          title={bare ? `${ariaLabel ? `${ariaLabel}: ` : ''}${active?.label ?? active?.key}` : undefined}
          className={`inline-flex items-center gap-1.5 rounded-pill bg-surface-container text-on-surface transition-colors hover:bg-surface-high ${disabled ? 'opacity-50 pointer-events-none' : ''} ${bare ? (sm ? 'size-6 justify-center' : 'size-8 justify-center') : (sm ? 'h-6 px-2.5 text-[0.75rem]' : 'h-8 px-m text-[0.8125rem]')}`}
          style={fvs(550)}>
          {ActiveIcon && <ActiveIcon size={sm ? 12 : 15} className="shrink-0" />}
          {!bare && <span className="truncate">{active?.label ?? active?.key}</span>}
          {!bare && <ChevronDown size={sm ? 12 : 14} className="shrink-0 text-on-surface-low" />}
        </button>
      )}
    >
      {(close) => <CollapsedOptions options={options} value={value} onChange={onChange} close={close} ariaLabel={ariaLabel} />}
    </Popover>
  )
}

/** The collapsed pill's option list. A separate component because `ui/Popover` mounts its children
 *  only while open, which is what lets the cursor hook treat MOUNT as "opened" — no open flag has to
 *  be threaded back out of the Popover.
 *
 *  Before this it was a bare `role="listbox"`: measured on `#/knowledge` at 430px (where the strip
 *  collapses), focus stayed on the trigger, ArrowDown did nothing, and all five options carried
 *  `tabindex="0"`. Escape/close already restored focus to the trigger — that half was Popover's. */
function CollapsedOptions({ options, value, onChange, close, ariaLabel }: {
  options: SegOption[]; value: string; onChange: (k: string) => void; close: () => void; ariaLabel?: string
}) {
  const listRef = useRef<HTMLDivElement>(null)
  const { move, tabIndexFor } = useMenuCursor({
    containerRef: listRef,
    count: options.length,
    openKey: 'open',
    initialIndex: Math.max(0, options.findIndex((o) => o.key === value)),
  })
  useEffect(() => {
    // Escape is Popover's (it closes one layer and restores the trigger); this adds the arrows.
    const onKey = (e: KeyboardEvent) => { menuCursorKeydown(e, { move, dismiss: close }) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [move, close])
  return (
    <div ref={listRef} role="listbox" aria-orientation="vertical" aria-label={ariaLabel}>
      {options.map((o, i) => (
        <MenuRow key={o.key} role="option" icon={o.icon ? <o.icon size={15} /> : undefined}
          label={o.label ?? o.key} selected={o.key === value} tabIndex={tabIndexFor(i)}
          onClick={() => { onChange(o.key); close() }} />
      ))}
    </div>
  )
}
