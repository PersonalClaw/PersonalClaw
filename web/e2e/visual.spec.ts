import { expect, test, type Page } from '@playwright/test'
import { ROUTES, VIEW_ROUTES, THEMES, ORB_STATE_SHOTS, REDUCED_MOTION_SHOTS, type ReducedMotionOpen } from './routes'
import {
  seedTheme, gotoRoute, expectRouteScreenshot, assertPristineFlywheel, settleDom, settleEntranceAnimations,
} from './helpers'

// ── Visual-regression baselines — every nav route × both themes ─────────────
// This is the safety rail. Capture baselines BEFORE touching a surface:
//   npm run e2e:update        (regenerates all baselines)
//   npm run e2e               (verifies against baselines — must be ZERO diff)
// A consistency fix that forces a REAL visual change: implement it, run
// e2e:update for that surface, and say in the change's description which baselines
// moved and why (e2e/README.md). Never silently keep/revert a visual change.
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
// So this spec raises every stage together, plus a test timeout that can actually
// contain them (the default 30s cannot — `gotoRoute` would spend most of it
// settling and time out mid-screenshot). Nothing about the comparison is
// loosened: `maxDiffPixelRatio` stays at the config's 0.01. The cost is
// wall-clock on a suite no CI job runs, which is the cheapest thing in the
// budget to spend.
//
// 🕐 EVERY NUMBER HERE IS A BUDGET, NEVER A TOLERANCE — how long a PRECONDITION
// may take to be satisfied, not how much difference the comparison accepts. The
// sweeps' caps (6s chrome / 8s settle / 2s fades) are sized to fit inside
// Playwright's default 30s test timeout, which is the wrong trade for a pixel
// comparison: there an exhausted stage does not degrade the measurement, it
// FABRICATES one. Raising them is what lets a loaded host make this rail SLOWER
// instead of making it report a different answer, and `SettleReport` turns the
// leftover case into a named failure rather than a drift ratio about a frame
// that does not exist once the page settles.
//
// `settleMs` is the one sized from a per-route measurement rather than a
// multiple. With 2s of latency injected on every `/api/**` response — which
// emulates a starved host without loading the machine — the slowest route to
// come fully to rest was `#/tools` at ~6.3s, and `#/dashboard`/`#/settings`
// cleared their last skeleton at ~5.8s/~6.0s while raising 45 and 32 loading
// affordances on the way. 45s is that worst case with an order of magnitude of
// headroom for a genuinely saturated box, not a round number.
const BUDGET = {
  chromeMs: 30_000,
  settleMs: 45_000,
  fadeMs: 10_000,
} as const

for (const theme of THEMES) {
  // `@visual` is the selector `npm run e2e` uses to run this file separately from the
  // turn-driving specs (`--grep-invert @visual`, then `npm run e2e:visual`). A tag rather
  // than a title regex so renaming a describe cannot silently fold the two back together.
  test.describe(`visual: ${theme} theme`, { tag: '@visual' }, () => {
    // Must contain the BUDGET sum above (85s) plus a screenshot on the very host that is the
    // reason the budgets are large. 90s could not: it was barely more than the settle it was
    // meant to wrap, so a starved route died mid-capture — a timeout that reads as a broken
    // test rather than as a busy machine.
    test.describe.configure({ timeout: 300_000 })
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
        const settle = await gotoRoute(page, route, BUDGET)
        await expectRouteScreenshot(page, `${id ?? route}-${theme}`, settle)
      })
    }
  })
}

// ── The agent orb's states: every phase at 20 and 64 px ─────────────────────
// Settings → Design shows them on request (`pages/settings/AgentOrbStates.tsx`), and this
// photographs that gallery alone, in both themes, with motion and under reduced motion. The
// orb is a canvas drawn from the page's clock, so a moving state is a new picture every frame:
// the clock is installed before the page loads and paused at one instant before the gallery is
// shown, so every orb paints its first frame at that instant and, with no timer firing, paints no
// other. (Paused after the gallery is shown, an orb would keep the frame it last painted: it paints
// at most 30 a second, on a timer, and the frame a timer due at the pause asks for comes after it.)
// Under reduced motion each state is its one painted frame anyway. So the comparison is exact
// (`maxDiffPixels: 0`), stricter than the routes': measured on Darwin, the unpaused gallery differs
// from itself by 1,100-1,400 px, under the config's 1% and so invisible to it, and the paused one by
// none. The titles carry no `(#/`: these are not route scans, so `assert-no-route-skips.mjs` does
// not count them.
const ORB_CLOCK_START = new Date('2026-01-01T09:00:00Z')
const ORB_CLOCK_PAUSE = new Date('2026-01-01T09:05:00Z')

for (const { key, theme, reducedMotion } of ORB_STATE_SHOTS) {
  test.describe(`visual: ${key}`, { tag: '@visual' }, () => {
    test.describe.configure({ timeout: 300_000 })
    // Tall enough for the whole gallery: the settings pane scrolls inside its own container, so
    // an element screenshot of anything below the viewport comes back blank.
    test.use({ reducedMotion, viewport: { width: 1280, height: 2400 } })
    test('every phase at 20 and 64 px', async ({ page }) => {
      await seedTheme(page, theme)
      await page.clock.install({ time: ORB_CLOCK_START })
      const settle = await gotoRoute(page, 'settings/design', BUDGET)
      expect(settle.exhausted, `settings/design never reached rest: ${settle.exhausted.join(', ')}`).toEqual([])
      await page.clock.pauseAt(ORB_CLOCK_PAUSE)
      await page.getByRole('button', { name: 'Show its states' }).click()
      const gallery = page.locator('[data-orb-gallery]')
      await expect(gallery.locator('[data-agent-orb]')).toHaveCount(30)
      await expect(gallery).toHaveScreenshot(`${key}.png`, { animations: 'disabled', maxDiffPixels: 0 })
    })
  })
}

// ── Reduced motion: the surfaces the motion audit stripped, at rest under the preference ───────
// `routes.ts`'s REDUCED_MOTION_SHOTS: each route the audit touched that this suite shoots, and the
// command palette and the composer's + menu open, with `prefers-reduced-motion: reduce` in force.
// The shots above prove each page looks right once its motion ends; these prove a user who asked
// for no motion is shown each page whole, with nothing left at an entrance's first frame. Two
// things are proved before the capture, because a picture alone cannot tell them apart from a
// clean result: that the preference was in force on the page photographed, and that an opened
// surface came to rest (under reduced motion it has no fade to wait for). The titles carry no
// `(#/`: these are not route scans, so `assert-no-route-skips.mjs` does not count them.
const OPEN: Record<ReducedMotionOpen, (page: Page) => Promise<void>> = {
  'command palette': async (page) => {
    // The listener is the shell's, on `window`; `gotoRoute` has already proved the shell mounted.
    await page.keyboard.press('ControlOrMeta+k')
    await expect(page.getByRole('listbox', { name: 'Commands' })).toBeVisible()
  },
  'composer menu': async (page) => {
    await page.getByRole('button', { name: 'Add to message' }).click()
    await expect(page.getByRole('button', { name: 'Attach files' })).toBeVisible()
  },
}

for (const { key, theme, route, open } of REDUCED_MOTION_SHOTS) {
  test.describe(`visual: ${key}`, { tag: '@visual' }, () => {
    test.describe.configure({ timeout: 300_000 })
    test.use({ reducedMotion: 'reduce' })
    test(open ? `${open} open` : 'the route at rest', async ({ page }) => {
      await assertPristineFlywheel(page)
      await seedTheme(page, theme)
      const loaded = await gotoRoute(page, route, BUDGET)
      expect(
        await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches),
        `${key}: prefers-reduced-motion is not in force, so this would photograph a user with no preference`,
      ).toBe(true)
      const exhausted = [...loaded.exhausted]
      if (open) {
        await OPEN[open](page)
        if (!(await settleDom(page, undefined, BUDGET.settleMs))) exhausted.push(`settleDom (the ${open})`)
        if (!(await settleEntranceAnimations(page, BUDGET.fadeMs))) exhausted.push(`settleEntranceAnimations (the ${open})`)
      }
      const settle = { settled: exhausted.length === 0, exhausted }
      await expectRouteScreenshot(page, key, settle)
    })
  })
}
