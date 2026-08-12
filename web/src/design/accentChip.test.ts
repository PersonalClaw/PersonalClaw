import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { accentChip } from './accent'

// ── The accent as both ink and background ──────────────────────────────────────────────
//
// 27 inline style objects across 23 files drew an "active" chip as
// `background: color-mix(var(--color-primary) N%, transparent)` with `color: var(--color-primary)`.
// That fails WCAG AA in LIGHT mode at every strength anyone used. Measured, with the arithmetic
// validated by reproducing the 3.33:1 that `ux-audit` and axe independently report for the 20% case:
//
//     primary ink over primary tint, light:  14% → 3.62   16% → 3.52   18% → 3.42   20% → 3.33
//     the same pairs in dark:                          5.74 / 5.55 / 5.36 / 5.17  — all fine
//
// **A tint is not symmetric across modes.** It darkens the backdrop AWAY from a light accent (dark
// mode) and lifts it TOWARD a dark accent (light mode) until the two converge. Any
// "tint the accent behind accent-coloured text" pattern is a light-mode contrast bug by construction.
//
// The fix is the pair the system already ships for a tinted accent surface, and which shipped for the
// knowledge filter chip: `--color-primary-container` + `--color-on-primary-container` — 13.1:1 light,
// 10.43:1 dark, guaranteed across all 12 schemes by `schemeContrast.test.ts`.
//
// ⚠️ SEMANTIC TONES ARE OUT OF SCOPE ON PURPOSE. `info`/`ok`/`warn`/`danger` at 14–16% measure
// 4.54–4.71 and PASS; only ≥18% dips under (4.39–4.43), and none has a `<tone>-container` sibling, so
// routing them through the coral container would be wrong. 47 such sites are left untouched, and the
// four that sit at 18% are recorded as their own family rather than swept in here.

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\./.test(n) ? [p] : []
  })

describe('accentChip', () => {
  it('uses the container pair, not the accent as ink', () => {
    expect(accentChip.background).toBe('var(--color-primary-container)')
    expect(accentChip.color).toBe('var(--color-on-primary-container)')
  })

  it('carries no tint strength to drift', () => {
    // The 27 sites used FOUR different strengths (14/16/18/20%) for one idea. A container has none.
    expect(JSON.stringify(accentChip)).not.toMatch(/color-mix|%/)
  })
})

describe('no primary tint under primary ink survives', () => {
  const offenders: string[] = []
  for (const abs of walk(SRC)) {
    const text = readFileSync(abs, 'utf8')
    text.split('\n').forEach((line, i) => {
      const bg = /background:\s*'?`?color-mix\(in srgb, var\(--color-primary\) \d+%/.test(line)
      const ink = /color:\s*'?var\(--color-primary\)/.test(line)
      if (bg && ink) offenders.push(`${abs.slice(SRC.length + 1)}:${i + 1}`)
    })
  }

  it('has none left', () => {
    expect(
      offenders,
      `the accent is still both tint and ink (3.33–3.62:1 in light) at:\n  ${offenders.join('\n  ')}`,
    ).toEqual([])
  })

  it('scans real files (not vacuously green)', () => {
    expect(walk(SRC).length).toBeGreaterThan(200)
  })
})

// ── The same defect, spelled in Tailwind classes ─────────────────────────────────────────
//
// The sweep above matches the STYLE-OBJECT spelling only (`background: color-mix(…)` +
// `color: var(--color-primary)`). A second population wrote the identical pair as utilities —
// `bg-primary/12 text-primary` — and was therefore invisible to it. axe found one of them on
// `#/tools` in light mode, `[serious] color-contrast`, 3 nodes at **4.09:1** (`#c8452e` on `#f8e9e6`).
//
// Re-measured per alpha with the method validated by reproducing that exact 4.09 (light, primary ink
// over primary tint on `--color-surface`):
//
//     10% → 4.20   12% → 4.09   14% → 3.97   15% → 3.92   20% → 3.64   25% → 3.39
//     the same alphas in dark:  5.84 / 5.67 / 5.52 / 5.43 / 4.94 / 4.50 — all pass
//
// So EVERY class-spelled coral chip at ≥10% fails AA in light, and only 5% (4.52) squeaks through.
// Seven static chips were converged onto the container pair; two interactive controls are held back
// below because they carry `hover:bg-primary/25` on the coral branch and a container fill has no
// hover shade in the token set — picking one is a visual-language decision, not a contrast fix.

const CLASS_TINT_ALLOWED = new Set([
  // Interactive, with a hover ON the coral branch. `Button`'s `tonal` variant (14 callers) and the
  // Code cockpit's autopilot toggle both go `bg-primary/15 → hover:bg-primary/25`, i.e. 3.92 → 3.39.
  // Both need a hover treatment for a container fill before they can move; recorded, not swept.
  'ui/Button.tsx',
  'pages/code/CodeCockpitPage.tsx',
])

describe('no primary tint under primary ink survives — utility spelling', () => {
  const offenders: string[] = []
  const seen: string[] = []
  for (const abs of walk(SRC)) {
    const rel = abs.slice(SRC.length + 1)
    readFileSync(abs, 'utf8').split('\n').forEach((line, i) => {
      // One class string carrying both the coral tint and coral ink. Alpha-bearing only: a bare
      // `bg-primary` is the solid accent with `text-on-primary`, which is a different, passing pair.
      if (!/bg-primary\/\d+/.test(line)) return
      if (!/text-primary\b/.test(line)) return
      seen.push(`${rel}:${i + 1}`)
      if (!CLASS_TINT_ALLOWED.has(rel)) offenders.push(`${rel}:${i + 1}`)
    })
  }

  it('has none left outside the two recorded interactive holdouts', () => {
    expect(
      offenders,
      `coral ink on a coral tint is 3.64–4.20:1 in light — use bg-primary-container + text-on-primary-container:\n  ${offenders.join('\n  ')}`,
    ).toEqual([])
  })

  it('still finds the holdouts — the allowlist is not stale', () => {
    // If these move or get fixed, this fails and the allowlist shrinks. An allowlist nobody checks
    // becomes a permanent exemption.
    expect(seen.length, 'the matcher stopped matching anything at all').toBeGreaterThan(0)
    for (const rel of CLASS_TINT_ALLOWED) {
      expect(seen.some((s) => s.startsWith(rel + ':')), `${rel} no longer carries the pattern — drop it from the allowlist`).toBe(true)
    }
  })
})

describe('the sweep actually adopted the shared definition', () => {
  const adopters = walk(SRC).filter((abs) => /\baccentChip\b/.test(readFileSync(abs, 'utf8')))

  it('is used across the tree, not in one corner', () => {
    // 23 files at the time of writing, plus the definition itself.
    expect(adopters.length, 'adopters of the shared accent chip').toBeGreaterThanOrEqual(20)
  })

  it('every adopter imports it rather than re-declaring the colours', () => {
    const bad = adopters
      .filter((abs) => !abs.endsWith(join('design', 'accent.ts')))
      .filter((abs) => !/import \{[^}]*accentChip[^}]*\} from '[^']*design\/accent'/.test(readFileSync(abs, 'utf8')))
    expect(bad.map((b) => b.slice(SRC.length + 1)), 'uses accentChip without importing it').toEqual([])
  })
})
