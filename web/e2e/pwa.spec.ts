import { test, expect } from '@playwright/test'
import { createServer, type Server } from 'node:http'
import { createReadStream, existsSync, statSync } from 'node:fs'
import { extname, join, normalize } from 'node:path'
import { fileURLToPath } from 'node:url'
import { dirname } from 'node:path'

// ── PWA behaviour: /api is NEVER served from cache (MOBILE-COMPANION T3.1) ────
//
// This is the PROOF for the atom's security clause. `src/app/swPolicy.test.ts`
// asserts the policy's return values and sw.ts's call sites; neither can show what
// a browser actually does. A service worker that caches an authenticated API
// response is a data leak — a later visitor, or the same browser after logout,
// gets the previous session's approvals off disk — so the rule has to be
// demonstrated, not configured.
//
// The method: install the real worker against the real `dist/` build, then take
// the origin genuinely offline by SHUTTING THE SERVER DOWN (no network emulation,
// nothing to get subtly wrong) and observe what still resolves.
//
// The load-bearing pairing is the last two assertions together:
//
//   * offline, a NAVIGATION still renders the app shell → the worker is installed,
//     controlling this page, and actively serving from Cache Storage. Without this
//     the next assertion would be vacuous: `/api` "failing" proves nothing if the
//     worker was never running.
//   * offline, `fetch('/api/ping')` FAILS → and it fails while, in the very same
//     instant, the same worker is happily serving the shell from disk. That
//     contrast is the proof.
//
// Runs against its own server on a private port, so it is independent of
// playwright.config.ts's `webServer` and of any gateway on :10000.

const DIST = join(dirname(fileURLToPath(import.meta.url)), '..', 'dist')
const PORT = Number(process.env.PW_PWA_PORT || 10944)
const ORIGIN = `http://127.0.0.1:${PORT}`

const MIME: Record<string, string> = {
  '.html': 'text/html',
  '.js': 'text/javascript',
  '.css': 'text/css',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.woff2': 'font/woff2',
  '.json': 'application/json',
  '.webmanifest': 'application/manifest+json',
}

/** A static server for `dist/` plus one live API route.
 *
 *  `/api/ping` answers with a COUNTER, which makes a cache hit unmistakable: a
 *  worker serving the API from cache would replay a number it has already served.
 */
function serveDist(): { server: Server; ready: Promise<void> } {
  let calls = 0
  const server = createServer((req, res) => {
    const url = new URL(req.url || '/', ORIGIN)
    if (url.pathname === '/api/ping') {
      calls += 1
      res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
      res.end(JSON.stringify({ call: calls, secret: `payload-${calls}` }))
      return
    }
    // Mirrors the gateway: sw.js and the manifest sit at the dist ROOT (a worker's
    // scope is its path), and unknown paths fall back to the SPA document.
    const rel = url.pathname === '/' ? 'index.html' : normalize(url.pathname).replace(/^[/\\]+/, '')
    let file = join(DIST, rel)
    if (!existsSync(file) || !statSync(file).isFile()) file = join(DIST, 'index.html')
    res.writeHead(200, {
      'Content-Type': MIME[extname(file)] || 'application/octet-stream',
      'Cache-Control': 'no-store',
    })
    createReadStream(file).pipe(res)
  })
  const ready = new Promise<void>((resolve) => server.listen(PORT, '127.0.0.1', resolve))
  return { server, ready }
}

/** Close the listener AND drop live keep-alive sockets, so the origin is really
 *  gone rather than merely refusing new connections. */
async function shutdown(server: Server): Promise<void> {
  server.closeAllConnections()
  await new Promise<void>((resolve) => server.close(() => resolve()))
}

test.describe('service worker', () => {
  // Serial: both tests bind the same port, and playwright.config sets
  // fullyParallel, which would otherwise start them in two workers at once.
  test.describe.configure({ mode: 'serial' })

  test.skip(!existsSync(join(DIST, 'sw.js')), 'run `npm run build` first — dist/sw.js is required')

  test('serves the shell offline but NEVER serves /api from cache', async ({ page }) => {
    const { server, ready } = serveDist()
    await ready
    try {
      // ── 1. Install, then take control. There is no clients.claim() by design,
      //       so the worker controls the page from the SECOND visit onward.
      //       NOTE: it must be a reload, not a second goto to the same hash URL —
      //       a URL differing only in its fragment is a SAME-DOCUMENT navigation,
      //       so no new client is created and nothing ever becomes controlled.
      await page.goto(`${ORIGIN}/#/companion`)
      await page.evaluate(() => navigator.serviceWorker.ready)
      await page.reload()
      const controlled = await page.evaluate(() => navigator.serviceWorker.controller !== null)
      expect(controlled, 'the worker must control the page or nothing below is meaningful').toBe(
        true,
      )

      // ── 2. Online, back-to-back API reads must hit the network every time.
      //       A cache-first bug would replay call 1 instead of counting up.
      const first = await page.evaluate(() => fetch('/api/ping').then((r) => r.json()))
      const second = await page.evaluate(() => fetch('/api/ping').then((r) => r.json()))
      expect(first.call).toBe(1)
      expect(second.call).toBe(2)

      // ── 3. Inspect the real Cache Storage the real worker wrote. The shell must
      //       be retrievable; nothing from /api may be present or matchable.
      //
      //       Asserted by LOOKUP, not by string-matching `keys()`. Chromium reports
      //       the stored key WITH its fragment — after a reload the shell slot is
      //       keyed `/#/onboarding`, so `keys()` contains no bare `/` — while the
      //       matching algorithm compares URLs with fragments excluded, which is
      //       why `match('/')` still resolves. A key-string assertion here reads as
      //       a broken precache when the precache is fine.
      const cache = await page.evaluate(async () => {
        const names = await caches.keys()
        const store = await caches.open(names[0])
        const urls = (await store.keys()).map((r) => r.url)
        return {
          names,
          total: urls.length,
          apiKeys: urls.filter((u) => new URL(u).pathname.startsWith('/api')),
          shellMatches: (await store.match('/')) !== undefined,
          apiMatches: (await store.match('/api/ping')) !== undefined,
        }
      })
      expect(cache.names).toEqual([expect.stringContaining('personalclaw-shell-')])
      expect(cache.total, 'the worker precached nothing — check install()').toBeGreaterThan(0)
      expect(cache.shellMatches, 'the shell document is not retrievable from cache').toBe(true)
      // The clause, from the cache's own side: the API response the page just read
      // twice is neither stored under an /api key nor findable by lookup.
      expect(cache.apiKeys).toEqual([])
      expect(cache.apiMatches).toBe(false)

      // ── 4. Go genuinely offline: the origin stops existing.
      await shutdown(server)
      await expect(async () => {
        await page.request.get(`${ORIGIN}/api/ping`)
      }).rejects.toBeTruthy()

      // ── 5. The vacuity floor. Offline, a navigation STILL renders the shell —
      //       proof that the worker is alive and serving from Cache Storage right
      //       now, which is what makes assertion 6 meaningful. A reload, again:
      //       re-navigating to the same hash URL would not fetch anything at all
      //       and this floor would pass without testing a thing.
      await page.reload()
      expect(await page.evaluate(() => document.querySelector('#root') !== null)).toBe(true)
      expect(await page.evaluate(() => navigator.serviceWorker.controller !== null)).toBe(true)

      // ── 6. THE CLAUSE. Same worker, same instant, offline: the API must fail
      //       rather than hand back the payload it saw in step 2.
      const offline = await page.evaluate(() =>
        fetch('/api/ping').then(
          (r) => r.text().then((body) => ({ ok: true as const, status: r.status, body })),
          (err) => ({ ok: false as const, error: String(err) }),
        ),
      )
      expect(offline.ok, `offline /api resolved instead of failing: ${JSON.stringify(offline)}`).toBe(
        false,
      )
      if (!offline.ok) expect(offline.error).toMatch(/Failed to fetch|NetworkError|network error/i)
      expect(JSON.stringify(offline)).not.toContain('payload-')
    } finally {
      await shutdown(server).catch(() => {
        /* already closed in step 4 */
      })
    }
  })

  test('the manifest is installable and the worker is scoped to the origin root', async ({
    page,
    context,
  }) => {
    const { server, ready } = serveDist()
    await ready
    try {
      await page.goto(`${ORIGIN}/`)
      const registration = await page.evaluate(async () => {
        const reg = await navigator.serviceWorker.ready
        return { scope: reg.scope, hasActive: reg.active !== null }
      })
      // Root scope: a worker under /assets/ could not control the SPA at /.
      expect(registration.scope).toBe(`${ORIGIN}/`)
      expect(registration.hasActive).toBe(true)

      // The document really links a parseable manifest, served as a manifest.
      const href = await page.getAttribute('link[rel=manifest]', 'href')
      expect(href).toBe('/manifest.webmanifest')
      const res = await page.request.get(`${ORIGIN}/manifest.webmanifest`)
      expect(res.headers()['content-type']).toContain('application/manifest+json')
      const manifest = (await res.json()) as { start_url: string; display: string; icons: unknown[] }
      expect(manifest.start_url).toBe('/#/companion')
      expect(manifest.display).toBe('standalone')
      expect(manifest.icons.length).toBeGreaterThan(0)

      // ── The manifest as CHROME sees it, not as our disk holds it ─────────────
      //
      // The atom's done-when names "Lighthouse installability". That audit no
      // longer exists: Lighthouse 12 REMOVED the PWA category and every audit in
      // it (`installable-manifest`, `service-worker`, `maskable-icon`, …) — a
      // 12.8.2 run against this build reports only performance / accessibility /
      // best-practices / seo. So the criteria are taken from the browser directly.
      //
      // `Page.getInstallabilityErrors` looks like the ideal replacement — it is the
      // verdict DevTools > Application shows — and it is deliberately NOT used
      // here: it was measured and found INERT. It returns `[]` for an unparseable
      // manifest and `[]` for `display: "browser"`, so an assertion on it can never
      // fail. `Page.getAppManifest` was measured the same way and DOES have teeth
      // (an invalid manifest yields `Line: 1, column: 3, Syntax error.`), so the
      // rail is built on that instead.
      //
      // Asserting Chrome's own `data` rather than re-reading the file proves the
      // browser actually FETCHED the manifest — a 403 (this origin is
      // session-gated) or an HTML body from the SPA fallback would fail here.
      const cdp = await context.newCDPSession(page)
      const appManifest = await cdp.send('Page.getAppManifest')
      expect(appManifest.errors, 'Chrome reported manifest parse errors').toEqual([])
      expect(appManifest.url).toContain('/manifest.webmanifest')
      const asChromeSawIt = JSON.parse(appManifest.data ?? '{}') as {
        display: string
        start_url: string
        icons: unknown[]
      }
      expect(asChromeSawIt.display).toBe('standalone')
      expect(asChromeSawIt.start_url).toBe('/#/companion')
      expect(asChromeSawIt.icons.length).toBeGreaterThan(0)
    } finally {
      await shutdown(server)
    }
  })
})
