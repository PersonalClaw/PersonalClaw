/**
 * The chat's Activity rail is its session inspector, and what it opens shows in one detail tile
 * beside the thread (#3820's Oct 18 amendment).
 *
 * "The session inspector is a persistent narrow column … with independently expandable,
 * keyboard-operable sections. Each section has a disclosure control, a compact collapsed summary,
 * optional reorder handle, and remembered open state": Environment, Context, Connectors, Outputs and
 * Background tasks, beside the Files, Sources and Side question the rail already had. "Clicking an
 * inline workflow card or a Background tasks row opens the same detail tile … Closing or collapsing
 * it never stops work." "At compact widths the inspector becomes a drawer and the selected detail
 * stacks or replaces it with a deterministic Back path."
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import { runState } from '../../test/runState'
import type { HistMsg } from './chatTypes'

const SESSION = 'chat-inspector'
const toolRow = (callId: string, tool: string, input: string, output: string): HistMsg => ({
  role: 'tool', content: tool, ts: '2026-10-20T09:00:02Z',
  meta: { tool_call_id: callId, purpose: '', input, kind: 'other', done: true, output },
})
const MESSAGES: HistMsg[] = [
  { role: 'user', content: 'Draft the launch plan and start the digest.', ts: '2026-10-20T09:00:00Z' },
  toolRow('tc-1', 'artifact_save', '{"name":"Launch plan","kind":"markdown"}', "Saved artifact 'Launch plan' (slug: launch-plan, version 1, base 9f2c)."),
  toolRow('tc-2', 'write_file', '{"path":"notes/todo.md","content":"- ship"}', 'Wrote notes/todo.md'),
  toolRow('tc-3', 'workflow_start', '{"workflow":"digest"}', '{"run_id": "a1b2c3d4", "workflow": "digest", "status": "running"}'),
  { role: 'assistant', content: 'The plan is saved and the digest is running.', ts: '2026-10-20T09:00:09Z' },
]
const STARTED = new Date(Date.now() - 79_000).toISOString()
const RUNNING = {
  running: [
    { work: 'workflow:a1b2c3d4', kind: 'workflow', session: SESSION, phase: 'working', line: 'Step 2 of 4', since: STARTED, name: 'Nightly digest', agent: '', started_at: STARTED, route: '#/workflows/runs/a1b2c3d4', blocked: false, waiting: [] },
    { work: 'subagent:aaaa1111', kind: 'subagent', session: SESSION, phase: 'working', line: '', since: STARTED, name: 'Read up on LSM trees', agent: 'researcher', started_at: STARTED, route: '', blocked: false, waiting: [] },
    { work: 'workflow:elsewhere', kind: 'workflow', session: 'chat-other', phase: 'working', line: '', since: STARTED, name: 'Another chat’s run', agent: '', started_at: STARTED, route: '', blocked: false, waiting: [] },
  ],
  finished: [
    { work: 'workflow:old1', kind: 'workflow', session: SESSION, phase: 'complete', line: '', since: STARTED, name: 'Yesterday’s digest', agent: '', started_at: STARTED, route: '', blocked: false, waiting: [] },
  ],
}
const server = (name: string, status: string, over: Record<string, unknown> = {}) => ({ name, status, tools: [], enabled: true, ...over })
const SERVERS = [
  server('calendar', 'connected'), server('tracker', 'error', { error: 'connection refused' }), server('drive', 'signin'),
  server('notes', 'connected'), server('search', 'connected'), server('legacy', 'connected', { enabled: false }),
]
/** The calls that act: a sign-in, a reconnect, a stop. Reading never makes one. */
const ACTS = ['reconnectMcp', 'mcpSignIn', 'signInMcp', 'cancelSpawnedAgent', 'cancelWorkflowRun', 'cancelFanout']

const h = vi.hoisted(() => ({ detail: null as unknown, calls: [] as string[] }))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const base: Record<string, (...args: unknown[]) => unknown> = {
    chatSessionDetail: () => Promise.resolve(h.detail),
    mcpServers: () => Promise.resolve(SERVERS),
    running: () => Promise.resolve(RUNNING),
    projects: () => Promise.resolve([{ id: 'p-launch', name: 'Launch' }]),
    chatBoundary: () => Promise.resolve({
      sandbox: { confined: true, label: 'Sandboxed', said: 'The commands it runs are sandboxed on this computer.' },
      reach: { tier: 'all', label: 'Network on', said: 'Its web tools reach public hosts.' },
    }),
    workflowRun: () => Promise.resolve({
      run_id: 'a1b2c3d4', workflow: 'digest', status: 'complete', spec_version: 1, error: '', attention: null, tokens: 120, elapsed_secs: 4,
      nodes: [{ instance_path: 'root', node_id: 'gather', state: 'done' }],
    }),
    spawnedAgentStatus: () => Promise.resolve({ id: 'aaaa1111', task: 'Read up on LSM trees', agent: 'researcher', done: false, last_tool: 'web_search' }),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
    themes: () => new Promise(() => {}),
    theme: () => new Promise(() => {}),
  }
  const api = new Proxy(base, {
    get(t, p: string) {
      return (...args: unknown[]) => {
        h.calls.push(p)
        return p in t ? t[p](...args) : Promise.resolve([])
      }
    },
  })
  return { ...real, api }
})
// The tile's viewers are their own pages' business: here each says what it was handed.
vi.mock('../artifacts/ArtifactViewer', () => ({ ArtifactViewer: ({ slug }: { slug: string }) => <p>The artifact {slug}</p> }))
vi.mock('../files/browse/FileViewer', async (orig) => ({
  ...(await orig<typeof import('../files/browse/FileViewer')>()),
  FileViewer: ({ entry }: { entry: { path: string } }) => <p>The file {entry.path}</p>,
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
const frame = (type: string, data: Record<string, unknown>) =>
  act(() => { FakeSocket.last?.onmessage?.({ data: JSON.stringify({ type, data: { session: SESSION, ...data } }) }) })

function atWidth(width: number): void {
  vi.stubGlobal('innerWidth', width)
  vi.stubGlobal('matchMedia', (query: string) => {
    const max = /max-width:\s*([\d.]+)px/.exec(query)
    const min = /min-width:\s*([\d.]+)px/.exec(query)
    const matches = (!!max || !!min) && (!max || width <= Number(max[1])) && (!min || width >= Number(min[1]))
    return { matches, media: query, onchange: null, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false }
  })
}

let query: Record<string, string> = {}
let ChatPageUnderTest: typeof import('../ChatPage').ChatPage
function Chat({ initial }: { initial: Record<string, string> }) {
  const [q, setQ] = useState(initial)
  query = q
  return (
    <ChatPageUnderTest sub={SESSION} navigate={() => {}} query={q}
      setQuery={(patch: Record<string, string | null | undefined>) => setQ((prev) => {
        const next = { ...prev }
        for (const [k, v] of Object.entries(patch)) { if (v === null || v === undefined || v === '') delete next[k]; else next[k] = v }
        return next
      })} />
  )
}
async function openChat(initial: Record<string, string> = { activity: '1' }) {
  ChatPageUnderTest = (await import('../ChatPage')).ChatPage
  const { AppearanceProvider } = await import('../../app/appearance')
  const view = render(<AppearanceProvider><Chat initial={initial} /></AppearanceProvider>)
  await screen.findByText('The plan is saved and the digest is running.')
  await act(async () => { await new Promise((r) => setTimeout(r, 60)) })
  return view
}

const inspector = () => document.querySelector('[data-inspector]') as HTMLElement | null
const section = (name: string) => within(inspector()!).getByRole('region', { name })
const header = (name: string) => within(section(name)).getAllByRole('button')[0]
/** A section's name: the title its header holds, which names its region. */
const nameOf = (r: Element) => document.getElementById(r.getAttribute('aria-labelledby') ?? '')?.textContent ?? ''
const order = () => within(inspector()!).getAllByRole('region').map(nameOf)
const openSections = () => within(inspector()!).getAllByRole('region').filter((r) => within(r).getAllByRole('button')[0].getAttribute('aria-expanded') === 'true').map(nameOf)
const acted = () => h.calls.filter((c) => ACTS.includes(c))
const tile = () => document.querySelector('[data-pane-host="chat"] section[aria-labelledby]') as HTMLElement | null

beforeEach(() => {
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  vi.stubGlobal('IntersectionObserver', class { observe() {} unobserve() {} disconnect() {} takeRecords() { return [] } })
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  Element.prototype.scrollIntoView ??= () => {}
  Element.prototype.scrollTo ??= () => {}
  const box = HTMLElement.prototype.getBoundingClientRect
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    if (this.getAttribute('data-pane-host') !== 'chat') return box.call(this)
    return { width: innerWidth, height: 800, top: 0, left: 0, right: innerWidth, bottom: 800, x: 0, y: 0, toJSON: () => ({}) } as DOMRect
  })
  localStorage.clear()
  sessionStorage.clear()
  h.calls = []
  atWidth(1280)
  h.detail = {
    key: SESSION, title: 'Launch', messages: MESSAGES, run_state: runState({ session: SESSION }), queue: [],
    task_mode: 'agent', approval: 'normal', memory_mode: 'persistent', workspace_dir: '/work/launch',
  }
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('the session inspector', () => {
  it('is a column of sections, each a disclosure with a summary while it is folded', async () => {
    await openChat()
    expect(order()).toEqual(['Environment', 'Context', 'Connectors', 'Outputs', 'Files', 'Sources', 'Background tasks', 'Side question'])
    expect(openSections(), 'what the chat made, read and still runs is open at first').toEqual(['Outputs', 'Sources', 'Background tasks'])
    expect(header('Connectors').textContent, 'a folded section says what it holds').toMatch(/2 need you · 3 ready/)
    expect(header('Environment').textContent).toMatch(/Ask first|Normal|Network on/)
  })

  it('opens, folds and moves each section, and shows them as she left them after a reload', async () => {
    const first = await openChat()
    for (const name of order()) {
      const wasOpen = header(name).getAttribute('aria-expanded') === 'true'
      fireEvent.click(header(name))
      expect(header(name).getAttribute('aria-expanded'), name).toBe(String(!wasOpen))
      fireEvent.click(header(name))
      expect(header(name).getAttribute('aria-expanded'), name).toBe(String(wasOpen))
    }
    fireEvent.click(header('Environment'))
    fireEvent.click(header('Outputs'))
    fireEvent.click(within(section('Connectors')).getByRole('button', { name: 'Move Connectors up' }))
    fireEvent.click(within(section('Background tasks')).getByRole('button', { name: 'Move Background tasks down' }))
    const left = { order: order(), open: openSections() }
    expect(left.order).toEqual(['Environment', 'Connectors', 'Context', 'Outputs', 'Files', 'Sources', 'Side question', 'Background tasks'])
    expect(left.open).toEqual(['Environment', 'Sources', 'Background tasks'])
    first.unmount()
    await openChat()
    expect({ order: order(), open: openSections() }).toEqual(left)
    expect(within(section('Environment')).getByRole('button', { name: 'Move Environment up' }), 'nothing above the first').toHaveAttribute('aria-disabled', 'true')
  })

  it('says where the chat works, and its Change opens the composer\'s Execution Boundary', async () => {
    await openChat()
    fireEvent.click(header('Environment'))
    const env = section('Environment')
    await waitFor(() => expect(env.textContent).toMatch(/\/work\/launch/))
    expect(env.textContent).toMatch(/sandboxed on this computer/)
    expect(env.textContent).toMatch(/reach public hosts/)
    const boundary = screen.getByRole('button', { name: /Execution boundary/i })
    expect(boundary).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(within(env).getByRole('button', { name: 'Change in the composer' }))
    await waitFor(() => expect(boundary).toHaveAttribute('aria-expanded', 'true'))
  })

  it('lists the connectors that need her first, shows more without losing focus, and runs nothing by being opened', async () => {
    await openChat()
    fireEvent.click(header('Connectors'))
    const list = () => within(section('Connectors')).getAllByRole('listitem').map((li) => li.textContent?.split(/(?=ready|error|sign-in|disabled)/)[0])
    await waitFor(() => expect(list()).toEqual(['tracker', 'drive', 'calendar', 'notes']))
    const more = within(section('Connectors')).getByRole('button', { name: 'Show 2 more' })
    more.focus()
    fireEvent.click(more)
    expect(list()).toEqual(['tracker', 'drive', 'calendar', 'notes', 'search', 'legacy'])
    expect(document.activeElement, 'the same control keeps the focus').toBe(more)
    expect(more).toHaveTextContent('Show less')
    expect(more).toHaveAttribute('aria-expanded', 'true')
    expect(within(section('Connectors')).getByRole('link', { name: 'Sign in on the Tools page' })).toHaveAttribute('href', '#/tools?open=drive')
    expect(acted(), 'opening the section signed in, reconnected or stopped something').toEqual([])
  })
})

describe('the detail tile', () => {
  it('opens an output from the inspector beside the thread, under its address, and gives focus back to its row on close', async () => {
    await openChat()
    const outputs = section('Outputs')
    expect(within(outputs).getAllByRole('button', { name: /^Open / }).map((b) => b.getAttribute('aria-label'))).toEqual(['Open todo.md', 'Open Launch plan'])
    const row = within(outputs).getByRole('button', { name: 'Open Launch plan' })
    row.focus()
    fireEvent.click(row)
    expect(query.beside).toBe('artifact:launch-plan')
    const pane = await screen.findByRole('region', { name: 'Launch plan' })
    expect(await within(pane).findByText('The artifact launch-plan')).toBeInTheDocument()
    expect(row, 'the row says its detail is the one open').toHaveAttribute('aria-current', 'true')
    const close = within(pane).getByRole('button', { name: 'Close artifact' })
    close.focus()
    fireEvent.click(close)
    await waitFor(() => expect(tile()).toBeNull())
    expect(query.beside).toBeUndefined()
    expect(document.activeElement, 'focus is back on the row it was opened from').toBe(row)
  })

  it('opens a run from a Background tasks row and from its card in the thread, in the same tile', async () => {
    await openChat()
    const background = section('Background tasks')
    await waitFor(() => expect(within(background).getByRole('button', { name: 'Open Nightly digest' })).toBeInTheDocument())
    expect(background.textContent, 'another chat\'s run is not this chat\'s').not.toMatch(/Another chat/)
    expect(background.textContent).toMatch(/Step 2 of 4 · for 1m \d+s/)
    expect(background.textContent).toMatch(/1 finished/)
    fireEvent.click(within(background).getByRole('button', { name: 'Open Nightly digest' }))
    expect(query.beside).toBe('run:a1b2c3d4')
    const fromRow = await screen.findByRole('region', { name: 'Workflow run' })
    fireEvent.click(within(fromRow).getByRole('button', { name: 'Close run' }))
    await waitFor(() => expect(tile()).toBeNull())
    // The run's own card in the thread opens the same address in the same host.
    fireEvent.click(await screen.findByRole('button', { name: 'Open digest beside the conversation' }))
    expect(query.beside).toBe('run:a1b2c3d4')
    expect(await screen.findByRole('region', { name: 'Workflow run' })).toBe(tile())
  })

  it('opens what a copied address names', async () => {
    await openChat({ beside: 'file:notes/todo.md' })
    const pane = await screen.findByRole('region', { name: 'todo.md' })
    expect(pane.textContent).toMatch(/The file notes\/todo\.md/)
    expect(inspector(), 'the address opened no inspector').toBeNull()
  })

  it('stops nothing when the tile, the inspector or the sidebar go', async () => {
    await openChat()
    frame('subagent_spawn', { id: 'aaaa1111', task: 'Read up on LSM trees', agent: 'researcher' })
    const background = section('Background tasks')
    await waitFor(() => expect(within(background).getByRole('button', { name: 'Open Read up on LSM trees' })).toBeInTheDocument())
    expect(background.textContent, 'the helper says how long it has run').toMatch(/for 1m \d+s/)
    fireEvent.click(within(background).getByRole('button', { name: 'Open Read up on LSM trees' }))
    expect(query.beside).toBe('helper:aaaa1111')
    const pane = await screen.findByRole('region', { name: 'Read up on LSM trees' })
    expect(pane.textContent).toMatch(/Running/)
    fireEvent.click(within(pane).getByRole('button', { name: 'Close helper' }))
    await waitFor(() => expect(tile()).toBeNull())
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(inspector()).toBeNull())
    expect(acted(), 'closing a surface stopped work').toEqual([])
  })
})

describe('a turn that made an artifact', () => {
  it('shows it as a card under its answer, out of the folded steps, and the card opens it in the tile', async () => {
    await openChat({})
    const turn = document.querySelector('[data-transcript-turn="assistant"]') as HTMLElement
    const made = within(turn).getByRole('list', { name: 'What this turn made' })
    expect(within(made).getAllByRole('listitem').map((li) => li.textContent)).toEqual([expect.stringContaining('Launch plan')])
    expect(made.closest('[aria-expanded]'), 'the card is not folded away with the steps').toBeNull()
    expect(within(made).getByRole('link', { name: 'Open Launch plan in the Library' })).toHaveAttribute('href', '#/artifacts/launch-plan')
    fireEvent.click(within(made).getByRole('button', { name: 'Open Launch plan beside the conversation' }))
    expect(query.beside).toBe('artifact:launch-plan')
    expect(await screen.findByRole('region', { name: 'Launch plan' })).toBe(tile())
  })

  it('shows no card for a save that failed', async () => {
    h.detail = { ...(h.detail as Record<string, unknown>), messages: MESSAGES.map((m) => (m.meta?.tool_call_id === 'tc-1' ? { ...m, meta: { ...m.meta, ok: false } } : m)) }
    await openChat({})
    expect(screen.queryByRole('list', { name: 'What this turn made' })).toBeNull()
  })
})

describe('on a phone', () => {
  it('the detail takes the inspector\'s place, and Back to Activity brings the inspector back', async () => {
    atWidth(390)
    await openChat()
    expect(within(section('Outputs')).queryByRole('button', { name: 'Move Outputs up' }), 'the drawer is not reordered').toBeNull()
    fireEvent.click(within(section('Outputs')).getByRole('button', { name: 'Open Launch plan' }))
    const pane = await screen.findByRole('region', { name: 'Launch plan' })
    await waitFor(() => expect(inspector(), 'the detail stands in for the inspector').toBeNull())
    expect(query.activity, 'the address keeps the inspector to come back to').toBe('1')
    fireEvent.click(within(pane).getByRole('button', { name: 'Back to Activity' }))
    await waitFor(() => expect(inspector()).not.toBeNull())
    expect(query.beside).toBeUndefined()
    expect(tile()).toBeNull()
  })
})
