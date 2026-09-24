/**
 * PERSONALITY-THEMES §S2 (PT-5) — the claw-arcade SPARKLE field is static under
 * `prefers-reduced-motion`.
 *
 * claw-arcade is the first identity that turns the halftone backdrop into a moving
 * decoration a user did not individually opt into: activating it presets
 * `--dot-shape: sparkle` and drives `--expressiveness` to 1. DotGlow has always
 * honoured reduced motion, but nothing asserted it — so the contract this proof now
 * leans on was a comment. This file makes it a rail.
 *
 * The two halves are paired, because each one alone is satisfiable by a bug:
 *
 *  - REDUCED MOTION: exactly ONE frame is scheduled, and running it schedules no
 *    successor. A frozen CSS animation would still cost a compositor layer and still
 *    read as stuck; this is a single painted frame and then nothing.
 *  - AND IT IS PAINTED, with the sparkle glyph. Without this, a DotGlow that drew
 *    nothing at all — a null context, an early return, a deleted loop — would satisfy
 *    "no second frame" perfectly.
 *
 * Both cases live in one file on purpose. DotGlow reads `window.matchMedia` inside its
 * effect, call-time, so a stub swapped between renders is honest here — and the
 * motion-ALLOWED case running second is what proves that: if the probe were ever
 * cached in a module singleton (framer-motion's `useReducedMotion` is, and a sibling
 * atom in this plan lost time to exactly that), the second describe would go red
 * rather than quietly measuring the first case twice.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'
import { DotGlow } from './DotGlow'
import { runtime } from '../design/runtime'
import { PERSONALITIES } from '../design/personalities'

// DotGlow resolves the backdrop mode from the appearance store; 'waves' is the
// default and the only mode with a live loop to suppress. Colour application is
// PT-1's concern and has nothing to do with this contract.
vi.mock('../app/appearance', () => ({
  useAppearance: () => ({ selectValue: () => 'waves' }),
}))

/** The 2D context is the instrument. jsdom implements no canvas, so this records the
 *  drawing calls — which is also how the sparkle's own path is identified: it is the
 *  only glyph in `drawDot` built from quadratic curves. */
const RECORDED = [
  'setTransform', 'clearRect', 'beginPath', 'closePath', 'fill', 'moveTo', 'lineTo',
  'arc', 'quadraticCurveTo', 'fillRect', 'save', 'restore', 'translate', 'rotate',
] as const

function fakeContext() {
  // Pre-seeded at 0 rather than filled lazily, so "never called" fails as
  // `expected 0 to be greater than 0` instead of a TypeError on `undefined`.
  const calls: Record<string, number> = Object.fromEntries(RECORDED.map((k) => [k, 0]))
  const tally = (k: string) => () => { calls[k] += 1 }
  return {
    calls,
    ctx: {
      setTransform: tally('setTransform'), clearRect: tally('clearRect'),
      beginPath: tally('beginPath'), closePath: tally('closePath'), fill: tally('fill'),
      moveTo: tally('moveTo'), lineTo: tally('lineTo'), arc: tally('arc'),
      quadraticCurveTo: tally('quadraticCurveTo'), fillRect: tally('fillRect'),
      save: tally('save'), restore: tally('restore'), translate: tally('translate'),
      rotate: tally('rotate'), fillStyle: '',
    } as unknown as CanvasRenderingContext2D,
  }
}

let frames: FrameRequestCallback[] = []
const ORIGINAL = {
  matchMedia: window.matchMedia,
  raf: window.requestAnimationFrame,
  caf: window.cancelAnimationFrame,
  getContext: HTMLCanvasElement.prototype.getContext,
  dotShape: runtime.dotShape,
}

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

/** The dot glyph claw-arcade actually presets — read from the registry, so this stays
 *  the arcade's contract rather than a hardcoded shape that could drift away from it. */
const ARCADE_SHAPE = PERSONALITIES.find((p) => p.id === 'claw-arcade')!.behavior.dials!.dotShape!

let instrument = fakeContext()

beforeEach(() => {
  frames = []
  instrument = fakeContext()
  Object.defineProperty(window, 'requestAnimationFrame', {
    configurable: true, writable: true,
    value: (cb: FrameRequestCallback) => frames.push(cb),
  })
  Object.defineProperty(window, 'cancelAnimationFrame', {
    configurable: true, writable: true, value: () => {},
  })
  HTMLCanvasElement.prototype.getContext = (() => instrument.ctx) as never
  // A real viewport, so the projection maths runs on numbers instead of 0/0.
  for (const prop of ['clientWidth', 'clientHeight'] as const) {
    Object.defineProperty(HTMLDivElement.prototype, prop, {
      configurable: true, value: prop === 'clientWidth' ? 800 : 600,
    })
  }
  runtime.dotShape = ARCADE_SHAPE
})

afterEach(() => {
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL.matchMedia })
  Object.defineProperty(window, 'requestAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.raf })
  Object.defineProperty(window, 'cancelAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.caf })
  HTMLCanvasElement.prototype.getContext = ORIGINAL.getContext
  runtime.dotShape = ORIGINAL.dotShape
})

/** Render, then run whatever frames are queued (one pass), returning how many the
 *  loop re-scheduled for itself. */
function paintOnce(): number {
  render(<DotGlow />)
  expect(frames.length, 'the loop never scheduled its first frame').toBe(1)
  const first = frames.shift()!
  first(16)
  return frames.length
}

describe('the arcade sparkle field is a single static frame under reduced motion', () => {
  beforeEach(() => setReducedMotion(true))

  it('paints once and schedules no successor', () => {
    expect(paintOnce(), 'the loop kept running under reduced motion').toBe(0)
  })

  it('but it DOES paint, in the sparkle glyph — the floor under the claim above', () => {
    paintOnce()
    expect(instrument.calls.clearRect, 'nothing was drawn at all').toBeGreaterThan(0)
    expect(instrument.calls.fill, 'no dot was filled').toBeGreaterThan(0)
    // `quadraticCurveTo` is unique to the sparkle path in `drawDot`, so this pins the
    // glyph rather than merely "some field was drawn".
    expect(ARCADE_SHAPE).toBe('sparkle')
    expect(instrument.calls.quadraticCurveTo, 'the sparkle glyph never ran').toBeGreaterThan(0)
  })
})

describe('and animates when reduced motion is NOT requested', () => {
  beforeEach(() => setReducedMotion(false))

  it('keeps re-scheduling itself', () => {
    // The other direction, and the reason the file's absence claim means anything: if
    // DotGlow simply never looped, the reduced-motion test would pass for free.
    expect(paintOnce(), 'the loop stopped after one frame with motion allowed').toBe(1)
  })
})
