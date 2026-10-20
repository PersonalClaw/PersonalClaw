import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── One shared reset button, twenty instances, two defects ───────────────────────────────────
//
// Finishing the target-size sweep on `#/settings/*` (the ledger's carry-over). Swept eleven settings
// sub-views with the ancestor-aware probe; the population is concentrated, not spread:
//
//   #/settings/design    **21** real violations   ← 20 of them ONE component
//   #/settings            6                        (3 mode pills at 22px, 2 wide-but-short links)
//   #/settings/voice      2
//   account · chat · providers · models · search · prompts · memory · agent   0
//
// The twenty are `TokenControls`' shared `ResetButton`, and it had TWO defects that multiply rather than
// average out across instances:
//
//   • **15×24 measured** — the button WAS the glyph. It sits in a `gap-m` flex row, so SC 2.5.8's spacing
//     exception cannot rescue it either.
//   • **`title="Reset"` twenty times** — a non-null name can still be ambiguous. Every row's reset
//     announced the same word, so a screen-reader user hears "Reset" twenty times with no way to tell
//     which token they are about to revert. The row already knows the label.
//
// Driven on `#/settings/design`, parent worktree vs this one (`grep -c 'title="Reset"'` = 1 there, 0
// here — the check that proves which tree a dev port is serving):
//
//                        before                 after
//   controls              20                     20
//   under 24px            **20**                 **0**
//   distinct names        **1**                  **20**  ("Reset UI zoom", "Reset Font size", …)
//   hit box               15×24                  **24×24**
//   row heights           41/41/52/53/41         41/41/52/53/41   ← unchanged
//
// 🪤 THE PAGE-LEVEL CAPTURE SAID 0% AND WAS MEASURING THE WRONG PIXELS. The token rows sit below the
// fold, and this app is a fixed-shell layout with INTERNAL scrollers — so `--full` returns the same
// 1440×900 frame and a page diff can never see them. Cropped the row itself instead, scrolled into view:
// **0.84% dark / 0.85% light, box 250×17** at the row's right-hand cluster. That is the honest cost, and
// it is worth stating exactly rather than hiding behind a 0%:
//
//   • the glyph is 0.5px left and **2.5px lower** — it is now truly centred in its box, where before the
//     bare button's line-height sat it slightly high;
//   • the value/slider cluster shifts **~1px** left, because `size-6` with `-mx-1` returns 8 of the 9px
//     the button gained. No token spelling lands on exactly 15px, and a pseudo-element hit area would
//     fix the pointer target while still measuring 15px to every automated check — so a 1px reflow is
//     the minimum honest cost of an AA fix here.

const SRC = join(process.cwd(), 'src')
const code = readFileSync(join(SRC, 'ui/TokenControls.tsx'), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the shared token reset is a 24px target with its own name', () => {
  it('clicks 24×24', () => {
    expect(code).toMatch(/className="grid size-6 -mx-1 place-items-center text-on-surface-low/)
  })

  it('hands most of the width back, so the row does not reflow vertically', () => {
    // Row heights measured identical either side (41/41/52/53/41).
    expect(code).toMatch(/-mx-1/)
  })

  it('keeps the 15px glyph — the fix is the hit box, not the design', () => {
    expect(code).toMatch(/<RotateCcw size=\{15\} strokeWidth=\{2\} \/>/)
  })

  it('names WHICH token it resets', () => {
    expect(code).toMatch(/title=\{`Reset \$\{label\}`\}/)
    expect(code, 'the ambiguous shared name must be gone').not.toMatch(/title="Reset"/)
  })

  it('every adopter passes the label — all three row kinds', () => {
    // Color, Select and Scalar rows all render it; a missed one would announce "Reset undefined".
    const passes = [...code.matchAll(/<ResetButton label=\{token\.label\}/g)]
    expect(passes.length, 'ColorControl, SelectControl and ScalarControl').toBe(3)
    expect(code, 'no adopter may render it without a label').not.toMatch(/<ResetButton onReset/)
  })

  it('the label is required, so a future adopter cannot forget it', () => {
    expect(code).toMatch(/function ResetButton\(\{ onReset, label \}: \{ onReset: \(\) => void; label: string \}\)/)
  })

  it('keeps the spin microinteraction', () => {
    // The reason this control is shared at all: one "rewind to default" gesture across every token row.
    expect(code).toMatch(/setSpins\(\(n\) => n - 1\)/)
    expect(code).toMatch(/animate=\{\{ rotate: spins \* 360 \}\}/)
  })
})
