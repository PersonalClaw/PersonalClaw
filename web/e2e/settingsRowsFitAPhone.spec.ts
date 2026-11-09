/** Every settings row fits a phone. At 390, on each settings page, no row is wider than its panel or
 *  the screen, and no row's label, hint or control runs out of its own box. Below the settings
 *  page's phone breakpoint (`sm`) a row stacks: its label and hint full width on top, its control
 *  under them. Each page is read as the run's gateway answers it, and nothing is written. The shots
 *  it attaches, Reply style at 390 in both themes, are the stacked row as the owner meets it. */
import { test, expect, type Locator, type Page } from '@playwright/test'
import { gotoRoute, seedTheme } from './helpers'
import { SETTINGS_PANELS } from './routes'

const PHONE = { width: 390, height: 900 }

/** Each settings row on the page that does not fit, named by its label, with what runs over. A
 *  settings row is `settingsUI`'s `Row`: a grid with a bottom rule whose first part is its label. */
async function rowsThatDoNotFit(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = []
    const rows = [...document.querySelectorAll('div')].filter(
      (el) => el.classList.contains('grid') && el.classList.contains('border-b')
        && el.firstElementChild?.getAttribute('data-type') === 'body-s',
    )
    for (const row of rows) {
      if ((row as HTMLElement).offsetParent === null) continue
      const parts = [...row.children] as HTMLElement[]
      const label = parts[0].textContent?.trim() || '(no label)'
      const box = row.getBoundingClientRect()
      const panel = row.parentElement!.getBoundingClientRect()
      if (box.right > panel.right + 1) out.push(`${label}: the row ends ${Math.round(box.right - panel.right)} px past its panel`)
      if (box.right > window.innerWidth + 1) out.push(`${label}: the row ends ${Math.round(box.right - window.innerWidth)} px past the screen`)
      parts.forEach((part, i) => {
        const what = i === 0 ? 'its label' : i === parts.length - 1 ? 'its control' : 'its hint'
        if (part.scrollWidth > part.clientWidth + 1) out.push(`${label}: ${what} needs ${part.scrollWidth} px and has ${part.clientWidth}`)
        const end = part.getBoundingClientRect().right
        if (end > box.right + 1) out.push(`${label}: ${what} ends ${Math.round(end - box.right)} px past the row`)
      })
    }
    return out
  })
}

/** A settings row, by its label. */
const row = (page: Page, label: string): Locator => page.getByText(label, { exact: true }).locator('xpath=..')

test.describe('Settings rows on a phone', () => {
  for (const id of SETTINGS_PANELS) {
    test(`every row on settings/${id} fits at ${PHONE.width}`, async ({ page }) => {
      await page.setViewportSize(PHONE)
      await gotoRoute(page, `settings/${id}`)
      expect(await rowsThatDoNotFit(page)).toEqual([])
    })
  }

  for (const theme of ['light', 'dark'] as const) {
    test(`Reply style at ${PHONE.width} in ${theme}`, async ({ page }, info) => {
      await page.setViewportSize({ width: PHONE.width, height: 1600 })
      await seedTheme(page, theme)
      await gotoRoute(page, 'settings/agent')
      const style = row(page, 'Reply style')
      await expect(style.getByRole('button', { name: /^Reply style: / }).first()).toBeVisible()
      const name = `settings-reply-style-${PHONE.width}-${theme}.png`
      const file = info.outputPath(name)
      await style.screenshot({ path: file })
      await info.attach(name, { path: file, contentType: 'image/png' })
    })
  }
})
