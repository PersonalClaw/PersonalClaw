/** What a failed turn's error bar says.
 *
 *  The gateway composes a sentence for every turn failure — including the ones whose exception
 *  carries no message (`httpx.ReadError('')`, every timeout), which it used to send as
 *  `content: ""`. A transcript persisted before that fix still holds the empty string, and a
 *  live frame is only as good as its sender. Both writers of an error segment — the live
 *  `chat_message` frame and the reload hydration — therefore go through here, so an EMPTY
 *  message is treated exactly like a MISSING one. `??` alone caught only the missing case,
 *  which is how a failed turn rendered as a red bar with nothing in it. */
export const TURN_ERROR_WITHOUT_REASON =
  'This turn failed, and no reason was recorded. Try again; if it keeps failing, check the gateway log.'

export function turnErrorText(content: unknown): string {
  const text = content == null ? '' : String(content)
  return text.trim() ? text : TURN_ERROR_WITHOUT_REASON
}
