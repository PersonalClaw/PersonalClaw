import { type Page, expect } from '@playwright/test'
import type { Theme } from './routes'

// ── Harness helpers ─────────────────────────────────────────────────────────

/** Set the color theme deterministically BEFORE the app boots by seeding
 *  localStorage['mode'] (the key theme.tsx reads) + prefers-color-scheme. */
export async function seedTheme(page: Page, theme: Theme): Promise<void> {
  await page.addInitScript((t) => {
    try { localStorage.setItem('mode', t) } catch { /* ignore */ }
  }, theme)
  await page.emulateMedia({ colorScheme: theme })
}

/** Navigate to a hash route and wait for the shell to settle: no spinner, fonts
 *  loaded, network idle. Returns after the route's chrome is painted. */
export async function gotoRoute(page: Page, route: string): Promise<void> {
  await page.goto(`/#/${route}`)
  // Fonts must be ready or text metrics shift the screenshot.
  await page.evaluate(() => (document as unknown as { fonts?: { ready: Promise<unknown> } }).fonts?.ready)
  // Bounded: routes that poll (agents status, live feeds) NEVER go network-idle,
  // and the default waitForLoadState timeout equals the test timeout — the test
  // would die before the catch fires. 5s settles real loads; pollers fall through.
  await page.waitForLoadState('networkidle', { timeout: 5_000 }).catch(() => { /* long-poll routes never idle; fall through */ })
  // Give the route cross-fade a beat to finish (animations are disabled for the
  // screenshot itself, but the mount still needs to resolve).
  await page.waitForTimeout(400)
}

/** Assert an interaction actually grew the DOM, i.e. the surface really opened.
 *
 *  Cycle 46 deleted the served bundle mid-run and the gateway fell back to its
 *  "dashboard isn't built yet" page — 34 elements, 0 buttons. The axe probe reported
 *  **0 defects on every surface**, which is byte-identical to a clean tree. An absolute
 *  floor plus a growth check is what separates "measured clean" from "measured nothing". */
export async function assertMounted(page: Page, before: number, label: string): Promise<void> {
  const after = await page.evaluate(() => document.querySelectorAll('*').length)
  expect(after, `${label}: only ${after} elements — the app did not render`).toBeGreaterThan(80)
  expect(
    after,
    `${label}: element count did not grow (${before} → ${after}). The opener ran without\n` +
      `opening anything, so a clean axe result here would be meaningless.`,
  ).toBeGreaterThan(before)
}

/** A surface that exists only after an interaction, plus how to reach it.
 *
 *  `open` returns false when its target is absent (no seeded data, a renamed control) so
 *  the caller can skip rather than silently scan the un-opened route. */
export interface Opener {
  label: string
  route: string
  open: (page: Page) => Promise<boolean>
}

/** Click a list row's BODY. Rows are `absolute inset-0 -z-10` overlay buttons under
 *  their own content, so a locator click resolves to the content and Playwright reports
 *  the target as covered; the real user path is a click over the row that bubbles. */
async function clickRowBody(page: Page): Promise<boolean> {
  const row = page.locator('button.absolute.inset-0').first()
  if (!(await row.count())) return false
  const box = await row.boundingBox()
  if (!box) return false
  await page.mouse.click(box.x + Math.min(400, box.width / 2), box.y + box.height / 2)
  return true
}

/** The recipes, all proven by hand in cycles 45/49 before being wired in here. */
export const OPENERS: Opener[] = [
  {
    label: 'command palette',
    route: 'chat',
    open: async (page) => {
      // The app binds Meta+k OR Control+k; Playwright's ControlOrMeta picks per-platform.
      await page.keyboard.press('ControlOrMeta+k')
      return true
    },
  },
  {
    label: 'chat slash menu',
    route: 'chat',
    open: async (page) => {
      // The composer is a CodeMirror contenteditable, invisible to input/textarea.
      const cm = page.locator('[contenteditable="true"]').first()
      if (!(await cm.count())) return false
      await cm.click()
      await cm.pressSequentially('/')
      return true
    },
  },
  { label: 'knowledge peek dock', route: 'knowledge', open: clickRowBody },
  { label: 'inbox peek dock', route: 'inbox', open: clickRowBody },
  {
    label: 'new project modal',
    route: 'projects',
    open: async (page) => {
      const btn = page.getByRole('button', { name: /new project/i }).first()
      if (!(await btn.count())) return false
      await btn.click()
      return true
    },
  },
]

/** Assert a full-page screenshot matches the platform-qualified baseline. */
export async function expectRouteScreenshot(page: Page, name: string): Promise<void> {
  await expect(page).toHaveScreenshot(`${name}.png`, {
    fullPage: true,
    animations: 'disabled',
    // Mask volatile regions (clocks, live counters) if they appear — add
    // selectors here as flaky spots are found.
    mask: [],
  })
}
