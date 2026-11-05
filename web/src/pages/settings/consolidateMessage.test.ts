import { describe, it, expect } from 'vitest'
import { ApiError } from '../../lib/api'
import { consolidateMessage } from './consolidateMessage'

// Consolidate now (Settings → Memory) asks the route once per chat. It said "Consolidation
// started for 2/4 sessions" and nothing of why two did not, though each refusal says why. Only a
// 200 answer counts as started, and each skip says why, from the answer.

const ok = { status: 'fulfilled', value: { ok: true } } as const
const refused = (message: string, status: number) =>
  ({ status: 'rejected', reason: new ApiError(message, status) }) as const

describe('consolidateMessage', () => {
  it('counts two started and two skipped, with why, for one 403 and one 409 among four', () => {
    const said = consolidateMessage([
      ok,
      refused('Notes Helper started this conversation without your memory, so nothing of it is kept.', 403),
      ok,
      refused('consolidation already running', 409),
    ])

    expect(said).toBe(
      'Consolidation started for 2 chats; 2 skipped: Notes Helper started this conversation without '
      + 'your memory, so nothing of it is kept (1); already running (1).',
    )
  })

  it('says a chat with no conversation kept yet (404), and counts each reason', () => {
    const said = consolidateMessage([
      refused("No conversation is kept under 'chat-9', so there is nothing to consolidate.", 404),
      refused('No such chat session.', 404),
      ok,
    ])

    expect(said).toBe('Consolidation started for 1 chat; 2 skipped: no conversation kept yet (2).')
  })

  it('says only the start when every chat started', () => {
    expect(consolidateMessage([ok, ok, ok])).toBe('Consolidation started for 3 chats.')
  })

  it('names another failure in its own words', () => {
    expect(consolidateMessage([refused('consolidator not available', 503)]))
      .toBe('Consolidation started for 0 chats; 1 skipped: consolidator not available (1).')
  })
})
