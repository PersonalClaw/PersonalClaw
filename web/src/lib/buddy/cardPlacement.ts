// ── Where the buddy's card opens beside its orb (#5216, `docs/design/buddy/surfaces.md`) ──────────
//
// The orb stays where the owner put it, and nothing the buddy says moves it. Its card goes on the
// side of the orb that has room on that display: below an orb near the top, above one near the
// bottom, then to its right or its left, and it never covers the orb. Along that side it is centred
// on the orb when it fits, and otherwise kept to the display's edge. A card bigger than every side
// takes the side with the most room at the size that fits, and its content scrolls inside it.
//
// Pure: the orb's box and the display in points, the card's wanted size in, where it goes out.

export interface Box { x: number; y: number; width: number; height: number }
export interface Size { width: number; height: number }

export type CardSide = 'below' | 'above' | 'right' | 'left'
/** Along the side: the card's start edge on the orb's start edge, centred on the orb, or its end on
 *  the orb's end. */
export type CardAlign = 'start' | 'center' | 'end'

export interface CardPlace {
  side: CardSide
  align: CardAlign
  /** The room the card gets: its wanted size, or less where the display has less. */
  width: number
  height: number
}

/** Between the orb and its card. */
export const CARD_GAP = 8
/** Kept clear at the display's edges. */
export const DISPLAY_EDGE = 8

const SIDES: readonly CardSide[] = ['below', 'above', 'right', 'left']
const across = (side: CardSide) => side === 'below' || side === 'above'

/** Where the card goes for an orb at *orb* on *display*, wanting *card*. */
export function cardPlace(orb: Box, display: Size, card: Size): CardPlace {
  const room: Record<CardSide, number> = {
    below: display.height - (orb.y + orb.height) - CARD_GAP - DISPLAY_EDGE,
    above: orb.y - CARD_GAP - DISPLAY_EDGE,
    right: display.width - (orb.x + orb.width) - CARD_GAP - DISPLAY_EDGE,
    left: orb.x - CARD_GAP - DISPLAY_EDGE,
  }
  const maxWidth = Math.max(0, display.width - 2 * DISPLAY_EDGE)
  const maxHeight = Math.max(0, display.height - 2 * DISPLAY_EDGE)
  const sizeOn = (side: CardSide): Size => across(side)
    ? { width: Math.min(card.width, maxWidth), height: Math.min(card.height, Math.max(0, room[side])) }
    : { width: Math.min(card.width, Math.max(0, room[side])), height: Math.min(card.height, maxHeight) }
  const fits = (side: CardSide) => {
    const s = sizeOn(side)
    return s.width >= Math.min(card.width, maxWidth) && s.height >= Math.min(card.height, maxHeight) && room[side] > 0
  }
  const area = (side: CardSide) => { const s = sizeOn(side); return s.width * s.height }
  const side = SIDES.find(fits) ?? [...SIDES].sort((a, b) => area(b) - area(a))[0]
  const size = sizeOn(side)
  const align = (['center', 'start', 'end'] as const).find((a) => onDisplay(cardRect(orb, { side, align: a, ...size }), display)) ?? 'center'
  return { side, align, ...size }
}

const onDisplay = (r: Box, d: Size) =>
  r.x >= DISPLAY_EDGE && r.y >= DISPLAY_EDGE && r.x + r.width <= d.width - DISPLAY_EDGE && r.y + r.height <= d.height - DISPLAY_EDGE

/** The card's box for *place* beside the orb at *orb*. */
export function cardRect(orb: Box, place: CardPlace): Box {
  const { side, align, width, height } = place
  if (across(side)) {
    const y = side === 'below' ? orb.y + orb.height + CARD_GAP : orb.y - CARD_GAP - height
    const x = align === 'start' ? orb.x : align === 'end' ? orb.x + orb.width - width : orb.x + orb.width / 2 - width / 2
    return { x, y, width, height }
  }
  const x = side === 'right' ? orb.x + orb.width + CARD_GAP : orb.x - CARD_GAP - width
  const y = align === 'start' ? orb.y : align === 'end' ? orb.y + orb.height - height : orb.y + orb.height / 2 - height / 2
  return { x, y, width, height }
}
