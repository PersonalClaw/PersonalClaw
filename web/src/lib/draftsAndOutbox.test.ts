import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './api'
import { memoryBackend, setStoreForTests } from './browserStore'
import { MAX_DRAFTS, MAX_DRAFT_CHARS, clearAllDrafts, clearDraft, draftSessions, loadDraft, saveDraft } from './drafts'
import { MAX_PER_CHAT, STALE_AFTER_MS, clearAllWaiting, clearChat, flush, hold, isHeld, pending, type HeldSend } from './outbox'

// What this browser keeps of a chat beside the gateway (#4012): each chat's draft, and the sends
// the gateway could not take. Driven on the in-memory backend the IndexedDB adapter is swapped for.

beforeEach(() => setStoreForTests(memoryBackend()))
afterEach(() => setStoreForTests(null))

const send = (id: string, session = 'chat-1', message = `message ${id}`): HeldSend =>
  ({ client_message_id: id, session, message })

describe('a chat draft', () => {
  it('is kept per chat, and gives back what was typed, attached and mentioned', async () => {
    await saveDraft('chat-1', {
      text: 'Half a thought about the', attachments: [{ path: 'uploads/plan.md', name: 'plan.md', size: 12, mime: 'text/markdown' }],
      mentions: [{ target: 'file', ref: 'notes/beds.md', label: 'beds.md' }],
      pastes: [{ seq: 1, lines: 3, content: 'a\nb\nc' }],
    }, 1000)
    await saveDraft('chat-2', { text: 'Another chat', attachments: [], mentions: [], pastes: [] }, 2000)
    expect(await loadDraft('chat-1')).toEqual({
      text: 'Half a thought about the',
      attachments: [{ path: 'uploads/plan.md', name: 'plan.md', size: 12, mime: 'text/markdown' }],
      mentions: [{ target: 'file', ref: 'notes/beds.md', label: 'beds.md' }],
      pastes: [{ seq: 1, lines: 3, content: 'a\nb\nc' }],
      updated_at: 1000,
    })
    expect((await loadDraft('chat-2'))?.text).toBe('Another chat')
    expect(await draftSessions()).toEqual(new Set(['chat-1', 'chat-2']))
  })

  it('goes when it is emptied, cleared, or on sign-out', async () => {
    await saveDraft('chat-1', { text: 'one', attachments: [], mentions: [], pastes: [] })
    await saveDraft('chat-2', { text: 'two', attachments: [], mentions: [], pastes: [] })
    await saveDraft('chat-1', { text: '   ', attachments: [], mentions: [], pastes: [] })
    expect(await loadDraft('chat-1')).toBeNull()
    await clearDraft('chat-2')
    expect(await loadDraft('chat-2')).toBeNull()
    await saveDraft('chat-3', { text: 'three', attachments: [], mentions: [], pastes: [] })
    await clearAllDrafts()
    expect(await draftSessions()).toEqual(new Set())
  })

  it('keeps the newest MAX_DRAFTS, of at most MAX_DRAFT_CHARS each', async () => {
    for (let i = 0; i <= MAX_DRAFTS; i++) await saveDraft(`chat-${i}`, { text: `draft ${i}`, attachments: [], mentions: [], pastes: [] }, i)
    const kept = await draftSessions()
    expect(kept.size).toBe(MAX_DRAFTS)
    expect(kept.has('chat-0')).toBe(false)
    expect(kept.has(`chat-${MAX_DRAFTS}`)).toBe(true)
    await saveDraft('long', { text: 'x'.repeat(MAX_DRAFT_CHARS + 50), attachments: [], mentions: [], pastes: [] })
    expect((await loadDraft('long'))?.text).toHaveLength(MAX_DRAFT_CHARS)
  })
})

describe('a send waiting for the gateway', () => {
  it('waits only through a failure that never reached the gateway, or a restart', () => {
    expect(isHeld(new TypeError('Failed to fetch'))).toBe(true)
    expect(isHeld(new ApiError('Restarting.', 503, 'gateway_restarting'))).toBe(true)
    expect(isHeld(new ApiError('Busy.', 503, 'session_busy'))).toBe(false)
    expect(isHeld(new ApiError('No.', 400, 'invalid_request'))).toBe(false)
    expect(isHeld(new DOMException('stopped', 'AbortError'))).toBe(false)
  })

  it('is posted again in order, once each, with its own id', async () => {
    await hold(send('msg_a'), true, 1)
    await hold(send('msg_b'), true, 2)
    await hold(send('msg_a'), true, 3)
    const post = vi.fn().mockResolvedValue({ duplicate: false })
    const result = await flush('chat-1', post, async () => true, { now: 10 })
    expect(post.mock.calls.map(([s]) => s.client_message_id)).toEqual(['msg_a', 'msg_b'])
    expect(result.sent.map((i) => i.client_message_id)).toEqual(['msg_a', 'msg_b'])
    expect(await pending('chat-1')).toEqual([])
    expect(await flush('chat-1', post, async () => true)).toEqual({ sent: [], refused: [], dropped: [], waiting: [] })
    expect(post).toHaveBeenCalledTimes(2)
  })

  it('stops at a send the gateway still cannot take, and what is behind it keeps its place', async () => {
    await hold(send('msg_a'), true, 1)
    await hold(send('msg_b'), true, 2)
    const post = vi.fn().mockRejectedValueOnce(new TypeError('Failed to fetch'))
    const result = await flush('chat-1', post, async () => true, { now: 10 })
    expect(post).toHaveBeenCalledTimes(1)
    expect(result.waiting.map((i) => i.client_message_id)).toEqual(['msg_a', 'msg_b'])
    expect((await pending('chat-1')).map((i) => i.client_message_id)).toEqual(['msg_a', 'msg_b'])
  })

  it('is never kept to send again once it is answered with a refusal', async () => {
    await hold(send('msg_a'), true, 1)
    const refusal = new ApiError('That chat is busy.', 409, 'session_busy')
    const result = await flush('chat-1', vi.fn().mockRejectedValue(refusal), async () => true, { now: 10 })
    expect(result.refused).toEqual([{ item: expect.objectContaining({ client_message_id: 'msg_a' }), error: refusal }])
    expect(await pending('chat-1')).toEqual([])
  })

  it('asks after a day instead of going on its own, and goes on Send now', async () => {
    await hold(send('msg_a'), true, 0)
    const post = vi.fn().mockResolvedValue({})
    const later = await flush('chat-1', post, async () => true, { now: STALE_AFTER_MS })
    expect(post).not.toHaveBeenCalled()
    expect(later.waiting.map((i) => i.client_message_id)).toEqual(['msg_a'])
    const sendNow = await flush('chat-1', post, async () => true, { now: STALE_AFTER_MS, includeStale: true })
    expect(sendNow.sent).toHaveLength(1)
  })

  it('is never sent into a chat deleted meanwhile, and a chat\'s first message still goes', async () => {
    await hold(send('msg_a'), true, 1)
    await hold(send('msg_first', 'chat-new'), false, 1)
    const post = vi.fn().mockResolvedValue({})
    const deleted = await flush('chat-1', post, async () => false, { now: 10 })
    expect(post).not.toHaveBeenCalled()
    expect(deleted.dropped.map((i) => i.client_message_id)).toEqual(['msg_a'])
    expect(await pending('chat-1')).toEqual([])
    const first = await flush('chat-new', post, async () => false, { now: 10 })
    expect(first.sent.map((i) => i.client_message_id)).toEqual(['msg_first'])
  })

  it('goes unsent when its chat is deleted, or on sign-out, and holds at most MAX_PER_CHAT', async () => {
    await hold(send('msg_a'), true)
    await hold(send('msg_b', 'chat-2'), true)
    await clearChat('chat-1')
    expect(await pending('chat-1')).toEqual([])
    expect(await pending('chat-2')).toHaveLength(1)
    await clearAllWaiting()
    expect(await pending('chat-2')).toEqual([])
    for (let i = 0; i < MAX_PER_CHAT; i++) expect(await hold(send(`msg_${i}`), true, i)).toBe(true)
    expect(await hold(send('msg_over'), true, MAX_PER_CHAT)).toBe(false)
  })
})
