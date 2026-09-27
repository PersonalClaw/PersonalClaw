import { useSyncExternalStore } from 'react'

/** This tab's session has ended — and what the gateway said about it (ledger 255).
 *
 *  A device signed out elsewhere, or whose sign-in ran out, used to find out one panel at a time:
 *  every read failed with the gateway's bare `{"error": "token superseded"}`, each panel drew its
 *  own "Couldn't load…", and the tab kept polling a gateway that would refuse it forever. Now the
 *  FIRST refusal that says so (`403` + `X-Auth-Required: true`) is recorded here once, the app shell
 *  swaps itself for one signed-out screen that shows the gateway's sentence, and the api client
 *  stops sending requests that can only be refused. */
export interface SignedOut {
  /** What to show: the gateway's own sentence (why, when, how to sign back in) when it gave one. */
  message: string
  /** `session_signed_out` / `session_expired`, or '' for a refusal with no known session behind it. */
  code: string
  /** The `detail.reason` the gateway named (`signed_out_elsewhere`, `limit`, `expired`, …), or ''. */
  reason: string
}

/** The sentence for a refusal the gateway could not explain: no session it recognises was
 *  presented at all (a cleared cookie, a reset signing key). True in every such case. */
export const NOT_SIGNED_IN =
  'This browser is no longer signed in to PersonalClaw. Sign in again to continue.'

let current: SignedOut | null = null
const listeners = new Set<() => void>()

/** True when *r* is the gateway refusing THIS browser's session (not a route refusing an action). */
export function isSignedOutRefusal(r: Response): boolean {
  return r.status === 403 && r.headers.get('X-Auth-Required') === 'true'
}

/** Record that the session ended. The first report wins: later refusals say the same thing
 *  less well (a poll that races the first read has no more to add). */
export function reportSignedOut(next: SignedOut): void {
  if (current) return
  current = {
    ...next,
    message: next.code === 'session_signed_out' || next.code === 'session_expired'
      ? next.message
      : NOT_SIGNED_IN,
  }
  for (const listener of [...listeners]) listener()
}

/** The recorded ending, or null while the session is live. */
export function signedOutState(): SignedOut | null {
  return current
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

/** The ending, as React state: null until the session ends, then the one `SignedOut`. */
export function useSignedOut(): SignedOut | null {
  return useSyncExternalStore(subscribe, signedOutState, signedOutState)
}

/** Tests only: forget the ending, so one test's sign-out does not leak into the next. */
export function resetSignedOutForTests(): void {
  current = null
}
