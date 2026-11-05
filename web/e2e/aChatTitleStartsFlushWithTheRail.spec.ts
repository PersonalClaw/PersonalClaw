import { test, expect, type Page } from '@playwright/test'
import { gotoRoute } from './helpers'

// ── A CHAT'S TITLE IN THE SIDEBAR STARTS FLUSH WITH THE RAIL'S OTHER ROWS ────────────────────────
//
// The rail is narrow and a chat's title needs a few words to make sense, so none of its width goes
// to an indent: a row's title starts at the left edge New chat's icon and the group headers'
// chevrons start at, under a group header or not, with a state dot or without (the dot sits at the
// row's right end). jsdom lays nothing out, so `aChatTitleStartsFlushWithTheRail.test.tsx` reads the
// padding classes; this measures the edge in a real browser, at the rail's narrowest width and at
// its default one. The chats are route stubs, so the layout is the only variable.

const LONG = 'Plan the venue walkthrough for the November meetup and confirm the AV list'

const SESSIONS = [
  { key: 'e2e-flush-long', title: LONG, messages: 2, run_state: 'idle' },
  { key: 'e2e-flush-calm', title: 'Kitchen tiles', messages: 2, run_state: 'idle' },
].map((s, i) => ({ ...s, last_activity_ts: new Date(Date.UTC(2026, 9, 1) - i * 60_000).toISOString() }))

async function stubThreads(page: Page): Promise<void> {
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

interface Edges {
  /** New chat's icon: the rail's own left edge for its rows' content. */
  newChat: number
  /** The group header's chevron. */
  header: number
  /** Each row's title text, by key. */
  titles: Record<string, number>
}

async function readEdges(page: Page): Promise<Edges> {
  return page.evaluate(() => {
    const group = document.querySelector('nav[data-tour="rail"] [role="group"][aria-label="Chat history"]')
    if (!group) throw new Error('no Chat history group')
    const newChat = [...group.querySelectorAll('button')].find((b) => b.textContent?.trim() === 'New chat')
    // The open group's header: it names the list it controls. The search row's Group by button
    // opens a menu (`aria-expanded` too) and comes first, so `aria-expanded` alone finds it.
    const header = group.querySelector('button[aria-expanded="true"][aria-controls]')
    if (!newChat || !header) throw new Error('no New chat button or group header')
    const left = (el: Element | null | undefined) => {
      if (!el) throw new Error('missing element')
      return el.getBoundingClientRect().left
    }
    const titles: Record<string, number> = {}
    for (const row of group.querySelectorAll('[data-thread-key]')) {
      // The title is the row's own text span, the one that truncates.
      const span = [...row.querySelectorAll('span')].find((s) => s.classList.contains('truncate'))
      titles[row.getAttribute('data-thread-key') ?? ''] = left(span)
    }
    return { newChat: left(newChat.querySelector('svg')), header: left(header.querySelector('svg')), titles }
  })
}

for (const narrowest of [false, true]) {
  test.describe(`the chat list at the sidebar's ${narrowest ? 'narrowest' : 'default'} width`, () => {
    test.use({ viewport: { width: 1280, height: 800 } })

    test("each title starts at the rail's own left edge", async ({ page }) => {
      await stubThreads(page)
      await gotoRoute(page, 'dashboard')
      await expect(page.getByRole('button', { name: new RegExp(LONG.slice(0, 20)) })).toBeAttached()
      if (narrowest) {
        // The resize handle's own keyboard steps, down to the narrowest the sidebar goes.
        await page.getByRole('separator', { name: /Resize navigation/ }).focus()
        await page.keyboard.press('ArrowLeft', { delay: 10 })
        for (let i = 0; i < 20; i++) await page.keyboard.press('ArrowLeft')
      }

      // The search field's placeholder reads whole at either width: its text box holds "Search".
      const fits = await page.getByRole('searchbox', { name: 'Search chats' }).evaluate((el) => {
        const input = el as HTMLInputElement
        const cs = getComputedStyle(input)
        const ctx = document.createElement('canvas').getContext('2d')!
        ctx.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`
        const room = input.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight)
        return { room, text: ctx.measureText(input.placeholder).width }
      })
      expect(fits.text, `"Search" (${fits.text.toFixed(1)} px) fits the field's ${fits.room.toFixed(1)} px`).toBeLessThanOrEqual(fits.room)

      const edges = await readEdges(page)
      expect(Math.abs(edges.header - edges.newChat), 'the group header starts at the rail edge').toBeLessThanOrEqual(1)
      expect(Object.keys(edges.titles).sort()).toEqual(['e2e-flush-calm', 'e2e-flush-long'])
      for (const [key, x] of Object.entries(edges.titles)) {
        expect(Math.abs(x - edges.newChat), `${key}'s title is flush with the rail`).toBeLessThanOrEqual(1)
      }
    })
  })
}
