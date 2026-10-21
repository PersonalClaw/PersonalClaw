import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, within, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { runState } from '../../../test/runState'

// ── One Execution Boundary control beside the composer says where the next turn may act ──────────
//
// Before a turn runs, the composer's footer shows where it may act, in one control: the permission
// mode, the working folder, the sandbox its commands run in, the network its work reaches and the
// memory mode. The permission mode is changed there and nowhere else in the chat, through the same
// call the header's pill made (`POST /api/chat/mode`), and a mode the gateway refuses stays as it
// was, with the gateway's reason. The task mode sits beside it in the footer, the same way. The
// sandbox and the network are the gateway's words (`GET /api/chat/boundary`), never the browser's.
//
// The double below records EVERY api call, so "the same call and nothing new" is checked against
// everything the page sent, not against the one call a test thought to look for.

const SESSION = 'chat-9-edge'
const REFUSAL = 'Approvals on this machine ask a person: the operator ceiling allows no standing grant.'
const PLAN_HELD = 'This chat’s plan is awaiting your review — approve it or cancel plan mode to change the task mode.'
const SANDBOX = 'The commands it runs are sandboxed on this computer: ~/.gnupg and ~/.docker are hidden from them.'
const REACH = 'Its web tools reach public hosts, within your Network egress settings, and the commands it runs keep the network.'

const h = vi.hoisted(() => ({
  calls: [] as Array<[string, unknown[]]>,
  real: {} as Record<string, unknown>,
  detail: {} as Record<string, unknown>,
  setApprovalMode: (..._a: unknown[]): Promise<unknown> => Promise.resolve({ ok: true }),
  setTaskMode: (..._a: unknown[]): Promise<unknown> => Promise.resolve({ ok: true }),
}))

vi.mock('../../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../../lib/api')>()
  h.real = real.api as unknown as Record<string, unknown>
  const answers: Record<string, (...args: unknown[]) => unknown> = {
    chatSessionDetail: () => Promise.resolve({ ...h.detail }),
    chatBoundary: () => Promise.resolve({
      sandbox: { confined: true, label: 'Sandboxed', said: 'The commands it runs are sandboxed on this computer: ~/.gnupg and ~/.docker are hidden from them.' },
      reach: { tier: 'all', label: 'Network on', said: 'Its web tools reach public hosts, within your Network egress settings, and the commands it runs keep the network.' },
    }),
    setApprovalMode: (...a: unknown[]) => h.setApprovalMode(...a),
    setTaskMode: (...a: unknown[]) => h.setTaskMode(...a),
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
  h.setApprovalMode = () => Promise.resolve({ ok: true })
  h.setTaskMode = () => Promise.resolve({ ok: true })
  ;({ ChatPage } = await import('../../ChatPage'))
  ;({ AppearanceProvider } = await import('../../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

/** Whether the real api's `name` only reads: it goes through the gateway's `get` and no writer. */
const onlyReads = (name: string) => {
  const source = String(h.real[name])
  return /\bget[<(]/.test(source) && !/\b(post|put|patch|del|write)[<(]/.test(source)
}
/** The calls the page made that change something on the gateway, with their string arguments. */
const writes = () => h.calls.filter(([name]) => !onlyReads(name)).map(([name, args]) => [name, args.filter((a) => typeof a === 'string')])
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 60)) })

async function open(sub: string) {
  const user = userEvent.setup()
  render(<AppearanceProvider><ChatPage sub={sub} navigate={() => {}} query={{}} setQuery={() => {}} /></AppearanceProvider>)
  if (sub !== 'new') await screen.findByText(/Two of them, both in the parser/)
  await settle()
  return user
}

/** The composer and what is stacked with it: where the next turn is decided. */
const composer = () => document.querySelector<HTMLElement>('[data-tour="chat"]')!
const boundary = () => within(composer()).getByRole('button', { name: /^Execution boundary:/ })
const taskMode = () => within(composer()).getByRole('button', { name: /^Task mode:/ })
async function openBoundary(user: ReturnType<typeof userEvent.setup>) {
  await user.click(boundary())
  return screen.findByRole('menu', { name: 'Execution boundary' })
}
const permissionRows = (menu: HTMLElement) => within(within(menu).getByRole('group', { name: 'Permission mode' })).getAllByRole('menuitemradio')
const checked = (rows: HTMLElement[]) => rows.filter((r) => r.getAttribute('aria-checked') === 'true')

describe('one control beside the composer, and none in the header', () => {
  it('the header carries no task-mode, permission, agent or model control; the footer one of each', async () => {
    await open(SESSION)
    const banner = screen.getByRole('banner')
    for (const name of [/^Task mode/, /^Permission mode/, /^Agent/, /^Model/, /^Execution boundary/]) {
      expect(within(banner).queryByRole('button', { name }), String(name)).toBeNull()
    }
    expect(within(composer()).getAllByRole('button', { name: /^Execution boundary:/ })).toHaveLength(1)
    expect(within(composer()).getAllByRole('button', { name: /^Task mode:/ })).toHaveLength(1)
    expect(within(composer()).getAllByRole('button', { name: /^Agent and model:/ })).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: /^(Execution boundary|Task mode|Agent and model):/ })).toHaveLength(3)
    // The permission mode is inside the boundary, never a pill of its own.
    expect(screen.queryByRole('button', { name: /^Permission mode/ })).toBeNull()
  })
})

describe('the permission mode, as the gateway has it', () => {
  it.each([
    ['normal', 'Normal'], ['trust_reads', 'Trust reads'], ['trust', 'Trust'], ['yolo', 'YOLO'],
  ])('a chat on %s shows %s, and only that mode is checked', async (approval, label) => {
    h.detail = chat({ approval })
    const user = await open(SESSION)
    await waitFor(() => expect(boundary()).toHaveAccessibleName(`Execution boundary: ${label}`))
    const rows = permissionRows(await openBoundary(user))
    expect(checked(rows)).toHaveLength(1)
    expect(checked(rows)[0]).toHaveAccessibleName(new RegExp(`^${label}\\b`))
  })

  it('each mode keeps the hint the header pill gave it, word for word', async () => {
    const user = await open(SESSION)
    const rows = permissionRows(await openBoundary(user))
    expect(rows.map((r) => r.getAttribute('title'))).toEqual([
      'Normal — a tool that only reads runs; the rest ask when they need approval',
      'Trust reads — read-only shell commands run without asking too',
      'Trust — auto-approve every tool in this chat',
      'YOLO — auto-approve everywhere; auto-expires, re-enable to extend',
    ])
  })
})

describe('changing it', () => {
  it('makes the one call the header pill made, and nothing else', async () => {
    const user = await open(SESSION)
    const before = writes().length
    const menu = await openBoundary(user)
    await user.click(within(menu).getByRole('menuitemradio', { name: /^Trust\b(?! reads)/ }))
    await waitFor(() => expect(boundary()).toHaveAccessibleName('Execution boundary: Trust'))
    await settle()
    expect(writes().slice(before)).toEqual([['setApprovalMode', ['trust', SESSION]]])
  })

  it('a mode the gateway refuses stays as it was, with the gateway’s reason', async () => {
    h.setApprovalMode = () => Promise.reject(new Error(REFUSAL))
    const user = await open(SESSION)
    const menu = await openBoundary(user)
    await user.click(within(menu).getByRole('menuitemradio', { name: /^YOLO/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent(REFUSAL)
    expect(boundary()).toHaveAccessibleName('Execution boundary: Normal')
    const rows = permissionRows(await openBoundary(user))
    expect(checked(rows)[0]).toHaveAccessibleName(/^Normal\b/)
  })

  it('in an app’s conversation the mode is the app’s, said with why, and nothing is sent', async () => {
    h.detail = chat({ created_by_app: 'inbox-triage', created_by_app_name: 'Inbox Triage' })
    const user = await open(SESSION)
    const before = writes().length
    const menu = await openBoundary(user)
    const group = within(menu).getByRole('group', { name: 'Permission mode' })
    expect(group).toHaveAccessibleDescription("Inbox Triage's permissions decide this chat, not yours")
    for (const row of permissionRows(menu)) expect(row).toHaveAttribute('aria-disabled', 'true')
    await user.click(within(menu).getByRole('menuitemradio', { name: /^Trust\b(?! reads)/ }))
    await settle()
    expect(writes().slice(before)).toEqual([])
  })
})

describe('the task mode beside it', () => {
  it('is changed through the call the header’s Task mode pill made, and nothing else', async () => {
    const user = await open(SESSION)
    const before = writes().length
    await user.click(taskMode())
    const menu = await screen.findByRole('menu', { name: 'Task mode' })
    await user.click(within(menu).getByRole('menuitemradio', { name: /^Plan/ }))
    await waitFor(() => expect(taskMode()).toHaveAccessibleName('Task mode: Plan'))
    await settle()
    expect(writes().slice(before)).toEqual([['setTaskMode', ['plan', SESSION]]])
  })

  it('a task mode the gateway refuses stays as it was, with its reason', async () => {
    h.detail = chat({ task_mode: 'plan' })
    h.setTaskMode = () => Promise.reject(new Error(PLAN_HELD))
    const user = await open(SESSION)
    await waitFor(() => expect(taskMode()).toHaveAccessibleName('Task mode: Plan'))
    await user.click(taskMode())
    const menu = await screen.findByRole('menu', { name: 'Task mode' })
    await user.click(within(menu).getByRole('menuitemradio', { name: /^Agent/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent(PLAN_HELD)
    expect(taskMode()).toHaveAccessibleName('Task mode: Plan')
  })
})

describe('everything it shows is read before anything is sent', () => {
  it('a new chat reads its sandbox and its network from the gateway, and writes nothing', async () => {
    const user = await open('new')
    await waitFor(() => expect(h.calls.some(([name]) => name === 'chatBoundary')).toBe(true))
    const menu = await openBoundary(user)
    expect(menu).toHaveAccessibleDescription(new RegExp(SANDBOX.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
    expect(menu).toHaveAccessibleDescription(new RegExp(REACH.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
    // Not one message was sent, and nothing was written to get here.
    expect(h.calls.some(([name]) => name === 'sendChat')).toBe(false)
    expect(writes()).toEqual([])
  })

  it('an open chat names its working folder, and the folder is changed from the control', async () => {
    const user = await open(SESSION)
    const menu = await openBoundary(user)
    expect(menu).toHaveAccessibleDescription(/\/home\/user\/src\/parser/)
    expect(within(menu).getByRole('menuitem', { name: /^Change working folder/ })).toBeTruthy()
  })

  it('a new chat’s memory mode is chosen in the control, and the trigger says it', async () => {
    const user = await open('new')
    const menu = await openBoundary(user)
    const memory = within(within(menu).getByRole('group', { name: 'Memory' })).getAllByRole('menuitemradio')
    expect(memory.map((r) => r.getAttribute('title'))).toEqual([
      'Persistent — Remember across sessions',
      'Temporary — No memory read or written, and forgotten when the session ends',
      'Incognito — Reads memory, writes nothing back',
    ])
    await user.click(memory[2])
    await waitFor(() => expect(boundary()).toHaveAccessibleName('Execution boundary: Normal · Incognito'))
    expect(writes()).toEqual([])
    // The control is the one place it is chosen: no second memory switch under the composer.
    expect(screen.queryByRole('radiogroup', { name: 'Memory mode' })).toBeNull()
  })

  it.each([
    ['temporary', 'Temporary', 'No memory read or written, and forgotten when the session ends'],
    ['incognito', 'Incognito', 'Reads memory, writes nothing back'],
  ])('a %s chat is marked on the control, in the memory mode’s own words', async (memory_mode, label, hint) => {
    h.detail = chat({ memory_mode })
    const user = await open(SESSION)
    await waitFor(() => expect(boundary()).toHaveAccessibleName(`Execution boundary: Normal · ${label}`))
    const menu = await openBoundary(user)
    // Set when the chat started: shown, not offered.
    expect(within(menu).queryByRole('group', { name: 'Memory' })).toBeNull()
    expect(menu).toHaveAccessibleDescription(new RegExp(`${label} — ${hint}`))
  })
})

describe('keyboard and screen reader', () => {
  it('opens on the checked mode, moves without changing anything, picks on Enter and gives focus back', async () => {
    const user = await open(SESSION)
    const before = writes().length
    boundary().focus()
    await user.keyboard('{Enter}')
    const menu = await screen.findByRole('menu', { name: 'Execution boundary' })
    await waitFor(() => expect(document.activeElement).toHaveAccessibleName(/^Normal\b/))
    await user.keyboard('{ArrowDown}')
    expect(document.activeElement).toHaveAccessibleName(/^Trust reads\b/)
    await user.keyboard('{ArrowDown}')
    expect(document.activeElement).toHaveAccessibleName(/^Trust\b(?! reads)/)
    expect(writes().slice(before)).toEqual([])
    await user.keyboard('{Enter}')
    await waitFor(() => expect(boundary()).toHaveAccessibleName('Execution boundary: Trust'))
    await waitFor(() => expect(menu).not.toBeInTheDocument())
    expect(document.activeElement).toBe(boundary())
    expect(writes().slice(before)).toEqual([['setApprovalMode', ['trust', SESSION]]])

    await user.keyboard('{Enter}')
    await screen.findByRole('menu', { name: 'Execution boundary' })
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('menu', { name: 'Execution boundary' })).toBeNull())
    expect(document.activeElement).toBe(boundary())
  })

  it('the trigger says the whole boundary to a reader that does not open it', async () => {
    await open(SESSION)
    await waitFor(() => expect(boundary()).toHaveAccessibleDescription(/Sandboxed/))
    expect(boundary()).toHaveAccessibleDescription(/Normal/)
    expect(boundary()).toHaveAccessibleDescription(/\/home\/user\/src\/parser/)
    expect(boundary()).toHaveAccessibleDescription(/Network on/)
    expect(boundary()).toHaveAccessibleDescription(/Persistent/)
  })
})

describe('one tab stop', () => {
  it('each menu is one tab stop, on its checked mode', async () => {
    const user = await open('new')
    const menu = await openBoundary(user)
    // Four permission modes, then the three memory modes: the cursor starts on Normal.
    expect(within(menu).getAllByRole('menuitemradio').map((r) => r.getAttribute('tabindex'))).toEqual(['0', '-1', '-1', '-1', '-1', '-1', '-1'])
    await user.keyboard('{Escape}')
    await user.click(taskMode())
    const tasks = await screen.findByRole('menu', { name: 'Task mode' })
    expect(within(tasks).getAllByRole('menuitemradio').map((r) => r.getAttribute('tabindex'))).toEqual(['0', '-1', '-1', '-1'])
  })
})
