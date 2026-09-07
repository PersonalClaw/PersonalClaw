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
 *  refuses the click. When `busy` is true it goes NATIVELY disabled and carries `aria-busy`. When
 *  nothing is missing the returned props are empty and the button behaves exactly as before.
 *
 *  🔴 THE BUSY BRANCH USED TO ANNOUNCE NOTHING, AND THIS COMMENT SAID OTHERWISE. It returned
 *  `{ disabled: true, title }` under the claim that *"its own spinner already carries that state"* —
 *  and `ui/Button` refutes that for the same spinner in its own words: the swap puts in an
 *  **`aria-hidden`** spinner, *"so sighted users see the action is in flight and everyone else got NO
 *  signal at all"*. So the one helper the raw-`<button>` tier routes in-flight state through gave a
 *  screen-reader user native `disabled` — which announces **"unavailable"**, not "working" — and, worse
 *  for this module in particular, **removed the tab stop**, throwing away the exact property the file
 *  exists to preserve. Two of the nine `busy` call sites have no spinner at all, so the old claim was
 *  not even true at the call site.
 *
 *  🪤 AND ITS OWN TEST CERTIFIED THE GAP. `unavailable.test.tsx` asserted `disabled`, then
 *  `aria-disabled` undefined, then `title` undefined — and never `aria-busy`, under a comment reading
 *  *"aria-busy already announces the state"*. A test that names the property it does not assert reads
 *  as coverage. Fixed there too.
 *
 *  🔑 The kit already ruled on this distinction; this branch was the one place that contradicted it.
 *  `ui/IconButton`: *"`disabled` says 'unavailable', `loading` says 'working'"* — a false state, and to
 *  a screen-reader user indistinguishable from a gate they can never satisfy.
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
     *  disabled so it cannot be fired twice, AND carries `aria-busy` so the state announces as
     *  "working" rather than as "unavailable". */
    busy?: boolean
    /** An existing `title`; the reason is appended after an em dash rather than replacing it. */
    title?: string
  },
): {
  disabled?: true
  'aria-disabled'?: true
  /** Set only on the busy branch. Native `disabled` alone announces the wrong state. */
  'aria-busy'?: true
  title?: string
  onClickCapture?: (e: React.MouseEvent) => void
} {
  // 🪤 `aria-busy` AND native `disabled` together, deliberately. The two say different things:
  // `disabled` stops the second click (which `IconButton`'s `off = !!disabled || loading` guard does
  // in code, but a raw <button> has no such guard), and `aria-busy` says WHY. Dropping the native
  // attribute here to keep the tab stop would trade an announcement for a double-fire.
  if (opts?.busy) return { disabled: true, 'aria-busy': true, title: opts.title }
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
