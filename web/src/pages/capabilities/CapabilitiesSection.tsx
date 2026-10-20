import type { ReactNode } from 'react'
import { DestinationTabs } from '../../app/DestinationTabs'
import type { RouteProps } from '../../app/useQueryState'
import { CAPABILITY_TABS, capabilityTabOf } from './capabilityTabs'

/** Capabilities (#3817): Agents, Skills, Tools, Prompts, Apps and the Store, the configuration of
 *  what the agent can do, as the tabs of one destination. The shell draws this around the page the
 *  address names; `DestinationTabs` is the strip, and how it keeps each tab's address. */
export function CapabilitiesSection({ route, query, navigate, fallback, children }: Pick<RouteProps, 'query' | 'navigate'> & {
  /** The route the address names, which picks the tab. */
  route: string
  /** What shows while the tab's page loads, under the strip. */
  fallback: ReactNode
  /** The tab's page. */
  children: ReactNode
}) {
  return (
    <DestinationTabs name="Capabilities" tabs={CAPABILITY_TABS} tab={capabilityTabOf(route, query)} navigate={navigate} fallback={fallback}>
      {children}
    </DestinationTabs>
  )
}
