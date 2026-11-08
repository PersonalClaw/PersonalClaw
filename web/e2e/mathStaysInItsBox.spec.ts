import { test, expect, type Page } from '@playwright/test'
import { gotoRoute } from './helpers'
import { idleRunState } from '../src/lib/workPhase'

// ── MATH IN A MESSAGE STAYS IN ITS BOX ──────────────────────────────────────────────────────────
//
// Math is drawn at the sizes it asks for: a rule, a negative space that moves what follows to the
// left, a box raised or lowered with its height taken away (`\smash`). jsdom lays nothing out, so
// `src/ui/mathStaysInItsBox.test.tsx` reads the sizes and the containment each element carries; this
// asks a real browser where the math lands. Each containment case first checks that its math's own
// boxes do reach past the box that holds it, inside what the page shows, so the check is the clip and
// not the page around it. Then no point outside the box's clip edge, on an 8px grid, has its math on
// top, in a quoted message and in a chat reply, and no point of a quoted or thread message's caption
// has the math of the body under it. A long formula is whole, scrolled inside its own display. A
// focused link at a reply's edge, and a focused display, keep their whole ring inside their body's
// clip edge. A body in a scroll box with nothing but a line in it does not scroll. The second block
// lays the bodies out as a browser that reads no clip margin does (its `@supports not` rules, put in
// force here), and holds the same of it. The last, at 390px, has a long code span and a long
// source-numbered link end inside the reply, the number on the link's last line. The messages are
// route stubs, so the layout is the only variable.

const OWNER = 'e2e-owner'
const ITEM_ID = 'C9_math'
const KEY = 'e2e-math-1790000000'

/** A formula wider than any column at these widths, and nothing in it asks for a size. */
const LONG = String.raw`f(x)=\sum_{n=0}^{\infty}\frac{f^{(n)}(a)}{n!}(x-a)^n=f(a)+f'(a)(x-a)+\frac{f''(a)}{2!}(x-a)^2+\frac{f'''(a)}{3!}(x-a)^3+\frac{f''''(a)}{4!}(x-a)^4+\cdots+\frac{f^{(k)}(a)}{k!}(x-a)^k+R_k(x)`

/** Three boxes, each moved past one edge of its message: raised past its top, and lowered past its
 *  foot, each with no height of its own (`\smash`), so the line around it does not grow to hold it;
 *  and, opening a paragraph, moved left past its start by a space, so it straddles the box's left
 *  edge inside the gap the page shows beside it. A space is written in em: KaTeX's px is a TeX
 *  point, about a tenth of an em. */
const RAISED = String.raw`$$\smash{\rule[7em]{2em}{2em}}$$`
const LOWERED = String.raw`$$\smash{\rule[-7em]{2em}{2em}}$$`
const leftBy = (em: number) => String.raw`$$\kern{-${em}em}\rule{2em}{2em}$$`
/** How far each left payload moves, in em: past the box's clip edge, and short of the page's edge. */
const FRAME_LEFT = 1.3
const REPLY_LEFT = 0.6

const MESSAGE = [
  `${leftBy(FRAME_LEFT)} opens the notes.`,
  `Notes ${RAISED} on the numbers.`,
  '$$\n\\rule{4000px}{3000px}\n$$',
  `$$\n${LONG}\n$$`,
  `The last ${LOWERED} line.`,
].join('\n\n')

/** A message whose first line raises a small box onto the caption above its body, as laid out
 *  (`\smash` keeps its line from growing to hold it). */
const ONTO_CAPTION = String.raw`$$\smash{\rule[1.5em]{1em}{1em}}$$ past the top`

const REPLY = `${leftBy(REPLY_LEFT)} opens it.\n\n[The notes](https://example.com/notes) say ${RAISED} and ${LOWERED} and nothing more.`
const HEX = 'a3f9c2e8b1d7406f9e2c5a8b3d1f7e6c4a9b2d8e1f3c7a6b5d4e2f1a9c8b7d6e'
const WIDE = `A digest \`${HEX}\` and [https://example.com/a/very/long/path/that/has/no/break/of/its/own/at/all/anywhere](https://example.com/x) end.`

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })

/** The stubs the cases read. `fetched`, when given, is a page the reply's turn retrieved first, so
 *  the reply's link to it is numbered as its source. */
async function stubTheMessages(page: Page, reply = REPLY, fetched?: string, message = MESSAGE): Promise<void> {
  const item = {
    id: ITEM_ID, channel: 'C9', channel_name: '#ops', thread_ts: null, message, sender_id: 'U9', sender_name: 'Sender',
    classification: 'fyi', confidence: 'high', status: 'pending', created_at: 1790000000, item_kind: 'message',
    can_reply: false, owner_username: 'someone-else',
    // A thread message whose first line raises a box past its body's top, as laid out (\smash keeps
    // its line from growing to hold it).
    thread_context: [{ sender_name: 'Ana', text: ONTO_CAPTION, ts: '1790000001' }],
  }
  await page.route('**/api/inbox/status', (route) => route.fulfill(json({
    enabled: true, health: {}, open_count: 1, total_count: 1, owner: OWNER, my_open_count: 0, my_total_count: 0,
  })))
  await page.route(/\/api\/inbox(\?.*)?$/, (route) =>
    route.request().method() === 'GET' ? route.fulfill(json([item])) : route.fallback())
  // Opening the item marks it seen and opened: answered here, so the shared gateway is written nothing.
  await page.route('**/api/inbox/seen', (route) => route.fulfill(json({ ok: true, seen: 1 })))
  await page.route('**/api/inbox/*/open', (route) => route.fulfill(json({ ok: true })))
  await page.route(`**/api/chat/sessions/${KEY}`, (route) =>
    route.request().method() === 'GET'
      ? route.fulfill(json({
        key: KEY, title: 'Math', run_state: idleRunState(KEY), queue: [], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
        messages: [
          { role: 'user', content: 'What do the notes say?', ts: '2026-11-08T09:00:00+00:00' },
          ...(fetched ? [{ role: 'tool', content: 'web_fetch', ts: '2026-11-08T09:00:10+00:00', meta: { tool_call_id: 'tc-1', purpose: '', input: JSON.stringify({ url: fetched }), kind: 'fetch', done: true, output: 'The notes' } }] : []),
          { role: 'assistant', content: reply, ts: '2026-11-08T09:00:20+00:00' },
        ],
      }))
      : route.fallback())
}

/** Mark `box`'s first element as the box under test, and the nearest scroll box around it. */
async function markBox(page: Page, box: string): Promise<void> {
  await page.locator(box).first().evaluate((el) => {
    el.setAttribute('data-e2e-box', '')
    for (let p = el.parentElement; p; p = p.parentElement) {
      if (/(auto|scroll)/.test(getComputedStyle(p).overflowY)) { p.setAttribute('data-e2e-scroll', ''); return }
    }
    document.documentElement.setAttribute('data-e2e-scroll', '')
  })
}

/** Where each moved box reaches: the edge of the box's clip edge that part of its rule lies past,
 *  inside what the scroll box shows, and whether the page shows that rule at a point of that part. */
async function reaches(page: Page): Promise<Array<{ edge: string; shown: boolean }>> {
  return page.evaluate(() => {
    const host = document.querySelector('[data-e2e-box]')!
    const s = getComputedStyle(host)
    const m = s.overflowX === 'clip' ? parseFloat((String(s.overflowClipMargin || '').match(/[\d.]+px/) ?? ['0'])[0]) || 0 : 0
    const b = host.getBoundingClientRect()
    const v = document.querySelector('[data-e2e-scroll]')!.getBoundingClientRect()
    const seen = { top: Math.max(v.top, 0), left: Math.max(v.left, 0), bottom: Math.min(v.bottom, innerHeight), right: Math.min(v.right, innerWidth) }
    // Just past the clip edge, on each side a box is moved past.
    const e = { top: b.top - m - 2, bottom: b.bottom + m + 2, left: b.left - m - 2 }
    // The middle of a stretch at least 2px long, or null.
    const span = (lo: number, hi: number) => (hi - lo >= 2 ? (lo + hi) / 2 : null)
    const out: Array<{ edge: string; shown: boolean }> = []
    for (const rule of host.querySelectorAll('.katex .katex-rule')) {
      const r = rule.getBoundingClientRect()
      if (r.width > 100) continue // the 10em rule display, which stays inside
      const xIn = span(Math.max(r.left, seen.left), Math.min(r.right, seen.right))
      const yIn = span(Math.max(r.top, seen.top), Math.min(r.bottom, seen.bottom))
      const top = span(Math.max(r.top, seen.top), Math.min(r.bottom, e.top))
      const bottom = span(Math.max(r.top, e.bottom), Math.min(r.bottom, seen.bottom))
      const left = span(Math.max(r.left, seen.left), Math.min(r.right, e.left))
      // A point on the rule itself, past the edge and inside what the page shows.
      const at = xIn !== null && top !== null ? { edge: 'top', x: xIn, y: top }
        : xIn !== null && bottom !== null ? { edge: 'bottom', x: xIn, y: bottom }
        : left !== null && yIn !== null ? { edge: 'left', x: left, y: yIn }
        : null
      const hit = at ? document.elementFromPoint(at.x, at.y) : null
      out.push({ edge: at?.edge ?? 'none', shown: !!hit && !!hit.closest('.katex') && hit.closest('.katex') === rule.closest('.katex') })
    }
    return out
  })
}

/** The points of the viewport outside the box's clip edge (on an 8px grid) where the page's topmost
 *  element is the box's own math, named by the side they are on. */
async function mathOutside(page: Page): Promise<{ outside: string[]; inside: number }> {
  return page.evaluate(() => {
    const host = document.querySelector('[data-e2e-box]')!
    const s = getComputedStyle(host)
    const m = s.overflowX === 'clip' ? parseFloat((String(s.overflowClipMargin || '').match(/[\d.]+px/) ?? ['0'])[0]) || 0 : 0
    const box = host.getBoundingClientRect()
    const r = { top: box.top - m, left: box.left - m, right: box.right + m, bottom: box.bottom + m }
    const outside: string[] = []
    let inside = 0
    for (let x = 4; x < innerWidth; x += 8) {
      for (let y = 4; y < innerHeight; y += 8) {
        // Only the box's own math: another message's math, in a box of its own, is not this one's.
        const hit = document.elementFromPoint(x, y)?.closest('.katex')
        if (!hit || !host.contains(hit)) continue
        if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) { inside++; continue }
        const side = y < r.top ? 'above' : y > r.bottom ? 'below' : x < r.left ? 'left' : 'right'
        outside.push(`${side} ${x},${y}`)
      }
    }
    return { outside, inside }
  })
}

/** How much room `el`'s focus ring has inside the clip edge of the body around it, on each side. */
async function ringRoom(page: Page, el: ReturnType<Page['locator']>, body: string) {
  await page.keyboard.press('Shift')
  await el.focus()
  return el.evaluate((node, bodySel) => {
    const s = getComputedStyle(node)
    const out = parseFloat(s.outlineWidth) + parseFloat(s.outlineOffset)
    const a = node.getBoundingClientRect()
    const root = node.closest(bodySel)!
    const rs = getComputedStyle(root)
    // The clip edge: the box, and its clip margin where the browser reads one.
    const m = parseFloat((String(rs.overflowClipMargin || '').match(/[\d.]+px/) ?? ['0'])[0]) || 0
    const b = root.getBoundingClientRect()
    return {
      visible: node.matches(':focus-visible'), style: s.outlineStyle, offset: parseFloat(s.outlineOffset), out,
      room: { left: a.left - out - (b.left - m), top: a.top - out - (b.top - m), right: b.right + m - (a.right + out), bottom: b.bottom + m - (a.bottom + out) },
    }
  }, body)
}

const REPLY_BODY = '[data-transcript-scroll] div.text-on-surface'

/** For the body right after `caption`: how many of its rules lie on the caption as laid out (`onto`, 2px or more on both axes), and at how many of the caption's points its math is topmost (`hits`). */
async function bodyMathOn(caption: ReturnType<Page['locator']>): Promise<{ onto: number; hits: number }> {
  return caption.evaluate((el) => {
    const r = el.getBoundingClientRect()
    const body = el.nextElementSibling
    // The body's boxes that lie on the caption, as laid out: 2px of it or more on both axes.
    const onto = Array.from(body?.querySelectorAll('.katex .katex-rule') ?? []).map((k) => k.getBoundingClientRect())
      .filter((k) => Math.min(k.right, r.right) - Math.max(k.left, r.left) >= 2 && Math.min(k.bottom, r.bottom) - Math.max(k.top, r.top) >= 2).length
    let hits = 0
    for (let x = Math.ceil(r.left); x < r.right; x++) for (let y = Math.ceil(r.top); y < r.bottom; y++) {
      // Only this body's math: another message's, in a box of its own, is not this one's.
      const k = document.elementFromPoint(x, y)?.closest('.katex')
      if (k && body?.contains(k)) hits++
    }
    return { onto, hits }
  })
}

async function aThreadMessageStaysInItsBody(page: Page): Promise<void> {
  const caption = page.getByText('Ana', { exact: true })
  await expect(caption).toBeVisible()
  await page.evaluate(() => document.fonts.ready)
  // The raised box does lie on the caption, as laid out: the clip is what keeps it off.
  const { onto, hits } = await bodyMathOn(caption)
  expect(onto, "the body's boxes that lie on its caption").toBeGreaterThan(0)
  expect(hits, 'points of the caption where its body\'s math is topmost').toBe(0)
}

/** The quoted frame's own caption, over a message whose first line raises a box onto it. The page's
 *  stubs are replaced first: a later route wins over the one the block set up. */
async function aQuotedMessagesCaptionStaysClear(page: Page, noClipMargin = false): Promise<void> {
  await stubTheMessages(page, REPLY, undefined, ONTO_CAPTION)
  await gotoRoute(page, `inbox?open=${ITEM_ID}`)
  if (noClipMargin) await asIfNoClipMargin(page)
  const frame = page.getByRole('group', { name: /quoted, not your own$/ }).first()
  await expect(frame.locator('.katex').first()).toBeVisible()
  await page.evaluate(() => document.fonts.ready)
  const { onto, hits } = await bodyMathOn(frame.locator('[data-type=caption]').first())
  expect(onto, "the body's boxes that lie on its caption").toBeGreaterThan(0)
  expect(hits, "points of the caption where its body's math is topmost").toBe(0)
}

async function aDisplaysRingStaysInItsBody(page: Page): Promise<void> {
  const display = page.getByRole('group', { name: 'Formula' }).first()
  await expect(display).toBeVisible()
  // Found by the class the body carried before it clipped, so a body with no clip fails on the room.
  const m = await ringRoom(page, display, 'div.text-on-surface')
  expect(m.visible && m.style !== 'none', 'the display shows its ring').toBe(true)
  expect(m.offset, 'the ring stands outside the display, over nothing of the formula').toBeGreaterThanOrEqual(0)
  for (const [side, room] of Object.entries(m.room)) expect(room, `room for the ring on the ${side}`).toBeGreaterThanOrEqual(-0.5)
}

async function aLinksRingStaysInItsReply(page: Page): Promise<void> {
  const reply = page.locator(REPLY_BODY).filter({ hasText: 'nothing more' }).last()
  const link = reply.getByRole('link', { name: 'The notes' })
  await expect(link).toBeVisible()
  const m = await ringRoom(page, link, REPLY_BODY)
  expect(m.visible && m.style !== 'none', 'the link shows its ring').toBe(true)
  expect(m.out, 'the ring stands out from the link').toBeGreaterThan(0)
  for (const [side, room] of Object.entries(m.room)) expect(room, `room for the ring on the ${side}`).toBeGreaterThanOrEqual(-0.5)
}

/** How far a scroll box holding a body with one line scrolls, bare and with a padding of its own. */
async function oneLineScrolls(page: Page): Promise<{ bare: { x: number; y: number }; padded: { x: number; y: number } }> {
  const reply = page.locator(REPLY_BODY).filter({ hasText: 'nothing more' }).last()
  await expect(reply).toBeVisible()
  return reply.evaluate((root) => {
    const box = (padding: string) => {
      const host = document.createElement('div')
      host.style.cssText = `max-height:10rem;overflow:auto;width:20rem;padding:${padding}`
      const body = root.cloneNode(false) as HTMLElement
      body.innerHTML = '<p>One line.</p>'
      host.append(body)
      document.body.append(host)
      const r = { x: host.scrollWidth - host.clientWidth, y: host.scrollHeight - host.clientHeight }
      host.remove()
      return r
    }
    return { bare: box('0'), padded: box('4px') }
  })
}

test.describe('math in a message stays in its box', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 1400 })
    await stubTheMessages(page)
  })

  test('in a quoted message, nothing it draws lands outside the frame', async ({ page }) => {
    await gotoRoute(page, `inbox?open=${ITEM_ID}`)
    const frame = page.getByRole('group', { name: /quoted, not your own$/ }).first()
    await expect(frame.locator('.katex').first()).toBeVisible()
    await page.evaluate(() => document.fonts.ready)
    await markBox(page, '[role="group"][aria-label$="quoted, not your own"]')
    const moved = await reaches(page)
    // Each moved box does reach past an edge of the frame, where the page shows it: the clip is what
    // keeps it in, not the panel around the frame. Soft, so a run records the grid below as well.
    expect.soft(moved.map((m) => m.edge).sort(), 'one box past each edge').toEqual(['bottom', 'left', 'top'])
    expect.soft(moved.filter((m) => m.shown), 'a moved box the page shows past the frame').toEqual([])
    const { outside, inside } = await mathOutside(page)
    expect(inside, 'the math is drawn, inside the frame').toBeGreaterThan(0)
    expect(outside, 'points outside the frame where its math is topmost').toEqual([])
  })

  test('in a thread message, nothing it draws lands on the caption above its body', async ({ page }) => {
    await gotoRoute(page, `inbox?open=${ITEM_ID}`)
    await aThreadMessageStaysInItsBody(page)
  })

  test('in a quoted message, nothing it draws lands on the caption above its body', async ({ page }) => {
    await aQuotedMessagesCaptionStaysClear(page)
  })

  test('a long formula is whole, scrolled inside its own display', async ({ page }) => {
    await gotoRoute(page, `inbox?open=${ITEM_ID}`)
    const display = page.getByRole('group', { name: 'Formula' }).last()
    await expect(display).toBeVisible()
    await page.evaluate(() => document.fonts.ready)
    await display.scrollIntoViewIfNeeded()
    // None of its height is cut: every glyph it draws sits within the display's own top and bottom.
    const cut = await display.evaluate((el) => {
      const box = el.getBoundingClientRect()
      const bottom = box.top + el.clientHeight
      return Array.from(el.querySelectorAll('.katex-html *'))
        .filter((g) => g.childElementCount === 0 && (g.textContent ?? '').trim())
        .map((g) => ({ text: g.textContent, r: g.getBoundingClientRect() }))
        .filter(({ r }) => r.top < box.top - 0.5 || r.bottom > bottom + 0.5)
        .map(({ text, r }) => `${text} ${Math.round(r.top - box.top)}..${Math.round(r.bottom - bottom)}`)
    })
    expect(cut, 'glyphs the display cuts above or below').toEqual([])
    const m = await display.evaluate((el) => {
      const box = el.getBoundingClientRect()
      const wide = el.scrollWidth > el.clientWidth
      el.scrollLeft = el.scrollWidth
      // The drawn formula: the last glyph of its visible half, scrolled to its end.
      const glyphs = Array.from(el.querySelectorAll('.katex-html *')).filter((g) => g.childElementCount === 0 && (g.textContent ?? '').trim())
      if (!glyphs.length) throw new Error(`no drawn glyphs in the display: ${el.innerHTML.slice(0, 200)}`)
      const last = glyphs[glyphs.length - 1].getBoundingClientRect()
      return { wide, lastRight: last.right, right: box.right, left: box.left }
    })
    expect(m.wide, 'the formula is wider than its column').toBe(true)
    expect(m.lastRight, 'its end scrolls into its box').toBeLessThanOrEqual(m.right + 1)
    expect(m.lastRight).toBeGreaterThan(m.left)
  })

  test("a focused display's ring is outside it and inside its body's clip edge", async ({ page }) => {
    await gotoRoute(page, `inbox?open=${ITEM_ID}`)
    await aDisplaysRingStaysInItsBody(page)
  })

  test("in a chat reply, nothing it draws lands outside the reply's clip edge", async ({ page }) => {
    await gotoRoute(page, `chat/${KEY}`)
    const reply = page.locator(REPLY_BODY).filter({ hasText: 'nothing more' }).last()
    await expect(reply.locator('.katex')).toHaveCount(3)
    await page.evaluate(() => document.fonts.ready)
    await reply.evaluate((el) => el.setAttribute('data-e2e-reply', ''))
    await markBox(page, '[data-e2e-reply]')
    // The sidebar's resize grip is a hit area with no paint of its own (its band is a pseudo-element),
    // over the transcript's left padding: it takes no hit here, so a point there says what the page draws.
    await page.addStyleTag({ content: '.cursor-col-resize, .cursor-col-resize::before { pointer-events: none !important; }' })
    const moved = await reaches(page)
    expect.soft(moved.map((m) => m.edge).sort(), 'one box past each edge').toEqual(['bottom', 'left', 'top'])
    expect.soft(moved.filter((m) => m.shown), 'a moved box the page shows past the reply').toEqual([])
    const { outside, inside } = await mathOutside(page)
    expect(inside, 'the math is drawn, inside the reply').toBeGreaterThan(0)
    expect(outside, 'points outside the reply where its math is topmost').toEqual([])
  })

  test("a focused link at a reply's edge has its whole ring inside the reply's clip edge", async ({ page }) => {
    await gotoRoute(page, `chat/${KEY}`)
    await aLinksRingStaysInItsReply(page)
  })

  test('a body with one line in a scroll box of its size does not scroll', async ({ page }) => {
    await gotoRoute(page, `chat/${KEY}`)
    test.skip(!(await page.evaluate(() => CSS.supports('overflow-clip-margin: 4px'))), 'a browser that reads no clip margin is the next block')
    // A body keeps its own box where the clip margin is read, so even a box with no padding does not.
    expect(await oneLineScrolls(page)).toEqual({ bare: { x: 0, y: 0 }, padded: { x: 0, y: 0 } })
  })
})

/** Lay the page's bodies out as a browser that reads no clip margin does: no clip margin, and the
 *  rules the stylesheet keeps for that browser (`@supports not (overflow-clip-margin: …)`) in force. */
async function asIfNoClipMargin(page: Page): Promise<void> {
  await page.evaluate(() => {
    const rules: CSSRule[] = []
    const walk = (list: CSSRuleList) => {
      for (const r of Array.from(list)) {
        if (r instanceof CSSSupportsRule && /not\s*\(\s*overflow-clip-margin/.test(r.conditionText)) rules.push(...Array.from(r.cssRules))
        else if (r instanceof CSSGroupingRule) walk(r.cssRules)
      }
    }
    for (const sheet of Array.from(document.styleSheets)) {
      try { walk(sheet.cssRules) } catch { /* a sheet from another origin, which holds none of these */ }
    }
    if (!rules.length) throw new Error('the stylesheet holds no rules for a browser that reads no clip margin')
    const style = document.createElement('style')
    style.textContent = ['.md-clip { overflow-clip-margin: 0px; }', ...rules.map((r) => r.cssText)].join('\n')
    document.head.append(style)
    // The rules are in force: a body reads no clip margin, and holds each value they give it.
    const body = document.querySelector('.md-clip')
    if (!body) throw new Error('no body on the page to lay out')
    const now = getComputedStyle(body)
    // A browser that reads no clip margin has no value for it at all, which is none.
    const margin = now.getPropertyValue('overflow-clip-margin')
    if ((parseFloat(margin) || 0) !== 0) throw new Error(`a body still reads a clip margin: ${margin}`)
    for (const r of rules) {
      if (!(r instanceof CSSStyleRule) || r.selectorText !== '.md-clip') continue
      for (const prop of Array.from(r.style)) {
        const want = parseFloat(r.style.getPropertyValue(prop)), got = parseFloat(now.getPropertyValue(prop))
        if (!Number.isNaN(want) && want !== got) throw new Error(`a body's ${prop} is ${now.getPropertyValue(prop)}, not the ${r.style.getPropertyValue(prop)} its rules give`)
      }
    }
  })
}

test.describe('where the clip margin is not read', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 1400 })
    await stubTheMessages(page)
  })

  test('in a thread message, nothing it draws lands on the caption above its body', async ({ page }) => {
    await gotoRoute(page, `inbox?open=${ITEM_ID}`)
    await asIfNoClipMargin(page)
    await aThreadMessageStaysInItsBody(page)
  })

  test('in a quoted message, nothing it draws lands on the caption above its body', async ({ page }) => {
    await aQuotedMessagesCaptionStaysClear(page, true)
  })

  test("a focused display's ring is outside it and inside its body's clip edge", async ({ page }) => {
    await gotoRoute(page, `inbox?open=${ITEM_ID}`)
    await asIfNoClipMargin(page)
    await aDisplaysRingStaysInItsBody(page)
  })

  test("a focused link at a reply's edge has its whole ring inside the reply's clip edge", async ({ page }) => {
    await gotoRoute(page, `chat/${KEY}`)
    await asIfNoClipMargin(page)
    await aLinksRingStaysInItsReply(page)
  })

  test('a body with one line scrolls in a bare scroll box, and not in one with a padding of its own', async ({ page }) => {
    await gotoRoute(page, `chat/${KEY}`)
    await asIfNoClipMargin(page)
    const m = await oneLineScrolls(page)
    // A scroll box with a padding of its own does not scroll in this layout either.
    expect(m.padded).toEqual({ x: 0, y: 0 })
    expect(m.bare, 'a bare box scrolls by the room the body keeps for a ring').not.toEqual({ x: 0, y: 0 })
  })
})

test.describe('text with no break of its own wraps inside a narrow reply', () => {
  test("a long code span and a long link end inside the reply, the link's source number on its last line", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 900 })
    // The turn fetched the page the link names, so the link is numbered as its source.
    await stubTheMessages(page, WIDE, 'https://example.com/x')
    await gotoRoute(page, `chat/${KEY}`)
    const reply = page.locator(REPLY_BODY).filter({ hasText: 'A digest' }).last()
    await expect(reply.locator('sup[data-source-n]')).toBeVisible()
    const m = await reply.evaluate((root) => {
      const edge = root.getBoundingClientRect().right
      const ends = (el: Element | null) => (el ? Math.max(...Array.from(el.getClientRects(), (r) => r.right)) : Infinity)
      const link = root.querySelector('a')!
      const rects = Array.from(link.getClientRects())
      const last = rects[rects.length - 1]
      const sup = root.querySelector('sup[data-source-n]')!.getBoundingClientRect()
      return { edge, code: ends(root.querySelector('code')), link: ends(link), lines: rects.length, lastTop: last.top, lastBottom: last.bottom, sup: { top: sup.top, bottom: sup.bottom, left: sup.left }, lastRight: last.right }
    })
    expect(m.code, 'the code span ends inside the reply').toBeLessThanOrEqual(m.edge + 0.5)
    expect(m.link, 'the link ends inside the reply').toBeLessThanOrEqual(m.edge + 0.5)
    expect(m.lines, 'the link wraps').toBeGreaterThan(1)
    // The number sits on the link's last line, right after it, not alone on a line of its own.
    expect(m.sup.top, "the number's top is on the link's last line").toBeGreaterThanOrEqual(m.lastTop - 8)
    expect(m.sup.bottom, "the number's foot is on the link's last line").toBeLessThanOrEqual(m.lastBottom + 1)
    expect(m.sup.left, 'the number follows the link').toBeGreaterThanOrEqual(m.lastRight - 0.5)
  })
})
