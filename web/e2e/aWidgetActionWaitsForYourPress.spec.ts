import { test, expect, type FrameLocator, type Page, type Route, type WebSocketRoute } from '@playwright/test'
import { gotoRoute } from './helpers'
import { idleRunState } from '../src/lib/workPhase'

// ── A WIDGET'S ACTION WAITS FOR THE OWNER'S PRESS ───────────────────────────────────────────────
//
// In a real browser, where a frame's own script can post to the parent and a click in a frame is a
// real click: an action a widget raises never sends a turn by itself. In a chat it fills an idle
// composer when the owner clicked or tapped into its frame, as best the page can tell, is offered
// as a chip otherwise, and only the owner's Send sends it; a post the widget's script makes as it
// loads, with focus elsewhere, puts nothing in front of the owner; on a dashboard tile the shell
// asks first, with focus on "Don't send", and answers only a press begun after a quiet moment. The
// chat, the tiles and their artifacts are answered here, and every send the page makes is recorded;
// the first chat leg, the two Space legs and the typing leg are its controls, showing the recorder
// sees a send.
//
// A hand moves the pointer across the page to what it clicks; Playwright's own click puts it there
// in one jump. The clicks in frames here move it in steps, as a hand does (`handClick`), from the
// page's own scroll of the frame into view. The page's console is printed with each leg, so a leg
// that sees nothing says which check dropped the frame's post.
//
// Playwright gives the page a user activation for every call it makes into it, so no leg here can
// show the activation fence (fence 4): the jsdom pins carry it.

const KEY = 'widget-action-waits-1790000002'
/** Where a widget's own controls sit: below the toolbar a chat's widget shows over its top edge. */
const BELOW_THE_TOOLBAR = 'style="display:block;margin-top:56px"'
/** Home's Attention view, where the owner's pinned tiles and its "Start something" field are. */
const HOME = 'dashboard?view=attention'
/** Every send the page makes, `/api/chat?ws=1` included. */
const SEND = /\/api\/chat(\?.*)?$/

/** A widget with a button the owner can click, and a script that tries to post on its own. */
function widget(onLoad: boolean, title = 'Sales', action = 'refresh'): string {
  const script = onLoad
    ? `<script>parent.postMessage({ type: 'widget-action', action: 'on_load', payload: { range: '30d' } }, '*')</script>`
    : ''
  return `<widget title="${title}"><button ${BELOW_THE_TOOLBAR} data-action="${action}" data-payload='{"range":"30d"}'>Refresh</button>${script}</widget>`
}

/** A widget whose script takes focus and posts, over and over. */
const LOOPING = (title: string, extra = '') =>
  `<widget title="${title}"><div ${extra}><button ${BELOW_THE_TOOLBAR} data-action="refresh">Refresh</button></div><script>setInterval(() => { window.focus(); document.body.focus(); parent.postMessage({ type: "widget-action", action: "steal" }, "*") }, 100)</script></widget>`

function detail(content: string, running = false) {
  return {
    key: KEY, title: 'Sales this month', queue: [],
    run_state: running ? { ...idleRunState(KEY), phase: 'thinking', line: 'Thinking', since: STARTED, seq: 1, turn: { message_id: STARTED, started_at: STARTED } } : idleRunState(KEY),
    task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
    messages: [
      { role: 'user', content: 'Show me the sales view.', ts: '2026-11-01T09:00:00+00:00' },
      ...(content ? [{ role: 'assistant', content: `Here it is.\n\n${content}`, ts: '2026-11-01T09:00:20+00:00' }] : []),
    ],
  }
}
const STARTED = new Date(Date.now() - 5_000).toISOString()

/** Record every send the page makes, refused, in *sends*. */
async function recordSends(page: Page): Promise<string[]> {
  const sends: string[] = []
  await page.route(SEND, (route: Route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    sends.push(String(route.request().postDataJSON()?.message ?? ''))
    return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'e2e: no send' }) })
  })
  return sends
}

/** The chat answers from here with *content* as its widget message, and every send is kept. */
async function pinTheChat(page: Page, content: string | (() => unknown)): Promise<string[]> {
  await page.route(`**/api/chat/sessions/${KEY}`, (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(typeof content === 'string' ? detail(content) : content()) })
      : route.fallback())
  return recordSends(page)
}

/** The dashboard shows one pinned tile per artifact content in *contents*, in order. */
async function pinTheTiles(page: Page, ...contents: string[]): Promise<void> {
  const slug = (i: number) => (i === 0 ? 'sales-view' : `view-${i}`)
  await page.route('**/api/dashboard/views', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ views: [{ id: 'overview', name: 'Overview', tiles: contents.map((_, i) => ({ ref: `artifact:${slug(i)}`, order: i, added_by: 'user' })) }] }),
      })
      : route.fallback())
  for (const [i, content] of contents.entries()) {
    await page.route(`**/api/artifacts/${slug(i)}`, (route) =>
      route.request().method() === 'GET'
        ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ slug: slug(i), name: i === 0 ? 'Sales' : `View ${i}`, kind: 'widget', content }) })
        : route.fallback())
  }
  // A yes opens a new chat: it is answered here too, so nothing is made in the gateway.
  await page.route('**/api/chat/sessions', (route) =>
    route.request().method() === 'POST'
      ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ key: 'widget-yes-1790000003', title: 'New chat' }) })
      : route.fallback())
}

const composer = (page: Page) => page.locator('[data-composer-region] .cm-content').first()
const ask = (page: Page) => page.getByRole('dialog', { name: /A widget titled “Sales” asks to send this/ })
const anyAsk = (page: Page) => page.getByRole('dialog', { name: /asks to send this/ })
const tileFrame = (page: Page, i = 0) => page.frameLocator('[data-testid="pinned-tiles"] iframe').nth(i)

/** Where *selector* sits in *frame*, in the page's viewport: the page scrolls the frame into view
 *  itself, since a frame's own scroll may not reach its parent, and the frame is
 *  checked to be in view first. */
async function inView(frame: FrameLocator, selector: string): Promise<{ x: number; y: number }> {
  const owner = frame.owner()
  await owner.evaluate((el) => el.scrollIntoView({ block: 'center' }))
  await expect(owner).toBeInViewport()
  const box = await frame.locator(selector).boundingBox()
  if (!box) throw new Error('nothing there to press')
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 }
}

/** A hand's click on *selector* in *frame*: the pointer crosses the page to it in steps, then
 *  presses. */
async function handClick(page: Page, frame: FrameLocator, selector: string): Promise<void> {
  const at = await inView(frame, selector)
  await page.mouse.move(at.x, at.y, { steps: 12 })
  await page.mouse.down()
  await page.mouse.up()
}

/** A finger's tap on *selector* in *frame*. */
async function handTap(page: Page, frame: FrameLocator, selector: string): Promise<void> {
  const at = await inView(frame, selector)
  await page.touchscreen.tap(at.x, at.y)
}

const chatFrame = (page: Page, title: string) => page.frameLocator(`iframe[title="${title}"]`)

/** A looping frame's own posts, in a chat: offered as a chip naming *frame*, or dropped at the
 *  focus check (its console line), and never in the composer. */
async function offeredOrDropped(page: Page, browserName: string, frame: string): Promise<void> {
  if (browserName === 'chromium') {
    await expect(page.getByTestId('widget-suggestion')).toContainText(frame, { timeout: 10_000 })
  } else {
    await page.waitForTimeout(2_000)
    await expect(page.getByTestId('widget-suggestion')).toHaveCount(0)
    expect(consoleLines.get(page)?.some((l) => l.includes('its frame does not hold focus'))).toBe(true)
  }
  await expect(composer(page)).not.toContainText('[UI] steal')
}

// The page's console, printed with each leg: a drop's debug line names the check that fired.
const consoleLines = new WeakMap<Page, string[]>()
test.beforeEach(({ page }) => {
  const lines: string[] = []
  consoleLines.set(page, lines)
  page.on('console', (m) => { if (/widget/i.test(m.text())) lines.push(`[${m.type()}] ${m.text()}`) })
})
test.afterEach(({ page }, info) => {
  const lines = consoleLines.get(page) ?? []
  if (lines.length) console.log(`page console, ${info.title}:\n  ${lines.join('\n  ')}`)
})

/** The page is still Home: no answer of the ask's opened a chat. Checked again after a wait. */
async function stillHome(page: Page, sends: string[]): Promise<void> {
  await expect(page).toHaveURL(/#\/dashboard/)
  await page.waitForTimeout(1_500)
  await expect(page).toHaveURL(/#\/dashboard/)
  expect(sends).toEqual([])
}

test.describe('a widget action waits for the owner, in a chat', () => {
  test('a real click on a widget button fills the composer and sends nothing; the owner\'s Enter after a press in it sends it', async ({ page }) => {
    const sends = await pinTheChat(page, widget(false))
    await gotoRoute(page, `chat/${KEY}`)
    await handClick(page, chatFrame(page, 'Sales'), 'button[data-action="refresh"]')
    await expect(composer(page)).toContainText('[UI] refresh: {"range":"30d"}')
    await expect(page.getByTestId('widget-draft-from')).toContainText('From “Sales”')
    // A reflexive Enter right after the fill, in the composer reached with no press in it, is not
    // a send, and the notice line says why.
    await composer(page).evaluate((el) => (el as HTMLElement).focus())
    await page.keyboard.press('Enter')
    await expect(page.getByText('Read the message the widget put here first').first()).toBeVisible()
    await page.waitForTimeout(500)
    expect(sends).toEqual([])
    // Positive control: the owner presses in the composer, waits, and sends. The recorder sees it.
    await composer(page).click()
    await page.waitForTimeout(1_100)
    await page.keyboard.press('Enter')
    await expect.poll(() => sends).toEqual(['[UI] refresh: {"range":"30d"}'])
  })

  test('a post the widget makes as it loads leaves the composer empty, with nothing offered', async ({ page }) => {
    const sends = await pinTheChat(page, widget(true))
    await gotoRoute(page, `chat/${KEY}`)
    await expect(page.locator('iframe[title="Sales"]')).toBeVisible()
    await page.waitForTimeout(1_000)
    await expect(composer(page)).not.toContainText('on_load')
    await expect(page.getByTestId('widget-suggestion')).toHaveCount(0)
    expect(sends).toEqual([])
  })

  test('a frame that takes focus itself while the owner types is offered as a chip where it holds focus, and dropped where it doesn\'t', async ({ page, browserName }) => {
    const sends = await pinTheChat(page, LOOPING('Sales'))
    await gotoRoute(page, `chat/${KEY}`)
    await composer(page).click()
    await page.keyboard.type('my own words', { delay: 40 })
    await offeredOrDropped(page, browserName, 'Sales')
    await expect(page.getByTestId('widget-draft-from')).toHaveCount(0)
    expect(sends).toEqual([])
  })

  test('a real click whose mousedown the widget cancels still reaches the composer', async ({ page }) => {
    // The widget cancels the mousedown, so the browser moves no focus into its frame; the host's own
    // press script focuses it, and the action counts.
    const sends = await pinTheChat(page,
      `<widget title="Sales"><button ${BELOW_THE_TOOLBAR} data-action="refresh" onmousedown="event.preventDefault()">Refresh</button></widget>`)
    await gotoRoute(page, `chat/${KEY}`)
    await handClick(page, chatFrame(page, 'Sales'), 'button[data-action="refresh"]')
    await expect(composer(page)).toContainText('[UI] refresh')
    expect(sends).toEqual([])
  })

  test('a click in one widget and then in another offers the second one\'s message as a chip', async ({ page }) => {
    const sends = await pinTheChat(page, `${widget(false)}\n\n${widget(false, 'Notes', 'note')}`)
    await gotoRoute(page, `chat/${KEY}`)
    await handClick(page, chatFrame(page, 'Sales'), 'button[data-action="refresh"]')
    await expect(composer(page)).toContainText('[UI] refresh')
    await handClick(page, chatFrame(page, 'Notes'), 'button[data-action="note"]')
    await expect(page.getByTestId('widget-suggestion')).toContainText('Notes')
    await expect(composer(page)).not.toContainText('[UI] note')
    await page.waitForTimeout(1_100)
    expect(sends).toEqual([])
  })

  test('a widget that streams in under the resting pointer and takes focus is offered as a chip where it holds focus, and dropped where it doesn\'t', async ({ page, browserName }) => {
    // The owner's message is sent and its turn runs; the pointer rests over the transcript, where
    // the answer's widget then appears and its script takes focus and posts.
    const sockets: WebSocketRoute[] = []
    await page.routeWebSocket('**/api/ws', (ws) => { sockets.push(ws) })
    const answer = LOOPING('Loop', 'style="height:900px"')
    let done = false
    const sends = await pinTheChat(page, () => (done ? detail(answer) : detail('', true)))
    await gotoRoute(page, `chat/${KEY}`)
    const transcript = page.locator('[data-transcript-scroll]')
    const box = await transcript.boundingBox()
    if (!box) throw new Error('no transcript')
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 6 })
    await page.waitForTimeout(2_500)
    const frame = (type: string, data: Record<string, unknown>) => {
      for (const ws of sockets) ws.send(JSON.stringify({ type, data: { session: KEY, ...data } }))
    }
    // The block opens, and closes a second later: the frame appears at its close.
    const half = answer.indexOf('<button')
    frame('chat_chunk', { content: `Here it is.\n\n${answer.slice(0, half)}` })
    await page.waitForTimeout(1_000)
    frame('chat_chunk', { content: answer.slice(half) })
    done = true
    frame('chat_done', { outcome: 'complete' })
    const loop = page.locator('iframe[title="Loop"]')
    await expect(loop).toBeVisible()
    await expect.poll(() => loop.evaluate((el) => el.matches(':hover')), { message: 'premise: the frame came to rest under the pointer' }).toBe(true)
    await offeredOrDropped(page, browserName, 'Loop')
    expect(sends).toEqual([])
  })
})

test.describe('a widget action waits for the owner, on a dashboard tile', () => {
  test('a tile\'s click opens the shell\'s ask, with focus on "Don\'t send"; a reflexive Enter is a no', async ({ page }) => {
    await pinTheTiles(page, `<button ${BELOW_THE_TOOLBAR} data-action="refresh">Refresh</button>`)
    const sends = await recordSends(page)
    await gotoRoute(page, HOME)
    await handClick(page, tileFrame(page), 'button[data-action="refresh"]')
    await expect(ask(page)).toBeVisible()
    await expect(page.getByRole('button', { name: 'Don’t send' })).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(ask(page)).toBeHidden()
    await stillHome(page, sends)
  })

  test('a click in one tile\'s body and then on another tile\'s button opens the ask', async ({ page }) => {
    await pinTheTiles(page,
      `<p ${BELOW_THE_TOOLBAR} id="body">The week so far</p>`,
      `<button ${BELOW_THE_TOOLBAR} data-action="refresh">Refresh</button>`)
    const sends = await recordSends(page)
    await gotoRoute(page, HOME)
    await handClick(page, tileFrame(page, 0), '#body')
    await handClick(page, tileFrame(page, 1), 'button[data-action="refresh"]')
    await expect(anyAsk(page)).toBeVisible()
    await expect(page.getByTestId('widget-action-text')).toHaveText('[UI] refresh')
    await page.getByRole('button', { name: 'Don’t send' }).click()
    await stillHome(page, sends)
  })

  for (const when of ['during its entrance', 'once it is shown'] as const) {
    test(`a Space held on "Send in a new chat" from ${when === 'during its entrance' ? 'the ask\'s entrance' : 'when the ask is shown'} is not a yes`, async ({ page }) => {
      await pinTheTiles(page, `<button ${BELOW_THE_TOOLBAR} data-action="refresh">Refresh</button>`)
      const sends = await recordSends(page)
      await gotoRoute(page, HOME)
      await handClick(page, tileFrame(page), 'button[data-action="refresh"]')
      await expect(ask(page)).toBeVisible()
      if (when === 'once it is shown') await page.waitForTimeout(800)
      await page.keyboard.press('Tab')
      await expect(page.getByRole('button', { name: 'Send in a new chat' })).toBeFocused()
      await page.keyboard.down('Space')
      await page.waitForTimeout(2_000)
      await page.keyboard.up('Space')
      await page.waitForTimeout(300)
      await expect(ask(page)).toBeVisible()
      expect(sends).toEqual([])
      // Positive control: a pointer click once the ask has been quiet a moment answers yes.
      await page.waitForTimeout(1_200)
      await page.getByRole('button', { name: 'Send in a new chat' }).click()
      await expect.poll(() => sends).toEqual(['[UI] refresh'])
    })
  }

  test('typing that runs on into the ask, then Tab and Enter, is not a yes; a click after a quiet moment is', async ({ page }) => {
    // The tile's own script posts on each key in its own input, so the ask opens mid-word and the
    // owner's typing runs on into it.
    await pinTheTiles(page,
      `<input ${BELOW_THE_TOOLBAR} aria-label="Note" id="n"><script>document.getElementById("n").addEventListener("keydown", () => parent.postMessage({ type: "widget-action", action: "note" }, "*"))</script>`)
    const sends = await recordSends(page)
    await gotoRoute(page, HOME)
    await handClick(page, tileFrame(page), '#n')
    // Letters only: a Space that lands on the ask's "Don't send" is that button's own press, a no.
    await page.keyboard.type('rememberthemilk', { delay: 60 })
    await expect(ask(page).or(anyAsk(page))).toBeVisible()
    // The owner's typing runs on past the ask's quiet moment, then a Tab lands
    // on "Send in a new chat" and the next press goes to it at once.
    await page.keyboard.type('andeggsandbreadandbutter', { delay: 90 })
    await page.keyboard.press('Tab')
    await expect(page.getByRole('button', { name: 'Send in a new chat' })).toBeFocused()
    await page.keyboard.press('Enter')
    await page.keyboard.press('Space')
    await page.waitForTimeout(300)
    expect(sends).toEqual([])
    await expect(anyAsk(page)).toBeVisible()
    await expect(page).toHaveURL(/#\/dashboard/)
    // Positive control: a pointer click once the ask has been quiet a moment answers yes, and the
    // new chat it opens sends the frame's text.
    await page.waitForTimeout(1_200)
    await page.getByRole('button', { name: 'Send in a new chat' }).click()
    await expect.poll(() => sends).toEqual(['[UI] note'])
  })

  test('a tile that takes focus and posts in a loop asks one question at a time, and none after a "Don\'t send"', async ({ page, browserName }) => {
    await pinTheTiles(page,
      `<button ${BELOW_THE_TOOLBAR} data-action="refresh">Refresh</button><script>setInterval(() => { window.focus(); document.body.focus(); parent.postMessage({ type: "widget-action", action: "steal" }, "*") }, 50)</script>`)
    const sends = await recordSends(page)
    await gotoRoute(page, HOME)
    // No press of the owner's: Playwright's own calls into the page give it the activation a
    // frame's post needs (the header), so the loop's posts reach the shell.
    /** The most asks open at once over *ms*, sampled in the page every 50 ms. */
    const most = (ms: number) => page.evaluate(async (ms) => {
      let n = 0
      for (const end = Date.now() + ms; Date.now() < end; await new Promise((r) => setTimeout(r, 50))) {
        n = Math.max(n, [...document.querySelectorAll('[role="dialog"]')].filter((d) => d.textContent?.includes('asks to send this')).length)
      }
      return n
    }, ms)
    // Its posts are the frame's own: the shell asks about them, one question at a time.
    const asked = await most(1_500)
    expect(asked).toBeLessThanOrEqual(1)
    // Where a frame can take focus itself, its post asks; otherwise it is dropped at the focus check.
    if (browserName === 'chromium') expect(asked, 'premise: the loop\'s own post asks').toBe(1)
    if (asked) {
      await page.getByRole('button', { name: 'Don’t send' }).click()
      await expect(anyAsk(page)).toHaveCount(0)
      // The no holds for that frame: no ask opens again over a few seconds of its loop.
      expect(await most(3_000)).toBe(0)
    }
    await expect(page).toHaveURL(/#\/dashboard/)
    expect(sends).toEqual([])
  })
})

test.describe('a widget action waits for the owner, from a tap', () => {
  test.use({ hasTouch: true })

  test('a tap on a widget button in a chat fills the composer and sends nothing', async ({ page }) => {
    const sends = await pinTheChat(page, widget(false))
    await gotoRoute(page, `chat/${KEY}`)
    await expect(page.locator('iframe[title="Sales"]')).toBeVisible()
    await page.waitForTimeout(800)
    await handTap(page, chatFrame(page, 'Sales'), 'button[data-action="refresh"]')
    await expect(composer(page)).toContainText('[UI] refresh: {"range":"30d"}')
    await page.waitForTimeout(1_100)
    expect(sends).toEqual([])
  })

  test('a tap on a tile\'s button opens the shell\'s ask, with focus on "Don\'t send"', async ({ page }) => {
    await pinTheTiles(page, `<button ${BELOW_THE_TOOLBAR} data-action="refresh">Refresh</button>`)
    const sends = await recordSends(page)
    await gotoRoute(page, HOME)
    await handTap(page, tileFrame(page), 'button[data-action="refresh"]')
    await expect(ask(page)).toBeVisible()
    await expect(page.getByRole('button', { name: 'Don’t send' })).toBeFocused()
    await page.getByRole('button', { name: 'Don’t send' }).click()
    await stillHome(page, sends)
  })
})
