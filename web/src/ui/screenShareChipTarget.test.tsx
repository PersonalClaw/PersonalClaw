import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The screen-share indicator is also the OFF SWITCH, so it needs a real hit target ───────────────
//
// `ScreenShareChip`'s own docstring makes the design argument: *"Clicking it stops sharing: the visible
// indicator is also the off switch, so a user who notices the chip never has to hunt for the control
// that clears it."* That makes its target size a privacy control, not a nicety — it is how a user stops
// a screen capture.
//
// Measured in the live app by injecting the chip's exact markup so it inherited the real stylesheet:
// **133×22px**, i.e. **2px under WCAG 2.5.8's 24px floor**, in both themes. Contrast was fine
// (4.53:1 light / 6.51:1 dark on its own 16% warn tint), so height was the whole defect.
//
// 🔑 CONVERGENCE, NOT INVENTION. `ui/DegradedChip` is the near-identical sibling — a `rounded-pill`
// status chip at `text-[0.75rem]` with the same `hover:brightness-110` — and it already carries
// `min-h-6`. `dashboard/widgets/kit` and `DashboardPage` pair `min-h-6` with a negative `-my-*` so the
// row does not grow. This chip now uses `min-h-6 -my-px`: content box 24px, margin box 24 − 2 = **22px**,
// exactly the height it occupied before, so the composer row is unchanged.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

describe('the screen-share chip is hittable', () => {
  it('holds a 24px minimum height', () => {
    expect(read('ui/ScreenShareChip.tsx')).toMatch(/className="inline-flex min-h-6 -my-px shrink-0/)
  })

  it('absorbs the extra 2px so the composer row does not grow', () => {
    // Without the negative margin this becomes a 2px reflow of the row the chip sits in.
    expect(read('ui/ScreenShareChip.tsx')).toMatch(/min-h-6 -my-px/)
  })

  it('is still the button that stops sharing — the target IS the control', () => {
    const code = read('ui/ScreenShareChip.tsx')
    expect(code).toMatch(/aria-label="Sharing your screen with this chat — stop sharing"/)
    expect(code).toMatch(/onClick=\{onStop\}/)
  })

  it('the sibling it converges on still uses the same idiom', () => {
    // Vacuity floor: if DegradedChip stops using `min-h-6`, the precedent cited above is gone and this
    // chip's height should be re-argued rather than silently kept.
    expect(read('ui/DegradedChip.tsx'), 'the near-identical status pill').toMatch(/min-h-6/)
  })
})
