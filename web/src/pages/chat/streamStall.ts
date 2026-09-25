/** How long a silent-while-streaming window must last before the server's "no run in
 *  flight" is allowed to end the client's streaming claim.
 *
 *  It exists to exclude ONE window: a send whose dispatch has not landed yet. `send()`
 *  flips `streaming` on before it POSTs, so between the optimistic user bubble and the
 *  handler assigning `session.task` (`chat_handlers.py`) the server legitimately holds no
 *  task for the session. Clearing there would advertise "Send message" over a turn about
 *  to stream — the mirror-image lie of issue #3444, which is the defect this heals sits
 *  next to. The window is measured from the last transcript change, so it restarts on
 *  every frame a live turn paints and can only elapse once the transcript has genuinely
 *  gone quiet. */
export const STREAM_SETTLED_GRACE_MS = 3_500

/** What ChatPage logs when the reconciler settles a stream whose terminal frame never came.
 *  One string, so the e2e turn driver (`web/e2e/helpers.ts`) can refuse a turn that only
 *  completed because this net caught it (#3575). */
export const STREAM_HEAL_WARNING = 'the turn ended but its terminal frame never reached this tab'

/** What a silent streaming window means once session detail has been read back.
 *
 *  Two different stalls reach this point, and only the server can tell them apart:
 *
 *  · `settled` — the server holds no task, so nothing is in flight and the client's
 *    streaming claim is false. This is the state a LOST TERMINAL FRAME leaves behind:
 *    `chat_done` is the only frame that clears `streaming`. Its measured source was the
 *    session-create remount — sending on a brand-new chat re-keys `ChatSession`, whose socket
 *    was its own, so the replacement's was not yet listening while a fast turn streamed and
 *    ended. That gap is now closed at its cause: the tab's one socket outlives the remount,
 *    and the replacement reads the session only once it is listening and settles the
 *    handed-off claim from that read. This stays as the net for any
 *    other way a terminal frame goes missing, and it says so (`STREAM_HEAL_WARNING`) when it
 *    catches one. `chat_handlers.py` records the same gap for `routing_suggestion` (issue
 *    569) and closed it by riding the send response; a terminal frame has no second
 *    transport. So the server's `running` is read as the authority — the one reading that
 *    makes the stall recoverable whatever lost the frame, and the same reading a reconnect's
 *    resync makes on the socket-outage form of it.
 *
 *  · `recover-approval` — the server IS running, parked on an approval the client is not
 *    showing. A parked turn sends no `chat_done` either, so the stream just goes quiet; if
 *    the `approval` frame was lost or early the card never appears. Re-hydrating surfaces
 *    the persisted card.
 *
 *  · `wait` — genuinely quiet (a long model think). Leave it alone.
 *
 *  Kept out of `ChatPage` so both readings are assertable: nothing in `web/` can mount
 *  that file, and "the client corrects itself against the server" is a rule, not a render. */
export function resolveStalledStream(input: {
  /** The server's `running` for this session — a live task, not a guess. */
  serverRunning: boolean
  /** The server's `pending_approval` for this session. */
  serverPendingApproval: boolean
  /** Milliseconds the transcript has been unchanged while the client claims the stream. */
  msSinceTranscriptChange: number
}): 'settled' | 'recover-approval' | 'wait' {
  if (!input.serverRunning && input.msSinceTranscriptChange >= STREAM_SETTLED_GRACE_MS) return 'settled'
  if (input.serverPendingApproval) return 'recover-approval'
  return 'wait'
}
