import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'

// ── A chat can continue on a channel, from the chat's own menu ──────────────────────────────────
//
// `POST /api/chat/sessions/{session}/handoff` had an API route and no caller: nothing in the UI
// could hand a chat to Telegram or Discord. The chat's header now carries "Continue on <channel>"
// for each connected chat channel, and the handoff names the channel it chose.

const h = vi.hoisted(() => ({
  detail: {
    key: 'chat-7-x', title: 'Launch plan', running: false, queue: [] as unknown[],
    task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
    messages: [
      { role: 'user', content: 'Plan the launch week.', ts: '2026-09-26T09:00:00+00:00' },
      { role: 'assistant', content: 'Here is the plan.', ts: '2026-09-26T09:00:20+00:00' },
    ] as unknown[],
  } as Record<string, unknown>,
  chatSessionDetail: vi.fn(),
  channels: vi.fn(),
  handoffSession: vi.fn(),
  confirm: vi.fn(),
  notify: vi.fn(),
}))

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api')
  const ok = () => Promise.resolve([])
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    channels: h.channels,
    handoffSession: h.handoffSession,
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
  }
  const api = new Proxy(base, {
    get(t, p: string) {
      if (p in t) return t[p]
      return (t[p] = (...__a: unknown[]) => ok())
    },
  })
  return { ...actual, api }
})

vi.mock('../../ui/dialog', async () => {
  const actual = await vi.importActual<typeof import('../../ui/dialog')>('../../ui/dialog')
  return { ...actual, confirm: h.confirm }
})

vi.mock('../../app/appSdk', async () => {
  const actual = await vi.importActual<typeof import('../../app/appSdk')>('../../app/appSdk')
  return { ...actual, notify: h.notify }
})

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

let ChatPage: typeof import('../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../app/appearance')['AppearanceProvider']

const telegram = {
  name: 'telegram', display_name: 'Telegram', connected: true, app: 'telegram-channel',
  capabilities: { owner_pairing: true }, health: { state: 'ready' }, owner: { id: '4242', source: 'channel' },
}
const discord = {
  name: 'discord', display_name: 'Discord', connected: true, app: 'discord-channel',
  capabilities: { owner_pairing: true }, health: { state: 'ready' }, owner: { id: '', source: '' },
}
const webui = { name: 'webui', display_name: 'Web UI', connected: true, app: '', health: { state: 'ready' } }

beforeEach(async () => {
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  Element.prototype.scrollIntoView = vi.fn() as unknown as Element['scrollIntoView']
  if (typeof globalThis.IntersectionObserver === 'undefined') {
    vi.stubGlobal('IntersectionObserver', class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
      takeRecords(): [] { return [] }
    })
  }
  h.chatSessionDetail.mockReset().mockResolvedValue({ ...h.detail })
  h.channels.mockReset().mockResolvedValue([webui, telegram, discord])
  h.handoffSession.mockReset().mockResolvedValue({ ok: true, thread_ts: '17', provider: 'telegram' })
  h.confirm.mockReset().mockResolvedValue(true)
  h.notify.mockReset()
  const { invalidateKeys } = await import('../../lib/data')
  invalidateKeys('settings:channels', true)
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

async function openChat(): Promise<HTMLElement> {
  render(
    <AppearanceProvider>
      <ChatPage sub="chat-7-x" navigate={vi.fn()} query={{}} setQuery={() => {}} />
    </AppearanceProvider>,
  )
  const banner = await screen.findByRole('banner')
  await waitFor(() => expect(within(banner).getByText('Launch plan')).toBeTruthy())
  return banner
}

describe("the chat's menu", () => {
  it('offers each chat channel, and not the dashboard itself', async () => {
    const banner = await openChat()
    expect(await within(banner).findByRole('button', { name: 'Continue on Telegram' })).toBeTruthy()
    expect(within(banner).getByRole('button', { name: 'Continue on Discord' })).toBeTruthy()
    expect(within(banner).queryByRole('button', { name: 'Continue on Web UI' })).toBeNull()
  })

  it('hands this chat to the channel chosen', async () => {
    const banner = await openChat()
    fireEvent.click(await within(banner).findByRole('button', { name: 'Continue on Telegram' }))
    await waitFor(() => expect(h.handoffSession).toHaveBeenCalledWith('chat-7-x', 'telegram'))
    expect(h.confirm.mock.calls[0][0].title).toBe('Continue on Telegram?')
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith('This chat is in your Telegram messages now.', 'success'))
  })

  it('a failed channel read offers no channel, and the header still works', async () => {
    h.channels.mockReset().mockRejectedValue(new Error('HTTP 500'))
    const banner = await openChat()
    expect(within(banner).queryByRole('button', { name: /^Continue on / })).toBeNull()
    expect(within(banner).getByText('Launch plan')).toBeTruthy()
  })

  it('a channel with no owner says where to pair one instead of sending into nowhere', async () => {
    const banner = await openChat()
    fireEvent.click(await within(banner).findByRole('button', { name: 'Continue on Discord' }))
    await waitFor(() => expect(h.notify).toHaveBeenCalled())
    expect(String(h.notify.mock.calls[0][0])).toMatch(/Discord doesn't know who you are yet. Pair its owner in Settings → Providers → Discord → Configure/)
    expect(h.handoffSession).not.toHaveBeenCalled()
  })
})
