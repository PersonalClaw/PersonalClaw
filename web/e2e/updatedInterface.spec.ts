import { test, expect, type Page } from '@playwright/test'
import { appendFileSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import { SHELL_SELECTOR } from './helpers'
import { DIST, buildOf, restartFromPage, startGateway } from './ownGateway'

// ── An open page offers Reload once its gateway restarts into a newer build (#5901) ─────────────
//
// An update builds a new SPA and restarts the gateway, and the page that was open kept running
// the old build. The gateway now says which build it serves, in the document and on every socket
// open, and the page offers Reload when the two differ (`src/app/UpdatedInterfaceNotice.tsx`).
//
// This runs it end to end on a gateway of the spec's own: its own home, port and HOME beside it,
// started through the harness's environment prefix as the run's gateway is. That gateway serves a
// COPY of `dist/` (`PERSONALCLAW_SPA_DIST`), and the spec makes a newer build by changing the
// copy, so a run that is killed part way leaves the real build as it was. It restarts only that
// gateway (`POST /api/system/restart`, a re-exec in place that keeps the environment), so the
// run's gateway, and every spec on it, is never touched.
//
// The page is the gateway's own document, not the preview's: the gateway stamps the build into
// the documents it serves, and the preview serves `dist/` as it is. The service worker is
// registered and controls the page, and the copy's worker changes with its index, so Reload is
// shown to load the new build past a new worker waiting behind the old one (`sw.ts`: no
// skipWaiting, no clients.claim).

const BUILD_META = 'meta[name="personalclaw-spa-build"]'

const bootedBuild = (page: Page) =>
  page.evaluate((sel) => document.querySelector(sel)?.getAttribute('content') ?? '', BUILD_META)

const waitingWorker = (page: Page) =>
  page.evaluate(async () => !!(await navigator.serviceWorker.getRegistration())?.waiting)

/** Count the `spa_build` frames the page's sockets receive from now on. */
function spaBuildFrames(page: Page): { count: number } {
  const seen = { count: 0 }
  page.on('websocket', (socket) => {
    socket.on('framereceived', ({ payload }) => {
      try {
        if (JSON.parse(String(payload)).type === 'spa_build') seen.count += 1
      } catch { /* not a JSON frame */ }
    })
  })
  return seen
}

test.describe('the interface-updated notice', () => {
  test.skip(!existsSync(join(DIST, 'sw.js')), 'run `npm run build` first: dist/sw.js is required')
  // Signed in on the gateway's own address below, not through the run's preview.
  test.use({ storageState: { cookies: [], origins: [] } })

  test('an open page offers Reload when its gateway restarts into a newer build, and Reload loads it past a waiting worker', async ({ page }, testInfo) => {
    test.setTimeout(360_000)
    const gw = await startGateway(testInfo)
    try {
      await page.goto(`${gw.origin}/?token=${encodeURIComponent(gw.token)}`)
      await page.goto(`${gw.origin}/#/dashboard`)
      await expect(page.locator(SHELL_SELECTOR), 'the shell never mounted on the spec\'s gateway').toBeVisible({ timeout: 60_000 })

      // Installed, then in control from the next load: there is no clients.claim by design.
      await page.evaluate(() => navigator.serviceWorker.ready)
      await page.reload()
      await expect(page.locator(SHELL_SELECTOR)).toBeVisible({ timeout: 60_000 })
      expect(await page.evaluate(() => navigator.serviceWorker.controller !== null), 'the worker must control the page').toBe(true)
      const booted = await bootedBuild(page)
      expect(booted, 'the gateway stamped the build it served').toBe(buildOf(gw.dist))

      // A newer build: another index, and another worker with it.
      appendFileSync(join(gw.dist, 'index.html'), '\n<!-- e2e: a newer build -->\n')
      appendFileSync(join(gw.dist, 'sw.js'), '\n// e2e: a newer worker\n')
      const newer = buildOf(gw.dist)
      expect(newer).not.toBe(booted)

      await restartFromPage(page)
      const notice = page.locator('[data-updated-interface]')
      await expect(notice, 'no notice after the restart into a newer build').toBeVisible({ timeout: 120_000 })
      await expect(notice).toContainText("PersonalClaw's interface was updated. The changes take effect when you reload.")
      await expect(page.getByRole('status').filter({ hasText: "PersonalClaw's interface was updated." })).toHaveCount(1)
      expect(await bootedBuild(page), 'the page still runs the build it booted').toBe(booted)

      // The newer worker installs and waits behind the one in control.
      await page.evaluate(() => navigator.serviceWorker.getRegistration().then((r) => r?.update()))
      await expect.poll(() => waitingWorker(page), { timeout: 30_000, message: 'no worker waiting' }).toBe(true)

      await Promise.all([
        page.waitForEvent('load'),
        notice.getByRole('button', { name: /^Reload/ }).click(),
      ])
      await expect(page.locator(SHELL_SELECTOR)).toBeVisible({ timeout: 60_000 })
      expect(await bootedBuild(page), 'Reload did not load the build the gateway serves').toBe(newer)
      expect(await page.evaluate(() => navigator.serviceWorker.controller !== null), 'the old worker still controls the page').toBe(true)
      expect(await waitingWorker(page), 'the new worker still waits').toBe(true)
      await expect(notice).toHaveCount(0)

      // A restart with nothing rebuilt says nothing: the page runs what the gateway serves.
      const frames = spaBuildFrames(page)
      await restartFromPage(page)
      await expect.poll(() => frames.count, { timeout: 120_000, message: 'the page never reconnected' }).toBeGreaterThan(0)
      await expect(page.getByRole('alertdialog', { name: 'Update progress' })).toHaveCount(0, { timeout: 30_000 })
      await page.waitForTimeout(1_000)
      await expect(notice).toHaveCount(0)
    } finally {
      await gw.stop()
    }
  })
})
