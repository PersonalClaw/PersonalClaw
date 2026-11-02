import { test, expect, type Page } from '@playwright/test'
import { gotoRoute } from './helpers'

// ── THE SIDEBAR IS A SHELL: ITS TOP AND BOTTOM BLOCKS STAY, AND ONLY THE CHAT HISTORY SCROLLS ─────
//
// #5878's acceptance, asked of a real browser, since jsdom lays nothing out. With two hundred chats,
// at 1440 × 900 and at 1280 × 720, the sidebar itself never scrolls: the top block (the icon and
// name, the leading destinations) and the bottom block (Inbox, Automations, Capabilities, Settings)
// keep their place while the chat history scrolls between them, it fills the height between them,
// and every control in both blocks is whole in the window and the topmost thing at its centre,
// before the chat history is scrolled and after. In a window too short for both blocks and the chat
// history's floor, the sidebar scrolls as one column, the chat history keeps its floor, and Settings,
// the last control, is still reached and shown whole.
//
// #5880's, asked of the same browser: the icon and name at the top are Home's link, from any page
// they open Home on Overview and light while it is open, and Home has no row of its own.
//
// The chats are route stubs, so the layout is the only variable.

const RAIL = 'nav[data-tour="rail"]'
const SCROLLER = `${RAIL} [data-rail-scroll]`

const COUNT = 200
const key = (i: number) => `e2e-shell-${String(i).padStart(3, '0')}`
/** Two hundred chats in no folder, newest first in the sidebar: `e2e-shell-000` is the newest. */
const SESSIONS = Array.from({ length: COUNT }, (_, i) => ({
  key: key(i), title: `Shell chat ${String(i).padStart(3, '0')}`, messages: 2, run_state: 'idle',
  last_activity_ts: new Date(Date.UTC(2026, 9, 1) - i * 60_000).toISOString(),
}))

async function stubChats(page: Page): Promise<void> {
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  await page.route('**/api/loops', (route) =>
    route.request().method() === 'GET' ? route.fulfill(json({ loops: [] })) : route.fallback())
  await page.route('**/api/chat/sessions', (route) =>
    route.request().method() === 'GET' ? route.fulfill(json(SESSIONS)) : route.fallback())
  await page.route('**/api/projects', (route) =>
    route.request().method() === 'GET' ? route.fulfill(json({ projects: [] })) : route.fallback())
  await page.route('**/api/chat/folders', (route) =>
    route.request().method() === 'GET' ? route.fulfill(json([])) : route.fallback())
  await page.route('**/api/chat/tags', (route) =>
    route.request().method() === 'GET' ? route.fulfill(json([])) : route.fallback())
}

interface Edges { top: number; bottom: number }

interface Shell {
  /** Whether the sidebar itself scrolls, as one column. */
  navScrolls: boolean
  top: Edges
  bottom: Edges
  history: Edges & { scrolls: boolean; height: number; floor: number }
  /** The controls of both blocks that are not whole in the window or not topmost at their centre. */
  hidden: string[]
}

/** Read the sidebar in one evaluate, so every number shares a layout pass. */
async function readShell(page: Page): Promise<Shell> {
  return page.evaluate((rail) => {
    const nav = document.querySelector<HTMLElement>(rail)!
    const edges = (el: Element) => {
      const r = el.getBoundingClientRect()
      return { top: r.top, bottom: r.bottom }
    }
    const top = nav.querySelector('[data-rail-top]')!
    const bottom = nav.querySelector('[data-rail-pinned]')!
    const history = nav.querySelector<HTMLElement>('[data-rail-scroll]')!
    const hidden = [...top.querySelectorAll<HTMLElement>('button, a[href]'), ...bottom.querySelectorAll<HTMLElement>('button, a[href]')]
      .filter((el) => el.getClientRects().length > 0)
      .filter((el) => {
        const r = el.getBoundingClientRect()
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)
        return r.top < 0 || r.bottom > innerHeight || !hit || !el.contains(hit)
      })
      .map((el) => el.getAttribute('aria-label') ?? el.textContent?.trim() ?? el.tagName)
    return {
      navScrolls: nav.scrollHeight > nav.clientHeight + 1,
      top: edges(top),
      bottom: edges(bottom),
      history: {
        ...edges(history),
        scrolls: history.scrollHeight > history.clientHeight + 1,
        height: history.clientHeight,
        floor: parseFloat(getComputedStyle(history).minHeight) || 0,
      },
      hidden,
    }
  }, RAIL)
}

/** Let the sidebar paint. */
const settle = (page: Page) => page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))))

for (const size of [{ width: 1440, height: 900 }, { width: 1280, height: 720 }]) {
  test.describe(`the sidebar at ${size.width} × ${size.height}, with ${COUNT} chats`, () => {
    test.use({ viewport: size })

    test('keeps both blocks whole and still while only the chat history scrolls', async ({ page }) => {
      await stubChats(page)
      await gotoRoute(page, 'dashboard')
      await expect(page.locator(`${RAIL} [data-thread-key="chat:${key(0)}"]`)).toBeVisible()

      const before = await readShell(page)
      expect(before.navScrolls, 'the sidebar itself does not scroll').toBe(false)
      expect(before.history.scrolls, 'the chat history does').toBe(true)
      expect(Math.abs(before.history.top - before.top.bottom), 'the chat history starts where the top block ends').toBeLessThanOrEqual(1)
      expect(Math.abs(before.history.bottom - before.bottom.top), 'and ends where the bottom block starts').toBeLessThanOrEqual(1)
      expect(before.bottom.bottom, 'the bottom block ends inside the window').toBeLessThanOrEqual(size.height + 0.5)
      expect.soft(before.hidden, 'every control in both blocks is whole and topmost').toEqual([])

      await page.evaluate((sel) => { document.querySelector(sel)!.scrollTop = 1_000_000 }, SCROLLER)
      await settle(page)
      const after = await readShell(page)
      expect(after.top, 'scrolled to its end, the top block has not moved').toEqual(before.top)
      expect(after.bottom, 'nor has the bottom block').toEqual(before.bottom)
      expect.soft(after.hidden, 'and every control in both is still whole and topmost').toEqual([])
    })
  })
}

test.describe('the sidebar in a window too short for both blocks and the chat history', () => {
  test('scrolls as one column, keeps the chat history\'s floor, and still reaches Settings whole', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 })
    await stubChats(page)
    await gotoRoute(page, 'dashboard')
    await expect(page.locator(`${RAIL} [data-thread-key="chat:${key(0)}"]`)).toBeVisible()
    // Short of the two blocks and the floor by 40 px, whatever the blocks measure in this build.
    const tall = await readShell(page)
    const blocks = (tall.top.bottom - tall.top.top) + (tall.bottom.bottom - tall.bottom.top)
    expect(tall.history.floor, 'the chat history has a floor').toBeGreaterThan(0)
    await page.setViewportSize({ width: 1280, height: Math.floor(blocks + tall.history.floor - 40) })
    await settle(page)

    const short = await readShell(page)
    expect(short.navScrolls, 'the sidebar scrolls as one column').toBe(true)
    expect(short.history.height, 'the chat history keeps its floor').toBeGreaterThanOrEqual(short.history.floor - 1)

    const settings = page.locator(`${RAIL} [data-rail-pinned] button[aria-label="Settings"]`)
    await settings.focus()
    await settle(page)
    const shown = await settings.evaluate((el) => {
      const r = el.getBoundingClientRect()
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)
      return { whole: r.top >= 0 && r.bottom <= innerHeight, mine: !!hit && el.contains(hit) }
    })
    expect(shown, 'focus brings Settings into view, whole and topmost').toEqual({ whole: true, mine: true })
  })
})

test.describe('the icon and name', () => {
  test.use({ viewport: { width: 1280, height: 800 } })

  test("are Home's link: from any page they open Home on Overview, and light while it is open", async ({ page }) => {
    await stubChats(page)
    await gotoRoute(page, 'projects')
    const home = page.locator(RAIL).getByRole('button', { name: 'PersonalClaw, Home', exact: true })
    await expect(home).not.toHaveAttribute('aria-current', 'page')
    await home.click()
    await expect.poll(() => page.evaluate(() => location.hash)).toBe('#/dashboard')
    await expect(page.getByRole('radiogroup', { name: 'View' }).getByRole('radio', { name: /Overview/ })).toBeChecked()
    await expect(home).toHaveAttribute('aria-current', 'page')
    await expect(page.locator(RAIL).getByRole('button', { name: /^Home(,|$)/ }), 'and Home has no row of its own').toHaveCount(0)
  })
})
