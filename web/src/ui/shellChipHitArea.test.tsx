import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The last two undersized targets in the app, and one of them was on every route ────────────────
//
// Phone-width census (390×844), 16 surfaces, counting only controls that are visible, receive pointer
// events, and have no `opacity:0` / `pointer-events:none` / `aria-hidden` / `inert` ancestor — the
// filters earlier cycles paid for. The result is concentrated rather than spread:
//
//   `DegradedChip`  **25×21 on 16 of 16 routes**   ← shell chrome: one component, every destination
//   ToolsPage's "Discovered in other tools" disclosure  **244×18** (phone AND desktop)
//   everything else                                     0
//
// Both fail SC 2.5.8 on the HEIGHT axis only, and neither can use the spacing exception: the chip sits
// in the shell corner cluster beside a 36px theme toggle and a 28px status dot, and the disclosure is a
// full-width row. Same move as the settings pills and the token reset before them — **grow the hit
// area, leave the drawn control alone**:
//
//   DegradedChip   `min-h-6` → 25×24. Width untouched at 25px, deliberately: this chip is icon-only
//                  below the mobile breakpoint precisely because the shell corner's WIDTH starves every
//                  page header (its own comment records returning ~100px to the page), so a width change
//                  would trade one defect for another.
//   disclosure     `min-h-6 -my-0.5` → 244×24.
//
// Measured after, same probe: **0 undersized controls across all 16 routes.** Pixel cost, phone, both
// themes: **0.021%, bounding box `322,12 25×24`** — the chip's own box and nothing else. Desktop tools:
// **0% identical**. The disclosure's wrapper grew 26→30px with its top pulled up 2px, so content after
// it shifts 4px inside the page's scroll container; it sits far below the fold and no capture moved.
//
// 🪤 THE ONE REMAINING PROBE HIT IS A FALSE POSITIVE, AND IT IS WORTH KNOWING WHY. `#/loops` still
// reports a 14×14 `input` — the Scratch checkbox. Its target is the natively-associated `<label>`, which
// measures **63×24** and contains the input (`labelIsTarget: true`, verified in the DOM). A probe that
// counts inputs rather than their labels will keep reporting it; the fix shipped in cycle ~139 and the
// code says so at the call site.
//
// 🟡 AND THE ONE REAL FAILURE THIS CYCLE COULD NOT FIX IS THE OWNER'S CALL. `#/loops` at 390px still has
// `[serious] target-size` on the Granularity pill, and axe's reason is not size:
//
//     "Target has insufficient size because it is partially obscured
//      (smallest space is 19px by 32px, should be at least 24px by 24px)"
//
// The pill is 101×32. It is the shell's floating corner cluster painting over it — the standing 390px
// header-overlap ruling (`#/loops`, `#/loop`, `#/code`, `#/apps`): at 390px the shell corners reserve
// 68px left + 152px right, leaving the page header 170px for a row whose content measures 495px, so the
// Mode pill (293→411) is off-screen entirely. That needs a decision about how the page header slot and
// the shell corners share width, not a hit-area tweak.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

describe('the degraded chip is a 24px target on every route', () => {
  const src = read('ui/DegradedChip.tsx')

  it('the trigger carries a 24px minimum height', () => {
    expect(src).toMatch(/className=\{`flex min-h-6 items-center gap-1\.5 rounded-pill py-1/)
  })

  it('its width is left alone, because the shell corner width is load-bearing', () => {
    // 25px already clears the floor, and this component is icon-only on mobile SPECIFICALLY to give the
    // page header its width back. Growing it sideways would re-create the defect it was built to fix.
    expect(src, 'still icon-only below the mobile breakpoint').toMatch(/isMobile \? 'px-1\.5' : 'px-2\.5'/)
    expect(src, 'no min-w on the trigger').not.toMatch(/min-w-6/)
  })

  it('keeps the accessible name it needs when icon-only', () => {
    // The hit-area change must not disturb the naming fix that shipped with the icon-only variant.
    expect(src).toMatch(/aria-label=\{isMobile \? summary : undefined\}/)
  })
})

describe("the tools page's discovered-servers disclosure is a 24px target", () => {
  const src = read('pages/tools/ToolsPage.tsx')

  it('grows the box and hands the space back', () => {
    expect(src).toMatch(/className="mb-s flex min-h-6 -my-0\.5 items-center gap-s text-on-surface-low/)
  })

  it('is still the disclosure it was, announcing its state', () => {
    expect(src, 'aria-expanded is what makes it a disclosure').toMatch(/aria-expanded=\{open\}[^>]*className="mb-s flex min-h-6/)
  })
})

describe('the pattern these two joined', () => {
  // Converging on what the app already does, so a future pass finds one idiom and not four.
  const ADOPTERS: [string, RegExp][] = [
    ['pages/loop/LoopComposer.tsx', /inline-flex min-h-6 cursor-pointer items-center/],
    ['pages/dashboard/widgets/kit.tsx', /inline-flex min-h-6 -my-px items-center/],
    ['pages/dashboard/DashboardPage.tsx', /inline-flex min-h-6 -my-0\.5 items-center/],
  ]
  for (const [rel, re] of ADOPTERS) {
    it(`${rel} still uses the grow-the-box idiom`, () => {
      expect(read(rel), 'the idiom moved — reconcile rather than fork it').toMatch(re)
    })
  }

  it('and the rail that recorded why a min-height is wrong for inline TEXT is intact', () => {
    // 🪤 `min-h-6` needs `inline-flex`, which re-rounds a text baseline: on `TextLink` it moved 0.83% of
    // `#/tasks`. Both controls here are already flex boxes, which is why the idiom is safe for them and
    // padding is still correct for text.
    const rail = read('ui/nestedTargetSize.test.tsx')
    expect(rail).toMatch(/a min-height would need inline-flex, which moves the baseline/)
  })
})
