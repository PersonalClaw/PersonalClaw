import { test, expect } from '@playwright/test'
import { SMOKE_ROUTES, renderProblems, withoutAGateway } from './smokeChecks'

// ── The render smoke: the BUILT bundle mounts in a real browser, route by route ────────────────
//
// The first project of every run, and the browser half of the local pre-push gate. Its checks and
// the reason for each are in `smokeChecks.ts`. No gateway is reached: the page's API is answered
// in the page, so a green here is a statement about the bundle and nothing behind it.

test.describe('the built bundle mounts', () => {
  test.use({ storageState: { cookies: [], origins: [] } })
  // The budget the standalone smoke gave each route: a 30 s load, a 15 s mount and a settle.
  // Playwright's default 30 s per test is less than the load alone.
  test.describe.configure({ timeout: 60_000 })

  test.beforeEach(async ({ context }) => {
    await withoutAGateway(context)
  })

  for (const route of SMOKE_ROUTES) {
    test(`renders ${route}`, async ({ page }) => {
      expect(await renderProblems(page, route), `${route} did not render clean`).toEqual([])
    })
  }

  // ── The checks can fail ─────────────────────────────────────────────────────────────────────
  // Each plants one defect and asserts the checks above name it. Without these, a check that
  // rotted into a no-op would read exactly like a healthy bundle.

  test('a page that throws as it mounts is caught', async ({ page }) => {
    // The settings page's lazy chunk, replaced by a module that throws when it loads: the
    // page's own render crashes, which only its ErrorBoundary sees.
    await page.route(/\/assets\/SettingsPage-[^/]+\.js$/, (route) =>
      route.fulfill({ status: 200, contentType: 'text/javascript', body: 'throw new Error("planted render crash")' }),
    )
    const problems = await renderProblems(page, '#/settings')
    expect(problems.join('\n'), 'the planted crash went unseen').toMatch(/ErrorBoundary fallback|uncaught page error/)
  })

  test('a widget its boundary caught is caught', async ({ page }) => {
    // What a WidgetBoundary leaves in a crashed widget's place, planted after the shell mounts.
    await page.addInitScript(() => {
      new MutationObserver((_m, observer) => {
        const root = document.getElementById('root')
        if (!root || !root.querySelector('nav')) return
        const notice = document.createElement('p')
        notice.textContent = 'Couldn’t show the system readings.'.replace('’', "'")
        root.appendChild(notice)
        observer.disconnect()
      }).observe(document, { childList: true, subtree: true })
    })
    const problems = await renderProblems(page, '#/dashboard')
    expect(problems.join('\n'), 'the planted widget catch went unseen').toContain('WidgetBoundary notice')
  })

  test('an uncaught error is caught', async ({ page }) => {
    await page.addInitScript(() => {
      window.addEventListener('load', () => setTimeout(() => { throw new Error('planted uncaught error') }, 0))
    })
    const problems = await renderProblems(page, '#/dashboard')
    expect(problems.join('\n')).toContain('uncaught page error: planted uncaught error')
  })
})
