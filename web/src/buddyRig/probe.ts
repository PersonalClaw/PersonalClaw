/** What the measurement window reads from the rig harness (#3887): `window.__buddyRig`.
 *
 *  The window's script (`docs/design/buddy/decisions/rig-window.cjs`) drives the harness through the
 *  same controls the owner uses, and reads three things only the page can see: how many frames a
 *  scheme change takes to reach the painted dots, how far a pose's values overshoot and travel at
 *  each end of the dials, and whether any value passes through a value between two poses under
 *  reduced motion. Frames are counted by the window itself, before any of the app's code runs. */

import { computedToken } from '../design/editorTheme'
import { POSE_KEYS, poseOf, type PoseKey, type RigPhase } from './poses'
import type { RigCandidate } from './route'
import type { PoseHandle } from './usePose'

export interface RigProbe {
  ready: true
  show: (phase: RigPhase) => void
  card: (open: boolean) => void
  loop: (on: boolean, stepMs?: number) => void
  dials: (bounciness: number, expressiveness: number) => void
  scheme: (id: string) => void
  state: () => Record<string, unknown>
  /** Applies scheme `to` and counts the frames until each candidate's painted dots take its hue.
   *  Asked in a pose whose dots are all in the alive ink: moving, or a still under reduced motion. */
  retint: (to: string, frames?: number) => Promise<Record<string, unknown>>
  dial: (from: RigPhase, to: RigPhase, key: PoseKey, ms?: number) => Promise<Record<string, unknown>>
  /** Counts each pose value's changes from now: a jump is one change, a move is many. */
  countChanges: () => () => Record<string, number>
}

declare global {
  interface Window { __buddyRig?: RigProbe }
}

const nextFrame = () => new Promise<number>((resolve) => requestAnimationFrame(resolve))
const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

/** The hue, in degrees, of an `r, g, b` colour, or null for a grey. */
export function hueOf(r: number, g: number, b: number): number | null {
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const d = max - min
  if (d < 12) return null
  const h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4
  return (h * 60 + 360) % 360
}

/** The distance between two hues around the wheel. */
export const hueDistance = (a: number, b: number) => Math.min(Math.abs(a - b), 360 - Math.abs(a - b))

/** The hue of a token's resolved colour. */
export function tokenHue(token: string): number | null {
  const value = computedToken(token).trim()
  const ctx = document.createElement('canvas').getContext('2d')
  if (!ctx || !value) return null
  ctx.fillStyle = value
  ctx.fillRect(0, 0, 1, 1)
  const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data
  return hueOf(r, g, b)
}

/** The hue of what a canvas has painted: the mean of its opaque pixels. */
export function canvasHue(canvas: HTMLCanvasElement): number | null {
  const ctx = canvas.getContext('2d')
  if (!ctx || !canvas.width) return null
  const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height)
  let r = 0, g = 0, b = 0, n = 0
  for (let i = 0; i < data.length; i += 4) {
    if (data[i + 3] < 96) continue
    r += data[i]; g += data[i + 1]; b += data[i + 2]; n++
  }
  return n ? hueOf(r / n, g / n, b / n) : null
}

/** How far a move travelled and overshot: `samples` of one value from `start` toward `target`. */
export function travel(samples: readonly number[], start: number, target: number): { amplitude: number; overshoot: number } {
  const span = target - start
  if (span === 0) return { amplitude: 0, overshoot: 0 }
  const sign = Math.sign(span)
  const peak = Math.max(...samples.map((v) => (v - target) * sign))
  return { amplitude: Math.abs(span), overshoot: Math.max(0, peak) / Math.abs(span) }
}

interface Controls {
  show: (phase: RigPhase) => void
  card: (open: boolean) => void
  loop: (on: boolean, stepMs?: number) => void
  dials: (bounciness: number, expressiveness: number) => void
  scheme: (id: string) => void
  state: () => Record<string, unknown>
  poses: () => Partial<Record<RigCandidate, PoseHandle>>
}

/** Waits until no candidate's pose is moving, or `ms` passes. */
async function settled(controls: Controls, ms = 3000): Promise<void> {
  const until = performance.now() + ms
  while (performance.now() < until) {
    if (Object.values(controls.poses()).every((p) => !p?.moving)) return
    await wait(50)
  }
}

export function makeProbe(controls: Controls): RigProbe {
  return {
    ready: true,
    show: controls.show,
    card: controls.card,
    loop: controls.loop,
    dials: controls.dials,
    scheme: controls.scheme,
    state: controls.state,

    async retint(to, frames = 30) {
      const canvases = [...document.querySelectorAll<HTMLElement>('[data-rig-candidate]')]
        .map((el) => ({ candidate: el.dataset.rigCandidate!, token: el.dataset.rigInkToken!, canvas: el.querySelector('canvas')! }))
      const before = Object.fromEntries(canvases.map((c) => [c.candidate, canvasHue(c.canvas)]))
      const root = document.documentElement
      const was = root.dataset.theme
      if (was === to) return { from: was, to, error: 'the scheme is already the one asked for' }
      const flipped = new Promise<void>((resolve) => {
        const mo = new MutationObserver(() => { if (root.dataset.theme !== was) { mo.disconnect(); resolve() } })
        mo.observe(root, { attributes: true, attributeFilter: ['data-theme'] })
      })
      controls.scheme(to)
      await flipped
      const expected = Object.fromEntries(canvases.map((c) => [c.candidate, tokenHue(c.token)]))
      const followed: Record<string, number | null> = Object.fromEntries(canvases.map((c) => [c.candidate, null]))
      const after: Record<string, number | null> = {}
      for (let n = 1; n <= frames && Object.values(followed).some((v) => v === null); n++) {
        await nextFrame()
        for (const c of canvases) {
          const hue = canvasHue(c.canvas)
          after[c.candidate] = hue
          const want = expected[c.candidate]
          if (followed[c.candidate] === null && hue !== null && want !== null && hueDistance(hue, want) < 15) followed[c.candidate] = n
        }
      }
      return { from: was, to, before, after, expected, framesToFollow: followed }
    },

    async dial(from, to, key, ms = 1500) {
      controls.show(from)
      await settled(controls)
      await wait(200)
      const poses = controls.poses()
      const start = Object.fromEntries(Object.entries(poses).map(([c, p]) => [c, p!.values[key].get()]))
      const samples: Record<string, number[]> = Object.fromEntries(Object.keys(poses).map((c) => [c, []]))
      controls.show(to)
      const until = performance.now() + ms
      while (performance.now() < until) {
        await nextFrame()
        for (const [c, p] of Object.entries(controls.poses())) samples[c].push(p!.values[key].get())
      }
      const target = (c: string) => (key === 'swirl' ? start[c] + poseOf(to).swirl : poseOf(to)[key])
      return Object.fromEntries(Object.keys(poses).map((c) => [c, { start: start[c], target: target(c), frames: samples[c].length, ...travel(samples[c], start[c], target(c)) }]))
    },

    countChanges() {
      const counts: Record<string, number> = {}
      const stops: (() => void)[] = []
      for (const [c, p] of Object.entries(controls.poses())) {
        for (const key of POSE_KEYS) {
          const name = `${c}.${key}`
          counts[name] = 0
          stops.push(p!.values[key].on('change', () => { counts[name] += 1 }))
        }
      }
      return () => { for (const stop of stops) stop(); return counts }
    },
  }
}
