import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act, fireEvent, cleanup, within } from '@testing-library/react'

// ── A turn reads the same after a reload as it did while it ran ────────────────────────────────
//
// An agent CLI ran five of a turn's calls without asking her (its own settings allowed them) and
// asked about two, which she denied. While the turn ran, every unasked call read exactly like one
// she approved ("Tool Terminal git log … — completed"). After a reload the turn grew from 9 steps
// to 16: each "(ungated: …)" line the gateway wrote became a tool row of its own, and each denied
// call appeared a second time as "Cat n src/… (rejected) — completed", its title put through the
// tool-name humanizer (dashes gone, the path lower-cased).
//
// Now the unasked calls say so on their own cards, live and after a reload, and the reloaded turn
// is the live one: the same rows, in the same order, under the same step count. The frames and the
// rows below are what the gateway sends and persists for that turn (`chat_runner.run_chat`).

const SESSION = 'chat-1-feedsmith'
const NOTE = "Ran without asking you — allowed by Claude Code's own settings."

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

const ASKED = { role: 'user', content: 'Explain how fetch.py decides a feed is unchanged.', ts: '2026-10-01T16:50:00Z' }
const ANSWER = 'It sends the stored validators and believes the status code: only a 304 is unchanged.'

interface Call {
  id: string
  tool: string
  kind: string
  detail: string
  input: Record<string, string>
  /** Asked first, and denied: the permission's request id and the title it asked about. */
  denied?: { request: string; title: string }
}

const CAT = 'cat -n src/feedsmith/fetch.py && echo ---- && git log --oneline | head -30 && echo ---- && ls -la /home/user/.personalclaw/projects/p-bcd05d40/context 2>&1'
const LS = 'ls -la /home/user/.personalclaw/projects/p-bcd05d40/context'
const CALLS: Call[] = [
  { id: 'tc1', tool: 'Terminal', kind: 'execute', detail: CAT, input: { command: CAT }, denied: { request: 'req-1', title: CAT } },
  { id: 'tc2', tool: 'Read File', kind: 'read', detail: 'Read src/feedsmith/fetch.py', input: { file_path: 'src/feedsmith/fetch.py' } },
  { id: 'tc3', tool: 'Read File', kind: 'read', detail: 'Read CHANGELOG.md', input: { file_path: 'CHANGELOG.md' } },
  { id: 'tc4', tool: 'Terminal', kind: 'execute', detail: "git log --format='%h %ad %s' --date=short -- src/feedsmith/fetch.py", input: { command: 'git log' } },
  { id: 'tc5', tool: 'Terminal', kind: 'execute', detail: 'grep -rnIi -E 304 --exclude-dir=.git . | head -40', input: { command: 'grep' } },
  { id: 'tc6', tool: 'Read File', kind: 'read', detail: 'Read src/feedsmith/cli.py', input: { file_path: 'src/feedsmith/cli.py' } },
  { id: 'tc7', tool: 'Terminal', kind: 'execute', detail: LS, input: { command: LS }, denied: { request: 'req-2', title: LS } },
]
const DENIED_OUTPUT = "The user doesn't want to proceed with this tool use."
/** The agent's own option the Deny was sent as, which the gateway puts on the refused step's line. */
const ANSWERED = 'Answered “No”'

/** The frames the open chat receives while the turn runs, in order. */
function runLive(call: Call) {
  const preview = JSON.stringify(call.input)
  pushFrame('tool_call', { tool_call_id: call.id, tool: call.tool, kind: call.kind, purpose: '', input_preview: preview, input: call.input })
  pushFrame('tool_call', { tool_call_id: call.id, tool: call.tool, input_preview: preview, input: call.input, detail: call.detail, update: true })
  if (call.denied) {
    pushFrame('approval', {
      id: `${SESSION}:${call.denied.request}`, request_id: call.denied.request, tool: call.denied.title,
      tool_input: preview, tool_purpose: '', risk: 'destructive', is_read_only: false, grant_agent: '',
    })
    pushFrame('approval_resolved', { request_id: call.denied.request, approved: false, outcome: 'rejected' })
    // Every row the gateway appends reaches the open chat as a `chat_message` frame, this one too.
    pushFrame('chat_message', { role: 'tool', content: `${call.denied.title} (rejected)`, cls: 'msg msg-tool', meta: { detail: ANSWERED } })
    pushFrame('tool_result', { tool_call_id: call.id, output: DENIED_OUTPUT, ok: false })
    return
  }
  pushFrame('tool_result', { tool_call_id: call.id, output: 'read' })
  pushFrame('tool_call', { tool_call_id: call.id, tool: call.tool, ungated: NOTE, update: true })
  pushFrame('activity_event', { kind: 'permission', text: `Ran without host approval: ${call.tool} (Claude Code never asked)` })
}

/** The rows the gateway persisted for the same turn — what a reload is rebuilt from. */
function persisted(): unknown[] {
  const rows: unknown[] = [ASKED]
  for (const call of CALLS) {
    const meta: Record<string, unknown> = {
      tool_call_id: call.id, purpose: '', input: JSON.stringify(call.input), kind: call.kind,
      detail: call.detail, done: true,
    }
    if (call.denied) {
      rows.push({ role: 'tool', content: call.tool, cls: 'msg msg-tool', meta: { ...meta, output: DENIED_OUTPUT, ok: false } })
      const asked = {
        request_id: call.denied.request, tool_call_id: call.id, tool_input: JSON.stringify(call.input),
        is_read_only: '', risk: 'destructive', grant_agent: '', resolved: 'rejected',
      }
      rows.push({ role: 'permission', content: call.denied.title, cls: JSON.stringify(asked), meta: asked })
      rows.push({ role: 'tool', content: `${call.denied.title} (rejected)`, cls: 'msg msg-tool', meta: { detail: ANSWERED } })
    } else {
      rows.push({ role: 'tool', content: call.tool, cls: 'msg msg-tool', meta: { ...meta, output: 'read', ungated: NOTE, ungated_declared: false } })
    }
  }
  rows.push({ role: 'assistant', content: ANSWER, ts: '2026-10-01T16:53:00Z' })
  return rows
}

const detail = (messages: unknown[], running: boolean) => ({
  key: SESSION, title: SESSION, messages, running, queue: [], task_mode: 'agent',
  approval: 'normal', memory_mode: 'persistent',
})

function openChat() {
  render(
    <AppearanceProvider>
      <ChatPage sub={SESSION} navigate={() => {}} query={{}} setQuery={() => {}} />
    </AppearanceProvider>,
  )
}

/** The turn's folded work, opened as she opens it, read as the rows she sees, in order: a card by
 *  its accessible name, any other step (an approval's outcome) by its text. */
async function openTheWork(): Promise<{ fold: string; rows: string[] }> {
  const fold = await waitFor(() => screen.getByRole('button', { name: /Worked through \d+ steps/ }))
  const folded = fold.textContent ?? ''
  fireEvent.click(fold)
  const work = fold.parentElement as HTMLElement
  await waitFor(() => expect(within(work).getAllByRole('button', { name: /^Tool / }).length).toBeGreaterThan(0))
  // The opened fold lists one element per step.
  const steps = [...(fold.nextElementSibling?.firstElementChild?.children ?? [])]
  const rows = steps.map((step) =>
    step.querySelector('button[aria-label^="Tool "]')?.getAttribute('aria-label') ?? step.textContent ?? '')
  return { fold: folded, rows }
}

describe('a turn whose agent CLI ran calls without asking, and asked about two she denied', () => {
  it('reads the same after a reload as it did live: the same rows under the same step count', async () => {
    h.detail = detail([ASKED], true)
    openChat()
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeNull())
    for (const call of CALLS) runLive(call)
    pushFrame('chat_chunk', { content: ANSWER, seq: 1 })
    h.detail = detail(persisted(), false)
    pushFrame('chat_done', { outcome: 'complete' })
    const live = await openTheWork()
    cleanup()

    h.detail = detail(persisted(), false)
    openChat()
    await screen.findByText(ANSWER)
    const reloaded = await openTheWork()

    expect(live.fold).toContain('Worked through 9 steps')
    expect(reloaded.fold).toBe(live.fold)
    expect(reloaded.rows).toEqual(live.rows)
    expect(live.rows).toHaveLength(9)
  })

  it('marks each unasked call on its own card, and a denied call is one card and its denial', async () => {
    h.detail = detail(persisted(), false)
    openChat()
    await screen.findByText(ANSWER)
    const { fold, rows } = await openTheWork()

    // Folded, the turn already says how many steps ran without asking her.
    expect(fold).toContain('2 failed')
    expect(fold).toContain('5 ran without asking you')
    const unasked = rows.filter((r) => r.includes('ran without asking you'))
    expect(unasked).toHaveLength(5)
    expect(screen.getAllByText(NOTE)).toHaveLength(5)
    // The denied call: its card (the CLI reported it failed) and the line saying she denied it,
    // with the agent's own option the Deny was sent as.
    expect(rows.filter((r) => r.startsWith('Tool Terminal cat -n src/feedsmith/fetch.py'))).toHaveLength(1)
    expect(rows.filter((r) => r === `${CAT} — denied · ${ANSWERED}`)).toHaveLength(1)
    // No row is a line about a step read as a step: none says "(rejected)" or "(ungated", and
    // none carries a title put through the humanizer.
    expect(rows.filter((r) => /\((rejected|ungated)/.test(r))).toEqual([])
    expect(rows.filter((r) => r.includes('Cat n src') || r.includes('p bcd05d40'))).toEqual([])
  })
})
