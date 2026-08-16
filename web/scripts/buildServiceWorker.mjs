// Builds web/dist/sw.js from web/src/sw.ts (MOBILE-COMPANION T3.1).
//
// Why a separate esbuild pass instead of a Vite rollup entry: a service worker
// must be served from the ORIGIN ROOT to register at scope `/`. As a normal Vite
// entry it would land in `dist/assets/` under a hashed name — scope `/assets/` —
// and could not control the SPA at `/`. A dedicated bundle also keeps the worker
// out of the app's chunk graph, so it never shares a lazy chunk with a route.
//
// Runs from a Vite `closeBundle` hook (mirroring buildUiDocs.mjs) so `dist/assets`
// already exists and its filenames can be hashed into the cache version.
//
// Node/ESM build tool; NOT part of the shipped SPA bundle.
import esbuild from 'esbuild'
import { createHash } from 'node:crypto'
import { existsSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

/**
 * @param {string} webDir absolute path to the web/ package root
 * @returns {Promise<{ version: string, path: string, assetCount: number }>}
 */
export async function buildServiceWorker(webDir) {
  const distDir = join(webDir, 'dist')
  const assetsDir = join(distDir, 'assets')

  // Cache version = hash of the built asset filenames. Content-addressed inputs
  // make this deterministic (no timestamp → reproducible builds) while still
  // changing whenever the bundle does, which is exactly when the worker's
  // activate() should evict the previous cache's orphans.
  const assets = existsSync(assetsDir) ? readdirSync(assetsDir).sort() : []
  const version = createHash('sha256').update(assets.join('\n')).digest('hex').slice(0, 12)

  const outfile = join(distDir, 'sw.js')
  await esbuild.build({
    entryPoints: [join(webDir, 'src', 'sw.ts')],
    outfile,
    bundle: true,
    format: 'iife',
    target: 'es2022',
    minify: true,
    // The worker's only compile-time input. Declared in src/sw.ts.
    define: { __SW_CACHE_VERSION__: JSON.stringify(version) },
  })

  return { version, path: outfile, assetCount: assets.length }
}
