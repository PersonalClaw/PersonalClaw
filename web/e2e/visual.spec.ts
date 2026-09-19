import { test } from '@playwright/test'
import { ROUTES, VIEW_ROUTES, THEMES } from './routes'
import { seedTheme, gotoRoute, expectRouteScreenshot, assertPristineFlywheel } from './helpers'

// ── Visual-regression baselines — every nav route × both themes ─────────────
// This is the S2/S3 safety rail. Capture baselines BEFORE touching a surface:
//   npm run e2e:update        (regenerates all baselines)
//   npm run e2e               (verifies against baselines — must be ZERO diff)
// A consistency fix that forces a REAL visual change: implement it, run
// e2e:update for that surface, and record the new baseline in the plan's
// Execution log for owner review. Never silently keep/revert a visual change.
//
// ── This spec needs a PRISTINE gateway, and that is a command-level contract ─
// Both commands above run this file as their OWN invocation, and the `@visual`
// tag below is how they select it. That is not tidiness: the specs that drive
// real scripted chat turns write flywheel state into the ONE gateway every spec
// shares, which changes what `#/learning` RENDERS — so a golden captured in a
// mixed run is wrong in a visual-only run and vice versa. `assertPristineFlywheel`
// is the rail that refuses to compare pixels across that boundary; see its
// docstring in `helpers.ts` for the two write paths and the measured diff.

// ── Why this spec buys a bigger budget than its siblings ────────────────────
// A pixel comparison is the strictest consumer of `gotoRoute` in the suite: an
// arrival the a11y sweep would never notice — a bento card's count landing, a
// list resolving from skeleton to rows — is a failed golden here, because it
// moves everything below it. Measured on Darwin: the settings hub alone mounts
// ~30 independently-fetching cards, and a card growing inside a SHORTER grid
// column changes no document height, so only quiescence can see it.
//
// So this spec raises both halves together: a longer `settleDom` cap, and a test
// timeout that can actually contain it (the default 30s cannot — `gotoRoute`
// would spend most of it settling and time out mid-screenshot). Nothing about
// the comparison is loosened: `maxDiffPixelRatio` stays at the config's 0.01.
// The cost is wall-clock on a suite no CI job runs, which is the cheapest thing
// in the budget to spend.
const SETTLE_MS = 20_000

for (const theme of THEMES) {
  // `@visual` is the selector `npm run e2e` uses to run this file separately from the
  // turn-driving specs (`--grep-invert @visual`, then `npm run e2e:visual`). A tag rather
  // than a title regex so renaming a describe cannot silently fold the two back together.
  test.describe(`visual: ${theme} theme`, { tag: '@visual' }, () => {
    test.describe.configure({ timeout: 90_000 })
    // VIEW_ROUTES are a nav page's query-param sub-surfaces (e.g. the knowledge
    // graph). They snapshot under their `id`, since `?`/`=` cannot go in a
    // baseline filename.
    for (const { route, id, label } of [...ROUTES, ...VIEW_ROUTES]) {
      test(`${label} (#/${route})`, async ({ page }) => {
        // FIRST, before the settle. In the wrong invocation this fails for all 40 tests, and
        // paying a up-to-20s quiescence wait each time to report the same invocation error
        // would burn ~13 minutes to say it 40 times. Per-test rather than once per file
        // because `fullyParallel` lets a sibling's turn land between two of these tests, so a
        // one-shot check could pass for the run and still be false by the time #35 captures.
        await assertPristineFlywheel(page)
        await seedTheme(page, theme)
        await gotoRoute(page, route, { settleMs: SETTLE_MS })
        await expectRouteScreenshot(page, `${id ?? route}-${theme}`)
      })
    }
  })
}
