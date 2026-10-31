import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { MoreVertical, PanelLeftClose, PanelLeftOpen, SquareTerminal } from 'lucide-react'
import { spring } from '../design/motion'
import { ThemeControl, useThemeCycle } from './TopBar'
import { MenuRow, Popover } from './Popover'
import { NotificationBell } from './NotificationBell'
import { NeedsYouBadge } from './NeedsYouBadge'
import { DegradedChip } from './DegradedChip'
import { WidgetBoundary } from '../app/ErrorBoundary'
import { accentChip } from '../design/accent'
import { bindingLabel } from '../app/keymap'
import { TOUCH_TARGET } from './Toggle'

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
 *  clear it, and its height to `--shell-corner-lh`, which a TopBar whose row has
 *  moved below the corners (too narrow a band between them) clears instead. */
export function ShellCornerLeft({ collapsed, onToggle, focusMode = false }: {
  collapsed: boolean
  onToggle: () => void
  /** Set while focus mode hides the rail: the tab is then the visible way out of it, and names
   *  focus mode's key. */
  focusMode?: boolean
}) {
  const ref = useRef<HTMLDivElement>(null)
  useShellCornerWidth(ref, '--shell-corner-l')
  useShellCornerHeight(ref, '--shell-corner-lh')
  const label = focusMode ? 'Leave focus mode' : collapsed ? 'Expand sidebar' : 'Collapse sidebar'
  // The key the tab answers to, from the keymap: focus mode's while it hides the rail, else the
  // sidebar's.
  const key = bindingLabel(focusMode ? 'focus-mode' : 'sidebar')
  return (
    // A pull-tab hugging the nav's top-right edge: flush to the left (no gap),
    // rounded only on the right so it reads as physically attached to the rail.
    <div ref={ref} className="absolute left-0 top-0 z-30 py-2">
      <button type="button" onClick={onToggle} data-rail-toggle=""
        aria-label={label} title={`${label} (${key})`}
        className={`grid h-9 w-8 place-items-center rounded-r-lg border border-l-0 border-outline-variant/40 bg-surface-low/80 text-on-surface-low backdrop-blur-sm transition-colors hover:bg-surface-low hover:text-on-surface ${TOUCH_TARGET}`}>
        {/* the collapse/expand glyph morphs (spring scale+fade key-swap) as the
            rail toggles, so the pull-tab reflects the state change with life */}
        <AnimatePresence mode="wait" initial={false}>
          <motion.span key={focusMode || collapsed ? 'open' : 'close'} initial={{ opacity: 0, scale: 0.6 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.6 }} transition={spring.spatialFast} className="grid place-items-center">
            {focusMode || collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
          </motion.span>
        </AnimatePresence>
      </button>
    </div>
  )
}

/** Below this width the right corner folds Open terminal and Theme into one More (`CornerMore`),
 *  and its three 44 px targets (`TOUCH_TARGET`) sit edge to edge with no padding around them
 *  (`max-md:`, the same 768 px). Four targets made the corner 201 px wide at 390 and three with
 *  their gaps 153, and a started chat's header stacked below the corners at 390 (116 px, not 56);
 *  at 133 it is one row at 390 and 412. From 768 px up the corner is as it was. */
const FOLD_QUERY = '(max-width: 767px)'

function useFoldsTheCorner(): boolean {
  const [folds, setFolds] = useState(() => typeof window !== 'undefined' && window.matchMedia(FOLD_QUERY).matches)
  useEffect(() => {
    const mq = window.matchMedia(FOLD_QUERY)
    const onChange = () => setFolds(mq.matches)
    mq.addEventListener('change', onChange)
    onChange()
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return folds
}

/** The phone corner's More: Open terminal (with its shortcut) and Theme, in the corner's order and
 *  by the names their buttons have, as rows of one menu. Its glyph is a vertical ⋮: at 390 px it
 *  sits beside a page's own More actions (`…`, `HeaderActions`), and two of one glyph opening two
 *  different menus read as one control. */
function CornerMore({ terminalOpen, onToggleTerminal }: { terminalOpen: boolean; onToggleTerminal: () => void }) {
  const theme = useThemeCycle()
  const ThemeIcon = theme.icon
  return (
    <Popover align="right" width={260} placement="bottom" portal
      trigger={(open, toggle) => (
        <button type="button" onClick={toggle} aria-label="More" title="More: the terminal and the theme" aria-expanded={open}
          className={`grid size-7 place-items-center rounded-pill transition-colors ${TOUCH_TARGET}`}
          style={open || terminalOpen ? accentChip : { color: 'var(--color-on-surface-low)' }}>
          <MoreVertical size={16} />
        </button>
      )}>
      {(close) => (
        <div className="flex flex-col gap-0.5">
          <MenuRow icon={<SquareTerminal size={16} />} label={terminalOpen ? 'Hide terminal' : 'Open terminal'} nameIsLabel
            hint={bindingLabel('drawer')} selected={terminalOpen} onClick={() => { onToggleTerminal(); close() }} />
          <MenuRow icon={<ThemeIcon size={16} />} label={theme.label} nameIsLabel onClick={() => { theme.cycle(); close() }} />
        </div>
      )}
    </Popover>
  )
}

/** Right corner — the screen's top-right. Terminal-drawer toggle + content-width + what waits on
 *  you + notifications + theme as native shell controls, above any page chrome. Below 768 px the
 *  terminal and the theme fold into one More (`CornerMore`). Publishes width to
 *  `--shell-corner-r`. */
export function ShellCornerRight({ terminalOpen, onToggleTerminal, navigate }: { terminalOpen: boolean; onToggleTerminal: () => void; navigate: (path: string) => void }) {
  const ref = useRef<HTMLDivElement>(null)
  useShellCornerWidth(ref, '--shell-corner-r')
  useShellCornerHeight(ref, '--shell-corner-rh')
  const folds = useFoldsTheCorner()
  return (
    // Flush to the screen's top-right corner: the control cluster sits tight to
    // the top + right edges, rounded only on the inner (bottom-left) corner so it
    // reads as connected to the screen edges rather than floating.
    <div ref={ref} className="absolute right-0 top-0 z-30 flex items-center">
      <div data-shell-corner-cluster className="flex items-center gap-1 max-md:gap-0 rounded-bl-xl border border-r-0 border-t-0 border-outline-variant/40 bg-surface-low/80 px-1.5 max-md:px-0 py-1.5 max-md:py-0 backdrop-blur-sm">
        {folds ? (
          <WidgetBoundary what="the terminal and theme menu" compact><CornerMore terminalOpen={terminalOpen} onToggleTerminal={onToggleTerminal} /></WidgetBoundary>
        ) : (
          <button type="button" onClick={onToggleTerminal}
            aria-label={terminalOpen ? 'Hide terminal' : 'Open terminal'}
            title={`${terminalOpen ? 'Hide terminal' : 'Open terminal'} (${bindingLabel('drawer')})`}
            className={`grid size-7 place-items-center rounded-pill transition-colors ${TOUCH_TARGET}`}
            style={terminalOpen
              ? accentChip
              : { color: 'var(--color-on-surface-low)' }}>
            <SquareTerminal size={16} />
          </button>
        )}
        {/* Each control renders inside its own boundary: one that throws leaves a glyph and Retry
            in its slot, and its neighbours and the rest of the app keep working. With none, the
            System status popover throwing on one poll unmounted the whole app. */}
        <WidgetBoundary what="what waits on you" compact><NeedsYouBadge navigate={navigate} className={TOUCH_TARGET} /></WidgetBoundary>
        <WidgetBoundary what="notifications" compact><NotificationBell navigate={navigate} className={TOUCH_TARGET} /></WidgetBoundary>
        {!folds && <WidgetBoundary what="the theme control" compact><ThemeControl className={TOUCH_TARGET} /></WidgetBoundary>}
        {/* No-model degraded-mode chip — self-polling;
            renders nothing unless a model-dependent surface is on its LLM-free floor. The gateway
            connection is the status line's (`app/StatusLine`). */}
        <WidgetBoundary what="the model status" compact><DegradedChip className={TOUCH_TARGET} /></WidgetBoundary>
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
 *  the floating corner overlap its sticky header / close button, and a TopBar
 *  whose row cannot fit between the corners can lay it out below both. */
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
