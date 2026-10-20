/** The addresses the dashboard has retired, and where each one opens now (#3819).
 *
 *  A page that moves leaves its old address behind: in bookmarks, old transcripts, Inbox items,
 *  notifications and copied links. Each retired address is one row here, and the hash router
 *  (`useHashRoute`) is the one place that reads them. It rewrites the address in the bar to the
 *  row's target before any page draws: on the first load, on Back and Forward, on a link the
 *  desktop shell or a notification opens, and on every `navigate`. The rewrite replaces the history
 *  entry, so Back never returns to the old address.
 *
 *  What each row holds to (`routeRedirects.test.tsx`):
 *
 *    · it names the old address, the new one and the pull request that retired the old one;
 *    · it lands on an address that opens as itself, never on another row's old address, so a
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
]

/** The segments `pattern` captures from `segments`, or null when they are not its address. */
function captures(pattern: string, segments: readonly string[]): Record<string, string> | null {
  const parts = pattern.split('/')
  if (parts.length !== segments.length) return null
  const out: Record<string, string> = {}
  for (let i = 0; i < parts.length; i++) {
    if (parts[i].startsWith(':')) out[parts[i].slice(1)] = segments[i]
    else if (parts[i] !== segments[i]) return null
  }
  return out
}

/** Where `address` opens now, or null when no row retires it. `address` is written as the bar
 *  holds it after `#/` (a leading `#/` is ignored), its segments percent-encoded; a carried segment
 *  keeps its encoding, so it fills its own segment of the new address and no other. */
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
      const value = old.get(key)
      if (value !== null && !query.has(name)) query.set(name, value)
    }
    const written = query.toString()
    return written ? `${target}?${written}` : target
  }
  return null
}
