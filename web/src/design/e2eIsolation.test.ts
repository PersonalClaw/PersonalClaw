import { afterEach, describe, it, expect, vi } from 'vitest'
import { spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
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
import IsolationReporter from '../../e2e/isolationReporter'

// ── The browser run's isolation modes are complete, and enforced ──────────────────────────────
//
// `e2e/isolation.ts` says which specs may share the harness's one gateway in parallel and which
// run serially, and `e2e/isolationReporter.ts` fails a run in which a spec runs where its mode
// forbids. These hold the table to the tree, and plant the violation the reporter exists for: the
// real config with the shared project no longer leaving the stateful set out. And they run the real
// config's projects, so the stateful passes are seen to run one after another, and a pass that
// never ran is seen to red the run.

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

describe('a merged report keeps every test\'s isolation mode', () => {
  // A blob report carries a test's annotations at its end as its result's, and the merge makes
  // those the test's. So the reporter puts its own on a result that has annotations of its own.
  const ended = (file: string, project: string, own: Array<{ type: string; description?: string }>) => {
    const reporter = new IsolationReporter()
    reporter.onBegin({ metadata: { statefulSeed: '7', statefulOrder: [] } } as never, { allTests: () => [] } as never)
    const test = { location: { file: `/x/e2e/${file}` }, parent: { project: () => ({ name: project }) }, annotations: [...own] }
    const result = { annotations: [...own] }
    reporter.onTestEnd(test as never, result as never)
    return result.annotations.map((a) => a.type)
  }

  it('a result with annotations of its own gets the isolation mode, and a second pass its seed', () => {
    expect(ended('staleWrite.spec.ts', 'stateful', [{ type: 'serial' }])).toEqual(['serial', 'isolation'])
    expect(ended('staleWrite.spec.ts', 'stateful-again-1', [{ type: 'serial' }])).toEqual(['serial', 'isolation', 'stateful-seed'])
  })

  it('a result with none is left as it is: the test\'s own annotations reach the merge then', () => {
    expect(ended('a11y.spec.ts', 'chromium', [])).toEqual([])
  })
})

describe('a test that never ran fails the run, named', () => {
  const test = (project: string, file: string, title: string, results: unknown[]) => ({
    location: { file: `/x/e2e/${file}` }, parent: { project: () => ({ name: project }) }, annotations: [],
    titlePath: () => ['', project, file, title], results,
  })
  async function end(mode: string, tests: unknown[]) {
    const reporter = new IsolationReporter({ _mode: mode })
    reporter.onBegin({ metadata: { statefulSeed: '7', statefulOrder: [] } } as never, { allTests: () => tests } as never)
    const said: string[] = []
    const spy = vi.spyOn(console, 'error').mockImplementation((...a: unknown[]) => { said.push(a.join(' ')) })
    try {
      return { verdict: await reporter.onEnd(), said: said.join('\n') }
    } finally {
      spy.mockRestore()
    }
  }
  const ran = [{ status: 'passed' }]

  it('reds a run whose second pass was skipped, naming each test of it', async () => {
    const { verdict, said } = await end('test', [
      test('stateful', 'staleWrite.spec.ts', 'tab B is refused', [{ status: 'failed' }]),
      test('stateful-again-1', 'staleWrite.spec.ts', 'tab B is refused', []),
      test('stateful-again-2', 'deployedArtifacts.spec.ts', 'a deployed page loads', []),
    ])
    expect(verdict).toEqual({ status: 'failed' })
    expect(said).toMatch(/2 test\(s\) did not run/)
    expect(said).toMatch(/stateful-again-1 › staleWrite\.spec\.ts › tab B is refused/)
    expect(said).toMatch(/stateful-again-2 › deployedArtifacts\.spec\.ts › a deployed page loads/)
  })

  it('passes a test that skipped itself: it has a result', async () => {
    expect((await end('test', [test('chromium', 'a11y.spec.ts', 'Home', ran), test('chromium', 'a11y.spec.ts', 'dock', [{ status: 'skipped' }])])).verdict)
      .toBeUndefined()
  })

  it('passes a listing, which runs no test at all', async () => {
    expect((await end('list', [test('chromium', 'a11y.spec.ts', 'Home', [])])).verdict).toBeUndefined()
  })
})

describe('the real projects run the stateful passes one after another', () => {
  // The real config's projects over stand-in specs that take no browser, since the real ones need
  // the gateway. Each stand-in has the name of the spec it stands for, so the real projects pick it
  // up, and the run's report goes through `e2e/assert-one-run.mjs`, as CI's merged report does.
  const PW_TEST = createRequire(join(WEB, 'package.json')).resolve('@playwright/test')
  let dir = ''
  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true })
  })

  function harness(): string {
    dir = mkdtempSync(join(tmpdir(), 'pw-passes-'))
    writeFileSync(join(dir, 'package.json'), '{ "type": "module" }\n')
    const specs = join(dir, 'specs')
    mkdirSync(specs)
    // CommonJS, so a stand-in finds `@playwright/test` by its path from outside the repository.
    writeFileSync(join(specs, 'package.json'), '{ "type": "commonjs" }\n')
    const spec = (name: string, body: string) =>
      writeFileSync(join(specs, name), `import { test } from ${JSON.stringify(PW_TEST)}\n${body}\n`)
    const wait = (ms: number) => `await new Promise((r) => setTimeout(r, ${ms}))`
    spec('auth.setup.ts', "test('authenticate', () => {})")
    spec('smoke.spec.ts', "test('renders', () => {})")
    // A shared phase long enough that a pass let loose during it would start inside it.
    spec('a11y.spec.ts', Array.from({ length: 6 }, (_, i) => `test('route ${i}', async () => { ${wait(500)} })`).join('\n'))
    // The planted red: PW_PLANT_RED fails the stale-write spec's first pass.
    spec('staleWrite.spec.ts', `test('tab B is refused', async () => { ${wait(200)}\n` +
      "  if (process.env.PW_PLANT_RED && test.info().project.name === 'stateful') throw new Error('planted red first pass') })")
    spec('deployedArtifacts.spec.ts', `test('a deployed page loads', async () => { ${wait(200)} })`)
    const config = join(dir, 'passes.config.ts')
    writeFileSync(
      config,
      `import base from ${JSON.stringify(join(WEB, 'playwright.config.ts'))}\n` +
        `export default { ...base, testDir: ${JSON.stringify(specs)}, retries: 0, webServer: undefined,\n` +
        `  reporter: [[${JSON.stringify(join(E2E, 'isolationReporter.ts'))}], ['json', { outputFile: ${JSON.stringify(join(dir, 'report.json'))} }]] }\n`,
    )
    return config
  }

  /** One run of the stand-ins, and the run check's verdict on its report. `only` is CI's
   *  `e2e-stateful` job: the stateful specs named, so the passes chain by dependencies. */
  function run(only: boolean, plant = false) {
    const config = harness()
    const ran = spawnSync('npx', ['playwright', 'test', '-c', config, ...(only ? ['staleWrite.spec.ts', 'deployedArtifacts.spec.ts'] : [])], {
      cwd: WEB,
      encoding: 'utf8',
      // The run's output goes in the scratch folder too: a run that ran tests leaves its output
      // folder behind, and the default one is under web/test-results/.
      env: {
        ...process.env, PW_NO_SERVER: '1', PW_STATEFUL_SEED: '7', PW_STATEFUL_CHAIN: '1',
        PW_STATEFUL_ONLY: only ? '1' : '0', PW_OUTPUT_DIR: join(dir, 'out'), ...(plant ? { PW_PLANT_RED: '1' } : {}),
      },
      timeout: 120_000,
    })
    const judged = spawnSync(process.execPath, [join(E2E, 'assert-one-run.mjs'), join(dir, 'report.json')], { cwd: WEB, encoding: 'utf8' })
    return { ran, out: `${ran.stdout}\n${ran.stderr}`, judged, verdict: `${judged.stdout}\n${judged.stderr}` }
  }
  const IN_ORDER = /each pass after the one before it had ended/

  /** When `project`'s first result started and its last one ended, in the run's own report. The
   *  run check cannot hold the first pass to the shared phase, since in CI they are different
   *  jobs on different clocks; one run of the real projects is one clock, so this does. */
  function span(project: string): { start: number; end: number; n: number } {
    interface Suite { specs?: Array<{ tests?: Array<{ projectName: string; results?: Array<{ startTime: string; duration: number }> }> }>; suites?: Suite[] }
    const results: Array<{ startTime: string; duration: number }> = []
    const walk = (suites: Suite[] | undefined) => {
      for (const suite of suites ?? []) {
        for (const spec of suite.specs ?? []) {
          for (const test of spec.tests ?? []) if (test.projectName === project) results.push(...(test.results ?? []))
        }
        walk(suite.suites)
      }
    }
    walk(JSON.parse(readFileSync(join(dir, 'report.json'), 'utf8')).suites)
    const starts = results.map((r) => Date.parse(r.startTime))
    return { start: Math.min(...starts), end: Math.max(...results.map((r, i) => starts[i] + r.duration)), n: results.length }
  }
  /** The first pass started once every shared test had ended. */
  function expectStatefulAfterShared() {
    const shared = span('chromium')
    const first = span('stateful')
    expect(shared.n, 'the shared phase ran no test').toBeGreaterThan(0)
    expect(first.n, 'the first pass ran no test').toBeGreaterThan(0)
    expect(first.start - shared.end, 'stateful started before chromium ended (ms after its end)').toBeGreaterThanOrEqual(0)
  }

  it('a stateful-only run: every pass runs, one after another', () => {
    const { ran, out, judged, verdict } = run(true)
    expect(ran.status, out).toBe(0)
    expect(judged.status, verdict).toBe(0)
    expect(verdict).toMatch(IN_ORDER)
  }, 180_000)

  it('a stateful-only run with a red first pass: the skipped second pass reds the run and the run check, named', () => {
    const { ran, out, judged, verdict } = run(true, true)
    expect(ran.status, out).toBe(1)
    // Which project runs which spec is the seed's (7 draws deployedArtifacts first); both are named.
    expect(out).toMatch(/2 test\(s\) did not run/)
    expect(out).toMatch(/stateful-again-\d › staleWrite\.spec\.ts › tab B is refused/)
    expect(out).toMatch(/stateful-again-\d › deployedArtifacts\.spec\.ts › a deployed page loads/)
    expect(judged.status, verdict).toBe(1)
    expect(verdict).toMatch(/1 test failed: stateful › staleWrite\.spec\.ts › tab B is refused/)
    expect(verdict).toMatch(/2 tests did not run: /)
    expect(verdict).toMatch(/stateful-again-\d › staleWrite\.spec\.ts › tab B is refused/)
    expect(verdict).toMatch(/stateful-again-\d › deployedArtifacts\.spec\.ts › a deployed page loads/)
  }, 180_000)

  it('a full run: the passes follow the shared phase, one after another', () => {
    const { ran, out, judged, verdict } = run(false)
    expect(ran.status, out).toBe(0)
    expect(judged.status, verdict).toBe(0)
    expect(verdict).toMatch(IN_ORDER)
    expectStatefulAfterShared()
  }, 180_000)

  it('a full run with a red first pass: the second pass still runs, after it', () => {
    // A full run's passes are teardowns, which run whether or not their owner passed.
    const { ran, out, judged, verdict } = run(false, true)
    expect(ran.status, out).toBe(1)
    expect(out).not.toMatch(/did not run/)
    expect(judged.status, verdict).toBe(1)
    expect(verdict).toMatch(/1 test failed: stateful › staleWrite\.spec\.ts › tab B is refused/)
    expect(verdict).not.toMatch(/did not run|started before/)
    expectStatefulAfterShared()
  }, 180_000)
})
