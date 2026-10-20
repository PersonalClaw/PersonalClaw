import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act, fireEvent, cleanup } from '@testing-library/react'
import { runState, runningState } from '../../test/runState'

// ── A finished turn's work folds into one line that says how long the turn worked ──────────────
//
// The fold read "Worked through 6 steps · Terminal, Read File" and nothing said how long the turn
// took. It now reads "Worked for 12s · 6 steps", and names no tool. The time is the turn's own: from
// when the gateway started it to when it said it ended (the `run_state` its `work_phase` frames
// carry), and where a page never heard that, as after a reload, the time the gateway measured and
// stored with the turn's answer (`turn_telemetry.duration_ms`). Only a turn stored without one is
// timed from its first row to its last. While the turn runs its steps stay open; once it has ended
// they fold, and a click opens them again.

const SESSION = 'chat-1-export'
const ASKED = { role: 'user', content: 'Find why the nightly export is slow and fix it.', ts: '2026-10-04T09:00:00.100000+00:00' }
const ANSWER = 'The export re-read the whole table for every page; it now reads it once.'
/** When the gateway started the turn, and when its last frame said it ended: 12.1 s. */
const STARTED = '2026-10-04T09:00:00.200000+00:00'
const ENDED = '2026-10-04T09:00:12.300000+00:00'

interface Call { id: string; tool: string; kind: string; detail: string; at: string }
const CALLS: Call[] = [
  { id: 'tc1', tool: 'Read File', kind: 'read', detail: 'Read export/nightly.py', at: '2026-10-04T09:00:02+00:00' },
  { id: 'tc2', tool: 'Terminal', kind: 'execute', detail: 'make bench-export', at: '2026-10-04T09:00:03+00:00' },
  { id: 'tc3', tool: 'Read File', kind: 'read', detail: 'Read export/pages.py', at: '2026-10-04T09:00:05+00:00' },
  { id: 'tc4', tool: 'Edit File', kind: 'edit', detail: 'Edit export/pages.py', at: '2026-10-04T09:00:06+00:00' },
  { id: 'tc5', tool: 'Terminal', kind: 'execute', detail: 'make bench-export', at: '2026-10-04T09:00:07+00:00' },
  { id: 'tc6', tool: 'Terminal', kind: 'execute', detail: 'make test', at: '2026-10-04T09:00:09+00:00' },
]
/** The settled answer's row: the last the turn wrote. Its rows run from 09:00:02 to here, 10.25 s. */
const ANSWERED_AT = '2026-10-04T09:00:12.250000+00:00'
/** What the gateway measured for the turn and stored with its answer row: 14.2 s, said "14s", so a
 *  line that reads it is told apart from the run state's 12 s and the rows' 10 s. */
const STORED_MS = 14_200

const h = vi.hoisted(() => ({ detail: null as unknown, chatSessionDetail: vi.fn() }))

vi.mock('../../lib/api', () => {
  const ok = () => Promise.resolve([])
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
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
  sessionStorage.clear()
  localStorage.clear()
  FakeSocket.last = null
  h.chatSessionDetail.mockReset().mockImplementation(() => Promise.resolve(h.detail))
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

/** The rows the gateway persisted for the turn, each with the time it was written, and the answer's
 *  row with the time the gateway measured for the turn (`stored`), as it writes it. */
function persisted({ timed = true, stored }: { timed?: boolean; stored?: number } = {}): unknown[] {
  const at = (ts: string) => (timed ? { ts } : {})
  const rows: unknown[] = [ASKED]
  for (const c of CALLS) {
    rows.push({
      role: 'tool', content: c.tool, cls: 'msg msg-tool', ...at(c.at),
      meta: { tool_call_id: c.id, purpose: '', input: '{}', kind: c.kind, detail: c.detail, done: true, output: 'ok' },
    })
  }
  const telemetry = stored === undefined ? {} : {
    meta: { turn_telemetry: { duration_ms: stored, line: 'Turn complete: 13 events, 6 tool calls', model: '', chosen: '' } },
  }
  rows.push({ role: 'assistant', content: ANSWER, ...at(ANSWERED_AT), ...telemetry })
  return rows
}

/** The chat's run state while the turn runs: the turn named by the row that started it. */
const running = runningState({ session: SESSION, turn: { message_id: ASKED.ts, started_at: STARTED } })
const detail = (messages: unknown[], live: boolean) => ({
  key: SESSION, title: SESSION, messages, run_state: live ? running : runState({ session: SESSION }), queue: [], task_mode: 'agent',
  approval: 'normal', memory_mode: 'persistent',
})

function openChat() {
  render(
    <AppearanceProvider>
      <ChatPage sub={SESSION} navigate={() => {}} query={{}} setQuery={() => {}} />
    </AppearanceProvider>,
  )
}

const toolCards = () => screen.queryAllByRole('button', { name: /^Tool / })

/** Every step of the turn, as the gateway sends it while the turn runs. */
function runTheTurn() {
  for (const c of CALLS) {
    pushFrame('tool_call', { tool_call_id: c.id, tool: c.tool, kind: c.kind, purpose: '', input_preview: '{}', detail: c.detail })
    pushFrame('tool_result', { tool_call_id: c.id, output: 'ok' })
  }
  pushFrame('chat_chunk', { content: ANSWER, seq: 1 })
}

describe('a finished turn says how long it worked on its folded line', () => {
  it('keeps the steps open while the turn runs, then folds them under the time the gateway measured', async () => {
    h.detail = detail([ASKED], true)
    openChat()
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeNull())
    runTheTurn()
    // Running, the turn's steps are open, one card each, and nothing is folded yet.
    expect(toolCards()).toHaveLength(6)
    expect(screen.queryByRole('button', { name: /Worked/ })).toBeNull()

    // The turn's last frame says it ended, then `chat_done`; the rows read after it say 10 s and
    // the time stored with them 14 s, and the gateway's own start and end on this page (12.1 s) are
    // what the line says: the live page's time is as it was.
    pushFrame('work_phase', { ...running, phase: 'done', line: 'Done in 12 seconds', since: ENDED, turn: null, last_outcome: 'complete', seq: 2 })
    h.detail = detail(persisted({ stored: STORED_MS }), false)
    pushFrame('chat_done', { outcome: 'complete' })

    const fold = await waitFor(() => screen.getByRole('button', { name: /Worked for 12s · 6 steps/ }))
    expect(fold.getAttribute('aria-expanded')).toBe('false')
    expect(toolCards()).toHaveLength(0)
    // Folded, it is one line: the time and the step count, and no tool's name.
    expect(fold.textContent).toBe('Worked for 12s · 6 steps')
    for (const c of CALLS) expect(fold.textContent).not.toContain(c.tool)

    fireEvent.click(fold)
    expect(fold.getAttribute('aria-expanded')).toBe('true')
    await waitFor(() => expect(toolCards()).toHaveLength(6))
    // Once the read after `chat_done` has landed, the line still says the gateway's time: the
    // rows' own 10 s do not replace it.
    await waitFor(() => expect(h.chatSessionDetail).toHaveBeenCalledTimes(2))
    await act(async () => { await h.chatSessionDetail.mock.results[1].value })
    fireEvent.click(fold)
    expect(fold.textContent).toBe('Worked for 12s · 6 steps')
  })

  it('reads the stored time when the page never heard the turn end', async () => {
    h.detail = detail([ASKED], true)
    openChat()
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeNull())
    runTheTurn()
    // No closing `work_phase` frame reached this page: the read after `chat_done` times the turn.
    h.detail = detail(persisted({ stored: STORED_MS }), false)
    pushFrame('chat_done', { outcome: 'complete' })
    const fold = await screen.findByRole('button', { name: /Worked for 14s · 6 steps/ })
    expect(fold.textContent).toBe('Worked for 14s · 6 steps')
  })

  it('times a turn from its rows when the page never heard it end and nothing was stored', async () => {
    h.detail = detail([ASKED], true)
    openChat()
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeNull())
    runTheTurn()
    h.detail = detail(persisted(), false)
    pushFrame('chat_done', { outcome: 'complete' })
    expect(await screen.findByRole('button', { name: /Worked for 10s · 6 steps/ })).toBeTruthy()
  })

  it('after a reload, says the time the gateway stored for the turn, not its rows\' span', async () => {
    h.detail = detail(persisted({ stored: STORED_MS }), false)
    openChat()
    await screen.findByText(ANSWER)
    const fold = await waitFor(() => screen.getByRole('button', { name: /Worked for 14s · 6 steps/ }))
    expect(fold.getAttribute('aria-expanded')).toBe('false')
    expect(fold.textContent).toBe('Worked for 14s · 6 steps')
    expect(toolCards()).toHaveLength(0)
  })

  it('after a reload, times a turn stored without its time from its first row to its last', async () => {
    h.detail = detail(persisted(), false)
    openChat()
    await screen.findByText(ANSWER)
    const fold = await waitFor(() => screen.getByRole('button', { name: /Worked for 10s · 6 steps/ }))
    expect(fold.getAttribute('aria-expanded')).toBe('false')
    expect(toolCards()).toHaveLength(0)
  })

  it('says no time for a turn whose rows carry none, rather than a made-up one', async () => {
    h.detail = detail(persisted({ timed: false }), false)
    openChat()
    await screen.findByText(ANSWER)
    const fold = await waitFor(() => screen.getByRole('button', { name: /Worked through 6 steps/ }))
    expect(fold.textContent).toBe('Worked through 6 steps')
  })
})
