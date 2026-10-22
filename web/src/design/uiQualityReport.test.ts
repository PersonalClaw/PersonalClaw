import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

// ── The UI quality check names every check that failed on a surface, in one line (#3941) ──────
//
// `e2e/ui-quality-report.mjs` reads the browser run's merged JSON report and, for each `@ui-quality`
// surface and theme that failed, says every step that failed on it, in ONE line, so a red check
// names the surface's whole damage rather than its first failure. It fails a report with a failed
// surface, and one that measured nothing: no `@ui-quality` test at all, or every one skipped. A flaky
// surface passes and is named, and so is a skipped one.
//
// VACUITY FLOOR. The failing cases sit beside a passing report of the same shape, run by the same
// checker, so a checker that always fails or always passes cannot pass both.

const WEB = process.cwd()
const CHECKER = join(WEB, 'e2e/ui-quality-report.mjs')

type Step = { title: string; error?: { message: string }; steps?: Step[] }
type Test = { status: string; projectName: string; results: { status: string; steps: Step[]; errors?: { message: string }[] }[] }
type Spec = { title: string; tags: string[]; tests: Test[] }

const ok = (): Test => ({ status: 'expected', projectName: 'chromium', results: [{ status: 'passed', steps: [{ title: 'axe' }, { title: 'reduced motion' }] }] })

/** A merged report: two themes of the matrix, one surface each, and one spec of another check. */
function report(edit: (dark: Spec[], light: Spec[], other: Spec[]) => void = () => {}): object {
  const dark: Spec[] = [{ title: 'Home (#/dashboard)', tags: ['@ui-quality'], tests: [ok()] }, { title: 'Chat (#/chat)', tags: ['@ui-quality'], tests: [ok()] }]
  const light: Spec[] = [{ title: 'Home (#/dashboard)', tags: ['@ui-quality'], tests: [ok()] }]
  const other: Spec[] = [{ title: 'the rail stays fixed', tags: [], tests: [{ ...ok(), status: 'unexpected' }] }]
  edit(dark, light, other)
  return {
    suites: [
      { title: 'surfaces.spec.ts', file: 'surfaces.spec.ts', specs: [], suites: [
        { title: 'UI quality: dark theme', file: 'surfaces.spec.ts', specs: dark },
        { title: 'UI quality: light theme', file: 'surfaces.spec.ts', specs: light },
      ] },
      { title: 'sessionMap.spec.ts', file: 'sessionMap.spec.ts', specs: other },
    ],
  }
}

let dir = ''
beforeEach(() => { dir = mkdtempSync(join(tmpdir(), 'ui-quality-report-')) })
afterEach(() => { rmSync(dir, { recursive: true, force: true }) })

function check(planted: object | string, summary = ''): { code: number; out: string } {
  const path = join(dir, 'report.json')
  writeFileSync(path, typeof planted === 'string' ? planted : JSON.stringify(planted))
  try {
    return { code: 0, out: execFileSync('node', [CHECKER, path], { encoding: 'utf8', stdio: 'pipe', env: { ...process.env, GITHUB_STEP_SUMMARY: summary } }) }
  } catch (e) {
    const err = e as { status: number; stdout: string; stderr: string }
    return { code: err.status, out: `${err.stdout}${err.stderr}` }
  }
}

describe('the UI quality check’s report', () => {
  it('passes a report where every @ui-quality surface passed, whatever other specs did', () => {
    const { code, out } = check(report())
    expect(code, out).toBe(0)
    expect(out).toContain('UI quality: 3 of 3 surface checks passed')
  })

  it('🔑 fails a surface and names EVERY check that failed on it, in one line with its theme', () => {
    const { code, out } = check(report((dark) => {
      dark[1].tests[0] = {
        status: 'unexpected', projectName: 'chromium',
        results: [{ status: 'failed', steps: [
          { title: 'axe', error: { message: 'color-contrast' } },
          { title: 'reduced motion' },
          { title: '44 px targets at 390px', error: { message: 'too small' } },
          // An assertion inside a step fails the STEP, which is reported by the matrix's name.
          { title: 'accessibility tree', steps: [{ title: 'expect.toEqual', error: { message: 'differs' } }] },
        ] }],
      }
    }))
    expect(code).toBe(1)
    const lines = out.split('\n').filter((l) => l.startsWith('✗'))
    expect(lines, 'the surface was reported more or less than once').toHaveLength(1)
    expect(lines[0]).toBe('✗ UI quality: dark theme › Chat (#/chat): axe; 44 px targets at 390px; accessibility tree')
    expect(out).toMatch(/::error file=web\/e2e\/surfaces\.spec\.ts,title=UI quality: Chat \(#\/chat\)::UI quality: dark theme: axe; 44 px targets at 390px; accessibility tree/)
    expect(out).toContain('2 of 3 surface checks passed, 1 failed')
  })

  it('names a failure that no step carries by its message’s first line', () => {
    const { code, out } = check(report((_dark, light) => {
      light[0].tests[0] = { status: 'unexpected', projectName: 'chromium', results: [{ status: 'failed', steps: [], errors: [{ message: 'Error: accessibility tree on #/dashboard (light)\n  diff…' }] }] }
    }))
    expect(code).toBe(1)
    expect(out).toContain('✗ UI quality: light theme › Home (#/dashboard): accessibility tree on #/dashboard (light)')
  })

  it('passes a flaky surface and names it', () => {
    const { code, out } = check(report((dark) => { dark[0].tests[0].status = 'flaky' }))
    expect(code, out).toBe(0)
    expect(out).toContain('~ UI quality: dark theme › Home (#/dashboard): flaky, passed on retry')
  })

  it('🔑 fails a report that holds no @ui-quality test, and one it cannot read', () => {
    const none = check(report((dark, light) => { dark.length = 0; light.length = 0 }))
    expect(none.code).toBe(1)
    expect(none.out).toContain('holds no @ui-quality test')
    const unreadable = check('{ not json')
    expect(unreadable.code).toBe(1)
    expect(unreadable.out).toContain('has no report to read')
  })

  it('🔑 fails a report whose every @ui-quality test was skipped: nothing was measured', () => {
    const skip = (spec: Spec) => { spec.tests[0] = { status: 'skipped', projectName: 'chromium', results: [{ status: 'skipped', steps: [] }] } }
    const { code, out } = check(report((dark, light) => { [...dark, ...light].forEach(skip) }))
    expect(code, out).toBe(1)
    expect(out).toContain('0 of 3 surface checks passed, 3 skipped')
    expect(out).toContain('measured no surface')
  })

  it('names each skipped surface, in the log and the step summary, and passes when the rest passed', () => {
    const summary = join(dir, 'summary.md')
    writeFileSync(summary, '')
    const { code, out } = check(report((dark) => {
      dark[1].tests[0] = { status: 'skipped', projectName: 'chromium', results: [{ status: 'skipped', steps: [] }] }
    }), summary)
    expect(code, out).toBe(0)
    expect(out).toContain('2 of 3 surface checks passed, 1 skipped')
    expect(out).toContain('- UI quality: dark theme › Chat (#/chat): skipped')
    expect(readFileSync(summary, 'utf8')).toContain('| UI quality: dark theme | Chat (#/chat) | skipped |')
  })

  it('reads the tag however the reporter wrote it', () => {
    const { code, out } = check(report((dark, light) => { for (const s of [...dark, ...light]) s.tags = ['ui-quality'] }))
    expect(code, out).toBe(0)
    expect(out).toContain('3 of 3')
  })

  it('is the check every @ui-quality spec belongs to', () => {
    for (const spec of ['surfaces.spec.ts', 'surfaceMatrix.control.spec.ts', 'idleCost.spec.ts']) {
      expect(readFileSync(join(WEB, 'e2e', spec), 'utf8'), spec).toContain("tag: '@ui-quality'")
    }
  })
})
