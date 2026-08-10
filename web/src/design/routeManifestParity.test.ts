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
// Scope: NAV ids only. `App.tsx` also declares a `ROUTABLE` set with six routes that have no
// nav tile on purpose — `notifications`, `discover`, `loop`, `loops`, `code`, `app`. The
// manifest's own contract is "the nav-reachable routes of the SPA", so those are a deliberate
// distinction, not drift. If the harness should cover them too that is a product decision
// about baseline scope, not a sync bug — and it would need its own owner call, because
// `loop`/`loops`/`code` carry the already-logged overflowing-control-row taste call and would
// red the axe gate on arrival.

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
})
