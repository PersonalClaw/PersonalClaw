// ── Where the buddy's card opens beside its orb (#5216, `docs/design/buddy/surfaces.md`) ──────────
//
// The orb stays where the owner put it, and nothing the buddy says moves it. Its card goes on the
// side of the orb that has room on that display, by the shell's own rule (`desktop/buddyPlacement.js`,
// `layoutAt`), so the playground and a browser tab lay the buddy out as its window does: right of
// the orb if the card's width fits there, else left, opening down from the orb's top if the card's
// height fits, else up from its bottom; and if neither side has the width, below the orb or above
// it (where there is more room), centred on it and kept on the display. The card's area is the
// largest it may use; the card is drawn in it, anchored to the orb, and never covers the orb.
//
// Pure: the display's work area and the orb's spot in points, the card's budget in, the layout out,
// in the display's points.

export interface Box { x: number; y: number; width: number; height: number }

/** The orb's box: its 48 pt drawing in a 56 pt target. */
export const ORB_BOX = 56
/** Between the orb and its card. */
export const CARD_GAP = 8
/** The card's largest area: its width, and its tallest before it scrolls inside. */
export const CARD_BUDGET = Object.freeze({ width: 420, maxHeight: 560 })

export type CardSide = 'right' | 'left' | 'below' | 'above'

export interface CardLayout {
  side: CardSide
  opens: 'down' | 'up'
  orb: Box
  /** The area the card may use. */
  card: Box
}

const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), Math.max(lo, hi))

/** The orb's box at *orb* on a display with the work area *display*, and its card's area. */
export function layoutAt(display: Box, orb: { x: number; y: number }, budget: { width: number; maxHeight: number } = CARD_BUDGET): CardLayout {
  const right = display.x + display.width
  const bottom = display.y + display.height
  const cardWidth = Math.min(budget.width, display.width)
  const orbBox: Box = { x: orb.x, y: orb.y, width: ORB_BOX, height: ORB_BOX }
  const fitsRight = orb.x + ORB_BOX + CARD_GAP + cardWidth <= right
  const fitsLeft = orb.x - CARD_GAP - cardWidth >= display.x
  if (fitsRight || fitsLeft) {
    const side: CardSide = fitsRight ? 'right' : 'left'
    const height = Math.min(budget.maxHeight, display.height)
    const x = side === 'right' ? orb.x + ORB_BOX + CARD_GAP : orb.x - CARD_GAP - cardWidth
    if (orb.y + height <= bottom) return { side, opens: 'down', orb: orbBox, card: { x, y: orb.y, width: cardWidth, height } }
    if (orb.y + ORB_BOX - height >= display.y) return { side, opens: 'up', orb: orbBox, card: { x, y: orb.y + ORB_BOX - height, width: cardWidth, height } }
    return { side, opens: 'down', orb: orbBox, card: { x, y: Math.max(display.y, bottom - height), width: cardWidth, height } }
  }
  const below = bottom - (orb.y + ORB_BOX + CARD_GAP)
  const above = orb.y - CARD_GAP - display.y
  const side: CardSide = below >= above ? 'below' : 'above'
  const height = Math.max(0, Math.min(budget.maxHeight, side === 'below' ? below : above))
  const x = Math.round(clamp(orb.x + ORB_BOX / 2 - cardWidth / 2, display.x, right - cardWidth))
  const y = side === 'below' ? orb.y + ORB_BOX + CARD_GAP : orb.y - CARD_GAP - height
  return { side, opens: side === 'below' ? 'down' : 'up', orb: orbBox, card: { x, y, width: cardWidth, height } }
}
