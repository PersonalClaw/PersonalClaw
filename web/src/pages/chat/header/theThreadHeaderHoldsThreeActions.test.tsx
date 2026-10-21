import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, within, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { runState } from '../../../test/runState'

// ── A thread's header holds its own actions only: the title, three actions and an overflow ───────
//
// The chat header carried ten controls: the task and permission pills, Copy chat link, a "Continue
// on" per channel, Brief the agent, Working directory, Save as starter, New chat, Session map and
// Activity. What decides the next turn moved beside the composer (its Execution Boundary control and
// the task mode), and the header keeps the thread's own actions: Share (the link and the hand-offs),
// the Session map, Activity, and the `…` (Save as starter, New chat). Everything that left has a
// home one step away: Brief the agent is a row of the composer's `+` menu, and the working folder is
// changed from the boundary control.

const SESSION = 'chat-10-head'

const h = vi.hoisted(() => ({
  detail: {} as Record<string, unknown>,
  channels: [] as unknown[],
}))

vi.mock('../../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../../lib/api')>('../../../lib/api')
  const ok = () => Promise.resolve([])
  const base: Record<string, unknown> = {
    chatSessionDetail: () => Promise.resolve({ ...h.detail }),
    chatBoundary: () => Promise.resolve({
      sandbox: { confined: true, label: 'Sandboxed', said: 'The commands it runs are sandboxed on this computer.' },
      reach: { tier: 'all', label: 'Network on', said: 'Its web tools reach public hosts.' },
    }),
    channels: () => Promise.resolve(h.channels),
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    sessionTemplates: () => Promise.resolve([]),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
  }
  const api = new Proxy(base, {
    get(t, p: string) {
      if (p in t) return t[p]
      return (t[p] = (...__a: unknown[]) => ok())
    },
  })
  return { ...actual, api }
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
  h.channels = []
  h.detail = {
    key: SESSION, title: 'Tidy the parser', run_state: runState({ session: SESSION }), queue: [],
    task_mode: 'agent', approval: 'normal', memory_mode: 'persistent', workspace_dir: '',
    messages: [
      { role: 'user', content: 'Which tests fail?', ts: '2026-10-06T09:00:00+00:00' },
      { role: 'assistant', content: 'Two of them, both in the parser.', ts: '2026-10-06T09:00:05+00:00' },
    ],
  }
  ;({ ChatPage } = await import('../../ChatPage'))
  ;({ AppearanceProvider } = await import('../../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

async function open(sub: string) {
  const user = userEvent.setup()
  render(<AppearanceProvider><ChatPage sub={sub} navigate={() => {}} query={{}} setQuery={() => {}} /></AppearanceProvider>)
  if (sub !== 'new') await screen.findByText(/Two of them, both in the parser/)
  await act(async () => { await new Promise((r) => setTimeout(r, 60)) })
  return user
}

/** The header's own actions: every control in the bar but the title's (the way back, the title
 *  that renames itself, its regenerate) and the `…` that holds the rest. */
const TITLE_CONTROLS = /^(Back to chat history|Tidy the parser|Regenerate title|More actions)$/
function headerActions(): string[] {
  const banner = screen.getByRole('banner')
  return within(banner).getAllByRole('button').map((b) => b.getAttribute('aria-label') ?? b.textContent ?? '')
    .map((n) => n.trim()).filter((n) => !TITLE_CONTROLS.test(n))
}

describe('the header of a started chat', () => {
  it('holds three actions besides its title and overflow: Share, Session map and Activity', async () => {
    await open(SESSION)
    expect(headerActions()).toEqual(['Share', 'Session map', 'Activity'])
    expect(within(screen.getByRole('banner')).getByRole('button', { name: 'More actions' })).toBeTruthy()
  })

  it('Share holds the chat link and each channel the chat can continue on', async () => {
    h.channels = [{ name: 'telegram', display_name: 'Telegram', owner: { id: 'u-1' } }]
    const user = await open(SESSION)
    await user.click(within(screen.getByRole('banner')).getByRole('button', { name: 'Share' }))
    const menu = await screen.findByRole('menu', { name: 'Share' })
    expect(within(menu).getAllByRole('menuitem').map((r) => r.textContent)).toEqual([
      expect.stringMatching(/^Copy chat link/), expect.stringMatching(/^Continue on Telegram/),
    ])
  })

  it('the overflow holds Save as starter and New chat', async () => {
    const user = await open(SESSION)
    await user.click(within(screen.getByRole('banner')).getByRole('button', { name: 'More actions' }))
    expect(await screen.findByRole('button', { name: /^Save as starter/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /^New chat/ })).toBeTruthy()
  })

  it('Brief the agent is a row of the composer’s + menu', async () => {
    const user = await open(SESSION)
    expect(within(screen.getByRole('banner')).queryByRole('button', { name: /Brief the agent/ })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Add to message' }))
    expect(await screen.findByRole('button', { name: /^Brief the agent/ })).toBeTruthy()
  })
})

describe('the header of a new chat', () => {
  it('holds Chat history, and New chat in its overflow', async () => {
    const user = await open('new')
    expect(headerActions()).toEqual(['Chat history'])
    await user.click(within(screen.getByRole('banner')).getByRole('button', { name: 'More actions' }))
    expect(await screen.findByRole('button', { name: /^New chat/ })).toBeTruthy()
  })
})

describe('a control kept in the overflow', () => {
  it('runs what it does now, not what it did when it was first drawn', async () => {
    const { HeaderActions, HeaderControl } = await import('../../../ui/HeaderActions')
    const ran: string[] = []
    const Header = ({ agent }: { agent: string }) => (
      <header>
        <HeaderActions>
          <HeaderControl label="Save as starter" inOverflow onClick={() => ran.push(agent)} />
        </HeaderActions>
      </header>
    )
    const view = render(<Header agent="first" />)
    view.rerender(<Header agent="second" />)
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'More actions' }))
    await user.click(await screen.findByRole('button', { name: 'Save as starter' }))
    expect(ran).toEqual(['second'])
  })
})
