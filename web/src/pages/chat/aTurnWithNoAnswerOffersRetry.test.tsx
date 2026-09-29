import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── A turn that ran its steps and wrote no answer says so, and offers Retry ──────────────────
//
// Measured: a review turn ran fifteen commands and its model then answered with nothing. The page
// showed the fifteen rows and "Response complete." — no reply, no notice, and nothing to press. The
// gateway now ends such a turn in an error that says it has no answer, so the turn is announced as
// ended in an error and its notice, the one it ended on, carries Retry, live and after a reload
// alike. Retry is the gateway's Regenerate, which runs a failed turn again from the message that
// started it.

const NOTICE = 'The agent ran 2 steps but did not write an answer. Send your message again to retry.'

const h = vi.hoisted(() => ({
  detail: {
    key: 'chat-6-x', title: 'Change review', messages: [] as unknown[], running: false,
    queue: [] as unknown[], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
  },
  chatSessionDetail: vi.fn(),
  regenerate: vi.fn(),
}))

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api')
  const ok = () => Promise.resolve([])
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    regenerate: h.regenerate,
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
  FakeSocket.last = null
  h.regenerate.mockReset().mockResolvedValue({ ok: true })
  h.chatSessionDetail.mockReset().mockResolvedValue({ ...h.detail })
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

const page = () =>
  render(
    <AppearanceProvider>
      <ChatPage sub="chat-6-x" navigate={() => {}} query={{}} setQuery={() => {}} />
    </AppearanceProvider>,
  )

const step = (id: string, command: string) => ({
  role: 'tool', content: 'bash', ts: '2026-03-02T09:01:00+00:00',
  meta: { tool_call_id: id, tool: 'bash', input: JSON.stringify({ command }), done: true },
})

const question = { role: 'user', content: 'Review the uncommitted diff.', ts: '2026-03-02T09:00:00+00:00' }

/** The alert the notice renders in, found by its sentence — not just any alert on the page. */
async function noticeStrip(): Promise<HTMLElement> {
  const text = await screen.findByText(NOTICE)
  const strip = text.closest('[role="alert"]')
  expect(strip, 'the notice is not in an alert').not.toBeNull()
  return strip as HTMLElement
}

describe('a turn that ran its steps and wrote no answer', () => {
  it('arriving live, says so, ends as an error, and Retry asks the question again', async () => {
    // The turn is running when the page opens, as the reviewer's was.
    h.chatSessionDetail.mockResolvedValue({ ...h.detail, running: true, messages: [question, step('call-1', 'git status'), step('call-2', 'ls -F')] })
    page()
    await screen.findByText('Review the uncommitted diff.')
    h.chatSessionDetail.mockResolvedValue({ ...h.detail, messages: [question, step('call-1', 'git status'), step('call-2', 'ls -F')] })
    pushFrame('chat_message', { session: 'chat-6-x', role: 'error', content: NOTICE })
    pushFrame('chat_done', { session: 'chat-6-x', outcome: 'error' })

    await noticeStrip()
    // The turn is not announced as a finished answer.
    await waitFor(() => expect(screen.getByText('Response ended with an error.')).toBeTruthy())
    expect(screen.queryByText('Response complete.')).toBeNull()

    // Read again once the turn has ended: Retry is offered on a settled turn only.
    const strip = await noticeStrip()
    await userEvent.setup().click(await within(strip).findByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(h.regenerate).toHaveBeenCalledWith('chat-6-x'))
  })

  it('after a reload, offers the same Retry', async () => {
    h.chatSessionDetail.mockResolvedValue({
      ...h.detail,
      last_turn_outcome: 'error',
      messages: [question, step('call-1', 'git status'), step('call-2', 'ls -F'), { role: 'error', content: NOTICE }],
    })
    page()
    const strip = await noticeStrip()
    await userEvent.setup().click(within(strip).getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(h.regenerate).toHaveBeenCalledWith('chat-6-x'))
  })

  it('is offered no Retry on an earlier turn — Retry re-asks only the last question', async () => {
    h.chatSessionDetail.mockResolvedValue({
      ...h.detail,
      messages: [
        question, step('call-1', 'git status'), { role: 'error', content: NOTICE },
        { role: 'user', content: 'Try once more, from the project folder.', ts: '2026-03-02T09:05:00+00:00' },
        { role: 'assistant', content: 'The change renames one helper.', ts: '2026-03-02T09:06:00+00:00' },
      ],
    })
    page()
    const strip = await noticeStrip()
    expect(within(strip).queryByRole('button', { name: 'Retry' })).toBeNull()
  })
})
