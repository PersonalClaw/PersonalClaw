/** The buddy as it shows (#3832): drawn from the state machine's view (`viewOf`), the same for the
 *  playground's stage and the live presence at `#/buddy`.
 *
 *  Docked, the top edge holds the island: the peek's orb, the compact line with its count of other
 *  work, or the expanded card. Undocked, the floating orb sits with its bubble or its card. The
 *  card is the caller's, so the playground draws it from made-up rows and the presence from the
 *  gateway's. Nothing here answers: a click on the orb or the line is the machine's `click`, and
 *  only the card's answer controls answer. */

import type { ReactNode } from 'react'
import { BuddyOrb } from '../../ui/BuddyOrb'
import { Button } from '../../ui/Button'
import { Morph } from '../../ui/motion/Morph'
import { orbStateOf, type BuddyState as OrbState } from '../../ui/buddy/orbPoses'
import { focusedWork, viewOf, type BuddyState, type Truth } from '../../lib/buddy/machine'
import { lineOf } from '../../lib/buddy/lines'

/** The orb's state for a truth, over the focused work's own phase for the lines the machine adds
 *  of its own (connected again, answered elsewhere, the return summary). */
export function orbOf(truth: Truth, machine: BuddyState): OrbState {
  return orbStateOf(truth, focusedWork(machine)?.phase ?? 'idle')
}

export interface PresenceHandlers {
  /** A click on the orb or the compact line: the machine's `click`. */
  onClick?: () => void
  /** The pointer rests on the orb, or leaves it. */
  onPointer?: (at: 'orb' | 'away') => void
}

/** A control the owner may press, where the caller handles one; the playground's stage draws the
 *  same thing inert, since its inputs are its own controls. The live one is the shared `Button`, a
 *  44 px target and more, and the pointer resting on it is the machine's hover. */
function Hit({ label, className, children, onClick, onPointer }: { label?: string; className?: string; children: ReactNode } & PresenceHandlers) {
  if (!onClick) return className ? <div className={className}>{children}</div> : <>{children}</>
  return (
    // The face: in the buddy's own window, clicks land here and pass through everywhere else, and a
    // drag of it moves the window (`app/BuddyWindowRoot.tsx`, #3831).
    <div data-buddy-shape="" data-buddy-drag="" className="flex min-w-0 max-w-full"
      onPointerEnter={() => onPointer?.('orb')} onPointerLeave={() => onPointer?.('away')}>
      <Button variant={className ? 'secondary' : 'ghost'} size="lg" ariaLabel={label} onClick={onClick} className="min-w-0 max-w-full">
        {children}
      </Button>
    </div>
  )
}

/** The buddy's room where it stands: `null` when nothing shows. */
export function BuddyPresence({ machine, card, orbId, onClick, onPointer }: {
  machine: BuddyState
  /** The expanded card, drawn by the caller for the machine as it stands. */
  card: ReactNode
  /** The shared element's id: the compact island's orb and the card's are one object. */
  orbId: string
} & PresenceHandlers) {
  const view = viewOf(machine)
  const truth = view.truth
  if (!view.shows || !truth) return null
  const hit = { onClick, onPointer }
  // The open card takes clicks in the buddy's own window, as the face does.
  const shaped = onClick && card ? <div data-buddy-shape="" className="flex w-full min-w-0 justify-center">{card}</div> : card
  if (machine.docked) {
    return (
      <div data-buddy-island="" data-room={view.room ?? 'none'} className="flex w-full min-w-0 max-w-md flex-col items-center">
        {view.room === 'peek' ? (
          <Hit {...hit} label={lineOf(truth, 'topEdgeCompact')}>
            <BuddyOrb state={orbOf(truth, machine)} line="" size={18} />
          </Hit>
        ) : view.room === 'expanded' ? (
          shaped
        ) : (
          <Hit {...hit} className="flex w-full min-w-0 max-w-sm items-center gap-s rounded-full bg-surface-high px-m py-xs">
            <Morph id={orbId} className="flex min-w-0 flex-1"><BuddyOrb state={orbOf(truth, machine)} line={lineOf(truth, 'topEdgeCompact')} size={24} /></Morph>
            {view.inSight ? <span data-type="label-s" className="shrink-0 text-on-surface-var">+{view.inSight}</span> : null}
          </Hit>
        )}
      </div>
    )
  }
  return (
    <div data-buddy-floating="" className="mt-auto flex w-full min-w-0 flex-col items-end gap-s">
      {machine.card === 'open' ? shaped : null}
      {machine.card === 'hover' || machine.shown ? (
        <p data-type="body-s" className="max-w-full rounded-lg bg-surface-high px-m py-xs text-on-surface">{lineOf(truth, 'floatingBubble')}</p>
      ) : null}
      <Hit {...hit} label={lineOf(truth, 'floatingBubble')}>
        <BuddyOrb state={orbOf(truth, machine)} line="" size={48} />
      </Hit>
    </div>
  )
}
