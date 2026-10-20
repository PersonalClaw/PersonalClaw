/** Rig candidate 1 (#3887): the agent orb's drawing extended in place.
 *
 *  Everything is drawn into one canvas with the agent orb's own engine and painter: the phase's
 *  drawing with the pose's density and arrangement, the whole orb's rise and fall inside the frame,
 *  and the badge and the wash drawn with the dots, in the alert's tone. One frame loop paints all of
 *  it, and runs only while the dots move or the pose does (`docs/design/buddy/rig.md`,
 *  "Candidates"). */

import { useEffect, useMemo, useRef } from 'react'
import { paintFrame, resolvePreset, type Dot } from 'thinking-orbs/engine'
import { useMode } from '../app/theme'
import { onEditorTokensChange } from '../design/editorTheme'
import { STILL_AT, inkOf } from '../ui/AgentOrb'
import { modeOf, poseFrame } from './arrange'
import { useMayMove, useOnScreen, useRigInks, type CandidateProps } from './candidate'
import { poseOf, rigLook, tunedDrawingFor, type BadgeMark } from './poses'
import { usePose } from './usePose'

/** The badge's marks as dots: rows of a small matrix, 1 where a dot sits. */
const GLYPHS: Record<BadgeMark, readonly string[]> = {
  working: ['10101'],
  approval: ['010', '010', '010', '000', '010'],
  input: ['110', '001', '010', '000', '010'],
  error: ['101', '010', '101'],
  done: ['00001', '00010', '10100', '01000'],
}

/** How far the canvas reaches past the orb on each side, as a share of its size: room for the
 *  badge on the upper edge. */
const PAD = 0.2

/** An ink as the painter takes it. */
function tintOf(ink: string | undefined): { r: number; g: number; b: number } | undefined {
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

const dot = (x: number, y: number, r: number, a: number): Dot => ({ x, y, z: 0, r, white: 0, a })

/** The wash: a soft field of dots rising from the orb's lower edge, strongest at the edge. */
function washDots(size: number, strength: number, dy: number): Dot[] {
  const out: Dot[] = []
  const c = size / 2
  const step = size / 14
  const r = Math.max(0.35, size * 0.016)
  for (let y = c; y <= size; y += step) {
    const half = Math.sqrt(Math.max(0, c * c - (y - c) * (y - c)))
    const rise = (y - c) / c
    for (let x = c - half; x <= c + half; x += step) out.push(dot(x, y + dy, r, strength * 0.55 * rise ** 1.4))
  }
  return out
}

/** The badge: a ring of dots on the orb's upper edge with its mark inside, drawn with the dots. */
function badgeDots(size: number, mark: BadgeMark, dy: number): { cx: number; cy: number; radius: number; dots: Dot[] } {
  const radius = Math.max(4, size * 0.17)
  const cx = size * 0.86
  const cy = size * 0.14 + dy
  const dots: Dot[] = []
  const ring = 14
  const r = Math.max(0.35, radius * 0.09)
  for (let i = 0; i < ring; i++) {
    const a = (i / ring) * 2 * Math.PI
    dots.push(dot(cx + Math.cos(a) * radius * 0.92, cy + Math.sin(a) * radius * 0.92, r, 1))
  }
  const rows = GLYPHS[mark]
  const cols = rows[0].length
  const cell = (radius * 1.15) / Math.max(rows.length, cols)
  rows.forEach((row, j) => [...row].forEach((on, i) => {
    if (on !== '1') return
    dots.push(dot(cx + (i - (cols - 1) / 2) * cell, cy + (j - (rows.length - 1) / 2) * cell, Math.max(0.35, cell * 0.4), 1))
  }))
  return { cx, cy, radius, dots }
}

export function CandidateInPlace({ phase, size, still = false, dials = '', onPose, fps = null }: CandidateProps) {
  const look = rigLook(phase)
  const pose = usePose(phase, dials)
  const mayMove = useMayMove()
  const { mode } = useMode()
  const { inkToken, ink, toneToken, tone } = useRigInks(look)
  const canvas = useRef<HTMLCanvasElement>(null)
  const onScreen = useOnScreen(canvas)
  const dotsMove = look.moves && !still && mayMove
  const running = (dotsMove || (pose.moving && mayMove)) && onScreen
  const { values } = pose

  useEffect(() => { onPose?.(pose) }, [onPose, pose])

  const preset = useMemo(() => resolvePreset(look.drawing, tunedDrawingFor(size)), [look.drawing, size])
  const frame = useMemo(() => poseFrame(modeOf(look.drawing), pose.read, { still: !dotsMove, lift: true }), [look.drawing, pose.read, dotsMove])

  useEffect(() => {
    const el = canvas.current
    const ctx = el?.getContext('2d')
    if (!el || !ctx) return
    const pad = Math.round(size * PAD)
    const box = size + pad * 2
    const dpr = Math.min(2, (typeof devicePixelRatio !== 'undefined' && devicePixelRatio) || 1)
    el.width = Math.round(box * dpr)
    el.height = Math.round(box * dpr)
    const dark = mode === 'dark'
    let inkTint = tintOf(ink)
    let toneTint = tintOf(tone)
    const paint = (t: number) => {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, box, box)
      ctx.setTransform(dpr, 0, 0, dpr, pad * dpr, pad * dpr)
      paintFrame(ctx, frame(size, t, preset.opts), dark, inkTint)
      const dy = values.lift.get() * size
      const wash = values.wash.get()
      if (wash > 0.01) paintFrame(ctx, { dots: washDots(size, wash, dy), lines: [] }, dark, toneTint)
      if (look.badge) {
        const badge = badgeDots(size, look.badge, dy)
        ctx.globalCompositeOperation = 'destination-out'
        ctx.beginPath()
        ctx.arc(badge.cx, badge.cy, badge.radius * 1.15, 0, Math.PI * 2)
        ctx.fill()
        ctx.globalCompositeOperation = 'source-over'
        paintFrame(ctx, { dots: badge.dots, lines: [] }, dark, toneTint)
      }
    }
    // The painter is this candidate's own, so a scheme change reaches it as the change lands: the
    // tokens are read again and a still is painted at once, and a moving orb's next frame has them.
    const retint = onEditorTokensChange(() => {
      inkTint = tintOf(inkOf(inkToken))
      toneTint = tintOf(inkOf(toneToken))
      if (!running) paint(STILL_AT)
    })
    if (!running) { paint(STILL_AT); return retint }
    // Painted at once as well as on every frame after, so a change of pose shows in the frame it
    // lands in rather than the one after.
    let live = true
    let raf = 0
    let wait = 0
    // A capped rate asks for no frame in between: it waits out the gap on a timer and then asks for
    // the next frame, so the page draws nothing at all between two paints.
    const next = () => {
      if (!live) return
      if (fps) wait = window.setTimeout(() => { if (live) raf = requestAnimationFrame(loop) }, 1000 / fps)
      else raf = requestAnimationFrame(loop)
    }
    const loop = () => {
      if (!live) return
      paint((performance.now() / 1000) * preset.speed)
      next()
    }
    paint((performance.now() / 1000) * preset.speed)
    next()
    return () => { live = false; retint(); cancelAnimationFrame(raf); window.clearTimeout(wait) }
  }, [frame, preset, size, mode, ink, tone, inkToken, toneToken, running, look.badge, values, phase, dials, fps])

  const pad = Math.round(size * PAD)
  return (
    <span
      data-rig-candidate="in-place" data-rig-phase={phase} data-rig-family={look.family}
      data-rig-moving={String(running)} data-rig-ink={ink ?? ''} data-rig-ink-token={inkToken}
      data-rig-tone={look.tone} data-rig-tone-ink={tone ?? ''}
      {...(look.badge ? { 'data-rig-badge': look.badge } : {})}
      {...(poseOf(phase).wash > 0 ? { 'data-rig-wash': look.tone } : {})}
      className="relative inline-block shrink-0"
      style={{ width: size, height: size }}
    >
      <canvas
        ref={canvas} role="img" aria-label={look.label}
        className="absolute block"
        style={{ width: size + pad * 2, height: size + pad * 2, left: -pad, top: -pad }}
      />
    </span>
  )
}
