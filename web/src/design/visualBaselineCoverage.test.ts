import { describe, expect, it } from 'vitest'
import { readdirSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import { ROUTES, VIEW_ROUTES, THEMES } from '../../e2e/routes'

// ── Every surface the visual gate snapshots must HAVE a committed baseline ───────────────────
//
// `e2e/visual.spec.ts` iterates `[...ROUTES, ...VIEW_ROUTES] × THEMES` and calls
// `expectRouteScreenshot`. A surface with no committed golden does not skip and does not pass:
// Playwright writes the actual image and FAILS. Verified rather than assumed — deleting
// `terminal-light-darwin.png` and running that one test gives `Expected:
// e2e/__screenshots__/…/terminal-light-darwin.png`, `1 failed`.
//
// So a route added to `routes.ts` without capturing baselines turns the visual suite red for a
// missing FILE, which reads identically to a real regression. That happened: three routes
// (`artifacts`, `learning`, `knowledge-graph`) are in the manifest with no goldens, contributing 6
// of the 28 failures measured on an untouched `main` (the other 22 are render drift).
//
// ── Why this rail lives in vitest and not in the Playwright suite ──
//
// Because **no CI job runs `visual.spec.ts`**. The only `playwright test` invocation under
// `.github/workflows/` is `e2e/a11y.spec.ts`. A missing-baseline check inside the visual suite would
// therefore be as unexecuted as the suite it guards. This file runs in the `web` job's vitest step,
// which does run, so the manifest and the goldens cannot drift apart unnoticed again — which is the
// actual cause of the mess above, not any individual missing file.
//
// It deliberately checks FILE EXISTENCE only. It cannot and should not compare pixels: that is the
// visual suite's job, it is platform-qualified, and it needs a browser.

const BASELINES = join(process.cwd(), 'e2e', '__screenshots__', 'visual.spec.ts')

/** `<id|route>-<theme>` — the `arg` half of playwright's `snapshotPathTemplate`. */
function expectedKeys(): string[] {
  const keys: string[] = []
  for (const theme of THEMES) {
    for (const { route, id } of [...ROUTES, ...VIEW_ROUTES]) {
      keys.push(`${id ?? route}-${theme}`)
    }
  }
  return keys
}

/** Committed goldens grouped by the `{platform}` suffix playwright appends. */
function byPlatform(): Map<string, Set<string>> {
  const out = new Map<string, Set<string>>()
  for (const name of readdirSync(BASELINES)) {
    const m = /^(.+)-([a-z0-9]+)\.png$/.exec(name)
    if (!m) continue
    const [, key, platform] = m
    if (!out.has(platform)) out.set(platform, new Set())
    out.get(platform)!.add(key)
  }
  return out
}

/** Surfaces in the manifest with no committed golden, recorded rather than silently tolerated.
 *
 *  SELF-CLEARING: the test below asserts each entry is STILL missing, so capturing a baseline turns
 *  this file red and names the entry to delete. A plain count would absorb the capture and leave
 *  permanent slack.
 *
 *  Not captured here on purpose. This dev machine's render disagrees with the committed set — 22 of
 *  the existing goldens fail on it — so baselines captured here would be inconsistent with their 32
 *  neighbours and would look like a mass visual regression to whoever owns them. Capturing belongs
 *  on a machine that matches the committed set, or as a wholesale recapture. */
const UNCAPTURED: { key: string; why: string }[] = [
  { key: 'artifacts-light', why: 'route added to routes.ts without capturing goldens' },
  { key: 'artifacts-dark', why: 'route added to routes.ts without capturing goldens' },
  { key: 'learning-light', why: 'route added to routes.ts without capturing goldens' },
  { key: 'learning-dark', why: 'route added to routes.ts without capturing goldens' },
  { key: 'knowledge-graph-light', why: 'VIEW_ROUTE added without capturing goldens' },
  { key: 'knowledge-graph-dark', why: 'VIEW_ROUTE added without capturing goldens' },
]

describe('the visual gate has a committed baseline for every surface it snapshots', () => {
  it('the baseline directory and the route manifest are both non-empty (vacuity floor)', () => {
    // Either side being empty would make every assertion below trivially true: an empty manifest
    // expects nothing, and an empty directory would report every surface missing — which the
    // allowance list would then have to grow to cover, quietly.
    expect(existsSync(BASELINES), `the baseline directory is gone: ${BASELINES}`).toBe(true)
    expect(readdirSync(BASELINES).length, 'no committed goldens at all').toBeGreaterThan(10)
    expect(expectedKeys().length, 'the route manifest yielded no surfaces').toBeGreaterThan(10)
  })

  it('every platform that has ANY baseline has a COMPLETE set', () => {
    // The failure this prevents is a PARTIAL capture, which is exactly how the current gap arose:
    // someone snapshots the routes they touched and the manifest silently outgrows the goldens.
    // Scoped per platform because the suffix is part of the filename — a half-captured `-linux` set
    // would make the suite unrunnable on CI in precisely the way it is today.
    const platforms = byPlatform()
    expect(platforms.size, 'no platform-suffixed goldens found — has the naming changed?').toBeGreaterThan(0)

    const allowed = new Set(UNCAPTURED.map((u) => u.key))
    const problems: string[] = []
    for (const [platform, have] of platforms) {
      const missing = expectedKeys().filter((k) => !have.has(k) && !allowed.has(k))
      if (missing.length) {
        problems.push(`${platform}: ${missing.length} missing → ${missing.join(', ')}`)
      }
    }
    expect(
      problems,
      `a surface in routes.ts has no committed golden for a platform that has others. ` +
        `e2e/visual.spec.ts does not skip it — playwright writes the actual image and FAILS, so a ` +
        `missing file is indistinguishable from a real regression in that report. Capture with ` +
        `\`npm run e2e:update\` and commit the goldens in the same change as the route:\n  ` +
        problems.join('\n  '),
    ).toEqual([])
  })

  it('every recorded UNCAPTURED surface is still uncaptured', () => {
    // Self-clearing. Without this the list would silently widen the gate the moment someone did the
    // right thing, and the next missing route would hide behind a stale entry.
    const have = new Set<string>()
    for (const set of byPlatform().values()) for (const k of set) have.add(k)
    for (const { key, why } of UNCAPTURED) {
      expect(
        have.has(key),
        `UNCAPTURED names "${key}", but a baseline now exists for it. The gate is wider than the ` +
          `repo needs: DELETE the entry.\n  ${why}`,
      ).toBe(false)
    }
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
