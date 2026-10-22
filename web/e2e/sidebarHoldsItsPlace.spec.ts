import { test, expect, type Page } from '@playwright/test'
import { gotoRoute } from './helpers'
import { idleRunState } from '../src/lib/workPhase'

// ── THE SIDEBAR HOLDS ITS PLACE: HUNDREDS OF THREADS, A LONG NAME, THE WAY BACK, AND THE PHONE ──────
//
// #3814's and #3812's acceptance, asked of a real browser, since jsdom lays nothing out:
//
// · Hundreds of threads draw a bounded number of rows, and the keyboard still walks every one of
//   them in the sidebar's order: End reaches the last thread, which was never drawn, and the arrow
//   keys go on past the drawn rows one thread at a time.
// · A long name is cut short on screen, and shown whole while its row has keyboard focus.
// · Back and Forward light the thread the address opens, and collapsing and reopening the sidebar,
//   or a reload, brings back the lit thread, the place it was scrolled to and the row focus was on.
// · Every primary destination is one click away at 320, 390, 412, 768, 1024 and 1440 px, in the
//   sidebar or in the drawer the toolbar opens, and its row lights once it is reached.
// · At 320, 390 and 412 px the sidebar is the drawer the toolbar opens. A copied address, with the
//   thread's Activity pane open, opens that thread with its pane; the drawer holds every primary
//   destination and the threads, the open thread lit; a thread opened from it closes it; and Back
//   and Forward go between the two threads with the pane as it was.
//
// The threads and their project are route stubs, so the gateway's own state is never the variable.

const RAIL = 'nav[data-tour="rail"]'
const SCROLLER = `${RAIL} [data-rail-scroll]`
const DRAWER = '[role="dialog"][aria-label="Navigation"]'

const COUNT = 300
const pad = (i: number) => String(i).padStart(3, '0')
const key = (i: number) => `e2e-many-${pad(i)}`
const rowKey = (i: number) => `chat:${key(i)}`

/** Three hundred chats in one project, newest first in the sidebar: `e2e-many-000` is the newest. */
const MANY = Array.from({ length: COUNT }, (_, i) => ({
  key: key(i), title: `Garden thread ${pad(i)}`, messages: 2, run_state: 'idle', project_id: 'p-garden',
  last_activity_ts: new Date(Date.UTC(2026, 9, 1) - i * 60_000).toISOString(),
}))

const LONG = 'Find a supplier for heritage tomato seeds that ships to the north before the first frost of the year'
const LONG_PROJECT = 'The garden behind the house on the hill, with the raised beds, the greenhouse and the orchard'
const FEW = [
  { key: 'e2e-few-0', title: 'Plan the beds', messages: 2, run_state: 'idle', project_id: 'p-garden', last_activity_ts: '2026-10-01T00:03:00Z' },
  { key: 'e2e-few-1', title: LONG, messages: 2, run_state: 'idle', project_id: 'p-garden', last_activity_ts: '2026-10-01T00:02:00Z' },
  { key: 'e2e-few-2', title: 'Order the soil', messages: 2, run_state: 'idle', project_id: 'p-garden', last_activity_ts: '2026-10-01T00:01:00Z' },
]

async function stubThreads(page: Page, sessions: { key: string; title: string }[], project = 'Garden'): Promise<void> {
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  await page.route('**/api/loops', (route) =>
    route.request().method() === 'GET' ? route.fulfill(json({ loops: [] })) : route.fallback())
  await page.route('**/api/chat/sessions', (route) =>
    route.request().method() === 'GET' ? route.fulfill(json(sessions)) : route.fallback())
  await page.route('**/api/projects', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill(json({ projects: [{ id: 'p-garden', name: project, status: 'active' }] }))
      : route.fallback())
  // A thread the address opens reads its own detail: an idle chat with one exchange.
  await page.route(/\/api\/chat\/sessions\/e2e-(many|few)-\d+$/, (route) => {
    const k = new URL(route.request().url()).pathname.split('/').pop()!
    return route.request().method() === 'GET'
      ? route.fulfill(json({
        key: k, title: sessions.find((s) => s.key === k)?.title ?? k, run_state: idleRunState(k), queue: [],
        messages: [
          { role: 'user', content: 'Hello.', ts: '2026-10-01T00:00:00+00:00' },
          { role: 'assistant', content: 'Hello again.', ts: '2026-10-01T00:00:05+00:00' },
        ],
        task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
      }))
      : route.fallback()
  })
}

/** The thread row focus is on, by its key, or null when focus is on no thread row. */
const focusedThread = (page: Page) => page.evaluate(() => document.activeElement?.getAttribute('data-thread-key') ?? null)
/** The thread rows drawn in *within*. */
const drawnRows = (page: Page, within: string) => page.evaluate((sel) => document.querySelectorAll(`${sel} [data-thread-key]`).length, within)
/** The lit thread row's key in *within*. */
const litThread = (page: Page, within: string) =>
  page.evaluate((sel) => document.querySelector(`${sel} [data-thread-key][aria-current="page"]`)?.getAttribute('data-thread-key') ?? null, within)
/** Whether the phone's drawer is open: on screen and not hidden from assistive technology. It is
 *  always mounted, parked off the screen's left edge, `aria-hidden` and inert while closed. */
const drawerOpen = (page: Page) => page.evaluate((sel) => {
  const d = document.querySelector(sel)
  return !!d && d.getAttribute('aria-hidden') !== 'true' && d.getBoundingClientRect().right > 0
}, DRAWER)
const scrollTop = (page: Page) => page.evaluate((sel) => Math.round(document.querySelector(sel)!.scrollTop), SCROLLER)
const hash = (page: Page) => page.evaluate(() => location.hash)

test.describe('hundreds of threads', () => {
  test.use({ viewport: { width: 1280, height: 800 } })

  test('draw a bounded number of rows, and the keyboard walks every thread in order', async ({ page }) => {
    await stubThreads(page, MANY)
    await gotoRoute(page, 'dashboard')
    const first = page.locator(`${RAIL} [data-thread-key="${rowKey(0)}"]`)
    await expect(first).toBeVisible()

    const drawn = await drawnRows(page, RAIL)
    expect(drawn, 'some rows are drawn').toBeGreaterThan(0)
    expect(drawn, 'not all three hundred').toBeLessThan(COUNT / 2)

    await first.focus()
    await page.keyboard.press('End')
    await expect.poll(() => focusedThread(page), { message: 'End reaches the last thread, never drawn before' }).toBe(rowKey(COUNT - 1))
    expect(await drawnRows(page, RAIL), 'still bounded at the end').toBeLessThan(COUNT / 2)

    await page.keyboard.press('Home')
    await expect.poll(() => focusedThread(page), { message: 'Home returns to the first thread' }).toBe(rowKey(0))
    const walked: (string | null)[] = []
    for (let i = 1; i <= 90; i++) {
      await page.keyboard.press('ArrowDown')
      walked.push(await focusedThread(page))
    }
    expect(walked, 'ArrowDown goes on past the drawn rows, one thread at a time, in order')
      .toEqual(Array.from({ length: 90 }, (_, i) => rowKey(i + 1)))
  })
})

/** Whether a row's label is cut short on screen, and how tall the row is. */
const measure = (row: ReturnType<Page['locator']>) => row.evaluate((b) => {
  const label = b.querySelector<HTMLElement>('.truncate')!
  return { cut: label.scrollWidth > label.clientWidth + 1, height: b.getBoundingClientRect().height }
})

test.describe('a long name', () => {
  test.use({ viewport: { width: 1280, height: 800 } })

  test("a thread's and a project's are cut short on screen, and shown whole while the row has keyboard focus", async ({ page }) => {
    await stubThreads(page, FEW, LONG_PROJECT)
    await gotoRoute(page, 'dashboard')
    const row = page.locator(`${RAIL} [data-thread-key="chat:e2e-few-1"]`)
    await expect(row).toBeVisible()
    const resting = await measure(row)
    expect(resting.cut, 'the name is cut short on screen').toBe(true)
    expect(await row.getAttribute('aria-label'), 'and whole in its name').toBe(LONG)

    // Focus by the keyboard, from the row above it.
    await page.locator(`${RAIL} [data-thread-key="chat:e2e-few-0"]`).focus()
    await page.keyboard.press('Tab')
    await expect.poll(() => focusedThread(page)).toBe('chat:e2e-few-1')
    const focused = await measure(row)
    expect(focused.cut, 'the whole name shows while the row has focus').toBe(false)
    expect(focused.height, 'the row grows to hold it').toBeGreaterThan(resting.height)

    // The project's own row, its disclosure.
    const project = page.locator(RAIL).getByRole('button', { name: `${LONG_PROJECT}, 3 threads` })
    const projectResting = await measure(project)
    expect(projectResting.cut, "the project's name is cut short on screen").toBe(true)
    await page.locator(RAIL).getByRole('searchbox', { name: 'Search threads' }).focus()
    await page.keyboard.press('Tab')
    await expect(project, 'Tab from the search reaches the project').toBeFocused()
    const projectFocused = await measure(project)
    expect(projectFocused.cut, "the project's whole name shows while its row has focus").toBe(false)
    expect(projectFocused.height).toBeGreaterThan(projectResting.height)
  })
})

test.describe('the way back to a thread', () => {
  test.use({ viewport: { width: 1280, height: 800 } })

  test('Back and Forward light the thread the address opens; a collapse and a reload bring back the lit row, the scroll and the focus', async ({ page }) => {
    await stubThreads(page, MANY)
    await gotoRoute(page, `chat/${key(2)}`)
    await expect.poll(() => litThread(page, RAIL)).toBe(rowKey(2))

    // Scroll into the threads and open one there, as a click does.
    await page.evaluate((sel) => { document.querySelector(sel)!.scrollTop = 600 }, SCROLLER)
    const deep = page.locator(`${RAIL} [data-thread-key="${rowKey(20)}"]`)
    await deep.scrollIntoViewIfNeeded()
    await deep.click()
    await expect.poll(() => hash(page)).toBe(`#/chat/${key(20)}`)
    await expect.poll(() => litThread(page, RAIL)).toBe(rowKey(20))

    await page.goBack()
    await expect.poll(() => hash(page), { message: 'Back opens the thread before' }).toBe(`#/chat/${key(2)}`)
    await expect.poll(() => litThread(page, RAIL), { message: 'and lights it' }).toBe(rowKey(2))
    await page.goForward()
    await expect.poll(() => hash(page), { message: 'Forward opens the thread after' }).toBe(`#/chat/${key(20)}`)
    await expect.poll(() => litThread(page, RAIL), { message: 'and lights it' }).toBe(rowKey(20))

    await deep.focus()
    const scrolled = await scrollTop(page)
    expect(scrolled, 'the sidebar is scrolled into the threads').toBeGreaterThan(0)

    await page.getByRole('button', { name: 'Collapse sidebar' }).click()
    await expect(page.locator(`${RAIL} [data-thread-key]`)).toHaveCount(0)
    await page.getByRole('button', { name: 'Expand sidebar' }).click()
    await expect.poll(() => litThread(page, RAIL), { message: 'reopened, the same thread is lit' }).toBe(rowKey(20))
    await expect.poll(() => focusedThread(page), { message: 'and focus is back on the row that had it' }).toBe(rowKey(20))
    await expect.poll(() => scrollTop(page), { message: 'and the sidebar is where it was scrolled' }).toBe(scrolled)

    await page.reload()
    await expect.poll(() => litThread(page, RAIL), { message: 'reloaded, the same thread is lit' }).toBe(rowKey(20))
    await expect.poll(() => scrollTop(page), { message: 'and the sidebar is where it was scrolled' }).toBe(scrolled)
  })
})

/** The rows a person reaches the primary destinations by. */
const PRIMARY = ['Home', 'Chat', 'Projects', 'Library', 'Inbox', 'Automations', 'Capabilities', 'Settings']

/** A destination's row, by its name: a row's badge rides its name ("Inbox, 2 new"). */
const destination = (within: ReturnType<Page['locator']>, name: string) =>
  within.getByRole('button', { name: new RegExp(`^${name}(,|$)`) }).first()

for (const width of [320, 390, 412, 768, 1024, 1440]) {
  test.describe(`every primary destination at ${width} px`, () => {
    test.use({ viewport: { width, height: 800 } })

    test('is one click away, in the sidebar or the drawer the toolbar opens, and lights when reached', async ({ page }) => {
      await stubThreads(page, FEW)
      await gotoRoute(page, 'dashboard')
      const drawn = await page.evaluate((sel) => !!document.querySelector(sel), DRAWER)
      const where = page.locator(drawn ? `${DRAWER} nav` : RAIL)
      const open = async () => {
        if (!drawn) return
        await page.getByRole('button', { name: 'Expand sidebar' }).click()
        await expect.poll(() => drawerOpen(page)).toBe(true)
      }
      for (const name of PRIMARY) {
        await open()
        const before = await hash(page)
        await destination(where, name).click()
        if (drawn) await expect.poll(() => drawerOpen(page), { message: `${name}: the drawer closes` }).toBe(false)
        if (name !== 'Home' || before !== '#/dashboard') {
          await expect.poll(() => hash(page), { message: `${name} opens its page` }).not.toBe(before)
        }
        await open()
        await expect(destination(where, name), `${name} is lit`).toHaveAttribute('aria-current', 'page')
        if (drawn) {
          await page.keyboard.press('Escape')
          await expect.poll(() => drawerOpen(page)).toBe(false)
        }
      }
    })
  })
}

for (const width of [320, 390, 412]) {
  test.describe(`at ${width} px`, () => {
    test.use({ viewport: { width, height: 800 } })

    test('the drawer holds the destinations and the threads; a copied address, Back and Forward keep the thread and its pane', async ({ page }) => {
      await stubThreads(page, FEW)
      // The copied address: a thread with its Activity pane open.
      const copied = 'chat/e2e-few-0?activity=1'
      await gotoRoute(page, copied)
      await expect(page.getByRole('region', { name: 'Activity' })).toBeVisible()
      expect(await drawerOpen(page), 'the sidebar is the drawer, closed until the toolbar opens it').toBe(false)

      await page.getByRole('button', { name: 'Expand sidebar' }).click()
      const drawer = page.locator(DRAWER)
      await expect.poll(() => drawerOpen(page), { message: 'the toolbar opens the drawer' }).toBe(true)
      const rows = await drawer.locator('nav button[aria-label]').evaluateAll((bs) => bs.map((b) => (b.getAttribute('aria-label') ?? '').split(', ')[0]))
      for (const name of PRIMARY) expect(rows, `the drawer has ${name}`).toContain(name)
      await expect.poll(() => litThread(page, DRAWER), { message: 'the open thread is lit in the drawer' }).toBe('chat:e2e-few-0')

      await drawer.locator('[data-thread-key="chat:e2e-few-2"]').click()
      await expect.poll(() => drawerOpen(page), { message: 'a thread opened from the drawer closes it' }).toBe(false)
      await expect.poll(() => hash(page)).toBe('#/chat/e2e-few-2')

      await page.goBack()
      await expect.poll(() => hash(page), { message: 'Back opens the copied address' }).toBe(`#/${copied}`)
      await expect(page.getByRole('region', { name: 'Activity' }), 'with its pane open').toBeVisible()
      await page.goForward()
      await expect.poll(() => hash(page), { message: 'Forward opens the other thread' }).toBe('#/chat/e2e-few-2')
      await page.getByRole('button', { name: 'Expand sidebar' }).click()
      await expect.poll(() => drawerOpen(page)).toBe(true)
      await expect.poll(() => litThread(page, DRAWER), { message: 'and the drawer lights it' }).toBe('chat:e2e-few-2')
    })
  })
}
