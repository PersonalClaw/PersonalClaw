import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { frameData } from 'framer-motion'

import type { ComposerProps } from './types'

// ── The Stop control sweeps while a turn runs, and only then ──────────────────────────────────
//
// #3807: while a turn runs, the composer's send button is the Stop control, a stop square with a
// thin sweep round it. The sweep's motion is `design/motion`'s `sweep()`: one linear turn per
// Loading sweep (`--duration-loading`), for as long as the turn runs. It is a loop, so under
// reduced motion there is no sweep at all, never a faster one, and the Stop control is still
// there and still stops the turn. The button's other kinds (send, steer, queue, processing) carry
// no sweep, and the sweep never remounts the button: a Send button that had keyboard focus is the
// Stop control once the turn starts, with the focus still on it. While the turn waits on her (an
// approval or a question) the sweep holds still, as the agent orb does, and turns on once the turn
// runs again.
//
// jsdom runs framer's animations on its timer frames, so the arc's turn is read from the
// transform framer writes, against the frame clock framer drew it by.

// The OS preference, live and flippable: `sweep()` reads it at call time.
let REDUCE = false
Object.defineProperty(window, 'matchMedia', {
  configurable: true,
  writable: true,
  value: (query: string) => ({
    get matches() { return query.includes('prefers-reduced-motion') ? REDUCE : false },
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
    onchange: null,
  }) as unknown as MediaQueryList,
})

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: new Proxy(real.api, {
      get: (target, key) => (key === 'dashboardConfig'
        ? () => Promise.resolve({ send_on_enter: true })
        : (target as Record<string | symbol, unknown>)[key]),
    }),
  }
})

const { Composer } = await import('../Composer')
const { runtime } = await import('../../design/runtime')
const { ORB_LOOKS } = await import('../AgentOrb')
const { WORK_PHASES } = await import('../../lib/workPhase')

const CONTROLS = { agent: false, model: false, approval: false, reasoning: false, attach: false, mic: false, optimize: false }
const LOADING = runtime.durationLoading

function composer(props: Partial<ComposerProps> = {}) {
  return <Composer value="" onChange={() => {}} onSend={() => {}} onStop={() => {}} controls={CONTROLS} {...props} />
}

const stopControl = () => screen.getByRole('button', { name: 'Stop' })
/** The arc, wherever the composer draws it: decoration, so it is found by its mark, not its role. */
const arcs = () => document.querySelectorAll<HTMLElement>('[data-stop-sweep-arc]')
/** The angle framer has written on the arc, in degrees (0 before its first frame). */
function angleOf(el: HTMLElement): number {
  const m = /rotate\((-?[\d.]+)deg\)/.exec(el.style.transform)
  return m ? Number(m[1]) : 0
}
/** The same angle as a turn of the circle, in [0, 360). */
const turnOf = (el: HTMLElement) => ((angleOf(el) % 360) + 360) % 360
/** How far forward the arc went from `a` to `b`, in degrees, in [0, 360). */
const forward = (a: number, b: number) => (((b - a) % 360) + 360) % 360
const sweepOf = () => stopControl().closest('[data-stop-sweep]')?.getAttribute('data-stop-sweep')
const wait = (ms: number) => act(async () => { await new Promise((r) => setTimeout(r, ms)) })
/** Wait for the arc's first drawn frame, however late a loaded machine draws it. */
async function untilTurning(arc: HTMLElement) {
  for (let i = 0; i < 150 && angleOf(arc) === 0; i++) await wait(20)
  expect(angleOf(arc), 'the arc never started turning').toBeGreaterThan(0)
}

afterEach(() => {
  cleanup()
  REDUCE = false
  runtime.durationLoading = LOADING
})

describe('while a turn runs, the Stop control carries a thin sweep', () => {
  it('draws one thin arc round the Stop control, and it is decoration only', () => {
    render(composer({ streaming: true }))
    const stop = stopControl()
    const found = arcs()
    expect(found, 'the running turn\'s Stop control has no sweep').toHaveLength(1)
    const arc = found[0]
    expect(arc.closest('[data-stop-sweep]')?.getAttribute('data-stop-sweep')).toBe('animated')
    expect(arc.closest('[data-stop-sweep]')?.contains(stop), 'the arc is not drawn on the Stop control').toBe(true)
    expect(stop.contains(arc), 'the arc is inside the button, so it is part of its name').toBe(false)
    expect(arc.getAttribute('aria-hidden')).toBe('true')
    expect(arc.className, 'the arc takes the pointer, so a click on it misses Stop').toMatch(/\bpointer-events-none\b/)

    // Thin, and a part of the circle rather than a ring: an arc that sweeps.
    const circle = arc.querySelector('circle')
    expect(circle, 'the arc draws nothing').not.toBeNull()
    expect(Number(circle!.getAttribute('stroke-width'))).toBeLessThanOrEqual(2)
    const [dash, gap] = (circle!.getAttribute('stroke-dasharray') ?? '').split(/\s+/).map(Number)
    expect(dash, 'the arc has no length').toBeGreaterThan(0)
    expect(gap, 'the arc is a whole ring, so nothing sweeps').toBeGreaterThan(dash)
  })

  it('turns at one lap per Loading sweep, and follows its slider', async () => {
    // Degrees per second of framer's own frame clock (`frameData.timestamp`, the time the arc's
    // last frame was drawn for), so a slow machine's late frames change nothing.
    const degreesPerSecond = async (lap: number) => {
      runtime.durationLoading = lap
      const { unmount } = render(composer({ streaming: true }))
      const arc = arcs()[0]
      await untilTurning(arc)
      const [t0, a0] = [frameData.timestamp, angleOf(arc)]
      await wait(250)
      const [t1, a1] = [frameData.timestamp, angleOf(arc)]
      unmount()
      const ms = t1 - t0
      expect(ms, 'no frame was drawn').toBeGreaterThan(0)
      expect(ms, 'the frames were a lap apart, so one lap cannot be told from the next').toBeLessThan(lap * 1000)
      return ((((a1 - a0) % 360) + 360) % 360) / ms * 1000
    }
    // At the default, and with the slider moved to twice the length: one lap each time.
    for (const lap of [1.4, 2.8]) {
      const rate = await degreesPerSecond(lap)
      expect(Math.abs(rate - 360 / lap) / (360 / lap), `${rate.toFixed(1)}°/s is not one lap per ${lap}s`).toBeLessThan(0.05)
    }
  })

  it('keeps turning through the page\'s re-renders: a streamed word does not restart the lap', async () => {
    // The chat page renders again for every streamed word; a sweep that restarted on each one
    // would twitch back to the top instead of turning.
    const { rerender } = render(composer({ streaming: true }))
    const arc = arcs()[0]
    await untilTurning(arc)
    await wait(200)
    const [t0, a0] = [frameData.timestamp, angleOf(arc)]
    for (let pct = 1; pct <= 5; pct++) rerender(composer({ streaming: true, contextPct: pct }))
    await wait(100)
    const [t1, a1] = [frameData.timestamp, angleOf(arc)]
    expect(arcs()[0], 'a re-render drew a new arc').toBe(arc)
    const expected = (a0 + 360 * (t1 - t0) / (runtime.durationLoading * 1000)) % 360
    const off = Math.min(Math.abs(a1 - expected), 360 - Math.abs(a1 - expected))
    expect(off, `the arc is at ${a1.toFixed(0)}°, not the ${expected.toFixed(0)}° it would have reached`).toBeLessThan(10)
  })

  it('goes with the turn: once it ends, the Send button has no sweep', () => {
    const { rerender } = render(composer({ streaming: true }))
    expect(arcs()).toHaveLength(1)
    rerender(composer({ streaming: false, value: 'next' }))
    expect(screen.getByRole('button', { name: 'Send message' })).toBeInTheDocument()
    expect(arcs(), 'the sweep outlived the turn').toHaveLength(0)
    expect(document.querySelector('[data-stop-sweep]')).toBeNull()
  })
})

describe('under reduced motion there is no sweep, and Stop still stops the turn', () => {
  it('draws no arc, says so, and the Stop control works', () => {
    REDUCE = true
    const onStop = vi.fn()
    render(composer({ streaming: true, onStop }))
    const stop = stopControl()
    expect(arcs(), 'a loop under reduced motion must not start at all').toHaveLength(0)
    // The branch is stated, so an arc dropped for some other reason cannot pass for this one.
    expect(stop.closest('[data-stop-sweep]')?.getAttribute('data-stop-sweep')).toBe('instant')
    // Positive control: rendering no Stop control answers "no sweep" for free.
    fireEvent.click(stop)
    expect(onStop).toHaveBeenCalledTimes(1)
  })
})

describe('only the Stop control sweeps', () => {
  it.each<[string, Partial<ComposerProps>, string]>([
    ['an empty draft', {}, 'Send message'],
    ['a draft ready to send', { value: 'hello' }, 'Send message'],
    ['a draft steered into the running turn', { streaming: true, value: 'hello', canQueue: true, canSteer: true }, 'Steer — send into the running turn'],
    ['a draft queued behind the running turn', { streaming: true, value: 'hello', canQueue: true }, 'Queue — sent when this turn ends'],
    ['a one-shot pass before sending', { processing: true, value: 'hello' }, 'Processing…'],
  ])('%s: no sweep', (_name, props, label) => {
    render(composer(props))
    // Positive control: the button is in the kind this row names.
    expect(screen.getByRole('button', { name: label })).toBeInTheDocument()
    expect(arcs()).toHaveLength(0)
    expect(document.querySelector('[data-stop-sweep]')).toBeNull()
  })
})

describe('the sweep never remounts the button', () => {
  it('a focused Send button is the Stop control once the turn starts, still focused, and Send again after', () => {
    const { rerender } = render(composer({ value: 'hello' }))
    const send = screen.getByRole('button', { name: 'Send message' })
    act(() => { send.focus() })
    expect(document.activeElement).toBe(send)

    rerender(composer({ value: '', streaming: true }))
    expect(arcs(), 'the turn started and the Stop control has no sweep').toHaveLength(1)
    expect(stopControl(), 'the Stop control is a new button: the sweep remounted it').toBe(send)
    expect(document.activeElement, 'the keyboard focus was lost when the turn started').toBe(send)

    rerender(composer({ value: 'next', streaming: false }))
    expect(screen.getByRole('button', { name: 'Send message' })).toBe(send)
    expect(document.activeElement, 'the keyboard focus was lost when the turn ended').toBe(send)
  })
})

// ── While the turn waits on her, the sweep holds still, as the orb does ──────────────────────────
//
// The agent orb goes still the moment the owner is needed: an approval or a question is a still
// frame, so the card that needs her holds her attention (`docs/design/agent-orb.md`). The Stop
// control reads the turn's phase the same way, from the orb's own table (`ORB_LOOKS`): its sweep
// turns exactly while the orb moves, and while the turn waits on her the arc holds still where it
// is, still drawn, and turns on from there once the turn runs again, so it never jumps to the top.
describe('while the turn waits on her, the sweep holds still, as the orb does', () => {
  it('an approval and a question are still frames of the orb', () => {
    // Positive control for the rows below, which read the same table: both waits are in it, still.
    expect(ORB_LOOKS.approval.moves).toBe(false)
    expect(ORB_LOOKS.input.moves).toBe(false)
    expect(ORB_LOOKS.tool.moves).toBe(true)
  })

  it.each([...WORK_PHASES])('%s: the sweep turns exactly when the orb moves, and the arc is drawn either way', (phase) => {
    render(composer({ streaming: true, phase }))
    expect(sweepOf()).toBe(ORB_LOOKS[phase].moves ? 'animated' : 'still')
    expect(arcs(), 'holding still took the arc away').toHaveLength(1)
  })

  it('a turn the gateway has not described yet is waiting to start, and that moves', () => {
    render(composer({ streaming: true, phase: null }))
    expect(sweepOf()).toBe('animated')
  })

  it('under reduced motion a wait draws no arc either', () => {
    REDUCE = true
    render(composer({ streaming: true, phase: 'approval' }))
    expect(arcs()).toHaveLength(0)
    expect(sweepOf()).toBe('instant')
  })

  it.each(['approval', 'input'] as const)('%s: no frame turns the arc while the turn waits, and it turns on from where it held after', async (waiting) => {
    const { rerender } = render(composer({ streaming: true, phase: 'tool' }))
    const arc = arcs()[0]
    await untilTurning(arc)
    // Held well away from the top, so an arc that started again from the top cannot pass for one
    // that went on from where it held.
    for (let i = 0; i < 300 && (turnOf(arc) < 90 || turnOf(arc) > 270); i++) await wait(5)
    const waitedAt = performance.now()
    rerender(composer({ streaming: true, phase: waiting }))
    // Read on a frame drawn after the wait began, however late a loaded machine draws it.
    for (let i = 0; i < 150 && frameData.timestamp <= waitedAt; i++) await wait(10)
    await wait(30)
    const held = turnOf(arc)
    expect(held, 'the arc was not held away from the top').toBeGreaterThan(60)
    expect(held).toBeLessThan(300)
    await wait(300)
    expect(turnOf(arc), 'the arc turned while the turn waited on her').toBe(held)
    expect(arcs()[0], 'holding still drew a new arc').toBe(arc)
    expect(sweepOf()).toBe('still')
    // The page renders again while she reads the card (the gateway says the wait again): the arc
    // stays where it held.
    rerender(composer({ streaming: true, phase: waiting, contextPct: 3 }))
    await wait(100)
    expect(turnOf(arc), 'a re-render moved the held arc').toBe(held)

    // The turn runs again: the arc goes on from where it held, at one lap per Loading sweep. Read
    // on a frame drawn after it, since a render's own write to the arc is not a drawn frame.
    const before = performance.now()
    rerender(composer({ streaming: true, phase: 'streaming' }))
    for (let i = 0; i < 150 && (frameData.timestamp <= before || turnOf(arc) === held); i++) await wait(20)
    const [t0, a0] = [frameData.timestamp, turnOf(arc)]
    expect(a0, 'the arc never turned again').not.toBe(held)
    expect(arcs()[0], 'turning again drew a new arc').toBe(arc)
    expect(sweepOf()).toBe('animated')
    const lap = runtime.durationLoading * 1000
    const due = (360 * (t0 - before)) / lap
    expect(forward(held, a0), `the arc went ${forward(held, a0).toFixed(0)}° on from where it held, more than the ${due.toFixed(0)}° the time since allows: it jumped`).toBeLessThan(due + 5)
    await wait(250)
    const [t1, a1] = [frameData.timestamp, turnOf(arc)]
    const rate = (forward(a0, a1) / (t1 - t0)) * 1000
    expect(Math.abs(rate - 360 / runtime.durationLoading) / (360 / runtime.durationLoading), `${rate.toFixed(1)}°/s is not one lap per Loading sweep`).toBeLessThan(0.05)
  })
})
