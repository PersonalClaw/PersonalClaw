import { describe, it, expect } from 'vitest'
import { spawnSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { UPDATE_SNAPSHOTS } from '../../e2e/run-pinned.mjs'

// ── The pinned visual leg stops at its second failure, and tells each red once (#3943) ─────────
//
// On run 40599132936 the pinned browser was starved: every light text-200 shot failed its settle,
// and each retry hung on its first call to the browser for its whole 300 s test timeout, until
// run-job stopped the leg at 39 minutes. A passing leg takes about 4. So under CI the config stops
// the leg (`run-pinned.mjs -- e2e/visual.spec.ts`) at its second failure, and the spec retries
// nothing and gives each action 30 s. Any other run keeps going to the end, a refresh too: it
// must not leave a golden after the second it fixes stale.

const WEB = join(__dirname, '..', '..')

/** The config's `maxFailures` for a `playwright test` of `files`, read from `--list`'s JSON. */
function maxFailures(files: string[], ci: boolean): number {
  const env: NodeJS.ProcessEnv = { ...process.env, PW_NO_SERVER: '1', PW_STATEFUL_SEED: '7' }
  for (const decided of ['CI', 'PW_VISUAL_COMPARE', 'PW_ALONE_HOME', 'PW_STATEFUL_CHAIN', 'PW_STATEFUL_ONLY', 'PLAYWRIGHT_JSON_OUTPUT_NAME']) {
    delete env[decided]
  }
  if (ci) env.CI = '1'
  const out = spawnSync('npx', ['playwright', 'test', '--list', '--reporter=json', ...files], { cwd: WEB, encoding: 'utf8', env, timeout: 120_000 })
  expect(out.status, `${out.stdout}\n${out.stderr}`).toBe(0)
  const report = JSON.parse(out.stdout.slice(out.stdout.indexOf('{'), out.stdout.lastIndexOf('}') + 1))
  return report.config.maxFailures
}

describe('the pinned visual leg stops early', () => {
  it('stops at its second failure under CI, and only there', () => {
    // The leg's compare, as run-pinned starts it.
    expect(maxFailures(['e2e/visual.spec.ts', '--update-snapshots=none'], true)).toBe(2)
    expect(maxFailures(['e2e/visual.spec.ts'], true)).toBe(2)
    expect(maxFailures(['e2e/visual.spec.ts'], false)).toBe(0)
    // A refresh, as run-pinned starts it, and the same by hand.
    expect(maxFailures(['e2e/visual.spec.ts', UPDATE_SNAPSHOTS], true)).toBe(0)
    expect(maxFailures(['e2e/visual.spec.ts', '-u'], true)).toBe(0)
    expect(maxFailures(['e2e/chat.spec.ts'], true)).toBe(0)
  }, 300_000)

  it('retries no shot, and gives each action 30 s, for the whole file', () => {
    const spec = readFileSync(join(WEB, 'e2e', 'visual.spec.ts'), 'utf8')
    // At the top level, so every test in the file takes them.
    expect(spec).toMatch(/^test\.describe\.configure\(\{ retries: 0 \}\)$/m)
    expect(spec).toMatch(/^test\.use\(\{ actionTimeout: 30_000 \}\)$/m)
  })
})
