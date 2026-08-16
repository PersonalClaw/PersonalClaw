/// <reference lib="webworker" />
// ── PersonalClaw service worker (MOBILE-COMPANION T3.1) ─────────────────────
//
// Thin plumbing. Every caching decision is delegated to `app/swPolicy.ts`, which
// is pure and unit-tested; this file only wires it to the worker lifecycle. Built
// to `dist/sw.js` by `scripts/buildServiceWorker.mjs` (esbuild, invoked from a
// Vite `closeBundle` hook) so it lands at the dist ROOT and therefore registers
// at scope `/` — a worker emitted under `/assets/` could only control `/assets/`.
//
// It is typechecked by `tsconfig.sw.json`, not the app's `tsconfig.json`: the
// `webworker` and `dom` libs cannot coexist in one TypeScript program (they
// redeclare `self`, `location`, `fetch`, …), so the worker gets its own program.
// `npm run typecheck` runs both.
//
// UPDATE / ACTIVATION STRATEGY — deliberately the conservative default:
// NO `skipWaiting()`, NO `clients.claim()`. A new worker waits until every tab
// running the old one is gone, and only then purges the old caches. Three
// reasons, in order of weight:
//
//   1. It buys nothing. Navigations are already network-first, so a reachable
//      gateway always serves the freshest `index.html` — a stale shell cannot
//      pin an old bundle even under the old worker. `skipWaiting` would only
//      make the swap happen sooner, not make it correct.
//   2. It would actively break live tabs. `App.tsx` lazy-loads nearly every
//      route, so an open tab requests hashed chunks on demand. Claiming clients
//      and purging the previous cache in the same activation swaps the asset
//      cache out from under that tab; a chunk the new build no longer ships
//      would then 404 and the route would fail to mount.
//   3. The gateway serves this same SPA to the desktop app at `/`. A wrong
//      update strategy here is not a phone bug, it is every user's bug.
//
// `CACHE_NAME` is versioned by a hash of the built `/assets` filenames, so a
// build whose output changed gets a fresh cache and activation evicts the
// orphans — deterministically, with no timestamp to make builds irreproducible.

import { APP_SHELL, SHELL_DOCUMENT, mayCache, strategyFor } from './app/swPolicy'

/** Injected by `scripts/buildServiceWorker.mjs` via esbuild `define`. */
declare const __SW_CACHE_VERSION__: string

// `self` is typed as a plain WorkerGlobalScope by the `webworker` lib; the
// service-worker-only members (skipWaiting, clients, registration) live on the
// narrower interface.
const sw = self as unknown as ServiceWorkerGlobalScope

const CACHE_NAME = `personalclaw-shell-${__SW_CACHE_VERSION__}`

/** Store *response* for *request* — but only if the policy gate allows it.
 *
 *  Every write to a cache in this worker goes through here. `status === 200`
 *  rather than `response.ok` because the Cache API refuses a 206 outright. */
async function store(request: Request, url: URL, response: Response): Promise<void> {
  if (response.status !== 200) return
  if (!mayCache(url, sw.location.origin)) return
  const cache = await caches.open(CACHE_NAME)
  await cache.put(request, response.clone())
}

/** Network wins; the cache is consulted only when the network fails. */
async function networkFirst(request: Request, url: URL): Promise<Response> {
  try {
    const response = await fetch(request)
    await store(request, url, response)
    return response
  } catch (err) {
    // Offline. Serve the precached shell so the SPA still boots and can render
    // its own offline state. Keyed on SHELL_DOCUMENT, never on the requested
    // URL — a deep link boots the same shell and client routing takes over.
    const cache = await caches.open(CACHE_NAME)
    const shell = await cache.match(SHELL_DOCUMENT)
    if (shell) return shell
    throw err
  }
}

/** Cache wins. Only reached for content-addressed, user-independent build output. */
async function cacheFirst(request: Request, url: URL): Promise<Response> {
  const cache = await caches.open(CACHE_NAME)
  const cached = await cache.match(request)
  if (cached) return cached
  const response = await fetch(request)
  await store(request, url, response)
  return response
}

sw.addEventListener('install', (event) => {
  // Atomic: a missing shell entry fails installation loudly rather than leaving a
  // worker that half-works offline. `swPolicy.test.ts` asserts each entry exists
  // in the build inputs, so this cannot fail from a typo.
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll([...APP_SHELL])))
})

sw.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      for (const key of await caches.keys()) {
        if (key !== CACHE_NAME) await caches.delete(key)
      }
    })(),
  )
})

sw.addEventListener('fetch', (event) => {
  const { request } = event
  // Mutations are never cached and never re-issued through the worker: re-running
  // a request that carries a body can break streamed uploads (`POST /api/files`).
  if (request.method !== 'GET') return

  const url = new URL(request.url)
  const strategy = strategyFor(url, sw.location.origin, request.mode === 'navigate')

  // `network-only` — which is what every `/api/*` request resolves to — returns
  // WITHOUT calling respondWith. The browser then performs the fetch itself, so
  // the response never enters worker JavaScript, let alone a cache. That is a
  // stronger guarantee than `respondWith(fetch(request))`: there is no code path
  // in this file through which an API response could reach `cache.put`.
  if (strategy === 'network-only') return

  event.respondWith(
    strategy === 'network-first' ? networkFirst(request, url) : cacheFirst(request, url),
  )
})
