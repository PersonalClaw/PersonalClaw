import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

// ── The hint-contract population, and why this asserts a FLOOR ──────────────────────────
//
// `ui/forms.tsx`'s docstring states how many hinted publishers render, because "196 call sites pass a
// hint today and none of them has to change" is the argument for putting the id in context rather than
// at 196 call sites. A number carrying an argument has to be true.
//
// 🪤 IT ROTS IN A DAY. That line read **260/229 (Field 118, NumberRow 34)**, recounted 2026-08-27 with
// a depth-tracking scan. Recounted 2026-08-28 — one tick of ordinary feature work later — it is
// **271/236 (Field 120, NumberRow 39)**. Nobody touched the contract; other people shipped features
// that happen to use a hinted row. A second stale copy had drifted much further: `settingsUI`'s `Row`
// comment still said **69**, which was the count from two passes earlier and 10% low.
//
// So an EXACT pin is the wrong rail here. It would red the gate every time someone adds a hinted row —
// a rail that fails on healthy growth is a rail the next person weakens or deletes, and this program has
// already had to repair two rails that mandated a stale value. The floor catches the failures that
// actually matter:
//
//   · the scan breaking (a JSX shape it no longer matches) — which would otherwise read as "clean"
//   · publishers DISAPPEARING, i.e. the contract quietly losing coverage
//
// and it names the command that refreshes the prose, so the docstring's numbers can be re-derived
// rather than guessed at. Floors are set slightly below the measured value so ordinary churn in either
// direction does not red CI; the point is the order of magnitude and the direction, not the digit.
//
// 🪤 AND THE SCAN MUST DEPTH-TRACK. A 600-char window undercounted `Row` at 76 against its true 77,
// because one call site's props are longer than the window — so the "off by one" looked like a
// disagreement between two honest measurements when it was a broken matcher. A tag's props end at its
// own `>` at brace depth 0, and nowhere else.

const SRC = join(import.meta.dirname, '..')

/** Every `.tsx` under `src/`, excluding tests. Anchored on `import.meta.dirname`, not `process.cwd()`:
 *  a cwd-derived root ENOENTs from anywhere but `web/`, which reads as a crash rather than a finding. */
function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const abs = join(dir, entry.name)
    if (entry.isDirectory()) sourceFiles(abs, out)
    else if (entry.name.endsWith('.tsx') && !entry.name.includes('.test.')) out.push(abs)
  }
  return out
}

/** The props of every `<Name …>` in one source string, sliced to that tag's OWN closing `>` at brace
 *  depth 0.
 *
 *  🪤 THE SYNTHETIC GUARD BELOW MUST CALL THIS FUNCTION, not a copy of it. My first draft duplicated
 *  the loop inside the guard's describe block, and mutation-testing caught the consequence: breaking
 *  ONLY the census copy left all nine assertions green, because the guard was proving a private
 *  reimplementation correct while the census silently undercounted. A rail that tests a copy of the
 *  mechanism tests nothing about the mechanism in use. */
function sliceTags(src: string, name: string): string[] {
  const open = new RegExp(`<${name}\\b(?![A-Za-z])`, 'g')
  const out: string[] = []
  for (const m of src.matchAll(open)) {
    let depth = 0
    for (let i = m.index! + m[0].length; i < src.length; i++) {
      const c = src[i]
      if (c === '{') depth++
      else if (c === '}') depth--
      else if (c === '>' && depth === 0) { out.push(src.slice(m.index! + m[0].length, i)); break }
    }
  }
  return out
}

const propsOf = (name: string, files: string[]): string[] =>
  files.flatMap((abs) => sliceTags(readFileSync(abs, 'utf8'), name))

const files = sourceFiles(SRC)
const hinted = (name: string) => propsOf(name, files).filter((p) => /\bhint=/.test(p)).length

/** Measured 2026-08-28. Floors sit just under the reading so ordinary churn does not red the gate. */
const PUBLISHERS = [
  { name: 'Field', measured: 120, floor: 100 },
  { name: 'Row', measured: 77, floor: 65 },
  { name: 'NumberRow', measured: 39, floor: 30 },
] as const

/** Local wrappers that forward a hint into one of the three above. */
const FORWARDERS = [
  { name: 'ToggleRow', measured: 25, floor: 20 },
  { name: 'EnumRow', measured: 3, floor: 2 },
  { name: 'CheckList', measured: 3, floor: 2 },
  { name: 'TextRow', measured: 2, floor: 1 },
  { name: 'StrListField', measured: 2, floor: 1 },
] as const

describe('the scan slices a tag at ITS OWN closing angle bracket', () => {
  // 🪤 SYNTHETIC ON PURPOSE, because the tree cannot be relied on to exercise it and mutation-testing
  // proved the gap: breaking `depth === 0` so the slice stops at the FIRST `>` left every assertion in
  // this file green. The counts merely shifted, and floors loose enough to tolerate growth are loose
  // enough to hide an undercount. A `>` inside a brace expression is the whole hazard — an arrow
  // function, a comparison, a generic — and it is what a naive matcher trips on.
  //
  // These call `sliceTags` — the SAME function the census uses. See its docstring: a guard holding a
  // copy of the loop passed every mutation that broke the real one.
  const slice = sliceTags

  it('an arrow function in the props does not end the tag', () => {
    const src = '<Row onChange={(v) => set(v)} hint="a sentence">x</Row>'
    expect(slice(src, 'Row')).toHaveLength(1)
    expect(slice(src, 'Row')[0], 'the hint must be inside the slice').toMatch(/hint="a sentence"/)
  })

  it('a comparison in the props does not end the tag', () => {
    const src = '<Row label={n > 3 ? "many" : "few"} hint="counts">x</Row>'
    expect(slice(src, 'Row')[0]).toMatch(/hint="counts"/)
  })

  it('a hint AFTER a brace-nested angle bracket is still counted', () => {
    // The undercount this catches: with a first-`>` matcher the slice ends early and `hint=` is missed,
    // so the site scores as unhinted and the population silently shrinks.
    const src = '<Field right={<Tag on={a > b} />} hint="after the nesting">y</Field>'
    expect(slice(src, 'Field')[0]).toMatch(/hint="after the nesting"/)
  })

  it('the name match is exact — Row must not swallow RowGroup', () => {
    const src = '<RowGroup><Row hint="inner">z</Row></RowGroup>'
    expect(slice(src, 'Row'), 'RowGroup is a different component').toHaveLength(1)
  })
})

describe('the hint contract covers as many publishers as its docstring claims', () => {
  it('the scan reads a real tree (vacuity floor)', () => {
    // If this collapses, every count below reads 0 and the floors are what catch it — but say so here
    // too, so the failure names the cause instead of looking like a coverage collapse.
    expect(files.length, 'the .tsx sweep found nothing — the scan root is wrong').toBeGreaterThan(200)
  })

  it('every publisher still carries its population', () => {
    const low: string[] = []
    for (const { name, measured, floor } of PUBLISHERS) {
      const n = hinted(name)
      if (n < floor) low.push(`${name}: ${n} hinted call sites, floor ${floor} (was ${measured} on 2026-08-28)`)
    }
    expect(
      low,
      `a hint publisher lost coverage, or the depth-tracking scan stopped matching its JSX:\n  ${low.join('\n  ')}`,
    ).toEqual([])
  })

  it('every forwarding wrapper still forwards', () => {
    const low: string[] = []
    for (const { name, measured, floor } of FORWARDERS) {
      const n = hinted(name)
      if (n < floor) low.push(`${name}: ${n}, floor ${floor} (was ${measured})`)
    }
    expect(low, `a wrapper stopped forwarding a hint:\n  ${low.join('\n  ')}`).toEqual([])
  })

  it("forms.tsx's docstring states a total in the right neighbourhood, and dates it", () => {
    const doc = readFileSync(join(SRC, 'ui/forms.tsx'), 'utf8')
    const claimed = Number(doc.match(/\*\*(\d+)\*\* hinted publishers render today/)?.[1])
    expect(claimed, 'forms.tsx no longer states a publisher total').toBeGreaterThan(0)
    const actual = [...PUBLISHERS, ...FORWARDERS].reduce((sum, p) => sum + hinted(p.name), 0)
    // ±5%, and the number is measured rather than picked: mutation-testing this rail at ±15% showed it
    // would NOT have caught the defect it was written for — `settingsUI`'s stale **69** against a true
    // 77 is 10.4% off. A band that tolerates the motivating bug is decoration. 5% still absorbs a tick
    // of ordinary growth (271 → 284 is 4.8%), so it fails on rot without failing on health.
    // `npx vitest run src/ui/fieldHintCounts.test.ts` prints the live figure.
    expect(
      Math.abs(claimed - actual) / actual,
      `forms.tsx claims ${claimed} hinted publishers; the scan counts ${actual}. Re-derive the ` +
        `docstring's numbers from this test's per-name counts and re-date the line.`,
    ).toBeLessThan(0.05)
    expect(doc, 'the count must be dated, so a reader knows its vintage').toMatch(/Recounted \*\*20\d\d-\d\d-\d\d\*\*/)
  })

  it("settingsUI's Row comment agrees with the scan, which is where it drifted 10% low", () => {
    const ui = readFileSync(join(SRC, 'pages/settings/settingsUI.tsx'), 'utf8')
    const claimed = Number(ui.match(/\((\d+) hinted rows/)?.[1])
    expect(claimed, "settingsUI's Row comment no longer states a count").toBeGreaterThan(0)
    expect(
      Math.abs(claimed - hinted('Row')) / hinted('Row'),
      `settingsUI says ${claimed} hinted rows; the scan counts ${hinted('Row')}.`,
    ).toBeLessThan(0.05)
  })
})
