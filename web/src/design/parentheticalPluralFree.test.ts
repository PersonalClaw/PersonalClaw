import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── No composed sentence hedges its own count ────────────────────────────────────────────────
//
// `0 proposal(s) filed` is the single most recognisable tell of unfinished product copy, and this tree
// carried it in **43 places**: roll-back confirmations, the Introspect answers, the restructure
// summary, the degraded-surfaces tooltip, the first-run import notice, a maturity title, a tool-output
// label. Every one had its count one token to the left, so none of them needed the shortcut.
//
// The canonical form is the inline conditional — measured at **156 sites** against ~43 hedges, which is
// what settled it as drift rather than house style. Two page-local `plural()` helpers exist
// (`settings/PortabilityPanel`, `settings/MemoryGraph`); a third copy was deliberately NOT added,
// because three implementations of one idiom is worse than 156 uses of one expression.
//
// ── Why this rail could not be written until now ──
//
// Two earlier passes deferred it, correctly, because the population was **known to be wrong**. The
// obvious detector — `(s)` preceded by a letter — cannot tell a hedged noun from a function call:
// `new Set(s).add(id)`, `open(s)`, `decodeURIComponent(s)`, `String(s)` all match. A tree-wide run
// reported **80** against a hand count near 40, and "a ratchet on a population known to be wrong is
// worse than none" is this repo's own rule.
//
// 🔑 THE DISCRIMINATOR THAT WORKS ANCHORS ON THE RENDERED NUMBER. A hedged noun always follows a count
// — an interpolation `}` or a literal digit — then at most a few words. A call's callee never does:
// it follows `.`, `(`, `${` or a line start. Measured on `main`: **49 matches, zero false positives**,
// where the follower-character version had ~40% noise.
//
//     (?:\}|\d)[^`'"()]{0,40}?\s[a-z][a-z-]*\((?:s|es)\)
//
// The `[^`'"()]` window keeps it inside one string and stops it spanning into an argument list; the
// lazy bound keeps it from reaching across a whole line to an unrelated call.

const SRC = join(process.cwd(), 'src')

const HEDGE = /(?:\}|\d)[^`'"()]{0,40}?\s[a-z][a-z-]*\((?:s|es)\)/
const stripComments = (t: string) =>
  t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

/** Every `.ts`/`.tsx` under `src/`, excluding tests. */
function sources(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) sources(p, out)
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(p)
  }
  return out
}

/** 🏁 THE EIGHT THAT WERE ONCE EXEMPT — now a POSITIVE regression set.
 *
 *  These were excluded while their conversions sat in open PRs (`learning/*` · `DurabilityPanel` ·
 *  `IntrospectPanel` · `DegradedChip` + `ImportStep` + `genui/registry`). Excluding rather than
 *  asserting-still-broken was deliberate: a self-clearing guard would have reded `main` the moment any
 *  one of them merged, because a PR cannot delete an entry from a file that does not exist on its own
 *  base. So every merge order stayed green and the list simply went vacuous.
 *
 *  🔑 All eight have merged (#2236, #2238, #2243, #2245), so the exemption is retired — and rather than
 *  deleting the names, they are INVERTED. The scan no longer skips them; it must reach each one and find
 *  it clean. An exemption list that becomes a regression list is strictly stronger than one that
 *  disappears, because these eight are the family's hardest members and the most likely to regress.
 *
 *  Verified before the change: emptying the exclusion list left this rail GREEN (4/4), which is the
 *  proof the family is closed rather than merely exempted. */
const ONCE_EXEMPT_NOW_CLEAN = [
  'pages/learning/HealthPanel.tsx',
  'pages/learning/LearningPage.tsx',
  'pages/learning/learningMeta.ts',
  'pages/settings/DurabilityPanel.tsx',
  'pages/workflows/IntrospectPanel.tsx',
  'ui/DegradedChip.tsx',
  'app/onboarding/ImportStep.tsx',
  'ui/genui/registry.ts',
]

/** Backend-composed sentences this tree renders VERBATIM by design, so their grammar is not ours to
 *  change here. Both are recorded in the rails that keep them verbatim:
 *  `workflows/introspection.py`'s branch-distribution string and the `proof.summary` line. Converting
 *  them means changing the Python producer, which is a separate concern (§4 rows 56, 63-adjacent). */
const BACKEND_VERBATIM_NOTE =
  'strings composed in Python and rendered verbatim are excluded by construction — they never appear ' +
  'as literals in this tree, so the scan cannot see them and must not pretend to.'

function offenders(): string[] {
  const out: string[] = []
  for (const abs of sources(SRC)) {
    const rel = abs.slice(SRC.length + 1).replace(/\\/g, '/')
    stripComments(readFileSync(abs, 'utf8')).split('\n').forEach((line, i) => {
      if (HEDGE.test(line) && !/http\(s\)/.test(line)) {
        out.push(`${rel}:${i + 1}  ${line.trim().slice(0, 90)}`)
      }
    })
  }
  return out
}

describe('no composed sentence hedges its own count', () => {
  it('the detector still works, in both directions', () => {
    // 🪤 A "zero matches" claim is equally satisfied by a pattern that cannot match, which is how the
    // §5 `[^;]` sweep reported an area clean while two offenders sat in it. Positive controls first.
    expect(HEDGE.test('`${week.produced_total} proposal(s) filed`'), 'must catch an interpolation').toBe(true)
    expect(HEDGE.test('over 4 pass(es), and'), 'must catch (es) after a literal digit').toBe(true)
    expect(HEDGE.test('`${n} of ${files.length} file(s) to this point?`'), 'must catch the second count').toBe(true)
    // …and must NOT fire on a call whose argument happens to be `s` or `es`.
    expect(HEDGE.test('setBusy((s) => new Set(s).add(id))'), 'Set(s).add is code').toBe(false)
    expect(HEDGE.test('onClick={() => open(s)}'), 'open(s) is code').toBe(false)
    expect(HEDGE.test('`${location.pathname}#/chat/${encodeURIComponent(s)}`'), 'encodeURIComponent is code').toBe(false)
    expect(HEDGE.test('return `${m}:${String(s).padStart(2, "0")}`'), 'String(s) is code').toBe(false)
    // Permitted: a protocol, not a plural.
    expect(HEDGE.test('// allow relative, anchors, mailto/tel, http(s)')).toBe(false)
  })

  it('the scan reads the tree (a scan over nothing reports everything clean)', () => {
    const all = sources(SRC)
    expect(all.length, 'no sources found under src/').toBeGreaterThan(400)
    // And it reaches each of the eight that were once exempt. This was the vacuity half of the old
    // exclusion list; now it is a regression guard, so a rename that silently drops one of the family's
    // hardest members out of the walk fails here instead of passing quietly.
    for (const rel of ONCE_EXEMPT_NOW_CLEAN) {
      expect(
        all.some((a) => a.endsWith(rel)),
        `${rel} was once exempt and the walk no longer sees it — stale entry or broken walk`,
      ).toBe(true)
    }
  })

  it('no file composes a hedged plural', () => {
    expect(
      offenders(),
      'The count is already in hand at each of these. Write the sentence:\n' +
        "  `${n} thing${n === 1 ? '' : 's'}`  — the form 156 other sites in this tree already use.\n" +
        'Do NOT add a local `plural()` helper; two page-local copies already exist and a third makes ' +
        'the eventual consolidation bigger.\n' +
        BACKEND_VERBATIM_NOTE,
    ).toEqual([])
  })

  it('🏁 the rail exempts NOTHING — the end state the interim list was aiming at', () => {
    // The old ceiling bounded an interim exclusion list at 8 and said "the follow-up that lands after
    // those merge deletes the list entirely". This is that follow-up, so the assertion inverts: there is
    // no exemption mechanism left to grow. If a future pass reintroduces one, this fails and the reason
    // has to be argued rather than added.
    const src = readFileSync(join(SRC, 'design/parentheticalPluralFree.test.ts'), 'utf8')
    const code = stripComments(src)
    expect(code, 'no file may be skipped by name — convert the site, do not exempt it')
      .not.toMatch(/\.includes\(rel\)\s*\)\s*continue/)
    // And the eight formerly-exempt files are asserted CLEAN by the sweep above rather than skipped,
    // which is what makes the inversion real rather than cosmetic.
    expect(ONCE_EXEMPT_NOW_CLEAN.length, 'the family\'s hardest members stay named').toBe(8)
    // One scan, checked eight times — offenders() walks the whole tree, and calling
    // it per file ran ~9 full scans in one test, which is a 20s timeout on a loaded
    // machine (and this test gates every push via the pre-push hook).
    const found = offenders()
    for (const rel of ONCE_EXEMPT_NOW_CLEAN) {
      expect(found.some((o) => o.startsWith(rel)), `${rel} regressed a hedged plural`).toBe(false)
    }
  })
})
