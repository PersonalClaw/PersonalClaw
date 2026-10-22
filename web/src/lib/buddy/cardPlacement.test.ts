import { describe, expect, it } from 'vitest'
import { CARD_BUDGET, CARD_GAP, ORB_BOX, layoutAt, type Box } from './cardPlacement'

// ── Where the card opens beside the orb (#5216) ──────────────────────────────────────────────────
//
// The shell's rule (`desktop/buddyPlacement.js`, `layoutAt`), the same here so the playground and a
// browser tab lay out as the buddy's window does: right of the orb if the card fits there, else
// left; down from the orb's top if it fits, else up from its bottom; if neither side has the width,
// below the orb (or above it, where there is more room), centred on it and kept on the display. At
// every edge and corner, for each card budget (320, 390, 412 and 420 px wide), the card is never
// over the orb and never off the display, and the orb is never moved to make room.

const DISPLAY: Box = { x: 0, y: 0, width: 1440, height: 900 }
const EDGE = 8
const SPOTS: Record<string, { x: number; y: number }> = {
  'top centre': { x: (1440 - ORB_BOX) / 2, y: EDGE },
  'top left': { x: EDGE, y: EDGE },
  'top right': { x: 1440 - ORB_BOX - EDGE, y: EDGE },
  'bottom left': { x: EDGE, y: 900 - ORB_BOX - EDGE },
  'bottom right': { x: 1440 - ORB_BOX - EDGE, y: 900 - ORB_BOX - EDGE },
  'bottom centre': { x: (1440 - ORB_BOX) / 2, y: 900 - ORB_BOX - EDGE },
  'left middle': { x: EDGE, y: (900 - ORB_BOX) / 2 },
  'right middle': { x: 1440 - ORB_BOX - EDGE, y: (900 - ORB_BOX) / 2 },
  centre: { x: (1440 - ORB_BOX) / 2, y: (900 - ORB_BOX) / 2 },
}
const BUDGETS = [320, 390, 412, 420].map((width) => ({ width, maxHeight: CARD_BUDGET.maxHeight }))

const overlaps = (a: Box, b: Box) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height
const inside = (a: Box, d: Box) => a.x >= d.x && a.y >= d.y && a.x + a.width <= d.x + d.width && a.y + a.height <= d.y + d.height

describe('the card opens beside the orb, as the window lays it out', () => {
  for (const [where, at] of Object.entries(SPOTS)) {
    for (const budget of BUDGETS) {
      it(`orb ${where}, a ${budget.width} px budget: beside it, on the display, never over it`, () => {
        const l = layoutAt(DISPLAY, at, budget)
        expect(l.orb).toEqual({ ...at, width: ORB_BOX, height: ORB_BOX })
        expect(overlaps(l.card, l.orb), JSON.stringify(l)).toBe(false)
        expect(inside(l.card, DISPLAY), JSON.stringify(l)).toBe(true)
        expect(l.card.width).toBeLessThanOrEqual(budget.width)
      })
    }
  }

  it('right of the orb where it fits, opening down from its top', () => {
    const l = layoutAt(DISPLAY, SPOTS['top left'])
    expect(l).toMatchObject({ side: 'right', opens: 'down' })
    expect(l.card.x).toBe(EDGE + ORB_BOX + CARD_GAP)
    expect(l.card.y).toBe(l.orb.y)
  })

  it('left of an orb at the right edge, and up from its bottom near the bottom', () => {
    const l = layoutAt(DISPLAY, SPOTS['bottom right'])
    expect(l).toMatchObject({ side: 'left', opens: 'up' })
    expect(l.card.x + l.card.width).toBe(l.orb.x - CARD_GAP)
    expect(l.card.y + l.card.height).toBe(l.orb.y + ORB_BOX)
  })

  it('below an orb on a display too narrow for either side, centred on it and kept on the display', () => {
    const narrow: Box = { x: 0, y: 0, width: 412, height: 844 }
    const l = layoutAt(narrow, { x: (412 - ORB_BOX) / 2, y: EDGE })
    expect(l).toMatchObject({ side: 'below', opens: 'down' })
    expect(l.card.y).toBe(EDGE + ORB_BOX + CARD_GAP)
    expect(inside(l.card, narrow)).toBe(true)
  })

  it('above an orb low on a narrow display, where there is more room above', () => {
    const narrow: Box = { x: 0, y: 0, width: 390, height: 844 }
    const l = layoutAt(narrow, { x: 100, y: 844 - ORB_BOX - EDGE })
    expect(l).toMatchObject({ side: 'above', opens: 'up' })
    expect(l.card.y + l.card.height).toBe(l.orb.y - CARD_GAP)
  })

  it('a short display clamps the card to its height, and the orb stays where it is', () => {
    const short: Box = { x: 0, y: 0, width: 1440, height: 400 }
    const l = layoutAt(short, { x: 100, y: 172 })
    expect(l.card.height).toBe(400)
    expect(l.orb).toEqual({ x: 100, y: 172, width: ORB_BOX, height: ORB_BOX })
    expect(inside(l.card, short)).toBe(true)
  })

  it('a display that does not start at the origin keeps the card on it', () => {
    const second: Box = { x: 1440, y: 0, width: 1280, height: 800 }
    const l = layoutAt(second, { x: 1440 + 1280 - ORB_BOX - EDGE, y: EDGE })
    expect(l.side).toBe('left')
    expect(inside(l.card, second)).toBe(true)
  })
})
