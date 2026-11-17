/**
 * The agent orb paints the halftone orb (`docs/design/agent-orb.md`, "The form"): its state's frame
 * of the sphere's dots, in its ink tokens, and nothing else. No gradient, no filter, no image: the
 * default look draws no ambient light (`web/DESIGN.md`, the Glow-Is-Alive Rule). The one shadow is the
 * plate's, under the mark of done, a wait on the owner or a stop, at 64 px. What it paints is the
 * state's frame at the clock's instant while the state moves, and its still frame while it holds
 * still, under reduced motion and as a list's 12 px mark.
 *
 * Each orb is painted with the clock held at one instant, into a context that records every call and
 * every style it is given, in order, and the last paint is compared with the painter's own paint of
 * the frame the orb should be showing.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { ThemeProvider, type Mode } from '../app/theme'
import { AgentOrb, ORB_PAD, type AgentOrbSize } from './AgentOrb'
import { ORB_SCENE_OF, ORB_STAGES, ORB_STAGE_LABELS, SCENE_MOVES, markSceneOfPhase } from '../lib/orbState'
import { PLATE_SCENES } from './orb/halftone'
import { WORK_PHASES, phaseLabel } from '../lib/workPhase'
import { STILL_CLOCK, lastPaint, orbPaint, recorder, twoInks, type Entry } from '../test/orbPaint'

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

/** What a canvas was given, and its last paint. */
async function paintOf(ui: ReactElement, mode: Mode) {
  localStorage.setItem('mode', mode)
  const { container, unmount } = render(<ThemeProvider>{ui}</ThemeProvider>)
  await act(async () => {})
  const canvas = container.querySelector('canvas') as HTMLCanvasElement
  const shape = {
    width: canvas.width, height: canvas.height,
    style: { width: canvas.style.width, height: canvas.style.height },
    role: canvas.getAttribute('role'), label: canvas.getAttribute('aria-label'),
  }
  const last = lastPaint(logs.get(canvas) ?? [])
  unmount()
  return { shape, last }
}

const INKS = twoInks({ r: 0x33, g: 0x66, b: 0xcc }, { r: 0x8a, g: 0x8f, b: 0x98 })
const SIZES: AgentOrbSize[] = [12, 20, 64]
const MODES: Mode[] = ['light', 'dark']
const boxOf = (size: number) => size + Math.ceil(size * ORB_PAD) * 2

describe('the orb paints the halftone orb and nothing else', () => {
  for (const motion of ['moving', 'reduced'] as const) {
    describe(motion === 'moving' ? 'with motion allowed' : 'under reduced motion', () => {
      it.each(SIZES.flatMap((size) => MODES.map((mode) => [size, mode] as const)))('at %i px, %s', async (size, mode) => {
        Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 2 })
        for (const phase of WORK_PHASES) {
          reduced = motion === 'reduced'
          // At 12 px, the sphere's own form for the phase; at 20 and 64, its state.
          const scene = size === 12 ? markSceneOfPhase(phase) : ORB_SCENE_OF[phase]
          const got = await paintOf(<AgentOrb phase={phase} size={size} />, mode)
          const box = boxOf(size)
          expect(got.shape, phase).toEqual({
            width: box * 2, height: box * 2, style: { width: `${box}px`, height: `${box}px` }, role: 'img', label: phaseLabel(phase),
          })
          // Its frame: the state's at this instant while it moves (entered this instant), its still otherwise.
          const moving = SCENE_MOVES[scene] && !reduced && size !== 12
          const clock = moving ? { t: NOW_MS / 1000, since: 0, still: false } : STILL_CLOCK
          expect(got.last, `${phase} is painted as ${scene}`).toEqual(orbPaint({ [scene]: 1 }, { size, dpr: 2, clock, inks: INKS }))
          // Dots and short lines in the orb's inks; no gradient, no filter, no image.
          const calls = got.last.map((e) => e[0])
          expect(calls.filter((c) => c === 'arc' || c === 'lineTo').length, phase).toBeGreaterThan(0)
          for (const banned of ['createRadialGradient', 'createLinearGradient', 'createPattern', 'drawImage']) {
            expect(calls, `${phase} calls ${banned}`).not.toContain(banned)
          }
          const styles = got.last.filter((e) => e[0] === '=').map((e) => e[1])
          expect(styles, phase).not.toContain('filter')
          const inks = got.last.filter((e) => e[0] === '=' && (e[1] === 'fillStyle' || e[1] === 'strokeStyle')).map((e) => String(e[2]))
          expect(inks.some((c) => c.startsWith('rgba(51,102,204,') || c.startsWith('rgba(138,143,152,')), `${phase} draws in its inks`).toBe(true)
        }
      })
    })
  }

  it('the one shadow is the plate\'s, under a mark at 64 px', async () => {
    Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 2 })
    // With a plate ground resolved, as on a page with the design's tokens.
    document.documentElement.style.setProperty('--color-surface', '#131314')
    try {
      for (const size of SIZES) for (const phase of WORK_PHASES) {
        const got = await paintOf(<AgentOrb phase={phase} size={size} />, 'dark')
        const blurs = got.last.filter((e) => e[0] === '=' && e[1] === 'shadowBlur')
        const plated = (PLATE_SCENES as readonly string[]).includes(ORB_SCENE_OF[phase]) && size === 64
        expect(blurs.length, `${phase} at ${size} px`).toBe(plated ? 1 : 0)
      }
    } finally {
      document.documentElement.style.removeProperty('--color-surface')
    }
  })

  it.each([[1], [3]])('at a display scale of %i, its pixels are capped at twice its box', async (dpr) => {
    Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: dpr })
    for (const size of SIZES) {
      const got = await paintOf(<AgentOrb phase="searching" size={size} />, 'dark')
      expect(got.shape.width).toBe(boxOf(size) * Math.min(2, dpr))
      const clock = size === 12 ? STILL_CLOCK : { t: NOW_MS / 1000, since: 0, still: false }
      expect(got.last).toEqual(orbPaint({ [size === 12 ? 'thinking' : 'searching']: 1 }, { size, dpr: Math.min(2, dpr), clock, inks: INKS }))
    }
  })

  it('a stage is painted as its own state, under its own words', async () => {
    Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 2 })
    reduced = true
    for (const stage of ORB_STAGES) {
      const got = await paintOf(<AgentOrb phase="thinking" stage={stage} size={64} />, 'dark')
      expect(got.shape.label).toBe(ORB_STAGE_LABELS[stage])
      expect(got.last, stage).toEqual(orbPaint({ [stage]: 1 }, { size: 64, dpr: 2, clock: STILL_CLOCK, inks: INKS }))
    }
  })

  it('each state paints a still of its own at 64 and 20 px', async () => {
    Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 2 })
    reduced = true
    for (const size of [20, 64] as const) {
      const paints = new Map<string, string>()
      for (const phase of WORK_PHASES) paints.set(ORB_SCENE_OF[phase], JSON.stringify((await paintOf(<AgentOrb phase={phase} size={size} />, 'dark')).last))
      expect(new Set(paints.values()).size, `${size} px`).toBe(paints.size)
    }
  })
})
