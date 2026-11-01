import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { runState, runningState } from '../../test/runState'

// ── Plan mode switched on while a turn runs stops it only when asked (#5722) ──────────────────────
//
// "Plan this first" while a turn ran parked it: the turn was asked to stop, and nothing on the
// control said so. While a turn runs the + menu now offers two choices instead: "Plan after this
// turn", which stops nothing (the turn runs to its end, and the mode changes then), and "Stop and
// plan", which stops it as Stop does. Driven through the real ChatPage.

const SESSION = 'chat-12-plan'

const h = vi.hoisted(() => ({ chatSessionDetail: vi.fn(), chatPlanActivate: vi.fn() }))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    chatPlanActivate: h.chatPlanActivate,
    useCaseSettings: () => Promise.resolve({ value: {}, revision: 'r1' }),
    personalclawConfig: () => Promise.resolve({ voice: {} }),
    agents: () => Promise.resolve({ agents: [] }),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
    chatPlanSession: () => Promise.resolve({ session: null, binding: {}, awaiting_step_id: '', task_mode: 'agent' }),
  }
  const api = new Proxy(base, { get(t, p: string) { return p in t ? t[p] : (t[p] = () => Promise.resolve([])) } })
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

function detail(running: boolean) {
  return {
    key: SESSION, title: SESSION, queue: [], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
    run_state: running ? runningState({ session: SESSION }) : runState({ session: SESSION }),
    messages: [{ role: 'user', content: 'rename the module', ts: 't1' }, { role: 'assistant', content: 'On it.', ts: 't2' }],
  }
}

beforeEach(async () => {
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  vi.stubGlobal('requestAnimationFrame', () => 0)
  vi.stubGlobal('cancelAnimationFrame', () => {})
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
  h.chatPlanActivate.mockReset()
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})
afterEach(() => { vi.unstubAllGlobals() })

async function openPlusMenu(running: boolean) {
  h.chatSessionDetail.mockReset().mockResolvedValue(detail(running))
  const user = userEvent.setup()
  render(
    <AppearanceProvider>
      <ChatPage sub={SESSION} navigate={vi.fn()} query={{}} setQuery={() => {}} />
    </AppearanceProvider>,
  )
  await waitFor(() => expect(h.chatSessionDetail).toHaveBeenCalled())
  if (running) await screen.findByRole('button', { name: 'Stop this turn' })
  await user.click(await screen.findByRole('button', { name: 'Add to message' }))
  return user
}

describe('plan mode switched on while a turn runs', () => {
  it('offers to plan after the turn or to stop and plan, and not to plan at once', async () => {
    await openPlusMenu(true)
    await screen.findByRole('button', { name: /Plan after this turn/ })
    expect(screen.getByRole('button', { name: /Stop and plan/ })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Plan this first/ })).toBeNull()
  })

  it('"Plan after this turn" stops nothing and leaves the mode as it is until the turn ends', async () => {
    h.chatPlanActivate.mockResolvedValue({ ok: true, parked: false, after_turn: true, task_mode: 'agent' })
    const user = await openPlusMenu(true)
    await user.click(await screen.findByRole('button', { name: /Plan after this turn/ }))
    await waitFor(() => expect(h.chatPlanActivate).toHaveBeenCalledWith(SESSION, {}))
    await screen.findByText('Plan mode starts when this turn ends. Your next message gets a plan.')
    expect(screen.getByRole('button', { name: 'Task mode: Agent' })).toBeTruthy()
  })

  it('"Stop and plan" asks for the stop', async () => {
    h.chatPlanActivate.mockResolvedValue({ ok: true, parked: true, after_turn: false, task_mode: 'plan' })
    const user = await openPlusMenu(true)
    await user.click(await screen.findByRole('button', { name: /Stop and plan/ }))
    await waitFor(() => expect(h.chatPlanActivate).toHaveBeenCalledWith(SESSION, { stop: true }))
    await screen.findByRole('button', { name: 'Task mode: Plan' })
  })

  it('an idle chat plans at once, as before', async () => {
    h.chatPlanActivate.mockResolvedValue({ ok: true, parked: false, after_turn: false, task_mode: 'plan' })
    const user = await openPlusMenu(false)
    await user.click(await screen.findByRole('button', { name: /Plan this first/ }))
    expect(screen.queryByRole('button', { name: /Stop and plan/ })).toBeNull()
    await waitFor(() => expect(h.chatPlanActivate).toHaveBeenCalledWith(SESSION, {}))
    await screen.findByRole('button', { name: 'Task mode: Plan' })
  })
})
