import { afterEach, describe, expect, it } from 'vitest'
import { ORB_STATES, ORB_STATE_OF, type OrbStateName } from '../../lib/orbState'
import { duration } from '../../design/motion'
import { runtime } from '../../design/runtime'
import { FASTEST_REPEAT_S, MOVES, STROKES, formAt, mix, mixToward, periodsOf, restForm, transitionOf, workingLap, type Form } from './clawRing'
import { paintRing } from './paintRing'

// ── The claw ring: the orb's one form and its five signatures (#6117) ──────────────────────────
//
// The orb is six curved strokes drawn like the brand mark's two, around a soft centre. Each state is one set of
// stroke values; thinking and working move, the others hold still; the form morphs from one state
// to the next and never jumps; and done arrives with one success bloom, which a failure never plays.

/** How far apart two angles are, the short way round. */
function turn(a: number, b: number): number {
  const d = Math.abs(a - b) % (Math.PI * 2)
  return Math.min(d, Math.PI * 2 - d)
}

/** The largest change of any stroke value between two forms; angles by how far they turn. */
function gap(a: Form, b: Form): number {
  let most = turn(a.spin, b.spin)
  a.strokes.forEach((s, i) => {
    const t = b.strokes[i]
    most = Math.max(most, turn(s.angle, t.angle))
    for (const key of ['radius', 'span', 'hook', 'bulge', 'width'] as const) most = Math.max(most, Math.abs(s[key] - t[key]))
  })
  return most
}

describe('one form of six strokes', () => {
  it.each(ORB_STATES)('%s is six strokes', (state) => {
    expect(restForm(state).strokes).toHaveLength(STROKES)
    expect(STROKES).toBe(6)
  })

  it('every state has a form of its own: no two rest forms are the same', () => {
    for (const a of ORB_STATES) for (const b of ORB_STATES) if (a !== b) {
      expect(gap(restForm(a), restForm(b)), `${a} and ${b}`).toBeGreaterThan(0.05)
    }
  })
})

describe('each state has its own motion', () => {
  it('thinking and working move; ready, waiting and done hold still', () => {
    expect(MOVES).toEqual({ ready: false, thinking: true, working: true, waiting: false, done: false })
  })

  it.each<OrbStateName>(['ready', 'waiting', 'done'])('%s is the same form at every instant', (state) => {
    for (const t of [0, 0.37, 1.2, 5, 61]) expect(gap(formAt(state, t), restForm(state))).toBe(0)
  })

  it('thinking swells its strokes outward and back, each at its own phase', () => {
    const a = formAt('thinking', 0)
    const b = formAt('thinking', duration.breath / 4)
    expect(gap(a, b)).toBeGreaterThan(0.02)
    // Irregular: at one instant the strokes are not all at one radius.
    const radii = a.strokes.map((s) => s.radius)
    expect(Math.max(...radii) - Math.min(...radii)).toBeGreaterThan(0.02)
    // Back where it began after one breath.
    expect(gap(formAt('thinking', 0), formAt('thinking', duration.breath))).toBeLessThan(1e-9)
  })

  it('working turns two rings in opposite directions', () => {
    const a = formAt('working', 0)
    const b = formAt('working', 0.1)
    const turned = a.strokes.map((s, i) => b.strokes[i].angle - s.angle)
    const inner = turned.filter((_, i) => i % 2 === 0)
    const outer = turned.filter((_, i) => i % 2 === 1)
    expect(inner.every((d) => d > 0)).toBe(true)
    expect(outer.every((d) => d < 0)).toBe(true)
    // Two rings: the inner strokes sit nearer the centre than the outer ones.
    expect(Math.max(...a.strokes.filter((_, i) => i % 2 === 0).map((s) => s.radius)))
      .toBeLessThan(Math.min(...a.strokes.filter((_, i) => i % 2 === 1).map((s) => s.radius)))
  })

  it.each([0.4, 1.4, 4])('at a Loading length of %s s nothing looks the same again sooner than a third of a second', (loading) => {
    runtime.durationLoading = loading
    const lap = workingLap()
    expect(lap).toBe(Math.max(loading, 1))
    const periods = periodsOf()
    expect(periods.thinking).toEqual([duration.breath])
    expect(periods.working).toEqual([lap / 3, (2 * lap) / 3])
    for (const list of Object.values(periods)) for (const p of list) expect(p).toBeGreaterThanOrEqual(FASTEST_REPEAT_S)
    // The list is the forms' own: the inner ring is its own picture again after a third of the lap,
    // and the outer after two thirds, and neither sooner.
    const ring = (f: Form, at: 0 | 1) => f.strokes.filter((_, i) => i % 2 === at)
    const same = (a: Form, b: Form, at: 0 | 1) => ring(a, at).every((s) => ring(b, at).some((t) => turn(s.angle, t.angle) < 1e-9 && Math.abs(s.radius - t.radius) < 1e-9))
    expect(same(formAt('working', 0), formAt('working', lap / 3), 0), 'the inner ring is not its own picture after a third of a lap').toBe(true)
    for (const share of [0.1, 0.2, 0.3]) expect(same(formAt('working', 0), formAt('working', lap * share), 0), `the inner ring is its own picture at ${share} of a lap`).toBe(false)
    expect(same(formAt('working', 0), formAt('working', (2 * lap) / 3), 1), 'the outer ring is not its own picture after two thirds of a lap').toBe(true)
    for (const share of [0.2, 1 / 3, 0.5]) expect(same(formAt('working', 0), formAt('working', lap * share), 1), `the outer ring is its own picture at ${share} of a lap`).toBe(false)
  })
})

afterEach(() => { runtime.durationLoading = 1.4 })

describe('it morphs from one state to the next, and never jumps', () => {
  it.each(ORB_STATES.flatMap((a) => ORB_STATES.filter((b) => b !== a).map((b) => [a, b] as const)))(
    '%s to %s starts at the one and ends at the other, in small steps',
    (from, to) => {
      const a = restForm(from)
      const b = restForm(to)
      expect(gap(mix(a, b, 0), a)).toBeLessThan(1e-9)
      expect(gap(mix(a, b, 1), b)).toBeLessThan(1e-9)
      const whole = gap(a, b)
      let prev = mix(a, b, 0)
      for (let k = 0.02; k <= 1.0001; k += 0.02) {
        const next = mix(a, b, k)
        expect(gap(prev, next)).toBeLessThanOrEqual(whole * 0.02 + 1e-9)
        prev = next
      }
    },
  )

  /** The largest frame-to-frame turn of any stroke as a frozen form is mixed toward working's moving
   *  rings, on a smooth-spring k, against the most the continuous path turns between those frames. */
  function worstStray(mixer: (from: Form, target: Form, kept: Form, k: number) => Form): number {
    const k = (n: number) => 1 - (1 + n / 4) * Math.exp(-n / 4)
    let worst = 0
    for (let start = 0; start < 1400; start++) {
      const t0 = start * 0.0137
      const from = mix(restForm('waiting'), formAt('thinking', t0 * 1.7), (start % 7) / 7)
      const kept = formAt('working', t0)
      let prev = mixer(from, kept, kept, k(0))
      for (let n = 1; n <= 30; n++) {
        const target = formAt('working', t0 + n / 30)
        const next = mixer(from, target, kept, k(n))
        // The continuous path from + k(n) × (the way round + the target's turn since the start): its
        // step is the change of k times at most half a turn plus that turn, and k times this frame's.
        const turned = (2 * Math.PI * (n / 30)) / workingLap()
        const pathStep = Math.abs(k(n) - k(n - 1)) * (Math.PI + turned) + k(n) * ((2 * Math.PI) / (30 * workingLap()))
        next.strokes.forEach((s, i) => { worst = Math.max(worst, turn(s.angle, prev.strokes[i].angle) - pathStep) })
        prev = next
      }
    }
    return worst
  }

  it('a morph into working never flips a stroke the other way round, at any start', () => {
    expect(worstStray(mixToward), 'a stroke strayed from the continuous path').toBeLessThan(1e-6)
    // The control: choosing the way round afresh at every frame, as a plain mix does, flips strokes.
    expect(worstStray((from, target, _kept, k) => mix(from, target, k)), 'the check cannot see a flip').toBeGreaterThan(0.3)
  })

  it('an angle turns the short way round', () => {
    const a: Form = { spin: 0, strokes: restForm('ready').strokes.map((s) => ({ ...s, angle: 3.1 })) }
    const b: Form = { spin: 0, strokes: restForm('ready').strokes.map((s) => ({ ...s, angle: -3.1 })) }
    expect(Math.abs(mix(a, b, 0.5).strokes[0].angle)).toBeGreaterThan(3.1)
  })
})

describe('done blooms once; a failure closes to done with no bloom', () => {
  it('arriving at done from a live state plays the bloom', () => {
    const plan = transitionOf(ORB_STATE_OF.tool, ORB_STATE_OF.done, { reduced: false })
    expect(plan).toEqual({ instant: false, morph: 'smooth', bloom: true })
  })

  it('arriving at a failure closes to the same form and plays no bloom', () => {
    const plan = transitionOf(ORB_STATE_OF.tool, ORB_STATE_OF.error, { reduced: false })
    expect(plan).toEqual({ instant: false, morph: 'smooth', bloom: false })
    expect(restForm(ORB_STATE_OF.error.state)).toEqual(restForm('done'))
  })

  it('done to done blooms no second time', () => {
    expect(transitionOf(ORB_STATE_OF.done, ORB_STATE_OF.done, { reduced: false }).bloom).toBe(false)
  })

  it('under reduced motion every change is an instant swap, with no bloom', () => {
    for (const from of Object.values(ORB_STATE_OF)) for (const to of Object.values(ORB_STATE_OF)) {
      expect(transitionOf(from, to, { reduced: true })).toEqual({ instant: true, morph: null, bloom: false })
    }
  })
})

describe('the painter draws nothing with no ink', () => {
  it('a token that resolves to nothing paints no stroke, in no fallback colour', () => {
    const log: (string | number | boolean | undefined)[][] = []
    const ctx = new Proxy({} as Record<string, unknown>, {
      get: (target, key: string) => (key in target ? target[key] : (...args: unknown[]) => { log.push([key, ...(args as (string | number)[])]) }),
      set: (target, key: string, value) => { target[key] = value; log.push(['=', key, value]); return true },
    }) as unknown as CanvasRenderingContext2D
    paintRing(ctx, restForm('ready'), { size: 20, dpr: 1, ink: undefined, scale: 1 })
    expect(log.filter((e) => e[0] === 'stroke'), 'a stroke was drawn').toEqual([])
    expect(log.filter((e) => e[0] === '=' && e[1] === 'strokeStyle'), 'an ink was set').toEqual([])
    // The control: with an ink, it draws its six strokes.
    paintRing(ctx, restForm('ready'), { size: 20, dpr: 1, ink: { r: 1, g: 2, b: 3 }, scale: 1 })
    expect(log.filter((e) => e[0] === 'stroke')).toHaveLength(6)
  })
})
