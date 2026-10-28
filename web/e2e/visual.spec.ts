import { expect, test, type Locator, type Page } from '@playwright/test'
import {
  ROUTES, VIEW_ROUTES, THEMES, ORB_STATE_SHOTS, REDUCED_MOTION_SHOTS, axisShots, type ReducedMotionOpen, type VisualAxis,
} from './routes'
import {
  seedTheme, gotoRoute, expectRouteScreenshot, assertPristineFlywheel, settleDom, settleEntranceAnimations, type SettleReport,
} from './helpers'
import { FIXED_CLOCK } from './fixedClock'
import { AUDIT_VERIFIED, LEARNING_WEEK, SYSTEM_READING, answerStubbedRead, type StubbedRead } from './stubbedReads'

// ── Visual-regression baselines — every nav route × both themes ─────────────
// This is the safety rail. Its baselines are made and compared against the pinned image's browser
// alone (`e2e/run-pinned.mjs`, #3943), and every one is `-linux`:
//   the Refresh visual baselines workflow   (regenerates them, as an artifact to review)
//   npm run e2e:visual                     (verifies against them — must be ZERO diff)
// A consistency fix that forces a REAL visual change: implement it, dispatch the workflow on the
// branch, and say in the change's description which baselines moved and why (e2e/README.md).
// Never silently keep/revert a visual change.
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
// wall-clock in a leg of its own (e2e-pinned), which is the cheapest thing in
// the budget to spend.
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

// ── A red is told once (#3943) ───────────────────────────────────────────────────────────────
// No retry: a shot compares one frame with its golden, so a retry only classifies a red that
// `failOnFlakyTests` keeps red. On run 40599132936 the retry did worse. The pinned browser was
// starved, every light text-200 shot failed its settle, and each retry, in a new worker, hung on its
// first call to the browser (`seedTheme`'s `addInitScript`) for its whole 300 s test timeout, until
// run-job stopped the leg at 39 minutes. The config stops this leg at its second failure
// (`maxFailures`, playwright.config.ts), and each action here (a click, a focus) gives up after 30 s.
test.describe.configure({ retries: 0 })
test.use({ actionTimeout: 30_000 })

// ── What makes a shot the same picture on every run ────────────────────────────────────────────
// A refresh writes a golden only where the compare fails (`run-pinned.mjs`, UPDATE_SNAPSHOTS), so a
// surface that draws something the run does not decide moves its golden on whichever run crosses
// the tolerance. The Inbox refresh (run 40150040941) found four, each under it on that run: the
// day, the run's own counts and readings, a probe's timing and a caret. Each is pinned before the
// shot, on every shot of the route (its themes, axes and reduced-motion shots alike):
//   · a route in FIXED_CLOCK (`fixedClock.ts`, the UI matrix's table) loads under its instant;
//   · a read in VISUAL_STUBS answers with fixed values, and each must have answered by the shot,
//     so a read that moved and no longer matches is a red, never a live value photographed;
//   · a read in ANSWERED is waited for, and then the state it settles;
//   · under forced colours a focused text field is blurred (`blurField`).
// The tests at the end of this file plant each cause and fail when its pin stops holding.

/** Reads a shot answers with fixed values (`StubbedRead`, `e2e/stubbedReads.ts`), and why there. */
const VISUAL_STUBS: Record<string, StubbedRead[]> = {
  learning: [LEARNING_WEEK],
  settings: [AUDIT_VERIFIED, SYSTEM_READING],
}

/** A read a route draws a state from with nothing on screen saying it is still loading, so the
 *  page settles before it answers; and the state the page is in until it does. */
const ANSWERED: Record<string, { read: RegExp; pending: (page: Page) => Locator; why: string }> = {
  terminal: {
    read: /\/api\/terminal\/sessions(\?.*)?$/,
    // The persistent-sessions toggle is disabled, under its action's label, until the host's
    // capability comes back (`persistToggleCopy`): grey under forced colours, black once answered.
    pending: (page) => page.getByRole('button', { name: /^(Enable|Disable) persistent sessions$/, disabled: true }),
    why: 'the persistent-sessions toggle is disabled until the host says whether it can keep a session',
  },
}

/** Before the route loads: its fixed clock and its stubbed reads. Returns the hit count per stub. */
async function pinRoute(page: Page, route: string): Promise<Map<StubbedRead, number>> {
  if (FIXED_CLOCK[route]) await page.clock.setFixedTime(new Date(FIXED_CLOCK[route]))
  const hits = new Map<StubbedRead, number>()
  for (const stub of VISUAL_STUBS[route] ?? []) {
    hits.set(stub, 0)
    await page.route(stub.url, (r) => {
      hits.set(stub, (hits.get(stub) ?? 0) + 1)
      return answerStubbedRead(stub, r)
    })
  }
  return hits
}

/** Once the route has settled: its ANSWERED read has come back and the page has left the state
 *  it held until then, and every stub answered at least once. */
async function waitForPins(page: Page, route: string, answered: Promise<unknown> | null, hits: Map<StubbedRead, number>, exhausted: string[]): Promise<void> {
  const wait = ANSWERED[route]
  if (wait && answered) {
    await answered
    await expect(wait.pending(page), `${route}: ${wait.why}`).toHaveCount(0)
    if (!(await settleDom(page, undefined, BUDGET.settleMs))) exhausted.push(`settleDom (after ${route}'s probe answered)`)
  }
  for (const [stub, n] of hits) expect(n, `${route}: the stubbed read ${stub.url.source} was never asked, so the shot is not of its fixed values`).toBeGreaterThan(0)
}

/** Load `route` for a shot: pinned, settled, and waited for. */
async function loadPinned(page: Page, route: string): Promise<SettleReport> {
  const hits = await pinRoute(page, route)
  const answered = ANSWERED[route] ? page.waitForResponse(ANSWERED[route].read) : null
  const loaded = await gotoRoute(page, route, BUDGET)
  const exhausted = [...loaded.exhausted]
  await waitForPins(page, route, answered, hits, exhausted)
  return { settled: exhausted.length === 0, exhausted }
}

/** Blur a focused text field. Playwright hides the caret with `caret-color: transparent`, which
 *  forced colours override, so a field focused on load (Settings' search) photographed its caret
 *  on one run and not the next, by where in its blink the shot fell. */
async function blurField(page: Page): Promise<boolean> {
  return page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null
    if (!el || !(el.matches('input, textarea, select') || el.isContentEditable)) return false
    el.blur()
    return true
  })
}

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
        const settle = await loadPinned(page, route)
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
      const loaded = await loadPinned(page, route)
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

// ── The axes: every route in both themes again, at each way a user asks it to differ ────────────
// `routes.ts`'s VISUAL_AXES. Each shot proves its axis was in force on the page it photographs before
// the capture, since a picture of the default page is otherwise indistinguishable from a clean
// result: the width, the doubled root font, or the media queries forced colours answer. Text zoom is
// applied once the route is at rest, as the UI matrix applies it, and the page is settled again after
// it. The titles carry no `(#/`: these are not route scans, so `assert-no-route-skips.mjs` does not
// count them.
const AXIS_USE: Record<VisualAxis, Parameters<typeof test.use>[0]> = {
  '390px': { viewport: { width: 390, height: 844 } },
  'text-200': {},
  'high-contrast': { forcedColors: 'active', contrast: 'more' },
}

const AXIS_IN_FORCE: Record<VisualAxis, (page: Page) => Promise<boolean>> = {
  '390px': (page) => page.evaluate(() => window.innerWidth === 390),
  'text-200': (page) => page.evaluate(() => document.documentElement.dataset.pcTextZoom === '200'),
  'high-contrast': (page) =>
    page.evaluate(() => matchMedia('(forced-colors: active)').matches && matchMedia('(prefers-contrast: more)').matches),
}

for (const { key, axis, theme, route, label } of axisShots()) {
  test.describe(`visual: ${key}`, { tag: '@visual' }, () => {
    test.describe.configure({ timeout: 300_000 })
    test.use(AXIS_USE[axis])
    test(`${label} at ${axis}`, async ({ page }) => {
      await assertPristineFlywheel(page)
      await seedTheme(page, theme)
      const loaded = await loadPinned(page, route)
      const exhausted = [...loaded.exhausted]
      if (axis === 'text-200') {
        await page.evaluate(() => {
          const root = document.documentElement
          root.style.fontSize = `${(parseFloat(getComputedStyle(root).fontSize) || 16) * 2}px`
          root.dataset.pcTextZoom = '200'
        })
        if (!(await settleDom(page, undefined, BUDGET.settleMs))) exhausted.push('settleDom (200% text)')
      }
      expect(await AXIS_IN_FORCE[axis](page), `${key}: ${axis} is not in force, so this would photograph the default page`).toBe(true)
      if (axis === 'high-contrast' && (await blurField(page)) && !(await settleDom(page, undefined, BUDGET.settleMs))) {
        exhausted.push('settleDom (after the blur)')
      }
      const settle = { settled: exhausted.length === 0, exhausted }
      await expectRouteScreenshot(page, key, settle)
    })
  })
}

// ── Each pin, planted ──────────────────────────────────────────────────────────────────────────
// Each cause the pins above remove, planted on its own route, with the pin shown to hold. A pin
// that stops holding (a stub whose read moved, a wait that no longer waits, a blur that no longer
// happens) fails here, and not as one golden among two hundred moving on some later run. The titles
// carry no `(#/`: these are not route scans, so `assert-no-route-skips.mjs` does not count them.
test.describe('visual: each pin holds', { tag: '@visual' }, () => {
  test.describe.configure({ timeout: 300_000 })

  test("Learning's week ends on its fixed day, whatever day the gateway is on", async ({ page }) => {
    await assertPristineFlywheel(page)
    await seedTheme(page, 'light')
    const hits = await pinRoute(page, 'learning')
    await gotoRoute(page, 'learning', BUDGET)
    await waitForPins(page, 'learning', null, hits, [])
    // Each tile's title starts with its day (`YYYY-MM-DD — ...`): the seven end on the fixed day.
    const days = await page.evaluate(() =>
      [...document.querySelectorAll('[title]')].map((el) => /^(\d{4}-\d{2}-\d{2}) — /.exec(el.getAttribute('title') ?? '')?.[1]).filter(Boolean),
    )
    const fixed = FIXED_CLOCK.learning.slice(0, 10)
    const before = (n: number) => new Date(Date.parse(`${fixed}T00:00:00Z`) - n * 86_400_000).toISOString().slice(0, 10)
    expect(days).toEqual([6, 5, 4, 3, 2, 1, 0].map(before))
  })

  test("Settings' tiles read the fixed count and reading, not the run's", async ({ page }) => {
    await assertPristineFlywheel(page)
    await seedTheme(page, 'light')
    await loadPinned(page, 'settings')
    await expect(page.getByText('all 150 events verified').first()).toBeVisible()
    await expect(page.getByText('Memory 3.2/15.6GB').first()).toBeVisible()
    await expect(page.getByText('CPU 12%').first()).toBeVisible()
  })

  test("the terminal's shot waits for a probe that answers after the page settles", async ({ page }) => {
    await assertPristineFlywheel(page)
    await seedTheme(page, 'light')
    // The probe held until the page has settled without it, as a slow host answers it.
    let release!: () => void
    const held = new Promise<void>((r) => (release = r))
    await page.route(ANSWERED.terminal.read, async (r) => {
      await held
      await r.continue()
    })
    const answered = page.waitForResponse(ANSWERED.terminal.read)
    const hits = await pinRoute(page, 'terminal')
    await gotoRoute(page, 'terminal', BUDGET)
    // The cause: settled, and still showing the state it holds until the probe answers.
    await expect(ANSWERED.terminal.pending(page), 'the planted slow probe left the toggle pending at rest').toHaveCount(1)
    release()
    await waitForPins(page, 'terminal', answered, hits, [])
    await expect(ANSWERED.terminal.pending(page)).toHaveCount(0, { timeout: 0 })
  })

  test.describe('under forced colours', () => {
    test.use(AXIS_USE['high-contrast'])
    test('a focused text field is blurred before the shot', async ({ page }) => {
      await assertPristineFlywheel(page)
      await seedTheme(page, 'light')
      await loadPinned(page, 'settings')
      const field = page.getByRole('searchbox').or(page.getByRole('textbox')).first()
      await field.focus()
      expect(await page.evaluate(() => document.activeElement?.matches('input, textarea') ?? false), 'the plant: a text field holds focus').toBe(true)
      expect(await blurField(page)).toBe(true)
      expect(await page.evaluate(() => document.activeElement?.matches('input, textarea') ?? false)).toBe(false)
    })
  })
})
