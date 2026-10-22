import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, fireEvent, waitFor, within } from '@testing-library/react'
import { runState } from '../../test/runState'
import { turnSources, sourceKey } from './turnSources'
import { SourcesRow } from './transcript/SourcesRow'
import type { Segment, ToolSegment } from './chatTypes'

// ── An answer's sources are what its turn retrieved (#3820) ─────────────────────────────────────
//
// "The Sources row and the pane's Sources tab list a link only when the answer cites it and it
// matches a URL the same turn actually retrieved: a result of the turn's web search calls, or a page
// its fetch calls read." A link the model wrote from memory stays a plain link in the text, with no
// badge. The row is built from the turn's recorded tool results, so it fetches nothing: no preview,
// no favicon. The title it shows is the one the search or the fetch returned.

const SESSION = 'chat-27-sources'
const ASKED = { role: 'user', content: 'Which storage engine suits a write-heavy log?', ts: '2026-10-06T09:00:00+00:00' }
const toolRow = (callId: string, tool: string, input: string, output: string) => ({
  role: 'tool', content: tool, ts: '2026-10-06T09:00:03+00:00',
  meta: { tool_call_id: callId, purpose: '', input, kind: 'other', done: true, output },
})
const fenced = (text: string) => `<untrusted_content source="web_search">\n${text}\n</untrusted_content>`
// One search (two hits, one of them never cited) and one fetch, then an answer citing both pages it
// read, one of them in another spelling, and a third page nothing in the turn retrieved.
const SEARCH = toolRow('tc-1', 'web_search', '{"query":"lsm tree write amplification"}', JSON.stringify({
  results: [
    { url: 'https://lsm.example.org/paper?utm_source=search&id=7', title: fenced('The LSM-tree paper'), snippet: fenced('…') },
    { url: 'https://other.example.net/post', title: fenced('An uncited hit') },
  ],
}))
const FETCH = toolRow('tc-2', 'web_fetch', '{"url":"https://btree.example.com/notes/"}', fenced('# B-tree notes\n\nPages split as keys arrive.'))
const ANSWER = {
  role: 'assistant', ts: '2026-10-06T09:00:09+00:00',
  content: 'An LSM tree favours writes ([the paper](https://lsm.example.org/paper?id=7#intro)), while a B-tree '
    + 'favours reads (https://btree.example.com/notes). A [survey](https://made-up.example.io/survey) agrees.',
}
const HOSTS = ['lsm.example.org', 'btree.example.com', 'made-up.example.io', 'other.example.net']

const h = vi.hoisted(() => ({ detail: {} as Record<string, unknown> }))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const answers: Record<string, (...args: unknown[]) => unknown> = {
    chatSessionDetail: () => Promise.resolve({ ...h.detail }),
    chatBoundary: () => Promise.resolve({
      sandbox: { confined: true, label: 'Sandboxed', said: 'The commands it runs are sandboxed on this computer.' },
      reach: { tier: 'all', label: 'Network on', said: 'Its web tools reach public hosts.' },
    }),
    chatSessions: () => Promise.resolve([]),
    dashboardConfig: () => Promise.resolve({ send_on_enter: true }),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
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
  readyState = 0
  constructor(public url: string) { setTimeout(() => { this.readyState = 1; this.onopen?.() }, 0) }
  send(): void {}
  close(): void { this.readyState = 3 }
}

let fetchMock: ReturnType<typeof vi.fn>
let ChatPage: typeof import('../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../app/appearance')['AppearanceProvider']

beforeEach(async () => {
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  vi.stubGlobal('IntersectionObserver', class { observe() {} unobserve() {} disconnect() {} takeRecords() { return [] } })
  fetchMock = vi.fn(() => Promise.resolve(new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } })))
  vi.stubGlobal('fetch', fetchMock)
  Element.prototype.scrollIntoView ??= () => {}
  sessionStorage.clear()
  localStorage.clear()
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 60)) })
async function openChat(query: Record<string, string> = {}) {
  h.detail = {
    key: SESSION, title: 'Storage engines', queue: [], run_state: runState({ session: SESSION }),
    task_mode: 'agent', approval: 'normal', memory_mode: 'persistent', workspace_dir: '',
    messages: [ASKED, SEARCH, FETCH, ANSWER],
  }
  render(<AppearanceProvider><ChatPage sub={SESSION} navigate={() => {}} query={query} setQuery={() => {}} /></AppearanceProvider>)
  await screen.findByText(/An LSM tree favours writes/)
  await settle()
}
const turn = () => document.querySelector('[data-transcript-turn="assistant"]') as HTMLElement
const requested = () => fetchMock.mock.calls.map((c) => String(c[0]))

describe('a turn that searched and fetched two pages and cites a third it never retrieved', () => {
  it('shows exactly the two it retrieved, by the titles they came back with', async () => {
    await openChat()
    const row = within(turn()).getByRole('region', { name: 'Sources' })
    const links = within(row).getAllByRole('link')
    expect(links.map((a) => a.textContent)).toEqual([
      expect.stringContaining('The LSM-tree paper'), expect.stringContaining('B-tree notes'),
    ])
    expect(links.map((a) => a.getAttribute('href'))).toEqual([
      'https://lsm.example.org/paper?id=7#intro', 'https://btree.example.com/notes',
    ])
    expect(row.textContent, 'a page the model wrote from memory is not a source').not.toMatch(/made-up|survey/)
    expect(row.textContent, 'a hit the answer never cited is not a source').not.toMatch(/uncited|other\.example/)
    expect(row.textContent, 'the fence is the model\'s, not the reader\'s').not.toMatch(/untrusted_content/)
  })

  it('badges the two in the text, and leaves the third a plain link', async () => {
    await openChat()
    const prose = within(turn()).getByText(/An LSM tree favours writes/).closest('p') as HTMLElement
    // A link's badge is the element right after it.
    const badge = (host: string) => {
      const next = within(prose).getAllByRole('link').find((a) => a.getAttribute('href')?.includes(host))!.nextElementSibling
      return next?.hasAttribute('data-source-n') ? next.getAttribute('data-source-n') : null
    }
    expect(within(prose).getAllByRole('link')).toHaveLength(3)
    expect(prose.querySelectorAll('[data-source-n]')).toHaveLength(2)
    expect([...prose.querySelectorAll('[data-source-n]')].map((b) => b.getAttribute('data-source-n'))).toEqual(['1', '2'])
    expect(badge('lsm.example.org')).toBe('1')
    expect(badge('btree.example.com')).toBe('2')
    expect(badge('made-up.example.io'), 'no badge for a link nothing retrieved').toBeNull()
  })

  it('fetches nothing to show them: no preview, no favicon', async () => {
    await openChat()
    expect(within(turn()).getByRole('region', { name: 'Sources' })).toBeTruthy()
    for (const host of HOSTS) expect(requested().filter((u) => u.includes(host)), host).toEqual([])
    expect(within(turn()).getByRole('region', { name: 'Sources' }).querySelector('img')).toBeNull()
  })

  it('lists the same two in the Activity pane\'s Sources tab', async () => {
    await openChat({ activity: '1' })
    // The Activity panel's own strip (`ChatActivityPanel`), by the id it gives its Sources tab.
    fireEvent.click(await waitFor(() => document.getElementById('act-tab-sources') as HTMLElement))
    const panel = await waitFor(() => document.getElementById('act-panel-sources') as HTMLElement)
    expect(within(panel).getAllByRole('link').map((a) => a.getAttribute('href'))).toEqual([
      'https://lsm.example.org/paper?id=7#intro', 'https://btree.example.com/notes',
    ])
  })
})

describe('the row on its own', () => {
  it('makes no request when it renders', () => {
    fetchMock.mockClear()
    render(<SourcesRow sources={[{ url: 'https://lsm.example.org/paper', title: 'The LSM-tree paper', host: 'lsm.example.org', n: 1 }]} />)
    expect(screen.getByRole('link', { name: /The LSM-tree paper/ }).getAttribute('href')).toBe('https://lsm.example.org/paper')
    expect(fetchMock).not.toHaveBeenCalled()
    expect(document.querySelector('img')).toBeNull()
  })

  it('renders nothing for a turn with no sources', () => {
    const { container } = render(<SourcesRow sources={[]} />)
    expect(container.textContent).toBe('')
  })
})

describe('a fetched result that is no web address', () => {
  it('is no source, and the row renders no link for it', async () => {
    const tool = (over: Partial<ToolSegment>): Segment => ({ kind: 'tool', id: 'tc', tool: 'web_fetch', done: true, input: '{"url":"javascript:alert(document.cookie)"}', output: '# Gotcha', ...over })
    const segs = [tool({}), tool({ id: 'tc-2', tool: 'web_search', input: '{}', output: JSON.stringify({ results: [{ url: 'javascript:alert(1)', title: 'Click me' }, { url: 'data:text/html,<script>alert(1)</script>', title: 'Me too' }] }) }),
      { kind: 'text', text: 'See [this](javascript:alert(document.cookie)), [that](javascript:alert(1)) and data:text/html,<script>alert(1)</script>.' } as Segment]
    expect(turnSources(segs)).toEqual([])
    // Handed one anyway, the row holds it to the answer's own link rule: its words, and no link.
    render(<SourcesRow sources={[{ url: 'javascript:alert(document.cookie)', title: 'Gotcha', host: 'x', n: 1 }]} />)
    const row = screen.getByRole('region', { name: 'Sources' })
    expect(within(row).queryByRole('link')).toBeNull()
    expect(row.querySelector('a'), 'no anchor at all').toBeNull()
    expect(row.textContent).toMatch(/Gotcha/)
  })

  it('opens a web address the way the answer\'s own links do: a new tab with no opener and no referrer', () => {
    render(<SourcesRow sources={[{ url: 'https://lsm.example.org/paper', title: 'The LSM-tree paper', host: 'lsm.example.org', n: 1 }]} />)
    const link = screen.getByRole('link', { name: /The LSM-tree paper/ })
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  })
})

describe('matching a cited link to a retrieved one', () => {
  const tool = (over: Partial<ToolSegment>): Segment => ({ kind: 'tool', id: 'tc', tool: 'web_fetch', done: true, input: '{"url":"https://a.example/x"}', output: '# A', ...over })
  const text = (t: string): Segment => ({ kind: 'text', text: t } as Segment)

  it('drops tracking parameters, the fragment and a trailing slash, and keeps the rest of the query', () => {
    expect(sourceKey('https://a.example/x/?utm_medium=mail&b=2&a=1#top')).toBe(sourceKey('https://a.example/x?a=1&b=2'))
    expect(sourceKey('https://a.example/x?page=2')).not.toBe(sourceKey('https://a.example/x?page=3'))
    expect(sourceKey('http://a.example/x'), 'the scheme is part of the page').not.toBe(sourceKey('https://a.example/x'))
    expect(sourceKey('mailto:me@a.example')).toBeNull()
  })

  it('counts no failed call, no unfinished one and no other tool', () => {
    for (const seg of [tool({ ok: false }), tool({ done: false }), tool({ tool: 'bash' }), tool({ tool: 'mcp__someone-else__web_fetch' })]) {
      expect(turnSources([seg, text('See https://a.example/x.')]), JSON.stringify(seg)).toEqual([])
    }
  })

  it('reads an agent CLI\'s call through the core server, and a search result that is not JSON', () => {
    expect(turnSources([tool({ tool: 'mcp__personalclaw-core__web_fetch' }), text('See https://a.example/x.')]).map((s) => s.title)).toEqual(['A'])
    const plain = tool({ tool: 'web_search', input: '{}', output: 'Results:\n1. A page https://b.example/y\n' })
    expect(turnSources([plain, text('Per https://b.example/y, yes.')]).map((s) => [s.title, s.host])).toEqual([['b.example', 'b.example']])
  })

  it('numbers each source once, in the order the answer first cites it', () => {
    const segs = [tool({}), tool({ id: 'tc-2', input: '{"url":"https://c.example/z"}', output: '# C' })]
    const got = turnSources([...segs, text('First https://c.example/z, then https://a.example/x, then https://c.example/z/ again.')])
    expect(got.map((s) => [s.n, s.title])).toEqual([[1, 'C'], [2, 'A']])
  })
})
