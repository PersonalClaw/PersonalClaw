/** What the orb paints, for its tests: a 2D context that records every call and every style it is
 *  given, in order, and the paint the orb should have made for a state, made the way it makes one
 *  (`ui/AgentOrb.tsx`): a reset of the transform, a clear, the pixel ratio's transform, then the
 *  halftone frame (`ui/orb/halftone.ts`). Comparing the two says the orb drew exactly that state. */
import { ORB_PAD } from '../ui/AgentOrb'
import { drawFrame, type Clock, type Inks, type Rgb, type Weights } from '../ui/orb/halftone'
import { SCENES } from '../ui/orb/scenes'

export type Entry = (string | number | boolean | undefined)[]

/** A 2D context that keeps everything it is told, in order. */
export function recorder(log: Entry[]): CanvasRenderingContext2D {
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

/** The paints in a log, each from its reset of the transform. */
export function paintsIn(log: Entry[], from = 0): Entry[][] {
  const out: Entry[][] = []
  for (let i = from; i < log.length; i++) {
    const e = log[i]
    // A paint resets the transform and clears; the pixel ratio's transform after it is part of it.
    if (e[0] === 'setTransform' && e[1] === 1 && e[5] === 0 && e[6] === 0 && log[i + 1]?.[0] === 'clearRect') out.push([])
    if (out.length) out[out.length - 1].push(e)
  }
  return out
}
/** A log's last paint. */
export const lastPaint = (log: Entry[]): Entry[] => paintsIn(log).at(-1) ?? []

/** The instant a still frame is drawn at. */
export const STILL_CLOCK: Clock = { t: 0.9, since: 1e6, still: true }

/** The inks a test page resolves when it sets only the two orb tokens: every status tone falls back
 *  to the alive ink, the plate has no ground, and its hairline is the rest ink. */
export function twoInks(alive: Rgb, rest: Rgb): Inks {
  return { alive, rest, ok: alive, warn: alive, info: alive, danger: alive, plate: undefined, hair: rest, dark: true }
}

/** The paint the orb makes of `weights` at `clock`, in a box of `size` at pixel ratio `dpr`. */
export function orbPaint(weights: Weights, { size, dpr, clock, inks, bloom = false }: { size: number; dpr: number; clock: Clock; inks: Inks; bloom?: boolean }): Entry[] {
  const log: Entry[] = []
  const ctx = recorder(log)
  const pad = Math.ceil(size * ORB_PAD), px = Math.round((size + pad * 2) * dpr)
  ctx.setTransform(1, 0, 0, 1, 0, 0)
  ctx.clearRect(0, 0, px, px)
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  drawFrame(ctx, weights, clock, inks, SCENES, { size, x: pad, y: pad, bloom })
  return log
}
