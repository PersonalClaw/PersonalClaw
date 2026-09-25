import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { TURN_ERROR_WITHOUT_REASON } from './turnError'

// ── A failed turn is SAID and SEEN, and edit & resend never silently deletes turns ───────────
//
// Each block below reproduces one measured defect on a fresh instance
// (`.lanes/day56b-evidence/`, `.lanes/ootb-evidence/`), driven through the real ChatPage:
//
//   1. `s20`: a provider whose connection was refused → `chat_message {role:'error', content:''}`
//      → a red error bar with NOTHING in it (the client's fallback only caught a MISSING value).
//   2. `s24E`: Edit & resend on a MIDDLE turn → `{"rewound": 0}` and the later turn gone from
//      disk, with nothing on screen saying it would be.
//   3. `c2-25`: a 60k-character paste → the turn's error landed ~21,000px below the viewport,
//      reachable only through "Jump to latest".
//   4. `s26`/`s33`: a link to a chat that does not exist rendered as a normal empty chat; a
//      message sent there failed "No such chat session." and was not saved.

const h = vi.hoisted(() => ({
  detail: {
    key: 'chat-6-x', title: 'Some chat', messages: [] as unknown[], running: false,
    queue: [] as unknown[], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
  },
  chatSessionDetail: vi.fn(),
  sendChat: vi.fn(),
  editResend: vi.fn(),
  navigate: vi.fn(),
}))

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api')
  const ok = () => Promise.resolve([])
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    sendChat: h.sendChat,
    editResend: h.editResend,
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
  }
  // The page reads ~40 endpoints; anything not named above resolves to a shape-agnostic value.
  const api = new Proxy(base, {
    get(t, p: string) {
      if (p in t) return t[p]
      return (t[p] = (...__a: unknown[]) => ok())
    },
  })
  // Every REAL export (the error class, the code predicate) with only `api` replaced: the page
  // branches on them, and a mock that dropped them would make every failure path throw.
  return { ...actual, api }
})

class FakeSocket {
  static last: FakeSocket | null = null
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 1
  constructor(public url: string) { FakeSocket.last = this; setTimeout(() => this.onopen?.(), 0) }
  send(): void {}
  close(): void { this.readyState = 3 }
}

function pushFrame(type: string, data: Record<string, unknown>) {
  act(() => { FakeSocket.last?.onmessage?.({ data: JSON.stringify({ type, data }) }) })
}

let ChatPage: typeof import('../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../app/appearance')['AppearanceProvider']
let ApiError: typeof import('../../lib/api')['ApiError']
let scrollIntoView: ReturnType<typeof vi.fn>

beforeEach(async () => {
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  scrollIntoView = vi.fn()
  Element.prototype.scrollIntoView = scrollIntoView as unknown as Element['scrollIntoView']
  if (typeof globalThis.IntersectionObserver === 'undefined') {
    vi.stubGlobal('IntersectionObserver', class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
      takeRecords(): [] { return [] }
    })
  }
  FakeSocket.last = null
  h.navigate.mockReset()
  h.sendChat.mockReset().mockResolvedValue({ ok: true, session: 'chat-6-x' })
  h.editResend.mockReset().mockResolvedValue({ ok: true, rewound: 0 })
  h.chatSessionDetail.mockReset().mockResolvedValue({ ...h.detail })
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
  ;({ ApiError } = await import('../../lib/api'))
})

afterEach(() => { vi.unstubAllGlobals() })

const page = (query: Record<string, string> = {}) =>
  render(
    <AppearanceProvider>
      <ChatPage sub="chat-6-x" navigate={h.navigate} query={query} setQuery={() => {}} />
    </AppearanceProvider>,
  )

const exchange = (i: number, answer: string) => [
  { role: 'user', content: `question ${i}`, ts: `2026-09-25T10:0${i}:00+00:00` },
  { role: 'assistant', content: answer, ts: `2026-09-25T10:0${i}:30+00:00` },
]

// ── 1. SAID: an error with no message still says something ──────────────────────────────────

describe('a turn error whose message is empty', () => {
  it('arriving live, renders a sentence — never an empty bar', async () => {
    page()
    await waitFor(() => expect(h.chatSessionDetail).toHaveBeenCalled())
    pushFrame('chat_message', { session: 'chat-6-x', role: 'error', content: '' })
    const bar = await screen.findByRole('alert')
    expect(bar.textContent?.trim()).toBe(TURN_ERROR_WITHOUT_REASON)
  })

  it('persisted that way (a transcript from before the fix), renders the same sentence on reload', async () => {
    h.chatSessionDetail.mockResolvedValue({
      ...h.detail,
      messages: [{ role: 'user', content: 'hi', ts: '2026-09-25T10:00:00+00:00' }, { role: 'error', content: '' }],
    })
    page()
    const bar = await screen.findByRole('alert')
    expect(bar.textContent?.trim()).toBe(TURN_ERROR_WITHOUT_REASON)
  })

  it('with a message, shows the message (the fallback only fills a gap)', async () => {
    page()
    await waitFor(() => expect(h.chatSessionDetail).toHaveBeenCalled())
    pushFrame('chat_message', { session: 'chat-6-x', role: 'error', content: 'The connection to the model provider was lost.' })
    expect((await screen.findByRole('alert')).textContent).toContain('The connection to the model provider was lost.')
  })
})

// ── 2. Edit & resend on an EARLIER turn: said before, kept after ────────────────────────────

describe('Edit & resend', () => {
  async function openEditorOn(turnText: string) {
    const user = userEvent.setup()
    await screen.findByText(turnText)
    // The user turn's own action row: its "Edit & resend" button, in turn order.
    const bubbles = screen.getAllByRole('button', { name: 'Edit & resend' })
    const row = bubbles.find((b) => b.closest('.group\\/msg')?.textContent?.includes(turnText))!
    await user.click(row)
    return user
  }

  it('on an earlier turn, says the later turns will be replaced and where they go, and resends as a rewind', async () => {
    h.chatSessionDetail.mockResolvedValue({ ...h.detail, messages: [...exchange(1, 'ALPHA'), ...exchange(2, 'BRAVO')] })
    page()
    const user = await openEditorOn('question 1')

    // BEFORE it happens: the consequence is on screen while the editor is open.
    const notice = screen.getByText(/Resending replaces everything below this message/)
    expect(notice.textContent).toMatch(/kept in this chat’s history/)
    expect(notice.textContent).toMatch(/restore it as a branch/)
    const resend = screen.getByRole('button', { name: 'Resend & replace' })
    // …and it is wired to the field a screen reader reads.
    expect(screen.getByRole('textbox', { name: 'Edit your message' }).getAttribute('aria-describedby')).toBeTruthy()

    await user.click(resend)
    await waitFor(() => expect(h.editResend).toHaveBeenCalled())
    // 6th arg is `rewind`. On the defect it was undefined → the server deleted the later
    // turns with no trail ({"rewound": 0}).
    expect(h.editResend.mock.calls[0][5]).toBe(true)
  })

  it('on the latest turn, is a plain resend — nothing below it is replaced', async () => {
    h.chatSessionDetail.mockResolvedValue({ ...h.detail, messages: [...exchange(1, 'ALPHA'), ...exchange(2, 'BRAVO')] })
    page()
    const user = await openEditorOn('question 2')
    expect(screen.queryByText(/Resending replaces everything below this message/)).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Resend' }))
    await waitFor(() => expect(h.editResend).toHaveBeenCalled())
    expect(h.editResend.mock.calls[0][5]).toBe(false)
  })

  it('on a temporary chat, does not promise a branch the server would refuse', async () => {
    h.chatSessionDetail.mockResolvedValue({ ...h.detail, memory_mode: 'temporary', messages: [...exchange(1, 'ALPHA'), ...exchange(2, 'BRAVO')] })
    page()
    await openEditorOn('question 1')
    const notice = screen.getByText(/Resending replaces everything below this message/)
    expect(notice.textContent).not.toMatch(/branch/)
  })
})

// ── 3. SEEN: sending follows the turn to its outcome ────────────────────────────────────────

describe('where a turn’s outcome lands', () => {
  /** jsdom has no layout. Give the transcript scroller the geometry of the measured case: a
   *  bubble far taller than the viewport, the view parked at its top. */
  function tallTranscript() {
    const el = document.querySelector('[data-transcript-scroll]') as HTMLElement
    Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => 30_000 })
    Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => 800 })
    Object.defineProperty(el, 'scrollTop', { configurable: true, writable: true, value: 0 })
    return el
  }

  it('lands on the outcome after a send, even when the new message is taller than the view', async () => {
    h.chatSessionDetail.mockResolvedValue({ ...h.detail, messages: exchange(1, 'ALPHA') })
    const user = userEvent.setup()
    page({ seed: 'a very long paste' })
    await screen.findByText('ALPHA')
    tallTranscript()
    scrollIntoView.mockClear()

    await user.click(screen.getByRole('button', { name: 'Send message' }))
    await waitFor(() => expect(h.sendChat).toHaveBeenCalled())
    // The outcome: the error frame the measured turn ended with.
    pushFrame('chat_message', { session: 'chat-6-x', role: 'error', content: 'Unable to allocate 26.4 GiB' })
    await screen.findByRole('alert')

    // On the defect the distance-from-bottom was measured AFTER the tall bubble rendered
    // (29,200px) and nothing scrolled: the error sat ~21,000px below the viewport.
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalled())
  })

  it('follows a NEW chat’s first message to its outcome, across the remount its own send causes', async () => {
    // The measured OOTB case (`e1-60k-repro-newchat`) was a brand-new chat. Its first send
    // creates the session and navigates `new` → `chat/<key>`, which REMOUNTS ChatSession — so
    // a follow armed on the instance that sent would die with it.
    const geometry = (key: 'scrollHeight' | 'clientHeight', value: number) => {
      const real = Object.getOwnPropertyDescriptor(HTMLElement.prototype, key)
      Object.defineProperty(HTMLElement.prototype, key, {
        configurable: true,
        get(this: HTMLElement) { return this.hasAttribute('data-transcript-scroll') ? value : (real?.get?.call(this) ?? 0) },
      })
      return () => { if (real) Object.defineProperty(HTMLElement.prototype, key, real) }
    }
    const restore = [geometry('scrollHeight', 30_000), geometry('clientHeight', 800)]
    try {
      const { api } = await import('../../lib/api')
      ;(api as unknown as Record<string, unknown>).createChatSession = () => Promise.resolve({ key: 'chat-new-1' })
      h.chatSessionDetail.mockResolvedValue({
        ...h.detail, key: 'chat-new-1', running: true,
        messages: [{ role: 'user', content: 'a very long paste', ts: '2026-09-25T10:00:00+00:00' }],
      })
      const user = userEvent.setup()
      const tree = (sub: string) => (
        <AppearanceProvider>
          <ChatPage sub={sub} navigate={h.navigate} query={sub ? {} : { seed: 'a very long paste' }} setQuery={() => {}} />
        </AppearanceProvider>
      )
      const r = render(tree(''))
      await user.click(await screen.findByRole('button', { name: 'Send message' }))
      await waitFor(() => expect(h.navigate).toHaveBeenCalledWith('chat/chat-new-1', { replace: true }))
      scrollIntoView.mockClear()
      r.rerender(tree('chat-new-1'))  // the remount the navigate causes
      await screen.findByText('a very long paste')

      pushFrame('chat_message', { session: 'chat-new-1', role: 'error', content: 'Unable to allocate 26.4 GiB' })
      await screen.findByRole('alert')
      await waitFor(() => expect(scrollIntoView).toHaveBeenCalled())
    } finally {
      restore.forEach((undo) => undo())
    }
  })

  it('does not yank a reader who scrolled up while the answer streams', async () => {
    // The falsifiability control: "always scroll" would pass the test above.
    h.chatSessionDetail.mockResolvedValue({ ...h.detail, messages: exchange(1, 'ALPHA') })
    page()
    await screen.findByText('ALPHA')
    const el = tallTranscript()
    act(() => { el.dispatchEvent(new Event('scroll')) })  // the user's own scroll, far from the bottom
    scrollIntoView.mockClear()

    pushFrame('chat_message', { session: 'chat-6-x', role: 'error', content: 'late news' })
    await screen.findByRole('alert')
    expect(scrollIntoView).not.toHaveBeenCalled()
  })
})

// ── 4. A chat that does not exist says so ───────────────────────────────────────────────────

describe('a link to a chat that does not exist', () => {
  it('says the chat does not exist and offers a new one — instead of an empty composer', async () => {
    h.chatSessionDetail.mockRejectedValue(new ApiError('not found', 404))
    const user = userEvent.setup()
    page()
    expect(await screen.findByRole('heading', { name: 'This chat doesn’t exist' })).toBeTruthy()
    // No composer to type into — on the defect this was the empty-chat hero + composer.
    expect(screen.queryByRole('button', { name: 'Send message' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Start a new chat' }))
    expect(h.navigate).toHaveBeenCalledWith('chat/new', { replace: true })
  })

  it('a message sent to it is not lost — it rides into the new chat', async () => {
    // The read raced the send (or the chat was deleted in another tab): the page is live,
    // and the send is what finds out.
    h.sendChat.mockRejectedValue(new ApiError('No such chat session.', 404, 'session_not_found'))
    const user = userEvent.setup()
    page({ seed: 'keep me' })
    await waitFor(() => expect(h.chatSessionDetail).toHaveBeenCalled())
    await user.click(await screen.findByRole('button', { name: 'Send message' }))

    const heading = await screen.findByRole('heading', { name: 'This chat doesn’t exist' })
    expect(within(heading.parentElement!).getByText(/Your message was not sent/)).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Start a new chat' }))
    expect(h.navigate).toHaveBeenCalledWith(`chat/new?seed=${encodeURIComponent('keep me')}`, { replace: true })
  })

  it('a read that failed for another reason is a load failure with a retry — not "does not exist"', async () => {
    h.chatSessionDetail.mockRejectedValueOnce(new ApiError('upstream broke', 500))
    const user = userEvent.setup()
    page()
    expect(await screen.findByText('Couldn\'t load your chat')).toBeTruthy()
    expect(screen.queryByText('This chat doesn’t exist')).toBeNull()
    await user.click(screen.getByRole('button', { name: /Retry/ }))
    await waitFor(() => expect(h.chatSessionDetail).toHaveBeenCalledTimes(2))
  })
})
