import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { useState } from 'react'
import { render, screen, waitFor, act } from '@testing-library/react'
import { EditorView } from '@codemirror/view'
import { runState } from '../../test/runState'

// ── Attaching to a chat you were in keeps what you typed ──────────────────────────────────────────
//
// The library's "Attach to <the chat you were last in>" is `#/chat/<key>?attach=<ref>` (PR 3): the
// item joins that chat's draft as a chip. A draft with text in it keeps its text, a chip already
// there stays and isn't added twice, and nothing is sent. The link may reach the chat while it is
// already open (the address names other refs), and the chips join then too.

const SESSION = 'chat-lisbon-weekend'

const h = vi.hoisted(() => ({
  calls: [] as Array<[string, unknown[]]>,
}))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const items: Record<string, { id: string; title: string }> = {
    k1: { id: 'k1', title: 'Router manual' },
    k2: { id: 'k2', title: 'Packing list' },
  }
  const answers: Record<string, (...args: unknown[]) => unknown> = {
    chatSessionDetail: () => Promise.resolve({
      key: SESSION, title: 'Lisbon weekend', run_state: runState({ session: SESSION }), queue: [],
      task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
      messages: [
        { role: 'user', content: 'Plan two days in Lisbon.', ts: '2026-11-01T09:00:00+00:00' },
        { role: 'assistant', content: 'Here is a plan.', ts: '2026-11-01T09:00:05+00:00' },
      ],
    }),
    knowledgeItem: (id) => (items[id as string] ? Promise.resolve(items[id as string]) : Promise.reject(new real.ApiError('not found', 404))),
    artifact: () => Promise.resolve({ slug: 'lisbon', name: 'Lisbon trip plan' }),
    sendChat: () => Promise.resolve({ ok: true }),
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
  }
  const api = new Proxy({} as Record<string, unknown>, {
    get(_t, p) {
      if (typeof p !== 'string') return undefined
      return (...args: unknown[]) => {
        h.calls.push([p, args])
        return p in answers ? answers[p](...args) : Promise.resolve([])
      }
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

const called = (name: string) => h.calls.filter(([n]) => n === name)
const settle = (ms = 40) => act(async () => { await new Promise((r) => setTimeout(r, ms)) })
const editor = () => EditorView.findFromDOM(document.querySelector('[data-composer-region] .cm-editor') as HTMLElement)
const draft = () => editor()?.state.doc.toString() ?? ''
const chips = () => screen.queryAllByRole('button', { name: /^Remove (knowledge|artifact) reference$/ })

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
  h.calls.length = 0
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

function page(query: Record<string, string>) {
  return (
    <AppearanceProvider>
      <ChatPage sub={SESSION} navigate={() => {}} query={query} setQuery={() => {}} />
    </AppearanceProvider>
  )
}

/** The chat as the router hosts it: its query in the address, and `setQuery` writing it there, so a
 *  key the page takes off the address is gone when the page is opened at that address again. */
function Routed({ initial, onAddress }: { initial: Record<string, string>; onAddress: (q: Record<string, string>) => void }) {
  const [query, setQueryState] = useState(initial)
  const setQuery = (patch: Record<string, string | null | undefined>) => setQueryState((q) => {
    const next = { ...q }
    for (const [k, v] of Object.entries(patch)) { if (v == null || v === '') delete next[k]; else next[k] = v }
    onAddress(next)
    return next
  })
  return <AppearanceProvider><ChatPage sub={SESSION} navigate={() => {}} query={query} setQuery={setQuery} /></AppearanceProvider>
}

describe('attaching to a chat you were in', () => {
  it('🔑 the link gives up attach once it is read: a chip taken out stays out when the address opens again', async () => {
    let address: Record<string, string> = { attach: 'knowledge:k1' }
    const first = render(<Routed initial={address} onAddress={(q) => { address = q }} />)
    await waitFor(() => expect(screen.queryByTitle('Router manual')).not.toBeNull())
    expect(address, 'the address no longer names the item').toEqual({})
    // Take the chip out, then open the chat again at the address the bar holds (a reload, Back).
    act(() => { screen.getByRole('button', { name: 'Remove knowledge reference' }).click() })
    expect(screen.queryByTitle('Router manual')).toBeNull()
    first.unmount()
    render(<Routed initial={address} onAddress={(q) => { address = q }} />)
    await screen.findByText('Here is a plan.')
    await settle(80)
    expect(screen.queryByTitle('Router manual'), 'the chip taken out is not back').toBeNull()
    expect(called('knowledgeItem'), 'the item was read once, for the link').toHaveLength(1)
  })


  it('🔑 the chips join a draft that has text, and the text stays', async () => {
    const { rerender } = render(page({}))
    await screen.findByText('Here is a plan.')
    await settle(60)
    act(() => { editor()?.dispatch({ changes: { from: 0, insert: 'Also check the weather' } }) })
    expect(draft()).toBe('Also check the weather')

    // The link reaches the chat while it is open: the address now names an item.
    rerender(page({ attach: 'knowledge:k1' }))
    await waitFor(() => expect(screen.queryByTitle('Router manual')).not.toBeNull())
    expect(draft(), 'what was typed stays').toBe('Also check the weather')

    // Another link: its item joins, the first stays, and it is held once.
    rerender(page({ attach: 'knowledge:k1,artifact:lisbon' }))
    await waitFor(() => expect(screen.queryByTitle('Lisbon trip plan')).not.toBeNull())
    expect(chips()).toHaveLength(2)
    expect(draft()).toBe('Also check the weather')
    await settle(60)
    expect(called('sendChat'), 'nothing was sent').toEqual([])
  })

  it('a chat the link opens holds the chip, with an empty draft', async () => {
    render(page({ attach: 'knowledge:k2' }))
    await screen.findByText('Here is a plan.')
    await waitFor(() => expect(screen.queryByTitle('Packing list')).not.toBeNull())
    expect(draft()).toBe('')
    expect(called('sendChat')).toEqual([])
  })
})
