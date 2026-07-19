import { useEffect, useRef } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { PanelLeftClose, PanelLeftOpen, SquareTerminal } from 'lucide-react'
import { spring } from '../design/motion'
import { ThemeControl } from './TopBar'
import { WidthPill } from './WidthPill'
import { NotificationBell } from './NotificationBell'
import { SystemWidget } from './SystemWidget'
import { useIsMobile } from '../app/useIsMobile'

/** The app shell's two persistent CORNER regions — not a full header row, just
 *  the two corners that carry native shell controls, floating above page content:
 *   • LEFT: a cut-out attached to the nav rail's top-right edge → the nav
 *     collapse/expand toggle.
 *   • RIGHT: the screen's top-right corner → theme + content-width controls.
 *  Each is a subtle surface tile (matching the rail tint) so it reads as shell
 *  chrome distinct from the page beneath. */

/** Left corner — attached to the nav's top-right edge. Rendered inside the main
 *  area (which is `relative`), pinned top-left so it hugs the nav boundary.
 *  Publishes its measured width to `--shell-corner-l` so page TopBars pad to
 *  clear it. */
export function ShellCornerLeft({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  useShellCornerWidth(ref, '--shell-corner-l')
  return (
    // A pull-tab hugging the nav's top-right edge: flush to the left (no gap),
    // rounded only on the right so it reads as physically attached to the rail.
    <div ref={ref} className="absolute left-0 top-0 z-30 py-2">
      <button type="button" onClick={onToggle}
        aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'} title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        className="grid h-9 w-8 place-items-center rounded-r-lg border border-l-0 border-outline-variant/40 bg-surface-low/80 text-on-surface-low backdrop-blur-sm transition-colors hover:bg-surface-low hover:text-on-surface">
        {/* the collapse/expand glyph morphs (spring scale+fade key-swap) as the
            rail toggles, so the pull-tab reflects the state change with life */}
        <AnimatePresence mode="wait" initial={false}>
          <motion.span key={collapsed ? 'open' : 'close'} initial={{ opacity: 0, scale: 0.6 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.6 }} transition={spring.spatialFast} className="grid place-items-center">
            {collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
          </motion.span>
        </AnimatePresence>
      </button>
    </div>
  )
}

/** Right corner — the screen's top-right. Terminal-drawer toggle + theme +
 *  content-width as native shell controls, above any page chrome. Publishes
 *  width to `--shell-corner-r`. */
export function ShellCornerRight({ terminalOpen, onToggleTerminal, navigate }: { terminalOpen: boolean; onToggleTerminal: () => void; navigate: (path: string) => void }) {
  const ref = useRef<HTMLDivElement>(null)
  useShellCornerWidth(ref, '--shell-corner-r')
  useShellCornerHeight(ref, '--shell-corner-rh')
  // On mobile the content-width preset is force-ignored (always full width), so
  // the width control has nothing to do — drop it from the corner cluster.
  const isMobile = useIsMobile()
  return (
    // Flush to the screen's top-right corner: the control cluster sits tight to
    // the top + right edges, rounded only on the inner (bottom-left) corner so it
    // reads as connected to the screen edges rather than floating.
    <div ref={ref} className="absolute right-0 top-0 z-30 flex items-center">
      <div className="flex items-center gap-1 rounded-bl-xl border border-r-0 border-t-0 border-outline-variant/40 bg-surface-low/80 px-1.5 py-1.5 backdrop-blur-sm">
        <button type="button" onClick={onToggleTerminal}
          aria-label={terminalOpen ? 'Hide terminal' : 'Open terminal'} title={terminalOpen ? 'Hide terminal (⌘`)' : 'Open terminal (⌘`)'}
          className="grid size-7 place-items-center rounded-pill transition-colors"
          style={terminalOpen
            ? { background: 'color-mix(in srgb, var(--color-primary) 16%, transparent)', color: 'var(--color-primary)' }
            : { color: 'var(--color-on-surface-low)' }}>
          <SquareTerminal size={16} />
        </button>
        {!isMobile && (
          <>
            <span className="h-4 w-px bg-outline-variant/40" aria-hidden />
            <WidthPill />
          </>
        )}
        <NotificationBell navigate={navigate} />
        <ThemeControl />
        {/* Gateway connectivity dot (live) → click opens the full system widget. */}
        <span className="h-4 w-px bg-outline-variant/40" aria-hidden />
        <SystemWidget />
      </div>
    </div>
  )
}

/** Measure a corner's width and publish it as a CSS var on :root, so page
 *  TopBars can reserve exactly that much space (kept in sync via ResizeObserver). */
function useShellCornerWidth(ref: React.RefObject<HTMLDivElement | null>, varName: string) {
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const set = () => document.documentElement.style.setProperty(varName, `${Math.ceil(el.getBoundingClientRect().width)}px`)
    set()
    const ro = new ResizeObserver(set)
    ro.observe(el)
    return () => ro.disconnect()
  }, [ref, varName])
}

/** Publish a corner's measured HEIGHT (incl. its padding) as a CSS var, so a
 *  right-docked side panel can start below the corner band instead of letting
 *  the floating corner overlap its sticky header / close button. */
function useShellCornerHeight(ref: React.RefObject<HTMLDivElement | null>, varName: string) {
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const set = () => document.documentElement.style.setProperty(varName, `${Math.ceil(el.getBoundingClientRect().height)}px`)
    set()
    const ro = new ResizeObserver(set)
    ro.observe(el)
    return () => ro.disconnect()
  }, [ref, varName])
}
