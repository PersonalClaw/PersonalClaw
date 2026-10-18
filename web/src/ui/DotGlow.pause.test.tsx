/**
 * Claw Arcade's wave field pauses whenever nobody can see it, and slows down while nobody is
 * working in its window.
 *
 * `web/DESIGN.md` §7: the field (`waves`) pauses in a hidden tab and while its stage is
 * offscreen, and picks up again on the reverse. A window behind another app is still on screen,
 * so its field keeps moving there, at 30 fps rather than every frame the display offers, and gets
 * its full rate back with the window's focus. Nothing stopped or slowed the loop before: a hidden
 * tab or a stage scrolled away kept asking for a frame every 16ms that nobody would ever see, and
 * behind another app the field repainted some 19,000 dots on every frame the display offered.
 *
 * The loop holds at most ONE pending frame, so pausing and resuming any number of times leaves
 * exactly one loop running rather than one per resume, and a frame it lets pass at 30 fps still
 * asks for exactly one more. The instrument is a queue of pending frame callbacks that
 * `cancelAnimationFrame` really removes from, so a cancelled frame cannot be mistaken for a
 * running one, and a count of the canvas clears, one per painted frame. jsdom has no
 * IntersectionObserver, and its `document.hasFocus()` answers whether an element was ever
 * focused rather than whether the window has focus; the stubs here say what the test tells them.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'
import { DotGlow } from './DotGlow'

vi.mock('../app/appearance', () => ({
  useAppearance: () => ({ selectValue: () => 'waves' }),
}))

/** Pending frames, by id. A cancelled one is gone. */
let pending = new Map<number, FrameRequestCallback>()
let nextId = 1
let hidden = false
let windowFocused = true
/** Frames the field painted: each one starts by clearing the canvas. */
let paints = 0
/** The frame clock, in ms. */
let now = 0
const observers: FakeObserver[] = []

class FakeObserver {
  targets: Element[] = []
  disconnected = false
  constructor(public cb: IntersectionObserverCallback) { observers.push(this) }
  observe(el: Element) { this.targets.push(el) }
  unobserve() {}
  disconnect() { this.disconnected = true }
  takeRecords() { return [] }
  /** Report the stage as on or off screen. */
  report(isIntersecting: boolean) {
    this.cb(this.targets.map((target) => ({ isIntersecting, target }) as IntersectionObserverEntry), this as unknown as IntersectionObserver)
  }
}

const ORIGINAL = {
  raf: window.requestAnimationFrame,
  caf: window.cancelAnimationFrame,
  getContext: HTMLCanvasElement.prototype.getContext,
  io: (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver,
}

beforeEach(() => {
  pending = new Map()
  nextId = 1
  hidden = false
  windowFocused = true
  paints = 0
  now = 0
  observers.length = 0
  Object.defineProperty(window, 'requestAnimationFrame', {
    configurable: true, writable: true,
    value: (cb: FrameRequestCallback) => { const id = nextId++; pending.set(id, cb); return id },
  })
  Object.defineProperty(window, 'cancelAnimationFrame', {
    configurable: true, writable: true, value: (id: number) => { pending.delete(id) },
  })
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => (hidden ? 'hidden' : 'visible') })
  Object.defineProperty(document, 'hasFocus', { configurable: true, value: () => windowFocused })
  HTMLCanvasElement.prototype.getContext = (() => new Proxy({}, {
    get: (_t, name) => (name === 'clearRect' ? () => { paints += 1 } : () => {}),
  })) as never
  ;(globalThis as { IntersectionObserver?: unknown }).IntersectionObserver = FakeObserver
  for (const prop of ['clientWidth', 'clientHeight'] as const) {
    Object.defineProperty(HTMLDivElement.prototype, prop, { configurable: true, value: prop === 'clientWidth' ? 800 : 600 })
  }
})

afterEach(() => {
  Object.defineProperty(window, 'requestAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.raf })
  Object.defineProperty(window, 'cancelAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.caf })
  delete (document as unknown as Record<string, unknown>).hidden
  delete (document as unknown as Record<string, unknown>).visibilityState
  delete (document as unknown as Record<string, unknown>).hasFocus
  HTMLCanvasElement.prototype.getContext = ORIGINAL.getContext
  ;(globalThis as { IntersectionObserver?: unknown }).IntersectionObserver = ORIGINAL.io
  for (const prop of ['clientWidth', 'clientHeight'] as const) delete (HTMLDivElement.prototype as unknown as Record<string, unknown>)[prop]
})

/** Run every frame that is pending now, once (each one may ask for its successor). */
function tick(t = 16) {
  const due = [...pending.entries()]
  pending = new Map()
  for (const [, cb] of due) cb(t)
}

function setHidden(next: boolean) {
  hidden = next
  document.dispatchEvent(new Event('visibilitychange'))
}

/** Another app takes the focus, or gives it back: what a browser reports, both ways. */
function setWindowFocus(next: boolean) {
  windowFocused = next
  window.dispatchEvent(new Event(next ? 'focus' : 'blur'))
}

/** Run the frame clock for `ms` on a display refreshing `hz` times a second; the paints in it. */
function paintsOver(ms: number, hz: number): number {
  const before = paints
  for (let i = 0; i < Math.round((ms * hz) / 1000); i++) {
    now += 1000 / hz
    tick(now)
  }
  return paints - before
}

const stage = () => {
  expect(observers, 'the field never watched whether its stage is on screen').toHaveLength(1)
  return observers[0]
}

describe('the wave field runs while it can be seen', () => {
  it('positive control: mounted on screen, it asks for a frame, and each frame asks for the next', () => {
    render(<DotGlow />)
    expect(pending.size).toBe(1)
    tick()
    expect(pending.size, 'the loop stopped on its own').toBe(1)
  })
})

describe('a window behind another app', () => {
  // Still on screen, so the field keeps moving there; but nobody is working in it, so it paints at
  // 30 fps, whatever rate the display offers.
  it.each([[60], [120]])('keeps moving at 30 fps on a %i Hz display, and gets every frame back with the focus', (hz) => {
    render(<DotGlow />)
    expect(paintsOver(1000, hz), 'the focused field skipped frames').toBe(hz)
    setWindowFocus(false)
    expect(paintsOver(1000, hz), 'the field kept its full rate behind another app').toBe(30)
    expect(pending.size, 'the field froze in a window that can still be seen, or runs two loops').toBe(1)
    setWindowFocus(true)
    expect(paintsOver(1000, hz), 'the field stayed at 30 fps with the focus back').toBe(hz)
    expect(pending.size).toBe(1)
  })

  it('mounted in a window without the focus, it starts at 30 fps', () => {
    windowFocused = false
    render(<DotGlow />)
    expect(paintsOver(1000, 60)).toBe(30)
  })
})

describe('a hidden tab', () => {
  it('asks for no frame, and the field picks up again when the tab is shown', () => {
    render(<DotGlow />)
    tick()
    setHidden(true)
    expect(pending.size, 'a frame is still pending in a hidden tab').toBe(0)
    tick()
    expect(pending.size).toBe(0)
    setHidden(false)
    expect(pending.size, 'the field did not resume when the tab came back').toBe(1)
  })

  it('mounted hidden, it waits for the tab to be shown before its first frame', () => {
    hidden = true
    render(<DotGlow />)
    expect(pending.size).toBe(0)
    setHidden(false)
    expect(pending.size).toBe(1)
  })
})

describe('a stage scrolled offscreen', () => {
  it('asks for no frame, and the field picks up again when the stage comes back', () => {
    render(<DotGlow />)
    tick()
    stage().report(false)
    expect(pending.size, 'a frame is still pending for a stage nobody can see').toBe(0)
    stage().report(true)
    expect(pending.size, 'the field did not resume when its stage came back').toBe(1)
  })

  it('watches its own layer, the box the light is drawn in', () => {
    const { container } = render(<DotGlow />)
    expect(stage().targets).toEqual([container.querySelector('[data-dot-glow]')])
  })

  it('needs both: shown again while still offscreen, it stays paused', () => {
    render(<DotGlow />)
    stage().report(false)
    setHidden(true)
    setHidden(false)
    expect(pending.size, 'the tab came back, but the stage is still offscreen').toBe(0)
    stage().report(true)
    expect(pending.size).toBe(1)
  })
})

describe('pausing and resuming never leaves more than one loop', () => {
  it('hidden, shown, hidden, shown, offscreen, on screen: still exactly one frame pending', () => {
    render(<DotGlow />)
    for (let i = 0; i < 3; i++) { setHidden(true); setHidden(false) }
    stage().report(false)
    stage().report(true)
    stage().report(true)
    expect(pending.size).toBe(1)
    tick()
    expect(pending.size, 'two loops are running side by side').toBe(1)
  })
})

describe('unmounting', () => {
  it('cancels the pending frame and stops listening', () => {
    const { unmount } = render(<DotGlow />)
    const io = stage()
    unmount()
    expect(pending.size, 'a frame outlived its layer').toBe(0)
    expect(io.disconnected, 'the stage is still watched').toBe(true)
    setHidden(true)
    setHidden(false)
    expect(pending.size, 'a tab shown after unmount restarted a dead loop').toBe(0)
  })
})
