import { type ReactNode } from 'react'
import { AnimatePresence } from 'framer-motion'

/** Standard list-with-detail page skeleton. A full-width `TopBar` stays pinned
 *  on top; the scrollable body and an optional right-docked `SidePanel` sit in a
 *  row BELOW it. The panel pushes only the body — never the header — so opening
 *  it can't shove the header's action buttons off the right edge.
 *
 *  Usage: pass the page's `<TopBar keepCornerPadding … />` as `topBar` (keep the
 *  corner padding so the actions clear the floating shell corner that hovers over
 *  the header), the already-gated panel node (e.g. `open && <SidePanel fillHeight
 *  … />`) as `panel`, and the centered page content as children. */
export function WorkbenchLayout({ topBar, panel, scroll = true, children }: {
  /** The page's header, every page-level control in its action cluster: no bar sits under it. */
  topBar: ReactNode
  /** Already gated + wrapped, e.g. `open && <SidePanel fillHeight … />`. The
   *  panel docks below the TopBar, so it must be a `fillHeight` SidePanel. */
  panel?: ReactNode
  /** When true (default) the layout owns the body's vertical scroll. Set false
   *  for pages whose body manages its own height/scroll (e.g. a Kanban shell). */
  scroll?: boolean
  children: ReactNode
}) {
  return (
    <div className="flex h-full flex-col">
      {topBar}
      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col">
          <div className={`min-w-0 flex-1 ${scroll ? 'overflow-y-auto' : 'flex min-h-0 flex-col'}`}>{children}</div>
        </div>
        <AnimatePresence>{panel}</AnimatePresence>
      </div>
    </div>
  )
}
