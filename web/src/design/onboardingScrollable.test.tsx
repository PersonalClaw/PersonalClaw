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
    // `min-h-full` is what keeps the centred look while there is room: the column fills the
    // scroller's content area, the steps are centred in a `flex-1` box within it (above the
    // navigation bar), and once the content is taller the column grows and the centring has
    // nothing left to distribute.
    expect(ONB).toMatch(/className="mx-auto flex min-h-full w-full flex-col"/)
    expect(ONB).toMatch(/className="mx-auto flex w-full flex-1 flex-col justify-center pb-2xl"/)
  })

  it('pins the navigation bar inside the column that spans the scroll height', () => {
    // A sticky box moves only within its parent. The bar is the design system's sticky footer,
    // and it has to be a direct child of the full-height column — inside a wrapper of its own
    // height it would have nowhere to stick, and would scroll away with a long step.
    const column = ONB.indexOf('className="mx-auto flex min-h-full w-full flex-col"')
    const bar = ONB.indexOf('<FormFooter>', column)
    expect(column).toBeGreaterThan(-1)
    expect(bar, 'the bar is ui/FormFooter, inside the column').toBeGreaterThan(column)
    // …and the scroller leaves no bottom padding under it, so it sits at the foot of the screen.
    const [box] = scrollBoxes(ONB)
    expect(box).not.toMatch(/\bpy-/)
    expect(box).not.toMatch(/\bpb-/)
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

  it('still has the skip control in the bar at the foot of every non-final step', () => {
    // The control the measurement used as the bottom edge. If it stops existing the numbers above
    // stop describing anything, so the rail keeps a hold on it.
    //
    // It moved, at the owner's request, from a centred link under the step into the navigation
    // bar — still the last region in the panel, and now on screen without scrolling. The labels
    // are short ("Skip the rest of setup", or "Skip setup for now" on the first step) and the
    // consequence is a caption with the content. What this rail cares about is unchanged — a skip
    // control, gated to the non-final steps, at the bottom of the panel — so it pins the gate, both
    // labels, and that they sit in the bar.
    const bar = ONB.slice(ONB.indexOf('<FormFooter>'), ONB.indexOf('</FormFooter>'))
    expect(bar).toMatch(/\{step !== 'ready' && \(/)
    expect(bar).toMatch(/'Skip setup for now' : 'Skip the rest of setup'/)
  })
})
