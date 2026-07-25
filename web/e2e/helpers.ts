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
