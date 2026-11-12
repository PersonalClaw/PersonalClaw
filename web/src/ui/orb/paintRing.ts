import type { Form } from './clawRing'

/** An ink as the painter takes it: the orb token's value, resolved (`AgentOrb.inkOf`). */
export interface Rgb { r: number; g: number; b: number }

export interface RingPaint {
  /** The orb's box, in CSS pixels, and the canvas's pixel ratio. */
  size: number
  dpr: number
  /** The strokes' ink; with none (a token that resolves to nothing) nothing is drawn until it does. */
  ink: Rgb | undefined
  /** The whole form's scale: 1 at rest, above 1 through done's one bloom. */
  scale: number
  /** Where the orb's box sits in the canvas, in CSS pixels, and whether the paint clears the
   *  canvas first: at its origin and clearing for the agent orb; past its pad, and over its wash,
   *  for the buddy's (`ui/buddy/paintBuddy.ts`). */
  at?: { x: number; y: number }
  clear?: boolean
}

/** Where a point at `radius` (a share of the orb's radius) and `angle` (radians from the top,
 *  clockwise) falls in the box. */
function point(r: number, angle: number, centre: number, unit: number): [number, number] {
  return [centre + r * unit * Math.sin(angle), centre - r * unit * Math.cos(angle)]
}

/** One stroke as it is drawn in the orb's box: its start, its curve's control point and its end,
 *  in CSS pixels, and its width. */
export interface DrawnStroke { x0: number; y0: number; cx: number; cy: number; x1: number; y1: number; width: number }

/** The claw ring's `form` as drawn in a box of `size` at `scale`: each stroke one quadratic curve,
 *  from its start round to its hooked end, bowed outward at its middle. The one geometry: the
 *  canvas painter below and the buddy's drawn sources (`buddyRig/poseSources.ts`) both take it. */
export function ringStrokes(form: Form, size: number, scale = 1): DrawnStroke[] {
  const centre = size / 2
  // The strokes keep inside the box at rest: the widest stroke's half-width and the outer ring.
  const unit = (size / 2) * 0.78 * scale
  // A small orb's strokes are drawn wider, so its shape still reads at 20 and 12 px.
  const boost = size <= 20 ? 1.45 : 1
  return form.strokes.map((s) => {
    const a = s.angle + form.spin
    const [x0, y0] = point(s.radius, a - s.span / 2, centre, unit)
    const [cx, cy] = point(s.radius + s.bulge, a, centre, unit)
    const [x1, y1] = point(Math.max(0, s.radius - s.hook), a + s.span / 2, centre, unit)
    return { x0, y0, cx, cy, x1, y1, width: Math.max(1.25, s.width * unit * boost) }
  })
}

/** Paint the claw ring's `form` into `ctx`: each stroke one curve with round caps, from its start
 *  round to its hooked end, bowed outward at its middle, in the orb's ink. Nothing else is drawn:
 *  no glow and no backdrop (`web/DESIGN.md`, the Glow-Is-Alive Rule and the default look). */
export function paintRing(ctx: CanvasRenderingContext2D, form: Form, p: RingPaint): void {
  const at = p.at ?? { x: 0, y: 0 }
  ctx.setTransform(p.dpr, 0, 0, p.dpr, at.x * p.dpr, at.y * p.dpr)
  if (p.clear ?? true) ctx.clearRect(0, 0, p.size, p.size)
  if (!p.ink) return
  ctx.strokeStyle = `rgb(${p.ink.r}, ${p.ink.g}, ${p.ink.b})`
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  for (const s of ringStrokes(form, p.size, p.scale)) {
    ctx.lineWidth = s.width
    ctx.beginPath()
    ctx.moveTo(s.x0, s.y0)
    ctx.quadraticCurveTo(s.cx, s.cy, s.x1, s.y1)
    ctx.stroke()
  }
}
