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
const EXEMPT_FROM_THE_HARNESS: Record<string, string> = {
  notifications: 'attention surface; reached from the header bell, no nav tile',
  discover: 'store/discovery surface reached from Apps',
  loop: 'loop detail — the Loop nav tile was retired; launched from within Projects',
  loops: 'loop history/planning sub-route (#/loops/<id>)',
  code: 'code sub-route of a loop (#/code/<id>)',
  app: 'per-app surface (#/app/<name>), one route per installed app',
  'mission-control':
    'locked DASHBOARD VIEW registered server-side (views_store._mission_control_preset), ' +
    'reached only from the command palette until a rail is built from /api/dashboard/views. ' +
    'NOT yet axe-scanned — an owner call on harness scope, recorded rather than assumed.',
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
      expect(Object.keys(EXEMPT_FROM_THE_HARNESS).length).toBeGreaterThan(3)
    })

    it('every non-nav routable page is either in the manifest or declared exempt', () => {
      const nav = navIds()
      const manifest = manifestRoutes()
      const undeclared = routableExtras().filter(
        (r) => !nav.includes(r) && !manifest.includes(r) && !(r in EXEMPT_FROM_THE_HARNESS),
      )
      expect(
        undeclared,
        'These routes are reachable in the SPA (they are in App.tsx ROUTABLE) but are in no nav\n' +
          'tile, no e2e route manifest, and no exemption — so they get NO axe scan and NO visual\n' +
          'baseline, and nothing says that was intended. Add them to web/e2e/routes.ts to scan\n' +
          'them, or to EXEMPT_FROM_THE_HARNESS above with the reason they cannot be.',
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
      const manifest = manifestRoutes()
      const both = Object.keys(EXEMPT_FROM_THE_HARNESS).filter((r) => manifest.includes(r))
      expect(both, 'these routes are declared exempt AND scanned — drop the exemption').toEqual([])
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
