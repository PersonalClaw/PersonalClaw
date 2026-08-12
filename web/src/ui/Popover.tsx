import { useEffect, useRef, useState, type ReactNode } from 'react'
import { fvs } from '../design/fontWeight'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { overlayEnter, spring } from '../design/motion'

/** Vertical room a menu needs before a side is considered viable. Matches the
 *  composer SlashMenu/MentionMenu flip threshold (`roomAbove < 160`) so every
 *  composer flyout flips at the same point rather than disagreeing by a few px. */
const MENU_ROOM = 160

/** Anchored flyout — opaque NE surface, 20px radius, soft ambient shadow, spring
 *  entrance. Opens above the trigger by default (composer sits low); pass
 *  ``placement="bottom"`` for a top-anchored trigger (e.g. a top-bar control).
 *  Pass ``portal`` when the trigger lives inside an overflow-clipping container
 *  (e.g. a scrolling kanban column): the flyout renders into document.body with
 *  position:fixed, anchored to the trigger's rect, viewport-clamped, and closes
 *  on any scroll (the fixed menu would drift off its anchor otherwise) — mirrors
 *  ui/motion/ContextMenu. Default off so existing consumers are untouched.
 *
 *  ``placement`` is a PREFERENCE, not a promise: on open the trigger's position is
 *  measured, and if the preferred side lacks room the menu flips to the other one.
 *  The same composer renders in two very different places — docked low in a chat
 *  (open upward) and high on the dashboard launcher (upward would clip off the top
 *  of the page) — so a fixed direction is wrong for one of them no matter which is
 *  chosen. Mirrors the flip the composer's own SlashMenu/MentionMenu already do. */
export function Popover({
  trigger, children, align = 'left', width, placement = 'top', openSignal, portal = false,
}: {
  trigger: (open: boolean, toggle: () => void) => ReactNode
  children: (close: () => void) => ReactNode
  align?: 'left' | 'right'
  width?: number
  placement?: 'top' | 'bottom'
  /** Monotonic counter — each increment forces the popover open. Lets a host
   *  open it programmatically (e.g. a "/model" slash command opening the model
   *  pill) without making it fully controlled. */
  openSignal?: number
  /** Render the flyout via a body portal (see doc above). */
  portal?: boolean
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  // Portaled mode: the trigger's viewport rect, measured when the menu opens.
  const [anchor, setAnchor] = useState<DOMRect | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  // The side actually used this time — `placement` flipped when it wouldn't fit.
  const [side, setSide] = useState<'top' | 'bottom'>(placement)
  // Open on each new signal value (ignore the initial mount / 0).
  const lastSignal = useRef(openSignal ?? 0)
  useEffect(() => {
    if (openSignal === undefined || openSignal === lastSignal.current) return
    lastSignal.current = openSignal
    if (openSignal > 0) setOpen(true)
  }, [openSignal])
  // Restore focus to the trigger when the menu closes via Escape or selection, so
  // keyboard focus isn't dropped to <body> (which strands keyboard navigation).
  // Skip restore on outside-CLICK (the user's pointer is already elsewhere).
  const triggerFocusRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!open) return
    triggerFocusRef.current = ref.current?.querySelector<HTMLElement>('button, [tabindex]') ?? null
    // Portaled flyout lives OUTSIDE ref — a click inside it must not count as
    // an outside-click, so check both containers.
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node
      if (ref.current && !ref.current.contains(t) && !(menuRef.current && menuRef.current.contains(t))) setOpen(false)
    }
    // Escape closes THIS popover and stops there — without stopPropagation the same
    // keydown bubbles to other document-level Esc handlers (e.g. a docked SidePanel),
    // so one press would close two layers. Consuming it keeps Escape single-layer.
    const onEsc = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); setOpen(false); triggerFocusRef.current?.focus() } }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onEsc)
    // Portaled mode: the menu is position:fixed while its anchor scrolls with the
    // container — close on ANY scroll (capture) so it never drifts off its trigger.
    const onScroll = () => setOpen(false)
    if (portal) window.addEventListener('scroll', onScroll, true)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onEsc)
      if (portal) window.removeEventListener('scroll', onScroll, true)
    }
  }, [open, portal])

  /** The side that actually has room for this menu.
   *
   *  Measured from the trigger, not assumed: `MENU_ROOM` is the space a typical
   *  menu needs, and a preferred side with less than that flips. Only flips when
   *  the OTHER side is genuinely roomier, so a menu taller than both sides keeps
   *  its caller's preference rather than thrashing.
   */
  const resolveSide = (): 'top' | 'bottom' => {
    const rect = ref.current?.getBoundingClientRect()
    if (!rect) return placement
    const above = rect.top
    const below = window.innerHeight - rect.bottom
    if (placement === 'top' && above < MENU_ROOM && below > above) return 'bottom'
    if (placement === 'bottom' && below < MENU_ROOM && above > below) return 'top'
    return placement
  }

  const toggle = () => setOpen((o) => {
    if (!o) {
      setSide(resolveSide())
      if (portal) setAnchor(ref.current?.getBoundingClientRect() ?? null)
    }
    return !o
  })
  // openSignal-forced opens skip `toggle`, so they need the side measured too.
  useEffect(() => { if (open) setSide(resolveSide()) }, [open])  // eslint-disable-line react-hooks/exhaustive-deps
  // openSignal-forced opens need the anchor too.
  useEffect(() => { if (open && portal && !anchor) setAnchor(ref.current?.getBoundingClientRect() ?? null) }, [open, portal, anchor])
  // Portaled mode: after the flyout renders, measure its REAL height and nudge
  // it back inside the viewport if it overflows the bottom/top fold. An
  // estimate-based clamp (à la ContextMenu) over-shifts small menus, and the
  // menu height here varies with folders/tags count.
  useEffect(() => {
    if (!open || !portal) return
    const el = menuRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    const pad = 8
    if (r.bottom > window.innerHeight - pad) { el.style.top = `${Math.max(pad, window.innerHeight - pad - r.height)}px`; el.style.bottom = 'auto' }
    if (r.top < pad) { el.style.top = `${pad}px`; el.style.bottom = 'auto' }
  }, [open, portal, anchor])

  const flyout = (
    <AnimatePresence>
      {open && (portal ? anchor != null : true) && (
        <motion.div
          ref={menuRef}
          variants={overlayEnter} initial="initial" animate="animate" exit="exit"
          className={portal
            ? 'glass fixed z-50 rounded-lgi p-s'
            : `glass absolute z-30 rounded-lgi p-s ${side === 'bottom' ? 'top-full mt-s' : 'bottom-full mb-s'} ${align === 'right' ? 'right-0' : 'left-0'}`}
          style={{
            transformOrigin: `${side === 'bottom' ? 'top' : 'bottom'} ${align === 'right' ? 'right' : 'left'}`,
            width, minWidth: 200,
            ...(portal && anchor ? portalPos(anchor, side, align, width ?? 200) : null),
          }}
        >
          {children(() => { setOpen(false); triggerFocusRef.current?.focus() })}
        </motion.div>
      )}
    </AnimatePresence>
  )

  return (
    <div ref={ref} className="relative">
      {trigger(open, toggle)}
      {portal ? createPortal(flyout, document.body) : flyout}
    </div>
  )
}

/** Fixed-position coordinates for a portaled flyout: same placement/align
 *  semantics as the inline mode (relative to the trigger rect), horizontally
 *  clamped to the viewport with an 8px gutter. Vertical overflow is corrected
 *  after mount by the measure-nudge effect (real height beats an estimate). */
function portalPos(anchor: DOMRect, placement: 'top' | 'bottom', align: 'left' | 'right', w: number) {
  const gap = 6, pad = 8
  const left = align === 'right' ? anchor.right - w : anchor.left
  const pos: React.CSSProperties = { left: Math.min(Math.max(pad, left), Math.max(pad, window.innerWidth - w - pad)) }
  if (placement === 'bottom') pos.top = anchor.bottom + gap
  else pos.bottom = window.innerHeight - anchor.top + gap
  return pos
}

/** A row inside a popover menu/list. Redesign-v2: the icon nudges in and the row
 *  presses on tap (spring) — a light physical touch on a high-frequency surface,
 *  kept restrained (menus are dense). The `group` lets the icon shift on hover. */
export function MenuRow({
  icon, label, hint, selected, onClick, role,
}: {
  icon?: ReactNode; label: string; hint?: string; selected?: boolean; onClick?: () => void
  /** The ITEM role required by the popup's container role, when it declares one.
   *
   *  A bare `<button>` is right inside a role-less popover — 28 of the 30 call sites — but a
   *  container that says `role="menu"` or `role="listbox"` promises children of a matching type,
   *  and this row is what those two containers are made of. Measured before this prop existed:
   *  the collapsed `Segmented` announced "View, list box" with **0 options** and the row context
   *  menu a `menu` with **0 menuitems**, while every popup NOT built from this row
   *  (`ProjectPicker`, `SlashMenu`, `HeaderActions`, `MentionMenu`) marked its items correctly.
   *  The shared row was the reason those two were the outliers. */
  role?: 'menuitem' | 'menuitemradio' | 'option'
}) {
  return (
    <motion.button
      onClick={onClick}
      role={role}
      // The selected state goes in the vocabulary the role actually supports: `aria-selected`
      // for an option, `aria-checked` for a radio item. A plain `menuitem` has neither, which is
      // correct for an action row (Peek / Open / Pin) — nothing there is "on".
      aria-selected={role === 'option' ? !!selected : undefined}
      aria-checked={role === 'menuitemradio' ? !!selected : undefined}
      whileTap={{ scale: 0.97 }}
      transition={spring.spatialFast}
      className="group flex items-center gap-s w-full rounded-md px-m py-2 text-left text-on-surface hover:bg-surface-high transition-colors"
    >
      {icon && <span className="shrink-0 text-on-surface-var transition-transform duration-150 group-hover:translate-x-0.5">{icon}</span>}
      <span className="flex-1 min-w-0">
        <span className="block text-[0.8125rem] truncate" style={fvs(selected ? 500 : 400)}>{label}</span>
        {hint && <span className="block text-[0.75rem] text-on-surface-low truncate">{hint}</span>}
      </span>
      {selected && <span className="size-1.5 shrink-0 rounded-pill bg-primary" />}
    </motion.button>
  )
}
