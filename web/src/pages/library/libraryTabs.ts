import { BookOpen, Database, FileCode, Files } from 'lucide-react'
import type { DestinationTab } from '../../app/DestinationTabs'

/** Library's tabs (#3815): everything the agent knows or made, in one destination.
 *
 *  Knowledge, Files and Artifacts were three sidebar rows, and Memory a page of Settings. Each is a
 *  tab here. Knowledge, Files and Artifacts keep the addresses their pages always had, so a
 *  bookmark, a reload, Back and a copied link open the tab they did before. Memory's page left
 *  Settings for `#/memory`, so `#/settings/memory` is a row of `routeRedirects.ts`, which carries
 *  the view and the record an old link names. `#/library`, the sidebar row's own address, opens
 *  the first tab.
 *
 *  Pure data and two functions, so the shell can read which routes are Library's without loading
 *  a page. */

export type LibraryTabId = 'knowledge' | 'memory' | 'files' | 'artifacts'

/** The tabs, in the order the strip shows them. */
export const LIBRARY_TABS: readonly DestinationTab<LibraryTabId>[] = [
  { id: 'knowledge', label: 'Knowledge', icon: BookOpen, address: 'knowledge' },
  { id: 'memory', label: 'Memory', icon: Database, address: 'memory' },
  { id: 'files', label: 'Files', icon: Files, address: 'files' },
  { id: 'artifacts', label: 'Artifacts', icon: FileCode, address: 'artifacts' },
]

/** Every route Library draws: its own, and each tab page's. */
export const LIBRARY_ROUTES: ReadonlySet<string> = new Set(['library', ...LIBRARY_TABS.map((t) => t.address)])

/** Whether the shell draws `route` inside Library. */
export const isLibraryRoute = (route: string): boolean => LIBRARY_ROUTES.has(route)

/** The tab a route opens; Library's own address opens the first. */
export const libraryTabOf = (route: string): LibraryTabId => LIBRARY_TABS.find((t) => t.address === route)?.id ?? 'knowledge'
