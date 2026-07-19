import { useEffect, useRef, useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import { motion } from 'framer-motion'
import { cx } from './cx'
import { Wordmark, Spark } from './Spark'
import { spring } from '../design/motion'

export interface NavItem {
  id: string
  label: string
  icon: LucideIcon
  badge?: string
  section?: string
  /** Pinned to the bottom of the rail (rendered after the flex spacer, above the
   *  system widget) instead of inline in scroll order — e.g. Settings. */
  pinBottom?: boolean
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
  items, activeId, onSelect, collapsed, overlay = false, overlayOpen = false, onScrimClick,
}: {
  items: NavItem[]
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

  const renderItem = (item: NavItem, withSection: boolean) => {
    const showSection = withSection && !collapsed && item.section && item.section !== lastSection
    if (withSection) lastSection = item.section
    const active = item.id === activeId
    const Icon = item.icon
    return (
      <div key={item.id}>
        {showSection && (
          <div className="px-s pt-l pb-1 text-[0.8125rem] uppercase tracking-wide text-on-surface"
            style={{ opacity: 0.55, fontVariationSettings: '"wght" 400' }}>
            {item.section}
          </div>
        )}
        <motion.button
          type="button" onClick={() => onSelect(item.id)} whileTap={{ scale: 0.98 }} transition={spring.spatialFast}
          title={collapsed ? item.label : undefined} aria-label={item.label}
          className={cx(
            'group relative flex items-center gap-s w-full rounded-pill text-left transition-colors duration-100',
            collapsed ? 'justify-center px-0' : 'px-s',
            active ? 'text-on-surface' : 'text-on-surface-var hover:bg-surface-low/60 hover:text-on-surface',
          )}
          style={{ height: 32, fontVariationSettings: active ? '"wght" 470' : '"wght" 400' }}>
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
        {showFull ? <Wordmark /> : <Spark size={22} />}
      </div>

      {topItems.map((item) => renderItem(item, true))}

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
        <div role="separator" aria-orientation="vertical"
          onMouseDown={() => { dragging.current = true; document.body.style.cursor = 'col-resize' }}
          className="absolute right-0 top-0 z-10 h-full w-1 cursor-col-resize transition-colors hover:bg-primary/30" />
      )}
    </div>
  )
}
