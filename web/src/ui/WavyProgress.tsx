import { motion } from 'framer-motion'

/** Wavy progress — an animated sine wave in the accent
 *  gradient (replaces the flat M2 progress bar).
 *
 *  Indeterminate by default (the wave crest travels). Pass `value` (0–1) for a
 *  determinate bar: the wave is drawn full-width as a faint track with the filled
 *  portion overlaid up to `value`. Used by the bundled-model download manager,
 *  where the byte-progress total is known (determinate) or absent (indeterminate). */
export function WavyProgress({
  width = 120,
  color = 'var(--color-primary)',
  value,
  label,
}: { width?: number; color?: string } & (
  // 🔴 A DETERMINATE BAR MUST BE NAMED; AN INDETERMINATE ONE MUST NOT BE ANNOUNCED AT ALL.
  // This component already shipped `role="progressbar"` + `aria-valuemin/max/now` for the
  // determinate path — but with **no accessible name**, so assistive tech announced a bare
  // percentage with no subject ("progressbar, 42%"). That is the missing half of Name/Role/Value
  // (4.1.2), and axe's `aria-progressbar-name` would report it — except the bar only renders while
  // a download job is RUNNING, so no audit of `#/settings/models` ever reached it. Cycle 178 fixed
  // the same gap on `ui/ProgressRing` and recorded this one; this is that follow-up.
  //
  // The type enforces the distinction rather than trusting a convention (the shape `WidgetRow`
  // uses): pass a `value` and you must name it; omit `value` and there is nothing to name, because
  // the indeterminate wave is `aria-hidden` on purpose — the call site's own text already says
  // "downloading · 120 / 400 MB", so announcing a second, valueless progressbar would only repeat it.
  | { value: number; label: string }
  | { value?: undefined; label?: never }
)) {
  const d = `M0 4 Q ${width * 0.125} 0 ${width * 0.25} 4 T ${width * 0.5} 4 T ${width * 0.75} 4 T ${width} 4`

  if (value == null) {
    return (
      <svg width={width} height={8} viewBox={`0 0 ${width} 8`} fill="none" aria-hidden>
        <motion.path
          d={d}
          stroke={color}
          strokeWidth={2.5}
          strokeLinecap="round"
          animate={{ pathOffset: [0, 1] }}
          transition={{ duration: 1.4, ease: 'linear', repeat: Infinity }}
          style={{ pathLength: 0.6 }}
        />
      </svg>
    )
  }

  const pct = Math.max(0, Math.min(1, value))
  return (
    <svg
      width={width}
      height={8}
      viewBox={`0 0 ${width} 8`}
      fill="none"
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct * 100)}
    >
      <path d={d} stroke={color} strokeWidth={2.5} strokeLinecap="round" opacity={0.2} />
      <motion.path
        d={d}
        stroke={color}
        strokeWidth={2.5}
        strokeLinecap="round"
        style={{ pathLength: pct }}
        animate={{ pathLength: pct }}
        transition={{ ease: 'easeOut', duration: 0.4 }}
      />
    </svg>
  )
}
