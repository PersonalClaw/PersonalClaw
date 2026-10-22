import { test, expect, type Page } from '@playwright/test'
import { gotoRoute, openTheInspectedChat } from './helpers'

// ── The session inspector and the detail tile, as a browser lays them out ──────────────────────
//
// #3820's Oct 18 amendment: "The inspector and detail tile may coexist with the navigation rail on a
// wide screen … At compact widths the inspector becomes a drawer and the selected detail stacks or
// replaces it with a deterministic Back path." The UI quality matrix reads the inspector open, but
// its Escape (which closes whatever the keyboard walk opened) closes the rail before the widths are
// measured, so the boxes are held here: on a wide screen the thread, the tile and the inspector sit
// side by side inside the window; on a phone the inspector is the drawer, each section's header a
// 44 px target, and a detail takes its place with a Back to Activity that brings it back. The chat
// is the route-stubbed one the matrix opens (`openTheInspectedChat`).

type Box = { left: number; right: number; top: number; bottom: number; width: number; height: number }

async function boxes(page: Page) {
  return page.evaluate(() => {
    const box = (el: Element | null): Box | null => {
      if (!el) return null
      const r = el.getBoundingClientRect()
      return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height }
    }
    return {
      viewport: window.innerWidth,
      layout: document.querySelector('[data-pane-host="chat"]')?.getAttribute('data-pane-layout') ?? null,
      pageWidth: document.documentElement.scrollWidth,
      thread: box(document.querySelector('[data-transcript-scroll]')),
      tile: box(document.querySelector('[data-pane-host="chat"] section[aria-labelledby]')),
      inspector: box(document.querySelector('[data-inspector]')?.closest('[role="region"]') ?? null),
      headers: [...document.querySelectorAll('[data-inspector] section > div > button')].map((b) => box(b)!.height),
    }
  })
}

async function open(page: Page, width: number, query: string) {
  await page.setViewportSize({ width, height: width > 768 ? 900 : 800 })
  await gotoRoute(page, 'chat')
  expect(await openTheInspectedChat(page, query)).toBe(true)
  await page.waitForTimeout(600)
}

test.describe('on a wide screen', () => {
  for (const width of [1440, 1024]) {
    test(`${width}px — the thread, the tile and the inspector share the window, none over another`, async ({ page }) => {
      await open(page, width, '?activity=1&beside=helper:aaaa1111')
      await expect(page.getByRole('region', { name: 'Read up on LSM trees' })).toBeVisible()
      const g = await boxes(page)
      const report = JSON.stringify(g)
      expect(g.inspector, report).not.toBeNull()
      expect(g.tile, report).not.toBeNull()
      expect(g.inspector!.right, `the inspector runs past the window: ${report}`).toBeLessThanOrEqual(g.viewport)
      expect(g.tile!.right, `the tile overlaps the inspector: ${report}`).toBeLessThanOrEqual(g.inspector!.left + 1)
      // Beside the thread where the room left by the inspector holds both, and under it where it
      // does not (the pane host's own rule): either way neither covers the other.
      if (g.layout === 'beside') expect(g.thread!.right, `the thread overlaps the tile: ${report}`).toBeLessThanOrEqual(g.tile!.left + 1)
      else expect(g.tile!.top, `the stacked tile is not under the thread: ${report}`).toBeGreaterThanOrEqual(g.thread!.bottom - 1)
      if (width === 1440) expect(g.layout, `1440 px holds the tile beside the thread: ${report}`).toBe('beside')
      expect(g.pageWidth, `the page runs past the window: ${report}`).toBeLessThanOrEqual(g.viewport)
    })
  }
})

test.describe('on a phone', () => {
  for (const width of [320, 390, 412]) {
    test(`${width}px — the inspector is the drawer, and a detail takes its place with a Back`, async ({ page }) => {
      await open(page, width, '?activity=1')
      const g = await boxes(page)
      const report = JSON.stringify(g)
      expect(g.inspector, report).not.toBeNull()
      expect(g.inspector!.left, report).toBeGreaterThanOrEqual(0)
      expect(g.inspector!.right, `the drawer runs past the window: ${report}`).toBeLessThanOrEqual(g.viewport)
      expect(g.headers.length, report).toBeGreaterThan(5)
      for (const h of g.headers) expect(h, `a section header is under 44 px: ${report}`).toBeGreaterThanOrEqual(44)
      expect(page.getByRole('button', { name: /^Move .* (up|down)$/ }), 'the drawer is not reordered').toHaveCount(0)
      expect(g.pageWidth, `the page runs past the window: ${report}`).toBeLessThanOrEqual(g.viewport)
      // The row's hit target sits under its content; a click on the row is how a person opens it.
      await page.getByRole('button', { name: 'Open Launch plan', exact: true }).locator('..').click()
      await expect(page.getByRole('button', { name: 'Back to Activity' })).toBeVisible()
      await expect(page.locator('[data-inspector]'), 'the detail stands in for the inspector').toHaveCount(0)
      const t = await boxes(page)
      expect(t.tile!.right, JSON.stringify(t)).toBeLessThanOrEqual(t.viewport)
      await page.getByRole('button', { name: 'Back to Activity' }).click()
      await expect(page.locator('[data-inspector]')).toHaveCount(1)
      expect(page.url()).not.toMatch(/[?&]beside=/)
    })
  }
})
