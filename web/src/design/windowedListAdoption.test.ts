import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

// ── Windowing-adoption ratchet (DSC-13, resuming SM-3) ──────────────────────
// The INVERSE of primitiveAdoption.test.ts. That ratchet counts bespoke chrome and
// holds it DOWN; this one counts surfaces that adopted `ui/WindowedList` and holds it
// UP, so the measured improvement cannot quietly leak back out — a surface that drops
// the primitive and returns to rendering every row turns CI red.
//
// Same idiom as the DSC-3 rail on purpose: count-based, source-TEXT, no browser, runs
// in the existing CI `web` vitest job alongside tokenLint-strict.
//
// 🔑 THE MEASUREMENT LIVES BESIDE THE COUNT, in windowedListAdoption.baseline.json —
// before/after on a real 5,000-row store under a 4x CPU throttle. A ratchet on a number
// nobody can trace back to an observation is just a number.
//
// 🪤 THE ADOPTION IS THE POINT, NOT THE FILE. `done_when` says "ADOPTED at the surfaces
// a real library actually gets long in … rather than added once and left unused", so
// this test asserts the five named surfaces individually rather than only a total: a
// total can be held up by five adoptions at surfaces nobody's list ever gets long in.

const SRC = join(process.cwd(), 'src')
const BASELINE = join(SRC, 'design', 'windowedListAdoption.baseline.json')

interface Baseline { adopters: number; surfaces: string[]; before: { samples: unknown[] }; after: unknown }
const base: Baseline = JSON.parse(readFileSync(BASELINE, 'utf8'))

function walk(dir: string, out: string[] = []): string[] {
  for (const e of readdirSync(dir)) {
    const p = join(dir, e)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.tsx$/.test(e) && !/\.test\.tsx$/.test(e)) out.push(p)
  }
  return out
}

/** Files that render a `<WindowedList` (the primitive's own file and its tests excluded
 *  by construction — it lives in a .tsx under ui/ and is matched by the OPENING TAG,
 *  which its own definition does not contain). */
const adopters = walk(SRC)
  .filter((p) => /<WindowedList[\s>]/.test(readFileSync(p, 'utf8')))
  .map((p) => relative(SRC, p).split(/[\\/]/).join('/'))
  .sort()

describe('windowing adoption ratchet (the window may only spread, never retreat)', () => {
  it(`at least ${base.adopters} surfaces render <WindowedList>`, () => {
    expect(
      adopters.length,
      `A surface dropped ui/WindowedList (${adopters.length} < ${base.adopters}). Long lists degrade `
        + `as they grow — that is measured, not asserted (see windowedListAdoption.baseline.json). `
        + `If a surface genuinely no longer needs it, lower "adopters" AND remove it from `
        + `"surfaces" in the baseline, in the same commit, with the reason in the plan's Execution log. `
        + `Live: ${adopters.join(', ')}`,
    ).toBeGreaterThanOrEqual(base.adopters)
  })

  it('every surface the baseline names still adopts it, by name', () => {
    const missing = base.surfaces.filter((s) => !adopters.includes(s))
    expect(
      missing,
      `These surfaces are recorded as windowed but no longer render <WindowedList>: ${missing.join(', ')}. `
        + `An adoption count that stays flat while a NAMED long-list surface drops out is the `
        + `"added once and left unused" failure DSC-13 exists to prevent.`,
    ).toEqual([])
  })

  it('the five surfaces the atom names are the five that adopted it', () => {
    // knowledge items, sessions, runs, inbox, logs — verbatim from `done_when`.
    for (const [surface, file] of [
      ['knowledge items', 'pages/knowledge/KnowledgeListPage.tsx'],
      ['sessions', 'pages/ChatPage.tsx'],
      ['runs', 'pages/workflows/WorkflowsListPage.tsx'],
      ['inbox', 'pages/inbox/InboxPage.tsx'],
      ['logs', 'pages/settings/DiagnosticsPanel.tsx'],
    ] as const) {
      expect(adopters, `${surface} (${file}) must window`).toContain(file)
    }
  })

  it('every adopter DECLARES its row-height constraint rather than assuming one', () => {
    // The atom allows either variable heights OR a declared uniform constraint — it does
    // not allow silence. `rowHeights` is a required prop so tsc already forces this; the
    // rail is here because the honest value is a judgment tsc cannot check.
    const undeclared: string[] = []
    for (const rel of adopters) {
      const text = readFileSync(join(SRC, rel), 'utf8')
      // One declaration per <WindowedList in the file.
      const opens = (text.match(/<WindowedList[\s>]/g) ?? []).length
      const declared = (text.match(/rowHeights=["{]/g) ?? []).length
      if (declared < opens) undeclared.push(`${rel} (${opens} lists, ${declared} declarations)`)
    }
    expect(undeclared, `rowHeights must be stated per list: ${undeclared.join('; ')}`).toEqual([])
  })

  it('every adopter states a find-in-page alternative', () => {
    // Ctrl+F cannot see un-rendered rows and that is unfixable, so the clause allows "a
    // stated alternative" — and this is what stops the statement being an empty string.
    const bad: string[] = []
    for (const rel of adopters) {
      const text = readFileSync(join(SRC, rel), 'utf8')
      const opens = (text.match(/<WindowedList[\s>]/g) ?? []).length
      const hints = [...text.matchAll(/findHint=(?:"([^"]*)"|\{["'`]([^"'`]*)["'`]\})/g)]
        .map((m) => m[1] ?? m[2] ?? '')
        .filter((h) => h.trim().length > 20)
      if (hints.length < opens) bad.push(`${rel} (${opens} lists, ${hints.length} substantive hints)`)
    }
    expect(bad, `findHint must name a real affordance: ${bad.join('; ')}`).toEqual([])
  })

  it('the recorded measurement is a real before/after, not a placeholder', () => {
    type S = { rows: number; domRows: number; keystrokeMs: number; frameMedianMs: number; mountMs: number }
    const before = base.before.samples as S[]
    const after = (base.after as { samples?: S[] }).samples
    expect(before.length).toBeGreaterThanOrEqual(4)
    expect(after, 'the "after" half of the measurement must be measured, not promised').toBeDefined()
    expect(after!.length).toBe(before.length)

    // 1. The claim, in one number: rendered rows stop tracking collection size.
    const domRows = after!.map((s) => s.domRows)
    expect(Math.max(...domRows) - Math.min(...domRows)).toBeLessThanOrEqual(4)
    // …and the BEFORE really did track it, so the pair is a real contrast and not two
    // runs of the same build (which is exactly what the first "after" run turned out to
    // be — see the baseline's _measurement note on the editable-install bundle).
    const beforeRows = before.map((s) => s.domRows)
    expect(Math.max(...beforeRows)).toBe(Math.max(...before.map((s) => s.rows)))

    // 2. At the largest N, the three costs that matter fell by more than 4x.
    const worstN = Math.max(...before.map((s) => s.rows))
    const b = before.find((s) => s.rows === worstN)!
    const a = after!.find((s) => s.rows === worstN)!
    expect(a.frameMedianMs).toBeLessThan(b.frameMedianMs / 4)
    expect(a.keystrokeMs).toBeLessThan(b.keystrokeMs / 4)
    expect(a.mountMs).toBeLessThan(b.mountMs / 4)
  })

  it('the ratchet is not vacuous — it really found the primitive in the tree', () => {
    expect(adopters.length).toBeGreaterThan(0)
    expect(readFileSync(join(SRC, 'ui', 'WindowedList.tsx'), 'utf8')).toContain('WINDOWING_THRESHOLD')
  })
})
