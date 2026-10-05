import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath } from 'node:url'
import { dirname } from 'node:path'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { thirdPartyNotices } from './scripts/thirdPartyNotices.mjs'

const BACKEND = `http://127.0.0.1:${process.env.PERSONALCLAW_PORT || 10000}`
const WEB_DIR = dirname(fileURLToPath(import.meta.url))

// The licence notices of every npm package whose code the build emits: dist/
// THIRD_PARTY_NOTICES_NPM.txt and .json, read from the bundler's own module graph, so the app
// build, each web worker's build and sw.js all report into this one collector. The minifier
// strips every licence comment from the code, so these files are where the notices ship. See
// scripts/thirdPartyNotices.mjs.
const notices = thirdPartyNotices(WEB_DIR)

// After Vite writes dist/, generate dist/ui-docs.json — the documentation-as-data
// artifact for the ui/ kit that the gateway serves and UiDocsToolProvider reads.
// It fuses the hand-authored <Name>.doc.ts objects with prop types derived from
// the TypeScript source; see scripts/buildUiDocs.mjs.
function uiDocsPlugin(): Plugin {
  return {
    name: 'ui-docs',
    apply: 'build',
    async closeBundle() {
      const { buildUiDocs } = await import('./scripts/buildUiDocs.mjs')
      const { componentCount, path } = await buildUiDocs(WEB_DIR)
      this.info?.(`ui-docs.json: ${componentCount} components → ${path}`)
    },
  }
}

// After Vite writes dist/, bundle src/sw.ts to dist/sw.js — at the dist ROOT, so
// the worker registers at scope '/' and can control the SPA. A normal Vite entry
// would land in dist/assets/ under a hashed name and be scoped to /assets/. Runs
// in closeBundle so dist/assets already exists: the cache version is a hash of
// those filenames. See scripts/buildServiceWorker.mjs.
function serviceWorkerPlugin(): Plugin {
  return {
    name: 'service-worker',
    apply: 'build',
    async closeBundle() {
      const { buildServiceWorker } = await import('./scripts/buildServiceWorker.mjs')
      const { version, path, assetCount, inputs } = await buildServiceWorker(WEB_DIR)
      notices.recordOutput('sw.js', inputs)
      this.info?.(`sw.js: cache personalclaw-shell-${version} (${assetCount} assets) → ${path}`)
    },
  }
}

// Replicate PersonalClaw's dev token handshake: when the browser hits the dev
// server with /?token=xxx, forward to the backend, relay its Set-Cookie
// (pc_token_<port>) onto our origin, then redirect to clean /. After that the
// cookie rides on all same-origin proxied /api + /api/ws calls.
function tokenProxyPlugin(): Plugin {
  const handshake = async (req: IncomingMessage, res: ServerResponse, next: () => void) => {
    const url = new URL(req.url || '/', 'http://localhost')
    const token = url.searchParams.get('token')
    if (!token) return next()
    try {
      const r = await fetch(`${BACKEND}/?token=${encodeURIComponent(token)}`, { redirect: 'manual' })
      // getSetCookie(), NOT get(): the gateway sends TWO Set-Cookie headers (the
      // session cookie, then a `pc_token=""; Max-Age=0` that clears the legacy
      // non-port-specific one). `get()` joins them with ", " into ONE header, so a
      // browser reads a single cookie whose LAST Max-Age attribute is 0 — the
      // session was set and expired in the same response, and every route rendered
      // onboarding. curl showed a healthy Set-Cookie the whole time.
      const cookies = r.headers.getSetCookie()
      if (cookies.length) res.setHeader('set-cookie', cookies)
    } catch { /* backend down — fall through */ }
    res.statusCode = 302
    res.setHeader('location', '/')
    res.end()
  }
  return {
    name: 'token-proxy',
    configureServer(server) {
      server.middlewares.use(handshake)
    },
    // The PREVIEW server needs it too, and `configureServer` does NOT cover it.
    // The e2e harness serves the BUILT app through `vite preview`, so without
    // this the /?token= handshake e2e/auth.setup.ts performs silently returned
    // index.html with no cookie — every route rendered the onboarding screen and
    // axe scanned a surface no user ever sees.
    configurePreviewServer(server) {
      server.middlewares.use(handshake)
    },
  }
}

// PersonalClaw web app.
// Proxies API/WS to the existing backend so we reuse PersonalClaw's data layer.
export default defineConfig({
  // `notices.plugin()` writes its files in a `closeBundle` ordered after every other plugin's,
  // because sw.js is built in one and has to be in the census.
  plugins: [react(), tailwindcss(), tokenProxyPlugin(), uiDocsPlugin(), serviceWorkerPlugin(), notices.plugin()],
  // Monaco's editor, json, css, html and ts workers are each a separate Rolldown build.
  worker: { plugins: () => [notices.workerPlugin()] },
  server: {
    port: 3100,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true, ws: true },
      // 🔴 App-CONTRIBUTED UI bundles, and without this they cannot load in ANY vite session.
      // `AppHostPage` builds a ROOT-RELATIVE `/apps/<name>/ui/<entry>` — correct in production,
      // where the gateway serves the SPA and the bundle from one origin. Under vite the SPA has
      // its own origin, so an unproxied `/apps/...` hits the SPA fallback and the app page fails
      // with `Unexpected token '<'`: the dynamic import received index.html. That breaks the app
      // page for the three first-party apps that contribute UI, in `npm run dev` and in the e2e
      // harness's `vite preview` alike — measured, not inferred.
      // `preview` inherits `server.proxy` (vite defaults proxy/port/host from `server`), which is
      // why `/api` already works there and why one entry is enough for both.
      '/apps': { target: BACKEND, changeOrigin: true },
      // A deployed artifact's page, for the same reason: the Deploy bar's Preview and Open use
      // the ROOT-RELATIVE URL the gateway hands out, so unproxied it hit the SPA fallback and the
      // pane showed the dashboard instead of the page.
      '/artifacts/serve': { target: BACKEND, changeOrigin: true },
    },
  },
  build: { outDir: 'dist' },
})
