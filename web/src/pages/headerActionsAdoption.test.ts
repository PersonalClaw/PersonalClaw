import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

// ── A header's right slot with MORE THAN ONE control must degrade ────────────
//
// `HeaderActions` is the ONE responsive header cluster (`responsive-header-controls.md`): the
// row sheds label → icon → `…` menu as space shrinks, so its controls stay reachable on a
// phone. A hand-rolled `<div className="flex …">` in `TopBar`'s `right` slot degrades not at
// all — and because that slot is `shrink-0`, it does not even compress. It simply runs off
// the edge, taking the page title's width with it.
//
// Measured at 390×844 on `#/knowledge`, which was the one multi-control page bypassing the
// cluster: its right slot laid out at **651px inside a 155px content box** — 496px of
// overflow, still 364px over at 834px. Conflicts / Regenerate / "Add knowledge" were
// off-screen and the "Knowledge" title was squeezed to **0px**. Through the cluster the same
// row measures 80px at 390px (a view pill + `…`) and 224px at 834px, with every shed action
// reachable from the `…` menu — verified by driving real clicks, not geometry.
//
// This is a SOURCE scan because the invariant is about construction, and jsdom has no layout,
// so the tier math (real width comparisons via ResizeObserver) never runs in a unit test.
// Scanning which primitive each `right={…}` slot reaches for is the honest check.
//
// SCOPE — this rail is deliberately narrow, because a wider one cries wolf:
//
//  · A single control is exempt. It cannot overflow a header on its own — `#/loops` (one 40px
//    button) and `WorkflowDefDetail` (one Run button) both measured **0px overflow, fully
//    reachable** at 390px and 834px. Converging them would be churn.
//  · A `Segmented` counts as a control cluster in its own right, so it must be paired with the
//    cluster whenever it shares the slot.
//  · Counting JSX tags OVERCOUNTS conditional branches, so the rail allows an explicit,
//    justified exemption list rather than pretending the count is the truth.
//    `WorkflowRunDetail` declares 5 QuietButtons but renders at most 4, and only 2 in the
//    state I could actually reach: a terminal run shows Workspace + Fork, which measured
//    **1px overflow, 0 unreachable** at 390px and clean at 834px. Its 4-control mid-run
//    branch needs a live running workflow to observe, and I could not drive one from the
//    seeded fixture — so it is LOGGED as a candidate rather than converted blind. Converting
//    a surface whose defect you have not measured is how a "fix" becomes a regression.

const PAGES = join(process.cwd(), 'src/pages')

/** Every `.tsx` under src/pages. */
function pageFiles(): string[] {
  const out: string[] = []
  const walk = (dir: string) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const p = join(dir, e.name)
      if (e.isDirectory()) { walk(p); continue }
      if (/\.tsx$/.test(e.name) && !/\.test\.tsx$/.test(e.name)) out.push(p)
    }
  }
  walk(PAGES)
  return out
}

/** Extract the balanced `{…}` body of every `right={` prop in a source string. */
function rightSlots(src: string): Array<{ line: number; body: string }> {
  const out: Array<{ line: number; body: string }> = []
  for (const m of src.matchAll(/right=\{/g)) {
    const open = m.index! + m[0].length - 1
    let depth = 0
    let end = open
    for (let i = open; i < Math.min(src.length, open + 4000); i++) {
      if (src[i] === '{') depth++
      else if (src[i] === '}') { depth--; if (depth === 0) { end = i; break } }
    }
    out.push({ line: src.slice(0, m.index!).split('\n').length, body: src.slice(open, end) })
  }
  return out
}

/** Control-ish JSX tags a header slot might render. */
const CONTROL = /<(Button|IconButton|SquareIconButton|QuietButton|Segmented|FilterMenu|Popover|Checkbox)\b/g

/** Slots whose control count is inflated by mutually-exclusive branches, with the measurement
 *  that justifies the exemption. Add to this list only with a driven measurement — never to
 *  make a red go green. */
const EXEMPT: Record<string, string> = {
  'workflows/WorkflowRunDetail.tsx':
    '5 declared, at most 4 rendered (Steer/Pause/Cancel are mid-run only; a terminal run shows ' +
    'Workspace + Fork). Measured on a terminal run at 390px: 1px overflow, 0 unreachable. The ' +
    '4-control mid-run branch needs a live running workflow to observe — logged, not converted.',
}

describe('header right slots use the responsive cluster', () => {
  const files = pageFiles()

  it('scans a real tree (guards against a silently-empty sweep)', () => {
    expect(files.length).toBeGreaterThan(40)
    expect(files.some((f) => f.includes('KnowledgeListPage'))).toBe(true)
  })

  it('every exemption is still real (a stale waiver silently widens the rail)', () => {
    for (const rel of Object.keys(EXEMPT)) {
      const f = join(PAGES, rel)
      expect(files, `exempt file ${rel} no longer exists — drop the entry`).toContain(f)
      const src = readFileSync(f, 'utf8')
      const slots = rightSlots(src).filter((s) => !s.body.includes('HeaderActions'))
      // If it has since adopted the cluster, the waiver is dead weight and must go — otherwise
      // it would keep excusing a file that no longer needs excusing.
      expect(
        slots.some((s) => [...s.body.matchAll(CONTROL)].length >= 2),
        `${rel} no longer has a hand-rolled multi-control slot — remove it from EXEMPT`,
      ).toBe(true)
    }
  })

  it('every multi-control right slot goes through HeaderActions', () => {
    const offenders: string[] = []
    for (const f of files) {
      const src = readFileSync(f, 'utf8')
      if (!src.includes('right={')) continue
      const rel = f.slice(PAGES.length + 1)
      if (rel in EXEMPT) continue
      for (const { line, body } of rightSlots(src)) {
        if (body.includes('HeaderActions')) continue          // canonical
        const n = [...body.matchAll(CONTROL)].length
        // One control cannot overflow a header by itself — see the note above.
        if (n < 2) continue
        offenders.push(`${rel}:${line} — ${n} controls, no HeaderActions`)
      }
    }
    expect(
      offenders,
      'A hand-rolled multi-control header row does not shed labels or overflow into a `…` ' +
        'menu, and TopBar\'s right slot is shrink-0 — so it runs off the edge and takes the ' +
        `page title's width with it.\n${offenders.join('\n')}`,
    ).toEqual([])
  })
})
