import { useEffect, useRef, useState } from 'react'
import { animate, motionValue } from 'framer-motion'
import { physics } from '../../design/motion'
import type { OrbScene } from '../../lib/orbState'
import { cappedDriver } from '../orbDots'
import { DONE_MOVES_S, type Weights } from './halftone'

export interface SceneMorph {
  /** Whether a change is moving the dots now: a morph, or a morph into done's swirl and bump. */
  changing: 'none' | 'morph' | 'bloom'
  /** Each state's weight now: the target alone when settled, blended from what was last painted
   *  while a morph runs, and remembered as what was last painted. */
  weights: () => Weights
  /** Seconds since the state now shown was entered, at the page clock's `now` (seconds). */
  since: (now: number) => number
  /** Whether done plays its swirl and bump: only on an arrival that blooms. */
  blooms: () => boolean
}

const onehot = (s: OrbScene): Weights => ({ [s]: 1 })
const seconds = () => performance.now() / 1000

/** The halftone orb's change of state (`docs/design/agent-orb.md`, "Behaviour"): every dot moves
 *  from its place in the weights last painted to its place in the new state's, on the smooth spring,
 *  on the capped clock the orb paints on; mid-change the dots burst a little and land
 *  (`halftone.ts`). Arriving at done from a turn that completed plays done's one swirl and bump on
 *  top, and the change counts as moving until they have landed.
 *
 *  It is keyed on the state, so a phase that changes inside one state is no change. An `instant`
 *  change (reduced motion, a hidden tab, an orb off screen, a still mark, an orb that has never been
 *  painted) swaps at once, and a change
 *  in flight when `instant` turns on settles at its target, so nothing is left half-drawn or
 *  painting. A scene with phases starts from its beginning each time its state is entered; an orb
 *  that mounts in done is drawn settled. `onSettled` is called with the state settled on: when a
 *  change ends, at once for a swap, and on the first paint. `bloom` (default true) says whether an
 *  arrival at done may play its swirl, read when the change starts. */
export function useSceneMorph(scene: OrbScene, instant: boolean, onSettled?: (scene: OrbScene) => void, bloom = true): SceneMorph {
  const last = useRef<Weights>(onehot(scene))
  // Whether anything has been painted: an orb with no canvas to paint on has nothing to morph from.
  const painted = useRef(false)
  const prev = useRef<OrbScene>(scene)
  const from = useRef<Weights | null>(null)
  const entered = useRef(seconds())
  const blooming = useRef(false)
  const [k] = useState(() => motionValue(1))
  const [changing, setChanging] = useState<SceneMorph['changing']>('none')
  const inFlight = useRef(false)
  const blooms = useRef(bloom)
  blooms.current = bloom
  useEffect(() => {
    const was = prev.current
    prev.current = scene
    const settle = () => { inFlight.current = false; from.current = null; k.set(1); setChanging('none') }
    if (was === scene) { if (from.current || inFlight.current) { blooming.current = false; settle() } return }
    entered.current = seconds()
    blooming.current = scene === 'done' && blooms.current && !instant
    if (instant || !painted.current) { blooming.current = false; settle(); return }
    from.current = last.current
    inFlight.current = true
    k.set(0)
    setChanging(blooming.current ? 'bloom' : 'morph')
    const run = animate(k, 1, { ...physics.smooth, driver: cappedDriver() })
    // Done's swirl and bump land by `DONE_MOVES_S`: the change holds until both the morph and they have.
    const landed = blooming.current ? new Promise<void>((resolve) => { window.setTimeout(resolve, DONE_MOVES_S * 1000) }) : Promise.resolve()
    let live = true
    Promise.all([run.finished, landed]).then(() => { if (live) settle() })
    return () => { live = false; run.stop() }
  }, [scene, instant]) // eslint-disable-line react-hooks/exhaustive-deps -- keyed on the state

  const settledRef = useRef(onSettled)
  settledRef.current = onSettled
  useEffect(() => { if (changing === 'none' && !inFlight.current) settledRef.current?.(scene) }, [changing, scene])

  return {
    changing,
    weights: () => {
      const f = from.current
      let w: Weights = onehot(scene)
      if (f) {
        const kk = k.get()
        w = {}
        for (const s of new Set([...Object.keys(f), scene] as OrbScene[])) {
          const a = f[s] ?? 0, b = s === scene ? 1 : 0
          const v = Math.max(0, a + (b - a) * kk)
          if (v > 0.001) w[s] = v
        }
      }
      last.current = w
      painted.current = true
      return w
    },
    since: (now) => now - entered.current,
    blooms: () => blooming.current,
  }
}
