import type { BrowserContext, Page } from '@playwright/test'

// ── The render smoke's checks: does the BUILT bundle mount at all? ─────────────────────────────
//
// The v0.1.0 dashboard shipped blank: a dual-React dependency skew crashed the bundle at first
// render while tsc, vitest (jsdom) and `vite build` all stayed green, because nothing loaded the
// built artifact in a browser. These checks do, and they need no gateway: every `/api/*` call is
// answered in the page with a 503, except the config read, which names a user so the app routes
// past onboarding to the real page chunks. The socket upgrade is refused so the app settles into
// its disconnected state instead of waiting on it.
//
// Per route, four things fail it: `#root` mounted less than a shell's worth of markup, an uncaught
// page error fired, the per-page ErrorBoundary fallback is showing, or a WidgetBoundary notice is
// (a boundary's catch never becomes an uncaught error, so each needs its own check).

/** One route per major lazy chunk, and an unknown hash, which falls back to the dashboard. */
export const SMOKE_ROUTES = ['#/dashboard', '#/chat', '#/projects', '#/settings', '#/apps', '#/definitely-unknown']

/** The shell is far larger than this; an empty mount or a bare error card is far smaller. */
export const MIN_ROOT_HTML_CHARS = 1000

/** Answer the page's API as if no gateway were behind it. */
export async function withoutAGateway(context: BrowserContext): Promise<void> {
  await context.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/dashboard/config') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ user_name: 'Render Smoke' }) })
      return
    }
    await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'render smoke: no gateway' }) })
  })
  await context.routeWebSocket(/\/api\/ws/, (ws) => ws.close())
}

/** What is wrong with `route` as rendered, or nothing. */
export async function renderProblems(page: Page, route: string): Promise<string[]> {
  const pageErrors: string[] = []
  page.on('pageerror', (err) => pageErrors.push(err.message.split('\n')[0]))
  const problems: string[] = []
  try {
    await page.goto(`/${route}`, { waitUntil: 'load', timeout: 30_000 })
    // networkidle never comes: the app retries its socket forever. If an uncaught error already
    // fired, the mount is not coming, so the wait is short.
    await page.waitForSelector('#root > *', { timeout: pageErrors.length > 0 ? 3_000 : 15_000 })
    await page.waitForTimeout(750)
    const rootHtml = await page.evaluate(() => document.getElementById('root')?.innerHTML ?? '')
    if (rootHtml.length < MIN_ROOT_HTML_CHARS) {
      problems.push(`#root rendered only ${rootHtml.length} chars (want ≥ ${MIN_ROOT_HTML_CHARS})`)
    }
    if ((await page.getByText('This page hit an error').count()) > 0) {
      problems.push('ErrorBoundary fallback is showing (page render crashed)')
    }
    const notices = await page.getByText(/^Couldn't show /).allTextContents()
    if (notices.length > 0) problems.push(`WidgetBoundary notice is showing: ${notices.join(' | ')}`)
  } catch (err) {
    problems.push(`navigation/mount failed: ${(err as Error).message.split('\n')[0]}`)
  }
  problems.push(...pageErrors.map((e) => `uncaught page error: ${e}`))
  return problems
}
