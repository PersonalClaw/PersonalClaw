import { Workflow, Zap } from 'lucide-react'
import type { DestinationTab } from '../../app/DestinationTabs'

/** Automations' tabs (#3816): everything that runs without you, in one destination.
 *
 *  Triggers and Workflows were two sidebar rows. The one list of automations is the first tab, at
 *  `#/automations`, the sidebar row's own address. Workflow definitions, which an automation runs
 *  but which run nothing on their own, and the runs no automation started, are the Workflows tab,
 *  at the address its page always had. No address is retired: `#/triggers` and its sub-routes open
 *  the list they always did, now inside Automations, so a bookmark, an old transcript's link, a
 *  notification's and a copied link land where they did. None of them is a row of
 *  `routeRedirects.ts`.
 *
 *  Pure data and two functions, so the shell can read which routes are Automations' without loading
 *  a page. */

export type AutomationsTabId = 'automations' | 'workflows'

/** The tabs, in the order the strip shows them. */
export const AUTOMATIONS_TABS: readonly DestinationTab<AutomationsTabId>[] = [
  { id: 'automations', label: 'Automations', icon: Zap, address: 'automations' },
  { id: 'workflows', label: 'Workflows', icon: Workflow, address: 'workflows' },
]

/** Every route Automations draws: its own, the list's address from before, and the Workflows tab. */
export const AUTOMATIONS_ROUTES: ReadonlySet<string> = new Set(['automations', 'triggers', 'workflows'])

/** Whether the shell draws `route` inside Automations. */
export const isAutomationsRoute = (route: string): boolean => AUTOMATIONS_ROUTES.has(route)

/** The tab a route opens: the Workflows page's, else the list. */
export const automationsTabOf = (route: string): AutomationsTabId => (route === 'workflows' ? 'workflows' : 'automations')
