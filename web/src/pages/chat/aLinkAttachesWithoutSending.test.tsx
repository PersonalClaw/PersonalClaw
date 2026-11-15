import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── A link opens a new chat with library items attached, and sends nothing ─────────────────────
//
// `#/chat/new?attach=knowledge:k1,artifact:lisbon` is how the library hands items to a chat (its
// New chat with this, PR 3). Each item the library can read is a chip in the composer, the chip an
// @-mention or a picker makes; nothing is sent until the person sends. A ref that names nothing the
// library can read is said on the line above the composer, not dropped without a word, and what
// an intent found, which is a log and not an item, is not a chip: the line says so and offers Ask
// about this, a new chat that reads it.
//
// Driven through ChatPage, as a link opens it; each request is read where it leaves the page.

const h = vi.hoisted(() => ({
  calls: [] as Array<[string, unknown[]]>,
  knowledge: {} as Record<string, { id: string; title: string }>,
  artifacts: {} as Record<string, { slug: string; name: string }>,
  reports: [] as Array<{ id: string; name: string; finding_item_id: string | null }>,
}))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const missing = () => Promise.reject(new real.ApiError('not found', 404))
  const answers: Record<string, (...args: unknown[]) => unknown> = {
    knowledgeItem: (id) => (h.knowledge[id as string] ? Promise.resolve(h.knowledge[id as string]) : missing()),
    artifact: (slug) => (h.artifacts[slug as string] ? Promise.resolve(h.artifacts[slug as string]) : missing()),
    researchReports: () => Promise.resolve({ reports: h.reports }),
    investigate: () => Promise.resolve({ session_key: 'chat-asks-9', context: { opening_prompt: 'What did it find?' } }),
    sendChat: () => Promise.resolve({ ok: true, session: 'chat-4-x' }),
    createChatSession: () => Promise.resolve({ key: 'chat-4-x' }),
    dashboardConfig: () => Promise.resolve({ send_on_enter: true }),
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

const WRITE_UP = '0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d'
const called = (name: string) => h.calls.filter(([n]) => n === name)
const settle = (ms = 40) => act(async () => { await new Promise((r) => setTimeout(r, ms)) })

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
  h.knowledge = { k1: { id: 'k1', title: 'Router manual' }, [WRITE_UP]: { id: WRITE_UP, title: 'Weekly perf digest' } }
  h.artifacts = { lisbon: { slug: 'lisbon', name: 'Lisbon trip plan' } }
  h.reports = [{ id: 'rpt-1a2b3c4d', name: 'Weekly perf digest', finding_item_id: WRITE_UP }, { id: 'rpt-unrun', name: 'Not yet', finding_item_id: '' }]
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

function openLink(query: Record<string, string>, navigate = vi.fn()) {
  render(
    <AppearanceProvider>
      <ChatPage sub="new" navigate={navigate} query={query} setQuery={() => {}} />
    </AppearanceProvider>,
  )
  return navigate
}

const chip = (name: string) => screen.queryByTitle(name)

describe('a link that attaches library items', () => {
  it('🔑 opens a new chat with two chips and sends nothing', async () => {
    openLink({ attach: 'knowledge:k1,artifact:lisbon' })
    await waitFor(() => expect(chip('Router manual'), 'the knowledge item is a chip').not.toBeNull())
    expect(chip('Lisbon trip plan'), 'the artifact is a chip').not.toBeNull()
    expect(screen.getByRole('button', { name: 'Remove knowledge reference' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Remove artifact reference' })).toBeTruthy()
    await settle(80)
    expect(called('sendChat'), 'nothing was sent').toEqual([])
    expect(called('createChatSession'), 'no chat was made for it').toEqual([])
    expect(screen.queryByRole('alert'), 'every ref attached, so nothing is said').toBeNull()
  })

  it('🔑 sends them with the person\'s message, by the ids the library named', async () => {
    const user = userEvent.setup()
    openLink({ attach: 'knowledge:k1,artifact:lisbon', seed: 'Pack for this' })
    await waitFor(() => expect(chip('Lisbon trip plan')).not.toBeNull())
    await user.click(screen.getByRole('button', { name: 'Send message' }))
    await waitFor(() => expect(called('sendChat')).toHaveLength(1))
    const sent = JSON.stringify(called('sendChat')[0][1])
    expect(sent).toContain('"knowledge":["k1"]')
    expect(sent).toContain('"artifacts":["lisbon"]')
  })

  it('a report attaches as its write-up', async () => {
    openLink({ attach: 'report:rpt-1a2b3c4d' })
    await waitFor(() => expect(chip('Weekly perf digest')).not.toBeNull())
    expect(called('knowledgeItem').map(([, a]) => a[0]), 'the write-up the report keeps is what is read').toEqual([WRITE_UP])
  })

  it('🔑 an attach naming nothing readable says why, and attaches what it can', async () => {
    openLink({ attach: 'knowledge:gone,artifact:lisbon,note:k1,report:rpt-unrun' })
    const line = await screen.findByRole('alert')
    expect(line.textContent).toContain("Couldn't attach 3 of the items this link names. They aren't in your library, or couldn't be read.")
    expect(chip('Lisbon trip plan'), 'the one it could read is attached').not.toBeNull()
    expect(called('sendChat')).toEqual([])
  })

  it('🔑 a findings ref is not a chip: the chat says so and offers Ask about this', async () => {
    const user = userEvent.setup()
    openLink({ attach: 'findings:power-bill' })
    const line = await screen.findByRole('alert')
    expect(line.textContent).toContain("What an intent found can't be attached to a chat. You can ask about it in a new chat instead.")
    expect(screen.queryByRole('button', { name: /Remove .* reference/ }), 'no chip for it').toBeNull()
    expect(called('sendChat')).toEqual([])
    await user.click(within(line).getByRole('button', { name: 'Ask about this' }))
    await waitFor(() => expect(called('investigate').map(([, a]) => a[0])).toEqual([expect.objectContaining({ kind: 'findings', id: 'power-bill' })]))
    expect(called('sendChat'), 'asking opens a chat; it sends nothing either').toEqual([])
  })

  it('with project, the chat is in that project and holds the chip', async () => {
    openLink({ project: 'p-0a1b2c3d', attach: 'knowledge:k1' })
    await waitFor(() => expect(chip('Router manual')).not.toBeNull())
    expect(called('sendChat')).toEqual([])
  })
})
