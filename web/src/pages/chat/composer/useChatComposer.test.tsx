import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup } from '@testing-library/react'
import { useState } from 'react'
import { runState } from '../../../test/runState'
import { userTurn, assistantTurn, turnText, type ChatTurn } from '../chatTypes'
import type { ChatStream } from '../useChatStream'

// ── The composer's controller ───────────────────────────────────────────────────────────────────
//
// `useChatComposer` decides the next turn and sends it: on a new chat it creates the session and
// hands the run across the remount, mid-turn it steers, a slash command it runs itself, and a
// selection it applies to the session and reports when the session refuses it. Driven here against
// a stream it is handed, the way ChatSession hands it one.

const NEW_KEY = 'chat-6-composer'

const h = vi.hoisted(() => ({
  createChatSession: vi.fn(),
  sendChat: vi.fn(),
  setApprovalMode: vi.fn(),
  setSessionReplyStyle: vi.fn(),
  notify: vi.fn(),
}))

vi.mock('../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/api')>()
  const api = new Proxy({
    createChatSession: h.createChatSession, sendChat: h.sendChat, setApprovalMode: h.setApprovalMode,
    setSessionReplyStyle: h.setSessionReplyStyle,
  } as Record<string, unknown>, {
    get(t, p: string) { return p in t ? t[p] : (t[p] = () => Promise.resolve({})) },
  })
  return { ...real, api }
})
vi.mock('../../../app/appSdk', () => ({ notify: h.notify }))
vi.mock('../../../lib/useComposerData', () => ({ useComposerData: () => ({ discovered: {}, agents: [], providers: [], models: [], ready: true }) }))
vi.mock('../../../app/usePlatform', () => ({ usePlatform: () => 'linux' }))
vi.mock('../../../ui/composer/useScreenShare', () => ({
  useScreenShare: () => ({ available: false, sharing: false, disabledReason: '', toggle: vi.fn(), captureAndStage: vi.fn(), lastFrame: () => null }),
}))

let useChatComposer: typeof import('./useChatComposer')['useChatComposer']

/** The page around the controller: the stream it reads, and the two lazy hooks into its peers. */
function mount({ sessionId = null as string | null, turns: initial = [] as ChatTurn[], streaming = false, takesSteers = false } = {}) {
  const navigate = vi.fn()
  const setLiveRun = vi.fn()
  const followNewTurn = vi.fn()
  const oweSpokenReply = vi.fn()
  const setSteered = vi.fn()
  const markStreaming = vi.fn()
  const hook = renderHook(() => {
    const [turns, setTurns] = useState<ChatTurn[]>(initial)
    const stream = {
      sessionRef: { current: sessionId }, turns, setTurns,
      patchLastAssistant: (fn: (s: ChatTurn['segments']) => ChatTurn['segments']) => setTurns((prev) => prev.map((t, i) => (i === prev.length - 1 ? { ...t, segments: fn(t.segments) } : t))),
      dropTextRun: vi.fn(), streamingRef: { current: streaming }, markStreaming, sayTurnEnded: vi.fn(),
      turnSeqRef: { current: 0 }, stoppedTurnRef: { current: false }, takesSteersRef: { current: takesSteers },
      setMissing: vi.fn(), setSteered, followups: [], setFollowups: vi.fn(), followupsNeed: '', setFollowupsNeed: vi.fn(),
      checkWorkOffer: null, setCheckWorkOffer: vi.fn(), sessionBindingRef: { current: null }, bindingToldRef: { current: false }, bindingNonce: 0,
    } as unknown as ChatStream
    const c = useChatComposer({
      sessionId, navigate, seed: '', initialProjectId: '', initialAgent: '', pendingRouting: null, setRoutingSuggestion: vi.fn(),
      setLiveRun, started: turns.length > 0, stream, followNewTurn, oweSpokenReply,
    })
    return { c, turns }
  })
  return { ...hook, navigate, setLiveRun, followNewTurn, oweSpokenReply, setSteered, markStreaming }
}

beforeEach(async () => {
  for (const f of Object.values(h)) f.mockReset()
  h.createChatSession.mockResolvedValue({ key: NEW_KEY })
  h.sendChat.mockResolvedValue({ ok: true, session: NEW_KEY })
  h.setApprovalMode.mockResolvedValue({})
  h.setSessionReplyStyle.mockResolvedValue({ ok: true, reply_style: 'terse', reply_style_effective: 'terse', reply_style_source: 'conversation' })
  ;({ useChatComposer } = await import('./useChatComposer'))
})
afterEach(() => cleanup())

describe('useChatComposer sends', () => {
  it('creates the session on a new chat, hands the run across before navigating, and sends the draft', async () => {
    const m = mount()
    act(() => m.result.current.c.setInput('Why does the feed refetch?'))
    await act(async () => { await m.result.current.c.send() })
    expect(h.createChatSession).toHaveBeenCalledTimes(1)
    expect(m.setLiveRun).toHaveBeenCalledWith(NEW_KEY)
    expect(m.setLiveRun.mock.invocationCallOrder[0]).toBeLessThan(m.navigate.mock.invocationCallOrder[0])
    expect(m.navigate).toHaveBeenCalledWith(`chat/${NEW_KEY}`, { replace: true })
    expect(h.sendChat).toHaveBeenCalledWith('Why does the feed refetch?', NEW_KEY, expect.objectContaining({ client_ts: expect.any(String) }), undefined, undefined)
    expect(m.oweSpokenReply).toHaveBeenCalledWith(NEW_KEY)
    expect(m.followNewTurn).toHaveBeenCalled()
    expect(m.result.current.c.input).toBe('')
    expect(m.result.current.turns.map(turnText)).toEqual(['Why does the feed refetch?'])
  })

  it('writes a reply style picked before the chat existed onto the chat it creates, before the first turn runs', async () => {
    // The web never sends the style in the send body, so this write at create is the only way the
    // pick reaches turn 1: without it the pill reads Terse while the first turn runs without it.
    const m = mount()
    await act(async () => { await m.result.current.c.replyStyle.pill.onSelect('terse') })
    expect(h.setSessionReplyStyle, 'no chat yet, so nothing to write').not.toHaveBeenCalled()
    act(() => m.result.current.c.setInput('Summarize the incident'))
    await act(async () => { await m.result.current.c.send() })
    expect(h.setSessionReplyStyle).toHaveBeenCalledWith(NEW_KEY, 'terse')
    expect(h.setSessionReplyStyle.mock.invocationCallOrder[0]).toBeLessThan(h.sendChat.mock.invocationCallOrder[0])
  })

  it('steers a running turn that takes one, and shows the steer until the turn takes it', async () => {
    h.sendChat.mockResolvedValue({ ok: true, steered: true })
    const m = mount({ sessionId: NEW_KEY, turns: [userTurn('Fix it'), assistantTurn('Working…')], streaming: true, takesSteers: true })
    act(() => m.result.current.c.setInput('use the ETag too'))
    await act(async () => { await m.result.current.c.send() })
    expect(h.sendChat).toHaveBeenCalledWith('use the ETag too', NEW_KEY, expect.objectContaining({ client_ts: expect.any(String) }), 'steer')
    expect(m.setSteered).toHaveBeenCalled()
    expect(m.result.current.c.input).toBe('')
    expect(m.markStreaming, 'a steer starts no turn').not.toHaveBeenCalled()
  })

  it('answers /help itself, and nothing reaches the model', async () => {
    const m = mount({ sessionId: NEW_KEY })
    await act(async () => { await m.result.current.c.send('/help') })
    expect(h.sendChat).not.toHaveBeenCalled()
    expect(m.result.current.turns.map((t) => t.role)).toEqual(['user', 'assistant'])
    expect(turnText(m.result.current.turns[1])).toContain('## Slash commands')
  })
})

describe('useChatComposer applies the selection', () => {
  it('moves the pill at once and persists it to the session', () => {
    const m = mount({ sessionId: NEW_KEY })
    act(() => m.result.current.c.applySelection({ approval: 'trust' }))
    expect(m.result.current.c.selection.approval).toBe('trust')
    expect(h.setApprovalMode).toHaveBeenCalledWith('trust', NEW_KEY)
  })

  it('says so when the session refuses it', async () => {
    h.setApprovalMode.mockRejectedValue(new Error('session is busy'))
    const m = mount({ sessionId: NEW_KEY })
    await act(async () => { m.result.current.c.applySelection({ approval: 'yolo' }); await Promise.resolve() })
    expect(h.notify).toHaveBeenCalledWith("Couldn't apply this approval mode to this session: session is busy", 'error')
  })

  it('takes the posture, memory mode, voice and recall history from the chat’s first read', () => {
    const m = mount({ sessionId: NEW_KEY })
    act(() => m.result.current.c.adoptDetail({
      key: NEW_KEY, title: 'Feed', run_state: runState({ session: NEW_KEY }), task_mode: 'plan', approval: 'trust_reads', memory_mode: 'incognito',
      reply_style: 'terse', reply_style_effective: 'terse', reply_style_source: 'conversation', reply_style_agent_default: '',
      messages: [{ role: 'user', content: 'first' }, { role: 'assistant', content: 'ok' }, { role: 'user', content: 'second' }],
    } as never))
    const c = m.result.current.c
    expect(c.selection).toMatchObject({ taskMode: 'plan', approval: 'trust_reads' })
    expect(c.memoryMode).toBe('incognito')
    expect(c.replyStyle.pill).toMatchObject({ choice: 'terse', effective: 'terse', source: 'conversation', agentDefault: '' })
    expect(c.recallPrompt(0)).toBe('first')
    expect(c.recallPrompt(1)).toBe('second')
  })
})
