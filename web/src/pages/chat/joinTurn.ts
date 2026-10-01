/** A tab joining a turn it did not start.
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
 *
 *  The turn's opening status ("Thinking…") is one of those frames. Without it the other tab
 *  joined only on the first word of the reply, so while the model had not started answering (a
 *  busy local model can take minutes, or time out first) it showed nothing of the turn at all.
 */

/** Frames that are only ever sent while a turn is being answered: `chat_status` is the status
 *  the gateway sends as each turn starts, and the rest are the answer. An approval is not one of
 *  them: it is keyed by its own id and can carry no session. */
const TURN_FRAMES = new Set(['chat_status', 'chat_chunk', 'chat_thinking', 'tool_call', 'tool_result'])

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
