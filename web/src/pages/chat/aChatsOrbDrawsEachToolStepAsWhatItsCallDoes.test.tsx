import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import { runState, runningState } from '../../test/runState'

// ── A chat's live orb draws each tool step as what its call does ────────────────────────────────
//
// The running turn's orb, in its live line (`ui/TurnStatusRow`), drew every tool step as running a
// command, whatever the call did. The gateway's `work_phase` frame says now what a call does
// (`act`: `read`, `edit` or `run`, only while the turn is in `tool`), and the orb draws it: a call
// that reads is reading, one that edits is editing, one that runs is running a command, and one the
// gateway says nothing of is running, as before. The line stays the gateway's, and the phase is
// still `tool`. A step's state holds 1.2 s from its start while the turn thinks again, so a read
// over in milliseconds is still seen; the line says Thinking at once. The frames below are what the gateway sends for a turn that reads a file, edits it
// and runs a command (`tests/test_a_tool_call_says_whether_it_reads_edits_or_runs.py`).

const SESSION = 'chat-orb-acts'

const h = vi.hoisted(() => {
  const idle = {
    key: 'chat-orb-acts', title: 'chat-orb-acts',
    messages: [
      { role: 'user', content: 'earlier question', ts: '2026-10-19T09:00:00Z' },
      { role: 'assistant', content: 'earlier answer', ts: '2026-10-19T09:00:05Z' },
    ] as unknown[],
    queue: [] as unknown[], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
  }
  return { idle, detailCalls: [] as { resolve: (d: unknown) => void }[], chatSessionDetail: vi.fn(), stopChat: vi.fn() }
})

vi.mock('../../lib/api', () => {
  const ok = () => Promise.resolve([])
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    stopChat: h.stopChat,
    sendChat: () => Promise.resolve({ ok: true, session: 'chat-orb-acts' }),
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
  }
  const api = new Proxy(base, {
    get(t, p: string) {
      if (p in t) return t[p]
      return (t[p] = (...__a: unknown[]) => ok())
    },
  })
  return { api, errText: (e: unknown) => String((e as Error)?.message ?? e) }
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
  FakeSocket.last = null
  h.detailCalls.length = 0
  h.stopChat.mockReset().mockResolvedValue({ stopped: true })
  h.chatSessionDetail.mockReset().mockImplementation(
    () => new Promise((resolve) => { h.detailCalls.push({ resolve }) }),
  )
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

const orbs = () => document.querySelectorAll('[data-agent-orb]')
const head = () => document.querySelector('[data-turn-status]') as HTMLElement | null
const line = () => document.querySelector('[data-turn-state]')?.textContent

/** The chat open and idle, then a turn starting on it: her question, and the turn waiting to start. */
async function aTurnStarts() {
  render(
    <AppearanceProvider>
      <ChatPage sub={SESSION} navigate={() => {}} query={{}} setQuery={() => {}} />
    </AppearanceProvider>,
  )
  await waitFor(() => expect(h.detailCalls.length).toBe(1))
  await act(async () => { h.detailCalls[0].resolve({ ...h.idle, run_state: runState({ session: SESSION }) }) })
  await waitFor(() => expect(screen.queryByText('earlier answer')).not.toBeNull())
  // Nothing runs: nothing at the head of any reply, and nothing on the page animates.
  expect(orbs()).toHaveLength(0)
  pushFrame('work_phase', { ...runningState({ session: SESSION, phase: 'queued', line: 'Waiting to start' }) })
  await waitFor(() => expect(h.detailCalls.length).toBe(2))
  await act(async () => {
    h.detailCalls[1].resolve({
      ...h.idle,
      run_state: runningState({ session: SESSION, phase: 'queued', line: 'Waiting to start' }),
      messages: [...h.idle.messages, { role: 'user', content: 'which stage is the run on?', ts: '2026-10-19T09:05:00Z' }],
    })
  })
  await waitFor(() => expect(screen.queryByText('which stage is the run on?')).not.toBeNull())
}

const orb = () => document.querySelector('[data-turn-status] [data-agent-orb]') as HTMLElement | null

describe('a running turn’s orb draws each tool step as what its call does', () => {
  it('reading, editing, running a command, and running for a call that says nothing; the line is the gateway’s', async () => {
    await aTurnStarts()
    const steps: [string, string | null, string][] = [
      ['read_file', 'read', 'reading'],
      ['edit_file', 'edit', 'editing'],
      ['bash', 'run', 'running'],
      ['mcp__notes__append', null, 'running'],
    ]
    let seq = 2
    for (const [title, did, scene] of steps) {
      pushFrame('work_phase', { ...runningState({ session: SESSION, phase: 'tool', act: did as never, line: title, seq: seq++ }) })
      await waitFor(() => expect(line()).toBe(title))
      expect(orb()?.dataset.orbScene, title).toBe(scene)
      expect(orb()?.dataset.orbPhase).toBe('tool')
      expect(head()?.dataset.phase).toBe('tool')
    }
    // Between its steps the turn thinks. The line says so at once; the orb holds the step just ended
    // until 1.2 s after it began (`ui/orb/useHeldToolStep.ts`), then thinks too.
    pushFrame('work_phase', { ...runningState({ session: SESSION, phase: 'thinking', line: 'Thinking', seq: seq++ }) })
    await waitFor(() => expect(line()).toBe('Thinking'))
    expect(head()?.dataset.phase).toBe('thinking')
    expect(orb()?.dataset.orbPhase).toBe('thinking')
    expect(orb()?.dataset.orbScene).toBe('running')
    await waitFor(() => expect(orb()?.dataset.orbScene).toBe('thinking'), { timeout: 3000 })
    pushFrame('work_phase', { ...runState({ session: SESSION, phase: 'done', line: 'Done in 9 seconds', last_outcome: 'complete', seq: seq++ }) })
    await waitFor(() => expect(line()).toBe('Done in 9 seconds'))
    expect(orb()?.dataset.orbScene).toBe('done')
  })

  it('a read over in milliseconds is still drawn reading while the line says Thinking, and the answer replaces it at once', async () => {
    await aTurnStarts()
    pushFrame('work_phase', { ...runningState({ session: SESSION, phase: 'tool', act: 'read', line: 'read_file', seq: 2 }) })
    await waitFor(() => expect(line()).toBe('read_file'))
    pushFrame('work_phase', { ...runningState({ session: SESSION, phase: 'thinking', line: 'Thinking', seq: 3 }) })
    await waitFor(() => expect(line()).toBe('Thinking'))
    expect(orb()?.dataset.orbScene).toBe('reading')
    pushFrame('work_phase', { ...runningState({ session: SESSION, phase: 'streaming', line: 'Writing', seq: 4 }) })
    await waitFor(() => expect(head()?.dataset.phase).toBe('streaming'))
    expect(orb()?.dataset.orbScene).toBe('answering')
  })

  it('a word the gateway does not use is drawn as a call that says nothing', async () => {
    await aTurnStarts()
    pushFrame('work_phase', { ...runningState({ session: SESSION, phase: 'tool', act: 'delete' as never, line: 'Delete build/', seq: 2 }) })
    await waitFor(() => expect(line()).toBe('Delete build/'))
    expect(orb()?.dataset.orbScene).toBe('running')
  })
})
