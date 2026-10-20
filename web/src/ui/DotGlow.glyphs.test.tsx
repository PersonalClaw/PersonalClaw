/**
 * The backdrop's dot glyphs: what the Settings picker offers is exactly what the canvas can draw,
 * and the default glyph is PersonalClaw's own mark, drawn where and as large as it says.
 *
 * jsdom paints nothing, so the instrument is a context that records every call. That is enough to
 * pin a glyph's geometry: which outline is filled, and the transform it is filled through.
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { CLAW_GLYPH_REACH, DOT_GLYPHS } from './DotGlow'
import { CLAW_MARK_BOX, CLAW_MARK_PATH } from './ClawMark'
import { SelectControl } from './TokenControls'
import { TOKENS, type SelectToken } from '../design/tokenRegistry'
import type { DotShape } from '../design/runtime'

// The picker reads and writes through the appearance store; only what it renders matters here.
vi.mock('../app/appearance', () => ({
  useAppearance: () => ({ selectValue: () => 'claw', setSelect: () => {}, resetToken: () => {} }),
}))

const DOT_SHAPE = TOKENS.find((t): t is SelectToken => t.kind === 'select' && t.varName === '--dot-shape')!
const SHAPES = Object.keys(DOT_GLYPHS) as DotShape[]

interface Call { name: string; args: unknown[] }

/** A 2D context that records every method call, in order, and draws nothing. */
function recorder() {
  const calls: Call[] = []
  const ctx = new Proxy({}, {
    get: (_target, name) => (...args: unknown[]) => { calls.push({ name: String(name), args }) },
  }) as unknown as CanvasRenderingContext2D
  return { calls, ctx }
}

/** The transform in force at the first fill, replayed from the recorded translate/scale calls
 *  (the claw is upright, so these two are all it may use). Returns the map from mark units to
 *  canvas px. */
function transformAtFill(calls: Call[]) {
  let a = 1, d = 1, e = 0, f = 0
  for (const c of calls) {
    if (c.name === 'fill') break
    const [p, q] = c.args as number[]
    if (c.name === 'translate') { e += a * p; f += d * q }
    if (c.name === 'scale') { a *= p; d *= q }
  }
  return (u: number, v: number) => ({ x: a * u + e, y: d * v + f })
}

/** Where the claw glyph puts the mark's box, for a dot of radius r at (x, y). */
function clawBox(x: number, y: number, r: number) {
  const { calls, ctx } = recorder()
  DOT_GLYPHS.claw(ctx, x, y, r)
  const map = transformAtFill(calls)
  const tl = map(CLAW_MARK_BOX.x, CLAW_MARK_BOX.y)
  const br = map(CLAW_MARK_BOX.x + CLAW_MARK_BOX.width, CLAW_MARK_BOX.y + CLAW_MARK_BOX.height)
  return { calls, left: tl.x, top: tl.y, right: br.x, bottom: br.y }
}

describe('the Dot shape picker offers exactly the shapes the backdrop draws', () => {
  it('its options are the glyph table, one pill each', () => {
    expect(new Set(DOT_SHAPE.options).size, 'a shape is offered twice').toBe(DOT_SHAPE.options.length)
    expect([...DOT_SHAPE.options].sort()).toEqual([...SHAPES].sort())
  })

  it('and the rendered picker shows those names, with the default chosen', () => {
    render(<SelectControl token={DOT_SHAPE} />)
    const pills = screen.getAllByRole('button', { name: /^Dot shape: / })
    expect(pills.map((b) => b.getAttribute('aria-label')!.slice('Dot shape: '.length)).sort()).toEqual([...SHAPES].sort())
    expect(screen.getByRole('button', { name: 'Dot shape: claw' }).getAttribute('aria-pressed')).toBe('true')
  })
})

describe('the claw glyph is PersonalClaw’s own mark, centred on its dot', () => {
  it('fills the mark’s own outline, once, upright, and restores the context', () => {
    const { calls } = clawBox(100, 60, 4)
    const fills = calls.filter((c) => c.name === 'fill')
    expect(fills).toHaveLength(1)
    expect((fills[0].args[0] as { d?: unknown }).d, 'the glyph is not the mark the logo draws').toBe(CLAW_MARK_PATH)
    expect(calls.some((c) => c.name === 'rotate'), 'the mark is drawn upright').toBe(false)
    expect(calls[0].name).toBe('save')
    expect(calls[calls.length - 1].name, 'the next dot would inherit this one’s transform').toBe('restore')
  })

  it('reaches CLAW_GLYPH_REACH radii above and below its point, and keeps the mark’s proportions', () => {
    const r = 4, reach = CLAW_GLYPH_REACH * r
    const box = clawBox(100, 60, r)
    expect(box.top).toBeCloseTo(60 - reach, 9)
    expect(box.bottom).toBeCloseTo(60 + reach, 9)
    const halfWidth = reach * (CLAW_MARK_BOX.width / CLAW_MARK_BOX.height)
    expect(box.left).toBeCloseTo(100 - halfWidth, 9)
    expect(box.right).toBeCloseTo(100 + halfWidth, 9)
  })

  it('scales with the dot: twice the radius, twice the reach, same centre', () => {
    const small = clawBox(30, 40, 2), large = clawBox(30, 40, 4)
    expect(large.bottom - large.top).toBeCloseTo(2 * (small.bottom - small.top), 9)
    expect((large.left + large.right) / 2).toBeCloseTo(30, 9)
    expect((large.top + large.bottom) / 2).toBeCloseTo(40, 9)
  })

  it('is drawn past the dot’s own radius, because the mark is mostly air', () => {
    // Two slim talons fill well under half the mark's box; at a reach of 1 the default field
    // would read as the dimmest of every glyph on offer.
    expect(CLAW_GLYPH_REACH).toBeGreaterThan(1)
  })

  it('builds its outline once, not once per dot', () => {
    const Base = globalThis.Path2D
    let built = 0
    globalThis.Path2D = class extends Base {
      constructor(d?: string | Path2D) { super(d); built += 1 }
    }
    try {
      const { ctx } = recorder()
      for (let i = 0; i < 50; i++) DOT_GLYPHS.claw(ctx, i, i, 2)
      expect(built).toBeLessThanOrEqual(1)
    } finally {
      globalThis.Path2D = Base
    }
  })
})

describe('every glyph on offer paints, and leaves the context as it found it', () => {
  it.each(SHAPES)('%s', (shape) => {
    const { calls, ctx } = recorder()
    DOT_GLYPHS[shape](ctx, 50, 50, 3)
    const painted = calls.filter((c) => c.name === 'fill' || c.name === 'fillRect').length
    expect(painted, `${shape} drew nothing`).toBeGreaterThan(0)
    const count = (name: string) => calls.filter((c) => c.name === name).length
    expect(count('restore'), `${shape} left a save open`).toBe(count('save'))
  })
})
