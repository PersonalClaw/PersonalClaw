/**
 * The orb's change of state when the page around it changes (#6117, `docs/design/agent-orb.md`): a
 * tab hidden mid-morph, reduced motion turned on mid-morph, an orb scrolled out of view, done's swirl
 * cut short, and a phase that changes inside one state. Each change settles on its target's still,
 * and once settled the orb asks for no frame; a change nobody can see is an instant swap, painted at
 * once, so the orb shows the new state when it is seen. A change that starts mid-change starts from
 * what was last painted, and a scene starts from its beginning each time its state is entered.
 *
 * The timers and the page's clock are faked, the display is a loop that runs the frames asked for
 * at each refresh, and every paint is recorded, so the last one is compared with the painter's own
 * paint of the frame the orb should show, and every frame's weights and clock are kept. The tab's visibility, the reduced-motion query and the
 * orb's place on screen (an IntersectionObserver) are each a switch the test flips, with the event
 * the browser sends.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import { AgentOrb } from './AgentOrb'
import type { OrbScene } from '../lib/orbState'
import type { Clock, Weights } from './orb/halftone'
import { STILL_CLOCK, orbPaint, paintsIn, recorder, twoInks, type Entry } from '../test/orbPaint'

/** Every frame the orb draws, in order: its weights, its clock and whether done may swirl. */
const frameLog = vi.hoisted(() => [] as { w: Weights; clock: Clock; bloom: boolean }[])
vi.mock('./orb/halftone', async (orig) => {
  const real = await orig<typeof import('./orb/halftone')>()
  return {
    ...real,
    drawFrame: (...args: Parameters<typeof real.drawFrame>) => {
      frameLog.push({ w: { ...args[1] }, clock: { ...args[2] }, bloom: !!args[5].bloom })
      real.drawFrame(...args)
    },
  }
})

let log: Entry[] = []
let frames: FrameRequestCallback[] = []
let asked = 0
let visibility: DocumentVisibilityState = 'visible'
let reduced = false
const mediaListeners = new Set<() => void>()
let observers: { cb: IntersectionObserverCallback; el: Element | null }[] = []
const ORIGINAL = {
  getContext: HTMLCanvasElement.prototype.getContext,
  raf: window.requestAnimationFrame,
  caf: window.cancelAnimationFrame,
  matchMedia: window.matchMedia,
  io: (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver,
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] })
  log = []
  frames = []
  asked = 0
  visibility = 'visible'
  reduced = false
  mediaListeners.clear()
  observers = []
  frameLog.length = 0
  HTMLCanvasElement.prototype.getContext = (() => recorder(log)) as never
  Object.defineProperty(window, 'requestAnimationFrame', { configurable: true, writable: true, value: (cb: FrameRequestCallback) => { asked += 1; return frames.push(cb) } })
  Object.defineProperty(window, 'cancelAnimationFrame', { configurable: true, writable: true, value: () => {} })
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility })
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: (query: string) => ({
      get matches() { return reduced && query.includes('prefers-reduced-motion') },
      media: query, onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: (_: string, cb: () => void) => { mediaListeners.add(cb) },
      removeEventListener: (_: string, cb: () => void) => { mediaListeners.delete(cb) },
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList,
  })
  class StubObserver {
    cb: IntersectionObserverCallback
    constructor(cb: IntersectionObserverCallback) { this.cb = cb }
    observe(el: Element) { observers.push({ cb: this.cb, el }) }
    disconnect() { observers = observers.filter((o) => o.cb !== this.cb) }
    unobserve() {}
    takeRecords() { return [] }
  }
  ;(globalThis as { IntersectionObserver?: unknown }).IntersectionObserver = StubObserver
  document.documentElement.style.setProperty('--color-orb-alive', '#3366cc')
  document.documentElement.style.setProperty('--color-orb-rest', '#8a8f98')
})

afterEach(() => {
  vi.useRealTimers()
  HTMLCanvasElement.prototype.getContext = ORIGINAL.getContext
  Object.defineProperty(window, 'requestAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.raf })
  Object.defineProperty(window, 'cancelAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.caf })
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL.matchMedia })
  ;(globalThis as { IntersectionObserver?: unknown }).IntersectionObserver = ORIGINAL.io
  Reflect.deleteProperty(document, 'visibilityState')
  for (const name of ['--color-orb-alive', '--color-orb-rest']) document.documentElement.style.removeProperty(name)
})

/** A 60 Hz display run for `ms`: the timers due by each refresh fire, then every frame asked for. */
function displayFor(ms: number): number {
  const before = asked
  for (let i = 0; i < Math.round((ms * 60) / 1000); i++) {
    act(() => {
      vi.advanceTimersByTime(1000 / 60)
      for (const frame of frames.splice(0)) frame(performance.now())
    })
  }
  return asked - before
}
/** Long enough for any change to settle, with the promise a settled change resolves let run. */
async function settle(): Promise<void> {
  await act(async () => { displayFor(3000) })
  await act(async () => {})
}
const hide = (on: boolean) => act(() => { visibility = on ? 'hidden' : 'visible'; document.dispatchEvent(new Event('visibilitychange')) })
const reduce = (on: boolean) => act(() => { reduced = on; for (const cb of [...mediaListeners]) cb() })
const onScreen = (on: boolean) => act(() => {
  for (const o of [...observers]) o.cb([{ isIntersecting: on, target: o.el } as IntersectionObserverEntry], {} as IntersectionObserver)
})

/** The paints since a mark in the log. */
const paints = (from = 0) => paintsIn(log, from)
const lastPaint = () => paints().at(-1) ?? []
const INKS = twoInks({ r: 0x33, g: 0x66, b: 0xcc }, { r: 0x8a, g: 0x8f, b: 0x98 })
/** The painter's own paint of `scene`'s still at 20 px. */
const stillPaint = (scene: OrbScene): Entry[] => orbPaint({ [scene]: 1 }, { size: 20, dpr: 1, clock: STILL_CLOCK, inks: INKS })
const orbOf = (c: HTMLElement) => c.querySelector('[data-agent-orb]') as HTMLElement
/** How far the weights moved between two frames: the sum of each state's change. */
function weightStep(a: Weights, b: Weights): number {
  let d = 0
  for (const s of new Set([...Object.keys(a), ...Object.keys(b)]) as Set<OrbScene>) d += Math.abs((a[s] ?? 0) - (b[s] ?? 0))
  return d
}
const largestWeightStep = (list: Weights[]) => Math.max(0, ...list.slice(1).map((w, i) => weightStep(list[i], w)))

describe('a change cut short settles on its target', () => {
  it('a tab hidden mid-morph settles on needs your OK once it is shown, and then asks for nothing', async () => {
    const { container, rerender } = render(<AgentOrb phase="tool" />)
    displayFor(200)
    rerender(<AgentOrb phase="approval" />)
    displayFor(120)
    expect(orbOf(container).dataset.orbChange, 'the change did not start').toBe('morph')
    hide(true)
    displayFor(200)
    hide(false)
    await settle()
    expect(orbOf(container).dataset.orbChange).toBe('none')
    expect(lastPaint(), 'needs your OK is not what was painted last').toEqual(stillPaint('approval'))
    expect(displayFor(1000), 'a settled wait asked for a frame').toBe(0)
  })

  it('reduced motion turned on mid-morph settles at once on needs your OK', async () => {
    const { container, rerender } = render(<AgentOrb phase="tool" />)
    displayFor(200)
    rerender(<AgentOrb phase="approval" />)
    displayFor(120)
    reduce(true)
    await settle()
    expect(orbOf(container).dataset.orbChange).toBe('none')
    expect(lastPaint()).toEqual(stillPaint('approval'))
    expect(displayFor(1000)).toBe(0)
  })

  it('done’s swirl cut by a hidden tab settles on done', async () => {
    const { container, rerender } = render(<AgentOrb phase="streaming" />)
    displayFor(200)
    rerender(<AgentOrb phase="done" />)
    displayFor(100)
    expect(orbOf(container).dataset.orbChange).toBe('bloom')
    hide(true)
    displayFor(200)
    hide(false)
    await settle()
    expect(orbOf(container).dataset.orbChange).toBe('none')
    expect(lastPaint()).toEqual(stillPaint('done'))
    expect(displayFor(1000)).toBe(0)
  })

  it('a running orb that is hidden stops asking for frames, and asks again once shown: the control', () => {
    render(<AgentOrb phase="tool" />)
    expect(displayFor(500), 'the control: a working orb asks for frames').toBeGreaterThan(5)
    hide(true)
    expect(displayFor(1000), 'a hidden orb asked for a frame').toBe(0)
    hide(false)
    expect(displayFor(500), 'the shown orb did not move again').toBeGreaterThan(5)
  })
})

describe('a change nobody can see is a swap, painted at once, so the orb shows it when seen', () => {
  it('a change made off screen asks for no frame, and the orb shows the new state, before and after it scrolls in', () => {
    const { container, rerender } = render(<AgentOrb phase="tool" />)
    displayFor(200)
    onScreen(false)
    const mark = log.length
    rerender(<AgentOrb phase="approval" />)
    expect(displayFor(1000), 'a change off screen asked for frames').toBe(0)
    expect(orbOf(container).dataset.orbChange).toBe('none')
    expect(paints(mark).at(-1), 'the new state was not painted').toEqual(stillPaint('approval'))
    onScreen(true)
    displayFor(200)
    expect(lastPaint()).toEqual(stillPaint('approval'))
  })

  it('an orb scrolled out 80 ms into thinking → idle shows ready when it comes back', async () => {
    const { container, rerender } = render(<AgentOrb phase="thinking" />)
    displayFor(200)
    rerender(<AgentOrb phase="idle" />)
    displayFor(80)
    onScreen(false)
    expect(displayFor(1000), 'an orb scrolled out mid-morph asked for frames').toBe(0)
    await settle()
    onScreen(true)
    await settle()
    expect(orbOf(container).dataset.orbChange).toBe('none')
    expect(lastPaint()).toEqual(stillPaint('ready'))
  })
})

describe('a change that starts mid-change starts from what was last painted', () => {
  it('done, then approval 65 ms later, moves on from the frame it was at, in steps no larger than a plain morph’s', async () => {
    // The reference: a plain morph from running to needs your OK.
    const plain = render(<AgentOrb phase="tool" />)
    displayFor(200)
    let at = frameLog.length
    plain.rerender(<AgentOrb phase="approval" />)
    await settle()
    const morphStep = largestWeightStep(frameLog.slice(at).map((f) => f.w))
    expect(morphStep, 'the control: the plain morph moved the weights').toBeGreaterThan(0.05)
    plain.unmount()

    const { container, rerender } = render(<AgentOrb phase="tool" />)
    displayFor(200)
    rerender(<AgentOrb phase="done" />)
    displayFor(65)
    at = frameLog.length
    rerender(<AgentOrb phase="approval" />)
    await settle()
    const frames = frameLog.slice(at - 1).map((f) => f.w)
    expect(frames.length, 'the control: the second change was painted').toBeGreaterThan(3)
    expect(largestWeightStep(frames), 'the weights jumped at the change').toBeLessThanOrEqual(morphStep * 1.05 + 1e-6)
    expect(orbOf(container).dataset.orbChange).toBe('none')
    expect(lastPaint()).toEqual(stillPaint('approval'))
  })
})

describe('a phase that changes inside one state is no change of state', () => {
  it('queued → waiting_model keeps moving on the same clock, with no morph and no fresh start', () => {
    const { container, rerender } = render(<AgentOrb phase="queued" />)
    displayFor(300)
    const at = frameLog.length
    rerender(<AgentOrb phase="waiting_model" />)
    expect(orbOf(container).dataset.orbChange).toBe('none')
    displayFor(400)
    const after = frameLog.slice(at)
    expect(after.length, 'the control: it kept painting').toBeGreaterThan(5)
    for (const f of after) expect(f.w).toEqual({ queued: 1 })
    const since = after.map((f) => f.clock.since)
    for (let i = 1; i < since.length; i++) expect(since[i], 'the scene started over').toBeGreaterThan(since[i - 1])
    expect(since[0], 'the scene started over at the change').toBeGreaterThan(0.25)
  })

  it('a phase that changes inside one state mid-morph keeps the morph going, with no snap', () => {
    const { container, rerender } = render(<AgentOrb phase="approval" />)
    displayFor(200)
    const start = frameLog.length
    rerender(<AgentOrb phase="queued" />)
    displayFor(100)
    const mark = frameLog.length
    rerender(<AgentOrb phase="waiting_model" />)
    expect(orbOf(container).dataset.orbChange, 'the morph stopped').toBe('morph')
    displayFor(100)
    const before = frameLog.slice(start, mark).map((f) => f.w)
    const after = frameLog.slice(mark).map((f) => f.w)
    expect(before.length, 'the control: the morph painted before the change').toBeGreaterThan(1)
    expect(after.length, 'the control: it painted after the change').toBeGreaterThan(0)
    expect(weightStep(before.at(-1)!, after[0]), 'the weights snapped at the change').toBeLessThanOrEqual(largestWeightStep(before) * 1.05 + 1e-6)
  })
})

describe('a scene starts from its beginning each time its state is entered', () => {
  it('running, then needs your OK, then running again: the second running starts at its first instant', async () => {
    const { rerender } = render(<AgentOrb phase="tool" />)
    displayFor(1500)
    expect(frameLog.at(-1)!.clock.since, 'the control: running had run a while').toBeGreaterThan(1.2)
    rerender(<AgentOrb phase="approval" />)
    await settle()
    const at = frameLog.length
    rerender(<AgentOrb phase="tool" />)
    displayFor(100)
    expect(frameLog[at].clock.since, 'running did not start over').toBeLessThan(0.1)
  })
})

describe('done swirls only for the turn whose ending lets it', () => {
  // `bloom` is the caller's word on the turn's ending (`ui/turnEnding.ts`'s `bloomsFor`): a turn that
  // completed swirls and bumps, and a stopped or declined one settles into done without.
  it('a completed turn’s done swirls, and settles on done: the control', async () => {
    const { container, rerender } = render(<AgentOrb phase="tool" />)
    displayFor(200)
    const at = frameLog.length
    rerender(<AgentOrb phase="done" bloom />)
    displayFor(100)
    expect(orbOf(container).dataset.orbChange).toBe('bloom')
    await settle()
    expect(frameLog.slice(at).some((f) => f.bloom && !f.clock.still), 'done never swirled').toBe(true)
    expect(orbOf(container).dataset.orbChange).toBe('none')
    expect(lastPaint()).toEqual(stillPaint('done'))
  })

  it.each(['stopped', 'declined'])('a %s turn’s done morphs into done with no swirl', async () => {
    const { container, rerender } = render(<AgentOrb phase="tool" />)
    displayFor(200)
    const at = frameLog.length
    rerender(<AgentOrb phase="done" bloom={false} />)
    displayFor(100)
    expect(orbOf(container).dataset.orbChange).toBe('morph')
    await settle()
    expect(frameLog.slice(at).every((f) => !f.bloom), 'a held-back done swirled').toBe(true)
    expect(orbOf(container).dataset.orbChange).toBe('none')
    expect(lastPaint()).toEqual(stillPaint('done'))
    expect(displayFor(1000)).toBe(0)
  })
})

describe('two waits on the owner are two states', () => {
  it('approval → input morphs the mark from "!" to "?", then asks for nothing', async () => {
    const { container, rerender } = render(<AgentOrb phase="approval" />)
    displayFor(200)
    rerender(<AgentOrb phase="input" />)
    expect(orbOf(container).dataset.orbChange).toBe('morph')
    await settle()
    expect(orbOf(container).dataset.orbGlyph).toBe('?')
    expect(lastPaint()).toEqual(stillPaint('question'))
    expect(displayFor(1000), 'a settled question asked for frames').toBe(0)
  })
})
