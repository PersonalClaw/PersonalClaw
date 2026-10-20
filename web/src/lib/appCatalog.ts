/** THE one place that flattens `/api/apps/catalog`'s four app lists into one list.
 *
 *  The payload splits available apps across `bundled` / `localApps` / `remoteApps` /
 *  `gitApps` because four different scanners produce them. Three consumers used to
 *  concatenate those lists themselves, in THREE different orders — the card grid put
 *  `localApps` before `gitApps`, the Sources-panel consent lookup put `gitApps` FIRST, and
 *  the onboarding step used a third order. With a name in two lists that is three answers
 *  to "which copy of this app am I looking at?" (#2528).
 *
 *  The backend now resolves collisions before serialising (`apps/catalog.py`:
 *  `resolve_catalog_entries`), so at most one entry per name reaches the wire and the order
 *  here cannot change an answer. This function exists so that stays true: one flattening,
 *  in the backend's own precedence order, for every consumer to share.
 *
 *  🔴 Do not re-concatenate the four lists at a call site — `test_one_owner_merges_the_app_catalog`
 *  reds on a second merge.
 */
import type { AppCatalogEntry } from './api'

/** The subset of the catalog payload this needs — every consumer's own type is wider. */
export interface CatalogAppLists {
  bundled?: AppCatalogEntry[]
  localApps?: AppCatalogEntry[]
  remoteApps?: AppCatalogEntry[]
  gitApps?: AppCatalogEntry[]
}

/** Every available-to-install app the catalog surfaced, in the backend's precedence order
 *  (shipped → local disk → indexed → cloned). Deduped by name as a belt-and-braces floor:
 *  the payload should already carry each name once, and if a regression ever puts a name in
 *  two lists again, every consumer at least agrees on which one it sees. */
export function catalogApps(catalog: CatalogAppLists | null | undefined): AppCatalogEntry[] {
  const all = [
    ...(catalog?.bundled ?? []),
    ...(catalog?.localApps ?? []),
    ...(catalog?.remoteApps ?? []),
    ...(catalog?.gitApps ?? []),
  ]
  const byName = new Map<string, AppCatalogEntry>()
  for (const e of all) if (!byName.has(e.name)) byName.set(e.name, e)
  return [...byName.values()]
}
