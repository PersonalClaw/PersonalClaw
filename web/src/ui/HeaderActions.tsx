import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { menuCursorKeydown, useMenuCursor } from '../lib/useMenuCursor'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { Check, ChevronDown, MoreHorizontal, type LucideIcon } from 'lucide-react'
import { cx } from './cx'
import { spring } from '../design/motion'
import { fvs, withWeight } from '../design/fontWeight'
import { Popover, MenuRow } from './Popover'
import { Segmented, type SegOption } from './Segmented'

/** Responsive header controls — the ONE 4-tier cluster (`responsive-header-controls.md`).
 *  The row of controls degrades TOGETHER (whole-cluster) as horizontal space
 *  shrinks, on one ladder:
 *
 *    FULL      icon + label
 *    TEXT      label only          (drop icons)
 *    ICON      icon only           (label → tooltip + aria; frees width for the title)
 *    OVERFLOW  a `…` menu          (too tight even for icons: highest-priority controls
 *                                   stay icon-only, the rest fall into the menu)
 *
 *  The container measures each tier's natural width via offscreen `aria-hidden`
 *  probes + a `ResizeObserver`, then picks the richest tier that fits. In OVERFLOW
 *  it greedily keeps the highest-`priority` controls visible (icon-only) and pushes
 *  the remainder — lowest priority first — into an internal auto-`…` menu. No page
 *  hand-builds a header overflow menu anymore; it falls out of the same child list.
 *
 *  Because the cluster narrows as it sheds tiers, the TopBar's flexing `left` slot
 *  (the title) reclaims the freed width — satisfying "frees up space for the title".
 *
 *  ORDERING TENET (children render left→right in DOM order — order accordingly):
 *    • A control that OPENS A SIDE PANEL (a `<SidePanel>`/detail/inspector rail —
 *      e.g. "Details", "Activity", "More details") is the RIGHTMOST child, always.
 *    • A DESTRUCTIVE control (Delete) sits LEFTMOST of the right-edge group.
 *    • Everything else (primary actions, toggles, modals, nav) sits in between.
 *  So the canonical shape is:  [Delete] … [other controls] … [open-side-panel].
 *  `priority` (primary/default/low) governs OVERFLOW shedding independently of this
 *  visual order, so keep Delete/panel-opener `priority="low"` even though their
 *  positions are fixed by the tenet. */
export type Tier = 'full' | 'text' | 'icon' | 'overflow'
export type Priority = 'primary' | 'default' | 'low'

/** A child registers itself so the container knows its capabilities + can render it
 *  as a `…`-menu row when it overflows. `render(tier)` draws the visible control at a
 *  given tier; `menu` is the OverflowAction shape for the collapsed menu row. */
interface ChildReg {
  id: number
  priority: Priority
  /** Can this control shed its label to become icon-only (needs an icon)? A
   *  label-only Button can't reach the ICON tier — the container must know. */
  canIcon: boolean
  /** This control can never be pushed into the `…` menu (it has no meaningful single-
   *  row form — e.g. a Segmented mode-slider). It always stays visible; in OVERFLOW it
   *  renders icon-only. It still participates in the tier decision. */
  neverOverflow?: boolean
  /** Overflow menu row descriptor (used when this control is pushed into `…`). */
  menu: { label: string; icon?: LucideIcon; hint?: string; danger?: boolean; onSelect?: () => void }
}

interface ClusterCtx {
  tier: Tier
  /** In OVERFLOW: the set of child ids that stay VISIBLE (icon-only); the rest are
   *  rendered by the container as menu rows. Empty/!overflow → all visible. */
  visibleIds: Set<number> | null
  register: (reg: ChildReg) => void
  unregister: (id: number) => void
}

const Cluster = createContext<ClusterCtx | null>(null)
const PRIORITY_RANK: Record<Priority, number> = { primary: 0, default: 1, low: 2 }

let _uid = 0
const nextId = () => ++_uid

/** Read the cluster's current tier from inside a child (drives its own render). */
export function useHeaderTier(): Tier {
  return useContext(Cluster)?.tier ?? 'full'
}

/** Register a child control with the cluster. Returns whether this child should
 *  render itself VISIBLY (false → the container is showing it as a `…`-menu row in
 *  OVERFLOW, so the child renders nothing). Stable across renders for a given child. */
export function useHeaderChild(reg: Omit<ChildReg, 'id'>): { visible: boolean; tier: Tier } {
  const ctx = useContext(Cluster)
  const idRef = useRef<number>(0)
  if (!idRef.current) idRef.current = nextId()
  const id = idRef.current
  // Keep the latest descriptor registered (priority/menu can change across renders,
  // e.g. a disabled/relabelled control). Register on mount, refresh on change.
  const regRef = useRef(reg)
  regRef.current = reg
  useLayoutEffect(() => {
    if (!ctx) return
    ctx.register({ id, ...regRef.current })
    return () => ctx.unregister(id)
    // Re-register when the descriptor's identity-bearing fields change so the
    // container's overflow math + menu labels stay correct.
  }, [ctx, id, reg.priority, reg.canIcon, reg.menu.label, reg.menu.danger, reg.menu.hint])
  if (!ctx) return { visible: true, tier: 'full' }
  const visible = ctx.tier !== 'overflow' || !ctx.visibleIds || ctx.visibleIds.has(id)
  return { visible, tier: ctx.tier }
}

const TIER_ORDER: Tier[] = ['full', 'text', 'icon', 'overflow']

/** The title (left slot) keeps a floor before the cluster eats into it — but the floor must
 *  scale DOWN on narrow headers, else on a phone (~360px, inner ~154px) reserving a fixed 96px
 *  leaves the cluster almost nothing and forces it to overflow far too early. Reserve at most
 *  ~1/3 of the inner width (the title truncates), clamped to a legible [48, 96] band. This is
 *  "shedding frees width for the title" in reverse: when space is scarce the TITLE yields so
 *  the controls stay usable. */
export const titleFloor = (inner: number): number =>
  Math.round(Math.min(96, Math.max(48, inner * 0.34)))

/** What the title is ACTUALLY owed: nothing when the slot holds no visible content, otherwise
 *  `min(its natural width, titleFloor)`.
 *
 *  Exported and pure so the arithmetic can be tested. It cannot be exercised through a render:
 *  jsdom reports every box as 0, so the whole width computation collapses to zeros there and a
 *  component test would pass against any implementation — which is exactly what happened when
 *  this was first written as a render assertion.
 *
 *  `hasContent` is the load-bearing input. Several headers render `left={undefined}` (`#/chat`
 *  on a new chat is one), and an empty flex slot still reports a non-zero `scrollWidth` from
 *  its own padding/gap — so keying on width alone would keep reserving for a title that does
 *  not exist. Measured cost of that phantom reserve at 390px: the rail capped at 58px for 88px
 *  of mode pills, and the permission-mode pill collided with the `…` and became unclickable. */
export function titleReserveFor(
  { hasContent, naturalWidth, inner }: { hasContent: boolean; naturalWidth: number; inner: number },
): number {
  if (!hasContent) return 0
  return Math.min(naturalWidth, titleFloor(inner))
}

/** The rail's hard ceiling: the inner box minus the `…` slot minus whatever the title is owed.
 *  Keeping this in one place is what stops the two halves of the width split from disagreeing —
 *  the availability math and the ceiling were computing the title's share differently, and the
 *  ceiling's blanket floor is what starved the controls on a title-less header. */
export function railCeiling(
  { inner, dots, title }: { inner: number; dots: number; title: number },
): number {
  return Math.max(0, inner - dots - title)
}

export function HeaderActions({ children, className }: { children: ReactNode; className?: string }) {
  const outerRef = useRef<HTMLDivElement>(null)
  // Offscreen probe rows, one per non-overflow tier — their scrollWidth is the
  // cluster's natural width at that density.
  const probeFull = useRef<HTMLDivElement>(null)
  const probeText = useRef<HTMLDivElement>(null)
  const probeIcon = useRef<HTMLDivElement>(null)

  const [tier, setTier] = useState<Tier>('full')
  const [visibleIds, setVisibleIds] = useState<Set<number> | null>(null)

  // The probes are measurement-only chrome: aria-hidden alone still leaves
  // their real <button> children in the tab order (axe: aria-hidden-focus —
  // a keyboard user could focus an invisible control). `inert` removes the
  // whole subtree from focus/AT without affecting layout, so scrollWidth
  // stays truthful. Imperative because React 18 has no boolean `inert` prop.
  useEffect(() => {
    for (const p of [probeFull, probeText, probeIcon]) p.current?.setAttribute('inert', '')
  }, [])
  // Width cap for the rare over-full row: the TopBar right slot is `shrink-0`, so without
  // a cap the rail grows PAST the header's content box (under the floating shell corner)
  // when even the always-visible controls + `…` don't fit. null = fits (no cap, right-
  // aligned). When set (= measured `avail`), the rail fills it and scrolls; the sticky `…`
  // stays pinned in-box.
  const [maxW, setMaxW] = useState<number | null>(null)

  // Child registry: id → descriptor, in DOM/registration order. Kept in a ref (the
  // container reads it during measurement) + a version counter to re-measure when
  // the child set changes.
  const regs = useRef<Map<number, ChildReg>>(new Map())
  const [regVersion, setRegVersion] = useState(0)
  const register = useCallback((r: ChildReg) => { regs.current.set(r.id, r); setRegVersion((v) => v + 1) }, [])
  const unregister = useCallback((id: number) => { regs.current.delete(id); setRegVersion((v) => v + 1) }, [])

  const tierRef = useRef<Tier>(tier)
  tierRef.current = tier

  useLayoutEffect(() => {
    const outer = outerRef.current
    if (!outer) return
    // Hysteresis: require a small margin BEYOND a threshold before stepping back UP
    // to a richer tier, so a control hovering at the exact break width can't flip-
    // flop every RO tick. Only applied when moving up (down-steps are immediate to
    // avoid clipping).
    const HYST = 8
    const GAP_TO_TITLE = 16

    // Available width = the header's inner CONTENT box (its width minus the shell-corner
    // padding it reserves on both ends) MINUS the title's reserve — NOT the cluster's own
    // (content-collapsed) width. Measuring our own box would latch overflow: shedding
    // shrinks the box → re-measures as "no room" → never recovers.
    // The header's inner CONTENT box — its width minus the padding it reserves to clear
    // the floating shell corners. This is the hard ceiling for anything in the row: grow
    // past it and you are under the corner chrome, which swallows clicks.
    const innerBox = (): number => {
      const header = outer.closest('header')
      if (!header) return outer.clientWidth
      const cs = getComputedStyle(header)
      return header.clientWidth - (parseFloat(cs.paddingLeft) || 0) - (parseFloat(cs.paddingRight) || 0)
    }
    // Read the live title measurements and hand them to the pure `titleReserveFor` above,
    // which both this and the rail's ceiling use — so the two cannot disagree about what the
    // title is owed. (They did: the ceiling subtracted a blanket floor even for an EMPTY slot.)
    const titleReserve = (inner: number): number => {
      const header = outer.closest('header')
      const left = header?.querySelector<HTMLElement>('[data-header-left]')
      if (!left) return 0
      // An empty flex slot still reports a non-zero `scrollWidth` from its own padding/gap, so
      // measure the CONTENT: no visible child means there is no title to protect.
      const hasContent = Array.from(left.children).some((c) => {
        const r = (c as HTMLElement).getBoundingClientRect()
        return r.width > 2 && r.height > 2
      })
      return titleReserveFor({
        hasContent,
        naturalWidth: Math.min(left.scrollWidth, left.clientWidth || left.scrollWidth),
        inner,
      })
    }
    const availableWidth = (): number => {
      const header = outer.closest('header')
      if (!header) return outer.clientWidth
      const inner = innerBox()
      // The title needs its reserve; give the cluster the rest. On narrow headers the floor
      // shrinks so the cluster keeps usable width.
      return Math.max(0, inner - titleReserve(inner) - GAP_TO_TITLE)
    }

    const measure = () => {
      const avail = availableWidth()
      const wFull = probeFull.current?.scrollWidth ?? 0
      const wText = probeText.current?.scrollWidth ?? 0
      const wIcon = probeIcon.current?.scrollWidth ?? 0
      const cur = tierRef.current

      // Pick the richest tier that fits. Down-steps fire as soon as the current tier
      // no longer fits; up-steps need HYST slack so we don't oscillate at the edge.
      const fits = (natural: number, target: Tier) => {
        const rank = TIER_ORDER.indexOf(target)
        const curRank = TIER_ORDER.indexOf(cur)
        const margin = rank < curRank ? HYST : 0 // stepping UP (richer) needs slack
        return natural + margin <= avail
      }

      let next: Tier
      if (fits(wFull, 'full')) next = 'full'
      else if (fits(wText, 'text')) next = 'text'
      else if (fits(wIcon, 'icon')) next = 'icon'
      else next = 'overflow'

      if (next !== 'overflow') {
        if (next !== cur) setTier(next)
        setVisibleIds((prev) => (prev === null ? prev : null))
        setMaxW((prev) => (prev === null ? prev : null)) // fits → no cap, right-align to content
        return
      }

      // OVERFLOW: greedy fill by priority. Reserve a slot for the `…` trigger, then
      // walk children (primary → default → low; ties keep DOM order) adding each
      // one's icon width until the next won't fit. The rest become menu rows.
      const DOTS = 44 // `…` trigger (40px) + gap
      const GAP = 8
      const ordered = [...regs.current.values()]
        .map((r, domOrder) => ({ r, domOrder }))
        .sort((a, b) => PRIORITY_RANK[a.r.priority] - PRIORITY_RANK[b.r.priority] || a.domOrder - b.domOrder)
      // Per-child icon width: measure from the icon probe's children in DOM order.
      const iconEls = probeIcon.current ? Array.from(probeIcon.current.children) as HTMLElement[] : []
      const domIds = [...regs.current.keys()]
      const iconW = (id: number) => {
        const i = domIds.indexOf(id)
        return (iconEls[i]?.offsetWidth ?? 40) + GAP
      }
      const keep = new Set<number>()
      let used = DOTS
      // `neverOverflow` children (e.g. a Segmented mode-slider) can't live in the `…`
      // menu — reserve their icon-only width up front so they always stay visible.
      // Total that reservation separately: it is the rail's FLOOR (see the cap below).
      let floorW = 0
      for (const r of regs.current.values()) {
        if (r.neverOverflow) { keep.add(r.id); used += iconW(r.id); floorW += iconW(r.id) }
      }
      for (const { r } of ordered) {
        if (keep.has(r.id)) continue
        // A control that can't go icon-only can't be a bare visible item in overflow
        // — it always lives in the menu.
        if (!r.canIcon) continue
        const w = iconW(r.id)
        if (used + w <= avail) { used += w; keep.add(r.id) }
      }
      if (cur !== 'overflow') setTier('overflow')
      setVisibleIds((prev) => (sameSet(prev, keep) ? prev : keep))
      // Cap the SCROLL RAIL only when the kept controls + `…` truly exceed the space.
      // The `…` now lives OUTSIDE the rail (a flex sibling), so reserve its width here:
      // cap the rail at `avail - DOTS` so the rail + the `…` beside it together fit in
      // `avail` and the `…` stays in-box (never pushed under the shell corner). The rail
      // then fills its cap and scrolls its controls horizontally beneath the fixed `…`.
      // FLOOR the cap at the `neverOverflow` children's own total width, because capping
      // below it cannot help: those controls can't move into the `…` menu, so a too-small
      // cap only CLIPS them — and scrolling is no escape here, since scrollbars are hidden
      // app-wide by owner tenet, making a clipped control read as absent. Measured at
      // 390px: #/chat carries two mode pills (40px each = 88px) in a rail capped to 42px,
      // so the permission-mode pill was unreachable.
      //
      // But the floor is itself bounded by the header's inner box: letting the rail grow
      // past it pushes the `…` trigger under the floating shell corner, which INTERCEPTS
      // the click (verified — Playwright times out on it, while a programmatic .click()
      // still works, so a geometry-only check misses this).
      //
      // The ceiling must ALSO leave the title its floor. `availableWidth()` already promises
      // the title `titleFloor(inner)` px, but the ceiling was computed from the raw inner box
      // and so could overrule that promise: on #/prompts a 3-icon strip (108px) plus the `…`
      // (40px) exactly filled the 155px box, leaving the title slot **0px** — the page name
      // vanished rather than truncating. Subtracting the same floor keeps the two halves of
      // this file telling the same story, and the title then truncates inside a slot that is
      // never zero. The row is `justify-end` in a `min-w-0 flex-1` slot, so whatever the rail
      // does not take goes back to the title.
      //
      // Reserve what the title actually NEEDS, not a blanket floor. Using `titleFloor()`
      // unconditionally held 53px back for headers whose left slot is EMPTY (`#/chat` on a
      // new chat renders `left={undefined}`), and 53px was exactly what its two 40px mode
      // pills were short of: the rail capped at 58px for 88px of content, so the
      // permission-mode pill painted out to x=181 and collided with the `…` at x=159 — the
      // `…` is a later sibling, so it won and the pill became unclickable. `titleReserve()`
      // returns 0 when there is no title, which lets the pills have the room.
      const inner = innerBox()
      const ceiling = railCeiling({ inner, dots: DOTS, title: titleReserve(inner) })
      const cap = used > avail
        ? Math.min(Math.max(floorW, Math.round(avail - DOTS)), ceiling)
        : null
      setMaxW((prev) => (prev === cap ? prev : cap))
    }

    measure()
    // Observe the HEADER (available width changes when the header/title/sidebar/panel
    // resize), not just our own box — our box is content-sized now and wouldn't fire.
    const ro = new ResizeObserver(measure)
    ro.observe(outer)
    const header = outer.closest('header')
    if (header) ro.observe(header)
    const left = header?.querySelector<HTMLElement>('[data-header-left]')
    if (left) ro.observe(left)
    return () => ro.disconnect()
  }, [regVersion])

  const ctx = useMemo<ClusterCtx>(() => ({ tier, visibleIds, register, unregister }), [tier, visibleIds, register, unregister])

  // The overflow menu's actions = children NOT kept visible (in overflow), lowest
  // priority first (they collapsed first, so read top-to-bottom low→high feels off —
  // keep DOM order for predictability, which mirrors the row's left→right order).
  const overflowActions = useMemo(() => {
    if (tier !== 'overflow') return []
    const menuIds = [...regs.current.keys()].filter((id) => !visibleIds || !visibleIds.has(id))
    return menuIds
      .map((id) => regs.current.get(id))
      .filter((r): r is ChildReg => !!r)
      .map((r) => ({ id: r.id, ...r.menu }))
  }, [tier, visibleIds, regVersion])

  return (
    <Cluster.Provider value={ctx}>
      <div ref={outerRef} className={cx('relative min-w-0 flex-1 flex items-center justify-end', className)}>
        {/* three offscreen probes — one per tier — measure the cluster's natural
            width at each density. Rendered via the same children so widths are real. */}
        <div ref={probeFull} aria-hidden className="pointer-events-none absolute right-0 top-0 opacity-0 -z-10 flex items-center gap-s whitespace-nowrap">
          <Cluster.Provider value={{ tier: 'full', visibleIds: null, register: noop, unregister: noop }}>{children}</Cluster.Provider>
        </div>
        <div ref={probeText} aria-hidden className="pointer-events-none absolute right-0 top-0 opacity-0 -z-10 flex items-center gap-s whitespace-nowrap">
          <Cluster.Provider value={{ tier: 'text', visibleIds: null, register: noop, unregister: noop }}>{children}</Cluster.Provider>
        </div>
        <div ref={probeIcon} aria-hidden className="pointer-events-none absolute right-0 top-0 opacity-0 -z-10 flex items-center gap-s whitespace-nowrap">
          <Cluster.Provider value={{ tier: 'icon', visibleIds: null, register: noop, unregister: noop }}>{children}</Cluster.Provider>
        </div>
        {/* Live cluster = a scrolling rail for the CONTROLS + the `…` trigger as a
            SIBLING outside that rail. Keeping `…` out of the scroll context is what
            makes it behave: (1) its dropdown (an absolute Popover) is no longer
            clipped by the rail's `overflow` — inside the rail it opened into a 40px-
            tall scrollport and was invisible ("`…` shows but nothing opens"); (2) it
            can't scroll away or slide under the shell corner; (3) the gap between the
            last control and `…` is the flex `gap-s`, identical to every other control
            gap (no bespoke sticky/padding seam). The rail is capped (maxW) only when
            the kept controls truly exceed the space, so it scrolls horizontally
            instead of spilling; the `…` always sits flush at the right, in-box. */}
        <div className="min-w-0 flex items-center gap-s">
          <div className="min-w-0 flex items-center gap-s overflow-x-auto no-scrollbar"
            style={maxW != null ? { maxWidth: maxW } : undefined}>
            {children}
          </div>
          {/* the overflow `…` springs in when the cluster collapses to that tier
              (and out when it re-expands) rather than snapping. It sits OUTSIDE the
              measured scroll row, so animating it can't feed the ResizeObserver. */}
          <AnimatePresence>
            {tier === 'overflow' && overflowActions.length > 0 && (
              <motion.div key="overflow" initial={{ opacity: 0, scale: 0.7 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.7 }} transition={spring.spatialFast}>
                <HeaderOverflow actions={overflowActions} />
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </Cluster.Provider>
  )
}

function noop() {}
function sameSet(a: Set<number> | null, b: Set<number>): boolean {
  if (!a || a.size !== b.size) return false
  for (const x of b) if (!a.has(x)) return false
  return true
}

/** The internal `…` menu the container owns — the collapsed tail of the cluster. */
function HeaderOverflow({ actions }: { actions: { id: number; label: string; icon?: LucideIcon; hint?: string; danger?: boolean; onSelect?: () => void }[] }) {
  return (
    <Popover align="right" width={220} placement="bottom"
      trigger={(open, toggle) => (
        <button type="button" onClick={toggle} aria-label="More actions" title="More actions" aria-expanded={open}
          className={cx('inline-flex items-center justify-center size-10 rounded-pill shrink-0 transition-colors',
            open ? 'bg-surface-high text-on-surface' : 'text-on-surface-var hover:bg-surface-high hover:text-on-surface')}>
          <MoreHorizontal size={20} />
        </button>
      )}>
      {(close) => (
        <div className="flex flex-col gap-0.5">
          {actions.map((a) => (
            <div key={a.id} className={a.danger ? '[&_button]:text-danger' : ''}>
              <MenuRow icon={a.icon ? <a.icon size={16} /> : undefined} label={a.label} hint={a.hint}
                onClick={() => { a.onSelect?.(); close() }} />
            </div>
          ))}
        </div>
      )}
    </Popover>
  )
}

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
const variants: Record<Variant, string> = {
  primary: 'bg-primary text-on-primary hover:bg-primary-emphasis',
  secondary: 'bg-surface-high text-on-surface hover:bg-surface-highest',
  ghost: 'bg-transparent text-on-surface hover:bg-surface-high',
  // `text-on-danger`, not `text-white` — same as Button's danger variant. Hardcoding white
  // pinned one ink across both themes, and the two themes need OPPOSITE inks: dark's danger is
  // a light red (#f66c66) where white is 2.89:1, light's is a deep red (#af2f29) where white is
  // 6.44:1. This was the only site that bypassed the token, and the only one that failed.
  danger: 'bg-danger text-on-danger hover:opacity-90',
}

/** One responsive header control. Declares an `icon` + `label` (+ optional
 *  `priority`/`danger`); the cluster renders it at the chosen tier (icon+label →
 *  label → icon-only) OR — when the row overflows and this control's priority lost
 *  the greedy fill — as a row in the container's `…` menu (nothing is rendered here
 *  in that case; the container draws the menu row from this control's declaration). */
export function HeaderControl({
  icon: Icon, label, onClick, variant = 'ghost', active, disabled, danger, priority = 'default', hint, className,
}: {
  icon?: LucideIcon
  label: string
  onClick?: () => void
  variant?: Variant
  active?: boolean
  disabled?: boolean
  danger?: boolean
  priority?: Priority
  /** Secondary line shown on the `…`-menu row (not in the button). */
  hint?: string
  className?: string
}) {
  const { visible, tier } = useHeaderChild({
    priority,
    canIcon: !!Icon,
    menu: { label, icon: Icon, hint, danger, onSelect: disabled ? undefined : onClick },
  })
  if (!visible) return null // container is showing this as a `…`-menu row
  // Rendering per tier:
  //  • FULL  → icon + label
  //  • TEXT  → icon + label if it HAS an icon (an icon'd control shouldn't drop its
  //            icon just to keep a redundant label); label-only for a control with no
  //            icon. TEXT exists to shave label-less buttons; icon'd controls treat it
  //            as FULL so the whole cluster degrades icon+label → icon-only uniformly.
  //  • ICON / OVERFLOW(kept) → icon-only (label → tooltip + aria); a label-only
  //            control can't reach here (canIcon=false → it lives in the … menu).
  const iconOnly = (tier === 'icon' || tier === 'overflow') && !!Icon
  const showLabel = !iconOnly
  const showIcon = !!Icon // icon shows in full/text/icon (never dropped while visible)
  const eff: Variant = danger ? 'danger' : variant
  return (
    <motion.button
      type="button" onClick={onClick} disabled={disabled} title={label}
      aria-label={iconOnly ? label : undefined}
      // 🔴 `active` DECIDED A COLOUR AND NOTHING ELSE. 14 call sites across 7 files pass it — the chat
      // Activity/History panels, the code cockpit's terminal, the files explorer, inbox settings, the
      // knowledge detail rail — and every one rendered an identical accessible node whether the panel it
      // controls was open or shut. `Button.ariaPressed` already carries this contract in its own doc: "a
      // button acting as a SELECTED/UNSELECTED choice must announce that state, or a screen-reader user
      // hears an identical label for the row they are on and the row they are not." `undefined` when
      // `active` is not passed, so a plain header action gains no misleading state.
      aria-pressed={active}
      whileTap={disabled ? undefined : { scale: 0.96 }}
      transition={spring.spatialFast}
      className={cx(
        'inline-flex items-center justify-center gap-1.5 rounded-pill text-[0.8125rem] select-none shrink-0',
        'transition-colors duration-100 disabled:opacity-40 disabled:pointer-events-none',
        iconOnly ? 'size-10' : 'h-10 px-l',
        active ? variants.secondary : variants[eff],
        className,
      )}
      style={fvs(470)}
    >
      {showIcon && <Icon size={16} />}
      {showLabel && <span className="whitespace-nowrap">{label}</span>}
    </motion.button>
  )
}

/** A `Segmented` that lives in the header cluster and degrades with it. Per the
 *  resolved model, EVERY option carries an icon + a label, so the control degrades
 *  uniformly: icon + label at FULL, icon-only at TEXT/ICON/OVERFLOW (the active
 *  segment stays highlighted, so the current value is always visible). It never
 *  drops into the `…` menu (a mode-slider has no single-row form) — it participates
 *  in the tier decision and stays visible, going icon-only when tight. Options MUST
 *  supply `icon` (dev-guard in place); label-only options can't reach the icon tier.
 *
 *  ICON-ONLY IS NOT THE LAST RUNG. A 4-option strip is still ~142px icon-only, and a
 *  phone header's whole content box is ~155px — so on a narrow screen the cluster ran
 *  out of ladder: `neverOverflow` kept the strip visible, the rail capped itself below
 *  the strip's width, and the surplus segments were simply CLIPPED. Measured at 390px
 *  before this was fixed: 6 surfaces lost 1-3 controls each — #/tasks showed 1 of its 4
 *  view buttons (Cards / Board / Graph gone, 2 of them under the `…` trigger),
 *  #/prompts lost System + Snippets, #/chat its permission-mode pill, #/workflows its
 *  Definitions tab. Horizontal scroll was NOT a rescue: scrollbars are hidden app-wide
 *  by owner tenet, so a clipped segment reads as absent.
 *
 *  So pass `collapse="menu"`: below its own fit threshold the strip becomes ONE pill
 *  showing the active option, which opens the full list in a Popover. That is the same
 *  final rung `ArtifactCompare` and `LoopComposer` already use — the canonical form
 *  existed, this wrapper just never offered it. Widening the cap instead cannot work:
 *  even with the title fully yielded the box is 39px short of strip + `…`. */
export function HeaderSegmented({ options, value, onChange, ariaLabel, disabled }: {
  options: SegOption[]
  value: string
  onChange: (k: string) => void
  ariaLabel?: string
  disabled?: boolean
}) {
  // Register with the cluster: it's a primary, never-overflow child (always visible;
  // collapses to icon-only rather than into the menu). canIcon=true (icons present).
  const { tier } = useHeaderChild({
    priority: 'primary',
    canIcon: true,
    neverOverflow: true,
    menu: { label: ariaLabel ?? 'Options' }, // unused (neverOverflow) but required by the type
  })
  // Match HeaderControl: icon+label at FULL/TEXT, icon-only when tight (ICON/OVERFLOW).
  // The active segment stays highlighted so the current value is always visible.
  const iconOnly = tier === 'icon' || tier === 'overflow'
  return <Segmented options={options} value={value} onChange={onChange} ariaLabel={ariaLabel} disabled={disabled} iconOnly={iconOnly} collapse="menu" />
}

/** A header mode control that shows ONLY the current selection, and expands on
 *  hover into the full option list — the shell's WidthPill idiom, applied to a
 *  header cluster control. Collapsed it's a single pill (active icon + label, or
 *  icon-only when the cluster is tight, mirroring HeaderControl); hovering (or
 *  keyboard-focusing) expands a floating menu of every option below the trigger,
 *  and picking one applies it and collapses back to the single indicator.
 *
 *  Use for an orthogonal *mode* selector (Chat Task mode / Permission mode) where
 *  the row shouldn't spend width on all N options at rest. It participates in the
 *  responsive cluster exactly like HeaderSegmented (primary, never-overflow: it
 *  always stays visible, going icon-only when tight, never into the `…` menu). */
export function HeaderModePill({ options, value, onChange, ariaLabel, disabled }: {
  options: SegOption[]
  value: string
  onChange: (k: string) => void
  ariaLabel?: string
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [rect, setRect] = useState<DOMRect | null>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  // Debounced close so the pointer can cross the trigger→menu gap: leaving either
  // arms a short timer; entering either cancels it. (The portaled menu is NOT a DOM
  // child of the wrapper, so a plain wrapper onMouseLeave would fire on that hop.)
  const closeTimer = useRef<number | undefined>(undefined)
  const { tier } = useHeaderChild({
    priority: 'primary',
    canIcon: true,
    neverOverflow: true,
    menu: { label: ariaLabel ?? 'Options' }, // unused (neverOverflow) but required by the type
  })
  const iconOnly = tier === 'icon' || tier === 'overflow'
  const active = options.find((o) => o.key === value) ?? options[0]
  // The menu declared `role="menu"` with `menuitemradio` options and implemented none of the
  // keyboard model: measured on `#/chat`, focus stayed on the trigger, ArrowDown did nothing, and
  // because the menu is PORTALED it sits last in the document — so Tab skipped straight past it to
  // the next header control with the menu still open. `autoFocus: false` because this popup also
  // opens on HOVER, and a pointer crossing a control must not steal focus; the first Arrow key
  // pulls focus in. The cursor starts on the CHECKED option (APG), not the top.
  const { move, restoreFocus, tabIndexFor } = useMenuCursor({
    containerRef: menuRef,
    count: options.length,
    openKey: open ? 'open' : null,
    initialIndex: Math.max(0, options.findIndex((o) => o.key === value)),
    autoFocus: false,
  })

  const measure = useCallback(() => {
    const el = wrapRef.current
    if (el) setRect(el.getBoundingClientRect())
  }, [])
  // 🪤 Set while focus is being handed BACK to the trigger on dismiss. The wrapper opens on
  // `onFocus` (the hover/keyboard-reveal contract), so restoring focus would re-open the menu the
  // instant Escape or Tab closed it — measured exactly that: `Escape closed: true → false`.
  const suppressFocusOpen = useRef(false)
  const doOpen = useCallback(() => {
    if (disabled || suppressFocusOpen.current) return
    window.clearTimeout(closeTimer.current)
    measure()
    setOpen(true)
  }, [disabled, measure])
  const armClose = useCallback(() => {
    window.clearTimeout(closeTimer.current)
    closeTimer.current = window.setTimeout(() => setOpen(false), 120)
  }, [])
  const closeNow = useCallback(() => { window.clearTimeout(closeTimer.current); setOpen(false) }, [])
  /** Close AND hand focus back to the trigger, with the reopen latch held across the focus event. */
  const dismiss = useCallback(() => {
    suppressFocusOpen.current = true
    closeNow()
    restoreFocus()
    window.setTimeout(() => { suppressFocusOpen.current = false }, 0)
  }, [closeNow, restoreFocus])

  // While open: reposition on scroll/resize (fixed portal must track the trigger),
  // close on Escape + outside pointerdown.
  useEffect(() => {
    if (!open) return
    const onScrollResize = () => measure()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { dismiss(); return }
      menuCursorKeydown(e, { move, dismiss })
    }
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node
      if (wrapRef.current?.contains(t) || menuRef.current?.contains(t)) return
      closeNow()
    }
    window.addEventListener('scroll', onScrollResize, true)
    window.addEventListener('resize', onScrollResize)
    window.addEventListener('keydown', onKey)
    window.addEventListener('pointerdown', onDown, true)
    return () => {
      window.removeEventListener('scroll', onScrollResize, true)
      window.removeEventListener('resize', onScrollResize)
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('pointerdown', onDown, true)
    }
  }, [open, measure, closeNow, move, dismiss])
  useEffect(() => () => window.clearTimeout(closeTimer.current), [])

  if (!active) return null
  const ActiveIcon = active.icon
  const label = active.label ?? active.key

  return (
    <div ref={wrapRef} className="relative shrink-0"
      onMouseEnter={doOpen} onMouseLeave={armClose}
      onFocus={doOpen}
      onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) armClose() }}>
      {/* Collapsed trigger — the current selection. Matches HeaderControl sizing so
          it sits flush beside sibling controls (h-10 pill, or size-10 icon-only). */}
      <button type="button" aria-haspopup="menu" aria-expanded={open} aria-label={`${ariaLabel ?? 'Mode'}: ${label}`}
        title={active.title ?? `${ariaLabel ?? 'Mode'}: ${label}`} disabled={disabled}
        onClick={() => (open ? closeNow() : doOpen())}
        className={cx(
          'inline-flex items-center justify-center gap-1.5 rounded-pill text-[0.8125rem] select-none shrink-0',
          'transition-colors duration-100 disabled:opacity-40 disabled:pointer-events-none',
          iconOnly ? 'size-10' : 'h-10 px-l',
          open ? 'bg-surface-highest text-on-surface' : 'bg-surface-high text-on-surface hover:bg-surface-highest',
        )}
        style={fvs(470)}>
        {ActiveIcon && <ActiveIcon size={16} className="shrink-0" />}
        {!iconOnly && <span className="whitespace-nowrap">{label}</span>}
        {!iconOnly && <ChevronDown size={13} className="shrink-0 -mr-1 text-on-surface-low transition-transform" style={{ transform: open ? 'rotate(180deg)' : 'none' }} />}
      </button>
      {/* Portal to <body> with position:fixed so the menu escapes the header's
          stacking + overflow context (the header row is painted UNDER the page
          body, so an in-flow absolute menu is invisible). Anchored to the
          trigger's rect, right-aligned to the trigger's right edge so it can't
          spill under the shell corner. */}
      {createPortal(
        <AnimatePresence>
          {open && !disabled && rect && (
            <motion.div ref={menuRef}
              initial={{ opacity: 0, scale: 0.97, y: -4 }}
              animate={{ opacity: 1, scale: 1, y: 0, transition: spring.spatialFast }}
              exit={{ opacity: 0, scale: 0.98, transition: spring.effects }}
              style={{ position: 'fixed', top: rect.bottom + 4, right: Math.max(8, window.innerWidth - rect.right), transformOrigin: 'top right' }}
              role="menu" aria-label={ariaLabel}
              className="z-[var(--z-menu)]"
              onMouseEnter={doOpen} onMouseLeave={armClose}>
              {/* Match the canonical menu idiom (Popover/FilterMenu): a rounded-lgi
                  container with rounded-md rows. The inner row radius is smaller than
                  the container's, and the container padding (p-s) frames the rows so
                  the corners nest cleanly (fully-round pill rows don't sit right inside
                  a rounded-rect container). */}
              <div className="flex flex-col gap-0.5 rounded-lgi bg-surface-container p-s min-w-[11rem]" style={{ boxShadow: 'var(--shadow-menu)' }}>
                {options.map((o, i) => {
                  const on = o.key === value
                  const OptIcon = o.icon
                  return (
                    <button key={o.key} type="button" role="menuitemradio" aria-checked={on}
                      tabIndex={tabIndexFor(i)}
                      onClick={() => { onChange(o.key); dismiss() }}
                      title={o.title ?? o.label}
                      className="flex items-center gap-s w-full rounded-md px-m h-9 text-left text-[0.8125rem] transition-colors"
                      style={on
                        ? withWeight({ background: 'var(--color-primary)', color: 'var(--color-on-primary)' }, 550)
                        : { color: 'var(--color-on-surface)' }}
                      onMouseEnter={(e) => { if (!on) e.currentTarget.style.background = 'var(--color-surface-high)' }}
                      onMouseLeave={(e) => { if (!on) e.currentTarget.style.background = 'transparent' }}>
                      {OptIcon && <OptIcon size={15} className="shrink-0" />}
                      <span className="whitespace-nowrap">{o.label ?? o.key}</span>
                      {on && <Check size={14} className="ml-auto shrink-0" />}
                    </button>
                  )
                })}
              </div>
            </motion.div>
          )}
        </AnimatePresence>,
        document.body,
      )}
    </div>
  )
}
