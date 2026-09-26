import { test, expect, type Page } from '@playwright/test'
import { gotoRoute } from './helpers'

// ── THE CHAT HEADER NEVER OVERLAPS ITSELF, AT ANY WIDTH ─────────────────────────────────────────
//
// The chat header is the busiest header in the product: a back button, the chat's title and its
// regenerate affordance, up to six chips about the conversation (screen share, the app that started
// it, the project, where it was branched from, what it investigates, what it has cost), and a
// cluster of nine controls — all between two shell corners that float over the same band. #3632's
// lane measured the row full below ~1300px, and this rail measured the rest, on `0b487d9c7`, with a
// branched chat whose title and parent title are both long and whose cost chip is showing:
//
//   • it overlapped at EVERY width, 1440 included. "Branched from <parent title>" had no bound, so
//     at 1440 it painted from x=464 to x=1312 — under all eight cluster controls and the shell
//     corner — and "Copy chat link" was pushed to x=1318, past the viewport at 1024 and below.
//   • the chat's own title was the ONE thing in the row that could shrink, so it shrank to 8px at
//     every width: the conversation lost its name before any chip gave an inch.
//   • at 320 and 390 the band between the corners is 85 and 155px, less than the cluster's floor
//     (two mode pills that never overflow, plus `…`) — the back button sat under the Task pill.
//
// jsdom lays nothing out (every rect is zero), so no vitest rail can see any of that. This one asks
// a real browser the PROPERTY at each width: every control and chip in the header, and every shell
// corner control over it, is fully painted (no ancestor clips it), inside the viewport, the topmost
// thing at its own centre, and clear of every other one. The chips are pinned by stubbing the
// session's detail and cost — the same shape a real branched, app-started, used chat returns — so
// that geometry is the only variable, as `onboardingGeometry.spec.ts` pins its import scan.

/** The widths the header must survive: the brief's five, plus the phone contract `walkthrough`
 *  uses (390) and the desktop the goldens use (1280). */
const WIDTHS = [320, 390, 500, 700, 1024, 1280, 1440] as const

const KEY = 'chat-geo-1790000000'
const TITLE = 'Planning the quarterly product launch across marketing, engineering and support with every dependency spelled out'
const PARENT_TITLE = 'The original launch plan, before anyone asked what a two-person team would do with it'
const APP_NAME = 'Probe Alpha'

/** A branched conversation an app started, with turns and a priced cost — every chip at once. */
const DETAIL = {
  key: KEY,
  title: TITLE,
  messages: [
    { role: 'user', content: 'Plan the launch week in detail.', ts: '2026-09-26T09:00:00+00:00' },
    { role: 'assistant', content: 'Here is the plan, day by day.', ts: '2026-09-26T09:00:20+00:00' },
  ],
  running: false,
  queue: [],
  task_mode: 'agent',
  approval: 'normal',
  memory_mode: 'persistent',
  forked_from: 'dashboard:chat-geo-parent',
  forked_from_title: PARENT_TITLE,
  created_by_app: 'probe-alpha',
  created_by_app_name: APP_NAME,
  app_auto_approves: false,
}

const USAGE = {
  since: '', until: '', session: KEY,
  totals: { input_tokens: 41_000, output_tokens: 5_000, cache_read_tokens: 0, cache_creation_tokens: 0, cost_usd: 0.0123, turns: 3, priced: true },
}

async function pinTheChat(page: Page): Promise<void> {
  await page.route(`**/api/chat/sessions/${KEY}`, (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(DETAIL) })
      : route.fallback())
  await page.route(`**/api/usage/totals?*`, (route) =>
    new URL(route.request().url()).searchParams.get('session') === KEY
      ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(USAGE) })
      : route.fallback())
}

interface Atom {
  name: string
  left: number
  right: number
  top: number
  bottom: number
  /** Pixels of its own box an ancestor's overflow cuts off. */
  clipped: number
  /** What `elementFromPoint` finds at its centre, when that is not the atom itself. */
  occludedBy: string
  opacity: number
}

interface HeaderGeometry {
  viewport: number
  atoms: Atom[]
  overlaps: string[]
  /** The title's painted width — the chat's name must survive every width. */
  titleWidth: number
  startedBy: Atom | null
  /** Every accessible name / tooltip in the header, so "the full text is available" is checkable. */
  names: string[]
}

/** Read the header in ONE evaluate, so every number shares a layout pass. */
async function readHeader(page: Page): Promise<HeaderGeometry> {
  return page.evaluate(() => {
    const header = document.querySelector('header')
    if (!header) throw new Error('no <header> on the chat page')
    const hiddenSubtree = (el: Element) => !!el.closest('[aria-hidden="true"], [inert]')
    const INTERACTIVE = 'button, a[href], [role="button"]'
    // The shell corners float over the header's band; a control under one is a control lost.
    const corners = [
      document.querySelector('button[aria-label="Collapse sidebar"], button[aria-label="Expand sidebar"]')?.closest('div.absolute'),
      document.querySelector('button[aria-label="Open terminal"], button[aria-label="Hide terminal"]')?.closest('div.absolute'),
    ].filter((c): c is Element => !!c)
    const scopes = [header, ...corners]
    const candidates = scopes.flatMap((s) =>
      Array.from(s.querySelectorAll<HTMLElement>(`${INTERACTIVE}, [data-type="title-l"], .rounded-pill`)))
    // An atom is a control (not nested in another control), or a non-interactive chip or title that
    // sits in no control. That keeps a badge inside the bell, or the title inside its rename button,
    // from standing in for the control that contains it.
    const atoms = candidates.filter((el) => {
      if (hiddenSubtree(el)) return false
      const cs = getComputedStyle(el)
      if (cs.visibility === 'hidden' || cs.display === 'none' || Number(cs.opacity) === 0) return false
      const outer = el.parentElement?.closest(INTERACTIVE)
      return !outer || !scopes.some((s) => s.contains(outer))
    })
    const nameOf = (el: Element) =>
      (el.getAttribute('aria-label') || el.getAttribute('title') || el.textContent || el.tagName)
        .trim().replace(/\s+/g, ' ').slice(0, 60)
    const visibleBox = (el: Element) => {
      const r = el.getBoundingClientRect()
      let { left, right, top, bottom } = r
      for (let a = el.parentElement; a; a = a.parentElement) {
        const cs = getComputedStyle(a)
        if (cs.overflowX !== 'visible' || cs.overflowY !== 'visible') {
          const ar = a.getBoundingClientRect()
          left = Math.max(left, ar.left); right = Math.min(right, ar.right)
          top = Math.max(top, ar.top); bottom = Math.min(bottom, ar.bottom)
        }
      }
      return { r, left, right, top, bottom }
    }
    const measured = atoms.map((el) => {
      const { r, left, right, top, bottom } = visibleBox(el)
      const w = Math.max(0, right - left), h = Math.max(0, bottom - top)
      const hit = w > 1 && h > 1 ? document.elementFromPoint((left + right) / 2, (top + bottom) / 2) : null
      const mine = !!hit && (hit === el || el.contains(hit))
      // What the eye gets: the element's opacity times every ancestor's, so a parent mid-fade
      // (the `…` springs in) cannot pass as painted.
      let opacity = 1
      for (let a: Element | null = el; a; a = a.parentElement) opacity *= Number(getComputedStyle(a).opacity)
      return {
        el,
        atom: {
          name: nameOf(el),
          left: Math.round(r.left), right: Math.round(r.right), top: Math.round(r.top), bottom: Math.round(r.bottom),
          clipped: Math.round(Math.max(r.width - w, r.height - h)),
          occludedBy: mine ? '' : hit ? nameOf(hit) : '(nothing painted)',
          opacity: Math.round(opacity * 100) / 100,
        },
        box: { left, right, top, bottom },
      }
    })
    const overlaps: string[] = []
    for (let i = 0; i < measured.length; i++) {
      for (let j = i + 1; j < measured.length; j++) {
        const a = measured[i].box, b = measured[j].box
        const ix = Math.min(a.right, b.right) - Math.max(a.left, b.left)
        const iy = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top)
        if (ix > 1 && iy > 1) overlaps.push(`"${measured[i].atom.name}" × "${measured[j].atom.name}" (${Math.round(ix)}px)`)
      }
    }
    const title = header.querySelector('[data-type="title-l"]')
    const started = measured.find((m) => /^Started by /.test((m.el.textContent || '').trim()))
    return {
      viewport: window.innerWidth,
      atoms: measured.map((m) => m.atom),
      overlaps,
      titleWidth: title ? Math.round(title.getBoundingClientRect().width) : 0,
      startedBy: started ? started.atom : null,
      names: Array.from(header.querySelectorAll('[aria-label], [title]'))
        .filter((el) => !hiddenSubtree(el))
        .flatMap((el) => [el.getAttribute('aria-label') || '', el.getAttribute('title') || '']),
    }
  })
}

/** Two identical reads in a row: the cluster measures itself with a ResizeObserver and its `…`
 *  springs in, so a single read can land mid-settle and report a transient overlap. */
async function settledHeader(page: Page): Promise<HeaderGeometry> {
  let prev = ''
  for (let i = 0; i < 20; i++) {
    const g = await readHeader(page)
    const sig = JSON.stringify(g.atoms)
    if (sig === prev) return g
    prev = sig
    await page.waitForTimeout(150)
  }
  return readHeader(page)
}

test.describe('the chat header fits at every width', () => {
  for (const width of WIDTHS) {
    test(`${width}px — nothing overlaps, nothing is hidden, and it names the app`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      await pinTheChat(page)
      await gotoRoute(page, `chat/${KEY}`)
      await expect(page.locator('header [data-type="title-l"]'), 'the chat never opened').toHaveText(TITLE)
      const g = await settledHeader(page)
      expect(g.viewport, 'the viewport did not apply').toBe(width)

      const report = g.atoms.map((a) => `  [${a.left}..${a.right}] ${a.name}`).join('\n')
      expect(g.overlaps, `header boxes overlap at ${width}px:\n${report}`).toEqual([])
      for (const a of g.atoms) {
        expect(a.clipped, `"${a.name}" is partly cut off by an ancestor at ${width}px:\n${report}`).toBeLessThanOrEqual(1)
        expect(a.occludedBy, `"${a.name}" is covered at its centre at ${width}px:\n${report}`).toBe('')
        expect(a.left, `"${a.name}" starts off-screen at ${width}px`).toBeGreaterThanOrEqual(0)
        expect(a.right, `"${a.name}" runs off-screen at ${width}px`).toBeLessThanOrEqual(width)
        // 0.4 is the house's disabled dim — in an app's chat the Permission pill wears it on
        // purpose (#3632: the app's grant decides, so the pill is not yours to move). Anything
        // fainter is not a control anyone is being shown.
        expect(a.opacity, `"${a.name}" is painted at ${a.opacity} opacity at ${width}px`).toBeGreaterThanOrEqual(0.4)
      }
      // The conversation keeps its name: the title may truncate, never vanish.
      expect(g.titleWidth, `the chat's title is ${g.titleWidth}px wide at ${width}px`).toBeGreaterThanOrEqual(40)

      // "Started by <App>", in the header, where you look — not only in the history row.
      expect(g.startedBy, `no "Started by ${APP_NAME}" in the chat header at ${width}px:\n${report}`).not.toBeNull()
      expect(g.startedBy?.opacity, `"Started by ${APP_NAME}" is not fully painted at ${width}px`).toBe(1)
      // A chip may truncate; its full text must still be there for the pointer and the reader.
      expect(g.names.some((n) => n.includes(PARENT_TITLE)), 'the branch chip lost its full parent title').toBe(true)
      expect(g.names.some((n) => n.includes(APP_NAME)), 'the app chip lost the app name').toBe(true)
    })
  }
})
