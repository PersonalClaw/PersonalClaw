/**
 * The two rig candidates (#3887) hold the buddy's cost rules before anything is measured.
 *
 * `docs/design/buddy/motion.md`: frames run only while something moves, a hold with nothing moving
 * draws nothing, and under reduced motion every change is instant. Each half is paired with the
 * control that makes it mean something, as the agent orb's own frame tests are:
 *
 *  - an active pose, and a change between two still poses with motion allowed, DO ask for frames,
 *    so a spy that never saw one cannot make the "no frame" checks pass for free;
 *  - every still pose, and every pose and change under reduced motion, asks for none and is still
 *    painted once, so an orb that drew nothing cannot pass either.
 *
 * Both paint their dots at most at the buddy's 30 frames a second, asking for no frame inside a
 * gap, and every frame the display draws only when told to; the agent orb in chat draws through the
 * same loop at the same rate.
 *
 * Then the two things the spike's record states for both: the ink follows the scheme without a
 * remount, a moving orb's as soon as the change lands rather than at its next capped paint, and the
 * badge's mark (not only its colour) says which waiting or failed state it is.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, waitFor } from '@testing-library/react'
import type { ComponentType } from 'react'
import { CandidateInPlace } from './CandidateInPlace'
import { CandidateLayered } from './CandidateLayered'
import { BUDDY_FPS, RIG_PHASES, rigLook, type BuddySize, type RigPhase } from './poses'

type Candidate = ComponentType<{ phase: RigPhase; size: BuddySize; still?: boolean; fps?: number | null }>
const CANDIDATES: [string, Candidate][] = [['in place', CandidateInPlace], ['layered', CandidateLayered]]

/** A 2D context that only counts what is drawn: any method call is tallied by name. */
function fakeContext() {
  const calls: Record<string, number> = {}
  const ctx = new Proxy({ fillStyle: '', strokeStyle: '', lineWidth: 1, globalCompositeOperation: 'source-over' } as Record<string, unknown>, {
    get(target, key: string) {
      if (key in target) return target[key]
      return () => { calls[key] = (calls[key] ?? 0) + 1 }
    },
    set(target, key: string, value) { target[key] = value; return true },
  })
  return { calls, ctx: ctx as unknown as CanvasRenderingContext2D }
}

let frames: FrameRequestCallback[] = []
let instrument = fakeContext()
const ORIGINAL = {
  matchMedia: window.matchMedia,
  raf: window.requestAnimationFrame,
  caf: window.cancelAnimationFrame,
  getContext: HTMLCanvasElement.prototype.getContext,
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

beforeEach(() => {
  frames = []
  instrument = fakeContext()
  Object.defineProperty(window, 'requestAnimationFrame', {
    configurable: true, writable: true, value: (cb: FrameRequestCallback) => frames.push(cb),
  })
  Object.defineProperty(window, 'cancelAnimationFrame', { configurable: true, writable: true, value: () => {} })
  HTMLCanvasElement.prototype.getContext = (() => instrument.ctx) as never
  setReducedMotion(false)
})

afterEach(() => {
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL.matchMedia })
  Object.defineProperty(window, 'requestAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.raf })
  Object.defineProperty(window, 'cancelAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.caf })
  HTMLCanvasElement.prototype.getContext = ORIGINAL.getContext
  for (const name of ['--color-orb-alive', '--color-orb-rest', '--color-warn']) document.documentElement.style.removeProperty(name)
})

const ACTIVE = RIG_PHASES.filter((p) => rigLook(p).moves)
const STILL = RIG_PHASES.filter((p) => !rigLook(p).moves)
const rigOf = (container: HTMLElement) => container.querySelector('[data-rig-candidate]') as HTMLElement
const painted = () => instrument.calls.arc ?? 0
/** One gap of the buddy's capped rate, and a millisecond over. */
const GAP_MS = Math.ceil(1000 / BUDDY_FPS) + 1

describe.each(CANDIDATES)('the %s candidate', (_name, Candidate) => {
  describe('with motion allowed', () => {
    beforeEach(() => { vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] }) })
    afterEach(() => { vi.useRealTimers() })

    it.each(ACTIVE)('%s animates: a frame is asked for after each gap, and each frame waits out the next', (phase) => {
      render(<Candidate phase={phase} size={48} />)
      vi.advanceTimersByTime(GAP_MS)
      expect(frames.length, 'no frame was asked for live work').toBeGreaterThan(0)
      const before = frames.length
      frames.shift()!(16)
      vi.advanceTimersByTime(GAP_MS)
      expect(frames.length, 'the loop did not go on').toBeGreaterThanOrEqual(before)
      expect(painted(), 'no dot was drawn').toBeGreaterThan(0)
    })

    it(`caps its dots at the buddy’s ${BUDDY_FPS} frames a second, asking for no frame inside a gap`, () => {
      render(<Candidate phase="tool" size={48} />)
      expect(frames.length, 'a capped orb asked for a frame before its gap was out').toBe(0)
      vi.advanceTimersByTime(GAP_MS)
      expect(frames.length, 'the gap ran out and no frame was asked for').toBe(1)
      frames.shift()!(GAP_MS)
      expect(instrument.calls.clearRect, 'one clear a paint: at mount, then the frame after the gap').toBe(2)
      expect(frames.length, 'it asked for the next frame at once instead of waiting').toBe(0)
      vi.advanceTimersByTime(GAP_MS)
      expect(frames.length, 'the loop stopped while capped').toBe(1)
    })

    it('paints every frame the display draws only when nothing caps it', () => {
      render(<Candidate phase="tool" size={48} fps={null} />)
      for (const t of [8.3, 16.7, 25, 33.4]) frames.shift()!(t)
      expect(instrument.calls.clearRect).toBe(5)
    })

    it.each(STILL)('%s holds: no frame, and painted once', (phase) => {
      const { container } = render(<Candidate phase={phase} size={48} />)
      vi.advanceTimersByTime(GAP_MS)
      expect(frames.length, `${phase} asked for a frame`).toBe(0)
      expect(painted(), `${phase} was never painted`).toBeGreaterThan(0)
      expect(rigOf(container).dataset.rigMoving).toBe('false')
    })

    it('a change between two still poses asks for frames while the pose moves', () => {
      const { container, rerender } = render(<Candidate phase="idle" size={48} />)
      expect(frames.length).toBe(0)
      rerender(<Candidate phase="approval" size={48} />)
      expect(rigOf(container).dataset.rigMoving).toBe('true')
      vi.advanceTimersByTime(GAP_MS)
      expect(frames.length, 'the pose moved without a frame').toBeGreaterThan(0)
    })

    it('scrolled out of view, an active pose asks for no frame', () => {
      const observed: IntersectionObserverCallback[] = []
      const ORIGINAL_IO = globalThis.IntersectionObserver
      globalThis.IntersectionObserver = class {
        constructor(cb: IntersectionObserverCallback) { observed.push(cb) }
        observe(el: Element) { for (const cb of observed) cb([{ isIntersecting: false, target: el } as IntersectionObserverEntry], this as unknown as IntersectionObserver) }
        disconnect() {}
        unobserve() {}
        takeRecords() { return [] }
      } as unknown as typeof IntersectionObserver
      try {
        const { container } = render(<Candidate phase="tool" size={48} />)
        vi.advanceTimersByTime(GAP_MS)
        frames.splice(0).forEach((f) => f(16))
        vi.advanceTimersByTime(GAP_MS)
        expect(frames.length, 'an orb out of view kept drawing').toBe(0)
        expect(rigOf(container).dataset.rigMoving).toBe('false')
        expect(painted(), 'it was never painted').toBeGreaterThan(0)
      } finally {
        globalThis.IntersectionObserver = ORIGINAL_IO
      }
    })

    it('a still frame for the contact sheet holds even an active pose', () => {
      render(<Candidate phase="tool" size={48} still />)
      vi.advanceTimersByTime(GAP_MS)
      expect(frames.length).toBe(0)
      expect(painted()).toBeGreaterThan(0)
    })
  })

  describe('under reduced motion', () => {
    beforeEach(() => setReducedMotion(true))

    it.each([...RIG_PHASES])('%s is one painted still', (phase) => {
      const { container } = render(<Candidate phase={phase} size={48} />)
      expect(frames.length, `${phase} asked for a frame`).toBe(0)
      expect(painted()).toBeGreaterThan(0)
      expect(rigOf(container).dataset.rigMoving).toBe('false')
    })

    it('every change between poses is instant: no frame at any step', () => {
      const { container, rerender } = render(<Candidate phase="idle" size={48} />)
      for (const phase of [...RIG_PHASES, 'idle' as const]) {
        rerender(<Candidate phase={phase} size={48} />)
        expect(frames.length, `the change to ${phase} asked for a frame`).toBe(0)
        expect(rigOf(container).dataset.rigMoving).toBe('false')
      }
    })
  })

  it('retints from the scheme without a remount', async () => {
    document.documentElement.style.setProperty('--color-orb-alive', '#3366cc')
    const { container } = render(<Candidate phase="approval" size={48} />)
    const canvas = container.querySelector('canvas')
    expect(rigOf(container).dataset.rigInk).toBe('#3366cc')
    const before = painted()
    document.documentElement.style.setProperty('--color-orb-alive', '#228844')
    await waitFor(() => expect(rigOf(container).dataset.rigInk).toBe('#228844'))
    expect(container.querySelector('canvas'), 'the drawing was remounted').toBe(canvas)
    expect(painted(), 'the new ink was never painted').toBeGreaterThan(before)
  })

  it('repaints a moving orb as the scheme change lands, not at its next capped paint', async () => {
    document.documentElement.style.setProperty('--color-orb-alive', '#3366cc')
    const { container } = render(<Candidate phase="tool" size={48} />)
    const before = instrument.calls.clearRect
    // The change reaches the page as a mutation, in a microtask. Inside act, React holds every render
    // the change causes until the scope ends, so what is painted by then was painted by the change.
    let atOnce = 0
    let inkThen: string | undefined
    await act(async () => {
      document.documentElement.style.setProperty('--color-orb-alive', '#228844')
      await Promise.resolve()
      await Promise.resolve()
      atOnce = instrument.calls.clearRect - before
      inkThen = rigOf(container).dataset.rigInk
    })
    expect(inkThen, 'a render came first, so this says nothing about the change itself').toBe('#3366cc')
    expect(atOnce, 'the new ink waited for a render or the next paint').toBeGreaterThan(0)
    expect(frames.length, 'it waited for a frame').toBe(0)
    expect(rigOf(container).dataset.rigInk).toBe('#228844')
  })

  it.each([
    ['idle', null], ['thinking', 'working'], ['approval', 'approval'], ['input', 'input'], ['error', 'error'], ['done', 'done'],
  ] as [RigPhase, string | null][])('%s carries the badge %s', (phase, badge) => {
    const { container } = render(<Candidate phase={phase} size={48} />)
    expect(rigOf(container).dataset.rigBadge ?? null).toBe(badge)
  })

  it('the badge and the wash take the alert’s tone', () => {
    document.documentElement.style.setProperty('--color-warn', '#cc8800')
    const { container } = render(<Candidate phase="approval" size={48} />)
    expect(rigOf(container).dataset.rigTone).toBe('warn')
    expect(rigOf(container).dataset.rigWash).toBe('warn')
  })
})

describe('where the two differ', () => {
  it.each([18, 24, 48, 96] as BuddySize[])('layered draws its dots at %s px itself, from the tuned drawing that size shares, rather than scaling a tuned canvas', (size) => {
    const { container } = render(<CandidateLayered phase="tool" size={size} />)
    const canvas = container.querySelector('canvas') as HTMLCanvasElement
    expect(canvas.width, 'the canvas is a tuned size scaled to fit').toBe(size)
    expect(canvas.style.width).toBe(`${size}px`)
  })

  it('in place draws the badge and the wash with the dots: one canvas and nothing else', () => {
    document.documentElement.style.setProperty('--color-warn', '#cc8800')
    const { container } = render(<CandidateInPlace phase="approval" size={48} />)
    expect(container.querySelectorAll('canvas')).toHaveLength(1)
    expect(container.querySelector('svg')).toBeNull()
    expect(rigOf(container).dataset.rigToneInk, 'the badge’s dots are painted in the tone the token resolves to').toBe('#cc8800')
  })

  it('layered draws them in an SVG layer whose colours are the tokens, so they retint with the scheme for free', () => {
    const { container } = render(<CandidateLayered phase="approval" size={48} />)
    const badge = container.querySelector('[data-rig-badge-layer]') as SVGElement
    expect(badge.textContent).toContain('!')
    expect(badge.getAttribute('style') ?? '').toContain('var(--color-warn)')
    const wash = container.querySelector('[data-rig-wash-layer] stop') as SVGElement
    expect(wash.getAttribute('stop-color') ?? wash.getAttribute('style') ?? '').toContain('var(--color-warn)')
  })
})
