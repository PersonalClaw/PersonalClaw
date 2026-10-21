/** The addresses the dashboard has retired, and where each one opens now (#3819).
 *
 *  A page that moves leaves its old address behind: in bookmarks, old transcripts, Inbox items,
 *  notifications and copied links. Each retired path, with the query keys it carries, is one row
 *  here, and the hash router (`useHashRoute`) is the one place that reads them. It rewrites the
 *  address in the bar to the row's target before any page draws: on the first load, on Back and
 *  Forward, on a link the desktop shell or a notification opens, and on every `navigate`. The
 *  rewrite replaces the history entry, so Back never returns to the old address.
 *
 *  A row retires the path the app wrote, matched as the router reads it: segment by segment, each
 *  decoded once and compared exactly, empty segments dropped, the query from the first `?`. A path
 *  of more or fewer segments is another address, even one the router joins into the same sub-path
 *  (`a/b/c` and `a/b%2Fc`). An artifact's slug never holds a `/`, so its old link was always
 *  `files/<slug>`, and that is the path retired. A retired query value is not a row, since the
 *  table maps query keys only: an old value a page still accepts, such as an old Settings → Memory
 *  tab, is mapped by that page.
 *
 *  What each row holds to (`routeRedirects.test.tsx`):
 *
 *    · it names the old address, the new one and the pull request that retired the old one, and its
 *      new address names its page: a carried segment never picks which page opens;
 *    · it lands on an address that opens as itself, never on an address another row retires, so a
 *      redirect never chains: when a page moves again, the rows that point at it move with it;
 *    · it keeps the substate the new page reads. Each `:name` segment of the old address fills the
 *      same `:name` of the new one, as it was written, and each query key in `keeps` is carried
 *      under the name the new page reads it by. Any other key meant something only to the old page,
 *      and is dropped;
 *    · its old address is an entry of `shippedRoutes.ts` whose `lands` is this row's target, and the
 *      target is an entry too, which opens a page.
 *
 *  A page that sends its address somewhere else because of what it holds right now is not retiring
 *  the address, and keeps that landing to itself: the bare loop and code addresses while no draft
 *  is under review, a room address with no room, setup once it is done.
 *
 *  Pure data and one function: no DOM, no imports.
 */

export interface RouteRedirect {
  /** The retired address's path, after `#/`: fixed segments, and `:name` for a segment carried
   *  over to the new address. */
  readonly from: string
  /** Where it opens now: a path using the same `:name`s, and the query, if any, that makes the new
   *  page show what the old one did. */
  readonly to: string
  /** The old address's query keys the new page reads, each to the name the new page reads it by. */
  readonly keeps?: Readonly<Record<string, string>>
  /** The pull request that retired the old address; `before v0.1.0` for one already retired in the
   *  first release. */
  readonly pr: number | 'before v0.1.0'
}

export const ROUTE_REDIRECTS: readonly RouteRedirect[] = [
  // Artifacts left Files for a page of their own. Old transcripts and events still hold the link
  // to an artifact from before.
  { from: 'files/:slug', to: 'artifacts/:slug', pr: 65 },
  // Vocabulary became a section of Speech & Transcription.
  { from: 'settings/vocabulary', to: 'settings/voice?section=vocabulary', pr: 'before v0.1.0' },
  // The memory studio left Settings to be Library's Memory tab (#3815). An old link names the view
  // (`tab`), the record (`sel`), a learned preference (`pref`) and a folder's own memory
  // (`partition`), all of which the page reads under the same names.
  { from: 'settings/memory', to: 'memory', keeps: { tab: 'tab', sel: 'sel', pref: 'pref', partition: 'partition' }, pr: 3815 },
  // Notifications became a filter of the Inbox. The feed's own narrowing (unread, or one kind) is
  // the filter's `show`, beside the `filter` that names it, and the note it had open is the one
  // the filter opens. The bell, the palette and old transcripts held this address.
  { from: 'notifications', to: 'inbox?filter=notifications', keeps: { filter: 'show', open: 'open' }, pr: 3818 },
]

/** A segment as the router hands it to a page: decoded, or as written when it is not valid
 *  encoding (`useHashRoute`'s parse). */
function decoded(segment: string): string {
  try { return decodeURIComponent(segment) } catch { return segment }
}

/** The segments `pattern` captures from `segments`, or null when they are not its address. A fixed
 *  segment is compared as the router reads it, so `%66iles` is `files` here as it is to the page. */
function captures(pattern: string, segments: readonly string[]): Record<string, string> | null {
  const parts = pattern.split('/')
  if (parts.length !== segments.length) return null
  const out: Record<string, string> = {}
  for (let i = 0; i < parts.length; i++) {
    if (parts[i].startsWith(':')) out[parts[i].slice(1)] = segments[i]
    else if (parts[i] !== decoded(segments[i])) return null
  }
  return out
}

/** Where `address` opens now, or null when no row retires it. `address` is written as the bar
 *  holds it, with or without its one `#/`, its segments percent-encoded. A fixed segment matches
 *  however it is encoded; a carried segment keeps its encoding, so it fills its own segment of the
 *  new address and no other. A kept key the old address repeats carries its last value, the one
 *  the router hands a page. */
export function redirectFor(address: string, table: readonly RouteRedirect[] = ROUTE_REDIRECTS): string | null {
  const bare = address.replace(/^#?\/?/, '')
  const at = bare.indexOf('?')
  const segments = (at < 0 ? bare : bare.slice(0, at)).split('/').filter(Boolean)
  for (const row of table) {
    const captured = captures(row.from, segments)
    if (!captured) continue
    const [path, fixed = ''] = row.to.split('?')
    const target = path.split('/').map((part) => (part.startsWith(':') ? captured[part.slice(1)] : part)).join('/')
    const query = new URLSearchParams(fixed)
    const old = new URLSearchParams(at < 0 ? '' : bare.slice(at + 1))
    for (const [key, name] of Object.entries(row.keeps ?? {})) {
      const value = old.getAll(key).at(-1)
      if (value !== undefined && !query.has(name)) query.set(name, value)
    }
    const written = query.toString()
    return written ? `${target}?${written}` : target
  }
  return null
}
