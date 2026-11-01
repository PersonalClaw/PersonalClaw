import { test, expect, type Page } from '@playwright/test'
import { gotoRoute } from './helpers'
import { idleRunState } from '../src/lib/workPhase'

// ── A CHAT OPENS AT ITS LATEST MESSAGE (#5866) ──────────────────────────────────────────────────
//
// Opening a chat, from the sidebar, a link or a reload, lands the transcript at the end of its
// conversation, and it stays there while late layout settles (a code block's highlighting, an image
// that loads after the text), until the owner moves the view. A scroll up while it loads is the
// owner's, and nothing snaps it back. Measured where it is drawn, at the goldens' 1280 and the
// phone contract's 390, on a chat far longer than the window whose last reply ends in a code block
// and an image that arrives late.

const KEY = 'chat-opens-at-latest-1790000001'
const SLUG = 'chat-opens-late-image'
/** An image with a size of its own, so its arrival moves the end of the conversation down. */
const IMAGE = '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600"><rect width="800" height="600" fill="#c9d4e0"/></svg>'

const PARAGRAPH = 'The plan for this part of the week, with who owns each piece, what it waits on, and the one risk that would move it, written out so the thread runs well past the window.'
const LAST = [
  'Here is the release step, and the screenshot of the board as it stands.',
  '',
  '```bash',
  'git switch -c release/launch-week',
  'git push origin release/launch-week',
  'npm run build && npm run test',
  '```',
  '',
  `![The launch board](/api/artifacts/${SLUG}/raw)`,
  '',
  'That is the end of the plan.',
].join('\n')

function detail() {
  const messages: { role: string; content: string; ts: string }[] = []
  for (let i = 0; i < 12; i++) {
    messages.push({ role: 'user', content: `Plan day ${i + 1} of the launch.`, ts: `2026-11-01T09:${String(i * 2).padStart(2, '0')}:00+00:00` })
    messages.push({ role: 'assistant', content: `${PARAGRAPH}\n\n${PARAGRAPH}\n\n${PARAGRAPH}`, ts: `2026-11-01T09:${String(i * 2).padStart(2, '0')}:20+00:00` })
  }
  messages.push({ role: 'user', content: 'And the release step?', ts: '2026-11-01T09:30:00+00:00' })
  messages.push({ role: 'assistant', content: LAST, ts: '2026-11-01T09:30:20+00:00' })
  return {
    key: KEY, title: 'The launch week, day by day', messages, run_state: idleRunState(KEY), queue: [],
    task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
  }
}

/** The chat answers from here, and its image `delayMs` after it is asked for. */
async function pinTheChat(page: Page, delayMs: number): Promise<void> {
  const body = JSON.stringify(detail())
  await page.route(`**/api/chat/sessions/${KEY}`, (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ status: 200, contentType: 'application/json', body })
      : route.fallback())
  await page.route(`**/api/artifacts/${SLUG}/raw**`, async (route) => {
    await new Promise((r) => setTimeout(r, delayMs))
    await route.fulfill({ status: 200, contentType: 'image/svg+xml', body: IMAGE })
  })
}

interface Scroll { top: number; gap: number; overflow: number }

/** The transcript's scroller: how far down it is, how far from its end, and by how much the
 *  conversation is taller than the window. */
async function readScroll(page: Page): Promise<Scroll> {
  return page.evaluate(() => {
    const el = document.querySelector<HTMLElement>('[data-transcript-scroll]')
    if (!el) throw new Error('no transcript scroller')
    return {
      top: Math.round(el.scrollTop),
      gap: Math.round(el.scrollHeight - el.scrollTop - el.clientHeight),
      overflow: Math.round(el.scrollHeight - el.clientHeight),
    }
  })
}

/** The late image has arrived and been laid out, and two frames have passed for what follows it. */
async function imageArrived(page: Page): Promise<void> {
  const img = page.locator(`img[src*="${SLUG}"]`)
  await expect(img, 'the reply never drew its image').toHaveCount(1)
  await expect.poll(() => img.evaluate((e: HTMLImageElement) => e.complete && e.naturalHeight > 0),
    { message: 'the late image never loaded', timeout: 15_000 }).toBe(true)
  await frames(page)
}

async function turnsDrawn(page: Page): Promise<void> {
  await expect(page.locator('[data-transcript-turn]'), 'the chat never showed its turns').toHaveCount(detail().messages.length)
}

/** Two frames, for a layout the last change asked for. */
async function frames(page: Page): Promise<void> {
  await page.evaluate(() => new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => r()))))
}

/** The view is at the end of the conversation, within 2 px. The image is lazy, so a view left
 *  at the top never even asks for it: this is read before the image is waited for. */
async function expectAtEnd(page: Page, when: string): Promise<void> {
  await frames(page)
  const s = await readScroll(page)
  expect(s.overflow, `the chat is not longer than the window: ${JSON.stringify(s)}`).toBeGreaterThan(900)
  expect(s.gap, `${when}, the view is not at the end of the conversation: ${JSON.stringify(s)}`).toBeLessThanOrEqual(2)
}

test.describe('a chat opens at its latest message', () => {
  for (const width of [1280, 390] as const) {
    test(`${width}px — a chat longer than the window opens at its end, and stays there as its code block and image arrive`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      await pinTheChat(page, 1500)
      await gotoRoute(page, `chat/${KEY}`)
      await turnsDrawn(page)
      await expectAtEnd(page, 'once its turns are drawn')
      await imageArrived(page)
      await expectAtEnd(page, 'once its code block and image have arrived')
    })

    test(`${width}px — after a reload the chat opens at its end again`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      await pinTheChat(page, 1500)
      await gotoRoute(page, `chat/${KEY}`)
      await turnsDrawn(page)
      await page.reload()
      await turnsDrawn(page)
      await expectAtEnd(page, 'after a reload')
      await imageArrived(page)
      await expectAtEnd(page, 'after a reload, once the image has arrived')
    })

    test(`${width}px — a scroll up while the chat loads stays where the owner put it`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      await pinTheChat(page, 3000)
      await page.goto(`/#/chat/${KEY}`)
      await turnsDrawn(page)
      const box = await page.locator('[data-transcript-scroll]').boundingBox()
      if (!box) throw new Error('no transcript scroller box')
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
      await page.mouse.wheel(0, -1200)
      await frames(page)
      const before = await readScroll(page)
      await imageArrived(page)
      const after = await readScroll(page)
      expect(Math.abs(after.top - before.top), `the view moved from where the owner put it: ${JSON.stringify({ before, after })}`).toBeLessThanOrEqual(2)
      expect(after.gap, `the view snapped back to the end: ${JSON.stringify(after)}`).toBeGreaterThan(200)
    })
  }
})
