import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { QualityBadges, qualityBadges } from './qualityBadges'
import type { AppQualityWire } from '../../lib/api'

// ── Honest badges (APE-4) ──────────────────────────────────────────────────
// The backend half of this atom makes a dishonest DECLARATION fail CI. This half
// covers the other direction: a card that renders three different facts identically
// is dishonest even when every declaration is true.
//
// Three states per axis — met / declared-miss / not declared — and the load-bearing
// assertion is that all three RENDER DIFFERENTLY. Asserting "true shows Tested" alone
// would pass just as happily if absent also showed Tested.

const TESTED_TRUE: AppQualityWire = { tested: true }
const TESTED_FALSE: AppQualityWire = { tested: false }

describe('quality badges — the three states are distinguishable', () => {
  it('a met claim renders a met badge', () => {
    render(<QualityBadges quality={TESTED_TRUE} />)
    expect(screen.getByTestId('quality-tested')).toHaveAttribute('data-tone', 'met')
    expect(screen.getByText('Tested')).toBeInTheDocument()
  })

  it('a declared MISS renders a badge, with a different tone and a different label', () => {
    render(<QualityBadges quality={TESTED_FALSE} />)
    const b = screen.getByTestId('quality-tested')
    expect(b).toHaveAttribute('data-tone', 'miss')
    expect(b.textContent).toBe('Not tested')
    // The whole point: a miss must not be able to read as a pass.
    expect(b.textContent).not.toBe('Tested')
  })

  it('an app that declares NOTHING renders no badge row at all', () => {
    // Not an empty chrome row, not a row of misses: nothing. An app that never
    // entered the bar must not be shown failing it.
    const { container } = render(<QualityBadges quality={undefined} />)
    expect(container.innerHTML).toBe('')
    expect(screen.queryByTestId('quality-badges')).toBeNull()
  })

  it('an EMPTY declared block also renders nothing', () => {
    // `{}` reaches the wire when the backend parsed a block with no recognised axes.
    // It claims nothing, so it must render like nothing.
    const { container } = render(<QualityBadges quality={{}} />)
    expect(container.innerHTML).toBe('')
  })

  it('absent ≠ passing AND absent ≠ false — all three renderings differ', () => {
    // The one assertion the other three cannot make on their own. Compare the actual
    // rendered markup of the three states pairwise; any collapse fails here.
    const html = (q: AppQualityWire | undefined) => render(<QualityBadges quality={q} />).container.innerHTML
    const met = html(TESTED_TRUE)
    const miss = html(TESTED_FALSE)
    const absent = html(undefined)
    expect(new Set([met, miss, absent]).size, `met=${met}\nmiss=${miss}\nabsent=${absent}`).toBe(3)
    // …and specifically: absent carries neither the met nor the miss label.
    expect(absent).not.toContain('Tested')
    expect(absent).not.toContain('Not tested')
  })

  it('an undeclared axis does not appear beside a declared one', () => {
    // Per-axis, not just per-block: declaring `tested` must not conjure the other two.
    render(<QualityBadges quality={{ tested: true }} />)
    expect(screen.getByTestId('quality-tested')).toBeInTheDocument()
    expect(screen.queryByTestId('quality-designSystem')).toBeNull()
    expect(screen.queryByTestId('quality-a11y')).toBeNull()
  })
})

describe('quality badges — the decision table', () => {
  it('every axis has a met and a miss rendering, and they never coincide', () => {
    const cases: [AppQualityWire, AppQualityWire, string][] = [
      [{ tested: true }, { tested: false }, 'tested'],
      [{ designSystem: 'v2' }, { designSystem: 'legacy' }, 'designSystem'],
      [{ a11y: true }, { a11y: false }, 'a11y'],
    ]
    for (const [metQ, missQ, axis] of cases) {
      const met = qualityBadges(metQ)
      const miss = qualityBadges(missQ)
      expect(met.map((b) => b.axis)).toEqual([axis])
      expect(miss.map((b) => b.axis)).toEqual([axis])
      expect(met[0].tone).toBe('met')
      expect(miss[0].tone).toBe('miss')
      expect(met[0].label).not.toBe(miss[0].label)
      expect(met[0].title).not.toBe(miss[0].title)
    }
  })

  it('"n/a" is its own answer — a miss tone, but never the legacy label', () => {
    // A backend-only app has no UI to style. That is not a failure and not silence,
    // so it still gets a badge, with wording that says what it actually means.
    const na = qualityBadges({ designSystem: 'n/a' })
    const legacy = qualityBadges({ designSystem: 'legacy' })
    expect(na[0].label).toBe('No UI')
    expect(na[0].label).not.toBe(legacy[0].label)
    expect(na[0].title).not.toBe(legacy[0].title)
  })

  it('all three axes at once render in a stable, declared order', () => {
    const all = qualityBadges({ tested: true, designSystem: 'v2', a11y: true })
    expect(all.map((b) => b.axis)).toEqual(['tested', 'designSystem', 'a11y'])
  })

  it('the axis vocabulary matches the backend\'s QUALITY_AXES', () => {
    // Two vocabularies for one block is how a badge and a check start disagreeing
    // about what is being claimed. Read the backend's tuple and pin it.
    const py = readFileSync(
      join(process.cwd(), '..', 'src', 'personalclaw', 'apps', 'manifest.py'), 'utf8')
    const m = py.match(/QUALITY_AXES = \(([^)]*)\)/)
    expect(m, 'QUALITY_AXES not found in apps/manifest.py').not.toBeNull()
    const backendAxes = [...m![1].matchAll(/"([a-zA-Z0-9]+)"/g)].map((x) => x[1])
    expect(backendAxes.length).toBe(3)
    expect(qualityBadges({ tested: true, designSystem: 'v2', a11y: true }).map((b) => b.axis))
      .toEqual(backendAxes)
  })
})

describe('quality badges — the tooltip does not overclaim', () => {
  it('says DECLARES, never VERIFIED — the block is self-declared', () => {
    // CI verification covers FIRST-PARTY apps only. A third-party card showing
    // "verified" would be the atom's own defect rendered as UI copy.
    for (const q of [
      { tested: true }, { tested: false },
      { designSystem: 'v2' as const }, { designSystem: 'legacy' as const },
      { designSystem: 'n/a' as const }, { a11y: true }, { a11y: false },
    ]) {
      for (const b of qualityBadges(q)) {
        expect(b.title.toLowerCase(), b.title).toContain('declares')
        expect(b.title.toLowerCase(), b.title).not.toContain('verified')
      }
    }
  })
})
