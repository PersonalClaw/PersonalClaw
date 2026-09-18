import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── SKILLS → BROWSE MUST HAVE AT LEAST ONE SCOPE THAT RETURNS SOMETHING (issue #301) ──────────────
//
// Browse was a closed trap on a stock install, from two independent bugs that composed:
//
//   1. the server's unscoped fan-out DROPPED every already-installed hit. The gateway copies the whole
//      bundled skill tree into the user's skills dir at startup, and the `native` marketplace is
//      registered against that same bundled dir — so `native`'s ids were ALWAYS a subset of the
//      installed set and "all marketplaces" answered 0 results for EVERY query, structurally. It also
//      inverted the endpoint's own documented contract: unscoped returned strictly less than scoped.
//   2. the frontend's marketplace `<select>` filtered out BOTH registered marketplaces
//      (`x.name !== 'installed' && x.name !== 'native'`), leaving only the hardcoded "All
//      marketplaces" option — so the user could not escape the broken default either.
//
// Both halves are pinned, in one file, because either alone still reads as broken: fixing the server
// leaves a dropdown with no marketplace in it, and fixing the dropdown leaves a default scope that
// returns nothing. The cross-language shape follows the house rails (`promisedMechanismsExist`) — a
// frontend claim is only true if the Python behind it is.

const SRC = join(__dirname, '..', '..')
const PY = join(__dirname, '../../../../src/personalclaw')
const strip = (t: string) => t
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '')
  .replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
const page = strip(readFileSync(join(SRC, 'pages', 'skills', 'SkillsPage.tsx'), 'utf8'))
const handlers = readFileSync(join(PY, 'dashboard', 'handlers', 'skills.py'), 'utf8')
const marketplace = readFileSync(join(PY, 'skills', 'marketplace.py'), 'utf8')

/** A python `def` body, to the next top-level `def`/`async def`. */
function pyFn(src: string, signature: string): string {
  const at = src.indexOf(signature)
  expect(at, `${signature} must exist`).toBeGreaterThan(0)
  const rest = src.slice(at + signature.length)
  const end = rest.search(/\n(?:async )?def /)
  return end === -1 ? rest : rest.slice(0, end)
}

describe('the marketplace picker offers a scope that works', () => {
  it('reads its subject', () => {
    expect(page.length, 'SkillsPage.tsx did not read').toBeGreaterThan(5_000)
    expect(page, 'the picker is still fed from the registry').toMatch(/api\.skillMarketplaces\(\)/)
  })

  it('🔴 does not narrow the registry to nothing', () => {
    const filter = page.slice(page.indexOf('api.skillMarketplaces()'))
      .slice(0, 300)
    // `/api/skills/marketplaces` returns exactly {installed, native} on a stock install. Excluding
    // both yields [], zero <option>s, and a "try a different marketplace" hint over no other choice.
    expect(filter, 'native must remain selectable').not.toMatch(/!==\s*'native'/)
    // `installed` stays out on its own terms — it mirrors the user's own skills dir, so scoping to it
    // would search the Installed tab from the Browse tab.
    expect(filter, "and `installed` is still excluded").toMatch(/!==\s*'installed'/)
  })
})

describe('the unscoped fan-out returns what a scoped one would', () => {
  it('reads its subject', () => {
    expect(handlers, 'skills.py did not read').toMatch(/def search_marketplaces_counted/)
  })

  it('🔴 annotates already-installed hits instead of dropping them', () => {
    const fn = pyFn(handlers, 'def search_marketplaces_counted(')
    // The exact regression: a comprehension that filters on the installed set.
    expect(fn, 'no result may be withheld for being installed')
      .not.toMatch(/\bfor\s+r\s+in\s+all_results\s+if\s+r\.id\s+not\s+in/)
    expect(fn, 'and nothing filters on installed-ness at all')
      .not.toMatch(/not\s+in\s+installed_names/)
    expect(fn, 'installed-ness is stamped, through the one shared marker').toMatch(/_mark_installed\(/)
    // The `installed` SOURCE is still skipped — a different fact, and the correct one.
    expect(fn, 'the installed mirror is still not fanned out to')
      .toMatch(/if\s+name\s*==\s*"installed"/)
  })

  it('and the annotation reaches the wire on BOTH branches', () => {
    expect(marketplace, 'SkillEntry carries it').toMatch(/installed:\s*bool\s*=\s*False/)
    const toDict = pyFn(marketplace, 'def to_dict(')
    expect(toDict, 'and serialises it').toMatch(/"installed":\s*self\.installed/)
    const endpoint = pyFn(handlers, 'async def api_skills_search(')
    // A row is installed or not regardless of how it was asked for; two answers for one row is how
    // the frontend ends up unable to annotate anything.
    expect((endpoint.match(/_mark_installed\(/g) ?? []).length,
      'the scoped branch stamps it too').toBeGreaterThanOrEqual(1)
  })

  it('the frontend labels a row from the server, not only from this session', () => {
    expect(page, 'the row badge reads the annotation').toMatch(/installedIds\.has\(r\.id\)\s*\|\|\s*!!r\.installed/)
    expect(page, 'and so does the detail panel').toMatch(/installedIds\.has\(open\.id\)\s*\|\|\s*!!open\.installed/)
  })
})
