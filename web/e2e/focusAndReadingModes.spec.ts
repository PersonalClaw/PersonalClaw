import { test, expect, type Page } from '@playwright/test'
import { gotoRoute, driveScriptedTurns } from './helpers'
import { PALETTE_SEARCH_LABEL } from '../src/app/paletteSearchLabel'

// ── Focus mode and reading mode, by key and from the palette ────────────────────────────────────
//
// `web/REDESIGN.md` §4: focus mode is one key that hides the sidebar and the side pane, and the work
// surface takes ~90% of the window. §5 convention 11: reading mode folds the composer while the owner
// reads, and it comes back on a key or when they type. Both are reachable by key and from the
// palette, which shows each one's key. The unit half (which pane opens, the draft through a fold,
// focus) is `src/app/focusAndReadingModes.test.tsx`; this half is what only a browser can say: the
// real keys reach the shell, and the window's share is measured. The WCAG AA scan of both modes on
// is `a11y.spec.ts`'s Tier 7.
//
// The share is measured as `docs/screenshots/baseline.mjs` measures it for the baseline report: the
// shell's main area, minus the top band (the corner controls and the page header), minus the
// composer's height when the screen has one, minus a docked right pane, over the viewport.

const PROMPT = 'Read me a long answer, please'
const RAIL = 'nav[data-tour="rail"]'
const REGION = '[data-composer-region]'
const FOCUS_KEY = 'ControlOrMeta+Backslash'
const READING_KEY = 'ControlOrMeta+Shift+Period'

test.use({ viewport: { width: 1280, height: 900 } })

/** The work surface's share of the window, in percent, measured the way the baseline report is. */
function workShare(page: Page): Promise<number> {
  return page.evaluate(() => {
    const vw = innerWidth
    const vh = innerHeight
    const box = (el: Element | null | undefined) => {
      if (!el) return null
      const r = el.getBoundingClientRect()
      const x = Math.max(0, r.left), y = Math.max(0, r.top)
      const w = Math.min(vw, r.right) - x, h = Math.min(vh, r.bottom) - y
      return w > 0 && h > 0 ? { x, y, w, h } : null
    }
    const visible = (el: Element) => !!box(el) && el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })
    const main = [...document.querySelectorAll('main')].find((m) => !m.parentElement?.closest('main') && visible(m))
    const mainBox = box(main) ?? { x: 0, y: 0, w: vw, h: vh }
    const corners = main ? [...main.children].filter((c) => c.matches('.absolute.top-0') && visible(c)) : []
    const header = main ? [...main.querySelectorAll('header')].find((h) => visible(h) && h.getBoundingClientRect().top < 80) : null
    const composer = [...document.querySelectorAll('[data-composer-stage]')].find(visible) ?? null
    const pane = main ? [...main.querySelectorAll('[role="region"][aria-labelledby]')].find((r) => {
      const b = box(r)
      return visible(r) && b && Math.abs(b.x + b.w - (mainBox.x + mainBox.w)) <= 2 && b.h > vh * 0.5
    }) : null
    const headerBox = box(header)
    const cornerBottom = Math.max(0, ...corners.map((c) => { const b = box(c)!; return b.y + b.h }))
    const topBand = Math.max(cornerBottom, headerBox ? headerBox.y + headerBox.h : 0)
    const composerBox = box(composer)
    const paneBox = box(pane)
    const workW = (paneBox ? paneBox.x : mainBox.x + mainBox.w) - mainBox.x
    const workH = vh - topBand - (composerBox ? composerBox.h : 0)
    return Math.round((Math.max(0, workW) * Math.max(0, workH)) / (vw * vh) * 1000) / 10
  })
}

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
  const share = await workShare(page)
  expect(share, `the work surface took ${share}% of the window in focus mode`).toBeGreaterThanOrEqual(90)
})
