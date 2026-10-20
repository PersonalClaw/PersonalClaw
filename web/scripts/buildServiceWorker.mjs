// Builds web/dist/sw.js from web/src/sw.ts.
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
import { join, resolve } from 'node:path'

/**
 * @param {string} webDir absolute path to the web/ package root
 * @returns {Promise<{ version: string, path: string, assetCount: number, inputs: string[] }>}
 *   `inputs` are the absolute paths of the source files with code in sw.js, from esbuild's
 *   metafile, for the third-party notices (scripts/thirdPartyNotices.mjs).
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
  const { metafile } = await esbuild.build({
    entryPoints: [join(webDir, 'src', 'sw.ts')],
    outfile,
    bundle: true,
    format: 'iife',
    target: 'es2022',
    minify: true,
    // The worker's only compile-time input. Declared in src/sw.ts.
    define: { __SW_CACHE_VERSION__: JSON.stringify(version) },
    // Which files have code in sw.js. Metafile paths are relative to absWorkingDir.
    metafile: true,
    absWorkingDir: webDir,
  })
  const output = Object.values(metafile.outputs).find((o) => o.entryPoint)
  const inputs = Object.entries(output?.inputs ?? {})
    .filter(([, input]) => input.bytesInOutput > 0)
    .map(([path]) => resolve(webDir, path))

  return { version, path: outfile, assetCount: assets.length, inputs }
}
