import { motion } from 'framer-motion'
import { spring } from '../design/motion'

/** The ONE canonical on/off switch for the whole app. A pill track + a knob that
 *  springs across on toggle (bounce-tier settle). Track = primary when on, neutral
 *  when off; knob uses the on-primary ink token. role="switch" + aria-checked for
 *  a11y. Replaces ~11 hand-rolled inline-styled copies scattered across pages. */
export function Toggle({
  on, onChange, label, disabled = false, size = 'md', readOnly = false, decorative = false,
}: {
  on: boolean
  /** Omit + set readOnly for a display-only indicator (renders a non-interactive
   *  span, so it can sit inside a larger clickable row without nesting buttons). */
  onChange?: (v: boolean) => void
  label?: string
  disabled?: boolean
  /** 'sm' for dense rows (h-5 w-9), 'md' default (h-6 w-10). */
  size?: 'sm' | 'md'
  readOnly?: boolean
  /** Purely visual — the switch sits INSIDE an already-labeled clickable control
   *  (a wrapping <button aria-label>). Hidden from the a11y tree so it doesn't
   *  surface as a second, unnamed switch node duplicating the button. */
  decorative?: boolean
}) {
  const sm = size === 'sm'
  const knob = sm ? 14 : 16
  const travel = sm ? 16 : 18
  const trackCls = `relative inline-flex shrink-0 items-center rounded-pill transition-colors ${sm ? 'h-5 w-9' : 'h-6 w-10'}`
  const trackStyle = { background: on ? 'var(--color-primary)' : 'var(--color-surface-highest)' }
  const knobEl = (
    <motion.span
      className="ml-0.5 inline-block rounded-full"
      style={{ width: knob, height: knob, background: 'var(--color-on-primary)' }}
      animate={{ x: on ? travel : 0 }}
      transition={spring.spatialFast}
    />
  )
  if (readOnly || !onChange) {
    // Decorative: no switch role/aria — the wrapping labeled control is the a11y
    // node. Otherwise a standalone display switch keeps role+state+label.
    return decorative
      ? <span aria-hidden className={trackCls} style={trackStyle}>{knobEl}</span>
      : <span role="switch" aria-checked={on} aria-label={label} className={trackCls} style={trackStyle}>{knobEl}</span>
  }
  return (
    <button
      type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled}
      onClick={() => onChange(!on)}
      className={`${trackCls} disabled:cursor-not-allowed disabled:opacity-40`}
      style={trackStyle}
    >
      {knobEl}
    </button>
  )
}
