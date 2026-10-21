import { useState } from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, within, fireEvent, waitFor } from '@testing-library/react'
import { runState } from '../../test/runState'
import { PRIVATE_THREAD_TINT } from './memoryModeCopy'

// ── A private chat wears a faint tint, so it is never mistaken for one that is kept (#3808) ───
//
// A temporary chat keeps nothing after it ends and an incognito one keeps nothing at all
// (`memoryModeCopy`). The Execution Boundary control names the mode on itself, and the notice above
// the composer says it; the thread's own surface also takes a faint tint, so a glance at the page,
// not only at the control, tells a private chat from a kept one. A chat that is kept has no tint.
// The tint follows the chat's memory mode from the moment it is picked for a new chat to the chat's
// end: across the first send, which remounts the chat under its new key before its first read
// lands, and on a revisit painted from the chat's recent copy, whether or not its read ever lands.

const h = vi.hoisted(() => ({
  details: {} as Record<string, () => Promise<unknown>>,
  /** The key the next chat made gets: each test's own, so no copy of another test's chat is read. */
  newKey: '',
}))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const answers: Record<string, (...args: unknown[]) => unknown> = {
    chatSessionDetail: (key: unknown) => (h.details[key as string] ?? (() => new Promise(() => {})))(),
    createChatSession: () => Promise.resolve({ key: h.newKey }),
    sendChat: () => Promise.resolve({ ok: true, session: h.newKey }),
    chatBoundary: () => Promise.resolve({
      sandbox: { confined: true, label: 'Sandboxed', said: 'The commands it runs are sandboxed on this computer.' },
      reach: { tier: 'all', label: 'Network on', said: 'Its web tools reach public hosts.' },
    }),
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

/** A settled chat kept as `memory_mode`, its read answered at once. */
function chatKeptAs(key: string, memory_mode: string) {
  const detail = {
    key, title: 'A quiet question', run_state: runState({ session: key }), queue: [],
    task_mode: 'agent', approval: 'normal', memory_mode, workspace_dir: '',
    messages: [
      { role: 'user', content: `What does ${key} keep?`, ts: '2026-10-06T09:00:00+00:00' },
      { role: 'assistant', content: `Only what ${key} was given.`, ts: '2026-10-06T09:00:05+00:00' },
    ],
  }
  h.details[key] = () => Promise.resolve(detail)
  return key
}

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
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 60)) })
/** The thread's surface: the chat column the transcript and the composer sit on. */
const surface = () => document.querySelector('[data-thread-surface]') as HTMLElement
/** The tint the surface wears, as the browser read it back, or '' for none. */
const tint = () => surface().style.backgroundColor
/** `PRIVATE_THREAD_TINT` as the browser reads it back from a style. */
function theTint() {
  const probe = document.createElement('div')
  probe.style.backgroundColor = PRIVATE_THREAD_TINT
  return probe.style.backgroundColor
}

/** ChatPage under an address the hash router would keep: a `chat/<key>` move opens that chat. */
function page(start: string, query: Record<string, string> = {}) {
  const address = { sub: start, go: (_sub: string) => {} }
  function Routed() {
    const [sub, setSub] = useState(start)
    address.sub = sub
    address.go = setSub
    const navigate = (to: string) => { const m = /^chat\/(.+)$/.exec(to); if (m) setSub(m[1]) }
    return <ChatPage sub={sub} navigate={navigate} query={query} setQuery={() => {}} />
  }
  render(<AppearanceProvider><Routed /></AppearanceProvider>)
  return address
}

async function pickMemory(label: string) {
  fireEvent.click(await screen.findByRole('button', { name: /^Execution boundary:/ }))
  const menu = await screen.findByRole('menu', { name: 'Execution boundary' })
  fireEvent.click(within(menu).getAllByRole('menuitemradio').find((r) => (r.textContent ?? '').startsWith(label))!)
}

describe('an open chat', () => {
  it.each(['temporary', 'incognito'])('kept as %s, its thread wears the tint', async (memory) => {
    const key = chatKeptAs(`chat-11-${memory}`, memory)
    page(key)
    await screen.findByText(`Only what ${key} was given.`)
    await settle()
    expect(surface().getAttribute('data-memory-mode')).toBe(memory)
    expect(tint()).not.toBe('')
    expect(tint()).toBe(theTint())
  })

  it('kept as usual, its thread wears none, even straight after a private one', async () => {
    const quiet = chatKeptAs('chat-12-quiet', 'incognito')
    const kept = chatKeptAs('chat-13-kept', 'persistent')
    const address = page(quiet)
    await screen.findByText(`Only what ${quiet} was given.`)
    await waitFor(() => expect(tint()).toBe(theTint()))
    act(() => address.go(kept))
    await screen.findByText(`Only what ${kept} was given.`)
    await settle()
    expect(surface().getAttribute('data-memory-mode')).toBeNull()
    expect(tint()).toBe('')
  })

  it('revisited from its recent copy, wears the tint from the first frame, read or no read', async () => {
    const quiet = chatKeptAs('chat-14-quiet', 'temporary')
    const kept = chatKeptAs('chat-15-kept', 'persistent')
    const address = page(quiet)
    await screen.findByText(`Only what ${quiet} was given.`)
    await waitFor(() => expect(tint()).toBe(theTint()))
    act(() => address.go(kept))
    await screen.findByText(`Only what ${kept} was given.`)
    await settle()
    h.details[quiet] = () => Promise.reject(new Error('the gateway went away'))
    act(() => address.go(quiet))
    expect(screen.getByText(`Only what ${quiet} was given.`), 'painted from its copy').toBeTruthy()
    expect(surface().getAttribute('data-memory-mode')).toBe('temporary')
    expect(tint()).toBe(theTint())
    await settle()
    expect(tint(), 'after its read failed').toBe(theTint())
  })
})

describe('a new chat', () => {
  it('takes the tint as soon as a private mode is chosen for it, and loses it again', async () => {
    page('')
    await screen.findByRole('button', { name: /^Execution boundary:/ })
    await settle()
    expect(surface().getAttribute('data-memory-mode')).toBeNull()
    expect(tint()).toBe('')
    await pickMemory('Incognito')
    await waitFor(() => expect(surface().getAttribute('data-memory-mode')).toBe('incognito'))
    expect(tint()).toBe(theTint())
    await pickMemory('Persistent')
    await waitFor(() => expect(surface().getAttribute('data-memory-mode')).toBeNull())
    expect(tint()).toBe('')
  })

  it.each(['Incognito', 'Temporary'])('kept as %s, keeps the tint through its first send, before its first read lands', async (label) => {
    h.newKey = `chat-31-${label.toLowerCase()}`
    const address = page('new', { seed: 'What does the parser keep?' })
    await screen.findByRole('button', { name: /^Execution boundary:/ })
    await settle()
    await pickMemory(label)
    await waitFor(() => expect(tint()).toBe(theTint()))
    fireEvent.click(screen.getByRole('button', { name: /^Send/ }))
    // The send makes the chat and moves the address to its key, which remounts it; its first read
    // is held for the whole test, so only what the chat was made as can say how it is kept.
    await waitFor(() => expect(address.sub).toBe(h.newKey))
    expect(screen.getAllByText('What does the parser keep?').length, 'the sent message is on the remounted thread').toBeGreaterThan(0)
    expect(surface().getAttribute('data-memory-mode')).toBe(label.toLowerCase())
    expect(tint()).toBe(theTint())
  })
})
