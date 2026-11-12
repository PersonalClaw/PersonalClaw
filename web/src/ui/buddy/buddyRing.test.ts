import { describe, expect, it } from 'vitest'
import { ORB_STATES } from '../../lib/orbState'
import { restForm } from '../orb/clawRing'
import { MARK_STROKES, SOLID_MARKS, badgeAt, markStrokes, posedForm, strokeOutline, strokePoints, washArcs, type Mark } from './buddyRing'

// ── The buddy's orb on the claw ring: what a pose does to the strokes (#6117) ─────────────────────
//
// A pose moves the agent orb's form for the state and never replaces it: quiet's strokes are drawn
// thinner, an approval draws them in, an error sags them, done's turns once, and nothing squashes or
// stretches the form. The badge's marks and the alert's wash are strokes too.

const IDLE = { density: 1, spread: 1, sink: 0, swirl: 0 }

describe('a pose moves the form, and at rest is the form itself', () => {
  it.each(ORB_STATES)('%s at rest is the agent orb’s form exactly', (state) => {
    const form = restForm(state)
    expect(posedForm(form, IDLE)).toBe(form)
    // A whole turn lands where it began: done's flourish ends upright.
    expect(posedForm(form, { ...IDLE, swirl: 1 })).toBe(form)
  })

  it('quiet draws the strokes thinner, and only thinner', () => {
    const form = restForm('ready')
    const quiet = posedForm(form, { ...IDLE, density: 0.45 })
    quiet.strokes.forEach((s, i) => {
      expect(s.width).toBeCloseTo(form.strokes[i].width * 0.7525, 6)
      expect({ ...s, width: 0 }).toEqual({ ...form.strokes[i], width: 0 })
    })
  })

  it('an approval draws the strokes in toward the centre', () => {
    const form = restForm('waiting')
    const held = posedForm(form, { ...IDLE, spread: 0.74 })
    held.strokes.forEach((s, i) => expect(s.radius).toBeCloseTo(form.strokes[i].radius * 0.74, 9))
  })

  it('an error sags the strokes: their bows flatten and their hooks bend in', () => {
    const form = restForm('done')
    const droop = posedForm(form, { ...IDLE, sink: 0.7 })
    droop.strokes.forEach((s, i) => {
      expect(s.bulge).toBeLessThan(form.strokes[i].bulge)
      expect(s.hook).toBeGreaterThan(form.strokes[i].hook)
      expect(s.radius, 'the sag keeps each stroke where it sits').toBe(form.strokes[i].radius)
    })
  })

  it('done’s flourish turns the whole ring, and turns nothing else', () => {
    const form = restForm('done')
    const half = posedForm(form, { ...IDLE, swirl: 0.5 })
    expect(half.spin).toBeCloseTo(form.spin + Math.PI, 9)
    half.strokes.forEach((s, i) => expect(s).toEqual(form.strokes[i]))
  })
})

describe('the badge and the wash are strokes', () => {
  it.each(Object.keys(MARK_STROKES) as Mark[])('%s is drawn in strokes inside the badge', (mark) => {
    for (const size of [18, 48]) {
      const { cx, cy, radius } = badgeAt(size)
      const strokes = markStrokes(size, mark, 0, size >= 24)
      expect(strokes.length, mark).toBeGreaterThan(0)
      for (const s of strokes) {
        expect(s.width).toBeGreaterThan(0)
        for (const [x, y] of s.pts) expect(Math.hypot(x - cx, y - cy), `${mark} at ${size} px leaves its badge`).toBeLessThan(radius)
      }
    }
  })

  it('the working mark is three points; the ! and ? end in one', () => {
    expect(MARK_STROKES.working.map((s) => s.pts.length)).toEqual([1, 1, 1])
    expect(MARK_STROKES.approval.at(-1)!.pts).toHaveLength(1)
    expect(MARK_STROKES.input.at(-1)!.pts).toHaveLength(1)
    expect([...SOLID_MARKS].sort()).toEqual(['approval', 'input'])
  })

  it('a solid badge’s cut is the stroke’s own outline: it reaches the stroke’s half-width and no further', () => {
    for (const mark of SOLID_MARKS) {
      for (const s of markStrokes(18, mark, 0, false)) {
        const along = strokePoints(s.pts)
        const outline = strokeOutline(along, s.width / 2)
        expect(outline.length).toBeGreaterThanOrEqual(along.length === 1 ? 16 : along.length * 2)
        for (const [x, y] of outline) {
          const near = Math.min(...along.map(([px, py]) => Math.hypot(x - px, y - py)))
          expect(near, mark).toBeLessThan(s.width / 2 + 0.2)
        }
      }
    }
  })

  it('the microphone is drawn from 22 px up; below it the mark is one plain dot', () => {
    for (const size of [16, 18, 20]) {
      const marks = markStrokes(size, 'listening', 0, false)
      expect(marks, `${size} px`).toHaveLength(1)
      expect(marks[0].pts, `${size} px`).toHaveLength(1)
      expect(marks[0].pts[0]).toEqual([badgeAt(size).cx, badgeAt(size).cy])
    }
    expect(markStrokes(22, 'listening', 0, false)).toHaveLength(MARK_STROKES.listening.length)
    expect(markStrokes(48, 'listening', 0, true)).toHaveLength(MARK_STROKES.listening.length)
  })

  it('the wash is dropped at 20 px and under, where shape and colour carry the state', () => {
    for (const size of [16, 18, 20]) expect(washArcs(size, 1), `${size} px`).toEqual([])
    expect(washArcs(22, 1)).toHaveLength(3)
  })

  it('the wash is three arcs in the lower half, fainter as they rise, and nothing without an alert', () => {
    expect(washArcs(48, 0)).toEqual([])
    const arcs = washArcs(48, 1)
    expect(arcs).toHaveLength(3)
    for (const [, from, to] of arcs) {
      expect(from).toBeGreaterThan(0)
      expect(to).toBeLessThan(Math.PI)
    }
    const alphas = arcs.map(([, , , a]) => a)
    expect([...alphas].sort((a, b) => b - a)).toEqual(alphas)
  })
})
