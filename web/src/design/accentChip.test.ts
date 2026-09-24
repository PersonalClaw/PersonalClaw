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
// Seven static chips were converged onto the container pair; ONE interactive control is held back
// below because it carries `hover:bg-primary/25` on the coral branch and a container fill has no
// hover shade in the token set — picking one is a visual-language decision, not a contrast fix.

const CLASS_TINT_ALLOWED = new Set([
  // Interactive, with a hover ON the coral branch: `bg-primary/15 → hover:bg-primary/25`, i.e.
  // 3.92 → 3.39. Needs a hover treatment for a container fill before it can move; recorded, not swept.
  //
  // 🔑 This list held TWO entries until DSC-12, and the comment above them had already done the
  // hard part: it named `Button`'s `tonal` variant and the Code cockpit's autopilot toggle as the same
  // control spelled twice, right down to the shared alpha pair. The toggle is now
  // `<Button variant="tonal">` (caller 15), so the debt has ONE owner and one place to fix. Worth being
  // exact about what that bought: the contrast did not improve, it stopped being duplicated.
  'ui/Button.tsx',
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

// ── A THIRD SPELLING, swept in its own file ────────────────────────────────────────────────────────
//
// Both sweeps above match a colour written ON THE LINE — a literal `var(--color-primary)` in a style
// object, or a `bg-primary/N text-primary` class pair. Neither can see a tint whose colour is
// INTERPOLATED from a meta registry (`color-mix(in srgb, ${meta.tone} 14%, transparent)` with
// `color: meta.tone`), because no accent token appears in the source at all. `ui/RungChip` was one:
// `lib/rungs.ts` maps `autonomous → var(--color-primary)`, so "runs on its own" measured **3.97:1** in
// light — the 14% row of the table above — on 7 live chips on `#/triggers`.
//
// That population is **43 sites**, and a regex cannot decide them: whether `${x.tone}` reaches coral
// depends on the registry behind `x`, and most resolve to semantic tones that pass. So it is swept
// per-registry, behaviourally, in **`accentChipTone.test.tsx`** — which renders every rung and asserts
// the coral one uses this file's `accentChip` while the three passing tones keep their tint. Its header
// carries the remaining worklist and the reasons for what is deliberately left alone.

describe('the third spelling has a home', () => {
  it('is swept behaviourally next door, not silently ignored here', () => {
    expect(readFileSync(join(SRC, 'design/accentChipTone.test.tsx'), 'utf8'))
      .toMatch(/a rung chip inks coral through the container pair/)
  })
})

// ── The FOURTH spelling: class ink, style-object tint ──────────────────────────────────────────
//
// The two sweeps above are blind to it, and the gap is structural rather than an oversight:
//
//   the style-object sweep  requires the tint AND `color: var(--color-primary)` on ONE LINE
//   the utility sweep       requires the `bg-primary/N` + `text-primary` CLASS pair
//
// A chip that puts the ink in a Tailwind class and the tint in a style attribute on the NEXT line
// satisfies neither test while being exactly the defect both exist to prevent. Three sites shipped
// that way and were found by `ux-audit` and axe agreeing on a live page, not by this file:
//
//   ConflictPanel's "higher-trust source"  measured 3.97:1 at 12px — axe [serious] color-contrast,
//                                          LIGHT MODE ONLY (dark passes, as the header explains)
//   AppsSection's "Update" badge           same shape, 14% tint under class ink…
//   AppsSection's provider label           …fifteen lines from a sibling already using accentChip
//
// 🪤 SCOPED BY A WINDOW, NOT BY THE ELEMENT. Finding the end of a JSX tag with a regex is not
// reliable — `>` appears inside attribute expressions — so this looks BACKWARDS a bounded distance
// from the tint for a `className` carrying `text-primary`. The bound is the tradeoff being made: a
// `text-primary` class more than 320 characters before a primary tint is not reported, and two
// unrelated elements inside that window would be a false positive. It is deliberately the same
// direction as the real spelling (class first, style second), which is how JSX attributes order.
describe('no primary tint under CLASS-spelled primary ink survives', () => {
  const TINT = /color-mix\(in srgb, var\(--color-primary\) \d+%/g
  const INK_CLASS = /className="[^"]*\btext-primary\b[^"]*"/
  const offenders: string[] = []
  for (const abs of walk(SRC)) {
    const text = readFileSync(abs, 'utf8')
    for (const m of text.matchAll(TINT)) {
      const window = text.slice(Math.max(0, m.index! - 320), m.index!)
      if (INK_CLASS.test(window)) {
        offenders.push(`${abs.slice(SRC.length + 1)}:${text.slice(0, m.index!).split('\n').length}`)
      }
    }
  }

  it('has none left', () => {
    expect(
      offenders,
      `class-spelled accent ink over a primary tint (3.62:1 in light) at:\n  ${offenders.join('\n  ')}`,
    ).toEqual([])
  })

  it('the matcher still recognises the shape it polices — not vacuously green', () => {
    // Both halves proven against a synthetic sample, so a regex that stops matching anything cannot
    // pass as "clean". The real files are swept above; this pins the detector itself.
    const sample = [
      '        <span className="shrink-0 rounded px-1.5 text-[0.75rem] text-primary"',
      "          style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' }}>",
    ].join('\n')
    const hit = [...sample.matchAll(TINT)].some((m) => INK_CLASS.test(sample.slice(0, m.index!)))
    expect(hit, 'the detector matches the spelling that shipped three times').toBe(true)
    // And it does NOT fire when the ink is absent — a tint alone is legitimate (semantic tones pass).
    const benign = sample.replace(' text-primary', ' text-on-surface-low')
    const falsePositive = [...benign.matchAll(TINT)].some((m) => INK_CLASS.test(benign.slice(0, m.index!)))
    expect(falsePositive, 'a primary tint under non-accent ink is left alone').toBe(false)
  })
})
