/** How both rig candidates (#3887) paint the agent orb's dots in a canvas of their own: at most at
 *  the buddy's frame rate, and at once when the scheme moves the ink.
 *
 *  The agent orb's component paints every frame the display draws and takes no rate, so each
 *  candidate paints the same drawing with the same engine and painter itself (`thinking-orbs`). Only
 *  the candidates import this, so its size is counted with theirs. */

import { onEditorTokensChange } from '../design/editorTheme'
import { STILL_AT } from '../ui/AgentOrb'
import { BUDDY_FPS } from './poses'

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
  /** The most frames a second: the buddy's unless set, `null` for every frame the display draws. */
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
export function runDots(paint: (t: number) => void, { running, fps = BUDDY_FPS, speed, retint }: DotLoop): () => void {
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
