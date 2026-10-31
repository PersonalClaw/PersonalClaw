import { test, expect, type Page } from '@playwright/test'
import { gotoRoute } from './helpers'
import { idleRunState } from '../src/lib/workPhase'

// ── A PHONE'S COMPOSER KEEPS ITS FOOTER ON TWO ROWS, AND NO TALLER THAN #3804's ──────────────────
//
// #3808 measured the chat composer with #3804's method (`docs/screenshots/baseline.mjs`): 276 px at
// 390 before the redesign, 316 after it, because the footer wrapped one control per row with `+`
// alone on the first. The footer now keeps `+` and the agent-and-model pill on the first row beside
// the send cluster, and every other control on a second. jsdom lays nothing out
// (`aNarrowComposerKeepsItsFooterOnTwoRows.test.tsx` pins the structure only), so this asks a
// browser, on a new chat and on a chat with a turn, across the phone widths and at the desktop one:
// no control overlaps another or leaves the footer, the rows hold where the layout has room for
// them, and the composer is no taller than before. The loop composer, which has no first group,
// keeps its one row.

const KEY = 'chat-footer-1790000000'
const DETAIL = {
  key: KEY,
  title: 'Planning the reading digest',
  messages: [
    { role: 'user', content: "Plan this week's reading digest.", ts: '2026-09-26T09:00:00+00:00' },
    { role: 'assistant', content: 'Here is the digest, by day.', ts: '2026-09-26T09:00:20+00:00' },
  ],
  run_state: idleRunState(KEY),
  queue: [],
  task_mode: 'agent',
  approval: 'normal',
  memory_mode: 'persistent',
}

/** The same chat, measured (34% of its window) and with a cost: its composer carries the Context
 *  control, the one place that reading and cost show (#3823), and its header no cost chip. */
const MEASURED = { ...DETAIL, context_usage: { pct: 34, window: 200_000 } }
const USAGE = {
  since: '', until: '', session: KEY,
  totals: { input_tokens: 41_000, output_tokens: 5_000, cache_read_tokens: 0, cache_creation_tokens: 0, cost_usd: 0.0123, turns: 3, priced: true },
}

/** The phone widths: the narrowest, common phones, a large phone, and up to the breakpoint. */
const PHONES = [320, 360, 390, 412, 600, 768] as const
/** The composer's height at #3804's measurement: no taller than before. */
const MAX_COMPOSER_H: Record<number, number> = { 390: 276, 1280: 156 }

async function pinTheChat(page: Page, measured = false, detail: object = measured ? MEASURED : DETAIL): Promise<void> {
  await page.route(`**/api/chat/sessions/${KEY}`, (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(detail) })
      : route.fallback())
  if (measured) {
    await page.route('**/api/usage/totals?*', (route) =>
      new URL(route.request().url()).searchParams.get('session') === KEY
        ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(USAGE) })
        : route.fallback())
  }
}

interface Box { name: string; left: number; right: number; top: number; bottom: number; mid: number }
interface Footer { height: number; footer: Box; controls: Box[]; byName: Record<string, Box | null> }

/** The composer's box, its footer's, and every footer control's, read in one layout pass. */
async function readFooter(page: Page): Promise<Footer> {
  return page.evaluate(() => {
    const visible = (el: Element) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 }
    const stage = [...document.querySelectorAll('[data-composer-stage]')].find(visible)
    if (!stage) throw new Error('no composer on the page')
    // The footer is marked; a build from before the mark has it as the row the send cluster sits in.
    const sendButton = [...stage.querySelectorAll('button')].find((b) => b.getAttribute('aria-label') === 'Send message')
    const footer = stage.querySelector('[data-composer-footer]') ?? sendButton?.closest('.items-end.justify-between')
    if (!footer) throw new Error('no composer footer')
    const box = (name: string, el: Element) => {
      const r = el.getBoundingClientRect()
      return { name, left: r.left, right: r.right, top: r.top, bottom: r.bottom, mid: (r.top + r.bottom) / 2 }
    }
    const controls = [...footer.querySelectorAll('button')].filter(visible)
      .map((b) => box(b.getAttribute('aria-label') || b.textContent?.trim() || 'button', b))
    const find = (re: RegExp) => controls.find((c) => re.test(c.name)) ?? null
    return {
      height: stage.getBoundingClientRect().height,
      footer: box('footer', footer),
      controls,
      byName: {
        plus: find(/^(Add to message|Attach files)$/),
        pill: find(/^Agent and model: /),
        send: find(/^Send message$/),
        task: find(/^Task mode: /),
        boundary: find(/^Execution boundary: /),
        style: find(/^Reply style: /),
        runsOn: find(/^Runs on: /),
        context: find(/^Context$/),
      },
    }
  })
}

const sameRow = (a: Box, b: Box) => Math.abs(a.mid - b.mid) <= 6
const where = (b: Box) => `${b.name} [${Math.round(b.left)}-${Math.round(b.right)} x ${Math.round(b.top)}-${Math.round(b.bottom)}]`

/** No control overlaps another, and each stays inside the footer (a pixel of rounding allowed). */
function expectNothingCollides(f: Footer) {
  for (const c of f.controls) {
    expect(c.left, `${where(c)} inside the footer's left edge`).toBeGreaterThanOrEqual(f.footer.left - 1)
    expect(c.right, `${where(c)} inside the footer's right edge`).toBeLessThanOrEqual(f.footer.right + 1)
  }
  for (let i = 0; i < f.controls.length; i++) {
    for (let j = i + 1; j < f.controls.length; j++) {
      const a = f.controls[i], b = f.controls[j]
      const x = Math.min(a.right, b.right) - Math.max(a.left, b.left)
      const y = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top)
      expect(x > 1 && y > 1, `${where(a)} overlaps ${where(b)}`).toBe(false)
    }
  }
}

for (const screen of ['a new chat', 'a chat with a turn', 'a chat with a measured context'] as const) {
  test.describe(screen, () => {
    test.beforeEach(async ({ page }) => { if (screen !== 'a new chat') await pinTheChat(page, screen === 'a chat with a measured context') })
    const route = screen === 'a new chat' ? 'chat' : `chat/${KEY}`

    for (const width of PHONES) {
      test(`at ${width}: + and the pill on the first row, the rest on one row below, nothing colliding`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 })
        await gotoRoute(page, route)
        await expect(page.getByRole('button', { name: /^Execution boundary: / })).toBeVisible()
        const f = await readFooter(page)
        const { plus, pill, send, task, boundary, style, context } = f.byName
        for (const [k, b] of Object.entries({ plus, pill, send, task, boundary })) expect(b, `${k} is in the composer`).not.toBeNull()
        if (screen === 'a chat with a measured context') expect(context, 'the Context control is in the composer').not.toBeNull()
        expectNothingCollides(f)
        expect(sameRow(plus!, pill!), `+ and the pill share the first row: ${where(plus!)}, ${where(pill!)}`).toBe(true)
        // The send cluster keeps the first row wherever `+`, the shortest pill and it fit: every
        // phone but the narrowest, where it takes a line of its own rather than collide.
        if (width >= 360) expect(sameRow(pill!, send!), `the send cluster is on the first row: ${where(send!)}`).toBe(true)
        const rest = [task!, boundary!, ...(style ? [style] : []), ...(context ? [context] : [])]
        for (const b of rest) expect(b.top, `${where(b)} is below the first row`).toBeGreaterThanOrEqual(pill!.bottom)
        for (const b of rest) expect(sameRow(b, task!), `${where(b)} shares the second row`).toBe(true)
        if (MAX_COMPOSER_H[width]) {
          expect(f.height, `the composer's height at ${width}, against #3804's ${MAX_COMPOSER_H[width]}`).toBeLessThanOrEqual(MAX_COMPOSER_H[width])
        }
      })
    }

    test('at 1280: every control on one row, no taller than before', async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 900 })
      await gotoRoute(page, route)
      await expect(page.getByRole('button', { name: /^Execution boundary: / })).toBeVisible()
      const f = await readFooter(page)
      expectNothingCollides(f)
      for (const b of f.controls) expect(sameRow(b, f.byName.pill!), `${where(b)} shares the one row`).toBe(true)
      expect(f.height, `the composer's height at 1280, against #3804's ${MAX_COMPOSER_H[1280]}`).toBeLessThanOrEqual(MAX_COMPOSER_H[1280])
    })
  })
}

// ── IN THE CHAT'S READING COLUMN THE FOOTER KEEPS ONE ROW, WHATEVER THE NAMES IN IT ────────────────
//
// The chat's composer sits in its reading column (768 px, #5718), so a real model's or agent's name
// makes its row tight. The agent-and-model pill gives up width first, truncating its label with the
// whole of it on hover, and nothing wraps: a thread answered by a long model, with every control the
// composer carries (the Context control among them), and a new chat started with a long agent's name.
const LONG_MODEL = 'claude-opus-5-5'
const ANSWERED_BY_LONG_MODEL = {
  ...MEASURED,
  messages: [MEASURED.messages[0], { ...MEASURED.messages[1], meta: { turn_telemetry: { model: LONG_MODEL } } }],
}
const LONG_AGENT = 'research-assistant-for-the-weekly-reading-digest'
for (const width of [1280, 1920] as const) {
  for (const [screen, name] of [['a thread a long model answered', LONG_MODEL], ['a new chat with a long agent', LONG_AGENT]] as const) {
    test(`at ${width}, ${screen}: every control on one row, the pill truncating`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      if (name === LONG_MODEL) await pinTheChat(page, true, ANSWERED_BY_LONG_MODEL)
      await gotoRoute(page, name === LONG_MODEL ? `chat/${KEY}` : `chat?agent=${LONG_AGENT}`)
      await expect(page.getByRole('button', { name: /^Execution boundary: / })).toBeVisible()
      const f = await readFooter(page)
      const pill = f.byName.pill
      expect(pill, 'the agent-and-model pill is in the composer').not.toBeNull()
      expect(pill!.name, 'its name carries the long label').toContain(name)
      if (name === LONG_MODEL) expect(f.byName.context, 'the Context control is in the composer').not.toBeNull()
      expectNothingCollides(f)
      for (const b of f.controls) expect(sameRow(b, pill!), `${where(b)} shares the one row`).toBe(true)
      const pillButton = page.getByRole('button', { name: /^Agent and model: / }).first()
      expect(await pillButton.getAttribute('title') ?? '', 'the whole label is on hover').toContain(name)
      const icon = await pillButton.locator('svg').first().evaluate((e) => e.getBoundingClientRect().width)
      expect(icon, 'the label gives up width, the icon keeps its 16 px').toBeGreaterThanOrEqual(15.5)
    })
  }
}

// The loop composer keeps main's one wrapping row: at 390 its two pills wrap beside the send
// cluster, as they did before; at 600 all of it is one row.
for (const width of [390, 600] as const) {
  test(`a new loop at ${width}: nothing colliding${width === 600 ? ', and one row' : ''}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    await gotoRoute(page, 'loops')
    await expect(page.getByRole('button', { name: /^Execution boundary: / })).toBeVisible()
    const f = await readFooter(page)
    const { send, runsOn, boundary } = f.byName
    for (const [k, b] of Object.entries({ send, runsOn, boundary })) expect(b, `${k} is in the composer`).not.toBeNull()
    expectNothingCollides(f)
    if (width === 600) {
      expect(sameRow(runsOn!, boundary!), `Runs on and the Execution Boundary share a row: ${where(runsOn!)}, ${where(boundary!)}`).toBe(true)
      expect(sameRow(boundary!, send!), `at 600 the loop's footer is one row: ${where(send!)}`).toBe(true)
    }
  })
}

// A started chat with nothing else to say about itself (no project, branch, app or investigation)
// gets its one-row header back once the cost chip leaves it: 56 px, against 88 with the chip.
for (const width of [390, 1280] as const) {
  test(`a started chat with a cost, at ${width}: a one-row header, with no cost in it`, async ({ page }) => {
    await pinTheChat(page, true)
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 })
    await gotoRoute(page, `chat/${KEY}`)
    await expect(page.getByRole('button', { name: 'Context' })).toBeVisible()
    const header = page.locator('header').first()
    await expect(header).not.toContainText('$0.0123')
    const h = await header.evaluate((el) => el.getBoundingClientRect().height)
    expect(h, `the header's height at ${width}`).toBeLessThanOrEqual(56)
  })
}
