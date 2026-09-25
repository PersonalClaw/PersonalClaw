import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── First run must be reachable at a short viewport, at BOTH edges of the scroll box ───────────
//
// The owner, on the installed app: "onboarding page can get pretty long and is not scrollable.
// This experience needs to be made much better."
//
// The box already had `overflow-y-auto`, which is why this reads as impossible until you measure
// it. Two independent mistakes put content outside the scrollable range:
//
//  1. `items-center` ON the scroller. When the flex item is taller than the box,
//     `align-items: center` distributes the overflow to BOTH ends, and the part past the START
//     edge cannot be reached: `scrollTop` clamps at 0 and `scrollHeight` does not count it.
//  2. the hero (claw mark + `<h1>Welcome to PersonalClaw` + subtitle) was `absolute bottom-full`,
//     i.e. out of flow and ABOVE the flow origin — outside the scrollable range at any height.
//
// Measured in Chrome on a fresh home at 1280×700, step 2 ("Bring your setup over", the longest
// step), BEFORE → AFTER:
//
//   scrollTop 0 (top of range)   h1 at -201.5px  (invisible, unreachable)  →  h1 at +140px
//   scrollTop max                "Skip setup" bottom at 800.5px (> 700)    →  at 676px (in view)
//   scrollHeight / clientHeight  825 / 700, content spanning ~1100         →  1166 / 700
//
// and at 1024×600 after the fix: h1 at +140 at the top, "Skip setup" bottom at 576 (< 600). The
// last control is also focus-reachable: focusing it scrolled the box to 466 and put it at 650–676.
//
// ⚠️ WHY THIS RAIL IS STRUCTURAL AND NOT GEOMETRIC. jsdom computes NO layout — every
// `clientHeight`/`scrollHeight` is 0 and every `getBoundingClientRect()` is all zeros — so the
// measurement above cannot be reproduced in vitest, and a test that asserted on those numbers
// would pass on the broken build too. The geometry is measured in a real browser (see the numbers
// above, and `make test-e2e` is where a future geometric assertion belongs). What is asserted here
// is the two SHAPES that produced it, which is what a regression would reintroduce.
//
// 🔑 IT IS ALSO A GAP REPORT. Neither existing gate could see this: `design/scrollRegionNamed`
// and `design/overlaySurfaceA11y` never render `Onboarding`, and no gate in `web/` computes
// geometry at all. Unscrollable content is also keyboard-unreachable, so this was an
// accessibility failure with no rail, not merely a layout one.

const SRC = join(process.cwd(), 'src')
const ONB = readFileSync(join(SRC, 'app', 'Onboarding.tsx'), 'utf8')

/** The lines declaring a scroll container inside the onboarding shell. */
function scrollBoxes(source: string): string[] {
  return source
    .split('\n')
    .filter((l) => /className="[^"]*overflow-y-auto/.test(l))
}

describe('the onboarding shell scrolls at every viewport', () => {
  it('declares exactly one scroll container', () => {
    // The floor for every assertion below: a selector that matches nothing passes vacuously.
    expect(scrollBoxes(ONB)).toHaveLength(1)
  })

  it('does not CENTRE on the scroll container itself', () => {
    // `items-center` / `justify-center` on the scroller is mistake 1. The centring belongs on a
    // box inside it, where `justify-content` simply runs out of spare space instead of pushing
    // content past an edge that cannot be scrolled back to.
    const [box] = scrollBoxes(ONB)
    expect(box).not.toMatch(/\bitems-center\b/)
    expect(box).not.toMatch(/\bjustify-center\b/)
  })

  it('centres on an inner box that can only GROW, never overflow the start edge', () => {
    // `min-h-full` is what keeps the centred look while there is room: the box fills the
    // scroller's content area, `justify-center` centres within it, and once the content is taller
    // the box grows and the centring has nothing left to distribute.
    expect(ONB).toMatch(/className="flex min-h-full flex-col items-center justify-center"/)
  })

  it('keeps the hero IN FLOW, so the product title is inside the scrollable range', () => {
    // Mistake 2. Positioning the hero out of flow above the panel put the h1 above the flow
    // origin: not merely below the fold, but outside the scroll range entirely, at every viewport
    // height. Asserted over `className` attributes only — the prose above names the old shape, and
    // matching the whole file would red on this very comment.
    const classNames = [...ONB.matchAll(/className="([^"]*)"/g)].map((m) => m[1])
    expect(classNames.length).toBeGreaterThan(10)  // the selector found the attributes at all
    expect(classNames.filter((c) => /\bbottom-full\b/.test(c))).toEqual([])
    // The hero is still the first thing in the panel, above the stepper.
    const heroAt = ONB.indexOf('<ClawMark size={52} animated blob />')
    const stepperAt = ONB.indexOf('<ol className=')
    expect(heroAt).toBeGreaterThan(-1)
    expect(stepperAt).toBeGreaterThan(-1)
    expect(heroAt).toBeLessThan(stepperAt)
  })

  it('still has the skip link as the last control on every non-final step', () => {
    // The control the measurement used as the bottom edge. If it stops existing the numbers above
    // stop describing anything, so the rail keeps a hold on it.
    //
    // The copy changed when the flow gained a visible Back control beside it: the label is short now
    // ("Skip the rest of setup", or "Skip setup for now" on the first step) and the consequence moved
    // into a caption underneath, which says both what skipping costs and where to resume. What this
    // rail cares about is unchanged — a skip control, gated to the non-final steps, at the bottom of
    // the panel — so it pins the gate and both labels rather than one sentence.
    expect(ONB).toMatch(/\{step !== 'ready' && \(/)
    expect(ONB).toMatch(/'Skip setup for now' : 'Skip the rest of setup'/)
  })
})
