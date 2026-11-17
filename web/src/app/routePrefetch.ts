/** ── A reload starts its page while the app learns who you are ─────────────────────────────
 *
 *  The shell shows its loading fallback until `GET /api/dashboard/config` answers (`identity.tsx`,
 *  `App`'s `identityStatus === 'loading'`), and only then mounts the route, whose code then loads
 *  and whose reads then go out. On production v0.3.9, on a calm machine, Needs you's code was
 *  fetched 3.8 s after a reload, once that read had answered, and its first card showed 5.7 to
 *  7.3 s after it. So while identity is read, the route's code and its first reads start too,
 *  through the data layer's own dedup (`fetchKey`): the page, once it mounts, joins the requests
 *  still in flight or finds their answers landed. Nothing renders early. The fallback stays until
 *  identity answers, so onboarding never flashes and nothing shows for a device that is not
 *  signed in.
 *
 *  A read the gateway refuses (a device not signed in: 401 or 403) writes nothing, since the data
 *  layer writes only an answer. And when identity itself fails, every key started here is
 *  forgotten (`forgetRoutePrefetch`), so a device that turns out not to be signed in keeps
 *  nothing read before the app knew.
 *
 *  Only the routes whose first paint waits on a read are here; any other route starts nothing.
 */
import { fetchKey, forgetKeys } from '../lib/data'
import { NEEDS_YOU_KEY, readNeedsYou } from '../lib/useNeedsYou'
import { redirectFor } from './routeRedirects'
import { parseHash } from './useHashRoute'

type Start = (key: string, read: () => Promise<unknown>) => void

/** Each route's code and first reads: the same keys and reads its page makes on mount. */
const ROUTES: Record<string, (query: Record<string, string>, start: Start) => Promise<unknown>> = {
  'mission-control': async (_query, start) => {
    start(NEEDS_YOU_KEY, readNeedsYou)
    await import('../pages/dashboard/NeedsYouPage')
  },
  library: async (query, start) => {
    // The list view only: a power-user tab reads its own.
    if (query.view) return
    // Each read is the page's own (its module's), so a page that joins it gets the same answer.
    const lib = await import('../pages/library/useLibrary')
    start('projects:list', lib.readLibraryProjects)
    start('knowledge:collections', lib.readLibraryCollections)
    start('knowledge:stats', lib.readLibraryStats)
    // The list's key is built from the address and the sort kept on this device, as the page
    // builds it.
    const params = lib.libraryParamsOf(lib.mappedQuery(query), lib.storedLibrarySort())
    start(lib.libraryKey(params), () => lib.readLibrary(params))
    await import('../pages/library/LibraryPage')
  },
}

/** The keys this boot's prefetch started, for `forgetRoutePrefetch`. */
const started = new Set<string>()
/** Identity failed: nothing more starts, even a read a route reaches after its code loads. */
let forgotten = false
/** The prefetch ran this boot (a second call, as StrictMode's second effect, starts nothing). */
let ran = false

/** Start the code and first reads of the route *hash* opens (the bar's own by default), once per
 *  boot. Returns the keys it started. A failure here is the page's to meet: it reads again. */
export function startRoutePrefetch(hash: string = typeof location === 'undefined' ? '' : location.hash): string[] {
  if (ran) return [...started]
  ran = true
  const moved = redirectFor(hash)
  const { route, sub, query } = parseHash('dashboard', moved === null ? hash : `#/${moved}`)
  // The route comes from the address: only the table's own keys, never an inherited member
  // (`#/constructor`, `#/__proto__`, `#/toString`), which is no prefetch and returns no promise.
  const prefetch = (sub || !Object.hasOwn(ROUTES, route)) ? undefined : ROUTES[route]
  if (!prefetch) return []
  const start: Start = (key, read) => {
    if (forgotten) return
    started.add(key)
    fetchKey(key, read).catch(() => { /* the page reads again and says what failed */ })
  }
  prefetch(query, start).catch(() => { /* a chunk that failed to load is the page's to retry */ })
  return [...started]
}

/** Forget every key this boot's prefetch started: identity failed, so the device may not keep
 *  what was read before the app knew whether it is signed in. */
export function forgetRoutePrefetch(): void {
  forgotten = true
  forgetKeys([...started])
}

/** Test-only: a new boot. */
export function resetRoutePrefetch(): void {
  started.clear()
  forgotten = false
  ran = false
}
