import { motion, useReducedMotion } from 'framer-motion'
import { spring } from '../design/motion'

/** A circular progress arc for loop cycle progress — the ring beside a loop row that
 *  reads "how far through its cycle budget this run is".
 *
 *  Extracted from two byte-identical copies (`dashboard/widgets/ActiveWork` and
 *  `loops/LoopsListPage`) that agreed on every number — radius, 2.5 stroke, the
 *  `surface-high` track, the -90° start so the arc grows clockwise from 12 o'clock —
 *  and differed only in that ONE of them animated. The dashboard copy tweened the
 *  arc with a spring; the list copy set `strokeDashoffset` directly, so the same
 *  ring jumped there while every other element in that row (the row itself, its
 *  context menu) animated. That is drift with a visible cost, not two designs.
 *
 *  The animated behaviour wins because it is the one a user reads as progress
 *  ADVANCING rather than a value being replaced — and because a cycle completing is
 *  exactly the moment worth showing. `initial={false}` keeps mount silent: the first
 *  paint is the true value, so a list of eight loops doesn't sweep eight arcs from
 *  zero on every navigation. Under `prefers-reduced-motion` the arc is set directly,
 *  matching the global token rule that collapses durations rather than easing them. */
export function ProgressRing({ pct, tone, size = 28, label }: {
  /** Progress as a FRACTION in 0..1 (not 0..100). Callers clamp; a loop that ran past
   *  its budget still reads full rather than winding a second time round. */
  pct: number
  /** Stroke color for the filled arc — pass the status tone so the ring agrees with the
   *  label beside it (a CSS var or any color string). The track stays `surface-high`. */
  tone: string
  /** Outer diameter in px. The 2.5 stroke is fixed, so very small sizes read as heavy. */
  size?: number
  /** 🔴 WHAT THIS RING IS MEASURING — required, because the ring was SILENT. Read from the live AX
   *  tree on `#/loops/history` and `#/dashboard`: `role`, `aria-label`, `aria-hidden` and `<title>`
   *  all absent, on both of its call sites. A graphic that is the only carrier of a number is
   *  either named or hidden; it was neither, so assistive tech got nothing at all where a sighted
   *  user reads a progress arc (WCAG 1.1.1 / 4.1.2).
   *
   *  The semantics converge on `ui/WavyProgress`, this app's other progress indicator, which already
   *  ships `role="progressbar"` + `aria-valuemin/max/now`. What it does NOT ship is a NAME, so a
   *  screen-reader user hears a bare percentage with no subject — that is why this prop is required
   *  here rather than optional: two call sites, both of which know what they are counting. */
  label: string
}) {
  const reduce = useReducedMotion()
  const r = size / 2 - 2.5, c = 2 * Math.PI * r
  const offset = c * (1 - pct)
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="shrink-0"
      role="progressbar" aria-label={label}
      aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(Math.max(0, Math.min(1, pct)) * 100)}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--color-surface-high)" strokeWidth={2.5} />
      <motion.circle
        cx={size / 2} cy={size / 2} r={r} fill="none" stroke={tone} strokeWidth={2.5} strokeLinecap="round"
        strokeDasharray={c}
        initial={false}
        animate={{ strokeDashoffset: offset }}
        transition={reduce ? { duration: 0 } : spring.spatialSlow}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
    </svg>
  )
}
