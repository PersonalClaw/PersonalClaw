import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── Finishing the target-size sweep: ten more surfaces, one more shape ─────────────────────
//
// #1181 fixed `#/tasks` and left ~30 surfaces unmeasured. Swept ten of them with the
// ancestor-aware probe (a control inside a larger clickable surface can never use SC 2.5.8's
// spacing exception, however far its nearest sibling is):
//
//   /#/loops /#/workflows /#/code /#/chat /#/skills /#/prompts /#/learning /#/notifications   clean
//   /#/tools        1 under-24 target, genuinely exempt (not nested, ≥24px clearance)          pass
//   /#/files       13 × 20×20  "Actions for <name>"  — each inside its own clickable row       FAIL
//
// One control, one file, one instance per row. After: **0 real violations across all ten.**
//
// 🪤 THE WRINKLE THIS ONE HAD THAT THE OTHERS DID NOT: it is **absolutely positioned**
// (`absolute right-1 top-1/2 -translate-y-1/2`). Growing a flow-positioned button and handing the
// space back with `-m-0.5` leaves its content where it was; growing an absolutely-positioned one
// moves the content, because the offset pins an EDGE rather than the centre. `size-6` with
// `right-0.5` absorbs the extra 4px, and the measurement proves it:
//
//                     before          after
//   hit box           20×20 @1401,278  **24×24** @1399,276
//   glyph centre      1411, 287.8      **1411, 287.8**  ← unchanged
//   row               388×32           388×32
//
// 🔑 So the recipe is not one class list but one INVARIANT: **grow the hit box, leave the paint
// where it is** — and which classes achieve that depends on how the control is positioned.

describe('the file-tree row-action trigger', () => {
  const src = readFileSync(join(process.cwd(), 'src/pages/files/browse/FileTree.tsx'), 'utf8')

  it('clicks 24px', () => {
    expect(src).toMatch(/grid size-6 place-items-center rounded text-on-surface-low/)
  })

  it('absorbs the extra width in its offset, so the glyph does not move', () => {
    // `right-1` + `size-6` would have shifted the glyph 2px left. Measured: centre 1411 → 1411.
    expect(src).toMatch(/absolute right-0\.5 top-1\/2 -translate-y-1\/2 grid size-6/)
    expect(src, 'the old offset would move the paint').not.toMatch(/absolute right-1 top-1\/2 -translate-y-1\/2 grid size-6/)
  })

  it('keeps the 13px glyph — the fix is the hit box, not the design', () => {
    expect(src).toMatch(/<MoreHorizontal size=\{13\} \/>/)
  })

  it('stays hidden until the row is hovered or focused', () => {
    // The control is `opacity-0` until hover, which is WHY the surface capture is 0%: nothing about
    // this change is visible at rest. Worth pinning so a future pass does not "fix" the reveal.
    expect(src).toMatch(/opacity-0 transition-opacity[^"]*focus-visible:opacity-100 group-hover\/row:opacity-100/)
  })
})
