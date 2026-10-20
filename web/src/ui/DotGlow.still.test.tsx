/**
 * Retro Terminal's still field: painted once, with no halo, and repainted only when what it is
 * lit from changes.
 *
 * `web/DESIGN.md` §7: `still` is one frozen frame, and the identity that brings it keeps the
 * default look's rules over it — no halo (the bloom around the composer and the focus aura), and
 * nothing that answers the caret. So at rest it asks for no frame at all.
 *
 * But a field painted once is still lit from the composer, so a frame that never moves would
 * leave its light where the composer used to be: after the new chat's composer flies down to the
 * dock, as it grows with a long draft, or when the hero shifts to make room for its chips. Light
 * with no surface under it reads as a stain behind the transcript (§7's Contained-Light Rule). So
 * the field repaints when its inputs change — the stage, the composer's box, the glow colours (a
 * light/dark or scheme switch) — follows a moving composer frame by frame until it holds still,
 * and then stops again.
 *
 * Paints are counted by `clearRect`, which every paint opens with; a check that finds nothing
 * changed draws nothing.
 */

import { act, render } from '@testing-library/react'
import { createRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DotGlow } from './DotGlow'
import { runtime } from '../design/runtime'

let style = 'still'
vi.mock('../app/appearance', () => ({
  useAppearance: () => ({ selectValue: () => style }),
}))

let frames: FrameRequestCallback[] = []
let paints = 0
let draws = 0
const ORIGINAL = {
  raf: window.requestAnimationFrame,
  caf: window.cancelAnimationFrame,
  getContext: HTMLCanvasElement.prototype.getContext,
  matchMedia: window.matchMedia,
  glowA: runtime.glowA,
}

function context(): CanvasRenderingContext2D {
  return new Proxy({}, {
    get: (_t, name) => (..._args: unknown[]) => {
      if (name === 'clearRect') paints += 1
      if (name === 'fill' || name === 'fillRect') draws += 1
    },
    set: () => true,
  }) as unknown as CanvasRenderingContext2D
}

function setReducedMotion(on: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: (query: string) => ({
      matches: on && query.includes('prefers-reduced-motion'), media: query, onchange: null,
      addListener: () => {}, removeListener: () => {}, addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
    }),
  })
}

beforeEach(() => {
  style = 'still'
  frames = []
  paints = 0
  draws = 0
  Object.defineProperty(window, 'requestAnimationFrame', { configurable: true, writable: true, value: (cb: FrameRequestCallback) => frames.push(cb) })
  Object.defineProperty(window, 'cancelAnimationFrame', { configurable: true, writable: true, value: () => {} })
  HTMLCanvasElement.prototype.getContext = (() => context()) as never
  for (const prop of ['clientWidth', 'clientHeight'] as const) {
    Object.defineProperty(HTMLDivElement.prototype, prop, { configurable: true, value: prop === 'clientWidth' ? 800 : 600 })
  }
  setReducedMotion(false)
})

afterEach(() => {
  Object.defineProperty(window, 'requestAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.raf })
  Object.defineProperty(window, 'cancelAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.caf })
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL.matchMedia })
  HTMLCanvasElement.prototype.getContext = ORIGINAL.getContext
  for (const prop of ['clientWidth', 'clientHeight'] as const) delete (HTMLDivElement.prototype as unknown as Record<string, unknown>)[prop]
  runtime.glowA = ORIGINAL.glowA
})

/** A composer whose box is whatever `box` says now. */
function composerAt(box: { left: number; top: number; width: number; height: number }) {
  const el = document.createElement('div')
  el.getBoundingClientRect = () => ({ ...box, right: box.left + box.width, bottom: box.top + box.height, x: box.left, y: box.top, toJSON: () => ({}) }) as DOMRect
  const ref = createRef<HTMLElement>() as React.MutableRefObject<HTMLElement | null>
  ref.current = el
  return ref
}

/** Run the frames pending now, once each. */
function tick(t = 16) {
  const due = frames
  frames = []
  for (const cb of due) cb(t)
}

/** Run frames until none is pending (bounded, so a loop that never ends fails instead of hanging). */
function drain(limit = 50): number {
  let n = 0
  while (frames.length) {
    if (++n > limit) throw new Error(`still asking for frames after ${limit}: the field never came to rest`)
    tick(16 * n)
  }
  return n
}

const docked = () => ({ left: 16, top: 420, width: 768, height: 140 })

describe('at rest', () => {
  it('paints once on mount, then asks for nothing', () => {
    render(<DotGlow composerRef={composerAt(docked())} />)
    expect(frames, 'the field was never painted').toHaveLength(1)
    tick()
    expect(paints).toBe(1)
    expect(draws, 'the still field drew no dot').toBeGreaterThan(0)
    expect(frames, 'a still field kept asking for frames').toHaveLength(0)
  })

  it('a re-render that changes nothing repaints nothing', () => {
    const ref = composerAt(docked())
    const { rerender } = render(<DotGlow composerRef={ref} />)
    tick()
    rerender(<DotGlow composerRef={ref} />)
    drain()
    expect(paints, 'an unchanged field was repainted').toBe(1)
  })
})

describe('no halo', () => {
  it('the bloom around the composer stays dark', () => {
    const { container } = render(<DotGlow composerRef={composerAt(docked())} />)
    tick()
    const [bloom] = Array.from(container.querySelectorAll<HTMLElement>('[data-dot-glow] > div'))
    expect(bloom.style.opacity).toBe('0')
  })

  it('positive control: the same mount under the wave field lights it', () => {
    style = 'waves'
    const { container } = render(<DotGlow composerRef={composerAt(docked())} />)
    tick()
    const [bloom] = Array.from(container.querySelectorAll<HTMLElement>('[data-dot-glow] > div'))
    expect(bloom.style.opacity).toBe('1')
  })

  it('the caret gathers no aura, and the field does not brighten for it', () => {
    const ref = composerAt(docked())
    const { container, rerender } = render(<DotGlow composerRef={ref} />)
    tick()
    rerender(<DotGlow composerRef={ref} focused />)
    drain()
    expect(container.querySelector<HTMLElement>('[data-dot-glow-focus]')!.style.opacity).toBe('0')
    expect(paints, 'the still field repainted for the caret').toBe(1)
  })

  it('positive control: under the wave field the caret does gather the aura', () => {
    style = 'waves'
    const { container } = render(<DotGlow composerRef={composerAt(docked())} focused />)
    expect(container.querySelector<HTMLElement>('[data-dot-glow-focus]')!.style.opacity).toBe('1')
  })
})

describe('repainted when what it is lit from changes', () => {
  it('a new glow colour (a light/dark or scheme switch) repaints it once', () => {
    const ref = composerAt(docked())
    const { rerender } = render(<DotGlow composerRef={ref} />)
    tick()
    // The store writes the canvas's colours in its own effect, which runs after this one, then
    // re-renders its consumers; the field reads the colour when its check runs, a frame later.
    runtime.glowA = [61, 220, 116]
    rerender(<DotGlow composerRef={ref} />)
    drain()
    expect(paints, 'the frozen field kept the old colour').toBe(2)
    expect(frames).toHaveLength(0)
  })

  it('a composer that moves is followed frame by frame until it holds still, then the field stops', () => {
    const box = { left: 16, top: 230, width: 768, height: 140 }
    const ref = composerAt(box)
    const { rerender } = render(<DotGlow composerRef={ref} />)
    tick()
    // The first send: the composer flies from the hero down to the dock over several frames.
    rerender(<DotGlow composerRef={ref} />)
    for (const top of [260, 320, 380, 420]) { box.top = top; tick() }
    expect(paints, 'the light did not follow the composer down').toBe(5)
    expect(drain(), 'the field went on checking long after the composer stopped').toBeLessThanOrEqual(2)
    expect(paints, 'a composer at rest was repainted').toBe(5)
  })

  it('a layout shift in its stage that moves the composer, with no render of its own, repaints it', async () => {
    const box = { left: 16, top: 230, width: 768, height: 140 }
    const ref = composerAt(box)
    const { container } = render(<div data-stage><DotGlow composerRef={ref} /></div>)
    tick()
    // The hero's chips arrive and the centred composer moves up to make room.
    await act(async () => {
      container.querySelector('[data-stage]')!.appendChild(document.createElement('div'))
      box.top = 200
    })
    drain()
    expect(paints, 'the light stayed where the composer was before the chips arrived').toBe(2)
  })

  it('a neighbour that only animates in place does not make it look again, the composer itself does', async () => {
    // The new chat's claw mark spins forever beside the composer: a style write every frame on an
    // element that neither holds the composer nor sits inside it, which cannot move it.
    const box = { left: 16, top: 230, width: 768, height: 140 }
    const ref = composerAt(box)
    const { container } = render(<div data-stage><span data-mark /><DotGlow composerRef={ref} /></div>)
    const stageEl = container.querySelector('[data-stage]')!
    await act(async () => { stageEl.appendChild(ref.current!) })
    drain()
    const settled = paints
    frames = []
    await act(async () => { container.querySelector<HTMLElement>('[data-mark]')!.style.transform = 'rotate(30deg)' })
    expect(frames, 'the field looked again for a mark spinning in place').toHaveLength(0)
    // Positive control: a style write on the composer (its flight to the dock) is looked at.
    await act(async () => { ref.current!.style.transform = 'translateY(40px)'; box.top = 270 })
    expect(frames.length, 'the composer moved and the field never looked').toBeGreaterThan(0)
    drain()
    expect(paints).toBe(settled + 1)
  })

  it('its own repaint is not mistaken for a layout shift', async () => {
    const ref = composerAt(docked())
    render(<div data-stage><DotGlow composerRef={ref} /></div>)
    tick()
    await act(async () => {})
    drain()
    expect(paints).toBe(1)
  })
})

describe('under reduced motion', () => {
  it('still one frame on mount, and none after it', () => {
    setReducedMotion(true)
    render(<DotGlow composerRef={composerAt(docked())} />)
    expect(frames).toHaveLength(1)
    tick()
    expect(frames).toHaveLength(0)
    expect(paints).toBe(1)
  })
})
