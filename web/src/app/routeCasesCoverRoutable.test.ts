/** An unknown hash route corrects its URL, and every routable route has a case (#306).
 *
 *  `#/nonsense` rendered the DASHBOARD while leaving `#/nonsense` in the address bar: `rendered`
 *  clamps an unknown route to `'dashboard'`, and nothing corrected the hash. So a typo, a stale
 *  bookmark, or a link from an older version all looked like the dashboard had simply moved there,
 *  with nothing to tell the user which — and the `default:` branch that would have said something
 *  was unreachable dead code.
 *
 *  These are source assertions on purpose. The route resolution is a `switch` over 25 lazy-loaded
 *  page modules inside the app shell; mounting `App` to reach it would load the entire SPA and
 *  assert on mocks of the thing under test. What is actually verifiable — and what broke — is a
 *  relationship between two hand-maintained lists (`ROUTABLE` and the `case` labels) and the
 *  presence of the correcting effect. Both are read from the real file.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const APP = resolve(__dirname, 'App.tsx')
const src = readFileSync(APP, 'utf8')

/** `App.tsx` with comment-only lines removed.
 *
 *  🪤 Learned in cycle 194 and hit again here: the fix's own comment QUOTES the copy it replaced
 *  ("coming soon") to explain what changed, so a raw-text search reported the defect as still
 *  present. A rail that reds on an accurate explanation and greens on a rename is worse than none.
 *  Only the copy assertions need this; the structural ones read `src` because a `case` label or a
 *  `navigate(...)` call inside a comment is not a thing this codebase does. */
const code = src
  .split('\n')
  .filter((l) => {
    const t = l.trim()
    return !t.startsWith('//') && !t.startsWith('*') && !t.startsWith('/*')
  })
  .join('\n')

/** `NAV`'s ids — the block is defined inline in App.tsx. */
function navIds(): string[] {
  const block = src.slice(src.indexOf('const NAV: NavItem[] = ['), src.indexOf('const ROUTABLE'))
  return [...block.matchAll(/id: '([^']+)'/g)].map((m) => m[1])
}

/** The extra strings `ROUTABLE` adds beyond `NAV`. */
function routableExtras(): string[] {
  const line = src.match(/const ROUTABLE = new Set\(\[\.\.\.NAV\.map\(\(n\) => n\.id\), ([^\]]+)\]/)
  if (!line) return []
  return [...line[1].matchAll(/'([^']+)'/g)].map((m) => m[1])
}

/** Every `case '<x>':` label in the render switch. */
function caseLabels(): string[] {
  return [...src.matchAll(/case '([^']+)':/g)].map((m) => m[1])
}

/** The `SHELL_ROUTES` literal — the routes the shell renders from an early return. */
function shellRoutes(): string[] {
  const line = code.match(/const SHELL_ROUTES = new Set\(\[([^\]]*)\]\)/)
  if (!line) return []
  return [...line[1].matchAll(/'([^']+)'/g)].map((m) => m[1])
}

/** Every route the component returns for ITSELF, full-screen, instead of handing it to the nav
 *  switch — read off `App`'s own top-level `if (route === '<x>')` guards.
 *
 *  Anchored at exactly two spaces of indentation, which is what distinguishes a guard in the
 *  component body from the identically-shaped branch four spaces deep inside the route effect.
 *  Read from `code` (comment-free) because this file's own prose names those routes, and a rail
 *  that matches its own explanation measures nothing. */
function earlyReturnRoutes(): string[] {
  return [...new Set([...code.matchAll(/^ {2}if \(route === '([^']+)'/gm)].map((m) => m[1]))]
}

describe('the routable set and the render switch agree', () => {
  it('🪤 every ROUTABLE route has a case — which is what makes `default:` unreachable', () => {
    // Two hand-maintained lists, in one file, that must stay in step. A route added to ROUTABLE
    // with no case would fall to `default:` and render "This view isn't available" — the shape of
    // a shipped nav tile that opens onto nothing.
    const routable = [...navIds(), ...routableExtras()]
    const cases = new Set(caseLabels())
    const uncovered = routable.filter((r) => !cases.has(r))
    expect(uncovered).toEqual([])
  })

  it('🪤 vacuity floor — the scanner actually found the three lists', () => {
    // Every assertion here reads source. A regex that matched nothing would make the check above
    // pass on an empty array, so the sizes are pinned as non-trivial.
    expect(navIds().length).toBeGreaterThanOrEqual(15)
    expect(routableExtras().length).toBeGreaterThanOrEqual(5)
    expect(caseLabels().length).toBeGreaterThanOrEqual(20)
    // And that they overlap at all — a scanner reading two unrelated regions would also pass above.
    expect(caseLabels()).toContain('dashboard')
    expect(navIds()).toContain('dashboard')
  })

  it('no case is registered for a route that is not routable', () => {
    // The other direction: a `case` for a route `ROUTABLE` does not admit is unreachable, because
    // `rendered` can never produce it. Dead code that looks live.
    const routable = new Set([...navIds(), ...routableExtras()])
    // `onboarding` and `companion` are deliberately outside ROUTABLE and handled by early returns
    // above the switch, not by a case.
    const orphans = caseLabels().filter((c) => !routable.has(c))
    expect(orphans).toEqual([])
  })
})

describe('an unknown route corrects its URL', () => {
  it('🔑 navigates to the dashboard when the shell can render nothing for the route', () => {
    expect(code).toMatch(/if \(route && !renderable\(route\)\) \{ navigate\('dashboard'/)
  })

  it('replaces rather than pushes', () => {
    // A push would leave the bogus hash in history: Back would return the user to the broken URL
    // they were just rescued from, and re-running this effect would bounce them forward again.
    expect(code).toMatch(/!renderable\(route\)\) \{ navigate\('dashboard', \{ replace: true \}\)/)
  })

  it('🔴 asks `renderable`, not `ROUTABLE` — the two are not the same question (#3506)', () => {
    // `ROUTABLE` answers "does the nav switch have a case". The corrector's question is "can the
    // shell render this AT ALL", and `onboarding`/`companion` answer yes by early return while
    // answering no to `ROUTABLE`. Testing the wrong one made the corrector rewrite the app's own
    // PWA `start_url` and every onboarding exit deep-link to `#/dashboard`.
    expect(code).toMatch(/const renderable = \(route: string\): boolean =>\s*ROUTABLE\.has\(route\) \|\| SHELL_ROUTES\.has\(route\)/)
    expect(code, 'the corrector must not test ROUTABLE directly').not.toMatch(
      /!ROUTABLE\.has\(route\)\) \{? ?navigate\('dashboard'/)
  })

  it('🔴 every route the shell renders itself is one the corrector will not rewrite (#3506)', () => {
    // The relationship that broke, asserted as a relationship rather than as two literals: a
    // full-screen route added with an early return and NOT added to SHELL_ROUTES would be
    // corrected away a tick after it rendered — reachable by code, unreachable by URL.
    const admitted = new Set(shellRoutes())
    const orphans = earlyReturnRoutes().filter((r) => !admitted.has(r))
    expect(orphans, 'these routes render from an early return but SHELL_ROUTES does not admit them')
      .toEqual([])
  })

  it('🪤 vacuity floor — both route lists were actually found', () => {
    // Either regex matching nothing would make the check above pass on an empty array.
    expect(earlyReturnRoutes().sort()).toEqual(['companion', 'onboarding'])
    expect(shellRoutes().sort()).toEqual(['companion', 'onboarding'])
  })

  it('🔴 the correction is downstream of the onboarding branches, in ONE effect (#3506)', () => {
    // It used to be a second effect "gated on `loaded && onboarded` so it cannot race the
    // onboarding effect above". That gate named the wrong window: at the instant `onboarded`
    // flips, `route` is STILL the stale `'onboarding'`, so both effects fired on one commit and
    // the corrector `replace`d the destination the guard had just pushed. Ordering two effects by
    // a predicate is what failed; the fix is that there is only one, and correction sits after
    // every `return` that precedes it, so it cannot run while a route decision is pending.
    const effects = [...code.matchAll(/useEffect\(\(\) => \{\s*\n\s*if \(!loaded\)/g)]
    expect(effects.length, 'exactly one effect may decide the route').toBe(1)
    const body = code.slice(code.indexOf('if (!loaded) return'))
    const onboardingBranch = body.indexOf("if (route === 'onboarding')")
    const correction = body.indexOf('!renderable(route)')
    expect(onboardingBranch).toBeGreaterThan(-1)
    expect(correction).toBeGreaterThan(onboardingBranch)
    // And the onboarding branch must RETURN, or "downstream" would not mean "unreachable".
    expect(body.slice(onboardingBranch, correction)).toContain('return }')
  })

  it('still renders something for that tick rather than blanking', () => {
    // The clamp stays: the effect corrects the URL on the next tick, so this render needs a page.
    // `ROUTABLE.has` here is correct and deliberate — a SHELL_ROUTES member never reaches the
    // switch, so widening this would hand the switch a route with no case.
    expect(code).toContain("const rendered = ROUTABLE.has(route) ? route : 'dashboard'")
  })
})

describe('the fallback branch says something true', () => {
  it('no longer promises a feature is coming', () => {
    // It read "<label> — coming soon", which was wrong twice: for an unknown route the label
    // resolved to `undefined` (a bare " — coming soon"), and for a ROUTABLE route with a missing
    // case it told the user a feature was PLANNED when the truth is a missing branch.
    expect(code).not.toContain('coming soon')
  })

  it('the switch still has a fallback at all', () => {
    // 🪤 The floor for the assertion above: deleting `default:` would also remove the string and
    // pass, while making the switch return `undefined` for any uncovered route.
    expect(code).toMatch(/default: return </)
  })
})
