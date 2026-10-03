import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act, fireEvent, cleanup, within } from '@testing-library/react'

// ── What the gateway said about a call reads the same live and after a reload ─────────────────────
//
// An agent CLI's turn: Ask mode refused one call before it ran, and another kept failing the same
// way until the loop breaker said so. Each line the gateway wrote about those calls was persisted
// with the turn and drawn nowhere, live or after a reload, so neither the refusal nor the warning
// ever reached her. The line saying what fed the turn showed only while the turn ran, twice: in
// its footer, and as the first line of its work. And the turn's telemetry, which a reload's footer
// shows, never reached the live footer of a turn that called tools. Now each note is on its call's
// card, and the footer says what fed the turn and its telemetry, the same live as after a reload.
// The frames and rows below are what the gateway sends and persists for that turn
// (`chat_runner.run_chat`, `dashboard/step_notes.py`).

const SESSION = 'chat-1-notes'
const NOT_RUN = 'Not run: Ask mode — only read-only tools run (switch to Agent to make changes).'
const FAILING = 'Failed 3 times in a row with the same arguments.'
const FED = 'Injected 1,204 chars of context (memory, lessons, history, episodic)'
const STATS = 'Turn complete · 4 tool calls · 12 events'

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

// ── the turn ────────────────────────────────────────────────────────────────────────────────

const ASKED = { role: 'user', content: 'Tidy the notes folder, then run the tests.', ts: '2026-10-02T09:00:00Z' }
const ANSWER = 'Ask mode kept me from writing the file, and the tests keep failing the same way.'

interface Call { id: string; tool: string; kind: string; detail: string; failed: boolean; note?: string }

const CALLS: Call[] = [
  { id: 'tc1', tool: 'Write File', kind: 'edit', detail: 'notes/index.md', failed: true, note: NOT_RUN },
  { id: 'tc2', tool: 'Terminal', kind: 'execute', detail: 'make test', failed: true },
  { id: 'tc3', tool: 'Terminal', kind: 'execute', detail: 'make test', failed: true },
  { id: 'tc4', tool: 'Terminal', kind: 'execute', detail: 'make test', failed: true, note: FAILING },
]

/** The line the gateway writes about a call, as its row: what the frame and the reload both carry. */
const lineAbout = (c: Call) => ({ role: 'tool', content: `${c.tool} — ${c.note}`, cls: 'msg msg-tool', meta: { about_call: c.id, note: c.note } })

function runLive(c: Call) {
  pushFrame('tool_call', { tool_call_id: c.id, tool: c.tool, kind: c.kind, purpose: '', input_preview: '{}', detail: c.detail })
  // Ask mode refuses the call before it runs: the line arrives before the CLI reports its result.
  if (c.note === NOT_RUN) pushFrame('chat_message', lineAbout(c))
  pushFrame('tool_result', { tool_call_id: c.id, output: 'failed', ok: c.failed ? false : undefined })
  // The breaker says so once the result is in.
  if (c.note === FAILING) pushFrame('chat_message', lineAbout(c))
}

function persisted(): unknown[] {
  const rows: unknown[] = [ASKED]
  for (const c of CALLS) {
    rows.push({
      role: 'tool', content: c.tool, cls: 'msg msg-tool',
      meta: { tool_call_id: c.id, purpose: '', input: '{}', kind: c.kind, detail: c.detail, done: true, output: 'failed', ...(c.failed ? { ok: false } : {}) },
    })
    if (c.note) rows.push(lineAbout(c))
  }
  rows.push({ role: 'assistant', content: ANSWER, ts: '2026-10-02T09:01:00Z', meta: { context_fed: FED, turn_telemetry: { line: STATS } } })
  return rows
}

const detail = (messages: unknown[], running: boolean) => ({
  key: SESSION, title: SESSION, messages, running, queue: [], task_mode: 'ask',
  approval: 'normal', memory_mode: 'persistent',
})

function openChat() {
  render(
    <AppearanceProvider>
      <ChatPage sub={SESSION} navigate={() => {}} query={{}} setQuery={() => {}} />
    </AppearanceProvider>,
  )
}

/** The turn as she reads it: its folded work opened, each step by its accessible name (a card) or
 *  its text, and the footer that says what fed the turn. */
async function readTheTurn(): Promise<{ fold: string; rows: string[]; footer: string }> {
  const fold = await waitFor(() => screen.getByRole('button', { name: /Worked through \d+ steps/ }))
  const folded = fold.textContent ?? ''
  fireEvent.click(fold)
  const work = fold.parentElement as HTMLElement
  await waitFor(() => expect(within(work).getAllByRole('button', { name: /^Tool / }).length).toBeGreaterThan(0))
  const steps = [...(fold.nextElementSibling?.firstElementChild?.children ?? [])]
  const rows = steps.map((step) =>
    step.querySelector('button[aria-label^="Tool "]')?.getAttribute('aria-label') ?? step.textContent ?? '')
  const footer = screen.getByRole('button', { name: /recalled context|Turn details|telemetry/ }).textContent ?? ''
  return { fold: folded, rows, footer }
}

describe('a turn whose agent CLI was refused one call and kept failing another', () => {
  it('reads the same after a reload: the notes on their cards, the footer saying what fed it', async () => {
    h.detail = detail([ASKED], true)
    openChat()
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeNull())
    pushFrame('activity_event', { kind: 'context', text: FED })
    for (const c of CALLS) runLive(c)
    pushFrame('chat_chunk', { content: ANSWER, seq: 1 })
    // The turn's telemetry, said as it ends: a turn of tool calls kept it out of its footer.
    pushFrame('activity_event', { kind: 'stats', text: STATS })
    h.detail = detail(persisted(), false)
    pushFrame('chat_done', { outcome: 'complete' })
    const live = await readTheTurn()
    expect(screen.getAllByText(NOT_RUN)).toHaveLength(1)
    expect(screen.getAllByText(FAILING)).toHaveLength(1)
    cleanup()

    h.detail = detail(persisted(), false)
    openChat()
    await screen.findByText(ANSWER)
    const reloaded = await readTheTurn()
    expect(screen.getAllByText(NOT_RUN)).toHaveLength(1)
    expect(screen.getAllByText(FAILING)).toHaveLength(1)

    expect(live.fold).toContain('Worked through 4 steps')
    expect(reloaded.fold).toBe(live.fold)
    expect(reloaded.rows).toEqual(live.rows)
    expect(live.rows).toEqual([
      'Tool Write file notes/index.md — failed, not run: Ask mode — only read-only tools run (switch to Agent to make changes). Expand details',
      'Tool Terminal make test — failed. Expand details',
      'Tool Terminal make test — failed. Expand details',
      'Tool Terminal make test — failed, failed 3 times in a row with the same arguments. Expand details',
    ])
    expect(live.footer).toBe('recalled context · telemetry')
    expect(reloaded.footer).toBe(live.footer)
  })
})
