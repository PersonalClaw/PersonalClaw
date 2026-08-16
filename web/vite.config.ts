import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath } from 'node:url'
import { dirname } from 'node:path'

const BACKEND = `http://127.0.0.1:${process.env.PERSONALCLAW_PORT || 10000}`
const WEB_DIR = dirname(fileURLToPath(import.meta.url))

// After Vite writes dist/, generate dist/ui-docs.json — the documentation-as-data
// artifact for the ui/ kit that the gateway serves and UiDocsToolProvider reads
// (Platform-Legibility §5). It fuses the hand-authored <Name>.doc.ts objects with
// prop types derived from the TypeScript source; see scripts/buildUiDocs.mjs.
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
// the worker registers at scope '/' and can control the SPA (MOBILE-COMPANION
// T3.1). A normal Vite entry would land in dist/assets/ under a hashed name and
// be scoped to /assets/. Runs in closeBundle so dist/assets already exists: the
// cache version is a hash of those filenames. See scripts/buildServiceWorker.mjs.
function serviceWorkerPlugin(): Plugin {
  return {
    name: 'service-worker',
    apply: 'build',
    async closeBundle() {
      const { buildServiceWorker } = await import('./scripts/buildServiceWorker.mjs')
      const { version, path, assetCount } = await buildServiceWorker(WEB_DIR)
      this.info?.(`sw.js: cache personalclaw-shell-${version} (${assetCount} assets) → ${path}`)
    },
  }
}

// Replicate PersonalClaw's dev token handshake: when the browser hits the dev
// server with /?token=xxx, forward to the backend, relay its Set-Cookie
// (pc_token_<port>) onto our origin, then redirect to clean /. After that the
// cookie rides on all same-origin proxied /api + /api/ws calls.
function tokenProxyPlugin(): Plugin {
  return {
    name: 'token-proxy',
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        const url = new URL(req.url || '/', 'http://localhost')
        const token = url.searchParams.get('token')
        if (!token) return next()
        try {
          const r = await fetch(`${BACKEND}/?token=${encodeURIComponent(token)}`, { redirect: 'manual' })
          const setCookie = r.headers.get('set-cookie')
          if (setCookie) res.setHeader('set-cookie', setCookie)
        } catch { /* backend down — fall through */ }
        res.statusCode = 302
        res.setHeader('location', '/')
        res.end()
      })
    },
  }
}

// PersonalClaw web app.
// Proxies API/WS to the existing backend so we reuse PersonalClaw's data layer.
export default defineConfig({
  plugins: [react(), tailwindcss(), tokenProxyPlugin(), uiDocsPlugin(), serviceWorkerPlugin()],
  server: {
    port: 3100,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true, ws: true },
    },
  },
  build: { outDir: 'dist' },
})
