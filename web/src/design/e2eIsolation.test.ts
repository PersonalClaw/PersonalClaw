import { afterEach, describe, it, expect } from 'vitest'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  PROJECTS_FOR,
  SPEC_ISOLATION,
  STATEFUL_SPECS,
  isolationViolation,
  seededOrder,
  unclassified,
} from '../../e2e/isolation'

// ── The browser run's isolation modes are complete, and enforced ──────────────────────────────
//
// `e2e/isolation.ts` says which specs may share the harness's one gateway in parallel and which
// run serially, and `e2e/isolationReporter.ts` fails a run in which a spec runs where its mode
// forbids. These hold the table to the tree, and plant the violation the reporter exists for: the
// real config with the shared project no longer leaving the stateful set out.

const WEB = process.cwd()
const E2E = join(WEB, 'e2e')

describe('every spec has an isolation mode', () => {
  it('the table classifies every spec in e2e/, and names none that is gone', () => {
    const specs = readdirSync(E2E).filter((f) => f.endsWith('.spec.ts'))
    expect(specs.length, 'no specs found: this check would pass vacuously').toBeGreaterThan(10)
    expect(unclassified(specs)).toEqual([])
    expect(Object.keys(SPEC_ISOLATION).filter((s) => !specs.includes(s))).toEqual([])
  })

  it('every entry says why', () => {
    for (const [spec, { why }] of Object.entries(SPEC_ISOLATION)) expect(why.length, spec).toBeGreaterThan(20)
  })

  it('the stateful set is the specs the audit found writing what other specs read', () => {
    expect([...STATEFUL_SPECS].sort()).toEqual(['deployedArtifacts.spec.ts', 'staleWrite.spec.ts'])
    // onboardingGeometry renders first run without writing to the gateway, so it shares.
    expect(SPEC_ISOLATION['onboardingGeometry.spec.ts'].mode).toBe('shared')
  })

  it('a stateful spec in a shared project is a violation, and in the serial ones is not', () => {
    expect(isolationViolation('chromium', 'staleWrite.spec.ts')).toMatch(/is serial/)
    for (const project of PROJECTS_FOR.serial) expect(isolationViolation(project, 'staleWrite.spec.ts')).toBe('')
    expect(isolationViolation('stateful', 'a11y.spec.ts')).toMatch(/is shared/)
    expect(isolationViolation('chromium', 'not-in-the-table.spec.ts')).toMatch(/no isolation mode/)
  })
})

describe('the second pass is drawn from a seed', () => {
  it('one seed gives one order, and the seeds between them give both orders', () => {
    const items = ['a', 'b']
    expect(seededOrder(items, 7)).toEqual(seededOrder(items, 7))
    const orders = new Set(Array.from({ length: 32 }, (_, seed) => seededOrder(items, seed).join()))
    expect(orders).toEqual(new Set(['a,b', 'b,a']))
    expect(seededOrder(items, 3)).toHaveLength(2)
  })
})

/** `playwright test --list` over `config`, with every server off: the reporter's verdict on the
 *  projects alone. */
function list(config: string): ReturnType<typeof spawnSync> {
  return spawnSync('npx', ['playwright', 'test', '--list', '-c', config], {
    cwd: WEB,
    encoding: 'utf8',
    env: { ...process.env, PW_NO_SERVER: '1', PW_STATEFUL_SEED: '7', PW_STATEFUL_CHAIN: '1' },
    timeout: 120_000,
  })
}

describe('the isolation reporter fails a run that shares a stateful spec', () => {
  let dir = ''
  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true })
  })

  it('passes the real projects, and fails them once the shared project takes the stateful set', () => {
    // A scratch folder of the test's own under the OS temp directory, never the repository. Its
    // package.json makes the planted configs an ES-module package, as web/ is, so they resolve
    // the config they import the way web/'s own files do.
    dir = mkdtempSync(join(tmpdir(), 'pw-isolation-'))
    writeFileSync(join(dir, 'package.json'), '{ "type": "module" }\n')
    const base = join(WEB, 'playwright.config.ts')
    const reporter = join(E2E, 'isolationReporter.ts')
    const write = (name: string, chromium: string) => {
      const path = join(dir, name)
      writeFileSync(
        path,
        `import base from ${JSON.stringify(base)}\n` +
          `export default { ...base, testDir: ${JSON.stringify(E2E)},\n` +
          `  reporter: [[${JSON.stringify(reporter)}]], webServer: undefined,\n` +
          `  projects: base.projects.map((p) => (p.name === 'chromium' ? ${chromium} : p)) }\n`,
      )
      return path
    }
    const clean = list(write('clean.config.ts', 'p'))
    expect(clean.status, `${clean.stdout}\n${clean.stderr}`).toBe(0)
    // The plant: the shared project leaves out only the smoke, so it lists the stateful set too.
    const planted = list(write('planted.config.ts', "{ ...p, testIgnore: [/smoke\\.spec\\.ts$/] }"))
    expect(planted.status, 'a stateful spec in the shared parallel project went unseen').not.toBe(0)
    expect(String(planted.stderr)).toMatch(/staleWrite\.spec\.ts is serial .* ran in the chromium project/)
  }, 180_000)
})
