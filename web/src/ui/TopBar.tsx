import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
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

/** Make the left slot's "…flexes and truncates" promise actually TRUE.
 *
 *  `min-w-0 flex-1` shrinks the SLOT correctly, but a child `<span>` has `min-width: auto`
 *  and **42 of the app's 52 `title-l` call sites carry no `truncate` class** — so the text
 *  laid out at its full intrinsic width and PAINTED THROUGH the slot, sliding under the
 *  `shrink-0` control row. Measured at 390px: the title overlapped the controls on 6
 *  otherwise-canonical pages (prompts, workflows, notifications, inbox, knowledge,
 *  learning), 2 at 834px, 0 at 1280px, identically in both themes.
 *
 *  Fixing it at the slot that OWNS the contract beats adding `truncate` to 42 call sites
 *  that would each drift again:
 *   · `truncate` on the title gives it `overflow-hidden` + `text-overflow: ellipsis` +
 *     `nowrap`, so it stops at the slot edge with an ellipsis rather than a cut mid-glyph.
 *     Scoped by `[data-type]` so it hits the page title, not sibling chips/buttons. Matched
 *     as a DESCENDANT, not a direct child: three of the six wrap the title one level deeper
 *     (`learning`/`workflows` in a `<div>`) or make it a flex container whose own child
 *     overflows (`inbox`), so a `>` selector reached only half the family.
 *   · `[&_div]:min-w-0` so the shrink can propagate — a nested flex wrapper otherwise
 *     re-establishes `min-width: auto` and the text overflows again.
 *   · `pr-s` — a title that ends flush against the control row reads as broken rather than
 *     truncated. Measured `gapToControls: 0` on all six before this.
 *
 *  NOT `overflow-hidden` on the SLOT, which was the first thing I tried. It fixes titles but
 *  it also clips the pages that put a whole CONTROL ROW in this slot (`#/loops`, `#/code`,
 *  `#/files` — the already-logged LoopComposer overflow), turning controls that were merely
 *  overlapping into controls that are **gone**: measured reachable 3 → 1 on `#/loops` and
 *  `#/code`. Clipping text is a graceful degradation; clipping a button is a regression. The
 *  overflowing-control-row family stays an owner taste call, untouched here.
 *
 *  Also depends on `HeaderActions` leaving the title `titleFloor()` px — see the ceiling in
 *  that file. Truncating inside a slot that can still reach 0px width shows nothing at all;
 *  measured on #/prompts, which is why that ceiling fix ships with this one. */
const TITLE_TRUNCATES = [
  // every ancestor between the slot and the title must allow shrinking, else the innermost
  // flex box keeps `min-width: auto` and the text still overflows.
  '[&_div]:min-w-0',
  '[&_[data-type]]:min-w-0 [&_[data-type]]:truncate [&_[data-type]]:pr-s',
].join(' ')

/** How a `HeaderActions` cluster inside this bar moves the bar's row below the shell corners.
 *  Module-private: the only writer is the cluster, through `useStackBelowCorners`. */
const StackBelowCorners = createContext<((stacked: boolean) => void) | null>(null)

/** For the header cluster: put this bar's row BELOW the shell corners (`true`) or back between
 *  them. `null` outside a TopBar, where there are no corners to clear.
 *
 *  The cluster decides, not the bar, because the decision needs the one number only the cluster
 *  measures — the narrowest it can get (its never-overflow controls plus its `…`). The bar only
 *  applies it. */
export function useStackBelowCorners(): ((stacked: boolean) => void) | null {
  return useContext(StackBelowCorners)
}

/** The bar's measurement probe: a zero-height box across the bar, padded exactly as the row is
 *  when it sits BETWEEN the corners. Its content width is that band's width whichever way the row
 *  is laid out, so the stacking decision never reads its own result. Read by `HeaderActions`. */
export const TOP_BAR_BAND = '[data-top-bar-band]'

/** App top bar — sparse NE chrome. Left slot for context (model pill / page
 *  title), right slot for actions. Theme + width controls are NOT here — they
 *  live in the persistent shell CORNERS (see ShellCorners), which float above
 *  this bar. The bar pads BOTH ends so its content lays out only in the space
 *  BETWEEN the two corners (collapse-toggle left, theme+width right) and never
 *  slides under either. The corner widths are CSS vars set by the shell. When a
 *  docked right panel is open the right corner sits over the sidebar, so the
 *  right padding collapses (no dead gap before the panel).
 *
 *  🔑 WHEN THE BAND BETWEEN THE CORNERS IS TOO NARROW, THE ROW GOES BELOW THEM. At 320px the band
 *  is 85px and at 390px 155px — less than the chat's two never-overflow mode pills and their `…`
 *  (140px), before its back button or title. No arrangement of that row inside the band can avoid an
 *  overlap, so the `HeaderActions` cluster asks for `stacked`: the corners keep the top of the bar
 *  and the row lays out under them across the full width, the way `SidePanel` already docks below
 *  the corner band. Only a bar with a cluster can stack, and only when the band cannot hold what the
 *  title slot cannot shed, a legible title and the cluster's floor side by side.
 *
 *  `below` is a context line under the row: facts about the page (who started it, where it came
 *  from, what it cost) that wrap onto more lines instead of competing with the actions for the
 *  row. A chip in the row can only overlap the controls or starve the title; a chip below it can
 *  only wrap. */
export function TopBar({ left, right, below, keepCornerPadding = false, contentAligned = false }: {
  left?: ReactNode; right?: ReactNode
  /** A context line under the row — chips that describe the page. It wraps; it never shares the
   *  row with the actions. */
  below?: ReactNode
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
  const [stacked, setStacked] = useState(false)
  // Clear each shell corner PLUS a gap, so header content never butts up against the
  // flush-to-edge corner chrome (collapse tab / control cluster).
  const cornerL = 'calc(var(--shell-corner-l, 56px) + var(--spacing-m, 12px))'
  const cornerR = panelOpen ? 'var(--spacing-l, 16px)' : 'calc(var(--shell-corner-r, 140px) + var(--spacing-m, 12px))'
  // contentAligned: align the row to the SAME centered content column the body uses, so the
  // title/breadcrumb line up with the content below and track the width toggle. The body centers
  // a `--content-width` column in the main pane via mx-auto; the equivalent gutter is
  // `(100% - content-width)/2`. Pad by the LARGER of that gutter and the corner clearance: at
  // narrow/default the gutter wins → the left edge matches the body column; at 'full' the corner
  // clearance wins → the actions still clear the floating shell corner.
  const gutter = 'calc((100% - var(--content-width)) / 2)'
  const bandL = contentAligned ? `max(${cornerL}, ${gutter})` : cornerL
  const bandR = contentAligned ? `max(${cornerR}, ${gutter})` : cornerR
  // Stacked, the row is under BOTH corners (the left tab is the taller one), so only the page's
  // own edge is kept on either side.
  const edge = contentAligned ? `max(var(--spacing-l, 16px), ${gutter})` : 'var(--spacing-l, 16px)'
  const padding = stacked
    ? { paddingLeft: edge, paddingRight: edge, paddingTop: 'max(var(--shell-corner-lh, 52px), var(--shell-corner-rh, 44px))' }
    : { paddingLeft: bandL, paddingRight: bandR }
  return (
    <StackBelowCorners.Provider value={setStacked}>
      <header className="relative flex shrink-0 flex-col" style={padding} data-stacked={stacked || undefined}>
        <div aria-hidden data-top-bar-band className="pointer-events-none invisible absolute inset-x-0 top-0 h-0"
          style={{ paddingLeft: bandL, paddingRight: bandR }} />
        {/* Left flexes + truncates; right is content-sized. A responsive HeaderActions cluster
            measures the AVAILABLE gap (header width − what the left slot cannot shed) rather than
            its own content box, so shedding controls can't latch overflow (see HeaderActions).
            contentAligned: the inner `px-l` mirrors the body wrapper's own edge padding so content
            edges (not just column edges) coincide. */}
        <div className={contentAligned ? 'flex h-14 items-center' : 'flex h-14 items-center justify-between'}>
          <div className={`flex min-w-0 flex-1 items-center gap-s ${contentAligned ? 'pl-l ' : ''}${TITLE_TRUNCATES}`} data-header-left>{left}</div>
          <div className={`flex shrink-0 items-center gap-s${contentAligned ? ' pr-l' : ''}`}>{right}</div>
        </div>
        {below && (
          <div data-header-below className={`flex min-w-0 flex-wrap items-center gap-x-s gap-y-xs pb-s${contentAligned ? ' px-l' : ''}`}>
            {below}
          </div>
        )}
      </header>
    </StackBelowCorners.Provider>
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
