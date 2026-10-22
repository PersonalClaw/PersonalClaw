import { describe, expect, it } from 'vitest'
import { CARD_GAP, cardPlace, cardRect, type Box } from './cardPlacement'

// ── Where the card opens beside the orb (#5216) ──────────────────────────────────────────────────
//
// On the side of the orb that has room on its display, never over the orb, at every edge and
// corner, for each compact card budget (320, 390, 412 px wide) and at 200% text zoom (the card
// twice as tall). The orb is never moved to make room: the card goes where the room is.

const DISPLAY = { width: 1440, height: 900 }
const ORB = 56
const SPOTS: Record<string, Box> = {
  'top centre': { x: (1440 - ORB) / 2, y: 8, width: ORB, height: ORB },
  'top left': { x: 8, y: 8, width: ORB, height: ORB },
  'top right': { x: 1440 - ORB - 8, y: 8, width: ORB, height: ORB },
  'bottom left': { x: 8, y: 900 - ORB - 8, width: ORB, height: ORB },
  'bottom right': { x: 1440 - ORB - 8, y: 900 - ORB - 8, width: ORB, height: ORB },
  'bottom centre': { x: (1440 - ORB) / 2, y: 900 - ORB - 8, width: ORB, height: ORB },
  'left middle': { x: 8, y: (900 - ORB) / 2, width: ORB, height: ORB },
  'right middle': { x: 1440 - ORB - 8, y: (900 - ORB) / 2, width: ORB, height: ORB },
  centre: { x: (1440 - ORB) / 2, y: (900 - ORB) / 2, width: ORB, height: ORB },
}
const CARDS = [320, 390, 412].flatMap((width) => [
  { width, height: 360, zoom: '100%' },
  { width, height: 720, zoom: '200%' },
])

const overlaps = (a: Box, b: Box) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height
const inside = (a: Box, d: { width: number; height: number }) => a.x >= 0 && a.y >= 0 && a.x + a.width <= d.width && a.y + a.height <= d.height

describe('the card opens beside the orb, where the room is', () => {
  for (const [where, orb] of Object.entries(SPOTS)) {
    for (const card of CARDS) {
      it(`orb ${where}, a ${card.width} px card at ${card.zoom}: beside it, on the display, never over it`, () => {
        const place = cardPlace(orb, DISPLAY, card)
        const rect = cardRect(orb, place)
        expect(overlaps(rect, orb), JSON.stringify({ place, rect })).toBe(false)
        expect(inside(rect, DISPLAY), JSON.stringify({ place, rect })).toBe(true)
        expect(rect.width).toBeLessThanOrEqual(card.width)
      })
    }
  }

  it('below an orb at the top, centred on it when it fits', () => {
    expect(cardPlace(SPOTS['top centre'], DISPLAY, { width: 412, height: 360 })).toMatchObject({ side: 'below', align: 'center' })
  })

  it('above an orb at the bottom, and its edge kept on the display near a corner', () => {
    expect(cardPlace(SPOTS['bottom right'], DISPLAY, { width: 412, height: 360 })).toMatchObject({ side: 'above', align: 'end' })
    expect(cardPlace(SPOTS['bottom left'], DISPLAY, { width: 412, height: 360 })).toMatchObject({ side: 'above', align: 'start' })
  })

  it('beside an orb in the middle of a short display, where neither above nor below has the room', () => {
    const short = { width: 1440, height: 600 }
    const orb = { x: 8, y: (600 - ORB) / 2, width: ORB, height: ORB }
    const place = cardPlace(orb, short, { width: 412, height: 360 })
    expect(place.side).toBe('right')
    expect(overlaps(cardRect(orb, place), orb)).toBe(false)
  })

  it('a card taller than any side holds its room and scrolls inside, the orb unmoved', () => {
    const tiny = { width: 500, height: 300 }
    const orb = { x: 222, y: 8, width: ORB, height: ORB }
    const place = cardPlace(orb, tiny, { width: 412, height: 720 })
    const rect = cardRect(orb, place)
    expect(rect.height).toBeLessThan(720)
    expect(inside(rect, tiny)).toBe(true)
    expect(overlaps(rect, orb)).toBe(false)
  })

  it('keeps a gap between the orb and the card', () => {
    const orb = SPOTS['top centre']
    const rect = cardRect(orb, cardPlace(orb, DISPLAY, { width: 390, height: 360 }))
    expect(rect.y - (orb.y + orb.height)).toBe(CARD_GAP)
  })
})
