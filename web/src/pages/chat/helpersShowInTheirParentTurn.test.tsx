import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, fireEvent, waitFor, within } from '@testing-library/react'
import { runState, runningState } from '../../test/runState'
import { helpersFromTool } from './transcript/TurnHelpers'
import { workflowRefFromTool } from './WorkflowProgressCard'

// ── Helpers show in the turn that started them (#3820) ──────────────────────────────────────────
//
// "Background subagents appear inside the parent turn's collapsed steps, not only in the pane's
// Subagents tab": each helper a `subagent_run` call started is a row under that call, among the
// turn's steps, matched on the call by the helper ids its result lists. The ids ride the stored
// tool row, so a reload finds the rows again, and each row reads its state from the gateway
// (`GET /api/spawn/{id}`) until a live frame says more. A running helper can be stopped from its
// row; a stop reads "Stopped", never a failure. The Subagents tab stays the full list.

const SESSION = 'chat-26-helpers'
const ASKED = { role: 'user', content: 'Survey the two storage engines in parallel.', ts: '2026-10-06T09:00:00+00:00' }
const ANSWER = { role: 'assistant', content: 'Both helpers are on it.', ts: '2026-10-06T09:00:09+00:00' }
const spawned = (id: string, agent: string, task: string) =>
  `Spawned 1 subagent(s). Results will arrive as completion events:\n  ${id} (${agent}): ${task}`
const spawnRow = (callId: string, tool: string, output: string) => ({
  role: 'tool', content: tool, ts: '2026-10-06T09:00:03+00:00',
  meta: { tool_call_id: callId, purpose: '', input: '{}', kind: 'other', done: true, output },
})
// Two calls, each starting one helper: the native agent's, and an agent CLI's through the core
// MCP server, which titles the same tool by its server.
const TWO_SPAWNS = [
  spawnRow('tc-1', 'subagent_run', spawned('aaaa1111', 'researcher', 'Read up on LSM trees')),
  spawnRow('tc-2', 'mcp__personalclaw-core__subagent_run', spawned('bbbb2222', 'researcher', 'Read up on B-trees')),
]

const h = vi.hoisted(() => ({
  detail: {} as Record<string, unknown>,
  status: {} as Record<string, Record<string, unknown>>,
  readFails: false,
  stopped: [] as string[],
}))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const answers: Record<string, (...args: unknown[]) => unknown> = {
    chatSessionDetail: () => Promise.resolve({ ...h.detail }),
    spawnedAgentStatus: (id: unknown) => {
      if (h.readFails) return Promise.reject(new real.ApiError('the gateway did not answer', 503))
      const s = h.status[String(id)]
      return s ? Promise.resolve({ id, ...s }) : Promise.reject(new real.ApiError('not found', 404))
    },
    cancelSpawnedAgent: (id: unknown) => { h.stopped.push(String(id)); return Promise.resolve({ ok: true }) },
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
  static all: FakeSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 0
  constructor(public url: string) {
    FakeSocket.all.push(this)
    setTimeout(() => { this.readyState = 1; this.onopen?.() }, 0)
  }
  send(): void {}
  close(): void { this.readyState = 3 }
  static get live(): FakeSocket { return FakeSocket.all[FakeSocket.all.length - 1] }
}
const frame = (type: string, data: Record<string, unknown>) =>
  act(() => { for (const s of FakeSocket.all) s.onmessage?.({ data: JSON.stringify({ type, data: { session: SESSION, ...data } }) }) })

let ChatPage: typeof import('../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../app/appearance')['AppearanceProvider']

beforeEach(async () => {
  FakeSocket.all = []
  h.status = {}
  h.readFails = false
  h.stopped = []
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  vi.stubGlobal('IntersectionObserver', class { observe() {} unobserve() {} disconnect() {} takeRecords() { return [] } })
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }))))
  Element.prototype.scrollIntoView ??= () => {}
  sessionStorage.clear()
  localStorage.clear()
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 60)) })
async function openChat(running: boolean) {
  h.detail = {
    key: SESSION, title: 'Storage survey', queue: [], run_state: running ? runningState({ session: SESSION }) : runState({ session: SESSION }),
    task_mode: 'agent', approval: 'normal', memory_mode: 'persistent', workspace_dir: '',
    messages: [ASKED, ...TWO_SPAWNS, ANSWER],
  }
  render(<AppearanceProvider><ChatPage sub={SESSION} navigate={() => {}} query={{}} setQuery={() => {}} /></AppearanceProvider>)
  await screen.findByText(ANSWER.content)
  await settle()
}
const turn = () => document.querySelector('[data-transcript-turn="assistant"]') as HTMLElement
const helperRows = () => within(turn()).queryAllByRole('listitem').filter((li) => li.closest('[aria-label="Helpers this step started"]'))
const rowOf = (task: RegExp) => helperRows().find((li) => task.test(li.textContent ?? ''))!

describe('a turn that started two helpers', () => {
  it('shows both inside its folded steps, each under the call that started it', async () => {
    h.status = {
      aaaa1111: { task: 'Read up on LSM trees', agent: 'researcher', done: false, last_tool: 'web_search' },
      bbbb2222: { task: 'Read up on B-trees', agent: 'researcher', done: false, last_tool: '' },
    }
    await openChat(false)
    expect(helperRows(), 'folded away with the rest of the work').toHaveLength(0)
    fireEvent.click(within(turn()).getByRole('button', { name: /^Worked/ }))
    await waitFor(() => expect(helperRows()).toHaveLength(2))
    expect(rowOf(/LSM trees/).textContent).toContain('researcher')
    expect(rowOf(/B-trees/).textContent).toContain('researcher')
  })

  it('reads each helper\'s state from the gateway after a reload, with no frame received', async () => {
    h.status = {
      aaaa1111: { task: 'Read up on LSM trees', agent: 'researcher', done: false, last_tool: 'web_search' },
      bbbb2222: { task: 'Read up on B-trees', agent: 'researcher', done: true, error: '', result: 'B-trees win reads.' },
    }
    await openChat(true)
    await waitFor(() => expect(helperRows()).toHaveLength(2))
    await waitFor(() => expect(rowOf(/LSM trees/).textContent).toMatch(/Running · web_search/))
    expect(rowOf(/B-trees/).textContent).toMatch(/Done/)
    expect(within(rowOf(/B-trees/)).queryByRole('button', { name: /^Stop/ }), 'nothing to stop once it ended').toBeNull()
  })

  it('takes a live frame on its own row and adds no row for it', async () => {
    h.status = {
      aaaa1111: { task: 'Read up on LSM trees', agent: 'researcher', done: false, last_tool: '' },
      bbbb2222: { task: 'Read up on B-trees', agent: 'researcher', done: false, last_tool: '' },
    }
    await openChat(true)
    await waitFor(() => expect(helperRows()).toHaveLength(2))
    frame('subagent_spawn', { id: 'aaaa1111', task: 'Read up on LSM trees', agent: 'researcher' })
    frame('subagent_tool', { id: 'aaaa1111', tool: 'web_fetch' })
    await waitFor(() => expect(rowOf(/LSM trees/).textContent).toMatch(/Running · web_fetch/))
    frame('subagent_done', { id: 'bbbb2222', error: null, result: 'B-trees win reads.', elapsed: 4.2 })
    await waitFor(() => expect(rowOf(/B-trees/).textContent).toMatch(/Done/))
    expect(helperRows()).toHaveLength(2)
  })

  it('stops one helper from its row, which then reads Stopped, not failed', async () => {
    h.status = {
      aaaa1111: { task: 'Read up on LSM trees', agent: 'researcher', done: false, last_tool: '' },
      bbbb2222: { task: 'Read up on B-trees', agent: 'researcher', done: false, last_tool: '' },
    }
    await openChat(true)
    await waitFor(() => expect(helperRows()).toHaveLength(2))
    fireEvent.click(within(rowOf(/LSM trees/)).getByRole('button', { name: /^Stop helper/ }))
    await waitFor(() => expect(h.stopped).toEqual(['aaaa1111']))
    frame('subagent_done', { id: 'aaaa1111', error: 'Cancelled by user', stopped: true, result: '' })
    await waitFor(() => expect(rowOf(/LSM trees/).textContent).toMatch(/Stopped/))
    expect(rowOf(/LSM trees/).textContent).not.toMatch(/Failed|Cancelled by user/)
    expect(rowOf(/B-trees/).textContent).toMatch(/Running/)
  })
})

describe('a helper the gateway no longer holds', () => {
  it('says its state is not known, with nothing to stop', async () => {
    h.status = { bbbb2222: { task: 'Read up on B-trees', agent: 'researcher', done: false, last_tool: '' } }
    await openChat(true)
    await waitFor(() => expect(rowOf(/LSM trees/).textContent).toMatch(/No longer tracked/))
    expect(within(rowOf(/LSM trees/)).queryByRole('button', { name: /^Stop/ })).toBeNull()
    expect(rowOf(/B-trees/).textContent).toMatch(/Running/)
  })
})

describe('a helper whose state the gateway did not give', () => {
  it('says its state was not read, not that it runs', async () => {
    h.readFails = true
    await openChat(true)
    await waitFor(() => expect(rowOf(/LSM trees/).textContent).toMatch(/State not read: the gateway did not answer/))
    expect(rowOf(/LSM trees/).textContent).not.toMatch(/Running/)
  })
})

describe('reading a call\'s helpers', () => {
  it('reads the ids a subagent_run result lists, by any of the core server\'s titles', () => {
    const out = 'Spawned 2 subagent(s). Results will arrive as completion events:\n  aaaa1111 (researcher): Read up on LSM trees\n  bbbb2222: Read up on B-trees'
    const both = [{ id: 'aaaa1111', agent: 'researcher', task: 'Read up on LSM trees' }, { id: 'bbbb2222', agent: '', task: 'Read up on B-trees' }]
    expect(helpersFromTool('subagent_run', out)).toEqual(both)
    expect(helpersFromTool('mcp__personalclaw-core__subagent_run', out)).toEqual(both)
    expect(helpersFromTool('mcp.personalclaw-core.subagent_run', out)).toEqual(both)
    expect(helpersFromTool('Running: @personalclaw-core/subagent_run', out)).toEqual(both)
  })

  it('reads none from another tool, another server, or the tasks that did not start', () => {
    const out = 'Spawned 1 subagent(s). Results will arrive as completion events:\n  aaaa1111 (researcher): Read up\n\n1 task(s) did not start:\n  - Other: over the limit'
    expect(helpersFromTool('subagent_run', out).map((x) => x.id)).toEqual(['aaaa1111'])
    expect(helpersFromTool('bash', out)).toEqual([])
    expect(helpersFromTool('mcp__someone-else__subagent_run', out)).toEqual([])
    expect(helpersFromTool('subagent_run', '{"run_id": "abcdef12", "status": "running"}')).toEqual([])
    expect(helpersFromTool('subagent_run', undefined)).toEqual([])
  })

  it('takes a batch an agent CLI started for the run its card follows, as the native agent\'s', () => {
    const out = '{"run_id": "abcdef12", "status": "running"}'
    expect(workflowRefFromTool('mcp__personalclaw-core__subagent_run', out)).toEqual(workflowRefFromTool('subagent_run', out))
    expect(workflowRefFromTool('mcp__personalclaw-core__subagent_run', out)?.runId).toBe('abcdef12')
    expect(workflowRefFromTool('mcp__someone-else__subagent_run', out)).toBeNull()
  })
})
