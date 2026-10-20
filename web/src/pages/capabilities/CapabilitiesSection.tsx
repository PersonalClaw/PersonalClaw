import { Suspense, type ReactNode } from 'react'
import { Segmented } from '../../ui/Segmented'
import { CornersClearedAbove, TopBar } from '../../ui/TopBar'
import { useIsMobile } from '../../app/useIsMobile'
import type { RouteProps } from '../../app/useQueryState'
import { CAPABILITY_TABS, capabilityTabOf } from './capabilityTabs'

/** Capabilities (#3817): Agents, Skills, Tools, Prompts, Apps and the Store, the configuration of
 *  what the agent can do, as the tabs of one destination.
 *
 *  The shell draws this around the page the address names (`renderPage` still routes each tab's
 *  address to that tab's page), so each tab keeps its address and its own code-split page, and the
 *  strip stays mounted while the tab under it changes: the focus stays on the tab just chosen.
 *
 *  The strip sits in the band between the shell corners, as a page's header does. The tab's page
 *  draws its own header below it, which therefore has no corner over it and pads to the page's
 *  edges (`CornersClearedAbove`). Choosing a tab replaces the history entry, as every in-page view
 *  does: the address is the tab's, so a reload and a copied link open it, and Back leaves
 *  Capabilities for where you were before it.
 *
 *  Below the width the six labels need, the strip folds into one pill naming the tab, which opens
 *  the six (`Segmented`'s `collapse="menu"`), icon-only on a phone. */
export function CapabilitiesSection({ route, query, navigate, fallback, children }: Pick<RouteProps, 'query' | 'navigate'> & {
  /** The route the address names, which picks the tab. */
  route: string
  /** What shows while the tab's page loads, under the strip. */
  fallback: ReactNode
  /** The tab's page. */
  children: ReactNode
}) {
  const isMobile = useIsMobile()
  const tab = capabilityTabOf(route, query)
  const choose = (id: string) => {
    const next = CAPABILITY_TABS.find((t) => t.id === id)
    if (next && next.id !== tab) navigate(next.address, { replace: true })
  }
  return (
    <div className="flex h-full flex-col">
      <TopBar keepCornerPadding left={
        <Segmented ariaLabel="Capabilities" collapse="menu" iconOnly={isMobile} value={tab} onChange={choose}
          options={CAPABILITY_TABS.map(({ id, label, icon }) => ({ key: id, label, icon }))} />
      } />
      <div className="min-h-0 flex-1">
        <CornersClearedAbove.Provider value>
          <Suspense fallback={fallback}>{children}</Suspense>
        </CornersClearedAbove.Provider>
      </div>
    </div>
  )
}
