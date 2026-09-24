import { describe, it, expect } from 'vitest'
import {
  EASE_TAU, KIND_SCALE, STATE_VISUAL, easeStep, interpolateScene, layoutScene,
  pickRenderTier, sceneSummary, staticScene,
} from './worldScene'
import type { AgentActivityEntity, AgentActivityState } from '../../../lib/useAgentActivity'

// ── The world's scene model ──────────────────────────────────────────────────
//
// A canvas cannot be asserted on in jsdom, so every claim the atom makes about the
// VISUAL layer that CAN be held as a function is held here: attention pulls inward,
// a state change eases instead of teleporting, and reduced motion is a static
// layout rather than a slow one. What this file deliberately does NOT prove is
// listed in the atom report: nothing here looks at a pixel.

const ent = (over: Partial<AgentActivityEntity> = {}): AgentActivityEntity => ({
  id: 'loop:a', kind: 'loop', state: 'working', title: 'A', refs: { link: '' }, ...over,
})

const dist = (n: { x: number; y: number }) => Math.hypot(n.x - 0.5, n.y - 0.5)

describe('layout: attention is closer to the centre', () => {
  it('the ring order is strictly inward by salience', () => {
    const order: AgentActivityState[] = ['needs_input', 'waiting_approval', 'error', 'working', 'idle']
    const rings = order.map((s) => STATE_VISUAL[s].ring)
    // A world whose "needs you" sat further out than "idle" would invert the one
    // thing the scene is for.
    expect(rings).toEqual([...rings].sort((a, b) => a - b))
  })

  it('a needs_input node really is placed nearer the centre than an idle one', () => {
    const [a, b] = layoutScene([ent({ id: 'n', state: 'needs_input' }), ent({ id: 'i', state: 'idle' })])
      .sort((x, y) => (x.id === 'n' ? -1 : y.id === 'n' ? 1 : 0))
    expect(dist(a)).toBeLessThan(dist(b))
  })

  it('idle nodes never breathe, working ones do', () => {
    expect(STATE_VISUAL.idle.pulse).toBe(0)
    expect(STATE_VISUAL.working.pulse).toBeGreaterThan(0)
    // A sleeping agent that throbs reads as busy — the exact lie an ambient scene
    // must not tell.
    expect(layoutScene([ent({ state: 'idle' })])[0].pulse).toBe(0)
  })

  it('node weight follows the nesting: loop > session > subagent', () => {
    expect(KIND_SCALE.loop).toBeGreaterThan(KIND_SCALE.session)
    expect(KIND_SCALE.session).toBeGreaterThan(KIND_SCALE.subagent)
  })

  it('layout is deterministic and total — one placement per entity, same twice', () => {
    const es = [ent({ id: 'a' }), ent({ id: 'b', state: 'idle' }), ent({ id: 'c', state: 'error' })]
    const one = layoutScene(es)
    // Vacuity floor: the model must actually place things.
    expect(one.length).toBe(3)
    expect(one).toEqual(layoutScene(es))
  })

  it('same-ring nodes are spread, not stacked on one point', () => {
    const many = Array.from({ length: 6 }, (_, i) => ent({ id: `l${i}`, state: 'working' }))
    const pts = layoutScene(many).map((p) => `${p.x.toFixed(4)},${p.y.toFixed(4)}`)
    expect(new Set(pts).size).toBe(6)
  })

  it('progress passes through, and absence stays absent', () => {
    expect(layoutScene([ent({ progress: 0.4 })])[0].progress).toBe(0.4)
    expect(layoutScene([ent()])[0]).not.toHaveProperty('progress')
  })
})

describe('interpolation: a state change eases, it never teleports', () => {
  it('one frame moves a node PART of the way to its new ring, not all of it', () => {
    const before = staticScene([ent({ id: 'x', state: 'idle' })])
    const target = layoutScene([ent({ id: 'x', state: 'needs_input' })])
    const after = interpolateScene(before, target, 16)[0]
    const d0 = dist(before[0])
    const d1 = dist(after)
    const dT = dist(target[0])
    // Moved inward…
    expect(d1).toBeLessThan(d0)
    // …but did NOT arrive. This is the assertion that fails if someone "simplifies"
    // the painter by assigning the target directly.
    expect(d1).toBeGreaterThan(dT)
  })

  it('and it does arrive — repeated frames converge on the target', () => {
    let live = staticScene([ent({ id: 'x', state: 'idle' })])
    const target = layoutScene([ent({ id: 'x', state: 'needs_input' })])
    for (let i = 0; i < 200; i++) live = interpolateScene(live, target, 16)
    expect(dist(live[0])).toBeCloseTo(dist(target[0]), 4)
    expect(live[0].mix).toBeCloseTo(1, 4)
  })

  it('a tone change restarts the crossfade from the OLD tone', () => {
    const before = staticScene([ent({ id: 'x', state: 'idle' })])
    const target = layoutScene([ent({ id: 'x', state: 'error' })])
    const after = interpolateScene(before, target, 16)[0]
    expect(after.fromTone).toBe(STATE_VISUAL.idle.tone)
    expect(after.tone).toBe(STATE_VISUAL.error.tone)
    expect(after.mix).toBe(0)  // fade starts at the old colour, not a hard cut
  })

  it('a new node enters from outside its orbit at zero opacity, it does not pop', () => {
    const target = layoutScene([ent({ id: 'fresh', state: 'working' })])
    const [n] = interpolateScene([], target, 16)
    expect(dist(n)).toBeGreaterThan(dist(target[0]))
    expect(n.mix).toBe(0)
    expect(n.r).toBeLessThan(target[0].r)
  })

  it('a departed node is dropped — the scene equals the target set', () => {
    const before = staticScene([ent({ id: 'a' }), ent({ id: 'b' })])
    const after = interpolateScene(before, layoutScene([ent({ id: 'a' })]), 16)
    expect(after.map((n) => n.id)).toEqual(['a'])
  })

  it('the ease is frame-rate independent: 2x60fps ≈ 1x30fps of travel', () => {
    const start = staticScene([ent({ id: 'x', state: 'idle' })])
    const target = layoutScene([ent({ id: 'x', state: 'needs_input' })])
    const fast = interpolateScene(interpolateScene(start, target, 16), target, 16)[0]
    const slow = interpolateScene(start, target, 32)[0]
    // Within a percent — the exponential is not exactly additive, but a 144Hz display
    // must not settle 5x faster than a 30Hz one.
    expect(Math.abs(dist(fast) - dist(slow))).toBeLessThan(0.01)
  })

  it('easeStep is bounded 0..1 and never overshoots', () => {
    expect(easeStep(0)).toBe(0)
    expect(easeStep(EASE_TAU)).toBeCloseTo(1 - Math.exp(-1), 6)
    expect(easeStep(100_000)).toBeLessThanOrEqual(1)
    expect(easeStep(-5)).toBe(0)
  })
})

describe('reduced motion is a STATIC layout, not a slower one', () => {
  const es = [ent({ id: 'a', state: 'working' }), ent({ id: 'b', state: 'needs_input' }), ent({ id: 'c', state: 'idle' })]

  it('every node has zero pulse AND zero speed — nothing breathes, nothing orbits', () => {
    const nodes = staticScene(es)
    expect(nodes.length).toBe(3)  // vacuity floor: there is something to be still
    for (const n of nodes) {
      expect(n.pulse, `${n.id} still breathes`).toBe(0)
      expect(n.speed, `${n.id} still orbits`).toBe(0)
    }
  })

  it('every node is already SETTLED — no ease is left to run', () => {
    for (const n of staticScene(es)) expect(n.mix).toBe(1)
  })

  it('the still layout is the SAME layout, not a degraded one', () => {
    // Reduced motion must not cost information: identical positions and tones to the
    // animated scene's resting state.
    const still = staticScene(es)
    const resting = layoutScene(es)
    expect(still.map(({ id, x, y, tone, r }) => ({ id, x, y, tone, r })))
      .toEqual(resting.map(({ id, x, y, tone, r }) => ({ id, x, y, tone, r })))
  })

  it('the animated scene, by contrast, DOES move — the positive control', () => {
    // Without this the three tests above pass for a model that animates nothing at
    // all, which is the vacuous version of the whole clause.
    const moving = layoutScene(es)
    expect(moving.some((n) => n.pulse > 0), 'nothing in the animated scene pulses').toBe(true)
    expect(moving.some((n) => n.speed > 0), 'nothing in the animated scene orbits').toBe(true)
  })
})

describe('the scene always has a text equivalent', () => {
  it('counts every state it shows, and says so plainly', () => {
    const s = sceneSummary([
      ent({ id: '1', state: 'needs_input' }), ent({ id: '2', state: 'working' }),
      ent({ id: '3', state: 'working' }), ent({ id: '4', state: 'idle' }),
    ], 0)
    expect(s).toBe('1 waiting on you, 2 working, 1 idle.')
  })

  it('reports what it had to leave out', () => {
    expect(sceneSummary([ent({ state: 'working' })], 12)).toContain('12 more not shown')
  })

  it('an empty scene says so rather than returning an empty string', () => {
    expect(sceneSummary([], 0)).toBe('Nothing is running.')
  })

  it('a state with no members is not mentioned', () => {
    expect(sceneSummary([ent({ state: 'idle' })], 0)).toBe('1 idle.')
  })
})

describe('render tier degrades honestly', () => {
  it('no drawing context at all -> static (the DOM list, never a blank box)', () => {
    expect(pickRenderTier({ getContext: () => null })).toBe('static')
    expect(pickRenderTier(null)).toBe('static')
  })

  it('a 2d context -> 2d', () => {
    expect(pickRenderTier({ getContext: (id) => (id === '2d' ? {} : null) })).toBe('2d')
  })

  it('a webgl-only browser still degrades to static, because there is no webgl path', () => {
    // Deliberate: the `webgl` tier was removed rather than shipped untested (see the
    // note in worldScene.ts). A browser that offers webgl but not 2d — which does not
    // exist in practice — must fall back, not render nothing.
    expect(pickRenderTier({ getContext: (id) => (id === 'webgl' ? {} : null) })).toBe('static')
  })

  it('a browser that THROWS from getContext degrades instead of crashing the page', () => {
    // Canvas-blocking privacy extensions throw rather than return null.
    expect(pickRenderTier({ getContext: () => { throw new Error('canvas blocked') } })).toBe('static')
  })
})
