/** The buddy as it shows (#3832, #5216): drawn from the state machine's view (`viewOf`), the same
 *  for the playground's stage and the live presence at `#/buddy`.
 *
 *  The buddy is one orb, of one size, that stays where it was put: its state is in its dots, its
 *  badge and its wash, never in its size or its place. Both are drawn where `layout` says: the
 *  orb's box, and beside it the area its card may use, on the side that has room (the buddy
 *  window's shell chooses it; elsewhere `lib/buddy/cardPlacement.ts` runs the same rule). In that
 *  area goes one line while work runs (what the top edge's compact pill said) or on a floating
 *  orb's alert, and the full card (`ui/BuddyCard.tsx`) when it opens, anchored to the orb and
 *  growing away from it. Opening, growing, shrinking or closing it never moves the orb or changes
 *  its size. The full
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
import type { BuddyLayout, BuddyRect } from '../../lib/desktopBridge'

/** The orb's one size, in every state, family and event. */
export const ORB_SIZE = 48
/** Its slot: a 56 pt square around the 48 pt orb (the shared `Button`'s `orb` size), the box the
 *  buddy's window keeps for it. */
export const ORB_SLOT = 56

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

/** The line card's widest: what the compact pill held. */
export const LINE_WIDTH = 360

const px = (r: BuddyRect) => ({ left: r.x, top: r.y, width: r.width, height: r.height })

/** The orb, in its one slot: at `at` in the buddy's window (the shell's layout), or in the flow.
 *  Live (with `onClick`), the face is the shape clicks land on and the handle a drag moves the
 *  window by (`app/BuddyWindowRoot.tsx`, #3831), and the shared `Button`, a 44 px target and more;
 *  in the playground's stage it is drawn inert. */
export function BuddyFace({ state, label, at, onClick, onPointer }: { state: OrbState; label: string; at?: BuddyRect } & PresenceHandlers) {
  const orb = <BuddyOrb state={state} line="" size={ORB_SIZE} />
  return (
    <div data-buddy-orb-slot="" {...(onClick ? { 'data-buddy-shape': '', 'data-buddy-drag': '' } : {})}
      className={at ? 'absolute flex items-center justify-center' : 'relative flex shrink-0'} style={at ? px(at) : undefined}
      onPointerEnter={() => onPointer?.('orb')} onPointerLeave={() => onPointer?.('away')}>
      {onClick ? <Button variant="ghost" size="orb" ariaLabel={label} onClick={onClick}>{orb}</Button> : <span className="flex h-14 w-14 items-center justify-center">{orb}</span>}
    </div>
  )
}

/** Along the area's width, where the card sits: against the orb's side, or centred on it. */
const ACROSS: Record<BuddyLayout['side'], string> = { right: 'justify-start', left: 'justify-end', below: 'justify-center', above: 'justify-center' }

/** A surface in the area the shell's layout gives the card: anchored to the orb's edge and growing
 *  away from it, down from the orb's top or up from its bottom. The area itself takes no click;
 *  only what is drawn in it does. */
export function CardInArea({ layout, live, wide, children }: { layout: BuddyLayout; live: boolean; wide: boolean; children: ReactNode }) {
  const up = layout.side === 'above' || (layout.side !== 'below' && layout.opens === 'up')
  return (
    <div data-buddy-card-area="" data-side={layout.side} data-opens={layout.opens}
      className={cx('pointer-events-none absolute z-10 flex', ACROSS[layout.side], up ? 'items-end' : 'items-start')} style={px(layout.card)}>
      <div data-buddy-card-surface="" data-side={layout.side} {...(live ? { 'data-buddy-shape': '' } : {})}
        className="pointer-events-auto flex max-h-full min-w-0 overflow-y-auto"
        style={wide ? { width: '100%' } : { maxWidth: Math.min(layout.card.width, LINE_WIDTH) }}>
        {children}
      </div>
    </div>
  )
}

/** The buddy where it stands: `null` when nothing shows. */
export function BuddyPresence({ machine, card, layout, onClick, onPointer }: {
  machine: BuddyState
  /** The full card, drawn by the caller for the machine as it stands. */
  card: ReactNode
  /** Where the orb's box is and the area its card may use, in the page's points: the buddy
   *  window's shape from its shell, or the same rule run over a tab or the playground's screen
   *  (`lib/buddy/cardPlacement.ts`). */
  layout: BuddyLayout
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
  const lineCard = live && room === 'compact' ? (
    <Button variant="secondary" size="sm" onClick={onClick} className="min-w-0 max-w-full">
      <span className="min-w-0 truncate">{line}</span>
      {view.inSight ? <span data-type="label-s" className="shrink-0 text-on-surface-var">+{view.inSight}</span> : null}
    </Button>
  ) : (
    <p data-buddy-line-card="" data-type="body-s" className="flex min-w-0 max-w-full items-center gap-s rounded-lg bg-surface-high px-m py-xs text-on-surface">
      <span className="min-w-0 truncate">{line}</span>
      {room === 'compact' && view.inSight ? <span data-type="label-s" className="shrink-0 text-on-surface-var">+{view.inSight}</span> : null}
    </p>
  )
  return (
    <div data-buddy-presence="" data-room={room ?? 'none'} className="absolute inset-0">
      {/* Its name is the card's whole line, never a shortened one: a listener gets every word. */}
      <BuddyFace state={orbOf(truth, machine)} label={lineOf(truth, 'expandedCard')} at={layout.orb} onClick={onClick} onPointer={onPointer} />
      {open && card ? <CardInArea layout={layout} live={live} wide>{card}</CardInArea>
        : line ? <CardInArea layout={layout} live={live && room === 'compact'} wide={false}>{lineCard}</CardInArea>
          : null}
    </div>
  )
}
