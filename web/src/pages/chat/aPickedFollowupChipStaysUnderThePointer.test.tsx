import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { runState } from '../../test/runState'

// ── A picked follow-up chip stays where it was, and the caret goes to the composer ────────────
//
// Measured in a browser on a chat two turns long: a double-click on a chip's label filled the
// composer, and its second click landed on the reply's Speak button, which asked for the reply to
// be read aloud; the dblclick landed on Stop. The page took the chips away on the first click, and
// the scrolled transcript moved the reply's own actions (Speak, Regenerate, Branch) under the
// pointer. The pick also left focus nowhere, though the chip's tooltip says it is there to edit.
//
// Now a pick fills the composer, puts the caret in it and leaves the chips in place, so a second
// click lands on the same chip. They go once the user moves on: types, sends, or opens another
// chat. (jsdom has no layout, so the fall-through itself is the browser's; what is pinned here is
// its cause.)

const CHIP = 'Add it to the school-term.md file'

const h = vi.hoisted(() => ({
  detail: {
    key: 'chat-7-x', title: 'Swim class', get run_state() { return runState({ session: 'chat-7-x' }) }, queue: [] as unknown[], task_mode: 'agent',
    approval: 'normal', memory_mode: 'persistent',
    messages: [
      { role: 'user', content: "Remind me what time Lina's swim class starts on Saturdays?", ts: '2026-10-02T09:00:00+00:00' },
      { role: 'assistant', content: "I couldn't find it. Tell me the time and I'll save it in school-term.md.", ts: '2026-10-02T09:00:05+00:00' },
    ] as unknown[],
  },
  chatSessionDetail: vi.fn(),
  sendChat: vi.fn(),
  voiceSynthesize: vi.fn(),
  regenerate: vi.fn(),
  onboarding: vi.fn(),
  answerExplain: vi.fn(),
}))

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api')
  const ok = () => Promise.resolve([])
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    sendChat: h.sendChat,
    voiceSynthesize: h.voiceSynthesize,
    regenerate: h.regenerate,
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
    // The new chat's explain question: setup done and the question open, and the claim answers.
    onboarding: h.onboarding,
    answerExplain: h.answerExplain,
  }
  // The page reads ~40 endpoints; anything not named above resolves to a shape-agnostic value.
  const api = new Proxy(base, {
    get(t, p: string) {
      if (p in t) return t[p]
      return (t[p] = (...__a: unknown[]) => ok())
    },
  })
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
  FakeSocket.last = null
  h.chatSessionDetail.mockReset().mockResolvedValue({ ...h.detail })
  h.sendChat.mockReset().mockResolvedValue({ ok: true, session: 'chat-7-x' })
  h.voiceSynthesize.mockReset().mockResolvedValue({ ok: true, chunks: 0 })
  h.regenerate.mockReset().mockResolvedValue({ ok: true })
  h.onboarding.mockReset().mockResolvedValue({ step: 'done', explain_asked: false })
  h.answerExplain.mockReset().mockResolvedValue({ ok: true, state: { step: 'done', explain_asked: true } })
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

async function chatWithChips() {
  const { container } = render(
    <AppearanceProvider>
      <ChatPage sub="chat-7-x" navigate={() => {}} query={{}} setQuery={() => {}} />
    </AppearanceProvider>,
  )
  await screen.findByText(/Tell me the time and I'll save it/)
  pushFrame('chat_followups', { session: 'chat-7-x', items: [CHIP] })
  const label = await screen.findByRole('button', { name: CHIP })
  const editor = container.querySelector('.cm-content') as HTMLElement
  expect(editor, 'the composer mounted its editor').toBeTruthy()
  return { label, editor }
}

const chips = () => screen.queryByRole('group', { name: 'Suggested follow-ups' })

describe('a picked follow-up chip', () => {
  it('fills the composer, puts the caret there, and stays where the second click lands', async () => {
    const user = userEvent.setup()
    const { label, editor } = await chatWithChips()

    await user.dblClick(label)

    expect(editor.textContent).toContain(CHIP)
    expect(chips(), 'the chips are still under the pointer for the second click').not.toBeNull()
    await waitFor(() => expect(editor.contains(document.activeElement), 'the caret is in the composer').toBe(true))
    expect(h.sendChat, 'a double-click sends nothing').not.toHaveBeenCalled()
    expect(h.voiceSynthesize, 'nor reads the reply aloud').not.toHaveBeenCalled()
    expect(h.regenerate, 'nor regenerates it').not.toHaveBeenCalled()
  })

  it('goes once the message it put in the composer is sent, which sends it as it reads', async () => {
    const user = userEvent.setup()
    const { label } = await chatWithChips()

    await user.click(label)
    expect(h.sendChat, 'a pick sends nothing').not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() => expect(h.sendChat).toHaveBeenCalledTimes(1))
    expect(h.sendChat.mock.calls[0][0]).toBe(CHIP)
    await waitFor(() => expect(chips(), 'sending is moving on: the chips go').toBeNull())
  })
})

// The new chat's page hands the explain question its draft and its composer: it waits while the
// composer holds a draft, and the editor has the focus once the window has gone.
describe('the explain question on a new chat', () => {
  beforeEach(async () => {
    vi.resetModules()
    ;({ ChatPage } = await import('../ChatPage'))
    ;({ AppearanceProvider } = await import('../../app/appearance'))
  })

  it('asks nothing while the new chat opens with a draft in its composer', async () => {
    render(
      <AppearanceProvider>
        <ChatPage sub="new" query={{ seed: 'half a thought' }} navigate={() => {}} setQuery={() => {}} />
      </AppearanceProvider>,
    )
    await act(() => new Promise((r) => setTimeout(r, 1200)))
    // The composer's model notice reads the onboarding state too: the claim is the question's own.
    expect(h.answerExplain, 'claimed while the composer holds a draft').not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog', { name: 'How should I explain things?' })).toBeNull()
  })

  it("gives the composer's editor the focus once the window has gone", async () => {
    const user = userEvent.setup()
    const { container } = render(
      <AppearanceProvider>
        <ChatPage sub="new" query={{}} navigate={() => {}} setQuery={() => {}} />
      </AppearanceProvider>,
    )
    const editor = await waitFor(() => {
      const e = container.querySelector('.cm-content') as HTMLElement | null
      expect(e, 'the composer mounted its editor').toBeTruthy()
      return e!
    })
    // The trap gives the focus back to what held it: off the editor, only the page's own hand-back
    // can put it there.
    ;(document.activeElement as HTMLElement | null)?.blur()
    const win = await screen.findByRole('dialog', { name: 'How should I explain things?' })
    await user.click(within(win).getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(editor.contains(document.activeElement), 'the caret is in the composer').toBe(true))
  })
})
