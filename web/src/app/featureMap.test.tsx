// @module-tag tree-scan
/** `docs/feature-map.md` is what the app holds (#3812).
 *
 *  The map answers "how does a person reach this?" for every page the router serves, and this keeps
 *  the answer from drifting: the map is regenerated here from the shell's own tables and compared
 *  with the committed file, and a difference names the routes whose rows changed. A route added to
 *  the router without a way to it shows under "Not findable", and so fails here until the map is
 *  regenerated, where the list says so in words.
 *
 *  The palette's rows are the real shell's, as `thePaletteOpensEveryDestination.test.tsx` reads
 *  them: the shell is booted on a set-up home, the palette is handed its rows, and each page row is
 *  run, with the address it writes read back. The sidebar's rows are `App.tsx`'s `NAV`, read from
 *  its source; the tabs are Library's, Capabilities' and Automations' own tables; the links are the source files
 *  that name a route in a navigation or an address.
 *
 *  `npm run docs:feature-map` sets `PC_WRITE_FEATURE_MAP=1`, and only then is the file written.
 */
import { StrictMode } from 'react'
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render } from '@testing-library/react'
import { readFileSync, writeFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import { SHIPPED_ROUTES } from './shippedRoutes'
import type { Command } from './CommandPalette'
import { featureMapMarkdown, featureRows, notFindable, type FeatureMapInput, type PaletteRow, type SidebarRow } from './featureMap'
import { CAPABILITY_TABS } from '../pages/capabilities/capabilityTabs'
import { LIBRARY_TABS } from '../pages/library/libraryTabs'
import { AUTOMATIONS_TABS } from '../pages/automations/automationTabs'
import { filesUnder } from '../test/sourceTree'

/** The opt-in. Named on the npm script, so the intent is visible in package.json. */
export const WRITE_ENV = 'PC_WRITE_FEATURE_MAP'

const SRC = join(process.cwd(), 'src')
const MAP = join(process.cwd(), '..', 'docs', 'feature-map.md')

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

/** An address's page: its path's first part, without `#/` or its query. */
const pageOf = (address: string): string => address.replace(/^#?\/?/, '').split(/[?/]/)[0]

const PROBE = '#/dashboard?feature-map-probe=1'

/** The palette's page rows, each with the page it opens. */
function paletteRows(commands: readonly Command[]): PaletteRow[] {
  const out: PaletteRow[] = []
  for (const row of commands) {
    if (!/^(go|act):/.test(row.id) || row.disabledReason) continue
    history.replaceState(null, '', PROBE)
    act(() => row.run())
    if (location.hash !== PROBE) out.push({ label: row.label, hint: row.hint, page: pageOf(location.hash) })
  }
  history.replaceState(null, '', '#/dashboard')
  return out
}

const APP = () => readFileSync(join(SRC, 'app', 'App.tsx'), 'utf8')

/** A `NavItem` literal's id, label and section, in the order the source declares them. */
function navItems(source: string): SidebarRow[] {
  return [...source.matchAll(/\{ id: '([a-z][a-z0-9-]*)', label: '([^']+)', icon: \w+(?:, section: '([^']+)')?/g)]
    .map(([, id, label, section]) => (section ? { id, label, section } : { id, label }))
}

/** `App.tsx`'s `NAV`: the sidebar's rows. */
function sidebarRows(source: string): SidebarRow[] {
  const at = source.indexOf('const NAV: NavItem[] = [')
  const end = source.indexOf('\n]\n', at)
  expect(at, 'NAV was not found in App.tsx').toBeGreaterThan(0)
  return navItems(source.slice(at, end))
}

/** The rows only the phone's drawer carries (`COMPANION_RAIL_ITEM`). */
function drawerRows(source: string): SidebarRow[] {
  const line = source.split('\n').find((l) => l.startsWith('const COMPANION_RAIL_ITEM'))
  expect(line, 'COMPANION_RAIL_ITEM was not found in App.tsx').toBeTruthy()
  return navItems(line!)
}

/** A route named where a page navigates or writes an address. */
const LINK = /\bnavigate\(\s*['"`]([a-z][a-z0-9-]*)|['"`]#\/([a-z][a-z0-9-]*)/g

/** The files whose source names each page in a link, outside the shell's own tables, the route
 *  catalogues, the tests and the page's own folder (a page that links to itself is no way in). */
function linksByPage(pages: ReadonlySet<string>): Record<string, string[]> {
  const SKIP = new Set(['app/App.tsx', 'app/shippedRoutes.ts', 'app/routeRedirects.ts', 'app/featureMap.ts'])
  const out: Record<string, string[]> = {}
  for (const file of filesUnder(SRC, (e) => /\.tsx?$/.test(e) && !/\.(test|doc)\.tsx?$/.test(e))) {
    const rel = relative(SRC, file).split(/[\\/]/).join('/')
    if (SKIP.has(rel) || rel.startsWith('test/')) continue
    const found = new Set<string>()
    for (const m of readFileSync(file, 'utf8').matchAll(LINK)) {
      const page = m[1] ?? m[2]
      if (pages.has(page) && !rel.startsWith(`pages/${page}/`)) found.add(page)
    }
    for (const page of found) (out[page] ??= []).push(rel)
  }
  return out
}

/** The routes only a typed address opens, and why each may stay so. */
const TYPED_ONLY: Readonly<Record<string, string>> = {
  buddy: 'the buddy playground, reached by its address only until the buddy ships (#3892)',
}

/** The router's routes on a set-up home, each with the page it opens. */
const ROUTES = SHIPPED_ROUTES
  .filter((e) => e.from.includes('router') && !e.home)
  .map((e) => ({ route: e.route, page: pageOf(e.lands ?? e.route) }))

let input: FeatureMapInput

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
  const source = APP()
  input = {
    routes: ROUTES,
    sidebar: sidebarRows(source),
    drawerOnly: drawerRows(source),
    tabs: [
      { row: 'library', tabs: LIBRARY_TABS },
      { row: 'capabilities', tabs: CAPABILITY_TABS },
      // Automations' tabs, and the list at the address it had as the Triggers page (#3816).
      { row: 'automations', tabs: [...AUTOMATIONS_TABS, { label: 'Automations', address: 'triggers' }] },
    ],
    palette: paletteRows(rows),
    links: linksByPage(new Set(ROUTES.map((r) => r.page))),
  }
}, 60_000)

afterAll(() => {
  cleanup()
  vi.unstubAllGlobals()
})

/** Each table line of a map, by its route. */
function linesByRoute(doc: string): Map<string, string> {
  return new Map(doc.split('\n').filter((l) => l.startsWith('| `#/')).map((l) => [l.slice(5, l.indexOf('`', 5)), l]))
}

/** What differs between two maps: the routes whose lines were added, dropped or changed, and the
 *  not-findable list when it changed. */
function changed(committed: string, generated: string): string[] {
  const was = linesByRoute(committed)
  const now = linesByRoute(generated)
  const routes = [...new Set([...was.keys(), ...now.keys()])].filter((r) => was.get(r) !== now.get(r))
  const tail = (doc: string) => doc.slice(doc.indexOf('## Not findable'))
  return [...routes.map((r) => `#/${r}`), ...(tail(committed) !== tail(generated) ? ['Not findable'] : [])]
}

const committed = () => readFileSync(MAP, 'utf8')

describe('the feature map', () => {
  it('🪤 vacuity floor: the routes, the sidebar, the palette and the links were all found', () => {
    expect(input.routes.length).toBeGreaterThanOrEqual(27)
    expect(input.sidebar.map((r) => r.label)).toEqual(expect.arrayContaining(['Home', 'Chat', 'Projects', 'Library', 'Inbox', 'Settings']))
    expect(input.drawerOnly.map((r) => r.label)).toEqual(['Companion'])
    expect(new Set(input.palette.map((p) => p.page)).size, 'the pages palette rows open').toBeGreaterThanOrEqual(20)
    expect(Object.keys(input.links).length, 'the pages some file links to').toBeGreaterThanOrEqual(10)
  })

  it.runIf(process.env[WRITE_ENV] === '1')('writes docs/feature-map.md', () => {
    const doc = featureMapMarkdown(input)
    writeFileSync(MAP, doc, 'utf8')
    expect(committed()).toBe(doc)
  })

  it('🔑 docs/feature-map.md is what the app holds (npm run docs:feature-map regenerates it)', () => {
    expect(changed(committed(), featureMapMarkdown(input)), 'the routes whose rows changed').toEqual([])
  })

  it('🔑 no route is reached only by typing its address, but the ones with a reason to be', () => {
    expect(notFindable(featureRows(input))).toEqual(Object.keys(TYPED_ONLY))
  })

  it('🔴 control: a route with no way to it is not findable, and the map names it as changed', () => {
    const planted = { ...input, routes: [...input.routes, { route: 'zz-typed-only', page: 'zz-typed-only' }] }
    expect(notFindable(featureRows(planted))).toEqual([...Object.keys(TYPED_ONLY), 'zz-typed-only'])
    const real = featureMapMarkdown(input)
    expect(changed(real, featureMapMarkdown(planted))).toEqual(['#/zz-typed-only', 'Not findable'])
    // A row taken from the sidebar is a changed row too, and a link is a way to a page.
    const withoutInbox = { ...input, sidebar: input.sidebar.filter((r) => r.id !== 'inbox') }
    expect(changed(real, featureMapMarkdown(withoutInbox))).toEqual(['#/inbox', '#/notifications'])
    const linked = { ...planted, links: { ...planted.links, 'zz-typed-only': ['pages/zz/Planted.tsx'] } }
    expect(notFindable(featureRows(linked))).toEqual(Object.keys(TYPED_ONLY))
  })
})
