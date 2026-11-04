import { test, expect, type Page } from '@playwright/test'
import { gotoRoute } from './helpers'

// ── THE SIDEBAR'S ICON AND NAME STAY IN ITS CENTRE ───────────────────────────────────────────────
//
// The expanded sidebar opened on the PersonalClaw icon and name, held to its left edge, so a sidebar
// dragged wider left them in its corner. They sit in the sidebar's centre now, at every width it
// takes: the default, the narrowest and the widest, and every width between while it is dragged. The
// collapsed rail keeps its icon in its centre, as it was. The collapse toggle sits outside the
// sidebar, so it never covers them or pulls them off centre. Opening the sidebar is instant (the rail
// has no expand animation), so they land in the centre on the first frame, and under reduced motion
// too, in the sidebar and in the phone's drawer.
//
// Asked of a real browser, since jsdom lays nothing out. Within 1 px.

const RAIL = 'nav[data-tour="rail"]'
const DRAWER = '[role="dialog"][aria-label="Navigation"]'

interface Box { left: number; right: number; top: number; bottom: number }
interface Reading { nav: Box; brand: Box; toggle: Box | null }

/** The sidebar, its icon and name, and the collapse toggle, read in one layout pass. `scope` picks
 *  the sidebar or the phone's drawer. The icon and name are the first thing in the sidebar's Home
 *  link: the lockup expanded, the icon alone collapsed. */
function read(page: Page, scope: string): Promise<Reading> {
  return page.evaluate((scope) => {
    const box = (el: Element): Box => {
      const r = el.getBoundingClientRect()
      return { left: r.left, right: r.right, top: r.top, bottom: r.bottom }
    }
    const nav = document.querySelector(scope === 'drawer' ? '[role="dialog"][aria-label="Navigation"] nav' : 'nav[data-tour="rail"]')!
    const home = nav.querySelector('[data-rail-top] button')!
    const toggle = document.querySelector('[data-rail-toggle]')
    return { nav: box(nav), brand: box(home.firstElementChild!), toggle: toggle ? box(toggle) : null }
  }, scope)
}

/** The same reading, on the first frame after `act`. */
async function firstFrame(page: Page, scope: string, act: () => Promise<void>): Promise<Reading> {
  await act()
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => r(null))))
  return read(page, scope)
}

const mid = (b: Box) => (b.left + b.right) / 2
const overlaps = (a: Box, b: Box) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom

/** Centred on the sidebar's full width within 1 px, wholly inside it, and clear of the toggle. */
function expectCentred(r: Reading, at: string): void {
  expect(Math.abs(mid(r.brand) - mid(r.nav)), `${at}: off centre by`).toBeLessThanOrEqual(1)
  expect(r.brand.left, `${at}: cut at the left`).toBeGreaterThanOrEqual(r.nav.left)
  expect(r.brand.right, `${at}: cut at the right`).toBeLessThanOrEqual(r.nav.right)
  if (r.toggle) expect(overlaps(r.toggle, r.brand), `${at}: the toggle covers the icon and name`).toBe(false)
}

const separator = (page: Page) => page.getByRole('separator', { name: /^Resize navigation/ })
const width = async (page: Page) => Number(await separator(page).getAttribute('aria-valuenow'))

test.describe("the sidebar's icon and name", () => {
  test.beforeEach(async ({ page }) => { await page.setViewportSize({ width: 1440, height: 900 }) })

  test('sit in its centre at the default, narrowest and widest widths, and while it is dragged', async ({ page }) => {
    await gotoRoute(page, 'dashboard')
    expect(await width(page)).toBe(196)
    expectCentred(await read(page, 'rail'), 'default 196')

    await separator(page).focus()
    await page.keyboard.press('Home')
    expect(await width(page)).toBe(172)
    expectCentred(await read(page, 'rail'), 'narrowest 172')

    await page.keyboard.press('End')
    expect(await width(page)).toBe(380)
    expectCentred(await read(page, 'rail'), 'widest 380')

    // A drag, read at each step before the button is let go.
    const s = (await separator(page).boundingBox())!
    await page.mouse.move(s.x + s.width / 2, s.y + s.height / 2)
    await page.mouse.down()
    for (const x of [340, 287, 233, 180]) {
      await page.mouse.move(x, s.y + s.height / 2)
      expect(await width(page)).toBe(x)
      expectCentred(await read(page, 'rail'), `dragged to ${x}`)
    }
    await page.mouse.up()
  })

  test('keep the collapsed rail as it was: the icon alone, in its centre', async ({ page }) => {
    await gotoRoute(page, 'dashboard')
    await page.getByRole('button', { name: 'Collapse sidebar' }).click()
    const r = await read(page, 'rail')
    expect(r.nav.right - r.nav.left).toBe(64)
    expect(await page.locator(RAIL).getByRole('button', { name: 'Home', exact: true }).count()).toBe(1)
    expectCentred(r, 'collapsed')
  })

  for (const reduced of [false, true]) {
    test(`land in its centre on the first frame it opens${reduced ? ', under reduced motion' : ''}`, async ({ page }) => {
      if (reduced) await page.emulateMedia({ reducedMotion: 'reduce' })
      await page.addInitScript(() => { localStorage.setItem('nav-collapsed', '1'); localStorage.setItem('nav-width-v2', '380') })
      await gotoRoute(page, 'dashboard')
      const r = await firstFrame(page, 'rail', () => page.getByRole('button', { name: 'Expand sidebar' }).click())
      expect(r.nav.right - r.nav.left).toBe(380)
      expectCentred(r, 'opened at 380')
    })
  }

  test("sit in the centre of the phone's drawer, under reduced motion from its first frame", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.setViewportSize({ width: 390, height: 844 })
    await gotoRoute(page, 'dashboard')
    const r = await firstFrame(page, 'drawer', () => page.getByRole('button', { name: 'Expand sidebar' }).click())
    await expect(page.locator(DRAWER)).toBeVisible()
    expect(r.nav.left).toBeGreaterThanOrEqual(0)
    expectCentred({ ...r, toggle: null }, 'drawer')
  })
})
