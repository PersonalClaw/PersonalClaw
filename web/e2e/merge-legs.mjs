// ── The browser run's legs, merged into the run's one report ────────────────────────────────────
//
// `node e2e/merge-legs.mjs <blob root> <leg>...`, from web/. The run is split over CI jobs: each
// `e2e-shard` leg runs half of the specs that share a gateway, then one run on a home of its own
// (the `idle` home on one leg, the stateful set and its seeded second pass on the other), and
// `e2e-pinned` runs the `matrix` home and the visual rail against the pinned image's browser.
// Each run leaves one blob report in `<blob root>/<leg>/`. This merges
// them into `e2e-report.json` and `playwright-report/`, the report every later check reads.
//
// 🔑 A LEG WITH NO BLOB IS A RED, NOT A SMALLER REPORT. A leg that failed to install, to boot or to
// finish leaves no blob, and a merge of the others is a well-formed report of a run that skipped
// its tests: every count in it would agree with itself. So each named leg must have left exactly
// one blob, or nothing is merged and this exits 1 naming each leg that did not.
//
// Playwright's merge keeps one blob's metadata, and the shard legs run without the stateful chain,
// so the seed it names can be a leg's that drew no order. The seed and the order the second pass
// actually ran in are written back over it (`withStatefulMetadata`), from the tests themselves.
//
// Deliberately NOT a *.spec.ts: a report tool, not a test, like assert-no-route-skips.mjs.
import { spawnSync } from 'node:child_process'
import { copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const REPORT = 'e2e-report.json'

/** Every test of a Playwright JSON report, with its spec file and project. */
function* tests(suites, file = '') {
  for (const suite of suites ?? []) {
    const at = suite.file ?? file
    for (const spec of suite.specs ?? []) {
      for (const test of spec.tests ?? []) yield { file: (spec.file ?? at).split('/').pop(), project: test.projectName, test }
    }
    yield* tests(suite.suites, at)
  }
}

/** `report` with `config.metadata.statefulSeed` and `statefulOrder` read from its second pass: the
 *  seed every `stateful-again-*` test carries, and the spec each of those projects ran, in order. A
 *  report whose second pass names no single seed is returned as it is, for the run check to name. */
export function withStatefulMetadata(report) {
  const again = [...tests(report.suites)].filter((t) => /^stateful-again-\d+$/.test(t.project))
  const seeds = new Set(again.flatMap((t) => (t.test.annotations ?? []).filter((a) => a.type === 'stateful-seed').map((a) => a.description)))
  if (seeds.size !== 1) return report
  const order = [...new Set(
    again
      .sort((a, b) => Number(a.project.split('-').pop()) - Number(b.project.split('-').pop()))
      .map((t) => t.file),
  )]
  const metadata = { ...(report.config?.metadata ?? {}), statefulSeed: [...seeds][0], statefulOrder: order }
  return { ...report, config: { ...(report.config ?? {}), metadata } }
}

function main(argv) {
  const [root, ...legs] = argv
  if (!root || !legs.length) {
    console.error('usage: node e2e/merge-legs.mjs <blob root> <leg>...')
    process.exit(2)
  }
  const missing = []
  const found = []
  for (const leg of legs) {
    const dir = resolve(root, leg)
    const blobs = existsSync(dir) ? readdirSync(dir).filter((f) => f.endsWith('.zip')) : []
    if (blobs.length === 1) found.push([leg, join(dir, blobs[0])])
    else missing.push(`the ${leg} leg left ${blobs.length ? `${blobs.length} blob reports` : 'no blob report'} in ${dir}`)
  }
  if (missing.length) {
    console.error('\n✗ the browser run cannot be merged:')
    for (const line of missing) console.error(`    ${line}`)
    console.error(
      '\n  A leg with no blob did not run, or did not finish: its tests are in no report, and a merge of\n' +
        '  the others would read as a whole run. One leg is one run, so it leaves exactly one blob.\n',
    )
    process.exit(1)
  }
  // `merge-reports` reads the blobs of one folder, not of the folders under it.
  const merged = resolve(root, 'merged')
  rmSync(merged, { recursive: true, force: true })
  mkdirSync(merged, { recursive: true })
  for (const [leg, blob] of found) copyFileSync(blob, join(merged, `${leg}.zip`))
  const ran = spawnSync('npx', ['playwright', 'merge-reports', '--reporter=json,html', merged], {
    stdio: 'inherit',
    env: { ...process.env, PLAYWRIGHT_JSON_OUTPUT_NAME: REPORT, PLAYWRIGHT_HTML_OPEN: 'never' },
  })
  if (ran.status !== 0) {
    console.error(`\n✗ playwright merge-reports exited ${ran.status ?? ran.signal}`)
    process.exit(1)
  }
  const report = JSON.parse(readFileSync(REPORT, 'utf8'))
  writeFileSync(REPORT, JSON.stringify(withStatefulMetadata(report), null, 2))
  console.log(`merged ${found.length} legs (${legs.join(', ')}) into ${REPORT}`)
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) main(process.argv.slice(2))
