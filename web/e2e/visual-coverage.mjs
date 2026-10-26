// Every surface the visual spec snapshots has a golden, on each platform this repo commits
// goldens for (#3943).
//
//     node e2e/visual-coverage.mjs
//
// web/src/design/visualBaselineCoverage.test.ts holds the committed set to it in the web job, and
// reads the manifest through this module, so the two can never disagree. The Refresh visual
// baselines workflow runs it after its visual update, where a test runner may not run: a surface
// the refresh drew no golden for is a red there, not a gap the next pull request finds. It reads
// web/e2e/routes.ts directly, which Node runs with its types stripped.
import { readdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { ORB_STATE_SHOTS, REDUCED_MOTION_SHOTS, ROUTES, THEMES, VIEW_ROUTES, axisShots } from './routes.ts'

/** The visual spec's goldens. */
export const GOLDENS = path.join(path.dirname(fileURLToPath(import.meta.url)), '__screenshots__', 'visual.spec.ts')

/** The `{platform}` suffixes this repo commits goldens for, as a CLOSED set.
 *
 *  Linux only, and only as the pinned image's browser renders it (#3943): every golden is made by
 *  the Refresh visual baselines workflow against `e2e/visual-image.json`'s image, which is
 *  linux/amd64, so a golden with another suffix was made somewhere else. `web/.gitignore` keeps a
 *  local run's own set out of `git add`, and `tests/rails/test_pinned_playwright_image_tree.py`
 *  fails one committed anyway, for the trees and the findings too. Adding a platform here is
 *  adding a second environment, which #3943 retired. */
export const CAPTURED_PLATFORMS = ['linux']

/** `<id|route>-<theme>` for every route, and each of the agent orb's state shots, each
 *  reduced-motion shot and each axis shot by its key — the `arg` half of playwright's
 *  `snapshotPathTemplate`. */
export function expectedKeys() {
  const keys = []
  for (const theme of THEMES) {
    for (const { route, id } of [...ROUTES, ...VIEW_ROUTES]) {
      keys.push(`${id ?? route}-${theme}`)
    }
  }
  for (const { key } of ORB_STATE_SHOTS) keys.push(key)
  for (const { key } of REDUCED_MOTION_SHOTS) keys.push(key)
  for (const { key } of axisShots()) keys.push(key)
  return keys
}

/** Goldens grouped by the `{platform}` suffix playwright appends. */
export function byPlatform(dir = GOLDENS) {
  const out = new Map()
  for (const name of readdirSync(dir)) {
    const m = /^(.+)-([a-z0-9]+)\.png$/.exec(name)
    if (!m) continue
    const [, key, platform] = m
    if (!out.has(platform)) out.set(platform, new Set())
    out.get(platform).add(key)
  }
  return out
}

/** Each declared platform's missing goldens, one line per platform that misses any. */
export function missingGoldens(dir = GOLDENS) {
  const want = expectedKeys()
  const platforms = byPlatform(dir)
  return CAPTURED_PLATFORMS.map((p) => ({ p, missing: want.filter((k) => !(platforms.get(p) ?? new Set()).has(k)) }))
    .filter((r) => r.missing.length > 0)
    .map((r) => `${r.p}: ${r.missing.length}/${want.length} missing → ${r.missing.join(', ')}`)
}

export function main(options = {}) {
  const dir = options.dir ?? GOLDENS
  const log = options.log ?? ((line) => console.log(line))
  let problems
  try {
    problems = missingGoldens(dir)
  } catch (error) {
    log(`::error title=visual goldens::the goldens could not be read at ${dir}: ${error.message}`)
    return 1
  }
  if (problems.length > 0) {
    log(`::error title=visual goldens::a surface the visual spec snapshots has no golden`)
    for (const line of problems) log(line)
    return 1
  }
  log(`✓ a golden for each of the ${expectedKeys().length} surfaces the visual spec snapshots, on ${CAPTURED_PLATFORMS.join(', ')}`)
  return 0
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = main()
}
