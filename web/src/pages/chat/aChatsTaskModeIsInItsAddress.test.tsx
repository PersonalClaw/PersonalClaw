import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { useState } from 'react'
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react'
import { runState } from '../../test/runState'

// ── A new thread opens in the task mode its address names, and nothing is written from it ─────
//
// `?mode=` is the chat's task mode in its address, so a reload, a copied address and a
// `personalclaw://threads/new?mode=plan` link open a NEW thread in that mode. A link is input from
// any app, so the address only says what to SHOW:
// - a new thread starts in the address's mode, and keeps it in the address as it changes;
// - a new thread follows the address when it moves (Back, Forward, a link into the open window),
//   and its approval goes back to Normal with it, as on a new thread a link opens (the shell's
//   side of a link into an open window is `app/aModeLinkIntoAnOpenNewThreadPutsItsApprovalBack`);
// - an existing thread keeps the mode its gateway copy has, and the address is corrected to it.
// Nothing reaches the gateway from the address: the double below records EVERY api call, and the
// calls a thread makes from an address that names a mode are the ones it makes from one that does
// not, with no write among them.

const SESSION = 'chat-7-mode'

const h = vi.hoisted(() => ({
  calls: [] as Array<[string, unknown[]]>,
  detail: (): Promise<unknown> => new Promise(() => {}),
  real: {} as Record<string, unknown>,
}))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  h.real = real.api as unknown as Record<string, unknown>
  const answers: Record<string, (...args: unknown[]) => unknown> = {
    chatSessionDetail: () => h.detail(),
    createChatSession: () => Promise.resolve({ key: SESSION }),
    sendChat: () => Promise.resolve({ ok: true, session: SESSION }),
    setTaskMode: () => Promise.resolve({}),
    setApprovalMode: () => Promise.resolve({}),
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
  }
  // Every member is recorded, reads and writes alike, so a call the address drives through any of
  // them shows up.
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
  h.calls.length = 0
  h.detail = () => new Promise(() => {})
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

type Query = Record<string, string>
type Patch = Record<string, string | null | undefined>

/** ChatPage under an address that behaves as the hash router's does: `setQuery` merges its patch
 *  into the address and the page renders the new one; `go` moves the address as Back, Forward or a
 *  link would. */
function page(sub: string, initial: Query) {
  const address = { query: initial, writes: [] as Array<[Patch, unknown]>, go: (_q: Query) => {} }
  function Routed() {
    const [query, setQuery] = useState<Query>(initial)
    address.query = query
    address.go = setQuery
    const write = (patch: Patch, opts?: unknown) => {
      address.writes.push([patch, opts])
      setQuery((cur) => {
        const next = { ...cur }
        for (const [k, v] of Object.entries(patch)) { if (v == null || v === '') delete next[k]; else next[k] = v }
        return next
      })
    }
    return <ChatPage sub={sub} navigate={() => {}} query={query} setQuery={write} />
  }
  const view = render(<AppearanceProvider><Routed /></AppearanceProvider>)
  return {
    ...view,
    address,
    go: (q: Query) => act(() => address.go(q)),
    modeWrites: () => address.writes.filter(([patch]) => 'mode' in patch),
  }
}

const pill = () => screen.getByRole('button', { name: /^Task mode:/ })
const permission = () => screen.getByRole('button', { name: /^Permission mode:/ })
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 60)) })
async function pick(control: () => HTMLElement, name: string) {
  act(() => { fireEvent.mouseEnter(control().parentElement!) })
  fireEvent.click(await screen.findByRole('menuitemradio', { name }))
  await waitFor(() => expect(control()).toHaveAccessibleName(new RegExp(`: ${name}$`)))
  await settle()
}
const callNames = () => [...new Set(h.calls.map(([name]) => name))].sort()
/** Whether the real api's `name` only reads: it goes through the gateway's `get` and no writer.
 *  Anything else, a member the api does not have among them, counts as a write. */
const onlyReads = (name: string) => {
  const source = String(h.real[name])
  return /\bget\(/.test(source) && !/\b(post|put|patch|del|write)\(/.test(source)
}
/** The calls the page made that change something on the gateway, with their string arguments. */
const writes = () => h.calls.filter(([name]) => !onlyReads(name)).map(([name, args]) => [name, args.filter((a) => typeof a === 'string')])
const detail = (task_mode: string) => () => Promise.resolve({
  key: SESSION, title: SESSION, messages: [{ role: 'user', content: 'hi' }, { role: 'assistant', content: 'hello' }],
  run_state: runState({ session: SESSION }), queue: [], task_mode, approval: 'normal', memory_mode: 'persistent',
})

/** The api calls a page at `sub` makes from an address with no mode, then unmounted. */
async function callsWithNoMode(sub: string, ready: string): Promise<string[]> {
  const base = page(sub, {})
  await waitFor(() => expect(pill()).toHaveAccessibleName(ready))
  await settle()
  const names = callNames()
  base.unmount()
  h.calls.length = 0
  return names
}

it('the gateway double tells a write from a read', () => {
  for (const name of ['chatSessionDetail', 'chatSessions', 'agents', 'models']) expect(onlyReads(name), name).toBe(true)
  for (const name of ['setTaskMode', 'setApprovalMode', 'chatPlanActivate', 'setSessionModel', 'createChatSession', 'sendChat', 'noSuchMember']) {
    expect(onlyReads(name), name).toBe(false)
  }
})

describe('a new thread', () => {
  for (const [mode, label] of [['plan', 'Plan'], ['build', 'Build'], ['ask', 'Ask']] as const) {
    it(`opens in the mode its address names (${mode}), with the calls an address with no mode makes and no write`, async () => {
      const base = await callsWithNoMode('new', 'Task mode: Agent')
      const m = page('new', { mode })
      await waitFor(() => expect(pill()).toHaveAccessibleName(`Task mode: ${label}`))
      await settle()
      expect(writes(), 'an address never changes how a chat runs').toEqual([])
      expect(callNames()).toEqual(base)
      expect(m.modeWrites(), 'the address already says it').toEqual([])
    })
  }

  it('opens in Agent for a mode the chat does not have, and corrects the address', async () => {
    const m = page('new', { mode: 'yolo' })
    await waitFor(() => expect(pill()).toHaveAccessibleName('Task mode: Agent'))
    await waitFor(() => expect(m.modeWrites()).toEqual([[{ mode: null }, { replace: true }]]))
    expect(m.address.query).toEqual({})
    expect(writes()).toEqual([])
  })

  it('records a mode picked on the pill in the address', async () => {
    const m = page('new', {})
    await waitFor(() => expect(pill()).toHaveAccessibleName('Task mode: Agent'))
    await pick(pill, 'Ask')
    expect(m.modeWrites()).toEqual([[{ mode: 'ask' }, { replace: true }]])
    expect(m.address.query).toEqual({ mode: 'ask' })
  })

  it('records a mode picked on the pill over the one the address names, and drops the key for Agent', async () => {
    const m = page('new', { mode: 'plan' })
    await waitFor(() => expect(pill()).toHaveAccessibleName('Task mode: Plan'))
    await pick(pill, 'Ask')
    expect(m.address.query).toEqual({ mode: 'ask' })
    await pick(pill, 'Agent')
    expect(m.address.query).toEqual({})
    expect(m.modeWrites()).toEqual([[{ mode: 'ask' }, { replace: true }], [{ mode: null }, { replace: true }]])
  })

  it('writes the address once for each mode picked, and never puts an old one back', async () => {
    const m = page('new', {})
    await waitFor(() => expect(pill()).toHaveAccessibleName('Task mode: Agent'))
    for (const name of ['Plan', 'Ask', 'Agent']) await pick(pill, name)
    expect(m.modeWrites()).toEqual([[{ mode: 'plan' }, { replace: true }], [{ mode: 'ask' }, { replace: true }], [{ mode: null }, { replace: true }]])
    expect(m.address.query).toEqual({})
  })

  it('follows the address when it moves to another mode, and writes nothing over it', async () => {
    const m = page('new', { mode: 'plan' })
    await waitFor(() => expect(pill()).toHaveAccessibleName('Task mode: Plan'))
    m.go({ mode: 'build' })
    await waitFor(() => expect(pill()).toHaveAccessibleName('Task mode: Build'))
    await settle()
    expect(m.modeWrites()).toEqual([])
    expect(m.address.query).toEqual({ mode: 'build' })
  })

  it('follows the address back to an entry with no mode, which names Agent, and leaves that entry bare', async () => {
    const m = page('new', {})
    await waitFor(() => expect(pill()).toHaveAccessibleName('Task mode: Agent'))
    m.go({ mode: 'build' })
    await waitFor(() => expect(pill()).toHaveAccessibleName('Task mode: Build'))
    m.go({})
    await settle()
    expect(pill()).toHaveAccessibleName('Task mode: Agent')
    expect(m.address.query, 'the entry the person went Back to').toEqual({})
    expect(m.modeWrites()).toEqual([])
  })
})

describe('a link into a new thread that is already open', () => {
  // The person set the thread up by hand; then a link names a mode. The thread follows the link's
  // mode, and its approval goes back to Normal, as on a new thread a link opens: a link never
  // leaves a chat with an auto-approve the person picked for another mode.
  async function setUpByHand(m: ReturnType<typeof page>) {
    await waitFor(() => expect(pill()).toHaveAccessibleName('Task mode: Agent'))
    await pick(pill, 'Ask')
    await pick(permission, 'Trust')
    expect(m.address.query.mode).toBe('ask')
  }
  async function sendDraft() {
    h.calls.length = 0
    fireEvent.click(screen.getByRole('button', { name: /^Send/ }))
    await waitFor(() => expect(h.calls.some(([name]) => name === 'sendChat')).toBe(true))
    await settle()
    return writes().map(([name, args]) => [name, (args as string[]).filter((a) => a !== SESSION)])
  }

  it('moves it to Agent with approval back to Normal, so Send runs nothing auto-approved', async () => {
    const m = page('new', { seed: 'list the files' })
    await setUpByHand(m)
    m.go({ ...m.address.query, mode: 'agent' })
    await settle()
    expect(pill()).toHaveAccessibleName('Task mode: Agent')
    expect(permission()).toHaveAccessibleName('Permission mode: Normal')
    expect(m.address.query.mode).toBeUndefined()
    expect(await sendDraft()).toEqual([['createChatSession', []], ['sendChat', ['list the files']]])
  })

  it('moves it to Build with approval back to Normal, so Build asks every approval it asks when picked', async () => {
    const m = page('new', { seed: 'list the files' })
    await setUpByHand(m)
    m.go({ ...m.address.query, mode: 'build' })
    await settle()
    expect(pill()).toHaveAccessibleName('Task mode: Build')
    expect(permission()).toHaveAccessibleName('Permission mode: Normal')
    expect(await sendDraft()).toEqual([['createChatSession', []], ['setTaskMode', ['build']], ['sendChat', ['list the files']]])
  })

  it('leaves the approval alone when the address moves to the mode the thread already shows', async () => {
    const m = page('new', { seed: 'list the files' })
    await setUpByHand(m)
    m.go({ mode: 'ask' })
    await settle()
    expect(pill()).toHaveAccessibleName('Task mode: Ask')
    expect(permission()).toHaveAccessibleName('Permission mode: Trust')
  })
})

describe('an existing thread', () => {
  for (const mode of ['plan', 'build', 'agent', 'yolo']) {
    it(`keeps the mode it has over the one its address names (${mode}), with the calls an address with no mode makes and no write`, async () => {
      h.detail = detail('ask')
      const base = await callsWithNoMode(SESSION, 'Task mode: Ask')
      const m = page(SESSION, { mode })
      await waitFor(() => expect(pill()).toHaveAccessibleName('Task mode: Ask'))
      await waitFor(() => expect(m.address.query).toEqual({ mode: 'ask' }))
      m.go({ mode: 'build' })
      await settle()
      m.go({ mode: 'plan' })
      await settle()
      expect(pill()).toHaveAccessibleName('Task mode: Ask')
      expect(m.address.query).toEqual({ mode: 'ask' })
      expect(writes(), 'an address never changes how a chat runs').toEqual([])
      expect(callNames()).toEqual(base)
    })
  }

  it('does not show the address\'s mode while its first read is out, and writes nothing to the address', async () => {
    const m = page(SESSION, { mode: 'plan' })
    await waitFor(() => expect(pill()).toBeTruthy())
    expect(pill(), 'a thread\'s mode is the one it has, never the one a link names').toHaveAccessibleName('Task mode: Agent')
    await settle()
    expect(m.modeWrites()).toEqual([])
    expect(writes()).toEqual([])
  })

  it('does not follow the address to another mode, and puts its own mode back in the address', async () => {
    h.detail = detail('ask')
    const m = page(SESSION, { mode: 'ask' })
    await waitFor(() => expect(pill()).toHaveAccessibleName('Task mode: Ask'))
    await settle()
    m.go({ mode: 'plan' })
    await waitFor(() => expect(m.modeWrites()).toEqual([[{ mode: 'ask' }, { replace: true }]]))
    expect(pill()).toHaveAccessibleName('Task mode: Ask')
    expect(m.address.query).toEqual({ mode: 'ask' })
  })

  it('records the mode of a thread with none in its address', async () => {
    h.detail = detail('build')
    const m = page(SESSION, {})
    await waitFor(() => expect(m.modeWrites()).toEqual([[{ mode: 'build' }, { replace: true }]]))
    expect(m.address.query).toEqual({ mode: 'build' })
  })
})
