import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A selected chip must not draw the accent as BOTH tint and ink ───────────────────────
//
// `FilterChip`'s selected state was `background: color-mix(primary 20%, transparent)` with
// `color: primary`. Measured on #/knowledge in light mode: **3.33:1**, below the 4.5 floor, and
// reported by axe as `[serious] color-contrast` — 2 of 9 chips, and exactly the 2 that were
// selected. Dark was never affected (6.99:1): there the tint darkens the backdrop AWAY from the
// light accent, while in light mode it lifts the backdrop TOWARD the dark accent until the two
// converge.
//
// The design system already ships the pair for an accent-tinted surface —
// `--color-primary-container` + `--color-on-primary-container` — which measures 13.1:1 in light and
// 10.43:1 in dark, and is now guaranteed across all 12 schemes by `schemeContrast.test.ts`.
//
// A per-type `tone` keeps its own tint and ink deliberately: there is no `<tone>-container` sibling
// to pair with, and putting a type hue on the coral container would be a new contrast risk. Those
// chips never render selected on this surface in any state measured here.

const SRC = readFileSync(join(process.cwd(), 'src/pages/knowledge/KnowledgeListPage.tsx'), 'utf8')

describe('the selected filter chip', () => {
  it('uses the accent CONTAINER pair, not the accent as ink', () => {
    expect(SRC).toMatch(/background: 'var\(--color-primary-container\)'/)
    expect(SRC).toMatch(/color: 'var\(--color-on-primary-container\)'/)
  })

  it('no longer paints a primary tint under primary ink', () => {
    // The exact regressed shape: a primary-mixed background whose own colour is also primary.
    expect(
      /color-mix\(in srgb, \$\{tone \?\? 'var\(--color-primary\)'\} 20%/.test(SRC),
      'the accent must not be both the tint and the ink',
    ).toBe(false)
  })

  it('leaves a type-toned chip exactly as it was', () => {
    // Untouched on purpose — asserted so a later sweep does not "tidy" it onto the coral container.
    expect(SRC).toMatch(/background: `color-mix\(in srgb, \$\{tone\} 20%, transparent\)`, color: tone/)
  })

  it('keeps the unselected chip neutral', () => {
    expect(SRC).toMatch(/background: 'var\(--color-surface-high\)', color: 'var\(--color-on-surface-var\)'/)
  })

  it('reads the real file (not vacuously green)', () => {
    expect(SRC.length).toBeGreaterThan(2000)
    expect(SRC).toMatch(/function FilterChip\(/)
  })
})
