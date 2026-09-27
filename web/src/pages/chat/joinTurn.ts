/** A tab joining a turn it did not start (F-42).
 *
 *  Two tabs on one chat: the tab that sends adds the question itself, so the server never echoes
 *  a sent message back, and it is the only tab that turns its Stop control on. Measured before
 *  this: the other tab painted the reply's chunks onto the previous answer, with no question
 *  above them and no way to stop the turn. The same was true of a turn a channel, an app or a
 *  trigger started in a chat this tab had open.
 *
 *  So the first frame of a turn this tab is not following makes it read the chat once. The
 *  snapshot holds the question and the answer so far, and that frame and every one after it
 *  replay on top of it (`readSnapshot` holds them), exactly as when a chat is opened mid-turn.
 */

/** Frames that are only ever sent while a turn is being answered. `chat_status` is not one of
 *  them (it also clears a status after the turn), and neither is an approval, which is keyed by
 *  its own id and can carry no session. */
const TURN_FRAMES = new Set(['chat_chunk', 'chat_thinking', 'tool_call', 'tool_result'])

/** Whether `frame` is the first sign of a turn in the chat this tab has `open` that the tab is not
 *  `following` — it did not start it and has not joined it. */
export function joinsATurnStartedElsewhere(
  frame: { type: string; data?: Record<string, unknown> },
  open: string | null,
  following: boolean,
): boolean {
  if (following || !open || !TURN_FRAMES.has(frame.type)) return false
  return frame.data?.session === open
}
