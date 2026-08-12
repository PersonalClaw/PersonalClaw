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
  // 🪤 THE `sm` TRACK IS 20px TALL, AND A TARGET MUST BE 24px (WCAG 2.2 SC 2.5.8).
  //
  // The button used to BE the track, so an `sm` switch was a 36×20 target. axe reported
  // `[serious] target-size: Target has insufficient size (36px by 20px, should be at least
  // 24px by 24px)` on five of them at once, on the settings hub.
  //
  // SC 2.5.8's undersized-target exception does NOT rescue it, and the reason is easy to measure
  // wrongly: switch-to-switch centres are 34–107px apart, which looks like ample spacing. But the
  // exception requires the 24px circle to clear *another target*, and on the settings hub each
  // switch sits inside a full-card nav overlay (`bento.tsx`: a `<button aria-label="Open … settings"
  // class="absolute inset-0">`). The circle is inside that button by construction, so the exception
  // can never apply to a control embedded in a larger clickable surface.
  //
  // So the button becomes a transparent 24px-tall hit box and the TRACK moves inside it. The track
  // still renders at 20px, and `-my-0.5` gives the extra 4px back to the layout, so the switch
  // occupies exactly the space it did before: same visual, same row heights, a reachable target.
  // `md` is already 24px tall and needs no correction — its wrapper is the same height as its track.
  return (
    <button
      type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled}
      onClick={() => onChange(!on)}
      className={`inline-flex shrink-0 items-center justify-center rounded-pill disabled:cursor-not-allowed disabled:opacity-40 ${sm ? 'h-6 -my-0.5' : 'h-6'}`}
    >
      <span className={trackCls} style={trackStyle}>{knobEl}</span>
    </button>
  )
}
