/**
 * One frame of the wave field sets at most `FIELD_BUCKETS` fill colours.
 *
 * The field used to build a fresh `rgba(…)` string for every dot and hand it to the canvas, which
 * parses it: about 15,000 strings a frame at density 1, sixty times a second. Each dot's light is
 * now one of `FIELD_BUCKETS` brightness steps, the dots of a step are drawn together, and the
 * canvas is given one colour per step.
 *
 * The instrument is a context that records every `fillStyle` it is given and every draw, over one
 * full frame of Claw Arcade's own field (its glyph and lattice) lit by a composer in the middle of
 * an 800×600 stage. The draw count is the floor under the colour count: a frame that drew nothing
 * would set no colour at all.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'
import { createRef } from 'react'
import { DotGlow, FIELD_BUCKETS } from './DotGlow'
import { runtime } from '../design/runtime'
import { PERSONALITIES } from '../design/personalities'

vi.mock('../app/appearance', () => ({
  useAppearance: () => ({ selectValue: () => 'waves' }),
}))

const ARCADE = PERSONALITIES.find((p) => p.id === 'claw-arcade')!.behavior.dials!

let frames: FrameRequestCallback[] = []
const fills: string[] = []
let draws = 0
const ORIGINAL = {
  raf: window.requestAnimationFrame,
  caf: window.cancelAnimationFrame,
  getContext: HTMLCanvasElement.prototype.getContext,
  dotShape: runtime.dotShape,
  dotPattern: runtime.dotPattern,
}

/** A context that keeps every fill colour it is given and counts what it draws. */
function recordingContext(): CanvasRenderingContext2D {
  const methods = new Proxy({} as Record<string, unknown>, {
    get: (_t, name) => (name === 'fillStyle' ? fills[fills.length - 1] ?? '' : (..._args: unknown[]) => {
      if (name === 'fill' || name === 'fillRect') draws += 1
    }),
    set: (_t, name, value) => {
      if (name === 'fillStyle') fills.push(String(value))
      return true
    },
  })
  return methods as unknown as CanvasRenderingContext2D
}

beforeEach(() => {
  frames = []
  fills.length = 0
  draws = 0
  Object.defineProperty(window, 'requestAnimationFrame', { configurable: true, writable: true, value: (cb: FrameRequestCallback) => frames.push(cb) })
  Object.defineProperty(window, 'cancelAnimationFrame', { configurable: true, writable: true, value: () => {} })
  HTMLCanvasElement.prototype.getContext = (() => recordingContext()) as never
  for (const prop of ['clientWidth', 'clientHeight'] as const) {
    Object.defineProperty(HTMLDivElement.prototype, prop, { configurable: true, value: prop === 'clientWidth' ? 800 : 600 })
  }
  runtime.dotShape = ARCADE.dotShape!
  runtime.dotPattern = ARCADE.dotPattern!
})

afterEach(() => {
  Object.defineProperty(window, 'requestAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.raf })
  Object.defineProperty(window, 'cancelAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.caf })
  HTMLCanvasElement.prototype.getContext = ORIGINAL.getContext
  for (const prop of ['clientWidth', 'clientHeight'] as const) delete (HTMLDivElement.prototype as unknown as Record<string, unknown>)[prop]
  runtime.dotShape = ORIGINAL.dotShape
  runtime.dotPattern = ORIGINAL.dotPattern
})

/** A composer 400×120, centred low on the stage, the way a docked one sits. */
function composer() {
  const el = document.createElement('div')
  el.getBoundingClientRect = () => ({ left: 200, top: 420, width: 400, height: 120, right: 600, bottom: 540, x: 200, y: 420, toJSON: () => ({}) }) as DOMRect
  const ref = createRef<HTMLElement>() as React.MutableRefObject<HTMLElement | null>
  ref.current = el
  return ref
}

describe('one frame of the wave field', () => {
  it(`sets at most ${FIELD_BUCKETS} fill colours, however many dots it draws`, () => {
    render(<DotGlow composerRef={composer()} />)
    expect(frames, 'the field never asked for its frame').toHaveLength(1)
    frames.shift()!(1000)
    expect(draws, 'the frame drew no dot, so the colour count below proves nothing').toBeGreaterThan(FIELD_BUCKETS)
    const distinct = new Set(fills)
    expect(distinct.size, `the frame set ${distinct.size} distinct fill colours`).toBeLessThanOrEqual(FIELD_BUCKETS)
    expect(fills.length, 'a colour was set more than once per step').toBeLessThanOrEqual(FIELD_BUCKETS)
  })

  it('is lit: every colour it sets is a translucent tint of the glow, never solid and never empty', () => {
    render(<DotGlow composerRef={composer()} />)
    frames.shift()!(1000)
    expect(fills.length).toBeGreaterThan(0)
    for (const fill of fills) {
      const m = fill.match(/^rgba\((\d+),(\d+),(\d+),([\d.]+)\)$/)
      expect(m, `"${fill}" is not an rgba() colour`).not.toBeNull()
      const alpha = Number(m![4])
      expect(alpha).toBeGreaterThan(0)
      expect(alpha).toBeLessThanOrEqual(0.95)
    }
  })
})
