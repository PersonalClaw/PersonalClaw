import type { Form } from '../orb/clawRing'
import { paintRing, type Rgb } from '../orb/paintRing'
import { tintOf } from '../orbDots'
import { inkOf } from '../AgentOrb'
import { PAD, RING_FROM, SOLID_MARKS, SINK_DROP, badgeAt, formOfFrame, markStrokes, ringWidth, washArcs, type Mark } from './buddyRing'
import { poseOf, stillOf, type BuddyState } from './orbPoses'

/** What one paint of the buddy orb needs besides its form. */
export interface OrbPaint {
  size: number
  /** The canvas's room around the orb, in points, and its pixel ratio. */
  pad: number
  dpr: number
  ink: Rgb | undefined
  tone: Rgb | undefined
  /** The strokes' brightness, the whole orb's rise (a share of its size), an error's settle (the
   *  strokes alone) and the alert's wash. */
  dim: number
  lift: number
  sink: number
  wash: number
  mark: Mark | null
  /** The whole orb's move to one side (a share of its size) and its scale, one factor for both
   *  axes, so it never squashes: an answer's shake and a swell. */
  sway?: number
  swell?: number
  /** How far the badge is shown, 1 in full: it folds toward 0 as an answer elsewhere closes it. */
  badge?: number
}

const rgb = (c: Rgb | undefined) => (c ? `rgb(${c.r}, ${c.g}, ${c.b})` : 'gray')

function strokePath(ctx: CanvasRenderingContext2D, pts: [number, number][]): void {
  ctx.beginPath()
  ctx.moveTo(pts[0][0], pts[0][1])
  // A point is a stroke a hair long: a canvas draws no cap on a stroke of no length at all.
  if (pts.length === 1) ctx.lineTo(pts[0][0] + 0.01, pts[0][1])
  else if (pts.length === 2) ctx.lineTo(pts[1][0], pts[1][1])
  else ctx.quadraticCurveTo(pts[1][0], pts[1][1], pts[2][0], pts[2][1])
  ctx.stroke()
}

/** Paint the buddy orb into `ctx`: its form, the alert's wash under it and its badge cut out of
 *  it, in the badge's tone. The one painter: `BuddyOrb` calls it every frame it draws, and
 *  `stillOrbPng` once, for a still. */
export function paintOrb(ctx: CanvasRenderingContext2D, form: Form, p: OrbPaint): void {
  const box = p.size + p.pad * 2
  ctx.setTransform(p.dpr, 0, 0, p.dpr, 0, 0)
  ctx.clearRect(0, 0, box, box)
  const dy = p.lift * p.size
  const settle = SINK_DROP * p.sink * p.size
  const dx = (p.sway ?? 0) * p.size
  const at = (x: number, y: number) => ctx.setTransform(p.dpr, 0, 0, p.dpr, (p.pad + dx + x) * p.dpr, (p.pad + y) * p.dpr)
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  // The wash first, so the strokes sit over it.
  at(0, dy)
  ctx.strokeStyle = rgb(p.tone)
  for (const [r, from, to, alpha, width] of washArcs(p.size, p.wash)) {
    ctx.globalAlpha = alpha
    ctx.lineWidth = width
    ctx.beginPath()
    ctx.arc(p.size / 2, p.size / 2, r, from, to)
    ctx.stroke()
  }
  // The form, through the agent orb's own painter, in the buddy's box, over the wash.
  ctx.globalAlpha = p.dim
  paintRing(ctx, form, { size: p.size, dpr: p.dpr, ink: p.ink, scale: p.swell ?? 1, at: { x: p.pad + dx, y: p.pad + dy + settle }, clear: false })
  ctx.globalAlpha = 1
  const fold = p.badge ?? 1
  if (!p.mark || fold <= 0.01) return
  at(0, 0)
  const ring = p.size >= RING_FROM
  const solid = !ring && SOLID_MARKS.has(p.mark)
  const { cx, cy, radius } = badgeAt(p.size, dy)
  // A folding badge shrinks about its own centre.
  ctx.translate(cx, cy)
  ctx.scale(fold, fold)
  ctx.translate(-cx, -cy)
  ctx.globalCompositeOperation = 'destination-out'
  ctx.beginPath()
  ctx.arc(cx, cy, radius * 1.15, 0, Math.PI * 2)
  ctx.fill()
  ctx.globalCompositeOperation = 'source-over'
  ctx.strokeStyle = rgb(p.tone)
  ctx.fillStyle = rgb(p.tone)
  const marks = markStrokes(p.size, p.mark, dy, ring)
  if (solid) {
    // A waiting badge below 24 px is one solid disc in the tone with its mark cut out of it.
    ctx.beginPath()
    ctx.arc(cx, cy, radius, 0, Math.PI * 2)
    ctx.fill()
    ctx.globalCompositeOperation = 'destination-out'
  } else if (ring) {
    ctx.lineWidth = ringWidth(radius)
    ctx.beginPath()
    ctx.arc(cx, cy, radius * 0.92, 0, Math.PI * 2)
    ctx.stroke()
  }
  for (const s of marks) {
    ctx.lineWidth = s.width
    strokePath(ctx, s.pts)
  }
  ctx.globalCompositeOperation = 'source-over'
}

/** The ink token a still's strokes take, and its badge's tone token, as `BuddyOrb` reads them at
 *  the platform's ordinary contrast. */
export function stillTokens(state: BuddyState): { ink: string; tone: string } {
  const f = stillOf(state)
  return {
    ink: f.ink === 'alive' ? '--color-orb-alive' : '--color-orb-rest',
    tone: f.tone === 'neutral' ? '--color-orb-rest' : `--color-${f.tone}`,
  }
}

/** *state*'s still orb as a PNG data URL, at *size* points and twice their pixels, in the page's
 *  light or dark ink: what a native banner shows as its icon (#3897). "" where nothing can paint
 *  (no canvas, a test's DOM), and the banner shows its line alone. */
export function stillOrbPng(state: BuddyState, size: 48 | 96 = 96): string {
  if (typeof document === 'undefined') return ''
  const canvas = document.createElement('canvas')
  let ctx: CanvasRenderingContext2D | null = null
  try {
    ctx = canvas.getContext('2d')
  } catch {
    return ''
  }
  if (!ctx) return ''
  const pad = Math.round(size * PAD)
  const dpr = 2
  canvas.width = (size + pad * 2) * dpr
  canvas.height = (size + pad * 2) * dpr
  const f = stillOf(state)
  const pose = poseOf(state)
  const tokens = stillTokens(state)
  paintOrb(ctx, formOfFrame({ ...f, ...pose, moving: false }), {
    size, pad, dpr,
    ink: tintOf(inkOf(tokens.ink)),
    tone: tintOf(inkOf(tokens.tone)),
    dim: f.dim, lift: pose.lift, sink: pose.sink, wash: pose.wash, mark: f.mark,
  })
  try {
    return canvas.toDataURL('image/png')
  } catch {
    return ''
  }
}
