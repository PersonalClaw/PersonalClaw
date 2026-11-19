import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act, fireEvent, within } from '@testing-library/react'
import { runState } from '../../../test/runState'

// #5792: the plan panel offers Cancel plan mode while the plan drafts, and the task-mode pill can
// leave plan mode under it. The pill's own route drops the plan, but the panel was read again only
// when a turn landed or ended, so it stayed up and kept offering Cancel for a plan that was gone.
// Now the composer reads the panel again when the task mode changes, and the panel goes with the
// plan. (The cancel route also changes nothing once no plan is open: that half is the server's.)
//
// The page is the chat as it is drawn, over an api whose plan reads answer what the gateway holds.

const SESSION = 'chat-5792-pill'

const h = vi.hoisted(() => ({ planOpen: true, taskMode: 'plan' }))

const drafting = {
  id: 'chat-plan-1', kind: 'chat_plan', title: 'Plan', objective: 'Plan the work before anything runs.',
  status: 'running', artifact: {}, comments: [],
}

vi.mock('../../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../../lib/api')>()
  const answers: Record<string, (...args: unknown[]) => unknown> = {
    chatSessionDetail: () => Promise.resolve(chat()),
    chatPlanSession: () => Promise.resolve(h.planOpen
      ? { session: { project_id: SESSION, steps: [drafting] }, awaiting_step_id: '', binding: { resume_task_mode: 'agent' }, task_mode: h.taskMode }
      : { session: null, awaiting_step_id: '', binding: {}, task_mode: h.taskMode }),
    // The pill's route, as the gateway answers it: leaving plan mode drops a plan that has no draft.
    setTaskMode: (mode: unknown) => {
      h.taskMode = String(mode)
      if (h.taskMode !== 'plan') h.planOpen = false
      return Promise.resolve({ ok: true })
    },
    chatBoundary: () => Promise.resolve({
      sandbox: { confined: true, label: 'Sandboxed', said: 'The commands it runs are sandboxed on this computer.' },
      reach: { tier: 'all', label: 'Network on', said: 'Its web tools reach public hosts.' },
    }),
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
      return (...args: unknown[]) => (p in answers ? answers[p](...args) : Promise.resolve([]))
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

function chat() {
  return {
    key: SESSION, title: 'Tidy the parser', run_state: runState({ session: SESSION }), queue: [],
    task_mode: h.taskMode, approval: 'normal', memory_mode: 'persistent', workspace_dir: '/home/user/src/parser',
    messages: [
      { role: 'user', content: 'Which tests fail?', ts: '2026-10-06T09:00:00+00:00' },
      { role: 'assistant', content: 'Two of them, both in the parser.', ts: '2026-10-06T09:00:05+00:00' },
    ],
  }
}

let ChatPage: typeof import('../../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../../app/appearance')['AppearanceProvider']

beforeEach(async () => {
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }))))
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
  Object.assign(h, { planOpen: true, taskMode: 'plan' })
  ;({ ChatPage } = await import('../../ChatPage'))
  ;({ AppearanceProvider } = await import('../../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 60)) })

describe('the task-mode pill leaving plan mode while the plan drafts', () => {
  it.each(['Ask', 'Agent'])('to %s closes the plan panel, so no Cancel is left for a plan that is gone', async (mode) => {
    render(<AppearanceProvider><ChatPage sub={SESSION} navigate={() => {}} query={{}} setQuery={() => {}} /></AppearanceProvider>)
    await screen.findByText(/Two of them, both in the parser/)
    expect(await screen.findByRole('button', { name: /Cancel plan mode/i })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /^Task mode:/ }))
    await settle()
    fireEvent.click(within(screen.getByRole('menu', { name: /^Task mode$/ })).getByRole('menuitemradio', { name: mode }))
    await settle()

    expect(h.taskMode).toBe(mode.toLowerCase())
    await waitFor(() => expect(screen.queryByRole('button', { name: /Cancel plan mode/i })).toBeNull())
    expect(screen.queryByText('Drafting…')).toBeNull()
  })
})
