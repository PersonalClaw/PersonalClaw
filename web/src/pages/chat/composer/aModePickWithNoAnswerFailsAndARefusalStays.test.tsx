import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act, fireEvent, within } from '@testing-library/react'
import { runState } from '../../../test/runState'

// ── A mode pick the gateway never answers fails visibly, and a refusal stays until it is seen ────
//
// The composer sends one permission or task mode pick at a time, so the gateway takes them in the
// order they were made, and shows a pick only once the gateway has taken it (#3808). Two things
// followed from that, and this file holds the answer to each:
//
// - A pick whose request is never answered held every later pick on both controls until a reload,
//   with nothing said. Now a pick waits `MODE_PICK_DEADLINE_MS` (`useChatComposer`) at most: then the control says the
//   gateway did not answer and that the chat may not have changed mode, keeps the mode the gateway
//   last took, and the next pick goes out. If the answer comes after all and no pick was made since,
//   the control shows the mode the gateway took.
// - A pick given up on could still reach the gateway after a newer pick of the same control, and put
//   the chat back in the older mode while the control showed the newer one. Now each pick is
//   numbered, increasing for the chat, and the gateway refuses one numbered no higher than the last
//   it took (`stale_pick`); after a wait given up on, or such a refusal, the control reads the
//   chat's mode back from the gateway. The gateway here judges the numbers as the real one does.
// - A refusal beside a control was cleared by the answer to a pick made before the refusal came
//   back, so its reason could go unseen. Now a refusal stays until the control is next opened or a
//   mode is picked again; an answer to an earlier pick never clears it.
//
// The page is the chat as it is drawn, over a gateway stood in at `fetch`.

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
/** How long a pick waits for the gateway (read with the page, after the globals it reads are stood in). */
let deadline = 0
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
  // A minute where no wait is set: long past any wait a person would sit through.
  deadline = (await import('./useChatComposer') as { MODE_PICK_DEADLINE_MS?: number }).MODE_PICK_DEADLINE_MS ?? 60_000
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


/** Run the clock past a pick's deadline, as a gateway that never answers would. */
async function pastTheDeadline() {
  await act(async () => { vi.advanceTimersByTime(deadline + 50) })
  await settle()
}

const pillSays = (axis: Axis) => (trigger(axis).getAttribute('aria-label') ?? trigger(axis).textContent ?? '')
const alerts = () => screen.queryAllByRole('alert').map((a) => a.textContent ?? '')

/** The chat open, with the clock under the test's hand from here on: a pick's wait starts on it. */
async function openWithTheClock() {
  await open()
  vi.useFakeTimers({ shouldAdvanceTime: true })
}
afterEach(() => { vi.useRealTimers() })

describe('a mode pick the gateway never answers', () => {
  it.each([['permission', 'Trust', 'Trust reads', '/api/chat/mode'], ['task', 'Plan', 'Ask', '/api/chat/task-mode']] as const)(
    'on the %s control, says so once its wait is over, keeps the mode, and lets the next pick go',
    async (axis, first, second, path) => {
      await openWithTheClock()
      gw.silent = 1
      await pick(axis, first)
      expect(gw.sent.filter((s) => s.path === path)).toHaveLength(1)
      expect(alerts().join(' '), 'nothing is said before the wait is over').not.toMatch(/answer/i)
      await pastTheDeadline()
      expect(alerts().join(' ')).toMatch(/gateway didn.t answer/i)
      expect(pillSays(axis), 'the control keeps the mode the gateway last took').not.toMatch(new RegExp(`: ${first}$`))
      await pick(axis, second)
      await waitFor(() => expect(gw.sent.filter((s) => s.path === path)).toHaveLength(2))
      await settle()
      expect(gw.sent.filter((s) => s.path === path).map((s) => (s.body as { mode: string }).mode)).toEqual(
        [first.toLowerCase(), second.toLowerCase().replace(' ', '_')],
      )
      expect(pillSays(axis)).toMatch(new RegExp(`: ${second}$`))
    },
  )

  it('answered after its wait, with no pick made since, shows the mode the gateway took', async () => {
    await openWithTheClock()
    gw.silent = 1
    await pick('permission', 'Trust')
    await pastTheDeadline()
    expect(pillSays('permission')).toMatch(/: Normal$/)
    await act(async () => { gw.late.pop()!() })
    await settle()
    expect(gw.mode).toBe('trust')
    expect(pillSays('permission'), 'the gateway took Trust, so the control must say so').toMatch(/: Trust$/)
  })

  it('answered after a later pick was taken, leaves the later pick shown', async () => {
    await openWithTheClock()
    gw.silent = 1
    await pick('permission', 'Trust')
    await pastTheDeadline()
    await pick('permission', 'Trust reads')
    await waitFor(() => expect(pillSays('permission')).toMatch(/: Trust reads$/))
    await act(async () => { gw.late.pop()!() })
    await settle()
    expect(pillSays('permission')).toMatch(/: Trust reads$/)
  })
  it('answered after its wait, shows the mode the gateway took though the other control was picked since', async () => {
    await openWithTheClock()
    gw.silent = 1
    await pick('permission', 'YOLO')
    await pastTheDeadline()
    await pick('task', 'Plan')
    await waitFor(() => expect(pillSays('task')).toMatch(/: Plan$/))
    await act(async () => { gw.late.pop()!() })
    await settle()
    expect(gw.mode).toBe('yolo')
    expect(pillSays('permission'), 'a task mode pick says nothing about the permission mode').toMatch(/: YOLO$/)
  })

  it('answered after its wait, shows the mode the gateway took though a later pick on it was refused', async () => {
    await openWithTheClock()
    gw.refuse.add('yolo')
    gw.silent = 1
    await pick('permission', 'Trust')
    await pastTheDeadline()
    await pick('permission', 'YOLO')
    expect(alerts().join(' ')).toContain(REFUSAL)
    await act(async () => { gw.late.pop()!() })
    await settle()
    expect(gw.mode).toBe('trust')
    expect(pillSays('permission'), 'the refused pick took nothing, so Trust is what the gateway holds').toMatch(/: Trust$/)
  })
})

describe('a pick that reaches the gateway after a newer one', () => {
  it('changes nothing: the chat ends in the newer mode, which the control shows', async () => {
    await openWithTheClock()
    gw.silent = 1
    await pick('permission', 'Trust')
    await pastTheDeadline()
    await pick('permission', 'Normal')
    await waitFor(() => expect(gw.last['/api/chat/mode']).toBeGreaterThan(0))
    // The Trust request arrives now, after Normal was taken.
    await act(async () => { gw.late.pop()!() })
    await settle()
    expect(gw.mode, 'the gateway refused the older pick').toBe('normal')
    expect(pillSays('permission')).toMatch(/: Normal$/)
  })

  it('each pick is sent numbered, higher than the one before, on either control', async () => {
    await open()
    await pick('permission', 'Trust')
    await pick('task', 'Plan')
    await pick('permission', 'Normal')
    const numbers = gw.sent.map((s) => (s.body as { pick?: number }).pick)
    expect(numbers.every((n) => typeof n === 'number')).toBe(true)
    expect([...numbers].sort((a, b) => a! - b!)).toEqual(numbers)
    expect(new Set(numbers).size).toBe(numbers.length)
  })

  it('refused as stale, says so and shows the mode the chat is in, read back from the gateway', async () => {
    await open()
    // Another tab took a pick numbered far ahead of this one's: the chat is in Trust.
    gw.last['/api/chat/mode'] = Number.MAX_SAFE_INTEGER - 1
    h.detail = chat({ approval: 'trust' })
    await pick('permission', 'Trust reads')
    expect(alerts().join(' ')).toContain(STALE)
    await waitFor(() => expect(pillSays('permission')).toMatch(/: Trust$/))
    expect(gw.mode).toBe('')
    // The next pick is numbered above the one the gateway took, so it goes through.
    await pick('permission', 'Normal')
    expect(gw.mode).toBe('normal')
    expect(pillSays('permission')).toMatch(/: Normal$/)
  })

  it('after its wait, shows the mode the chat is in, read back from the gateway', async () => {
    await openWithTheClock()
    gw.silent = 1
    await pick('permission', 'YOLO')
    // The gateway took it, and its answer is what never came back.
    h.detail = chat({ approval: 'yolo' })
    await pastTheDeadline()
    await waitFor(() => expect(pillSays('permission')).toMatch(/: YOLO$/))
  })
})

describe('reading the chat back', () => {
  it('never shows the mode a read found once the pick it was read for has been taken', async () => {
    await openWithTheClock()
    gw.silent = 1
    await pick('permission', 'Trust')
    h.holdReads = true
    await pastTheDeadline()
    // The read-back left before the gateway took Trust; Trust's answer comes first.
    await act(async () => { gw.late.pop()!() })
    await settle()
    expect(pillSays('permission')).toMatch(/: Trust$/)
    await act(async () => { h.heldReads.forEach((answer) => answer()) })
    await settle()
    expect(pillSays('permission'), 'the older read, of Normal, must not take Trust off the control').toMatch(/: Trust$/)
  })

  it('shows the latest read when two come back out of order', async () => {
    await openWithTheClock()
    h.holdReads = true
    // Trust gets no answer: the read after its wait finds the chat in Normal, and is slow.
    gw.silent = 1
    await pick('permission', 'Trust')
    await pastTheDeadline()
    // YOLO is taken, but its answer is lost: the read after it finds YOLO.
    gw.takeThenFail.add('yolo')
    await pick('permission', 'YOLO')
    expect(gw.mode).toBe('yolo')
    expect(h.heldReads).toHaveLength(2)
    // The later read comes back first, the earlier one last.
    await act(async () => { h.heldReads[1]() })
    await settle()
    await act(async () => { h.heldReads[0]() })
    await settle()
    expect(pillSays('permission'), 'the earlier read, of Normal, is older than the chat').toMatch(/: YOLO$/)
  })

  it('a read that fails is tried again until one says the mode', async () => {
    await openWithTheClock()
    gw.takeThenFail.add('trust')
    h.readFails = 2
    await pick('permission', 'Trust')
    expect(gw.mode).toBe('trust')
    expect(pillSays('permission')).toMatch(/: Normal$/)
    for (let i = 0; i < 2; i++) {
      await act(async () => { vi.advanceTimersByTime(12_050) })
      await settle()
    }
    await waitFor(() => expect(pillSays('permission'), 'the third read says Trust').toMatch(/: Trust$/))
  })

  it('after a pick that failed with a server error, shows the mode the gateway holds', async () => {
    await open()
    gw.takeThenFail.add('trust')
    // The gateway takes Trust, so its own read says Trust; its answer is what fails.
    h.detail = chat({ approval: 'trust' })
    await pick('permission', 'Trust')
    expect(gw.mode).toBe('trust')
    expect(alerts().join(' ')).toMatch(/No clear answer came back \(The answer was lost on its way back\.\), so this permission mode may or may not have been taken/)
    await waitFor(() => expect(pillSays('permission')).toMatch(/: Trust$/))
  })
})

describe('an older pick answered after a newer one', () => {
  it('is not shown over a newer pick the gateway took after it: the chat is read back', async () => {
    await openWithTheClock()
    // Trust is taken as it arrives, and its answer is slow to come back.
    gw.slow = 1
    await pick('permission', 'Trust')
    await pastTheDeadline()
    // Trust reads is taken after it, and its answer is lost.
    gw.takeThenFail.add('trust_reads')
    await pick('permission', 'Trust reads')
    expect(gw.mode).toBe('trust_reads')
    // Now Trust's answer arrives: it says the gateway took Trust, which it did, before Trust reads.
    await act(async () => { gw.late.pop()!() })
    await settle()
    await waitFor(() => expect(pillSays('permission')).toMatch(/: Trust reads$/))
  })
})

describe('a refusal beside a mode control', () => {
  it('is not cleared by the answer to a pick made before it came back', async () => {
    await open()
    gw.refuse.add('yolo')
    gw.hold = true
    await pick('permission', 'YOLO')
    await pick('permission', 'Trust reads')
    // YOLO is answered (refused) first, then Trust reads is taken.
    while (gw.held.length) {
      await act(async () => { gw.held.shift()!() })
      await settle()
    }
    await settle()
    expect(gw.mode).toBe('trust_reads')
    expect(pillSays('permission')).toMatch(/: Trust reads$/)
    expect(alerts().join(' '), 'the refusal is still there to be read').toContain(REFUSAL)
  })

  it('goes once the control is opened, or a mode is picked again', async () => {
    await open()
    gw.refuse.add('yolo')
    await pick('permission', 'YOLO')
    expect(alerts().join(' ')).toContain(REFUSAL)
    fireEvent.click(trigger('permission'))
    await settle()
    expect(alerts().join(' '), 'opened: seen').not.toContain(REFUSAL)
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    await settle()
    await pick('permission', 'YOLO')
    expect(alerts().join(' ')).toContain(REFUSAL)
    await pick('permission', 'Trust')
    expect(alerts().join(' '), 'picked again: gone').not.toContain(REFUSAL)
  })
})
