/**
 * The agent orb schedules an animation frame only while it shows live work, on screen, with motion
 * allowed.
 *
 * Each half is paired with the control that makes it mean something, as `DotGlow.reducedMotion`'s
 * are:
 *
 *  - MOTION ALLOWED, an active phase: frames ARE scheduled, and running one schedules the next. A
 *    spy that never saw a frame would make every "no frame" below vacuous.
 *  - REDUCED MOTION, a hidden tab, and every still phase (approval, input, error, done): no frame is
 *    scheduled at all, AND the orb is still painted once. An orb that drew nothing would satisfy
 *    "no frame" for free.
 *
 * The reduced-motion probe is `design/motion`'s call-time `prefersReducedMotion`, so the stub is
 * swapped between cases honestly.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { AgentOrb } from './AgentOrb'
import { WORK_PHASES, phaseFamily } from '../lib/workPhase'

const RECORDED = ['setTransform', 'clearRect', 'beginPath', 'arc', 'fill', 'moveTo', 'lineTo', 'stroke'] as const

function fakeContext() {
  const calls: Record<string, number> = Object.fromEntries(RECORDED.map((k) => [k, 0]))
  const tally = (k: string) => () => { calls[k] += 1 }
  const ctx = Object.fromEntries(RECORDED.map((k) => [k, tally(k)]))
  return { calls, ctx: { ...ctx, fillStyle: '', strokeStyle: '', lineWidth: 1 } as unknown as CanvasRenderingContext2D }
}

let frames: FrameRequestCallback[] = []
let instrument = fakeContext()
let visibility: DocumentVisibilityState = 'visible'
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
  visibility = 'visible'
  Object.defineProperty(window, 'requestAnimationFrame', {
    configurable: true, writable: true, value: (cb: FrameRequestCallback) => frames.push(cb),
  })
  Object.defineProperty(window, 'cancelAnimationFrame', { configurable: true, writable: true, value: () => {} })
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility })
  HTMLCanvasElement.prototype.getContext = (() => instrument.ctx) as never
  setReducedMotion(false)
})

afterEach(() => {
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL.matchMedia })
  Object.defineProperty(window, 'requestAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.raf })
  Object.defineProperty(window, 'cancelAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.caf })
  HTMLCanvasElement.prototype.getContext = ORIGINAL.getContext
  Reflect.deleteProperty(document, 'visibilityState')
})

const ACTIVE = WORK_PHASES.filter((p) => phaseFamily(p) === 'active')
const STILL = WORK_PHASES.filter((p) => phaseFamily(p) !== 'active')

describe('with motion allowed, an active phase animates: the positive control', () => {
  it.each(ACTIVE)('%s schedules a frame, and each frame the next', (phase) => {
    const { container } = render(<AgentOrb phase={phase} />)
    expect((container.querySelector('[data-agent-orb]') as HTMLElement).dataset.agentOrb).toBe('animated')
    expect(frames.length, 'no frame was scheduled for live work').toBe(1)
    frames.shift()!(16)
    expect(frames.length, 'the loop did not go on').toBe(1)
    expect(instrument.calls.arc, 'no dot was drawn').toBeGreaterThan(0)
  })
})

describe('under reduced motion, every phase is one static frame', () => {
  beforeEach(() => setReducedMotion(true))

  it.each([...WORK_PHASES])('%s paints once and schedules no frame', (phase) => {
    const { container } = render(<AgentOrb phase={phase} />)
    expect((container.querySelector('[data-agent-orb]') as HTMLElement).dataset.agentOrb).toBe(phaseFamily(phase) === 'active' ? 'instant' : 'still')
    expect(frames.length, 'a frame was scheduled under reduced motion').toBe(0)
    expect(instrument.calls.arc, 'the still frame was never painted').toBeGreaterThan(0)
  })
})

describe('in a hidden tab, nothing is scheduled', () => {
  it.each(ACTIVE)('%s paints once and waits', (phase) => {
    visibility = 'hidden'
    render(<AgentOrb phase={phase} />)
    expect(frames.length, 'a hidden tab scheduled a frame').toBe(0)
    expect(instrument.calls.arc).toBeGreaterThan(0)
  })
})

describe('a turn that needs her, or has ended, is a still frame', () => {
  it.each(STILL)('%s schedules no frame with motion allowed, and is painted', (phase) => {
    const { container } = render(<AgentOrb phase={phase} />)
    expect((container.querySelector('[data-agent-orb]') as HTMLElement).dataset.agentOrb).toBe('still')
    expect(frames.length, `${phase} scheduled a frame`).toBe(0)
    expect(instrument.calls.arc, `${phase} was never painted`).toBeGreaterThan(0)
  })

  it('a running orb that turns to approval stops asking for frames', () => {
    const { rerender } = render(<AgentOrb phase="tool" />)
    expect(frames.length).toBe(1)
    rerender(<AgentOrb phase="approval" />)
    frames.splice(0).forEach((f) => f(32))
    expect(frames.length, 'the approval kept the loop alive').toBe(0)
  })
})
