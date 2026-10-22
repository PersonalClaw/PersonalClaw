import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'

// ── The buddy orb draws nothing while its pose holds (#3891) ────────────────────────────────────
//
// Frames run only while something moves: a held pose asks for no animation frame at all over a
// minute, while the working hold (the dots moving in the agent orb's state for the phase) asks for
// them, at most the buddy's 30 a second. A pose change moves, arrives, and then holds as quietly.
//
// The motion library captures `requestAnimationFrame` when it loads, so the counter is installed
// before anything imports it. The display is simulated: at each refresh the timers due by then
// fire, and then every frame asked for so far runs.

const display = vi.hoisted(() => {
  const state = { asked: 0, queued: [] as FrameRequestCallback[] }
  globalThis.requestAnimationFrame = ((cb: FrameRequestCallback) => {
    state.asked += 1
    state.queued.push(cb)
    return state.queued.length
  }) as typeof requestAnimationFrame
  globalThis.cancelAnimationFrame = (() => {}) as typeof cancelAnimationFrame
  return state
})

const { BuddyOrb } = await import('./BuddyOrb')
const { ORB_FPS } = await import('./orbDots')
const { FAMILY_STATES } = await import('../buddyRig/poseSet')

const ctx = new Proxy({} as Record<string, unknown>, {
  get: (target, key: string) => (key in target ? target[key] : () => {}),
  set: (target, key: string, value) => { target[key] = value; return true },
}) as unknown as CanvasRenderingContext2D
const getContext = HTMLCanvasElement.prototype.getContext
const originalMatchMedia = window.matchMedia
let visibility: DocumentVisibilityState = 'visible'

function setReducedMotion(on: boolean): void {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: (query: string) => ({
      matches: on && query.includes('prefers-reduced-motion'), media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }),
  })
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] })
  display.asked = 0
  display.queued = []
  visibility = 'visible'
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility })
  HTMLCanvasElement.prototype.getContext = (() => ctx) as never
  setReducedMotion(false)
})

afterEach(() => {
  vi.useRealTimers()
  HTMLCanvasElement.prototype.getContext = getContext
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: originalMatchMedia })
})

/** Runs a `hz` display for `ms`; returns the frames asked for in that time. */
function displayFor(ms: number, hz = 120): number {
  const before = display.asked
  for (let i = 0; i < Math.round((ms * hz) / 1000); i++) {
    act(() => {
      vi.advanceTimersByTime(1000 / hz)
      for (const frame of display.queued.splice(0)) frame(performance.now())
    })
  }
  return display.asked - before
}

const MINUTE = 60_000
const HELD = [...FAMILY_STATES.quiet, ...FAMILY_STATES.waiting, ...FAMILY_STATES.blocked]
const moving = (container: HTMLElement) => (container.querySelector('[data-buddy-orb]') as HTMLElement).dataset.buddyMoving

describe('a held pose draws nothing', () => {
  it.each(HELD)('%s asks for no frame over a minute', (state) => {
    const { container } = render(<BuddyOrb state={state} line={state} />)
    expect(moving(container)).toBe('false')
    expect(displayFor(MINUTE, 60)).toBe(0)
  })

  it(`the working hold asks for frames, at most ${ORB_FPS} a second: the control`, () => {
    const { container } = render(<BuddyOrb state="tool" line="Working" />)
    expect(moving(container)).toBe('true')
    const second = displayFor(1000)
    expect(second, 'the spy saw no frame of the working hold').toBeGreaterThanOrEqual(ORB_FPS - 5)
    expect(second).toBeLessThanOrEqual(ORB_FPS + 1)
  })

  it('a pose change moves, arrives, and then holds with no frame', async () => {
    const { container, rerender } = render(<BuddyOrb state="idle" line="Nothing running" />)
    rerender(<BuddyOrb state="approval" line="Needs your OK" />)
    expect(moving(container), 'the change did not start').toBe('true')
    expect(displayFor(1000), 'the change moved on no frame').toBeGreaterThan(0)
    displayFor(2000)
    await act(async () => { await Promise.resolve() })
    expect(moving(container), 'the pose never arrived').toBe('false')
    expect(displayFor(MINUTE, 60), 'a pose at rest asked for a frame').toBe(0)
  })

  it('under reduced motion the working hold is its still, and a change is a jump', () => {
    setReducedMotion(true)
    const { container, rerender } = render(<BuddyOrb state="tool" line="Working" />)
    expect(moving(container)).toBe('false')
    rerender(<BuddyOrb state="approval" line="Needs your OK" />)
    expect(moving(container)).toBe('false')
    expect(displayFor(MINUTE, 60)).toBe(0)
  })

  it('in a hidden page the working hold asks for nothing', () => {
    visibility = 'hidden'
    render(<BuddyOrb state="tool" line="Working" />)
    expect(displayFor(MINUTE, 60)).toBe(0)
  })

  it('leaves nothing on the clock when it unmounts while working', () => {
    const { unmount } = render(<BuddyOrb state="tool" line="Working" />)
    displayFor(200)
    unmount()
    expect(displayFor(1000)).toBe(0)
  })
})
