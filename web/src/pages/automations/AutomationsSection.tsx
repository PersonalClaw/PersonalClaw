import type { ReactNode } from 'react'
import { DestinationTabs } from '../../app/DestinationTabs'
import type { RouteProps } from '../../app/useQueryState'
import { AUTOMATIONS_TABS, automationsTabOf } from './automationTabs'

/** Automations (#3816): the one list of everything that runs without you, and the workflows those
 *  automations run, as the tabs of one destination. The shell draws this around the page the
 *  address names; `DestinationTabs` is the strip, and how it keeps each tab's address. */
export function AutomationsSection({ route, navigate, fallback, children }: Pick<RouteProps, 'navigate'> & {
  /** The route the address names, which picks the tab. */
  route: string
  /** What shows while the tab's page loads, under the strip. */
  fallback: ReactNode
  /** The tab's page. */
  children: ReactNode
}) {
  return (
    <DestinationTabs name="Automations" tabs={AUTOMATIONS_TABS} tab={automationsTabOf(route)} navigate={navigate} fallback={fallback}>
      {children}
    </DestinationTabs>
  )
}
