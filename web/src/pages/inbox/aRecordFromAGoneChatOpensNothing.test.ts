import { describe, expect, it } from 'vitest'
import { refTarget } from './inboxMeta'

// A decision a deleted chat (or a Temporary chat that ended) asked for stays in the Inbox as a
// short record (`chat_forget_inbox`). It keeps the chat's key, so no agent reads it, and none of
// the refs that say where it was answered (`place_*`, `chat`), so it opens nothing: its chat is
// gone, and nothing of it is kept to show.

describe('a record kept from a chat that is gone', () => {
  it('opens nothing, though it keeps the chat’s key', () => {
    expect(refTarget({ refs: { approval: 'appr-1', session: 'dashboard:chat-a', chat_deleted: '1' } })).toBe('')
    expect(refTarget({ refs: { auto_denied: 'expired', tool: 'bash', session: 'chat-a', chat_deleted: '1' } })).toBe('')
  })

  it('a row of a chat still here goes on opening it', () => {
    expect(refTarget({ refs: { session: 'chat-b' } })).toBe('chat/chat-b')
  })
})
