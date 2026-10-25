import { describe, expect, it } from 'vitest'
import { readdirSync, existsSync } from 'node:fs'
import { CAPTURED_PLATFORMS, GOLDENS as BASELINES, byPlatform, expectedKeys } from '../../e2e/visual-coverage.mjs'

// ── Every surface the visual gate snapshots must HAVE a committed baseline ───────────────────
//
// `e2e/visual.spec.ts` iterates `[...ROUTES, ...VIEW_ROUTES] × THEMES` and calls
// `expectRouteScreenshot`. A surface with no committed golden does not skip and does not pass:
// Playwright writes the actual image and FAILS. Verified rather than assumed — deleting
// `terminal-light-darwin.png` and running that one test gives `Expected:
// e2e/__screenshots__/…/terminal-light-darwin.png`, `1 failed`.
//
// So a route added to `routes.ts` without capturing baselines turns the visual suite red for a
// missing FILE, which reads identically to a real regression. That happened, and the whole of it
// was measured on an untouched `main` (28924fe80, Darwin): 40 visual tests, 4 passed, 36 failed —
// 8 of those for a MISSING golden (`artifacts`, `learning`, `knowledge-graph`, `knowledge-reading`,
// each × both themes) and the other 28 for real render drift at 0.02–0.05 diff ratio against a 0.01
// cap. Two thirds of a red gate was the harness racing itself and a third of it was absent files;
// none of it was a regression anyone had introduced. That is what a rail nobody can act on looks
// like, and it is why the drift was root-caused (see `e2e/helpers.ts`'s settle helpers) rather than
// absorbed by raising the tolerance.
//
// ── Why this rail lives in vitest and not in the Playwright suite ──
//
// Because the visual suite runs in one leg alone (`e2e-pinned`, against the pinned image's browser),
// and only in the merge tier: a missing golden would be found there, after every cheaper job. This
// file runs in the `web` job's vitest step, on every change, so the manifest and the goldens cannot
// drift apart unnoticed again — which was the actual cause of the mess above, not any individual
// missing file.
//
// It deliberately checks FILE EXISTENCE only. It cannot and should not compare pixels: that is the
// visual suite's job, it is platform-qualified, and it needs a browser.

// The manifest's surfaces, the closed set of platforms and the goldens by platform are read through
// `e2e/visual-coverage.mjs`, which the Refresh visual baselines workflow runs after its visual
// update, so this rail and the refresh hold the goldens to the same list.

// ── There is no allowance list, and that is the point ────────────────────────────────────────
// This rail used to carry an `UNCAPTURED` allowance list of eight manifest surfaces with no golden
// (`artifacts`, `learning`, `knowledge-graph`, `knowledge-reading` × both themes), recorded because
// the dev machine's render disagreed with the committed set and a partial capture would have looked
// like a mass regression. That was resolved the only way it could be resolved — a WHOLESALE
// recapture — so all eight are captured and the list is DELETED rather than emptied.
//
// 🪤 It is deliberately not kept as an empty-but-available escape hatch. An allowance list is the
// one thing that can absorb a ZERO-CAPTURE silently: subtracting `allowed` from `missing` means a
// sweep that captured nothing passes as long as the list grew to match. The invariant below is now
// simply TRUE with no exceptions, so it is asserted with no exceptions. Re-introducing an exception
// is then a visible decision in a diff, which is what it should have been.

describe('the visual gate has a committed baseline for every surface it snapshots', () => {
  it('the baseline directory and the route manifest are both non-empty (vacuity floor)', () => {
    // Either side being empty would make every assertion below trivially true: an empty manifest
    // expects nothing, and an empty directory would report every surface missing.
    expect(existsSync(BASELINES), `the baseline directory is gone: ${BASELINES}`).toBe(true)
    expect(expectedKeys().length, 'the route manifest yielded no surfaces').toBeGreaterThan(10)
    // 🪤 DERIVED from the manifest, not a magic number. This floor used to be `> 10` against a set
    // that is 40 — it would have passed with three quarters of the goldens deleted, which is most
    // of the way to the zero-capture this rail exists to catch. One complete platform's worth is
    // the only defensible floor: fewer files than surfaces means no platform can be complete.
    const floor = expectedKeys().length * CAPTURED_PLATFORMS.length
    expect(
      readdirSync(BASELINES).filter((n) => n.endsWith('.png')).length,
      `fewer committed goldens than ${CAPTURED_PLATFORMS.length} declared platform(s) × ` +
        `${expectedKeys().length} manifest surfaces — so no declared platform can have a complete ` +
        `set, and a capture was either partial or never ran`,
    ).toBeGreaterThanOrEqual(floor)
  })

  it('every DECLARED platform has a complete set, and no undeclared platform has goldens', () => {
    // 🪤 THE HOLE THIS CLOSES, AND WHY IT NEEDS A DECLARED SET RATHER THAN A COUNT. The per-platform
    // check below only ever speaks about platforms that already have at least one file, so it says
    // NOTHING about a platform whose goldens are all gone — an absent platform has no entry to be
    // found incomplete.
    //
    // 🪤 "At least one platform is complete" does NOT close it, and that was measured, not reasoned:
    // written that way first, the falsification case (copy the 40 goldens to `-linux`, delete every
    // `-darwin`) PASSED all four assertions. Any one complete platform satisfies an existential, so
    // the platform developers actually capture on can drop to zero coverage while the rail stays
    // green. Only naming the platforms makes the claim falsifiable.
    //
    // So `CAPTURED_PLATFORMS` is a CLOSED set, asserted in both directions — every declared platform
    // complete, and no undeclared platform carrying goldens. Adding a platform then becomes a
    // deliberate two-part edit (declare it AND capture a full set).
    const want = expectedKeys()
    const platforms = byPlatform()

    const incomplete = CAPTURED_PLATFORMS.map((p) => {
      const have = platforms.get(p) ?? new Set<string>()
      const missing = want.filter((k) => !have.has(k))
      return { p, missing }
    }).filter((r) => r.missing.length > 0)

    expect(
      incomplete.map((r) => `${r.p}: ${r.missing.length}/${want.length} missing → ${r.missing.join(', ')}`),
      `a DECLARED capture platform does not have a golden for every surface in the manifest. An ` +
        `absent or half-deleted set is invisible to the per-platform check (it only inspects ` +
        `platforms that still have files), which is why the platform list is named here:`,
    ).toEqual([])

    expect(
      [...platforms.keys()].filter((p) => !CAPTURED_PLATFORMS.includes(p as never)).sort(),
      `goldens exist for a platform this repo does not declare. Either add it to ` +
        `CAPTURED_PLATFORMS with a full captured set, or delete the stray files — an undeclared ` +
        `half-set is how the suite becomes unrunnable on the platform that owns it:`,
    ).toEqual([])
  })

  it('every platform that has ANY baseline has a COMPLETE set', () => {
    // The failure this prevents is a PARTIAL capture: someone snapshots the routes they touched and
    // the manifest silently outgrows the goldens. Scoped per platform because the suffix is part of
    // the filename — a half-captured `-linux` set would make the suite unrunnable on CI.
    const platforms = byPlatform()
    expect(platforms.size, 'no platform-suffixed goldens found — has the naming changed?').toBeGreaterThan(0)

    const problems: string[] = []
    for (const [platform, have] of platforms) {
      const missing = expectedKeys().filter((k) => !have.has(k))
      if (missing.length) {
        problems.push(`${platform}: ${missing.length} missing → ${missing.join(', ')}`)
      }
    }
    expect(
      problems,
      `a surface in routes.ts has no committed golden for a platform that has others. ` +
        `e2e/visual.spec.ts does not skip it — playwright writes the actual image and FAILS, so a ` +
        `missing file is indistinguishable from a real regression in that report. Make them with ` +
        `the Refresh visual baselines workflow and commit them in the same change as the route:\n  ` +
        problems.join('\n  '),
    ).toEqual([])
  })

  it('no golden is orphaned — every committed baseline maps to a surface still in the manifest', () => {
    // The other direction. A route removed from routes.ts leaves its goldens behind, and a stale
    // 400KB PNG that nothing asserts is dead weight nobody notices.
    const expected = new Set(expectedKeys())
    const orphans: string[] = []
    for (const [platform, have] of byPlatform()) {
      for (const key of have) if (!expected.has(key)) orphans.push(`${key}-${platform}.png`)
    }
    expect(
      orphans,
      `these committed goldens correspond to no surface in routes.ts — the route was removed or ` +
        `renamed and its baseline was left behind:\n  ${orphans.join('\n  ')}`,
    ).toEqual([])
  })
})
