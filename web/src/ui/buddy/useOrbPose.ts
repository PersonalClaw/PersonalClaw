/** The buddy orb's pose (#3891): one motion value per pose parameter, moved into each pose by the
 *  transition its table names, on the dots' capped clock.
 *
 *  It enters the first pose where it stands, so a mount moves nothing. A change of what it shows
 *  (`entered`), or of the dials, moves each parameter that differs on its preset; a preset that
 *  reduced motion has made instant is applied as a jump, so a change under reduced motion asks for
 *  no frame at all. `moving` is true from a change until every parameter it started has settled:
 *  the orb draws frames only while it is, or while its dots move. The values step on the dots'
 *  capped clock (`cappedDriver`), at the buddy's frame rate and in the frame the dots are painted
 *  in, rather than on every frame the display draws (`docs/design/buddy/decisions/rig.md`). */

import { useEffect, useMemo, useRef, useState } from 'react'
import { animate, motionValue, type MotionValue, type Transition } from 'framer-motion'
import { cappedDriver, ORB_FPS } from '../orbDots'

/** The parameters a pose sets: the dots' share, spread, droop and turn, the whole orb's rise or
 *  fall, and the alert's wash. */
export const POSE_KEYS = ['density', 'spread', 'sink', 'lift', 'swirl', 'wash'] as const
export type PoseKey = (typeof POSE_KEYS)[number]
export type Pose = Record<PoseKey, number>

export type PoseValues = Readonly<Record<PoseKey, MotionValue<number>>>

export interface PoseHandle {
  values: PoseValues
  /** The pose's parameters as they stand now. */
  read: () => Pose
  /** True while a change is still moving any of them. */
  moving: boolean
}

/** What a pose table answers for the pose being entered. */
export interface PoseTable {
  /** Where `key` goes, from where it stands now (the done flourish turns on from wherever it is). */
  targetOf: (key: PoseKey, now: number) => number
  /** The transition that moves it there: one of the app's presets. */
  transitionOf: (key: PoseKey) => Transition
}

/** Whether `t` is the reduced-motion answer: no time at all. */
function isInstant(t: Transition): boolean {
  const { type, duration } = t as { type?: string; duration?: number }
  return type === 'tween' && duration === 0
}

/** The pose `table` names for `entered`, which is any value that changes when the pose or the
 *  dials do. `fps` is the dots' rate, as `runDots` takes it: the buddy's unless set, and `null` or 0
 *  for every frame the display draws. */
export function useOrbPose(entered: string, table: PoseTable, fps?: number | null): PoseHandle {
  const rate = fps === undefined ? ORB_FPS : fps
  const driver = useMemo(() => (rate ? cappedDriver(rate) : undefined), [rate])
  const latest = useRef(table)
  latest.current = table
  const values = useMemo<PoseValues>(
    () => Object.fromEntries(POSE_KEYS.map((key) => [key, motionValue(table.targetOf(key, 0))])) as Record<PoseKey, MotionValue<number>>,
    [], // the first pose only: every later change moves these values rather than replacing them
  )
  const [moving, setMoving] = useState(false)
  const shown = useRef(entered)
  const generation = useRef(0)

  useEffect(() => {
    if (shown.current === entered) return
    shown.current = entered
    const step = ++generation.current
    const runs: Promise<unknown>[] = []
    for (const key of POSE_KEYS) {
      const value = values[key]
      const target = latest.current.targetOf(key, value.get())
      if (value.get() === target) continue
      const transition = latest.current.transitionOf(key)
      if (isInstant(transition)) value.jump(target)
      else runs.push(animate(value, target, driver ? { ...transition, driver } : transition).finished)
    }
    if (!runs.length) { setMoving(false); return }
    setMoving(true)
    void Promise.all(runs).then(() => { if (generation.current === step) setMoving(false) })
  }, [entered, values, driver])

  // An orb that leaves stops its pose where it stands, so nothing of it stays on the clock.
  useEffect(() => () => { for (const key of POSE_KEYS) values[key].stop() }, [values])

  return useMemo(() => ({
    values,
    moving,
    read: () => Object.fromEntries(POSE_KEYS.map((key) => [key, values[key].get()])) as Pose,
  }), [values, moving])
}
