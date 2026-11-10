/** Settings › Agent › Agent defaults' Explanations row, in the built app: its four choices from the
 *  one table (`src/personalclaw/explanations.json`), in the table's order and right after Reply
 *  style, with the choice the config states pressed and its hint beside it. The config's read is
 *  answered in the page, from the run's own config with one explanation chosen, so the spec writes
 *  nothing to the run's home. The shots it attaches, Reply style and Explanations at 1280 and 390
 *  in both themes, are the row as the owner meets it, kept in the run's report. */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, expect, type Locator, type Page } from '@playwright/test'
import { gotoRoute, seedTheme } from './helpers'

const TABLE = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'src', 'personalclaw', 'explanations.json')
const LABELS = (JSON.parse(readFileSync(TABLE, 'utf8')) as { id: string; label: string }[]).map((r) => r.label)
/** The choice the answered config states: not the default, so the pressed pill is one a person picked. */
const CHOSEN = 'step_by_step'
const HINT = 'How replies explain things to you. Changes how things are explained, never a fact, a caveat or a refusal. A chat can choose its own in Chat preferences.'

/** Answers the page's config read with the run's own config, its explanation set to {@link CHOSEN}.
 *  Every other request, and any write, goes to the run's gateway as it would. */
async function answerTheConfigRead(page: Page): Promise<void> {
  await page.route(/\/api\/config\/personalclaw(\?.*)?$/, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const response = await route.fetch()
    const body = (await response.json()) as { agent?: Record<string, unknown> }
    await route.fulfill({ response, json: { ...body, agent: { ...(body.agent ?? {}), explain: CHOSEN } } })
  })
}

/** A settings row, by its label. */
const row = (page: Page, label: string): Locator => page.getByText(label, { exact: true }).locator('xpath=..')

test.describe('Settings › Agent › Agent defaults › Explanations', () => {
  test('offers the table’s choices after Reply style, the stated one pressed', async ({ page }) => {
    await answerTheConfigRead(page)
    await gotoRoute(page, 'settings/agent')
    const explanations = row(page, 'Explanations')
    await expect(explanations).toBeVisible()
    await expect(explanations.getByText(HINT, { exact: true })).toBeVisible()
    const pills = explanations.getByRole('button', { name: /^Explanations: / })
    await expect(pills).toHaveText(LABELS)
    await expect(explanations.getByRole('button', { name: 'Explanations: Step by step' })).toHaveAttribute('aria-pressed', 'true')
    await expect(pills.and(page.locator('[aria-pressed="true"]'))).toHaveCount(1)
    const [style, mine] = [await row(page, 'Reply style').boundingBox(), await explanations.boundingBox()]
    expect(style!.y + style!.height).toBeLessThanOrEqual(mine!.y + 1)
  })

  for (const theme of ['light', 'dark'] as const) {
    // A phone's height leaves the rows under the fixed bottom bar, which a shot of them would draw over.
    for (const [w, h, tag] of [[1280, 900, 'desktop'], [390, 1600, 'mobile']] as const) {
      test(`the row at ${w} in ${theme}`, async ({ page }, info) => {
        await page.setViewportSize({ width: w, height: h })
        await seedTheme(page, theme)
        await answerTheConfigRead(page)
        await gotoRoute(page, 'settings/agent')
        const explanations = row(page, 'Explanations')
        await expect(explanations.getByRole('button', { name: 'Explanations: Step by step' })).toHaveAttribute('aria-pressed', 'true')
        await row(page, 'Reply style').scrollIntoViewIfNeeded()
        const [style, mine] = [(await row(page, 'Reply style').boundingBox())!, (await explanations.boundingBox())!]
        for (const box of [style, mine]) {
          expect(box.y, 'a row starts above the viewport').toBeGreaterThanOrEqual(0)
          expect(box.y + box.height, 'a row ends below the viewport').toBeLessThanOrEqual(h)
        }
        const top = Math.min(style.y, mine.y)
        const left = Math.min(style.x, mine.x)
        const clip = {
          x: left, y: top,
          width: Math.max(style.x + style.width, mine.x + mine.width) - left,
          height: Math.max(style.y + style.height, mine.y + mine.height) - top,
        }
        const name = `settings-agent-explanations-${tag}-${theme}.png`
        const file = info.outputPath(name)
        await page.screenshot({ path: file, clip })
        await info.attach(name, { path: file, contentType: 'image/png' })
      })
    }
  }
})
