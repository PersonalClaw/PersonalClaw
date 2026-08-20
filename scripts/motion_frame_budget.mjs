#!/usr/bin/env node
// Motion frame budget — measure the dashboard's real frame times WHILE it animates.
//
// `docs/design/motion.md` §2 calls motion "budgeted", and FM-7 asks for that budget to be
// proven at 60fps "with no jank" on ChatPage and a cockpit, at bounciness 1 and 0. A frame
// rate is the easiest measurement to fake, so this driver is built around what would make
// its number a lie:
//
//   1. **jsdom has no frame clock.** `requestAnimationFrame` in the unit tier is a
//      setTimeout shim — it can report any rate you like. This has to be a real browser,
//      which is why it lives next to `render_smoke.mjs` rather than in `web/src/**`.
//   2. **An idle page is not evidence.** 60fps while nothing moves proves nothing, so every
//      surface is MEASURED DURING a provoked interaction (route crossfade + entrance
//      cascade, an overlay opening on `physics.playful`, a wheel scroll, a hover sweep),
//      and each run also carries an `idle` control window so the reader can see what this
//      machine costs with the same page at rest. The control is the baseline, never the claim.
//   3. **A mean hides a stall.** "60fps" with one 400ms freeze is jank and averages to
//      nothing. So the unit of the report is the DISTRIBUTION of `requestAnimationFrame`
//      deltas — frames, mean, p50/p95/p99, worst, and the count over one 60Hz frame
//      (16.7ms) and over two (33.3ms). `worstMs` is what the clause's "no jank" is about.
//   4. **A harness that can't see a stall measures nothing.** `--inject-stall <ms>` blocks
//      the main thread synchronously from inside the measured window. If a run with
//      `--inject-stall 200` does not report a ~200ms `worstMs` outlier, the numbers from
//      every other run are void. Run it once before trusting anything here.
//   5. **A probe that navigates itself off the surface measures the wrong page.** Every
//      result carries the `hash` it was measured on, asserted against the surface's
//      expected route before the window opens (`#/loops` canonicalises to `#/loop`, and a
//      loop cockpit is `#/loops/<8hex>` — neither is guessable), plus a mountedness floor
//      (`elements`, and the shell rail) so an onboarding hijack or an unbuilt `dist`
//      cannot read as a clean surface.
//   6. **An unthrottled headless run is a generous environment.** The `env` block records
//      the binary, headless-ness, viewport, DPR and CPU-throttle rate so a number is never
//      quoted without them. `--cpu-throttle 4` applies CDP `Emulation.setCPUThrottlingRate`
//      to show what a slower machine sees.
//
// The harness also samples `document.getAnimations()` during the window (amortized to every
// 3rd frame, so the probe does not become the thing it measures) and reports
// `animationsPeak`. A measurement whose interaction window never observed a running
// animation did not provoke any motion, and the runner marks it `provokedMotion: false`
// rather than publishing a clean-looking number for a still page.
//
// USAGE — needs a running gateway serving the CURRENT web/dist (see docs/design/motion.md
// §8 for the recipe; `--seed demo-home` is what gives the cockpit a loop to open):
//
//     node scripts/motion_frame_budget.mjs --url http://127.0.0.1:10473
//     node scripts/motion_frame_budget.mjs --url ... --inject-stall 200   # falsify the harness
//     node scripts/motion_frame_budget.mjs --url ... --cpu-throttle 4     # slower machine
//     node scripts/motion_frame_budget.mjs --url ... --bounciness 1       # one dial only
//
// Deliberately NOT a Playwright spec under `web/e2e/`: that directory is the zero-diff
// GATE tier (visual baselines + axe), and CI runs exactly one file out of it by explicit
// path (`ci.yml` → `npx playwright test e2e/a11y.spec.ts`). A frame-time distribution has
// no committed baseline and no machine-independent threshold, so a green/red there would be
// a lie about hardware. This prints a measurement; a human reads it.

import { chromium } from 'playwright'
import { existsSync, readdirSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'

// ── CLI ─────────────────────────────────────────────────────────────────────
function parseArgs(argv) {
  const out = {
    url: process.env.PC_MOTION_URL || 'http://127.0.0.1:10473',
    bounciness: [1, 0],
    surfaces: null,
    cpuThrottle: Number(process.env.PC_MOTION_CPU_THROTTLE || 1),
    injectStall: 0,
    headed: false,
    chromium: process.env.PC_MOTION_CHROMIUM || '',
    windowMs: 3000,
    viewport: { width: 1440, height: 900 },
  }
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i]
    const next = () => argv[++i]
    if (a === '--url') out.url = next()
    else if (a === '--bounciness') out.bounciness = next().split(',').map(Number)
    else if (a === '--surfaces') out.surfaces = next().split(',')
    else if (a === '--cpu-throttle') out.cpuThrottle = Number(next())
    else if (a === '--inject-stall') out.injectStall = Number(next())
    else if (a === '--window-ms') out.windowMs = Number(next())
    else if (a === '--viewport') {
      const [w, h] = next().split('x').map(Number)
      out.viewport = { width: w, height: h }
    }
    else if (a === '--headed') out.headed = true
    else if (a === '--chromium') out.chromium = next()
    else if (a === '--help' || a === '-h') { usage(); process.exit(0) }
    else { console.error(`unknown flag: ${a}`); usage(); process.exit(2) }
  }
  return out
}

function usage() {
  console.error(`motion_frame_budget — frame-time distributions per surface per bounciness

  --url <base>          gateway serving the built SPA (default $PC_MOTION_URL or :10473)
  --bounciness <list>   comma list of --bounciness values to measure (default "1,0")
  --surfaces <list>     comma list of surface ids (default all: ${SURFACES.map((s) => s.id).join(',')})
  --cpu-throttle <n>    CDP CPU throttling rate, 1 = none (default 1)
  --inject-stall <ms>   FALSIFICATION: block the main thread this long inside the window
  --window-ms <ms>      length of the idle control window (default 3000)
  --viewport <WxH>      viewport (default 1440x900) — it decides which lists overflow
  --headed              run the full chromium headed instead of the headless shell
  --chromium <path>     explicit browser executable`)
}

/** Playwright's bundled revision is not the one in this machine's cache (it resolves
 *  `chromium-1228`; the cache holds `-1234`), so `chromium.launch()` with no
 *  `executablePath` fails on a missing build. Scan the cache for the newest build of the
 *  flavour we want instead of pinning a revision that will rot. */
function resolveBrowser({ headed, chromium: explicit }) {
  if (explicit) return { executablePath: explicit, label: explicit }
  const cache = path.join(os.homedir(), 'Library', 'Caches', 'ms-playwright')
  const arch = process.arch === 'arm64' ? 'arm64' : 'x64'
  const candidates = []
  if (existsSync(cache)) {
    for (const dir of readdirSync(cache)) {
      if (headed && /^chromium-\d+$/.test(dir)) {
        candidates.push(path.join(cache, dir, `chrome-mac-${arch}`,
          'Google Chrome for Testing.app', 'Contents', 'MacOS', 'Google Chrome for Testing'))
      }
      if (!headed && /^chromium_headless_shell-\d+$/.test(dir)) {
        candidates.push(path.join(cache, dir, `chrome-headless-shell-mac-${arch}`, 'chrome-headless-shell'))
      }
    }
  }
  candidates.sort().reverse()
  const hit = candidates.find((p) => existsSync(p))
  if (hit) return { executablePath: hit, label: hit }
  // Fall through to whatever playwright ships with — if that is absent the launch
  // failure names the missing build, which is a better error than ours.
  return { executablePath: undefined, label: 'playwright default' }
}

// ── Surfaces + their interaction recipes ────────────────────────────────────
// A step returns `true` (or `{ detail }` when WHICH control it found matters) when it ran,
// or `{ skip: <why> }` when its precondition is absent — the `OpenResult` convention from
// `web/e2e/helpers.ts`. A hardcoded "nothing found" message cannot tell "this surface has
// no scroller" from "the control was renamed", and those need different next actions from
// whoever reads the report.

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/** Navigate INTO the surface from another route, inside the measured window: the route
 *  crossfade (`viewTransition`) plus the arriving surface's `EntranceGroup` cascade. This
 *  is the one step that must be first — every other step measures the settled page. A hash
 *  write is what a nav click does; `page.goto` would reload the SPA and measure a boot. */
async function navigateIn(page, surface) {
  await page.evaluate((r) => { location.hash = r.slice(1) }, surface.route)
  await sleep(1400)
  return true
}

/** `app/CommandPalette` renders a `div.fixed.inset-0` holding `[role="listbox"]` with
 *  `aria-label="Commands"` — and NO `role="dialog"`. A `[role="dialog"], [cmdk-root]`
 *  probe therefore reported "the palette did not open" on a run whose own frame slice
 *  showed 41 frames at a 23ms mean, i.e. while it demonstrably *was* opening. Keyed to
 *  the real selector, and the skip stays because a renamed control must not read as a
 *  measured-clean surface. */
const PALETTE = '[role="listbox"][aria-label="Commands"]'

/** Open the command palette — `overlayEnter` on `physics.playful`, the most overshoot in
 *  the vocabulary, over 22 staggered `[role="option"]` rows. The ⌘K listener lives on
 *  `window`, registered by the SHELL, so with no shell this changes nothing; the
 *  mountedness floor is what reports that. Left OPEN for the scroll step that follows. */
async function paletteOpen(page) {
  await page.keyboard.press('ControlOrMeta+k')
  await page.locator(PALETTE).waitFor({ state: 'visible', timeout: 4000 }).catch(() => {})
  await sleep(500)
  const n = await page.locator(`${PALETTE} [role="option"]`).count()
  if (!n) return { skip: `${PALETTE} did not appear on ControlOrMeta+K — the palette did not open` }
  return true
}

/** Scroll the OPEN palette's option list, then close it. This is the only list on either
 *  surface that actually overflows with the `demo-home` fixture at 1440x900 (431px of
 *  overflow over 22 rows), so it is where the scroll path gets measured; the per-surface
 *  scroll step keeps its honest skip. Both wheel and keyboard, because arrow-key
 *  navigation moves the active row's own highlight spring as well as the scroll. */
async function paletteScrollAndClose(page) {
  const box = await page.evaluate((sel) => {
    const lb = document.querySelector(sel)
    if (!lb) return null
    const r = lb.getBoundingClientRect()
    return { over: lb.scrollHeight - lb.clientHeight, x: r.x + r.width / 2, y: r.y + r.height / 2 }
  }, PALETTE)
  if (!box) return { skip: 'the palette is not open — nothing to scroll (see the preceding step)' }
  if (box.over < 40) return { skip: `the palette list overflows by only ${box.over}px — no scroll to drive` }
  await page.mouse.move(box.x, box.y)
  for (let i = 0; i < 6; i++) { await page.mouse.wheel(0, 140); await sleep(90) }
  for (let i = 0; i < 10; i++) { await page.keyboard.press('ArrowDown'); await sleep(60) }
  await page.keyboard.press('Escape')
  await sleep(450)
  return true
}

/** Wheel-scroll the tallest scrollable element: momentum + whatever is `whileInView`.
 *  A page-level scroll is not enough — this app's surfaces scroll inside an inner
 *  column, and `window.scrollBy` on an inner-scroller layout is a no-op. */
async function scrollTallestScroller(page) {
  const box = await page.evaluate(() => {
    let best = null
    for (const el of document.querySelectorAll('*')) {
      const over = el.scrollHeight - el.clientHeight
      if (over < 200 || el.clientHeight < 120) continue
      const s = getComputedStyle(el)
      if (!/auto|scroll/.test(s.overflowY)) continue
      if (!best || over > best.over) {
        const r = el.getBoundingClientRect()
        best = { over, x: r.x + r.width / 2, y: r.y + Math.min(r.height / 2, 300) }
      }
    }
    return best
  })
  if (!box) return { skip: 'no element scrolls by >200px on this surface — nothing to scroll' }
  await page.mouse.move(box.x, box.y)
  for (let i = 0; i < 8; i++) { await page.mouse.wheel(0, 220); await sleep(90) }
  for (let i = 0; i < 8; i++) { await page.mouse.wheel(0, -220); await sleep(90) }
  return true
}

/** Sweep the pointer across the surface's buttons — hover-lift / press-depth springs,
 *  the `expr()`-scaled magnitudes, on every control the sweep crosses. */
async function hoverSweep(page) {
  const boxes = await page.evaluate(() => {
    const out = []
    for (const el of document.querySelectorAll('button, a[href], [role="button"], [role="tab"]')) {
      const r = el.getBoundingClientRect()
      if (r.width > 8 && r.height > 8 && r.y > 0 && r.y < innerHeight - 8) {
        out.push({ x: r.x + r.width / 2, y: r.y + r.height / 2 })
      }
      if (out.length >= 14) break
    }
    return out
  })
  if (!boxes.length) return { skip: 'no on-screen controls to hover' }
  for (const b of boxes) { await page.mouse.move(b.x, b.y); await sleep(70) }
  return true
}

/** Open then close a disclosure that belongs to the SURFACE — a layout animation, which is
 *  the expensive kind (projection, not just opacity).
 *
 *  Deliberately skips anything inside `nav` or the top bar: notifications, the degraded-
 *  services popover and the width menu are shell chrome present on every route, so
 *  measuring one of those would produce the same number for every surface and attribute
 *  it to whichever page happened to be underneath. The report names the control it drove
 *  so a reader can tell which surface affordance the numbers describe. */
async function disclosureCycle(page) {
  const pick = await page.evaluate(() => {
    const shell = (el) => el.closest('nav') || el.closest('header') || el.closest('[data-tour="rail"]')
    for (const el of [...document.querySelectorAll('[aria-expanded="false"]')].reverse()) {
      if (shell(el)) continue
      const r = el.getBoundingClientRect()
      if (r.width < 8 || r.height < 8 || r.y < 0 || r.y > innerHeight - 8) continue
      return {
        label: (el.getAttribute('aria-label') || el.textContent || '(unnamed)').trim().slice(0, 48),
        x: r.x + r.width / 2, y: r.y + r.height / 2,
      }
    }
    return null
  })
  if (!pick) return { skip: 'no on-screen [aria-expanded="false"] outside the nav/top bar — no surface disclosure to drive' }
  await page.mouse.click(pick.x, pick.y)
  await sleep(650)
  await page.keyboard.press('Escape')
  await sleep(450)
  return { detail: `drove "${pick.label}"` }
}

/** Type into the chat composer (a CodeMirror contenteditable, invisible to input/textarea)
 *  and open the slash menu — a staggered `listItemEnter` cascade over fetched rows. */
async function composerSlashMenu(page) {
  const cm = page.locator('[contenteditable="true"]').first()
  if (!(await cm.count())) return { skip: 'no contenteditable composer on this surface' }
  await cm.click()
  await cm.pressSequentially('motion budget probe', { delay: 25 })
  await sleep(200)
  await page.keyboard.press('Enter').catch(() => {})   // no provider configured: this is a no-op send
  await sleep(200)
  await cm.pressSequentially('/')
  await page.locator('[role="listbox"]').first().waitFor({ state: 'visible', timeout: 4000 }).catch(() => {})
  await sleep(400)
  await page.keyboard.press('Escape')
  await sleep(200)
  return true
}

const SURFACES = [
  {
    id: 'chat',
    label: 'ChatPage',
    // Arrive FROM another route so the measured window contains the route crossfade
    // (`viewTransition`) and the entrance cascade, not just the steady state.
    from: '#/dashboard',
    route: '#/chat',
    // A PATTERN, not a literal: the composer step sends a message, which creates a session
    // and re-addresses the page to `#/chat/<session-id>`. That is still ChatPage — and a
    // better one to measure, since it has a message list — but a literal `#/chat` check
    // failed on it, which is the failure mode worth keeping visible: the assertion fired
    // rather than the harness quietly measuring whatever it landed on.
    expectHash: /^#\/chat(\/|$)/,
    steps: [
      ['route crossfade + entrance cascade', navigateIn],
      ['command palette open (overlayEnter)', paletteOpen],
      ['palette list scroll + close', paletteScrollAndClose],
      ['composer typing + slash menu', composerSlashMenu],
      ['surface disclosure open/close', disclosureCycle],
      ['hover sweep over controls', hoverSweep],
      ['wheel scroll the surface scroller', scrollTallestScroller],
    ],
  },
  {
    id: 'loop-cockpit',
    label: 'Loop cockpit',
    from: '#/dashboard',
    // `#/loops` (bare) redirects to `#/loop`; the cockpit is the concrete 8-hex id. Seeded
    // by `--seed demo-home` (loop a17c3f92, kind=research, status=complete), which is the
    // only reason this route resolves to a cockpit rather than the composer.
    route: '#/loops/a17c3f92',
    // The trailing `(\?…)?` is not slack: the cockpit's own disclosure is a QUERY-state
    // panel, so driving it leaves the probe on `#/loops/a17c3f92?prompt=1`. Same surface —
    // and per motion.md §6 a query-only change deliberately does not animate at all, which
    // is exactly what that step's frame slice shows.
    expectHash: /^#\/loops\/a17c3f92(\?|$)/,
    steps: [
      ['route crossfade + entrance cascade', navigateIn],
      ['surface disclosure open/close', disclosureCycle],
      ['hover sweep over controls', hoverSweep],
      ['wheel scroll the surface scroller', scrollTallestScroller],
      ['command palette open (overlayEnter)', paletteOpen],
      ['palette list scroll + close', paletteScrollAndClose],
    ],
  },
]

// ── The frame collector ─────────────────────────────────────────────────────
// `requestAnimationFrame` deltas are the honest primitive: they are the times the browser
// actually presented a frame, so a long task shows up as one big delta rather than being
// smeared across an average.

const COLLECTOR = () => {
  const st = { t: [], marks: [], animPeak: 0, animFrames: 0, stop: false, i: 0 }
  window.__fm7 = st
  const tick = (ts) => {
    st.t.push(ts)
    // Amortized: a `getAnimations()` walk every frame would make the probe part of the
    // measurement. Every 3rd frame is enough to prove motion was running.
    if ((st.i++ % 3) === 0 && typeof document.getAnimations === 'function') {
      const n = document.getAnimations().filter((a) => a.playState === 'running').length
      if (n > 0) st.animFrames++
      if (n > st.animPeak) st.animPeak = n
    }
    if (!st.stop) requestAnimationFrame(tick)
  }
  requestAnimationFrame(tick)
}

async function startCollector(page) {
  await page.evaluate(COLLECTOR)
}

/** Stamp the frame stream so an outlier can be ATTRIBUTED to the interaction that caused
 *  it. Without this a run reports "worst 400ms somewhere in the window", which is not a
 *  finding anyone can act on — and "no jank" is a per-interaction claim, not a per-window one. */
async function mark(page, label) {
  await page.evaluate((l) => { window.__fm7?.marks.push({ label: l, t: performance.now() }) }, label)
}

/** One entry per presented frame: how long it took, and WHEN it was presented. The `at` is
 *  what lets a stall be attributed to the step it happened in — and it is the END of the
 *  delta on purpose, so a stall that spans a step boundary lands in the step that caused it
 *  rather than being dropped between two slices. */
function deltasOf(timestamps) {
  const d = []
  for (let i = 1; i < timestamps.length; i++) d.push({ ms: timestamps[i] - timestamps[i - 1], at: timestamps[i] })
  return d
}

function stats(deltas) {
  const d = deltas.map((x) => x.ms)
  if (!d.length) return { frames: 0 }
  const sorted = [...d].sort((a, b) => a - b)
  const q = (p) => sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))]
  const sum = d.reduce((a, b) => a + b, 0)
  const r2 = (n) => Math.round(n * 100) / 100
  return {
    frames: d.length,
    spanMs: r2(sum),
    meanMs: r2(sum / d.length),
    fps: r2(1000 / (sum / d.length)),
    p50Ms: r2(q(0.5)),
    p95Ms: r2(q(0.95)),
    p99Ms: r2(q(0.99)),
    worstMs: r2(sorted[sorted.length - 1]),
    over16_7: d.filter((x) => x > 16.7).length,
    over33_3: d.filter((x) => x > 33.3).length,
    // The clause's "no jank": a single frame this long is a visible hitch no mean shows.
    jank: r2(sorted[sorted.length - 1]) > 50,
  }
}

async function readCollector(page) {
  const raw = await page.evaluate(() => {
    const st = window.__fm7
    st.stop = true
    return { t: st.t, marks: st.marks, animPeak: st.animPeak, animFrames: st.animFrames }
  })
  const deltas = deltasOf(raw.t)
  const out = { ...stats(deltas), animationsPeak: raw.animPeak, framesWithAnimation: raw.animFrames }
  if (raw.marks.length) {
    out.perStep = raw.marks
      .map((m, i) => {
        const until = raw.marks[i + 1]?.t ?? Infinity
        return { step: m.label, ...stats(deltas.filter((x) => x.at >= m.t && x.at < until)) }
      })
      .filter((s) => s.frames > 0)
  }
  return out
}

// ── Floors ──────────────────────────────────────────────────────────────────
// A measurement on the onboarding screen, or on the gateway's "dashboard isn't built yet"
// page, is byte-identical in shape to a clean one — it just reports a suspiciously good
// number. These two make that state the loud failure instead.
const SHELL = 'nav[data-tour="rail"]'

async function assertSurface(page, surface) {
  const hash = await page.evaluate(() => location.hash)
  if (!surface.expectHash.test(hash)) {
    throw new Error(`${surface.id}: measured hash is ${hash || '(empty)'}, expected ${surface.expectHash} — ` +
      `the probe is not on the surface it claims (a route can canonicalise: #/loops → #/loop)`)
  }
  const info = await page.evaluate((sel) => ({
    elements: document.querySelectorAll('*').length,
    shell: !!document.querySelector(sel),
  }), SHELL)
  if (!info.shell || info.elements < 200) {
    throw new Error(`${surface.id}: shell=${info.shell} elements=${info.elements} — this is the ` +
      `onboarding screen or an unbuilt dist, not the route under test. Any clean frame ` +
      `distribution measured here is meaningless.`)
  }
  return { hash, elements: info.elements }
}

// ── Runner ──────────────────────────────────────────────────────────────────
async function measureSurface(browser, opts, surface, bounciness) {
  const context = await browser.newContext({ viewport: opts.viewport, deviceScaleFactor: 2 })
  // Set the dial the way the SLIDER does: `app/appearance.tsx` persists every scalar
  // override under localStorage['appearance'].scalars, keyed by CSS var name, and applies
  // them to `runtime` on mount. Editing motion.ts instead would measure a different app.
  await context.addInitScript((b) => {
    try {
      const raw = localStorage.getItem('appearance')
      const ov = raw ? JSON.parse(raw) : {}
      ov.scalars = { ...(ov.scalars || {}), '--bounciness': b }
      localStorage.setItem('appearance', JSON.stringify(ov))
      localStorage.setItem('mode', 'dark')
    } catch { /* a context with no storage access falls back to the default dial */ }
  }, bounciness)
  const page = await context.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e.message || e)))

  let cdp = null
  if (opts.cpuThrottle > 1) {
    cdp = await context.newCDPSession(page)
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: opts.cpuThrottle })
  }

  const results = []
  try {
    // Land on the `from` route first so the measured navigation is a real route CHANGE.
    await page.goto(`${opts.url}/${surface.from}`, { waitUntil: 'domcontentloaded' })
    await page.waitForSelector(SHELL, { timeout: 20000 })
    await page.evaluate(() => document.fonts?.ready)
    await sleep(1200)   // let the arrival settle so the idle control is genuinely idle

    // ── the idle CONTROL: same page, same environment, nothing provoked ──
    await startCollector(page)
    await sleep(opts.windowMs)
    const idle = await readCollector(page)
    results.push({ window: 'idle-control', surface: `${surface.id} (at ${surface.from})`, ...idle })

    // ── the measured window: navigate in, then run the recipe ──
    await startCollector(page)
    const t0 = Date.now()
    const steps = []
    for (const [label, fn] of surface.steps) {
      await mark(page, label)
      const r = await fn(page, surface).catch((e) => ({ skip: `threw: ${String(e.message || e).split('\n')[0]}` }))
      steps.push({
        step: label,
        ran: r === true || !!(r && !r.skip),
        ...(r && r.skip ? { skip: r.skip } : {}),
        ...(r && r.detail ? { detail: r.detail } : {}),
      })
      // FALSIFICATION HOOK: block the main thread from inside the measured window, in its
      // own marked slice. A run with --inject-stall that does NOT show the stall in
      // `worstMs` invalidates every other number this script prints.
      if (opts.injectStall > 0 && label.startsWith('hover')) {
        await mark(page, `INJECTED STALL ${opts.injectStall}ms (falsification)`)
        await page.evaluate((ms) => { const end = performance.now() + ms; while (performance.now() < end) { /* spin */ } }, opts.injectStall)
        await sleep(300)
      }
    }
    const active = await readCollector(page)
    const seen = await assertSurface(page, surface)
    results.push({
      window: 'interaction',
      surface: surface.id,
      ...active,
      elapsedMs: Date.now() - t0,
      steps,
      provokedMotion: active.framesWithAnimation > 0,
      ...seen,
    })
  } finally {
    if (cdp) await cdp.detach().catch(() => {})
    await context.close()
  }
  return { results, pageErrors: errors }
}

async function main() {
  const opts = parseArgs(process.argv)
  const wanted = opts.surfaces ? SURFACES.filter((s) => opts.surfaces.includes(s.id)) : SURFACES
  if (!wanted.length) { console.error(`no surfaces matched ${opts.surfaces}`); process.exit(2) }

  const { executablePath, label } = resolveBrowser(opts)
  const browser = await chromium.launch({ headless: !opts.headed, executablePath })
  const report = {
    env: {
      browser: label,
      browserVersion: browser.version(),
      headless: !opts.headed,
      viewport: `${opts.viewport.width}x${opts.viewport.height}`,
      deviceScaleFactor: 2,
      cpuThrottlingRate: opts.cpuThrottle,
      injectedStallMs: opts.injectStall,
      platform: `${os.platform()} ${os.arch()} ${os.release()}`,
      cpus: `${os.cpus().length}x ${os.cpus()[0]?.model ?? 'unknown'}`,
      node: process.version,
      url: opts.url,
      when: new Date().toISOString(),
      // Say it here so no reader has to infer it: an unthrottled headless run on a dev
      // machine is the most generous environment this app will ever see.
      caveat: opts.cpuThrottle > 1
        ? `CPU throttled ${opts.cpuThrottle}x via CDP Emulation.setCPUThrottlingRate`
        : 'UNTHROTTLED headless — a generous environment, not a guarantee for user hardware',
    },
    runs: [],
  }

  let failed = false
  try {
    for (const bounciness of opts.bounciness) {
      for (const surface of wanted) {
        process.stderr.write(`[motion-budget] ${surface.id} @ bounciness=${bounciness}…\n`)
        try {
          const { results, pageErrors } = await measureSurface(browser, opts, surface, bounciness)
          report.runs.push({ bounciness, surface: surface.id, label: surface.label, pageErrors, windows: results })
        } catch (e) {
          failed = true
          report.runs.push({ bounciness, surface: surface.id, label: surface.label, error: String(e.message || e) })
          process.stderr.write(`[motion-budget] FAIL ${surface.id} @ ${bounciness}: ${e.message}\n`)
        }
      }
    }
  } finally {
    await browser.close()
  }

  console.log(JSON.stringify(report, null, 2))

  // A one-line-per-window summary on stderr, so a human reading the terminal sees the
  // distribution without piping through jq.
  process.stderr.write('\n[motion-budget] summary (frames / mean / p95 / worst / >16.7ms)\n')
  for (const run of report.runs) {
    if (run.error) { process.stderr.write(`  b=${run.bounciness} ${run.surface}: ERROR ${run.error}\n`); continue }
    for (const w of run.windows) {
      process.stderr.write(
        `  b=${run.bounciness} ${w.window.padEnd(16)} ${String(run.surface).padEnd(13)} ` +
        `${String(w.frames).padStart(4)}f  mean ${String(w.meanMs).padStart(6)}ms (${w.fps}fps)  ` +
        `p95 ${String(w.p95Ms).padStart(6)}ms  worst ${String(w.worstMs).padStart(7)}ms  ` +
        `>16.7ms ${String(w.over16_7).padStart(3)}  >33.3ms ${String(w.over33_3).padStart(3)}` +
        `${w.jank ? '  JANK' : ''}${w.provokedMotion === false ? '  NO-MOTION-PROVOKED' : ''}\n`,
      )
    }
  }
  if (failed) process.exitCode = 1
}

main().catch((e) => { console.error(`[motion-budget] ${e.stack || e}`); process.exit(1) })
