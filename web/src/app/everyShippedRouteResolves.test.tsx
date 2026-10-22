/** Every address the app has shipped still opens a page (#3819).
 *
 *  Each entry of `shippedRoutes.ts` is opened the way a copied URL, a bookmark or a link from an
 *  old transcript is opened: the address is in the bar before anything renders, and the real shell
 *  boots onto it with `main.tsx`'s own providers and boot calls. Then two things are asserted.
 *
 *  · **Where it settles.** The address the bar holds once the shell is done with it is the entry's
 *    `lands`, or the entry itself. An address the router does not know is replaced with
 *    `#/dashboard` (the URL corrector, #306), so this is what catches a link that stopped opening;
 *    a redirect (`#/files/<slug>` to its artifact, a row of `routeRedirects.ts`) is caught too,
 *    unless the entry names where it goes.
 *  · **Back.** Opening it leaves no entry of its own in the history, so a redirect replaces the old
 *    address: Back from where it lands returns to the page before it, not to the old address, which
 *    would redirect again.
 *  · **What it shows.** A page: not the shell's loading fallback left in place, not the switch's
 *    "This view isn't available", not the error boundary, and not nothing. On Settings, the page
 *    the address names and not the Settings home, which is what an id Settings does not know opens,
 *    in place, keeping the address.
 *
 *  The home is empty and the gateway slow: who you are is read (set up, or not for an entry on a
 *  new home), and every other read is still on its way. Pending rather than answered, so no page is
 *  handed a shape it does not expect, and what a page shows while it waits is that page. What a
 *  page shows for a record no home holds is its own business, not the address's.
 *
 *  Each check is one function over what the shell did (`problems`), so the controls below plant an
 *  address that falls back to the dashboard, a Settings page that does not exist and a redirect the
 *  catalogue does not name, and show the same function reporting each.
 */
import { StrictMode } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { SHIPPED_ROUTES, type ShippedRoute } from './shippedRoutes'

/** Whether the home being opened has finished first-run setup. */
let setUp = true

// The chat socket and the first-run flow's canvas wave are outside what jsdom runs.
vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../ui/DotGlow', () => ({ DotGlow: () => null }))
vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  const gateway = new Proxy({}, {
    get: (_t, method: string) => () => method === 'dashboardConfig'
      ? Promise.resolve(setUp ? { user_name: 'Ada', username: 'ada' } : { user_name: '', username: '' })
      : new Promise(() => {}),
  })
  return { ...real, api: gateway }
})

const { App } = await import('./App')
const { ErrorBoundary } = await import('./ErrorBoundary')
const { ThemeProvider } = await import('./theme')
const { AppearanceProvider } = await import('./appearance')
const { PersonalityProvider } = await import('./personality')
const { IdentityProvider } = await import('./identity')
const { installAppSdk } = await import('./appSdk')
const { registerBuiltinContentTypes } = await import('../ui/content/registerBuiltins')

// `main.tsx`'s boot, before its first render (the service worker it also registers is a production
// build's, and never gates the render).
installAppSdk()
registerBuiltinContentTypes()

// Every page the shell code-splits, loaded once up front: a page's first load is a module transform,
// and on a busy machine that can outlast any wait an address is given below. Read off `App.tsx`'s
// own `lazy(() => import('…'))` lines, so a page added there is loaded here too.
beforeAll(async () => {
  const app = readFileSync(join(import.meta.dirname, 'App.tsx'), 'utf8')
  const pages = [...app.matchAll(/lazy\(\(\) => import\('([^']+)'\)/g)].map((m) => resolve(import.meta.dirname, `${m[1]}.tsx`))
  expect(pages.length, 'the lazy pages were not found').toBeGreaterThanOrEqual(20)
  await Promise.all(pages.map((page) => import(/* @vite-ignore */ page)))
}, 120_000)

beforeEach(() => {
  vi.stubGlobal('fetch', () => new Promise(() => {}))
  localStorage.clear()
  sessionStorage.clear()
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

/** The switch's fallback, the error boundary's two headings: none of them is the page. */
const NOT_A_PAGE = ['This view isn’t available.', 'This page hit an error', 'A new version is available']

/** An address as the router reads it: no `#/`, and its query in one order. */
function normal(address: string): string {
  const bare = address.replace(/^#?\/?/, '')
  const at = bare.indexOf('?')
  if (at < 0) return bare
  const query = [...new URLSearchParams(bare.slice(at + 1))].sort(([a, x], [b, y]) => a.localeCompare(b) || x.localeCompare(y))
  return `${bare.slice(0, at)}?${new URLSearchParams(query).toString()}`
}

/** The page's own part of the shell: the route's slot in `<main>` (after the shell's corners and
 *  banners), or the whole document for the full-screen routes, which draw no shell. */
function pageOf(container: HTMLElement): Element {
  return container.querySelector('main')?.lastElementChild ?? container
}

/** The shell's code-split fallback, still standing where the page should be. */
function stillLoading(page: Element): boolean {
  const only = page.children.length === 1 ? page.firstElementChild : null
  return !!only && only.getAttribute('role') === 'status' && only.getAttribute('aria-busy') === 'true'
    && !!only.querySelector('svg.animate-spin') && only.textContent === 'Loading…'
}

interface Opened {
  /** The address the bar settled on. */
  readonly settled: string
  /** The history entries opening it added. */
  readonly pushed: number
  readonly page: Element
  readonly loading: boolean
  /** Whether the page has Settings' way back from one of its pages. */
  readonly settingsPage: boolean
}

/** Open `entry` fresh, and wait until the shell has stopped moving: the page's chunk is in and the
 *  address has held still for a few ticks. */
async function open(entry: ShippedRoute): Promise<Opened> {
  cleanup()
  setUp = entry.home !== 'new'
  history.replaceState(null, '', `#/${entry.route}`)
  const before = history.length
  const view = render(
    <StrictMode>
      <ThemeProvider><AppearanceProvider><PersonalityProvider><IdentityProvider>
        <ErrorBoundary><App /></ErrorBoundary>
      </IdentityProvider></PersonalityProvider></AppearanceProvider></ThemeProvider>
    </StrictMode>,
  )
  let last = ''
  let still = 0
  for (let tick = 0; tick < 600 && still < 3; tick++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 20)) })
    const now = `${location.hash}|${stillLoading(pageOf(view.container))}`
    still = now === last && !stillLoading(pageOf(view.container)) ? still + 1 : 0
    last = now
  }
  const page = pageOf(view.container)
  return {
    settled: location.hash,
    pushed: history.length - before,
    page,
    loading: stillLoading(page),
    settingsPage: !!view.container.querySelector('button[aria-label="Back to Settings"]'),
  }
}

/** What is wrong with how `entry` opened, in words; empty when it opened its page. */
function problems(entry: ShippedRoute, opened: Opened): string[] {
  const out: string[] = []
  const expected = normal(entry.lands ?? entry.route)
  const settled = normal(opened.settled)
  if (settled !== expected) {
    out.push(settled === 'dashboard'
      ? `the shell did not know it, and replaced it with #/dashboard (expected #/${expected})`
      : `settled on #/${settled}, not #/${expected}`)
  }
  if (opened.pushed > 0) out.push(`it left ${opened.pushed} history entr${opened.pushed === 1 ? 'y' : 'ies'} of its own, so Back returns to the old address`)
  if (opened.loading) out.push('the shell\'s loading fallback never gave way to the page')
  const text = (opened.page.textContent ?? '').trim()
  if (!text && !opened.page.querySelector('[role], img, svg, canvas, iframe')) out.push('it shows nothing')
  for (const line of NOT_A_PAGE) if (text.includes(line)) out.push(`it shows "${line}"`)
  const settingsId = settled.match(/^settings\/([^?/]+)/)?.[1]
  if (settingsId && !opened.settingsPage) out.push(`Settings shows its home, not a page "${settingsId}"`)
  return out
}

describe('every shipped address opens a page', () => {
  it('🪤 vacuity floor: the catalogue holds the router\'s routes, their sub-routes and Settings\' pages', () => {
    expect(SHIPPED_ROUTES.length).toBeGreaterThanOrEqual(140)
    expect(SHIPPED_ROUTES.filter((e) => e.from.includes('router')).length).toBeGreaterThanOrEqual(27)
    expect(SHIPPED_ROUTES.filter((e) => e.route.startsWith('settings/')).length).toBeGreaterThanOrEqual(40)
    expect(SHIPPED_ROUTES.filter((e) => e.home === 'new').length, 'and setup, on a home that has not done it').toBeGreaterThanOrEqual(1)
  })

  it.each(SHIPPED_ROUTES.map((e) => [`#/${e.route}${e.home ? ` (${e.home} home)` : ''}`, e] as const))('%s', async (_name, entry) => {
    expect(problems(entry, await open(entry))).toEqual([])
  })
})

describe('🔴 control: each check reports the failure it is for', () => {
  it('an address the router does not know falls back to the dashboard, and is reported', async () => {
    const planted: ShippedRoute = { route: 'zz-never-shipped/item?tab=one', from: ['documented'] }
    expect(problems(planted, await open(planted))).toEqual([
      'the shell did not know it, and replaced it with #/dashboard (expected #/zz-never-shipped/item?tab=one)',
    ])
  })

  it('a Settings page that does not exist opens the Settings home, and is reported', async () => {
    const planted: ShippedRoute = { route: 'settings/zz-never-shipped', from: ['documented'] }
    expect(problems(planted, await open(planted))).toEqual(['Settings shows its home, not a page "zz-never-shipped"'])
  })

  it('a redirect the entry does not name is reported, and naming it is what passes', async () => {
    const unnamed: ShippedRoute = { route: 'files/report-1', from: ['documented'] }
    expect(problems(unnamed, await open(unnamed))).toEqual(['settled on #/artifacts/report-1, not #/files/report-1'])
    expect(problems({ ...unnamed, lands: 'artifacts/report-1' }, await open(unnamed))).toEqual([])
  })

  it('a page that shows nothing, an error or the switch\'s fallback is reported', () => {
    const entry: ShippedRoute = { route: 'tools', from: ['documented'] }
    const page = (html: string) => { const el = document.createElement('div'); el.innerHTML = html; return el }
    const opened = (html: string): Opened => ({ settled: '#/tools', pushed: 0, page: page(html), loading: false, settingsPage: false })
    expect(problems(entry, opened(''))).toEqual(['it shows nothing'])
    expect(problems(entry, opened('<p>This page hit an error</p>'))).toEqual(['it shows "This page hit an error"'])
    expect(problems(entry, opened('<p>This view isn’t available.</p>'))).toEqual(['it shows "This view isn’t available."'])
    expect(problems(entry, { ...opened('<p>Loading…</p>'), loading: true })).toEqual(['the shell\'s loading fallback never gave way to the page'])
    expect(problems(entry, opened('<h1>Tools</h1>'))).toEqual([])
  })

  it('a redirect that pushes where it lands is reported, since Back would return to the old address', async () => {
    const planted: ShippedRoute = { route: 'dashboard', from: ['documented'], lands: 'inbox' }
    // Redirected as setup's exit redirected an address it had nothing to show for: a pushed hash.
    const push = setTimeout(() => { location.hash = '#/inbox' }, 0)
    try {
      expect(problems(planted, await open(planted))).toEqual([
        'it left 1 history entry of its own, so Back returns to the old address',
      ])
    } finally { clearTimeout(push) }
  })
})

describe('the shell is booted as main.tsx boots it', () => {
  it('the calls main.tsx makes before its first render are the ones made here', () => {
    // A boot step added to main.tsx that this file does not make would open every address in a shell
    // the app never runs. (The service worker is a production build's, and is left out above.)
    const main = readFileSync(join(import.meta.dirname, '..', 'main.tsx'), 'utf8')
    const calls = [...main.matchAll(/^(?:void )?([a-zA-Z]+)\(\)$/gm)].map((m) => m[1])
    expect(calls).toEqual(['installAppSdk', 'registerBuiltinContentTypes', 'registerServiceWorker'])
  })
})
