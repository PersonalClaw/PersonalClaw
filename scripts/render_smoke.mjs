#!/usr/bin/env node
// Render smoke — prove the BUILT SPA actually mounts in a real browser.
//
// This closes the verification hole behind the v0.1.0 blank-dashboard release:
// a dual-React dependency skew crashed the bundle at first render, yet tsc,
// vitest (jsdom), and `vite build` all stayed green — nothing in the gate ever
// loaded the built artifact in a browser. This script does exactly that:
//
//   1. serve web/dist over a throwaway static server (hash routing needs no
//      history fallback; /api/* answers 503 so the shell renders its empty
//      states instead of hanging);
//   2. load a set of key routes in headless Chromium — each one exercises a
//      different lazy chunk;
//   3. per route, assert: #root mounted non-trivial content, no uncaught page
//      error fired, and the per-page ErrorBoundary fallback is not showing
//      (a boundary catch never surfaces as an uncaught error, so it needs its
//      own check).
//
// Run it against the freshly built web/dist (`npm run build` first):
//     npm run smoke:render
// Or point it at a live gateway instead of the static server:
//     PC_SMOKE_URL=http://127.0.0.1:10000 npm run smoke:render
//
// Wired into the pre-push gate (scripts/run_prepush.sh) and the ci.yml `web`
// job. Requires the Playwright Chromium binary (npx playwright install chromium
// — cached after the first run).

import http from 'node:http'
import { readFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const DIST = path.join(REPO_ROOT, 'web', 'dist')

// One route per major lazy chunk; '#/definitely-unknown' exercises the
// unknown-route → dashboard fallback.
const ROUTES = ['#/dashboard', '#/chat', '#/projects', '#/settings', '#/apps']

// The shell (nav rail + page chrome) is far larger than this; an empty mount
// or a bare error card is far smaller. Generous on purpose — this is a
// did-it-render-at-all check, not a layout assertion.
const MIN_ROOT_HTML_CHARS = 1000

const MIME = {
  '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
  '.svg': 'image/svg+xml', '.json': 'application/json', '.png': 'image/png',
  '.woff2': 'font/woff2', '.woff': 'font/woff', '.map': 'application/json',
  '.txt': 'text/plain', '.ico': 'image/x-icon', '.wasm': 'application/wasm',
}

function log(msg) { console.log(`[render-smoke] ${msg}`) }
function fail(msg) { console.error(`[render-smoke] FAIL: ${msg}`); process.exitCode = 1 }

/** Static server over web/dist. /api/* → 503 JSON (no gateway in the smoke),
 *  EXCEPT /api/dashboard/config, which must answer with a user_name: identity
 *  is server-held, and without it the app routes every hash to the Onboarding
 *  screen — the smoke would render onboarding five times and never mount the
 *  actual page chunks. Unknown paths fall back to index.html (harmless for a
 *  hash-routed SPA). */
function serveDist() {
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost')
    if (url.pathname === '/api/dashboard/config') {
      res.writeHead(200, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ user_name: 'Render Smoke' }))
      return
    }
    if (url.pathname.startsWith('/api/')) {
      res.writeHead(503, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ error: 'render-smoke: no gateway' }))
      return
    }
    const rel = url.pathname === '/' ? 'index.html' : url.pathname.slice(1)
    const file = path.join(DIST, path.normalize(rel))
    const target = file.startsWith(DIST) && existsSync(file) ? file : path.join(DIST, 'index.html')
    try {
      const body = await readFile(target)
      res.writeHead(200, { 'Content-Type': MIME[path.extname(target)] ?? 'application/octet-stream' })
      res.end(body)
    } catch (err) {
      res.writeHead(500)
      res.end(String(err))
    }
  })
  // The SPA opens /api/ws — refuse the upgrade so it fails fast and the app
  // settles into its disconnected state instead of waiting.
  server.on('upgrade', (_req, socket) => socket.destroy())
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => resolve(server))
  })
}

async function main() {
  let base = process.env.PC_SMOKE_URL
  let server = null
  if (base) {
    log(`probing live server at ${base}`)
  } else {
    if (!existsSync(path.join(DIST, 'index.html'))) {
      fail('web/dist/index.html missing — run `npm run build` first')
      return
    }
    server = await serveDist()
    base = `http://127.0.0.1:${server.address().port}`
    log(`serving web/dist at ${base}`)
  }

  const browser = await chromium.launch()
  const failures = []
  try {
    for (const route of ROUTES) {
      const page = await browser.newPage()
      const pageErrors = []
      const consoleErrors = []
      page.on('pageerror', (err) => pageErrors.push(err))
      page.on('console', (msg) => { if (msg.type() === 'error') consoleErrors.push(msg.text()) })

      const problems = []
      try {
        await page.goto(`${base}/${route}`, { waitUntil: 'load', timeout: 30_000 })
        // Wait for React to mount SOMETHING under #root, then a short settle
        // so effects/lazy chunks flush. networkidle is unusable here — the app
        // retries its websocket forever against the static server. If an
        // uncaught error already fired during load, the mount is not coming —
        // shorten the wait instead of burning the full timeout per route.
        await page.waitForSelector('#root > *', { timeout: pageErrors.length > 0 ? 3_000 : 15_000 })
        await page.waitForTimeout(750)

        const rootHtml = await page.evaluate(() => document.getElementById('root')?.innerHTML ?? '')
        if (rootHtml.length < MIN_ROOT_HTML_CHARS) {
          problems.push(`#root rendered only ${rootHtml.length} chars (want ≥ ${MIN_ROOT_HTML_CHARS})`)
        }
        // A render crash caught by the per-page ErrorBoundary never becomes an
        // uncaught error — detect its fallback card directly.
        const boundaryText = await page.getByText('This page hit an error').count()
        if (boundaryText > 0) problems.push('ErrorBoundary fallback is showing (page render crashed)')
      } catch (err) {
        problems.push(`navigation/mount failed: ${err.message.split('\n')[0]}`)
      }

      if (pageErrors.length > 0) {
        problems.push(...pageErrors.map((e) => `uncaught page error: ${e.message.split('\n')[0]}`))
      }

      if (problems.length > 0) {
        const shot = path.join(os.tmpdir(), `render-smoke-${route.replace(/[^a-z]/gi, '')}.png`)
        try { await page.screenshot({ path: shot, fullPage: true }); problems.push(`screenshot: ${shot}`) } catch { /* best effort */ }
        if (consoleErrors.length > 0) problems.push(`console errors: ${consoleErrors.slice(0, 5).join(' | ')}`)
        failures.push({ route, problems })
        log(`✗ ${route}`)
      } else {
        log(`✓ ${route} mounted clean${consoleErrors.length ? ` (${consoleErrors.length} console error(s) tolerated — no gateway behind the smoke)` : ''}`)
      }
      await page.close()
    }
  } finally {
    await browser.close()
    if (server) server.close()
  }

  if (failures.length > 0) {
    for (const f of failures) {
      console.error(`[render-smoke] ${f.route}:`)
      for (const p of f.problems) console.error(`  - ${p}`)
    }
    fail(`${failures.length}/${ROUTES.length} route(s) failed to render`)
    return
  }
  log(`PASS: all ${ROUTES.length} routes mounted in Chromium with no uncaught errors`)
}

main().catch((err) => {
  fail(err.stack ?? String(err))
})
