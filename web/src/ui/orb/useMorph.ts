import { useEffect, useRef, useState } from 'react'
import { animate, motionValue } from 'framer-motion'
import { physics } from '../../design/motion'
import type { OrbState, OrbStateName } from '../../lib/orbState'
import { cappedDriver } from '../orbDots'
import { mixToward, transitionOf, type Form } from './clawRing'

export interface Morph {
  /** Whether a change is moving the form now: a morph, or a morph with done's bloom. */
  changing: 'none' | 'morph' | 'bloom'
  /** The form to paint for `target`, the state's form at this instant: mixed from the form last
   *  painted while a morph runs, and remembered as the form last painted. */
  formOf: (target: Form) => Form
  /** The whole form's scale: 1, above it through the bloom. */
  scale: () => number
}

/** The claw ring's change of state (`docs/design/agent-orb.md`, "The five states"): from the form
 *  last painted to `look`'s, on the smooth spring, and done's one bloom on the playful one, both on
 *  the capped clock the orb paints on, so they step in the frame it is painted in. The agent orb and
 *  the buddy's orb both change state through it.
 *
 *  It is keyed on the state, so a phase that changes inside one state is no change. An `instant`
 *  change (reduced motion, a hidden tab, an orb off screen, a still mark) swaps at once, and a change
 *  in flight when `instant` turns on settles at its target, so nothing is left half-formed or
 *  painting. A bloom cut short by the next change brings its scale home on that change's spring,
 *  never in a jump. Each stroke's way round is chosen once a morph, from the target as the morph's
 *  first frame found it (`mixToward`), so a target that keeps moving never flips a stroke.
 *  `onSettled` is called with the state settled on: when a change ends, at once for a swap, and
 *  on the first paint. `bloom` (default true) says whether arriving at done may bloom, read when the
 *  change starts: a turn that was stopped or declined closes to done's form without it. */
export function useMorph(look: OrbState, instant: boolean, onSettled?: (state: OrbStateName) => void, bloom = true): Morph {
  const lastForm = useRef<Form | null>(null)
  const prevLook = useRef<OrbState>(look)
  const morph = useRef<{ from: Form | null; kept: Form | null }>({ from: null, kept: null })
  const [k] = useState(() => motionValue(1))
  const [scale] = useState(() => motionValue(1))
  const [changing, setChanging] = useState<Morph['changing']>('none')
  // A change under way, set in the effect that starts it: the render that changed the state still
  // reads `changing` as 'none', and must not say it has settled.
  const inFlight = useRef(false)
  const blooms = useRef(bloom)
  blooms.current = bloom
  useEffect(() => {
    const from = prevLook.current
    prevLook.current = look
    const settle = () => { inFlight.current = false; morph.current.from = null; morph.current.kept = null; k.set(1); scale.set(1); setChanging('none') }
    // No change of state (the platform or the screen changed, or a phase changed inside one state):
    // finish any change in flight at its target.
    if (from.state === look.state) { if (morph.current.from || scale.get() !== 1) settle(); return }
    const planned = transitionOf(from, look, { reduced: instant })
    const plan = { ...planned, bloom: planned.bloom && blooms.current }
    if (plan.instant || !lastForm.current) { settle(); return }
    morph.current.from = lastForm.current
    morph.current.kept = null
    inFlight.current = true
    k.set(0)
    setChanging(plan.bloom ? 'bloom' : 'morph')
    const driver = cappedDriver()
    const runs = [animate(k, 1, { ...physics.smooth, driver })]
    if (plan.bloom) runs.push(animate(scale, 1, { ...physics.playful, velocity: 2.4, driver }))
    else if (scale.get() !== 1) runs.push(animate(scale, 1, { ...physics.smooth, driver }))
    let live = true
    Promise.all(runs.map((r) => r.finished)).then(() => { if (live) settle() })
    return () => { live = false; for (const r of runs) r.stop() }
  }, [look.state, instant]) // eslint-disable-line react-hooks/exhaustive-deps -- keyed on the state; the ink and the badge follow the look in the paint and the render

  const settledRef = useRef(onSettled)
  settledRef.current = onSettled
  useEffect(() => { if (changing === 'none' && !inFlight.current) settledRef.current?.(look.state) }, [changing, look.state])

  return {
    changing,
    formOf: (target) => {
      const from = morph.current.from
      let form = target
      if (from) {
        morph.current.kept ??= target
        form = mixToward(from, target, morph.current.kept, Math.min(1, Math.max(0, k.get())))
      }
      lastForm.current = form
      return form
    },
    scale: () => scale.get(),
  }
}
