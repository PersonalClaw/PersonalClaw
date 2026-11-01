import { test, expect, type Page, type WebSocketRoute } from '@playwright/test'
import { gotoRoute } from './helpers'
import { idleRunState } from '../src/lib/workPhase'

// ── A running turn's activity line sits bare at the end of the conversation ────────────────────
//
// What a running turn is doing now is one bare line, with no border, background or pill, the last
// thing in the turn column under the latest message, and it scrolls with the thread (#5867,
// `ui/TurnStatusRow.tsx`). Stopping stays in reach whatever the scroll: the composer's send button
// is Stop this turn while the turn runs, a Stop sits beside Steer or Queue once a draft is typed,
// and Esc stops it too. jsdom lays nothing out, so the component tests cannot see where it sits;
// this spec asks a real browser, on a planted turn of 120 rows: the line sits under the turn's last
// row and inside the transcript, it has no box of its own, it scrolls away with the transcript
// scrolled to its top, while the composer's Stop stays in view, its orb is the transcript's one
// live orb, Stop and Esc stop the turn, and the line goes when the turn ends.
//
// The chat is planted rather than driven: its read is a route stub that says a turn runs, and the
// socket is the spec's own, so the frames that move the turn are the ones sent here and nothing
// else. Its turn ends only when this spec says so.

const KEY = 'chat-turn-status-1790000000'
const PROMPT = 'Run the whole suite and list every check it makes.'
const STARTED = new Date(Date.now() - 40_000).toISOString()
/** The turn's answer so far: 120 rows. */
const ROWS = Array.from({ length: 120 }, (_, i) => `${i + 1}. Check ${i + 1} of the suite passed.`)
const TOOL_LINE = 'Terminal npm test'

const running = (seq: number, phase: string, line: string) => ({
  ...idleRunState(KEY), phase, line, since: STARTED, seq, turn: { message_id: STARTED, started_at: STARTED },
})

const detail = (turnRuns: boolean) => ({
  key: KEY,
  title: 'The whole suite, check by check',
  messages: [
    { role: 'user', content: PROMPT, ts: STARTED },
    ...(turnRuns ? [] : [{ role: 'assistant', content: ROWS.join('\n'), ts: new Date().toISOString() }]),
  ],
  run_state: turnRuns ? running(1, 'tool', TOOL_LINE) : { ...idleRunState(KEY), seq: 9, last_outcome: 'done' },
  queue: [],
  task_mode: 'agent',
  approval: 'normal',
  memory_mode: 'persistent',
})

interface Planted { send: (type: string, data: Record<string, unknown>) => void; stops: () => number; end: () => void }

/** The chat, planted with a running turn; its socket is this spec's. */
async function plantTheTurn(page: Page): Promise<Planted> {
  let turnRuns = true
  let stops = 0
  const sockets: WebSocketRoute[] = []
  await page.routeWebSocket('**/api/ws', (ws) => { sockets.push(ws) })
  await page.route(`**/api/chat/sessions/${KEY}`, (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(detail(turnRuns)) })
      : route.fallback())
  await page.route(`**/api/chat/sessions/${KEY}/stop*`, (route) => {
    stops++
    turnRuns = false
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ stopped: true }) })
  })
  return {
    send: (type, data) => { for (const ws of sockets) ws.send(JSON.stringify({ type, data: { session: KEY, ...data } })) },
    stops: () => stops,
    end: () => { turnRuns = false },
  }
}

const statusRow = (page: Page) => page.locator('[data-turn-status]')
const scroller = (page: Page) => page.locator('[data-transcript-scroll]')

/** The line's box and look, the composer's, the transcript's and its last row's, in one layout pass. */
async function boxes(page: Page) {
  return page.evaluate(() => {
    const box = (el: Element | null | undefined) => {
      if (!el) return null
      const r = el.getBoundingClientRect()
      return { top: r.top, bottom: r.bottom, height: r.height }
    }
    const scroll = document.querySelector('[data-transcript-scroll]')
    const rows = scroll?.querySelectorAll('[data-transcript-turn="assistant"] li')
    const line = document.querySelector<HTMLElement>('[data-turn-status]')
    const look = line ? getComputedStyle(line) : null
    return {
      viewport: window.innerHeight,
      row: box(line),
      inTranscript: !!(line && scroll?.contains(line)),
      look: look ? { border: look.borderTopWidth, background: look.backgroundColor, radius: look.borderTopLeftRadius } : null,
      composer: box(document.querySelector('[data-composer-stage]')),
      scroll: box(scroll),
      lastRow: box(rows?.[rows.length - 1]),
      rows: rows?.length ?? 0,
      orbsInTranscript: scroll?.querySelectorAll('[data-agent-orb]').length ?? -1,
    }
  })
}

const composerStop = (page: Page) => page.locator('[data-composer-stage]').getByRole('button', { name: 'Stop this turn' })

test.describe("a running turn's activity line sits bare at the end of the conversation", () => {
  test('under the last of 120 rows, inside the transcript and bare; it scrolls with the thread, and goes when the turn ends', async ({ page }) => {
    const turn = await plantTheTurn(page)
    await gotoRoute(page, `chat/${KEY}`)
    await expect(statusRow(page), 'the planted turn runs, so its line shows').toBeVisible({ timeout: 15_000 })

    // The turn writes its 120 rows while a tool runs.
    turn.send('chat_chunk', { content: ROWS.join('\n') })
    turn.send('work_phase', running(2, 'tool', TOOL_LINE))
    await expect.poll(async () => (await boxes(page)).rows, 'the planted turn drew its 120 rows').toBeGreaterThanOrEqual(100)
    await expect(statusRow(page).locator('[data-turn-state]')).toHaveText(TOOL_LINE)
    await expect(statusRow(page).locator('[data-turn-elapsed]'), 'the time since the turn began').toHaveText(/^\d+s$/)
    await expect(statusRow(page).locator('[data-turn-tokens]'), 'the runtime reported no tokens, so none show').toHaveCount(0)

    // At the end of the turn: the line is in the transcript, under the turn's last row, and bare.
    await scroller(page).evaluate((el) => { el.scrollTop = el.scrollHeight })
    await expect.poll(async () => {
      const b = await boxes(page)
      return b.lastRow && b.row ? b.row.top - b.lastRow.bottom : -1
    }, "the line sits under the turn's last row").toBeGreaterThanOrEqual(0)
    const atEnd = await boxes(page)
    expect(atEnd.inTranscript, 'the line is in the transcript, so it scrolls with the thread').toBe(true)
    expect(atEnd.look, `the line is bare: ${JSON.stringify(atEnd.look)}`).toEqual({ border: '0px', background: 'rgba(0, 0, 0, 0)', radius: '0px' })
    expect(atEnd.row && atEnd.scroll && atEnd.row.bottom <= atEnd.scroll.bottom + 1, 'the line is inside the transcript\'s view at its end').toBe(true)
    expect(atEnd.orbsInTranscript, "the transcript's one live orb is the line's").toBe(1)
    await expect(composerStop(page), "the composer's send button is Stop this turn while the turn runs").toBeVisible()

    // Scrolled up to the turn's head, the line has gone with the thread; the composer's Stop has not.
    expect(await scroller(page).evaluate((el) => el.scrollHeight / el.clientHeight), 'the turn runs past the fold').toBeGreaterThan(3)
    await scroller(page).evaluate((el) => { el.scrollTop = 0 })
    await expect.poll(() => scroller(page).evaluate((el) => el.scrollTop)).toBe(0)
    await expect(statusRow(page), 'the line scrolled away with the thread').not.toBeInViewport()
    await expect(composerStop(page), 'Stop stays in reach without a scroll').toBeInViewport()

    // The turn ends: its last frame, then `chat_done`.
    turn.end()
    turn.send('work_phase', { ...idleRunState(KEY), phase: 'done', since: new Date().toISOString(), seq: 3 })
    turn.send('chat_done', { content: ROWS.join('\n') })
    await expect(statusRow(page), 'the turn ended, so its line goes').toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeVisible()
  })

  test("the composer's Stop stops the turn, and is a 44 px target on a phone", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    const turn = await plantTheTurn(page)
    await gotoRoute(page, `chat/${KEY}`)
    await expect(statusRow(page)).toBeVisible({ timeout: 15_000 })
    await expect(statusRow(page).getByRole('button'), 'the line has no button of its own').toHaveCount(0)

    const stop = composerStop(page)
    const target = await stop.boundingBox()
    expect(target && target.height >= 44 && target.width >= 44, `Stop is ${target?.width}×${target?.height}`).toBe(true)
    await stop.click()
    await expect.poll(turn.stops, "the composer's Stop sent the stop").toBe(1)
    await expect(statusRow(page), "the stopped turn's line goes").toHaveCount(0)
  })

  test('with a draft typed, a Stop sits beside Steer or Queue, a 44 px target on a phone, and stops the turn', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    const turn = await plantTheTurn(page)
    await gotoRoute(page, `chat/${KEY}`)
    await expect(statusRow(page)).toBeVisible({ timeout: 15_000 })
    await page.getByRole('textbox', { name: 'Message input' }).click()
    await page.keyboard.type('and check the tests too')
    const stage = page.locator('[data-composer-stage]')
    await expect(stage.getByRole('button', { name: /^(Steer|Queue) — / }), 'the send button takes the draft').toHaveCount(1)

    const stop = composerStop(page)
    await expect(stop, 'the turn keeps one Stop beside it').toHaveCount(1)
    const target = await stop.boundingBox()
    expect(target && target.height >= 44 && target.width >= 44, `Stop is ${target?.width}×${target?.height}`).toBe(true)
    await stop.click()
    await expect.poll(turn.stops, 'the Stop beside the draft sent the stop').toBe(1)
    await expect(statusRow(page), "the stopped turn's line goes").toHaveCount(0)
  })

  test('Esc in the composer stops the turn', async ({ page }) => {
    const turn = await plantTheTurn(page)
    await gotoRoute(page, `chat/${KEY}`)
    await expect(statusRow(page)).toBeVisible({ timeout: 15_000 })
    await page.getByRole('textbox', { name: 'Message input' }).click()
    await page.keyboard.press('Escape')
    await expect.poll(turn.stops, 'Esc in the composer sent the stop').toBe(1)
    await expect(statusRow(page), "the stopped turn's line goes").toHaveCount(0)
  })
})
