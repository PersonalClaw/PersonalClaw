/** How a chat turn ended, as the gateway states it — never inferred on the page.
 *
 *  The chat's live region used to announce "Response complete." whenever the composer stopped
 *  streaming, so Stop, a failed turn and a mid-turn retry notice were all announced as a finished
 *  answer. The gateway knows which it was and says so in three places, and this is their one
 *  reader:
 *
 *  · `chat_done.outcome` — the turn's final frame (`chat_runner.terminal_outcome_for_turn`);
 *  · session detail's `last_turn_outcome` — the same fact for a tab that missed that frame
 *    (a reconnect's re-read, the stall reconciler, the mount read after a remount);
 *  · the Stop answer's `stopped` — whether THIS press stopped the turn.
 *
 *  A value this page does not know reads as `null`, and `null` announces nothing: silence is not
 *  a false statement, and an invented "complete" is exactly the defect this replaced. */

export type TurnOutcome = 'complete' | 'stopped' | 'error'

/** What the live region says while a turn runs, before any status line arrives. */
export const TURN_RESPONDING = 'Assistant is responding…'

const TURN_ENDED: Record<TurnOutcome, string> = {
  complete: 'Response complete.',
  stopped: 'Response stopped.',
  error: 'Response ended with an error.',
}

/** The live region's sentence for a turn that ended this way. */
export function turnEndedSentence(outcome: TurnOutcome): string {
  return TURN_ENDED[outcome]
}

/** Read a wire value as an outcome; anything else is `null` (not known). */
export function turnOutcomeOf(value: unknown): TurnOutcome | null {
  return value === 'complete' || value === 'stopped' || value === 'error' ? value : null
}

/** The outcome a `chat_done` frame reports. A superseded turn was stopped by the message that
 *  replaced it, which the frame also says in `outcome`; the flag is read first so the two cannot
 *  disagree on the page. */
export function chatDoneOutcome(data: { outcome?: unknown; superseded?: unknown }): TurnOutcome | null {
  return data.superseded ? 'stopped' : turnOutcomeOf(data.outcome)
}
