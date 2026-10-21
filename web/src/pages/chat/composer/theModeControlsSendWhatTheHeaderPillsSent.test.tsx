import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act, fireEvent, within } from '@testing-library/react'
import { runState } from '../../../test/runState'

// ── The chat's mode controls send what the header's pills sent, and nothing more ─────────────────
//
// The permission mode and the task mode left the chat header for the composer's footer. What they
// send to the gateway did not change: for every mode, picked from a chat in another mode or picked
// again from a chat already in it, one request, the same method, path and body the header's pills
// sent, and no other write, each numbered for the chat so the gateway can refuse a pick older than
// one it took (`stale_pick`). Picking the mode a chat is in still asks for it again: that is how YOLO,
// which expires, is extended ("re-enable to extend"). In an app's conversation nothing is sent.
//
// The controls are found by what they are named on either side of the move (the header's
// "Permission mode" pill, or the footer's Execution Boundary that holds the permission mode), so
// this file reads the same on the commit before the move as after it. Each request is read where it
// leaves the page, at `fetch`, and every other api write is recorded beside it.

const SESSION = 'chat-9-edge'

const h = vi.hoisted(() => ({
  calls: [] as Array<[string, unknown[]]>,
  real: {} as Record<string, unknown>,
  detail: {} as Record<string, unknown>,
}))

vi.mock('../../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../../lib/api')>()
  h.real = real.api as unknown as Record<string, unknown>
  const answers: Record<string, (...args: unknown[]) => unknown> = {
    chatSessionDetail: () => Promise.resolve({ ...h.detail }),
    chatBoundary: () => Promise.resolve({
      sandbox: { confined: true, label: 'Sandboxed', said: 'The commands it runs are sandboxed on this computer.' },
      reach: { tier: 'all', label: 'Network on', said: 'Its web tools reach public hosts.' },
    }),
    // The two writes under test go out through the real api, so their request is the real one.
    setApprovalMode: (...a: unknown[]) => (h.real.setApprovalMode as (...x: unknown[]) => unknown)(...a),
    setTaskMode: (...a: unknown[]) => (h.real.setTaskMode as (...x: unknown[]) => unknown)(...a),
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

/** A request as it left the page. */
interface Sent { method: string; path: string; body: unknown }

/** The gateway as these tests stand it in: it takes each mode request as it arrives, and answers
 *  when `answer` says (at once, unless a test holds the answers back). */
const gw = {
  sent: [] as Sent[],
  mode: '',
  hold: false,
  held: [] as Array<() => void>,
}

function fakeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const method = (init?.method ?? 'GET').toUpperCase()
  const path = String(input)
  const body = typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body ?? null
  if (method !== 'GET') gw.sent.push({ method, path, body })
  if (path === '/api/chat/mode') gw.mode = (body as { mode: string }).mode
  const ok = () => new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  if (gw.hold && method !== 'GET') return new Promise((resolve) => { gw.held.push(() => resolve(ok())) })
  return Promise.resolve(ok())
}

let ChatPage: typeof import('../../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../../app/appearance')['AppearanceProvider']

const chat = (over: Record<string, unknown> = {}) => ({
  key: SESSION, title: 'Tidy the parser', run_state: runState({ session: SESSION }), queue: [],
  task_mode: 'agent', approval: 'normal', memory_mode: 'persistent', workspace_dir: '/home/user/src/parser',
  messages: [
    { role: 'user', content: 'Which tests fail?', ts: '2026-10-06T09:00:00+00:00' },
    { role: 'assistant', content: 'Two of them, both in the parser.', ts: '2026-10-06T09:00:05+00:00' },
  ],
  ...over,
})

beforeEach(async () => {
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  vi.stubGlobal('fetch', vi.fn(fakeFetch))
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
  h.detail = chat()
  Object.assign(gw, { sent: [], mode: '', hold: false, held: [] })
  ;({ ChatPage } = await import('../../ChatPage'))
  ;({ AppearanceProvider } = await import('../../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

/** Whether the real api's `name` only reads: it goes through the gateway's `get` and no writer. */
const onlyReads = (name: string) => {
  const source = String(h.real[name])
  return /\bget[<(]/.test(source) && !/\b(post|put|patch|del|write)[<(]/.test(source)
}
/** Every api write the page made, by name, with its string arguments. */
const writes = () => h.calls.filter(([name]) => !onlyReads(name)).map(([name, args]) => [name, args.filter((a) => typeof a === 'string')])
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 60)) })

async function open() {
  render(<AppearanceProvider><ChatPage sub={SESSION} navigate={() => {}} query={{}} setQuery={() => {}} /></AppearanceProvider>)
  await screen.findByText(/Two of them, both in the parser/)
  await settle()
}

/** The control that holds an axis, under either name it has had, and the menu it opens. */
const AXES = {
  permission: { trigger: /^(Execution boundary|Permission mode):/, menu: /^(Execution boundary|Permission mode)$/ },
  task: { trigger: /^Task mode:/, menu: /^Task mode$/ },
} as const
type Axis = keyof typeof AXES
const trigger = (axis: Axis) => screen.getByRole('button', { name: AXES[axis].trigger })

/** Pick `label` from the control of `axis` as a pointer does: open it, choose the row. A control
 *  that does not open, or a row that cannot be chosen, picks nothing. */
async function pick(axis: Axis, label: string) {
  fireEvent.click(trigger(axis))
  await settle()
  const menu = screen.queryByRole('menu', { name: AXES[axis].menu })
  if (menu) {
    const row = within(menu).queryByRole('menuitemradio', { name: label })
    if (row) fireEvent.click(row)
  }
  await settle()
}

const PERMISSION = [['normal', 'Normal'], ['trust_reads', 'Trust reads'], ['trust', 'Trust'], ['yolo', 'YOLO']] as const
const TASK = [['agent', 'Agent'], ['ask', 'Ask'], ['plan', 'Plan'], ['build', 'Build']] as const
/** Each mode, picked from another mode and picked again from itself. */
const fromEach = <T extends readonly (readonly [string, string])[]>(modes: T, other: (id: string) => string) =>
  modes.flatMap(([id, label]) => [[id, label, other(id)], [id, label, id]] as const)

describe('the permission mode', () => {
  it.each(fromEach(PERMISSION, (id) => (id === 'normal' ? 'trust' : 'normal')))(
    'picking %s (%s) in a chat on %s sends POST /api/chat/mode with it, and nothing else',
    async (id, label, from) => {
      h.detail = chat({ approval: from })
      await open()
      const before = { sent: gw.sent.length, writes: writes().length }
      await pick('permission', label)
      await waitFor(() => expect(gw.sent.slice(before.sent)).toHaveLength(1))
      await settle()
      expect(gw.sent.slice(before.sent)).toEqual([{ method: 'POST', path: '/api/chat/mode', body: { mode: id, session: SESSION, pick: expect.any(Number) } }])
      expect(writes().slice(before.writes)).toEqual([['setApprovalMode', [id, SESSION]]])
      expect(trigger('permission')).toHaveAccessibleName(new RegExp(`: ${label}$`))
    },
  )

  it.each(PERMISSION)('in an app’s conversation, picking %s sends nothing', async (_id, label) => {
    h.detail = chat({ created_by_app: 'inbox-triage', created_by_app_name: 'Inbox Triage' })
    await open()
    const before = { sent: gw.sent.length, writes: writes().length }
    await pick('permission', label)
    expect(gw.sent.slice(before.sent)).toEqual([])
    expect(writes().slice(before.writes)).toEqual([])
  })

  it('a pick made while the last is unanswered leaves the mode the gateway was last asked for', async () => {
    await open()
    gw.hold = true
    await pick('permission', 'Trust')
    await pick('permission', 'Normal')
    // Answered last first, the worst order: the control still ends on the mode the gateway holds.
    while (gw.held.length) {
      await act(async () => { gw.held.pop()!() })
      await settle()
    }
    await settle()
    expect(gw.mode).toBe('normal')
    expect(trigger('permission')).toHaveAccessibleName(/: Normal$/)
  })
})

describe('the task mode', () => {
  it.each(fromEach(TASK, (id) => (id === 'agent' ? 'ask' : 'agent')))(
    'picking %s (%s) in a chat on %s sends POST /api/chat/task-mode with it, and nothing else',
    async (id, label, from) => {
      h.detail = chat({ task_mode: from })
      await open()
      const before = { sent: gw.sent.length, writes: writes().length }
      await pick('task', label)
      await waitFor(() => expect(gw.sent.slice(before.sent)).toHaveLength(1))
      await settle()
      expect(gw.sent.slice(before.sent)).toEqual([{ method: 'POST', path: '/api/chat/task-mode', body: { mode: id, session: SESSION, pick: expect.any(Number) } }])
      expect(writes().slice(before.writes)).toEqual([['setTaskMode', [id, SESSION]]])
    },
  )
})
