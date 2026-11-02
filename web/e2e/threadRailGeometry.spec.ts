import { test, expect, type Page } from '@playwright/test'
import { gotoRoute } from './helpers'
import { idleRunState } from '../src/lib/workPhase'

// ── THE CHAT HISTORY'S BAR STAYS PINNED AT ITS TOP WHILE THE ROWS SCROLL UNDER IT ────────────────
//
// The sidebar is a shell (#5878): a top block (the icon and name, the leading destinations) and a
// bottom block (Inbox, Automations, Capabilities, then Settings) that keep their height, and between
// them the chat history, the one part that scrolls. New chat and the search are one bar at the top
// of the chat history (#3814), and while the rows scroll under it the bar stays at the top of the
// scroll area, on the rail's solid surface, so no row shows through it. Scrolled to its end, the
// last row shows whole above the bottom block, and neither block moves.
//
// jsdom lays nothing out, so `threadRailKeepsItsTopPinned.test.tsx` reads the bar's styles and checks
// the scrolling against sizes set by hand. This asks a real browser the property: with forty chats
// and a short window, scrolled into the chat history, the bar's top is the scroll area's top, the bar
// is the topmost thing everywhere on it while a row passes under it, and it paints opaque. Focus
// moved up a row by the keyboard, and a chat the address opens above the bar, each land below the
// bar, whole and topmost, the ring clear of it too (WCAG 2.4.11); focus moved down to a row at the
// fold lands with its ring clear of the fold. In a 720 px window Settings, in the bottom block,
// keeps its whole ring. The chats and projects are route stubs, so the layout is the only variable.

/** How far a focus ring reaches past its control (`RING_REACH`, `ui/NavRail.tsx`). */
const RING = 4

/** The sidebar's scroll area: the chat history, between the top and the bottom block. */
const SCROLLER = 'nav[data-tour="rail"] [data-rail-scroll]'

const COUNT = 40
const key = (i: number) => `e2e-rail-${String(i).padStart(2, '0')}`

/** Forty chats with no project, newest first in the sidebar: `e2e-rail-00` is the newest. */
const SESSIONS = Array.from({ length: COUNT }, (_, i) => ({
  key: key(i), title: `Rail thread ${String(i).padStart(2, '0')}`, messages: 2, run_state: 'idle',
  last_activity_ts: new Date(Date.UTC(2026, 9, 1) - i * 60_000).toISOString(),
}))

async function stubThreads(page: Page): Promise<void> {
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  await page.route('**/api/loops', (route) =>
    route.request().method() === 'GET' ? route.fulfill(json({ loops: [] })) : route.fallback())
  await page.route('**/api/chat/sessions', (route) =>
    route.request().method() === 'GET' ? route.fulfill(json(SESSIONS)) : route.fallback())
  // No projects, so every thread is under No project and no project row moves the layout.
  await page.route('**/api/projects', (route) =>
    route.request().method() === 'GET' ? route.fulfill(json({ projects: [] })) : route.fallback())
  // A thread the address opens reads its own detail: an idle chat with one exchange.
  await page.route('**/api/chat/sessions/e2e-rail-*', (route) => {
    const k = new URL(route.request().url()).pathname.split('/').pop()!
    return route.request().method() === 'GET'
      ? route.fulfill(json({
        key: k, title: SESSIONS.find((s) => s.key === k)?.title ?? k, run_state: idleRunState(k), queue: [],
        messages: [
          { role: 'user', content: 'Hello.', ts: '2026-10-01T00:00:00+00:00' },
          { role: 'assistant', content: 'Hello again.', ts: '2026-10-01T00:00:05+00:00' },
        ],
        task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
      }))
      : route.fallback()
  })
}

interface RailGeometry {
  areaTop: number
  barTop: number
  barBottom: number
  /** Thread rows whose box crosses the bar's band: what is scrolled under it. */
  rowsUnderBar: number
  /** Points on the bar where something else is topmost, by what is there. */
  shownThrough: string[]
  /** The bar's own background colour, as painted. */
  barBackground: string
}

/** Read the sidebar in one evaluate, so every number shares a layout pass. */
async function readRail(page: Page): Promise<RailGeometry> {
  return page.evaluate((scroller) => {
    const area = document.querySelector<HTMLElement>(scroller)
    const newThread = [...document.querySelectorAll('button')].find((b) => b.textContent?.trim() === 'New thread')
    const bar = newThread?.parentElement
    const group = bar?.closest('[role="group"]')
    if (!area || !bar || !group) throw new Error('no sidebar scroll area, New thread bar or Threads group')
    const n = area.getBoundingClientRect()
    const b = bar.getBoundingClientRect()
    const rows = [...group.querySelectorAll('[data-thread-key]')].map((r) => r.getBoundingClientRect())
    const shownThrough: string[] = []
    for (const fx of [0.05, 0.5, 0.95]) {
      for (const fy of [0.1, 0.5, 0.9]) {
        const x = b.left + b.width * fx
        const y = b.top + b.height * fy
        if (y < n.top || y > n.bottom) continue
        const hit = document.elementFromPoint(x, y)
        if (!hit || !bar.contains(hit)) shownThrough.push(`${Math.round(fx * 100)}%,${Math.round(fy * 100)}%: ${hit?.closest('[data-thread-key]')?.getAttribute('aria-label') ?? hit?.tagName ?? 'nothing'}`)
      }
    }
    return {
      areaTop: n.top,
      barTop: b.top,
      barBottom: b.bottom,
      rowsUnderBar: rows.filter((r) => r.bottom > b.top + 1 && r.top < b.bottom - 1).length,
      shownThrough,
      barBackground: getComputedStyle(bar).backgroundColor,
    }
  }, SCROLLER)
}

/** Scroll the sidebar by *dy* and let it paint. */
async function scrollRail(page: Page, dy: number): Promise<void> {
  await page.evaluate(([sel, d]) => { document.querySelector(sel as string)!.scrollTop += d as number }, [SCROLLER, dy] as const)
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))))
}

/** Whether a painted CSS colour is fully opaque: not transparent, and no alpha under 1, whether the
 *  browser writes it `rgba(r, g, b, a)` or `<space>(… / a)`. */
const opaque = (color: string) => color !== 'transparent'
  && !/rgba\([^)]*,\s*(0|0?\.\d+)\)$/.test(color) && !/\/\s*(0|0?\.\d+)\)$/.test(color)

test.describe('the top of the thread rail', () => {
  test.use({ viewport: { width: 1280, height: 640 } })

  test('stays at the top of the sidebar while the threads scroll under it, and nothing shows through', async ({ page }) => {
    await stubThreads(page)
    await gotoRoute(page, 'dashboard')
    await expect(page.getByRole('button', { name: /Rail thread 39/ })).toBeAttached()

    const before = await readRail(page)
    expect(before.barTop, 'the bar is in the chat history').toBeGreaterThanOrEqual(before.areaTop - 1)
    // Scroll the rows well past where the bar sat: it stays at the scroll area's top.
    await scrollRail(page, before.barTop - before.areaTop + 300)
    const scrolled = await readRail(page)
    expect(Math.abs(scrolled.barTop - scrolled.areaTop), 'the bar is pinned to the top of the sidebar').toBeLessThanOrEqual(1)
    expect(scrolled.rowsUnderBar, 'a thread row is scrolled under the bar').toBeGreaterThan(0)
    expect(scrolled.shownThrough, 'the bar is the topmost thing everywhere on it').toEqual([])
    expect(opaque(scrolled.barBackground), `the bar paints opaque, not ${scrolled.barBackground}`).toBe(true)
  })

  test('scrolled to its end, the last row shows whole above the bottom block, and neither block moves', async ({ page }) => {
    await stubThreads(page)
    await gotoRoute(page, 'dashboard')
    await expect(page.getByRole('button', { name: /Rail thread 39/ })).toBeAttached()
    /** The top and bottom blocks' edges, which the chat history's scrolling never moves. */
    const blocks = () => page.evaluate(() => {
      const edges = (sel: string) => {
        const r = document.querySelector(`nav[data-tour="rail"] ${sel}`)!.getBoundingClientRect()
        return [r.top, r.bottom]
      }
      return { top: edges('[data-rail-top]'), bottom: edges('[data-rail-pinned]') }
    })
    const before = await blocks()
    await scrollRail(page, 100_000)
    expect(await blocks(), 'the top and bottom blocks stay where they were').toEqual(before)
    const past = await readRail(page)
    expect(Math.abs(past.barTop - past.areaTop), 'the bar is still pinned to the scroll area\'s top').toBeLessThanOrEqual(1)
    const last = await page.evaluate((sel) => {
      const area = document.querySelector(sel)!
      const rows = [...area.querySelectorAll<HTMLElement>('[data-thread-key]')]
      const row = rows[rows.length - 1]
      const r = row.getBoundingClientRect()
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)
      return { key: row.dataset.threadKey, bottom: r.bottom, fold: area.getBoundingClientRect().bottom, mine: !!hit && row.contains(hit) }
    }, SCROLLER)
    expect(last.key, 'the last row is drawn').toBe(`chat:${key(COUNT - 1)}`)
    expect(last.bottom, 'it ends inside the chat history, above the bottom block').toBeLessThanOrEqual(last.fold + 0.5)
    expect(last.mine, 'and is the topmost thing at its centre').toBe(true)
  })

  test('in a 720 px window the rows pinned to the foot stay whole, ring and all', async ({ page }) => {
    // The sidebar a fresh home draws: two empty projects and no chat. With Settings at the foot of
    // one scroller, that overflowed a 720 px window, and Settings sat in the rail's own bottom
    // padding with the bottom of its ring cut off by the scroller (the surface matrix measured 14 px
    // and 2 px). Forty chats would hide that: focus would scroll Settings into view, clear of the
    // edge.
    await page.setViewportSize({ width: 1280, height: 720 })
    const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
    await page.route('**/api/loops', (route) => route.request().method() === 'GET' ? route.fulfill(json({ loops: [] })) : route.fallback())
    await page.route('**/api/chat/sessions', (route) => route.request().method() === 'GET' ? route.fulfill(json([])) : route.fallback())
    await page.route('**/api/projects', (route) => route.request().method() === 'GET'
      ? route.fulfill(json({ projects: [
        { id: 'e2e-rail-p1', name: 'Personal', status: 'active' },
        { id: 'e2e-rail-p2', name: 'Repeatable', status: 'active' },
      ] }))
      : route.fallback())
    await gotoRoute(page, 'dashboard')
    await expect(page.getByRole('button', { name: /^Repeatable/ })).toBeAttached()
    const settings = page.locator('nav[data-tour="rail"] button[aria-label="Settings"]')
    await settings.focus()
    const geo = await settings.evaluate((el, sel) => {
      const nav = el.closest('nav')!
      const area = document.querySelector<HTMLElement>(sel)!
      const r = el.getBoundingClientRect()
      // Its focus ring: 2 px out and 2 px wide, as the global :focus-visible outline draws it.
      const ring = { top: r.top - 4, bottom: r.bottom + 4, left: r.left - 4, right: r.right + 4 }
      const cut: string[] = []
      for (let a = el.parentElement; a; a = a.parentElement) {
        const cs = getComputedStyle(a)
        if (cs.overflowX === 'visible' && cs.overflowY === 'visible') continue
        const ar = a.getBoundingClientRect()
        if (ring.top < ar.top || ring.bottom > ar.bottom || ring.left < ar.left || ring.right > ar.right) cut.push(`${a.tagName}.${a.className}`.slice(0, 60))
      }
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)
      return {
        cut, mine: !!hit && el.contains(hit), inWindow: ring.bottom <= innerHeight && ring.top >= 0,
        navScrolls: nav.scrollHeight > nav.clientHeight + 1, inArea: area.contains(el),
      }
    }, SCROLLER)
    // Soft, so a run names every one of them that fails.
    expect.soft(geo.cut, 'no ancestor cuts Settings\' focus ring').toEqual([])
    expect.soft(geo.inWindow, 'and the ring is inside the window').toBe(true)
    expect.soft(geo.mine, 'Settings is the topmost thing at its centre').toBe(true)
    expect.soft(geo.inArea, 'Settings is not in the scroll area').toBe(false)
    expect.soft(geo.navScrolls, 'the rail itself does not overflow').toBe(false)
  })

  test('focus moved up a row by the keyboard lands below the bar, never behind it', async ({ page }) => {
    await stubThreads(page)
    await gotoRoute(page, 'dashboard')
    await expect(page.getByRole('button', { name: /Rail thread 39/ })).toBeAttached()
    const before = await readRail(page)
    /** The thread row with the focus: its box, whether it is topmost at its centre, and the bar's. */
    const focused = () => page.evaluate(() => {
      const row = document.activeElement as HTMLElement
      const bar = [...document.querySelectorAll('button')].find((b) => b.textContent?.trim() === 'New thread')!.parentElement!
      const r = row.getBoundingClientRect()
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)
      return { key: row.dataset.threadKey ?? '', top: r.top, barBottom: bar.getBoundingClientRect().bottom, mine: !!hit && row.contains(hit) }
    })
    for (const keyName of ['ArrowUp', 'Shift+Tab']) {
      // The same place each time: scrolled into the threads, the bar stuck, a row under it.
      await page.evaluate((sel) => { document.querySelector(sel)!.scrollTop = 0 }, SCROLLER)
      await scrollRail(page, before.barTop - before.areaTop + 300)
      // The first row whole below the bar, then up one: the row above it is under the bar.
      const first = await page.evaluate(() => {
        const bar = [...document.querySelectorAll('button')].find((b) => b.textContent?.trim() === 'New thread')!.parentElement!
        const edge = bar.getBoundingClientRect().bottom
        return [...document.querySelectorAll<HTMLElement>('[data-thread-key]')].find((r) => r.getBoundingClientRect().top >= edge)!.dataset.threadKey!
      })
      await page.locator(`[data-thread-key="${first}"]`).focus()
      expect((await focused()).key, 'the row below the bar has the focus').toBe(first)
      await page.keyboard.press(keyName)
      const now = await focused()
      expect(now.key, `${keyName} moved the focus to the thread above`).not.toBe(first)
      expect(now.top - RING, `after ${keyName} the focused row and its ring start at or below the bar`).toBeGreaterThanOrEqual(now.barBottom - 1)
      expect(now.mine, `after ${keyName} the focused row is the topmost thing at its centre`).toBe(true)
    }
  })

  test('focus moved down to a row at the fold lands with its ring clear of the fold', async ({ page }) => {
    await stubThreads(page)
    await gotoRoute(page, 'dashboard')
    await expect(page.getByRole('button', { name: /Rail thread 39/ })).toBeAttached()
    // A row wholly in view with its bottom 2 px above the scroll area's fold, so its ring would cross
    // it: the browser scrolls a row the focus moves to only when the room it keeps clear says so.
    const target = key(10)
    await page.evaluate(([sel, k]) => {
      const area = document.querySelector<HTMLElement>(sel)!
      const row = document.querySelector<HTMLElement>(`[data-thread-key="chat:${k}"]`)!
      area.scrollTop += row.getBoundingClientRect().bottom - (area.getBoundingClientRect().bottom - 2)
    }, [SCROLLER, target] as const)
    await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))))
    for (const keyName of ['ArrowDown', 'Tab']) {
      await page.locator(`[data-thread-key="chat:${key(9)}"]`).focus()
      await page.evaluate(([sel, k]) => {
        const area = document.querySelector<HTMLElement>(sel)!
        const row = document.querySelector<HTMLElement>(`[data-thread-key="chat:${k}"]`)!
        area.scrollTop += row.getBoundingClientRect().bottom - (area.getBoundingClientRect().bottom - 2)
      }, [SCROLLER, target] as const)
      await page.keyboard.press(keyName)
      const at = await page.evaluate((sel) => {
        const row = document.activeElement as HTMLElement
        return { key: row.dataset.threadKey ?? '', bottom: row.getBoundingClientRect().bottom, fold: document.querySelector(sel)!.getBoundingClientRect().bottom }
      }, SCROLLER)
      expect(at.key, `${keyName} moved the focus to the row at the fold`).toBe(`chat:${target}`)
      expect(at.bottom + RING, `after ${keyName} the row's ring is clear of the fold`).toBeLessThanOrEqual(at.fold + 0.5)
    }
  })

  test('a thread the address opens above the bar is scrolled to just below it, whole', async ({ page }) => {
    await stubThreads(page)
    await gotoRoute(page, 'dashboard')
    await expect(page.getByRole('button', { name: /Rail thread 39/ })).toBeAttached()
    const before = await readRail(page)
    await scrollRail(page, before.barTop - before.areaTop + 600)

    await page.evaluate((k) => { location.hash = `#/chat/${k}` }, key(0))
    const row = page.locator(`[data-thread-key="chat:${key(0)}"]`)
    await expect(row).toHaveAttribute('aria-current', 'page')
    // The route change runs a view transition, during which the page under its snapshot takes no
    // hit at all: read the row once the transition has settled.
    await page.evaluate(() => (document as unknown as { activeViewTransition?: { finished: Promise<unknown> } }).activeViewTransition?.finished.catch(() => {}))
    await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))))
    const read = async () => {
      const geo = await readRail(page)
      const box = await row.evaluate((el) => {
        const r = el.getBoundingClientRect()
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)
        return { top: r.top, bottom: r.bottom, mine: !!hit && el.contains(hit) }
      })
      return { ...box, barBottom: geo.barBottom }
    }
    // Polled, so a transition still painting its snapshot reads as not yet, never as a failure.
    await expect.poll(async () => (await read()).mine, { message: 'the row is the topmost thing at its centre', timeout: 5_000 }).toBe(true)
    const box = await read()
    expect(box.top, `the row starts at or below the bar (row ${box.top}-${box.bottom}, bar to ${box.barBottom})`).toBeGreaterThanOrEqual(box.barBottom - 1)
  })
})
