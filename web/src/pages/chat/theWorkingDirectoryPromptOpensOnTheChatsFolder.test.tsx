import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, within, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── The Working directory prompt opens on the chat's folder, can clear it, and says what moved ───
//
// The prompt opened empty, so she could not see the folder the chat worked in, and its Set button
// stayed unavailable on an empty path, so the clear the gateway supports was out of reach. It now
// opens on the chat's folder (an untouched Set is no change), an empty path clears it (the chat
// goes back to the workspace a new chat starts in), and the line above the composer says where the
// chat works now, in the gateway's words for the folder: a turn running when the folder moved ends,
// and nothing more of it runs in the folder the chat left.

const SESSION = 'chat-8-x'
const RECIPES = '/home/user/src/recipe-box'
const GARDEN = '/home/user/src/garden-log'
const WORKSPACE = '/home/user/.personalclaw/workspace'

const h = vi.hoisted(() => ({
  detail: {
    key: 'chat-8-x', title: 'Fixing the tests', running: false, queue: [] as unknown[], task_mode: 'agent',
    approval: 'normal', memory_mode: 'persistent', workspace_dir: '/home/user/src/recipe-box',
    messages: [
      { role: 'user', content: 'Which tests fail?', ts: '2026-10-06T09:00:00+00:00' },
      { role: 'assistant', content: 'Two of them, both in the parser.', ts: '2026-10-06T09:00:05+00:00' },
    ] as unknown[],
  },
  chatSessionDetail: vi.fn(),
  setSessionWorkspaceDir: vi.fn(),
}))

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api')
  const ok = () => Promise.resolve([])
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    setSessionWorkspaceDir: h.setSessionWorkspaceDir,
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    sessionTemplates: () => Promise.resolve([]),
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
  act(() => { FakeSocket.last?.onmessage?.({ data: JSON.stringify({ type, data: { session: SESSION, ...data } }) }) })
}

let ChatPage: typeof import('../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../app/appearance')['AppearanceProvider']
let DialogHost: typeof import('../../ui/dialog/DialogHost')['DialogHost']

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
  h.setSessionWorkspaceDir.mockReset()
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
  ;({ DialogHost } = await import('../../ui/dialog/DialogHost'))
})

afterEach(() => { vi.unstubAllGlobals() })

async function openThePrompt(before: () => void = () => {}) {
  const user = userEvent.setup()
  render(
    <AppearanceProvider>
      <ChatPage sub={SESSION} navigate={() => {}} query={{}} setQuery={() => {}} />
      <DialogHost />
    </AppearanceProvider>,
  )
  await screen.findByText(/Two of them, both in the parser/)
  before()
  await user.click(screen.getByRole('button', { name: 'Working directory' }))
  const dialog = await screen.findByRole('dialog', { name: 'Working directory' })
  return { user, dialog, path: within(dialog).getByRole('textbox') as HTMLInputElement }
}

describe('the Working directory prompt', () => {
  it('opens on the folder the chat works in, so Set with nothing changed names it again', async () => {
    h.setSessionWorkspaceDir.mockResolvedValue({ ok: true, workspace_dir: RECIPES, auto_chain: 'Code & tools', moved: false })
    const { user, dialog, path } = await openThePrompt()
    expect(path.value).toBe(RECIPES)

    await user.click(within(dialog).getByRole('button', { name: 'Set' }))

    expect(h.setSessionWorkspaceDir).toHaveBeenCalledWith(SESSION, RECIPES)
  })

  it('clears it: an empty path is accepted, and the chat says it works in the workspace', async () => {
    h.setSessionWorkspaceDir.mockResolvedValue({ ok: true, workspace_dir: WORKSPACE, auto_chain: 'Chat', moved: false })
    const { user, dialog, path } = await openThePrompt()

    await user.clear(path)
    const set = within(dialog).getByRole('button', { name: 'Set' })
    expect(set.getAttribute('aria-disabled')).not.toBe('true')
    await user.click(set)

    expect(h.setSessionWorkspaceDir).toHaveBeenCalledWith(SESSION, '')
    expect(await screen.findByText(`Working directory cleared: this chat works in the workspace (${WORKSPACE}).`)).toBeTruthy()
  })

  it('names the folder the gateway set, which is the one the chat works in', async () => {
    h.setSessionWorkspaceDir.mockResolvedValue({ ok: true, workspace_dir: GARDEN, auto_chain: 'Code & tools', moved: false })
    const { user, dialog, path } = await openThePrompt()

    await user.clear(path)
    await user.type(path, '~/src/garden-log')
    await user.click(within(dialog).getByRole('button', { name: 'Set' }))

    expect(await screen.findByText(`Working directory set to ${GARDEN}.`)).toBeTruthy()
  })

  it('opens on the folder the chat moved to in another tab, or when a moved turn ended', async () => {
    const { user, dialog, path } = await openThePrompt(() => pushFrame('session_binding', {
      agent: '', model: '', acp_provider: '', acp_provider_agent: '', reasoning_effort: '',
      workspace_dir: GARDEN, auto_chain: 'Code & tools',
    }))
    expect(path.value).toBe(GARDEN)

    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(h.setSessionWorkspaceDir).not.toHaveBeenCalled()
  })

  it('says a turn running when the folder moved ends, and does nothing more in the old folder', async () => {
    h.setSessionWorkspaceDir.mockResolvedValue({ ok: true, workspace_dir: GARDEN, auto_chain: 'Code & tools', moved: true })
    const { user, dialog, path } = await openThePrompt()

    await user.clear(path)
    await user.type(path, GARDEN)
    await user.click(within(dialog).getByRole('button', { name: 'Set' }))

    expect(await screen.findByText(
      `Working directory set to ${GARDEN}. The turn that was running ends now; nothing more of it runs in the old folder.`,
    )).toBeTruthy()
  })
})
