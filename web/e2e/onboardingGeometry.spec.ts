import { test, expect, type Page } from '@playwright/test'
import { writeFile } from 'node:fs/promises'

// ── THE GEOMETRY RAIL FOR FIRST RUN ──────────────────────────────────────────────────────────────
//
// The owner, on the installed app: "onboarding page can get pretty long and is not scrollable."
// That was true on a box which ALREADY had `overflow-y-auto`, which is why it reads as impossible
// until something measures it. Two shapes put content outside the scrollable range:
//
//   1. `items-center` ON the scroller. When the flex item is taller than the box,
//      `align-items: center` distributes the overflow to BOTH ends — and the part past the START
//      edge cannot be reached, because `scrollTop` clamps at 0 and `scrollHeight` never counted it.
//   2. the hero (`<h1>Welcome to …`) at `absolute bottom-full`: out of flow and ABOVE the flow
//      origin, so outside the range at every viewport height.
//
// 🔑 WHY THIS FILE EXISTS AND `design/onboardingScrollable.test.tsx` IS NOT ENOUGH. That rail
// asserts the two SHAPES from source, because jsdom computes no layout: every `clientHeight` is 0
// and every `getBoundingClientRect()` is all zeros there, so a numeric assertion written in vitest
// would have passed on the broken build. It is a good rail and it stays — but it can only recognise
// the two mistakes we already made. NO GATE IN `web/` COMPUTED GEOMETRY AT ALL, which is why every
// existing frontend rail was green on a first-run screen whose `<h1>` sat at **-247px** and whose
// "go back to step 1" button sat at **-141px**, both unreachable at every viewport. Neither
// `design/scrollRegionNamed` nor `design/overlaySurfaceA11y` even renders `Onboarding`, so the
// screen had no a11y coverage either; unreachable content is also keyboard-unreachable.
//
// So this rail asserts the PROPERTY instead of the shape, in a browser that really lays out:
// every element the user must reach lies inside the scroller's reachable content range. That is
// one inequality, and it does not care which CSS mistake violated it.
//
// ── THE FORMULA, because "off screen" and "unreachable" are different claims ──────────────────────
//
// A scroll container S can be scrolled over `[0, S.scrollHeight]`. An element E's position in that
// coordinate system is
//
//     contentTop = E.rect.top - (S.rect.top + S.clientTop - S.scrollTop)
//
// E is REACHABLE iff `contentTop >= 0` and `contentTop + E.height <= S.scrollHeight`. Below the
// fold satisfies both (scroll down); `contentTop < 0` satisfies neither at ANY scroll position, and
// that is the defect. The subtraction of `S.scrollTop` is what makes the measurement independent of
// where the box happens to be scrolled when it is taken.
//
// ── MEASURED WITH THIS SPEC, step 2, `contentTop` of the two stranded elements ───────────────────
//
// BEFORE is `8fa584c94` (the last sha before the layout fix landed); AFTER is the fix. Both runs use
// the pinned `IMPORT_SCAN` below, so the two columns are comparable.
//
//   viewport            h1 "Welcome to …"        "Go back to step 1: Your name"
//   1280×800     -197.0 → +140.0                  -91.0 → +246.0
//   1280×700     -247.0 → +140.0                 -141.0 → +246.0
//   1024×600     -297.0 → +140.0                 -191.0 → +246.0
//    390×844     -248.5 → +140.0                 -142.5 → +246.0
//
// and the bottom edge, same order: "Skip setup and go to the dashboard" ended at 896.0 / 846.0 /
// 796.0 / 991.5 in viewports 800 / 700 / 600 / 844 px tall, with `scrollTop` already at its maximum.
// After the fix the scroller reports 1257 content px (1331 at 390) and the same control sits at
// 1207 → 1233 — inside the range, and reached by scrolling.
//
// Against the shipped build 9 of this file's 15 legs red. Against an isolated re-introduction of
// just `items-center` on the scroller (the hero left in flow), 7 of 15 red, with the `<h1>` at
// -88.5px at 1280×800 — so the rail recognises each half of the defect on its own, not only the
// pair. A rail that was never seen failing is the thing that let this ship.
//
// The numbers move with the step's content; the INEQUALITY is what is asserted.

/** The viewports first run must survive.
 *
 *  1280×700 and 1024×600 are where the owner's install broke — a 13" laptop with browser chrome,
 *  and the smallest desktop this product claims. 390×844 is the phone contract `walkthrough.spec.ts`
 *  already uses. 1280×800 is the roomy case, and it is here because it ALSO failed: `items-center`
 *  clips the start edge the moment content exceeds the box by one pixel, so a rail that only tested
 *  short viewports would have called the defect a small-screen problem. */
const VIEWPORTS = [
  { label: '1280x800', width: 1280, height: 800 },
  { label: '1280x700', width: 1280, height: 700 },
  { label: '1024x600', width: 1024, height: 600 },
  { label: '390x844', width: 390, height: 844 },
] as const

/** The step-2 content, FIXED — so that the geometry is the only variable in this rail.
 *
 *  🔑 WITHOUT THIS THE RAIL IS VACUOUS ON THE RUNNER, and that was measured, not feared. Step 2
 *  ("Bring your setup over") reads `GET /api/onboarding/import`, which scans the HOST for other
 *  agent tools. A CI runner has none, so the step renders one honest line, first run fits inside
 *  every viewport, and the reachability inequalities below are all satisfied by a page that never
 *  overflows — on the broken build included. Measured on this dev box against the live scan: only
 *  1024×600 overflowed enough to strand the `<h1>`; 1280×800, 1280×700 and 390×844 all passed on the
 *  build whose `<h1>` the owner could not see.
 *
 *  So the content is pinned to a payload with two detected tools and all five categories — the
 *  ordinary case for the user this step exists for, and tall enough that all four viewports
 *  overflow. Nothing about the LAYOUT is stubbed: the same components render the same DOM, and the
 *  numbers are then comparable between a dev box and a runner, which is what a geometry baseline
 *  needs. The live scan keeps its own coverage in `onboarding/importStep.test.tsx`. */
const IMPORT_SCAN = {
  categories: ['instructions', 'memories', 'mcp_servers', 'skills', 'settings'],
  sources: [
    {
      source: 'claude-code', display_name: 'Claude Code', root: '~/.claude', present: true, detected: true,
      counts: { instructions: 2, memories: 3, mcp_servers: 2, skills: 4, settings: 1 },
      secrets_skipped: 1, redactions: 2, notes: [],
      items: [
        ['instructions', 'CLAUDE.md'], ['instructions', 'AGENTS.md'],
        ['memories', 'project overview'], ['memories', 'engineering doctrine'], ['memories', 'dev playbook'],
        ['mcp_servers', 'filesystem'], ['mcp_servers', 'github'],
        ['skills', 'run'], ['skills', 'code-review'], ['skills', 'simplify'], ['skills', 'init'],
        ['settings', 'settings.json'],
      ].map(([category, key], i) => ({
        fingerprint: `cc-${i}`, source: 'claude-code', category, key, title: key,
        state: i % 5 === 0 ? 'existing' : 'new', destination: '', detail: i % 5 === 0 ? 'already imported' : '',
        secrets_skipped: 0, redactions: 0,
      })),
    },
    {
      source: 'codex', display_name: 'Codex', root: '~/.codex', present: true, detected: true,
      counts: { instructions: 1, memories: 1, mcp_servers: 1, skills: 1, settings: 1 },
      secrets_skipped: 0, redactions: 0, notes: [],
      items: [
        ['instructions', 'AGENTS.md'], ['memories', 'house style'], ['mcp_servers', 'sqlite'],
        ['skills', 'review'], ['settings', 'config.toml'],
      ].map(([category, key], i) => ({
        fingerprint: `cx-${i}`, source: 'codex', category, key, title: key,
        state: 'new', destination: '', detail: '', secrets_skipped: 0, redactions: 0,
      })),
    },
  ],
}

/** Everything the browser puts in the tab ring. An unreachable control is a WCAG 2.1.1 failure,
 *  not a cosmetic one, so the reachability set is exactly the operable set — plus the `<h1>`, which
 *  is not operable but is the product's name and was the first thing lost. */
const OPERABLE = 'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])'

/** A rounding allowance. Sub-pixel layout puts an element at -0.5 while it is visually flush, and a
 *  half pixel is not a reachability defect; -141 and -247 are. Deliberately tight: this is a
 *  tolerance on MEASUREMENT, not on the property. */
const EPS = 1

interface Box {
  label: string
  /** Position within the scroller's content, i.e. what `scrollTop` addresses. */
  contentTop: number
  contentBottom: number
  /** Position in the viewport at the moment of measurement — what the user can see right now. */
  viewportTop: number
  viewportBottom: number
  width: number
  height: number
  right: number
}

interface Geometry {
  /** How many scroll containers the flow renders. One, or the rest of this is measuring the wrong box. */
  scrollerCount: number
  scrollHeight: number
  clientHeight: number
  scrollWidth: number
  clientWidth: number
  scrollTop: number
  /** `scrollHeight - clientHeight` — the largest `scrollTop` the box will accept. */
  maxScrollTop: number
  /** The scroller's own client edges in viewport space, so "inside the box" is checkable. */
  boxTop: number
  boxBottom: number
  heading: Box | null
  operable: Box[]
  /** Page-level sideways overflow: a phone must not pan. */
  docScrollWidth: number
  docClientWidth: number
}

/** Read the whole geometry of the flow in ONE evaluate, so every number shares one layout pass.
 *
 *  Taken as separate calls, `scrollTop` could change between the origin and the rects and the
 *  content-space numbers would be silently wrong — which is the failure mode a geometry rail can
 *  least afford. */
async function readGeometry(page: Page): Promise<Geometry> {
  return page.evaluate(
    ({ operableSelector }) => {
      const scrollers = Array.from(document.querySelectorAll<HTMLElement>('div')).filter((el) => {
        const oy = getComputedStyle(el).overflowY
        return oy === 'auto' || oy === 'scroll'
      })
      const s = scrollers[0]
      if (!s) {
        return {
          scrollerCount: 0, scrollHeight: 0, clientHeight: 0, scrollWidth: 0, clientWidth: 0,
          scrollTop: 0, maxScrollTop: 0, boxTop: 0, boxBottom: 0, heading: null, operable: [],
          docScrollWidth: document.documentElement.scrollWidth,
          docClientWidth: document.documentElement.clientWidth,
        }
      }
      const sr = s.getBoundingClientRect()
      // Content y=0 expressed in viewport coordinates. `clientTop` is the top border: the scroll
      // origin sits inside it, and ignoring it puts every measurement off by the border width.
      const originY = sr.top + s.clientTop - s.scrollTop
      const box = (el: Element, label: string) => {
        const r = el.getBoundingClientRect()
        return {
          label,
          contentTop: r.top - originY,
          contentBottom: r.bottom - originY,
          viewportTop: r.top,
          viewportBottom: r.bottom,
          width: r.width,
          height: r.height,
          right: r.right,
        }
      }
      // A control with no box is display:none or inside a collapsed step body — it is not on
      // screen, so it is neither reachable nor unreachable. Measuring it would report the
      // collapsed rows' contents as defects on every step.
      const operable = Array.from(document.querySelectorAll<HTMLElement>(operableSelector))
        .filter((el) => {
          const r = el.getBoundingClientRect()
          return r.width > 0 && r.height > 0 && el.closest('[aria-hidden="true"]') === null
        })
        .map((el, i) => box(el, `${el.tagName.toLowerCase()}[${i}] ${(el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 46)}`))
      const h1 = document.querySelector('h1')
      return {
        scrollerCount: scrollers.length,
        scrollHeight: s.scrollHeight,
        clientHeight: s.clientHeight,
        scrollWidth: s.scrollWidth,
        clientWidth: s.clientWidth,
        scrollTop: s.scrollTop,
        maxScrollTop: s.scrollHeight - s.clientHeight,
        boxTop: sr.top + s.clientTop,
        boxBottom: sr.top + s.clientTop + s.clientHeight,
        heading: h1 ? box(h1, `h1 ${h1.textContent?.trim().slice(0, 40)}`) : null,
        operable,
        docScrollWidth: document.documentElement.scrollWidth,
        docClientWidth: document.documentElement.clientWidth,
      }
    },
    { operableSelector: OPERABLE },
  )
}

/** How many consecutive animation frames must agree before the layout counts as at rest. Eight is
 *  ~130 ms at 60 Hz: well under the ~450 ms the step transition takes, so it costs the run nothing,
 *  and long enough that the near-plateau inside that transition (five consecutive samples at 800–801
 *  px of content height) cannot satisfy it — the elements under the collapsing body keep moving
 *  through it, which is why the fingerprint below is not `scrollHeight` alone. */
const SETTLE_FRAMES = 8

/** Wait until the layout this rail measures has STOPPED MOVING, and fail loudly if it never does.
 *
 *  🔴 THE BOTTOM-EDGE LEG BELOW HAS A ~450 ms WINDOW IN WHICH IT IS WRONG, and this is what closes
 *  it. Advancing a step collapses step 1's body BEFORE step 2's expands, so the scroller's content
 *  height DIPS while step 2's controls are already laid out at their final places. Measured at
 *  1280×700, sampling every ~45 ms from the Continue click:
 *
 *      t(ms)   scrollHeight   running finite animations   elements past scrollHeight
 *       1061        925                  2                           0
 *       1151        846                  4                           4
 *       1287        801                  2                           5
 *       1507        800                  2                           5
 *       1552       1319                  1                           0
 *       1598       1319                  0                           0    ← at rest, and stays there
 *
 *  So for ~450 ms the box reports 800 px of content while `Import selected` sits at 972.6 px, and the
 *  leg's own message reads "5 element(s) extend past the scroller's content height" about a frame no
 *  user ever sees. It is this rail's race and NOT a regression: the identical trough is on
 *  `origin/main` (739–851 px against a resting 1257, six elements stranded), so only the numbers move
 *  with whatever content the build renders.
 *
 *  🪤 WHAT THE PREVIOUS WAIT ACTUALLY GUARANTEED, precisely — because it is less than it looks. It was
 *  `getAnimations().filter(running).every((a) => iterations === Infinity)`, and `Array.every` on an
 *  EMPTY list is `true`, so it returns on any poll that finds nothing running. It also `.catch()`-ed
 *  its own timeout and measured anyway. Neither hole was caught FIRING here: sampling all 90 frames
 *  after the advance, that predicate was satisfied on 0 frames while anything overflowed (27 frames
 *  overflowed). What it depends on is a COINCIDENCE — that some WAAPI animation outlasts the height
 *  spring, which framer drives in JS and which therefore never appears in `getAnimations()` itself.
 *  On this host the coincidence held on every frame; the CI frame that fired is not reproduced here,
 *  and a wait whose correctness rests on one animation outliving another is exactly the kind that
 *  holds on the box you test it on.
 *
 *  🔑 SO THE WAIT IS ON WHAT IS MEASURED, and it takes BOTH conditions, because each alone is
 *  satisfiable mid-flight. `scrollHeight` plateaus INSIDE the trough (801, 801, 801, 800, 800 across
 *  five samples, while the collapsing body still moves every element under it), and "nothing finite is
 *  animating" is exactly what the old wait already believed. Together they held on 0 of those same 90
 *  frames while anything overflowed, and they stop depending on any particular animator: a stable
 *  content height AND a stable content bottom, for eight consecutive frames, is a claim about the
 *  geometry rather than about who was moving it.
 *
 *  It THROWS on timeout instead of measuring anyway. A page that never comes to rest is an absence of
 *  evidence about its geometry, so "first run never settled" is the honest failure — where the old
 *  `.catch()` produced a bottom-overflow list naming a defect that was not there. */
async function settle(page: Page): Promise<void> {
  await page.evaluate(() => { delete (window as unknown as Record<string, unknown>).__pcSettleRun })
  await page.waitForFunction(
    ({ frames, operableSelector }) => {
      const w = window as unknown as { __pcSettleRun?: { key: string; n: number } }
      const moving = document.getAnimations()
        .filter((a) => a.playState === 'running')
        .some((a) => a.effect?.getComputedTiming().iterations !== Infinity)
      const s = Array.from(document.querySelectorAll<HTMLElement>('div')).find((el) => {
        const oy = getComputedStyle(el).overflowY
        return oy === 'auto' || oy === 'scroll'
      })
      if (!s || moving) {
        w.__pcSettleRun = undefined
        return false
      }
      // The fingerprint is the inequality's own inputs: the content height, and how far down the
      // content the last operable thing ends. Taken in CONTENT space (minus `scrollTop`) so that a
      // smooth `scrollIntoView` — which is not a Web Animation and so is invisible above — cannot
      // hold the wait open forever over a layout that is already at rest.
      const sr = s.getBoundingClientRect()
      const originY = sr.top + s.clientTop - s.scrollTop
      const bottoms = Array.from(document.querySelectorAll<HTMLElement>(operableSelector))
        .map((el) => el.getBoundingClientRect().bottom - originY)
      const key = `${s.scrollHeight}|${Math.round(Math.max(0, ...bottoms) * 10)}`
      const prev = w.__pcSettleRun
      w.__pcSettleRun = prev && prev.key === key ? { key, n: prev.n + 1 } : { key, n: 1 }
      return w.__pcSettleRun.n >= frames
    },
    { frames: SETTLE_FRAMES, operableSelector: OPERABLE },
    // 15 s is ~30× the transition it waits on. A budget this loose cannot mask a slow animation; it
    // only stops a starved host from being reported as a broken layout.
    { timeout: 15_000 },
  ).catch(() => {
    throw new Error(
      'first run never came to rest: the scroller\'s content height and the bottom of its content\n' +
        `kept changing (or a finite animation kept running) for 15s, so no ${SETTLE_FRAMES}-frame\n` +
        'window was stable enough to measure. Every geometry number below would be about a frame\n' +
        'nobody sees, which is an absence of evidence and not a defect — do NOT re-baseline anything\n' +
        'from this run.',
    )
  })
}

/** Set the scroller's `scrollTop` and report what the box ACCEPTED.
 *
 *  Returning the read-back value rather than the requested one is the whole point: a box that
 *  cannot scroll silently clamps to 0, and that clamp is the defect's mechanism. */
async function setScrollTop(page: Page, to: number): Promise<number> {
  return page.evaluate((target) => {
    const s = Array.from(document.querySelectorAll<HTMLElement>('div')).find((el) => {
      const oy = getComputedStyle(el).overflowY
      return oy === 'auto' || oy === 'scroll'
    })
    if (!s) return -1
    s.scrollTop = target
    return s.scrollTop
  }, to)
}

/** Render FIRST RUN without writing a byte to the gateway every other spec shares.
 *
 *  `app/identity.tsx` derives `onboarded` from a non-empty server `user_name`, and
 *  `playwright.config.ts` seeds one so the rest of the suite gets the app shell. Blanking it in the
 *  RESPONSE — not in the gateway — is what makes this spec a fresh install without a second gateway
 *  and without contaminating anyone: `App.tsx` then renders `<Onboarding />` and holds the route.
 *
 *  Both WRITE paths are stubbed too, so the flow cannot persist anything even if a future
 *  assertion clicks something that finishes it. `saveOnboardingState` is fire-and-forget by design
 *  (`Onboarding.tsx`'s `progress`), so a stubbed 200 is indistinguishable from the real one here. */
async function renderFirstRun(page: Page): Promise<void> {
  await page.route('**/api/dashboard/config', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' })
      return
    }
    const res = await route.fetch()
    const body = (await res.json()) as Record<string, unknown>
    await route.fulfill({ response: res, json: { ...body, user_name: '', username: '' } })
  })
  await page.route('**/api/onboarding/state', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true,"state":{}}' }),
  )
  await page.route('**/api/onboarding/import', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(IMPORT_SCAN) }),
  )
  await page.goto('/#/onboarding')
  // The floor. A blank page, a 401 or a gateway outage all render SOMETHING, and every inequality
  // below is vacuously satisfied by a page with no controls on it.
  await expect(
    page.getByRole('heading', { level: 1, name: /Welcome to/ }),
    'first run never rendered — the identity stub did not take, or the gateway is unreachable. Every\n' +
      'assertion in this file is vacuous on a page with no onboarding on it.',
  ).toBeVisible({ timeout: 20_000 })
  // Let the entrance spring and the step body's height animation land: a mid-animation height is a
  // real number about a frame nobody sees, and this rail must measure the resting layout.
  await settle(page)
}

/** Step 2's subtitle. `StepStack` renders a step's subtitle ONLY while that step is active, so this
 *  is the arrival signal — and it is prose rather than a role, deliberately: the geometry legs must
 *  be able to measure a tree in which the step titles are not yet headings. */
const STEP_2_ACTIVE = /Already use another local agent tool\?/

/** Advance from the name step to step 2 — "Bring your setup over", the longest step and the one the
 *  original measurement was taken on. */
async function advanceToImportStep(page: Page): Promise<void> {
  await page.getByRole('textbox', { name: 'Your name' }).fill('Keyur')
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page.getByText(STEP_2_ACTIVE)).toBeVisible({ timeout: 10_000 })
  await settle(page)
}

/** One line per measured element, so a failure carries its own before/after table. */
const table = (boxes: Box[]) =>
  boxes.map((b) => `    ${b.contentTop.toFixed(1).padStart(8)} → ${b.contentBottom.toFixed(1).padStart(8)}  ${b.label}`).join('\n')

for (const vp of VIEWPORTS) {
  test.describe(`first run is fully reachable at ${vp.label}`, () => {
    test.use({ viewport: { width: vp.width, height: vp.height } })

    test('every control and the product title lie inside the scroller`s reachable range', async ({ page }, testInfo) => {
      await renderFirstRun(page)
      await advanceToImportStep(page)
      const g = await readGeometry(page)

      // 🔑 PUBLISH THE MEASUREMENT, PASS OR FAIL. A rail that only speaks when it is angry gives you
      // a verdict and no baseline: "onboarding is reachable" is unfalsifiable prose next to
      // "h1 at -247.0 → +28.0". What made the original defect arguable for as long as it was, is
      // that nobody had a number for it.
      //
      // 🪤 WRITTEN TO A FILE, not attached as an inline `body`. An inline attachment lives only in
      // the report, and `ci.yml` uploads `web/test-results/` — not `web/playwright-report/` — so an
      // inline body would have been discarded on exactly the runs somebody wants to read. Going
      // through `testInfo.outputPath` puts it where the existing upload step already looks.
      const report =
        `${vp.label}  scroller ${g.scrollHeight} content / ${g.clientHeight} client ` +
        `(maxScrollTop ${g.maxScrollTop})\n  contentTop → contentBottom\n` +
        table([g.heading as Box, ...g.operable])
      const out = testInfo.outputPath(`geometry-${vp.label}.txt`)
      await writeFile(out, `${report}\n`)
      await testInfo.attach(`geometry-${vp.label}.txt`, { path: out, contentType: 'text/plain' })

      // ── The floors, first. Each of the inequalities below is trivially true on a page with one
      //    scroller and nothing in it, so the population is asserted before the property.
      expect(
        g.scrollerCount,
        'first run must declare exactly ONE scroll container. Two nested scrollers make "reachable"\n' +
          'ambiguous (reachable in which one?) and zero means this spec measured a different page.',
      ).toBe(1)
      expect(g.clientHeight, 'the scroll container has no height — nothing was laid out').toBeGreaterThan(200)
      expect(
        g.operable.length,
        'no operable controls on first run. The flow has a name field, a Continue arrow and a skip\n' +
          'link at minimum, so this is a broken render, not a clean page.',
      ).toBeGreaterThan(2)
      expect(g.heading, 'no <h1> on first run — the product never names itself').not.toBeNull()
      // 🔑 THE ANTI-VACUITY FLOOR. Every inequality below holds trivially on content that fits, and
      // the defect this rail exists for only EXISTS under overflow. The fixture above is sized so
      // all four viewports overflow, so a viewport that stops overflowing means the content shrank
      // and this rail quietly stopped testing anything.
      expect(
        g.scrollHeight,
        `${vp.label} no longer overflows (scrollHeight ${g.scrollHeight} <= clientHeight ${g.clientHeight}).\n` +
          'Every assertion below is then vacuous — content that fits is reachable by definition. Grow\n' +
          'IMPORT_SCAN until it overflows again, or drop the viewport; do NOT leave this green.',
      ).toBeGreaterThan(g.clientHeight)

      // ── THE PROPERTY. Nothing the user must reach sits before the scroll origin.
      const measured = [g.heading as Box, ...g.operable]
      const above = measured.filter((b) => b.contentTop < -EPS)
      expect(
        above.map((b) => `${b.contentTop.toFixed(1)}px  ${b.label}`),
        `${above.length} element(s) sit ABOVE the scroll origin at ${vp.label}, so they cannot be\n` +
          'reached at ANY scroll position: `scrollTop` clamps at 0 and `scrollHeight` does not count\n' +
          'them. This is the `items-center`-on-a-scroller / `absolute bottom-full` defect class. The\n' +
          'fix is never a bigger viewport or a tolerance — put the centring on a `min-h-full` box\n' +
          'INSIDE the scroller, and keep the hero in flow. Measured content offsets:\n' +
          table(measured),
      ).toEqual([])

      // ── And nothing sits past the end, which is the same defect mirrored: `align-items: center`
      //    pushes overflow out of BOTH edges, and the bottom half is why "Skip setup" ended at
      //    800.5px in a 700px viewport.
      const below = measured.filter((b) => b.contentBottom > g.scrollHeight + EPS)
      expect(
        below.map((b) => `${b.contentBottom.toFixed(1)}px > scrollHeight ${g.scrollHeight}  ${b.label}`),
        `${below.length} element(s) extend past the scroller's content height at ${vp.label}. Scrolling\n` +
          'to the very bottom still would not bring them into view.\n' + table(measured),
      ).toEqual([])
    })

    test('the scroller really scrolls, and scrolling really reveals', async ({ page }) => {
      await renderFirstRun(page)
      await advanceToImportStep(page)
      const g = await readGeometry(page)

      expect(
        g.scrollHeight,
        `${vp.label} does not overflow, so this leg cannot exercise the scroll path at all. See the\n` +
          'anti-vacuity note on IMPORT_SCAN.',
      ).toBeGreaterThan(g.clientHeight)

      // `scrollTop` must actually move. On the broken build it did — the box scrolled DOWNWARD
      // fine; what it could never do was reach the content above 0. So this leg is the other
      // half of the pair, not a restatement.
      const atBottom = await setScrollTop(page, g.scrollHeight)
      expect(
        atBottom,
        `the box reports ${g.scrollHeight} of content in a ${g.clientHeight} viewport but refused to\n` +
          'scroll. Content taller than the box with a fixed scrollTop is content nobody can read.',
      ).toBeGreaterThan(0)
      expect(atBottom, 'scrollTop clamped somewhere other than scrollHeight - clientHeight').toBe(g.maxScrollTop)

      // Every control can be brought FULLY inside the box. This is the user-facing form of the
      // inequality above, and it is what a tolerance could never fake: an element outside the
      // reachable range stays outside after `scrollIntoView`.
      const stranded = await page.evaluate(
        ({ operableSelector, eps }) => {
          const s = Array.from(document.querySelectorAll<HTMLElement>('div')).find((el) => {
            const oy = getComputedStyle(el).overflowY
            return oy === 'auto' || oy === 'scroll'
          })!
          const out: string[] = []
          for (const el of Array.from(document.querySelectorAll<HTMLElement>(operableSelector))) {
            const r0 = el.getBoundingClientRect()
            if (r0.width === 0 || r0.height === 0) continue
            el.scrollIntoView({ block: 'nearest', inline: 'nearest' })
            const sr = s.getBoundingClientRect()
            const top = sr.top + s.clientTop
            const bottom = top + s.clientHeight
            const r = el.getBoundingClientRect()
            // Taller than the viewport is not stranded, it is big; only require its top to land.
            const fits = r.height <= s.clientHeight
            if (r.top < top - eps || (fits && r.bottom > bottom + eps)) {
              out.push(
                `${(el.getAttribute('aria-label') || el.textContent || el.tagName).trim().slice(0, 46)}` +
                  ` — after scrollIntoView it sits at ${r.top.toFixed(1)}–${r.bottom.toFixed(1)} ` +
                  `outside the box's ${top.toFixed(1)}–${bottom.toFixed(1)}`,
              )
            }
          }
          return out
        },
        { operableSelector: OPERABLE, eps: EPS },
      )
      expect(
        stranded,
        `${stranded.length} control(s) cannot be scrolled into view at ${vp.label}. A control the\n` +
          'scroller cannot reach is a control no mouse, no keyboard and no screen reader can operate.',
      ).toEqual([])
    })

    test('the primary action and the door out are reachable, and the page does not pan sideways', async ({ page }) => {
      await renderFirstRun(page)

      // Step 1's primary action, before anything is filled in — the one control the whole flow
      // depends on. Named, so a renamed button fails here rather than silently shrinking the set.
      const primary = page.getByRole('button', { name: 'Continue' })
      await expect(primary, 'step 1 has no Continue button — first run cannot be started').toBeVisible()
      await primary.scrollIntoViewIfNeeded()
      await expect(primary).toBeInViewport({ ratio: 1 })

      // The one door out of the flow, on every step but the last. It is the LAST element in the
      // panel, which is what made it the bottom-edge witness in the original measurement.
      const skip = page.getByRole('button', { name: /^Skip setup/ })
      await expect(skip, 'no skip link — the flow has become a gate').toBeVisible()
      await skip.scrollIntoViewIfNeeded()
      await expect(skip, 'the skip link cannot be brought fully on screen').toBeInViewport({ ratio: 1 })

      // Focus is its own reachability claim: `scrollIntoViewIfNeeded` is the harness's scroll, and
      // a keyboard user's is the browser's. They are different code paths.
      await skip.focus()
      expect(
        await page.evaluate(() => document.activeElement?.textContent?.slice(0, 20) ?? ''),
        'focusing the skip link did not move focus to it',
      ).toMatch(/Skip setup/)
      await expect(skip, 'focusing the skip link did not scroll it into view').toBeInViewport({ ratio: 1 })

      const g = await readGeometry(page)
      expect(
        g.docScrollWidth,
        `the page pans sideways at ${vp.label} (${g.docScrollWidth} > ${g.docClientWidth}). First run\n` +
          'is the one screen a user cannot skip, and horizontal scrolling on it is WCAG 1.4.10.',
      ).toBeLessThanOrEqual(g.docClientWidth + EPS)
      const wide = g.operable.filter((b) => b.right > vp.width + EPS).map((b) => `${b.right.toFixed(1)}px  ${b.label}`)
      expect(wide, `control(s) extend past the right edge at ${vp.label}`).toEqual([])
    })
  })
}

// ── Focus, which is the same property expressed in the keyboard's terms ──────────────────────────
//
// Unreachable content is unreachable by Tab too, so these legs live with the geometry rather than in
// a second file: they share the harness, the stub and the "measured in a browser" reason.
test.describe('first run is operable by keyboard alone', () => {
  test.use({ viewport: { width: 1280, height: 700 } })

  test('advancing a step moves focus to the new step, not to <body>', async ({ page }) => {
    await renderFirstRun(page)
    await page.getByRole('textbox', { name: 'Your name' }).fill('Keyur')
    // Enter in the field, i.e. the keyboard path — not a synthetic click on the arrow.
    await page.getByRole('textbox', { name: 'Your name' }).press('Enter')
    await expect(page.getByText(STEP_2_ACTIVE)).toBeVisible({ timeout: 10_000 })
    await expect(
      page.getByRole('heading', { name: /Bring your setup over/ }),
      'the step titles are not headings, so there is nothing for focus to land on and nothing for a\n' +
        'screen-reader user to navigate the five steps by. First run has ONE <h1> and no <h2>.',
    ).toBeVisible()
    const landed = await page.evaluate(() => {
      const a = document.activeElement
      return { tag: a?.tagName.toLowerCase() ?? 'none', text: (a?.textContent ?? '').trim().slice(0, 40) }
    })
    expect(
      landed,
      'advancing a step dropped focus. The control that had it (the name field / its arrow) unmounts\n' +
        'with the collapsing step body, so focus falls to <body> and a keyboard user has to Tab from\n' +
        "the top of the document to find where they are. Focus belongs on the new step's heading.",
    ).toMatchObject({ tag: 'h2', text: expect.stringContaining('Bring your setup over') })
  })

  test('every visible control is reachable by Tab, in document order', async ({ page }) => {
    await renderFirstRun(page)
    await advanceToImportStep(page)
    const expected = await page.evaluate(
      (selector) =>
        Array.from(document.querySelectorAll<HTMLElement>(selector))
          .filter((el) => {
            const r = el.getBoundingClientRect()
            return r.width > 0 && r.height > 0 && el.closest('[aria-hidden="true"]') === null
          })
          .map((el) => (el.getAttribute('aria-label') || el.textContent || el.tagName).trim().slice(0, 46)),
      OPERABLE,
    )
    expect(expected.length, 'no controls to walk — the page did not render').toBeGreaterThan(2)

    const reached: string[] = []
    // One press per control plus headroom for the two headings that take focus programmatically;
    // a cap rather than a `while` so a focus trap fails the test instead of hanging it.
    for (let i = 0; i < expected.length + 8; i++) {
      await page.keyboard.press('Tab')
      const at = await page.evaluate(() => {
        const a = document.activeElement as HTMLElement | null
        if (!a || a === document.body) return null
        return (a.getAttribute('aria-label') || a.textContent || a.tagName).trim().slice(0, 46)
      })
      if (at && !reached.includes(at)) reached.push(at)
    }
    const missed = expected.filter((name) => !reached.includes(name))
    expect(
      missed,
      `${missed.length} visible control(s) are not in the tab ring: a mouse can operate them and a\n` +
        `keyboard cannot (WCAG 2.1.1), on the first screen of the product.\n  reached: ${reached.join(' | ')}`,
    ).toEqual([])
  })
})
