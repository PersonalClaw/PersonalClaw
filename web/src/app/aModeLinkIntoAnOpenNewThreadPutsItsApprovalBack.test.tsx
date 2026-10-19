import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

// ── A mode link into a new thread that is already open leaves no auto-approve behind ──────────
//
// `personalclaw://threads/new?mode=agent` reaches an open window as `#/chat/new?mode=agent`. The
// shell does not open the thread afresh for a mode alone, so the thread that is showing follows
// the address to the link's mode. The person may have set that thread up by hand, say Ask with
// Trust; the link must not leave her Trust on a mode she did not pick. So the thread is set up as
// a new thread the link opens: in the link's mode, with approval back to Normal, and her draft
// kept. This drives the REAL shell from the hash and reads what Send writes off the requests that
// go out.

vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {}, refreshKinds: () => [] }))
vi.mock('./identity', async (orig) => {
  const real = await orig<typeof import('./identity')>()
  return {
    ...real,
    useIdentity: () => ({ status: 'ready', name: 'Ada', username: 'ada', onboarded: true, setName: async () => {} }),
  }
})

const SESSION = 'chat-7-mode'
const ENVELOPES: Record<string, unknown> = {
  dashboardConfig: { user_name: 'Ada' },
  agents: { agents: [] },
  skillProposals: { proposals: [], lastReview: null },
  createChatSession: { key: SESSION },
  sendChat: { ok: true, session: SESSION },
}
// Each call is made for real, so it goes out as the request it is (to the stubbed `fetch` below);
// the page is handed a benign answer.
vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  const realApi = real.api as unknown as Record<string, unknown>
  const stub = new Proxy({}, {
    get: (_t, prop: string) => (...args: unknown[]) => {
      const fn = realApi[prop]
      if (typeof fn === 'function') {
        try { void Promise.resolve((fn as (...a: unknown[]) => unknown)(...args)).catch(() => {}) } catch { /* the stub's answer stands */ }
      }
      return Promise.resolve(prop in ENVELOPES ? ENVELOPES[prop] : [])
    },
  })
  return { ...real, api: stub }
})

class FakeSocket {
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 1
  constructor(public url: string) {}
  send(): void {}
  close(): void { this.readyState = 3 }
}

const { App } = await import('./App')
const { ThemeProvider } = await import('./theme')
const { AppearanceProvider } = await import('./appearance')
const { PersonalityProvider } = await import('./personality')
await import('../pages/ChatPage')

/** Every request that went out, as `METHOD path`. */
let requests: string[] = []

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  requests = []
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
    requests.push(`${(init?.method ?? 'GET').toUpperCase()} ${String(input).split('?')[0]}`)
    return Promise.resolve(new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } }))
  })
  Element.prototype.scrollIntoView ??= () => {}
  if (typeof globalThis.IntersectionObserver === 'undefined') {
    vi.stubGlobal('IntersectionObserver', class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
      takeRecords(): [] { return [] }
    })
  }
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const posted = () => requests.filter((r) => !r.startsWith('GET '))
const settle = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 100)) })
/** What the desktop does with a link once the app is open: it assigns the window's hash. */
const deliver = (hash: string) => act(async () => {
  location.hash = hash
  await new Promise((resolve) => setTimeout(resolve, 50))
})

function shell() {
  return render(<ThemeProvider><AppearanceProvider><PersonalityProvider><App /></PersonalityProvider></AppearanceProvider></ThemeProvider>)
}

async function composerText(container: HTMLElement): Promise<string> {
  const editor = await waitFor(() => {
    const el = container.querySelector('.cm-content') as HTMLElement | null
    expect(el, 'the new thread mounted its composer').toBeTruthy()
    return el!
  })
  return editor.textContent ?? ''
}
const pill = () => screen.getByRole('button', { name: /^Task mode:/ })
const permission = () => screen.getByRole('button', { name: /^Permission mode:/ })
async function pick(control: () => HTMLElement, name: string) {
  act(() => { fireEvent.mouseEnter(control().parentElement!) })
  fireEvent.click(await screen.findByRole('menuitemradio', { name }))
  await waitFor(() => expect(control()).toHaveAccessibleName(new RegExp(`: ${name}$`)))
  await settle()
}

describe('a mode link delivered to a new thread that is already open', () => {
  it('moves it to the link\'s mode with approval back to Normal, keeps the draft, and Send writes no auto-approve', async () => {
    location.hash = '#/chat/new?prompt=list%20the%20files'
    const { container } = shell()
    await waitFor(() => expect(pill()).toHaveAccessibleName('Task mode: Agent'))
    await pick(pill, 'Ask')
    await pick(permission, 'Trust')
    expect(posted(), 'picks on a new thread are only shown').toEqual([])

    await deliver('#/chat/new?mode=agent')
    await settle()
    expect(pill()).toHaveAccessibleName('Task mode: Agent')
    expect(permission()).toHaveAccessibleName('Permission mode: Normal')
    expect(location.hash).toBe('#/chat/new')
    expect(await composerText(container), 'the thread was not opened afresh').toContain('list the files')
    expect(posted(), 'the link wrote nothing').toEqual([])

    fireEvent.click(screen.getByRole('button', { name: /^Send/ }))
    await waitFor(() => expect(posted()).toContain('POST /api/chat'))
    await settle()
    expect(posted(), 'the draft is sent as Agent with Normal approval').toEqual(['POST /api/chat/sessions', 'POST /api/chat'])
  })
})
