import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { turnText, type ChatTurn } from '../chatTypes'
import type { ChatStream } from '../useChatStream'
import { receiptFor } from '../../../test/chatReceipt'
import { ApiError } from '../../../lib/api'
import { memoryBackend, setStoreForTests } from '../../../lib/browserStore'
import { loadDraft, saveDraft } from '../../../lib/drafts'
import { STALE_AFTER_MS, hold, pending } from '../../../lib/outbox'

// ── What a chat keeps through a reload, and a send that waits for PersonalClaw (#4012) ──────────
//
// The composer keeps each chat's draft in this browser and gives it back when the chat opens
// again; a Temporary or Incognito chat keeps none. A send PersonalClaw could not take (the request
// never reached it, or it was restarting) waits as "Waiting to send" and goes once, with the same
// id, when the chat's socket is open again; one it had in fact taken answers `duplicate: true` and
// shows one bubble. Any other refusal is an answer, never kept to send again. Driven through the
// real `useChatComposer` on the in-memory store, the socket's open played by the test.

const KEY = 'chat-7-kept'

const h = vi.hoisted(() => ({
  sendChat: vi.fn(),
  chatSessionDetail: vi.fn(),
  createChatSession: vi.fn(),
  socketOpen: [] as ((connected: boolean) => void)[],
}))

vi.mock('../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/api')>()
  const api = new Proxy({ sendChat: h.sendChat, chatSessionDetail: h.chatSessionDetail, createChatSession: h.createChatSession } as Record<string, unknown>, {
    get(t, p: string) { return p in t ? t[p] : (t[p] = () => Promise.resolve({})) },
  })
  return { ...real, api }
})
vi.mock('../../../lib/useChatSocket', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/useChatSocket')>()
  return { ...real, useChatSocket: (_m: unknown, _r: unknown, onStatus?: (c: boolean) => void) => { if (onStatus) h.socketOpen.push(onStatus) } }
})
vi.mock('../../../app/appSdk', () => ({ notify: vi.fn() }))
vi.mock('../../../lib/useComposerData', () => ({ useComposerData: () => ({ discovered: {}, agents: [], providers: [], models: [], ready: true }) }))
vi.mock('../../../app/usePlatform', () => ({ usePlatform: () => 'linux' }))
vi.mock('../../../ui/composer/useScreenShare', () => ({
  useScreenShare: () => ({ available: false, sharing: false, disabledReason: '', toggle: vi.fn(), captureAndStage: vi.fn(), lastFrame: () => null }),
}))

let useChatComposer: typeof import('./useChatComposer')['useChatComposer']

function mount({ sessionId = KEY as string | null } = {}) {
  return renderHook(() => {
    const [turns, setTurns] = useState<ChatTurn[]>([])
    const stream = {
      sessionRef: { current: sessionId }, turns, setTurns,
      patchLastAssistant: (fn: (s: ChatTurn['segments']) => ChatTurn['segments']) => setTurns((prev) => prev.map((t, i) => (i === prev.length - 1 ? { ...t, segments: fn(t.segments) } : t))),
      dropTextRun: vi.fn(), streamingRef: { current: false }, markStreaming: vi.fn(), sayTurnEnded: vi.fn(),
      turnSeqRef: { current: 0 }, stoppedTurnRef: { current: false }, takesSteersRef: { current: false },
      setMissing: vi.fn(), setSteered: vi.fn(), followups: [], setFollowups: vi.fn(), followupsNeed: '', setFollowupsNeed: vi.fn(),
      checkWorkOffer: null, setCheckWorkOffer: vi.fn(), sessionBindingRef: { current: null }, bindingToldRef: { current: false }, bindingNonce: 0,
    } as unknown as ChatStream
    const c = useChatComposer({
      sessionId, navigate: vi.fn(), seed: '', initialProjectId: '', initialAgent: '', pendingRouting: null, setRoutingSuggestion: vi.fn(),
      setLiveRun: vi.fn(), started: turns.length > 0, stream, followNewTurn: vi.fn(), oweSpokenReply: vi.fn(),
    })
    return { c, turns }
  })
}

/** The chat's socket opens: on a first connect, or again after a drop. */
async function socketOpens() {
  await act(async () => { for (const f of h.socketOpen.splice(0)) f(true) })
}

beforeEach(async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  setStoreForTests(memoryBackend())
  h.sendChat.mockReset().mockImplementation(receiptFor('started'))
  h.chatSessionDetail.mockReset().mockResolvedValue({ key: KEY, messages: [] })
  h.createChatSession.mockReset().mockResolvedValue({ key: KEY })
  h.socketOpen.length = 0
  ;({ useChatComposer } = await import('./useChatComposer'))
})
afterEach(() => { cleanup(); setStoreForTests(null); vi.useRealTimers() })

describe('a chat draft', () => {
  it('survives a reload: what she typed half-way is back when the chat opens again', async () => {
    const first = mount()
    await act(async () => {})  // the chat opens: its kept draft (none yet) is read
    act(() => first.result.current.c.setInput('Half a thought about the release'))
    await act(async () => { await vi.advanceTimersByTimeAsync(600) })
    expect((await loadDraft(KEY))?.text).toBe('Half a thought about the release')
    first.unmount()

    const again = mount()
    await waitFor(() => expect(again.result.current.c.input).toBe('Half a thought about the release'))
    expect(again.result.current.c.keptDraft.restored).toBe(true)
    act(() => again.result.current.c.keptDraft.discard())
    await waitFor(async () => expect(await loadDraft(KEY)).toBeNull())
  })

  it('is never kept for a Temporary chat, and switching a chat to one clears what it had', async () => {
    await saveDraft(KEY, { text: 'kept before', attachments: [], mentions: [], pastes: [] })
    const m = mount()
    act(() => m.result.current.c.setMemoryMode('temporary'))
    await waitFor(async () => expect(await loadDraft(KEY)).toBeNull())
    act(() => m.result.current.c.setInput('Not to be kept'))
    await act(async () => { await vi.advanceTimersByTimeAsync(600) })
    expect(await loadDraft(KEY)).toBeNull()
    expect(m.result.current.c.input).toBe('Not to be kept')
  })

  it('goes with its send', async () => {
    const m = mount()
    await act(async () => {})
    act(() => m.result.current.c.setInput('Ship it'))
    await act(async () => { await vi.advanceTimersByTimeAsync(600) })
    expect((await loadDraft(KEY))?.text, 'kept while it is typed').toBe('Ship it')
    await act(async () => { await vi.advanceTimersByTimeAsync(600) })
    await act(async () => { await m.result.current.c.send() })
    await act(async () => { await vi.advanceTimersByTimeAsync(600) })
    expect(await loadDraft(KEY)).toBeNull()
  })
})

describe('a send PersonalClaw could not take', () => {
  it('waits while the connection is down, and goes once with the same id when the socket is back', async () => {
    h.sendChat.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    const m = mount()
    act(() => m.result.current.c.setInput('Are the backups done?'))
    await act(async () => { await m.result.current.c.send() })
    expect(m.result.current.turns.map(turnText)).toEqual([])
    await waitFor(() => expect(m.result.current.c.waitingSends.waiting.map((i) => i.message)).toEqual(['Are the backups done?']))
    const held = h.sendChat.mock.calls[0][5].messageId

    await socketOpens()
    await waitFor(() => expect(h.sendChat).toHaveBeenCalledTimes(2))
    expect(h.sendChat.mock.calls[1][0]).toBe('Are the backups done?')
    expect(h.sendChat.mock.calls[1][5]).toEqual({ messageId: held })
    await waitFor(() => expect(m.result.current.c.waitingSends.waiting).toEqual([]))
    expect(m.result.current.turns.map(turnText)).toEqual(['Are the backups done?'])
    await socketOpens()
    expect(h.sendChat).toHaveBeenCalledTimes(2)
  })

  it('shows one bubble when the gateway had in fact taken it (duplicate: true)', async () => {
    h.sendChat.mockRejectedValueOnce(new TypeError('Failed to fetch')).mockImplementationOnce(receiptFor('started', { duplicate: true }))
    const m = mount()
    act(() => m.result.current.c.setInput('Once only'))
    await act(async () => { await m.result.current.c.send() })
    await socketOpens()
    await waitFor(() => expect(m.result.current.c.waitingSends.waiting).toEqual([]))
    expect(m.result.current.turns.map(turnText)).toEqual([])
  })

  it('is kept through a restarting gateway, and sent on reconnect', async () => {
    h.sendChat.mockRejectedValueOnce(new ApiError('PersonalClaw is restarting.', 503, 'gateway_restarting'))
    const m = mount()
    act(() => m.result.current.c.setInput('After the restart'))
    await act(async () => { await m.result.current.c.send() })
    await waitFor(() => expect(m.result.current.c.waitingSends.waiting).toHaveLength(1))
    await socketOpens()
    await waitFor(() => expect(m.result.current.c.waitingSends.waiting).toEqual([]))
    expect(h.sendChat).toHaveBeenCalledTimes(2)
  })

  it('is never kept to send again once it is refused', async () => {
    h.sendChat.mockRejectedValueOnce(new ApiError('That chat is busy.', 409, 'session_busy'))
    const m = mount()
    act(() => m.result.current.c.setInput('Refused'))
    await act(async () => { await m.result.current.c.send() })
    expect(await pending(KEY)).toEqual([])
    await socketOpens()
    expect(h.sendChat).toHaveBeenCalledTimes(1)
  })

  it('asks Send now or remove after a day, and goes only when asked', async () => {
    await hold({ client_message_id: 'msg_01aaaaaaaaaaaaaaaaaaaaaaaa', session: KEY, message: 'From yesterday' }, true, Date.now() - STALE_AFTER_MS)
    const m = mount()
    await waitFor(() => expect(m.result.current.c.waitingSends.stale).toBe(true))
    await socketOpens()
    expect(h.sendChat).not.toHaveBeenCalled()
    await act(async () => { await m.result.current.c.waitingSends.sendNow() })
    expect(h.sendChat).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(m.result.current.c.waitingSends.waiting).toEqual([]))
  })

  it('is never sent into a chat deleted meanwhile', async () => {
    await hold({ client_message_id: 'msg_01bbbbbbbbbbbbbbbbbbbbbbbb', session: KEY, message: 'Into a deleted chat' }, true)
    h.chatSessionDetail.mockRejectedValue(new ApiError('Not found.', 404, 'session_not_found'))
    const m = mount()
    await socketOpens()
    await waitFor(() => expect(m.result.current.c.waitingSends.waiting).toEqual([]))
    expect(h.sendChat).not.toHaveBeenCalled()
  })
})
