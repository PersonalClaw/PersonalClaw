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

/** How long `gotoRoute` may spend waiting for each stage of a surface to come to rest.
 *
 *  🪤 KNOBS, because the two consumers want different things and one number cannot serve
 *  both. The a11y/walkthrough sweeps are ~130 tests each and read the accessibility tree,
 *  which does not care whether a card's count arrived — for them the defaults keep the whole
 *  `gotoRoute` inside Playwright's 30s per-test budget. `visual.spec.ts` is the opposite
 *  trade: it compares PIXELS, so one late arrival is a failed golden, and it is 40 tests
 *  that no CI job runs. It buys longer stages and raises its own test timeout to match.
 *
 *  These are emphatically NOT tolerances: `maxDiffPixelRatio` is untouched. They give the
 *  settle more time to reach a resting state, rather than accepting more pixels of drift. */
export interface SettleBudget {
  /** Cap for `settleShellChrome`'s connectivity wait. */
  chromeMs?: number
  /** Cap for `settleDom`'s combined quiescence + loaded-ness wait. */
  settleMs?: number
  /** Cap for `settleEntranceAnimations`'s fade wait. */
  fadeMs?: number
}

/** Which of `gotoRoute`'s stages reached a resting state, and which ran out of budget.
 *
 *  🪤 THE RETURN VALUE IS THE POINT, AND ITS ABSENCE WAS THE DEFECT. Every stage below is
 *  non-throwing: each swallowed its own timeout so that a surface which never quiesces
 *  proceeds instead of reddening the gate for a reason that is not its clause. That is the
 *  right behaviour for the a11y/walkthrough sweeps, which read the accessibility tree and
 *  tolerate a late card. It is the WRONG behaviour for a pixel comparison, because there a
 *  swallowed timeout does not degrade the measurement — it FABRICATES one. The screenshot is
 *  taken anyway, mid-load, and `toHaveScreenshot` then reports "render drift" at 0.02–0.04
 *  against the 0.01 cap for a page that simply never finished arriving.
 *
 *  So the stages still do not throw, and the sweeps still ignore this value. What changes is
 *  that exhaustion is now SAYABLE: `expectRouteScreenshot` refuses to diff a page that never
 *  reached rest and names the stage, instead of publishing a drift number about a frame that
 *  does not exist once the page settles. */
export interface SettleReport {
  /** Every stage reached its resting state inside its budget. */
  settled: boolean
  /** The stages that ran out of budget, in the order they ran. */
  exhausted: string[]
}

/** Navigate to a hash route and return once it is AT REST — fonts loaded, the shell's polls
 *  resolved, every loading affordance cleared, the DOM and page height quiescent, no fade
 *  mid-flight — reporting any stage that ran out of budget rather than swallowing it. */
export async function gotoRoute(
  page: Page,
  route: string,
  budget: SettleBudget = {},
): Promise<SettleReport> {
  await page.goto(`/#/${route}`)
  // Fonts must be ready or text metrics shift the screenshot.
  await page.evaluate(() => (document as unknown as { fonts?: { ready: Promise<unknown> } }).fonts?.ready)
  // Give the route's mount effects a beat to FIRE. `useVisiblePoll` and every one-shot read
  // issue their first request from inside a `useEffect`, i.e. after first paint — so a
  // quiescence check run immediately after `goto` would observe a page that has not started
  // loading yet and call it settled. This wait is what makes the stages below meaningful.
  await page.waitForTimeout(400)
  // Every caller measures the route it just navigated to; none of them can tell an
  // onboarding hijack from a clean surface on their own.
  await assertShellMounted(page)
  // Then the SHELL's own async chrome, then the route's content, then its layout, then the
  // fades — in that order, and the order is the point. Each stage can only resolve once the
  // previous one has: the shell's polls cannot answer before the shell mounts, a route's
  // skeletons cannot clear before those answers arrive, layout cannot stop moving before the
  // content that moves it has landed, and an entrance fade cannot be waited for before the
  // element that fades in exists.
  const exhausted: string[] = []
  if (!(await settleShellChrome(page, budget.chromeMs))) exhausted.push('settleShellChrome')
  if (!(await settleDom(page, undefined, budget.settleMs))) {
    // Name WHICH half of the barrier was still unsatisfied. "The page never went quiet" and
    // "eleven cards are still skeletons" are different diagnoses with different next actions,
    // and the whole point of this rail is to stop reporting one thing as another.
    const pending = await page
      .evaluate((selector: string) => document.querySelectorAll(selector).length, LOADING_SELECTOR)
      .catch(() => -1)
    exhausted.push(pending > 0 ? `settleDom (${pending} loading affordances still on screen)` : 'settleDom')
  }
  // 🪤 LAST, not before `assertShellMounted` where this call used to sit. There it ran
  // ~400ms after navigation — before the route's data had arrived, so before the staggered
  // fades it exists to await had been triggered at all. It was waiting for animations that
  // had not started yet and then reporting the page settled.
  if (!(await settleEntranceAnimations(page, budget.fadeMs))) exhausted.push('settleEntranceAnimations')
  return { settled: exhausted.length === 0, exhausted }
}

/** 🪤 WHY THERE IS NO NETWORK BARRIER HERE, AND WHY `networkidle` IS GONE RATHER THAN TUNED.
 *
 *  "Wait until the page's requests have answered" is the obvious barrier, and it is the one
 *  this helper used to reach for. It cannot work in this app: `useConfigFsWatch.ts:19`,
 *  `DiagnosticsPanel.tsx:57`, `ModelsPanel.tsx:642` and `useModelDownloads.ts:27` each hold a
 *  long-lived `EventSource` open, so those routes never go network-idle at all. Its 5s cap
 *  therefore always elapsed and always fell through — the old comment said so ("pollers fall
 *  through") and treated a permanently-failing wait as an acceptable one.
 *
 *  And the network is the wrong quantity even where it is reachable: a poller that re-fetches
 *  the same numbers moves no pixels, while the arrival that DOES move them is visible either
 *  as a loading affordance clearing or as a DOM mutation — which is exactly what `settleDom`
 *  below now requires together. Those are the signals that survive contact with a
 *  permanently-polling app. Do not re-add a network wait here. */

/** 🪤 WHY THE TWO CONDITIONS ARE ONE BARRIER AND NOT TWO STAGES — MEASURED, because the
 *  obvious shape ("wait for the skeletons to clear, THEN wait for quiet") is wrong in a way
 *  that only shows up under load. Sampling every 250ms with 2s of injected latency on every
 *  `/api/**` response (Darwin, 18 cores, 1-min load 10–28), each condition is satisfied
 *  MID-LOAD on its own:
 *
 *  · Zero loading affordances is reached TRANSIENTLY, long before the route has finished
 *    arriving, because each card mounts its own skeleton when ITS fetch starts rather than
 *    sharing one barrier. `#/dashboard` first reads zero at 506ms and then raises skeletons
 *    again until 5755ms (peak 45); `#/settings` 531ms → 6010ms (peak 32); `#/inbox` 557ms →
 *    4313ms; `#/knowledge` 541ms → 4282ms. A bare `count === 0` wait returns in the first gap.
 *  · DOM quiescence is reached WITH skeletons on screen, because a pending fetch mutates
 *    nothing. On `#/tools` `settleDom` returned at 6053ms with **19 loading affordances still
 *    rendered**, and the last one did not clear until 6301ms.
 *
 *  So each condition covers exactly the other's hole, and only their CONJUNCTION is a resting
 *  state: the DOM has been quiet for `quietMs` AND nothing on screen says it is still loading.
 *  Two sequential stages would have let `#/tools` through on the second one. */

/** Anything the app renders to say "this region has not loaded yet".
 *
 *  Both halves of the skeleton kit, and between them they are exhaustive:
 *  · `.skeleton` — the bare atom (`ui/ListScaffold.tsx:251`), `aria-hidden`, no ARIA state.
 *  · `[aria-busy="true"]` — the shaped primitives (`ListSkeleton`/`FormSkeleton`/
 *    `CardGridSkeleton` and the bare-text loader at `:224`), which wrap the atoms in
 *    `role="status" aria-busy="true"`.
 *  Neither alone covers the other: the atom carries no ARIA, and the wrappers' own node has
 *  no `.skeleton` class.
 *
 *  🪤 NOT VACUOUS, and that was measured rather than assumed, because a "no skeletons" check is
 *  trivially true on a route that renders none. Peak affordance counts under 2s of injected API
 *  latency: 45 (`#/dashboard`), 32 (`#/settings`), 19 (`#/tools`, `#/inbox`, `#/knowledge`), 13
 *  (`#/learning`), 1 (`#/workflows`), 0 (`#/terminal`). Every measured route reaches zero, so
 *  the condition is satisfiable everywhere as well as load-bearing almost everywhere. */
const LOADING_SELECTOR = '.skeleton, [aria-busy="true"]'

/** How many times one target may mutate the same attribute before `settleDom` stops treating
 *  that stream as motion worth waiting for.
 *
 *  🕐 12, sized from the two measured extremes rather than chosen. `#/chat`'s rAF-driven rotating
 *  mark fires ~23 mutations/second, so it is reclassified in ~0.5s — early enough that it costs
 *  the barrier nothing. The busiest legitimate stream measured was `#/dashboard`'s live metric
 *  text at ~1/second (inside the masked island), which takes 12s to reach the same threshold and
 *  is by then genuinely live data rather than an arrival. A route's LOAD, by contrast, mutates
 *  many distinct targets a handful of times each, so nothing about it trips this. */
const PERPETUAL_MUTATIONS = 12

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
 *  🕐 BUDGET 6s, and the ceiling is arithmetic, not taste. The sweeps run on Playwright's
 *  default 30s test timeout and `gotoRoute` already spends 0.4s before this line, with
 *  `settleLoadState`, `settleDom`, the fades and their own assertions still to come. See
 *  `settleDom` for the full sum, and `visual.spec.ts` for the budgets the pixel rail buys. */
export async function settleShellChrome(page: Page, timeout = 6_000): Promise<boolean> {
  return page
    .waitForFunction(
      (pattern: string) => {
        const el = document.querySelector('button[aria-label*="System status"]')
        return !!el && new RegExp(pattern).test(el.getAttribute('aria-label') ?? '')
      },
      CONNECTIVITY_RESOLVED.source,
      { timeout },
    )
    .then(() => true)
    // Still `connecting`. The sweeps proceed — `settleDom` is their backstop — but the caller
    // is TOLD, so a pixel comparison can refuse instead of capturing an orange dot as green.
    .catch(() => false)
}

/** Block until the DOM stops changing for `quietMs` — the generic settle that covers every
 *  async arrival the harness cannot enumerate by name.
 *
 *  🪤 WHY QUIESCENCE AND NOT A BIGGER `waitForTimeout`. A fixed wait cannot establish rest,
 *  because load time is a property of the HOST while quiescence is a property of the PAGE —
 *  this scales with whatever the machine is doing, which is the only form that survives being
 *  run on both a busy dev box and a 4-core runner.
 *
 *  🪤 AND QUIET ALONE WAS NOT A RESTING STATE, which is the mistake that made this the last
 *  line of defence it was never able to be. A pending fetch mutates nothing, so 400ms of DOM
 *  silence is routinely reached MID-LOAD and the screenshot records the skeleton — measured on
 *  `#/tools` at 2s injected API latency, this helper returned at 6053ms with 19 loading
 *  affordances still rendered. So the quiet window is now only half the predicate: when it
 *  elapses, the barrier also requires that nothing on screen says it is still loading, and
 *  re-arms if anything does. See the `LOADING_SELECTOR` note above for why neither half works
 *  alone and why they are one barrier rather than two stages.
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
 *  Non-throwing by design, like its siblings: a surface that never quiesces proceeds after
 *  the cap rather than reddening the gate for a reason that is not its clause. What makes
 *  that safe is that it now RETURNS whether it settled, so the one caller that cannot
 *  tolerate an unsettled page — the pixel comparison — refuses instead of recording the frame
 *  that won the race. Swallowing this is how a host-load artifact got published as a
 *  0.02–0.04 drift ratio; see `SettleReport`.
 *
 *  🕐 BUDGET 8s, one step above `settleShellChrome`'s 6s. `#/learning` fires ten
 *  independent one-shot reads on mount with no shared "all loaded" barrier, each mounting
 *  its own section the moment ITS fetch answers, so its tail is the longest in the suite.
 *  Worst case through `gotoRoute` is 0.4s + 6s (shell chrome) + 8s (this) + 2s (fades)
 *  ≈ 16.4s, leaving ~13.6s of the 30s test timeout for the sweeps' own assertions. That sum
 *  is the reason no cap is simply generous: raising them all exceeds the per-test budget on
 *  the routes that never quiesce. The pixel rail does not live inside that 30s and buys its
 *  own budgets — see `visual.spec.ts`. */
export async function settleDom(
  page: Page,
  quietMs = 400,
  timeout = 8_000,
  loadingSelector = LOADING_SELECTOR,
): Promise<boolean> {
  return page
    .evaluate(
      ({ quiet, cap, selector, perpetual }) =>
        new Promise<boolean>((resolve) => {
          let timer = 0
          const finish = (settled: boolean) => {
            window.clearTimeout(timer)
            window.clearTimeout(hardCap)
            obs.disconnect()
            size.disconnect()
            resolve(settled)
          }
          // The quiet window elapsed. That is HALF of a resting state: the other half is that
          // nothing on screen still says it is loading. If a skeleton is up, re-arm instead of
          // resolving — a pending fetch mutates nothing, so quiet alone is routinely reached
          // mid-load, and this is the exact frame the rail used to publish as "drift".
          const quiesced = () => {
            if (document.querySelectorAll(selector).length === 0) finish(true)
            else bump()
          }
          const bump = () => {
            window.clearTimeout(timer)
            timer = window.setTimeout(quiesced, quiet)
          }
          // 🪤 AN ELEMENT THAT MUTATES ON EVERY FRAME IS ANIMATING, AND AN ANIMATION IS A
          // RESTING STATE — the ruling `settleEntranceAnimations` already makes for infinite
          // CSS animations, applied to the case the Animations API cannot see. `#/chat`'s empty
          // state renders a 47×47 mark whose inline `style` is rewritten with a new
          // `transform: rotate(…)` on every frame from rAF, so `getAnimations()` reports ZERO
          // on it and `animations: 'disabled'` cannot freeze it either. Measured: 692 mutations
          // in 15s with a largest quiet gap of 85ms, i.e. `#/chat` can NEVER reach a 400ms quiet
          // window. Under the swallowed timeout that was invisible; enforced, it would red two
          // goldens that have no drift.
          //
          // So a mutation stream is followed only until it has fired `perpetual` times from the
          // SAME target and attribute. A real arrival mutates a handful of times across MANY
          // targets and keeps bumping; a frame-driven animator blows the count in well under a
          // second and then stops holding the barrier open. This narrows what counts as motion;
          // it does not widen what counts as a match — `maxDiffPixelRatio` is untouched.
          const streams = new WeakMap<Node, Map<string, number>>()
          const isPerpetual = (r: MutationRecord): boolean => {
            let byKey = streams.get(r.target)
            if (!byKey) {
              byKey = new Map()
              streams.set(r.target, byKey)
            }
            const key = `${r.type}|${r.attributeName ?? ''}`
            const n = (byKey.get(key) ?? 0) + 1
            byKey.set(key, n)
            return n > perpetual
          }
          const obs = new MutationObserver((records) => {
            // `every` would let one animator's frame hide a real arrival in the same batch, so
            // this asks whether ANY record in the batch is still worth waiting for.
            if (records.some((r) => !isPerpetual(r))) bump()
          })
          // Height changes, from ANY cause — including ones that edit no DOM at all, and
          // ones whose reflow lands after their mutation. `box: 'border-box'` so a padding
          // change counts too.
          const size = new ResizeObserver(bump)
          const hardCap = window.setTimeout(() => finish(false), cap)
          obs.observe(document.documentElement, {
            subtree: true,
            childList: true,
            attributes: true,
            characterData: true,
          })
          size.observe(document.documentElement, { box: 'border-box' })
          timer = window.setTimeout(quiesced, quiet)
        }),
      { quiet: quietMs, cap: timeout, selector: loadingSelector, perpetual: PERPETUAL_MUTATIONS },
    )
    // Navigation raced the evaluate. The caller's own assertions still hold, and an
    // unmeasurable settle is reported as unsettled rather than as quiescent.
    .catch(() => false)
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
 *  style. Non-throwing by design: on timeout we proceed exactly as before, so a route
 *  with a permanently-animating element degrades to today's behaviour instead of
 *  failing — but the caller is now TOLD, so a pixel comparison can refuse.
 *
 *  Deliberately NOT solved by emulating `prefers-reduced-motion`: that would skip the
 *  animated path entirely, and this gate should measure what a user actually sees.
 *
 *  🪤 A TRANSLUCENT VALUE IS NOT A FADE, AND READING IT AS ONE MADE THIS PREDICATE VACUOUS ON
 *  EVERY ROUTE — the same defect as the infinite-animation half below, in the other half, and
 *  it survived the fix that closed that one. `ui/NavRail.tsx:139` renders the rail's section
 *  labels with a permanent inline `style={{ opacity: 0.65 }}`. The rail lives in the SHELL and
 *  `assertShellMounted` requires it on every route, so on all 40 surfaces there was always at
 *  least one element whose inline opacity sits strictly between 0.01 and 0.99 with nothing
 *  animating it. `midFade` was therefore permanently TRUE, `waitForFunction` could never
 *  resolve, and this helper was a 2s no-op on every route in the suite — including for the
 *  a11y sweep, the consumer whose composited-contrast flake it was written for.
 *
 *  The fix is to test for CHANGE rather than for a value: an element mid-fade has a different
 *  opacity from one frame to the next, and a static 0.65 does not. `waitForFunction` polls on
 *  rAF by default, so consecutive invocations are consecutive frames, and the previous sample
 *  is kept on `window` to compare against. The first invocation has nothing to compare to and
 *  reports "still moving", which costs one frame and cannot produce a false settle.
 */
export async function settleEntranceAnimations(page: Page, timeout = 2_000): Promise<boolean> {
  return page
    .waitForFunction(
      () => {
        const w = window as unknown as { __pcFadeSample?: string }
        // Every inline opacity on the page, in document order, as one comparable string.
        const sample = Array.from(document.querySelectorAll<HTMLElement>('[style*="opacity"]'))
          .map((el) => el.style.opacity)
          .join('|')
        const previous = w.__pcFadeSample
        w.__pcFadeSample = sample
        const midFade = previous === undefined || previous !== sample
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
    .then(() => true)
    // A permanently-animating surface must not fail the scan — fall through, but SAY so, so a
    // pixel comparison can refuse a frame that is still mid-fade.
    .catch(() => false)
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

/** Assert a full-page screenshot matches the platform-qualified baseline — but only once the
 *  page is known to be AT REST.
 *
 *  🪤 THE PRECONDITION IS CHECKED FIRST, AND IT IS NOT A TOLERANCE. `maxDiffPixelRatio` stays
 *  at the config's 0.01, no golden is exempted and no route is skipped. What this refuses is a
 *  comparison whose INPUT is unknown: `gotoRoute`'s stages are non-throwing, so before this
 *  check a page that ran out of settle budget was screenshotted anyway and the difference was
 *  attributed to the pixels. A screenshot taken mid-load differs from one taken at rest by
 *  whole content bands — a skeleton is a different HEIGHT from the rows that replace it, and a
 *  `fullPage` capture's dimensions are the page height — which is why this rail could report
 *  0.02–0.04 ratios with a DIFFERENT failing set on every run at one sha.
 *
 *  So an exhausted stage fails HERE, naming the stage, rather than being laundered into a
 *  drift number. That is strictly stronger than the old behaviour: it neither skips the route
 *  (which would let real drift through) nor accepts more pixels — it reports the measurement as
 *  invalid, which is what it was. The actionable response is a bigger budget or a quieter host,
 *  and the message says so; re-capturing would only bake the racing frame into the golden,
 *  which is how this rail broke the last two times. */
export async function expectRouteScreenshot(
  page: Page,
  name: string,
  settle: SettleReport,
): Promise<void> {
  expect(
    settle.exhausted,
    `${name}: the page never reached rest — ${settle.exhausted.join(', ')} ran out of budget, so\n` +
      `there is NOTHING here worth diffing. This is NOT render drift and the golden is NOT stale:\n` +
      `a screenshot taken mid-load differs from one taken at rest by whole content bands, which is\n` +
      `why this rail used to report 0.02–0.04 ratios with a different failing set on every run at\n` +
      `one sha. Do NOT run \`e2e:update\` — that records the racing frame as the baseline and moves\n` +
      `the failure to the next run. Raise the stage's budget in \`visual.spec.ts\`, or run on a\n` +
      `quieter host.`,
  ).toEqual([])
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

/** Refuse to compare pixels against a gateway a SIBLING SPEC has already written to.
 *
 *  🪤 THE VISUAL RAIL'S PRECONDITION IS THE WHOLE GATEWAY, NOT THE ROUTE. `e2e/README.md` states
 *  the invariant the 40 goldens were captured under — "data-backed routes still render their
 *  EMPTY state — the gateway's home is fresh — which is a valid baseline: we guard *chrome*, not
 *  data". That holds for the home at BOOT, and `playwright.config.ts` wipes it per run. It does
 *  NOT hold for the whole run: `a11y.spec.ts`, `chat.spec.ts` and `sessionMap.spec.ts` drive REAL
 *  scripted turns through `driveScriptedTurns`, into the ONE gateway every spec shares, and a turn
 *  writes flywheel state on two independent paths —
 *    · `src/personalclaw/context.py:211` records an allocation sample for EVERY ambient render, so
 *      `utilization.mean` stops being null ("no ambient render recorded yet" → "52% used"); and
 *    · `src/personalclaw/workflows/controller.py:4530` calls `run_end.capture()` at run end, so
 *      `capture.passes` stops being 0 ("1 of 1 pass clean").
 *  `#/learning` reads both back through `GET /api/learning/health` (`HealthPanel.tsx`), so its
 *  golden renders "not measured yet — nothing has run" before any turn and live numbers after one.
 *  With `fullyParallel`, WHICH of the two a mixed run captures is decided by the worker schedule —
 *  so one committed golden is simultaneously correct and wrong depending on the command that ran.
 *
 *  Measured 2026-09-19 on Darwin at 1-min load 7.06: `npm run e2e:visual` is 40/40 zero-diff. A run
 *  that also contains the turn-driving specs reds `learning-light`/`learning-dark` at ~14,900 px,
 *  with the golden showing the empty flywheel and the actual showing live values. Raising
 *  `maxDiffPixelRatio` could never address that — the two renderings are different CONTENT, not
 *  sub-pixel noise — and neither could re-capturing, which only moves the same race to the other
 *  side.
 *
 *  So the rail asserts its precondition instead of silently recording whichever side won the race.
 *  The failure names the INVOCATION, because the invocation is the bug. */
export async function assertPristineFlywheel(page: Page): Promise<void> {
  // 🪤 FAIL-OPEN ON THE READ, DELIBERATELY, AND ONLY ON THE READ. This is a PRECONDITION check on
  // a local rail, not a product assertion: what it protects — a golden captured under a different
  // gateway state than it is verified against — is already structurally prevented by `e2e` and
  // `e2e:update` each running this spec as their own invocation. The check is the backstop for a
  // direct `playwright test`, so a backstop that can itself red 40 goldens (a throwing request, an
  // endpoint outage, a shape change) would be strictly worse than the bug it guards. An unreadable
  // signal therefore yields to the golden it was protecting: `#/learning` renders a broken read as
  // that page's own `LoadError` and must red THERE, naming the outage, rather than failing the 38
  // goldens that never touch this endpoint. 404 is the ordinary case — `learning is disabled`
  // (`dashboard/handlers/learning.py::_enabled`) — and an install with no flywheel has no flywheel
  // state to contaminate, so it is pristine by definition rather than undetermined.
  //
  // What is NOT fail-open is the comparison below: once the signal is READ, a contaminated gateway
  // is a hard failure. That split is the whole point — an unknown is not the same claim as a known
  // dirty, and collapsing the two is how this defect survived a green run in the first place.
  let health: { composite?: { measured?: number } }
  try {
    const res = await page.request.get('/api/learning/health?days=7')
    if (!res.ok()) return
    health = (await res.json()) as { composite?: { measured?: number } }
  } catch {
    return
  }
  expect(
    health.composite?.measured ?? 0,
    'the flywheel already holds captured state, so this gateway is NOT the fresh home the 40\n' +
      'goldens were captured against: a sibling spec (a11y / chat / sessionMap) has driven a real\n' +
      'scripted turn through the gateway every spec shares. `#/learning` would be compared in its\n' +
      'POPULATED rendering against a golden captured EMPTY, and the diff would name the page\n' +
      'instead of the run. Give the visual rail its own invocation — `npm run e2e:visual` to\n' +
      'verify, `npm run e2e:update` to recapture; `npm run e2e` already does exactly that.',
  ).toBe(0)
}

/** Drive ONE real scripted chat turn and return with the transcript settled.
 *
 *  Shared because two specs need a STARTED session rather than the empty `#/chat` route:
 *  `sessionMap.spec.ts` (the map only exists in-session) and `a11y.spec.ts`'s mobile case. The
 *  recipe is `chat.spec.ts`'s, which owns the reasoning for each step — the CodeMirror composer,
 *  the aria-disabled send control, and "the turn ENDED" being two independent readings. Kept to
 *  the preconditions those specs need: this helper does not re-assert chat.spec.ts's clause.
 *
 *  `turns` sends the same prompt N times. The script fixture's `on_exhausted: repeat_last` means
 *  every turn gets the same reply, which is what makes a transcript long enough to SCROLL.
 *
 *  🔑 AND `turns` IS NOW A GUARANTEE RATHER THAN AN INTENTION. Callers index the session by prompt
 *  number and by mark order, so "6 sends produced 5 turns" is not a degraded start — it silently
 *  re-numbers every turn after the loss and turns the caller's next assertion into a claim about a
 *  session that does not exist. The loop therefore asserts the transcript's own per-turn row counts
 *  after every send (see the 🪤 inside), and reds at the send that was lost. `chat.spec.ts` states
 *  the discipline this restores: composer state and transcript state are two INDEPENDENT readings
 *  of "finished", and both belong inside the loop. */
export async function driveScriptedTurns(page: Page, prompt: string, turns = 1): Promise<void> {
  const composer = page.getByRole('textbox', { name: 'Message input' })
  await expect(composer).toBeVisible({ timeout: 15_000 })
  // ── THE TWO TRANSCRIPT READINGS THIS HELPER IS COUNTED BY ──────────────────────────────────
  // One row per USER turn: `ChatPage.tsx:3282` renders it as `{!streaming && <UserActions …/>}`,
  // so the count is "how many turns have landed" AND "nothing is streaming" in one reading.
  // One row per ASSISTANT turn: `actions={!(isLast && streaming) && <AssistantActions …/>}`
  // (`ChatPage.tsx:3288`), and `Speak` is the one control unconditional inside it — so the newest
  // turn joins this count only once it has STOPPED streaming. `exact` on both, and for the assistant
  // row it is load-bearing: a turn whose audio is playing re-labels that same button to 'Stop'
  // (`MessageActions.tsx:71`), which would then collide with the composer's own Stop. Nothing in
  // this harness speaks, so the count is whole; if something ever does, it undercounts and reds
  // rather than passing on the wrong control.
  const userTurnRows = page.getByRole('button', { name: 'Edit & resend', exact: true })
  const assistantTurnRows = page.getByRole('button', { name: 'Speak', exact: true })
  for (let n = 0; n < turns; n++) {
    await composer.click()
    await composer.pressSequentially(`${prompt} (${n + 1})`, { delay: 3 })
    const send = page.getByRole('button', { name: 'Send message', exact: true })
    await expect(
      send,
      'the composer refused the draft — send stayed aria-disabled, so no turn was ever started',
    ).not.toHaveAttribute('aria-disabled', 'true')
    await send.click()
    // 🪤 THE BARRIER IS A POSITIVE COUNT, AND THE ABSENCE CHECK IT REPLACES WAS SATISFIED BEFORE
    // THE TURN BEGAN — measured, not reasoned. This loop used to wait on
    // `expect(Stop).toHaveCount(0)`, which is true both AFTER a turn ends and BEFORE it starts:
    // the composer's action button only morphs to Stop once the stream is live. In run
    // 35949502119 (`e2e-a11y`, `sessionMap.spec.ts`) turn 1's wait returned in 386 ms while turn
    // 1's stream was still to come, and turn 2's wait then absorbed 5594 ms of it — i.e. turn 2
    // was TYPED AND SENT into a live run. That send was swallowed: the session ended with FIVE
    // user turns for six sends (`[data-session-mark][data-kind="user"]` counted 5 in the trace),
    // prompt `(2)` was absent from the transcript, and the red landed 300 lines later in
    // sessionMap.spec.ts as "the rail tick did not bring its turn on screen" — blaming the rail
    // for a turn that was never sent.
    //
    // A count cannot be satisfied early, because it is MONOTONE in the thing being waited for:
    // before this send the assistant rows numbered n, so n+1 can only mean "this turn produced a
    // reply and that reply finished". And the exact user-row count is what names the loss AT the
    // send that lost it, which is the whole point — a helper that promises N turns and delivers
    // N-1 silently is how a functional defect becomes somebody else's flake.
    await expect
      .poll(() => assistantTurnRows.count(), {
        message:
          `turn ${n + 1} never completed: the transcript still holds ${n} assistant action row(s), so the\n` +
          `reply either never arrived or is still streaming. The scripted provider answers every turn, so\n` +
          `this is the gateway or the socket, not the fixture.`,
        timeout: 90_000,
      })
      .toBeGreaterThanOrEqual(n + 1)
    await expect(
      userTurnRows,
      `SEND ${n + 1} OF ${turns} DID NOT BECOME A TURN. The transcript carries a different number of user\n` +
        `turns than this helper has sent, so every later assertion that assumes "the Nth turn is prompt\n` +
        `(N)" is now measuring a session that does not exist. A send issued while the previous run is\n` +
        `still live is absorbed rather than queued, and the composer looks idle in that window — so this\n` +
        `is the barrier above having let the loop type too early, or the product's send guard.`,
    ).toHaveCount(n + 1, { timeout: 30_000 })
    // The composer's own idle reading, independent of the two transcript ones (chat.spec.ts's
    // point: a half-finished turn fails one of them).
    await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeVisible({ timeout: 30_000 })
  }
  await expect(
    page.getByRole('button', { name: 'Regenerate', exact: true }),
    'the newest assistant turn has no action row — the transcript still considers the turn in flight',
  ).toBeVisible({ timeout: 30_000 })
  await settleEntranceAnimations(page)
}

/** The Session Map's one named control (§A.8) — reachable directly or from the header's
 *  overflow `…` menu, which is the only two places `ui/HeaderActions` can put a control.
 *
 *  Resolved through a helper rather than a bare `getByRole` because "reachable" is the clause
 *  SSM-10 asserts at a mobile viewport, and at 390px the header cluster sheds controls into the
 *  menu. A test that only looked in the row would report "mobile lost its session nav" for a
 *  control that is one tap away — and one that only looked in the menu would miss it on desktop.
 *  Returns the located control, or `null` when it is genuinely in neither place. */
export async function openHeaderOverflowIfNeeded(page: Page, name: string): Promise<boolean> {
  const direct = page.getByRole('button', { name, exact: true })
  if (await direct.count()) return true
  // `ui/HeaderActions` names its overflow trigger "More actions" and renders each shed control
  // as a `ui/Popover` `MenuRow` — a plain <button>, so the same locator finds it either way.
  const more = page.getByRole('button', { name: 'More actions', exact: true }).first()
  if (!(await more.count())) return false
  await more.click()
  await direct.first().waitFor({ state: 'visible', timeout: 4_000 }).catch(() => { /* reported by the caller's assertion */ })
  return (await direct.count()) > 0
}
