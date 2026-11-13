/**
 * The agent orb paints the claw ring (#6117): six curved strokes with round caps, in its ink token,
 * and nothing else. No fill, no arc, no shadow, no gradient: the default look draws no ambient light
 * (`web/DESIGN.md`, the Glow-Is-Alive Rule). What it paints is the state's form at the clock's
 * instant while the state moves, and its rest form while it holds still, under reduced motion and
 * as a list's 12 px mark.
 *
 * Each orb is painted with the clock held at one instant, into a context that records every call and
 * every style it is given, in order, and the last paint is compared with the painter's own paint of
 * the form the orb should be showing.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { ThemeProvider, type Mode } from '../app/theme'
import { AgentOrb, type AgentOrbSize } from './AgentOrb'
import { ORB_STATE_OF } from '../lib/orbState'
import { MOVES, STROKES, formAt, restForm } from './orb/clawRing'
import { paintRing } from './orb/paintRing'
import { WORK_PHASES, phaseLabel } from '../lib/workPhase'

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

const logs = new WeakMap<HTMLCanvasElement, Entry[]>()
const ORIGINAL = {
  getContext: HTMLCanvasElement.prototype.getContext,
  raf: window.requestAnimationFrame,
  matchMedia: window.matchMedia,
  dpr: Object.getOwnPropertyDescriptor(window, 'devicePixelRatio'),
}
let reduced = false
const NOW_MS = 12_345.678

beforeEach(() => {
  reduced = false
  HTMLCanvasElement.prototype.getContext = function getContext(this: HTMLCanvasElement) {
    let log = logs.get(this)
    if (!log) { log = []; logs.set(this, log) }
    return recorder(log)
  } as never
  // Frames are never run: each orb is compared at the instant it was first painted.
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
  vi.spyOn(performance, 'now').mockReturnValue(NOW_MS)
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

/** A canvas's last paint: everything from the last reset of its transform. */
const lastPaint = (log: Entry[]) => {
  const starts = log.flatMap((e, i) => (e[0] === 'setTransform' && e[5] === 0 && e[6] === 0 ? [i] : []))
  return starts.length ? log.slice(starts[starts.length - 1]) : []
}

/** What a canvas was given, and its last paint. */
async function paintOf(ui: ReactElement, mode: Mode) {
  localStorage.setItem('mode', mode)
  const { container, unmount } = render(<ThemeProvider>{ui}</ThemeProvider>)
  await act(async () => {})
  const canvas = container.querySelector('canvas') as HTMLCanvasElement
  const shape = {
    width: canvas.width, height: canvas.height,
    style: { width: canvas.style.width, height: canvas.style.height, display: canvas.style.display },
    role: canvas.getAttribute('role'), label: canvas.getAttribute('aria-label'),
  }
  const last = lastPaint(logs.get(canvas) ?? [])
  unmount()
  return { shape, last }
}

const INK = { alive: { r: 0x33, g: 0x66, b: 0xcc }, rest: { r: 0x8a, g: 0x8f, b: 0x98 } }
const SIZES: AgentOrbSize[] = [12, 20, 64]
const MODES: Mode[] = ['light', 'dark']

describe('the orb paints the claw ring and nothing else', () => {
  for (const motion of ['moving', 'reduced'] as const) {
    describe(motion === 'moving' ? 'with motion allowed' : 'under reduced motion', () => {
      it.each(SIZES.flatMap((size) => MODES.map((mode) => [size, mode] as const)))('at %i px, %s', async (size, mode) => {
        Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 2 })
        for (const phase of WORK_PHASES) {
          reduced = motion === 'reduced'
          const look = ORB_STATE_OF[phase]
          const got = await paintOf(<AgentOrb phase={phase} size={size} />, mode)
          expect(got.shape, phase).toEqual({
            width: size * 2, height: size * 2,
            style: { width: `${size}px`, height: `${size}px`, display: 'block' },
            role: 'img', label: phaseLabel(phase),
          })

          // Its form: the state's at this instant while it moves, its rest form otherwise.
          const moving = MOVES[look.state] && !reduced && size !== 12
          const form = moving ? formAt(look.state, NOW_MS / 1000) : restForm(look.state)
          const want: Entry[] = []
          paintRing(recorder(want), form, { size, dpr: 2, ink: INK[look.ink], scale: 1 })
          expect(got.last, `${phase} is painted as its form`).toEqual(want)

          // Six strokes, each one curve with round caps, in the token's ink; no fill and no light.
          const calls = got.last.map((e) => e[0])
          expect(calls.filter((c) => c === 'stroke'), phase).toHaveLength(STROKES)
          expect(calls.filter((c) => c === 'quadraticCurveTo'), phase).toHaveLength(STROKES)
          for (const banned of ['fill', 'fillRect', 'arc', 'createRadialGradient', 'createLinearGradient', 'drawImage']) {
            expect(calls, `${phase} calls ${banned}`).not.toContain(banned)
          }
          const styles = got.last.filter((e) => e[0] === '=').map((e) => e[1])
          expect(styles, phase).not.toContain('shadowBlur')
          expect(styles, phase).not.toContain('filter')
          const { r, g, b } = INK[look.ink]
          expect(got.last, phase).toContainEqual(['=', 'strokeStyle', `rgb(${r}, ${g}, ${b})`])
          expect(got.last, phase).toContainEqual(['=', 'lineCap', 'round'])
        }
      })
    })
  }

  it.each([[1], [3]])('at a display scale of %i, its pixels are capped at twice its box', async (dpr) => {
    Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: dpr })
    for (const size of SIZES) {
      const got = await paintOf(<AgentOrb phase="searching" size={size} />, 'dark')
      expect(got.shape.width).toBe(size * Math.min(2, dpr))
      const want: Entry[] = []
      const form = size === 12 ? restForm('working') : formAt('working', NOW_MS / 1000)
      paintRing(recorder(want), form, { size, dpr: Math.min(2, dpr), ink: INK.alive, scale: 1 })
      expect(got.last).toEqual(want)
    }
  })

  it('each state paints a form of its own at every size', async () => {
    Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 2 })
    reduced = true
    for (const size of SIZES) {
      const paints = new Map<string, string>()
      for (const phase of ['idle', 'thinking', 'tool', 'approval', 'done'] as const) {
        paints.set(ORB_STATE_OF[phase].state, JSON.stringify((await paintOf(<AgentOrb phase={phase} size={size} />, 'dark')).last.filter((e) => e[0] !== '=' || e[1] !== 'strokeStyle')))
      }
      expect(new Set(paints.values()).size, `${size} px`).toBe(5)
    }
  })
})
