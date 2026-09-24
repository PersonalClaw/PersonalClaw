import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The enable/disable toggle reports like its siblings ─────────────────────────────────────────
//
// `ScheduleDetail` owns a `setErr` surface that every action uses — runNow, dryRun, save,
// openChat — except, until the 2026-09-05 audit, `toggle()`: a failed enable/disable moved
// nothing and said nothing, so a disabled-LOOKING schedule could still be armed and the only
// recourse was to click again. This pin holds the toggle to the file's own contract; it lives
// outside `userActionReported.test.ts` because that census asserts the SHARED reporter
// (`reportingWrite`), and this file's established surface is its local `setErr`.
describe('the schedule enable/disable toggle reports failure', () => {
  it('toggle() catches and routes through setErr', () => {
    const src = readFileSync(join(process.cwd(), 'src', 'pages/schedule/ScheduleDetail.tsx'), 'utf8')
    const at = src.indexOf('async function toggle()')
    expect(at, 'the toggle must exist').toBeGreaterThan(-1)
    const fn = src.slice(at, src.indexOf('async function', at + 10))
    expect(fn, 'a failed enable/disable must reach setErr').toMatch(/catch\s*\(e\)\s*\{\s*setErr\(/)
    expect(fn, 'and still call the API').toContain('api.enableSchedule(')
  })
})
