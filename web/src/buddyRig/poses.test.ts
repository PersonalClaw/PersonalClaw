/**
 * The buddy's poses (`docs/design/buddy/character.md`, "The poses"): the four families on the
 * app's presets, with the dials doing what `docs/design/buddy/motion.md` says they do.
 *
 *  - each family's pose is its own, and expressiveness scales how far the orb lifts, sags, draws in
 *    and turns, keeping some of each at its floor;
 *  - every move is one of the presets, chosen by the buddy's transitions table
 *    (`ui/buddy/orbPoses.ts`), the wash on the effects curve, and reduced motion makes every change
 *    instant.
 *
 * What a pose does to the strokes is `ui/buddy/buddyRing.test.ts`'s.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { runtime } from '../design/runtime'
import { transitionOf } from '../ui/buddy/orbPoses'
import { POSE_KEYS, RIG_PHASES, poseOf, type PoseKey, type RigPhase } from './poses'
import { WORK_PHASES } from '../lib/workPhase'
import { ORB_STATE_OF } from '../lib/orbState'
import { MOVES } from '../ui/orb/clawRing'
import { movesOf } from './poseSet'

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

describe('the poses differ in thinness, arrangement and place', () => {
  const differs = (a: RigPhase, b: RigPhase) => POSE_KEYS.some((k) => poseOf(a)[k] !== poseOf(b)[k])

  it.each([0, 1])('at expressiveness %s every family’s pose is its own', (e) => {
    runtime.expressiveness = e
    const quiet = poseOf('idle')
    expect(quiet.density, 'quiet’s strokes are thin').toBeLessThan(poseOf('thinking').density)
    expect(poseOf('approval').spread, 'an approval draws the strokes in tight').toBeLessThan(1)
    expect(poseOf('input').lift, 'a question lifts the orb').toBeLessThan(0)
    expect(poseOf('error').sink, 'an error sags the strokes and settles the orb low').toBeGreaterThan(0)
    expect(poseOf('done').swirl, 'done turns the ring once').toBeGreaterThan(0)
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
    for (const phase of RIG_PHASES) expect(transitionOf(phase, 'wash')).toMatchObject({ duration: 0.2 })
  })

  it('under reduced motion every change is instant', () => {
    setReducedMotion(true)
    for (const phase of RIG_PHASES) {
      for (const key of POSE_KEYS) expect(transitionOf(phase, key), `${phase} ${key}`).toEqual({ type: 'tween', duration: 0 })
    }
  })
})

describe('the buddy moves where the agent orb moves', () => {
  it.each([...WORK_PHASES])('%s’s strokes move exactly where the agent orb’s state for it moves', (phase) => {
    expect(movesOf(phase)).toBe(MOVES[ORB_STATE_OF[phase].state])
  })
})
