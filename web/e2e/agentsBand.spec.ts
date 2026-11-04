/** Home › Overview's "Your agents today", in the built app: a row per agent on the hours around
 *  now, each with its state in words, an ask linked to Attention and answered nowhere else, and a
 *  loop that runs to a cycle and never to a guessed time. The band's reads are answered in the page
 *  itself, so it writes nothing to the run's home and reads nothing another spec counts. The shots
 *  it attaches, at 1440 and 390 in both themes, are the band as a person meets it. */
import { test, expect } from '@playwright/test'
import { answerTheBandsReads, band } from './agentsBandFixture'
import { gotoRoute, seedTheme } from './helpers'
import { MIN_TARGET_PX, axeFindings } from './surfaceChecks'
import { CLIPPED_RING } from './nonAxeA11yChecks'

/** The shared clipped-ring check, scoped to the band's own controls: the rest of Home is the
 *  surfaces matrix's to judge. */
const BAND_CLIPPED_RING = CLIPPED_RING.replace(
  'const els = [...document.querySelectorAll(',
  "const band = [...document.querySelectorAll('section')].find((s) => /Your agents today/.test(s.querySelector('h2')?.textContent ?? ''))\n  const els = [...band.querySelectorAll(",
)

test.describe('Your agents today', () => {
  test('reads as a person meets it', async ({ page }) => {
    await answerTheBandsReads(page)
    await gotoRoute(page, 'dashboard?view=overview')
    const list = band(page).getByRole('list', { name: 'Your agents' })
    await expect(list).toBeVisible()

    const rows = list.getByRole('listitem')
    await expect(rows.first()).toHaveAccessibleName(/^PersonalClaw, waiting on you: /)
    await expect(rows.nth(1)).toHaveAccessibleName(/^Code reviewer, waiting on you: /)
    await expect(list.getByRole('listitem', { name: /^Release helper, failed: Failed at \d\d:\d\d: Couldn’t reach api\.github\.com/ })).toBeVisible()
    await expect(band(page).getByText('2 asks wait on you · 1 failed today')).toBeVisible()
    await expect(band(page).getByText('· asked 2 min ago')).toBeVisible()
    await expect(list.getByRole('listitem', { name: /^Researcher, working: / })).toBeVisible()
    await expect(list.getByRole('listitem', { name: /^Scheduled, agent not named: / })).toBeVisible()

    await expect(list.getByRole('link', { name: /^Answer in Attention/ })).toHaveCount(2)
    await expect(list.getByRole('link', { name: 'Answer in Attention: PersonalClaw’s ask', exact: true })).toBeVisible()
    await expect(list.getByRole('link', { name: 'Answer in Attention: Code reviewer’s ask', exact: true })).toBeVisible()
    const answer = list.getByRole('link', { name: /^Answer in Attention/ }).first()
    await expect(answer).toHaveAttribute('href', /^#\/dashboard\?view=attention&open=waiting%3Aapproval%3Ae2e-band-ap$/)
    await expect(band(page).getByRole('button', { name: /^(approve|allow|deny)/i })).toHaveCount(0)
    await expect(band(page).getByText('Runs to cycle 8')).toBeVisible()
    await expect(band(page).getByText(/On its own today: \$1\.79 of \$5/)).toBeVisible()

    await answer.click()
    await expect(page).toHaveURL(/view=attention/)
  })

  // The 44 px rule for a touch screen, at the two phone widths; and each control's focus ring whole.
  for (const w of [320, 390] as const) {
    test(`its controls are ${MIN_TARGET_PX} px targets with whole focus rings at ${w}`, async ({ page }) => {
      await page.setViewportSize({ width: w, height: 1600 })
      await answerTheBandsReads(page)
      await gotoRoute(page, 'dashboard?view=overview')
      await expect(band(page).getByText('2 asks wait on you · 1 failed today')).toBeVisible()
      const { measured, small } = await band(page).locator('a[href], button').evaluateAll((els, min) => {
        const shown = els.filter((el) => (el as HTMLElement).offsetParent !== null)
        return {
          measured: shown.length,
          small: shown
            .map((el) => ({ name: el.getAttribute('aria-label') || el.textContent?.trim() || el.tagName, r: el.getBoundingClientRect() }))
            .filter(({ r }) => r.width < min - 0.5 || r.height < min - 0.5)
            .map(({ name, r }) => `${name}: ${Math.round(r.width)}×${Math.round(r.height)}`),
        }
      }, MIN_TARGET_PX)
      // The filter, Manage agents, three linked names and the two asks' links, at the least.
      expect(measured).toBeGreaterThanOrEqual(7)
      expect(small).toEqual([])
      // One Tab puts the page in keyboard modality, so each band control the check focuses draws its
      // focus-visible ring.
      await page.keyboard.press('Tab')
      expect(BAND_CLIPPED_RING).not.toBe(CLIPPED_RING)
      expect(await page.evaluate<string[]>(`(${BAND_CLIPPED_RING})()`)).toEqual([])
      // The shared check passes over a ring drawn inside its control, so a linked name's is held here:
      // drawn, and inside the link's own edge, where the wrapper that cuts the name can't cut it.
      const ring = await band(page).getByRole('link', { name: 'PersonalClaw', exact: true }).evaluate((el) => {
        ;(el as HTMLElement).focus()
        const cs = getComputedStyle(el)
        return { style: cs.outlineStyle, width: parseFloat(cs.outlineWidth), offset: parseFloat(cs.outlineOffset) }
      })
      expect(ring.style).not.toBe('none')
      expect(ring.width).toBeGreaterThan(0)
      expect(ring.offset).toBeLessThan(0)
    })
  }

  for (const theme of ['light', 'dark'] as const) {
    test(`passes axe with its asks and its failure in ${theme}`, async ({ page }) => {
      await seedTheme(page, theme)
      await answerTheBandsReads(page)
      await gotoRoute(page, 'dashboard?view=overview')
      await expect(band(page).getByText('2 asks wait on you · 1 failed today')).toBeVisible()
      expect(await axeFindings(page, 'dashboard')).toEqual([])
    })

    // A phone's height leaves the band under the fixed bottom bar, which a shot of it would draw over.
    for (const [w, h, tag] of [[1440, 900, 'desktop'], [390, 1600, 'mobile']] as const) {
      test(`the band at ${w} in ${theme}`, async ({ page }, info) => {
        await page.setViewportSize({ width: w, height: h })
        await seedTheme(page, theme)
        await answerTheBandsReads(page)
        await gotoRoute(page, 'dashboard?view=overview')
        await expect(band(page).getByRole('list', { name: 'Your agents' })).toBeVisible()
        // Two scheduled runs' names keep apart, each cut short at its share of the gap.
        const scheduled = band(page).getByRole('listitem', { name: /^Scheduled, agent not named: / })
        const box = async (name: string) => (await scheduled.locator('[aria-hidden="true"]').getByText(name, { exact: true }).boundingBox())!
        const [plants, summary] = [await box('Water the plants'), await box('Evening summary')]
        const [first, second] = plants.x < summary.x ? [plants, summary] : [summary, plants]
        expect(first.x + first.width).toBeLessThanOrEqual(second.x)
        const file = info.outputPath(`agents-band-${tag}-${theme}.png`)
        await band(page).screenshot({ path: file })
        await info.attach(`agents-band-${tag}-${theme}.png`, { path: file, contentType: 'image/png' })
      })
    }
  }
})
