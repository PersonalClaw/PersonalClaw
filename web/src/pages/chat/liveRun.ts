/** Whether a freshly-MOUNTED `ChatSession` is already inside a live run (#3444).
 *
 *  ── The window ────────────────────────────────────────────────────────────────
 *  Sending on a brand-new chat CREATES the session, and `ensureSession` navigates
 *  `new` → `chat/<key>`, which re-keys `ChatSession` and REMOUNTS it. The
 *  replacement instance's `streaming` was `useState(false)` while the run its own
 *  predecessor had just dispatched was still live, so for one async round trip —
 *  until the mount load's `d.running` came back — the composer's action button read
 *  **"Send message"**: an idle state it was not in.
 *
 *  `resolveSendButton` was already correct (it re-labels to "Steer — send into the
 *  running turn" while streaming). Its PRECONDITION was wrong.
 *
 *  ── Why that lost a message ───────────────────────────────────────────────────
 *  `send()` branches on the same flag (through `streamingRef`). In the window it
 *  therefore took the FRESH-TURN path: it painted an optimistic user turn and
 *  posted with no `queue_mode`. The server, which knows the run is live, did not
 *  start a turn — it queued the message and answered `{queued: true}`, a response
 *  the fresh-turn path does not read. So the transcript held a bubble for a turn
 *  that was never dispatched, and the user's message lived only in a server-side
 *  queue. On the steer path there is no phantom bubble at all: the message is
 *  injected into the running turn and echoed as a `steered` chip (or, when the
 *  runtime has no drain seam, queued with a `queue_push` chip). Either way it is
 *  on screen, which is the outcome #3444 asks for.
 *
 *  ── Why the handoff is owned by ChatPage ──────────────────────────────────────
 *  The instance that issued the send is destroyed by the navigate its own send
 *  caused, so the fact cannot live in it. `ChatPage` spans the boundary (the route
 *  wrapper is keyed on the route name), which is exactly the reasoning
 *  `sessionDelivery.ts` records for the pending routing suggestion — the sibling
 *  defect from this same remount. Identity resolves through the same one resolver
 *  rather than a second comparison.
 */

import { deliverableToOpenSession } from './sessionDelivery'
import type { HistMsg } from './chatTypes'

/** Whether a mount of `open` must start out streaming, given the session key a run
 *  was dispatched for. `''` means nothing was handed off. */
export function streamingAtMount(liveRun: string, open: string | null): boolean {
  if (!liveRun) return false
  return deliverableToOpenSession(liveRun, open)
}

/** Whether the handed-off mount's first snapshot was read before the send it was handed off
 *  from reached the server — the seed's latest user message is not in it.
 *
 *  `ensureSession` caches the just-sent message as the replacement's seed, navigates, and only
 *  then posts it, so the replacement's first read can land on either side of the post. Before
 *  it, the snapshot is honest and simply old: adopting it would erase the user's own message.
 *  After it, the snapshot holds the message and, because the gateway appends the message and
 *  starts the turn in one step, a `running: false` there means that turn has already ENDED —
 *  its `chat_done` went out before this instance was listening. Identity is the
 *  message's client stamp, which the gateway stores verbatim. */
export function snapshotPredatesSend(seed: readonly HistMsg[] | null | undefined, snapshot: readonly HistMsg[]): boolean {
  const sent = seed ? [...seed].reverse().find((m) => m.role === 'user') : undefined
  if (!sent?.ts) return false
  return !snapshot.some((m) => m.role === 'user' && m.ts === sent.ts)
}
