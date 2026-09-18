import { type Page, expect } from '@playwright/test'
import type { Theme } from './routes'

// ── Harness helpers ─────────────────────────────────────────────────────────

/** Set the color theme deterministically BEFORE the app boots by seeding
 *  localStorage['mode'] (the key theme.tsx reads) + prefers-color-scheme. */
export async function seedTheme(page: Page, theme: Theme): Promise<void> {
  await page.addInitScript((t) => {
    try { localStorage.setItem('mode', t) } catch { /* ignore */ }
  }, theme)
  await page.emulateMedia({ colorScheme: theme })
}

/** The app SHELL — `ui/NavRail`, rendered only once the server reports a non-empty
 *  `dashboard.user_name` (`onboarded` is derived from it in `app/identity.tsx`). So its
 *  presence proves three things at once: the gateway answered, the session is
 *  authenticated, and the install is onboarded. Its ABSENCE is the onboarding screen. */
export const SHELL_SELECTOR = 'nav[data-tour="rail"]'

/** Fail the test if we are not looking at the real, onboarded app.
 *
 *  Without a reachable gateway the SPA renders ONBOARDING for every route: no rail, no
 *  page content, no ⌘K listener. axe finds no serious/critical violations there, which is
 *  byte-identical to a genuinely clean route — so 96 route scans reported a pass while
 *  visiting a surface no user ever sees. Only `command palette [opened]` noticed, and it
 *  blamed the palette. This floor makes the harness's own breakage the loud failure.
 *
 *  Every entry in `routes.ts` is shell-bearing. `#/companion` and `?embed=1` deliberately
 *  render WITHOUT a NavRail — adding either to the manifest needs an opt-out here. */
export async function assertShellMounted(page: Page): Promise<void> {
  await expect(
    page.locator(SHELL_SELECTOR),
    `the app shell (${SHELL_SELECTOR}) is not mounted — this is the ONBOARDING screen, not\n` +
      `the route under test. The harness gateway is unreachable, unauthenticated or not\n` +
      `onboarded; any clean result measured here is meaningless. See playwright.config.ts.`,
  ).toBeVisible({ timeout: 10_000 })
}

/** Navigate to a hash route and wait for the shell to settle: no spinner, fonts
 *  loaded, network idle. Returns after the route's chrome is painted. */
/** How long `gotoRoute` may spend waiting for a surface to go quiet.
 *
 *  🪤 A KNOB, because the two consumers want different things and one number cannot serve
 *  both. The a11y/walkthrough sweeps are ~130 tests each and read the accessibility tree,
 *  which does not care whether a card's count arrived — for them the default keeps the whole
 *  `gotoRoute` inside Playwright's 30s per-test budget. `visual.spec.ts` is the opposite
 *  trade: it compares PIXELS, so one late arrival is a failed golden, and it is 40 tests
 *  that no CI job runs. It buys a longer settle and raises its own test timeout to match.
 *
 *  This is emphatically NOT a tolerance: `maxDiffPixelRatio` is untouched. It gives the
 *  settle more time to reach a resting state, rather than accepting more pixels of drift. */
export interface SettleBudget {
  /** Cap for `settleDom`'s quiescence wait. */
  settleMs?: number
}

export async function gotoRoute(page: Page, route: string, budget: SettleBudget = {}): Promise<void> {
  await page.goto(`/#/${route}`)
  // Fonts must be ready or text metrics shift the screenshot.
  await page.evaluate(() => (document as unknown as { fonts?: { ready: Promise<unknown> } }).fonts?.ready)
  // Bounded: routes that poll (agents status, live feeds) NEVER go network-idle,
  // and the default waitForLoadState timeout equals the test timeout — the test
  // would die before the catch fires. 5s settles real loads; pollers fall through.
  await page.waitForLoadState('networkidle', { timeout: 5_000 }).catch(() => { /* long-poll routes never idle; fall through */ })
  // Give the route cross-fade a beat to finish (animations are disabled for the
  // screenshot itself, but the mount still needs to resolve).
  await page.waitForTimeout(400)
  // Every caller measures the route it just navigated to; none of them can tell an
  // onboarding hijack from a clean surface on their own.
  await assertShellMounted(page)
  // Then the SHELL's own async chrome, then the route's DOM, then the fades — in that
  // order, and the order is the point. Each stage can only resolve once the previous one
  // has: the shell's polls cannot answer before the shell mounts, a route's content cannot
  // quiesce before those answers stop re-laying it out, and an entrance fade cannot be
  // waited for before the element that fades in has arrived.
  await settleShellChrome(page)
  await settleDom(page, undefined, budget.settleMs)
  // 🪤 LAST, not before `assertShellMounted` where this call used to sit. There it ran
  // ~400ms after navigation — before the route's data had arrived, so before the staggered
  // fades it exists to await had been triggered at all. It was waiting for animations that
  // had not started yet and then reporting the page settled.
  await settleEntranceAnimations(page)
}

/** The gateway-connectivity dot's LIVE resting state, as an accessible name.
 *
 *  `SystemWidget` (`web/src/ui/SystemWidget.tsx`) keeps a three-valued `ConnStatus`
 *  (`:21`) that starts at `connecting` (`:26`) and is resolved by its `/api/system` poll
 *  (`:44-47`) — resolving sets `connected`, rejecting sets `disconnected`. It publishes
 *  that state as its trigger's accessible name, ``System status — ${statusLabel}`` (`:74`,
 *  labels at `:69`), so the label is the signal and no test needs to know the dot's
 *  colours. The button has NO visible text: `System status` exists only in the aria-label.
 *
 *  🪤 `connected` ONLY — deliberately not `(connected|disconnected)`. "The poll resolved"
 *  is not one state, it is two, and they render two different colours, so a
 *  resolved-ness wait would still admit two renderings — the exact bug this closes. The
 *  harness starts and owns its gateway and has already asserted the shell against it, so
 *  on this run a disconnected dot is a failed poll, not a valid resting state; waiting for
 *  it would let a screenshot record a mid-outage frame as the golden.
 *
 *  🪤 The em dash is U+2014 with a space either side, copied from `:74` — not a hyphen.
 *  A hyphen here makes this predicate silently unsatisfiable, i.e. a 6s no-op. */
const CONNECTIVITY_RESOLVED = /System status — Gateway connected/

/** Block until the SHELL's self-polling chrome has RESOLVED — not merely mounted.
 *
 *  🪤 THIS IS THE HARNESS'S PRIMARY NON-DETERMINISM, AND IT IS GLOBAL TO EVERY ROUTE.
 *  `ShellCornerRight` (`web/src/ui/ShellCorners.tsx:50`) hosts three independently
 *  self-polling controls, each of which renders NOTHING — or something different — until
 *  its own fetch answers:
 *    · `NotificationBell` (`:77`) — unread badge absent until its count answers.
 *    · `DegradedChip`     (`:81`) — absent until its degraded read answers, then a pill.
 *    · `SystemWidget`     (`:84`) — dot starts ORANGE (`connecting`), turns GREEN on the
 *                                   first `/api/system` answer.
 *  `useVisiblePoll` (`web/src/lib/useVisiblePoll.ts:26`) fires its first fetch from inside
 *  a `useEffect`, i.e. AFTER first paint — so the first render of every route is painted
 *  with all three controls COLD, and a screenshot taken before they resolve is a different
 *  image from one taken after.
 *
 *  🔑 AND THE RACE IS NOT CONFINED TO THE HEADER, which is why it presents as whole-page
 *  drift rather than a small header patch. The module-private `useShellCornerWidth`
 *  (`ShellCorners.tsx:92`) measures the corner cluster and publishes it as a CSS custom
 *  property on the ROOT element (`:96`, `--shell-corner-l`/`--shell-corner-r`), and
 *  `TopBar` reserves its horizontal padding from that var (`web/src/ui/TopBar.tsx:116-117`).
 *  So a badge or chip arriving late re-lays-out page content far below the header. Raising
 *  the pixel tolerance could never have addressed this: the content MOVES.
 *
 *  Waiting on the connectivity label is what makes this cheap and non-brittle: all three
 *  polls are fired from the same shell mount, and this is the only one of the three whose
 *  resolved state is expressed in the accessibility tree rather than in pixels. `settleDom`
 *  below is what actually absorbs the other two — this wait exists so that quiescence is
 *  measured AFTER the shell's fetches are in flight, not during the idle window before
 *  they start.
 *
 *  🕐 BUDGET 6s, and the ceiling is arithmetic, not taste. Every spec runs on Playwright's
 *  default 30s test timeout and `gotoRoute` already spends up to 5s (`networkidle`) + 0.4s
 *  before this line, with `settleDom`, the fades and the screenshot still to come. See
 *  `settleDom` for the full sum. */
export async function settleShellChrome(page: Page, timeout = 6_000): Promise<void> {
  await page
    .waitForFunction(
      (pattern: string) => {
        const el = document.querySelector('button[aria-label*="System status"]')
        return !!el && new RegExp(pattern).test(el.getAttribute('aria-label') ?? '')
      },
      CONNECTIVITY_RESOLVED.source,
      { timeout },
    )
    .catch(() => { /* still `connecting` — settleDom is the backstop, and the 2× proof reports it */ })
}

/** Block until the DOM stops changing for `quietMs` — the generic settle that covers every
 *  async arrival the harness cannot enumerate by name.
 *
 *  🪤 WHY QUIESCENCE AND NOT A BIGGER `waitForTimeout`. The second non-determinism source
 *  is that `gotoRoute` could return while a route was still showing its LOADING SKELETON:
 *  every wait above this line is best-effort with a SWALLOWED timeout (`networkidle` 5s,
 *  the fade settle 2s), so on a loaded host the screenshot simply lands mid-load and the
 *  baseline records whichever frame won the race. A fixed wait cannot fix that, because
 *  load time is a property of the HOST while quiescence is a property of the PAGE — this
 *  scales with whatever the machine is doing, which is the only form that survives being
 *  run on both a busy dev box and a 4-core runner.
 *
 *  `attributes` is observed as well as `childList`, deliberately: the shell corner-width
 *  var is written to the root element's `style` attribute (`ShellCorners.tsx:96`), so an
 *  attribute-blind observer would call the page settled while it was still re-laying-out
 *  from that write — the very last thing to happen in the race above.
 *
 *  🔑 AND A `ResizeObserver` ON THE ROOT, WHICH A MUTATION OBSERVER CANNOT REPLACE. A
 *  `fullPage` screenshot's DIMENSIONS are the page height, so a late height change does not
 *  perturb a region — it re-lays-out everything below it and diffs the whole image. Two
 *  things make that invisible to `MutationObserver` alone: a height change can come from
 *  pure layout with no DOM edit at all (a CSS custom property write, a font swap, an image
 *  resolving), and where there IS an edit the mutation fires BEFORE the browser reflows, so
 *  the quiet window can elapse between the edit and the height it causes.
 *
 *  Measured on `#/dashboard` while this helper observed mutations only: the page rendered at
 *  two different heights across runs and the diff showed the SAME content band twice at two
 *  y-offsets (0.04 ratio / 28,614 px dark, 27,779 px light). The cause is a variable-height
 *  live region in the page FLOW — `DashboardPage`'s docked `SystemRailIsland` is `shrink-0`
 *  and `SystemHealth` renders its metric strip only once the `system` slice arrives
 *  (`SystemHealth.tsx:120`), with `flex-wrap`, so the rail is one height before that answer
 *  and another after. That is also why MASKING it was not enough on its own: a mask replaces
 *  a region's pixels, not its SIZE, so the masked box simply landed at a different y in the
 *  two captures and reddened anyway.
 *
 *  Best-effort by design, like its siblings: a surface that never quiesces proceeds after
 *  the cap rather than reddening the gate for a reason that is not its clause. What makes
 *  that safe HERE, and did not before, is that the two-capture determinism proof is the
 *  acceptance test for this helper — a route that still races is reported as a measured
 *  residue instead of being silently baked into a golden.
 *
 *  🕐 BUDGET 8s, one step above `settleShellChrome`'s 6s. `#/learning` fires ten
 *  independent one-shot reads on mount with no shared "all loaded" barrier, each mounting
 *  its own section the moment ITS fetch answers, so its tail is the longest in the suite.
 *  Worst case through `gotoRoute` is 5s (networkidle) + 0.4s + 6s (shell chrome) + 8s
 *  (this) + 2s (fades) ≈ 21.4s, leaving ~8.6s of the 30s test timeout for the screenshot
 *  assertion itself. That sum is the reason neither cap is simply generous: raising both to
 *  15s exceeds the per-test budget on the routes that never quiesce. */
export async function settleDom(page: Page, quietMs = 400, timeout = 8_000): Promise<void> {
  await page
    .evaluate(
      ({ quiet, cap }) =>
        new Promise<void>((resolve) => {
          let timer = 0
          const finish = () => {
            window.clearTimeout(timer)
            window.clearTimeout(hardCap)
            obs.disconnect()
            size.disconnect()
            resolve()
          }
          const bump = () => {
            window.clearTimeout(timer)
            timer = window.setTimeout(finish, quiet)
          }
          const obs = new MutationObserver(bump)
          // Height changes, from ANY cause — including ones that edit no DOM at all, and
          // ones whose reflow lands after their mutation. `box: 'border-box'` so a padding
          // change counts too.
          const size = new ResizeObserver(bump)
          const hardCap = window.setTimeout(finish, cap)
          obs.observe(document.documentElement, {
            subtree: true,
            childList: true,
            attributes: true,
            characterData: true,
          })
          size.observe(document.documentElement, { box: 'border-box' })
          timer = window.setTimeout(finish, quiet)
        }),
      { quiet: quietMs, cap: timeout },
    )
    .catch(() => { /* navigation raced the evaluate — the caller's own assertions still hold */ })
}

/** Block until no element is mid-fade, so a scan measures the page AT REST.
 *
 *  A partially-faded element composites its ink toward the background, and axe reads
 *  the composite. Measured on `#/dashboard` (dark): the suggestion rows' resting pair
 *  is `#c4c7c5` on `#141414` — **10.81:1**, comfortably AA — but axe reported
 *  `[serious] color-contrast … 2.77` against a foreground of `#5b5d5c`. That value is
 *  not a token: it is the resting colour composited at **α ≈ 0.40** (solving per
 *  channel: (0x5b-0x14)/(0xc4-0x14) = 0.40, (0x5d-0x14)/(0xc7-0x14) = 0.41). The rows
 *  animate `opacity: 0 → 1` with `delay: i * 0.04`, and the flagged node was
 *  `nth-child(4)` — the longest stagger. So the gate was failing on a frame that exists
 *  for ~200ms and is invisible once the page settles.
 *
 *  HOW OFTEN: once in four runs. The red appeared in a 9-worker/111-test run and then
 *  did NOT reproduce — not with this settle (107 passed), not without it in isolation
 *  (2 passed), and not without it in a second full 9-worker run (107 passed). So this is
 *  a flake whose MECHANISM is proven by the arithmetic above, not a deterministic
 *  failure, and it cannot be demonstrated by reverting this helper. It is shipped as
 *  hardening: the composite is real whenever the scan lands inside the fade, and a gate
 *  that reports `serious` from a ~200ms frame teaches people to ignore it.
 *
 *  The 400ms above is why this needs a CONDITION, not a bigger number: a fixed wait
 *  cannot know how many staggered children a route has, and the next widget to add a
 *  fifth row would slip past any constant we picked.
 *
 *  Scoped to INLINE opacity because that is what the motion library writes while
 *  animating; class-based translucency (a decorative overlay at rest) must not keep us
 *  waiting. `getAnimations()` is checked too, for animations that never touch inline
 *  style. Best-effort by design: on timeout we proceed exactly as before, so a route
 *  with a permanently-animating element degrades to today's behaviour instead of
 *  failing.
 *
 *  Deliberately NOT solved by emulating `prefers-reduced-motion`: that would skip the
 *  animated path entirely, and this gate should measure what a user actually sees.
 */
export async function settleEntranceAnimations(page: Page, timeout = 2_000): Promise<void> {
  await page
    .waitForFunction(
      () => {
        const midFade = Array.from(document.querySelectorAll<HTMLElement>('[style*="opacity"]')).some(
          (el) => {
            const v = Number.parseFloat(el.style.opacity)
            return Number.isFinite(v) && v > 0.01 && v < 0.99
          },
        )
        // 🪤 INFINITE animations must NOT count as "still settling", or this predicate is
        // VACUOUS on every route. `SystemWidget` renders a `.status-pulse` ring whenever the
        // gateway is connected (`SystemWidget.tsx:83-85`), and that class is
        // `animation: status-pulse … infinite` (`web/src/design/tokens.css:436`). It lives in
        // the SHELL, so it is present on every surface the harness visits and never reaches
        // `finished` — which means a bare `playState === 'running'` test can never go true
        // once the dot turns green. `waitForFunction` therefore always ran to its 2s timeout
        // and the `.catch()` below swallowed it. The docstring's escape hatch ("a route with
        // a permanently-animating element degrades to today's behaviour") reads as a rare
        // case; it was EVERY case, and it is exactly how a screenshot could still land
        // inside an entrance fade.
        //
        // Finite animations are what this helper exists for (the staggered opacity fades
        // above), so they are still awaited. An endless beacon is a resting state, not a
        // mid-transition one.
        const running =
          typeof document.getAnimations === 'function' &&
          document.getAnimations().some((a) => {
            if (a.playState !== 'running') return false
            return a.effect?.getComputedTiming().iterations !== Infinity
          })
        return !midFade && !running
      },
      undefined,
      { timeout },
    )
    .catch(() => {
      /* a permanently-animating surface must not fail the scan — fall through */
    })
}

/** Assert an interaction actually grew the DOM, i.e. the surface really opened.
 *
 *  Cycle 46 deleted the served bundle mid-run and the gateway fell back to its
 *  "dashboard isn't built yet" page — 34 elements, 0 buttons. The axe probe reported
 *  **0 defects on every surface**, which is byte-identical to a clean tree. An absolute
 *  floor plus a growth check is what separates "measured clean" from "measured nothing". */
export async function assertMounted(page: Page, before: number, label: string): Promise<void> {
  const after = await page.evaluate(() => document.querySelectorAll('*').length)
  expect(after, `${label}: only ${after} elements — the app did not render`).toBeGreaterThan(80)
  expect(
    after,
    `${label}: element count did not grow (${before} → ${after}). The opener ran without\n` +
      `opening anything, so a clean axe result here would be meaningless.`,
  ).toBeGreaterThan(before)
}

/** `true` = the recipe ran and the surface should now be open. `{ skip: … }` = its
 *  precondition is absent, and WHY.
 *
 *  A bare boolean forced one hardcoded skip message ("no seeded data on this route") onto
 *  causes that are not the same thing — an empty list, a collapsed row and a renamed control
 *  each need a different next action from whoever reads the report. */
export type OpenResult = true | { skip: string }

/** A surface that exists only after an interaction, plus how to reach it.
 *
 *  `open` returns a skip reason when its target is absent (no seeded data, a renamed
 *  control) so the caller can skip rather than silently scan the un-opened route. */
export interface Opener {
  label: string
  route: string
  open: (page: Page) => Promise<OpenResult>
}

/** Click a list row's BODY. Rows are `absolute inset-0 -z-10` overlay buttons under
 *  their own content, so a locator click resolves to the content and Playwright reports
 *  the target as covered; the real user path is a click over the row that bubbles. */
async function clickRowBody(page: Page): Promise<OpenResult> {
  const row = page.locator('button.absolute.inset-0').first()
  if (!(await row.count())) return { skip: 'no list rows on this route — nothing to peek at (seed data to cover it)' }
  const box = await row.boundingBox()
  if (!box) return { skip: 'the first list row has no box (collapsed or off-screen)' }
  await page.mouse.click(box.x + Math.min(400, box.width / 2), box.y + box.height / 2)
  return true
}

/** The recipes, all proven by hand in cycles 45/49 before being wired in here. */
export const OPENERS: Opener[] = [
  {
    label: 'command palette',
    route: 'chat',
    open: async (page) => {
      // Returns `true` unconditionally, unlike its siblings — deliberately. The ⌘K
      // listener lives on `window`, registered by `app/CommandPalette`, which the SHELL
      // mounts; with no shell the chord changes nothing and the growth floor below fired
      // while naming the palette. `gotoRoute` now asserts the shell FIRST, for all 108
      // tests instead of this one, so by the time we get here the listener provably
      // exists. A growth-floor failure therefore means the palette itself broke — which
      // must fail loudly, not skip.
      // The app binds Meta+k OR Control+k; Playwright's ControlOrMeta picks per-platform.
      await page.keyboard.press('ControlOrMeta+k')
      return true
    },
  },
  {
    label: 'chat slash menu',
    route: 'chat',
    open: async (page) => {
      // The composer is a CodeMirror contenteditable, invisible to input/textarea.
      const cm = page.locator('[contenteditable="true"]').first()
      if (!(await cm.count())) return { skip: 'no contenteditable composer on #/chat' }
      await cm.click()
      await cm.pressSequentially('/')
      // `ui/composer/SlashMenu` FETCHES its command list when it opens (`loadCommands()`)
      // and returns `null` while `results.length === 0` — so the menu can be open in state
      // and absent from the DOM. Under nine parallel workers that fetch outran the spec's
      // 700 ms wait and the run failed the growth floor with the "/" sitting in the live
      // composer, blaming a menu that was working. Waiting for the list is waiting for an
      // async load, not relaxing an assertion: if it never arrives, the floor still fails.
      await cm.page().locator('[role="listbox"]').first()
        .waitFor({ state: 'visible', timeout: 8_000 })
        .catch(() => { /* still absent — assertMounted is what reports that */ })
      return true
    },
  },
  { label: 'knowledge peek dock', route: 'knowledge', open: clickRowBody },
  { label: 'inbox peek dock', route: 'inbox', open: clickRowBody },
  {
    label: 'new project modal',
    route: 'projects',
    open: async (page) => {
      const btn = page.getByRole('button', { name: /new project/i }).first()
      if (!(await btn.count())) return { skip: 'no "New project" button on #/projects' }
      await btn.click()
      return true
    },
  },
]

/** Assert a full-page screenshot matches the platform-qualified baseline. */
export async function expectRouteScreenshot(page: Page, name: string): Promise<void> {
  await expect(page).toHaveScreenshot(`${name}.png`, {
    fullPage: true,
    animations: 'disabled',
    // Mask volatile regions (clocks, live counters). The settles in `gotoRoute` handle
    // values that are merely LATE; this list is for values that are supposed to keep
    // changing, which no amount of settling can fix.
    //
    // `system-rail-island` (dashboard only — `DashboardPage.tsx`'s `SystemRailIsland`) is
    // the one that qualifies today: `SystemHealth` renders live host reads straight into
    // the page body — uptime, cpu%, mem, net rx/tx, load average — from the same
    // `/api/system` poll the shell's connectivity dot uses. Two screenshots of an
    // otherwise byte-identical tree, taken seconds apart, show a different uptime and a
    // different load average BY CONSTRUCTION.
    //
    // The locator matches zero elements on every other route, and masking a locator that
    // resolves to nothing is a no-op, so this is applied unconditionally rather than
    // threading a per-route mask list through `visual.spec.ts`. The widget's own rendering
    // stays covered where that belongs — a deterministic unit test over fixed data
    // (`src/pages/dashboard/widgets/systemHealthLabels.test.tsx`), not a live-gateway pixel
    // diff. `SystemRailIsland` is mounted twice (docked from `lg` up, in-scroll below it),
    // and the mask covers both because it is declared on the component, not on a mount.
    mask: [page.locator('[data-testid="system-rail-island"]')],
  })
}
