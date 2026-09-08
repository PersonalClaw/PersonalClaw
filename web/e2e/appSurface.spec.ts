import { test, expect } from '@playwright/test'
import { gotoRoute, assertShellMounted } from './helpers'
import { MOUSE_ONLY, CLIPPED_RING } from './nonAxeA11yChecks'

// ── The proof that the app-AUTHORED sweep is real ────────────────────────────────────────────────
//
// `app/e2e-ui-fixture` is in `NON_NAV_ROUTES`, so axe and both non-axe detectors already visit it
// with every other route. That route entry on its own proves nothing, for two reasons, and this
// spec exists for exactly those two:
//
//  1. **Both detectors return `[]` on a healthy tree.** The fixture is deliberately clean, so
//     "swept and clean" and "never swept" are the same output. Only asserting the app's own DOM is
//     PRESENT distinguishes them.
//  2. **A failed fixture install degrades into an already-covered surface.** With the app absent,
//     `#/app/<name>` renders the SHELL's "isn't installed" EmptyState — the thing
//     `app/not-a-real-app` covers — and the sweep would pass over it happily while never loading a
//     line of app code. `playwright.config.ts` fails the boot for that case; this is the
//     belt-and-braces check at the page level, where the actual claim lives.
//
// This mirrors the shell's own mount assertion. Without it the route could render a bare sentence
// and the detectors would have no focusable element to judge — indistinguishable from "no defects".

const MARKER = 'e2e-app-surface'

test('the app-authored surface really mounts, and the detectors judge IT', async ({ page }) => {
  await gotoRoute(page, 'app/e2e-ui-fixture')
  await assertShellMounted(page)

  // 1. The app's OWN bundle ran. `[data-testid]` is set by the fixture's `mount()`, so its presence
  //    cannot be produced by the host: the shell's 404 branch and its LoadError render neither.
  const surface = page.locator(`[data-testid="${MARKER}"]`)
  await expect(surface, 'the app bundle did not mount — the sweep would be scanning host chrome')
    .toBeVisible()
  await expect(page.getByRole('heading', { name: 'App-authored surface' })).toBeVisible()

  // 2. It is not a bare sentence: there is a real control, and it can hold focus. This is what the
  //    clipped-ring detector needs in order to have anything to measure.
  const button = page.locator(`[data-testid="${MARKER}-button"]`)
  await expect(button).toBeVisible()
  await button.focus()
  await expect(button, 'the app-authored control cannot take focus').toBeFocused()

  // 3. And the host did NOT fall back to its not-installed branch — the exact silent mode above.
  await expect(page.getByText("isn’t installed")).toHaveCount(0)

  // 4. The detectors, run against this page, judge app-authored DOM and find it clean. Asserting
  //    `[]` here is weak on its own — that is precisely why steps 1-3 come first.
  const mouseOnly = await page.evaluate<string[]>(`(${MOUSE_ONLY})()`)
  const clippedRing = await page.evaluate<string[]>(`(${CLIPPED_RING})()`)
  expect(mouseOnly, 'app-authored mouse-only click targets').toEqual([])
  expect(clippedRing, 'app-authored clipped focus rings').toEqual([])
})
