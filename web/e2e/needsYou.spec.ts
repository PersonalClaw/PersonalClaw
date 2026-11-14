/** Needs you in the built app (#6149): the list across the tab's width in columns while nothing is
 *  open, one column beside a panel, and the panel in the list's place below 840 px; the list holding
 *  still under the pointer while an answer lands and a new ask arrives; the cut-edge fades; and Back
 *  on a phone. Its reads are answered in the page (`NEEDS_YOU_READS`), so it writes nothing to the
 *  run's home. It attaches its shots at 1280, 1920 and 390 in both themes; with `NEEDS_YOU_SHOTS`
 *  set to a folder, it writes them there too. */
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { test, expect, type Page, type TestInfo } from '@playwright/test'
import { gotoRoute, seedTheme } from './helpers'
import { FIXED_WAITING, NEEDS_YOU_READS, answerStubbedRead } from './stubbedReads'

/** Answer Needs you's reads with the fixed ones, the waiting list from `waiting` while it is set. */
async function answerNeedsYousReads(page: Page, waiting?: { current: unknown }): Promise<void> {
  for (const stub of NEEDS_YOU_READS) {
    await page.route(stub.url, (r) => (waiting && /\/api\/waiting/.test(r.request().url())
      ? r.fulfill({ json: waiting.current })
      : answerStubbedRead(stub, r)))
  }
}

const list = (page: Page) => page.getByRole('region', { name: 'Needs you' })
const cards = (page: Page) => list(page).locator('[data-need-card]')

/** How many distinct columns the cards of *group* sit in. */
async function columnsOf(page: Page, group: 'waiting' | 'review'): Promise<number> {
  return page.locator(`[data-need-group="${group}"]`).evaluate((g) => new Set([...g.querySelectorAll('[data-need-card]')].map((c) => Math.round(c.getBoundingClientRect().left))).size)
}

async function shot(page: Page, info: TestInfo, name: string): Promise<void> {
  const file = info.outputPath(`${name}.png`)
  await page.screenshot({ path: file })
  await info.attach(`${name}.png`, { path: file, contentType: 'image/png' })
  const folder = process.env.NEEDS_YOU_SHOTS
  if (folder) {
    mkdirSync(folder, { recursive: true })
    await page.screenshot({ path: join(folder, `${name}.png`) })
  }
}

test.describe('Needs you', () => {
  test('takes the width in columns with nothing open, and one column beside a panel', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 })
    await answerNeedsYousReads(page)
    await gotoRoute(page, 'mission-control')
    await expect(cards(page)).toHaveCount(6)
    expect(await columnsOf(page, 'waiting')).toBeGreaterThanOrEqual(2)
    // The first card's panel opens beside the list, which goes to one column.
    await list(page).getByRole('button', { name: /^Show the details of / }).first().click()
    await expect(page.getByRole('region', { name: 'bash' })).toBeVisible()
    await expect(list(page)).toBeVisible()
    expect(await columnsOf(page, 'waiting')).toBe(1)
  })

  test('at 200% text, one column', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 })
    await answerNeedsYousReads(page)
    await gotoRoute(page, 'mission-control')
    await page.evaluate(() => { document.documentElement.style.fontSize = '200%' })
    await expect.poll(() => columnsOf(page, 'waiting')).toBe(1)
  })

  test('below 840 px, the panel takes the list’s place, and Back brings the list back', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await answerNeedsYousReads(page)
    await gotoRoute(page, 'mission-control')
    await list(page).getByRole('button', { name: /^Show the details of / }).first().click()
    await expect(page.getByRole('region', { name: 'bash' })).toBeVisible()
    await expect(list(page)).toBeHidden()
    await page.goBack()
    await expect(list(page)).toBeVisible()
    await expect(page.getByRole('region', { name: 'bash' })).toHaveCount(0)
  })

  test('a cut edge fades, and an uncut one does not', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 520 })
    await answerNeedsYousReads(page)
    await gotoRoute(page, 'mission-control')
    await expect(page.locator('[data-fade="bottom"]').first()).toBeAttached()
    await expect(page.locator('[data-fade="top"]')).toHaveCount(0)
  })

  test('holds still under the pointer: an answered card keeps its place, and a new ask waits behind the header', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 })
    const waiting = { current: FIXED_WAITING as unknown }
    await answerNeedsYousReads(page, waiting)
    await page.route('**/api/workflows/runs/r-e2e/confirm', (r) => r.fulfill({ json: { ok: true } }))
    await gotoRoute(page, 'mission-control')
    const before = await cards(page).evaluateAll((cs) => cs.map((c) => c.getAttribute('data-need-key')))
    // The next read: the gate answered, and a new ask came in first.
    const fresh = { ...FIXED_WAITING.waiting[3], id: 'loop_question:l-e2e-new', work: 'loop:l-e2e-new', route: '#/loops/l-e2e-new',
      brief: { name: 'Tidy the downloads', question: 'Keep the installers?' } }
    waiting.current = { ...FIXED_WAITING, waiting: [fresh, ...FIXED_WAITING.waiting.filter((w) => !w.id.startsWith('gate:'))] }
    const gate = list(page).locator('[data-need-key="waiting:gate:r-e2e:conf-1:0"]')
    const box = await gate.boundingBox()
    await gate.getByRole('button', { name: /^Approve/ }).click()
    await expect(page.getByText('1 new ask')).toBeVisible()
    expect(await cards(page).evaluateAll((cs) => cs.map((c) => c.getAttribute('data-need-key')))).toEqual(before)
    expect((await gate.boundingBox())?.y).toBe(box?.y)
    await page.getByRole('button', { name: 'Show them' }).click()
    await expect(list(page).locator('[data-need-key="waiting:loop_question:l-e2e-new"]')).toBeVisible()
  })

  for (const theme of ['light', 'dark'] as const) {
    for (const [w, h] of [[1280, 900], [1920, 1080], [390, 844]] as const) {
      test(`its shots at ${w} in ${theme}`, async ({ page }, info) => {
        await page.setViewportSize({ width: w, height: h })
        await seedTheme(page, theme)
        await answerNeedsYousReads(page)
        await gotoRoute(page, 'mission-control')
        await expect(cards(page)).toHaveCount(6)
        await shot(page, info, `needs-you-${w}-${theme}`)
        if (w === 390) return
        await list(page).getByRole('button', { name: /^Show the details of / }).first().click()
        await expect(page.getByRole('region', { name: 'bash' })).toBeVisible()
        await shot(page, info, `needs-you-${w}-${theme}-panel`)
      })
    }
  }
})
