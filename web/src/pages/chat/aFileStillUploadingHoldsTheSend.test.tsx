import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── A message is not sent while its file is still uploading ──────────────────────────────────
//
// Measured on a new chat: an image pasted into the composer showed "Uploading chart-p99.png 0%"
// for as long as a busy gateway took to take it, while Send stayed live. Sent then, the message
// went with no file — the file had no path yet — and the send, which creates the chat, remounted
// the composer, so the upload that finished afterwards was attached to nothing: the image was
// on disk and in no message, and nothing on screen said so.
//
// While a file uploads, Send is off and says why; Enter (or any other way into a send) is
// refused with the same sentence where the user is looking, and the draft stays. Once the file
// is in, the message goes with it. A cancelled upload lets the message go without it.

const h = vi.hoisted(() => ({
  sendChat: vi.fn(),
  uploadFiles: vi.fn(),
}))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const base: Record<string, unknown> = {
    sendChat: h.sendChat,
    uploadFiles: h.uploadFiles,
    dashboardConfig: () => Promise.resolve({ send_on_enter: true }),
    // The chat's model takes images, so the attached one rides as itself.
    chatImageInput: () => Promise.resolve({ accepted: true, reason: '', model: 'vision-model' }),
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    createChatSession: () => Promise.resolve({ key: 'chat-4-x' }),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
  }
  // Anything else the page reads resolves to a benign, shape-agnostic value.
  const api = new Proxy(base, {
    get(t, p: string) {
      if (p in t) return t[p]
      return (t[p] = () => Promise.resolve([]))
    },
  })
  return { ...real, api }
})

// The size check reads the gateway's limits; this file is small, so it passes.
vi.mock('../../lib/chunkedUpload', async (orig) => ({
  ...(await orig<typeof import('../../lib/chunkedUpload')>()),
  precheck: () => Promise.resolve(null),
}))

class FakeSocket {
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 1
  constructor(public url: string) { setTimeout(() => this.onopen?.(), 0) }
  send(): void {}
  close(): void { this.readyState = 3 }
}

const HOLD = 'Wait for chart-p99.png to finish uploading, or cancel it.'
const STORED = '/uploads/3ec0c0e1_chart-p99.png'

let ChatPage: typeof import('../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../app/appearance')['AppearanceProvider']

/** The upload the paste started, held open until the test lets it finish. */
let finish: (r: { paths: string[] }) => void
let fail: (e: Error) => void

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
  h.sendChat.mockReset().mockResolvedValue({ ok: true, session: 'chat-4-x' })
  h.uploadFiles.mockReset().mockImplementation(() => new Promise((resolve, reject) => { finish = resolve; fail = reject }))
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

/** A new chat with ``draft`` in its composer and a screenshot pasted into it, still uploading. */
async function pasteWhileTyping(draft: string) {
  const { container } = render(
    <AppearanceProvider>
      <ChatPage sub="" navigate={() => {}} query={{ seed: draft }} setQuery={() => {}} />
    </AppearanceProvider>,
  )
  const editor = await waitFor(() => {
    const el = container.querySelector('.cm-content') as HTMLElement | null
    expect(el, 'the composer mounted its editor').toBeTruthy()
    return el!
  })
  // A clipboard paste that carries only an image file (a screenshot), in the one shape the
  // composer's paste path reads — jsdom has no DataTransfer.
  const shot = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], 'chart-p99.png', { type: 'image/png' })
  const ev = new Event('paste', { bubbles: true, cancelable: true })
  Object.defineProperty(ev, 'clipboardData', {
    value: { getData: () => '', items: [{ kind: 'file', type: shot.type, getAsFile: () => shot }], files: [shot], types: ['Files'] },
  })
  act(() => { editor.dispatchEvent(ev) })
  await waitFor(() => expect(h.uploadFiles).toHaveBeenCalledTimes(1))
  return editor
}

const sendButton = () => screen.getByRole('button', { name: 'Send message' })

describe('a message whose file is still uploading', () => {
  it('is held, says why, and goes with the file once it is in', async () => {
    const user = userEvent.setup()
    const editor = await pasteWhileTyping('What does the p99 chart show?')

    await waitFor(() => expect(sendButton().getAttribute('aria-disabled'), 'Send is off while it uploads').toBe('true'))
    expect(sendButton().getAttribute('title')).toBe(`Send message — ${HOLD}`)

    // Enter is the other way to send: refused with the same sentence, and the draft stays.
    editor.focus()
    await user.keyboard('{Enter}')
    expect(h.sendChat, 'nothing was sent without the file').not.toHaveBeenCalled()
    expect((await screen.findByText(HOLD)).closest('[role="alert"]'), 'said where the user is looking').not.toBeNull()
    expect(editor.textContent).toContain('What does the p99 chart show?')

    await act(async () => { finish({ paths: [STORED] }) })

    await waitFor(() => expect(sendButton().getAttribute('aria-disabled')).toBeNull())
    expect(screen.queryByText(HOLD), 'the hold is over, so its sentence goes').toBeNull()
    await user.click(sendButton())

    await waitFor(() => expect(h.sendChat).toHaveBeenCalledTimes(1))
    const [text, , meta] = h.sendChat.mock.calls[0]
    expect(text).toBe('What does the p99 chart show?')
    expect((meta as { files?: string[] }).files, 'the message carries the file').toEqual([STORED])
  })

  it('once its file is being finished, offers no cancel the gateway would not honour', async () => {
    await pasteWhileTyping('What does the p99 chart show?')
    await waitFor(() => expect(sendButton().getAttribute('aria-disabled')).toBe('true'))
    expect(screen.getByRole('button', { name: 'Cancel upload' })).toBeInTheDocument()
    const [, onProgress] = h.uploadFiles.mock.calls[0] as unknown as [File[], (i: number, p: object) => void]

    // Every byte is in: the gateway completes the upload whatever the page does now.
    act(() => { onProgress(0, { loaded: 4, total: 4, pct: 100, finishing: true }) })

    expect(screen.queryByRole('button', { name: 'Cancel upload' })).toBeNull()
    expect(sendButton().getAttribute('title')).toBe('Send message — Wait for chart-p99.png to finish uploading.')
  })

  it('goes without it once the upload is cancelled or fails', async () => {
    const user = userEvent.setup()
    await pasteWhileTyping('Send it anyway.')
    await waitFor(() => expect(sendButton().getAttribute('aria-disabled')).toBe('true'))

    await act(async () => { fail(Object.assign(new Error('The operation was aborted.'), { name: 'AbortError' })) })

    await waitFor(() => expect(sendButton().getAttribute('aria-disabled')).toBeNull())
    await user.click(sendButton())
    await waitFor(() => expect(h.sendChat).toHaveBeenCalledTimes(1))
    expect((h.sendChat.mock.calls[0][2] as { files?: string[] }).files).toBeUndefined()
  })
})
