import type { RunState } from '../../lib/api'

/** The meta line under a chat-list row: "N messages · running · model".
 *
 *  The count is the server's real one (`ConversationLog.list_sessions` / the live
 *  session's `message_count`), or `null` when the server could not read the file at
 *  all. A null count is left out: the line never shows a guess, and never "null
 *  messages". (The server used to send `size / 200` for any chat not in memory, so a
 *  five-message chat listed as "8 messages".) "running" is said while its run state names a turn. */
export function sessionRowMeta(s: { messages: number | null; run_state?: Pick<RunState, 'turn'>; model?: string }): string {
  const parts: string[] = []
  if (typeof s.messages === 'number') parts.push(`${s.messages} message${s.messages === 1 ? '' : 's'}`)
  if (s.run_state?.turn) parts.push('running')
  if (s.model) parts.push(s.model)
  return parts.join(' · ')
}
