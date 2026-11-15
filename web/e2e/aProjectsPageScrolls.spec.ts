/** A project's page scrolls, and so does the detail opened beside it. The work and About sit in one
 *  scroller under the header, inside a frame that clips (`overflow-hidden`); the detail sits in
 *  PaneHost's pane beside them, or under them on a phone. Each scroller needs a height of its own:
 *  when PaneHost took its content's height instead, nothing scrolled, and the frame cut the page and
 *  the detail at the fold, with everything under it out of a pointer's reach (#6186). The wheel
 *  scrolls each here, with more past the fold than the window shows. On a phone, an ask opened from
 *  Needs you is in view after the tap. Read from the visual spec's fixed reads (`PROJECT_PAGE`);
 *  nothing is written. */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { FIXED_CLOCK } from './fixedClock'
import { gotoRoute } from './helpers'
import { PROJECT_PAGE, answerStubbedRead } from './stubbedReads'

const ROUTE = 'projects/p-0a1b2c3d'
const WIDE = { width: 1280, height: 600 }
const PHONE = { width: 390, height: 844 }

async function open(page: Page, size: { width: number; height: number }, query = '') {
  await page.setViewportSize(size)
  await page.clock.setFixedTime(new Date(FIXED_CLOCK[ROUTE]))
  for (const stub of PROJECT_PAGE) await page.route(stub.url, (r) => answerStubbedRead(stub, r))
  await gotoRoute(page, `${ROUTE}${query}`)
  await expect(page.getByRole('region', { name: 'Recent' })).toBeAttached()
}

type Scroll = { top: number; room: number }

/** The nearest scroller at or around `inside`: up from it when `up`, else the first one inside it. */
async function scrollerOf(at: Locator, up: boolean) {
  const handle = await at.evaluateHandle((el, goUp) => {
    const scrolls = (e: Element) => ['auto', 'scroll'].includes(getComputedStyle(e).overflowY)
    if (goUp) {
      for (let p = el.parentElement; p; p = p.parentElement) if (scrolls(p)) return p
      return null
    }
    return [...el.querySelectorAll('*')].find(scrolls) ?? null
  }, up)
  const found = await handle.evaluate((s) => !!s)
  const read = (): Promise<Scroll> => handle.evaluate((s) => {
    const e = s as HTMLElement | null
    return e ? { top: e.scrollTop, room: e.scrollHeight - e.clientHeight } : { top: 0, room: 0 }
  })
  return { found, read }
}

/** Turn the wheel at (x, y) until the scroller is at its end, ten turns at most. */
async function wheelToTheEnd(page: Page, x: number, y: number, read: () => Promise<Scroll>) {
  await page.mouse.move(x, y)
  for (let i = 0; i < 10; i++) {
    await page.mouse.wheel(0, 400)
    const { top, room } = await read()
    if (room > 0 && top >= room - 1) break
  }
}

for (const size of [WIDE, PHONE]) {
  test(`a project's page scrolls to its last section with the wheel, at ${size.width}`, async ({ page }) => {
    await open(page, size)
    const recent = page.getByRole('region', { name: 'Recent' })
    const secrets = page.getByRole('link', { name: 'Secrets for Garden' })
    const { found, read } = await scrollerOf(recent, true)
    expect(found, 'nothing around the page\'s groups scrolls').toBe(true)
    expect((await read()).top, 'the page starts at its top').toBe(0)
    await expect(secrets, 'the window is too tall for this check: the page\'s end shows without scrolling').not.toBeInViewport()

    await wheelToTheEnd(page, size.width / 2, size.height / 2, read)
    await expect.poll(async () => (await read()).top, { message: "the wheel did not scroll the page's scroller" }).toBeGreaterThan(0)
    await expect(secrets, "the page's last part is out of reach").toBeInViewport()
  })
}

const DETAILS = [
  { what: 'what a new chat is told', query: '?open=told', title: 'What a new chat in Garden is told' },
  { what: "a task's detail", query: '?open=task:t-e2e-1', title: 'Order the soil' },
]

for (const d of DETAILS) {
  test(`the detail beside the work scrolls with the wheel, at 1280: ${d.what}`, async ({ page }) => {
    // Short, so the detail runs past the pane's fold.
    const size = { width: 1280, height: 320 }
    await open(page, size, d.query)
    const pane = page.getByRole('region', { name: d.title })
    await expect(pane).toBeVisible()
    const { found, read } = await scrollerOf(pane, false)
    expect(found, 'nothing in the detail scrolls').toBe(true)
    expect((await read()).room, 'the detail has no room to scroll in its pane: its scroller grew with it, or the window is too tall for this check').toBeGreaterThan(0)

    const box = (await pane.boundingBox())!
    await wheelToTheEnd(page, box.x + box.width / 2, Math.min(box.y + box.height / 2, size.height - 20), read)
    await expect.poll(async () => (await read()).top, { message: "the wheel did not scroll the detail's scroller" }).toBeGreaterThan(0)
  })
}

test('on a phone, an ask opened from Needs you is in view after the tap', async ({ page }) => {
  await open(page, PHONE)
  const needs = page.getByRole('region', { name: 'Needs you' })
  // A tap on the card, as a person makes it: the card's hit target lies under its text.
  await needs.getByText('Calibrate the soil sensors after the rain').click()
  await expect(page).toHaveURL(/[?&]open=need%3A|[?&]open=need:/)
  const close = page.getByRole('button', { name: 'Close detail' })
  await expect(close, "the ask's panel opened out of reach, under the fold").toBeInViewport()
})
