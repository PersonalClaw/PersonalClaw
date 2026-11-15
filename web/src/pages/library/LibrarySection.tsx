import type { ReactNode } from 'react'
import { DestinationTabs } from '../../app/DestinationTabs'
import type { RouteProps } from '../../app/useQueryState'
import { LIBRARY_TABS, libraryTabOf } from './libraryTabs'

/** Library, Memory and Files, what you keep and what the agent knows or made, as the tabs of one
 *  destination. The shell draws this around the page the address names; `DestinationTabs` is the
 *  strip, and how it keeps each tab's address. The Library alone passes the strip's two opt-ins:
 *  `keepLabels`, so a phone's strip keeps its words where they fit, and `markCurrent`, so the current
 *  tab shows under forced colours. The Capabilities and Automations strips pass
 *  neither and draw as before. */
export function LibrarySection({ route, navigate, fallback, children }: Pick<RouteProps, 'navigate'> & {
  /** The route the address names, which picks the tab. */
  route: string
  /** What shows while the tab's page loads, under the strip. */
  fallback: ReactNode
  /** The tab's page. */
  children: ReactNode
}) {
  return (
    <DestinationTabs name="Library" tabs={LIBRARY_TABS} tab={libraryTabOf(route)} navigate={navigate} fallback={fallback} keepLabels markCurrent>
      {children}
    </DestinationTabs>
  )
}
