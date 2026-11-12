/**
 * The orb's change of state when the page around it changes (#6117): a tab hidden mid-morph, reduced
 * motion turned on mid-morph, an orb scrolled out of view, a bloom cut short, and a phase that
 * changes inside one state. Each change settles on its target's form at scale 1, and once settled
 * the orb asks for no frame; a change nobody can see is an instant swap, painted at once, so the
 * orb shows the new state when it is seen. A morph into working keeps each stroke's way round.
 *
 * The timers and the page's clock are faked, the display is a loop that runs the frames asked for
 * at each refresh, and every paint is recorded, so the last one is compared with the painter's own
 * paint of the form the orb should show. The tab's visibility, the reduced-motion query and the
 * orb's place on screen (an IntersectionObserver) are each a switch the test flips, with the event
 * the browser sends.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import { AgentOrb } from './AgentOrb'
import { restForm } from './orb/clawRing'
import { paintRing } from './orb/paintRing'

/** The scale of every paint the orb makes, in order: the painter, passed through. */
const scales = vi.hoisted(() => [] as number[])
vi.mock('./orb/paintRing', async (orig) => {
  const real = await orig<typeof import('./orb/paintRing')>()
  return {
    ...real,
    paintRing: (...args: Parameters<typeof real.paintRing>) => { scales.push(args[2].scale); real.paintRing(...args) },
  }
})

type Entry = (string | number | boolean | undefined)[]

/** A 2D context that keeps everything it is told, in order. */
function recorder(log: Entry[]): CanvasRenderingContext2D {
  const state: Record<string, unknown> = { fillStyle: '', strokeStyle: '', lineWidth: 1, lineCap: 'butt', lineJoin: 'miter', globalAlpha: 1 }
  return new Proxy(state, {
    get(target, key: string) {
      if (key in target) return target[key]
      if (key === 'getImageData') return () => ({ data: [0, 0, 0, 0] })
      return (...args: (string | number | boolean)[]) => { log.push([key, ...args]) }
    },
    set(target, key: string, value) { target[key] = value; log.push(['=', key, value]); return true },
  }) as unknown as CanvasRenderingContext2D
}

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
  scales.length = 0
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

/** The paints since a mark in the log, each from its reset of the transform. */
function paints(from = 0): Entry[][] {
  const out: Entry[][] = []
  for (let i = from; i < log.length; i++) {
    const e = log[i]
    if (e[0] === 'setTransform' && e[5] === 0 && e[6] === 0) out.push([])
    if (out.length) out[out.length - 1].push(e)
  }
  return out
}
const lastPaint = () => paints().at(-1) ?? []
/** The painter's own paint of `state`'s rest form at 20 px, in the ink its token resolves to. */
function restPaint(state: Parameters<typeof restForm>[0], ink = { r: 0x33, g: 0x66, b: 0xcc }): Entry[] {
  const want: Entry[] = []
  paintRing(recorder(want), restForm(state), { size: 20, dpr: 1, ink, scale: 1 })
  return want
}
const orbOf = (c: HTMLElement) => c.querySelector('[data-agent-orb]') as HTMLElement
/** Each paint's stroke starts, the points a step between two paints is measured by. */
const startsOf = (p: Entry[]) => p.filter((e) => e[0] === 'moveTo').map((e) => [Number(e[1]), Number(e[2])] as const)
/** How far any stroke's start moved between two paints. */
function stepBetween(p: Entry[], q: Entry[]): number {
  const a = startsOf(p)
  const b = startsOf(q)
  let most = 0
  a.forEach(([x, y], j) => { if (b[j]) most = Math.max(most, Math.hypot(b[j][0] - x, b[j][1] - y)) })
  return most
}
function largestStep(list: Entry[][]): number {
  let most = 0
  for (let i = 1; i < list.length; i++) most = Math.max(most, stepBetween(list[i - 1], list[i]))
  return most
}

describe('a change cut short settles on its target', () => {
  it('a tab hidden mid-morph settles on the clasp once it is shown, and then asks for nothing', async () => {
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
    expect(lastPaint(), 'the clasp is not what was painted last').toEqual(restPaint('waiting'))
    expect(displayFor(1000), 'a settled clasp asked for a frame').toBe(0)
  })

  it('reduced motion turned on mid-morph settles at once on the clasp', async () => {
    const { container, rerender } = render(<AgentOrb phase="tool" />)
    displayFor(200)
    rerender(<AgentOrb phase="approval" />)
    displayFor(120)
    reduce(true)
    await settle()
    expect(orbOf(container).dataset.orbChange).toBe('none')
    expect(lastPaint()).toEqual(restPaint('waiting'))
    expect(displayFor(1000)).toBe(0)
  })

  it('done’s bloom cut by a hidden tab settles on the mark at scale 1', async () => {
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
    expect(lastPaint()).toEqual(restPaint('done', { r: 0x8a, g: 0x8f, b: 0x98 }))
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
    expect(paints(mark).at(-1), 'the new state was not painted').toEqual(restPaint('waiting'))
    onScreen(true)
    displayFor(200)
    expect(lastPaint()).toEqual(restPaint('waiting'))
  })

  it('an orb scrolled out 80 ms into thinking → idle shows ready’s form when it comes back', async () => {
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
    expect(lastPaint()).toEqual(restPaint('ready', { r: 0x8a, g: 0x8f, b: 0x98 }))
  })
})

describe('the bloom’s scale comes home with the next change', () => {
  it('done, then approval 65 ms later, brings the bloom’s scale home in steps no larger than the bloom’s own, and settles on the clasp at scale 1', async () => {
    // The reference: a plain bloom, and a plain morph from done to the clasp.
    const bloom = render(<AgentOrb phase="tool" />)
    displayFor(200)
    let mark = log.length
    let at = scales.length
    bloom.rerender(<AgentOrb phase="done" />)
    await settle()
    const bloomStep = largestStep(paints(mark))
    const bloomScales = scales.slice(at)
    const bloomScaleStep = Math.max(...bloomScales.slice(1).map((v, i) => Math.abs(v - bloomScales[i])))
    expect(Math.max(...bloomScales), 'the control: the bloom swelled').toBeGreaterThan(1.05)
    mark = log.length
    bloom.rerender(<AgentOrb phase="approval" />)
    await settle()
    const morphStep = largestStep(paints(mark))
    bloom.unmount()
    log = []

    const { container, rerender } = render(<AgentOrb phase="tool" />)
    displayFor(200)
    mark = log.length
    rerender(<AgentOrb phase="done" />)
    displayFor(65)
    at = scales.length
    rerender(<AgentOrb phase="approval" />)
    await settle()
    // The scale itself: home from about 1.075 in steps no larger than the bloom's own, through
    // values on the way, never set to 1 in one paint.
    const home = scales.slice(at - 1)
    const homeStep = Math.max(...home.slice(1).map((v, i) => Math.abs(v - home[i])))
    expect(homeStep, 'the scale jumped home').toBeLessThanOrEqual(bloomScaleStep * 1.05 + 1e-6)
    expect(home.filter((v) => v > 1.0001 && v < 1.0749).length, 'the scale did not pass through on its way home').toBeGreaterThanOrEqual(3)
    expect(home.at(-1)).toBe(1)
    expect(orbOf(container).dataset.orbChange).toBe('none')
    expect(lastPaint(), 'the clasp is not at scale 1').toEqual(restPaint('waiting'))
    const steps = paints(mark)
    expect(largestStep(steps), 'a stroke jumped').toBeLessThanOrEqual(Math.max(bloomStep, morphStep) * 1.05 + 0.01)
    // Springs come to rest: the change's last step is as small as a settled spring's, never the
    // scale snapping home at its end.
    expect(stepBetween(steps.at(-2)!, steps.at(-1)!), 'the scale snapped home at the end').toBeLessThan(0.05)
  })
})

describe('a phase that changes inside one state is no change of state', () => {
  it('tool → searching keeps turning steadily, with no morph', () => {
    const { container, rerender } = render(<AgentOrb phase="tool" />)
    displayFor(300)
    const mark = log.length
    rerender(<AgentOrb phase="searching" />)
    expect(orbOf(container).dataset.orbChange).toBe('none')
    displayFor(400)
    // Steady: each paint is the working form at its own instant, with no blend from the last.
    for (const p of paints(mark)) {
      const starts = startsOf(p)
      expect(starts.length).toBe(6)
    }
    const angles = paints(mark).map((p) => Math.atan2(startsOf(p)[0][0] - 10, 10 - startsOf(p)[0][1]))
    const steps = angles.slice(1).map((a, i) => { const d = (a - angles[i] + 3 * Math.PI) % (2 * Math.PI) - Math.PI; return d })
    const median = [...steps].sort((a, b) => a - b)[Math.floor(steps.length / 2)]
    expect(paints(mark).length, 'the control: the ring was painted').toBeGreaterThan(5)
    expect(Math.abs(median), 'the control: the ring turned').toBeGreaterThan(0.01)
    for (const d of steps) expect(Math.abs(d - median), 'the ring stalled or raced').toBeLessThan(Math.abs(median) * 0.35 + 0.002)
  })

  it('a phase that changes inside one state mid-morph keeps the morph going, with no snap', () => {
    const { container, rerender } = render(<AgentOrb phase="approval" />)
    displayFor(200)
    const start = log.length
    rerender(<AgentOrb phase="tool" />)
    displayFor(100)
    const mark = log.length
    rerender(<AgentOrb phase="searching" />)
    expect(orbOf(container).dataset.orbChange, 'the morph stopped').toBe('morph')
    displayFor(100)
    const after = paints(mark)
    const all = paints(start)
    const before = all.slice(0, all.length - after.length)
    expect(before.length, 'the control: the morph painted before the change').toBeGreaterThan(1)
    expect(after.length, 'the control: it painted after the change').toBeGreaterThan(0)
    expect(stepBetween(before.at(-1)!, after[0]), 'the form snapped at the change').toBeLessThanOrEqual(largestStep(before) * 1.05 + 0.01)
  })

  it('approval → input paints once, for its new badge, and asks for no frame', () => {
    const { container, rerender } = render(<AgentOrb phase="approval" />)
    displayFor(200)
    const mark = log.length
    rerender(<AgentOrb phase="input" />)
    expect(orbOf(container).dataset.orbChange).toBe('none')
    expect(displayFor(1000), 'a change inside the clasp asked for frames').toBe(0)
    expect(paints(mark).length, 'the clasp was painted more than once').toBe(1)
  })
})

describe('a morph into working keeps each stroke’s way round', () => {
  it('from a dozen start times, no stroke turns more than a quarter turn between two paints', () => {
    /** Each stroke start's angle about the centre of the 20 px box. */
    const anglesOf = (p: Entry[]) => startsOf(p).map(([x, y]) => Math.atan2(x - 10, 10 - y))
    let checked = 0
    for (const offset of [0, 37, 83, 129, 171, 233, 289, 347, 401, 463, 521, 587]) {
      log = []
      const { rerender, unmount } = render(<AgentOrb phase="approval" />)
      displayFor(200 + offset)
      const mark = log.length
      rerender(<AgentOrb phase="tool" />)
      displayFor(700)
      const list = paints(mark).map(anglesOf)
      for (let i = 1; i < list.length; i++) {
        list[i].forEach((a, j) => {
          const d = Math.abs(((a - list[i - 1][j] + 3 * Math.PI) % (2 * Math.PI)) - Math.PI)
          expect(d, `start ${offset} ms, paint ${i}, stroke ${j} flipped`).toBeLessThan(Math.PI / 2)
          checked += 1
        })
      }
      unmount()
    }
    expect(checked, 'the control: the morphs were painted').toBeGreaterThan(12 * 6 * 5)
  })
})
