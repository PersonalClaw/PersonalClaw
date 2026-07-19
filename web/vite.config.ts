import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const BACKEND = `http://127.0.0.1:${process.env.PERSONALCLAW_PORT || 10000}`

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
  plugins: [react(), tailwindcss(), tokenProxyPlugin()],
  server: {
    port: 3100,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true, ws: true },
    },
  },
  build: { outDir: 'dist' },
})
