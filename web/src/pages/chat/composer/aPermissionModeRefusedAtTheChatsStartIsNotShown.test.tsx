import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act, fireEvent, within } from '@testing-library/react'
import { useState } from 'react'

// ── A permission mode the gateway refuses at a chat's start is not shown as the chat's ───────────
//
// A new chat keeps the permission mode picked before its first message, and sets it when the send
// makes the chat (`ensureSession`). Under an operator ceiling that asks for every approval, the
// gateway refuses Trust and YOLO (409 `approval_grant_refused`). The refusal is said, and the chat
// runs in Normal; the control must say Normal too, not the mode it was refused, both on the page
// the send started from and on the chat it opens. The same pick taken by the gateway is shown, so a
// control that never moves cannot pass.
//
// Driven through ChatPage from a new chat, its address changing to the chat as the router does.

const NEW = 'chat-12-start'

const h = vi.hoisted(() => ({
  calls: [] as Array<[string, unknown[]]>,
  /** The mode the gateway holds for the new chat. */
  held: 'normal',
  /** Whether the gateway refuses a standing grant, as it does under the ceiling. */
  refuse: true,
}))

vi.mock('../../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../../lib/api')>()
  const answers: Record<string, (...args: unknown[]) => unknown> = {
    createChatSession: () => Promise.resolve({ key: NEW }),
    setApprovalMode: (mode) => {
      if (h.refuse && mode !== 'normal') {
        return Promise.reject(new real.ApiError('An operator setting on this computer asks you before every tool call.', 409, 'approval_grant_refused'))
      }
      h.held = mode as string
      return Promise.resolve({ ok: true })
    },
    sendChat: () => Promise.resolve({ ok: true, session: NEW }),
    chatSessionDetail: () => Promise.resolve({
      key: NEW, title: '', queue: [], task_mode: 'agent', approval: h.held, memory_mode: 'persistent',
      messages: [{ role: 'user', content: 'Run the tests', ts: '2026-11-21T10:00:00+00:00' }],
      run_state: { session: NEW, phase: 'idle' },
    }),
    chatBoundary: () => Promise.resolve({
      sandbox: { confined: true, label: 'Sandboxed', said: 'The commands it runs are sandboxed on this computer.' },
      reach: { tier: 'all', label: 'Network on', said: 'Its web tools reach public hosts.' },
    }),
    dashboardConfig: () => Promise.resolve({ send_on_enter: true }),
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

const toasts: string[] = []
const onToast = (e: Event) => { toasts.push(String((e as CustomEvent).detail?.message ?? '')) }
const settle = (ms = 60) => act(async () => { await new Promise((r) => setTimeout(r, ms)) })

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
  h.held = 'normal'
  h.refuse = true
  toasts.length = 0
  window.addEventListener('ne:toast', onToast)
  ;({ ChatPage } = await import('../../ChatPage'))
  ;({ AppearanceProvider } = await import('../../../app/appearance'))
})

afterEach(() => { window.removeEventListener('ne:toast', onToast); vi.unstubAllGlobals() })

/** The chat page at `#/chat/new`, whose address the send changes as the router does when `follow`
 *  is set; otherwise the page stays where the send started. */
function NewChat({ follow }: { follow: boolean }) {
  const [sub, setSub] = useState('new')
  const navigate = (to: string) => { if (follow) setSub(to.replace(/^chat\//, '')) }
  return (
    <AppearanceProvider>
      <ChatPage sub={sub} navigate={navigate} query={{ seed: 'Run the tests' }} setQuery={() => {}} />
    </AppearanceProvider>
  )
}

const permission = () => screen.getByRole('button', { name: /^(Execution boundary|Permission mode):/ })

async function pickThenSend(label: string, follow: boolean) {
  render(<NewChat follow={follow} />)
  await waitFor(() => expect(permission()).toBeTruthy())
  await settle()
  fireEvent.click(permission())
  await settle()
  const menu = screen.getByRole('menu', { name: /^(Execution boundary|Permission mode)$/ })
  fireEvent.click(within(menu).getByRole('menuitemradio', { name: label }))
  await settle()
  expect(permission(), 'a new chat keeps the pick until it is made').toHaveAccessibleName(new RegExp(`: ${label}$`))
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
  await waitFor(() => expect(h.calls.some(([n]) => n === 'setApprovalMode')).toBe(true))
  await settle(120)
}

describe('a permission mode picked on a new chat', () => {
  it.each([['Trust'], ['YOLO']])('🔴 refused at the chat’s start, shows Normal on the page the send started from (%s)', async (label) => {
    await pickThenSend(label, false)
    expect(toasts.some((t) => t.includes('approval mode') && t.includes('operator setting')), 'the refusal is said').toBe(true)
    expect(permission()).toHaveAccessibleName(/: Normal$/)
  })

  it.each([['Trust'], ['YOLO']])('refused at the chat’s start, shows Normal on the chat it opens (%s)', async (label) => {
    await pickThenSend(label, true)
    expect(toasts.some((t) => t.includes('approval mode')), 'the refusal is said').toBe(true)
    await waitFor(() => expect(permission()).toHaveAccessibleName(/: Normal$/))
  })

  it('taken at the chat’s start, is shown', async () => {
    h.refuse = false
    await pickThenSend('Trust', true)
    expect(toasts.filter((t) => t.includes('approval mode'))).toEqual([])
    await waitFor(() => expect(permission()).toHaveAccessibleName(/: Trust$/))
  })
})
