import { test, expect, type Page, type WebSocketRoute } from '@playwright/test'
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

/** The chat answers from here, and its image `delayMs` after it is asked for. Returns every
 *  address the image was asked for by. */
async function pinTheChat(page: Page, delayMs: number): Promise<string[]> {
  const body = JSON.stringify(detail())
  await page.route(`**/api/chat/sessions/${KEY}`, (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ status: 200, contentType: 'application/json', body })
      : route.fallback())
  return holdTheImages(page, `**/api/artifacts/${SLUG}/raw**`, delayMs)
}

/** Answers the images at *pattern* `delayMs` after each is asked for, as the gateway answers a
 *  signed address (kept out of every cache). Returns every address they were asked for by. */
async function holdTheImages(page: Page, pattern: string, delayMs: number): Promise<string[]> {
  const asked: string[] = []
  await page.route(pattern, async (route) => {
    asked.push(route.request().url())
    await new Promise((r) => setTimeout(r, delayMs))
    await route.fulfill({ status: 200, contentType: 'image/svg+xml', headers: { 'Cache-Control': 'private, no-store' }, body: IMAGE }).catch(() => {})
  })
  return asked
}

/** Where the page holds a pair, the gateway signs its images: every address one was asked for by
 *  carries its signature. Main's tree signs nothing, so there this checks nothing. */
async function expectAskedSigned(page: Page, asked: string[]): Promise<void> {
  if (!await page.evaluate(() => window.localStorage.getItem('pc_pair'))) return
  expect(asked.length, 'the image was never asked for').toBeGreaterThan(0)
  for (const url of asked) expect(url, 'the image was asked for by an address with no signature').toContain('pc_sig=')
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
      const asked = await pinTheChat(page, 1500)
      await gotoRoute(page, `chat/${KEY}`)
      await turnsDrawn(page)
      await expectAtEnd(page, 'once its turns are drawn')
      await imageArrived(page)
      await expectAtEnd(page, 'once its code block and image have arrived')
      await expectAskedSigned(page, asked)
    })

    test(`${width}px — after a reload the chat opens at its end again`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      const asked = await pinTheChat(page, 1500)
      await gotoRoute(page, `chat/${KEY}`)
      await turnsDrawn(page)
      await page.reload()
      await turnsDrawn(page)
      await expectAtEnd(page, 'after a reload')
      await imageArrived(page)
      await expectAtEnd(page, 'after a reload, once the image has arrived')
      await expectAskedSigned(page, asked)
    })

    test(`${width}px — a scroll up while the chat loads stays where the owner put it`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      const asked = await pinTheChat(page, 3000)
      const askedFor = page.waitForRequest((r) => r.url().includes(`/api/artifacts/${SLUG}/raw`))
      await page.goto(`/#/chat/${KEY}`)
      await turnsDrawn(page)
      // The scroll comes while the image is on its way: once it has been asked for (where the
      // gateway signs it, that is once its signature is back) and before it arrives.
      await askedFor
      const img = page.locator(`img[src*="${SLUG}"]`)
      await expect(img, 'the reply never drew its image').toHaveCount(1)
      expect(await img.evaluate((e: HTMLImageElement) => e.complete && e.naturalHeight > 0), 'the image had already arrived').toBe(false)
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
      await expectAskedSigned(page, asked)
    })
  }
})

// ── AN IMAGE WAITING FOR ITS SIGNATURE HAS THE BOX IT KEEPS ─────────────────────────────────────
//
// Where the gateway signs an image's address, the image waits for its signature before it is asked
// for. It is drawn all the same, in the box it keeps once signed, so nothing around it moves when
// the signature comes back: alone in a paragraph, mid-sentence, in a quote and in a table cell.
// Measured with the signature held, then again once it is back, while the image itself is held.

const BOX_KEY = 'chat-image-box-1790000002'
const BOX_SLUG = 'chat-image-box'
const PLACES = ['alone', 'inline', 'quote', 'cell'] as const
const SHAPES = [
  'The board, on a line of its own:',
  '',
  `![The launch board](/api/artifacts/${BOX_SLUG}-alone/raw)`,
  '',
  `The burn-down sits mid-sentence, here ![the burn-down](/api/artifacts/${BOX_SLUG}-inline/raw) and the sentence goes on.`,
  '',
  `> Quoted with its chart: ![the quoted chart](/api/artifacts/${BOX_SLUG}-quote/raw)`,
  '',
  '| Day | Chart |',
  '| --- | --- |',
  `| Monday | ![Monday's chart](/api/artifacts/${BOX_SLUG}-cell/raw) |`,
  '',
  'That is every chart.',
].join('\n')

interface Boxes { turn: number; images: { top: number; height: number; block: number }[] }

/** The last turn's height, and each of its images' place in it, its height and its block's height. */
async function imageBoxes(page: Page): Promise<Boxes> {
  return page.evaluate(() => {
    const turns = document.querySelectorAll('[data-transcript-turn]')
    const turn = turns[turns.length - 1]
    if (!turn) throw new Error('no turn')
    const t = turn.getBoundingClientRect()
    const images = [...turn.querySelectorAll('[data-image-pending], img[src*="/api/artifacts/"]')].map((img) => {
      const r = img.getBoundingClientRect()
      const block = img.closest('p, blockquote, td, li')
      return { top: r.top - t.top, height: r.height, block: block ? block.getBoundingClientRect().height : -1 }
    })
    return { turn: t.height, images }
  })
}

test.describe('an image waiting for its signature has the box it keeps', () => {
  for (const width of [1280, 390] as const) {
    test(`${width}px — alone, mid-sentence, quoted and in a table cell, nothing moves when its signature comes back`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      const body = JSON.stringify({
        key: BOX_KEY, title: 'Every chart of the week', run_state: idleRunState(BOX_KEY), queue: [],
        task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
        messages: [
          { role: 'user', content: 'Show me every chart.', ts: '2026-11-01T09:00:00+00:00' },
          { role: 'assistant', content: SHAPES, ts: '2026-11-01T09:00:20+00:00' },
        ],
      })
      await page.route(`**/api/chat/sessions/${BOX_KEY}`, (route) =>
        route.request().method() === 'GET'
          ? route.fulfill({ status: 200, contentType: 'application/json', body })
          : route.fallback())
      // The images are held past the end of the test, so neither read sees one arrive.
      const asked = await holdTheImages(page, `**/api/artifacts/${BOX_SLUG}-*/raw**`, 30_000)
      let release!: () => void
      const signatures = new Promise<void>((r) => { release = r })
      await page.route('**/api/auth/signed-urls', async (route) => { await signatures; await route.fallback() })

      await gotoRoute(page, `chat/${BOX_KEY}`)
      await expect(page.locator('[data-transcript-turn]'), 'the chat never showed its turns').toHaveCount(2)
      expect(await page.evaluate(() => window.localStorage.getItem('pc_pair')), 'the page holds no pair, so no image waits for its signature').toBeTruthy()
      await expect(page.locator('[data-image-pending]'), 'each image waits for its signature').toHaveCount(PLACES.length)
      await frames(page)
      const waiting = await imageBoxes(page)

      release()
      await expect(page.locator('img[src*="pc_sig="]'), 'each image took its signed address').toHaveCount(PLACES.length)
      await expect(page.locator('[data-image-pending]')).toHaveCount(0)
      await frames(page)
      const signed = await imageBoxes(page)
      const arrived = await page.locator('img[src*="pc_sig="]').evaluateAll((els) =>
        els.filter((e) => (e as HTMLImageElement).complete && (e as HTMLImageElement).naturalHeight > 0).length)
      expect(arrived, 'an image arrived before the second read, so it measures the image').toBe(0)

      const moved = JSON.stringify({ waiting, signed })
      expect(waiting.images, `not every waiting image was measured: ${moved}`).toHaveLength(PLACES.length)
      expect(signed.images, `not every signed image was measured: ${moved}`).toHaveLength(PLACES.length)
      // Place by place first: one place growing can hide another shrinking in the reply's height.
      PLACES.forEach((place, i) => {
        for (const k of ['top', 'height', 'block'] as const) {
          expect(Math.abs(signed.images[i][k] - waiting.images[i][k]), `the image ${place} moved (${k}) when its signature came back: ${moved}`).toBeLessThanOrEqual(0.5)
        }
      })
      expect(Math.abs(signed.turn - waiting.turn), `the reply's height changed when its signatures came back: ${moved}`).toBeLessThanOrEqual(0.5)
      // Each image was asked for once its signature was back, and by its signed address.
      await expect.poll(() => asked.length, { message: 'not every image was asked for once signed', timeout: 5_000 }).toBe(PLACES.length)
      await expectAskedSigned(page, asked)
    })
  }
})

// ── A REPLY'S IMAGE STAYS AS THE REPLY FINISHES ─────────────────────────────────────────────────
//
// When a reply's stream ends, its blocks are drawn again. An image already shown is drawn with the
// signature it holds, from the first render: it doesn't drop back to its box, it keeps its height,
// and it isn't asked for again. The turn is planted, and its socket is this spec's, so its frames
// are the ones sent here.

const LIVE_KEY = 'chat-image-live-1790000003'
const LIVE_SLUG = 'chat-image-live'
const LIVE_REPLY = LAST.replace(`/api/artifacts/${SLUG}/raw`, `/api/artifacts/${LIVE_SLUG}/raw`)

test.describe("a reply's image stays as the reply finishes", () => {
  test('1280px — the image is asked for once, and keeps its box as the reply finishes', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 })
    const started = new Date(Date.now() - 20_000).toISOString()
    let turnRuns = true
    const history = detail().messages.slice(0, -1)
    const sessionDetail = () => ({
      key: LIVE_KEY, title: 'The launch week, as it is written', queue: [], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
      messages: turnRuns ? history : [...history, { role: 'assistant', content: LIVE_REPLY, ts: new Date().toISOString() }],
      run_state: turnRuns
        ? { ...idleRunState(LIVE_KEY), phase: 'tool', line: 'Terminal git push', since: started, seq: 1, turn: { message_id: started, started_at: started } }
        : { ...idleRunState(LIVE_KEY), seq: 9, last_outcome: 'done' },
    })
    const sockets: WebSocketRoute[] = []
    await page.routeWebSocket('**/api/ws', (ws) => { sockets.push(ws) })
    const send = (type: string, data: Record<string, unknown>) => {
      for (const ws of sockets) ws.send(JSON.stringify({ type, data: { session: LIVE_KEY, ...data } }))
    }
    await page.route(`**/api/chat/sessions/${LIVE_KEY}`, (route) =>
      route.request().method() === 'GET'
        ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(sessionDetail()) })
        : route.fallback())
    const asked = await holdTheImages(page, `**/api/artifacts/${LIVE_SLUG}/raw**`, 200)

    await gotoRoute(page, `chat/${LIVE_KEY}`)
    await expect(page.locator('[data-turn-status]'), 'the planted turn runs').toBeVisible({ timeout: 15_000 })
    send('chat_chunk', { content: LIVE_REPLY })
    const img = page.locator(`img[src*="${LIVE_SLUG}"]`)
    await expect(img, 'the reply being written never drew its image').toHaveCount(1)
    await expect.poll(() => img.evaluate((e: HTMLImageElement) => e.complete && e.naturalHeight > 0),
      { message: 'the image of the reply being written never loaded', timeout: 15_000 }).toBe(true)
    await expectAtEnd(page, 'while it streams, once its image has arrived')

    const shownHeight = await img.evaluate((e) => e.getBoundingClientRect().height)
    // From here on, note any moment the reply shows its image without its source: back in its box
    // waiting for a signature, or not drawn at all.
    await page.evaluate(() => {
      const w = window as unknown as { __imageWaited: boolean; __imageBefore: Element | null }
      w.__imageWaited = false
      w.__imageBefore = document.querySelector('img[src*="/api/artifacts/"]')
      const seen = () => {
        if (!document.querySelector('[data-transcript-scroll] img[src*="/api/artifacts/"]')
          || document.querySelector('[data-transcript-scroll] [data-image-pending]')) w.__imageWaited = true
      }
      new MutationObserver(seen).observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['data-image-pending', 'src'] })
    })
    turnRuns = false
    send('work_phase', { ...idleRunState(LIVE_KEY), phase: 'done', since: new Date().toISOString(), seq: 3 })
    send('chat_done', { content: LIVE_REPLY })
    await expect(page.locator('[data-turn-status]'), 'the turn ended, so its line goes').toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeVisible()
    // A count that stays at one passes the moment it is read, so the end is given time to land.
    await page.waitForTimeout(1500)
    await expect(img, 'the ended reply draws its image once').toHaveCount(1)
    const end = await page.evaluate(() => {
      const w = window as unknown as { __imageWaited: boolean; __imageBefore: Element | null }
      return { waited: w.__imageWaited, drawnAgain: w.__imageBefore !== document.querySelector('img[src*="/api/artifacts/"]') }
    })
    expect(end.drawnAgain, `the stream's end didn't draw the image again, so this checks nothing: ${JSON.stringify(end)}`).toBe(true)
    expect(end.waited, `the image lost its source as the stream ended: ${JSON.stringify(end)}`).toBe(false)
    expect(await img.evaluate((e: HTMLImageElement) => e.complete && e.naturalHeight > 0), 'the image dropped back to its box').toBe(true)
    const keptHeight = await img.evaluate((e) => e.getBoundingClientRect().height)
    expect(Math.abs(keptHeight - shownHeight), `the image's height changed as the stream ended: ${shownHeight} to ${keptHeight}`).toBeLessThanOrEqual(0.5)
    expect(asked, `the image was asked for again as the stream ended: ${JSON.stringify(asked)}`).toHaveLength(1)
    await expectAskedSigned(page, asked)
  })
})
