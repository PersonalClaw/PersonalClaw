import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── Regenerate and Rewind ask first, in Retry's own dialog ─────────────────────────────────────
//
// Regenerate on an answer and Rewind to an earlier message run a turn again, and the attempt they
// replace goes, the calls it finished included, so the turn asked again may make them again. The
// gateway answers such a request with Retry's question (`retry_repeats_steps`, its steps and the
// `confirm` her yes sends back) instead of running it. Driven through the real ChatPage and the
// real dialog host: the question shows in the dialog Retry uses, and only "Run it again" sends the
// request again, carrying the `confirm`. Rewind and a resend she did not change also tell the
// gateway they send her message again (`again`), since what the page shows of a message is not
// always the row's own words; an edited message does not.

const h = vi.hoisted(() => ({
  chatSessionDetail: vi.fn(),
  editResend: vi.fn(),
  regenerate: vi.fn(),
  chatSessions: vi.fn(),
  notify: vi.fn(),
}))

vi.mock('../../app/appSdk', async (orig) => ({ ...(await orig<typeof import('../../app/appSdk')>()), notify: h.notify }))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    editResend: h.editResend,
    regenerate: h.regenerate,
    chatSessions: h.chatSessions,
    useCaseSettings: () => Promise.resolve({}),
    personalclawConfig: () => Promise.resolve({ voice: {} }),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    chatFolders: () => Promise.resolve([]),
    chatTags: () => Promise.resolve([]),
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
  constructor(public url: string) {
    FakeSocket.last = this
    setTimeout(() => this.onopen?.(), 0)
  }
  send(): void {}
  close(): void { this.readyState = 3 }
}

let ChatPage: typeof import('../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../app/appearance')['AppearanceProvider']
let ApiError: typeof import('../../lib/api')['ApiError']
let DialogHost: typeof import('../../ui/dialog/DialogHost')['DialogHost']

const SESSION = 'chat-41-x'
const FIRST = 'Save the plan to notes/plan.md.'
const TRANSCRIPT = [
  { role: 'user', content: FIRST, ts: '2026-09-26T10:00:00Z' },
  { role: 'assistant', content: 'Saved the plan.', ts: '2026-09-26T10:00:05Z' },
  { role: 'user', content: 'Thanks. What is next?', ts: '2026-09-26T10:01:00Z' },
  { role: 'assistant', content: 'Next is the launch checklist.', ts: '2026-09-26T10:01:05Z' },
]
const detail = () => ({
  key: SESSION, title: SESSION, messages: TRANSCRIPT, running: false, queue: [], task_mode: 'agent',
  approval: 'normal', memory_mode: 'persistent',
})

const CONFIRM = 'a1b2c3d4e5f60718293a4b5c6d7e8f90'
const SAID = 'This turn finished 1 step that may have changed something. Running the turn again replaces this attempt and may repeat it.'
const QUESTION = {
  title: 'Run this turn again?',
  said: SAID,
  steps: [{ tool: 'write_file', target: 'notes/plan.md' }],
  more: 0,
  confirm: CONFIRM,
}
const asked = () => new ApiError(
  'This turn finished 1 step that may have changed something: write_file (notes/plan.md). Running the turn again replaces this attempt and may repeat it. To run it anyway, send this request again with its confirmation.',
  409, 'retry_repeats_steps', QUESTION,
)

beforeEach(async () => {
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  // Frames run on a timer, so a dialog's exit animation ends and the dialog leaves the page.
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
  h.chatSessionDetail.mockReset().mockImplementation(() => Promise.resolve(detail()))
  h.editResend.mockReset()
  h.regenerate.mockReset()
  h.chatSessions.mockReset().mockResolvedValue([])
  h.notify.mockReset()
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
  ;({ ApiError } = await import('../../lib/api'))
  ;({ DialogHost } = await import('../../ui/dialog/DialogHost'))
})

afterEach(() => { vi.unstubAllGlobals() })

async function openChat() {
  render(
    <AppearanceProvider>
      <ChatPage sub={SESSION} navigate={() => {}} query={{}} setQuery={() => {}} />
      <DialogHost />
    </AppearanceProvider>,
  )
  expect(await screen.findByText('Next is the launch checklist.')).toBeTruthy()
  await waitFor(() => expect(FakeSocket.last).not.toBeNull())
}

/** The gateway's question, as the dialog shows it: Retry's title, its sentence and each step. */
async function theQuestion() {
  const dialog = await screen.findByRole('dialog', { name: 'Run this turn again?' })
  expect(within(dialog).getByText(SAID)).toBeTruthy()
  const steps = within(within(dialog).getByRole('list', { name: 'Steps that may repeat' })).getAllByRole('listitem')
  expect(steps.map((li) => li.textContent)).toEqual(['Writenotes/plan.mdwrite_file'])
  return dialog
}

describe('Regenerate on an answer the gateway asks about', () => {
  it('shows the question, and runs again only on her yes, carrying its confirm', async () => {
    const user = userEvent.setup()
    await openChat()
    h.regenerate.mockRejectedValueOnce(asked()).mockResolvedValueOnce({ ok: true })

    await user.click(screen.getByRole('button', { name: 'Regenerate' }))
    const dialog = await theQuestion()
    expect(h.regenerate).toHaveBeenCalledTimes(1)
    await user.click(within(dialog).getByRole('button', { name: 'Run it again' }))

    await waitFor(() => expect(h.regenerate).toHaveBeenCalledTimes(2))
    expect(h.regenerate.mock.calls[0]).toEqual([SESSION])
    expect(h.regenerate.mock.calls[1]).toEqual([SESSION, CONFIRM])
    expect(h.notify).not.toHaveBeenCalled()
  })

  it('a No sends nothing more and leaves the answer where it was', async () => {
    const user = userEvent.setup()
    await openChat()
    h.regenerate.mockRejectedValueOnce(asked())

    await user.click(screen.getByRole('button', { name: 'Regenerate' }))
    await user.click(within(await theQuestion()).getByRole('button', { name: 'Cancel' }))

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Run this turn again?' })).toBeNull())
    expect(h.regenerate).toHaveBeenCalledTimes(1)
    expect(screen.getByText('Next is the launch checklist.')).toBeTruthy()
    expect(h.notify).not.toHaveBeenCalled()
  })
})

describe('Rewind to an earlier message the gateway asks about', () => {
  it('shows the question after the Rewind, and runs again only on her yes, carrying its confirm', async () => {
    const user = userEvent.setup()
    await openChat()
    h.editResend.mockRejectedValueOnce(asked()).mockResolvedValueOnce({ ok: true, rewound: 2 })

    await user.click(screen.getAllByRole('button', { name: 'Rewind to here' })[0])
    await user.click(await screen.findByRole('button', { name: 'Rewind' }))
    const dialog = await theQuestion()
    expect(h.editResend).toHaveBeenCalledTimes(1)
    await user.click(within(dialog).getByRole('button', { name: 'Run it again' }))

    await waitFor(() => expect(h.editResend).toHaveBeenCalledTimes(2))
    const [first, second] = h.editResend.mock.calls
    // Her message as it was, as a rewind, said to be sent again; then the same with her yes.
    expect([first[1], first[5], first[6]]).toEqual([FIRST, true, { again: true, confirm: undefined }])
    expect([second[1], second[5], second[6]]).toEqual([FIRST, true, { again: true, confirm: CONFIRM }])
    expect(h.notify).not.toHaveBeenCalled()
  })

  it('a No sends nothing more and leaves every turn where it was', async () => {
    const user = userEvent.setup()
    await openChat()
    h.editResend.mockRejectedValueOnce(asked())

    await user.click(screen.getAllByRole('button', { name: 'Rewind to here' })[0])
    await user.click(await screen.findByRole('button', { name: 'Rewind' }))
    await user.click(within(await theQuestion()).getByRole('button', { name: 'Cancel' }))

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Run this turn again?' })).toBeNull())
    expect(h.editResend).toHaveBeenCalledTimes(1)
    expect(screen.getByText('Saved the plan.')).toBeTruthy()
    expect(screen.getByText('Next is the launch checklist.')).toBeTruthy()
    expect(h.notify).not.toHaveBeenCalled()
  })
})

describe('Edit & resend', () => {
  it('says it sends her message again when she did not change it, and not when she did', async () => {
    const user = userEvent.setup()
    await openChat()
    h.editResend.mockResolvedValue({ ok: true, rewound: 2 })

    await user.click(screen.getAllByRole('button', { name: 'Edit & resend' })[0])
    await user.click(screen.getByRole('button', { name: 'Resend & replace' }))
    await waitFor(() => expect(h.editResend).toHaveBeenCalledTimes(1))
    expect(h.editResend.mock.calls[0][6]).toEqual({ again: true, confirm: undefined })

    await user.click(screen.getAllByRole('button', { name: 'Edit & resend' })[0])
    const box = screen.getByRole('textbox', { name: 'Edit your message' })
    await user.clear(box)
    await user.type(box, 'Save the plan to notes/launch.md.')
    await user.click(screen.getByRole('button', { name: 'Resend & replace' }))
    await waitFor(() => expect(h.editResend).toHaveBeenCalledTimes(2))
    expect(h.editResend.mock.calls[1][6]).toEqual({ again: false, confirm: undefined })
  })
})
