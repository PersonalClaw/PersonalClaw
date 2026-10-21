import { describe, expect, it } from 'vitest'
import { START_MS, rowsBoundTheStart } from './useCutOff'

// ── When the rows a page holds can say what a restart cut off ───────────────────────────────────
//
// They can when they were current as the gateway went. On the gateway's own machine, a drop the page
// was awake and reading for is the gateway going, however long its next start takes. A page that
// slept or whose reads failed, or one a network stands between, holds rows that may be older than the
// gateway's going: only a start within a start's length of them is told.

const at = 1_800_000_000_000
const MIN = 60 * 1000

describe('rowsBoundTheStart', () => {
  it('on the gateway\'s machine, current at the drop: told, however long the start took', () => {
    expect(rowsBoundTheStart({ startedAt: at + 30 * MIN, knownUntil: at, atDrop: true, local: true })).toBe(true)
  })
  it('on the gateway\'s machine, not current at the drop: told only within a start\'s length', () => {
    expect(rowsBoundTheStart({ startedAt: at + 5 * 60 * MIN, knownUntil: at, atDrop: false, local: true })).toBe(false)
    expect(rowsBoundTheStart({ startedAt: at + START_MS, knownUntil: at, atDrop: false, local: true })).toBe(true)
  })
  it('across a network: told only within a start\'s length, current at the drop or not', () => {
    expect(rowsBoundTheStart({ startedAt: at + START_MS + 1, knownUntil: at, atDrop: true, local: false })).toBe(false)
    expect(rowsBoundTheStart({ startedAt: at + 20_000, knownUntil: at, atDrop: true, local: false })).toBe(true)
    expect(rowsBoundTheStart({ startedAt: at - 20_000, knownUntil: at, atDrop: false, local: false })).toBe(true)
  })
})
