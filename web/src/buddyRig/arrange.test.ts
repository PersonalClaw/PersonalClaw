/**
 * How a pose moves the agent orb's dots (#3887): the parameters both rig candidates add to the
 * drawing, applied to the frame the agent orb's own engine draws.
 *
 * The orb never squashes, stretches or turns into another shape (`docs/design/buddy/character.md`):
 * its dots gather and spread, crowd toward the lower edge, swirl, and the whole orb lifts or sinks.
 * So each parameter is checked against the frame it started from, and the identity arrangement is
 * checked to leave the agent orb's drawing exactly as its engine drew it.
 */
import { describe, expect, it } from 'vitest'
import { MODE_FRAMES, resolvePreset, type OrbFrame } from 'thinking-orbs'
import { IDENTITY, arrangeFrame, poseFrame } from './arrange'

const SIZE = 64
const C = SIZE / 2

function frameOf(t = 0.6): OrbFrame {
  const { mode, opts } = resolvePreset('breathing', 64)
  return MODE_FRAMES[mode](SIZE, t, opts)
}

const dist = (x: number, y: number) => Math.hypot(x - C, y - C)

describe('the identity arrangement is the agent orb’s own drawing', () => {
  it('moves no dot', () => {
    const base = frameOf()
    expect(arrangeFrame(base, SIZE, IDENTITY)).toEqual(base)
  })

  it('a pose frame with nothing to add draws what the engine draws', () => {
    const { mode, opts } = resolvePreset('breathing', 64)
    const frame = poseFrame(mode, () => IDENTITY, { still: false, lift: true })
    expect(frame(SIZE, 1.3, opts)).toEqual(MODE_FRAMES[mode](SIZE, 1.3, opts))
  })
})

describe('each parameter moves the dots the way its pose says', () => {
  it('spread draws every dot toward the centre by the same factor', () => {
    const base = frameOf()
    const tight = arrangeFrame(base, SIZE, { ...IDENTITY, spread: 0.5 })
    tight.dots.forEach((d, i) => {
      expect(dist(d.x, d.y)).toBeCloseTo(dist(base.dots[i].x, base.dots[i].y) * 0.5, 6)
      expect(d.r).toBe(base.dots[i].r)
    })
  })

  it('sink crowds the dots toward the lower edge, the upper ones the most', () => {
    const base = frameOf()
    const sunk = arrangeFrame(base, SIZE, { ...IDENTITY, sink: 1 })
    const moved = sunk.dots.map((d, i) => d.y - base.dots[i].y)
    expect(Math.min(...moved)).toBeGreaterThanOrEqual(0)
    const top = base.dots.reduce((a, d, i) => (d.y < base.dots[a].y ? i : a), 0)
    const bottom = base.dots.reduce((a, d, i) => (d.y > base.dots[a].y ? i : a), 0)
    expect(moved[top]).toBeGreaterThan(moved[bottom])
  })

  it('lift moves the whole orb, and only when the candidate draws its moves in the frame', () => {
    const base = frameOf()
    const lifted = arrangeFrame(base, SIZE, { ...IDENTITY, lift: -0.1 })
    lifted.dots.forEach((d, i) => expect(d.y).toBeCloseTo(base.dots[i].y - 6.4, 6))
    const { mode, opts } = resolvePreset('breathing', 64)
    const layered = poseFrame(mode, () => ({ ...IDENTITY, lift: -0.1 }), { still: true, lift: false })
    expect(layered(SIZE, 0, opts)).toEqual(MODE_FRAMES[mode](SIZE, 0.6, opts))
  })

  it('swirl turns the dots about the centre, a quarter at 0.25', () => {
    const base = frameOf()
    const turned = arrangeFrame(base, SIZE, { ...IDENTITY, swirl: 0.25 })
    turned.dots.forEach((d, i) => {
      const b = base.dots[i]
      expect(d.x - C).toBeCloseTo(-(b.y - C), 6)
      expect(d.y - C).toBeCloseTo(b.x - C, 6)
    })
  })

  it('density thins the dots through the engine’s own count scaler', () => {
    const { mode, opts } = resolvePreset('breathing', 64)
    const full = poseFrame(mode, () => IDENTITY, { still: true, lift: true })(SIZE, 0, opts)
    const sparse = poseFrame(mode, () => ({ ...IDENTITY, density: 0.45 }), { still: true, lift: true })(SIZE, 0, opts)
    expect(sparse.dots.length).toBeLessThan(full.dots.length * 0.7)
    expect(sparse.dots.length).toBeGreaterThan(0)
  })
})

describe('a still pose is one instant', () => {
  it('draws the same frame whatever the clock says', () => {
    const { mode, opts } = resolvePreset('working', 64)
    const still = poseFrame(mode, () => IDENTITY, { still: true, lift: true })
    expect(still(SIZE, 0, opts)).toEqual(still(SIZE, 42, opts))
  })

  it('and a moving one does not', () => {
    const { mode, opts } = resolvePreset('working', 64)
    const moving = poseFrame(mode, () => IDENTITY, { still: false, lift: true })
    expect(moving(SIZE, 0, opts)).not.toEqual(moving(SIZE, 0.5, opts))
  })
})
