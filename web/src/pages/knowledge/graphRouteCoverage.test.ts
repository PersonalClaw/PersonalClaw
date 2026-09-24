import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The knowledge graph must be REACHABLE as a route (KL-17) ──────────────────────────────────
//
// The graph is not a page. It is `KnowledgeListPage`'s `view` query param — one of Library /
// Graph / Intents — so navigating `#/knowledge` renders the DEFAULT view (`library`) and the
// graph never mounts. The harness scanned `knowledge`, got the library, and reported clean: the
// graph had no axe scan and no visual baseline, for the same structural reason `learning` had
// none (see the note in e2e/routes.ts) and the same reason 30 settings panels had none.
//
// 🔑 SMALLEST HONEST FIX: the graph ALREADY has a URL. `useHashRoute` splits the query off the
// path, `App.tsx` hands `query` to `KnowledgeSection`, which hands it to `KnowledgeListPage`,
// which reads `view` from it. So `#/knowledge?view=graph` resolves and mounts the graph today —
// nothing in the page had to be restructured. What was missing was a harness entry that could
// NAME it, which is `VIEW_ROUTES` in e2e/routes.ts.
//
// 🪤 WHAT BEING LISTED DOES AND DOES NOT BUY. Measured on this tree: `artifacts` and `learning`
// are both in ROUTES and neither has a committed baseline. Enumeration is necessary and not
// sufficient — the axe scan runs from the list in CI, but a visual baseline is a PNG that only
// `npm run e2e:update` can produce, on a Darwin box with the pinned browser. This rail therefore
// asserts the two things that ARE checkable at unit speed: that the route is enumerated and
// consumed by both specs, and that it genuinely resolves to the graph. It cannot and does not
// assert that a baseline image exists.
//
// 🪤 The harness's gateway starts with an EMPTY home, and the graph renders behind `!empty`, so
// what axe scans on this route is the graph tab's empty state until the harness seeds entities
// (entities are SQLite-only — the note at the top of graphMarkContrast.test.ts records the same
// obstacle). The route being covered is the fix; the marks themselves are still measured by
// graphMarkContrast.test.ts, from source, for every file that draws them.

const WEB = process.cwd()
const read = (p: string) => readFileSync(join(WEB, p), 'utf8')

const ROUTES_SRC = read('e2e/routes.ts')
const VISUAL_SRC = read('e2e/visual.spec.ts')
const A11Y_SRC = read('e2e/a11y.spec.ts')

interface Entry { route: string; id?: string }

/** Entries of a `RouteEntry[]` literal in e2e/routes.ts, by export name. */
function entries(exportName: string): Entry[] {
  const block = ROUTES_SRC.match(new RegExp(`export const ${exportName}: RouteEntry\\[\\] = \\[(.*?)\\n\\]`, 's'))
  expect(block, `could not locate the ${exportName} literal in e2e/routes.ts`).toBeTruthy()
  return [...block![1].matchAll(/route: '([^']+)'(?:[^\n]*?id: '([^']+)')?/g)]
    .map((m) => ({ route: m[1], id: m[2] }))
}

/** The nav routes, for checking that a sub-view hangs off a real page. */
function navRoutes(): string[] {
  const block = ROUTES_SRC.match(/export const ROUTES: RouteEntry\[\] = \[(.*?)\n\]/s)
  expect(block, 'could not locate the ROUTES literal').toBeTruthy()
  return [...block![1].matchAll(/route: '([^']+)'/g)].map((m) => m[1])
}

const VIEW_ROUTES = entries('VIEW_ROUTES')
const GRAPH_ROUTE = 'knowledge?view=graph'

describe('the knowledge graph is reachable as a harness route', () => {
  it('parses a non-empty sub-view list — the vacuity floor', () => {
    // A regex that stopped matching would make every assertion below trivially true, which is
    // exactly how a route manifest rots silently.
    expect(VIEW_ROUTES.length, 'VIEW_ROUTES parsed as empty — the matcher is broken').toBeGreaterThan(0)
    expect(navRoutes().length, 'the ROUTES matcher must still find the nav routes').toBeGreaterThan(10)
  })

  it('enumerates the graph', () => {
    expect(VIEW_ROUTES.map((e) => e.route), `#/${GRAPH_ROUTE} must be in the harness manifest`)
      .toContain(GRAPH_ROUTE)
  })

  it('gives every query-param route a filesystem-safe artifact id', () => {
    // `?` and `=` in a screenshot baseline or an axe attachment name is a broken filename, and
    // on some filesystems a silently mangled one. A query-param route MUST carry an id.
    for (const { route, id } of VIEW_ROUTES) {
      expect(id, `${route} carries a query string, so it needs an explicit id`).toBeTruthy()
      expect(id!, `${id} must be filesystem-safe`).toMatch(/^[a-z0-9-]+$/)
    }
  })

  it('keeps every artifact name unique across all three lists', () => {
    // A collision would have two surfaces writing one baseline — the second silently overwrites
    // the first, and the harness reports both as passing.
    const all = [...entries('ROUTES'), ...VIEW_ROUTES].map((e) => e.id ?? e.route)
    const panels = (ROUTES_SRC.match(/export const SETTINGS_PANELS = \[([\s\S]*?)\] as const/) ?? [])[1] ?? ''
    all.push(...[...panels.matchAll(/'([a-z-]+)'/g)].map((m) => `settings/${m[1]}`))
    expect(all.length, 'the id sweep found nothing to check').toBeGreaterThan(10)
    expect(new Set(all).size, `duplicate harness artifact ids: ${all.join(', ')}`).toBe(all.length)
  })

  it('is consumed by BOTH harness specs, so the list is not an inert declaration', () => {
    // The failure this closes: a manifest entry nothing iterates. Declared-and-unread is the
    // worst shape a control can ship in — it reads as coverage and measures nothing.
    for (const [name, src] of [['visual.spec.ts', VISUAL_SRC], ['a11y.spec.ts', A11Y_SRC]] as const) {
      expect(src, `${name} must iterate VIEW_ROUTES`).toMatch(/\.\.\.VIEW_ROUTES/)
      expect(src, `${name} must name its artifact by id, so no '?' reaches a filename`)
        .toMatch(/id \?\? route/)
    }
  })

  it('resolves to the graph — every link of the chain, from source', () => {
    // Derived from the route STRING, so editing the route to something that no longer mounts the
    // graph fails HERE rather than as a mysteriously blank baseline.
    const [path, qs] = GRAPH_ROUTE.split('?')
    const [param, value] = qs.split('=')

    // 1. the base path is a real nav page the harness already knows
    expect(navRoutes(), `${path} must be a nav route`).toContain(path)

    // 2. the router splits a query off the hash path, so `#/path?x=y` resolves to `path`
    const hash = read('src/app/useHashRoute.ts')
    expect(hash, 'the hash router must parse a query string').toMatch(/new URLSearchParams\(qs\)/)
    expect(hash, 'and must resolve the route from the path only').toMatch(/segs\[0\] \|\| fallback/)

    // 3. the shell routes that path to the knowledge section, passing the query through
    const app = read('src/app/App.tsx')
    expect(app).toMatch(new RegExp(`case '${path}': return <KnowledgeSection \\{\\.\\.\\.r\\} />`))

    // 4. which hands `query`/`setQuery` to the list page.
    //    NOT `<KnowledgeListPage[^>]*…`: the props include arrow functions, so a matcher that
    //    stops at the first `>` never reaches the props it is looking for and reds on a correct
    //    file. Non-greedy across the element instead.
    expect(read('src/pages/knowledge/KnowledgeSection.tsx'))
      .toMatch(/<KnowledgeListPage[\s\S]*?query=\{query\} setQuery=\{setQuery\}/)

    // 5. which reads THIS param name — and defaults to something else, which is precisely why
    //    scanning the bare nav route never reached the graph
    const page = read('src/pages/knowledge/KnowledgeListPage.tsx')
    const decl = new RegExp(`useQueryParam\\(query, setQuery, '${param}', '([a-z]+)'`).exec(page)
    expect(decl, `KnowledgeListPage must read the '${param}' query param`).toBeTruthy()
    expect(decl![1], `the default view must NOT be '${value}', or this route would be redundant`)
      .not.toBe(value)

    // 6. and renders the graph at THIS value
    expect(page, `view === '${value}' must render the graph`)
      .toMatch(new RegExp(`view === '${value}' &&[\\s\\S]{0,400}<KnowledgeGraph`))
  })
})
