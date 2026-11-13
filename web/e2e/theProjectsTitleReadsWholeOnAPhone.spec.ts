/** The Projects title reads whole on a phone. At 390 the header holds search, Show, its "…" and New
 *  project beside the title, so New project draws its icon alone there, still named and titled "New
 *  project", and "Projects" is never cut. Read from the visual spec's fixed reads; nothing is
 *  written. */
import { expect, test } from '@playwright/test'
import { FIXED_CLOCK } from './fixedClock'
import { gotoRoute } from './helpers'
import { PROJECTS_LIST, answerStubbedRead } from './stubbedReads'

const PHONE = { width: 390, height: 844 }

test(`the Projects title reads whole at ${PHONE.width}, and New project is still named`, async ({ page }) => {
  await page.setViewportSize(PHONE)
  await page.clock.setFixedTime(new Date(FIXED_CLOCK.projects))
  for (const stub of PROJECTS_LIST) await page.route(stub.url, (r) => answerStubbedRead(stub, r))
  await gotoRoute(page, 'projects')
  await expect(page.getByRole('heading', { level: 2, name: /^Garden/ })).toBeVisible()

  const title = page.getByRole('heading', { level: 1, name: 'Projects' })
  await expect(title).toBeVisible()
  // The header settles its tier once it has measured, so the title is read once that holds.
  await expect.poll(async () => title.evaluate((el) => el.scrollWidth - el.clientWidth),
    { message: 'the title is cut: it needs more than its box' }).toBeLessThanOrEqual(0)

  const create = page.getByRole('button', { name: 'New project' })
  await expect(create).toBeVisible()
  await expect(create).toHaveAttribute('title', 'New project')
})
