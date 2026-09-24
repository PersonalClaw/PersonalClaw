import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A separator glyph cannot survive wrapping ────────────────────────────────────────────────────
//
// `PromptsListPage`'s meta line is `flex flex-wrap` and held `· {r.description}` — the separator
// hard-coded onto the description. A description is the longest thing on the row, so it wraps, and the
// wrapped line then BEGAN with a bare middle dot. Measured against the `demo-home` fixture (39 of 40
// rows carry a description):
//
//     1920px    0 stranded      ← the only width where the bug is invisible
//     1440px    8
//     1024px   33
//      834px   39   every row
//      390px   39   every row
//
// 🪤 THE CANONICAL SIBLING'S GUARD DOES NOT FIX THIS. `TasksListPage`'s `MetaLine` writes
// `(lead.length > 0 || i > 0) ? '· ' : ''`, which is a PRESENCE test — "is anything in front of me at
// all" — not a line-position test. `sourceLabel(r.source)` always renders here, so the conditional form
// evaluates true on all 39 rows and strands all 39. Converging onto it would have looked like a fix and
// changed nothing, which is why this rail asserts the property rather than the canonical spelling.
//
// The property: a wrappable meta item separates by GAP, never by a glyph. The row already did exactly
// that between its first two items (`sourceLabel` and `N vars` have no dot between them) — the
// description was the only one carrying a literal.
//
// 🪤 AND THIS IS SOURCE-LEVEL BECAUSE JSDOM HAS NO LAYOUT. A `getBoundingClientRect` check for "is this
// glyph on a later line than its sibling" measures 0×0 boxes in jsdom and passes against anything. The
// live numbers above are the evidence and live in the PR; what this holds is the mechanism.

const SRC = join(import.meta.dirname, '..', '..')
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

/** The meta line of the prompts row: the `flex-wrap` div and its children. */
function promptsMetaLine(): string {
  const src = strip(readFileSync(join(SRC, 'pages/prompts/PromptsListPage.tsx'), 'utf8'))
  const at = src.indexOf('flex flex-wrap items-center gap-x-m')
  expect(at, "the prompts meta line moved — this rail measures nothing").toBeGreaterThan(-1)
  // To the end of that div: the next `</div>` at the same nesting is enough for a single-level block.
  return src.slice(at, src.indexOf('</div>', at))
}

describe('the prompts meta line separates by gap, not by a glyph', () => {
  it('reads the real meta line (vacuity floor)', () => {
    const meta = promptsMetaLine()
    expect(meta.length, 'the slice is empty — every assertion below is vacuous').toBeGreaterThan(80)
    // It must actually be the line under test: all three items present.
    expect(meta, 'expected the source label').toMatch(/sourceLabel\(/)
    expect(meta, 'expected the var count').toMatch(/vars\.length/)
    expect(meta, 'expected the description').toMatch(/r\.description/)
  })

  it('carries no separator glyph at all', () => {
    const meta = promptsMetaLine()
    // U+00B7 MIDDLE DOT, and the two glyphs most likely to be reached for instead.
    const glyphs = ['·', '•', '–'].filter((g) => meta.includes(g))
    expect(
      glyphs.map((g) => `U+${g.codePointAt(0)!.toString(16).toUpperCase()}`),
      'a separator glyph is back in a flex-wrap meta line. It will strand at the start of the ' +
        'wrapped line — 39 of 39 rows did, at 834px and 390px. Separate by `gap-x-*`.',
    ).toEqual([])
  })

  it('still separates its items — the gap is not zero', () => {
    // Deleting the glyph is only correct because the container spaces its children. Without this,
    // someone could drop `gap-x-m` and the three items would run together with the rail still green.
    expect(promptsMetaLine(), 'the meta line must keep a horizontal gap').toMatch(/gap-x-m\b/)
  })

  it('the sibling it would otherwise be converged onto is recorded as insufficient', () => {
    // Load-bearing comment: without it the next coherence pass reasonably "fixes" this row by adopting
    // `MetaLine`'s conditional — which tests presence, not line position, and strands all 39 again.
    const src = readFileSync(join(SRC, 'pages/prompts/PromptsListPage.tsx'), 'utf8')
    expect(
      src,
      'the note explaining why the canonical conditional does not fix wrapping must stay',
    ).toMatch(/PRESENCE, not line\s*\n?\s*\*?\s*position|tests PRESENCE, not line/)
  })
})
