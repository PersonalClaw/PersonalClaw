import { hasApiCode } from '../../lib/api'

/** Why New session was refused, said against the sessions that were open when it was asked.
 *
 *  A refusal is about THOSE sessions: once one opens or closes, what New session would get has
 *  changed, and a refusal for the limit is plainly wrong once a close has freed a place. So it is
 *  shown only while the same sessions are open (`standing`), which each render works out from the
 *  tabs it draws. Clearing it in an effect on the tab count ran one render late: that render had
 *  the tab gone while the refusal and the unavailable New session still showed. */
export interface OpenRefusal {
  /** The gateway's sentence, or a plain one when the request failed without one. */
  text: string
  /** Refused for the limit (`terminal_session_limit`), so asking again gets the same answer. */
  limit: boolean
  /** The sessions that were open when it was asked (`sessionsKey`). */
  against: string
}

/** The open sessions as one comparable value: their ids in strip order. A rename keeps it. */
export function sessionsKey(tabs: readonly { id: string }[]): string {
  return tabs.map((t) => t.id).join('\n')
}

/** The refusal a failed create is, against the sessions open when it was asked. */
export function openRefusal(e: unknown, asked: readonly { id: string }[]): OpenRefusal {
  return {
    text: e instanceof Error ? e.message : 'Could not open a terminal session.',
    limit: hasApiCode(e, 'terminal_session_limit'),
    against: sessionsKey(asked),
  }
}

/** The refusal while the sessions it was said against are the ones open, else null. */
export function standing(refusal: OpenRefusal | null, tabs: readonly { id: string }[]): OpenRefusal | null {
  return refusal !== null && refusal.against === sessionsKey(tabs) ? refusal : null
}
