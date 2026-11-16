/** A new chat scrolls on a phone. The empty chat stands its greeting, the composer and the chips in
 *  one column under the header, inside a frame that clips (`overflow-hidden`). On a fresh home the
 *  small-model card stands in the composer's stage too, and at 390 px the column is taller than the
 *  window: nothing scrolled, so the frame cut the last suggestions off at the fold, out of reach,
 *  and on a shorter phone part of the composer with them. Here the wheel reaches the last
 *  suggestion and the composer, each whole and the one thing under the pointer at its middle, at
 *  390×844 and at 390×667. The reads it needs are stubbed (the small-model card and six
 *  suggestions); nothing is written. */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { gotoRoute } from './helpers'

const SIZES = [
  { width: 390, height: 844 },
  { width: 390, height: 667 },
]
const SUGGESTIONS = [
  'Summarise what changed in my projects this week, and what still waits on me',
  'Draft a reply to the last message in my inbox that asks for a decision',
  'Plan tomorrow from my calendar and the tasks I left open today',
  'Find the notes I kept about the storage box in the rack and what they agree on',
  'Turn my reading list into a short brief with the three things worth my time',
  'Check which automations failed this week and say what each one needs from me',
]
const LAST = SUGGESTIONS[SUGGESTIONS.length - 1]

async function open(page: Page, size: { width: number; height: number }) {
  await page.setViewportSize(size)
  await page.route('**/api/onboarding', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const response = await route.fetch()
    const body = (await response.json()) as Record<string, unknown>
    // The fresh home's card about the small model that answers, and no "How should I explain
    // things?" window over the page.
    await route.fulfill({ response, json: { ...body, step: 'done', explain_asked: true, chat_is_bundled_floor: true } })
  })
  await page.route('**/api/suggestions*', (route) => route.request().method() === 'GET'
    ? route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ suggestions: SUGGESTIONS, generated_at: 1_790_000_000, stale: false, refreshing: false, needs_model: '' }),
    })
    : route.fallback())
  await gotoRoute(page, 'chat')
  await expect(page.getByTestId('bundled-floor-notice')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('button', { name: LAST })).toBeAttached()
}

/** Whether `target` is whole inside the window and is what the pointer finds at its middle. */
async function inReach(target: Locator, size: { height: number }) {
  const box = await target.boundingBox()
  if (!box || box.y < 0 || box.y + box.height > size.height) return false
  return target.evaluate((el, [x, y]) => {
    const hit = document.elementFromPoint(x, y)
    return !!hit && (el === hit || el.contains(hit))
  }, [box.x + box.width / 2, box.y + box.height / 2])
}

/** Wheels over the column, `dy` at a time, until `target` is in reach, at most ten times. */
async function wheelTo(page: Page, target: Locator, size: { width: number; height: number }, dy: number) {
  for (let i = 0; i <= 10; i++) {
    if (await inReach(target, size)) return true
    await page.mouse.move(size.width / 2, size.height / 2)
    await page.mouse.wheel(0, dy)
    await page.waitForTimeout(120)
  }
  return false
}

for (const size of SIZES) {
  test(`at ${size.width}×${size.height} the wheel reaches a new chat's last suggestion and its composer`, async ({ page }) => {
    await open(page, size)
    const last = page.getByRole('button', { name: LAST })
    const composer = page.locator('[data-composer-stage]')
    const fold = (await last.boundingBox())?.y ?? 0
    expect(fold, 'the last suggestion starts under the fold, so the column must scroll to reach it').toBeGreaterThan(size.height)
    expect(await wheelTo(page, last, size, 240), 'the wheel brings the last suggestion into reach').toBe(true)
    expect(await wheelTo(page, composer, size, -240), 'and back up to the whole composer').toBe(true)
    await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeInViewport()
  })
}
