import { useCallback, useEffect, useRef, useState } from 'react'
import { ORB_STATE_OF, type OrbStateName } from '../lib/orbState'
import type { WorkPhase } from '../lib/workPhase'
import { duration } from '../design/motion'
import type { TurnOutcome } from '../pages/chat/turnOutcome'

/** How long a shown turn's line or head stays at least, and how long it holds a turn's ending once
 *  its orb has settled on it (`docs/design/agent-orb.md`, `minHold`). */
export const MIN_HOLD_MS = 300
/** How long a turn's line or head takes to fade once its hold is over, in ms: the Medium length its
 *  fade's CSS transition takes (`--duration-medium`), read when the fade starts, so the line goes
 *  as its fade ends whatever length the owner set. */
export function fadeMs(): number {
  return duration.medium * 1000
}

/** How a turn ended, from its last `work_phase` frame: `done` or `error`, its ending line, and its
 *  outcome (`last_outcome`): only a `complete` turn plays done's bloom, and a stopped or declined
 *  one closes to done's form without it. */
export interface TurnEnding { phase: WorkPhase; line: string; outcome: TurnOutcome | null }

/** Whether an orb showing `ending` plays done's bloom: only for a turn that completed. With no ending
 *  (a turn still running), the orb's own rule stands. */
export function bloomsFor(ending: TurnEnding | null): boolean {
  return !ending || ending.outcome === 'complete'
}

/** The turn's ending while it is held, and the callback its orb calls once it has settled
 *  (`AgentOrb`'s `onSettled`). An ending is held from the turn's last frame until its orb has settled
 *  on the ending's form, done's bloom or the swap to a failure's ×, and `MIN_HOLD_MS` more; then
 *  `held` is `null`. An ending that arrives while nothing shows it (`showing` false) is not held. */
export function useHeldEnding(ending: TurnEnding | null, showing: boolean): { held: TurnEnding | null; onSettled: (state: OrbStateName) => void } {
  const [over, setOver] = useState<TurnEnding | null>(null)
  const current = useRef(ending)
  current.current = ending
  const timing = useRef<TurnEnding | null>(null)
  const timers = useRef(new Set<number>())
  useEffect(() => () => { for (const t of timers.current) window.clearTimeout(t) }, [])
  // Read when the ending arrives: an orb that is not shown has nothing to settle, so nothing holds.
  useEffect(() => { if (ending && !showing) setOver(ending) }, [ending]) // eslint-disable-line react-hooks/exhaustive-deps -- once per ending
  // Its orb settles in its own effect, which runs before this hook's host's: so the ending is read
  // from the render that drew it, and each ending's hold is timed once.
  const onSettled = useCallback((state: OrbStateName) => {
    const e = current.current
    if (!e || timing.current === e || state !== ORB_STATE_OF[e.phase].state) return
    timing.current = e
    const t = window.setTimeout(() => { timers.current.delete(t); setOver(e) }, MIN_HOLD_MS)
    timers.current.add(t)
  }, [])
  return { held: ending && ending !== over ? ending : null, onSettled }
}
