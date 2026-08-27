import { motion } from 'framer-motion'
import { spring } from '../design/motion'
import { useFieldHintId } from './forms'

/** The ONE canonical on/off switch for the whole app. A pill track + a knob that
 *  springs across on toggle (bounce-tier settle). Track = primary when on, neutral
 *  when off; knob uses the on-primary ink token. role="switch" + aria-checked for
 *  a11y. Replaces ~11 hand-rolled inline-styled copies scattered across pages. */
export function Toggle({
  on, onChange, label, disabled = false, disabledReason, size = 'md', readOnly = false, decorative = false,
}: {
  on: boolean
  /** Omit + set readOnly for a display-only indicator (renders a non-interactive
   *  span, so it can sit inside a larger clickable row without nesting buttons). */
  onChange?: (v: boolean) => void
  label?: string
  disabled?: boolean
  /** WHY this switch is unavailable, when `disabled` is true — the same contract `Button` carries.
   *
   *  A natively disabled switch leaves the tab order, so a keyboard user tabs straight past it and
   *  never learns the control exists, let alone what would unlock it. Measured on
   *  `#/settings/account`: both security switches were `disabled`, `title: null`, `NOT focusable`,
   *  with the precondition ("Set a password first") living only in the row's hint prose.
   *
   *  Given a reason the switch stays REACHABLE and announces it — `aria-disabled` (semantically
   *  unavailable, still focusable) with the click suppressed in the handler, plus the reason as its
   *  `title`. Omit it and nothing changes: `disabled` stays native, which is right when the
   *  unavailability is transient (in-flight, still loading) rather than a precondition to fix. */
  disabledReason?: string
  /** 'sm' for dense rows (h-5 w-9), 'md' default (h-6 w-10). */
  size?: 'sm' | 'md'
  readOnly?: boolean
  /** Purely visual — the switch sits INSIDE an already-labeled clickable control
   *  (a wrapping <button aria-label>). Hidden from the a11y tree so it doesn't
   *  surface as a second, unnamed switch node duplicating the button. */
  decorative?: boolean
}) {
  // ── THE SENTENCE BESIDE THE SWITCH BECOMES THE SWITCH'S DESCRIPTION ─────────────────────────
  //
  // `Row`, `settingsUI`'s `Field` and `ui/forms`' `Field` all publish the id of the hint they
  // render, exactly so the control inside can claim it (`Row`'s own comment says so). Six
  // form-family primitives already claim it — `TextInput`, `TextArea`, `NumberField`, `DateInput`,
  // `Select`, `ChipInput`. Toggle was the family's one non-consumer.
  //
  // 🔴 Measured on a demo-seeded home across all 34 `#/settings/*` subpages: 61 switches render, 58
  // of them sit inside a wrapper that publishes a hint id, and **0 carried any `aria-describedby`** —
  // so every one of those 58 visible sentences was sighted-only. A screen-reader user heard
  // "Timestamps, switch, off" and none of "Display a time on each message." After this, 53 of the 58
  // resolve `aria-describedby` to the hint's exact text; the other 5 are soft-off and keep their
  // reason instead (below). Nothing else changed: the switch, hint and row `getBoundingClientRect`
  // are byte-identical before and after on all 18 panels that render a hinted switch.
  //
  // axe cannot see this: a paragraph that happens to sit beside a switch is valid HTML with no
  // rule to violate. It is SC 1.3.1 (Info and Relationships) — a relationship conveyed only by
  // proximity. It is NOT 4.1.2: the name, role and state were all present and correct.
  const hintId = useFieldHintId()
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
    // `decorative` stays undescribed on purpose — it is `aria-hidden`, so an `aria-describedby` on
    // it would point out of the a11y tree from a node the tree does not contain.
    return decorative
      ? <span aria-hidden className={trackCls} style={trackStyle}>{knobEl}</span>
      : <span role="switch" aria-checked={on} aria-label={label} aria-describedby={hintId} className={trackCls} style={trackStyle}>{knobEl}</span>
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
  // Reachable-but-unavailable only when there is a reason to announce; otherwise stay native.
  // 🪤 THE DIMMING HAS TO MOVE WITH THE SEMANTICS: `disabled:opacity-40` cannot match an
  // `aria-disabled` element, so a soft-off switch would look fully enabled while refusing to
  // toggle. Both selectors are named below (cycle 111 hit this exact trap on `Button`).
  const softOff = disabled && !!disabledReason
  // 🪤 `aria-describedby` OUTRANKS `title`, so claiming the hint unconditionally would DELETE the
  // soft-off reason from what a screen reader announces. A soft-off switch carries its reason in
  // `title` — the kit's convention, ruled cycle 37 and re-confirmed on `Button`, which measured an
  // sr-only describedby target being concatenated into the accessible NAME instead. `title` is the
  // only carrier here, and per accname a resolved `aria-describedby` wins outright.
  //
  // Measured: 7 call sites pass a `disabledReason` to a switch, and 5 of the 58 hinted switches were
  // soft-off in the seeded state — soft-off is a STATE, not a call site, so that 5 moves with the
  // precondition while the 7 does not. On 3 of the 5 the row hint and the reason say the same thing
  // ("No model bound for this use case — bind one in Models to use this." vs "No model is bound for
  // this use case — bind one in Models first"), so pointing at the hint would have traded a sentence
  // for its own paraphrase. On 2 — account's "Require a 2FA code" and inbox's "Auto-execute the
  // trivial tier" — the hint genuinely adds what the reason omits (the 2FA row's hint carries the
  // extra "verify a code works before requiring it", and the inbox row's the entire undo contract).
  //
  // The reason still wins there, and deliberately: it is the blocking fact, and the loss is
  // transient and self-healing — clear the precondition, the switch stops being soft-off, and the
  // hint becomes its description on the same render. Announcing why a control is unusable beats
  // describing what it would do if it were.
  const describedBy = softOff ? undefined : hintId
  return (
    <button
      type="button" role="switch" aria-checked={on} aria-label={label}
      aria-describedby={describedBy}
      disabled={disabled && !softOff}
      aria-disabled={softOff || undefined}
      title={softOff ? disabledReason : undefined}
      onClick={() => { if (!softOff) onChange(!on) }}
      className={`inline-flex shrink-0 items-center justify-center rounded-pill disabled:cursor-not-allowed disabled:opacity-40 aria-disabled:cursor-not-allowed aria-disabled:opacity-40 ${sm ? 'h-6 -my-0.5' : 'h-6'}`}
    >
      <span className={trackCls} style={trackStyle}>{knobEl}</span>
    </button>
  )
}
