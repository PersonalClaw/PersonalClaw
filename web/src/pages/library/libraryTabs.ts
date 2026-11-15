import { Database, Files, Library } from 'lucide-react'
import type { DestinationTab } from '../../app/DestinationTabs'

/** Library's tabs: Library, Memory and Files, with words.
 *
 *  Library is one list of everything kept (notes, files you added, links, the pages and documents
 *  your chats made, report write-ups and what your intents found), at `#/library`. Knowledge and
 *  Artifacts, its two old tabs, are one list now: `#/knowledge` and `#/artifacts` are rows of
 *  `routeRedirects.ts` onto it, and their item pages (`#/knowledge/item/<id>`, `#/artifacts/<slug>`)
 *  draw under the Library tab. Memory's page left Settings for `#/memory`, so `#/settings/memory`
 *  is a redirect row too.
 *
 *  Pure data and two functions, so the shell can read which routes are Library's without loading
 *  a page. */

export type LibraryTabId = 'library' | 'memory' | 'files'

/** The tabs, in the order the strip shows them. */
export const LIBRARY_TABS: readonly DestinationTab<LibraryTabId>[] = [
  { id: 'library', label: 'Library', icon: Library, address: 'library' },
  { id: 'memory', label: 'Memory', icon: Database, address: 'memory' },
  { id: 'files', label: 'Files', icon: Files, address: 'files' },
]

/** Every route Library draws: each tab's page, and the item pages of the two stores it lists. */
export const LIBRARY_ROUTES: ReadonlySet<string> = new Set([...LIBRARY_TABS.map((t) => t.address), 'knowledge', 'artifacts'])

/** Whether the shell draws `route` inside Library. */
export const isLibraryRoute = (route: string): boolean => LIBRARY_ROUTES.has(route)

/** The tab a route opens: an item page is the Library tab's. */
export const libraryTabOf = (route: string): LibraryTabId => LIBRARY_TABS.find((t) => t.address === route)?.id ?? 'library'
