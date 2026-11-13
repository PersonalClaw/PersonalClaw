import { duration } from '../../design/motion'
import type { OrbState, OrbStateName } from '../../lib/orbState'

/** The claw ring: the orb's one form (#6117, `docs/design/agent-orb.md`, "The component").
 *
 *  PersonalClaw's mark is a claw of two curved strokes, a blade and a hook (`web/public/claw.svg`).
 *  The orb is six of those curved strokes around a soft centre, "a capable engine with a soft
 *  shell" (`web/DESIGN.md` §1). A state is one set of stroke values, so the form morphs between
 *  states by mixing them, and each state has its own signature:
 *
 *  - **ready**: the open ring, its strokes spaced, at rest;
 *  - **thinking**: the strokes swell outward and draw back, each at its own phase, over one breath;
 *  - **working**: two rings, inner and outer, turning in opposite directions, like a small mechanism;
 *  - **waiting on you**: the strokes lock into a closed clasp, two halves meeting, and hold;
 *  - **done**: the strokes close into the claw mark itself, its blade and its hook, and hold.
 *
 *  A stroke is drawn as one curve with round caps (`paintRing.ts`). Every value here is a share of
 *  the orb's radius, or an angle in radians, so each size draws the same form at its own scale. */

/** How many strokes the ring has. */
export const STROKES = 6

/** One curved stroke: a curve around the centre, from `angle - span/2` to `angle + span/2` at
 *  `radius`, its far end drawn in by `hook`, its middle pushed out by `bulge`. */
export interface Stroke {
  /** Where its middle points, in radians from the top, clockwise. */
  angle: number
  /** How far its curve sits from the centre, as a share of the orb's radius. */
  radius: number
  /** How much of the circle it spans, in radians. */
  span: number
  /** How far its far end bends in toward the centre, the claw's hook. */
  hook: number
  /** How far its middle bows outward. */
  bulge: number
  /** Its width, as a share of the orb's radius. */
  width: number
}

export interface Form {
  strokes: Stroke[]
  /** The whole ring's turn, in radians. */
  spin: number
}

const TAU = Math.PI * 2
const at = (i: number) => (i * TAU) / STROKES

/** The ring at rest: six strokes spaced around the centre, with a gap between each. */
function ready(): Form {
  return { spin: 0, strokes: Array.from({ length: STROKES }, (_, i) => ({ angle: at(i), radius: 0.62, span: 0.72, hook: 0.2, bulge: 0.1, width: 0.18 })) }
}

/** Each stroke's own phase in a breath: the golden angle, so no two swell together. */
const PHASE = Array.from({ length: STROKES }, (_, i) => (i * 2.39996) % TAU)

function thinking(t: number): Form {
  const T = duration.breath
  return {
    spin: 0,
    strokes: Array.from({ length: STROKES }, (_, i) => {
      const swell = Math.sin((TAU * t) / T + PHASE[i])
      return { angle: at(i), radius: 0.56 + 0.12 * swell, span: 0.66 + 0.16 * swell, hook: 0.2, bulge: 0.1 + 0.04 * swell, width: 0.18 + 0.03 * swell }
    }),
  }
}

/** The orb's bar for flashing: nothing in it looks the same again sooner than this, in seconds. */
export const FASTEST_REPEAT_S = 1 / 3

/** Working's lap, in seconds: the Loading length, floored where the bar puts it. Each ring is three
 *  strokes 120 degrees apart, so the inner ring looks the same again after a third of its lap; at a
 *  lap of at least a second it repeats at most three times a second, at every Loading length. */
export function workingLap(): number {
  return Math.max(duration.loading, 3 * FASTEST_REPEAT_S)
}

function working(t: number): Form {
  // The inner ring turns once a lap, clockwise; the outer once in two, the other way.
  const lap = workingLap()
  const inner = (TAU * t) / lap
  const outer = -(TAU * t) / (lap * 2)
  return {
    spin: 0,
    strokes: Array.from({ length: STROKES }, (_, i) => (i % 2 === 0
      ? { angle: at(i) + inner, radius: 0.34, span: 1.15, hook: 0.04, bulge: 0.04, width: 0.17 }
      : { angle: at(i) + outer, radius: 0.74, span: 0.8, hook: 0.06, bulge: 0.04, width: 0.14 })),
  }
}

/** The clasp: two halves of three strokes, right and left, closed round the centre and held. They
 *  meet at the top and the bottom with a narrow seam, and the last stroke of each half hooks in
 *  across its seam, so the two read as halves locked together rather than one ring. */
function waiting(): Form {
  const HALF = [-0.95, 0, 0.95]
  return {
    spin: 0,
    strokes: Array.from({ length: STROKES }, (_, i) => {
      const centre = i < 3 ? Math.PI / 2 : (3 * Math.PI) / 2
      const last = i % 3 === 2
      return { angle: centre + HALF[i % 3], radius: 0.54, span: 0.98, hook: last ? 0.16 : 0, bulge: 0.05, width: 0.24 }
    }),
  }
}

/** The mark: the first three strokes close into its blade, the short sweep at the upper left, and
 *  the last three into its hook, the long sweep down the right that curls in at the bottom. Each
 *  three chain end to end, wide at the top and thinning, as the mark's two shapes do. */
const MARK: Stroke[] = [
  { angle: 5.0, radius: 0.5, span: 0.7, hook: 0.06, bulge: 0.06, width: 0.3 },
  { angle: 5.62, radius: 0.42, span: 0.7, hook: 0.08, bulge: 0.06, width: 0.26 },
  { angle: 6.2, radius: 0.3, span: 0.6, hook: 0.1, bulge: 0.05, width: 0.2 },
  { angle: 0.75, radius: 0.66, span: 0.95, hook: 0.02, bulge: 0.05, width: 0.24 },
  { angle: 1.75, radius: 0.68, span: 1.0, hook: 0.04, bulge: 0.05, width: 0.21 },
  { angle: 2.75, radius: 0.6, span: 0.95, hook: 0.32, bulge: 0.05, width: 0.16 },
]
function done(): Form {
  return { spin: 0, strokes: MARK.map((s) => ({ ...s })) }
}

/** Whether a state moves: thinking and working do; ready, waiting and done hold still. */
export const MOVES: Readonly<Record<OrbStateName, boolean>> = Object.freeze({ ready: false, thinking: true, working: true, waiting: false, done: false })

/** A state's form at rest: a still state's only form, the instant a moving one is drawn at when it
 *  holds still (reduced motion, a hidden tab, the small mark), and the form a morph goes to. */
export function restForm(state: OrbStateName): Form {
  return formAt(state, 0)
}

/** A state's form at `t` seconds into its motion. A still state is its rest form at every `t`. */
export function formAt(state: OrbStateName, t: number): Form {
  switch (state) {
    case 'ready': return ready()
    case 'thinking': return thinking(t)
    case 'working': return working(t)
    case 'waiting': return waiting()
    case 'done': return done()
  }
}

/** How soon a moving state looks the same again, in seconds: thinking after its breath, working's
 *  inner ring after a third of its lap and its outer after a third of its double lap. Nothing in
 *  the orb repeats faster than three times a second (`FASTEST_REPEAT_S`). */
export function periodsOf(): Record<OrbStateName, number[]> {
  const lap = workingLap()
  return { ready: [], thinking: [duration.breath], working: [lap / 3, (2 * lap) / 3], waiting: [], done: [] }
}

const lerp = (a: number, b: number, k: number) => a + (b - a) * k
/** The form `k` of the way from `a` to `b` (0 is `a`, 1 is `b`): every stroke value mixed, each
 *  angle the short way round, so a change of state morphs the form and never jumps. It is
 *  `mixToward` with a still target, the one way the orb mixes a form. */
export const mix = (a: Form, b: Form, k: number): Form => mixToward(a, b, b, k)

/** The form `k` of the way from `from` to `target`, where `target` keeps moving (working's rings
 *  turn on through a morph) and `kept` is the target as it was on the morph's first painted frame.
 *  Each stroke's way round is chosen once, from `kept`, and the target's own turn since then is
 *  added whole, so no stroke flips the other way round mid-morph. With a still target, `kept` is the
 *  target, which is `mix`. */
export function mixToward(from: Form, target: Form, kept: Form, k: number): Form {
  const turn = (a: number, b0: number, b: number) => {
    let d = (b0 - a) % TAU
    if (d > Math.PI) d -= TAU
    if (d < -Math.PI) d += TAU
    return a + k * (d + (b - b0))
  }
  return {
    spin: turn(from.spin, kept.spin, target.spin),
    strokes: from.strokes.map((s, i) => {
      const t = target.strokes[i]
      return {
        angle: turn(s.angle, kept.strokes[i].angle, t.angle),
        radius: lerp(s.radius, t.radius, k),
        span: lerp(s.span, t.span, k),
        hook: lerp(s.hook, t.hook, k),
        bulge: lerp(s.bulge, t.bulge, k),
        width: lerp(s.width, t.width, k),
      }
    }),
  }
}

/** How the orb goes from one state to the next. A morph rides the `smooth` spring (DESIGN.md §5: a
 *  spatial change is a spring), and arriving at done from anything else plays the one success bloom
 *  on the `playful` spring, a sanctioned personality moment. A failure closes to done's form with no
 *  bloom. Under reduced motion every change is an instant swap. */
export function transitionOf(from: OrbState, to: OrbState, { reduced }: { reduced: boolean }): { instant: boolean; morph: 'smooth' | null; bloom: boolean } {
  if (reduced) return { instant: true, morph: null, bloom: false }
  return { instant: false, morph: 'smooth', bloom: to.bloom && from.state !== 'done' }
}
