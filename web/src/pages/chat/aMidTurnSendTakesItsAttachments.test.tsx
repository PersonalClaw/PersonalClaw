import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { runningState } from '../../test/runState'
import { receiptFor } from '../../test/chatReceipt'

// ── A message sent while a turn runs takes the composer's attachments with it ────────────────────
//
// Measured before this was written: a message sent into a running turn (a steer, or one queued for
// when the turn ends) carried its words and its pastes only. The files attached in the composer,
// and the knowledge items and artifacts mentioned there, were not sent with it and stayed in the
// composer, so they went with the next message, which may have been about something else.
//
// Now a mid-turn send carries them as a send between turns does. A steer joins a turn whose inputs
// were put together already, so a draft that carries any of them is queued for a turn of its own,
// and its button says Queue. The composer lets them go once the send is taken in. A send made while
// a file uploads is held on this path too. Driven through the real ChatPage and its composer.

const SESSION = 'chat-9-x'
const ASKED = 'Summarise the incident.'
const TYPED = 'also use this'
const STORED = '/uploads/5d1e0a77_timeline.png'

const h = vi.hoisted(() => ({
  detailCalls: [] as { resolve: (d: unknown) => void }[],
  chatSessionDetail: vi.fn(),
  sendChat: vi.fn(),
  uploadFiles: vi.fn(),
  knowledgeItem: vi.fn(),
}))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    sendChat: h.sendChat,
    uploadFiles: h.uploadFiles,
    knowledgeItem: h.knowledgeItem,
    // The chat's model takes images, so the attached one rides as itself.
    chatImageInput: () => Promise.resolve({ accepted: true, reason: '', model: 'vision-model' }),
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

// The size check reads the gateway's limits; these files are small, so they pass.
vi.mock('../../lib/chunkedUpload', async (orig) => ({
  ...(await orig<typeof import('../../lib/chunkedUpload')>()),
  precheck: () => Promise.resolve(null),
}))

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

let ChatPage: typeof import('../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../app/appearance')['AppearanceProvider']

/** The upload the paste started, held open until the test lets it finish. */
let finish: (r: { paths: string[] }) => void

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
  h.sendChat.mockReset().mockImplementation(receiptFor('queued'))
  h.uploadFiles.mockReset().mockImplementation(() => new Promise((resolve) => { finish = resolve }))
  h.knowledgeItem.mockReset().mockImplementation((id: string) => Promise.resolve({ id, title: 'Incident runbook' }))
  h.chatSessionDetail.mockReset().mockImplementation(
    () => new Promise((resolve) => { h.detailCalls.push({ resolve }) }),
  )
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

/** The chat with *TYPED* in its composer, its turn running and taking steers. */
async function running(query: Record<string, string> = {}) {
  const { container } = render(
    <AppearanceProvider>
      <ChatPage sub={SESSION} navigate={() => {}} query={{ seed: TYPED, ...query }} setQuery={() => {}} />
    </AppearanceProvider>,
  )
  await waitFor(() => expect(h.detailCalls.length).toBeGreaterThanOrEqual(1))
  await act(async () => {
    h.detailCalls[0].resolve({
      key: SESSION, title: 'Incident', queue: [], task_mode: 'agent', approval: 'normal',
      memory_mode: 'persistent', messages: [{ role: 'user', content: ASKED, ts: '2026-11-20T09:30:00.000Z' }],
      run_state: runningState({ steerable: true }),
    })
  })
  await waitFor(() => expect(FakeSocket.last).not.toBeNull())
  return container
}

/** A file pasted into the composer, its upload left open until the test finishes it. */
async function attachFile(container: HTMLElement) {
  const editor = await waitFor(() => {
    const el = container.querySelector('.cm-content') as HTMLElement | null
    expect(el, 'the composer mounted its editor').toBeTruthy()
    return el!
  })
  // A screenshot, the one shape the composer's paste path uploads (jsdom has no DataTransfer).
  const file = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], 'timeline.png', { type: 'image/png' })
  const ev = new Event('paste', { bubbles: true, cancelable: true })
  Object.defineProperty(ev, 'clipboardData', {
    value: { getData: () => '', items: [{ kind: 'file', type: file.type, getAsFile: () => file }], files: [file], types: ['Files'] },
  })
  act(() => { editor.dispatchEvent(ev) })
  await waitFor(() => expect(h.uploadFiles).toHaveBeenCalledTimes(1))
}

const queueButton = () => screen.findByRole('button', { name: 'Queue — sent when this turn ends' })

describe('a message she sends while a turn runs', () => {
  it('🔴 takes the file attached in the composer, is queued, and leaves nothing behind', async () => {
    const user = userEvent.setup()
    const page = await running()
    await attachFile(page)
    await act(async () => { finish({ paths: [STORED] }) })
    expect(await screen.findByRole('button', { name: 'Remove file' })).toBeTruthy()

    await user.click(await queueButton())
    await waitFor(() => expect(h.sendChat).toHaveBeenCalled())
    const [text, , meta, mode] = h.sendChat.mock.calls[0]
    expect([text, mode]).toEqual([TYPED, 'followup'])
    expect((meta as { files?: unknown }).files).toEqual([STORED])
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Remove file' }), 'the file left the composer with it').toBeNull())
  })

  it('🔴 takes the knowledge item mentioned in the composer, and is queued', async () => {
    const user = userEvent.setup()
    await running({ attach: 'knowledge:k1' })
    expect(await screen.findByTitle('Incident runbook')).toBeTruthy()

    await user.click(await queueButton())
    await waitFor(() => expect(h.sendChat).toHaveBeenCalled())
    const [, , meta, mode] = h.sendChat.mock.calls[0]
    expect(mode).toBe('followup')
    expect((meta as { knowledge?: unknown }).knowledge).toEqual(['k1'])
    await waitFor(() => expect(screen.queryByTitle('Incident runbook')).toBeNull())
  })

  it('with nothing attached, still steers a turn that takes steers', async () => {
    const user = userEvent.setup()
    h.sendChat.mockImplementation(receiptFor('steered'))
    await running()

    await user.click(await screen.findByRole('button', { name: 'Steer — send into the running turn' }))
    await waitFor(() => expect(h.sendChat).toHaveBeenCalled())
    const [, , meta, mode] = h.sendChat.mock.calls[0]
    expect(mode).toBe('steer')
    expect(meta).toBeUndefined()
  })

  it('🔴 is held while its file uploads', async () => {
    const user = userEvent.setup()
    const page = await running()
    await attachFile(page)

    // The send is refused with the upload's sentence, and nothing goes.
    const button = await screen.findByRole('button', { name: /Queue — sent when this turn ends|Steer — send into the running turn/ })
    await user.click(button)
    expect(h.sendChat).not.toHaveBeenCalled()
    await act(async () => { finish({ paths: [STORED] }) })
    await user.click(await queueButton())
    await waitFor(() => expect(h.sendChat).toHaveBeenCalled())
    expect((h.sendChat.mock.calls[0][2] as { files?: unknown }).files).toEqual([STORED])
  })
})
