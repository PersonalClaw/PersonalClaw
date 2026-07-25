import { test } from '@playwright/test'
import { ROUTES, THEMES } from './routes'
import { seedTheme, gotoRoute, expectRouteScreenshot } from './helpers'

// ── Visual-regression baselines — every nav route × both themes ─────────────
// This is the S2/S3 safety rail. Capture baselines BEFORE touching a surface:
//   npm run e2e:update        (regenerates all baselines)
//   npm run e2e               (verifies against baselines — must be ZERO diff)
// A consistency fix that forces a REAL visual change: implement it, run
// e2e:update for that surface, and record the new baseline in the plan's
// Execution log for owner review. Never silently keep/revert a visual change.

for (const theme of THEMES) {
  test.describe(`visual: ${theme} theme`, () => {
    for (const { route, label } of ROUTES) {
      test(`${label} (#/${route})`, async ({ page }) => {
        await seedTheme(page, theme)
        await gotoRoute(page, route)
        await expectRouteScreenshot(page, `${route}-${theme}`)
      })
    }
  })
}
