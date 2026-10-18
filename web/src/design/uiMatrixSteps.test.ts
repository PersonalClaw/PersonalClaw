import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { NON_NAV_ROUTES, ROUTES, SETTINGS_ROUTES, SUBSTATE_ROUTES, THEMES, VIEW_ROUTES } from '../../e2e/routes'
import { STEP_NAMES } from '../../e2e/surfaceChecks'
// @ts-expect-error: a plain .mjs module the report checker runs under node, with no declarations.
import { stepNamesFromSource } from '../../e2e/uiMatrixSteps.mjs'

// ── The report checker reads the matrix's steps as the matrix runs them ──────────────────────────
//
// `e2e/assert-no-route-skips.mjs` holds every route scan in the JSON report to the matrix's step
// list, and reads that list from `surfaceChecks.ts`'s source (it runs under plain node). This holds
// its reading to `STEP_NAMES` itself, and runs the checker on planted reports: a whole one passes,
// and one scan short of a step, a skipped scan and a missing scan each fail it.

const WEB = process.cwd()
const CHECKER = join(WEB, 'e2e/assert-no-route-skips.mjs')
const SURFACES = [...ROUTES, ...SETTINGS_ROUTES, ...VIEW_ROUTES, ...NON_NAV_ROUTES, ...SUBSTATE_ROUTES]

type Spec = { title: string; file: string; tests: { status: string; results: { steps: { title: string }[] }[] }[] }

function report(edit: (specs: Spec[]) => void = () => {}): object {
  const specs: Spec[] = THEMES.flatMap(() =>
    SURFACES.map(({ route, label }) => ({
      title: `${label} (#/${route})`,
      file: 'e2e/surfaces.spec.ts',
      tests: [{ status: 'expected', results: [{ steps: STEP_NAMES.map((title) => ({ title })) }] }],
    })),
  )
  edit(specs)
  return { suites: [{ specs }] }
}

/** Each test's scratch folder for its planted reports, under the OS temp directory. */
let dir = ''
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'ui-matrix-report-'))
})
afterEach(() => {
  rmSync(dir, { recursive: true, force: true })
})

/** The checker's exit code and output on a planted report. */
function check(planted: object): { code: number; out: string } {
  const path = join(dir, `report-${Math.random().toString(36).slice(2)}.json`)
  writeFileSync(path, JSON.stringify(planted))
  try {
    return { code: 0, out: execFileSync('node', [CHECKER, path], { encoding: 'utf8', stdio: 'pipe' }) }
  } catch (e) {
    const err = e as { status: number; stdout: string; stderr: string }
    return { code: err.status, out: `${err.stdout}${err.stderr}` }
  }
}

describe('the report checker holds every scan to the whole matrix', () => {
  it("reads surfaceChecks.ts's steps exactly as STEP_NAMES lists them", () => {
    expect(stepNamesFromSource(readFileSync(join(WEB, 'e2e/surfaceChecks.ts'), 'utf8'))).toEqual([...STEP_NAMES])
  })

  it('passes a report with one whole scan per surface and theme', () => {
    const { code, out } = check(report())
    expect(code, out).toBe(0)
    expect(out).toContain(`${SURFACES.length * THEMES.length} authenticated route scans ran`)
    expect(out).toContain(`all ${STEP_NAMES.length} steps`)
  })

  it('fails a scan that did not run one step', () => {
    const { code, out } = check(
      report((specs) => {
        specs[3].tests[0].results[0].steps = specs[3].tests[0].results[0].steps.filter((s) => s.title !== 'reduced motion')
      }),
    )
    expect(code).toBe(1)
    expect(out).toContain('missing reduced motion')
  })

  it('fails a skipped scan, and a scan that is not there', () => {
    const skipped = check(report((specs) => (specs[0].tests[0].status = 'skipped')))
    expect(skipped.code).toBe(1)
    expect(skipped.out).toContain('SKIPPED')
    const short = check(report((specs) => specs.pop()))
    expect(short.code).toBe(1)
    expect(short.out).toContain('manifest declares')
  })
})
