import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { runState } from '../../../test/runState'
import { userTurn, assistantTurn, turnText, type ChatTurn } from '../chatTypes'
import type { ChatStream } from '../useChatStream'
import { receiptFor } from '../../../test/chatReceipt'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render } from '@testing-library/react'
import { publishWidgetAction } from '../../../ui/widget/actionTurn'
import { declineWidgetHost, forgetPendingWidgetAction, forgetWidgetHost, useWidgetActionLauncher } from '../../../ui/widget/useWidgetActionBridge'
import { WidgetFrame } from '../../../ui/widget/WidgetFrame'
import { ApiError } from '../../../lib/api'
import { WIDGET_SAID } from '../chatTypes'
import { FILL_HOLD, GOAL_REFUSAL, MIXED_NOTICE, PART_CHANGED_NOTICE } from './useChatComposer'
import { pressIntoFrame } from '../../../test/pressIntoFrame'

const SALES_NO = '\u201cSales\u201d is turned down while this chat is open.'
/** Why a goal loop won't start from a draft whose words can't be told apart from a widget's. */
const GOAL_REFUSAL_MIXED = 'A goal loop takes its task as yours. Delete all of the draft first: its words can\u2019t be told apart from the widget\u2019s.'

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
function mount({ sessionId = null as string | null, turns: initial = [] as ChatTurn[], streaming = false, takesSteers = false, seed = '', seedFromWidget = false, widgetActionNonce = '' } = {}) {
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
      sessionId, navigate, seed, seedFromWidget, widgetActionNonce, initialProjectId: '', initialAgent: '', pendingRouting: null, setRoutingSuggestion: vi.fn(),
      setLiveRun, started: turns.length > 0, stream, followNewTurn, oweSpokenReply,
    })
    return { c, turns }
  })
  return { ...hook, navigate, setLiveRun, followNewTurn, oweSpokenReply, setSteered, markStreaming }
}

beforeEach(async () => {
  for (const f of Object.values(h)) f.mockReset()
  h.createChatSession.mockResolvedValue({ key: NEW_KEY })
  h.sendChat.mockImplementation(receiptFor('started'))
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
    expect(h.sendChat).toHaveBeenCalledWith('Why does the feed refetch?', NEW_KEY, {}, undefined, undefined, { messageId: expect.stringMatching(/^msg_[0-9a-hjkmnp-tv-z]{26}$/) })
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
    h.sendChat.mockImplementation(receiptFor('steered'))
    const m = mount({ sessionId: NEW_KEY, turns: [userTurn('Fix it'), assistantTurn('Working…')], streaming: true, takesSteers: true })
    act(() => m.result.current.c.setInput('use the ETag too'))
    await act(async () => { await m.result.current.c.send() })
    expect(h.sendChat).toHaveBeenCalledWith('use the ETag too', NEW_KEY, undefined, 'steer', undefined, expect.objectContaining({ messageId: expect.stringMatching(/^msg_/) }))
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

describe('a widget action waits for your Send', () => {
  const ACTION = '[UI] refresh: {"range":"30d"}'
  const id = expect.objectContaining({ messageId: expect.stringMatching(/^msg_/) })
  /** A frame's action as the bridge hands it on. Unless a case says otherwise, the owner gave the
   *  frame its focus (`earned`), which is what lets a chat fill its composer rather than offer a chip. */
  function raise(text: string, meta: Parameters<typeof publishWidgetAction>[1] = {}) {
    act(() => { publishWidgetAction(text, { earned: true, ...meta }) })
  }
  let hostSeq = 100
  /** A frame's mount id, fresh for each case so no "no" carries over between them. */
  const host = () => `frame-${++hostSeq}`

  it('🔴 puts a frame\'s action in the composer, sends nothing, and marks it once you send it', async () => {
    // Red before: the chat sent it as your turn the moment the frame posted it, with no press of
    // yours, and with nothing to say it was the widget's words.
    const m = mount({ sessionId: NEW_KEY })
    raise(ACTION, { frame: 'Sales', host: host() })
    await act(async () => {})
    expect(h.sendChat).not.toHaveBeenCalled()
    expect(m.result.current.c.input).toBe(ACTION)
    expect(m.result.current.c.widgetDraft?.frame).toBe('Sales')
    act(() => m.result.current.c.touchComposer())
    await act(async () => { await m.result.current.c.send() })
    expect(h.sendChat).toHaveBeenCalledTimes(1)
    expect(h.sendChat).toHaveBeenCalledWith(ACTION, NEW_KEY, { widget_action: true }, undefined, undefined, id)
    expect(m.result.current.c.widgetDraft, 'sent, the draft is no one\'s').toBeNull()
  })

  it('🔴 takes no Enter or Send as yours for a moment after a widget fills the composer', async () => {
    // A reflexive Enter right after the fill sends nothing.
    const m = mount({ sessionId: NEW_KEY })
    raise(ACTION, { frame: 'Sales', host: host() })
    await act(async () => {})
    await act(async () => { await m.result.current.c.send() })
    expect(h.sendChat).not.toHaveBeenCalled()
    // An edit of the owner's ends it at once.
    act(() => m.result.current.c.editInput(`${ACTION} please`))
    await act(async () => { await m.result.current.c.send() })
    expect(h.sendChat).toHaveBeenCalledWith(`${ACTION} please`, NEW_KEY, { widget_action: true }, undefined, undefined, id)
  })

  it('sends your Enter once, and a frame\'s post just after it waits as a chip', async () => {
    // A send leaves the composer empty, and a frame's post just after it waits as a chip: a
    // second Enter a moment later sends nothing of the frame's.
    const m = mount({ sessionId: NEW_KEY })
    act(() => m.result.current.c.editInput('my own words'))
    await act(async () => { await m.result.current.c.send() })
    const frame = host()
    raise(ACTION, { frame: 'Sales', host: frame })
    await act(async () => { await new Promise((r) => setTimeout(r, 50)) })
    await act(async () => { await m.result.current.c.send() })
    expect(h.sendChat).toHaveBeenCalledTimes(1)
    expect(h.sendChat).toHaveBeenCalledWith('my own words', NEW_KEY, {}, undefined, undefined, id)
    expect(m.result.current.c.input).toBe('')
    expect(m.result.current.c.widgetSuggestion).toEqual({ text: ACTION, frame: 'Sales', host: frame })
    // Add fills it, marked.
    act(() => m.result.current.c.addWidgetSuggestion())
    expect(m.result.current.c.input).toBe(ACTION)
    expect(m.result.current.c.widgetDraft?.host).toBe(frame)
    expect(m.result.current.c.widgetSuggestion).toBeNull()
  })

  it('🔴 keeps a frame\'s message out of a composer you are in, as a chip; Dismiss is your no', async () => {
    const m = mount({ sessionId: NEW_KEY })
    const field = document.createElement('div')
    const editor = document.createElement('textarea')
    field.appendChild(editor)
    document.body.appendChild(field)
    ;(m.result.current.c.composerRef as { current: HTMLElement | null }).current = field
    editor.focus()
    const frame = host()
    raise(ACTION, { frame: 'Sales', host: frame })
    expect(m.result.current.c.input).toBe('')
    expect(m.result.current.c.widgetSuggestion?.host).toBe(frame)
    act(() => m.result.current.c.dismissWidgetSuggestion())
    editor.blur()
    raise(ACTION, { frame: 'Sales', host: frame })
    expect(m.result.current.c.widgetSuggestion, 'its frame was told no').toBeNull()
    expect(m.result.current.c.input).toBe('')
    field.remove()
  })

  it('keeps a draft of yours: a frame\'s action waits as a chip, and your draft goes unmarked', async () => {
    const m = mount({ sessionId: NEW_KEY })
    act(() => m.result.current.c.setInput('my own words'))
    raise(ACTION, { frame: 'Sales', host: host() })
    await act(async () => {})
    expect(m.result.current.c.input).toBe('my own words')
    expect(m.result.current.c.widgetSuggestion?.frame).toBe('Sales')
    expect(h.sendChat).not.toHaveBeenCalled()
    await act(async () => { await m.result.current.c.send() })
    expect(h.sendChat).toHaveBeenCalledWith('my own words', NEW_KEY, {}, undefined, undefined, id)
  })

  it('replaces the frame\'s own earlier message while it is still as the frame put it', async () => {
    const m = mount({ sessionId: NEW_KEY })
    const frame = host()
    raise('[UI] first', { frame: 'Sales', host: frame })
    raise('[UI] second', { frame: 'Sales', host: frame })
    expect(m.result.current.c.input).toBe('[UI] second')
    expect(m.result.current.c.widgetDraft?.replaced).toBe(true)
  })

  it('counts emptying an offered draft as your no to that frame', async () => {
    const m = mount({ sessionId: NEW_KEY })
    const frame = host()
    raise(ACTION, { frame: 'Sales', host: frame })
    act(() => m.result.current.c.editInput(''))
    expect(m.result.current.c.widgetDraft).toBeNull()
    await act(async () => { await new Promise((r) => setTimeout(r, 2100)) })
    raise(ACTION, { frame: 'Sales', host: frame })
    expect(m.result.current.c.input, 'the composer stays empty').toBe('')
    expect(m.result.current.c.widgetSuggestion).toBeNull()
  })

  it('marks a widget\'s draft you send while a turn runs', async () => {
    h.sendChat.mockImplementation(receiptFor('steered'))
    const m = mount({ sessionId: NEW_KEY, turns: [userTurn('Fix it'), assistantTurn('Working…')], streaming: true, takesSteers: true })
    raise(ACTION, { host: host() })
    await act(async () => {})
    act(() => m.result.current.c.touchComposer())
    await act(async () => { await m.result.current.c.send() })
    expect(h.sendChat).toHaveBeenCalledWith(ACTION, NEW_KEY, { widget_action: true }, 'steer', undefined, expect.objectContaining({ messageId: expect.stringMatching(/^msg_/) }))
  })

  it('sends at once an action you pressed in the page, labelled and marked', async () => {
    const m = mount({ sessionId: NEW_KEY })
    raise('[UI] log_expense: {"amount":"12.40"}', { label: 'Log expense', pressed: true })
    await act(async () => {})
    expect(h.sendChat).toHaveBeenCalledWith('[UI] log_expense: {"amount":"12.40"}', NEW_KEY, { ui_label: 'Log expense', widget_action: true }, undefined, undefined, id)
    expect(m.result.current.turns.map(turnText)).toEqual(['Log expense'])
  })

  it('keeps the mark on a widget\'s draft given back by a missing chat', async () => {
    // The draft comes back marked, and its later Send says it came from a widget.
    h.sendChat.mockRejectedValueOnce(new ApiError('This chat does not exist', 404, 'session_not_found'))
    const m = mount({ sessionId: NEW_KEY })
    raise(ACTION, { host: host() })
    act(() => m.result.current.c.touchComposer())
    await act(async () => { await m.result.current.c.send() })
    expect(m.result.current.c.input).toBe(ACTION)
    expect(m.result.current.c.widgetDraft).not.toBeNull()
    // Sent again from there, it still says so.
    await act(async () => { await m.result.current.c.send() })
    expect(h.sendChat).toHaveBeenLastCalledWith(ACTION, NEW_KEY, { widget_action: true }, undefined, undefined, id)
    // …and the new chat it carries the draft to is told so (MissingChat's link).
    // Which of its words were the owner's isn't carried, so the × can't take any of them out.
    const carried = mount({ seed: ACTION, seedFromWidget: true })
    expect(carried.result.current.c.widgetDraft).toEqual({ text: ACTION, mixed: true })
  })

  it('🔴 keeps the mark on a widget\'s message held for an upload, given back to an empty composer', async () => {
    const m = mount({ sessionId: NEW_KEY })
    act(() => { void m.result.current.c.attach([new File(['x'], 'notes.txt')]) })
    expect(m.result.current.c.uploadHold).not.toBe('')
    // The correction rail's press: sent at once, no label; held, it comes back to the composer.
    raise('[UI] correction: 1 element marked', { pressed: true })
    await act(async () => {})
    expect(h.sendChat).not.toHaveBeenCalled()
    expect(m.result.current.c.input).toBe('[UI] correction: 1 element marked')
    expect(m.result.current.c.widgetDraft).not.toBeNull()
    // Once the file is in, the owner's Send of it says it is the widget's.
    await waitFor(() => expect(m.result.current.c.uploadHold).toBe(''))
    await act(async () => { await m.result.current.c.send() })
    expect(h.sendChat).toHaveBeenCalledWith('[UI] correction: 1 element marked', NEW_KEY, expect.objectContaining({ widget_action: true }), undefined, undefined, id)
  })

  it('keeps your own draft unmarked through an upload hold', async () => {
    const m = mount({ sessionId: NEW_KEY })
    act(() => m.result.current.c.editInput('my own words'))
    act(() => { void m.result.current.c.attach([new File(['x'], 'notes.txt')]) })
    await act(async () => { await m.result.current.c.send() })
    expect(h.sendChat, 'held for the upload').not.toHaveBeenCalled()
    expect(m.result.current.c.input).toBe('my own words')
    expect(m.result.current.c.widgetDraft).toBeNull()
    await waitFor(() => expect(m.result.current.c.uploadHold).toBe(''))
    await act(async () => { await m.result.current.c.send() })
    const meta = h.sendChat.mock.calls[0][2] as Record<string, unknown>
    expect(meta.widget_action).toBeUndefined()
  })

  it('keeps the mark on a queued widget message its Edit gives back', async () => {
    h.sendChat.mockImplementation(receiptFor('queued'))
    const m = mount({ sessionId: NEW_KEY, turns: [userTurn('Fix it'), assistantTurn('Working…')], streaming: true, takesSteers: false })
    raise(ACTION, { host: host() })
    act(() => m.result.current.c.touchComposer())
    await act(async () => { await m.result.current.c.send() })
    const sent = h.sendChat.mock.calls[0][5].messageId as string
    act(() => m.result.current.c.takeBackQueued(ACTION, sent))
    expect(m.result.current.c.widgetDraft).not.toBeNull()
    // Sent again from the draft, it still says so.
    await act(async () => { await m.result.current.c.send() })
    expect(h.sendChat).toHaveBeenLastCalledWith(ACTION, NEW_KEY, { widget_action: true }, 'followup', undefined, expect.objectContaining({ messageId: expect.stringMatching(/^msg_/) }))
    // One the gateway says came from a widget (a reload, another tab) comes back marked too.
    const reloaded = mount({ sessionId: NEW_KEY })
    act(() => reloaded.result.current.c.takeBackQueued(ACTION, 'msg_from_another_tab', true))
    expect(reloaded.result.current.c.widgetDraft).not.toBeNull()
    // A queued message of the owner's own comes back unmarked.
    const other = mount({ sessionId: NEW_KEY })
    act(() => other.result.current.c.takeBackQueued('my own words', 'msg_not_from_a_widget'))
    expect(other.result.current.c.widgetDraft).toBeNull()
  })

  it('keeps the mark through /optimize', async () => {
    const optimized = vi.fn(async () => ({ changed: true, optimized: `${ACTION} (tidied)` }))
    const { api } = await import('../../../lib/api')
    ;(api as unknown as Record<string, unknown>).optimizePrompt = optimized
    const m = mount({ sessionId: NEW_KEY })
    raise(ACTION, { host: host() })
    // The owner turns the widget's draft into an /optimize of it, and sends that draft.
    act(() => m.result.current.c.editInput(`/optimize ${ACTION}`))
    await act(async () => { await m.result.current.c.send() })
    await act(async () => {})
    expect(h.sendChat).toHaveBeenCalledWith(`${ACTION} (tidied)`, NEW_KEY, expect.objectContaining({ widget_action: true }), undefined, undefined, id)
  })

  it('🔴 marks a widget action the shell staged on your yes, drained by the chat it opened', async () => {
    function Shell() { useWidgetActionLauncher(); return null }
    const launches: string[] = []
    const onLaunch = (e: Event) => { launches.push(String((e as CustomEvent).detail?.widgetAction)) }
    window.addEventListener('ne:launch-chat', onLaunch)
    try {
      const shell = render(<Shell />)
      raise('[UI] submit: {"a":1}', { label: 'Submit', pressed: true })
      expect(launches).toHaveLength(1)
      shell.unmount()
      mount({ sessionId: NEW_KEY, widgetActionNonce: launches[0] })
      await act(async () => {})
      expect(h.sendChat).toHaveBeenCalledWith('[UI] submit: {"a":1}', NEW_KEY, { ui_label: 'Submit', widget_action: true }, undefined, undefined, id)
    } finally {
      window.removeEventListener('ne:launch-chat', onLaunch)
      forgetPendingWidgetAction()
    }
  })

  it('fills the composer from a widget frame\'s own post, and sends nothing', async () => {
    if (typeof URL.createObjectURL !== 'function') {
      URL.createObjectURL = () => 'blob:composer-test'
      URL.revokeObjectURL = () => {}
    }
    const m = mount({ sessionId: NEW_KEY })
    const view = render(<WidgetFrame html={'<button data-action="refresh">Refresh</button>'} title="Sales" />)
    await act(async () => {})
    const iframe = view.container.querySelector('iframe') as HTMLIFrameElement
    act(() => { pressIntoFrame(iframe) })
    act(() => {
      window.dispatchEvent(new MessageEvent('message', { data: { type: 'widget-action', action: 'refresh' }, source: iframe.contentWindow as MessageEventSource }))
    })
    await act(async () => {})
    expect(m.result.current.c.input).toContain('[UI] refresh')
    expect(m.result.current.c.widgetDraft?.frame).toBe('Sales')
    expect(h.sendChat).not.toHaveBeenCalled()
  })

  /** Move this test's clock on by *ms*, the timers left as they run. */
  function later(ms: number) { vi.setSystemTime(new Date(Date.now() + ms)) }

  it('keeps your own words apart from a message you Add after them', async () => {
    // Add keeps your draft yours: the same frame's next post never replaces your words, and its
    // × takes out only the frame's text.
    vi.useFakeTimers({ toFake: ['Date'] })
    try {
      const m = mount({ sessionId: NEW_KEY })
      const frame = host()
      act(() => m.result.current.c.editInput('mine'))
      raise(ACTION, { frame: 'Sales', host: frame })
      act(() => m.result.current.c.addWidgetSuggestion())
      expect(m.result.current.c.input).toBe(`mine\n\n${ACTION}`)
      later(5100)
      raise('[UI] again', { frame: 'Sales', host: frame })
      expect(m.result.current.c.input.startsWith('mine')).toBe(true)
      expect(m.result.current.c.input).toContain(ACTION)
      expect(m.result.current.c.widgetSuggestion?.text, 'it waits as a chip').toBe('[UI] again')
      // The × takes out the frame's message and leaves yours, unmarked.
      act(() => m.result.current.c.clearWidgetDraft())
      expect(m.result.current.c.input).toBe('mine')
      expect(m.result.current.c.widgetDraft).toBeNull()
    } finally { vi.useRealTimers() }
  })

  it('🔴 counts a file still uploading as a draft under way: a frame\'s message waits as a chip', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    const { api } = await import('../../../lib/api')
    ;(api as unknown as Record<string, unknown>).uploadFiles = () => new Promise(() => {})
    try {
      const m = mount({ sessionId: NEW_KEY })
      await act(async () => { void m.result.current.c.attach([new File(['x'], 'notes.txt')]); await Promise.resolve() })
      expect(m.result.current.c.uploads.length).toBe(1)
      expect(m.result.current.c.attachedPaths).toEqual([])
      later(2100)
      raise(ACTION, { frame: 'Sales', host: host() })
      expect(m.result.current.c.input).toBe('')
      expect(m.result.current.c.widgetSuggestion?.text).toBe(ACTION)
    } finally {
      vi.useRealTimers()
      delete (api as unknown as Record<string, unknown>).uploadFiles
    }
  })

  it('marks only the draft\'s own send while a widget\'s message sits in the composer', async () => {
    // A follow-up, Check this work, /compact or a spoken message sent while the draft holds one
    // is the owner's own, unmarked.
    const m = mount({ sessionId: NEW_KEY })
    raise(ACTION, { frame: 'Sales', host: host() })
    act(() => m.result.current.c.touchComposer())
    await act(async () => { await m.result.current.c.send('Explain the last chart') })
    expect(h.sendChat).toHaveBeenCalledWith('Explain the last chart', NEW_KEY, {}, undefined, undefined, id)
    // A picked follow-up or suggestion replaces the draft with words that are not the widget's.
    const picked = mount({ sessionId: NEW_KEY })
    raise(ACTION, { frame: 'Sales', host: host() })
    expect(picked.result.current.c.widgetDraft).not.toBeNull()
    act(() => picked.result.current.c.replaceDraft('What changed since Monday?'))
    expect(picked.result.current.c.widgetDraft).toBeNull()
  })

  it('cools a frame once you send its message: its next post a moment later is dropped', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    try {
      const m = mount({ sessionId: NEW_KEY })
      const frame = host()
      raise(ACTION, { frame: 'Sales', host: frame })
      act(() => m.result.current.c.touchComposer())
      await act(async () => { await m.result.current.c.send() })
      expect(h.sendChat).toHaveBeenCalledTimes(1)
      later(2100)
      raise('[UI] again', { frame: 'Sales', host: frame })
      expect(m.result.current.c.input).toBe('')
      expect(m.result.current.c.widgetSuggestion).toBeNull()
      // Another frame is not that one.
      raise('[UI] note', { frame: 'Notes', host: host() })
      expect(m.result.current.c.input).toBe('[UI] note')
    } finally { vi.useRealTimers() }
  })

  it('🔴 recalls with ↑ only what you typed, after a reload too', () => {
    // Red before: a reload's ↑ recall gave back a widget's message as if you had typed it.
    const m = mount({ sessionId: NEW_KEY })
    act(() => m.result.current.c.adoptDetail({
      key: NEW_KEY,
      messages: [
        { role: 'user', content: 'my own words', ts: '2026-11-01T09:00:00+00:00' },
        { role: 'assistant', content: 'ok', ts: '2026-11-01T09:00:01+00:00' },
        { role: 'user', content: ACTION, ts: '2026-11-01T09:00:02+00:00', meta: { widget_action: true } },
        { role: 'assistant', content: 'ok', ts: '2026-11-01T09:00:03+00:00' },
        { role: 'user', content: '[UI] submit: {"a":1}', ts: '2026-11-01T09:00:04+00:00', meta: { ui_label: 'Submit' } },
        { role: 'assistant', content: 'ok', ts: '2026-11-01T09:00:05+00:00' },
      ],
    } as never))
    expect(m.result.current.c.historyTexts).toEqual(['my own words'])
  })

  it('🔴 marks the draft a quote of a widget\'s message lands in', () => {
    const m = mount({ sessionId: NEW_KEY })
    act(() => m.result.current.c.quoteToComposer(ACTION, WIDGET_SAID))
    expect(m.result.current.c.input).toContain('**A widget said:**')
    expect(m.result.current.c.widgetDraft).not.toBeNull()
    const own = mount({ sessionId: NEW_KEY })
    act(() => own.result.current.c.quoteToComposer('my own words', 'You'))
    expect(own.result.current.c.widgetDraft).toBeNull()
  })

  it('🔴 offers a frame\'s action as a chip, never a fill, when the owner didn\'t give the frame its focus', async () => {
    const m = mount({ sessionId: NEW_KEY })
    const frame = host()
    raise(ACTION, { frame: 'Sales', host: frame, earned: false })
    await act(async () => {})
    expect(m.result.current.c.input).toBe('')
    expect(m.result.current.c.widgetSuggestion).toEqual({ text: ACTION, frame: 'Sales', host: frame })
    expect(h.sendChat).not.toHaveBeenCalled()
  })

  /** Send the draft as the owner would: a press in the editor, then Send. */
  async function sendDraft(m: ReturnType<typeof mount>) {
    act(() => m.result.current.c.touchComposer())
    await act(async () => { await m.result.current.c.send() })
  }
  const lastMeta = () => h.sendChat.mock.calls[h.sendChat.mock.calls.length - 1][2] as Record<string, unknown>

  it.each([['the same frame', false], ['another frame', true]] as const)(
    'keeps the mark when a message from %s is Added to a widget\'s edited draft and taken out again',
    async (_, other) => {
      const m = mount({ sessionId: NEW_KEY })
      const first = host()
      raise(ACTION, { frame: 'Sales', host: first })
      act(() => m.result.current.c.editInput(`${ACTION} for March`))
      raise('[UI] again', { frame: other ? 'Notes' : 'Sales', host: other ? host() : first })
      expect(m.result.current.c.widgetSuggestion?.text, 'it waits as a chip').toBe('[UI] again')
      act(() => m.result.current.c.addWidgetSuggestion())
      expect(m.result.current.c.input).toBe(`${ACTION} for March\n\n[UI] again`)
      act(() => m.result.current.c.clearWidgetDraft())
      expect(m.result.current.c.input).toBe(`${ACTION} for March`)
      expect(m.result.current.c.widgetDraft?.host, 'the first frame\'s mark is back').toBe(first)
      await sendDraft(m)
      expect(lastMeta()).toEqual({ widget_action: true })
    })

  it('keeps the mark when a widget\'s queued message given back by Edit joins a widget\'s draft and is taken out', async () => {
    const m = mount({ sessionId: NEW_KEY })
    raise(ACTION, { frame: 'Sales', host: host() })
    act(() => m.result.current.c.takeBackQueued('[UI] queued', 'msg_from_a_widget', true))
    expect(m.result.current.c.input).toBe(`${ACTION}\n\n[UI] queued`)
    act(() => m.result.current.c.clearWidgetDraft())
    expect(m.result.current.c.input).toBe(ACTION)
    await sendDraft(m)
    expect(lastMeta()).toEqual({ widget_action: true })
  })

  it('🔴 keeps the mark when a quote of a widget\'s words joins a widget\'s draft and is taken out', async () => {
    const m = mount({ sessionId: NEW_KEY })
    raise(ACTION, { frame: 'Sales', host: host() })
    act(() => m.result.current.c.quoteToComposer('[UI] earlier', WIDGET_SAID))
    expect(m.result.current.c.widgetDraft?.quoted).toBe(true)
    act(() => m.result.current.c.clearWidgetDraft())
    expect(m.result.current.c.input).toBe(ACTION)
    expect(m.result.current.c.widgetDraft).not.toBeNull()
    await sendDraft(m)
    expect(lastMeta()).toEqual({ widget_action: true })
  })

  it('gives your own words back unmarked when a quote of a widget\'s is taken out of them, and sends them as yours', async () => {
    const m = mount({ sessionId: NEW_KEY })
    act(() => m.result.current.c.editInput('mine'))
    act(() => m.result.current.c.quoteToComposer('[UI] earlier', WIDGET_SAID))
    expect(m.result.current.c.widgetDraft?.own).toBe('mine')
    act(() => m.result.current.c.clearWidgetDraft())
    expect(m.result.current.c.input).toBe('mine')
    expect(m.result.current.c.widgetDraft).toBeNull()
    await sendDraft(m)
    expect(lastMeta()).toEqual({})
  })

  it('gives back unmarked what the owner wrote after the frame\'s text, and keeps marked, saying why, what the × can\'t tell apart', async () => {
    // Words typed after the frame's text, and a queued message of the owner's given back after it,
    // are theirs.
    const after = mount({ sessionId: NEW_KEY })
    raise(ACTION, { frame: 'Sales', host: host() })
    act(() => after.result.current.c.editInput(`${ACTION} and the week before`))
    act(() => after.result.current.c.clearWidgetDraft())
    expect(after.result.current.c.input).toBe('and the week before')
    expect(after.result.current.c.widgetDraft).toBeNull()
    expect(after.result.current.c.notice.notice?.text, 'the × says the frame is turned down').toBe(SALES_NO)
    await sendDraft(after)
    expect(lastMeta()).toEqual({})
    const queued = mount({ sessionId: NEW_KEY })
    raise(ACTION, { frame: 'Sales', host: host() })
    act(() => queued.result.current.c.takeBackQueued('my own queued words', 'msg_not_from_a_widget'))
    act(() => queued.result.current.c.clearWidgetDraft())
    expect(queued.result.current.c.input).toBe('my own queued words')
    expect(queued.result.current.c.widgetDraft).toBeNull()
    // The owner's part before it was edited: what is left stays marked, and the notice says why.
    const before = mount({ sessionId: NEW_KEY })
    act(() => before.result.current.c.editInput('my own ask'))
    raise(ACTION, { frame: 'Sales', host: host() })
    act(() => before.result.current.c.addWidgetSuggestion())
    act(() => before.result.current.c.editInput(before.result.current.c.input.replace('my own ask', 'my ask')))
    act(() => before.result.current.c.clearWidgetDraft())
    expect(before.result.current.c.input).toBe('my ask')
    expect(before.result.current.c.widgetDraft?.mixed).toBe(true)
    expect(before.result.current.c.notice.notice?.text, 'both, as one line').toBe(`${SALES_NO} ${PART_CHANGED_NOTICE}`)
    // A frame's text edited past finding: the draft stays as it is, marked, and the notice says so.
    const edited = mount({ sessionId: NEW_KEY })
    raise(ACTION, { frame: 'Sales', host: host() })
    act(() => edited.result.current.c.editInput('[UI] refresh: {"range":"90d"}'))
    act(() => edited.result.current.c.clearWidgetDraft())
    expect(edited.result.current.c.input).toBe('[UI] refresh: {"range":"90d"}')
    expect(edited.result.current.c.widgetDraft?.mixed).toBe(true)
    expect(edited.result.current.c.notice.notice?.text, 'both, as one line').toBe(`${SALES_NO} ${MIXED_NOTICE}`)
    expect(MIXED_NOTICE).toMatch(/Delete all of it to start your own\.$/)
    await sendDraft(edited)
    expect(lastMeta()).toEqual({ widget_action: true })
    // An untitled frame's × says the same, of "the widget".
    const untitled = mount({ sessionId: NEW_KEY })
    raise(ACTION, { host: host() })
    act(() => untitled.result.current.c.clearWidgetDraft())
    expect(untitled.result.current.c.notice.notice?.text).toBe('The widget is turned down while this chat is open.')
  })

  it('gives a draft a missing chat sent back its own mark, so the × still takes out only the widget\'s words', async () => {
    h.sendChat.mockRejectedValueOnce(new ApiError('This chat does not exist', 404, 'session_not_found'))
    const m = mount({ sessionId: NEW_KEY })
    act(() => m.result.current.c.editInput('mine'))
    raise(ACTION, { frame: 'Sales', host: host() })
    act(() => m.result.current.c.addWidgetSuggestion())
    await sendDraft(m)
    expect(m.result.current.c.input).toBe(`mine\n\n${ACTION}`)
    act(() => m.result.current.c.clearWidgetDraft())
    expect(m.result.current.c.input).toBe('mine')
    expect(m.result.current.c.widgetDraft).toBeNull()
  })

  it('🔴 keeps the mark with the draft Revert to original brings back', async () => {
    const { api } = await import('../../../lib/api')
    ;(api as unknown as Record<string, unknown>).optimizePrompt = vi.fn(async () => ({ changed: true, optimized: 'A tidier ask' }))
    try {
      for (const between of ['a picked follow-up', 'the ×'] as const) {
        const m = mount({ sessionId: NEW_KEY })
        raise(ACTION, { frame: 'Sales', host: host() })
        await act(async () => { await m.result.current.c.optimize() })
        expect(m.result.current.c.input).toBe('A tidier ask')
        if (between === 'the ×') act(() => m.result.current.c.clearWidgetDraft())
        else act(() => m.result.current.c.replaceDraft('What changed since Monday?'))
        act(() => m.result.current.c.revertOptimize())
        expect(m.result.current.c.input).toBe(ACTION)
        expect(m.result.current.c.widgetDraft, between).not.toBeNull()
        expect(m.result.current.c.widgetDraft?.mixed, `${between}: its own mark, with its ×`).toBeFalsy()
        await sendDraft(m)
        expect(lastMeta()).toEqual({ widget_action: true })
      }
    } finally {
      delete (api as unknown as Record<string, unknown>).optimizePrompt
    }
  })

  it('takes the widget\'s words out with the × once Revert brings them back', async () => {
    const { api } = await import('../../../lib/api')
    ;(api as unknown as Record<string, unknown>).optimizePrompt = vi.fn(async () => ({ changed: true, optimized: 'A tidier ask' }))
    try {
      const m = mount({ sessionId: NEW_KEY })
      raise(ACTION, { frame: 'Sales', host: host() })
      await act(async () => { await m.result.current.c.optimize() })
      act(() => m.result.current.c.revertOptimize())
      act(() => m.result.current.c.clearWidgetDraft())
      expect(m.result.current.c.input).toBe('')
      expect(m.result.current.c.widgetDraft).toBeNull()
    } finally {
      delete (api as unknown as Record<string, unknown>).optimizePrompt
    }
  })

  it('sends a spoken message as the owner\'s words, and a hands-free submit says no to no frame', async () => {
    const m = mount({ sessionId: NEW_KEY })
    const frame = host()
    raise(ACTION, { frame: 'Sales', host: frame })
    expect(m.result.current.c.widgetDraft).not.toBeNull()
    // Hands-free puts each chunk in the draft in place of what was there (its buffer), then a
    // separate chunk confirms and sends.
    act(() => m.result.current.c.replaceDraft('check the logs'))
    act(() => m.result.current.c.replaceDraft('go ahead'))
    await act(async () => { await m.result.current.c.send('go ahead', { inputOrigin: 'voice' }) })
    expect(lastMeta()).toEqual({})
    // None of it was the owner's no to the frame: its next message is still offered.
    await act(async () => { await new Promise((r) => setTimeout(r, 2100)) })
    raise('[UI] next', { frame: 'Sales', host: frame })
    expect([m.result.current.c.input, m.result.current.c.widgetSuggestion?.text]).toContain('[UI] next')
  })

  it('sends a spoken message after an Optimize as the spoken words alone, with no original', async () => {
    const { api } = await import('../../../lib/api')
    ;(api as unknown as Record<string, unknown>).optimizePrompt = vi.fn(async () => ({ changed: true, optimized: 'A tidier ask' }))
    try {
      const m = mount({ sessionId: NEW_KEY })
      act(() => m.result.current.c.editInput('tidy this'))
      await act(async () => { await m.result.current.c.optimize() })
      expect(m.result.current.c.input, 'premise: rewritten').toBe('A tidier ask')
      // Hands-free's buffer, then its submit, even of the very words the rewrite holds.
      act(() => m.result.current.c.replaceDraft('A tidier ask'))
      await act(async () => { await m.result.current.c.send('A tidier ask', { inputOrigin: 'voice' }) })
      expect(lastMeta()).toEqual({})
      expect(m.result.current.turns.map(turnText).at(-1)).toBe('A tidier ask')
    } finally {
      delete (api as unknown as Record<string, unknown>).optimizePrompt
    }
  })

  it('routes hands-free\'s buffer and submit through the draft\'s replace, never its edit', () => {
    // A structure pin: dictation replaces the draft (no widget's mark carries over to it), and
    // the edit that empties a widget's draft, which is the owner's no to its frame, is not used.
    const composer = readFileSync(join(process.cwd(), 'src/ui/Composer.tsx'), 'utf8')
    expect(composer).toContain('onBuffer: (text) => (onReplace ?? onChange)(text)')
    expect(composer).toContain('onSubmit: (text) => { (onReplace ?? onChange)(text); onHandsFreeSubmit!(text) }')
    const chat = readFileSync(join(process.cwd(), 'src/pages/chat/composer/ChatComposer.tsx'), 'utf8')
    // Its replace also takes down what an edit would: Revert's saved draft and the follow-ups.
    expect(chat).toContain('onReplace={(v) => { replaceDraft(v); if (preOptimize !== null) setPreOptimize(null); if (followups.length) setFollowups([]) }}')
    expect(chat).toMatch(/onHandsFreeSubmit=\{\(t\) => \{ void send\(t, \{ inputOrigin: 'voice' \}\) \}\}/)
  })

  it('🔴 holds Send with its reason only while the draft holds the widget\'s message, and says why on the notice line', async () => {
    const m = mount({ sessionId: NEW_KEY })
    raise(ACTION, { frame: 'Sales', host: host() })
    expect(m.result.current.c.fillHold).toBe(FILL_HOLD)
    await act(async () => { await m.result.current.c.send() })
    expect(h.sendChat).not.toHaveBeenCalled()
    expect(m.result.current.c.notice.notice?.text).toBe(FILL_HOLD)
    act(() => m.result.current.c.replaceDraft('What changed since Monday?'))
    expect(m.result.current.c.fillHold).toBe('')
    expect(m.result.current.c.notice.notice, 'the line goes with the hold').toBeNull()
    // And when the hold runs out.
    m.unmount()
    const other = mount({ sessionId: NEW_KEY })
    raise(ACTION, { frame: 'Notes', host: host() })
    await act(async () => { await other.result.current.c.send() })
    expect(other.result.current.c.notice.notice?.text).toBe(FILL_HOLD)
    await act(async () => { await new Promise((r) => setTimeout(r, 1100)) })
    expect(other.result.current.c.fillHold).toBe('')
    expect(other.result.current.c.notice.notice, 'the line goes when the hold runs out').toBeNull()
  })

  it('🔴 counts a capture waiting for its crop as a draft under way: a frame\'s message waits as a chip', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    try {
      const m = mount({ sessionId: NEW_KEY })
      act(() => m.result.current.c.setSnip({ url: 'data:image/png;base64,AA==', width: 1, height: 1, source: document.createElement('canvas') }))
      later(2100)
      raise(ACTION, { frame: 'Sales', host: host() })
      expect(m.result.current.c.input).toBe('')
      expect(m.result.current.c.widgetSuggestion?.text).toBe(ACTION)
    } finally { vi.useRealTimers() }
  })

  it('gives a steer of your own back unmarked when its Edit takes it from the queue', async () => {
    h.sendChat.mockImplementation(receiptFor('steered'))
    const m = mount({ sessionId: NEW_KEY, turns: [userTurn('Fix it'), assistantTurn('Working…')], streaming: true, takesSteers: true })
    act(() => m.result.current.c.editInput('and the tests'))
    await act(async () => { await m.result.current.c.send() })
    const sent = h.sendChat.mock.calls[0][5].messageId as string
    act(() => m.result.current.c.takeBackQueued('and the tests', sent))
    expect(m.result.current.c.input).toBe('and the tests')
    expect(m.result.current.c.widgetDraft).toBeNull()
  })

  /** The optimizer, answering only when the case says so. */
  async function heldOptimizer() {
    const { api } = await import('../../../lib/api')
    let answer: (v: unknown) => void = () => {}
    ;(api as unknown as Record<string, unknown>).optimizePrompt = vi.fn(() => new Promise((r) => { answer = r }))
    return { answer: (optimized: string) => answer({ changed: true, optimized }), done: () => { delete (api as unknown as Record<string, unknown>).optimizePrompt } }
  }
  const REWRITE = 'Open https://x.example/a'
  const sentMetas = () => h.sendChat.mock.calls.map((c) => c[2] as Record<string, unknown>)

  it.each(['the ×', 'emptying it', 'a pick', 'Send'] as const)(
    'never puts the rewrite of a widget\'s message in the composer unmarked when %s comes while Optimize runs',
    async (between) => {
      const optimizer = await heldOptimizer()
      try {
        const m = mount({ sessionId: NEW_KEY })
        raise(ACTION, { frame: 'Sales', host: host() })
        let optimizing: Promise<void> = Promise.resolve()
        act(() => { optimizing = m.result.current.c.optimize() })
        if (between === 'the ×') act(() => m.result.current.c.clearWidgetDraft())
        if (between === 'emptying it') act(() => m.result.current.c.editInput(''))
        if (between === 'a pick') act(() => m.result.current.c.replaceDraft('mine'))
        if (between === 'Send') await sendDraft(m)
        await act(async () => { optimizer.answer(REWRITE); await optimizing })
        const c = m.result.current.c
        expect(c.input === REWRITE && c.widgetDraft === null, 'the rewrite, unmarked').toBe(false)
        if (between === 'a pick') expect(c.input).toBe('mine')
        // The ×'s no stays on the line: the dropped rewrite says nothing over it.
        if (between === 'the ×') expect(c.notice.notice?.text).toBe(SALES_NO)
        if (c.input.trim()) await sendDraft(m)
        for (const meta of sentMetas()) {
          if (between === 'a pick' && meta.widget_action === undefined) expect(meta.original, 'the owner\'s pick carries no original').toBeUndefined()
          else expect(meta.widget_action).toBe(true)
        }
        expect(h.sendChat.mock.calls.some((call) => call[0] === REWRITE && !(call[2] as Record<string, unknown>).widget_action)).toBe(false)
      } finally { optimizer.done() }
    })

  it('🔴 marks the rewrite of a widget\'s message the owner sends, and keeps the widget\'s words as its original', async () => {
    const optimizer = await heldOptimizer()
    try {
      const m = mount({ sessionId: NEW_KEY })
      raise(ACTION, { frame: 'Sales', host: host() })
      let optimizing: Promise<void> = Promise.resolve()
      act(() => { optimizing = m.result.current.c.optimize() })
      await act(async () => { optimizer.answer(REWRITE); await optimizing })
      expect(m.result.current.c.input).toBe(REWRITE)
      expect(m.result.current.c.widgetDraft).not.toBeNull()
      await sendDraft(m)
      expect(h.sendChat).toHaveBeenLastCalledWith(REWRITE, NEW_KEY, { widget_action: true, original: ACTION }, undefined, undefined, id)
      // The rewrite's very words put back by a replace, unmarked, are still the widget's rewrite.
      const again = mount({ sessionId: NEW_KEY })
      raise(ACTION, { frame: 'Notes', host: host() })
      act(() => { optimizing = again.result.current.c.optimize() })
      await act(async () => { optimizer.answer(REWRITE); await optimizing })
      act(() => again.result.current.c.replaceDraft(REWRITE))
      expect(again.result.current.c.widgetDraft, 'premise: the replace left it unmarked').toBeNull()
      expect(again.result.current.c.draftIsAWidgets, 'the one rule every send reads').toBe(true)
      await sendDraft(again)
      expect(h.sendChat).toHaveBeenLastCalledWith(REWRITE, NEW_KEY, { widget_action: true, original: ACTION }, undefined, undefined, id)
    } finally { optimizer.done() }
  })

  it.each([[`${ACTION} (tidied)`], [`${ACTION}\n\nOpen https://x.example/a`]])(
    'keeps an Optimize\'s rewrite of a widget\'s message marked through the ×: %s',
    async (rewrite) => {
      const optimizer = await heldOptimizer()
      try {
        const m = mount({ sessionId: NEW_KEY })
        raise(ACTION, { frame: 'Sales', host: host() })
        let optimizing: Promise<void> = Promise.resolve()
        act(() => { optimizing = m.result.current.c.optimize() })
        await act(async () => { optimizer.answer(rewrite); await optimizing })
        act(() => m.result.current.c.clearWidgetDraft())
        expect(m.result.current.c.widgetDraft).not.toBeNull()
        await sendDraft(m)
        expect(lastMeta()).toMatchObject({ widget_action: true })
      } finally { optimizer.done() }
    })

  it('keeps an Optimize\'s rewrite marked through the × where the owner\'s own words came first', async () => {
    const optimizer = await heldOptimizer()
    try {
      const m = mount({ sessionId: NEW_KEY })
      act(() => m.result.current.c.editInput('please check:'))
      raise(ACTION, { frame: 'Sales', host: host() })
      act(() => m.result.current.c.addWidgetSuggestion())
      let optimizing: Promise<void> = Promise.resolve()
      act(() => { optimizing = m.result.current.c.optimize() })
      await act(async () => { optimizer.answer(`please check:\n\n${ACTION}\n\nThen open https://x.example/a`); await optimizing })
      act(() => m.result.current.c.clearWidgetDraft())
      expect(m.result.current.c.widgetDraft).not.toBeNull()
      await sendDraft(m)
      expect(lastMeta()).toMatchObject({ widget_action: true })
    } finally { optimizer.done() }
  })

  /** Fill, Optimize, then send *what*: no send carries the widget's words as an unmarked original,
   *  no bubble shows them unmarked, and ↑ recall doesn't give them back. */
  async function sendAfterOptimize(what: 'a send of other words' | 'a pick, then Send' | 'a voice send' | '/compact') {
    const optimizer = await heldOptimizer()
    try {
      const m = mount({ sessionId: NEW_KEY })
      raise(ACTION, { frame: 'Sales', host: host() })
      let optimizing: Promise<void> = Promise.resolve()
      act(() => { optimizing = m.result.current.c.optimize() })
      await act(async () => { optimizer.answer(REWRITE); await optimizing })
      if (what === 'a send of other words') await act(async () => { await m.result.current.c.send('What changed since Monday?') })
      if (what === 'a pick, then Send') { act(() => m.result.current.c.replaceDraft('What changed since Monday?')); await sendDraft(m) }
      if (what === 'a voice send') await act(async () => { await m.result.current.c.send('turn the lights off', { inputOrigin: 'voice' }) })
      if (what === '/compact') await act(async () => { await m.result.current.c.send('/compact') })
      for (const call of h.sendChat.mock.calls) {
        const meta = call[2] as Record<string, unknown>
        expect(meta.original === ACTION && meta.widget_action !== true, 'the widget\'s words as an unmarked original').toBe(false)
      }
      expect(m.result.current.turns.some((t) => t.role === 'user' && turnText(t) === ACTION && !t.fromWidget), 'a bubble of the widget\'s words, unmarked').toBe(false)
      expect(m.result.current.c.historyTexts).not.toContain(ACTION)
    } finally { optimizer.done() }
  }

  it.each(['a send of other words', 'a voice send', '/compact'] as const)(
    '🔴 carries no widget\'s words as the original of %s after Optimize',
    (what) => sendAfterOptimize(what))

  it('carries no widget\'s words as the original of a pick, then Send after Optimize', () => sendAfterOptimize('a pick, then Send'))

  it('🔴 marks a draft a quote lands in when the selection takes in a widget\'s words, whoever it names', async () => {
    for (const attribution of [undefined, 'You'] as const) {
      const m = mount({ sessionId: NEW_KEY })
      act(() => m.result.current.c.quoteToComposer('the week so far', attribution, true))
      expect(m.result.current.c.widgetDraft?.quoted).toBe(true)
      await sendDraft(m)
      expect(lastMeta()).toEqual({ widget_action: true })
    }
  })

  it('brings back with Revert only the mark the saved draft had', async () => {
    const optimizer = await heldOptimizer()
    try {
      const m = mount({ sessionId: NEW_KEY })
      act(() => m.result.current.c.editInput('my own ask'))
      let optimizing: Promise<void> = Promise.resolve()
      act(() => { optimizing = m.result.current.c.optimize() })
      await act(async () => { optimizer.answer('A tidier ask'); await optimizing })
      // A widget's message Added after the Optimize.
      raise(ACTION, { frame: 'Sales', host: host() })
      act(() => m.result.current.c.addWidgetSuggestion())
      act(() => m.result.current.c.revertOptimize())
      expect(m.result.current.c.input).toBe('my own ask')
      expect(m.result.current.c.widgetDraft).toBeNull()
    } finally { optimizer.done() }
  })

  it('gives an empty draft back unmarked when the × takes out all of it, so the owner\'s next words are theirs', async () => {
    const m = mount({ sessionId: NEW_KEY })
    raise(ACTION, { frame: 'Sales', host: host() })
    act(() => m.result.current.c.quoteToComposer('[UI] earlier', WIDGET_SAID))
    // The owner deletes the frame's message by hand, leaving only the quote, then the × takes that.
    const quote = m.result.current.c.input.slice(m.result.current.c.input.indexOf('> **A widget said:**'))
    act(() => m.result.current.c.editInput(quote))
    act(() => m.result.current.c.clearWidgetDraft())
    expect(m.result.current.c.input).toBe('')
    expect(m.result.current.c.widgetDraft).toBeNull()
    // A second × finds nothing to take out, and leaves it so.
    act(() => m.result.current.c.clearWidgetDraft())
    expect([m.result.current.c.input, m.result.current.c.widgetDraft]).toEqual(['', null])
    act(() => m.result.current.c.editInput('mine'))
    await sendDraft(m)
    expect(lastMeta()).toEqual({})
  })

  it('keeps an error on the notice line over a widget\'s tell', async () => {
    const m = mount({ sessionId: NEW_KEY })
    raise(ACTION, { frame: 'Sales', host: host() })
    await act(async () => { await m.result.current.c.send() })
    expect(m.result.current.c.notice.notice?.text, 'premise: the held send\'s reason').toBe(FILL_HOLD)
    const declined = host()
    act(() => { declineWidgetHost(declined) })
    raise('[UI] note', { frame: 'Notes', host: declined })
    expect(m.result.current.c.notice.notice?.text).toBe(FILL_HOLD)
    // A tell the line didn't show spent nothing: once the error goes, the frame's next post says it.
    act(() => m.result.current.c.notice.clear('widget-hold'))
    raise('[UI] note', { frame: 'Notes', host: declined })
    expect(m.result.current.c.notice.notice?.text).toBe('\u201cNotes\u201d is turned down while this chat is open.')
    // A tell the line did show spends the bound: the same frame's next post a moment later says nothing.
    act(() => m.result.current.c.notice.clear())
    raise('[UI] note', { frame: 'Notes', host: declined })
    expect(m.result.current.c.notice.notice).toBeNull()
    forgetWidgetHost(declined)
  })

  it('🔴 starts no goal loop from a draft that holds a widget\'s words, and says why', () => {
    const m = mount({ seed: ACTION, seedFromWidget: true })
    act(() => m.result.current.c.startGoalLoop())
    expect(m.navigate).not.toHaveBeenCalled()
    expect(m.result.current.c.input).toBe(ACTION)
    // A carried draft's words can't be told apart from the widget's: it has no ×.
    expect(m.result.current.c.notice.notice?.text).toBe(GOAL_REFUSAL_MIXED)
    // /goal on a new chat goes the same way.
    act(() => m.result.current.c.editInput(`/goal ${ACTION}`))
    act(() => m.result.current.c.touchComposer())
    return act(async () => { await m.result.current.c.send() }).then(() => {
      expect(m.navigate).not.toHaveBeenCalled()
      expect(m.result.current.c.input).toBe(`/goal ${ACTION}`)
    })
  })

  it('asks for the widget\'s words out before a goal loop, and for all of the draft once they can\'t be told apart', async () => {
    const { api } = await import('../../../lib/api')
    ;(api as unknown as Record<string, unknown>).optimizePrompt = vi.fn(async () => ({ changed: true, optimized: 'A tidier ask' }))
    try {
      const m = mount({ sessionId: NEW_KEY })
      raise(ACTION, { frame: 'Sales', host: host() })
      // A fill has its × to take the widget's words out with.
      act(() => m.result.current.c.startGoalLoop())
      expect(m.result.current.c.notice.notice?.text).toBe(GOAL_REFUSAL)
      // An Optimize's rewrite of them has none.
      await act(async () => { await m.result.current.c.optimize() })
      expect(m.result.current.c.widgetDraft?.mixed, 'premise: the rewrite is mixed').toBe(true)
      act(() => m.result.current.c.startGoalLoop())
      expect(m.navigate).not.toHaveBeenCalled()
      expect(m.result.current.c.input).toBe('A tidier ask')
      expect(m.result.current.c.notice.notice?.text).toBe(GOAL_REFUSAL_MIXED)
    } finally {
      delete (api as unknown as Record<string, unknown>).optimizePrompt
    }
  })

  it.each(['/goal', '/help', '/context'] as const)('writes the turn %s makes from a widget\'s draft as a widget\'s', async (cmd) => {
    const m = mount({ sessionId: NEW_KEY, turns: [userTurn('Show me the sales view.'), assistantTurn('Here it is.')] })
    raise(ACTION, { frame: 'Sales', host: host() })
    act(() => m.result.current.c.editInput(`${cmd} ${ACTION}`))
    await sendDraft(m)
    expect(h.sendChat).not.toHaveBeenCalled()
    const said = m.result.current.turns.at(-2)
    expect(turnText(said!)).toBe(`${cmd} ${ACTION}`)
    expect(said?.fromWidget).toBe(true)
  })

  it('reads the mark from the same state as the draft\'s text', () => {
    // A structure pin: no effect can clear one without the other.
    const source = readFileSync(join(process.cwd(), 'src/pages/chat/composer/useChatComposer.ts'), 'utf8')
    expect(source).toContain('const input = draft.text')
    expect(source).toContain('const fromWidget = opts?.widgetAction === true || (ownDraft && draftIsAWidgets)')
    // The palette's combined send and MissingChat's carry read the same rule.
    expect(source).toContain('const widgetAction = draftIsAWidgets\n')
    expect(readFileSync(join(process.cwd(), 'src/pages/chat/composer/ChatComposer.tsx'), 'utf8')).toContain('onSend={sendWithPrompt}')
    expect(readFileSync(join(process.cwd(), 'src/pages/ChatPage.tsx'), 'utf8')).toContain('fromWidget={composer.draftIsAWidgets}')
    // The passive effect that cleared it after the text emptied is gone.
    expect(source).not.toContain('widgetDraftRef')
  })
})

describe('a goal loop starts from a new thread\'s composer (#3824)', () => {
  it('/goal <task> opens the loop composer on that task, as a goal loop, and nothing reaches the model', async () => {
    const m = mount()
    await act(async () => { await m.result.current.c.send('/goal Find a seed supplier that ships north') })
    expect(h.createChatSession).not.toHaveBeenCalled()
    expect(h.sendChat).not.toHaveBeenCalled()
    expect(m.navigate).toHaveBeenCalledWith(`loop?${new URLSearchParams({ kind: 'goal', task: 'Find a seed supplier that ships north' })}`)
  })

  it('the + menu\'s row takes the draft as the task, and the project the chat was scoped to', async () => {
    const m = mount()
    act(() => { m.result.current.c.setInput('Plan the raised beds'); m.result.current.c.setProjectId('p-garden') })
    act(() => { m.result.current.c.startGoalLoop() })
    expect(m.navigate).toHaveBeenCalledWith(`loop?${new URLSearchParams({ kind: 'goal', task: 'Plan the raised beds', project: 'p-garden' })}`)
  })

  it('a started chat says a goal loop starts from a new chat, and nothing reaches the model', async () => {
    const m = mount({ sessionId: NEW_KEY, turns: [userTurn('Hello'), assistantTurn('Hello again.')] })
    await act(async () => { await m.result.current.c.send('/goal Find a seed supplier') })
    expect(h.sendChat).not.toHaveBeenCalled()
    expect(m.navigate).not.toHaveBeenCalled()
    expect(turnText(m.result.current.turns.at(-1)!)).toContain('A goal loop starts from a new chat')
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
