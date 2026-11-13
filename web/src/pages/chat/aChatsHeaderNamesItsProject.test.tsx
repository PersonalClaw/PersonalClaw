import { useState } from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, fireEvent, waitFor } from '@testing-library/react'
import { runState } from '../../test/runState'

// ── A chat's header names the project it is in, however the chat was opened ─────────────────────
//
// The header's context line draws the chat's project as its chip, a link to the project's page.
// It used to take the project only from a new chat's draft (`?project=`, from a project's Chat
// button), and the first send opens the chat again under its own key, so the chip never drew:
// not on a chat opened by its link, and not on one started from its project. The header now takes
// the chat's own project when no draft names one: from the chat's read, and, on the first send's
// remount, from the copy its create wrote, before any read lands.

const h = vi.hoisted(() => ({
  details: {} as Record<string, () => Promise<unknown>>,
  newKey: '',
  made: [] as Record<string, unknown>[],
}))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const answers: Record<string, (...args: unknown[]) => unknown> = {
    chatSessionDetail: (key: unknown) => (h.details[key as string] ?? (() => new Promise(() => {})))(),
    createChatSession: (opts: unknown) => { h.made.push(opts as Record<string, unknown>); return Promise.resolve({ key: h.newKey }) },
    sendChat: () => Promise.resolve({ ok: true, session: h.newKey }),
    project: (id: unknown) => Promise.resolve(id === 'p-garden' ? { id: 'p-garden', name: 'Garden' } : null),
    projects: () => Promise.resolve([{ id: 'p-garden', name: 'Garden' }]),
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    sessionTemplates: () => Promise.resolve([]),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
  }
  const api = new Proxy({} as Record<string, unknown>, {
    get(_t, p) {
      if (typeof p !== 'string') return undefined
      return (...args: unknown[]) => (p in answers ? answers[p](...args) : Promise.resolve([]))
    },
  })
  return { ...real, api }
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

/** A settled chat in `project_id` (`''` for none), its read answered at once. */
function chatIn(key: string, project_id: string) {
  h.details[key] = () => Promise.resolve({
    key, title: 'Order the seeds', run_state: runState({ session: key }), queue: [],
    task_mode: 'agent', approval: 'normal', memory_mode: 'persistent', workspace_dir: '', project_id,
    messages: [
      { role: 'user', content: 'Which vendors carry all six varieties?', ts: '2026-11-11T09:00:00+00:00' },
      { role: 'assistant', content: 'Two do, and one ships this week.', ts: '2026-11-11T09:00:05+00:00' },
    ],
  })
  return key
}

/** ChatPage under an address the hash router would keep: a `chat/<key>` move opens that chat,
 *  and its query is the one it was opened with (the router drops it on that move; the page's
 *  own branch for a chat's key reads none). */
function page(start: string, query: Record<string, string> = {}) {
  const address = { sub: start }
  function Routed() {
    const [sub, setSub] = useState(start)
    address.sub = sub
    const navigate = (to: string) => { const m = /^chat\/(.+)$/.exec(to); if (m) setSub(m[1]) }
    return <ChatPage sub={sub} navigate={navigate} query={query} setQuery={() => {}} />
  }
  render(<AppearanceProvider><Routed /></AppearanceProvider>)
  return address
}

const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 60)) })
const chip = () => screen.queryByRole('link', { name: 'Open project Garden' })

beforeEach(async () => {
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }))))
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
  h.details = {}
  h.made = []
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

describe("a chat's header names its project", () => {
  it('on a chat in the project opened by its link', async () => {
    page(chatIn('chat-41-garden', 'p-garden'))
    await screen.findByText('Two do, and one ships this week.')
    const link = await screen.findByRole('link', { name: 'Open project Garden' })
    expect(link.getAttribute('href')).toBe('#/projects/p-garden')
  })

  it('on a chat started from the project, through its first send, before its first read lands', async () => {
    h.newKey = 'chat-42-garden'
    const address = page('new', { project: 'p-garden', seed: 'Which vendors carry all six varieties?' })
    await screen.findByRole('button', { name: /^Send/ })
    await settle()
    fireEvent.click(screen.getByRole('button', { name: /^Send/ }))
    // The send makes the chat in the project and moves the address to its key, which remounts it
    // with no draft; its first read is held for the whole test, so only what the chat was made in
    // can name its project.
    await waitFor(() => expect(address.sub).toBe(h.newKey))
    expect(h.made[0]?.project_id).toBe('p-garden')
    const link = await screen.findByRole('link', { name: 'Open project Garden' })
    expect(link.getAttribute('href')).toBe('#/projects/p-garden')
  })

  it('draws none on a chat in no project', async () => {
    page(chatIn('chat-43-loose', ''))
    await screen.findByText('Two do, and one ships this week.')
    await settle()
    expect(chip()).toBeNull()
  })
})
