import { describe, expect, it } from 'vitest'
import { workKindWord } from './runningRows'
import { workKindOf } from '../pages/companion/allWork'
import { KIND_GLYPH } from '../ui/BuddyCard'

// ── The library's upkeep is listed as live work while it runs ──────────────────────────────────
//
// Graph maintenance ran in the gateway's own process for minutes on a large library, holding its
// interpreter, and nothing named it: chats stalled and the health check timed out with no word of
// why. The running list now carries it while a pass runs (`running_now._maintenance`, kind
// `maintenance`, `maintenance:knowledge`), and each surface that lists live work says what it is.

describe('the library upkeep row', () => {
  it('says what kind of work it is', () => {
    expect(workKindWord({ kind: 'maintenance', loop_kind: '' })).toBe('Library upkeep')
  })

  it('is a kind the companion lists, with a glyph of its own', () => {
    expect(workKindOf('maintenance:knowledge')).toBe('maintenance')
    expect(KIND_GLYPH.maintenance).toBeTruthy()
  })
})
