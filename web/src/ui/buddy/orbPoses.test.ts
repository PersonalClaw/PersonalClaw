import { afterEach, describe, expect, it } from 'vitest'
import { animate, motionValue, type Transition } from 'framer-motion'
import * as motion from '../../design/motion'
import { runtime } from '../../design/runtime'
import { WORK_PHASES } from '../../lib/workPhase'
import { TIMINGS } from '../../lib/buddy/timing'
import { ORB_TRANSITIONS, poseOf, presetTransition, transitionNameOf, transitionOf, type BuddyState, type OrbTransition } from './orbPoses'
import { POSE_KEYS, type PoseKey } from './useOrbPose'

// ── The buddy orb's transitions table (#3891) ──────────────────────────────────────────────────
//
// Every transition names one of `design/motion.ts`'s presets and one of the buddy's timing tokens
// (`lib/buddy/timing.ts`), so no spring or length is written in the renderer. The dials then reach
// every move: at bounciness 0 no pose passes its target, and at the expressiveness floor every
// state still moves the orb away from the working pose.

const STATES: readonly BuddyState[] = [...WORK_PHASES, 'restarting', 'offline']
const DEFAULTS = { bounciness: runtime.bounciness, expressiveness: runtime.expressiveness }

afterEach(() => {
  Object.assign(runtime, DEFAULTS)
})

/** What is wrong with each entry of a transitions table: a preset that is not one of motion.ts's,
 *  or a length that is not a named timing token. */
function faults(table: Record<string, OrbTransition>): string[] {
  const out: string[] = []
  for (const [name, t] of Object.entries(table)) {
    const preset = t.preset === 'effects' ? 'effects' in motion.spring : t.preset in motion.physics
    if (!preset) out.push(`${name}: ${t.preset} is not a motion.ts preset`)
    if (!(t.token in TIMINGS)) out.push(`${name}: ${t.token} is not a timing token`)
  }
  return out
}

describe('every transition is a preset and a token', () => {
  it('walks the table: each names a motion.ts preset and a timing token', () => {
    expect(faults(ORB_TRANSITIONS)).toEqual([])
  })

  it('fails an entry with its own spring or its own length: the control', () => {
    const planted = {
      wobbly: { preset: 'wobbly', token: 'beat', does: 'a spring of its own' },
      long: { preset: 'snappy', token: '300ms', does: 'a length of its own' },
    } as unknown as Record<string, OrbTransition>
    expect(faults(planted)).toHaveLength(2)
  })

  it('hands out exactly the preset it names, read when the move starts', () => {
    for (const t of Object.values(ORB_TRANSITIONS)) {
      const fromMotion = t.preset === 'effects' ? motion.spring.effects : motion.physics[t.preset]
      expect(presetTransition(t.preset), t.preset).toEqual(fromMotion)
    }
    runtime.bounciness = 0
    expect(presetTransition('playful')).toEqual(motion.physics.playful)
  })

  it('moves every parameter of every state on an entry of the table', () => {
    for (const state of STATES) {
      for (const key of POSE_KEYS) expect(Object.keys(ORB_TRANSITIONS), `${state}.${key}`).toContain(transitionNameOf(state, key))
    }
  })
})

/** How far past its target `transition` carries a value moved from `from` to `to`, as a share of
 *  the move: the motion library run on a clock the test steps, 4 ms at a time for 3 s. */
function overshootOf(transition: Transition, from: number, to: number): number {
  let now = 0
  let tick: ((t: number) => void) | undefined
  const driver = (update: (t: number) => void) => ({ start: () => { tick = update }, stop: () => { tick = undefined }, now: () => now })
  const value = motionValue(from)
  let worst = 0
  value.on('change', (v) => { worst = Math.max(worst, (v - to) / (to - from)) })
  animate(value, to, { ...transition, driver } as Transition)
  for (; now <= 3000 && tick; now += 4) tick(now)
  value.stop()
  return worst
}

/** Every move between two states' poses: the transition that takes `key` into `to`. */
function moves(): { label: string; transition: Transition; from: number; to: number }[] {
  const out = []
  for (const from of STATES) {
    for (const to of STATES) {
      const a = poseOf(from)
      const b = poseOf(to)
      for (const key of POSE_KEYS) {
        if (key === 'swirl' || Math.abs(a[key] - b[key]) < 1e-6) continue
        out.push({ label: `${from} → ${to}, ${key}`, transition: transitionOf(to, key), from: a[key], to: b[key] })
      }
    }
  }
  // The done flourish turns on from wherever the orb stands.
  out.push({ label: 'done, swirl', transition: transitionOf('done', 'swirl'), from: 0, to: poseOf('done').swirl })
  return out
}

describe('the dials reach every move', () => {
  it('at bounciness 0 no pose passes its target', () => {
    runtime.bounciness = 0
    const all = moves()
    expect(all.length).toBeGreaterThan(50)
    const over = all.filter((m) => overshootOf(m.transition, m.from, m.to) > 1e-4).map((m) => m.label)
    expect(over).toEqual([])
  })

  it('at bounciness 1 the done flourish does overshoot: the control', () => {
    runtime.bounciness = 1
    expect(overshootOf(transitionOf('done', 'swirl'), 0, poseOf('done').swirl)).toBeGreaterThan(0.01)
  })

  it('at the expressiveness floor every state still moves the orb away from the working pose', () => {
    const away = (key: PoseKey, state: BuddyState) => Math.abs(poseOf(state)[key] - poseOf('tool')[key])
    const atDefault = STATES.flatMap((state) => POSE_KEYS.filter((key) => away(key, state) > 1e-6).map((key) => ({ state, key, by: away(key, state) })))
    expect(atDefault.map((m) => `${m.state}.${m.key}`)).toEqual(expect.arrayContaining(['approval.spread', 'input.lift', 'error.sink', 'done.swirl']))
    runtime.expressiveness = 0
    const flattened = atDefault.filter((m) => away(m.key, m.state) < m.by / 3).map((m) => `${m.state}.${m.key}`)
    expect(flattened, 'these moves all but vanish at the expressiveness floor').toEqual([])
  })
})
