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
  it('🔑 navigates to the dashboard when the route is not routable', () => {
    expect(src).toMatch(/if \(route && !ROUTABLE\.has\(route\)\) navigate\('dashboard'/)
  })

  it('replaces rather than pushes', () => {
    // A push would leave the bogus hash in history: Back would return the user to the broken URL
    // they were just rescued from, and re-running this effect would bounce them forward again.
    expect(src).toMatch(/!ROUTABLE\.has\(route\)\) navigate\('dashboard', \{ replace: true \}\)/)
  })

  it('is gated so it cannot fight the onboarding redirect', () => {
    // `#/onboarding` is deliberately NOT in ROUTABLE, so an ungated correction would compete with
    // the onboarding effect for the route.
    const effect = src.slice(src.indexOf("if (route && !ROUTABLE.has(route))") - 400)
    expect(effect).toMatch(/if \(!loaded \|\| !onboarded\) return[\s\S]{0,200}!ROUTABLE\.has\(route\)/)
  })

  it('still renders something for that tick rather than blanking', () => {
    // The clamp stays: the effect corrects the URL on the next tick, so this render needs a page.
    expect(src).toContain("const rendered = ROUTABLE.has(route) ? route : 'dashboard'")
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
