import { Suspense, type ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import { Segmented } from '../ui/Segmented'
import { TOUCH_TARGET } from '../ui/Toggle'
import { CornersClearedAbove, TopBar } from '../ui/TopBar'
import { useIsMobile } from './useIsMobile'
import type { RouteProps } from './useQueryState'

/** One tab of a destination whose tabs are pages with addresses of their own. */
export interface DestinationTab<Id extends string = string> {
  readonly id: Id
  readonly label: string
  readonly icon: LucideIcon
  /** The tab's address, after `#/`. */
  readonly address: string
}

/** The folded pill's 44 px floor below 1024 px. TopBar gives every `[data-type]` in its left slot
 *  `min-width: 0` (its title rule), from a descendant selector that outranks a plain class, and the
 *  pill carries a `data-type`. So the width half is marked important here, or the pill stays
 *  32 px wide inside a 44 px floor. */
const PILL_TARGET = `${TOUCH_TARGET} max-lg:min-w-11!`

/** A destination's tab strip over the page its address names (Capabilities, #3817; Library,
 *  #3815).
 *
 *  The shell draws this around the page `renderPage` picks for the address, so each tab keeps its
 *  address and its own code-split page, and the strip stays mounted while the tab under it
 *  changes: the focus stays on the tab just chosen.
 *
 *  The strip sits in the band between the shell corners, as a page's header does. The tab's page
 *  draws its own header below it, which therefore has no corner over it and pads to the page's
 *  edges (`CornersClearedAbove`). Choosing a tab replaces the history entry, as every in-page view
 *  does: the address is the tab's, so a reload and a copied link open it, and Back leaves the
 *  destination for where you were before it.
 *
 *  Below the width the labels need, the strip folds into one pill naming the tab, which opens the
 *  others (`Segmented`'s `collapse="menu"`), icon-only on a phone. Below 1024 px that pill is a
 *  44 px target (`TOUCH_TARGET`), as the shell's corner controls are. */
export function DestinationTabs({ name, tabs, tab, navigate, fallback, children }: Pick<RouteProps, 'navigate'> & {
  /** The destination's name, which names the strip. */
  name: string
  /** The tabs, in the order the strip shows them. */
  tabs: readonly DestinationTab[]
  /** The tab the address opens. */
  tab: string
  /** What shows while the tab's page loads, under the strip. */
  fallback: ReactNode
  /** The tab's page. */
  children: ReactNode
}) {
  const isMobile = useIsMobile()
  const choose = (id: string) => {
    const next = tabs.find((t) => t.id === id)
    if (next && next.id !== tab) navigate(next.address, { replace: true })
  }
  return (
    <div className="flex h-full flex-col">
      <TopBar keepCornerPadding left={
        <Segmented ariaLabel={name} collapse="menu" collapsedClassName={PILL_TARGET} iconOnly={isMobile} value={tab} onChange={choose}
          options={tabs.map(({ id, label, icon }) => ({ key: id, label, icon }))} />
      } />
      <div className="min-h-0 flex-1">
        <CornersClearedAbove.Provider value>
          <Suspense fallback={fallback}>{children}</Suspense>
        </CornersClearedAbove.Provider>
      </div>
    </div>
  )
}
