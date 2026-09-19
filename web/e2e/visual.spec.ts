import { test } from '@playwright/test'
import { ROUTES, VIEW_ROUTES, THEMES } from './routes'
import { seedTheme, gotoRoute, expectRouteScreenshot } from './helpers'

// ── Visual-regression baselines — every nav route × both themes ─────────────
// This is the S2/S3 safety rail. Capture baselines BEFORE touching a surface:
//   npm run e2e:update        (regenerates all baselines)
//   npm run e2e               (verifies against baselines — must be ZERO diff)
// A consistency fix that forces a REAL visual change: implement it, run
// e2e:update for that surface, and record the new baseline in the plan's
// Execution log for owner review. Never silently keep/revert a visual change.

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
  test.describe(`visual: ${theme} theme`, () => {
    test.describe.configure({ timeout: 90_000 })
    // VIEW_ROUTES are a nav page's query-param sub-surfaces (e.g. the knowledge
    // graph). They snapshot under their `id`, since `?`/`=` cannot go in a
    // baseline filename.
    for (const { route, id, label } of [...ROUTES, ...VIEW_ROUTES]) {
      test(`${label} (#/${route})`, async ({ page }) => {
        await seedTheme(page, theme)
        await gotoRoute(page, route, { settleMs: SETTLE_MS })
        await expectRouteScreenshot(page, `${id ?? route}-${theme}`)
      })
    }
  })
}
