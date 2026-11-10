import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, fireEvent, waitFor, within, renderHook, cleanup } from '@testing-library/react'
import { useState } from 'react'
import userEvent from '@testing-library/user-event'
import { EditorView } from '@codemirror/view'
import { runState, runningState } from '../../../test/runState'
import type { ChatTurn } from '../chatTypes'
import type { ChatStream } from '../useChatStream'

// ── Chat preferences hold the rare overrides ───────────────────────────────────────────────────
//
// This chat's explanations, its reply style and Optimize sit behind one gear on the chat box, and
// the footer keeps what is picked often. A pick applies at once to this chat only and says Saved;
// before the chat exists it is held and written as the chat is created, before the first message
// is sent. The gear shows a dot while the chat states a choice of its own and its description says
// which. Optimize this draft closes the window, says the optimizer is at work, and offers the
// revert with the caret back in the draft. /preferences opens the window; Escape closes it, gives
// the focus back to the gear and leaves a running turn running; Change your defaults opens
// Settings → Agent.
//
// Driven through ChatPage, as the owner meets it; each request is read where it leaves the page.

const SESSION = 'chat-prefs-rare'

const h = vi.hoisted(() => ({
  calls: [] as Array<[string, unknown[]]>,
  detail: {} as Record<string, unknown>,
  answers: {} as Record<string, (...args: unknown[]) => unknown>,
  /** The reply style the server holds for the chat, as its PATCHes set it. */
  style: '',
}))

vi.mock('../../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../../lib/api')>()
  const api = new Proxy({} as Record<string, unknown>, {
    get(_t, p) {
      if (typeof p !== 'string') return undefined
      return (...args: unknown[]) => {
        h.calls.push([p, args])
        return p in h.answers ? h.answers[p](...args) : Promise.resolve([])
      }
    },
  })
  return { ...real, api }
})

class FakeSocket {
  static open: FakeSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 1
  constructor(public url: string) { FakeSocket.open.push(this); setTimeout(() => this.onopen?.(), 0) }
  send(): void {}
  close(): void { this.readyState = 3 }
}

const STYLES = [
  { id: 'default', title: 'Default', description: 'No style instruction.' },
  { id: 'terse', title: 'Terse', description: 'Short and direct.' },
]

/** The voice state a detail and each PATCH answer carry: this chat's own choices, resolved, and the
 *  whole text the next turn appends, the reply style's section then the explanations'. */
function voice(style = '', explain = '') {
  const sections = [
    ...(style ? ['[REPLY STYLE: Terse]\nBe brief.\n[END REPLY STYLE]'] : []),
    ...(explain ? [`[EXPLANATIONS: ${explain}]\nExplain things that way.\n[END EXPLANATIONS]`] : []),
  ]
  return {
    reply_style: style, reply_style_effective: style || 'default', reply_style_source: style ? 'conversation' : 'platform',
    reply_style_agent_default: '',
    reply_style_preview: {
      style: style || 'default', source: style ? 'conversation' : 'platform', persona: '', explain: explain || 'briefly',
      explain_source: explain ? 'conversation' : 'platform', applies: style ? 'style' : 'none',
      instruction: sections.join('\n'), first_turn_only: false, refused: '',
    },
    explain, explain_effective: explain || 'briefly', explain_source: explain ? 'conversation' : 'platform', explain_default: 'briefly',
  }
}

const chat = (over: Record<string, unknown> = {}) => ({
  key: SESSION, title: 'Garden plans', run_state: runState({ session: SESSION }), queue: [],
  task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
  messages: [
    { role: 'user', content: 'Plan the garden.', ts: '2026-11-01T09:00:00+00:00' },
    { role: 'assistant', content: 'Here is a plan.', ts: '2026-11-01T09:00:05+00:00' },
  ],
  ...voice(),
  ...over,
})

const called = (name: string) => h.calls.filter(([n]) => n === name).map(([, args]) => args)
const settle = (ms = 30) => act(async () => { await new Promise((r) => setTimeout(r, ms)) })
const composerText = () => document.querySelector('[data-composer-region] .cm-content')?.textContent ?? ''
const gear = () => screen.getByRole('button', { name: 'Chat preferences' })
const dialog = () => screen.getByRole('dialog', { name: 'Chat preferences' })

let ChatPage: typeof import('../../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../../app/appearance')['AppearanceProvider']

beforeEach(async () => {
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  Element.prototype.scrollIntoView ??= () => {}
  if (typeof globalThis.IntersectionObserver === 'undefined') {
    vi.stubGlobal('IntersectionObserver', class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
      takeRecords(): [] { return [] }
    })
  }
  sessionStorage.clear()
  h.calls.length = 0
  h.style = ''
  h.detail = chat()
  h.answers = {
    chatSessionDetail: () => Promise.resolve({ ...h.detail }),
    replyStyles: () => Promise.resolve({ styles: STYLES, precedence: ['conversation', 'agent', 'platform'] }),
    // As the server does: a reply-style PATCH sets the chat's style, and an explanations PATCH
    // answers with the style the chat holds.
    setSessionReplyStyle: (_s, style) => { h.style = String(style); return Promise.resolve({ ok: true, ...voice(h.style) }) },
    setSessionExplain: (_s, explain) => Promise.resolve({ ok: true, ...voice(h.style, String(explain)) }),
    optimizePrompt: () => Promise.resolve({ changed: true, optimized: 'A clearer plan for the garden, please' }),
    sendChat: () => Promise.resolve({ disposition: 'started', message_id: 'msg_01jbxq3c6m8n9p2r4s5t6v7w8x', accepted_at: '2026-11-01T09:01:00+00:00' }),
    stopChat: () => Promise.resolve({ ok: true }),
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    sessionTemplates: () => Promise.resolve([]),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
  }
  ;({ ChatPage } = await import('../../ChatPage'))
  ;({ AppearanceProvider } = await import('../../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

async function open(navigate: (to: string) => void = () => {}) {
  render(<AppearanceProvider><ChatPage sub={SESSION} navigate={navigate} query={{}} setQuery={() => {}} /></AppearanceProvider>)
  await screen.findByText(/Here is a plan/)
  await settle(60)
}

function type(text: string) {
  const editor = EditorView.findFromDOM(document.querySelector('[data-composer-region] .cm-editor') as HTMLElement)
  act(() => { editor?.dispatch({ changes: { from: 0, to: editor.state.doc.length, insert: text } }) })
}

async function openPreferences() {
  fireEvent.click(gear())
  return screen.findByRole('dialog', { name: 'Chat preferences' })
}

const saidSaved = (el: HTMLElement) => within(el).queryAllByRole('status').some((s) => s.textContent === 'Saved')

describe('the chat box', () => {
  it('has Chat preferences, and neither a reply-style pill nor an Optimize button', async () => {
    await open()
    expect(gear().getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByRole('button', { name: /^Reply style/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Optimize prompt (⌘↵)' })).toBeNull()
    expect(gear().compareDocumentPosition(screen.getByRole('button', { name: /^Task mode: / })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('shows the dot and names what this chat changed on a chat with its own choices', async () => {
    h.detail = chat(voice('terse', 'step_by_step'))
    await open()
    await waitFor(() => expect(gear()).toHaveAccessibleDescription(/Changed for this chat: explanations, reply style\.$/))
    expect(document.querySelector('[data-prefs-changed]')?.getAttribute('aria-hidden')).toBe('true')
  })

  it('shows none on a chat that follows its defaults', async () => {
    await open()
    expect(gear()).toHaveAccessibleDescription('Chat preferences: explanations, reply style and Optimize, for this chat.')
    expect(gear().getAttribute('title')).toBeNull()
    expect(document.querySelector('[data-prefs-changed]')).toBeNull()
  })

  it('shows none for a style the agent chose: a default set elsewhere is named inside, not dotted', async () => {
    h.detail = chat({ reply_style_agent_default: 'terse', reply_style_effective: 'terse', reply_style_source: 'agent' })
    await open()
    expect(document.querySelector('[data-prefs-changed]')).toBeNull()
    expect(gear()).toHaveAccessibleDescription('Chat preferences: explanations, reply style and Optimize, for this chat.')
  })
})

describe('the Chat preferences window', () => {
  it('holds Explanations, Reply style with its note word for word, and Optimize this draft with its key', async () => {
    await open()
    const win = await openPreferences()
    expect(gear().getAttribute('aria-expanded')).toBe('true')
    const lead = document.getElementById(win.getAttribute('aria-describedby') ?? '')
    expect(lead?.textContent).toBe('For this chat only. Your defaults and the agent stay as they are.')
    const explanations = within(win).getByRole('radiogroup', { name: 'Explanations' })
    expect(within(explanations).getAllByRole('radio').map((r) => r.getAttribute('aria-labelledby') && document.getElementById(r.getAttribute('aria-labelledby')!)?.textContent))
      .toEqual(['Your default (Briefly)', 'No preference', 'Briefly', 'Step by step', 'With the background'])
    expect(within(win).getByRole('radiogroup', { name: 'Reply style' })).toBeTruthy()
    expect(within(win).getByText('Changes style only, never a fact, a caveat or a refusal. A choice here applies to this conversation and does not edit the agent.')).toBeTruthy()
    expect(within(win).getByRole('button', { name: 'Optimize this draft ⌘↵' })).toBeTruthy()
    expect(within(win).getByRole('button', { name: 'Change your defaults' })).toBeTruthy()
  })

  it('opens with focus on the checked Explanations row', async () => {
    h.detail = chat(voice('', 'step_by_step'))
    await open()
    await openPreferences()
    await waitFor(() => expect(document.activeElement).toBe(within(dialog()).getByRole('radio', { name: 'Step by step' })))
    expect(document.activeElement?.getAttribute('aria-checked')).toBe('true')
  })

  it('moves the focus with Tab from the checked row through each section to Close and Change your defaults', async () => {
    // Change your defaults sits on the window's first line, beside what it describes, so in the
    // DOM's order Tab reaches it after Close.
    h.style = 'terse'
    h.detail = chat(voice('terse'))
    // jsdom lays nothing out, so every element's offsetParent is null and the window's trap would
    // count nothing as focusable: a laid-out element has one.
    vi.spyOn(HTMLElement.prototype, 'offsetParent', 'get').mockImplementation(function (this: HTMLElement) { return this.isConnected ? document.body : null })
    const user = userEvent.setup()
    await open()
    await openPreferences()
    const win = dialog()
    const first = within(win).getByRole('radio', { name: 'Your default (Briefly)' })
    await waitFor(() => expect(document.activeElement).toBe(first))
    const order = [
      within(win).getByRole('radio', { name: 'Terse' }),
      within(win).getByRole('button', { name: 'Show what is sent' }),
      within(win).getByRole('button', { name: 'Optimize this draft ⌘↵' }),
      within(win).getByRole('button', { name: 'Close' }),
      within(win).getByRole('button', { name: 'Change your defaults' }),
      first,
    ]
    const named = (el: Element | null) => el?.getAttribute('aria-label') || el?.textContent?.trim().slice(0, 30) || el?.tagName
    const reached: (string | undefined)[] = []
    for (let i = 0; i < order.length; i++) { await user.tab(); reached.push(named(document.activeElement)) }
    expect(reached).toEqual(order.map(named))
  })

  it('a reply-style pick PATCHes the reply style and an explanation pick PATCHes explain, each saying Saved', async () => {
    await open()
    const win = await openPreferences()
    const style = within(win).getByRole('radiogroup', { name: 'Reply style' })
    fireEvent.click(within(style).getByRole('radio', { name: 'Terse' }))
    await waitFor(() => expect(called('setSessionReplyStyle')).toEqual([[SESSION, 'terse']]))
    await waitFor(() => expect(saidSaved(style.closest('section') as HTMLElement)).toBe(true))
    const explain = within(win).getByRole('radiogroup', { name: 'Explanations' })
    fireEvent.click(within(explain).getByRole('radio', { name: 'Step by step' }))
    await waitFor(() => expect(called('setSessionExplain')).toEqual([[SESSION, 'step_by_step']]))
    await waitFor(() => expect(saidSaved(explain.closest('section') as HTMLElement)).toBe(true))
    expect(within(explain.closest('section') as HTMLElement).getByText(/^Now:/).textContent).toBe('Now: Step by step, for this chat')
    // The explanations answer keeps this chat's reply style, and what is sent holds both.
    expect(within(style.closest('section') as HTMLElement).getByText(/^Now:/).textContent).toBe('Now: Terse, for this chat')
    fireEvent.click(within(win).getByRole('button', { name: 'Show what is sent' }))
    const sent = win.querySelector('pre')?.textContent ?? ''
    expect(sent).toContain('[REPLY STYLE: Terse]')
    expect(sent).toContain('[EXPLANATIONS: step_by_step]')
  })

  it('an explanations pick waits for the reply-style write before it, and keeps this chat\'s reply style', async () => {
    let release: () => void = () => {}
    h.answers.setSessionReplyStyle = (_s, style) => new Promise((ok) => { release = () => { h.style = String(style); ok({ ok: true, ...voice(h.style) }) } })
    await open()
    const win = await openPreferences()
    const style = within(win).getByRole('radiogroup', { name: 'Reply style' })
    const explain = within(win).getByRole('radiogroup', { name: 'Explanations' })
    fireEvent.click(within(style).getByRole('radio', { name: 'Terse' }))
    fireEvent.click(within(explain).getByRole('radio', { name: 'Step by step' }))
    await settle()
    expect(called('setSessionExplain')).toEqual([])
    await act(async () => { release() })
    await waitFor(() => expect(called('setSessionExplain')).toEqual([[SESSION, 'step_by_step']]))
    await waitFor(() => expect(within(style.closest('section') as HTMLElement).getByText(/^Now:/).textContent).toBe('Now: Terse, for this chat'))
  })

  it('a refused pick says so in its section, and its row goes back', async () => {
    h.answers.setSessionExplain = () => Promise.reject(new Error('gateway refused'))
    await open()
    const win = await openPreferences()
    const explain = within(win).getByRole('radiogroup', { name: 'Explanations' })
    const section = explain.closest('section') as HTMLElement
    fireEvent.click(within(explain).getByRole('radio', { name: 'Step by step' }))
    expect((await within(section).findByRole('alert')).textContent).toBe('Not saved: gateway refused')
    expect(within(explain).getByRole('radio', { name: 'Your default (Briefly)' }).getAttribute('aria-checked')).toBe('true')
    expect(saidSaved(section)).toBe(false)
  })

  it('Optimize this draft is off with its reason on an empty draft', async () => {
    await open()
    const win = await openPreferences()
    const optimize = within(win).getByRole('button', { name: 'Optimize this draft ⌘↵' })
    expect(optimize.getAttribute('aria-disabled')).toBe('true')
    expect(optimize.getAttribute('title')).toContain('Type something first')
    fireEvent.click(optimize)
    expect(called('optimizePrompt')).toEqual([])
  })

  it('a press closes the window, says Optimizing your prompt…, then offers the revert with the caret in the draft', async () => {
    let answer: (v: unknown) => void = () => {}
    h.answers.optimizePrompt = () => new Promise((r) => { answer = r })
    await open()
    type('plan the garden')
    act(() => gear().focus())
    const win = await openPreferences()
    fireEvent.click(within(win).getByRole('button', { name: 'Optimize this draft ⌘↵' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Chat preferences' })).toBeNull())
    expect(screen.getAllByRole('status').some((s) => s.textContent?.includes('Optimizing your prompt…'))).toBe(true)
    await act(async () => { answer({ changed: true, optimized: 'A clearer plan for the garden, please' }) })
    await waitFor(() => expect(composerText()).toContain('A clearer plan for the garden, please'))
    expect(screen.getByRole('button', { name: /revert to original/ })).toBeTruthy()
    expect(screen.queryAllByRole('status').some((s) => s.textContent?.includes('Optimizing your prompt…'))).toBe(false)
    await waitFor(() => expect(document.activeElement?.classList.contains('cm-content')).toBe(true))
  })

  it('opens from /preferences, which sends nothing', async () => {
    await open()
    type('/preferences')
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
    expect(await screen.findByRole('dialog', { name: 'Chat preferences' })).toBeTruthy()
    expect(called('sendChat')).toEqual([])
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Chat preferences' })).toBeNull())
    type('/help')
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
    expect((await screen.findAllByText(/\/preferences/, { selector: '[data-transcript-turn] *' })).length).toBeGreaterThan(0)
    expect(called('sendChat')).toEqual([])
  })

  it('closes on Escape and gives the focus back to the gear', async () => {
    await open()
    act(() => gear().focus())
    await openPreferences()
    await waitFor(() => expect(document.activeElement?.getAttribute('role')).toBe('radio'))
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Chat preferences' })).toBeNull())
    await waitFor(() => expect(document.activeElement).toBe(gear()))
    expect(document.querySelector('[data-tooltip-bubble]')).toBeNull()
    expect(gear().getAttribute('aria-expanded')).toBe('false')
  })

  it('gives the focus back to the gear when a click, not the keyboard, opened the window', async () => {
    await open()
    await openPreferences()
    await waitFor(() => expect(document.activeElement?.getAttribute('role')).toBe('radio'))
    fireEvent.click(within(dialog()).getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Chat preferences' })).toBeNull())
    await waitFor(() => expect(document.activeElement).toBe(gear()))
    expect(document.querySelector('[data-tooltip-bubble]')).toBeNull()
  })

  it('Change your defaults closes the window and opens Settings → Agent', async () => {
    const navigate = vi.fn()
    await open(navigate)
    const win = await openPreferences()
    fireEvent.click(within(win).getByRole('button', { name: 'Change your defaults' }))
    expect(navigate).toHaveBeenCalledWith('settings/agent')
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Chat preferences' })).toBeNull())
  })

  it('closes on Escape while a turn runs, and the turn keeps running', async () => {
    h.detail = chat({ run_state: runningState({ session: SESSION }) })
    await open()
    // The turn's Stop key is armed only once its status row shows.
    await waitFor(() => expect(document.querySelector('[data-turn-status]')).not.toBeNull())
    await openPreferences()
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Chat preferences' })).toBeNull())
    await settle()
    expect(called('stopChat')).toEqual([])
    // With the window gone, Escape stops the turn: the key was there to stop it all along.
    fireEvent.keyDown(document.body, { key: 'Escape' })
    await waitFor(() => expect(called('stopChat')).toHaveLength(1))
  })
})

// ── For a chat not created yet, the window reads your default and names it ─────────────────────

describe('the window on a chat not created yet', () => {
  async function openWindow(explain: { source: string; homeDefault: string }) {
    const { ChatPreferencesWindow } = await import('./ChatPreferences')
    const readHomeDefault = vi.fn()
    render(
      <AppearanceProvider>
        <ChatPreferencesWindow readHomeDefault={readHomeDefault} canOptimize={false} onOptimize={vi.fn()} onChangeDefaults={vi.fn()} onClose={vi.fn()}
          replyStyle={{ choice: '', effective: 'default', source: explain.source, agentDefault: '', preview: null, styles: [], refused: '', onSelect: vi.fn() }}
          explain={{ choice: '', effective: 'default', refused: '', onSelect: vi.fn(), ...explain }} />
      </AppearanceProvider>,
    )
    return readHomeDefault
  }

  it('reads your default as it opens, and names it in the first row', async () => {
    const read = await openWindow({ source: '', homeDefault: '' })
    expect(read).toHaveBeenCalledTimes(1)
    cleanup()
    await openWindow({ source: '', homeDefault: 'with_background' })
    expect(screen.getByRole('radio', { name: 'Your default (With the background)' }).getAttribute('aria-checked')).toBe('true')
    expect(screen.getByText(/^Now:/, { selector: 'section:first-of-type *' }).textContent).toBe('Now: Your default (With the background)')
  })

  it('reads nothing on a chat whose detail carried it', async () => {
    const read = await openWindow({ source: 'platform', homeDefault: 'briefly' })
    expect(read).not.toHaveBeenCalled()
  })
})

// ── Before the chat exists, a pick is held and written as it is created, before the send ────────

describe('on a chat not created yet', () => {
  const NEW_KEY = 'chat-prefs-new'

  function mount() {
    return renderHook(() => {
      const [turns, setTurns] = useState<ChatTurn[]>([])
      const stream = {
        sessionRef: { current: null }, turns, setTurns,
        patchLastAssistant: (fn: (s: ChatTurn['segments']) => ChatTurn['segments']) => setTurns((prev) => prev.map((t, i) => (i === prev.length - 1 ? { ...t, segments: fn(t.segments) } : t))),
        dropTextRun: vi.fn(), streamingRef: { current: false }, markStreaming: vi.fn(), sayTurnEnded: vi.fn(),
        turnSeqRef: { current: 0 }, stoppedTurnRef: { current: false }, takesSteersRef: { current: false },
        setMissing: vi.fn(), setSteered: vi.fn(), followups: [], setFollowups: vi.fn(), followupsNeed: '', setFollowupsNeed: vi.fn(),
        checkWorkOffer: null, setCheckWorkOffer: vi.fn(), sessionBindingRef: { current: null }, bindingToldRef: { current: false }, bindingNonce: 0,
      } as unknown as ChatStream
      return useChatComposerRef.current!({
        sessionId: null, navigate: (to: string) => { h.calls.push(['navigate', [to]]) }, seed: '', initialProjectId: '', initialAgent: '', pendingRouting: null, setRoutingSuggestion: vi.fn(),
        setLiveRun: vi.fn(), started: turns.length > 0, stream, followNewTurn: vi.fn(), oweSpokenReply: vi.fn(),
      })
    })
  }
  const useChatComposerRef: { current: typeof import('./useChatComposer')['useChatComposer'] | null } = { current: null }

  beforeEach(async () => {
    cleanup()
    h.answers.createChatSession = () => Promise.resolve({ key: NEW_KEY })
    ;({ useChatComposer: useChatComposerRef.current } = await import('./useChatComposer'))
  })

  it('holds both picks, then writes them after create and before the first send', async () => {
    const { result } = mount()
    await act(async () => {
      expect(await result.current.replyStyle.pill.onSelect('terse')).toBe('held')
      expect(await result.current.explain.choice.onSelect('briefly')).toBe('held')
    })
    expect(called('setSessionReplyStyle')).toEqual([])
    expect(called('setSessionExplain')).toEqual([])
    act(() => result.current.setInput('Plan the garden for spring'))
    await act(async () => { await result.current.send() })
    // Before the navigate: it remounts the chat, whose one read of the detail must carry them.
    const order = h.calls.map(([n]) => n).filter((n) => ['createChatSession', 'setSessionReplyStyle', 'setSessionExplain', 'navigate', 'sendChat'].includes(n))
    expect(order).toEqual(['createChatSession', 'setSessionReplyStyle', 'setSessionExplain', 'navigate', 'sendChat'])
    expect(called('setSessionReplyStyle')).toEqual([[NEW_KEY, 'terse']])
    expect(called('setSessionExplain')).toEqual([[NEW_KEY, 'briefly']])
    // The explanations answer keeps the reply style, and its preview holds both.
    expect(result.current.replyStyle.pill.choice).toBe('terse')
    expect(result.current.replyStyle.pill.preview?.instruction).toContain('[EXPLANATIONS: briefly]')
    expect(result.current.explain.choice.choice).toBe('briefly')
  })
})
