import type { ChatReceipt } from '../lib/api'

/** The receipt the gateway answers a send with (`POST /api/chat`), for a test's `api.sendChat`
 *  mock: the message the page named (its `ids.messageId`), taken in now, as *disposition* says,
 *  with *patch* over it. */
export function receiptFor(
  disposition: ChatReceipt['disposition'],
  patch: Partial<ChatReceipt> = {},
): (...args: unknown[]) => Promise<ChatReceipt> {
  return (_message, session, _meta, _mode, _origin, ids) => {
    const messageId = (ids as { messageId?: string } | undefined)?.messageId ?? 'msg_00000000000000000000000000'
    return Promise.resolve({
      session: String(session),
      message_id: messageId,
      accepted_at: new Date().toISOString(),
      disposition,
      turn_message_id: messageId,
      duplicate: false,
      ...patch,
    })
  }
}
