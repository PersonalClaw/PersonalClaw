/** The Library's Power user view fits a phone. Its tab list stood as a 184 px column beside the
 *  panel in one row, so at 390 px each panel had about 205 px: the Graph's heading read "Gra…" under
 *  its own controls, an item's title a few letters, and Library settings' rows a word a line. On a
 *  phone the tabs are a strip above the panel, which has the page's whole width, and no tab's name
 *  or panel's heading is cut. The tabs keep their roles, their order and their arrows (Right and
 *  Left along the strip). Opened as a visit (`?view=`), so the setting is never written. */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { gotoRoute } from './helpers'

const PHONE = { width: 390, height: 844 }
const VIEWS = ['graph', 'items', 'settings'] as const
const ORDER = ['Graph', 'Items', 'Tags', 'Conflicts', 'Decisions', 'Library settings']

const tabs = (page: Page) => page.getByRole('tablist', { name: 'Library views' })

/** Whether the text of `el` fits its own box: a name cut with an ellipsis is wider than it. */
const cut = (el: Locator) => el.evaluate((e) => e.scrollWidth > e.clientWidth + 1)

for (const view of VIEWS) {
  test(`at 390 px the ${view} tab's panel spans the page and nothing on it or its tabs is cut`, async ({ page }) => {
    await page.setViewportSize(PHONE)
    await gotoRoute(page, `library?view=${view}`)
    await expect(tabs(page)).toBeVisible({ timeout: 15_000 })
    const panel = page.getByRole('tabpanel')
    await expect(panel).toBeVisible()
    const box = (await panel.boundingBox())!
    expect(box.width, `the ${view} panel's width at 390 px`).toBeGreaterThanOrEqual(PHONE.width - 2)
    expect(box.x).toBeLessThanOrEqual(1)
    for (const heading of await panel.getByRole('heading').all()) {
      if (!(await heading.isVisible())) continue
      expect(await cut(heading), `the heading "${await heading.textContent()}" is whole`).toBe(false)
    }
    const names = tabs(page).getByRole('tab')
    expect((await names.allTextContents()).map((t) => ORDER.find((o) => t.startsWith(o)))).toEqual(ORDER)
    for (const name of await names.locator('[data-type="label-l"]').all()) {
      expect(await cut(name), `the tab "${await name.textContent()}" is whole`).toBe(false)
    }
  })
}

test('at 390 px the strip keeps its arrows: Right and Left move along the tabs in order', async ({ page }) => {
  await page.setViewportSize(PHONE)
  await gotoRoute(page, 'library?view=graph')
  await expect(tabs(page)).toHaveAttribute('aria-orientation', 'horizontal', { timeout: 15_000 })
  const graph = tabs(page).getByRole('tab', { name: /^Graph/ })
  await graph.focus()
  await page.keyboard.press('ArrowRight')
  await expect(tabs(page).getByRole('tab', { name: /^Items/ })).toBeFocused()
  await page.keyboard.press('ArrowLeft')
  await expect(graph).toBeFocused()
  await page.keyboard.press('ArrowLeft')
  await expect(tabs(page).getByRole('tab', { name: /^Library settings/ })).toBeFocused()
})

test("at 390 px a focused tab's ring is whole: the strip, which scrolls and so clips, never cuts it", async ({ page }) => {
  // A scroller clips its children's outlines: with the global 2 px ring 2 px outside each tab, the
  // strip cut 4 px off its top and bottom on every tab, and off the first one's left. And arrowing to
  // a tab the strip showed only in part left it cut at the strip's edge, focus scrolling it no further.
  await page.setViewportSize(PHONE)
  await gotoRoute(page, 'library?view=graph')
  await expect(tabs(page)).toHaveAttribute('aria-orientation', 'horizontal', { timeout: 15_000 })
  await tabs(page).getByRole('tab', { name: /^Graph/ }).focus()
  for (const [i, name] of ORDER.entries()) {
    if (i) await page.keyboard.press('ArrowRight')
    const tab = tabs(page).getByRole('tab', { name: new RegExp(`^${name}`) })
    await expect(tab).toBeFocused()
    // The strip scrolls a tab it shows only in part into view, which can take a frame or two: read once
    // it has settled.
    const ring = () => tab.evaluate((el) => {
      const s = getComputedStyle(el)
      const reach = s.outlineStyle === 'none' ? 0 : Math.max(0, parseFloat(s.outlineWidth) + parseFloat(s.outlineOffset))
      const t = el.getBoundingClientRect()
      const box = el.closest('[role="tablist"]')!.getBoundingClientRect()
      const r = (b: DOMRect) => [b.left, b.top, b.right, b.bottom].map((v) => Math.round(v)).join(',')
      return { reach, at: `tab ${r(t)} strip ${r(box)} scrollLeft ${(el.closest('[role="tablist"]') as HTMLElement).scrollLeft}`, inside: t.left - reach >= box.left - 0.5 && t.right + reach <= box.right + 0.5 && t.top - reach >= box.top - 0.5 && t.bottom + reach <= box.bottom + 0.5 }
    })
    await expect.poll(async () => (await ring()).inside, {
      message: `the ring of "${name}" (${(await ring()).reach} px past the tab) is inside the strip (${(await ring()).at})`,
      timeout: 3_000,
    }).toBe(true)
  }
})

