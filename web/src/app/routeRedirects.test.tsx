/** Every retired address goes through one table, and the router reads it (#3819).
 *
 *  `routeRedirects.ts` is the one list of the addresses the app has retired, each with where it
 *  opens now and the pull request that moved it. Held here:
 *
 *    · each row is well formed: an old address, an in-app new one, the pull request, and no two rows
 *      for one address;
 *    · a redirect never chains: no row lands on an address another row (or itself) retires;
 *    · a redirect keeps the substate the new page reads: its segments in place, as written, and the
 *      query keys it keeps under their new names, and nothing else;
 *    · every address of the shipped-route catalogue either opens as itself or is retired here to an
 *      address that does: the entry's `lands` is the row's target, the target is an entry with no
 *      landing of its own, and a query key the old entry carries is carried to it. Every row retires
 *      at least one entry, and every entry that lands elsewhere without a row here is a landing its
 *      page owns, listed below with why;
 *    · the hash router applies it: an old address in the bar, on Back or Forward, from a link the
 *      shell opens, or passed to `navigate`, becomes its new one before the router hands it to a
 *      page, in place of the old history entry.
 *
 *  `everyShippedRouteResolves.test.tsx` opens every catalogue entry, the retired ones and their
 *  targets among them, in the real shell. Each check here is a function over a table and a
 *  catalogue, so its control plants the break it is for.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { act, cleanup, render, renderHook } from '@testing-library/react'
import { ROUTE_REDIRECTS, redirectFor, type RouteRedirect } from './routeRedirects'
import { SAMPLE, SHIPPED_ROUTES, type ShippedRoute } from './shippedRoutes'
import { useHashRoute } from './useHashRoute'

/** An address as the router reads it: no `#/`, and its query in one order. */
function normal(address: string): string {
  const bare = address.replace(/^#?\/?/, '')
  const at = bare.indexOf('?')
  if (at < 0) return bare
  const query = [...new URLSearchParams(bare.slice(at + 1))].sort(([a, x], [b, y]) => a.localeCompare(b) || x.localeCompare(y))
  return `${bare.slice(0, at)}?${new URLSearchParams(query).toString()}`
}

const keysOf = (address: string): string[] => [...new URLSearchParams(address.split('?')[1] ?? '').keys()]

/** An address `row` retires, each `:name` filled with a sample. */
const sampleOf = (row: RouteRedirect): string => row.from.replace(/:([a-z]+)/gi, (_m, name: string) => `sample-${name}`)

/** The row of `table` that retires `address`. */
const rowFor = (address: string, table: readonly RouteRedirect[]): RouteRedirect | undefined =>
  table.find((row) => redirectFor(address, [row]) !== null)

const SEGMENT = /^(?:[a-z0-9][a-z0-9-]*|:[a-z]+)$/i

// ── The rows ─────────────────────────────────────────────────────────────────────────────────────

/** What is wrong with each row, in words. */
function malformed(table: readonly RouteRedirect[]): string[] {
  const out: string[] = []
  for (const row of table) {
    const at = `${row.from} -> ${row.to}`
    const [path, fixed = ''] = row.to.split('?')
    const names = row.from.split('/').filter((s) => s.startsWith(':'))
    if (!row.from.split('/').every((s) => SEGMENT.test(s))) out.push(`${at}: the old address is not a path of segments and :names`)
    if (!path.split('/').every((s) => SEGMENT.test(s))) out.push(`${at}: the new address is not an in-app path`)
    if (new Set(names).size !== names.length) out.push(`${at}: the old address names a segment twice`)
    for (const name of path.split('/').filter((s) => s.startsWith(':'))) {
      if (!names.includes(name)) out.push(`${at}: the new address fills ${name}, which the old one does not carry`)
    }
    const fixedKeys = keysOf(`?${fixed}`)
    for (const [key, name] of Object.entries(row.keeps ?? {})) {
      if (fixedKeys.includes(name)) out.push(`${at}: it keeps ${key} as ${name}, which its new address already sets`)
    }
    if (!(row.pr === 'before v0.1.0' || (Number.isInteger(row.pr) && row.pr > 0))) out.push(`${at}: it names no pull request`)
    const sample = sampleOf(row)
    const others = table.filter((other) => other !== row && redirectFor(sample, [other]) !== null)
    if (others.length) out.push(`${at}: #/${sample} is retired by another row too (${others.map((o) => o.from).join(', ')})`)
  }
  return out
}

/** Each redirect that lands on an address the table retires again. */
function chains(table: readonly RouteRedirect[]): string[] {
  return table.flatMap((row) => {
    const sample = sampleOf(row)
    const target = redirectFor(sample, table)
    const again = target === null ? null : redirectFor(target, table)
    return again === null ? [] : [`#/${sample} -> #/${target} -> #/${again}`]
  })
}

describe('the redirect table', () => {
  it('🪤 vacuity floor: it holds the addresses already retired on main', () => {
    expect(ROUTE_REDIRECTS.map((r) => r.from)).toEqual(expect.arrayContaining(['files/:slug', 'settings/vocabulary']))
  })

  it('🔑 each row names the old address, an in-app new one and the pull request, and owns its address alone', () => {
    expect(malformed(ROUTE_REDIRECTS)).toEqual([])
  })

  it('🔑 a redirect never chains: no row lands where a row retires', () => {
    expect(chains(ROUTE_REDIRECTS)).toEqual([])
  })

  it('🔴 control: a bad row, two rows for one address and a chain are each reported', () => {
    expect(malformed([
      { from: 'old/:id', to: 'https://elsewhere.test/:id', pr: 1 },
      { from: 'old2/:id', to: 'new/:slug', pr: 2 },
      { from: 'old3', to: 'new?tab=a', keeps: { tab: 'tab' }, pr: 3 },
      { from: 'old4', to: 'new', pr: 0 },
    ])).toEqual([
      'old/:id -> https://elsewhere.test/:id: the new address is not an in-app path',
      'old2/:id -> new/:slug: the new address fills :slug, which the old one does not carry',
      'old3 -> new?tab=a: it keeps tab as tab, which its new address already sets',
      'old4 -> new: it names no pull request',
    ])
    expect(malformed([{ from: 'old/:id', to: 'a/:id', pr: 1 }, { from: 'old/:slug', to: 'b/:slug', pr: 2 }]))
      .toEqual([
        'old/:id -> a/:id: #/old/sample-id is retired by another row too (old/:slug)',
        'old/:slug -> b/:slug: #/old/sample-slug is retired by another row too (old/:id)',
      ])
    expect(chains([{ from: 'a/:id', to: 'b/:id', pr: 1 }, { from: 'b/:id', to: 'c/:id', pr: 2 }]))
      .toEqual(['#/a/sample-id -> #/b/sample-id -> #/c/sample-id'])
    expect(chains([{ from: 'a/:id', to: 'a/:id/view', pr: 1 }, { from: 'a/:id/view', to: 'a/:id', pr: 2 }])).toHaveLength(2)
  })
})

// ── What a redirect keeps ────────────────────────────────────────────────────────────────────────

describe('a redirect keeps the substate the new page reads', () => {
  const moved: RouteRedirect = { from: 'old/:id', to: 'new/:id?tab=items', keeps: { read: 'read', q: 'search' }, pr: 1 }

  it('🔑 its segments arrive in place, as they were written, and the new page\'s query with them', () => {
    expect(redirectFor('old/a%2Fb%3Fc', [moved])).toBe('new/a%2Fb%3Fc?tab=items')
    expect(redirectFor('#/old/x', [moved]), 'written with its #/ too').toBe('new/x?tab=items')
    expect(redirectFor('files/report-1')).toBe('artifacts/report-1')
    expect(redirectFor('settings/vocabulary')).toBe('settings/voice?section=vocabulary')
  })

  it('🔑 the keys it keeps arrive under the new page\'s names; a key it does not keep meant something only to the old page', () => {
    expect(normal(redirectFor('old/x?read=1&q=hello+world&dir=%2Ftmp', [moved]) ?? '')).toBe(normal('new/x?tab=items&read=1&search=hello+world'))
    expect(redirectFor('old/x?dir=%2Ftmp', [moved]), 'a kept key the old address lacks stays absent').toBe('new/x?tab=items')
    // The old Files page's own state (its folder and its content search) is not an artifact's.
    expect(redirectFor('files/report-1?dir=%2Ftmp&q=todo&include=*.md')).toBe('artifacts/report-1')
  })

  it('an address with more or fewer segments, or another page\'s, is not retired', () => {
    for (const address of ['old', 'old/x/y', 'older/x', 'files', 'files?file=src%2Fapp.py', 'artifacts/report-1', 'settings/voice', 'settings']) {
      expect(redirectFor(address, [moved, ...ROUTE_REDIRECTS]), address).toBeNull()
    }
  })
})

// ── The catalogue ────────────────────────────────────────────────────────────────────────────────

/** The catalogue's entries that land somewhere else by their page's own code, not by a retirement,
 *  and why their page owns the landing. A move that retires an address goes into the table instead. */
const PAGE_LANDINGS: readonly { route: string; home?: ShippedRoute['home']; why: string }[] = [
  { route: 'loops', why: 'the loop composer, unless a draft is under review there: the address is still the plan review\'s' },
  { route: 'code', why: 'the code loop composer, unless a draft is under review there: the address is still the plan review\'s' },
  { route: 'chat/room', why: 'no room is named, so the list of rooms' },
  { route: 'onboarding', why: 'setup is done, so it leaves for Home or where setup was asked to go' },
  { route: 'onboarding', home: 'new', why: 'setup opens on its first step' },
  { route: `chat/${SAMPLE.chat}?find=report`, why: 'the find bar takes the term' },
  { route: 'chat/new?prompt=draft+this', why: 'a link\'s own word for the draft, written as the chat\'s (linkState.ts)' },
  { route: 'chat/new?pane=history', why: 'a link\'s own word for the pane, written as the chat\'s (linkState.ts)' },
  { route: `chat/${SAMPLE.chat}?pane=activity&file=src%2Fapp.py`, why: 'a link\'s own word for the pane, written as the chat\'s (linkState.ts)' },
]

const keyOf = (route: string, home?: ShippedRoute['home']): string => `${normal(route)}${home ? ` (${home} home)` : ''}`

/** Where the catalogue and the table disagree. */
function againstTheCatalogue(table: readonly RouteRedirect[], catalogue: readonly ShippedRoute[]): string[] {
  const out: string[] = []
  for (const entry of catalogue) {
    const target = redirectFor(entry.route, table)
    if (target === null) continue
    const named = entry.lands ?? entry.route
    if (normal(named) !== normal(target)) out.push(`#/${entry.route} is retired to #/${target}, and the catalogue says it lands on #/${named}`)
    const landing = catalogue.find((e) => normal(e.route) === normal(target) && e.home === entry.home)
    if (!landing) out.push(`#/${entry.route} is retired to #/${target}, which is not in the catalogue`)
    else if (landing.lands) out.push(`#/${entry.route} is retired to #/${target}, which opens #/${landing.lands} in turn`)
    const row = rowFor(entry.route, table)
    for (const key of keysOf(entry.route)) {
      if (!row?.keeps?.[key]) out.push(`#/${entry.route} carries ${key}, and its redirect drops it`)
    }
  }
  for (const row of table) {
    if (!catalogue.some((e) => redirectFor(e.route, [row]) !== null)) out.push(`${row.from}: it retires no address of the catalogue`)
  }
  return out
}

/** The entries that land elsewhere with no row here and no reason below, and the reasons left over. */
function unownedLandings(table: readonly RouteRedirect[], catalogue: readonly ShippedRoute[], listed: typeof PAGE_LANDINGS): string[] {
  const own = new Set(listed.map((l) => keyOf(l.route, l.home)))
  const elsewhere = catalogue.filter((e) => e.lands !== undefined && redirectFor(e.route, table) === null)
  const found = new Set(elsewhere.map((e) => keyOf(e.route, e.home)))
  return [
    ...elsewhere.filter((e) => !own.has(keyOf(e.route, e.home)))
      .map((e) => `#/${keyOf(e.route, e.home)} lands on #/${e.lands} with no redirect: retire it in routeRedirects.ts, or say why its page owns the landing`),
    ...[...own].filter((k) => !found.has(k)).map((k) => `#/${k} is listed as a page's own landing, and it is not one`),
  ]
}

describe('every catalogue address opens as itself, or is retired to one that does', () => {
  it('🪤 vacuity floor: the table retires catalogue entries', () => {
    expect(SHIPPED_ROUTES.filter((e) => redirectFor(e.route) !== null).length).toBeGreaterThanOrEqual(2)
  })

  it('🔑 each retired entry lands where its row says, on an entry that opens as itself, with its query', () => {
    expect(againstTheCatalogue(ROUTE_REDIRECTS, SHIPPED_ROUTES)).toEqual([])
  })

  it('🔑 every other landing is one its page owns, and each reason listed is still one', () => {
    expect(unownedLandings(ROUTE_REDIRECTS, SHIPPED_ROUTES, PAGE_LANDINGS)).toEqual([])
  })

  it('🔴 control: a disagreeing landing, a target outside the catalogue, a dropped key, a dead row and an unlisted landing are each reported', () => {
    const catalogue: ShippedRoute[] = [
      { route: 'old/x?read=1', from: ['documented'], lands: 'new/x?read=1' },
      { route: 'old/y', from: ['documented'], lands: 'new/elsewhere' },
      { route: 'new/x?read=1', from: ['documented'] },
      { route: 'gone/z', from: ['documented'], lands: 'moved/z' },
      { route: 'moved/z', from: ['documented'], lands: 'moved/z/again' },
      { route: 'kept-by-a-page', from: ['documented'], lands: 'somewhere' },
    ]
    const table: RouteRedirect[] = [
      { from: 'old/:id', to: 'new/:id', pr: 1 },
      { from: 'gone/:id', to: 'moved/:id', pr: 2 },
      { from: 'never-shipped', to: 'new/x', pr: 3 },
    ]
    expect(againstTheCatalogue(table, catalogue)).toEqual([
      '#/old/x?read=1 is retired to #/new/x, and the catalogue says it lands on #/new/x?read=1',
      '#/old/x?read=1 is retired to #/new/x, which is not in the catalogue',
      '#/old/x?read=1 carries read, and its redirect drops it',
      '#/old/y is retired to #/new/y, and the catalogue says it lands on #/new/elsewhere',
      '#/old/y is retired to #/new/y, which is not in the catalogue',
      '#/gone/z is retired to #/moved/z, which opens #/moved/z/again in turn',
      'never-shipped: it retires no address of the catalogue',
    ])
    // Keeping the key is what passes.
    expect(againstTheCatalogue([{ from: 'old/:id', to: 'new/:id', keeps: { read: 'read' }, pr: 1 }], catalogue.slice(0, 1).concat(catalogue[2])))
      .toEqual([])
    expect(unownedLandings(table, catalogue, [{ route: 'moved/z', why: 'planted' }, { route: 'never-lands', why: 'planted' }])).toEqual([
      '#/kept-by-a-page lands on #/somewhere with no redirect: retire it in routeRedirects.ts, or say why its page owns the landing',
      '#/never-lands is listed as a page\'s own landing, and it is not one',
    ])
  })
})

// ── The router ───────────────────────────────────────────────────────────────────────────────────

/** Every address the router handed its page, in the order it rendered them. */
let seen: string[] = []

function Probe() {
  const { route, sub, query } = useHashRoute('dashboard')
  const q = new URLSearchParams(query).toString()
  seen.push(`${[route, sub].filter(Boolean).join('/')}${q ? `?${q}` : ''}`)
  return null
}

/** What the browser does on Back or Forward, and what the desktop shell's link does: the hash moves
 *  and a hashchange lands, with no `navigate` anywhere. */
function hashChangeTo(hash: string): void {
  act(() => {
    history.replaceState(null, '', hash)
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  })
}

describe('the router sends a retired address to its new one', () => {
  beforeEach(() => {
    history.replaceState(null, '', '#/dashboard')
    seen = []
  })
  afterEach(cleanup)

  it('🔑 opened in the bar (a copied link, a bookmark, a reload), it is rewritten before any page sees it, in place', () => {
    history.replaceState(null, '', '#/files/report-1?dir=%2Ftmp')
    const entries = history.length
    render(<Probe />)
    expect(seen.filter((a) => !a.startsWith('artifacts/')), 'the page was handed the old address').toEqual([])
    expect(seen.at(-1)).toBe('artifacts/report-1')
    expect(location.hash).toBe('#/artifacts/report-1')
    expect(history.length, 'replaced, not pushed: Back never returns to it').toBe(entries)
  })

  it('🔑 reached by Back, Forward or a link the shell opens, it is rewritten in place', () => {
    render(<Probe />)
    const entries = history.length
    hashChangeTo('#/settings/vocabulary')
    expect(seen.filter((a) => a.startsWith('settings/vocabulary'))).toEqual([])
    expect(seen.at(-1)).toBe('settings/voice?section=vocabulary')
    expect(location.hash).toBe('#/settings/voice?section=vocabulary')
    expect(history.length).toBe(entries)
  })

  it('🔑 passed to navigate, it is the new address that is written, as one history entry, and Back leaves it', async () => {
    const { result } = renderHook(() => useHashRoute('dashboard'))
    const entries = history.length
    act(() => result.current.navigate('files/report-1'))
    expect(location.hash).toBe('#/artifacts/report-1')
    expect(history.length).toBe(entries + 1)
    act(() => { window.dispatchEvent(new HashChangeEvent('hashchange')) })
    expect(result.current.route).toBe('artifacts')
    expect(result.current.sub).toBe('report-1')
    // jsdom delivers the push's own hashchange on its event loop: let it land, then go Back.
    await act(async () => { await new Promise((r) => setTimeout(r, 0)) })
    history.back()
    await act(async () => {
      for (let i = 0; i < 100 && location.hash !== '#/dashboard'; i++) await new Promise((r) => setTimeout(r, 10))
    })
    expect(location.hash, 'Back returns to the page before, not to the old address').toBe('#/dashboard')
    expect(result.current.route).toBe('dashboard')
  })

  it('an address no row retires is left as it was', () => {
    history.replaceState(null, '', '#/files?file=src%2Fapp.py')
    render(<Probe />)
    expect(location.hash).toBe('#/files?file=src%2Fapp.py')
    expect(seen.at(-1)).toBe('files?file=src%2Fapp.py')
  })
})
