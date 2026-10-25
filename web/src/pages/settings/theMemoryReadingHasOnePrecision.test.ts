import { describe, expect, it } from 'vitest'
import { normalizeTree } from '../../../e2e/surfaceChecks'
import type { MemoryPressure } from '../../lib/api'
import { pressureDetail } from '../../lib/residency'

// ── Settings › models' memory reading keeps one precision whatever its value ─────────────────────
//
// The host probe rounds `used_pct` to one decimal, and the caption printed it as JSON gave it: `12.5%`
// on one reading and `12%` on the next, when the decimal happened to be 0. The caption's width moved
// with the value, and the surface matrix folded `#.#%` to `#%` so its tree would hold still (#3905).
// The caption now says a whole percent, as the bar's own `aria-valuenow` does, and the fold is gone:
// a decimal percentage anywhere else is a change the matrix sees.

function reading(used_pct: number): MemoryPressure {
  return { total_mb: 16384, used_mb: 7680, available_mb: 8704, used_pct, warn_pct: 85, warn: false, source: 'vm_stat' }
}

describe("the memory reading's precision", () => {
  it('says a whole percent for a reading with a decimal and one without', () => {
    expect(pressureDetail(reading(46.9))).toBe('7.5 GiB of 16.0 GiB in use · 47%')
    expect(pressureDetail(reading(47))).toBe('7.5 GiB of 16.0 GiB in use · 47%')
    expect(pressureDetail(reading(12.5))).toMatch(/ · 13%$/)
  })

  it('reads as the same tree line either way', () => {
    const line = (pct: number) => `  - text: ${pressureDetail(reading(pct))} No models are loaded right now.`
    expect(normalizeTree(line(46.9))).toBe(normalizeTree(line(47)))
    expect(normalizeTree(line(46.9))).toBe('  - text: #.# GiB of #.# GiB in use · #% No models are loaded right now.')
  })

  it("leaves another surface's decimal percentage for the matrix to see", () => {
    // Control: the fold that hid this reading's jitter would hide a change on any surface too.
    expect(normalizeTree('  - text: 12.5% match')).toBe('  - text: #.#% match')
    expect(normalizeTree('  - text: 9.6 GiB of 16.0 GiB in use')).toBe('  - text: #.# GiB of #.# GiB in use')
  })
})
