import { formAt, restForm, type Form } from '../orb/clawRing'
import type { Frame } from '../../buddyRig/poseSet'

/** The buddy's orb on the claw ring: the geometry its painters share (`paintBuddy.ts` on a canvas,
 *  `poseSources.ts` as SVG), so the window, the banner's icon and the drawn poses are one drawing.
 *
 *  A pose (`docs/design/buddy/character.md`, "The poses") moves the agent orb's form for the state
 *  and never replaces it: quiet's strokes are drawn thinner, an approval draws them in, an error's
 *  sag and settle low, done's thin open ring turns once and lands upright, and the whole orb lifts
 *  or sinks. Nothing squashes or stretches the form. The badge and the alert's wash are strokes too,
 *  in the alert's tone, so the buddy draws no dot at all. Every value is a share of the orb's size,
 *  or an angle in radians, in the orb's own frame (its box is 0 to `size`). */

/** What a pose does to the form: the share of quiet's thinness, how far the strokes are drawn in,
 *  how far an error's sag goes, and done's turn, in turns. */
export interface StrokePose {
  density: number
  spread: number
  sink: number
  swirl: number
}

/** How much thinner quiet's strokes are drawn, at its full sparseness (density 0). */
const THIN = 0.45
/** How far an error's strokes sag at a full droop: their bow flattens and their hooks bend in. */
const SAG_BOW = 0.6
const SAG_HOOK = 0.16
/** How far an error's strokes settle at a full droop, as a share of the orb's size. The badge and
 *  the wash stay where they are. */
export const SINK_DROP = 0.15

/** `form` as the pose moves it. Returns `form` itself when the pose moves nothing, so the buddy at
 *  rest draws the agent orb's form exactly. */
export function posedForm(form: Form, p: StrokePose): Form {
  const turn = p.swirl % 1
  if (p.density === 1 && p.spread === 1 && p.sink === 0 && turn === 0) return form
  const width = 1 - THIN * (1 - Math.max(0, Math.min(1, p.density)))
  return {
    spin: form.spin + turn * 2 * Math.PI,
    strokes: form.strokes.map((s) => ({
      ...s,
      radius: s.radius * p.spread,
      width: s.width * width,
      bulge: s.bulge * (1 - SAG_BOW * p.sink),
      hook: s.hook + SAG_HOOK * p.sink,
    })),
  }
}

/** How far the canvas reaches past the orb on each side, as a share of its size: room for the
 *  badge on the upper edge. */
export const PAD = 0.2

/** The badge has its ring from this orb size up; below it, its mark fills it, and a waiting mark's
 *  badge is solid, its mark cut out of it. */
export const RING_FROM = 24

/** Every mark the badge draws: working, the three alert marks, the check, the microphone while
 *  listening and the offline mark. */
export type Mark = 'working' | 'approval' | 'input' | 'error' | 'done' | 'listening' | 'offline'

/** The marks of the waiting family, whose badge is solid below `RING_FROM`. */
export const SOLID_MARKS: ReadonlySet<Mark> = new Set<Mark>(['approval', 'input'])

/** One stroke of a mark, in the badge's own frame (-1 to 1 across its inner circle): a line from
 *  its first point to its last, through a curve's control point where it has three. One point is
 *  a round-capped stroke of no length: a point, as the ! and ? end in and the working mark's three
 *  are. */
export interface MarkStroke {
  pts: readonly (readonly [number, number])[]
  /** Its width as a share of the mark's own, 1 unless it says: the microphone's capsule is heavy. */
  w?: number
}

const line = (...pts: [number, number][]): MarkStroke => ({ pts })

export const MARK_STROKES: Readonly<Record<Mark, readonly MarkStroke[]>> = {
  working: [line([-0.56, 0]), line([0, 0]), line([0.56, 0])],
  approval: [line([0, -0.62], [0, 0.18]), line([0, 0.6])],
  input: [line([-0.36, -0.3], [-0.1, -0.86], [0.38, -0.3]), line([0.38, -0.3], [0, -0.05], [0, 0.18]), line([0, 0.6])],
  error: [line([-0.45, -0.45], [0.45, 0.45]), line([0.45, -0.45], [-0.45, 0.45])],
  done: [line([-0.52, 0.02], [-0.16, 0.4]), line([-0.16, 0.4], [0.52, -0.46])],
  // The microphone: a heavy capsule over its cradle, on a short stem.
  listening: [{ pts: [[0, -0.56], [0, -0.1]], w: 2.3 }, line([-0.46, -0.16], [0, 0.6], [0.46, -0.16]), line([0, 0.22], [0, 0.66])],
  offline: [line([-0.5, 0.5], [0.5, -0.5])],
}

/** Where the badge sits on the orb's upper edge, and how large it is. */
export function badgeAt(size: number, dy = 0): { cx: number; cy: number; radius: number } {
  return { cx: size * 0.86, cy: size * 0.14 + dy, radius: Math.max(4, size * 0.17) }
}

/** The microphone is drawn from this orb size up. Below it the mark is a plain dot in the
 *  listening tone, and the words beside the orb say Listening. */
export const MIC_FROM = 22

/** The badge's mark strokes in the orb's frame, with their width: inside the ring from
 *  `RING_FROM` up, filling the badge below it. */
export function markStrokes(size: number, mark: Mark, dy: number, ring: boolean): { pts: [number, number][]; width: number }[] {
  const { cx, cy, radius } = badgeAt(size, dy)
  if (mark === 'listening' && size < MIC_FROM) return [{ pts: [[cx, cy]], width: radius * 1.1 }]
  const reach = radius * (ring ? 0.58 : 0.82)
  const width = Math.max(0.9, radius * (ring ? 0.2 : 0.28))
  return MARK_STROKES[mark].map((s) => ({
    pts: s.pts.map(([x, y]) => [cx + x * reach, cy + y * reach] as [number, number]),
    width: width * (s.w ?? 1),
  }))
}

/** The badge ring's width, from `RING_FROM` up. */
export const ringWidth = (radius: number) => Math.max(1, radius * 0.14)

/** The wash is drawn above this orb size; at it and under, the shape and the colour carry the
 *  state alone. */
export const WASH_ABOVE = 20

/** The alert's wash: three still arcs under the orb, rising from its lower edge, each fainter as it
 *  rises, in the alert's tone. They read as the orb calling the owner, with no light and no pulse. Each is `[radius, from, to, alpha, width]`: an arc
 *  about the orb's centre, from and to an angle in radians measured as a canvas measures them
 *  (from the right, clockwise). */
export function washArcs(size: number, strength: number): [number, number, number, number, number][] {
  if (strength <= 0.01 || size <= WASH_ABOVE) return []
  const R = size / 2
  return [0.86, 0.66, 0.46].map((share, i) => {
    const half = 0.62 - i * 0.12
    return [R * share, Math.PI / 2 - half, Math.PI / 2 + half, strength * (0.5 - i * 0.14), Math.max(0.8, size * 0.035)]
  })
}

/** The instant a pose's motion frame is drawn at, in seconds into its state's motion. */
export const MOVING_AT = 0.7

/** A frame's form: its state's claw ring, at its motion's instant where it moves, as its pose
 *  moves it. The drawn poses and the banner's icon draw a frame through it; the live orb mixes the
 *  same form through its morph. */
export function formOfFrame(frame: Frame, t = MOVING_AT): Form {
  const base = frame.moving ? formAt(frame.form, t) : restForm(frame.form)
  return posedForm(base, { density: frame.density, spread: frame.spread, sink: frame.sink, swirl: frame.swirl })
}

/** A mark's stroke as points along it: a line's two ends, a curve sampled, a point once. */
export function strokePoints(pts: readonly (readonly [number, number])[], samples = 10): [number, number][] {
  if (pts.length < 3) return pts.map(([x, y]) => [x, y])
  const [a, c, b] = pts
  return Array.from({ length: samples + 1 }, (_, i) => {
    const t = i / samples
    const u = 1 - t
    return [u * u * a[0] + 2 * u * t * c[0] + t * t * b[0], u * u * a[1] + 2 * u * t * c[1] + t * t * b[1]]
  })
}

/** The outline of a round-capped stroke of `half` its width along `pts`, as one closed shape: up
 *  one side, round the far cap, back down the other and round the near one. A point's outline is
 *  its round cap, a circle. What a solid badge cuts its mark out of itself with, as holes, where a
 *  one-colour image cannot paint a stroke in the background's colour. */
export function strokeOutline(pts: [number, number][], half: number): [number, number][] {
  if (pts.length === 1) {
    const [x, y] = pts[0]
    return Array.from({ length: 16 }, (_, i) => [x + half * Math.cos((i / 16) * 2 * Math.PI), y + half * Math.sin((i / 16) * 2 * Math.PI)])
  }
  const normal = (i: number): [number, number] => {
    const [ax, ay] = pts[Math.max(0, i - 1)]
    const [bx, by] = pts[Math.min(pts.length - 1, i + 1)]
    const len = Math.hypot(bx - ax, by - ay) || 1
    return [-(by - ay) / len, (bx - ax) / len]
  }
  const left = pts.map((p, i) => { const [nx, ny] = normal(i); return [p[0] + nx * half, p[1] + ny * half] as [number, number] })
  const right = pts.map((p, i) => { const [nx, ny] = normal(i); return [p[0] - nx * half, p[1] - ny * half] as [number, number] })
  const cap = (centre: [number, number], from: [number, number]): [number, number][] => {
    const a0 = Math.atan2(from[1] - centre[1], from[0] - centre[0])
    return Array.from({ length: 7 }, (_, i) => {
      const a = a0 - (i / 6) * Math.PI
      return [centre[0] + half * Math.cos(a), centre[1] + half * Math.sin(a)]
    })
  }
  return [...left, ...cap(pts[pts.length - 1], left[left.length - 1]).slice(1, -1), ...right.reverse(), ...cap(pts[0], right[right.length - 1] ?? right[0]).slice(1, -1)]
}
