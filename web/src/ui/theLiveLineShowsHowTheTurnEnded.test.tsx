/**
 * The turn's live line shows how the turn ended, then goes (#6117). When a turn's last frame says it
 * ended, the line keeps showing, with the ending's phase and line: done, with its one bloom only
 * when the turn completed, or a failure with its × and no bloom. The clock stops, the turning word
 * goes, Esc no longer presses Stop, and the live region empties, so nothing new is said, since the
 * transcript's narration says how the turn ended. The line holds until the orb's change has
 * settled, then `MIN_HOLD_MS` more, then fades over the Medium length and goes; under reduced motion
 * the ending's still form holds `MIN_HOLD_MS` and goes with no fade. A turn that starts inside the
 * hold takes the line back. The history peek's head waits on its orb the same way, so it is fully
 * formed before it fades.
 *
 * The orb really morphs and blooms here: the 2D context records every paint, the display is a loop
 * that runs the frames asked for at each refresh, and the page's timers and clock are faked.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { TurnStatusRow, SHOW_DELAY_MS } from './TurnStatusRow'
import { MIN_HOLD_MS, fadeMs } from './turnEnding'
import { runtime } from '../design/runtime'
import { TurnHead } from '../pages/chat/transcript/TurnHead'
import { restForm } from './orb/clawRing'
import { paintRing } from './orb/paintRing'

type Entry = (string | number | boolean | undefined)[]

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

const T0 = Date.UTC(2026, 10, 12, 9, 0, 0)
let log: Entry[] = []
let frames: FrameRequestCallback[] = []
let reduced = false
const ORIGINAL = { getContext: HTMLCanvasElement.prototype.getContext, raf: window.requestAnimationFrame, caf: window.cancelAnimationFrame, matchMedia: window.matchMedia }

beforeEach(() => {
  vi.useFakeTimers({ now: T0, toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'performance', 'Date'] })
  log = []
  frames = []
  reduced = false
  HTMLCanvasElement.prototype.getContext = (() => recorder(log)) as never
  Object.defineProperty(window, 'requestAnimationFrame', { configurable: true, writable: true, value: (cb: FrameRequestCallback) => frames.push(cb) })
  Object.defineProperty(window, 'cancelAnimationFrame', { configurable: true, writable: true, value: () => {} })
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: (query: string) => ({
      get matches() { return reduced && query.includes('prefers-reduced-motion') },
      media: query, onchange: null, addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
    }) as unknown as MediaQueryList,
  })
  document.documentElement.style.setProperty('--color-orb-alive', '#3366cc')
  document.documentElement.style.setProperty('--color-orb-rest', '#8a8f98')
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  HTMLCanvasElement.prototype.getContext = ORIGINAL.getContext
  Object.defineProperty(window, 'requestAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.raf })
  Object.defineProperty(window, 'cancelAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.caf })
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL.matchMedia })
  for (const name of ['--color-orb-alive', '--color-orb-rest']) document.documentElement.style.removeProperty(name)
})

/** One refresh of a 60 Hz display: the timers due fire, then every frame asked for, then the
 *  promises a settled change resolves. */
async function refresh(): Promise<void> {
  await act(async () => {
    vi.advanceTimersByTime(1000 / 60)
    for (const frame of frames.splice(0)) frame(performance.now())
  })
}
/** The display run for `ms`, calling `each` after every refresh. */
async function displayFor(ms: number, each: () => void = () => {}): Promise<void> {
  for (let i = 0; i < Math.round((ms * 60) / 1000); i++) { await refresh(); each() }
}
const line = () => document.querySelector<HTMLElement>('[data-turn-status]')
const head = () => document.querySelector<HTMLElement>('[data-turn-head]')
const orb = () => document.querySelector<HTMLElement>('[data-agent-orb]')
const live = () => screen.getByRole('status').textContent
/** The display run until the orb's change has settled, calling `each` after every refresh. */
async function untilSettled(each: () => void = () => {}): Promise<number> {
  let ms = 0
  while (orb()?.dataset.orbChange !== 'none' && ms < 3000) { await refresh(); each(); ms += 1000 / 60 }
  expect(orb()?.dataset.orbChange, 'the change never settled').toBe('none')
  return ms
}

type RowProps = Parameters<typeof TurnStatusRow>[0]
const props = (over: Partial<RowProps> = {}): RowProps => ({ running: true, phase: 'streaming', line: '', startedAt: T0, usage: null, onStop: () => {}, ending: null, ...over })
type Outcome = 'complete' | 'stopped' | 'error' | 'interrupted'
const ended = (phase: 'done' | 'error', words: string, outcome: Outcome = phase === 'done' ? 'complete' : 'error'): Partial<RowProps> =>
  ({ running: false, phase: null, line: '', ending: { phase, line: words, outcome } })

describe('the live line holds how the turn ended', () => {
  it('done after a completed turn: its one bloom plays on the line, which holds while it settles and MIN_HOLD_MS more, then fades and goes', async () => {
    const { rerender } = render(<TurnStatusRow {...props()} />)
    await displayFor(SHOW_DELAY_MS + 300)
    expect(line(), 'the control: the line shows while the turn runs').not.toBeNull()
    rerender(<TurnStatusRow {...props(ended('done', 'Done in 12 seconds'))} />)
    expect(line()?.dataset.phase).toBe('done')
    expect(line()?.querySelector('[data-turn-state]')?.textContent).toBe('Done in 12 seconds')
    await refresh()
    expect(orb()?.dataset.orbChange, 'done did not bloom').toBe('bloom')
    const took = await untilSettled(() => expect(line(), 'the line went before done had settled').not.toBeNull())
    expect(took, 'the bloom was cut short').toBeGreaterThan(200)
    await displayFor(MIN_HOLD_MS - 50)
    expect(line()?.dataset.turnStage).toBe('ending')
    expect(line()?.style.opacity).not.toBe('0')
    await displayFor(100)
    expect(line()?.dataset.turnStage).toBe('leaving')
    expect(line()?.style.opacity).toBe('0')
    await displayFor(fadeMs() + 50)
    expect(line(), 'the line stayed after its fade').toBeNull()
  })

  it('error: its × shows with no bloom, and the line holds and goes the same way', async () => {
    const { rerender } = render(<TurnStatusRow {...props({ phase: 'tool', line: 'Search the web' })} />)
    await displayFor(SHOW_DELAY_MS + 300)
    rerender(<TurnStatusRow {...props(ended('error', 'Stopped: the model refused the request'))} />)
    expect(line()?.querySelector('[data-turn-state]')?.textContent).toBe('Stopped: the model refused the request')
    expect(line()?.querySelector('[data-orb-badge]')?.textContent).toBe('×')
    await untilSettled(() => expect(orb()?.dataset.orbChange, 'a failure bloomed').not.toBe('bloom'))
    await displayFor(MIN_HOLD_MS + 50)
    expect(line()?.dataset.turnStage).toBe('leaving')
    await displayFor(fadeMs() + 50)
    expect(line()).toBeNull()
  })

  it('while it holds, the clock stops, the turning word goes, Esc no longer stops the turn, and nothing new is said', async () => {
    const onStop = vi.fn()
    const { rerender } = render(<TurnStatusRow {...props({ phase: 'tool', line: 'Search the web', onStop })} />)
    await displayFor(2950)
    const before = live()
    expect(before, 'the control: the running line is said').toBe('Search the web')
    expect(document.querySelector('[data-turn-word]'), 'the control: a word turns while it works').not.toBeNull()
    const at = line()!.querySelector('[data-turn-elapsed]')!.textContent
    rerender(<TurnStatusRow {...props({ ...ended('done', 'Done in 3 seconds'), onStop })} />)
    await displayFor(200)
    expect(line()?.dataset.turnStage).toBe('ending')
    expect(line()!.querySelector('[data-turn-elapsed]')!.textContent, 'the clock went on').toBe(at)
    expect(document.querySelector('[data-turn-word]'), 'a word still turns').toBeNull()
    // The region empties: removing its words says nothing, and nothing stale stays to be read.
    expect(live(), 'the region still says the running words').toBe('')
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(onStop, 'Esc stopped a turn that had ended').not.toHaveBeenCalled()
  })

  it.each([
    ['stopped', 'Stopped'],
    ['error', 'Claude stopped after you denied shell.'],
  ] as const)('a turn that ended %s holds done’s form with no bloom: only a completed turn blooms', async (outcome, words) => {
    const { rerender } = render(<TurnStatusRow {...props()} />)
    await displayFor(SHOW_DELAY_MS + 300)
    rerender(<TurnStatusRow {...props(ended('done', words, outcome))} />)
    expect(line()?.querySelector('[data-turn-state]')?.textContent).toBe(words)
    expect(orb()?.dataset.orbState).toBe('done')
    const seen: string[] = []
    await displayFor(1500, () => { if (orb()) seen.push(orb()!.dataset.orbChange ?? '') })
    expect(seen, 'the control: the orb was watched').not.toEqual([])
    expect(seen.filter((c) => c === 'bloom'), `a turn that ended ${outcome} bloomed`).toEqual([])
  })

  it('the fade lasts the Medium length the owner set, not a fixed one', async () => {
    runtime.durationMedium = 800
    try {
      const { rerender } = render(<TurnStatusRow {...props()} />)
      await displayFor(SHOW_DELAY_MS + 300)
      rerender(<TurnStatusRow {...props(ended('done', 'Done in 1 second'))} />)
      await untilSettled()
      await displayFor(MIN_HOLD_MS + 50)
      expect(line()?.dataset.turnStage).toBe('leaving')
      await displayFor(600)
      expect(line(), 'the line went before its fade ended').not.toBeNull()
      await displayFor(300)
      expect(line(), 'the line stayed after its fade').toBeNull()
    } finally { runtime.durationMedium = 300 }
  })

  it('under reduced motion, the ending’s still form holds MIN_HOLD_MS and goes with no fade', async () => {
    reduced = true
    const { rerender } = render(<TurnStatusRow {...props()} />)
    await displayFor(SHOW_DELAY_MS + 300)
    rerender(<TurnStatusRow {...props(ended('done', 'Done in 1 second'))} />)
    expect(orb()?.dataset.agentOrb).toBe('still')
    expect(orb()?.dataset.orbChange).toBe('none')
    const opacities: string[] = []
    await displayFor(MIN_HOLD_MS - 50, () => { if (line()) opacities.push(line()!.style.opacity) })
    expect(line(), 'the ending did not hold').not.toBeNull()
    await displayFor(100, () => { if (line()) opacities.push(line()!.style.opacity) })
    expect(line(), 'the ending held too long').toBeNull()
    expect(opacities.filter((o) => o === '0'), 'it faded').toEqual([])
  })

  it('a turn that starts inside the hold takes the line back, and its orb comes home to scale 1', async () => {
    const { rerender } = render(<TurnStatusRow {...props()} />)
    await displayFor(SHOW_DELAY_MS + 300)
    rerender(<TurnStatusRow {...props(ended('done', 'Done in 1 second'))} />)
    await displayFor(100)
    expect(orb()?.dataset.orbChange, 'the control: the bloom is under way').toBe('bloom')
    rerender(<TurnStatusRow {...props({ phase: 'queued' })} />)
    expect(line()?.dataset.phase).toBe('queued')
    expect(line()?.dataset.turnStage).toBe('shown')
    expect(line()?.querySelector('[data-turn-state]')?.textContent).toBe('Waiting to start')
    rerender(<TurnStatusRow {...props({ phase: 'approval' })} />)
    await untilSettled()
    const want: Entry[] = []
    paintRing(recorder(want), restForm('waiting'), { size: 20, dpr: 1, ink: { r: 0x33, g: 0x66, b: 0xcc }, scale: 1 })
    const start = log.map((e, i) => (e[0] === 'setTransform' && e[5] === 0 && e[6] === 0 ? i : -1)).filter((i) => i >= 0).at(-1)!
    expect(log.slice(start), 'the clasp is not at scale 1').toEqual(want)
    await displayFor(2000)
    expect(line(), 'the new turn’s line went').not.toBeNull()
  })

  it('the ending keeps the turn’s clock and its tokens, though its last frame names no turn', async () => {
    // Opened 4 minutes into the turn: its clock counts from when it began, not from when the page saw it.
    const began = T0 - 240_000
    const { rerender } = render(<TurnStatusRow {...props({ phase: 'tool', line: 'Search the web', startedAt: began, usage: { input: 1200, output: 34 } })} />)
    await displayFor(2000)
    const clock = () => line()!.querySelector('[data-turn-elapsed]')!.textContent
    const tokens = () => line()!.querySelector('[data-turn-tokens]')?.textContent ?? null
    const at = clock()
    expect(at, 'the control: the clock counts from the turn’s start').toMatch(/^4m/)
    expect(tokens(), 'the control: the tokens show').toBe('1,200 in · 34 out')
    // As the chat passes it: the last frame names no turn, so no start and no usage.
    rerender(<TurnStatusRow {...props({ ...ended('done', 'Done in 4 minutes'), startedAt: null, usage: null })} />)
    await displayFor(100)
    expect(line()?.dataset.turnStage).toBe('ending')
    expect(clock(), 'the ending recounted its clock').toBe(at)
    expect(tokens(), 'the ending dropped its tokens').toBe('1,200 in · 34 out')
  })

  it('a turn that ends before it shows leaves no clock behind for the next', async () => {
    const { rerender } = render(<TurnStatusRow {...props({ startedAt: null })} />)
    await displayFor(100)
    rerender(<TurnStatusRow {...props({ ...ended('done', 'Done in 1 second'), startedAt: null })} />)
    await displayFor(5000)
    expect(line(), 'the control: a turn under the show delay never shows').toBeNull()
    rerender(<TurnStatusRow {...props({ startedAt: null })} />)
    await displayFor(SHOW_DELAY_MS + 100)
    expect(line()!.querySelector('[data-turn-elapsed]')!.textContent, 'the next turn counted from the last one').toBe('0s')
  })

  it('a turn that ends with no ending said still goes at once, as before', async () => {
    const { rerender } = render(<TurnStatusRow {...props()} />)
    await displayFor(SHOW_DELAY_MS + 300)
    rerender(<TurnStatusRow {...props({ running: false, phase: null })} />)
    expect(line()).toBeNull()
  })
})

describe('the history peek’s head waits on its orb', () => {
  it('done’s bloom settles before the head fades', async () => {
    const { rerender } = render(<TurnHead running phase="streaming" line="" ending={null} />)
    await displayFor(SHOW_DELAY_MS + MIN_HOLD_MS)
    rerender(<TurnHead running={false} phase={null} line="" ending={{ phase: 'done', line: 'Done in 4 seconds', outcome: 'complete' }} />)
    await refresh()
    expect(orb()?.dataset.orbChange, 'done did not bloom').toBe('bloom')
    await untilSettled(() => expect(head()?.dataset.turnHeadStage, 'the head faded before done had settled').toBe('shown'))
    await displayFor(MIN_HOLD_MS - 50)
    expect(head()?.dataset.turnHeadStage).toBe('shown')
    await displayFor(100)
    expect(head()?.dataset.turnHeadStage).toBe('leaving')
  })

  it('a turn the peek sent, stopped before its send is done, closes to done with no bloom', async () => {
    const { rerender } = render(<TurnHead running phase="streaming" line="" ending={null} />)
    await displayFor(SHOW_DELAY_MS + MIN_HOLD_MS)
    // Still running as the peek sees it (its own send has no chat_done yet), with no phase and the ending said.
    rerender(<TurnHead running phase={null} line="" ending={{ phase: 'done', line: 'Stopped', outcome: 'stopped' }} />)
    expect(orb()?.dataset.orbState).toBe('done')
    const seen: string[] = []
    await displayFor(1500, () => { if (orb()) seen.push(orb()!.dataset.orbChange ?? '') })
    expect(seen, 'the control: the orb was watched').not.toEqual([])
    expect(seen.filter((c) => c === 'bloom'), 'a stopped turn bloomed').toEqual([])
  })
})
