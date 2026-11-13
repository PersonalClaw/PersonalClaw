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
}

/** Where a point at `radius` (a share of the orb's radius) and `angle` (radians from the top,
 *  clockwise) falls in the box. */
function point(r: number, angle: number, centre: number, unit: number): [number, number] {
  return [centre + r * unit * Math.sin(angle), centre - r * unit * Math.cos(angle)]
}

/** Paint the claw ring's `form` into `ctx`: each stroke one curve with round caps, from its start
 *  round to its hooked end, bowed outward at its middle, in the orb's ink. Nothing else is drawn:
 *  no glow and no backdrop (`web/DESIGN.md`, the Glow-Is-Alive Rule and the default look). */
export function paintRing(ctx: CanvasRenderingContext2D, form: Form, p: RingPaint): void {
  ctx.setTransform(p.dpr, 0, 0, p.dpr, 0, 0)
  ctx.clearRect(0, 0, p.size, p.size)
  const centre = p.size / 2
  // The strokes keep inside the box at rest: the widest stroke's half-width and the outer ring.
  const unit = (p.size / 2) * 0.78 * p.scale
  if (!p.ink) return
  ctx.strokeStyle = `rgb(${p.ink.r}, ${p.ink.g}, ${p.ink.b})`
  ctx.lineCap = 'round'
  // A small orb's strokes are drawn wider, so its shape still reads at 20 and 12 px.
  const boost = p.size <= 20 ? 1.45 : 1
  ctx.lineJoin = 'round'
  for (const s of form.strokes) {
    const a = s.angle + form.spin
    const [x0, y0] = point(s.radius, a - s.span / 2, centre, unit)
    const [cx, cy] = point(s.radius + s.bulge, a, centre, unit)
    const [x1, y1] = point(Math.max(0, s.radius - s.hook), a + s.span / 2, centre, unit)
    ctx.lineWidth = Math.max(1.25, s.width * unit * boost)
    ctx.beginPath()
    ctx.moveTo(x0, y0)
    ctx.quadraticCurveTo(cx, cy, x1, y1)
    ctx.stroke()
  }
}
