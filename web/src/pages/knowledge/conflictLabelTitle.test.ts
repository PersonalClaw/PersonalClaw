import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The label naming a claim's source is readable when it clips ────────────────────────────────
//
// This panel exists to ask a human which of two sources to trust — the backend route says so in as
// many words, refusing to ship a "resolve" endpoint because "deciding a conflict is a judgement about
// which source to trust, which is the owner's call". So *which document said this* is the question the
// surface is asking, and the label under each claim is the only thing that answers it.
//
// Measured at 390px on three real conflicts: it clips with `title: null` — **203px of the 369px it
// needs** on one side and **332px of 369px** on the other. At 1440px nothing clips (1057–1186px
// available), so a desktop-only sweep sees nothing, and `ux-audit` reported 0 blocking findings at both
// themes AND at 390px because a clipped label is valid, contrasty markup.
//
// 🔑 THIS IS THE THIRD SURFACE WITH THE SHAPE, SO IT WAS MEASURED RATHER THAN GUESSED. After tag names
// and intent goals, a 390px census of **all 55 surfaces** found **21** with a clipped, unrecoverable
// label, totalling **203 elements**, and classifying them is what makes the family actionable:
//
//   131  identifier-ish (clipped < 3x)   -> `title` is the right fix, this element among them
//    67  long prose (clipped >= 3x)      -> a title would be a wall of text; needs clamp/expand
//     5  overflowing containers          -> no `truncate` at all; a scroll concern, not a label
//
// So 72 of the 203 are deliberately NOT this fix. That is why the earlier cycle refused to sweep 237
// untitled elements blindly: the population is heterogeneous, and only measurement separates it.

const SRC = readFileSync(join(process.cwd(), 'src/pages/knowledge/ConflictPanel.tsx'), 'utf8')
const CODE = SRC.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '')

describe("a claim's source label survives truncation", () => {
  it('the truncating label carries its full text in a title', () => {
    expect(CODE).toMatch(
      /<div className="mt-0\.5 truncate text-\[0\.75rem\] text-on-surface-low" title=\{label\}>\{label\}<\/div>/,
    )
  })

  it('title and visible text are the same expression', () => {
    // A title that could drift from its label would name a different document than the one shown —
    // on a surface whose entire purpose is telling two documents apart.
    const m = /title=\{([^}]+)\}>\{([^}]+)\}<\/div>/.exec(CODE)
    expect(m, 'the pair is readable from source').toBeTruthy()
    expect(m![1].trim()).toBe(m![2].trim())
  })

  it('both sides of a conflict get it — the label is rendered once, for both claims', () => {
    // ClaimSide is used twice per conflict; one definition covers left and right. If it were ever
    // duplicated per side, this count is what would notice.
    const sides = [...CODE.matchAll(/<ClaimSide/g)]
    expect(sides.length, 'left and right').toBe(2)
    const defs = [...CODE.matchAll(/function ClaimSide/g)]
    expect(defs.length, 'one definition serving both').toBe(1)
  })

  it('the label still comes from the conflict, not a constant — the vacuity floor', () => {
    // A hard-coded label would satisfy every assertion above while naming nothing.
    expect(CODE, 'left side names the item carrying the conflict')
      .toMatch(/label=\{conflict\.item_title \|\| conflict\.left_item\}/)
    expect(CODE, 'right side names the other document').toMatch(/label=\{conflict\.right_item\}/)
  })

  it('the surface still refuses to pick a winner', () => {
    // The reason the label matters: there is no resolve control, by design. If one ever appears, the
    // reasoning in this file's header needs revisiting rather than silently passing.
    expect(CODE, 'no resolve affordance').not.toMatch(/resolve|Resolve/)
    expect(CODE, 'and the undecidable case says so instead')
      .toMatch(/Both sources carry the same weight/)
  })
})
