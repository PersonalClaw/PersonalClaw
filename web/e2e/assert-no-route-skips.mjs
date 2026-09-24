// ── "every authenticated route is axe-scanned in CI" — asserted, not assumed ─────────────
// A SKIP reads exactly like a PASS in a green job. `npx playwright test e2e/a11y.spec.ts`
// exits 0 whether it scanned 116 routes or skipped 116 of them, so the CI step alone proves
// the job RAN, not that anything was measured. This reads the JSON report back and holds it
// to the manifest:
//
//   · every route-level scan that ran must have PASSED or been retried — never skipped; and
//   · the NUMBER of route-level scans must equal (every declared route) × (every theme).
//
// The expected count is DERIVED from web/e2e/routes.ts rather than hard-coded, so deleting a
// route list — the one edit that would silently shrink the gate's coverage while leaving it
// green — reds here instead of passing quietly. That is the same failure mode
// `routeManifestParity.test.ts` guards on the authoring side; this is the executing side.
//
// Deliberately NOT a *.spec.ts: it is a report checker, not a test. (`_specs()` in
// tests/test_e2e_specs_are_executed.py globs `*.spec.ts`, so this file is correctly invisible
// to the every-spec-runs-in-CI rail.)
import { readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))

const reportPath = resolve(process.argv[2] ?? 'a11y-report.json')

/** Route-level scan titles carry the route in parentheses: `Home (#/dashboard)`. The
 *  interaction tier (`command palette [opened]`) deliberately does NOT, because those MAY
 *  legitimately skip when a fresh home has no row to open — the spec says so itself. */
const ROUTE_TITLE = /\(#\//

function fail(message) {
  console.error(`\n✗ ${message}\n`)
  process.exit(1)
}

// ── expected: what the manifest declares ────────────────────────────────────────────────
function literalBlock(src, header) {
  const block = src.match(new RegExp(`${header}[\\s\\S]*?\\n\\]`))
  if (!block) fail(`could not locate ${header} in web/e2e/routes.ts — re-point this checker`)
  return block[0]
}

const routesSrc = readFileSync(join(HERE, 'routes.ts'), 'utf8')

const counts = {
  ROUTES: [...literalBlock(routesSrc, 'export const ROUTES: RouteEntry\\[\\] = \\[').matchAll(/route: '/g)]
    .length,
  SETTINGS_PANELS: [
    ...literalBlock(routesSrc, 'export const SETTINGS_PANELS = \\[').matchAll(/'[a-z0-9-]+'/g),
  ].length,
  VIEW_ROUTES: [
    ...literalBlock(routesSrc, 'export const VIEW_ROUTES: RouteEntry\\[\\] = \\[').matchAll(/route: '/g),
  ].length,
  NON_NAV_ROUTES: [
    ...literalBlock(routesSrc, 'export const NON_NAV_ROUTES: RouteEntry\\[\\] = \\[').matchAll(/route: '/g),
  ].length,
}

// THEMES is a one-line literal, so it needs its own match rather than literalBlock's
// multi-line shape.
const themesLiteral = routesSrc.match(/export const THEMES = \[(.*?)\]/)
if (!themesLiteral) fail('could not locate THEMES in web/e2e/routes.ts — re-point this checker')
const themes = [...themesLiteral[1].matchAll(/'\w+'/g)].length

for (const [name, n] of Object.entries({ ...counts, THEMES: themes })) {
  if (n === 0) fail(`${name} parsed as EMPTY — this checker would then demand 0 scans and pass vacuously`)
}

const declared = Object.values(counts).reduce((a, b) => a + b, 0)
const expected = declared * themes

// ── actual: what the run reported ───────────────────────────────────────────────────────
let report
try {
  report = JSON.parse(readFileSync(reportPath, 'utf8'))
} catch (err) {
  fail(`could not read the Playwright JSON report at ${reportPath}: ${err.message}\n` +
    `  The a11y step must run with --reporter=…,json and PLAYWRIGHT_JSON_OUTPUT_NAME set.`)
}

const specs = []
;(function walk(suites) {
  for (const suite of suites ?? []) {
    specs.push(...(suite.specs ?? []))
    walk(suite.suites)
  }
})(report.suites)

if (specs.length === 0) fail('the JSON report contains no specs at all — the run measured nothing')

const routeScans = specs.filter((s) => ROUTE_TITLE.test(s.title))
const skipped = routeScans.filter((s) => (s.tests ?? []).some((t) => t.status === 'skipped'))

if (skipped.length > 0) {
  fail(
    `${skipped.length} authenticated route(s) were SKIPPED, not scanned:\n` +
      skipped.map((s) => `    ${s.title}`).join('\n') +
      `\n\n  A skipped route contributes no axe result, so the green job would be claiming\n` +
      `  coverage it does not have. Route scans need no fixture and must never skip — fix the\n` +
      `  route or remove it from the manifest (and say why in EXEMPT_FROM_THE_HARNESS).`,
  )
}

if (routeScans.length !== expected) {
  fail(
    `route scans ran: ${routeScans.length}, manifest declares: ${expected}\n` +
      `    (${declared} routes × ${themes} themes — ` +
      Object.entries(counts).map(([k, v]) => `${k}=${v}`).join(', ') +
      `)\n\n  Fewer means the gate silently stopped covering something; more means a list is\n` +
      `  double-counted. Either way the number CI reports and the number the manifest promises\n` +
      `  have to agree, or "every authenticated route is axe-scanned" is not a checkable claim.`,
  )
}

console.log(
  `✓ ${routeScans.length} authenticated route scans ran (${declared} routes × ${themes} themes), ` +
    `0 skipped — ` +
    Object.entries(counts).map(([k, v]) => `${k}=${v}`).join(', '),
)
