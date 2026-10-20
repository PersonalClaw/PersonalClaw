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

/** The reason a control is off because an action is in flight — ONE string, so the 74 call sites that
 *  need it cannot drift into 74 wordings of the same sentence.
 *
 *  🔑 DELIBERATELY NEUTRAL ABOUT WHOSE ACTION IT IS. Most busy gates are a shared flag: at most one
 *  button is working and its siblings are blocked BY it, and from the source alone you often cannot
 *  tell which is which — that is exactly why `busyIsNotAnnounced`'s Class D exists. "Another action is
 *  running" would be a lie on the working button; "Saving…" would be a lie on the four beside it. This
 *  sentence is true either way, which is what makes it safe to share.
 *
 *  🪤 IT IS NOT A SUBSTITUTE FOR `loading`. Where a button IS the one working and the site can say so,
 *  the answer is `loading={…}` — that publishes `aria-busy`, which announces **"working"**; this only
 *  announces "unavailable, and here is why". `busyIsNotAnnounced` asserts Class A and Class B EMPTY
 *  precisely so this cannot become the lazy option for a site that could distinguish itself. */
export const BUSY_REASON = 'An action is already in progress'

/** 🔑 WHY A BUSY `<Button>` MAY CARRY A REASON WHILE A BUSY RAW `<button>` MAY NOT — the distinction
 *  two rails stated identically and got backwards for one of the two tiers.
 *
 *  Both `ui/disabledReasonTriage.test.ts` and `ui/rawSoftOffContract.test.ts` exempt the busy class on
 *  the grounds that *"an in-flight action must not be re-clickable"*, and treat that as a property of
 *  BUSY GATES. It is not; it is a property of the TIER:
 *
 *    · `ui/Button` guards the click in code — `onClick={softOff ? (e) => e.preventDefault() : onClick}`,
 *      under its own comment *"Both paths must refuse the click."* So a busy `<Button>` given a reason
 *      goes `aria-disabled` + focusable AND STILL REFUSES THE CLICK. Nothing becomes re-clickable, and
 *      the exemption's stated ground is simply false for this tier — a second unchecked justification
 *      standing in the place of the first one that was retracted.
 *    · A raw `<button>` has no such guard, which is why `unavailableWhen`'s busy branch returns native
 *      `disabled` and says so in the trap note below. There the ground is TRUE, and it is now asserted
 *      rather than asserted-in-prose.
 *
 *  So the honest split is: the `<Button>` tier owes a reason on its busy gates (74 sites, closed with
 *  `BUSY_REASON`), and the raw tier correctly stays native. A class rule that reads across both tiers
 *  is wrong about one of them whichever way it is written. */

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
