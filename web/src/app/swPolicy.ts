// ── Service-worker caching POLICY (MOBILE-COMPANION T3.1 / §C2) ─────────────
//
// The whole security surface of the service worker lives in this file, on
// purpose: `sw.ts` is thin plumbing around these three functions, so the rule
// that matters can be unit-tested as pure data and cannot drift between the
// several places a response could enter a cache.
//
// THE RULE: `/api/*` responses are NEVER written to, nor read from, any cache.
// A cached authenticated API response is a data leak — a later visitor, or the
// same browser after logout, would be served the previous session's approvals,
// inbox rows or file listings off disk. Beyond leaking, stale approval data is
// dangerous on its own terms (plan §2.7 fail-closed for correctness): answering
// a tool call that already timed out is worse than seeing nothing.
//
// `mayCache()` is the ONE gate. `strategyFor()` is defined in terms of it, so a
// path can never be assigned a caching strategy without also being cacheable —
// they cannot disagree. Anything unrecognised is `network-only`: the default is
// fail-closed, so a future route is not silently cached because someone forgot
// to add it to a deny-list.

/** The app shell — the ONLY paths precached at install time.
 *
 *  Every entry is build-output that is identical for every user: the navigation
 *  document, the favicon, the manifest, the app icons, and the one font
 *  `index.html` preloads. Nothing user-specific, nothing from `/api`. Adding a
 *  user-scoped URL here would be the same leak in a different coat, which is why
 *  `swPolicy.test.ts` asserts the list is disjoint from `/api` AND that every
 *  entry exists in `web/public/` (a missing entry would fail `cache.addAll` and
 *  take the whole service worker down at install).
 *
 *  Hashed `/assets/*` bundles are deliberately NOT here: their names change every
 *  build, so enumerating them would mean codegen. They are content-addressed and
 *  therefore immutable, so runtime `cache-first` is both safe and correct.
 */
export const SHELL_DOCUMENT = '/'

export const APP_SHELL = [
  SHELL_DOCUMENT,
  '/claw.svg',
  '/manifest.webmanifest',
  '/icons/icon.svg',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/fonts/dm-sans.woff2',
] as const

/** Path prefixes holding immutable, user-independent build output. */
export const CACHEABLE_PREFIXES = ['/assets/', '/fonts/', '/icons/', '/sprites/', '/vendor/'] as const

export type FetchStrategy =
  /** Go to the network. Never read cache, never write cache. */
  | 'network-only'
  /** Network wins; fall back to cache only when the network fails (offline). */
  | 'network-first'
  /** Cache wins; go to the network only on a miss, then store the response. */
  | 'cache-first'

/** True for anything under the API surface — the never-cached namespace.
 *
 *  Matches `/api` itself as well as `/api/...` so a route added at the bare
 *  prefix cannot slip past. */
export function isApiPath(pathname: string): boolean {
  return pathname === '/api' || pathname.startsWith('/api/')
}

/** The single cache gate: may a response for *url* be stored, or served, from a
 *  cache? Consulted before every `cache.put` and before every `cache.match`.
 *
 *  Fail-closed: only same-origin build output qualifies. Cross-origin responses
 *  (a CDN, a model provider) are never cached, `/api` is never cached, and an
 *  unrecognised same-origin path is never cached. */
export function mayCache(url: URL, origin: string): boolean {
  if (url.origin !== origin) return false
  if (isApiPath(url.pathname)) return false
  if ((APP_SHELL as readonly string[]).includes(url.pathname)) return true
  return CACHEABLE_PREFIXES.some((p) => url.pathname.startsWith(p))
}

/** The strategy for one request.
 *
 *  A navigation is `network-first` so a fresh deploy's `index.html` — and with it
 *  the new `/assets/*` hashes — is picked up the moment the gateway is reachable.
 *  That is what stops a service worker from pinning an old bundle on the desktop
 *  app, where the gateway serves this same SPA at `/`. The cached shell is a
 *  strictly offline fallback, never a preference — and the fallback is keyed on
 *  `SHELL_DOCUMENT`, never on the requested URL, so a deep link cannot pull a
 *  non-cacheable URL out of the cache under its own key.
 *
 *  Everything the gate rejects — `/api` first among them — is `network-only`. */
export function strategyFor(url: URL, origin: string, isNavigation: boolean): FetchStrategy {
  if (isNavigation) return 'network-first'
  if (!mayCache(url, origin)) return 'network-only'
  return 'cache-first'
}
