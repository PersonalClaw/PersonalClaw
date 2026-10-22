import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'

// ── A pose change moves at the buddy's frame rate, not the display's ────────────────────────────
//
// `docs/design/buddy/decisions/rig.md`, "The quiet re-measure": with the dots capped at 30 frames a
// second, the motion library still moved the pose's values on every frame the display drew while a
// change ran, so the 60 s loop drew about 3,030 frames at 120 Hz and 1,660 at 60 Hz, and neither
// candidate passed the animating CPU bar at 120 Hz. The pose's values now step on the dots' capped
// clock: a change asks for at most the buddy's frames a second, whatever the display offers.
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

const { CandidateInPlace } = await import('./CandidateInPlace')
const { CandidateLayered } = await import('./CandidateLayered')
const { BUDDY_FPS } = await import('./poses')

/** A 2D context that draws nothing. */
const ctx = new Proxy({} as Record<string, unknown>, {
  get: (target, key: string) => (key in target ? target[key] : () => {}),
  set: (target, key: string, value) => { target[key] = value; return true },
}) as unknown as CanvasRenderingContext2D
const getContext = HTMLCanvasElement.prototype.getContext

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] })
  display.asked = 0
  display.queued = []
  HTMLCanvasElement.prototype.getContext = (() => ctx) as never
})

afterEach(() => {
  vi.useRealTimers()
  HTMLCanvasElement.prototype.getContext = getContext
})

/** Runs a `hz` display for `ms`; returns the frames asked for in that time. */
function displayFor(ms: number, hz: number): number {
  const before = display.asked
  for (let i = 0; i < Math.round((ms * hz) / 1000); i++) {
    act(() => {
      vi.advanceTimersByTime(1000 / hz)
      for (const frame of display.queued.splice(0)) frame(performance.now())
    })
  }
  return display.asked - before
}

const moving = (container: HTMLElement) => (container.querySelector('[data-rig-candidate]') as HTMLElement).dataset.rigMoving

describe.each([
  ['in place', CandidateInPlace],
  ['layered', CandidateLayered],
] as const)('the %s candidate, between two still poses', (_name, Candidate) => {
  it(`moves the pose at most ${BUDDY_FPS} frames a second on a 120 Hz display, and arrives`, async () => {
    const { container, rerender } = render(<Candidate phase="idle" size={48} />)
    displayFor(200, 120)
    rerender(<Candidate phase="approval" size={48} />)
    expect(moving(container), 'the change did not start').toBe('true')
    const first = displayFor(1000, 120)
    expect(first, `the pose asked for more than ${BUDDY_FPS} frames in its first second`).toBeLessThanOrEqual(BUDDY_FPS + 1)
    expect(first, 'the pose all but stopped').toBeGreaterThanOrEqual(BUDDY_FPS - 5)
    displayFor(2000, 120)
    await act(async () => { await Promise.resolve() })
    expect(moving(container), 'the pose never arrived').toBe('false')
    expect(displayFor(1000, 120), 'a pose at rest asked for a frame').toBe(0)
  })

  it('leaves nothing on the clock when it unmounts mid-change', () => {
    const { rerender, unmount } = render(<Candidate phase="idle" size={48} />)
    displayFor(200, 120)
    rerender(<Candidate phase="approval" size={48} />)
    displayFor(100, 120)
    unmount()
    expect(displayFor(1000, 120), 'an unmounted orb still moved its pose').toBe(0)
  })

  it('moves it on every frame the display draws only when nothing caps it: the control', () => {
    const { rerender } = render(<Candidate phase="idle" size={48} fps={null} />)
    displayFor(200, 120)
    rerender(<Candidate phase="approval" size={48} fps={null} />)
    expect(displayFor(500, 120), 'the counter does not see the frames an uncapped pose asks for').toBeGreaterThan(BUDDY_FPS)
  })
})
