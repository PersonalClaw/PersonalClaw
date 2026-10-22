/** How the agent orb's dots are painted, in chat and in the buddy: into a canvas of their own, at
 *  most `ORB_FPS` times a second while they move, and at once when the scheme moves the ink.
 *
 *  `thinking-orbs`' own component paints every frame the display draws and takes no rate, so the
 *  agent orb (`AgentOrb.tsx`) and both buddy rig candidates paint the same drawing with its engine
 *  and painter themselves, through this one loop. What moves with the dots (the buddy's pose)
 *  moves on the same clock, through `cappedDriver`, so nothing about the orb asks for a frame
 *  between two of its paints. */

import { useEffect, useState, type RefObject } from 'react'
import type { ValueTransition } from 'framer-motion'
import { onEditorTokensChange } from '../design/editorTheme'

/** The instant a still frame is drawn at: the one `thinking-orbs` paints under reduced motion. */
export const STILL_AT = 0.6

/** The most frames a second the orb's dots are painted at while they move, whatever the display
 *  offers: at 30 rather than 120 a working orb costs about a third of the CPU and of the GPU time
 *  (`docs/design/buddy/decisions/rig.md`, "The buddy's frame rate"). */
export const ORB_FPS = 30

/** An ink as the painter takes it. */
export function tintOf(ink: string | undefined): { r: number; g: number; b: number } | undefined {
  if (!ink) return undefined
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(ink.trim())
  if (hex) {
    const h = hex[1].length === 3 ? hex[1].replace(/./g, (c) => c + c) : hex[1]
    const n = parseInt(h, 16)
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 }
  }
  const fn = /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)/i.exec(ink.trim())
  return fn ? { r: Number(fn[1]), g: Number(fn[2]), b: Number(fn[3]) } : undefined
}

export interface DotLoop {
  /** Whether the dots or the pose move now; otherwise one still is painted. */
  running: boolean
  /** The most frames a second: `ORB_FPS` unless set, `null` for every frame the display draws. */
  fps?: number | null
  /** The drawing's own speed, which the time it is painted at is scaled by. */
  speed: number
  /** Reads the tokens again: called on a scheme change, before the repaint it causes. */
  retint: () => void
}

type Tick = (now: number) => void

/** One clock per rate. In each of its frames the updates run first (a pose's values), then the
 *  paints that read them, so everything at one rate shares one frame. */
interface Clock { update: Set<Tick>; paint: Set<Tick>; wait: number; raf: number; frame: object | null }
const clocks = new Map<number, Clock>()

/** Runs `tick` at most `fps` times a second until the returned stop is called. A capped clock waits
 *  out the gap on a timer and only then asks for a frame, so the page draws nothing at all between
 *  two ticks; with nothing left on it, it asks for no frame at all. */
function onTick(fps: number, stage: 'update' | 'paint', tick: Tick): () => void {
  let clock = clocks.get(fps)
  if (!clock) clocks.set(fps, (clock = { update: new Set(), paint: new Set(), wait: 0, raf: 0, frame: null }))
  const c = clock
  const idle = () => !c.update.size && !c.paint.size
  const arm = () => {
    if (c.wait || c.frame) return
    c.wait = window.setTimeout(() => {
      c.wait = 0
      // The frame this clock asked for: one it gave up on, and did not get to cancel, runs nothing.
      const frame = (c.frame = {})
      c.raf = requestAnimationFrame(() => {
        if (c.frame !== frame) return
        c.frame = null
        c.raf = 0
        const now = performance.now()
        for (const run of [...c.update]) run(now)
        for (const run of [...c.paint]) run(now)
        if (!idle()) arm()
      })
    }, 1000 / fps)
  }
  c[stage].add(tick)
  arm()
  return () => {
    c[stage].delete(tick)
    if (!idle()) return
    window.clearTimeout(c.wait)
    cancelAnimationFrame(c.raf)
    c.wait = 0
    c.raf = 0
    c.frame = null
  }
}

/** A motion-library driver on the capped clock: an animation it drives moves its value at most
 *  `fps` times a second, in the frame the dots are painted in and before they are, rather than on
 *  every frame the display draws. */
export function cappedDriver(fps: number = ORB_FPS): NonNullable<ValueTransition['driver']> {
  return (update) => {
    let stop: (() => void) | undefined
    return {
      start: () => { stop ??= onTick(fps, 'update', update) },
      stop: () => { stop?.(); stop = undefined },
      now: () => performance.now(),
    }
  }
}

/** Paints `paint` at once, and while `running` again at most `fps` times a second, on the capped
 *  clock. A scheme change repaints at once, moving or still, so the new ink shows in the frame the
 *  change lands in rather than at the next capped paint. Returns what stops it. */
export function runDots(paint: (t: number) => void, { running, fps = ORB_FPS, speed, retint }: DotLoop): () => void {
  const now = () => (running ? (performance.now() / 1000) * speed : STILL_AT)
  const stopRetint = onEditorTokensChange(() => { retint(); paint(now()) })
  paint(now())
  if (!running) return stopRetint
  if (fps) {
    const stop = onTick(fps, 'paint', () => paint(now()))
    return () => { stop(); stopRetint() }
  }
  let live = true
  let raf = 0
  const loop = () => {
    if (!live) return
    paint(now())
    raf = requestAnimationFrame(loop)
  }
  raf = requestAnimationFrame(loop)
  return () => { live = false; stopRetint(); cancelAnimationFrame(raf) }
}

/** Whether `ref`'s element is on screen: an orb scrolled out of view pauses. On screen wherever
 *  there is no way to tell. */
export function useOnScreen(ref: RefObject<Element | null>): boolean {
  const [onScreen, setOnScreen] = useState(true)
  useEffect(() => {
    const el = ref.current
    if (!el || typeof IntersectionObserver === 'undefined') return
    const io = new IntersectionObserver(([entry]) => setOnScreen(entry.isIntersecting))
    io.observe(el)
    return () => io.disconnect()
  }, [ref])
  return onScreen
}
