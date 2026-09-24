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
