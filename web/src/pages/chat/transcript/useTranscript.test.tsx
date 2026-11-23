import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup } from '@testing-library/react'
import { useState } from 'react'
import { userTurn, assistantTurn, markCoordOf, WIDGET_SAID, type ChatTurn } from '../chatTypes'
import type { ComposerValue } from '../../../ui/composer/types'

// ── The transcript's controller ─────────────────────────────────────────────────────────────────
//
// `useTranscript` holds what the chat's transcript region keeps and does: the find bar and its
// deep link, the Session Map's open state, and the actions a turn offers. Driven here without the
// page, against a stream it is handed, the way ChatSession hands it one.

const SESSION = 'chat-4-transcript'

const h = vi.hoisted(() => ({
  approve: vi.fn(),
  setTaskMode: vi.fn(),
  editResend: vi.fn(),
  regenerate: vi.fn(),
  chatSessionDetail: vi.fn(),
  editSessionBookmarks: vi.fn(),
  notify: vi.fn(),
  sendChat: vi.fn(),
}))

vi.mock('../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/api')>()
  const api = new Proxy({
    approve: h.approve, setTaskMode: h.setTaskMode, editResend: h.editResend,
    regenerate: h.regenerate, chatSessionDetail: h.chatSessionDetail, editSessionBookmarks: h.editSessionBookmarks,
    sendChat: h.sendChat,
  } as Record<string, unknown>, {
    get(t, p: string) { return p in t ? t[p] : (t[p] = () => Promise.resolve({})) },
  })
  return { ...real, api }
})
vi.mock('../../../app/appSdk', () => ({ notify: h.notify }))

let useTranscript: typeof import('./useTranscript')['useTranscript']

const TURNS: ChatTurn[] = [userTurn('Why does the feed refetch?', '2026-10-01T09:00:00Z'), assistantTurn('It ignores the validators.')]

/** The page around the controller: the URL, the composer's selection, a stream that holds turns. */
function mount({ sessionId = SESSION as string | null, query = {} as Record<string, string>, send = vi.fn(() => Promise.resolve()), initial = TURNS } = {}) {
  const setQuery = vi.fn()
  const notice = { notice: null, showError: vi.fn(), showInfo: vi.fn(), clear: vi.fn() }
  const markStreaming = vi.fn()
  const readSnapshot = vi.fn(() => Promise.reject(new Error('offline')))
  const hook = renderHook(() => {
    const [turns, setTurns] = useState<ChatTurn[]>(initial)
    const [selection, setSelection] = useState<ComposerValue>({ agent: 'researcher', model: 'Auto', approval: 'normal', taskMode: 'ask', reasoning: '' })
    const [bookmarks, setBookmarks] = useState<readonly string[]>([])
    const t = useTranscript({
      sessionId, query, setQuery, navigate: vi.fn(),
      stream: {
        sessionRef: { current: sessionId }, turns, setTurns, streaming: false, streamingRef: { current: false },
        markStreaming, dropTextRun: vi.fn(), readSnapshot, adoptSnapshot: vi.fn(), bookmarks, setBookmarks,
        takesSteersRef: { current: false },
      },
      started: turns.length > 0, notice, memoryMode: 'persistent', setSelection, agent: selection.agent, send,
    })
    return { t, turns, selection, bookmarks }
  })
  return { ...hook, setQuery, notice, markStreaming, send }
}

const cmdF = () => act(() => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'f', metaKey: true, bubbles: true })) })

beforeEach(async () => {
  for (const f of Object.values(h)) f.mockReset()
  h.approve.mockResolvedValue({})
  h.setTaskMode.mockResolvedValue({})
  ;({ useTranscript } = await import('./useTranscript'))
})
afterEach(() => cleanup())

describe("a manual automation's Run now is a live card", () => {
  it('🔴 the transcript keeps it out of the folded steps', () => {
    // Red before: the transcript's live cards were workflow runs and loops only, so the card the
    // reply points to ("use its Run now, shown with this reply") was folded into "Worked through N
    // steps", and no button was under the reply until she opened them.
    const data = { trigger: { id: 'manual:kitchen-quote-comparison', name: 'Kitchen quote comparison', kind: 'manual' }, needs_grant: [] }
    const made = { kind: 'tool' as const, id: 'c1', tool: 'automation_create', done: true, output: `Created it.\n<automation-data>${JSON.stringify(data)}</automation-data>` }
    const turns: ChatTurn[] = [
      userTurn('make me a button that runs the comparison', '2026-11-20T09:00:00Z'),
      { role: 'assistant', segments: [made, { kind: 'text', text: 'Use its Run now, shown with this reply.' }] },
    ]
    const { result } = mount({ initial: turns })
    expect(result.current.t.liveCards.has(made)).toBe(true)
  })
})

describe('a quote says whose words it is', () => {
  it('🔴 names a widget\'s message as a widget\'s, and yours as yours', () => {
    // Red before: a passage quoted from a widget's message read "You said:", and the draft it
    // landed in was the owner's own, so its links were granted as theirs.
    const turns: ChatTurn[] = [
      userTurn('my own words', '2026-11-01T09:00:00Z'),
      assistantTurn('ok'),
      { ...userTurn('[UI] refresh', '2026-11-01T09:00:02Z'), fromWidget: true },
    ]
    const { result } = mount({ initial: turns })
    // The turn nodes as the transcript draws and registers them: each says its side, and a
    // widget's is marked.
    const nodes = turns.map((turn, i) => {
      const el = document.createElement('div')
      el.dataset.transcriptTurn = turn.role
      if (turn.role === 'user' && turn.fromWidget) el.dataset.fromWidget = ''
      el.textContent = 'x'
      result.current.t.turnNodes.current.set(markCoordOf(turn, i), el)
      return el
    })
    expect(result.current.t.attributionForNode(nodes[0].firstChild)).toBe('You')
    expect(result.current.t.attributionForNode(nodes[2].firstChild)).toBe(WIDGET_SAID)
    expect(result.current.t.attributionForNode(nodes[1].firstChild)).toBe('researcher')
  })
})

describe('a widget\'s message keeps its mark', () => {
  const turns: ChatTurn[] = [
    userTurn('my own words', '2026-11-01T09:00:00Z'),
    assistantTurn('ok'),
    { ...userTurn('[UI] refresh', '2026-11-01T09:00:02Z'), fromWidget: true },
  ]

  it('says a selection takes in a widget\'s words when it runs across into one of its messages', () => {
    const { result } = mount({ initial: turns })
    const host = document.createElement('div')
    document.body.appendChild(host)
    ;(result.current.t.scrollRef as { current: HTMLDivElement | null }).current = host
    const nodes = turns.map((turn, i) => {
      const el = document.createElement('div')
      el.dataset.transcriptTurn = turn.role
      if (turn.role === 'user' && turn.fromWidget) el.dataset.fromWidget = ''
      el.textContent = `turn ${i}`
      host.appendChild(el)
      return el
    })
    const across = document.createRange()
    across.setStart(nodes[0].firstChild as Text, 2)
    across.setEnd(nodes[2].firstChild as Text, 3)
    expect(result.current.t.widgetWordsIn(across)).toBe(true)
    const within = document.createRange()
    within.setStart(nodes[0].firstChild as Text, 0)
    within.setEnd(nodes[1].firstChild as Text, 2)
    expect(result.current.t.widgetWordsIn(within)).toBe(false)
    host.remove()
  })

  it('🔴 sends a widget\'s message edited and sent again marked, and keeps the mark on the page', async () => {
    h.editResend.mockResolvedValue({})
    const { result } = mount({ initial: turns })
    await act(async () => { await result.current.t.editResend(2, '[UI] refresh: {"range":"90d"}') })
    expect(h.editResend).toHaveBeenCalledTimes(1)
    expect(h.editResend.mock.calls[0][6]).toMatchObject({ widget: true })
    // The page's own cut, with the gateway's snapshot unread, keeps it a widget's.
    await act(async () => { await Promise.resolve() })
    expect(result.current.turns.at(-1)).toMatchObject({ role: 'user', fromWidget: true })
    // The owner's own message goes without it: the ask carries no widget at all.
    const own = mount({ initial: turns.slice(0, 2).concat([userTurn('and again', '2026-11-01T09:00:04Z')]) })
    await act(async () => { await own.result.current.t.editResend(2, 'and again, shorter') })
    expect(h.editResend.mock.calls[1][6]).not.toHaveProperty('widget')
  })
})

describe('useTranscript keeps the find bar', () => {
  it('opens and closes it on ⌘F while a session is open', () => {
    const { result } = mount()
    expect(result.current.t.findOpen).toBe(false)
    cmdF()
    expect(result.current.t.findOpen).toBe(true)
    cmdF()
    expect(result.current.t.findOpen).toBe(false)
  })

  it('leaves ⌘F to the browser on a new chat', () => {
    const { result } = mount({ sessionId: null })
    cmdF()
    expect(result.current.t.findOpen).toBe(false)
  })

  it('opens seeded from a ?find deep link, and drops the param', () => {
    const { result, setQuery } = mount({ query: { find: 'validators' } })
    expect(result.current.t.findOpen).toBe(true)
    expect(result.current.t.findSeed).toBe('validators')
    expect(setQuery).toHaveBeenCalledWith({ find: null }, { replace: true })
  })
})

describe('useTranscript keeps the Session Map', () => {
  it('is open by default on a pointer device and writes a close to the URL', () => {
    const { result, setQuery } = mount()
    expect(result.current.t.mapOpen).toBe(true)
    act(() => result.current.t.setMapOpen(false))
    expect(setQuery).toHaveBeenCalledWith({ map: '0' }, undefined)
  })

  it('reads a deliberate close back from the URL', () => {
    expect(mount({ query: { map: '0' } }).result.current.t.mapOpen).toBe(false)
  })

  it('derives one entry per message she sent', () => {
    expect(mount().result.current.t.sessionEntries).toHaveLength(1)
  })
})

describe('useTranscript keeps the bookmarks', () => {
  it('🔑 shows a bookmark at once, keeps what the gateway stored, and says nothing more', async () => {
    h.editSessionBookmarks.mockResolvedValue({ ok: true, bookmarks: ['2026-10-01T09:00:00Z'] })
    const { result } = mount()
    const entry = result.current.t.sessionEntries[0]
    expect(entry.bookmarked).toBe(false)
    act(() => { result.current.t.toggleBookmark(entry) })
    // Shown before the gateway answers.
    expect(result.current.t.sessionEntries[0].bookmarked).toBe(true)
    expect(h.editSessionBookmarks).toHaveBeenCalledWith(SESSION, { add: ['2026-10-01T09:00:00Z'] })
    await act(async () => { await Promise.resolve() })
    expect(result.current.bookmarks).toEqual(['2026-10-01T09:00:00Z'])
  })

  it('a refusal puts the map back and says so in the composer’s notice', async () => {
    h.editSessionBookmarks.mockRejectedValue(new Error('the gateway is busy'))
    const { result, notice } = mount()
    act(() => { result.current.t.toggleBookmark(result.current.t.sessionEntries[0]) })
    expect(result.current.t.sessionEntries[0].bookmarked).toBe(true)
    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    expect(result.current.t.sessionEntries[0].bookmarked).toBe(false)
    expect(notice.showError).toHaveBeenCalledWith('Couldn’t bookmark that message: the gateway is busy')
  })
})

describe('useTranscript does what a turn offers', () => {
  it('moves the Permission pill only once the decision lands', async () => {
    const { result } = mount()
    await act(async () => { result.current.t.approve('req-1', 'trust') })
    expect(h.approve).toHaveBeenCalledWith(SESSION, 'trust', 'req-1')
    expect(result.current.selection.approval).toBe('trust')
  })

  it('tells the card whether its decision landed: true once it did, and not when it failed', async () => {
    const { result } = mount()
    let landed: unknown
    await act(async () => { landed = await result.current.t.approve('req-1', 'approved') })
    expect(landed).toBe(true)
    h.approve.mockRejectedValue(new Error('gateway restarted'))
    await act(async () => { landed = await result.current.t.approve('req-1', 'approved') })
    expect(landed).not.toBe(true)
  })

  it('leaves the pill where it was when the decision fails', async () => {
    h.approve.mockRejectedValue(new Error('gateway restarted'))
    const { result } = mount()
    await act(async () => { result.current.t.approve('req-1', 'yolo') })
    expect(result.current.selection.approval).toBe('normal')
  })

  it('switches to Agent and runs the continuation, and stops when the switch fails', async () => {
    const ok = mount()
    await act(async () => { await ok.result.current.t.switchToAgentAndRun('Write the fix.') })
    expect(h.setTaskMode).toHaveBeenCalledWith('agent', SESSION)
    expect(ok.send).toHaveBeenCalledWith('Write the fix.')
    cleanup()

    h.setTaskMode.mockRejectedValue(new Error('refused'))
    const failed = mount()
    await act(async () => { await failed.result.current.t.switchToAgentAndRun('Write the fix.') })
    expect(failed.send).not.toHaveBeenCalled()
    expect(h.notify).toHaveBeenCalledWith(expect.stringContaining("Couldn't switch this session to Agent"), 'error')
  })

  it('keeps the transcript and the editor when an edit is refused, and says why in the editor', async () => {
    h.editResend.mockRejectedValue(new Error('A turn is already running'))
    const { result, markStreaming } = mount()
    act(() => result.current.t.setEditingTurn(0))
    await act(async () => { await result.current.t.editResend(0, 'Why does the feed refetch at all?') })
    expect(result.current.turns).toBe(TURNS)
    expect(result.current.t.editingTurn).toBe(0)
    expect(result.current.t.editFailure).toMatch(/A turn is already running/)
    expect(markStreaming).not.toHaveBeenCalled()
  })

  it('names the message an edit sends in place of the old one, a new one each time', async () => {
    h.editResend.mockResolvedValue({ ok: true, rewound: 0 })
    const { result } = mount()
    await act(async () => { await result.current.t.editResend(0, 'Why does the feed refetch at all?') })
    await act(async () => { await Promise.resolve() })
    const [, , , , messageId] = h.editResend.mock.calls[0]
    expect(messageId, 'a message id, not a clock reading').toMatch(/^msg_[0-9a-hjkmnp-tv-z]{26}$/)
  })

  it('regenerates once the gateway accepts, and cuts the page copy when the read that follows fails', async () => {
    h.regenerate.mockResolvedValue({})
    const { result, markStreaming } = mount()
    await act(async () => { await result.current.t.regenerate() })
    expect(markStreaming).toHaveBeenCalledWith(true)
    await act(async () => { await Promise.resolve() })
    expect(result.current.turns.map((t) => t.role), 'the last answer goes; the reply streams in beneath its question').toEqual(['user'])
  })
})

// ── A reaction to an answer: shown at once, sent after a short wait ─────────────────────────────

describe('a reaction to an answer', () => {
  const ANSWER_TS = '2026-10-01T09:00:05Z'
  const ANSWERED: ChatTurn[] = [userTurn('Why does the feed refetch?', '2026-10-01T09:00:00Z'), { ...assistantTurn('It ignores the validators. Shall I fix it?'), answerTs: ANSWER_TS }]
  const answerOf = (turns: ChatTurn[]) => turns.find((t) => t.role === 'assistant')!
  beforeEach(() => { vi.useFakeTimers(); h.sendChat.mockResolvedValue({ session: SESSION, message_id: 'msg_x', accepted_at: '2026-10-01T09:01:00Z', disposition: 'started' }) })
  afterEach(() => { vi.useRealTimers() })

  it('🔴 shows on the answer at once and is sent once after 2 s, the last pick only', async () => {
    const m = mount({ initial: ANSWERED })
    act(() => m.result.current.t.react(ANSWER_TS, undefined, 'thumbs_up'))
    expect(answerOf(m.result.current.turns).reaction).toBe('thumbs_up')
    act(() => { vi.advanceTimersByTime(1000) })
    act(() => m.result.current.t.react(ANSWER_TS, 'thumbs_up', 'heart'))
    act(() => { vi.advanceTimersByTime(1999) })
    expect(h.sendChat).not.toHaveBeenCalled()
    await act(async () => { vi.advanceTimersByTime(1) })
    expect(h.sendChat).toHaveBeenCalledTimes(1)
    expect(h.sendChat.mock.calls[0].slice(0, 3)).toEqual(['', SESSION, { reaction: { to: ANSWER_TS, pick: 'heart' } }])
  })

  it('🔴 changed back to what the answer had inside the wait, sends nothing', async () => {
    const m = mount({ initial: [ANSWERED[0], { ...ANSWERED[1], reaction: 'thumbs_up' }] })
    act(() => m.result.current.t.react(ANSWER_TS, 'thumbs_up', 'thumbs_down'))
    act(() => m.result.current.t.react(ANSWER_TS, 'thumbs_down', 'thumbs_up'))
    await act(async () => { vi.advanceTimersByTime(5000) })
    expect(h.sendChat).not.toHaveBeenCalled()
    expect(answerOf(m.result.current.turns).reaction).toBe('thumbs_up')
  })

  it('🔴 one that starts a turn draws its line and the turn that answers it', async () => {
    const m = mount({ initial: ANSWERED })
    act(() => m.result.current.t.react(ANSWER_TS, undefined, 'thumbs_up'))
    await act(async () => { vi.advanceTimersByTime(2000) })
    const line = m.result.current.turns[m.result.current.turns.length - 1]
    expect(line.reacted).toEqual({ to: ANSWER_TS, pick: 'thumbs_up', previous: null, quote: 'It ignores the validators. Shall I fix it?' })
    expect(line.messageId).toBe('msg_x')
    expect(m.markStreaming).toHaveBeenCalledWith(true)
  })

  it('🔴 one the gateway refuses goes back to what the answer had, and says why', async () => {
    h.sendChat.mockRejectedValue(new Error('That answer is still being written.'))
    const m = mount({ initial: ANSWERED })
    act(() => m.result.current.t.react(ANSWER_TS, undefined, 'thumbs_up'))
    await act(async () => { vi.advanceTimersByTime(2000) })
    expect(answerOf(m.result.current.turns).reaction).toBeUndefined()
    expect(h.notify).toHaveBeenCalled()
  })
})

