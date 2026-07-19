import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { motion, useReducedMotion } from 'framer-motion'
import { X, Maximize2, Minimize2 } from 'lucide-react'
import { IconButton } from './IconButton'
import { spring, bounce, expr } from '../design/motion'
import type { RouteProps } from '../app/useQueryState'

const MIN_W = 320, MAX_W = 720, DEFAULT_W = 420

/** Reusable right-docked side panel (design-language building block, used across
 *  pages). Three modes via two controls:
 *   • docked  — in-flow panel that PUSHES the main content narrower; the left
 *     edge is a drag handle to resize (persisted per `storeKey`).
 *   • expanded — one button UNFURLS the panel to a full-viewport overlay via a
 *     right-anchored clip wipe (it reads as the panel sweeping across to fill the
 *     screen, not a separate surface crossfading in); the same button furls it
 *     back to the docked slab. Redesign-v2: the wipe replaces the old opacity
 *     crossfade so expand/collapse feels like ONE surface changing size, and its
 *     speed scales with the global expressiveness knob.
 *   • close — dismiss entirely.
 *  Header (title + expand/collapse + close) is non-scrolling; body scrolls. */
export function SidePanel({ title, icon, onClose, urlKey, storeKey = 'sidepanel-w', fillHeight = false, onExpand, children }: {
  title: ReactNode
  icon?: ReactNode
  onClose: () => void
  /** Optional URL-sync: `{ key, setQuery }` drops `?key` from the URL when the
   *  panel closes (via the X, Escape, or the parent's own close). Standardizes the
   *  "Back closes the panel" contract so a page opening a panel via `?open=<id>`
   *  (push) doesn't have to also hand-wire the close-clears-the-key half. The
   *  parent still owns WHEN the panel mounts (the `?key` presence); this only
   *  guarantees close ⇒ key cleared. */
  urlKey?: { key: string; setQuery: RouteProps['setQuery'] }
  storeKey?: string
  /** When the host page already has its own TopBar, the page content sits below
   *  the floating shell corner, so the panel must NOT add the corner offset (it
   *  would double-count and stop short of the viewport bottom). Set this on
   *  pages that dock the panel beneath a TopBar. */
  fillHeight?: boolean
  /** When the content has a DEDICATED full-page home, expand should go THERE
   *  instead of unfurling the in-place overlay — pass the navigation here and the
   *  expand button delegates (the internal overlay mode is bypassed entirely). */
  onExpand?: () => void
  children: ReactNode
}) {
  const reduce = useReducedMotion()
  // When URL-bound, closing the panel (X / Escape / parent) also clears its query
  // key so the URL + the open state can't diverge. One close path, both effects.
  const close = useCallback(() => {
    if (urlKey) urlKey.setQuery({ [urlKey.key]: null })
    onClose()
  }, [urlKey, onClose])
  const [width, setWidth] = useState<number>(() => {
    const v = Number(localStorage.getItem(storeKey))
    return v >= MIN_W && v <= MAX_W ? v : DEFAULT_W
  })
  const [expanded, setExpanded] = useState(false)
  useEffect(() => { localStorage.setItem(storeKey, String(width)) }, [width, storeKey])
  // While a panel is DOCKED open, the screen's top-right shell corner floats
  // over the sidebar (not the page header), so the page TopBar no longer needs
  // to reserve right-padding for it. Publish a ref-counted flag on :root that
  // TopBar reads to drop that padding. (Skipped while expanded — the overlay
  // covers the corner + header entirely.)
  useEffect(() => {
    if (expanded) return
    const root = document.documentElement
    const cur = () => Number(root.style.getPropertyValue('--rightpanel-open')) || 0
    root.style.setProperty('--rightpanel-open', String(cur() + 1))
    return () => root.style.setProperty('--rightpanel-open', String(Math.max(0, cur() - 1)))
  }, [expanded])
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') { if (expanded) setExpanded(false); else close() } }
    window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h)
  }, [expanded, close])

  const onHandleDown = useCallback((e: React.PointerEvent) => {
    e.preventDefault()
    const startX = e.clientX, startW = width
    const move = (ev: PointerEvent) => setWidth(Math.max(MIN_W, Math.min(MAX_W, startW + (startX - ev.clientX))))
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up) }
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up)
  }, [width])

  const header = (
    <div className="shrink-0 bg-surface/95 px-l py-m flex items-center justify-between border-b border-outline-variant/40">
      <div className="flex items-center gap-s min-w-0">{icon}<span data-type="title-l" className="text-on-surface truncate">{title}</span></div>
      <div className="flex items-center gap-1 shrink-0">
        <IconButton icon={expanded ? Minimize2 : Maximize2}
          label={expanded ? 'Collapse to panel' : (onExpand ? 'Open full page' : 'Expand to full width')}
          size={34}
          onClick={onExpand && !expanded ? onExpand : () => setExpanded((v) => !v)} />
        <IconButton icon={X} label="Close" size={34} onClick={close} />
      </div>
    </div>
  )

  if (expanded) {
    // full-viewport overlay. Portaled to <body> because an animated (transformed)
    // ancestor would otherwise become the containing block for position:fixed and
    // clip this to the docked column's width.
    //
    // Redesign-v2 morph: instead of a whole-surface opacity crossfade, the overlay
    // is fixed full-screen from the start but WIPES OPEN from the right edge — it
    // reveals only the docked-width strip first (exactly where the panel already
    // sits) and sweeps leftward to claim the viewport, so it reads as the SAME
    // panel growing rather than a new surface. Reverse on collapse via the exit
    // wipe. Wipe speed/overshoot scale with expressiveness; reduced-motion → no clip.
    const furled = `inset(0px 0px 0px calc(100dvw - ${width}px))`
    return createPortal(
      <motion.div className="fixed inset-0 z-50 flex flex-col bg-surface"
        initial={reduce ? { opacity: 0 } : { clipPath: furled }}
        animate={reduce ? { opacity: 1 } : { clipPath: 'inset(0px 0px 0px 0px)' }}
        exit={reduce ? { opacity: 0 } : { clipPath: furled }}
        transition={reduce ? spring.effects : { ...bounce.settle, stiffness: 240 + expr(120, 0.4) }}>
        {header}
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto px-l py-l" style={{ maxWidth: 'calc(var(--content-width) + 200px)' }}>{children}</div>
        </div>
      </motion.div>,
      document.body,
    )
  }

  // docked, resizable, in-flow (pushes content). The screen's top-right shell
  // corner (theme/width/terminal controls) floats over this column at z-30, so
  // the panel starts BELOW the corner band — its header + close button never sit
  // under the corner. (Scalable: any page using SidePanel gets this for free.)
  const dockOffset = fillHeight ? '0px' : 'var(--shell-corner-rh, 56px)'
  // A small bottom gap so the rounded BOTTOM-inner corner floats clear of the
  // viewport edge (else it jams flush to the screen bottom and the round reads as
  // square). The panel spans from just below the top offset to this gap above the
  // bottom → full available height, both inner corners visible on every page.
  const bottomGap = 'var(--spacing-m, 12px)'
  return (
    // The docked panel is attached to the viewport's RIGHT edge, so it rounds its
    // INNER (left) corners — the edge facing the content — to read as a floating
    // panel rather than a full-bleed column. Radius via a token (--radius-xl); the
    // outer (right) edge stays flush to the browser edge (square).
    <motion.div className="relative shrink-0 overflow-hidden border-l border-outline-variant/40 bg-surface"
      style={{ marginTop: dockOffset, marginBottom: bottomGap, height: `calc(100% - ${dockOffset} - ${bottomGap})`, borderTopLeftRadius: 'var(--radius-xl)', borderBottomLeftRadius: 'var(--radius-xl)' }}
      initial={{ width: 0, opacity: 0 }} animate={{ width, opacity: 1 }} exit={{ width: 0, opacity: 0 }} transition={spring.spatialDefault}>
      {/* left-edge resize handle — the visible seam springs thicker + brighter on
          hover (scaled by expr) so it telegraphs "drag to resize" with a little
          life instead of a bare 1px color swap. */}
      <div onPointerDown={onHandleDown} className="absolute left-0 top-0 bottom-0 w-1.5 cursor-ew-resize z-20 group">
        <motion.span
          className="absolute left-0 top-0 bottom-0 bg-outline-variant/40 group-hover:bg-primary transition-colors"
          initial={false}
          animate={{ width: 1 }}
          whileHover={{ width: 1 + expr(2.5, 0.3) }}
          transition={bounce.subtle}
        />
      </div>
      {/* flex column: the header stays PINNED (shrink-0) while only the content
          region scrolls — the panel title/close never scroll away. */}
      <div className="flex h-full flex-col" style={{ width }}>
        {header}
        <div className="min-h-0 flex-1 overflow-y-auto px-l py-l">{children}</div>
      </div>
    </motion.div>
  )
}
