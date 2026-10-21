import { Blocks, Brain, FileText, Sparkles, Store, Users, Wrench, type LucideIcon } from 'lucide-react'

/** Capabilities' tabs (#3817): what configures what the agent can do, in one destination.
 *
 *  Agents, Skills, Learning, Tools, Prompts and Apps were six sidebar rows, and the Store a view of
 *  Apps. Each is a tab here, Learning beside the Skills whose proposals it links to, and each keeps
 *  the address its page always had, so a bookmark, a reload, Back and a copied link open the tab
 *  they did before. No address is retired, so none of them is a row of `routeRedirects.ts`.
 *  `#/capabilities`, the sidebar row's own address, opens the first tab.
 *
 *  Pure data and two functions, so the shell can read which routes are Capabilities' without
 *  loading a page. */

export type CapabilityTabId = 'agents' | 'skills' | 'learning' | 'tools' | 'prompts' | 'apps' | 'store'

export interface CapabilityTab {
  readonly id: CapabilityTabId
  readonly label: string
  readonly icon: LucideIcon
  /** The tab's address, after `#/`: the one its page has always had. */
  readonly address: string
}

/** The tabs, in the order the strip shows them. */
export const CAPABILITY_TABS: readonly CapabilityTab[] = [
  { id: 'agents', label: 'Agents', icon: Users, address: 'agents' },
  { id: 'skills', label: 'Skills', icon: Sparkles, address: 'skills' },
  { id: 'learning', label: 'Learning', icon: Brain, address: 'learning' },
  { id: 'tools', label: 'Tools', icon: Wrench, address: 'tools' },
  { id: 'prompts', label: 'Prompts', icon: FileText, address: 'prompts' },
  { id: 'apps', label: 'Apps', icon: Blocks, address: 'apps' },
  // The Store is the Apps page's store view, at the address it has always had.
  { id: 'store', label: 'Store', icon: Store, address: 'apps?view=store' },
]

/** Every route Capabilities draws: its own, and each tab page's. */
export const CAPABILITY_ROUTES: ReadonlySet<string> = new Set(['capabilities', 'agents', 'skills', 'learning', 'tools', 'prompts', 'apps'])

/** Whether the shell draws `route` inside Capabilities. */
export const isCapabilityRoute = (route: string): boolean => CAPABILITY_ROUTES.has(route)

/** The tab an address opens: its route, and for the Apps page its view. */
export function capabilityTabOf(route: string, query: Readonly<Record<string, string>>): CapabilityTabId {
  if (route === 'apps') return query.view === 'store' ? 'store' : 'apps'
  return CAPABILITY_TABS.find((t) => t.address === route)?.id ?? 'agents'
}
