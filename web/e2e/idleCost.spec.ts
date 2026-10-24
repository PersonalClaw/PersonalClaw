import { test, expect, type Page, type TestInfo } from '@playwright/test'
import { gotoRoute, seedBackdrop } from './helpers'
import { IDLE_PROBE_SCRIPT, IDLE_ROUTES, IDLE_TRIES, IDLE_WINDOW_MS, idleCostFindings, measureIdle } from './idleCost'

// ── The idle-cost probe: an idle Home and Chat ask for no frame (#3811) ──────────────────────────
//
// Part of the one named UI quality check (`@ui-quality`, #3941). With the default identity, the
// window focused and no turn running, each route in `IDLE_ROUTES` is loaded, left to come to rest,
// and measured over a 5 s window by `idleCost.ts`: no animation frame at all, and no timer or
// request that recurs faster than once a second. A window in which the gateway pushed something
// to the page, or a turn ran anywhere, is not idle, so the probe measures again; this spec shares
// the run's gateway with specs that drive turns at any time. A route that polls at rest names the
// read its window must hold an answer of (`holds`), since what a page costs at rest is mostly what
// each answer makes it do: a window between two answers would pass a frame per answer. Neither
// Home nor Chat polls at rest.
//
// Every check here can fail, and the last two tests show it on the real pages, with the same probe:
// the wave backdrop (Claw Arcade's) keeps a frame loop, and a planted frame loop, a 100 ms interval
// and a 200 ms poll are each reported by name. `src/design/idleCostProbe.test.ts` plants the same
// in a page of fakes, so the probe is held without a browser too.

/** Idle work planted before the page boots: one of each kind the probe budgets. The poll's path is
 *  answered in the page (below), so the plant never reaches the gateway the other specs share. */
const PLANT = `(() => {
  // The frame loop moves one labelled element, so the finding must name what it moved too.
  let x = 0
  let moving = null
  const loop = () => {
    if (!moving && document.body) {
      moving = document.createElement('div')
      moving.setAttribute('aria-label', 'Planted mover')
      document.body.appendChild(moving)
    }
    if (moving) moving.style.transform = 'translateX(' + (x++ % 100) + 'px)'
    requestAnimationFrame(loop)
  }
  requestAnimationFrame(loop)
  setInterval(() => {}, 100)
  setInterval(() => { fetch('/api/idle-probe-plant').catch(() => {}) }, 200)
})()`

/** Load `route` with the probe in the page (and `plant` after it), and return what one idle window
 *  cost against the budget. What it measured, and any window it threw away, goes on the report. */
async function idleCost(page: Page, info: TestInfo, route: string, plant?: string, holds?: string): Promise<string[]> {
  await page.addInitScript(IDLE_PROBE_SCRIPT)
  if (plant) await page.addInitScript(plant)
  await gotoRoute(page, route)
  // The issue's precondition, stated rather than assumed: a page without the focus, or hidden,
  // slows or stops its own loops, and would read cleaner than it is.
  expect(await page.evaluate(() => document.hasFocus() && !document.hidden), `#/${route} has the focus and is shown`).toBe(true)
  const { sample, voided } = await measureIdle(page, holds)
  for (const why of voided) info.annotations.push({ type: 'idle-window-voided', description: why })
  await info.attach(`idle-cost-${route.replace(/[^a-z0-9-]+/gi, '-')}.json`, {
    body: JSON.stringify({ route, sample, voided }, null, 2),
    contentType: 'application/json',
  })
  return idleCostFindings(sample)
}

test.describe('UI quality: idle cost', { tag: '@ui-quality' }, () => {
  // The load, then up to IDLE_TRIES windows with a pause after each one thrown away.
  test.describe.configure({ timeout: 30_000 + IDLE_TRIES * (IDLE_WINDOW_MS + 2_000) })

  for (const { route, label, holds } of IDLE_ROUTES) {
    test(`${label} asks for no frame and no fast timer or poll while idle (#/${route})`, async ({ page }, info) => {
      expect(await idleCost(page, info, route, undefined, holds), `what an idle #/${route} scheduled`).toEqual([])
    })
  }

  test('the probe sees the wave backdrop ask for frames (#/chat)', async ({ page }, info) => {
    await seedBackdrop(page, 'waves')
    const findings = await idleCost(page, info, 'chat')
    expect(findings, 'the wave field keeps a frame loop, so the probe must report it').toContainEqual(
      expect.stringMatching(/^requestAnimationFrame asked for \d+ frames in /),
    )
  })

  test('the probe reports a planted frame loop, interval and poll, each by name (#/dashboard)', async ({ page }, info) => {
    await page.route(/\/api\/idle-probe-plant$/, (r) => r.fulfill({ json: {} }))
    const findings = await idleCost(page, info, 'dashboard', PLANT)
    expect(findings).toEqual(expect.arrayContaining([
      expect.stringMatching(/^requestAnimationFrame asked for \d+ frames in /),
      expect.stringMatching(/^while it framed, these changed: .*div\[Planted mover\] \(\d+ times\)/),
      expect.stringMatching(/^setInterval\(100 ms\) fired \d+ times in /),
      expect.stringMatching(/^setInterval\(200 ms\) fired \d+ times in /),
      expect.stringMatching(/^GET \/api\/idle-probe-plant was requested \d+ times in /),
    ]))
  })
})
