import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act, fireEvent, within } from '@testing-library/react'
import { runState } from '../../../test/runState'

// ── A chat's mode controls follow the gateway ──────────────────────────────────────────────────
//
// The composer's permission and task mode controls showed the chat's mode from its own read when it
// opened, and from the answers to their own picks. A mode set anywhere else (another tab or device
// picking for the same chat, YOLO turned on from another chat, YOLO lapsing) showed only once the
// chat was opened again, so a control could show a narrower mode than the chat ran in. The chat
// list the gateway pushes (`sessions`) now says each chat's mode, YOLO included, and an open
// composer shows it, unless a pick of its own on that control is still unanswered.
//
// The page is the chat as it is drawn, over a gateway stood in at `fetch`, its frames delivered
// through the page's socket.

const SESSION = 'chat-9-edge'

const h = vi.hoisted(() => ({
  calls: [] as Array<[string, unknown[]]>,
  real: {} as Record<string, unknown>,
  detail: {} as Record<string, unknown>,
  /** Reads of the chat held back until the test answers them, each with what it read then. */
  holdReads: false,
  /** How many reads of the chat fail before one answers. */
  readFails: 0,
  heldReads: [] as Array<() => void>,
}))

vi.mock('../../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../../lib/api')>()
  h.real = real.api as unknown as Record<string, unknown>
  const answers: Record<string, (...args: unknown[]) => unknown> = {
    chatSessionDetail: () => {
      if (h.readFails > 0) { h.readFails--; return Promise.reject(new Error('the gateway is restarting')) }
      // What the gateway took is what the chat's own read says, as on the real one.
      const read = { ...h.detail, ...(gw.mode ? { approval: gw.mode } : {}), ...(gw.task ? { task_mode: gw.task } : {}) }
      return h.holdReads ? new Promise((resolve) => { h.heldReads.push(() => resolve(read)) }) : Promise.resolve(read)
    },
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

/** A request as it left the page. */
interface Sent { method: string; path: string; body: unknown }

/** The gateway as these tests stand it in. It takes each mode request as it arrives and answers it
 *  at once, unless the test says otherwise: `silent` requests are never answered (until the test
 *  answers one late with `late`), and a mode in `refuse` is refused with `REFUSAL`. */
const gw = {
  sent: [] as Sent[],
  mode: '',
  task: '',
  silent: 0,
  late: [] as Array<() => void>,
  hold: false,
  held: [] as Array<() => void>,
  refuse: new Set<string>(),
  /** Requests the gateway takes as they arrive and answers only when the test says (`late`). */
  slow: 0,
  /** Modes the gateway takes and then answers with a server error, as a lost answer would. */
  takeThenFail: new Set<string>(),
  /** The number of the last pick taken, per path: the gateway refuses one numbered no higher. */
  last: {} as Record<string, number>,
}

const REFUSAL = 'An operator ceiling holds this chat at Normal.'
const STALE = 'A later pick of this mode was already taken, so this older one changed nothing.'

function fakeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const method = (init?.method ?? 'GET').toUpperCase()
  const path = String(input)
  const body = typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body ?? null
  const json = (status: number, b: unknown) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } })
  if (method === 'GET') return Promise.resolve(json(200, { ok: true }))
  gw.sent.push({ method, path, body })
  const { mode, pick } = body as { mode: string; pick?: number }
  const take = () => { if (path === '/api/chat/mode') gw.mode = mode; else gw.task = mode }
  // Judged when the request arrives, as the gateway judges it: a pick numbered no higher than the
  // last one taken on its control is stale and changes nothing.
  const answer = (): Response => {
    const last = gw.last[path] ?? -1
    if (typeof pick === 'number' && pick <= last) {
      return json(409, { error: { code: 'stale_pick', message: STALE, detail: { last } } })
    }
    if (gw.refuse.has(mode)) return json(409, { ok: false, error: REFUSAL })
    if (gw.takeThenFail.has(mode)) {
      if (typeof pick === 'number') gw.last[path] = pick
      take()
      return json(502, { error: { code: 'upstream', message: 'The answer was lost on its way back.' } })
    }
    if (typeof pick === 'number') gw.last[path] = pick
    take()
    return json(200, { ok: true })
  }
  if (gw.silent > 0) {
    gw.silent--
    return new Promise((resolve) => { gw.late.push(() => resolve(answer())) })
  }
  if (gw.slow > 0) {
    gw.slow--
    const answered = answer()
    return new Promise((resolve) => { gw.late.push(() => resolve(answered)) })
  }
  if (gw.hold) return new Promise((resolve) => { gw.held.push(() => resolve(answer())) })
  return Promise.resolve(answer())
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
  h.holdReads = false
  h.heldReads.length = 0
  h.readFails = 0
  Object.assign(gw, { sent: [], mode: '', task: '', silent: 0, slow: 0, late: [], hold: false, held: [], refuse: new Set<string>(), takeThenFail: new Set<string>(), last: {} })
  ;({ ChatPage } = await import('../../ChatPage'))
  ;({ AppearanceProvider } = await import('../../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

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


const pillSays = (axis: Axis) => (trigger(axis).getAttribute('aria-label') ?? trigger(axis).textContent ?? '')

/** The chat list as the gateway pushes it, this chat's row saying *mode*. */
async function listSays(row: Record<string, unknown>) {
  await waitFor(() => expect(FakeSocket.last).not.toBeNull())
  act(() => {
    FakeSocket.last?.onmessage?.({ data: JSON.stringify({ type: 'sessions', data: [{ key: 'chat-other', approval: 'normal', task_mode: 'agent' }, { key: SESSION, ...row }] }) })
  })
  await settle()
}

/** The owner-only refresh the gateway sends as YOLO turns on or off, or runs out. */
async function yoloRings() {
  await waitFor(() => expect(FakeSocket.last).not.toBeNull())
  act(() => {
    FakeSocket.last?.onmessage?.({ data: JSON.stringify({ type: 'refresh', data: { kinds: ['yolo'] } }) })
  })
  await settle()
}

describe('a mode set elsewhere', () => {
  it('🔴 shows on the open chat\'s controls without a reload', async () => {
    await open()
    expect(pillSays('permission')).toMatch(/: Normal$/)

    await listSays({ approval: 'trust', task_mode: 'plan' })

    expect(pillSays('permission')).toMatch(/: Trust$/)
    expect(pillSays('task')).toMatch(/: Plan$/)
  })

  it('🔴 YOLO, which holds for every chat, shows on this chat\'s control too', async () => {
    await open()

    await listSays({ approval: 'yolo', task_mode: 'agent' })

    expect(pillSays('permission')).toMatch(/: YOLO$/)
  })

  it('🔴 YOLO turning on or running out, which no list says, has the control read its chat again', async () => {
    await open()
    const reads = () => h.calls.filter(([p]) => p === 'chatSessionDetail').length
    const before = reads()

    gw.mode = 'yolo'
    await yoloRings()
    expect(reads(), 'the chat was read again').toBe(before + 1)
    expect(pillSays('permission')).toMatch(/: YOLO$/)

    gw.mode = 'normal'
    await yoloRings()
    expect(pillSays('permission')).toMatch(/: Normal$/)
  })

  it('does not cover a pick of its own still unanswered, which shows once the gateway takes it', async () => {
    await open()
    gw.slow = 1
    await pick('permission', 'Trust')

    await listSays({ approval: 'yolo', task_mode: 'agent' })
    expect(pillSays('permission'), 'the list is not shown over the pick still waiting').not.toMatch(/: YOLO$/)

    await act(async () => { gw.late.splice(0).forEach((answer) => answer()) })
    await settle()
    expect(pillSays('permission')).toMatch(/: Trust$/)
  })
})
