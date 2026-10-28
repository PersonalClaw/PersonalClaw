// @module-tag tree-scan
import { afterEach, describe, expect, it } from 'vitest'
import { spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, relative } from 'node:path'
import { PROJECTS_FOR } from '../../e2e/isolation'
import { withStatefulMetadata } from '../../e2e/merge-legs.mjs'
import { filesUnder, readSource } from '../test/sourceTree'

// ── The browser run is two legs and a stateful run, and one report holds all three ─────────────
//
// The `e2e-shard` legs each run half of the specs that share a gateway, and the second leg then runs
// the stateful set and its seeded second pass. The `e2e` job merges their blob reports into the
// run's one report and judges it. A leg that left no blob ran nothing anyone can read, and a
// merged report without the stateful run would pass every check that counts what is in it. So
// `e2e/merge-legs.mjs` refuses to merge with a leg missing, and `e2e/assert-one-run.mjs` holds the
// merged report to one run: every test once, each where its isolation mode allows, the stateful
// set and its second pass all there, under one seed, each pass after the one before it, and no
// failure. These plant each way a merged report can be wrong, and the exit code and the line that
// names it.

const WEB = process.cwd()
const node = (script: string, args: string[], cwd = WEB) =>
  spawnSync(process.execPath, [join(WEB, 'e2e', script), ...args], { cwd, encoding: 'utf8' })

const dirs: string[] = []
afterEach(() => { for (const d of dirs.splice(0)) rmSync(d, { recursive: true, force: true }) })
const scratch = () => {
  const d = mkdtempSync(join(tmpdir(), 'e2e-merged-'))
  dirs.push(d)
  return d
}

interface Row {
  project: string; file: string; title: string; status?: string
  /** The status of the test's one result, and the status the test expected, when they are not
   *  what `status` gives: an `interrupted` result, or a `skipped` one of a test that did not
   *  skip itself. */
  result?: string; expected?: string
  annotations?: Array<{ type: string; description?: string }>
  /** When the test started, in seconds from the run's start, and how long it took. */
  at?: number; took?: number
}

const SEED = '1944044356'
const isolated = (mode: string) => [{ type: 'isolation', description: `${mode}: why` }]
const seeded = (seed = SEED) => [...isolated('serial'), { type: 'stateful-seed', description: seed }]

/** What one good merged run holds: the setup of each leg, the shared specs split over two legs,
 *  and the stateful run with its second pass in the order its seed drew. */
const GOOD: Row[] = [
  { project: 'setup', file: 'auth.setup.ts', title: 'authenticate' },
  { project: 'setup', file: 'auth.setup.ts', title: 'authenticate' },
  { project: 'setup', file: 'auth.setup.ts', title: 'authenticate' },
  { project: 'smoke', file: 'smoke.spec.ts', title: 'the dashboard renders', annotations: isolated('shared') },
  { project: 'chromium', file: 'a11y.spec.ts', title: 'Home (#/dashboard)', annotations: isolated('shared') },
  { project: 'chromium', file: 'walkthrough.spec.ts', title: 'Tasks (#/tasks) light', annotations: isolated('shared') },
  { project: 'chromium', file: 'walkthrough.spec.ts', title: 'Tasks (#/tasks) dark', status: 'skipped', annotations: isolated('shared') },
  { project: 'stateful', file: 'staleWrite.spec.ts', title: 'tab B is refused', annotations: isolated('serial'), at: 20 },
  { project: 'stateful', file: 'deployedArtifacts.spec.ts', title: 'a deployed page loads', annotations: isolated('serial'), at: 26 },
  { project: 'stateful-again-1', file: 'staleWrite.spec.ts', title: 'tab B is refused', annotations: seeded(), at: 32 },
  { project: 'stateful-again-2', file: 'deployedArtifacts.spec.ts', title: 'a deployed page loads', annotations: seeded(), at: 38 },
]
const T0 = Date.parse('2026-10-19T00:00:00.000Z')
const METADATA = {
  statefulSeed: SEED,
  statefulOrder: ['staleWrite.spec.ts', 'deployedArtifacts.spec.ts'],
  isolation: {
    'smoke.spec.ts': 'shared', 'a11y.spec.ts': 'shared', 'walkthrough.spec.ts': 'shared', 'visual.spec.ts': 'shared',
    'staleWrite.spec.ts': 'serial', 'deployedArtifacts.spec.ts': 'serial',
  },
}

/** A Playwright JSON report holding `rows`, one file suite per spec. */
function report(rows: Row[], metadata: Record<string, unknown> = METADATA) {
  const byFile = new Map<string, Row[]>()
  for (const r of rows) byFile.set(r.file, [...(byFile.get(r.file) ?? []), r])
  return {
    config: { metadata },
    suites: [...byFile].map(([file, rs]) => ({
      title: file, file,
      specs: [],
      // A describe block, as most specs have, so the walk has to go down a level.
      suites: [{
        title: 'a describe', file,
        specs: rs.map((r) => ({
          title: r.title, file,
          tests: [{
            projectName: r.project, status: r.status ?? 'expected', annotations: r.annotations ?? [],
            expectedStatus: r.expected ?? (r.status === 'skipped' ? 'skipped' : 'passed'),
            results: r.status === 'unrun' ? [] : [{
              status: r.result ?? (r.status === 'skipped' ? 'skipped' : r.status === 'unexpected' ? 'failed' : 'passed'),
              startTime: new Date(T0 + (r.at ?? 1) * 1000).toISOString(), duration: (r.took ?? 5) * 1000,
            }],
          }],
        })),
        suites: [],
      }],
    })),
    stats: { startTime: new Date(T0).toISOString(), expected: rows.length, unexpected: 0, flaky: 0, skipped: 0 },
  }
}

function judge(rows: Row[], metadata?: Record<string, unknown>) {
  const dir = scratch()
  const path = join(dir, 'e2e-report.json')
  writeFileSync(path, JSON.stringify(report(rows, metadata)))
  return node('assert-one-run.mjs', [path])
}

// The homes a spec runs alone on (`alone` in isolation.ts): each is the one project of an
// invocation that ran nothing else, so the merged report holds each of those specs to its own.
const ALONE_GOOD: Row[] = [
  ...GOOD,
  { project: 'setup', file: 'auth.setup.ts', title: 'authenticate' },
  { project: 'setup', file: 'auth.setup.ts', title: 'authenticate' },
  { project: 'matrix', file: 'surfaces.spec.ts', title: 'Chat (#/chat)', annotations: isolated('alone') },
  { project: 'idle', file: 'idleCost.spec.ts', title: 'Chat idles', annotations: isolated('alone') },
]
const ALONE_METADATA = {
  ...METADATA,
  isolation: { ...METADATA.isolation, 'surfaces.spec.ts': 'alone', 'idleCost.spec.ts': 'alone' },
  isolationHome: { 'surfaces.spec.ts': 'matrix', 'idleCost.spec.ts': 'idle' },
}

describe('assert-one-run holds each spec that runs alone to its own home', () => {
  it('passes a run with each home there, and names them', () => {
    const ran = judge(ALONE_GOOD, ALONE_METADATA)
    expect(ran.stderr).toBe('')
    expect(ran.status).toBe(0)
    expect(ran.stdout).toMatch(/idleCost\.spec\.ts, surfaces\.spec\.ts each on a home of its own/)
  })

  const PLANTS: Array<[string, Row[], RegExp]> = [
    [
      'the matrix ran beside the shared specs',
      ALONE_GOOD.map((r) => (r.file === 'surfaces.spec.ts' ? { ...r, project: 'chromium' } : r)),
      /surfaces\.spec\.ts is alone on the matrix home but ran in the chromium project/,
    ],
    [
      'a home is missing from the merge',
      ALONE_GOOD.filter((r) => r.project !== 'idle'),
      /idleCost\.spec\.ts did not run on its own home \(idle\)/,
    ],
    [
      'a shared spec ran on a home of its own',
      [...ALONE_GOOD, { project: 'matrix', file: 'a11y.spec.ts', title: 'Home', annotations: isolated('shared') }],
      /a11y\.spec\.ts is shared but ran in the matrix project, a home of its own for other specs/,
    ],
  ]
  for (const [name, rows, message] of PLANTS) {
    it(`names it when ${name}`, () => {
      const ran = judge(rows, ALONE_METADATA)
      expect(ran.status).toBe(1)
      expect(ran.stderr).toMatch(message)
    })
  }
})

describe('assert-one-run holds the merged report to one run', () => {
  it('passes a good run, and says what it checked', () => {
    const ran = judge(GOOD)
    expect(ran.stderr).toBe('')
    expect(ran.status).toBe(0)
    expect(ran.stdout).toMatch(/8 tests/)
    expect(ran.stdout).toMatch(/second pass in seed 1944044356's order: staleWrite\.spec\.ts → deployedArtifacts\.spec\.ts/)
    expect(ran.stdout).toMatch(/each pass after the one before it had ended/)
  })

  it("knows the stateful projects as isolation.ts's serial ones", () => {
    expect(PROJECTS_FOR.serial).toEqual(['stateful', 'stateful-again-1', 'stateful-again-2'])
  })

  const PLANTS: Array<[string, Row[], RegExp, Record<string, unknown>?]> = [
    [
      'the stateful run is missing',
      GOOD.filter((r) => !r.project.startsWith('stateful')),
      /the stateful run is not in this report: no test ran in the stateful, stateful-again-1 or stateful-again-2 project/,
    ],
    [
      'a stateful spec has no second pass',
      GOOD.filter((r) => r.project !== 'stateful-again-2'),
      /deployedArtifacts\.spec\.ts ran in no stateful-again-\* project/,
    ],
    [
      'a stateful spec is missing from the first pass',
      GOOD.filter((r) => !(r.project === 'stateful' && r.file === 'deployedArtifacts.spec.ts')),
      /deployedArtifacts\.spec\.ts did not run in the stateful project/,
    ],
    [
      'a test ran in both legs',
      [...GOOD, GOOD[4]],
      /ran 2 times: chromium › a11y\.spec\.ts › a describe › Home \(#\/dashboard\)/,
    ],
    [
      'a stateful spec ran beside the shared ones',
      [...GOOD, { project: 'chromium', file: 'staleWrite.spec.ts', title: 'tab B is refused', annotations: isolated('serial') }],
      /staleWrite\.spec\.ts is serial but ran in the chromium project/,
    ],
    [
      'a shared spec ran in a stateful project',
      [...GOOD, { project: 'stateful', file: 'a11y.spec.ts', title: 'Home (#/dashboard)', annotations: isolated('shared') }],
      /a11y\.spec\.ts is shared but ran in the stateful project/,
    ],
    [
      'a test lost its isolation annotation',
      GOOD.map((r, i) => (i === 5 ? { ...r, annotations: [] } : r)),
      /1 test carries no isolation annotation: chromium › walkthrough\.spec\.ts/,
    ],
    [
      'a spec the metadata does not classify',
      [...GOOD, { project: 'chromium', file: 'new.spec.ts', title: 'x', annotations: isolated('shared') }],
      /new\.spec\.ts has no isolation mode in the report's metadata/,
    ],
    [
      'the second pass does not agree on its seed',
      GOOD.map((r) => (r.project === 'stateful-again-2' ? { ...r, annotations: seeded('7') } : r)),
      /the second pass names 2 seeds \(1944044356, 7\)/,
    ],
    [
      "the metadata's seed is another leg's",
      GOOD,
      /metadata\.statefulSeed is 99, and the second pass ran in seed 1944044356's order/,
      { ...METADATA, statefulSeed: '99' },
    ],
    [
      "the metadata's order is not the order the second pass ran in",
      GOOD,
      /metadata\.statefulOrder is deployedArtifacts\.spec\.ts → staleWrite\.spec\.ts, and the second pass ran staleWrite\.spec\.ts → deployedArtifacts\.spec\.ts/,
      { ...METADATA, statefulOrder: ['deployedArtifacts.spec.ts', 'staleWrite.spec.ts'] },
    ],
    [
      'a test failed in one leg',
      GOOD.map((r, i) => (i === 5 ? { ...r, status: 'unexpected' } : r)),
      /1 test failed: chromium › walkthrough\.spec\.ts › a describe › Tasks \(#\/tasks\) light/,
    ],
    [
      'the second pass never started (its tests are in the report with no result)',
      GOOD.map((r) => (r.project.startsWith('stateful-again') ? { ...r, status: 'unrun' } : r)),
      /2 tests did not run: stateful-again-1 › staleWrite\.spec\.ts › a describe › tab B is refused; stateful-again-2/,
    ],
    [
      // What a full run did when `stateful-again-2` was the smoke's teardown: it ran in the shared phase.
      'the second pass started before the first one ended',
      GOOD.map((r) => (r.project === 'stateful-again-2' ? { ...r, at: 10 } : r)),
      /stateful-again-2 started before stateful ended \(at 31\.0 s\): stateful-again-2 › deployedArtifacts\.spec\.ts › a describe › a deployed page loads at 10\.0 s/,
    ],
    [
      'a second-pass project started before the one ahead of it ended',
      GOOD.map((r) => (r.project === 'stateful-again-2' ? { ...r, at: 35 } : r)),
      /stateful-again-2 started before stateful-again-1 ended \(at 37\.0 s\)/,
    ],
    [
      'a first-pass test was still running when the second pass began',
      GOOD.map((r) => (r.project === 'stateful' && r.file === 'deployedArtifacts.spec.ts' ? { ...r, took: 7 } : r)),
      /stateful-again-1 started before stateful ended \(at 33\.0 s\): stateful-again-1 › staleWrite\.spec\.ts › a describe › tab B is refused at 32\.0 s/,
    ],
    [
      // Each leg runs the auth setup for itself, so the merged report holds it once per leg. Only
      // that count is exempt: a setup that failed or never ran is red.
      'the auth setup of one leg failed',
      GOOD.map((r, i) => (i === 1 ? { ...r, status: 'unexpected' } : r)),
      /1 test failed: setup › auth\.setup\.ts › a describe › authenticate/,
    ],
    [
      'the auth setup of one leg never ran',
      GOOD.map((r, i) => (i === 1 ? { ...r, status: 'unrun' } : r)),
      /1 test did not run: setup › auth\.setup\.ts › a describe › authenticate/,
    ],
    [
      // Playwright's own outcome for a test whose one result was interrupted is `skipped`, so it
      // is neither a failure nor a test with no result.
      'a test was interrupted mid-run',
      GOOD.map((r, i) => (i === 4 ? { ...r, status: 'skipped', result: 'interrupted' } : r)),
      /1 test did not run: chromium › a11y\.spec\.ts › a describe › Home \(#\/dashboard\)/,
    ],
    [
      // A skipped result of a test that expected to pass is one Playwright counts as not run.
      'a test was skipped without skipping itself',
      GOOD.map((r, i) => (i === 4 ? { ...r, status: 'skipped', expected: 'passed' } : r)),
      /1 test did not run: chromium › a11y\.spec\.ts › a describe › Home \(#\/dashboard\)/,
    ],
    ['the report is empty', [], /the report holds no test/],
  ]

  it.each(PLANTS)('reds when %s', (_, rows, says, metadata) => {
    const ran = judge(rows, metadata)
    expect(ran.status, ran.stdout + ran.stderr).toBe(1)
    expect(ran.stderr).toMatch(says)
  })

  // Under CI a test that passes only on its retry fails its leg (`failOnFlakyTests`), and the leg's
  // record holds the rerun alone the verdict's flake rule judges it by: here it is named, with the
  // leg run that ran it, and is no second red.
  const RETRIED: Array<[string, Row[], RegExp, Record<string, unknown>?]> = [
    [
      'a shared test of a shard leg',
      GOOD.map((r, i) => (i === 4 ? { ...r, status: 'flaky' } : r)),
      /• passed only on its retry, red on its leg \(a shard leg's shared run\): chromium › a11y\.spec\.ts › a describe › Home \(#\/dashboard\)/,
    ],
    [
      "one leg's auth setup",
      GOOD.map((r, i) => (i === 1 ? { ...r, status: 'flaky' } : r)),
      /• passed only on its retry, red on its leg \(one leg's auth setup\): setup › auth\.setup\.ts › a describe › authenticate/,
    ],
    [
      'a test of the stateful run',
      GOOD.map((r, i) => (i === 7 ? { ...r, status: 'flaky' } : r)),
      /• passed only on its retry, red on its leg \(the stateful run\): stateful › staleWrite\.spec\.ts › a describe › tab B is refused/,
    ],
    [
      'a test of a home of its own',
      ALONE_GOOD.map((r) => (r.project === 'idle' ? { ...r, status: 'flaky' } : r)),
      /• passed only on its retry, red on its leg \(the idle home's run\): idle › idleCost\.spec\.ts › a describe › Chat idles/,
      ALONE_METADATA,
    ],
  ]

  it.each(RETRIED)('names %s that passed only on its retry, and holds the run whole', (_, rows, says, metadata) => {
    const ran = judge(rows, metadata)
    expect(ran.status, ran.stdout + ran.stderr).toBe(0)
    expect(ran.stdout).toMatch(says)
    expect(ran.stdout).toMatch(/✓ one run/)
  })

  it('still reds a test that failed, beside one that passed only on its retry', () => {
    const rows = GOOD.map((r, i) => (i === 4 ? { ...r, status: 'flaky' } : i === 5 ? { ...r, status: 'unexpected' } : r))
    const ran = judge(rows)
    expect(ran.status, ran.stdout + ran.stderr).toBe(1)
    expect(ran.stderr).toMatch(/1 test failed: chromium › walkthrough\.spec\.ts › a describe › Tasks \(#\/tasks\) light/)
    expect(ran.stdout).toMatch(/passed only on its retry, red on its leg \(a shard leg's shared run\): chromium › a11y\.spec\.ts/)
  })

  it('reds when a leg stopped before its first test: its error is there, and none of its tests', () => {
    // A leg whose gateway never booted ends before Playwright lists its tests, so its blob holds
    // the error and no test, and every count above agrees with the other legs' tests alone.
    const path = join(scratch(), 'e2e-report.json')
    const rows = GOOD.filter((r) => r.file !== 'walkthrough.spec.ts')
    const errors = [{ message: 'Error: Timed out waiting 60000ms from config.webServer.\n    at the gateway' }]
    writeFileSync(path, JSON.stringify({ ...report(rows), errors }))
    const ran = node('assert-one-run.mjs', [path])
    expect(ran.status, ran.stdout + ran.stderr).toBe(1)
    expect(ran.stderr).toMatch(/1 error outside any test: Error: Timed out waiting 60000ms from config\.webServer\./)
    expect(ran.stderr).not.toMatch(/at the gateway/)
    // The same report with no error passes: the error is the whole of what is wrong with it.
    writeFileSync(path, JSON.stringify({ ...report(rows), errors: [] }))
    expect(node('assert-one-run.mjs', [path]).status).toBe(0)
  })

  it('names the error of a leg that ran no test at all, beside the empty report', () => {
    const path = join(scratch(), 'e2e-report.json')
    writeFileSync(path, JSON.stringify({ ...report([]), errors: [{ message: 'Error: No tests found' }] }))
    const ran = node('assert-one-run.mjs', [path])
    expect(ran.status).toBe(1)
    expect(ran.stderr).toMatch(/1 error outside any test: Error: No tests found/)
    expect(ran.stderr).toMatch(/the report holds no test/)
  })

  it('reds on a report it cannot read, naming it', () => {
    const ran = node('assert-one-run.mjs', [join(scratch(), 'absent.json')])
    expect(ran.status).toBe(1)
    expect(ran.stderr).toMatch(/cannot read the merged report .*absent\.json/)
  })
})

// ── A retried pass is named, not red, because its leg is red for it ───────────────────────────
//
// assert-one-run names a test that passed only on its retry rather than redding on it, since under
// CI its leg already failed for it (`failOnFlakyTests`) and the verdict judges that leg's record.
// That holds only while a retry happens where failOnFlakyTests is on and nowhere else: the config
// retries once and fails on a retried pass under CI, retries none without CI, and no spec turns its
// own retries up.
describe('a retried pass fails its leg wherever one can happen', () => {
  /** The config as `playwright test --list` resolves it, with CI set or not. */
  function listed(ci: boolean) {
    const dir = scratch()
    const env: Record<string, string | undefined> = {
      ...process.env,
      TMPDIR: dir,
      PLAYWRIGHT_JSON_OUTPUT_NAME: undefined,
      PLAYWRIGHT_BLOB_OUTPUT_DIR: join(dir, 'blob'),
      PLAYWRIGHT_HTML_OUTPUT_DIR: join(dir, 'html'),
      PW_E2E_HOME: '', PW_PORT: '', PW_GATEWAY_PORT: '', PW_NO_SERVER: '', PW_REUSE_SERVER: '',
      STORAGE_STATE: '', PW_OUTPUT_DIR: '',
      CI: ci ? '1' : undefined,
    }
    const ran = spawnSync('npx', ['playwright', 'test', '--list', '--reporter=json', '-c', 'playwright.config.ts'], {
      cwd: WEB, encoding: 'utf8', env: env as NodeJS.ProcessEnv, timeout: 120_000,
    })
    expect(ran.status, ran.stderr).toBe(0)
    const config = JSON.parse(ran.stdout).config as { failOnFlakyTests: boolean; projects: Array<{ name: string; retries: number }> }
    expect(config.projects.length).toBeGreaterThan(0)
    return config
  }

  it('retries once under CI, and fails the run on a retried pass there', () => {
    const config = listed(true)
    expect(config.failOnFlakyTests).toBe(true)
    expect(config.projects.filter((p) => p.retries !== 1)).toEqual([])
  })

  it('retries none without CI, so no run there has a retried pass', () => {
    const config = listed(false)
    expect(config.failOnFlakyTests).toBe(false)
    expect(config.projects.filter((p) => p.retries !== 0)).toEqual([])
  })

  it('has no spec or helper under e2e/ that sets its retries above 0', () => {
    const dir = join(WEB, 'e2e')
    const set: string[] = []
    for (const path of filesUnder(dir, (name) => /\.(ts|mjs)$/.test(name))) {
      for (const m of readSource(path).matchAll(/\bretries\s*:\s*([^,}\s]+)/g)) {
        if (m[1] !== '0') set.push(`${relative(dir, path)}: retries: ${m[1]}`)
      }
    }
    expect(set).toEqual([])
  })
})

describe('merge-legs refuses to merge with a leg missing', () => {
  /** A blob folder per leg named, each holding one stand-in blob unless the leg is `missing`. */
  function blobs(legs: string[], missing: string[] = [], extra: string[] = []) {
    const root = scratch()
    for (const leg of legs) {
      mkdirSync(join(root, leg), { recursive: true })
      if (!missing.includes(leg)) writeFileSync(join(root, leg, 'report-1.zip'), 'not a zip')
      if (extra.includes(leg)) writeFileSync(join(root, leg, 'report-2.zip'), 'not a zip')
    }
    return root
  }
  const LEGS = ['shard-1', 'shard-2', 'stateful']

  it.each(LEGS)('reds when the %s leg left no blob report, naming it', (leg) => {
    const root = blobs(LEGS, [leg])
    const ran = node('merge-legs.mjs', [root, ...LEGS])
    expect(ran.status).toBe(1)
    expect(ran.stderr).toMatch(new RegExp(`the ${leg} leg left no blob report in .*${leg}`))
    expect(ran.stderr).toMatch(/its tests are in no report/)
  })

  it('reds when a leg folder was never written at all', () => {
    const root = blobs(['shard-1', 'stateful'])
    const ran = node('merge-legs.mjs', [root, ...LEGS])
    expect(ran.status).toBe(1)
    expect(ran.stderr).toMatch(/the shard-2 leg left no blob report/)
  })

  it('reds when a leg left two, since one leg is one run', () => {
    const root = blobs(LEGS, [], ['shard-2'])
    const ran = node('merge-legs.mjs', [root, ...LEGS])
    expect(ran.status).toBe(1)
    expect(ran.stderr).toMatch(/the shard-2 leg left 2 blob reports/)
  })

  it('names every missing leg at once', () => {
    const root = blobs(LEGS, ['shard-1', 'stateful'])
    const ran = node('merge-legs.mjs', [root, ...LEGS])
    expect(ran.stderr).toMatch(/shard-1[\s\S]*stateful/)
  })

  it('refuses a call that names no leg', () => {
    const ran = node('merge-legs.mjs', [scratch()])
    expect(ran.status).toBe(2)
    expect(ran.stderr).toMatch(/usage/)
  })

  it('the stateful second pass sets the merged metadata, whichever blob Playwright took it from', () => {
    // The merge itself is Playwright's, which keeps one blob's metadata. What this script writes
    // after it is the seed and the order the second pass actually ran in.
    const merged = withStatefulMetadata(report(GOOD, { ...METADATA, statefulSeed: '99', statefulOrder: [] }))
    expect(merged.config.metadata.statefulSeed).toBe(SEED)
    expect(merged.config.metadata.statefulOrder).toEqual(['staleWrite.spec.ts', 'deployedArtifacts.spec.ts'])
    expect(judge(GOOD, merged.config.metadata).status).toBe(0)
    // A merged report with no second pass is left as it is, for the run check to name.
    const none = withStatefulMetadata(report(GOOD.filter((r) => !r.project.startsWith('stateful')), { ...METADATA, statefulSeed: '99' }))
    expect(none.config.metadata.statefulSeed).toBe('99')
  })
})
