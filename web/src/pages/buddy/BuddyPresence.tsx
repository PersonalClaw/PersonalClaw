/** The buddy as it shows (#3832, #5216): drawn from the state machine's view (`viewOf`), the same
 *  for the playground's stage and the live presence at `#/buddy`.
 *
 *  The buddy is one orb, of one size, that stays where it was put: its state is in its dots, its
 *  badge and its wash, never in its size or its place. What it has to say is a card beside it, on
 *  the side `place` gives (`lib/buddy/cardPlacement.ts`, where the display has room): one line
 *  while work runs (what the top edge's compact pill said) or on a floating orb's alert, and the
 *  full card (`ui/BuddyCard.tsx`) when it opens. The card is laid out apart from the orb, so
 *  opening, growing, shrinking or closing it never moves the orb or changes its size. The full
 *  card is the caller's, so the playground draws it from made-up rows and the presence from the
 *  gateway's. Nothing here answers: a click on the orb or the line is the machine's `click`, and
 *  only the card's answer controls answer. */

import type { ReactNode } from 'react'
import { BuddyOrb } from '../../ui/BuddyOrb'
import { Button } from '../../ui/Button'
import { cx } from '../../ui/cx'
import { orbStateOf, type BuddyState as OrbState } from '../../ui/buddy/orbPoses'
import { focusedWork, viewOf, type BuddyState, type Truth } from '../../lib/buddy/machine'
import { lineOf } from '../../lib/buddy/lines'
import type { CardAlign, CardSide } from '../../lib/buddy/cardPlacement'

/** The orb's one size, in every state, family and event. */
export const ORB_SIZE = 48
/** Its slot: the orb in the shared `Button`'s large size, 48 tall with its side padding. */
export const ORB_SLOT_WIDTH = 96

/** Where the card opens beside the orb; `width` and `height` are the room it gets, when the display
 *  has less than it wants. */
export interface PresencePlace {
  side: CardSide
  align: CardAlign
  width?: number
  height?: number
}

/** Until the shell says otherwise: below the orb, centred on it, as at the top of the screen. */
export const DEFAULT_PLACE: PresencePlace = { side: 'below', align: 'center' }

/** The orb's state for a truth, over the focused work's own phase for the lines the machine adds
 *  of its own (connected again, answered elsewhere, the return summary). */
export function orbOf(truth: Truth, machine: BuddyState): OrbState {
  return orbStateOf(truth, focusedWork(machine)?.phase ?? 'idle')
}

export interface PresenceHandlers {
  /** A click on the orb or its line: the machine's `click`. */
  onClick?: () => void
  /** The pointer rests on the orb, or leaves it. */
  onPointer?: (at: 'orb' | 'away') => void
}

/** Each side and alignment as classes: the card beside the orb, never over it. */
const AT: Record<CardSide, Record<CardAlign, string>> = {
  below: { start: 'top-full mt-s left-0', center: 'top-full mt-s left-1/2 -translate-x-1/2', end: 'top-full mt-s right-0' },
  above: { start: 'bottom-full mb-s left-0', center: 'bottom-full mb-s left-1/2 -translate-x-1/2', end: 'bottom-full mb-s right-0' },
  right: { start: 'left-full ml-s top-0', center: 'left-full ml-s top-1/2 -translate-y-1/2', end: 'left-full ml-s bottom-0' },
  left: { start: 'right-full mr-s top-0', center: 'right-full mr-s top-1/2 -translate-y-1/2', end: 'right-full mr-s bottom-0' },
}

/** The full card's width and its tallest before it scrolls inside, until the display has less
 *  (`surfaces.md`, the room budget). */
export const CARD_WIDTH = 420
export const CARD_HEIGHT = 360
/** The line card's widest: what the compact pill held. */
export const LINE_WIDTH = 360

/** The orb, in its one slot. Live (with `onClick`), the face is the shape clicks land on and the
 *  handle a drag moves the window by (`app/BuddyWindowRoot.tsx`, #3831), and the shared `Button`,
 *  a 44 px target and more; in the playground's stage it is drawn inert. */
export function BuddyFace({ state, label, onClick, onPointer }: { state: OrbState; label: string } & PresenceHandlers) {
  const orb = <BuddyOrb state={state} line="" size={ORB_SIZE} />
  return (
    <div data-buddy-orb-slot="" {...(onClick ? { 'data-buddy-shape': '', 'data-buddy-drag': '' } : {})} className="relative flex shrink-0"
      onPointerEnter={() => onPointer?.('orb')} onPointerLeave={() => onPointer?.('away')}>
      {onClick ? <Button variant="ghost" size="lg" ariaLabel={label} onClick={onClick}>{orb}</Button> : <span className="flex h-12 items-center px-2xl">{orb}</span>}
    </div>
  )
}

/** A surface beside the orb, at `place`: the line card or the full card. */
export function CardSurface({ place, live, wide, children }: { place: PresencePlace; live: boolean; wide: boolean; children: ReactNode }) {
  const width = Math.min(place.width ?? Infinity, wide ? CARD_WIDTH : LINE_WIDTH)
  return (
    <div
      data-buddy-card-surface="" data-side={place.side} data-align={place.align} {...(live ? { 'data-buddy-shape': '' } : {})}
      className={cx('absolute z-10 flex min-w-0', wide ? '' : 'w-max', AT[place.side][place.align])}
      // In points, as the display's room is measured: under a bigger text size the card wraps and
      // grows taller, and never wider than the room it was given.
      style={{ [wide ? 'width' : 'maxWidth']: width, ...(place.height ? { maxHeight: place.height, overflowY: 'auto' } : {}) }}
    >
      {children}
    </div>
  )
}

/** The buddy where it stands: `null` when nothing shows. */
export function BuddyPresence({ machine, card, place = DEFAULT_PLACE, onClick, onPointer }: {
  machine: BuddyState
  /** The full card, drawn by the caller for the machine as it stands. */
  card: ReactNode
  /** Where the card opens beside the orb. */
  place?: PresencePlace
} & PresenceHandlers) {
  const view = viewOf(machine)
  const truth = view.truth
  if (!view.shows || !truth) return null
  const live = Boolean(onClick)
  const room = view.room
  const open = room === 'expanded' || (room === 'floating' && machine.card === 'open')
  const line = room === 'compact' ? lineOf(truth, 'topEdgeCompact')
    : room === 'floating' && (machine.card === 'hover' || machine.shown) ? lineOf(truth, 'floatingBubble')
      : ''
  return (
    <div data-buddy-presence="" data-room={room ?? 'none'} className="relative inline-flex shrink-0">
      {/* Its name is the card's whole line, never a shortened one: a listener gets every word. */}
      <BuddyFace state={orbOf(truth, machine)} label={lineOf(truth, 'expandedCard')} onClick={onClick} onPointer={onPointer} />
      {open && card ? (
        <CardSurface place={place} live={live} wide>{card}</CardSurface>
      ) : line ? (
        <CardSurface place={place} live={live && room === 'compact'} wide={false}>
          {live && room === 'compact' ? (
            <Button variant="secondary" size="sm" onClick={onClick} className="min-w-0 max-w-full">
              <span className="min-w-0 truncate">{line}</span>
              {view.inSight ? <span data-type="label-s" className="shrink-0 text-on-surface-var">+{view.inSight}</span> : null}
            </Button>
          ) : (
            <p data-buddy-line-card="" data-type="body-s" className="flex min-w-0 max-w-full items-center gap-s rounded-lg bg-surface-high px-m py-xs text-on-surface">
              <span className="min-w-0 truncate">{line}</span>
              {room === 'compact' && view.inSight ? <span data-type="label-s" className="shrink-0 text-on-surface-var">+{view.inSight}</span> : null}
            </p>
          )}
        </CardSurface>
      ) : null}
    </div>
  )
}
