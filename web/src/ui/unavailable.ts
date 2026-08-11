/** Spread-in props that make a control UNAVAILABLE-but-reachable instead of natively disabled.
 *
 *  A native `disabled` button is removed from the tab order, so a keyboard user cannot reach it
 *  to hear anything — they tab straight past the action they are trying to take with no way to
 *  learn what is missing. `Button` solved this with its `disabledReason` prop, but a raw
 *  `<button>` cannot inherit a prop, and there are ten of them: icon-only send buttons in chat /
 *  the activity panel / task comments / onboarding, and the Add-row submits in the security,
 *  voice, projection-rules and Ollama panels.
 *
 *  Hand-rolling `aria-disabled` + a click guard + a title ten times is how three of them end up
 *  subtly different. This is the same logic `Button` runs, extracted so both paths agree.
 *
 *      <button
 *        type="button"
 *        onClick={send}
 *        {...unavailableWhen(!draft.trim(), 'Write a comment first', { busy })}
 *      >
 *
 *  When the reason applies the control keeps its tab stop, announces itself as unavailable, and
 *  refuses the click. When `busy` is true it goes NATIVELY disabled instead: an in-flight action
 *  must not be re-clickable, and its own spinner already carries that state. When nothing is
 *  missing the returned props are empty and the button behaves exactly as before.
 *
 *  Pair it with `aria-disabled:opacity-40 aria-disabled:cursor-not-allowed` on the button — the
 *  old `disabled:opacity-40` no longer fires, because nothing sets the native attribute.
 */
export function unavailableWhen(
  /** True when the action cannot run because an input is missing. */
  missing: boolean,
  /** WHY it cannot run — a short sentence naming what to do ("Write a comment first"). */
  reason: string,
  opts?: {
    /** True while the action is in flight. Takes precedence: a running action goes natively
     *  disabled so it cannot be fired twice. */
    busy?: boolean
    /** An existing `title`; the reason is appended after an em dash rather than replacing it. */
    title?: string
  },
): {
  disabled?: true
  'aria-disabled'?: true
  title?: string
  onClickCapture?: (e: React.MouseEvent) => void
} {
  if (opts?.busy) return { disabled: true, title: opts.title }
  if (!missing) return opts?.title ? { title: opts.title } : {}
  return {
    'aria-disabled': true,
    title: [opts?.title, reason].filter(Boolean).join(' — '),
    // aria-disabled is advisory to the browser, so the click has to be refused in code.
    // CAPTURE phase: the button's own onClick would otherwise already have run.
    onClickCapture: (e) => {
      e.preventDefault()
      e.stopPropagation()
    },
  }
}
