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

/** The app SHELL — `ui/NavRail`, rendered only once the server reports a non-empty
 *  `dashboard.user_name` (`onboarded` is derived from it in `app/identity.tsx`). So its
 *  presence proves three things at once: the gateway answered, the session is
 *  authenticated, and the install is onboarded. Its ABSENCE is the onboarding screen. */
export const SHELL_SELECTOR = 'nav[data-tour="rail"]'

/** Fail the test if we are not looking at the real, onboarded app.
 *
 *  Without a reachable gateway the SPA renders ONBOARDING for every route: no rail, no
 *  page content, no ⌘K listener. axe finds no serious/critical violations there, which is
 *  byte-identical to a genuinely clean route — so 96 route scans reported a pass while
 *  visiting a surface no user ever sees. Only `command palette [opened]` noticed, and it
 *  blamed the palette. This floor makes the harness's own breakage the loud failure.
 *
 *  Every entry in `routes.ts` is shell-bearing. `#/companion` and `?embed=1` deliberately
 *  render WITHOUT a NavRail — adding either to the manifest needs an opt-out here. */
export async function assertShellMounted(page: Page): Promise<void> {
  await expect(
    page.locator(SHELL_SELECTOR),
    `the app shell (${SHELL_SELECTOR}) is not mounted — this is the ONBOARDING screen, not\n` +
      `the route under test. The harness gateway is unreachable, unauthenticated or not\n` +
      `onboarded; any clean result measured here is meaningless. See playwright.config.ts.`,
  ).toBeVisible({ timeout: 10_000 })
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
  // Every caller measures the route it just navigated to; none of them can tell an
  // onboarding hijack from a clean surface on their own.
  await assertShellMounted(page)
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

/** `true` = the recipe ran and the surface should now be open. `{ skip: … }` = its
 *  precondition is absent, and WHY.
 *
 *  A bare boolean forced one hardcoded skip message ("no seeded data on this route") onto
 *  causes that are not the same thing — an empty list, a collapsed row and a renamed control
 *  each need a different next action from whoever reads the report. */
export type OpenResult = true | { skip: string }

/** A surface that exists only after an interaction, plus how to reach it.
 *
 *  `open` returns a skip reason when its target is absent (no seeded data, a renamed
 *  control) so the caller can skip rather than silently scan the un-opened route. */
export interface Opener {
  label: string
  route: string
  open: (page: Page) => Promise<OpenResult>
}

/** Click a list row's BODY. Rows are `absolute inset-0 -z-10` overlay buttons under
 *  their own content, so a locator click resolves to the content and Playwright reports
 *  the target as covered; the real user path is a click over the row that bubbles. */
async function clickRowBody(page: Page): Promise<OpenResult> {
  const row = page.locator('button.absolute.inset-0').first()
  if (!(await row.count())) return { skip: 'no list rows on this route — nothing to peek at (seed data to cover it)' }
  const box = await row.boundingBox()
  if (!box) return { skip: 'the first list row has no box (collapsed or off-screen)' }
  await page.mouse.click(box.x + Math.min(400, box.width / 2), box.y + box.height / 2)
  return true
}

/** The recipes, all proven by hand in cycles 45/49 before being wired in here. */
export const OPENERS: Opener[] = [
  {
    label: 'command palette',
    route: 'chat',
    open: async (page) => {
      // Returns `true` unconditionally, unlike its siblings — deliberately. The ⌘K
      // listener lives on `window`, registered by `app/CommandPalette`, which the SHELL
      // mounts; with no shell the chord changes nothing and the growth floor below fired
      // while naming the palette. `gotoRoute` now asserts the shell FIRST, for all 108
      // tests instead of this one, so by the time we get here the listener provably
      // exists. A growth-floor failure therefore means the palette itself broke — which
      // must fail loudly, not skip.
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
      if (!(await cm.count())) return { skip: 'no contenteditable composer on #/chat' }
      await cm.click()
      await cm.pressSequentially('/')
      // `ui/composer/SlashMenu` FETCHES its command list when it opens (`loadCommands()`)
      // and returns `null` while `results.length === 0` — so the menu can be open in state
      // and absent from the DOM. Under nine parallel workers that fetch outran the spec's
      // 700 ms wait and the run failed the growth floor with the "/" sitting in the live
      // composer, blaming a menu that was working. Waiting for the list is waiting for an
      // async load, not relaxing an assertion: if it never arrives, the floor still fails.
      await cm.page().locator('[role="listbox"]').first()
        .waitFor({ state: 'visible', timeout: 8_000 })
        .catch(() => { /* still absent — assertMounted is what reports that */ })
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
      if (!(await btn.count())) return { skip: 'no "New project" button on #/projects' }
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
