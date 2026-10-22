import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { CompanionPage } from './CompanionPage'
import { invalidateKeys } from '../../lib/data'
import type { PendingApproval, WaitingList } from '../../lib/api'
import { waitingApproval, waitingItem, waitingList } from '../../test/waiting'

// ── The phone lists everything waiting on you, in the list's one order (#3888) ─────────────────
//
// The phone listed approvals only, so a workflow's gate and a loop's question waited unseen there.
// It now reads the gateway's one list (`GET /api/waiting`): every item, in its order (by
// consequence, then age), each with its text. An approval is answered on its card; every other
// item opens its own page, where it is answered with its brief. An item past answering keeps no
// action that could still send: its Allow is gone, its Open is off, and a click sends nothing.

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

const waiting = vi.fn<() => Promise<WaitingList>>()
const resolveApproval = vi.fn()
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      waiting: () => waiting(),
      resolveApproval: (id: string, action: string) => resolveApproval(id, action),
      pushStatus: () => Promise.reject(new Error('no push in this test')),
      running: () => Promise.resolve({ running: [], finished: [] }),
      uLoops: () => Promise.resolve([]),
      tasks: () => Promise.resolve({ tasks: [], total: 0 }),
      inboxOpen: () => Promise.resolve([]),
      notifications: () => Promise.resolve({ notifications: [], unread: 0 }),
    },
  }
})

const AP: PendingApproval = {
  id: 'ap-1', request_id: 'ap-1', source: 'cron', tool: 'Bash', tool_input: 'rm -rf build',
  tool_purpose: 'Clear the build folder', session: '', ts: Math.round(Date.now() / 1000) - 30,
  session_title: '', agent: '', risk: 'destructive', grant_agent: '', source_label: 'automation “Nightly”',
  // It names no second confirmation, so Allow answers it at once.
  confirms: [],
}

/** A workflow run waiting at its gate, and a loop asking a question, as the gateway lists them. */
const GATE = waitingItem({
  kind: 'gate', id: 'gate:r1:approve:0', work: 'workflow:r1', consequence: 'changes',
  brief: { workflow: 'release', node_id: 'approve', ask: { kind: 'approval', prompt: 'Ship v1.2?' } },
  route: '#/workflows/runs/r1',
})
const LOOP = waitingItem({
  kind: 'loop_question', id: 'loop_question:l1', work: 'loop:l1', consequence: 'asks',
  brief: { name: 'Sort the inbox', question: 'May I archive last year’s mail?' },
  route: '#/loops/l1',
})

const route = () => ({ sub: '', navigate: vi.fn(), navEpoch: 0, query: {}, setQuery: vi.fn() })

beforeEach(() => {
  waiting.mockReset()
  resolveApproval.mockReset().mockResolvedValue({ ok: true })
  invalidateKeys('companion:', true)
  for (const k of ['loops-companion', 'tasks-companion', 'inbox-companion', 'notifications-companion']) invalidateKeys(k)
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

/** The section that lists what waits on you, and its entries in the order they are painted: an
 *  approval's card by its group's name, every other item by its own. */
async function listed(): Promise<string[]> {
  const heading = await screen.findByRole('heading', { name: /Waiting on you/ })
  const section = heading.closest('section') as HTMLElement
  return [...section.children]
    .map((el) => (el.tagName === 'ARTICLE' ? el : el.querySelector('[role="group"]')))
    .filter((el): el is Element => el !== null)
    .map((el) => el.getAttribute('aria-label') ?? '')
}

describe('the phone lists everything waiting on you', () => {
  it('lists a gate and a loop question beside an approval, in the list’s order, each with its text', async () => {
    waiting.mockResolvedValue(waitingList(waitingApproval(AP), GATE, LOOP))
    render(<CompanionPage {...route()} />)
    await screen.findByRole('button', { name: 'Allow Bash' })
    expect(await listed()).toEqual([
      'Permission needed to run Bash',
      'Workflow: Ship v1.2?',
      'Loop: May I archive last year’s mail?',
    ])
    expect(screen.getByRole('heading', { name: 'Waiting on you (3)' })).toBeTruthy()
    expect(screen.getAllByText('workflow “release”', { exact: false }).length).toBeGreaterThan(0)
    // Each item's one line, as the buddy says it (#3898).
    expect(screen.getByText('Workflow “release” asks: Ship v1.2?')).toBeTruthy()
  })

  it('answers an approval on its card, and opens every other item on its own page', async () => {
    waiting.mockResolvedValue(waitingList(waitingApproval(AP), GATE, LOOP))
    const r = route()
    render(<CompanionPage {...r} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Allow Bash' }))
    expect(resolveApproval).toHaveBeenCalledWith('ap-1', 'approve')
    await userEvent.click(screen.getByRole('button', { name: 'Open workflow: Ship v1.2?' }))
    expect(r.navigate).toHaveBeenCalledWith('workflows/runs/r1')
    await userEvent.click(screen.getByRole('button', { name: 'Open loop: May I archive last year’s mail?' }))
    expect(r.navigate).toHaveBeenCalledWith('loops/l1')
  })

  it('says a source it could not read, never "nothing waiting on you"', async () => {
    waiting.mockResolvedValue({ waiting: [], unreadable: ['gate'] })
    render(<CompanionPage {...route()} />)
    expect(await screen.findByText(/could not be read: Workflow/)).toBeTruthy()
    expect(screen.queryByText('Nothing waiting on you')).toBeNull()
  })
})

describe('an item is answered on its own route alone', () => {
  it('an approval whose route is not an approval’s offers no Allow, and opens its page instead', async () => {
    const grant = { method: 'POST', path: '/api/browse/grants/g-1/{answer}', takes: ['approve', 'reject'], body: {} }
    waiting.mockResolvedValue(waitingList({ ...waitingApproval(AP), answer: grant, route: '#/dashboard' }))
    render(<CompanionPage {...route()} />)
    expect(await screen.findByRole('button', { name: /^Open approval:/ })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Allow Bash' })).toBeNull()
    expect(resolveApproval).not.toHaveBeenCalled()
  })
})

describe('an item past answering keeps no action that could still send', () => {
  it('an expired approval offers no Allow, and an expired gate’s Open sends you nowhere', async () => {
    const gone = new Date(Date.now() - 1000).toISOString()
    waiting.mockResolvedValue(waitingList(waitingApproval(AP, { expires_at: gone }), { ...GATE, expires_at: gone }))
    const r = route()
    render(<CompanionPage {...r} />)
    const card = await screen.findByRole('group', { name: 'Permission needed to run Bash' })
    expect(within(card).queryByRole('button', { name: 'Allow Bash' })).toBeNull()
    expect(within(card).getByText('It can no longer be answered.')).toBeTruthy()
    const open = screen.getByRole('button', { name: 'Open workflow: Ship v1.2?' })
    expect(open.getAttribute('aria-disabled')).toBe('true')
    await userEvent.click(open)
    expect(r.navigate).not.toHaveBeenCalled()
    expect(resolveApproval).not.toHaveBeenCalled()
  })

  it('an approval that expires while it is shown loses its Allow at once', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const soon = new Date(Date.now() + 5_000).toISOString()
    waiting.mockResolvedValue(waitingList(waitingApproval(AP, { expires_at: soon })))
    render(<CompanionPage {...route()} />)
    expect(await screen.findByRole('button', { name: 'Allow Bash' })).toBeTruthy()
    await act(async () => { vi.advanceTimersByTime(6_000) })
    expect(screen.queryByRole('button', { name: 'Allow Bash' })).toBeNull()
    expect(waiting).toHaveBeenCalledTimes(1)
  })
})
