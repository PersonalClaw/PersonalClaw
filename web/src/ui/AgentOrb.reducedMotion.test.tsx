/**
 * The agent orb schedules an animation frame only while its state moves (thinking or working), on
 * screen, with motion allowed, at 20 or 64 px, or while it morphs from one state to the next, and
 * then at most 30 a second (#6117).
 *
 * Each half is paired with the control that makes it mean something, as `DotGlow.reducedMotion`'s
 * are:
 *
 *  - MOTION ALLOWED, a thinking or working phase: frames ARE scheduled, and running one schedules
 *    the next. A spy that never saw a frame would make every "no frame" below vacuous.
 *  - REDUCED MOTION, a hidden tab, the 12 px mark, and every still state (ready, waiting on you,
 *    done): no frame is scheduled at all, AND the orb is still painted once. An orb that drew
 *    nothing would satisfy "no frame" for free.
 *  - A CHANGE OF STATE: the morph asks for frames, on the same cap, and once it has settled the
 *    still state it arrived at asks for none.
 *
 * While it works it draws through the buddy's capped loop: it waits out each gap on a timer and only
 * then asks for one frame, so it asks for at most 30 a second, whatever the display offers, and none
 * between two paints. The timers and the page's clock are faked, and the display is a loop over them
 * that runs the frames asked for at each refresh.
 *
 * The reduced-motion probe is `design/motion`'s call-time `prefersReducedMotion`, so the stub is
 * swapped between cases honestly.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import { AgentOrb } from './AgentOrb'
import { WORK_PHASES } from '../lib/workPhase'
import { ORB_STATE_OF } from '../lib/orbState'
import { MOVES } from './orb/clawRing'

const RECORDED = ['setTransform', 'clearRect', 'beginPath', 'moveTo', 'quadraticCurveTo', 'stroke'] as const

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
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] })
  // The orb paints only in a resolved ink, as on any page with the design's tokens.
  document.documentElement.style.setProperty('--color-orb-alive', '#3366cc')
  document.documentElement.style.setProperty('--color-orb-rest', '#8a8f98')
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
  for (const name of ['--color-orb-alive', '--color-orb-rest']) document.documentElement.style.removeProperty(name)
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

/** Three seconds of display, then the promise a settled morph resolves is let run. */
async function settle(): Promise<void> {
  await act(async () => { displayFor(3000, 60) })
  await act(async () => {})
}

const MOVING = WORK_PHASES.filter((p) => MOVES[ORB_STATE_OF[p].state])
const STILL = WORK_PHASES.filter((p) => !MOVES[ORB_STATE_OF[p].state])

describe('with motion allowed, a thinking or working phase animates: the positive control', () => {
  it.each(MOVING)('%s schedules a frame after its gap, and each frame the next', (phase) => {
    const { container } = render(<AgentOrb phase={phase} />)
    expect((container.querySelector('[data-agent-orb]') as HTMLElement).dataset.agentOrb).toBe('animated')
    vi.advanceTimersByTime(GAP_MS)
    expect(frames.length, 'no frame was scheduled for live work').toBe(1)
    frames.shift()!(GAP_MS)
    vi.advanceTimersByTime(GAP_MS)
    expect(frames.length, 'the loop did not go on').toBe(1)
    expect(instrument.calls.stroke, 'no stroke was drawn').toBeGreaterThan(0)
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
    // Its morph and bloom move on the same capped clock, not on every frame the display draws.
    expect(read('ui/AgentOrb.tsx'), 'the morph runs off the capped clock').toMatch(/\bcappedDriver\(\)/)
    for (const candidate of ['buddyRig/CandidateInPlace.tsx', 'buddyRig/CandidateLayered.tsx']) {
      expect(read(candidate), `${candidate} draws through another loop`).toMatch(/import \{[^}]*\brunDots\b[^}]*\} from '\.\.\/ui\/orbDots'/)
    }
  })
})

describe('under reduced motion, every phase is one static frame', () => {
  beforeEach(() => setReducedMotion(true))

  it.each([...WORK_PHASES])('%s paints once and schedules no frame', (phase) => {
    const { container } = render(<AgentOrb phase={phase} />)
    expect((container.querySelector('[data-agent-orb]') as HTMLElement).dataset.agentOrb).toBe(MOVES[ORB_STATE_OF[phase].state] ? 'instant' : 'still')
    vi.advanceTimersByTime(1000)
    expect(frames.length, 'a frame was scheduled under reduced motion').toBe(0)
    expect(instrument.calls.stroke, 'the still frame was never painted').toBeGreaterThan(0)
  })
})

describe('in a hidden tab, nothing is scheduled', () => {
  it.each(MOVING)('%s paints once and waits', (phase) => {
    visibility = 'hidden'
    render(<AgentOrb phase={phase} />)
    vi.advanceTimersByTime(1000)
    expect(frames.length, 'a hidden tab scheduled a frame').toBe(0)
    expect(instrument.calls.stroke).toBeGreaterThan(0)
  })
})

describe('the 12 px mark never moves', () => {
  it.each(MOVING)('%s paints its rest form once and schedules no frame, with motion allowed', (phase) => {
    const { container } = render(<AgentOrb phase={phase} size={12} />)
    expect((container.querySelector('[data-agent-orb]') as HTMLElement).dataset.agentOrb).toBe('still')
    expect(displayFor(1000, 120).asked, `${phase}'s mark asked for a frame`).toBe(0)
    expect(instrument.calls.stroke, `${phase}'s mark was never painted`).toBeGreaterThan(0)
  })

  it('a mark that changes state swaps at once, with no morph', () => {
    const { container, rerender } = render(<AgentOrb phase="tool" size={12} />)
    rerender(<AgentOrb phase="done" size={12} />)
    expect((container.querySelector('[data-agent-orb]') as HTMLElement).dataset.orbChange).toBe('none')
    expect(displayFor(1000, 120).asked, 'the mark morphed').toBe(0)
  })
})

describe('a turn that is ready, needs the owner, or has ended, is a still frame', () => {
  it.each(STILL)('%s schedules no frame with motion allowed, and is painted', (phase) => {
    const { container } = render(<AgentOrb phase={phase} />)
    expect((container.querySelector('[data-agent-orb]') as HTMLElement).dataset.agentOrb).toBe('still')
    vi.advanceTimersByTime(1000)
    expect(frames.length, `${phase} scheduled a frame`).toBe(0)
    expect(instrument.calls.stroke, `${phase} was never painted`).toBeGreaterThan(0)
  })

  it('a running orb that turns to approval morphs into the clasp on the cap, then asks for nothing', async () => {
    const { container, rerender } = render(<AgentOrb phase="tool" />)
    displayFor(200, 60)
    rerender(<AgentOrb phase="approval" />)
    const orb = container.querySelector('[data-agent-orb]') as HTMLElement
    expect(orb.dataset.orbChange, 'the change did not morph').toBe('morph')
    // The control: the morph does ask for frames, and no more than the cap allows.
    const morphing = displayFor(300, 120)
    expect(morphing.asked, 'the morph asked for no frame').toBeGreaterThan(0)
    expect(morphing.asked, 'the morph went over the cap').toBeLessThanOrEqual(Math.ceil(0.3 * CAP) + 1)
    await settle()
    expect(orb.dataset.orbChange, 'the morph never settled').toBe('none')
    expect(displayFor(1000, 120).asked, 'the approval kept the loop alive').toBe(0)
  })

  it('a turn that ends blooms once, then asks for nothing', async () => {
    const { container, rerender } = render(<AgentOrb phase="streaming" />)
    displayFor(200, 60)
    rerender(<AgentOrb phase="done" />)
    const orb = container.querySelector('[data-agent-orb]') as HTMLElement
    expect(orb.dataset.orbChange).toBe('bloom')
    await settle()
    expect(orb.dataset.orbChange, 'the bloom never settled').toBe('none')
    expect(displayFor(1000, 120).asked, 'done kept the loop alive').toBe(0)
  })

  it('a failure closes with no bloom', () => {
    const { container, rerender } = render(<AgentOrb phase="tool" />)
    displayFor(200, 60)
    rerender(<AgentOrb phase="error" />)
    expect((container.querySelector('[data-agent-orb]') as HTMLElement).dataset.orbChange).toBe('morph')
  })

  it('under reduced motion a change of state is an instant swap', () => {
    setReducedMotion(true)
    const { container, rerender } = render(<AgentOrb phase="tool" />)
    rerender(<AgentOrb phase="done" />)
    expect((container.querySelector('[data-agent-orb]') as HTMLElement).dataset.orbChange).toBe('none')
    expect(displayFor(1000, 120).asked, 'a reduced change asked for a frame').toBe(0)
  })
})
