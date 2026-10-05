import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { hydrateTurns, turnText, type HistMsg } from './chatTypes'

// ── What she pasted goes with her message, whichever way it leaves the page ───────────────────
//
// A long paste becomes a card above the composer and a `[Paste #N]` marker in her draft. The
// marker was replaced by the pasted text only on a plain send. A message sent into a running turn
// (a steer, or one queued for when the turn ends), a rewind or an edit, a message she brought back
// with ↑, and the queue editor all sent the marker itself, so the agent read "[Paste #1]" and never
// saw what she pasted. Every way out now goes through one place that puts the pasted text in place
// of its marker and sends the blocks with it, so her bubble shows each as its chip, live and read
// back. Driven through the real ChatPage, its composer and the dialog host.

const SESSION = 'chat-8-x'
const ASKED = 'Help me tidy the parser.'
const TYPED = 'Use this version instead: '
// A block copied from an editor: four lines, and the newline after the last one.
const PASTE = 'def parse(line):\n    key, _, value = line.partition("=")\n    # the version she means\n    return key.strip(), value.strip()\n'
// Her message as it leaves the page: the block in place of its marker. The gateway trims a message,
// so a block at its end is kept without the newline after it, and so is the block the row records.
const BLOCK = PASTE.trimEnd()
const SENT = `${TYPED}${BLOCK}`
const DRAFTED = `${TYPED}[Paste #1]`
const PASTES = [{ seq: 1, lines: 4, content: BLOCK }]
const CHIP = 'View paste #1 (4 lines)'

const h = vi.hoisted(() => ({
  detailCalls: [] as { resolve: (d: unknown) => void }[],
  chatSessionDetail: vi.fn(),
  sendChat: vi.fn(),
  editResend: vi.fn(),
  cancelQueued: vi.fn(),
}))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    sendChat: h.sendChat,
    editResend: h.editResend,
    cancelQueued: h.cancelQueued,
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    personalclawConfig: () => Promise.resolve({ voice: {} }),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
  }
  const api = new Proxy(base, {
    get(t, p: string) {
      if (p in t) return t[p]
      return (t[p] = () => Promise.resolve([]))
    },
  })
  return { ...real, api }
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
  act(() => { FakeSocket.last?.onmessage?.({ data: JSON.stringify({ type, data: { session: SESSION, ...data } }) }) })
}

let ChatPage: typeof import('../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../app/appearance')['AppearanceProvider']
let DialogHost: typeof import('../../ui/dialog/DialogHost')['DialogHost']

beforeEach(async () => {
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => setTimeout(() => cb(performance.now()), 16) as unknown as number)
  vi.stubGlobal('cancelAnimationFrame', (id: number) => clearTimeout(id))
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
  localStorage.clear()
  FakeSocket.last = null
  h.detailCalls.length = 0
  h.sendChat.mockReset().mockResolvedValue({ ok: true, steered: true })
  h.editResend.mockReset().mockResolvedValue({ ok: true, rewound: 2 })
  h.cancelQueued.mockReset().mockResolvedValue({ ok: true })
  h.chatSessionDetail.mockReset().mockImplementation(
    () => new Promise((resolve) => { h.detailCalls.push({ resolve }) }),
  )
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
  ;({ DialogHost } = await import('../../ui/dialog/DialogHost'))
})

afterEach(() => { vi.unstubAllGlobals() })

const page = (query: Record<string, string> = {}) =>
  render(
    <AppearanceProvider>
      <ChatPage sub={SESSION} navigate={() => {}} query={query} setQuery={() => {}} />
      <DialogHost />
    </AppearanceProvider>,
  )

async function answerDetail(patch: Record<string, unknown>) {
  await waitFor(() => expect(h.detailCalls.length).toBeGreaterThanOrEqual(1))
  await act(async () => {
    h.detailCalls[0].resolve({
      key: SESSION, title: 'Parser', queue: [], task_mode: 'agent', approval: 'normal',
      memory_mode: 'persistent', ...patch,
    })
  })
  await waitFor(() => expect(FakeSocket.last).not.toBeNull())
}

const editor = () => document.querySelector('.cm-content') as HTMLElement

/** A paste into the composer, as the editor's paste handler reads one (jsdom has no DataTransfer). */
function pasteIntoComposer(text: string) {
  const ev = new Event('paste', { bubbles: true, cancelable: true })
  Object.defineProperty(ev, 'clipboardData', {
    value: { getData: (t: string) => (t === 'text/plain' ? text : ''), items: [], files: [], types: ['text/plain'] },
  })
  act(() => { editor().dispatchEvent(ev) })
}

const ASKED_ROW = { role: 'user', content: ASKED, ts: '2026-10-04T09:30:00.000Z' }
// Her pasted message as the chat row keeps it: the block in her text, and the block itself.
const PASTED_ROW = { role: 'user', content: SENT, ts: '2026-10-04T09:31:00.000Z', meta: { pastes: PASTES } }
const HISTORY = [
  PASTED_ROW,
  { role: 'assistant', content: 'That version splits on the first equals sign.', ts: '2026-10-04T09:31:05.000Z' },
  { role: 'user', content: 'Thanks.', ts: '2026-10-04T09:32:00.000Z' },
  { role: 'assistant', content: 'Glad it helps.', ts: '2026-10-04T09:32:05.000Z' },
]

/** What the chat shows of her message once it is read back from the gateway. */
const readBack = (row: HistMsg) => turnText(hydrateTurns([row])[0])

describe('a message she sends into a running turn', () => {
  it('a steer carries what she pasted, and her bubble shows it as its chip', async () => {
    const user = userEvent.setup()
    page({ seed: TYPED })
    await answerDetail({ messages: [ASKED_ROW], running: true, steerable: true })
    pasteIntoComposer(PASTE)
    expect(await screen.findByRole('button', { name: 'Remove paste #1' })).toBeTruthy()

    await user.click(screen.getByRole('button', { name: 'Steer — send into the running turn' }))
    await waitFor(() => expect(h.sendChat).toHaveBeenCalled())
    const [text, , meta, mode] = h.sendChat.mock.calls[0]
    expect(mode).toBe('steer')
    expect(text, 'the agent is sent what she pasted, not its marker').toBe(SENT)
    expect((meta as { pastes?: unknown }).pastes).toEqual(PASTES)
    // On its way, the line above the composer says it as she wrote it.
    expect(await screen.findByText(`Steering into this answer: ${DRAFTED}`)).toBeTruthy()

    // The turn takes it: her bubble shows the block as its chip, as the chat shows it read back.
    const ts = (meta as { client_ts: string }).client_ts
    pushFrame('chat_segment', {})
    pushFrame('chat_user_message', { content: SENT, ts, steer: true, pastes: PASTES })
    expect(await screen.findByTitle(CHIP)).toBeTruthy()
    expect(screen.queryByText(/partition\("="\)/)).toBeNull()
    expect(readBack({ ...PASTED_ROW, ts, meta: { pastes: PASTES, steered: true } })).toBe(DRAFTED)
  })

  it('a queued send carries what she pasted; the queue shows her words, and her bubble the chip', async () => {
    const user = userEvent.setup()
    h.sendChat.mockResolvedValue({ ok: true, queued: true })
    page({ seed: TYPED })
    await answerDetail({ messages: [ASKED_ROW], running: true, steerable: false })
    pasteIntoComposer(PASTE)
    await user.click(await screen.findByRole('button', { name: 'Queue — sent when this turn ends' }))

    await waitFor(() => expect(h.sendChat).toHaveBeenCalled())
    const [text, , meta, mode] = h.sendChat.mock.calls[0]
    expect([text, mode]).toEqual([SENT, 'followup'])
    expect((meta as { pastes?: unknown }).pastes).toEqual(PASTES)

    // The gateway queues it and says so with its blocks: the strip shows the message as she wrote it.
    pushFrame('queue_push', { content: SENT, ts: '2026-10-04T09:31:00.000Z', queue_id: 'q-1', pastes: PASTES })
    expect(await screen.findByTitle(DRAFTED)).toBeTruthy()
    // It runs: her bubble shows the block as its chip.
    pushFrame('queue_pop', { content: SENT, queue_id: 'q-1' })
    pushFrame('chat_user_message', { content: SENT, ts: '2026-10-04T09:31:09.000Z', pastes: PASTES })
    expect(await screen.findByTitle(CHIP)).toBeTruthy()
    expect(screen.queryByTitle(DRAFTED)).toBeNull()
  })

  it('the queue editor gives her message back with its paste, beside the draft she is writing', async () => {
    const user = userEvent.setup()
    const DRAFT = 'And rename the module.'
    h.sendChat.mockResolvedValue({ ok: true, queued: true })
    page({ seed: DRAFT })
    await answerDetail({ messages: [ASKED_ROW], running: true, steerable: false })
    pushFrame('queue_push', { content: SENT, ts: '2026-10-04T09:31:00.000Z', queue_id: 'q-1', pastes: PASTES })

    await user.click(await screen.findByRole('button', { name: 'Edit queued message' }))
    await waitFor(() => expect(h.cancelQueued).toHaveBeenCalledWith(SESSION, 'q-1'))
    // Nothing she wrote is lost: her draft stays, and the queued message joins it with its paste.
    expect(await screen.findByRole('button', { name: 'Remove paste #1' })).toBeTruthy()
    expect(editor().textContent).toContain(DRAFT)
    expect(editor().textContent).toContain(DRAFTED)

    await user.click(screen.getByRole('button', { name: 'Queue — sent when this turn ends' }))
    await waitFor(() => expect(h.sendChat).toHaveBeenCalled())
    const [text, , meta] = h.sendChat.mock.calls[0]
    expect(text).toBe(`${DRAFT}\n\n${SENT}`)
    expect((meta as { pastes?: unknown }).pastes).toEqual(PASTES)
  })

  it('a queued paste numbered like one in her draft takes the next number, and both are sent', async () => {
    const user = userEvent.setup()
    const DRAFT = 'And this query too: '
    const QUERY = 'SELECT name\nFROM parsers\nWHERE broken\nORDER BY name;'
    h.sendChat.mockResolvedValue({ ok: true, queued: true })
    page({ seed: DRAFT })
    await answerDetail({ messages: [ASKED_ROW], running: true, steerable: false })
    pasteIntoComposer(QUERY)
    pushFrame('queue_push', { content: SENT, ts: '2026-10-04T09:31:00.000Z', queue_id: 'q-1', pastes: PASTES })

    await user.click(await screen.findByRole('button', { name: 'Edit queued message' }))
    expect(await screen.findByRole('button', { name: 'Remove paste #2' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Remove paste #1' })).toBeTruthy()
    expect(editor().textContent).toContain(`${TYPED}[Paste #2]`)

    await user.click(screen.getByRole('button', { name: 'Queue — sent when this turn ends' }))
    await waitFor(() => expect(h.sendChat).toHaveBeenCalled())
    const [text, , meta] = h.sendChat.mock.calls[0]
    expect(text).toBe(`${DRAFT}${QUERY}\n\n${SENT}`)
    expect((meta as { pastes?: unknown }).pastes).toEqual([{ seq: 1, lines: 4, content: QUERY }, { seq: 2, lines: 4, content: BLOCK }])
  })
})

describe('a message she sends again', () => {
  it('Rewind to here sends what she pasted back, not its marker', async () => {
    const user = userEvent.setup()
    page()
    await answerDetail({ messages: HISTORY, running: false })
    expect(await screen.findByTitle(CHIP)).toBeTruthy()

    await user.click(screen.getAllByRole('button', { name: 'Rewind to here' })[0])
    await user.click(await screen.findByRole('button', { name: 'Rewind' }))
    await waitFor(() => expect(h.editResend).toHaveBeenCalled())
    const call = h.editResend.mock.calls[0]
    expect(call[1], 'the agent is sent what she pasted, not its marker').toBe(SENT)
    expect(call[7]).toEqual(PASTES)
    // The message she rewound to still shows the block as its chip.
    expect(screen.getByTitle(CHIP)).toBeTruthy()
  })

  it('Edit & resend sends what she pasted with the words she changed', async () => {
    const user = userEvent.setup()
    page()
    await answerDetail({ messages: HISTORY, running: false })
    await user.click((await screen.findAllByRole('button', { name: 'Edit & resend' }))[0])
    const box = screen.getByRole('textbox', { name: 'Edit your message' }) as HTMLTextAreaElement
    expect(box.value).toBe(DRAFTED)
    await user.clear(box)
    await user.type(box, 'Try this one: [[Paste #1]')
    await user.click(screen.getByRole('button', { name: 'Resend & replace' }))

    await waitFor(() => expect(h.editResend).toHaveBeenCalled())
    const call = h.editResend.mock.calls[0]
    expect(call[1]).toBe(`Try this one: ${BLOCK}`)
    expect(call[7]).toEqual(PASTES)
  })

  it('↑ brings back her message with its paste, and sending it sends what she pasted', async () => {
    const user = userEvent.setup()
    h.sendChat.mockResolvedValue({ ok: true })
    page()
    await answerDetail({ messages: HISTORY.slice(0, 2), running: false })
    await screen.findByTitle(CHIP)

    act(() => { editor().focus() })
    fireEvent.keyDown(editor(), { key: 'ArrowUp', code: 'ArrowUp', keyCode: 38 })
    expect(await screen.findByRole('button', { name: 'Remove paste #1' })).toBeTruthy()
    expect(editor().textContent).toBe(DRAFTED)

    await user.click(screen.getByRole('button', { name: 'Send message' }))
    await waitFor(() => expect(h.sendChat).toHaveBeenCalled())
    const [text, , meta] = h.sendChat.mock.calls[0]
    expect(text).toBe(SENT)
    expect((meta as { pastes?: unknown }).pastes).toEqual(PASTES)
  })

  it('Copy copies her message as she sent it, with what she pasted', async () => {
    const user = userEvent.setup()
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
    page()
    await answerDetail({ messages: HISTORY, running: false })
    await screen.findByTitle(CHIP)
    await user.click(screen.getAllByRole('button', { name: 'Copy' })[0])
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(SENT))
  })
})

describe('a message she sends between turns', () => {
  it('sends what she pasted, as it always did', async () => {
    const user = userEvent.setup()
    h.sendChat.mockResolvedValue({ ok: true })
    page({ seed: TYPED })
    await answerDetail({ messages: [], running: false })
    pasteIntoComposer(PASTE)
    await user.click(await screen.findByRole('button', { name: 'Send message' }))

    await waitFor(() => expect(h.sendChat).toHaveBeenCalled())
    const [text, , meta] = h.sendChat.mock.calls[0]
    expect(text).not.toContain('[Paste #1]')
    expect((text as string).trim()).toBe(SENT)
    const sent = (meta as { pastes: { seq: number; content: string }[] }).pastes
    expect(sent.map((p) => p.seq)).toEqual([1])
    expect(text).toContain(sent[0].content.trim())
    // Her bubble shows the chip.
    expect(await screen.findByTitle(/View paste #1/)).toBeTruthy()
  })

  it('keeps a block at the end of her message as the message holds it, so it is a chip read back', async () => {
    const user = userEvent.setup()
    h.sendChat.mockResolvedValue({ ok: true })
    page({ seed: TYPED })
    await answerDetail({ messages: [], running: false })
    pasteIntoComposer(PASTE)
    await user.click(await screen.findByRole('button', { name: 'Send message' }))

    await waitFor(() => expect(h.sendChat).toHaveBeenCalled())
    const [text, , meta] = h.sendChat.mock.calls[0]
    const pastes = (meta as { pastes: { seq: number; lines: number; content: string }[] }).pastes
    // The gateway keeps the message trimmed; the chat reads it back as the row holds it.
    expect(readBack({ role: 'user', content: (text as string).trim(), meta: { pastes } })).toBe(DRAFTED)
  })
})
