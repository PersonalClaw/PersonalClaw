/** Every address a producer makes is in the shipped-route catalogue, and every producer an entry
 *  names makes it (#3819).
 *
 *  `shippedRoutes.ts` is the one list of the addresses the app has handed out. A list written by
 *  hand forgets the address added after it, so each producer that can be read from here is read,
 *  and its addresses are held inside the list:
 *
 *    · the `personalclaw://` parser (`desktop/deepLinks.js`): each route and alias it takes opens a
 *      catalogued address, each entry naming it is a link the parser opens exactly as written, and
 *      each state parameter it takes appears in one of them;
 *    · the menu-bar item (`desktop/trayPresence.js` `DEEP_LINKS`, and Quick Capture's flag);
 *    · the router's tables and the palette's rows (`App.tsx`), the notification taps
 *      (`nativeNotifications.ts`), the approval destinations (`approvalDestination.ts`), and the
 *      e2e route manifest (`web/e2e/routes.ts`).
 *
 *  Each check is a function over a producer's addresses, so its control plants an address the
 *  catalogue lacks and shows the same function reporting it.
 */
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { join } from 'node:path'
import * as manifest from '../../e2e/routes'
import { approvalDestination } from './approvalDestination'
import { numberedThreads } from './threadRail'
import { chatFindPath } from '../pages/chat/searchDeepLink'
import { DEFAULT_NOTIFICATION_ROUTE, NOTIFICATION_SOURCE_ROUTES } from '../lib/nativeNotifications'
import type { ThreadChat, ThreadLoop } from '../lib/useThreads'
import { RELEASES, SAMPLE, SHIPPED_ROUTES, UNRELEASED, type RouteSource, type ShippedRoute } from './shippedRoutes'

const REPO = join(import.meta.dirname, '..', '..', '..')
const desktop = createRequire(join(REPO, 'desktop', 'package.json'))
const parser = desktop('./deepLinks.js') as {
  ROUTES: Set<string>
  ALIASES: Record<string, string>
  PARAMETERS: [string, unknown][]
  parseDeepLink: (raw: string) => { ok: true; route: string; hash: string } | { ok: false; reason: string }
}
const tray = desktop('./trayPresence.js') as { DEEP_LINKS: Record<string, string | ((id: string) => string)> }
const APP = readFileSync(join(import.meta.dirname, 'App.tsx'), 'utf8')

/** An address as the router reads it: no `#/`, and its query in one order. */
function normal(address: string): string {
  const bare = address.replace(/^#?\/?/, '')
  const at = bare.indexOf('?')
  if (at < 0) return bare
  const query = [...new URLSearchParams(bare.slice(at + 1))].sort(([a, x], [b, y]) => a.localeCompare(b) || x.localeCompare(y))
  return `${bare.slice(0, at)}?${new URLSearchParams(query).toString()}`
}

/** The addresses in `produced` the catalogue has no entry for. */
function uncatalogued(produced: readonly string[], catalogue: readonly ShippedRoute[] = SHIPPED_ROUTES): string[] {
  const known = new Set(catalogue.map((e) => normal(e.route)))
  return [...new Set(produced.map(normal))].filter((a) => !known.has(a)).sort()
}

/** The entries that name `source` while `source` does not make them. */
function misattributed(source: RouteSource, produced: readonly string[], catalogue: readonly ShippedRoute[] = SHIPPED_ROUTES): string[] {
  const made = new Set(produced.map(normal))
  return catalogue.filter((e) => e.from.includes(source) && !made.has(normal(e.route))).map((e) => e.route).sort()
}

/** The entries that do not name `source` while `source` makes them. */
function unattributed(source: RouteSource, produced: readonly string[], catalogue: readonly ShippedRoute[] = SHIPPED_ROUTES): string[] {
  const made = new Set(produced.map(normal))
  return catalogue.filter((e) => made.has(normal(e.route)) && !e.from.includes(source)).map((e) => e.route).sort()
}

const PLANTED = 'zz-never-shipped'

// ── The personalclaw:// parser ───────────────────────────────────────────────────────────────────

/** The address `personalclaw://<link>` opens, or why the parser refuses it. */
function opens(link: string): string {
  const r = parser.parseDeepLink(`personalclaw://${link}`)
  return r.ok ? r.hash.slice(2) : `refused: ${r.reason}`
}

/** The address each of the parser's routes and aliases opens, bare. */
function parserAddresses(routes: Iterable<string>, aliases: Record<string, string>): string[] {
  return [...routes, ...Object.keys(aliases)].map(opens)
}

/** The entries naming the parser that it does not open exactly as written. */
function notOpenedAsWritten(catalogue: readonly ShippedRoute[]): string[] {
  return catalogue.filter((e) => e.from.includes('deep-link') && opens(e.route) !== e.route)
    .map((e) => `${e.route} -> ${opens(e.route)}`)
}

/** The parser's state parameters no entry naming it carries. */
function parametersUncarried(catalogue: readonly ShippedRoute[]): string[] {
  const carried = new Set(catalogue.filter((e) => e.from.includes('deep-link'))
    .flatMap((e) => [...new URLSearchParams(e.route.split('?')[1] ?? '').keys()]))
  return parser.PARAMETERS.map(([key]) => key).filter((key) => !carried.has(key))
}

describe('the personalclaw:// parser opens only catalogued addresses', () => {
  const addresses = parserAddresses(parser.ROUTES, parser.ALIASES)

  it('🔑 each route and alias it takes opens an address in the catalogue, which names the parser', () => {
    expect(parser.ROUTES.size, 'vacuity floor: the parser\'s routes look truncated').toBeGreaterThanOrEqual(20)
    // `threads` is the one alias left since `automations` became a route of its own (#5167).
    expect(Object.keys(parser.ALIASES)).toContain('threads')
    expect(addresses.filter((a) => a.startsWith('refused')), 'a route the parser lists and refuses').toEqual([])
    expect(uncatalogued(addresses), 'opened by a link, missing from shippedRoutes.ts').toEqual([])
    expect(unattributed('deep-link', addresses), 'these entries open from a link and do not say so').toEqual([])
  })

  it('🔑 each entry that names the parser is a link it opens, exactly as the entry writes it', () => {
    expect(SHIPPED_ROUTES.filter((e) => e.from.includes('deep-link')).length).toBeGreaterThanOrEqual(30)
    expect(notOpenedAsWritten(SHIPPED_ROUTES)).toEqual([])
  })

  it('each state a link can carry is carried by one of them', () => {
    expect(parser.PARAMETERS.length, 'vacuity floor').toBeGreaterThanOrEqual(8)
    expect(parametersUncarried(SHIPPED_ROUTES)).toEqual([])
  })

  it('🔴 control: a route the parser gained, an entry it refuses and a parameter nobody carries are each reported', () => {
    // The parser's own set, grown for the length of this check: the parser takes the route, so the
    // link opens it, and the catalogue lacks it.
    parser.ROUTES.add(PLANTED)
    try {
      expect(uncatalogued(parserAddresses(parser.ROUTES, parser.ALIASES))).toContain(PLANTED)
    } finally {
      parser.ROUTES.delete(PLANTED)
    }
    const refusedByIt: ShippedRoute = { route: 'chat/new?artifact=report-2', from: ['deep-link'] }
    expect(notOpenedAsWritten([refusedByIt])).toEqual(['chat/new?artifact=report-2 -> refused: a parameter the app does not take'])
    const reordered: ShippedRoute = { route: `code/${SAMPLE.code}?task=${SAMPLE.task}&pane=changes`, from: ['deep-link'] }
    expect(notOpenedAsWritten([reordered]), 'the parser writes its parameters in its own order').toHaveLength(1)
    expect(parametersUncarried(SHIPPED_ROUTES.filter((e) => !e.route.includes('commit=')))).toContain('commit')
  })
})

// ── The menu-bar item ────────────────────────────────────────────────────────────────────────────

/** Every hash the menu-bar item writes: each `DEEP_LINKS` row (an id-taking row given the sample
 *  loop) and Quick Capture, which `desktop/main.js` builds as the inbox's link with `?capture=1`
 *  (`desktop/test/trayPresence.test.js` holds that line). */
function trayAddresses(links: Record<string, string | ((id: string) => string)>): string[] {
  const rows = Object.values(links).map((v) => (typeof v === 'function' ? v(SAMPLE.loop) : v))
  return [...rows, `${links.inbox}?capture=1`]
}

describe('the menu-bar item opens only catalogued addresses', () => {
  const addresses = trayAddresses(tray.DEEP_LINKS)

  it('🔑 every DEEP_LINKS row and Quick Capture is an entry, and every entry naming the tray is one of them', () => {
    expect(Object.keys(tray.DEEP_LINKS).length, 'vacuity floor').toBeGreaterThanOrEqual(5)
    expect(addresses.every((a) => a.startsWith('#/')), 'the tray writes hashes').toBe(true)
    expect(uncatalogued(addresses), 'the tray opens these, and shippedRoutes.ts lacks them').toEqual([])
    expect(misattributed('tray', addresses), 'these entries say the tray opens them; it does not').toEqual([])
    expect(unattributed('tray', addresses)).toEqual([])
  })

  it('🔴 control: a row the tray gained is reported', () => {
    expect(uncatalogued(trayAddresses({ ...tray.DEEP_LINKS, planted: `#/${PLANTED}` }))).toContain(PLANTED)
    expect(uncatalogued(trayAddresses({ ...tray.DEEP_LINKS, task: (id) => `#/tasks/${id}` }))).toContain(`tasks/${SAMPLE.loop}`)
  })
})

// ── The router and the palette ───────────────────────────────────────────────────────────────────

/** The router's top-level routes in `App.tsx` source: `NAV`'s ids, `ROUTABLE`'s extras and
 *  `SHELL_ROUTES` (read as `routeCasesCoverRoutable.test.ts` reads them). */
function routerRoutes(app: string): string[] {
  const nav = app.slice(app.indexOf('const NAV: NavItem[] = ['), app.indexOf('const ROUTABLE'))
  const extras = app.match(/const ROUTABLE = new Set\(\[\.\.\.NAV\.map\(\(n\) => n\.id\), ([^\]]+)\]/)?.[1] ?? ''
  const shell = app.match(/const SHELL_ROUTES = new Set\(\[([^\]]*)\]\)/)?.[1] ?? ''
  return [
    ...[...nav.matchAll(/id: '([^']+)'/g)].map((m) => m[1]),
    ...[...extras.matchAll(/'([^']+)'/g)].map((m) => m[1]),
    ...[...shell.matchAll(/'([^']+)'/g)].map((m) => m[1]),
  ]
}

/** Where the palette's rows go: a "Go to" row per `NAV` entry, every literal `navigate('…')` in the
 *  rows and in the keymap's runners, and the rows built from the owner's data with the sample's
 *  shape: a pinned app's tile, ⌘1 to ⌘9's threads (a chat, a goal loop, a code session and a
 *  run-backed loop) and a content search's hit. */
function paletteAddresses(app: string): string[] {
  const nav = app.slice(app.indexOf('const NAV: NavItem[] = ['), app.indexOf('const ROUTABLE'))
  const rows = app.slice(app.indexOf('const commands: Command[] = ['), app.indexOf('...keymapCommands(keyRunners)'))
  const keys = app.slice(app.indexOf('const keyRunners'), app.indexOf("useKeyBinding('sidebar'"))
  const chat = { key: SAMPLE.chat, origin: 'manual' } as ThreadChat
  const loops = [
    { id: SAMPLE.loop, kind: 'goal' },
    { id: SAMPLE.code, kind: 'code' },
    { id: SAMPLE.run, kind: 'general', run_id: SAMPLE.run },
  ] as ThreadLoop[]
  const threads = numberedThreads({ sessions: [chat], loops, items: [], approvals: [] }, [], {})
  return [
    ...[...nav.matchAll(/id: '([^']+)'/g)].map((m) => m[1]),
    ...[...(rows + keys).matchAll(/navigate\('([^']+)'\)/g)].map((m) => m[1]),
    `app/${SAMPLE.app}`,
    ...threads.map((t) => t.route),
    chatFindPath(SAMPLE.chat, 'report'),
  ]
}

describe('the router\'s tables and the palette\'s rows are catalogued', () => {
  const router = routerRoutes(APP)
  const palette = paletteAddresses(APP)

  it('🔑 every top-level route the router serves is an entry that names it, with the release it shipped in', () => {
    expect(router.length, 'vacuity floor: the tables were not found').toBeGreaterThanOrEqual(25)
    expect(router).toContain('dashboard')
    expect(router).toContain('companion')
    expect(uncatalogued(router)).toEqual([])
    expect(unattributed('router', router)).toEqual([])
  })

  it('an entry naming the router is a top-level route, with the release it shipped in', () => {
    const rows = SHIPPED_ROUTES.filter((e) => e.from.includes('router'))
    expect(rows.filter((e) => /[/?]/.test(e.route)).map((e) => e.route), 'the router names routes, not their sub-paths').toEqual([])
    expect(rows.filter((e) => !e.since).map((e) => e.route), 'read its release from the tags').toEqual([])
  })

  it('🔑 every palette destination is an entry, and every entry naming the palette is one', () => {
    expect(palette.length, 'vacuity floor').toBeGreaterThanOrEqual(22)
    expect(palette).toEqual(expect.arrayContaining(['mission-control', 'inbox?filter=notifications', 'discover', 'settings', 'chat/new']))
    expect(palette, 'Notifications is a filter of the Inbox (#3818): its row opens the filter, not the retired page')
      .not.toContain('notifications')
    // ⌘1 to ⌘9 open a thread of every kind at the address it has always had.
    expect(palette).toEqual(expect.arrayContaining([
      `chat/${SAMPLE.chat}`, `loops/${SAMPLE.loop}`, `code/${SAMPLE.code}`, `workflows/runs/${SAMPLE.run}`,
    ]))
    expect(uncatalogued(palette)).toEqual([])
    expect(misattributed('palette', palette)).toEqual([])
    expect(unattributed('palette', palette)).toEqual([])
  })

  it('🔴 control: a route the router gained and a row the palette gained are each reported', () => {
    const grown = APP
      .replace("const SHELL_ROUTES = new Set(['onboarding',", `const SHELL_ROUTES = new Set(['${PLANTED}', 'onboarding',`)
      .replace("{ id: 'act:settings',", `{ id: 'go:planted', run: () => navigate('${PLANTED}/sub') }, { id: 'act:settings',`)
    expect(grown, 'the plant took').not.toBe(APP)
    expect(uncatalogued(routerRoutes(grown))).toContain(PLANTED)
    expect(uncatalogued(paletteAddresses(grown))).toContain(`${PLANTED}/sub`)
  })
})

// ── Notification taps and approval destinations ─────────────────────────────────────────────────

/** Where a notification's tap lands, every source and the fallback. */
const notificationAddresses = [...Object.values(NOTIFICATION_SOURCE_ROUTES), DEFAULT_NOTIFICATION_ROUTE]

/** Where an approval is answered, for each place `approvalDestination` reads (`approval_place`). */
const approvalAddresses = [
  { work: `workflow:${SAMPLE.run}`, step: SAMPLE.node },
  { work: `loop:${SAMPLE.loop}` },
  { room: SAMPLE.room },
  { work: `chat:${SAMPLE.chat}` },
  { session: SAMPLE.chat, work: '' },
].map((place) => approvalDestination(place).href)

describe('the notification taps and the approval links are catalogued', () => {
  it('🔑 every tap\'s route is an entry naming notifications, and every such entry is one', () => {
    expect(Object.keys(NOTIFICATION_SOURCE_ROUTES).length, 'vacuity floor').toBeGreaterThanOrEqual(10)
    expect(uncatalogued(notificationAddresses)).toEqual([])
    expect(misattributed('notification', notificationAddresses)).toEqual([])
    expect(unattributed('notification', notificationAddresses)).toEqual([])
  })

  it('🔑 every approval\'s destination is an entry naming approvals, and every such entry is one', () => {
    expect(new Set(approvalAddresses).size, 'each shape lands somewhere of its own').toBe(4)
    expect(uncatalogued(approvalAddresses)).toEqual([])
    expect(misattributed('approval', approvalAddresses)).toEqual([])
    expect(unattributed('approval', approvalAddresses)).toEqual([])
  })

  it('🔴 control: a tap or an approval landing somewhere new is reported', () => {
    expect(uncatalogued([...notificationAddresses, PLANTED])).toContain(PLANTED)
    expect(uncatalogued([...approvalAddresses, `#/workflows/runs/${SAMPLE.run}?step=${SAMPLE.node}`]))
      .toContain(`workflows/runs/${SAMPLE.run}?step=${SAMPLE.node}`)
  })
})

// ── The e2e route manifest ───────────────────────────────────────────────────────────────────────

/** The manifest's own fixture names, as the catalogue's samples spell them. */
const FIXTURE_NAMES: Record<string, string> = {
  'not-a-real-app': SAMPLE.app,
  'e2e-ui-fixture': SAMPLE.app,
  'not-a-real-item': SAMPLE.knowledge,
}

function manifestAddresses(tiers: readonly (readonly { route: string }[])[]): string[] {
  return tiers.flat().map((r) => r.route.replace(/[a-z0-9-]+/g, (word) => FIXTURE_NAMES[word] ?? word))
}

describe('every address the e2e manifest visits is catalogued', () => {
  const tiers = [manifest.ROUTES, manifest.SETTINGS_ROUTES, manifest.VIEW_ROUTES, manifest.SUBSTATE_ROUTES, manifest.NON_NAV_ROUTES]

  it('🔑 each tier\'s routes are entries', () => {
    expect(manifestAddresses(tiers).length, 'vacuity floor').toBeGreaterThanOrEqual(80)
    expect(uncatalogued(manifestAddresses(tiers))).toEqual([])
  })

  it('🔑 every Settings page is an entry, with the release it shipped in', () => {
    const pages = manifest.SETTINGS_PANELS.map((id) => `settings/${id}`)
    expect(pages.length, 'vacuity floor').toBeGreaterThanOrEqual(35)
    const byRoute = new Map(SHIPPED_ROUTES.map((e) => [e.route, e]))
    expect(pages.filter((p) => !byRoute.get(p)?.since), 'read its release from the tags').toEqual([])
  })

  it('🔴 control: a route the manifest gained is reported', () => {
    expect(uncatalogued(manifestAddresses([...tiers, [{ route: `settings/${PLANTED}` }]]))).toContain(`settings/${PLANTED}`)
  })
})

// ── The catalogue itself ─────────────────────────────────────────────────────────────────────────

describe('the catalogue is well formed', () => {
  it('each entry is one address, opened once per kind of home, from at least one producer', () => {
    const seen = SHIPPED_ROUTES.map((e) => `${normal(e.route)}|${e.home ?? ''}`)
    expect(seen.filter((k, i) => seen.indexOf(k) !== i), 'listed twice').toEqual([])
    expect(SHIPPED_ROUTES.filter((e) => e.from.length === 0).map((e) => e.route)).toEqual([])
    expect(SHIPPED_ROUTES.filter((e) => /^[#/]|\s/.test(e.route)).map((e) => e.route), 'written as the router writes an address').toEqual([])
  })

  it('a landing is another address, and a release is one of the tags, or not yet one', () => {
    expect(SHIPPED_ROUTES.filter((e) => e.lands !== undefined && normal(e.lands) === normal(e.route)).map((e) => e.route)).toEqual([])
    const releases: readonly string[] = [...RELEASES, UNRELEASED]
    expect(SHIPPED_ROUTES.filter((e) => e.since && !releases.includes(e.since)).map((e) => e.route)).toEqual([])
  })
})
