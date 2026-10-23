// ── The merged browser report is one whole run — asserted, not assumed ──────────────────────────
//
// `node e2e/assert-one-run.mjs e2e-report.json`, from web/, after `merge-legs.mjs`. The run is two
// `e2e-shard` legs and the `e2e-stateful` job's runs (the stateful set, and each home a spec runs
// alone on), merged. Each leg's own run enforced its isolation
// modes (`isolationReporter.ts`), but no leg saw the others, and a merge is only as whole as what
// was handed to it. This holds the merged report to one run:
//
//   · no test failed or passed only on its retry, and every test ran, each leg's auth setup
//     included; and there are some. A test ran when one of its results passed, failed or timed
//     out, or it skipped itself: a test with no result never started, and one whose only result
//     was interrupted, or skipped while it expected to run, is one Playwright counts as not run;
//   · AND NO LEG ERRED OUTSIDE A TEST. A leg whose gateway never booted ends before Playwright lists
//     its tests, so its blob holds the error and none of its tests, and every count below agrees
//     with the other legs' tests alone. Its error is the one trace it leaves in the merge;
//   · every test ran once (the auth setup aside, which each leg runs for itself, so only its
//     count is exempt): a test twice is a shard split that overlapped;
//   · every test carries its `isolation` annotation, and ran where its mode allows: a serial spec
//     only in the stateful projects, a shared one never there, and an alone spec only in its own
//     home's project (`metadata.isolationHome`), which only an invocation of that home's specs has;
//   · EVERY HOME A SPEC RUNS ALONE ON IS THERE: each alone spec ran in its home's project, as the
//     stateful run must be there below;
//   · THE STATEFUL RUN IS THERE: every serial spec ran in `stateful` and once more in one
//     `stateful-again-*` project. Without this, a merged report missing the stateful run passes
//     every check above, because each counts only what is in it;
//   · the second pass names one seed, and `metadata.statefulSeed` and `statefulOrder` are that
//     seed and the order the second pass ran in;
//   · AND THE PASSES RAN ONE AFTER ANOTHER, read from each result's start and duration: no test of
//     `stateful-again-N` started before every pass ahead of it (`stateful`, then the lower N) had
//     ended. A second pass that overlaps the first has the right tests in the right projects, and
//     tells nothing about order; a full run once started `stateful-again-2` in the shared phase.
//
// The stateful projects are isolation.ts's PROJECTS_FOR.serial (`src/design/e2eMergedRun.test.ts`
// holds the two to each other); this reads only the report, so it judges a run without loading the
// tree that ran it. Deliberately NOT a *.spec.ts: a report checker, like assert-no-route-skips.mjs.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const STATEFUL = /^stateful(-again-\d+)?$/
const AGAIN = /^stateful-again-\d+$/

function fail(lines) {
  console.error('\n✗ the merged browser report is not one whole run:')
  for (const line of lines) console.error(`    ${line}`)
  console.error('')
  process.exit(1)
}

const path = resolve(process.argv[2] ?? 'e2e-report.json')
let report
try {
  report = JSON.parse(readFileSync(path, 'utf8'))
} catch (e) {
  fail([`cannot read the merged report ${path}: ${e.message}`])
}

/** Every test, with its spec file, its title path and its project. */
function* walk(suites, file = '', path = []) {
  for (const suite of suites ?? []) {
    const at = suite.file ?? file
    const here = suite.title && suite.title !== at ? [...path, suite.title] : path
    for (const spec of suite.specs ?? []) {
      for (const test of spec.tests ?? []) {
        const name = (spec.file ?? at).split('/').pop()
        yield { file: name, project: test.projectName, title: [...here, spec.title].join(' › '), test }
      }
    }
    yield* walk(suite.suites, at, here)
  }
}

const all = [...walk(report.suites)]
// The specs' tests: every test but the auth setup, which each leg runs for itself and which is no
// spec, so it has no isolation mode and is in the report once per leg.
const tests = all.filter((t) => t.project !== 'setup')
const named = (t) => `${t.project} › ${t.file} › ${t.title}`
const some = (list) => list.slice(0, 5).join('; ') + (list.length > 5 ? `; and ${list.length - 5} more` : '')
const problems = []

// Each error's first line: the rest is its stack.
const errors = (Array.isArray(report.errors) ? report.errors : report.errors == null ? [] : [report.errors])
  .map((e) => String(e?.message ?? e?.value ?? JSON.stringify(e)).split('\n')[0])
if (errors.length) problems.push(`${errors.length} error${errors.length === 1 ? '' : 's'} outside any test: ${some(errors)}`)

if (!tests.length) fail([...problems, 'the report holds no test'])

// These three read every test, the auth setup too: a leg whose setup failed, passed only on its
// retry or never ran is red here as its tests are.
const failed = all.filter((t) => t.test.status === 'unexpected').map(named)
if (failed.length) problems.push(`${failed.length} test${failed.length === 1 ? '' : 's'} failed: ${some(failed)}`)
// Under CI a test that passes only on its retry fails its leg (`failOnFlakyTests`), so it is a red
// here too, never a pass.
const flaky = all.filter((t) => t.test.status === 'flaky').map(named)
if (flaky.length) problems.push(`${flaky.length} test${flaky.length === 1 ? '' : 's'} passed only on a retry: ${some(flaky)}`)
// A test with no result never started: its project's dependency failed, the run was cut short, or
// Playwright never scheduled it. It is in the report, and it measured nothing. Nor did a test whose
// only result was interrupted, or skipped while the test expected to run: Playwright's own outcome
// for either is `skipped`, which is neither a failure nor a test with no result. A test that
// skipped itself (`test.skip`) expected the skip, so its `skipped` result counts as a run.
const ran = (t) => (t.test.results ?? []).some((r) =>
  ['passed', 'failed', 'timedOut'].includes(r.status) || (r.status === 'skipped' && t.test.expectedStatus === 'skipped'))
const unrun = all.filter((t) => !ran(t)).map(named)
if (unrun.length) problems.push(`${unrun.length} test${unrun.length === 1 ? '' : 's'} did not run: ${some(unrun)}`)

const counts = new Map()
for (const t of tests) counts.set(named(t), (counts.get(named(t)) ?? 0) + 1)
for (const [name, n] of counts) if (n > 1) problems.push(`ran ${n} times: ${name}`)

const modes = report.config?.metadata?.isolation ?? {}
const homes = report.config?.metadata?.isolationHome ?? {}
const bare = tests.filter((t) => !(t.test.annotations ?? []).some((a) => a.type === 'isolation')).map(named)
if (bare.length) problems.push(`${bare.length} test${bare.length === 1 ? '' : 's'} carr${bare.length === 1 ? 'ies' : 'y'} no isolation annotation: ${some(bare)}`)
const placed = new Set()
for (const t of tests) {
  const mode = modes[t.file]
  if (!mode) {
    placed.add(`${t.file} has no isolation mode in the report's metadata`)
  } else if (mode === 'alone') {
    if (t.project !== homes[t.file]) placed.add(`${t.file} is alone on the ${homes[t.file] ?? 'unnamed'} home but ran in the ${t.project} project`)
  } else if ((mode === 'serial') !== STATEFUL.test(t.project)) {
    placed.add(`${t.file} is ${mode} but ran in the ${t.project} project`)
  } else if (Object.values(homes).includes(t.project)) {
    placed.add(`${t.file} is ${mode} but ran in the ${t.project} project, a home of its own for other specs`)
  }
}
problems.push(...placed)

const alone = Object.keys(modes).filter((spec) => modes[spec] === 'alone').sort()
for (const spec of alone) {
  if (!homes[spec]) problems.push(`${spec} is alone but the report's metadata names no home for it`)
  else if (!tests.some((t) => t.project === homes[spec] && t.file === spec)) problems.push(`${spec} did not run on its own home (${homes[spec]})`)
}

const serial = Object.keys(modes).filter((spec) => modes[spec] === 'serial').sort()
const stateful = tests.filter((t) => STATEFUL.test(t.project))
if (!stateful.length) {
  problems.push('the stateful run is not in this report: no test ran in the stateful, stateful-again-1 or stateful-again-2 project')
} else {
  for (const spec of serial) {
    if (!stateful.some((t) => t.project === 'stateful' && t.file === spec)) problems.push(`${spec} did not run in the stateful project`)
    const again = new Set(stateful.filter((t) => AGAIN.test(t.project) && t.file === spec).map((t) => t.project))
    if (!again.size) problems.push(`${spec} ran in no stateful-again-* project`)
    else if (again.size > 1) problems.push(`${spec} ran in ${again.size} stateful-again projects: ${[...again].sort().join(', ')}`)
  }
}

const again = stateful.filter((t) => AGAIN.test(t.project))
const seeds = [...new Set(again.flatMap((t) => (t.test.annotations ?? []).filter((a) => a.type === 'stateful-seed').map((a) => a.description)))].sort()
const order = [...new Set(
  [...again].sort((a, b) => Number(a.project.split('-').pop()) - Number(b.project.split('-').pop())).map((t) => t.file),
)]
const unseeded = again.filter((t) => !(t.test.annotations ?? []).some((a) => a.type === 'stateful-seed')).map(named)
if (unseeded.length) problems.push(`${unseeded.length} second-pass test${unseeded.length === 1 ? '' : 's'} carr${unseeded.length === 1 ? 'ies' : 'y'} no stateful-seed: ${some(unseeded)}`)
if (seeds.length > 1) problems.push(`the second pass names ${seeds.length} seeds (${seeds.join(', ')}), and one run draws its order from one`)
if (seeds.length === 1) {
  const meta = report.config?.metadata ?? {}
  if (String(meta.statefulSeed ?? '') !== seeds[0]) {
    problems.push(`metadata.statefulSeed is ${meta.statefulSeed ?? 'unset'}, and the second pass ran in seed ${seeds[0]}'s order`)
  }
  const said = Array.isArray(meta.statefulOrder) ? meta.statefulOrder : []
  if (said.join() !== order.join()) {
    problems.push(`metadata.statefulOrder is ${said.join(' → ') || 'unset'}, and the second pass ran ${order.join(' → ')}`)
  }
}

// Each pass's results: when its first test started, and when its last one ended.
const T0 = Date.parse(report.stats?.startTime ?? '') || 0
const secs = (ms) => `${((ms - T0) / 1000).toFixed(1)} s`
const runs = (t) => (t.test.results ?? []).filter((r) => r.startTime).map((r) => {
  const start = Date.parse(r.startTime)
  return { start, end: start + (r.duration ?? 0) }
})
const passes = ['stateful', ...new Set(again.map((t) => t.project).sort((a, b) => Number(a.split('-').pop()) - Number(b.split('-').pop())))]
const ended = new Map(passes.map((p) => [p, Math.max(...stateful.filter((t) => t.project === p).flatMap(runs).map((r) => r.end))]))
for (const [i, pass] of passes.entries()) {
  for (const ahead of passes.slice(0, i)) {
    const end = ended.get(ahead)
    if (!Number.isFinite(end)) continue
    const early = stateful
      .filter((t) => t.project === pass)
      .flatMap((t) => runs(t).filter((r) => r.start < end).map((r) => `${named(t)} at ${secs(r.start)}`))
    if (early.length) problems.push(`${pass} started before ${ahead} ended (at ${secs(end)}): ${some(early)}`)
  }
}

if (problems.length) fail(problems)

const projects = new Set(tests.map((t) => t.project))
console.log(
  `✓ one run: ${tests.length} tests across ${projects.size} projects, each once and where its isolation mode allows` +
    (alone.length ? `, ${alone.join(', ')} each on a home of its own;` : ';') +
    ` the stateful set ran (${serial.join(', ')}), then its second pass in seed ${seeds[0]}'s order: ${order.join(' → ')},` +
    ' each pass after the one before it had ended',
)
