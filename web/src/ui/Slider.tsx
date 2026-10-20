import { useId } from 'react'

/** The one canonical bounded-range slider for the app — a native `<input type="range">`
 *  wearing the design-system accent, so a "pick a number on a scale" control (the grill's
 *  `slider` question kind, a granularity dial) looks identical everywhere instead of each
 *  call-site hand-rolling a raw range element that the primitive-adoption ratchet would flag.
 *
 *  Kept deliberately thin: a range input IS the right native control (keyboard + a11y come
 *  free); this primitive only owns the accent, the min/max/step contract, and the accessible
 *  name. A caller that also wants the numeric value shown pairs it with `NumberField`. */
export function Slider({ value, onChange, min = 0, max = 10, step = 1, ariaLabel, disabled = false }: {
  value: number
  onChange: (n: number) => void
  min?: number
  max?: number
  step?: number
  /** Accessible name — a bare slider has none of its own. */
  ariaLabel?: string
  disabled?: boolean
}) {
  const id = useId()
  return (
    <input
      type="range" id={id} value={value} min={min} max={max} step={step} disabled={disabled}
      aria-label={ariaLabel}
      onChange={(e) => onChange(Number(e.target.value))}
      className="w-full accent-primary disabled:opacity-40 disabled:cursor-not-allowed"
    />
  )
}
