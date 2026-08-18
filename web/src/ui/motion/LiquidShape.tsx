import { useEffect, useId } from 'react'
import { animate, motion, useMotionValue, useReducedMotion, useTransform } from 'framer-motion'
import { expr, exprHeavy, physics } from '../../design/motion'

/** A coral-tinted liquid blob that MORPHS between two shape states (FLUID-MOTION
 *  §S2 T2.2 / atom FM-3). Flip `active` and the silhouette flows from `from` to
 *  `to` — for loading→loaded, idle→busy, and ambient state transitions where a
 *  crossfade would read as a swap rather than a change of state.
 *
 *   • BOLD (exprHeavy): the morph on `physics.fluid` PLUS a slow idle breathe —
 *     the radius wanders continuously, so a settled blob still reads as liquid
 *     rather than as a static graphic.
 *   • REFINED (below the exprHeavy gate): the morph only. The breathe is DROPPED
 *     entirely — no perpetual animation driver at all — because the refined tier
 *     drops a heavy effect rather than shrinking it (per `exprHeavy`'s contract).
 *   • REDUCED-MOTION: instant. The target silhouette is rendered directly as a
 *     plain `<path>`; there is no motion value, no spring and no driver to run
 *     (the global CSS rule kills CSS transitions; JS/Motion must self-gate).
 *
 *  **SVG path, not a canvas metaball — decided by measurement**, per the plan's
 *  open question. At the shape counts this primitive is actually used at (one, a
 *  few) both fit the frame budget, but a metaball's cost is per-PIXEL over its
 *  own area, so it is the one that fails first: on a 20x-throttled CPU the
 *  metaball spends 10.9ms/frame of JS at 4 shapes and 39.9ms at 16 (239 of 240
 *  frames blowing 20ms), where this path costs 0.0ms and 2.3ms. The plan's
 *  premise that "canvas scales better for many shapes" holds for point/particle
 *  fields (`DotGlow`'s construction), NOT for a density field. Full numbers in
 *  the plan's execution log.
 *
 *  NO WebGL and NO gooey filter (the deleted-primitives lesson, per
 *  `Disintegrate`): the silhouette is real geometry, not blurred circles pushed
 *  through a contrast filter. That cheap canvas trick was measured too and was
 *  the ONLY variant to miss frames unthrottled — its price is raster, invisible
 *  to a JS-work probe — so it is disqualified on measurement as well as lineage.
 *
 *  Decorative by contract: `aria-hidden` + pointer-events-none, like `DotGlow`
 *  and `WavyProgress`'s indeterminate wave. If the state it depicts matters, the
 *  CALL SITE must say so in text — this primitive must never be the only place a
 *  user could learn something. Tint is `var(--color-primary)` (the theme's coral,
 *  never a hex) and every amplitude rides `expr()`, so it honors the theme, the
 *  expressiveness knob and reduced motion without the call site doing anything. */

/** The shape vocabulary. `circle` is the neutral resting form; `squircle` is a
 *  settled, deliberate form (a superellipse); `blob` is the unsettled, organic
 *  one. A morph reads as a change in COMPOSURE, which is why loading→loaded is
 *  `blob`→`squircle` rather than an arbitrary pair. */
export type LiquidShapeName = 'circle' | 'squircle' | 'blob'

/** Every tunable in one place — this is the taste surface the owner dials (the
 *  plan's owner task 1: "budget ~30 min per session dialing constants"). The
 *  feel is NOT claimed settled; these are a starting point, not a verdict. */
const TUNING = {
  /** Control points around the silhouette. 16 puts a point on every diagonal
   *  (so the squircle's corners land) and samples the blob's 5-lobe term above
   *  Nyquist; raising it costs path-string length, not frame budget. */
  points: 16,
  /** Base radius in viewBox units — leaves headroom for lobes + breathe. */
  radius: 34,
  /** Shape CHARACTER depth: how far a named shape departs from a circle. Rides
   *  `expr()`, so refined keeps a hint of the shape instead of flattening it. */
  character: 1,
  /** Idle breathe: radius wander as a fraction of the radius. Rides `expr()`,
   *  and is dropped entirely below the `exprHeavy` gate. */
  breathe: 0.055,
  /** Seconds for one full breathe cycle. */
  breatheCycle: 6.2,
  /** Fill opacity at the core, and at the silhouette's edge. The blob is a soft
   *  radial mass rather than a flat one, and this is NOT decoration for its own
   *  sake — the first browser pass rendered it at a single opacity and it read as
   *  a poster-weight coral slab beside `WavyProgress`'s hairline coral stroke and
   *  `DotGlow`'s luminous field. Grading core→edge is what puts it in the same
   *  visual language as both neighbours instead of shouting over them. */
  fillCore: 0.82,
  fillEdge: 0.4,
}

const BOX = 100
const CENTER = BOX / 2
const TAU = Math.PI * 2

/** How far a named shape departs from a unit circle at angle `a` (0 = circle). */
function character(shape: LiquidShapeName, a: number): number {
  switch (shape) {
    case 'circle':
      return 0
    case 'squircle':
      // Superellipse |x|^4 + |y|^4 = 1, expressed as a radius: flat-ish sides,
      // fuller diagonals — a rounded square rather than a wobble.
      return (Math.abs(Math.cos(a)) ** 4 + Math.abs(Math.sin(a)) ** 4) ** -0.25 - 1
    case 'blob':
      // Two incommensurate lobe terms, so the form is organic and not a polygon.
      return 0.19 * Math.sin(3 * a) + 0.11 * Math.cos(5 * a)
  }
}

/** The silhouette as a closed path. Radii are blended BEFORE the path is built —
 *  interpolating two finished `d` strings can pinch or self-intersect, whereas a
 *  blend of radii is always a valid star-shaped outline. */
function silhouette(
  from: LiquidShapeName,
  to: LiquidShapeName,
  t: number,
  phase: number,
  amp: number,
  breathe: number,
): string {
  const k = TUNING.points
  const x: number[] = []
  const y: number[] = []
  for (let i = 0; i < k; i++) {
    const a = (i / k) * TAU
    const dev = character(from, a) * (1 - t) + character(to, a) * t
    const wander = breathe === 0 ? 0 : breathe * Math.sin(phase + a * 2)
    const r = TUNING.radius * (1 + dev * amp + wander)
    x.push(CENTER + Math.cos(a) * r)
    y.push(CENTER + Math.sin(a) * r)
  }
  // Closed Catmull-Rom through the points, emitted as cubic beziers.
  const n = (v: number) => v.toFixed(2)
  let d = `M${n(x[0])} ${n(y[0])}`
  for (let i = 0; i < k; i++) {
    const p0 = (i - 1 + k) % k
    const p2 = (i + 1) % k
    const p3 = (i + 2) % k
    d += `C${n(x[i] + (x[p2] - x[p0]) / 6)} ${n(y[i] + (y[p2] - y[p0]) / 6)}`
      + ` ${n(x[p2] - (x[p3] - x[i]) / 6)} ${n(y[p2] - (y[p3] - y[i]) / 6)}`
      + ` ${n(x[p2])} ${n(y[p2])}`
  }
  return `${d}Z`
}

export function LiquidShape({
  from = 'circle',
  to = 'blob',
  active,
  intensity = 1,
  tint = 'var(--color-primary)',
  className,
}: {
  /** The resting silhouette, shown while `active` is false. */
  from?: LiquidShapeName
  /** The silhouette morphed TO while `active` is true. */
  to?: LiquidShapeName
  /** Flip true to morph `from`→`to`; false morphs back. */
  active: boolean
  /** Base amplitude 0..1 for shape character + breathe. **Pass a plain number:
   *  the primitive applies `expr()` itself**, so a call site cannot forget the
   *  expressiveness knob — and passing `expr(1)` here would scale it twice. */
  intensity?: number
  /** Any theme color var. Defaults to the coral primary; never pass a hex. */
  tint?: string
  /** Sizes the blob — it fills its box (e.g. `size-16`, or a sized parent). */
  className?: string
}) {
  const reduce = useReducedMotion()
  const heavy = exprHeavy()
  const amp = expr(TUNING.character) * intensity
  const breathe = heavy ? expr(TUNING.breathe) * intensity : 0

  // One scalar drives the morph and one drives the breathe; `d` is DERIVED from
  // them, so the interpolation is over geometry we control rather than over a
  // path string Motion would have to pattern-match.
  const t = useMotionValue(active ? 1 : 0)
  const phase = useMotionValue(0)
  const d = useTransform(
    [t, phase],
    ([tv, pv]: number[]) => silhouette(from, to, tv, pv, amp, breathe),
  )

  useEffect(() => {
    // Reduced motion: land on the target with no animation at all. (The plain
    // `<path>` branch below is what actually renders, but keeping the value in
    // sync means toggling reduced motion off mid-life resumes from the truth.)
    if (reduce) { t.set(active ? 1 : 0); return }
    const controls = animate(t, active ? 1 : 0, physics.fluid)
    return () => controls.stop()
  }, [active, reduce, t])

  useEffect(() => {
    // The breathe is the HEAVY tier: below the gate (or under reduced motion)
    // there is no driver, not a slower one.
    if (reduce || !heavy) return
    const controls = animate(phase, TAU, {
      duration: TUNING.breatheCycle,
      ease: 'linear',
      repeat: Infinity,
    })
    return () => controls.stop()
  }, [reduce, heavy, phase])

  // The gradient needs a document-unique id — two blobs on one page sharing one
  // would be a silent visual bug. `useId()` can emit characters that are awkward
  // in a `url(#…)` reference, so it is stripped down to a safe fragment name.
  const gradientId = `liquid-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`

  const shared = {
    className,
    viewBox: `0 0 ${BOX} ${BOX}`,
    'aria-hidden': true,
    focusable: 'false' as const,
    // Decoration must never intercept a click meant for the surface beneath it.
    style: { pointerEvents: 'none' as const },
  }
  // Both stops are the SAME theme var at two opacities — a soft mass, with no
  // second color introduced and no hex anywhere.
  const grade = (
    <defs>
      <radialGradient id={gradientId} cx="50%" cy="45%" r="62%">
        <stop offset="0%" stopColor={tint} stopOpacity={TUNING.fillCore} />
        <stop offset="100%" stopColor={tint} stopOpacity={TUNING.fillEdge} />
      </radialGradient>
    </defs>
  )
  const fill = `url(#${gradientId})`

  if (reduce) {
    return (
      <svg {...shared} data-liquid-shape="instant" data-liquid-tier="reduced">
        {grade}
        <path d={silhouette(from, to, active ? 1 : 0, 0, amp, 0)} fill={fill} />
      </svg>
    )
  }

  return (
    <svg {...shared} data-liquid-shape="morph" data-liquid-tier={heavy ? 'bold' : 'refined'}>
      {grade}
      <motion.path d={d} fill={fill} />
    </svg>
  )
}
