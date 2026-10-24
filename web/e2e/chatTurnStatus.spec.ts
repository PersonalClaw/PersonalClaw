import { test, expect, type Page, type WebSocketRoute } from '@playwright/test'
import { gotoRoute } from './helpers'
import { idleRunState } from '../src/lib/workPhase'

// ── A long turn's live status stays at the bottom, where the reader is ──────────────────────────
//
// A turn can run to hundreds of rows while the transcript follows its end, so what the turn is
// doing lives in one row pinned just above the composer, outside the transcript's scroll
// (`ui/TurnStatusRow.tsx`, `web/REDESIGN.md` "Live state sits at the bottom"). jsdom lays nothing
// out, so the component tests cannot see where it sits; this spec asks a real browser, on a planted
// turn of 120 rows: the row sits above the composer and inside the viewport, it stays there with
// the transcript scrolled to its top, it covers none of the turn's last row, no orb is left in the
// transcript, Esc in the composer stops the turn, and the row goes when the turn ends.
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

/** The row's box, the composer's, the transcript's and its last row's, in one layout pass. */
async function boxes(page: Page) {
  return page.evaluate(() => {
    const box = (el: Element | null | undefined) => {
      if (!el) return null
      const r = el.getBoundingClientRect()
      return { top: r.top, bottom: r.bottom, height: r.height }
    }
    const scroll = document.querySelector('[data-transcript-scroll]')
    const rows = scroll?.querySelectorAll('[data-transcript-turn="assistant"] li')
    return {
      viewport: window.innerHeight,
      row: box(document.querySelector('[data-turn-status]')),
      composer: box(document.querySelector('[data-composer-stage]')),
      scroll: box(scroll),
      lastRow: box(rows?.[rows.length - 1]),
      rows: rows?.length ?? 0,
      orbsInTranscript: scroll?.querySelectorAll('[data-agent-orb]').length ?? -1,
    }
  })
}

test.describe('a long turn keeps its live status at the bottom', () => {
  test('pinned above the composer through 120 rows and a scroll to the top, gone when the turn ends', async ({ page }) => {
    const turn = await plantTheTurn(page)
    await gotoRoute(page, `chat/${KEY}`)
    await expect(statusRow(page), 'the planted turn runs, so its status row shows').toBeVisible({ timeout: 15_000 })

    // The turn writes its 120 rows while a tool runs.
    turn.send('chat_chunk', { content: ROWS.join('\n') })
    turn.send('work_phase', running(2, 'tool', TOOL_LINE))
    await expect.poll(async () => (await boxes(page)).rows, 'the planted turn drew its 120 rows').toBeGreaterThanOrEqual(100)
    await expect(statusRow(page).locator('[data-turn-state]')).toHaveText(TOOL_LINE)
    await expect(statusRow(page).locator('[data-turn-elapsed]'), 'the time since the turn began').toHaveText(/^\d+s$/)
    await expect(statusRow(page).locator('[data-turn-tokens]'), 'the runtime reported no tokens, so none show').toHaveCount(0)

    // At the end of the turn: the row sits above the composer, inside the viewport, and the last
    // row is above it, uncovered.
    await scroller(page).evaluate((el) => { el.scrollTop = el.scrollHeight })
    await expect.poll(async () => {
      const b = await boxes(page)
      return b.lastRow && b.row ? b.row.top - b.lastRow.bottom : -1
    }, 'the turn\'s last row is above the status row, never under it').toBeGreaterThanOrEqual(0)
    const atEnd = await boxes(page)
    expect(atEnd.row && atEnd.composer && atEnd.row.bottom <= atEnd.composer.top + 1, 'the row sits above the composer').toBe(true)
    expect(atEnd.row && atEnd.row.bottom <= atEnd.viewport && atEnd.row.top >= atEnd.viewport / 2, 'the row is at the bottom of the viewport').toBe(true)
    expect(atEnd.scroll && atEnd.row && atEnd.scroll.bottom <= atEnd.row.top + 1, 'the transcript\'s scroll ends above the row: its height is reserved').toBe(true)
    expect(atEnd.orbsInTranscript, 'no live orb is left at the head of the turn').toBe(0)

    // Scrolled up to the turn's head, the row has not moved. The turn is several screens tall, so
    // the scroll is a real one.
    expect(await scroller(page).evaluate((el) => el.scrollHeight / el.clientHeight), 'the turn runs past the fold').toBeGreaterThan(3)
    await scroller(page).evaluate((el) => { el.scrollTop = 0 })
    await expect.poll(() => scroller(page).evaluate((el) => el.scrollTop)).toBe(0)
    await expect(statusRow(page)).toBeInViewport()
    const atTop = await boxes(page)
    expect(atTop.row?.top, 'the row stays where it was with the transcript scrolled up').toBeCloseTo(atEnd.row?.top ?? -1, 0)

    // The turn ends: its last frame, then `chat_done`.
    turn.end()
    turn.send('work_phase', { ...idleRunState(KEY), phase: 'done', since: new Date().toISOString(), seq: 3 })
    turn.send('chat_done', { content: ROWS.join('\n') })
    await expect(statusRow(page), 'the turn ended, so its status row goes').toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeVisible()
  })

  test('Esc in the composer stops the turn, and Stop is a 44 px target on a phone', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    const turn = await plantTheTurn(page)
    await gotoRoute(page, `chat/${KEY}`)
    await expect(statusRow(page)).toBeVisible({ timeout: 15_000 })
    await expect(statusRow(page)).toBeInViewport()

    const stop = statusRow(page).getByRole('button', { name: 'Stop this turn' })
    const target = await stop.boundingBox()
    expect(target && target.height >= 44 && target.width >= 44, `Stop is ${target?.width}×${target?.height}`).toBe(true)

    await page.getByRole('textbox', { name: 'Message input' }).click()
    await page.keyboard.press('Escape')
    await expect.poll(turn.stops, 'Esc in the composer sent the stop').toBe(1)
    await expect(statusRow(page), 'the stopped turn\'s status row goes').toHaveCount(0)
  })
})
