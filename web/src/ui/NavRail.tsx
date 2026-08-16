import { useEffect, useRef, useState } from 'react'
import { withWeight } from '../design/fontWeight'
import { ChevronDown, type LucideIcon } from 'lucide-react'
import { motion } from 'framer-motion'
import { cx } from './cx'
import { fvs } from '../design/fontWeight'
import { Wordmark, Spark } from './Spark'
import { usePersonality } from '../app/personality'
import { spring } from '../design/motion'

export interface NavItem {
  id: string
  label: string
  icon: LucideIcon
  badge?: string
  /** WHAT the badge counts, as a phrase — "1 active loop", "2 app updates available".
   *
   *  A bare number on a nav item is read as a count of that destination's CONTENTS, and on
   *  Projects that reading is simply wrong: the badge is the active-LOOP count, so it showed "1"
   *  beside a list of five projects.
   *
   *  It also fixes a silent loss. The button carries `aria-label={item.label}`, and an aria-label
   *  OVERRIDES the element's text — so the badge span was announced **nowhere**. Measured on
   *  #/projects: visible text "Projects1", accessible name "Projects", title null. Sighted users
   *  got an ambiguous number; screen-reader users got no number at all.
   *
   *  Supply it only where the caller genuinely knows the unit. Where it is absent the raw count
   *  is still announced (see below) — announcing "1" is worse than "1 active loop" but far better
   *  than dropping the signal, and it claims nothing the shell cannot back up. */
  badgeLabel?: string
  section?: string
  /** Pinned to the bottom of the rail (rendered after the flex spacer, above the
   *  system widget) instead of inline in scroll order — e.g. Settings. */
  pinBottom?: boolean
}

/** The progressive-disclosure control (ONBOARDING-UX C4) — the rail's own way in and out of
 *  "show everything", sitting at the end of scroll order.
 *
 *  The rail is deliberately dumb about the model: it is handed the already-filtered `items`
 *  plus this, so which surfaces are starter, which are pinned and how pins are persisted all
 *  live in ONE place (`app/navDisclosure.ts`) rather than half here.
 *
 *  Bidirectional on purpose. "One click to expand permanently" is the point, but a one-way
 *  door means a user who expands out of curiosity has no local undo and must go hunting in
 *  Settings — so the same control collapses again, and the Appearance toggle is its mirror,
 *  not its only home. */
export interface NavDisclosureControl {
  /** True while every surface shows (expert mode). */
  expanded: boolean
  /** How many surfaces the collapsed rail holds back — what expanding reveals, and what
   *  collapsing would hide. Zero renders NO control: a disclosure that discloses nothing is
   *  a button that appears to do something and does not. */
  moreCount: number
  onToggle: () => void
}

const W_KEY = 'nav-width-v2'
const MIN_W = 172
const MAX_W = 380
const COLLAPSED_W = 64
// Mobile overlay-drawer width — a comfortable touch target, capped under a phone's
// portrait width so the scrim always shows (tap-to-close affordance stays reachable).
const OVERLAY_W = 264
// Default to a snug, content-fitting width (the labels are short); the user can
// drag the right edge wider if they want more room.
const DEFAULT_W = 196

/** Side navigation — drag-resizable, persisted. Collapse is CONTROLLED by the
 *  shell (the collapse/expand toggle lives in the main area's top-left, not on
 *  the rail). When collapsed → icon-only 64px rail. Bottom: a live system
 *  health widget. No operator/identity footer (single-user; Settings is in nav). */
export function NavRail({
  items, activeId, onSelect, collapsed, overlay = false, overlayOpen = false, onScrimClick, disclosure,
}: {
  items: NavItem[]
  activeId: string
  onSelect: (id: string) => void
  collapsed: boolean
  /** Progressive disclosure over the rail (C4). Omit it and the rail renders exactly the
   *  items it is given, with no expander — the shape every non-shell caller wants. */
  disclosure?: NavDisclosureControl
  /** Mobile: render the rail as a fixed OVERLAY drawer (out of layout flow) instead
   *  of an in-flow column, so an expanded rail doesn't squeeze the page. */
  overlay?: boolean
  /** Overlay drawer is expanded (slid in). When false the drawer is off-screen and no
   *  scrim shows; the page is full-bleed. */
  overlayOpen?: boolean
  /** Tap the scrim behind the open overlay → close the drawer. */
  onScrimClick?: () => void
}) {
  const { wordmarkLabel } = usePersonality()
  const [width, setWidth] = useState(() => {
    const v = Number(localStorage.getItem(W_KEY))
    return v >= MIN_W && v <= MAX_W ? v : DEFAULT_W
  })
  const dragging = useRef(false)

  useEffect(() => { if (!collapsed) localStorage.setItem(W_KEY, String(width)) }, [width, collapsed])

  // drag-resize (mirrors SidePanel's handle pattern); disabled while collapsed
  useEffect(() => {
    if (collapsed) return
    const onMove = (e: MouseEvent) => { if (dragging.current) setWidth(Math.max(MIN_W, Math.min(MAX_W, e.clientX))) }
    const onUp = () => { if (dragging.current) { dragging.current = false; document.body.style.cursor = '' } }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp) }
  }, [collapsed])

  const w = collapsed ? COLLAPSED_W : width
  let lastSection: string | undefined

  // Top (scroll-order) items vs bottom-pinned items (e.g. Settings).
  const topItems = items.filter((i) => !i.pinBottom)
  const pinnedItems = items.filter((i) => i.pinBottom)

  // ONE geometry recipe for every row in the rail — the nav items and the disclosure control
  // that sits at the end of them. Hand-tuning a second copy is how a 32px row becomes a 34px
  // row that only looks wrong next to its neighbour.
  const rowCls = (tone: string) => cx(
    'group relative flex items-center gap-s w-full rounded-pill text-left transition-colors duration-100',
    collapsed ? 'justify-center px-0' : 'px-s',
    tone,
  )

  const renderItem = (item: NavItem, withSection: boolean) => {
    const showSection = withSection && !collapsed && item.section && item.section !== lastSection
    if (withSection) lastSection = item.section
    const active = item.id === activeId
    const Icon = item.icon
    // What the badge means, if there is one. Falls back to the bare count so an SDK-set app badge
    // (whose unit is app-defined and unknown to the shell) still reaches assistive tech.
    const badgeHint = item.badge ? (item.badgeLabel ?? item.badge) : undefined
    return (
      <div key={item.id}>
        {showSection && (
          <div className="px-s pt-l pb-1 text-[0.8125rem] uppercase tracking-wide text-on-surface"
            style={{ opacity: 0.65, ...fvs(400) }}>
            {item.section}
          </div>
        )}
        <motion.button
          type="button" onClick={() => onSelect(item.id)} whileTap={{ scale: 0.98 }} transition={spring.spatialFast}
          // The badge rides the accessible NAME because an aria-label overrides the element's
          // text, so a badge span inside the button is announced nowhere. Same composition the
          // notifications bell already uses ("Notifications, 3 unread" + a title spelling it out).
          // `badgeLabel` when the caller knows the unit; the bare count when it does not — the
          // fallback still carries the signal instead of silently dropping it.
          // Collapsed, the label is not visible either, so the title carries both. The collapsed
          // rail also replaces the number with a bare dot — which conveyed nothing at all until
          // the hint gave it something to say.
          title={collapsed ? (badgeHint ? `${item.label}, ${badgeHint}` : item.label) : badgeHint}
          aria-label={badgeHint ? `${item.label}, ${badgeHint}` : item.label}
          // The active item was distinguished ONLY visually — weight 470 vs 400 and a tinted
          // background. Measured on #/tasks: 18 nav buttons, 1 visually distinct, 0 announcing
          // anything, so a screen-reader user heard eighteen identical buttons with no sense of
          // where they were. `aria-current="page"` is the navigation token (NOT aria-selected,
          // which belongs to listbox/tab options — the app already uses that correctly in
          // Segmented, ProjectPicker, SlashMenu, MentionMenu and ChatActivityPanel).
          aria-current={active ? 'page' : undefined}
          className={rowCls(active ? 'text-on-surface' : 'text-on-surface-var hover:bg-surface-low/60 hover:text-on-surface')}
          style={withWeight({ height: 32 }, active ? 470 : 400)}>
          {/* Springy active pill — a single shared-layout element that SLIDES from
              the previously-active item to this one (layoutId), instead of each
              item toggling its own background. Sits behind the icon/label. */}
          {active && (
            <motion.span
              layoutId="nav-active-pill"
              transition={spring.spatialDefault}
              className="absolute inset-0 rounded-pill bg-surface-low"
            />
          )}
          <span className="relative z-10 shrink-0 inline-flex">
            <Icon size={18} strokeWidth={2} />
            {/* Collapsed rail has no room for the pill badge — show a dot so
                a count (e.g. goals running) is still visible. */}
            {collapsed && item.badge && (
              <span className="absolute -right-1 -top-1 size-2 rounded-pill ring-2 ring-surface" style={{ background: 'var(--color-primary)' }} />
            )}
          </span>
          {!collapsed && <span className="relative z-10 flex-1 truncate text-[0.9375rem]">{item.label}</span>}
          {!collapsed && item.badge && (
            <span className="relative z-10 inline-flex h-5 items-center rounded-pill px-s text-[0.75rem] text-on-surface"
              style={{ background: 'color-mix(in srgb, var(--color-on-surface) 12%, transparent)' }}>
              {item.badge}
            </span>
          )}
        </motion.button>
      </div>
    )
  }

  // The rail body — shared between the in-flow desktop column and the mobile overlay
  // drawer. In overlay mode it always shows full labels (a drawer has room; icon-only
  // makes no sense once you've deliberately opened it).
  const showFull = overlay ? true : !collapsed
  const railBody = (
    <nav className="flex h-full flex-col gap-1 overflow-y-auto overflow-x-hidden px-m py-l"
      style={{ width: overlay ? OVERLAY_W : w, background: 'var(--color-rail)' }}>
      {/* header — logo (the collapse toggle lives in the main area, not here) */}
      <div className={cx('flex items-center pb-m', showFull ? 'px-s' : 'justify-center')}>
        {/* The wordmark tracks the active PERSONALITY's label (default: PersonalClaw). */}
        {showFull ? <Wordmark label={wordmarkLabel} /> : <Spark size={22} />}
      </div>

      {/* Section headers belong to the EXPANDED rail. Measured on the starter rail they read as
          noise: five rows carrying "PLATFORM" over Inbox alone and "APPS" over Store alone —
          a heading per item, which groups nothing. The starter rail is one curated group by
          construction (essentials + what you have opened), and the sections it has are
          "starter" and "everything". Expert keeps Platform / Capabilities / Apps untouched. */}
      {topItems.map((item) => renderItem(item, !disclosure || disclosure.expanded))}

      {/* Progressive disclosure (C4) — the last row of scroll order, so it reads as "…and the
          rest" rather than as a section of its own. `aria-expanded` because it genuinely
          reveals adjacent content (the repo's own distinction: a MODE toggle reveals nothing
          and gets `aria-pressed` instead — see ui/rawToggleState.test.ts).

          The count rides the accessible NAME, not just the visible `+N`, for the reason the
          badge comment above spells out: an `aria-label` OVERRIDES the element's text, so
          anything only in a child span is announced nowhere. */}
      {disclosure && disclosure.moreCount > 0 && (
        <motion.button
          type="button" onClick={disclosure.onToggle} whileTap={{ scale: 0.98 }} transition={spring.spatialFast}
          aria-expanded={disclosure.expanded}
          aria-label={disclosure.expanded
            ? `Show fewer, hide ${disclosure.moreCount} surface${disclosure.moreCount === 1 ? '' : 's'}`
            : `Everything, show ${disclosure.moreCount} more surface${disclosure.moreCount === 1 ? '' : 's'}`}
          title={disclosure.expanded
            ? `Hide the ${disclosure.moreCount} surface${disclosure.moreCount === 1 ? '' : 's'} you have not opened yet`
            : `Show all ${disclosure.moreCount} remaining surface${disclosure.moreCount === 1 ? '' : 's'}`}
          className={rowCls('mt-1 text-on-surface-low hover:bg-surface-low/60 hover:text-on-surface-var')}
          style={withWeight({ height: 32 }, 400)}>
          <span className="relative z-10 shrink-0 inline-flex">
            <ChevronDown size={18} strokeWidth={2}
              className={cx('transition-transform', disclosure.expanded && 'rotate-180')} />
          </span>
          {!collapsed && (
            <span className="relative z-10 flex-1 truncate text-[0.9375rem]">
              {disclosure.expanded ? 'Show fewer' : 'Everything'}
            </span>
          )}
          {!collapsed && !disclosure.expanded && (
            <span className="relative z-10 text-[0.75rem] tabular-nums">+{disclosure.moreCount}</span>
          )}
        </motion.button>
      )}

      {/* flex spacer pushes pinned items (e.g. Settings) to the bottom. The live
          system widget now lives in the app-shell top-right corner (ShellCorners),
          collapsed to a gateway-connectivity dot. */}
      <div className="mt-auto" />
      {pinnedItems.map((item) => renderItem(item, false))}
    </nav>
  )

  // Mobile: a fixed overlay drawer that slides in from the left over a scrim, taking
  // NO layout flow (the page stays full-bleed). Off-screen (translateX -100%) when
  // closed; the shell's collapse toggle opens it, a nav tap or scrim tap closes it.
  if (overlay) {
    return (
      <>
        {/* scrim — only interactive/visible while open */}
        <motion.div
          className="fixed inset-0 z-40 bg-black/40"
          initial={false}
          animate={{ opacity: overlayOpen ? 1 : 0 }}
          transition={spring.effects}
          style={{ pointerEvents: overlayOpen ? 'auto' : 'none' }}
          onClick={onScrimClick} aria-hidden />
        <motion.div
          className="fixed left-0 top-0 z-50 h-full shadow-2xl"
          initial={false}
          animate={{ x: overlayOpen ? 0 : '-100%' }}
          transition={spring.spatialDefault}
          role="dialog" aria-label="Navigation" aria-hidden={!overlayOpen}
          // `aria-hidden` alone hides the drawer from the a11y TREE but leaves its 18 nav
          // buttons in the TAB ORDER — so at phone width the first Tab on every route landed
          // on an invisible off-screen "Home". axe flags it as `aria-hidden-focus` on all 37
          // surfaces; it was the single most widespread violation in the app.
          // `inert` removes focusability, pointer events and the a11y tree in one attribute,
          // which is exactly the "closed drawer" semantics.
          // React 19 types `inert` as a real boolean and OMITS the attribute when false, so the
          // plain prop is correct here. The attribute's mere presence applies, so it must be
          // absent (not `inert="false"`) while open or focus would be trapped in the OPEN drawer.
          inert={!overlayOpen}
          style={{ width: OVERLAY_W }}>
          {railBody}
        </motion.div>
      </>
    )
  }

  // Desktop: an in-flow, drag-resizable column that pushes the page.
  return (
    <div className="relative h-full shrink-0" style={{ width: w }}>
      {railBody}
      {/* drag-resize handle on the right border (expanded only) */}
      {!collapsed && (
        // 🔴 IT ALREADY CLAIMED `role="separator"` AND IMPLEMENTED NO KEYBOARD. A separator that
        // resizes is the APG window-splitter, and the role is a promise: arrow keys move it, and it
        // reports where it sits. Measured before this — `tabindex` null, no key handler, no
        // `aria-valuenow`, and no accessible name — so the rail could only be resized by dragging a
        // 1px strip with a mouse (WCAG 2.1.1, and 2.5.7 for the drag-only gesture), while announcing
        // itself to a screen reader as a control that does none of that.
        //
        // 🔑 CONVERGED ONTO WHAT THE TREE ALREADY SHIPS rather than inventing a form:
        // `pages/code/CodeCockpitPage.tsx` carries this exact shape twice (panel splitter + terminal
        // drawer) — role + orientation + `tabIndex={0}` + an arrow-key hint in the name +
        // valuenow/min/max + a `focus-visible` seam. This is that, with `width`'s own MIN_W/MAX_W.
        <div role="separator" aria-orientation="vertical" tabIndex={0}
          aria-label="Resize navigation — arrow keys to resize"
          aria-valuenow={Math.round(w)} aria-valuemin={MIN_W} aria-valuemax={MAX_W}
          onMouseDown={() => { dragging.current = true; document.body.style.cursor = 'col-resize' }}
          onKeyDown={(e) => {
            // Home/End jump to the bounds the drag clamps to, so a keyboard user can reach the
            // extremes without counting keystrokes. STEP is coarse enough to cross the 208px range
            // in a dozen presses and fine enough to settle on a width you meant.
            const STEP = 16
            const next = e.key === 'ArrowLeft' ? w - STEP
              : e.key === 'ArrowRight' ? w + STEP
              : e.key === 'Home' ? MIN_W
              : e.key === 'End' ? MAX_W
              : null
            if (next == null) return
            e.preventDefault()
            setWidth(Math.max(MIN_W, Math.min(MAX_W, next)))
          }}
          className="absolute right-0 top-0 z-10 h-full w-1 cursor-col-resize outline-none transition-colors hover:bg-primary/30 focus-visible:bg-primary/60" />
      )}
    </div>
  )
}
