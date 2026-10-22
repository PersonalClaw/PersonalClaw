// ── The UI quality check's report: one line per surface, every check that failed on it ───────
//
// The one named UI quality check (`ui-quality` in ci.yml, #3941 with #3811) is three parts: the
// raw-control ratchet, the design detector, and the `@ui-quality` browser matrix, which visits each
// route, substate URL and opened surface once per theme and runs every check on that visit as a
// named step with soft assertions. This reads the browser run's merged JSON report back and says,
// for each surface and theme that failed, EVERY step that failed on it, in one line, so a red check
// names the whole surface's damage rather than its first failure.
//
//   node e2e/ui-quality-report.mjs e2e-report.json
//
// It fails when any `@ui-quality` test failed, and when the report holds none at all or every one
// was skipped (a run that measured nothing must not read as a clean one). A flaky surface passes and
// is named, and so is each skipped one. Counting the
// scans against the route manifest is `assert-no-route-skips.mjs`'s, in the `e2e` job.
//
// Deliberately NOT a *.spec.ts: it is a report checker, not a test.
import { appendFileSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const TAG = 'ui-quality'

/** Every spec in the report, with the file its suite names and the describe titles above it
 *  (the theme is a describe: `UI quality: dark theme`). */
function specsOf(report) {
  const out = []
  const walk = (suites, file, path) => {
    for (const suite of suites ?? []) {
      const here = suite.file ?? file
      // A file's own suite is titled with its path; only describes name a part of the title.
      const titles = suite.title && suite.title !== suite.file ? [...path, suite.title] : path
      for (const spec of suite.specs ?? []) out.push({ ...spec, file: spec.file ?? here, path: titles })
      walk(suite.suites, here, titles)
    }
  }
  walk(report.suites, undefined, [])
  return out
}

/** The spec carries the UI quality tag, however the reporter wrote it. */
function tagged(spec) {
  return (spec.tags ?? []).some((t) => String(t).replace(/^@/, '') === TAG)
}

/** The names of the matrix's steps that failed in one result: each top-level step that, or any
 *  step inside which, carries an error (the steps inside are the assertions' own, not the
 *  matrix's names). Else the first line of each error the result reports, since each matrix
 *  assertion names its step in its message. */
export function failedSteps(result) {
  const broke = (s) => !!s.error || (s.steps ?? []).some(broke)
  const out = (result?.steps ?? []).filter(broke).map((s) => s.title)
  if (out.length) return [...new Set(out)]
  return [...new Set((result?.errors ?? []).map((e) => String(e.message ?? '').split('\n')[0].replace(/^Error:\s*/, '')).filter(Boolean))]
}

/** The check's verdict over one JSON report: every failing surface with its failed steps. */
export function uiQualityVerdict(report) {
  const rows = []
  for (const spec of specsOf(report).filter(tagged)) {
    for (const test of spec.tests ?? []) {
      const results = test.results ?? []
      const last = results[results.length - 1]
      rows.push({
        title: spec.title,
        file: spec.file,
        group: spec.path.join(' › '),
        status: test.status,
        steps: test.status === 'unexpected' ? failedSteps(last) : [],
      })
    }
  }
  return {
    total: rows.length,
    failed: rows.filter((r) => r.status === 'unexpected'),
    flaky: rows.filter((r) => r.status === 'flaky'),
    skipped: rows.filter((r) => r.status === 'skipped'),
  }
}

function main() {
  const path = resolve(process.argv[2] ?? 'e2e-report.json')
  let report
  try {
    report = JSON.parse(readFileSync(path, 'utf8'))
  } catch (e) {
    console.error(`✗ the UI quality check has no report to read: ${path} (${e.message})`)
    process.exit(1)
  }
  const v = uiQualityVerdict(report)
  if (!v.total) {
    console.error(`✗ ${path} holds no @${TAG} test: the browser matrix did not run, so nothing was measured`)
    process.exit(1)
  }
  const lines = []
  for (const r of v.failed) {
    const what = r.steps.length ? r.steps.join('; ') : 'failed outside any named step'
    lines.push(`✗ ${r.group ? `${r.group} › ` : ''}${r.title}: ${what}`)
    // One annotation per surface, naming every check that failed on it.
    // The report names each file from the config's testDir, `web/e2e`.
    console.log(`::error file=web/e2e/${r.file},title=UI quality: ${r.title}::${r.group ? `${r.group}: ` : ''}${what}`)
  }
  const where = (r) => `${r.group ? `${r.group} › ` : ''}${r.title}`
  for (const r of v.flaky) lines.push(`~ ${where(r)}: flaky, passed on retry`)
  for (const r of v.skipped) lines.push(`- ${where(r)}: skipped`)
  // Flaky surfaces passed on a retry, so they count as measured; a skipped one measured nothing.
  const passed = v.total - v.failed.length - v.skipped.length
  const head = `UI quality: ${passed} of ${v.total} surface checks passed` +
    `${v.failed.length ? `, ${v.failed.length} failed` : ''}${v.skipped.length ? `, ${v.skipped.length} skipped` : ''}`
  // A run whose gateway never came up can skip the whole matrix: that must not read as a pass.
  if (!passed && !v.failed.length) lines.push(`✗ the browser run measured no surface: every @${TAG} test was skipped`)
  console.log([head, ...lines].join('\n'))
  if (process.env.GITHUB_STEP_SUMMARY) {
    const rows = [
      ...v.failed.map((r) => [r, r.steps.join('; ') || 'outside any named step']),
      ...v.flaky.map((r) => [r, 'flaky, passed on retry']),
      ...v.skipped.map((r) => [r, 'skipped']),
    ]
    const table = rows.length
      ? ['| Where | Surface | Checks that failed, or why it counts apart |', '|---|---|---|', ...rows.map(([r, what]) => `| ${r.group} | ${r.title} | ${what} |`)].join('\n')
      : 'Every surface passed every check.'
    appendFileSync(process.env.GITHUB_STEP_SUMMARY, `### ${head}\n\n${table}\n`)
  }
  process.exit(v.failed.length || !passed ? 1 : 0)
}

if (import.meta.url === `file://${process.argv[1]}`) main()
