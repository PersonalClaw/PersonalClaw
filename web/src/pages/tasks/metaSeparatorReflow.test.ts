import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The tasks meta line separates by gap, and the width that proved it is 320px ────────────────
//
// `MetaLine` rendered its schedule group with a leading `·` guarded by
// `(lead.length > 0 || i > 0) ? '· ' : ''`. That guard tests PRESENCE — "is anything in front of me"
// — and cannot know LINE POSITION, so the moment a schedule item wrapped onto its own line, that
// line began with a bare middle dot.
//
// 🔑 WHY TWO EARLIER PASSES MEASURED THIS ROW AS CLEAN. Measured in a browser on the demo fixture,
// four meta lines carry a schedule item:
//
//     1440px  dotted 4  stranded 0   (h=20 — one line)
//      834px  dotted 4  stranded 0   (h=20)
//      390px  dotted 4  stranded 0   (h=20)  ← the harness's narrowest tier
//      360px  dotted 4  stranded 2   (h=41 — wrapped)
//      320px  dotted 4  stranded 4   (h=41)  ← the width WCAG SC 1.4.10 (Reflow) mandates
//
// So it was correct at every tier `scripts/surfaces.json` sweeps and wrong at the one AA requires.
// `ui/danglingSeparator.test.ts` recorded `#/tasks` as "8 phone, 0 desktop" and §4 row 43 recorded
// "1 of 8 rows at EVERY width, 1920 included"; **neither reproduces on this tree** — the real answer
// is narrower than one and wider than the other, and only a sub-390px measurement finds it.
//
// After: **0 dotted, 0 stranded at all four widths**, with the container's computed `column-gap`
// unchanged at **12px** — the row separates exactly as its identity group always did.
//
// 🪤 THE DETECTOR THAT PRODUCED THOSE NUMBERS HAD TO BE VALIDATED FIRST, because it returned zero
// everywhere on the first run. Injecting a `·` span forced onto its own line flipped exactly one row
// from `stranded: 0` to `stranded: 1` and left the other three alone — a known-true control. Without
// it, "0 stranded" is indistinguishable from a blind probe. Two false signals were also killed:
// counting distinct child `top` values over-reports wrapping (`items-center` gives children of
// different heights different tops on the SAME line — use the row's HEIGHT), and `textContent`
// concatenation hides the gap, so it cannot be read as "the items ran together".
//
// This applies the ruling #2224 established for `#/prompts`: a content separator cannot survive
// wrapping, only a gap can. `#/tasks` is the sibling that PR was compared against.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

/** `MetaLine` only — bounded to the construct, comments stripped so prose cannot satisfy a match. */
function metaLine(): string {
  const src = read('pages/tasks/TasksListPage.tsx')
  const at = src.indexOf('function MetaLine(')
  expect(at, 'MetaLine must still exist').toBeGreaterThan(-1)
  const end = src.indexOf('\nfunction ', at + 1)
  expect(end, 'MetaLine must terminate before the next top-level function').toBeGreaterThan(at)
  return src
    .slice(at, end)
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
}

describe('the tasks meta line separates by gap, not by a glyph', () => {
  it('reads the real meta line (vacuity floor)', () => {
    const meta = metaLine()
    expect(meta.length, 'the slice is empty — every assertion below is vacuous').toBeGreaterThan(400)
    // It must be the line under test: both groups present.
    expect(meta, 'expected the identity group').toMatch(/const lead\b/)
    expect(meta, 'expected the schedule group').toMatch(/const tail\b/)
    expect(meta, 'expected the exit-criteria item').toMatch(/criteria/)
  })

  it('carries no separator glyph at all', () => {
    const meta = metaLine()
    // U+00B7 MIDDLE DOT and the two glyphs most likely to be reached for instead.
    const glyphs = ['·', '•', '–'].filter((g) => meta.includes(g))
    expect(
      glyphs.map((g) => `U+${g.codePointAt(0)!.toString(16).toUpperCase()}`),
      'a separator glyph is back in a flex-wrap meta line. It strands at the start of the wrapped ' +
        'line — 4 of 4 rows did at 320px, the width SC 1.4.10 requires. Separate by `gap-x-*`.',
    ).toEqual([])
  })

  it('and the PRESENCE guard that could not see line position is gone', () => {
    // The specific shape, not just the glyph: this conditional is what made the bug look handled.
    expect(metaLine(), 'the presence-guarded separator must not come back')
      .not.toMatch(/lead\.length > 0 \|\| i > 0/)
  })

  it('still separates its items — the gap is not zero', () => {
    // Deleting the glyph is only correct because the container spaces its children. Measured at
    // 1440px: computed `column-gap` is 12px before and after, so this is the mechanism that took over.
    expect(metaLine(), 'the meta line must keep a horizontal gap').toMatch(/gap-x-m\b/)
    expect(metaLine(), 'and it must still be the wrapping container the measurement assumed')
      .toMatch(/flex-wrap/)
  })

  it('the 320px measurement stays written down, because no swept tier reproduces it', () => {
    // Without this, the next pass re-measures at 1440/834/390, finds nothing, and reasonably
    // concludes the glyph is safe to reintroduce. The harness's narrowest tier is 390.
    const src = read('pages/tasks/TasksListPage.tsx')
    expect(src, 'the reflow width must be named').toMatch(/320px/)
    expect(src, 'and the criterion that makes it in scope').toMatch(/1\.4\.10|Reflow/)
  })

  it('#2224 is no longer described as having a conditional to converge onto', () => {
    // That PR's rail pins the phrase "tests PRESENCE, not line position" in PromptsListPage, and its
    // surrounding claim named THIS guard in the present tense. Removing the guard made the claim
    // false, so the tense moved with it — a cross-file reference that would otherwise rot silently.
    const prompts = read('pages/prompts/PromptsListPage.tsx')
    expect(prompts, "the pinned phrase #2224's rail requires must survive")
      .toMatch(/tests PRESENCE, not line/)
    expect(prompts, 'but it must no longer claim the sibling still guards it that way')
      .toMatch(/USED TO guard|no longer a conditional form/)
  })
})
