import { test, expect, type Page } from '@playwright/test'
import { gotoRoute } from './helpers'
import { idleRunState } from '../src/lib/workPhase'

// ── A Chat history board card gives its room to the chat's title (#5879) ──────────────────────
//
// Chat history's board puts each chat in a column of its tag, about 260 px wide. A card there gives
// its room to the chat's title: the title wraps rather than being cut to one line, the tags sit on
// their own line under it, and there is no message count. The selection tick hangs half outside the
// card's top-right corner, so it takes no room inside, and shows on hover, on focus and while
// selecting; a tick never opens the peek, a click on the card does, and a card still drags to
// another tag's column. jsdom lays nothing out, so this asks a browser, on a board whose chats and
// tags are answered here.

const TAGS = [
  { id: 't-work', name: 'Work', color: '#3f8f6b' },
  { id: 't-home', name: 'Home', color: '' },
  { id: 't-garden', name: 'Garden', color: '#a0662b' },
]
/** Two lines at a 260 px column, and no more. */
const TITLE = 'Plan the allotment beds for spring and the seed order'

const chat = (key: string, title: string, tags: string[], messages = 12) => ({
  key, title, origin: 'manual', messages, tags, run_state: idleRunState(key), last_activity_ts: '2026-10-01T09:00:00Z',
})
const SESSIONS = [
  chat('chat-plan', TITLE, ['t-work', 't-garden']),
  chat('chat-digest', 'Nightly digest of the reading list', ['t-work']),
  chat('chat-notes', 'Tidy the notes folder', ['t-home']),
]

interface Board { tagEdits: { key: string; body: unknown }[] }

async function plantTheBoard(page: Page): Promise<Board> {
  const board: Board = { tagEdits: [] }
  await page.route('**/api/chat/sessions', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SESSIONS) })
      : route.fallback())
  await page.route('**/api/chat/tags', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(TAGS) })
      : route.fallback())
  await page.route('**/api/chat/folders', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      : route.fallback())
  await page.route('**/api/chat/sessions/*/tags', async (route) => {
    const key = decodeURIComponent(new URL(route.request().url()).pathname.split('/').slice(-2)[0])
    const body = route.request().postDataJSON() as { add?: string[]; remove?: string[] }
    board.tagEdits.push({ key, body })
    const was = SESSIONS.find((s) => s.key === key)?.tags ?? []
    const tags = [...was.filter((t) => !(body.remove ?? []).includes(t)), ...(body.add ?? [])]
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, tags }) })
  })
  return board
}

/** A chat's card in the first column it is in: a chat with two tags is in both of theirs. */
const card = (page: Page, key: string) => page.locator(`[data-board-card="${key}"]`).first()
const tick = (page: Page, key: string) => card(page, key).locator('[data-board-card-tick]')

async function openTheBoard(page: Page): Promise<Board> {
  await page.setViewportSize({ width: 1280, height: 900 })
  const board = await plantTheBoard(page)
  await gotoRoute(page, 'chat/history?view=board')
  await expect(card(page, 'chat-plan'), 'the board drew its cards').toBeVisible()
  return board
}

/** The tick's drawn opacity, once its fade has run. */
async function tickOpacity(page: Page, key: string): Promise<number> {
  await page.waitForTimeout(250)
  return tick(page, key).evaluate((el) => Number(getComputedStyle(el).opacity))
}

test.describe('a Chat history board card gives its room to the chat\'s title', () => {
  test('at a 260 px column, a card shows a two-line title in full, its tags under it, and no message count', async ({ page }) => {
    await openTheBoard(page)
    const m = await card(page, 'chat-plan').evaluate((el) => {
      const title = el.querySelector<HTMLElement>('[data-board-card-title]')!
      const tags = el.querySelector<HTMLElement>('[data-board-card-tags]')!
      const t = title.getBoundingClientRect()
      const lineHeight = parseFloat(getComputedStyle(title).lineHeight)
      return {
        width: Math.round(el.getBoundingClientRect().width),
        lines: Math.round(t.height / lineHeight),
        clipped: title.scrollHeight > title.clientHeight + 1,
        titleBottom: t.bottom,
        tagsTop: tags.getBoundingClientRect().top,
        text: el.textContent ?? '',
      }
    })
    expect(m.width, `the card is a board column's width: ${JSON.stringify(m)}`).toBeLessThanOrEqual(320)
    expect(m.lines, `the title wraps to two lines: ${JSON.stringify(m)}`).toBe(2)
    expect(m.clipped, 'the title is shown in full').toBe(false)
    expect(m.tagsTop, 'the tags sit under the title').toBeGreaterThanOrEqual(m.titleBottom - 1)
    expect(m.text).toContain('Work')
    expect(m.text).toContain('Garden')
    expect(m.text).not.toMatch(/\d+ messages?/)
  })

  test('the tick hangs half outside the card\'s top-right corner, inside the column\'s view', async ({ page }) => {
    await openTheBoard(page)
    await card(page, 'chat-plan').hover()
    const m = await card(page, 'chat-plan').evaluate((el) => {
      const c = el.getBoundingClientRect()
      const box = el.querySelector<HTMLElement>('[data-board-card-tick] input')!.getBoundingClientRect()
      let scroller: HTMLElement | null = el.parentElement
      while (scroller && getComputedStyle(scroller).overflowY !== 'auto') scroller = scroller.parentElement
      const s = scroller!.getBoundingClientRect()
      return { card: { top: c.top, right: c.right }, box: { top: box.top, right: box.right, left: box.left, bottom: box.bottom }, scroller: { top: s.top, right: s.right } }
    })
    const cx = (m.box.left + m.box.right) / 2
    const cy = (m.box.top + m.box.bottom) / 2
    expect(Math.abs(cx - m.card.right), `centred on the card's right edge: ${JSON.stringify(m)}`).toBeLessThanOrEqual(2)
    expect(Math.abs(cy - m.card.top), `centred on the card's top edge: ${JSON.stringify(m)}`).toBeLessThanOrEqual(2)
    expect(m.box.top, 'nothing of it is cut off by the column').toBeGreaterThanOrEqual(m.scroller.top)
    expect(m.box.right, 'nothing of it is cut off by the column').toBeLessThanOrEqual(m.scroller.right)
  })

  test('the tick shows on hover, on keyboard focus and while selecting', async ({ page }) => {
    await openTheBoard(page)
    expect(await tickOpacity(page, 'chat-notes'), 'hidden at rest').toBe(0)
    await card(page, 'chat-notes').hover()
    expect(await tickOpacity(page, 'chat-notes'), 'shown on hover').toBe(1)
    await page.mouse.move(0, 0)
    await card(page, 'chat-digest').focus()
    await page.keyboard.press('Shift+Tab')
    await page.keyboard.press('Tab')
    expect(await tickOpacity(page, 'chat-digest'), 'shown on keyboard focus').toBe(1)
    await tick(page, 'chat-plan').locator('input').check({ force: true })
    await page.mouse.move(0, 0)
    for (const key of ['chat-plan', 'chat-digest', 'chat-notes']) expect(await tickOpacity(page, key), `${key} while selecting`).toBe(1)
  })

  test('a tick never opens the peek, and a click on the card does', async ({ page }) => {
    await openTheBoard(page)
    await card(page, 'chat-plan').hover()
    await tick(page, 'chat-plan').locator('input').check()
    await expect(tick(page, 'chat-plan').getByRole('checkbox', { name: `Select ${TITLE}` })).toBeChecked()
    expect(page.url(), 'a tick opens no peek').not.toContain('peek=')
    await card(page, 'chat-notes').locator('[data-board-card-title]').click()
    await expect.poll(() => page.url()).toContain('peek=chat-notes')
  })

  test('a card dragged to another tag\'s column moves there', async ({ page }) => {
    const board = await openTheBoard(page)
    const home = page.locator('div').filter({ has: page.locator('text="Home"') }).filter({ has: card(page, 'chat-notes') }).last()
    await card(page, 'chat-digest').dragTo(home)
    await expect.poll(() => board.tagEdits, 'the drop moved the chat').toEqual([{ key: 'chat-digest', body: { remove: ['t-work'], add: ['t-home'] } }])
  })
})
