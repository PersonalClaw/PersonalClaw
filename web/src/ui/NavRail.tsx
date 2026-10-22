import { Fragment, useEffect, useRef, useState, type ReactNode } from 'react'
import { useRememberedScroll } from './railScroll'
import { withWeight } from '../design/fontWeight'
import type { LucideIcon } from 'lucide-react'
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
  /** Pinned to the rail's foot (`data-rail-pinned`), under the scroll area and never scrolled,
   *  instead of inline in scroll order — e.g. Settings. */
  pinBottom?: boolean
}

const W_KEY = 'nav-width-v2'
const MIN_W = 172
const MAX_W = 380
const COLLAPSED_W = 64
// Mobile overlay-drawer width — a comfortable touch target, capped under a phone's
// portrait width so the scrim always shows (tap-to-close affordance stays reachable).
/** How far a focus ring reaches past its control: the global `:focus-visible` outline is 2 px wide,
 *  2 px out. A scroll area keeps this much clear at an edge so a focused row's ring is never cut. */
export const RING_REACH = 4

const OVERLAY_W = 264
// How far past its own width the CLOSED overlay drawer parks: `shadow-2xl`'s 38px reach
// (spread −12 + blur 50), rounded up, so none of its shadow is left on screen.
const CLOSED_SHADOW_CLEARANCE = 48
// Default to a snug, content-fitting width (the labels are short); the user can
// drag the right edge wider if they want more room.
const DEFAULT_W = 196

/** Side navigation — drag-resizable, persisted. Collapse is CONTROLLED by the
 *  shell (the collapse/expand toggle lives in the main area's top-left, not on
 *  the rail). When collapsed → icon-only 64px rail. Its foot holds the pinned rows (Settings); the
 *  live system state is the status line's, at the window's bottom left. No operator/identity
 *  footer (single-user; Settings is in nav). */
export function NavRail({
  items, activeId, onSelect, collapsed, overlay = false, overlayOpen = false, onScrimClick, material = false, threads,
}: {
  items: NavItem[]
  /** The projects with their threads (`ThreadRail`), shown after the leading destinations and
   *  before the sectioned ones, in the expanded rail and the phone's drawer. The collapsed rail is
   *  icons only and shows none: a thread is reached there from Home, the palette or a key. */
  threads?: ReactNode
  /** The desktop window's translucent material shows behind the rail (`app/windowMaterial.ts`):
   *  the in-flow column is then clear so the material shows through it. The phone drawer floats
   *  over the page, so it keeps its own surface either way. */
  material?: boolean
  activeId: string
  onSelect: (id: string) => void
  collapsed: boolean
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
  const scrollArea = useRef<HTMLDivElement>(null)

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

  // ONE geometry recipe for every row in the rail. Hand-tuning a second copy is how a 32px row
  // becomes a 34px row that only looks wrong next to its neighbour.
  const rowCls = (tone: string) => cx(
    'group relative flex items-center gap-s w-full rounded-pill text-left transition-colors duration-(--duration-short)',
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
          <div data-type="body-s" className="px-s pt-l pb-1 uppercase tracking-wide text-on-surface"
            style={{ opacity: 0.65, ...fvs(400) }}>
            {item.section}
          </div>
        )}
        <button
          type="button" onClick={() => onSelect(item.id)}
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
          // The role sits on the BUTTON (size/line-height; the label span inherits the
          // font-size) because the active/inactive weight is the inline withWeight
          // below — a role on the label span would override that inherited weight.
          data-type="body-m"
          className={rowCls(active ? 'text-on-surface' : 'text-on-surface-var hover:bg-surface-low/60 hover:text-on-surface')}
          style={withWeight({ height: 32 }, active ? 470 : 400)}>
          {/* The active pill, behind the icon and label. It changes item at once, by colour: a rail
              click is the most frequent action in the app, and a pill sliding over from the last
              item says nothing about where you are now. */}
          {active && <span className="absolute inset-0 rounded-pill bg-surface-low" />}
          <span className="relative z-10 shrink-0 inline-flex">
            <Icon size={18} strokeWidth={2} />
            {/* Collapsed rail has no room for the pill badge — show a dot so
                a count (e.g. goals running) is still visible. */}
            {collapsed && item.badge && (
              <span className="absolute -right-1 -top-1 size-2 rounded-pill ring-2 ring-surface" style={{ background: 'var(--color-primary)' }} />
            )}
          </span>
          {!collapsed && <span className="relative z-10 flex-1 truncate">{item.label}</span>}
          {!collapsed && item.badge && (
            <span data-type="caption" className="relative z-10 inline-flex h-5 items-center rounded-pill px-s text-on-surface"
              style={{ background: 'color-mix(in srgb, var(--color-on-surface) 12%, transparent)' }}>
              {item.badge}
            </span>
          )}
        </button>
      </div>
    )
  }

  // The rail body — shared between the in-flow desktop column and the mobile overlay
  // drawer. In overlay mode it always shows full labels (a drawer has room; icon-only
  // makes no sense once you've deliberately opened it).
  const showFull = overlay ? true : !collapsed
  // The full rail comes back to where it was scrolled, after a reload or a collapse (`railScroll.ts`).
  useRememberedScroll(scrollArea, showFull)
  // The threads sit after the leading (unsectioned) destinations, before the first sectioned one.
  const firstSectioned = topItems.findIndex((i) => i.section)
  const threadsAt = firstSectioned < 0 ? topItems.length : firstSectioned
  // One keyed list, so the threads keep their place in React's tree (and their state) when the
  // destinations around them change, as an app pinned to the sidebar changes them.
  const topRows = topItems.map((item) => renderItem(item, true))
  if (showFull && threads) topRows.splice(threadsAt, 0, <Fragment key="threads">{threads}</Fragment>)
  const railBody = (
    // `data-tour` is the product tour's anchor for its first stop. A
    // literal, not a prop: the rail is the shell's one navigation and the tour points at it
    // by name, so a prop would only let a second rail claim the same anchor.
    //
    // Two parts, top to bottom: what scrolls (the wordmark, the destinations and the threads) and
    // the rows pinned under it (Settings, and the phone's Companion), which
    // never scroll. One scroller with Settings at its foot overflowed a 720 px window by 14 px, which
    // pushed Settings into the rail's bottom padding and cut its focus ring off at the edge. The foot
    // never meets the scroll area's edges. A row in the scroll area can sit at its fold while it
    // scrolls, so the scroll area keeps the ring's reach clear at its bottom (`RING_REACH`, and at its
    // top the thread rail's pinned bar and the ring): the browser scrolls the row the focus moves to
    // clear of either edge, ring and all.
    <nav data-tour="rail" className="flex h-full flex-col"
      style={{ width: overlay ? OVERLAY_W : w, background: material && !overlay ? 'transparent' : 'var(--color-rail)' }}>
      <div ref={scrollArea} data-rail-scroll className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto overflow-x-hidden px-m pt-l pb-s"
        style={{ scrollPaddingBottom: RING_REACH }}>
        {/* header — logo (the collapse toggle lives in the main area, not here) */}
        <div className={cx('flex items-center pb-m', showFull ? 'px-s' : 'justify-center')}>
          {/* The wordmark tracks the active PERSONALITY's label (default: PersonalClaw). */}
          {showFull ? <Wordmark label={wordmarkLabel} /> : <Spark size={22} />}
        </div>

        {topRows}
      </div>
      {/* The pinned rows, under the scroller at the rail's foot. The live system widget lives in the
          status line at the window's bottom left (app/StatusLine), collapsed to the gateway
          connection. */}
      {pinnedItems.length > 0 && (
        <div data-rail-pinned className="flex shrink-0 flex-col gap-1 px-m pt-xs pb-l">
          {pinnedItems.map((item) => renderItem(item, false))}
        </div>
      )}
    </nav>
  )

  // Mobile: a fixed overlay drawer that slides in from the left over a scrim, taking
  // NO layout flow (the page stays full-bleed). Off-screen (translateX -100%) when
  // closed; the shell's collapse toggle opens it, a nav tap or scrim tap closes it.
  if (overlay) {
    return (
      <>
        {/* scrim — only interactive/visible while open. It rides one rung UNDER its own drawer, which
            is what `calc(var(--z-modal) - 1)` says and a bare `z-40` did not: 40 was a number chosen
            relative to the drawer's old 50, so moving the drawer would silently have left the scrim
            two rungs adrift. Expressed against the drawer's rung, the pair cannot come apart. */}
        <motion.div
          className="fixed inset-0 z-[calc(var(--z-modal)-1)] bg-black/40"
          initial={false}
          animate={{ opacity: overlayOpen ? 1 : 0 }}
          transition={spring.effects}
          style={{ pointerEvents: overlayOpen ? 'auto' : 'none' }}
          onClick={onScrimClick} aria-hidden />
        {/* 🔴 A VALUE FIX, NOT A SPELLING ONE. This drawer DECLARES `role="dialog"` and draws its own
            scrim, which is exactly what tokens.css assigns to `--z-modal` ("dialogs, sheets, blocking
            takeovers"). At a bare `z-50` it rode the CONTENT ceiling instead — tying with
            `ui/SidePanel` and `chat/ChatFilePanel`, which are both `fixed inset-0 z-50`. Three
            overlays on one rung do not stack by design; they stack by DOM/portal order, so which one
            covered the other was an accident of mount sequence. */}
        {/* CLOSED IS PARKED PAST ITS OWN SHADOW, not at `-100%`. `shadow-2xl` reaches 38px past the
            drawer's edge (spread −12 + blur 50), so a drawer parked exactly off-screen left that
            shadow ON screen: a grey strip down the left edge of every route at phone width, visible
            on the light canvas ((221,224,228) at x=0 on (240,244,248)). Measured once the chat
            halo stopped running into the same edge and no longer covered it. */}
        <motion.div
          className="fixed left-0 top-0 z-[var(--z-modal)] h-full shadow-2xl"
          initial={false}
          animate={{ x: overlayOpen ? 0 : -(OVERLAY_W + CLOSED_SHADOW_CLEARANCE) }}
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
          // 4px WIDE, ON EVERY SURFACE: measured on `main` at 4x900 (desktop) and 4x1112
        // (tablet), i.e. 20px short of the 24px floor in the one axis that is short
        // (WCAG 2.2 SC 2.5.8). `hit-24-x` widens only the PRESSABLE band, leaving the 1px
        // seam and the resize arithmetic below untouched.
        className="absolute right-0 top-0 z-10 h-full w-1 cursor-col-resize outline-none transition-colors hover:bg-primary/30 focus-visible:bg-primary/60 hit-24-x" />
      )}
    </div>
  )
}
