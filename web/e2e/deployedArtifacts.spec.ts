import { test, expect, type Page } from '@playwright/test'
import { gotoRoute, assertShellMounted } from './helpers'

// ── A deployed artifact's page runs in an origin of its own ─────────────────────────────────────
//
// A deployed artifact is model-authored, and it is served from the dashboard's own host. What keeps
// its script from acting as the dashboard is the serve response: a CSP `sandbox allow-scripts`
// directive that puts the page in an opaque origin however it is opened, and the Preview pane's own
// `sandbox="allow-scripts"`. The server half (every answer carries the directive; the deployment's
// capability, not the session, authorizes its files) is `tests/test_a_deployed_page_runs_in_an_
// origin_of_its_own.py`. This is the browser half, which only a browser can answer:
//
//  1. The page still WORKS sandboxed. Its bundle is a file of its own, requested from an opaque
//     origin with no session cookie, so it loads only because the URL's capability authorizes it.
//     The probe below renders only if that bundle ran.
//  2. Framed in the Preview pane and opened in a tab, the page gets an opaque origin: no access to
//     the window around it, no cookie, no web storage, and (the CSP floor) no request of its own.
//  3. Tearing the deployment down, and deleting the artifact, take the URL down.

/** A same-origin call from inside the dashboard tab: the SPA's own cookie and session header. */
async function inTab<T>(page: Page, path: string, method = 'GET', body?: unknown): Promise<T> {
  return page.evaluate(async ({ path, method, body }) => {
    const r = await fetch(path, {
      method,
      headers: { 'Content-Type': 'application/json', 'X-Session-Key': 'dashboard:ui' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    if (!r.ok) throw new Error(`${method} ${path} → ${r.status} ${await r.text()}`)
    return r.json()
  }, { path, method, body }) as Promise<T>
}

/** The page under test: it reports what its own document can reach. Each probe answers `blocked`
 *  when the browser refuses it and `reachable` when it does not. `framed` tells the two openings
 *  apart: in a tab of its own, `window.parent` is the page itself. */
const PROBE = `
function App() {
  const [report, setReport] = React.useState(null)
  React.useEffect(() => {
    const probe = (f) => { try { f(); return 'reachable' } catch (e) { return 'blocked' } }
    const found = {
      origin: String(self.origin),
      framed: window.parent !== window,
      parent: probe(() => window.parent.document.title),
      cookie: probe(() => document.cookie),
      storage: probe(() => window.localStorage.length),
    }
    fetch('/api/health').then(() => 'reachable', () => 'blocked')
      .then((request) => setReport({ ...found, request }))
  }, [])
  return <pre data-testid="probe">{report ? JSON.stringify(report) : 'running'}</pre>
}
`

type Report = { origin: string; framed: boolean; parent: string; cookie: string; storage: string; request: string }

async function readProbe(probe: ReturnType<Page['getByTestId']>): Promise<Report> {
  await expect(probe, 'the page never rendered: its own bundle did not load or did not run').toBeVisible()
  await expect(probe).not.toHaveText('running')
  return JSON.parse((await probe.textContent()) ?? '{}') as Report
}

test('a deployed page loads its own files and reaches nothing of the dashboard, framed or in a tab', async ({ page, context }) => {
  await gotoRoute(page, 'artifacts')
  await assertShellMounted(page)

  const name = `seal-probe-${test.info().workerIndex}-${test.info().retry}`
  const art = await inTab<{ slug: string }>(page, '/api/artifacts', 'POST', { name, kind: 'react', content: PROBE })
  const slug = art.slug
  try {
    const { deployment } = await inTab<{ deployment: { url: string } }>(page, `/api/artifacts/${slug}/deploy`, 'POST', {})
    expect(deployment.url, 'a deployment URL names its slug and its capability').toMatch(
      new RegExp(`^/artifacts/serve/${slug}/[A-Za-z0-9_-]{43}/$`),
    )

    // Framed: the Preview pane in the artifact's own view.
    await gotoRoute(page, `artifacts/${slug}`)
    await page.getByRole('button', { name: 'Preview', description: 'Open the deployed page in a pane here' }).click()
    const frame = page.locator(`iframe[title="Deployed artifact: ${slug}"]`)
    await expect(frame).toHaveAttribute('sandbox', 'allow-scripts')
    const framed = await readProbe(page.frameLocator(`iframe[title="Deployed artifact: ${slug}"]`).getByTestId('probe'))
    expect(framed).toEqual({
      origin: 'null', framed: true, parent: 'blocked', cookie: 'blocked', storage: 'blocked', request: 'blocked',
    })

    // In a tab of its own: no attribute holds it there, only the response's directive.
    const tab = await context.newPage()
    await tab.goto(deployment.url)
    const alone = await readProbe(tab.getByTestId('probe'))
    expect(alone).toEqual({
      origin: 'null', framed: false, parent: 'reachable', cookie: 'blocked', storage: 'blocked', request: 'blocked',
    })
    await tab.close()

    // Tear down takes the URL down; a fresh deploy answers at a new URL only.
    await inTab(page, `/api/artifacts/${slug}/deploy`, 'DELETE')
    expect((await page.request.get(deployment.url)).status()).toBe(404)
    const again = await inTab<{ deployment: { url: string } }>(page, `/api/artifacts/${slug}/deploy`, 'POST', {})
    expect(again.deployment.url).not.toBe(deployment.url)
    expect((await page.request.get(again.deployment.url)).status()).toBe(200)

    // Deleting the artifact takes its page down with it.
    await inTab(page, `/api/artifacts/${slug}`, 'DELETE')
    expect((await page.request.get(again.deployment.url)).status()).toBe(404)
  } finally {
    await page.evaluate(async (s) => {
      await fetch(`/api/artifacts/${s}`, { method: 'DELETE', headers: { 'X-Session-Key': 'dashboard:ui' } })
    }, slug)
  }
})
