/** How the agent orb's dots are painted, in chat and in the buddy: into a canvas of their own, at
 *  most `ORB_FPS` times a second while they move, and at once when the scheme moves the ink.
 *
 *  `thinking-orbs`' own component paints every frame the display draws and takes no rate, so the
 *  agent orb (`AgentOrb.tsx`) and both buddy rig candidates paint the same drawing with its engine
 *  and painter themselves, through this one loop. */

import { useEffect, useState, type RefObject } from 'react'
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

/** Paints `paint` at once, and while `running` again at most `fps` times a second. A capped loop
 *  waits out the gap on a timer and only then asks for a frame, so the page draws nothing at all
 *  between two paints. A scheme change repaints at once, moving or still, so the new ink shows in
 *  the frame the change lands in rather than at the next capped paint. Returns what stops it. */
export function runDots(paint: (t: number) => void, { running, fps = ORB_FPS, speed, retint }: DotLoop): () => void {
  const now = () => (running ? (performance.now() / 1000) * speed : STILL_AT)
  const stopRetint = onEditorTokensChange(() => { retint(); paint(now()) })
  paint(now())
  if (!running) return stopRetint
  let live = true
  let raf = 0
  let wait = 0
  const next = () => {
    if (!live) return
    if (fps) wait = window.setTimeout(() => { if (live) raf = requestAnimationFrame(loop) }, 1000 / fps)
    else raf = requestAnimationFrame(loop)
  }
  const loop = () => {
    if (!live) return
    paint(now())
    next()
  }
  next()
  return () => { live = false; stopRetint(); cancelAnimationFrame(raf); window.clearTimeout(wait) }
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
