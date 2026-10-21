/**
 * The agent orb paints the drawing `thinking-orbs`' own component paints, call for call: at both its
 * sizes, in both modes, moving and still, at the display scale it caps. Drawing it through the
 * buddy's capped loop changes how often it is painted, never what is painted.
 *
 * The reference is the library's `ThinkingOrb`, handed what the orb handed it before it took the
 * loop: the phase's drawing, the size, the mode, the ink, and a still frame where the phase does not
 * move or motion is reduced. Both are painted with the clock held at one instant, into a context that
 * records every call and every style it is given, in order, and the last paint of each is compared,
 * with the canvas each was given.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import { useMemo, type ReactElement } from 'react'
import { MODE_FRAMES, STATE_TO_MODE, ThinkingOrb, type ModeFrame } from 'thinking-orbs'
import { ThemeProvider, useMode, type Mode } from '../app/theme'
import { prefersReducedMotion } from '../design/motion'
import { AgentOrb, ORB_LOOKS, STILL_AT, useInk, type AgentOrbSize } from './AgentOrb'
import { WORK_PHASES, phaseLabel, type WorkPhase } from '../lib/workPhase'

type Entry = (string | number | boolean | undefined)[]

/** A 2D context that keeps everything it is told, in order. */
function recorder(log: Entry[]): CanvasRenderingContext2D {
  const state: Record<string, unknown> = { fillStyle: '', strokeStyle: '', lineWidth: 1, globalAlpha: 1, globalCompositeOperation: 'source-over' }
  return new Proxy(state, {
    get(target, key: string) {
      if (key in target) return target[key]
      return (...args: (string | number | boolean)[]) => { log.push([key, ...args]) }
    },
    set(target, key: string, value) { target[key] = value; log.push(['=', key, value]); return true },
  }) as unknown as CanvasRenderingContext2D
}

const logs = new WeakMap<HTMLCanvasElement, Entry[]>()
const ORIGINAL = {
  getContext: HTMLCanvasElement.prototype.getContext,
  raf: window.requestAnimationFrame,
  matchMedia: window.matchMedia,
  dpr: Object.getOwnPropertyDescriptor(window, 'devicePixelRatio'),
}
let reduced = false

beforeEach(() => {
  reduced = false
  HTMLCanvasElement.prototype.getContext = function getContext(this: HTMLCanvasElement) {
    let log = logs.get(this)
    if (!log) { log = []; logs.set(this, log) }
    return recorder(log)
  } as never
  // Frames are never run: each drawing is compared at the instant it was first painted.
  Object.defineProperty(window, 'requestAnimationFrame', { configurable: true, writable: true, value: () => 1 })
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: (query: string) => ({
      get matches() { return reduced && query.includes('prefers-reduced-motion') },
      media: query, onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
    }) as unknown as MediaQueryList,
  })
  vi.spyOn(performance, 'now').mockReturnValue(12_345.678)
  document.documentElement.style.setProperty('--color-orb-alive', '#3366cc')
  document.documentElement.style.setProperty('--color-orb-rest', '#8a8f98')
})

afterEach(() => {
  vi.restoreAllMocks()
  HTMLCanvasElement.prototype.getContext = ORIGINAL.getContext
  Object.defineProperty(window, 'requestAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.raf })
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL.matchMedia })
  if (ORIGINAL.dpr) Object.defineProperty(window, 'devicePixelRatio', ORIGINAL.dpr)
  else Reflect.deleteProperty(window, 'devicePixelRatio')
  for (const name of ['--color-orb-alive', '--color-orb-rest']) document.documentElement.style.removeProperty(name)
  document.documentElement.classList.remove('light')
  localStorage.removeItem('mode')
})

/** The orb as it was drawn before the loop: the library's component, handed the orb's look. */
function Reference({ phase, size }: { phase: WorkPhase; size: AgentOrbSize }) {
  const look = ORB_LOOKS[phase]
  const { mode } = useMode()
  const ink = useInk(look.ink === 'alive' ? '--color-orb-alive' : '--color-orb-rest')
  const animate = look.moves && !prefersReducedMotion()
  const still = useMemo<ModeFrame>(() => {
    const draw = MODE_FRAMES[STATE_TO_MODE[look.drawing]]
    return (s, _t, opts) => draw(s, STILL_AT, opts)
  }, [look.drawing])
  return (
    <ThinkingOrb
      state={look.drawing} size={size} theme={mode} color={ink} paused={!animate}
      frame={animate ? undefined : still} aria-label={phaseLabel(phase)}
    />
  )
}

/** What a canvas was given, and its last paint: everything from the last reset of its transform. */
async function paintOf(ui: ReactElement, mode: Mode) {
  localStorage.setItem('mode', mode)
  const { container, unmount } = render(<ThemeProvider>{ui}</ThemeProvider>)
  await act(async () => {})
  const canvas = container.querySelector('canvas') as HTMLCanvasElement
  const log = logs.get(canvas) ?? []
  const starts = log.flatMap((e, i) => (e[0] === 'setTransform' && e[5] === 0 && e[6] === 0 ? [i] : []))
  const last = starts.length ? log.slice(starts[starts.length - 1]) : []
  const shape = {
    width: canvas.width, height: canvas.height,
    style: { width: canvas.style.width, height: canvas.style.height, display: canvas.style.display },
    role: canvas.getAttribute('role'), label: canvas.getAttribute('aria-label'),
  }
  unmount()
  return { shape, last }
}

const SIZES: AgentOrbSize[] = [20, 64]
const MODES: Mode[] = ['light', 'dark']

describe('the orb paints what the library’s component painted', () => {
  for (const motion of ['moving', 'reduced'] as const) {
    describe(motion === 'moving' ? 'with motion allowed' : 'under reduced motion', () => {
      it.each(SIZES.flatMap((size) => MODES.map((mode) => [size, mode] as const)))('at %i px, %s', async (size, mode) => {
        Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 2 })
        for (const phase of WORK_PHASES) {
          reduced = motion === 'reduced'
          const want = await paintOf(<Reference phase={phase} size={size} />, mode)
          const got = await paintOf(<AgentOrb phase={phase} size={size} />, mode)
          expect(want.last.length, `${phase}: the reference painted nothing`).toBeGreaterThan(10)
          expect(got.shape, phase).toEqual(want.shape)
          expect(got.last, `${phase} is painted differently`).toEqual(want.last)
        }
      })
    })
  }

  it.each([[1], [3]])('at a display scale of %i, with the same cap on its pixels', async (dpr) => {
    Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: dpr })
    for (const size of SIZES) {
      const want = await paintOf(<Reference phase="searching" size={size} />, 'dark')
      const got = await paintOf(<AgentOrb phase="searching" size={size} />, 'dark')
      expect(got.shape).toEqual(want.shape)
      expect(got.last).toEqual(want.last)
    }
  })
})
