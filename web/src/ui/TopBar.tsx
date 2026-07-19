import { useEffect, useState, type ReactNode } from 'react'
import { Sun, Moon, Monitor } from 'lucide-react'
import { IconButton } from './IconButton'
import { useMode } from '../app/theme'

/** Reactively track whether a docked right side panel is open (SidePanel sets a
 *  ref-counted `--rightpanel-open` on :root). When it is, the shell's
 *  top-right corner floats over the SIDEBAR rather than the page header, so the
 *  TopBar no longer reserves right-padding for it. */
function useRightPanelOpen(): boolean {
  const [open, setOpen] = useState(false)
  useEffect(() => {
    const root = document.documentElement
    const read = () => setOpen((Number(root.style.getPropertyValue('--rightpanel-open')) || 0) > 0)
    read()
    const mo = new MutationObserver(read)
    mo.observe(root, { attributes: true, attributeFilter: ['style'] })
    return () => mo.disconnect()
  }, [])
  return open
}

/** App top bar — sparse NE chrome. Left slot for context (model pill / page
 *  title), right slot for actions. Theme + width controls are NOT here — they
 *  live in the persistent shell CORNERS (see ShellCorners), which float above
 *  this bar. The bar pads BOTH ends so its content lays out only in the space
 *  BETWEEN the two corners (collapse-toggle left, theme+width right) and never
 *  slides under either. The corner widths are CSS vars set by the shell. When a
 *  docked right panel is open the right corner sits over the sidebar, so the
 *  right padding collapses (no dead gap before the panel). */
export function TopBar({ left, right, keepCornerPadding = false, contentAligned = false }: {
  left?: ReactNode; right?: ReactNode
  /** Keep the right corner padding even when a docked panel is open. Set on
   *  pages where the SidePanel docks BELOW this bar (e.g. the loop cockpit), so
   *  the shell corner still floats over the header and the action buttons must
   *  not slide under it. Pages where the panel reaches the screen top can leave
   *  this off so the padding collapses (no dead gap). */
  keepCornerPadding?: boolean
  /** Center the header's inner row to `--content-width` — the SAME centered
   *  column the page body uses — instead of spanning the full corner-padded bar.
   *  Set on pages whose header carries body-level controls (a breadcrumb + the
   *  item's title/actions, e.g. the Knowledge detail page) so those line up with
   *  the body column and track the global content-width toggle. The corner gaps
   *  are kept as MIN padding so the row still clears the shell corners when the
   *  content column is wider than the gap (the 'full' preset). */
  contentAligned?: boolean
}) {
  const panelOpen = useRightPanelOpen() && !keepCornerPadding
  if (contentAligned) {
    // Align the header's inner row to the SAME centered content column the body uses,
    // so the title/breadcrumb line up with the content below and track the width toggle.
    // The body centers a `--content-width` column in the main pane via mx-auto; the
    // equivalent left/right gutter is `(100% - content-width)/2`. We pad by the LARGER of
    // that gutter and the shell-corner clearance, so: at narrow/default the gutter wins →
    // the header's left edge matches the body column; at 'full' the corner clearance wins
    // → actions still clear the floating shell corner. The inner `px-l` mirrors the body
    // wrapper's own edge padding so content edges (not just column edges) coincide.
    const gutter = 'calc((100% - var(--content-width)) / 2)'
    const cornerL = 'calc(var(--shell-corner-l, 56px) + var(--spacing-m, 12px))'
    const cornerR = panelOpen ? 'var(--spacing-l, 16px)' : 'calc(var(--shell-corner-r, 140px) + var(--spacing-m, 12px))'
    return (
      <header className="flex h-14 shrink-0 items-center"
        style={{ paddingLeft: `max(${cornerL}, ${gutter})`, paddingRight: `max(${cornerR}, ${gutter})` }}>
        {/* Left takes the slack and truncates (the title shrinks gracefully); the action
            cluster keeps its full size so a wide set (Cancel/Save/Pin/… in edit mode)
            never crushes the breadcrumb into an overlap. */}
        <div className="flex min-w-0 flex-1 items-center gap-s pl-l" data-header-left>{left}</div>
        <div className="flex shrink-0 items-center gap-s pr-l">{right}</div>
      </header>
    )
  }
  return (
    <header className="flex items-center justify-between h-14 shrink-0"
      style={{
        // Clear each shell corner PLUS a gap, so header content never butts up
        // against the flush-to-edge corner chrome (collapse tab / control cluster).
        paddingLeft: 'calc(var(--shell-corner-l, 56px) + var(--spacing-m, 12px))',
        paddingRight: panelOpen ? 'var(--spacing-l, 16px)' : 'calc(var(--shell-corner-r, 140px) + var(--spacing-m, 12px))',
      }}>
      {/* Left flexes + truncates; right is content-sized. A responsive HeaderActions
          cluster measures the AVAILABLE gap (header width − left width) rather than its
          own content box, so shedding controls can't latch overflow (see HeaderActions). */}
      <div className="flex items-center gap-s min-w-0 flex-1" data-header-left>{left}</div>
      <div className="flex items-center gap-s shrink-0">{right}</div>
    </header>
  )
}

/** Cycles dark → light → system (follow OS). Icon reflects the chosen
 *  preference; tooltip names the next state. Rendered in the shell corner. */
export function ThemeControl() {
  const { preference, setPreference } = useMode()
  const next = preference === 'dark' ? 'light' : preference === 'light' ? 'auto' : 'dark'
  const icon = preference === 'dark' ? Moon : preference === 'light' ? Sun : Monitor
  const label = preference === 'auto' ? 'Theme: follow system' : preference === 'dark' ? 'Theme: dark' : 'Theme: light'
  return <IconButton icon={icon} label={`${label} — switch to ${next === 'auto' ? 'system' : next}`} size={36} onClick={() => setPreference(next)} />
}
