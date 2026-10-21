/**
 * The agent orb schedules an animation frame only while it shows live work, on screen, with motion
 * allowed, and then at most 30 a second.
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
 * While it works it draws through the buddy's capped loop: it waits out each gap on a timer and only
 * then asks for one frame, so it asks for at most 30 a second, whatever the display offers, and none
 * between two paints. The timers are faked, and the display is a loop over them that runs the frames
 * asked for at each refresh.
 *
 * The reduced-motion probe is `design/motion`'s call-time `prefersReducedMotion`, so the stub is
 * swapped between cases honestly.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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

/** The most frames a second the orb asks for while it works: the buddy's rate. */
const CAP = 30
/** One gap of the capped rate, and a millisecond over. */
const GAP_MS = Math.ceil(1000 / CAP) + 1

let frames: FrameRequestCallback[] = []
/** Every frame asked for, run or not. */
let asked = 0
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
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  frames = []
  asked = 0
  instrument = fakeContext()
  visibility = 'visible'
  Object.defineProperty(window, 'requestAnimationFrame', {
    configurable: true, writable: true, value: (cb: FrameRequestCallback) => { asked += 1; return frames.push(cb) },
  })
  Object.defineProperty(window, 'cancelAnimationFrame', { configurable: true, writable: true, value: () => {} })
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility })
  HTMLCanvasElement.prototype.getContext = (() => instrument.ctx) as never
  setReducedMotion(false)
})

afterEach(() => {
  vi.useRealTimers()
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL.matchMedia })
  Object.defineProperty(window, 'requestAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.raf })
  Object.defineProperty(window, 'cancelAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.caf })
  HTMLCanvasElement.prototype.getContext = ORIGINAL.getContext
  Reflect.deleteProperty(document, 'visibilityState')
})

/** A display refreshing `hz` times a second, run for `ms`: at each refresh the timers due by then
 *  fire, and then every frame asked for so far runs. Returns the frames asked for and the paints. */
function displayFor(ms: number, hz: number): { asked: number; painted: number } {
  const before = { asked, painted: instrument.calls.clearRect }
  for (let i = 0; i < Math.round((ms * hz) / 1000); i++) {
    vi.advanceTimersByTime(1000 / hz)
    for (const frame of frames.splice(0)) frame(performance.now())
  }
  return { asked: asked - before.asked, painted: instrument.calls.clearRect - before.painted }
}

const ACTIVE = WORK_PHASES.filter((p) => phaseFamily(p) === 'active')
const STILL = WORK_PHASES.filter((p) => phaseFamily(p) !== 'active')

describe('with motion allowed, an active phase animates: the positive control', () => {
  it.each(ACTIVE)('%s schedules a frame after its gap, and each frame the next', (phase) => {
    const { container } = render(<AgentOrb phase={phase} />)
    expect((container.querySelector('[data-agent-orb]') as HTMLElement).dataset.agentOrb).toBe('animated')
    vi.advanceTimersByTime(GAP_MS)
    expect(frames.length, 'no frame was scheduled for live work').toBe(1)
    frames.shift()!(GAP_MS)
    vi.advanceTimersByTime(GAP_MS)
    expect(frames.length, 'the loop did not go on').toBe(1)
    expect(instrument.calls.arc, 'no dot was drawn').toBeGreaterThan(0)
  })
})

describe(`while it works, it asks for at most ${CAP} frames a second, and none between two paints`, () => {
  it.each([[60], [120]])('on a %i Hz display', (hz) => {
    render(<AgentOrb phase="tool" />)
    const second = displayFor(1000, hz)
    expect(second.asked, `it asked for more than ${CAP} frames in a second`).toBeLessThanOrEqual(CAP)
    expect(second.painted, 'it painted more often than it asked for frames').toBeLessThanOrEqual(second.asked)
    // The control: capped is not stopped, nor slowed far under the cap.
    expect(second.painted, 'the capped orb all but stopped').toBeGreaterThanOrEqual(CAP - 5)
  })

  it('paints once at once, then asks for nothing until the gap is out', () => {
    render(<AgentOrb phase="streaming" size={64} />)
    expect(instrument.calls.clearRect, 'it was not painted as it mounted').toBe(1)
    expect(frames.length, 'it asked for a frame before its gap was out').toBe(0)
    vi.advanceTimersByTime(Math.floor(1000 / CAP) - 1)
    expect(frames.length, 'it asked for a frame inside the gap').toBe(0)
    vi.advanceTimersByTime(GAP_MS - Math.floor(1000 / CAP) + 1)
    expect(frames.length, 'the gap ran out and no frame was asked for').toBe(1)
    frames.shift()!(GAP_MS)
    expect(instrument.calls.clearRect, 'one clear a paint: at mount, then the frame after the gap').toBe(2)
    expect(frames.length, 'it asked for the next frame at once instead of waiting').toBe(0)
    vi.advanceTimersByTime(GAP_MS)
    expect(frames.length, 'the loop stopped while capped').toBe(1)
  })

  it('a still phase asks for none in a whole second', () => {
    for (const phase of STILL) {
      const { unmount } = render(<AgentOrb phase={phase} />)
      const second = displayFor(1000, 120)
      expect(second.asked, `${phase} asked for a frame`).toBe(0)
      unmount()
    }
  })

  it('the chat’s orb and the buddy draw through one loop', () => {
    const read = (path: string) => readFileSync(join(process.cwd(), 'src', path), 'utf8')
    expect(read('ui/AgentOrb.tsx'), 'the orb does not draw through the shared loop').toMatch(/\brunDots\(/)
    // The library's component paints every frame the display draws and takes no rate.
    expect(read('ui/AgentOrb.tsx')).not.toMatch(/<ThinkingOrb\b/)
    for (const candidate of ['buddyRig/CandidateInPlace.tsx', 'buddyRig/CandidateLayered.tsx']) {
      expect(read(candidate), `${candidate} draws through another loop`).toMatch(/import \{[^}]*\brunDots\b[^}]*\} from '\.\.\/ui\/orbDots'/)
    }
  })
})

describe('under reduced motion, every phase is one static frame', () => {
  beforeEach(() => setReducedMotion(true))

  it.each([...WORK_PHASES])('%s paints once and schedules no frame', (phase) => {
    const { container } = render(<AgentOrb phase={phase} />)
    expect((container.querySelector('[data-agent-orb]') as HTMLElement).dataset.agentOrb).toBe(phaseFamily(phase) === 'active' ? 'instant' : 'still')
    vi.advanceTimersByTime(1000)
    expect(frames.length, 'a frame was scheduled under reduced motion').toBe(0)
    expect(instrument.calls.arc, 'the still frame was never painted').toBeGreaterThan(0)
  })
})

describe('in a hidden tab, nothing is scheduled', () => {
  it.each(ACTIVE)('%s paints once and waits', (phase) => {
    visibility = 'hidden'
    render(<AgentOrb phase={phase} />)
    vi.advanceTimersByTime(1000)
    expect(frames.length, 'a hidden tab scheduled a frame').toBe(0)
    expect(instrument.calls.arc).toBeGreaterThan(0)
  })
})

describe('a turn that needs her, or has ended, is a still frame', () => {
  it.each(STILL)('%s schedules no frame with motion allowed, and is painted', (phase) => {
    const { container } = render(<AgentOrb phase={phase} />)
    expect((container.querySelector('[data-agent-orb]') as HTMLElement).dataset.agentOrb).toBe('still')
    vi.advanceTimersByTime(1000)
    expect(frames.length, `${phase} scheduled a frame`).toBe(0)
    expect(instrument.calls.arc, `${phase} was never painted`).toBeGreaterThan(0)
  })

  it('a running orb that turns to approval stops asking for frames', () => {
    const { rerender } = render(<AgentOrb phase="tool" />)
    vi.advanceTimersByTime(GAP_MS)
    expect(frames.length).toBe(1)
    rerender(<AgentOrb phase="approval" />)
    frames.splice(0).forEach((f) => f(2 * GAP_MS))
    vi.advanceTimersByTime(1000)
    expect(frames.length, 'the approval kept the loop alive').toBe(0)
  })
})
