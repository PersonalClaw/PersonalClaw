import type { ReactNode } from 'react'
import { DestinationTabs } from '../../app/DestinationTabs'
import type { RouteProps } from '../../app/useQueryState'
import { LIBRARY_TABS, libraryTabOf } from './libraryTabs'

/** Library (#3815): Knowledge, Memory, Files and Artifacts, what the agent knows or made, as the
 *  tabs of one destination. The shell draws this around the page the address names;
 *  `DestinationTabs` is the strip, and how it keeps each tab's address. */
export function LibrarySection({ route, navigate, fallback, children }: Pick<RouteProps, 'navigate'> & {
  /** The route the address names, which picks the tab. */
  route: string
  /** What shows while the tab's page loads, under the strip. */
  fallback: ReactNode
  /** The tab's page. */
  children: ReactNode
}) {
  return (
    <DestinationTabs name="Library" tabs={LIBRARY_TABS} tab={libraryTabOf(route)} navigate={navigate} fallback={fallback}>
      {children}
    </DestinationTabs>
  )
}
