import { describe, expect, it } from 'vitest'
import { titleFloor, titleReserveFor, railCeiling } from './HeaderActions'

// ── An EMPTY title slot is owed nothing ──────────────────────────────────────
//
// `HeaderActions` splits the header's inner width between the title and the control row. Both
// halves of that split have to agree on what the title is owed, and they did not:
// `availableWidth()` reserved `min(leftNatural, titleFloor(inner))` — nothing when the slot is
// empty — while the rail's CEILING subtracted `titleFloor(inner)` unconditionally.
//
// Several headers render `left={undefined}`. `#/chat` on a new chat is one, and 53px of phantom
// title reserve was exactly what its controls were short of. Measured at 390×844 against a live
// seeded gateway:
//
//     inner box                       155px
//     two 40px mode pills need         88px   (railScrollW)
//     rail was capped at               58px   = 155 − 44 (dots) − 53 (phantom title floor)
//     → the permission-mode pill painted out to x=181 and collided with the `…` at x=159.
//       The `…` is a LATER SIBLING, so it won the stacking order and the pill was unclickable:
//       a real mouse hover produced aria-expanded=false and ZERO menu items.
//
// After: `aria-expanded=true` with all four options (Normal / Trust reads / Trust / YOLO), and
// surfaces with unusable header controls went 5 → 4 at 390px. The 4 that remain are the
// already-logged overflowing-control-row taste call, untouched.
//
// WHY THIS TESTS PURE FUNCTIONS AND NOT A RENDER: I wrote it as a render assertion first, and
// it passed against the OLD code — jsdom reports every box as 0, so the entire width
// computation collapses to zeros and no component test can tell the two implementations apart.
// The arithmetic was therefore lifted out of the measure closure so the decision itself is
// observable. A test that cannot fail is worse than no test: it reads as coverage.

describe('titleFloor', () => {
  it('scales with the header and stays inside the legible band', () => {
    // A fixed 96px floor on a phone (~155px inner) leaves the cluster almost nothing and
    // forces overflow far too early — hence ~1/3, clamped.
    expect(titleFloor(155)).toBe(53)
    expect(titleFloor(1000)).toBe(96)   // clamped at the top
    expect(titleFloor(50)).toBe(48)     // clamped at the bottom
  })
})

describe('titleReserveFor', () => {
  it('reserves NOTHING when the slot holds no visible content', () => {
    // The whole defect in one assertion. `naturalWidth` is deliberately non-zero here because
    // that is what a real empty flex slot reports — its own padding and gap — so keying on
    // width instead of content is exactly the trap this guards.
    expect(titleReserveFor({ hasContent: false, naturalWidth: 49, inner: 155 })).toBe(0)
  })

  it('reserves the floor when a wide title IS present', () => {
    // Counterpart direction: the fix must not zero every reserve, or it re-opens the
    // 0px-title-slot defect closed on #/prompts (the page name vanished entirely).
    expect(titleReserveFor({ hasContent: true, naturalWidth: 300, inner: 155 })).toBe(53)
  })

  it('never reserves more than the title actually needs', () => {
    // A short title ("Tasks" ≈ 51px) must not hold back the full floor — the cluster gets the
    // difference, which is why `#/tasks` has slack at 390px while longer titles truncate.
    expect(titleReserveFor({ hasContent: true, naturalWidth: 51, inner: 155 })).toBe(51)
  })
})

describe('railCeiling', () => {
  it('gives a title-less header the width its controls need', () => {
    // 155 − 44 − 0 = 111 ≥ the 88px the two mode pills need, so nothing is clipped.
    const ceiling = railCeiling({ inner: 155, dots: 44, title: 0 })
    expect(ceiling).toBe(111)
    expect(ceiling).toBeGreaterThanOrEqual(88)
  })

  it('reproduces the old starvation when a phantom floor is subtracted', () => {
    // The pre-fix arithmetic, pinned so the regression is legible rather than folklore:
    // 155 − 44 − 53 = 58, which is 30px short of the pills' 88px.
    const starved = railCeiling({ inner: 155, dots: 44, title: titleFloor(155) })
    expect(starved).toBe(58)
    expect(starved).toBeLessThan(88)
  })

  it('still protects a real title from the controls', () => {
    // With a genuine title the ceiling must come DOWN, so the rail cannot eat the page name.
    expect(railCeiling({ inner: 155, dots: 44, title: 53 }))
      .toBeLessThan(railCeiling({ inner: 155, dots: 44, title: 0 }))
  })

  it('never returns a negative cap', () => {
    // A very narrow header must clamp at 0 rather than produce a nonsense max-width.
    expect(railCeiling({ inner: 40, dots: 44, title: 53 })).toBe(0)
  })
})
