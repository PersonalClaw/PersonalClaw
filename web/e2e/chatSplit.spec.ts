import { test, expect, type Page } from '@playwright/test'
import { gotoRoute } from './helpers'
import { idleRunState } from '../src/lib/workPhase'

// ── A reply's block opens beside the conversation, and a compact window stacks it ──────────────
//
// #3820's approved update: "Code, tables, diffs, artifacts, and review can use an adjustable split
// that remembers its size. Compact layouts stack the work and artifact without clipping either one.
// The URL records reading or artifact mode and the selected artifact." The unit tests hold the
// address and the layout's choice; this holds what only a browser can: the boxes. Beside, the block
// and the turns do not overlap and both are on screen; a drag changes the width and a reload keeps
// it; Back closes the split. Stacked at 320, 390 and 412, the block is under the thread and above
// the composer, at the width of the box they share, each with room to read, and nothing runs past
// the window. The chat's reads are answered
// with route stubs, so the page's geometry is the only variable.

const KEY = 'chat-split-1790000000'
const REPLY = [
  'Here is the script, the week and the change.',
  '',
  '```python',
  'def greet(name):',
  '    return f"hello {name}, this line is long enough to need the room a split gives it"',
  '```',
  '',
  '| Day | Owner | Task | Status | Due | Notes | Risk | Link |',
  '|---|---|---|---|---|---|---|---|',
  '| Mon | Ana | Write the brief | Doing | Tue | First draft | Low | brief.md |',
  '| Tue | Ben | Review the brief | Waiting | Wed | Needs the draft | Medium | brief.md |',
  '',
  '```diff',
  '-old line',
  '+new line',
  '```',
].join('\n')

const DETAIL = {
  key: KEY,
  title: 'The launch week',
  messages: [
    { role: 'user', content: 'Show me the script, the week and the change.', ts: '2026-10-20T09:00:00+00:00' },
    { role: 'assistant', content: REPLY, ts: '2026-10-20T09:00:20+00:00' },
  ],
  run_state: idleRunState(KEY),
  queue: [],
  task_mode: 'agent',
  approval: 'normal',
  memory_mode: 'persistent',
}

async function pinTheChat(page: Page): Promise<void> {
  await page.route(`**/api/chat/sessions/${KEY}`, (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(DETAIL) })
      : route.fallback())
}

type Box = { left: number; right: number; top: number; bottom: number; width: number; height: number }

/** The boxes that matter, read in one layout pass: the pane host's, the pane's, the transcript's
 *  scroller and its turn column, and how wide the page runs. */
async function readSplit(page: Page) {
  return page.evaluate(() => {
    const box = (el: Element | null): Box | null => {
      if (!el) return null
      const r = el.getBoundingClientRect()
      return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height }
    }
    const host = document.querySelector('[data-pane-host="chat"]')
    const pane = host?.querySelector('section[aria-labelledby]') ?? null
    const turn = document.querySelector('[data-transcript-turn]')
    return {
      layout: host?.getAttribute('data-pane-layout') ?? null,
      host: box(host ?? null),
      pane: box(pane),
      scroller: box(document.querySelector('[data-transcript-scroll]')),
      column: box(turn?.parentElement ?? null),
      composer: box(document.querySelector('.cm-editor')),
      pageWidth: document.documentElement.scrollWidth,
      viewport: window.innerWidth,
    }
  })
}

async function openTheCode(page: Page): Promise<void> {
  await pinTheChat(page)
  await gotoRoute(page, `chat/${KEY}`)
  await expect(page.locator('[data-transcript-turn]'), 'the chat never showed its turns').toHaveCount(2)
  await page.getByRole('button', { name: 'Open python code beside' }).click()
  await expect(page.getByRole('region', { name: 'python code' })).toBeVisible()
}

test.describe('a block opens beside the conversation', () => {
  test('1280px — beside the turns, both on screen, no overlap, and the address names it', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 })
    await openTheCode(page)
    const g = await readSplit(page)
    const report = JSON.stringify(g)
    expect(g.layout, report).toBe('beside')
    expect(g.pane!.left, `the block overlaps the turns: ${report}`).toBeGreaterThanOrEqual(g.scroller!.right - 1)
    expect(g.pane!.right, `the block runs off-screen: ${report}`).toBeLessThanOrEqual(g.viewport)
    expect(g.column!.width, `the turns lost their column: ${report}`).toBeGreaterThan(300)
    expect(g.pageWidth, `the page runs past the window: ${report}`).toBeLessThanOrEqual(g.viewport)
    expect(page.url()).toMatch(/[?&]beside=code-/)
  })

  test('1280px — a drag widens it, a reload opens it at that width, and Back closes it', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 })
    await openTheCode(page)
    const before = (await readSplit(page)).pane!.width
    const handle = page.getByRole('separator', { name: /^Resize code/ })
    const h = (await handle.boundingBox())!
    await page.mouse.move(h.x + h.width / 2, h.y + h.height / 2)
    await page.mouse.down()
    await page.mouse.move(h.x + h.width / 2 - 120, h.y + h.height / 2, { steps: 6 })
    await page.mouse.up()
    const widened = (await readSplit(page)).pane!.width
    expect(Math.round(widened - before), 'a 120 px drag toward the turns widens the block by 120 px').toBe(120)
    await page.reload()
    await expect(page.getByRole('region', { name: 'python code' }), 'a reload opens the same block').toBeVisible()
    expect(Math.round((await readSplit(page)).pane!.width), 'at the width it was left').toBe(Math.round(widened))
    await page.goBack()
    await expect(page.getByRole('region', { name: 'python code' }), 'Back closes the split').toHaveCount(0)
    expect(page.url()).not.toMatch(/[?&]beside=/)
  })
})

test.describe('a compact window stacks the block under the thread', () => {
  for (const width of [320, 390, 412]) {
    test(`${width}px — the block is between the thread and the composer at the box's width, neither clipped`, async ({ page }) => {
      await page.setViewportSize({ width, height: 800 })
      await openTheCode(page)
      const g = await readSplit(page)
      const report = JSON.stringify(g)
      expect(g.layout, report).toBe('stacked')
      expect(page.getByRole('separator', { name: /^Resize code/ }), 'no width to choose').toHaveCount(0)
      expect(g.pane!.top, `the block is not under the thread: ${report}`).toBeGreaterThanOrEqual(g.scroller!.bottom - 1)
      expect(g.pane!.bottom, `the composer is not under the block: ${report}`).toBeLessThanOrEqual(g.composer!.top + 1)
      expect(Math.abs(g.pane!.width - g.host!.width), `the block is not the box's width: ${report}`).toBeLessThanOrEqual(1)
      expect(g.pane!.left, report).toBeGreaterThanOrEqual(0)
      expect(g.pane!.right, `the block runs off-screen: ${report}`).toBeLessThanOrEqual(width)
      expect(g.scroller!.height, `the thread has no room: ${report}`).toBeGreaterThan(80)
      expect(g.pane!.height, `the block has no room: ${report}`).toBeGreaterThan(80)
      expect(g.pageWidth, `the page runs past the window: ${report}`).toBeLessThanOrEqual(width)
    })
  }
})
