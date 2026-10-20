import { test, expect, type Page } from '@playwright/test'
import { gotoRoute, driveScriptedTurns } from './helpers'
import { PALETTE_SEARCH_LABEL } from '../src/app/paletteSearchLabel'
import { measureWorkShare } from './workShare'

// ── Focus mode and reading mode, by key and from the palette ────────────────────────────────────
//
// `web/REDESIGN.md` §4: focus mode is one key that hides the sidebar, the side pane and, while the
// gateway answers, the status line, and the work surface takes ~90% of the window. §5 convention
// 11: reading mode folds the composer while the owner reads, and it comes back on a key or when
// they type. Both are reachable by key and from the palette, which shows each one's key. The unit
// half (which pane opens, the draft through a fold, focus) is
// `src/app/focusAndReadingModes.test.tsx`; this half is what only a browser can say: the real keys
// reach the shell, and the window's share is measured. The WCAG AA scan of both modes on is
// `a11y.spec.ts`'s Tier 7.
//
// The share is `workShare.ts`'s measure: the shell's main area, minus the top band (the corner
// controls and the page header), minus the composer's height when the screen has one, minus the
// status line while it shows, minus a docked right pane, over the viewport. Focus mode hides the
// status line while the gateway answers, as it does here.

const PROMPT = 'Read me a long answer, please'
const RAIL = 'nav[data-tour="rail"]'
const REGION = '[data-composer-region]'
const FOCUS_KEY = 'ControlOrMeta+Backslash'
const READING_KEY = 'ControlOrMeta+Shift+Period'
const STATUS_LINE = 'footer[aria-label="Status"]'

test.use({ viewport: { width: 1280, height: 900 } })

/** The work surface's share of the window, in percent (`workShare.ts`). */
const workShare = (page: Page): Promise<number> => page.evaluate(measureWorkShare)

async function openChatWithATurn(page: Page) {
  await gotoRoute(page, 'chat/new')
  await driveScriptedTurns(page, PROMPT, 1)
  await expect(page.locator(RAIL)).toBeVisible()
}

test('by key: focus mode hides the sidebar, reading mode folds the composer, and the work takes ~90% of the window', async ({ page }) => {
  await openChatWithATurn(page)
  const before = await workShare(page)

  await page.keyboard.press(FOCUS_KEY)
  await expect(page.locator(RAIL)).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Leave focus mode' })).toBeVisible()
  await expect(page.locator(STATUS_LINE), 'focus mode hides the status line while the gateway answers').toHaveCount(0)

  await page.keyboard.press(READING_KEY)
  await expect(page.locator(REGION)).toHaveAttribute('data-folded', 'true')
  await expect(page.getByRole('button', { name: /^Write a reply/ })).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Message input' })).not.toBeInViewport()
  const share = await workShare(page)
  expect(share, `the work surface took ${share}% of the window in focus and reading mode (${before}% before)`).toBeGreaterThanOrEqual(90)

  // Typing brings the composer back, and what was typed is the draft.
  await page.keyboard.type('Half a thought')
  await expect(page.locator(REGION)).toHaveAttribute('data-folded', 'false')
  const composer = page.getByRole('textbox', { name: 'Message input' })
  await expect(composer).toBeFocused()
  await expect(composer).toHaveText('Half a thought')

  // The key folds it and brings it back, and the draft survives the fold.
  await page.keyboard.press(READING_KEY)
  await expect(page.locator(REGION)).toHaveAttribute('data-folded', 'true')
  await expect(page.getByRole('button', { name: /^Continue your draft: Half a thought/ })).toBeFocused()
  await page.keyboard.press(READING_KEY)
  await expect(page.locator(REGION)).toHaveAttribute('data-folded', 'false')
  await expect(composer).toHaveText('Half a thought')

  await page.keyboard.press(FOCUS_KEY)
  await expect(page.locator(RAIL)).toBeVisible()
  await expect(page.locator(STATUS_LINE)).toBeVisible()
})

test('from the palette: each mode is a row that shows its key and runs it', async ({ page }) => {
  await openChatWithATurn(page)
  const palette = page.getByLabel(PALETTE_SEARCH_LABEL)

  await page.keyboard.press('ControlOrMeta+k')
  await palette.fill('focus mode')
  const focusRow = page.getByRole('option', { name: /Focus mode/ })
  await expect(focusRow).toContainText(/⌘\\|Ctrl\+\\/)
  await page.keyboard.press('Enter')
  await expect(page.locator(RAIL)).toHaveCount(0)

  await page.keyboard.press('ControlOrMeta+k')
  await palette.fill('reading mode')
  const readingRow = page.getByRole('option', { name: /Reading mode/ })
  await expect(readingRow).toContainText(/⌘⇧\.|Ctrl\+Shift\+\./)
  await page.keyboard.press('Enter')
  await expect(page.locator(REGION)).toHaveAttribute('data-folded', 'true')

  // The folded line is a control on screen, so leaving reading mode needs no key and no hover.
  await page.getByRole('button', { name: /^Write a reply/ }).click()
  await expect(page.locator(REGION)).toHaveAttribute('data-folded', 'false')
  await expect(page.getByRole('textbox', { name: 'Message input' })).toBeFocused()

  // And neither does leaving focus mode.
  await page.getByRole('button', { name: 'Leave focus mode' }).click()
  await expect(page.locator(RAIL)).toBeVisible()
})

test('a page with no composer gives the work surface ~90% of the window in focus mode', async ({ page }) => {
  await gotoRoute(page, 'knowledge')
  await page.keyboard.press(FOCUS_KEY)
  await expect(page.locator(RAIL)).toHaveCount(0)
  await expect(page.locator(STATUS_LINE)).toHaveCount(0)
  const share = await workShare(page)
  expect(share, `the work surface took ${share}% of the window in focus mode`).toBeGreaterThanOrEqual(90)
})
