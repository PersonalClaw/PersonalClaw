/**
 * The poses both rig candidates draw (#3887): the four families and the focused card, on the app's
 * presets, with the dials doing what `docs/design/buddy/motion.md` says they do.
 *
 * Both candidates read this one table, so the spike compares two ways of drawing the same poses
 * rather than two sets of poses. What it holds:
 *
 *  - each family's look follows the agent orb's (`ORB_LOOKS`): the same drawing, the alive ink while
 *    an agent works or waits on the owner, the rest ink where nothing is alive, and a badge whose
 *    mark, not its colour, says which waiting or failed state it is;
 *  - every move is one of the four presets or the effects curve, chosen by the transitions table,
 *    so `--bounciness` reaches the overshoot and reduced motion makes every change instant;
 *  - every amplitude goes through `expr()`, so expressiveness scales how far the orb lifts, sinks,
 *    draws in and swirls, and at its floor every pose still differs from its neighbours.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { Transition } from 'framer-motion'
import { runtime } from '../design/runtime'
import { ORB_LOOKS } from '../ui/AgentOrb'
import { phaseFamily } from '../lib/workPhase'
import {
  BUDDY_SIZES, POSE_KEYS, RIG_LOOP, RIG_PHASES, cardTransition, poseOf, poseTransition, rigLook, tunedDrawingFor,
  type PoseKey, type RigPhase,
} from './poses'

const ORIGINAL = { matchMedia: window.matchMedia, bounciness: runtime.bounciness, expressiveness: runtime.expressiveness }

function setReducedMotion(on: boolean): void {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: ((query: string) => ({
      matches: on && query.includes('prefers-reduced-motion'),
      media: query, onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia,
  })
}

beforeEach(() => setReducedMotion(false))
afterEach(() => {
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL.matchMedia })
  runtime.bounciness = ORIGINAL.bounciness
  runtime.expressiveness = ORIGINAL.expressiveness
})

/** How far a spring transition overshoots its target, as a fraction of the move: an underdamped
 *  spring's first peak, `exp(-ζπ/√(1-ζ²))`, and nothing for a tween or a critically damped one. */
function overshootOf(t: Transition): number {
  const s = t as { type?: string; stiffness?: number; damping?: number; mass?: number }
  if (s.type !== 'spring' || s.stiffness === undefined || s.damping === undefined) return 0
  const zeta = s.damping / (2 * Math.sqrt(s.stiffness * (s.mass ?? 1)))
  return zeta >= 1 ? 0 : Math.exp((-zeta * Math.PI) / Math.sqrt(1 - zeta * zeta))
}

describe('the looks follow the agent orb', () => {
  it('covers the four families', () => {
    expect(new Set(RIG_PHASES.map((p) => rigLook(p).family))).toEqual(new Set(['quiet', 'active', 'waiting', 'blocked']))
  })

  it.each([...RIG_PHASES])('%s keeps the agent orb’s drawing, motion and ink', (phase) => {
    const look = rigLook(phase)
    expect(look.family).toBe(phaseFamily(phase))
    expect(look.drawing).toBe(ORB_LOOKS[phase].drawing)
    expect(look.moves).toBe(ORB_LOOKS[phase].moves)
    expect(look.ink).toBe(ORB_LOOKS[phase].ink)
  })

  it('a badge’s mark tells the states apart without its colour', () => {
    const marks: Partial<Record<RigPhase, string | null>> = {}
    for (const phase of RIG_PHASES) marks[phase] = rigLook(phase).badge
    expect(marks).toEqual({ idle: null, thinking: 'working', tool: 'working', approval: 'approval', input: 'input', error: 'error', done: 'done' })
  })

  it('the wash is for alerts only, in the alert’s tone', () => {
    const washes = RIG_PHASES.filter((p) => poseOf(p).wash > 0).map((p) => [p, rigLook(p).tone])
    expect(washes).toEqual([['approval', 'warn'], ['input', 'info'], ['error', 'danger']])
  })
})

describe('the poses differ in density, arrangement and place', () => {
  const differs = (a: RigPhase, b: RigPhase) => POSE_KEYS.some((k) => poseOf(a)[k] !== poseOf(b)[k])

  it.each([0, 1])('at expressiveness %s every family’s pose is its own', (e) => {
    runtime.expressiveness = e
    const quiet = poseOf('idle')
    expect(quiet.density, 'quiet’s dots are sparse').toBeLessThan(poseOf('thinking').density)
    expect(poseOf('approval').spread, 'an approval draws the dots in tight').toBeLessThan(1)
    expect(poseOf('input').lift, 'a question lifts the orb').toBeLessThan(0)
    expect(poseOf('error').sink, 'an error sinks the dots toward the lower edge').toBeGreaterThan(0)
    expect(poseOf('done').swirl, 'done swirls the dots once').toBeGreaterThan(0)
    for (const [a, b] of [['idle', 'thinking'], ['thinking', 'approval'], ['approval', 'input'], ['input', 'error'], ['error', 'done']] as const) {
      expect(differs(a, b), `${a} and ${b} share a pose`).toBe(true)
    }
  })

  it.each([
    ['approval', 'spread'], ['input', 'lift'], ['error', 'sink'], ['done', 'swirl'],
  ] as [RigPhase, PoseKey][])('expressiveness scales %s’s %s, and keeps some of it at the floor', (phase, key) => {
    const rest = poseOf('thinking')[key]
    runtime.expressiveness = 0
    const refined = Math.abs(poseOf(phase)[key] - rest)
    runtime.expressiveness = 1
    const bold = Math.abs(poseOf(phase)[key] - rest)
    expect(refined).toBeGreaterThan(0)
    expect(bold).toBeGreaterThan(refined * 2)
  })
})

describe('every move is a preset, so the dials and reduced motion reach it', () => {
  it('the wash fades on the effects curve and never springs', () => {
    for (const phase of RIG_PHASES) expect(poseTransition(phase, 'wash')).toMatchObject({ duration: 0.2 })
  })

  it.each([
    ['approval', 'spread'], ['input', 'lift'], ['done', 'swirl'],
  ] as [RigPhase, PoseKey][])('%s’s %s overshoots at bounciness 1 and not at 0', (phase, key) => {
    runtime.bounciness = 1
    expect(overshootOf(poseTransition(phase, key))).toBeGreaterThan(0.05)
    runtime.bounciness = 0
    expect(overshootOf(poseTransition(phase, key))).toBeLessThan(0.01)
  })

  it('the error droops on smooth, which does not visibly overshoot at either end of the dial', () => {
    for (const b of [0, 1]) {
      runtime.bounciness = b
      expect(overshootOf(poseTransition('error', 'sink'))).toBeLessThan(0.01)
    }
  })

  it('the focused card opens with a slight overshoot and closes on the effects curve', () => {
    runtime.bounciness = 1
    expect(overshootOf(cardTransition(true))).toBeGreaterThan(0.05)
    expect(cardTransition(false)).toMatchObject({ duration: 0.2 })
  })

  it('under reduced motion every change is instant', () => {
    setReducedMotion(true)
    for (const phase of RIG_PHASES) {
      for (const key of POSE_KEYS) expect(poseTransition(phase, key), `${phase} ${key}`).toEqual({ type: 'tween', duration: 0 })
    }
    expect(cardTransition(true)).toEqual({ type: 'tween', duration: 0 })
    expect(cardTransition(false)).toEqual({ type: 'tween', duration: 0 })
  })
})

describe('the loop the measurement plays', () => {
  it('goes into each of the four families and opens and closes the focused card', () => {
    const families = new Set(RIG_LOOP.flatMap((s) => ('phase' in s ? [rigLook(s.phase).family] : [])))
    expect(families).toEqual(new Set(['quiet', 'active', 'waiting', 'blocked']))
    expect(RIG_LOOP.filter((s) => 'card' in s).map((s) => 'card' in s && s.card)).toEqual([true, false])
  })
})

describe('the buddy’s sizes', () => {
  it('each shares one of the agent orb’s two tuned drawings', () => {
    expect(BUDDY_SIZES.map((s) => [s, tunedDrawingFor(s)])).toEqual([[18, 20], [24, 20], [48, 64], [96, 64]])
  })
})
