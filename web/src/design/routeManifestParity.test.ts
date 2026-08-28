import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The e2e route manifest must actually mirror NAV ──────────────────────────
//
// `web/e2e/routes.ts` calls itself the "single source of truth for the visual/axe harness"
// and says "Keep in sync with App.tsx NAV". Both the visual spec and the a11y spec iterate it,
// so a nav route missing from that list gets **no accessibility scan and no visual baseline**.
//
// `learning` was missing — 18 NAV ids against 17 manifest entries. The gap was silent by
// construction: the manifest is the only thing that would have reported it, so the page it
// omitted was the one page nobody was checking. "Keep in sync" was a comment, not a contract;
// this test makes it one.
//
// Parsed from source rather than imported because `App.tsx` pulls in the whole SPA (lazy
// routes, framer-motion, the theme provider) and this assertion is about two LISTS, not about
// rendering. A regex over the two literals is the cheapest thing that can actually fail.
//
// Scope: NAV ids only. `App.tsx` also declares a `ROUTABLE` set carrying routes that have no
// nav tile on purpose. The manifest's own contract is "the nav-reachable routes of the SPA", so
// those are a deliberate distinction, not drift. If the harness should cover them too that is a
// product decision about baseline scope, not a sync bug — and it would need its own owner call,
// because `loop`/`loops`/`code` carry the already-logged overflowing-control-row taste call and
// would red the axe gate on arrival.
//
// 🪤 That exemption used to live in this comment ALONE, and it had already drifted. The prose
// named six extras; `App.tsx` had seven — `mission-control` was added later (a locked dashboard
// view reachable from the command palette) and so became an authenticated, routable page with
// NO axe scan, NO visual baseline, and nothing anywhere saying that was intended. A NAV↔manifest
// parity rail cannot see that: `mission-control` is in neither list, so both directions stay
// green. An exemption a human has to remember to update is the same shape as the bug this file
// was written to catch, one axis over. So the list below is now a CONTRACT: a new non-nav
// routable page reds this test, and whoever adds it either scans it or declares it here.
// Declaring it is cheap; the point is that it becomes a decision instead of a silence.
// 🪤 The second drift, found while closing the first (PHF-7): three of these "exemptions"
// stated why the page has no NAV TILE, not why the harness cannot reach it —
// `notifications`, `discover` and `mission-control` are all plain parameterless routes that
// `renderPage` serves off a bare `#/<id>`. "No nav tile" is not a reason a page cannot be
// scanned, and reading it as one is how three authenticated pages stayed unscanned. They
// now live in `NON_NAV_ROUTES` in web/e2e/routes.ts and are axe-scanned in both themes.
// What survives here is the residue that genuinely cannot be reached by a bare route.
const EXEMPT_FROM_THE_HARNESS: Record<string, string> = {
  loop: 'loop detail — needs a loop to address; also carries the logged overflowing-control-row taste call',
  loops: 'loop history/planning sub-route (#/loops/<id>) — needs a loop id to render a record',
  code: 'code sub-route of a loop (#/code/<id>) — needs a loop id to render a record',
  app: 'per-app surface (#/app/<name>) — needs an installed app name; one route per app',
}

const WEB = process.cwd()

/** The `id` fields of `const NAV: NavItem[] = [...]` in App.tsx, in order. */
function navIds(): string[] {
  const src = readFileSync(join(WEB, 'src/app/App.tsx'), 'utf8')
  const block = src.match(/const NAV: NavItem\[\] = \[(.*?)\n\]/s)
  if (!block) throw new Error('could not locate the NAV literal in App.tsx')
  return [...block[1].matchAll(/\{\s*id: '([^']+)'/g)].map((m) => m[1])
}

/** The `route` fields of `export const ROUTES: RouteEntry[] = [...]` in e2e/routes.ts. */
function manifestRoutes(): string[] {
  const src = readFileSync(join(WEB, 'e2e/routes.ts'), 'utf8')
  const block = src.match(/export const ROUTES: RouteEntry\[\] = \[(.*?)\n\]/s)
  if (!block) throw new Error('could not locate the ROUTES literal in e2e/routes.ts')
  return [...block[1].matchAll(/route: '([^']+)'/g)].map((m) => m[1])
}

/** The `route` fields of `export const NON_NAV_ROUTES: RouteEntry[] = [...]` in e2e/routes.ts. */
function nonNavRoutes(): string[] {
  const src = readFileSync(join(WEB, 'e2e/routes.ts'), 'utf8')
  const block = src.match(/export const NON_NAV_ROUTES: RouteEntry\[\] = \[(.*?)\n\]/s)
  if (!block) throw new Error('could not locate the NON_NAV_ROUTES literal in e2e/routes.ts')
  return [...block[1].matchAll(/route: '([^']+)'/g)].map((m) => m[1])
}

/** Every route the harness actually scans, across all of its lists. */
function scannedRoutes(): string[] {
  return [...manifestRoutes(), ...nonNavRoutes()]
}

/** The string literals in `ROUTABLE` that are NOT spread in from NAV — the non-nav routes. */
function routableExtras(): string[] {
  const src = readFileSync(join(WEB, 'src/app/App.tsx'), 'utf8')
  const block = src.match(/const ROUTABLE = new Set\(\[(.*?)\]\)/s)
  if (!block) throw new Error('could not locate the ROUTABLE literal in App.tsx')
  return [...block[1].matchAll(/'([^']+)'/g)].map((m) => m[1])
}

describe('e2e route manifest vs NAV', () => {
  it('parses both lists (guards against a silently-empty sweep)', () => {
    // A regex that stops matching would make every assertion below vacuously true.
    expect(navIds().length).toBeGreaterThan(10)
    expect(manifestRoutes().length).toBeGreaterThan(10)
  })

  it('covers every nav route', () => {
    const missing = navIds().filter((id) => !manifestRoutes().includes(id))
    expect(
      missing,
      'These nav routes are absent from web/e2e/routes.ts, so they get NO axe scan and NO ' +
        'visual baseline — the harness cannot report a page it does not know about.',
    ).toEqual([])
  })

  it('lists no route that is not in NAV', () => {
    // The other direction: a stale entry snapshots a route the shell no longer serves, which
    // fails as a mysterious blank baseline rather than as "this route is gone".
    const stale = manifestRoutes().filter((r) => !navIds().includes(r))
    expect(stale, 'web/e2e/routes.ts lists routes that NAV no longer has').toEqual([])
  })

  // ── The THIRD axis: routable-but-not-nav pages, which neither check above can see ────────
  describe('non-nav routable pages are scanned or declared', () => {
    it('parses the ROUTABLE extras (guards against a silently-empty sweep)', () => {
      // If this regex stops matching, `extras` is [] and the contract test below passes for a
      // repo whose every non-nav route is unscanned and undeclared.
      expect(routableExtras().length).toBeGreaterThan(3)
      // Both escape hatches must PARSE. An empty EXEMPT map is legitimate now (it would mean
      // every non-nav route is scanned), but a NON_NAV_ROUTES literal that stopped parsing
      // would throw rather than silently narrow the scanned set — that is what the parser's
      // own `throw` is for. Assert it is non-empty so the list cannot quietly become dead.
      expect(nonNavRoutes().length).toBeGreaterThan(0)
      expect(Object.keys(EXEMPT_FROM_THE_HARNESS).length).toBeGreaterThan(0)
    })

    it('every non-nav routable page is either scanned or declared exempt', () => {
      const nav = navIds()
      const scanned = scannedRoutes()
      const undeclared = routableExtras().filter(
        (r) => !nav.includes(r) && !scanned.includes(r) && !(r in EXEMPT_FROM_THE_HARNESS),
      )
      expect(
        undeclared,
        'These routes are reachable in the SPA (they are in App.tsx ROUTABLE) but are in no nav\n' +
          'tile, no e2e route manifest, and no exemption — so they get NO axe scan and NO visual\n' +
          'baseline, and nothing says that was intended. Add them to NON_NAV_ROUTES in\n' +
          'web/e2e/routes.ts to scan them, or to EXEMPT_FROM_THE_HARNESS above with the reason\n' +
          'they cannot be. "It has no nav tile" is NOT such a reason — renderPage serves every\n' +
          'ROUTABLE id off a bare #/<id>, so state what the harness cannot supply (a record id,\n' +
          'an installed app) or scan it.',
      ).toEqual([])
    })

    it('declares no exemption for a route that no longer exists', () => {
      // A stale exemption is a policy for a page nobody serves — it silently widens the
      // allowance for whatever route later reuses the name.
      const routable = new Set(routableExtras())
      const stale = Object.keys(EXEMPT_FROM_THE_HARNESS).filter((r) => !routable.has(r))
      expect(stale, 'EXEMPT_FROM_THE_HARNESS names routes App.tsx no longer routes to').toEqual([])
    })

    it('exempts no route that IS already scanned', () => {
      // Both claims cannot be true; one of them is a lie the next reader would trust.
      const scanned = scannedRoutes()
      const both = Object.keys(EXEMPT_FROM_THE_HARNESS).filter((r) => scanned.includes(r))
      expect(both, 'these routes are declared exempt AND scanned — drop the exemption').toEqual([])
    })

    it('scans no non-nav route the shell does not route to', () => {
      // The mirror of the stale-exemption check. A NON_NAV_ROUTES entry App.tsx no longer
      // serves would scan the ONBOARDING shell and report it clean — a green test for a page
      // that is gone, which is worse than no test.
      const routable = new Set(routableExtras())
      const stale = nonNavRoutes().filter((r) => !routable.has(r))
      expect(stale, 'NON_NAV_ROUTES names routes App.tsx no longer routes to').toEqual([])
    })

    it('keeps NON_NAV_ROUTES disjoint from NAV and from ROUTES', () => {
      // A nav route listed here would be scanned twice and, worse, would satisfy the
      // "covers every nav route" check from the wrong list — the nav page could then be
      // dropped from ROUTES and lose its VISUAL baseline without anything reding.
      const nav = navIds()
      const manifest = manifestRoutes()
      const overlap = nonNavRoutes().filter((r) => nav.includes(r) || manifest.includes(r))
      expect(
        overlap,
        'these routes are in NON_NAV_ROUTES and also in NAV/ROUTES — a nav route belongs in\n' +
          'ROUTES, which is what also gives it a visual baseline',
      ).toEqual([])
    })

    it('every exemption carries a real reason', () => {
      // "later" is not a reason. The text has to say what the route is and why the harness
      // cannot reach it — which is what makes the next person able to disagree with it.
      const thin = Object.entries(EXEMPT_FROM_THE_HARNESS)
        .filter(([, why]) => why.trim().length < 30)
        .map(([r]) => r)
      expect(thin, 'these exemptions have no stated reason').toEqual([])
    })
  })
})

// ── The FOURTH axis: a tier must reach every spec that claims whole-app coverage ─────────────
//
// The three checks above police which ROUTES the manifest lists. They cannot see which SPECS
// consume it — and `scannedRoutes()` above is named "every route the harness actually scans",
// which quietly assumes one harness. There are three specs, and a tier only covers a route in
// the spec that spreads it.
//
// 🪤 THIS ALREADY HAPPENED, AND THIS FILE'S OWN COMMENT RECORDS HALF OF IT. `NON_NAV_ROUTES`
// exists because `notifications`, `discover` and `mission-control` "stayed unscanned"; the note
// above closes that with "they now live in NON_NAV_ROUTES … and are axe-scanned in both themes."
// Precisely true, and precisely the problem: the tier was wired into `a11y.spec.ts` and NOT into
// `walkthrough.spec.ts`, whose `SURFACES` spread three tiers of four. So those three pages were
// axe-scanned and simultaneously exempt from all three walkthrough legs — keyboard focus
// visibility, reduced motion, and phone overflow — which are the three properties CI's own step
// comment says "axe cannot express any of these three". 3 routes × 3 legs × 2 themes = 18 tests
// that never existed, and nothing could report their absence because the population was a
// hand-written spread list.
//
// So the tier set is DERIVED from routes.ts here, and equality is asserted for the specs whose
// contract is whole-app. `visual.spec.ts` is a declared, self-clearing exception: a screenshot
// per route per theme is a real per-route cost, so its narrower scope is a decision rather than
// drift — but it is asserted to be exactly the declared subset, so a fifth tier forces a call
// for it too instead of being silently omitted.

/** Every `export const X: RouteEntry[]` in e2e/routes.ts — the tier vocabulary, derived. */
function routeTiers(): string[] {
  const src = readFileSync(join(WEB, 'e2e/routes.ts'), 'utf8')
  return [...src.matchAll(/export const ([A-Z_]+): RouteEntry\[\]/g)].map((m) => m[1])
}

/** Which of those tiers a spec spreads into its own surface population. */
function tiersSpreadBy(spec: string): string[] {
  const src = readFileSync(join(WEB, `e2e/${spec}`), 'utf8')
  const tiers = new Set(routeTiers())
  const found = new Set<string>()
  for (const m of src.matchAll(/\.\.\.([A-Z_]+)/g)) if (tiers.has(m[1])) found.add(m[1])
  return [...found]
}

/** Specs whose stated contract is "every reachable surface" — no per-route cost but time. */
const WHOLE_APP_SPECS = ['a11y.spec.ts', 'walkthrough.spec.ts']

/** The one declared narrower consumer, and the tiers it deliberately omits. Self-clearing: if it
 *  starts covering one of these, this reds and tells you to delete the entry, so the exception
 *  can never be wider than the code needs. */
const NARROWER_BY_DESIGN = {
  spec: 'visual.spec.ts',
  omits: ['SETTINGS_ROUTES', 'NON_NAV_ROUTES'],
  why:
    'a full-page screenshot per route per theme is a real per-route cost in baseline bytes and ' +
    'review time, unlike the axe and walkthrough legs which only cost wall-clock',
}

describe('every route tier reaches every spec that claims whole-app coverage', () => {
  it('parses the tiers and the spreads (guards against a silently-empty sweep)', () => {
    // Without this, a regex that stopped matching would make every check below compare [] to []
    // and pass for a harness covering nothing.
    expect(routeTiers().length, 'no RouteEntry[] tiers parsed from e2e/routes.ts').toBeGreaterThanOrEqual(4)
    for (const spec of [...WHOLE_APP_SPECS, NARROWER_BY_DESIGN.spec]) {
      expect(tiersSpreadBy(spec).length, `no route tiers parsed from e2e/${spec}`).toBeGreaterThan(0)
    }
  })

  it.each(WHOLE_APP_SPECS)('%s spreads every tier', (spec) => {
    const missing = routeTiers().filter((t) => !tiersSpreadBy(spec).includes(t))
    expect(
      missing,
      `e2e/${spec} claims whole-app coverage but does not spread these tier(s) from ` +
        `e2e/routes.ts, so every route in them is exempt from that spec's contract while ` +
        `looking covered. Add the spread, or move the tier's routes somewhere that says they ` +
        `are out of scope.`,
    ).toEqual([])
  })

  it(`${NARROWER_BY_DESIGN.spec} omits exactly the tiers it declares`, () => {
    const spread = tiersSpreadBy(NARROWER_BY_DESIGN.spec)
    const actuallyOmitted = routeTiers().filter((t) => !spread.includes(t))
    expect(
      actuallyOmitted.sort(),
      `e2e/${NARROWER_BY_DESIGN.spec}'s omissions no longer match what it declares. Either it ` +
        `gained coverage (delete the entry from NARROWER_BY_DESIGN.omits) or a new tier was ` +
        `added and nobody decided whether it needs a visual baseline.\n  reason on record: ` +
        NARROWER_BY_DESIGN.why,
    ).toEqual([...NARROWER_BY_DESIGN.omits].sort())
  })
})
