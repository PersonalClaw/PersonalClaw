/**
 * The composer's halo never meets an edge — it fades out before every edge of its stage.
 *
 * Owner report (2026-09-25): on an ongoing chat "the shadow suddenly gets cut off and I can see a
 * hard edge where it should be a smooth halo". Measured on a fresh home at the shipped `full`
 * width, 1440×900: the bloom reaches `HALO_REACH` (180px) past the composer, but a docked
 * composer sits one page gutter (16px) from the stage's bottom and sides, and the layer's own
 * `overflow-hidden` cut the halo there at ~94% of its strength — red 47–49 against a canvas of 15
 * at the page bottom, and on a light canvas a pink field that stopped on a vertical line at the
 * rail ((240,244,248) → (239,223,223) in one pixel).
 *
 * The fix is geometric, so the rail is too: `haloFades` spends exactly the room between the light
 * and each edge (capped at the reach) on a fade to nothing, the frame loop writes it onto the
 * layer every time the composer moves, and the layer's mask reads it. The pixel half of the claim
 * (the edge rows really are canvas-coloured) lives in `e2e/chatHalo.spec.ts`, because jsdom paints
 * nothing.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'
import { createRef } from 'react'
import { DotGlow, HALO_FADE_VARS, HALO_MASK, HALO_REACH, haloFades, type GlowRect } from './DotGlow'

vi.mock('../app/appearance', () => ({
  useAppearance: () => ({ selectValue: () => 'waves' }),
}))

/** A composer box, in the stage's own coordinates (the stage is the layer at 0,0). */
const rect = (left: number, top: number, width: number, height: number): GlowRect =>
  ({ cx: left + width / 2, cy: top + height / 2, halfW: width / 2, halfH: height / 2 })

describe('haloFades — the fade each edge gets is the room there is, never more than the reach', () => {
  // The measured geometry: a 1244×844 chat column, the composer 1212×156 in a 16px gutter.
  const W = 1244, H = 844

  it('a DOCKED composer fades within its 16px gutter on three sides and falls off naturally above', () => {
    expect(haloFades(rect(16, H - 16 - 156, 1212, 156), W, H)).toEqual({ l: 16, r: 16, t: HALO_REACH, b: 16 })
  })

  it('the new-chat HERO keeps its full falloff above and below — only the sides are short', () => {
    expect(haloFades(rect(16, 349, 1212, 156), W, H)).toEqual({ l: 16, r: 16, t: HALO_REACH, b: HALO_REACH })
  })

  it('a composer that has grown past the stage edge fades over nothing there (no negative width)', () => {
    expect(haloFades(rect(-6, 400, 1256, 156), W, H).l).toBe(0)
  })

  it('with no source measured yet, every edge fades by the full reach, so the fallback field is contained too', () => {
    expect(haloFades(null, W, H)).toEqual({ l: HALO_REACH, r: HALO_REACH, t: HALO_REACH, b: HALO_REACH })
  })
})

describe('the layer mask', () => {
  it('fades BOTH axes to transparent at both ends, each over its own edge’s fade width', () => {
    const [x, y] = HALO_MASK.split(/,\s*(?=linear-gradient)/)
    expect(x).toMatch(/^linear-gradient\(to right, rgb\(0 0 0 \/ 0\) 0,/)
    expect(y).toMatch(/^linear-gradient\(to bottom, rgb\(0 0 0 \/ 0\) 0,/)
    for (const axis of [x, y]) expect(axis).toMatch(/rgb\(0 0 0 \/ 0\) 100%\)$/)
    expect(x).toContain(`var(${HALO_FADE_VARS.l}, 0px)`)
    expect(x).toContain(`var(${HALO_FADE_VARS.r}, 0px)`)
    expect(y).toContain(`var(${HALO_FADE_VARS.t}, 0px)`)
    expect(y).toContain(`var(${HALO_FADE_VARS.b}, 0px)`)
  })
})

// ── The frame loop writes the fades, and re-writes them when the composer moves ────────────────

let frames: FrameRequestCallback[] = []
const ORIGINAL = {
  raf: window.requestAnimationFrame,
  caf: window.cancelAnimationFrame,
  getContext: HTMLCanvasElement.prototype.getContext,
}
const STAGE = { w: 1244, h: 844 }

beforeEach(() => {
  frames = []
  Object.defineProperty(window, 'requestAnimationFrame', { configurable: true, writable: true, value: (cb: FrameRequestCallback) => frames.push(cb) })
  Object.defineProperty(window, 'cancelAnimationFrame', { configurable: true, writable: true, value: () => {} })
  // jsdom has no canvas: a context whose every call is a no-op is enough, this file measures the layer.
  HTMLCanvasElement.prototype.getContext = (() => new Proxy({}, { get: () => () => {} })) as never
  for (const prop of ['clientWidth', 'clientHeight'] as const) {
    Object.defineProperty(HTMLDivElement.prototype, prop, { configurable: true, value: prop === 'clientWidth' ? STAGE.w : STAGE.h })
  }
})

afterEach(() => {
  Object.defineProperty(window, 'requestAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.raf })
  Object.defineProperty(window, 'cancelAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.caf })
  HTMLCanvasElement.prototype.getContext = ORIGINAL.getContext
  for (const prop of ['clientWidth', 'clientHeight'] as const) delete (HTMLDivElement.prototype as unknown as Record<string, unknown>)[prop]
})

/** A composer element whose box is whatever `box` currently says (the canvas sits at 0,0). */
function composerAt(box: { left: number; top: number; width: number; height: number }) {
  const el = document.createElement('div')
  el.getBoundingClientRect = () => ({ ...box, right: box.left + box.width, bottom: box.top + box.height, x: box.left, y: box.top, toJSON: () => ({}) }) as DOMRect
  return el
}

function runFrame(t = 16) {
  const next = frames.shift()
  expect(next, 'the loop scheduled no frame').toBeDefined()
  next!(t)
}

describe('DotGlow writes the fades onto its own layer, from the live composer rect', () => {
  it('docked, then moved: the fades follow the composer, frame by frame', () => {
    const box = { left: 16, top: STAGE.h - 16 - 156, width: 1212, height: 156 }
    const ref = createRef<HTMLElement>() as React.MutableRefObject<HTMLElement | null>
    ref.current = composerAt(box)
    const { container } = render(<DotGlow composerRef={ref} />)
    const layer = container.querySelector<HTMLElement>('[data-dot-glow]')!
    runFrame()
    const read = () => Object.fromEntries((['l', 'r', 't', 'b'] as const).map((k) => [k, layer.style.getPropertyValue(HALO_FADE_VARS[k])]))
    expect(read(), 'a docked composer: 16px of room on three sides').toEqual({ l: '16px', r: '16px', t: `${HALO_REACH}px`, b: '16px' })

    // The composer rises into the hero position (the reverse of the hero → dock flight).
    box.top = 349
    runFrame(32)
    expect(read(), 'the fades did not follow the composer when it moved').toEqual({ l: '16px', r: '16px', t: `${HALO_REACH}px`, b: `${HALO_REACH}px` })
  })
})
