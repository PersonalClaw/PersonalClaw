/** Every page the router serves is one ⌘K search away (#3819).
 *
 *  The sidebar is going to hold less, and a page that leaves it has to stay reachable without
 *  typing its address. So each top-level route the router serves (the shipped-route catalogue's
 *  router entries, which `everyProducedLinkIsAShippedRoute.test.ts` holds to `App.tsx`'s tables)
 *  must be opened by a palette row: the route itself, or, for a route that lands elsewhere, the page
 *  it lands on. A route with no row of its own is listed below with why it needs none, and the list
 *  is held to the routes that really have none.
 *
 *  The rows are the real shell's: it is booted on a set-up home as `main.tsx` boots it, the palette
 *  is handed the rows it would show, and each page row is run, with the address it writes read
 *  back. The keymap's rows are actions with keys (`everyChordIsOneRegistryRow.test.tsx` holds that
 *  each shows them), not pages, and are not run. A home still in setup is held in the full-screen
 *  flow, so the new-home entries are not destinations here.
 *
 *  Each check is a function over the rows' addresses, so its control plants a route with no row, a
 *  row taken away and a reason that no longer holds, and shows the same function reporting each.
 */
import { StrictMode } from 'react'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render } from '@testing-library/react'
import { SHIPPED_ROUTES, type ShippedRoute } from './shippedRoutes'
import type { Command } from './CommandPalette'

/** The rows the shell last handed the palette. */
let rows: readonly Command[] = []

vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../ui/DotGlow', () => ({ DotGlow: () => null }))
vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  const gateway = new Proxy({}, {
    get: (_t, method: string) => () => method === 'dashboardConfig'
      ? Promise.resolve({ user_name: 'Ada', username: 'ada' })
      : new Promise(() => {}),
  })
  return { ...real, api: gateway }
})
vi.mock('./CommandPalette', async (orig) => {
  const real = await orig<typeof import('./CommandPalette')>()
  return { ...real, CommandPalette: ({ commands }: { commands: Command[] }) => { rows = commands; return null } }
})

const { App } = await import('./App')
const { ErrorBoundary } = await import('./ErrorBoundary')
const { ThemeProvider } = await import('./theme')
const { AppearanceProvider } = await import('./appearance')
const { PersonalityProvider } = await import('./personality')
const { IdentityProvider } = await import('./identity')
const { installAppSdk } = await import('./appSdk')
const { registerBuiltinContentTypes } = await import('../ui/content/registerBuiltins')

installAppSdk()
registerBuiltinContentTypes()

/** The routes that need no row of their own, and why. */
const NO_ROW: Readonly<Record<string, string>> = {
  app: 'it names no app: each installed app\'s page is a row of its own, built from the apps the home has',
  companion: 'the phone\'s own page, full screen and not a desktop destination: the phone\'s drawer lists it',
  buddy: 'the buddy playground, reached by its address only until the buddy ships (#3892)',
}

/** An address's page: its path, without `#/` or its query. */
const pageOf = (address: string): string => address.replace(/^#?\/?/, '').split('?')[0]

/** Where the bar is put before each row runs, so a row that opens nothing is told apart. */
const PROBE = '#/dashboard?palette-probe=1'

/** The page each of `commands`' page rows opens, by the row's id. */
function opened(commands: readonly Command[]): Map<string, string> {
  const out = new Map<string, string>()
  for (const row of commands) {
    if (!/^(go|act):/.test(row.id) || row.disabledReason) continue
    history.replaceState(null, '', PROBE)
    act(() => row.run())
    if (location.hash !== PROBE) out.set(row.id, pageOf(location.hash))
  }
  return out
}

/** The router's routes on a set-up home: each with the page it opens. */
const destinations = (catalogue: readonly ShippedRoute[]) => catalogue
  .filter((e) => e.from.includes('router') && !e.home)
  .map((e) => ({ route: e.route, page: pageOf(e.lands ?? e.route) }))

/** The routes no row opens, directly or where they land, and no reason excuses. */
function unreachable(catalogue: readonly ShippedRoute[], pages: ReadonlySet<string>, noRow: Readonly<Record<string, string>>): string[] {
  return destinations(catalogue).filter((d) => !(d.route in noRow) && !pages.has(d.page)).map((d) => d.route)
}

/** The reasons that no longer hold: a route a row opens, or one the router does not serve. */
function staleReasons(catalogue: readonly ShippedRoute[], pages: ReadonlySet<string>, noRow: Readonly<Record<string, string>>): string[] {
  const served = new Map(destinations(catalogue).map((d) => [d.route, d.page]))
  return Object.keys(noRow).filter((route) => !served.has(route) || pages.has(served.get(route)!))
}

let byRow = new Map<string, string>()

beforeAll(async () => {
  vi.stubGlobal('fetch', () => new Promise(() => {}))
  localStorage.clear()
  history.replaceState(null, '', '#/dashboard')
  render(
    <StrictMode>
      <ThemeProvider><AppearanceProvider><PersonalityProvider><IdentityProvider>
        <ErrorBoundary><App /></ErrorBoundary>
      </IdentityProvider></PersonalityProvider></AppearanceProvider></ThemeProvider>
    </StrictMode>,
  )
  for (let tick = 0; tick < 500 && !rows.length; tick++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 20)) })
  }
  expect(rows.length, 'the shell never handed the palette its rows').toBeGreaterThan(0)
  byRow = opened(rows)
}, 60_000)

afterEach(() => { history.replaceState(null, '', '#/dashboard') })
afterAll(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('every page the router serves has a palette row', () => {
  it('🪤 vacuity floor: the rows were run, and the router\'s routes were found', () => {
    expect(byRow.size, 'the page rows that opened a page').toBeGreaterThanOrEqual(22)
    expect(destinations(SHIPPED_ROUTES).length).toBeGreaterThanOrEqual(27)
  })

  it('🔑 each route is opened by a row, or lands on a page one opens', () => {
    expect(unreachable(SHIPPED_ROUTES, new Set(byRow.values()), NO_ROW)).toEqual([])
  })

  it('🔑 each route listed as needing no row has none, and is one the router serves', () => {
    expect(staleReasons(SHIPPED_ROUTES, new Set(byRow.values()), NO_ROW)).toEqual([])
  })

  it('🔴 control: a route with no row, a row taken away and a reason that no longer holds are each reported', () => {
    // Each against what the real rows give, so the control holds whatever the real check finds.
    const pages = new Set(byRow.values())
    const real = unreachable(SHIPPED_ROUTES, pages, NO_ROW)
    const planted: ShippedRoute = { route: 'zz-never-shipped', from: ['router'], since: 'v0.2.3' }
    expect(unreachable([...SHIPPED_ROUTES, planted], pages, NO_ROW)).toEqual([...real, 'zz-never-shipped'])
    const withoutDiscover = new Set([...byRow].filter(([id]) => id !== 'go:discover').map(([, page]) => page))
    expect(withoutDiscover.has('discover'), 'only that row opens Discover').toBe(false)
    expect(unreachable(SHIPPED_ROUTES, withoutDiscover, NO_ROW).filter((r) => !real.includes(r))).toEqual(['discover'])
    const stale = staleReasons(SHIPPED_ROUTES, pages, NO_ROW)
    expect(staleReasons(SHIPPED_ROUTES, pages, { ...NO_ROW, discover: 'planted', 'zz-never-shipped': 'planted' }))
      .toEqual([...stale, 'discover', 'zz-never-shipped'])
  })
})
