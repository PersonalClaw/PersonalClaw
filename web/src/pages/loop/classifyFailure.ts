// What the Loop composer says when `POST /api/loops/classify` refuses (#3470).
//
// 🔴 THE SERVER ALREADY WROTE THE ANSWER AND THE COMPOSER THREW IT AWAY. On a home with no
// provider bound the route answers 409 `model_unresolved` with a complete, actionable
// sentence — "No model provider resolves for use case 'background'. Connect a model in
// Settings → Models to analyze tasks." — worded deliberately at `loop_routes.py` so
// `isNoModelSetupError` recognises it. `submit()` called
// `api.classifyULoop(…).catch(() => null)` and rendered a fixed sentence from the `null`:
//
//     Could not analyze the task — is a model configured?
//
// Two narrow things were wrong with that, and neither is a swallow — the failure was
// terminal and visible, nothing was fabricated. It ASKED THE USER A QUESTION THE SERVER
// HAD JUST ANSWERED, putting the diagnosis on the person least able to do it; and it DROPPED
// `Settings → Models`, which is the entire remediation. A shorter sentence is not a kindness
// when the longer one is the one that tells you what to do.
//
// 🪤 THE WRITTEN FALLBACK STAYS, AND IT IS NOT A DUAL PATH. `readableErrText` returns `''`
// for a closed opaque set (`Failed to fetch`, `Load failed`, `HTTP 500`) — a rejection that
// genuinely says nothing a user can act on, because the request never reached a backend that
// could author a sentence. Relaying `''` would paint an EMPTY alert box, which is worse than
// the question. So the server's sentence wins outright whenever there is one, and the local
// copy answers only the case the server was silent about. That is the calling convention
// `readableErrText` documents for itself: `readableErrText(e) || 'their sentence'`.
//
// 🔑 A FUNCTION RATHER THAN AN INLINE `||`, for the same reason `ui/composer/optimizeOutcome`
// owns the composer's other async-action copy: the decision "whose sentence wins" is the
// thing worth testing, and an inline expression inside a 75-line `submit()` can only be
// checked by reading the source.

import { readableErrText } from '../../lib/errText'

/** The sentence for a classify rejection that carried nothing readable — a dropped
 *  connection, not a refusal. Exported so the test can assert the page does not re-type it,
 *  which is how the server's sentence got overwritten in the first place. */
export const CLASSIFY_OPAQUE_FALLBACK = 'Could not analyze the task — is a model configured?'

/** The error sentence for a failed `classifyULoop`: the backend's own message when it
 *  authored one, else the written fallback above. Never `''` — an empty alert is a box with
 *  no news in it. */
export function classifyFailure(e: unknown): string {
  return readableErrText(e) || CLASSIFY_OPAQUE_FALLBACK
}
