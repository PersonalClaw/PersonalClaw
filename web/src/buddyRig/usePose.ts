/** A rig candidate's pose (#3887): one motion value per pose parameter, moved into each phase's pose
 *  by the transition the table names (`poses.ts`).
 *
 *  It enters the first pose where it stands, so a mount moves nothing. A change of phase, or of the
 *  dials, moves each parameter that differs on its preset; a preset that reduced motion has made
 *  instant is applied as a jump, so a change under reduced motion asks for no frame at all. `moving`
 *  is true from a change until every parameter it started has settled: the candidates draw frames
 *  only while it is, or while the dots themselves move. */

import { useEffect, useMemo, useRef, useState } from 'react'
import { animate, motionValue, type MotionValue, type Transition } from 'framer-motion'
import { POSE_KEYS, poseOf, poseTransition, type Pose, type PoseKey, type RigPhase } from './poses'

export type PoseValues = Readonly<Record<PoseKey, MotionValue<number>>>

export interface PoseHandle {
  values: PoseValues
  /** The pose's parameters as they stand now. */
  read: () => Pose
  /** True while a change is still moving any of them. */
  moving: boolean
}

/** Whether `t` is the reduced-motion answer: no time at all. */
function isInstant(t: Transition): boolean {
  const { type, duration } = t as { type?: string; duration?: number }
  return type === 'tween' && duration === 0
}

/** Where `key` goes for `phase` from `now`. The done flourish turns on from wherever the dots are,
 *  so it never unwinds: every other phase leaves the turn where it is. */
function targetOf(phase: RigPhase, key: PoseKey, now: number): number {
  const pose = poseOf(phase)
  return key === 'swirl' ? now + pose.swirl : pose[key]
}

/** The pose for `phase`. `dials` is any value that changes when the motion dials do, so a pose
 *  re-reads its amplitudes and its presets when the owner moves one. */
export function usePose(phase: RigPhase, dials: string): PoseHandle {
  const values = useMemo<PoseValues>(() => {
    const pose = poseOf(phase)
    return Object.fromEntries(POSE_KEYS.map((key) => [key, motionValue(pose[key])])) as Record<PoseKey, MotionValue<number>>
  }, []) // the first pose only: every later change moves these values rather than replacing them
  const [moving, setMoving] = useState(false)
  const entered = useRef<string>(`${phase}|${dials}`)
  const generation = useRef(0)

  useEffect(() => {
    const key = `${phase}|${dials}`
    if (entered.current === key) return
    entered.current = key
    const step = ++generation.current
    const runs: Promise<unknown>[] = []
    for (const param of POSE_KEYS) {
      const value = values[param]
      const target = targetOf(phase, param, value.get())
      if (value.get() === target) continue
      const transition = poseTransition(phase, param)
      if (isInstant(transition)) value.jump(target)
      else runs.push(animate(value, target, transition).finished)
    }
    if (!runs.length) { setMoving(false); return }
    setMoving(true)
    void Promise.all(runs).then(() => { if (generation.current === step) setMoving(false) })
  }, [phase, dials, values])

  return useMemo(() => ({
    values,
    moving,
    read: () => Object.fromEntries(POSE_KEYS.map((key) => [key, values[key].get()])) as unknown as Pose,
  }), [values, moving])
}
