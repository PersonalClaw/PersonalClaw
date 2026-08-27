import type { ReactNode } from 'react'
import { ApiError } from '../../lib/api'

/** The four eval panels' shared reading of a failed `/api/evals/*` call.
 *
 *  Every route under `/api/evals` answers 404 for TWO unrelated things — the substrate is
 *  switched off, and nothing has run yet — and they send a reader to two different places, so
 *  the panels have to tell them apart. `handlers/evals.py` mints a distinct code for each
 *  precisely so a client can.
 *
 *  🔴 Matched on the CODE, never on the sentence. Each panel used to ask
 *  `error.message.includes('evals_disabled')`, and the sentence the backend actually sends is
 *  "The eval substrate is off. Turn on `evals.enabled` to publish …" — which contains
 *  `evals.enabled`, not `evals_disabled`. Measured on a live gateway with the substrate off:
 *  every one of those branches missed and all four panels rendered "Couldn't load your …" with
 *  a Retry that could not succeed. `ApiError.code` is the backend's own stable value, so the
 *  copy above can be reworded freely without silently re-breaking the branch.
 */
export function evalsCode(error: unknown, code: string): boolean {
  return error instanceof ApiError && error.code === code
}

/** The substrate is switched off — one paragraph, four callers.
 *
 *  Deliberately NOT a `LoadError`: that primitive's whole shape is "a transient failure, try
 *  again", and it offers a Retry. A configuration switch is neither transient nor retryable —
 *  re-fetching answers 404 forever until the user changes a setting — so this states the one
 *  action instead of offering a button that cannot work.
 *
 *  The action is the CLI verb rather than a link to `#/settings`, because `evals.enabled` has
 *  no control on that page (it is in the PATCH allowlist, `handlers/core.py:666`, but no
 *  surface writes it). Pointing at a settings screen with nothing on it is a promise the app
 *  does not keep; `personalclaw config set` is the path that actually exists, and it is the
 *  same form the "nothing has run yet" states of these panels already use.
 *
 *  `children` is the per-panel purpose: what turning it on lets the user MEASURE. Deliberately the
 *  outcome and not the panel's run command — that command belongs to the "nothing has run yet"
 *  state, which is exactly where the user lands next. Naming both here would put two commands in
 *  one paragraph and blur the two states into one.
 *
 *  The purpose leads the sentence rather than the state, because there is ONE switch and so all
 *  four panels always render this together. Measured at 1440px with the four of them stacked:
 *  state-first gave four consecutive lines opening on the same nine words, which reads as a
 *  stutter and makes the one part that differs the easiest to skip. Purpose-first varies every
 *  opening, and "switch it on" says the substrate is off just as plainly. The four HEADINGS are
 *  kept (rather than collapsing to one page-level notice) because they are what teaches a new
 *  user that these four measurements exist at all.
 */
export function EvalsOffNotice({ children }: { children: ReactNode }) {
  return (
    <p className="text-on-surface-low text-[0.8125rem]">
      To {children}, switch the eval substrate on:{' '}
      <code className="text-on-surface-var">personalclaw config set evals.enabled true</code>
    </p>
  )
}
