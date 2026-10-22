import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { PendingApproval, RunningList, WaitingList } from '../../lib/api'
import { invalidateKeys } from '../../lib/data'
import type { WsMessage } from '../../lib/useChatSocket'
import { waitingApproval, waitingList } from '../../test/waiting'
import { CompanionPage } from '../companion/CompanionPage'
import { BuddyPage } from './BuddyPage'

// ── `#/buddy`, the buddy's live presence (#3832), driven as the owner drives it ──────────────────
//
// The host's own consent test (`lib/buddy/host.consent.test.ts`) drives every input; this one drives
// the page: what arrives shows and sends nothing, an answer goes only from the card's controls to
// the approval's own route, a broad-reach Allow asks its confirmation first, Escape decides nothing,
// a frame reads both lists again, and the approval reads word for word as the phone's.

let onFrame: ((m: WsMessage) => void) | null = null
vi.mock('../../lib/useChatSocket', async (orig) => ({
  ...(await orig<typeof import('../../lib/useChatSocket')>()),
  useChatSocket: (onMessage: (m: WsMessage) => void) => { onFrame = onMessage },
}))

const lists: { running: RunningList; waiting: WaitingList } = {
  running: { running: [], finished: [] },
  waiting: { waiting: [], unreadable: [] } as WaitingList,
}
const reads = { running: 0, waiting: 0 }
const resolveApproval = vi.fn()
const answerChatQuestion = vi.fn()
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      running: async () => { reads.running++; return lists.running },
      waiting: async () => { reads.waiting++; return lists.waiting },
      listening: async () => ({ on: false, sources: [], since: null }),
      resolveApproval: (id: string, action: string) => resolveApproval(id, action),
      answerChatQuestion: (...a: unknown[]) => answerChatQuestion(...a),
      approvals: async () => [],
      status: async () => ({}),
      uLoops: async () => [],
      tasks: async () => ({ tasks: [], total: 0 }),
      inboxOpen: async () => [],
      notifications: async () => ({ notifications: [], unread: 0 }),
    },
  }
})

const route = { sub: '', navigate: vi.fn(), navEpoch: 0, query: {}, setQuery: vi.fn() }

const AP: PendingApproval = {
  id: 'ap-1', request_id: 'ap-1', source: 'cron', tool: 'Bash',
  tool_input: 'rm -rf /tmp/scratch', tool_purpose: 'Clear the scratch directory',
  session: 'cron:nightly', ts: Math.round(Date.now() / 1000) - 90, confirms: [],
  session_title: '', agent: '', risk: 'safe', grant_agent: '', source_label: 'trigger “Nightly sweep”',
  deny_effect: 'The run stops here.',
}

const RUNNING: RunningList = {
  running: [{
    work: 'chat:s1', kind: 'chat', session: 's1', phase: 'approval', line: 'Needs your OK', since: null,
    name: 'Nightly sweep', agent: '', started_at: new Date().toISOString(), route: '#/chat/s1', blocked: false, waiting: ['ap-1'],
  }],
  finished: [],
}

function waitingFor(ap: PendingApproval): WaitingList {
  return waitingList(waitingApproval(ap, { work: 'chat:s1', consequence: 'changes' }))
}

const prompt = () => screen.findByRole('group', { name: /^Permission needed to run Bash/ })

beforeEach(() => {
  lists.running = RUNNING
  lists.waiting = waitingFor(AP)
  reads.running = 0
  reads.waiting = 0
  resolveApproval.mockReset().mockResolvedValue({ ok: true })
  answerChatQuestion.mockReset()
  onFrame = null
  invalidateKeys('companion:', true)
})
afterEach(() => cleanup())

describe('the buddy presence', () => {
  it('shows the approval that arrives, expanded with its brief, and sends nothing for its arrival', async () => {
    render(<BuddyPage {...route} />)
    const group = await prompt()
    expect(within(group).getByRole('button', { name: 'Allow Bash' })).toBeTruthy()
    expect(within(group).getByRole('button', { name: 'Deny Bash. The run stops here.' })).toBeTruthy()
    expect(resolveApproval).not.toHaveBeenCalled()
  })

  it('Deny sends one request, to the approval its own route names', async () => {
    render(<BuddyPage {...route} />)
    const group = await prompt()
    await userEvent.click(within(group).getByRole('button', { name: 'Deny Bash. The run stops here.' }))
    await waitFor(() => expect(resolveApproval).toHaveBeenCalledTimes(1))
    expect(resolveApproval).toHaveBeenCalledWith('ap-1', 'reject')
  })

  it('a broad-reach Allow asks its confirmation first: the Allow sends nothing, the confirm sends one', async () => {
    lists.waiting = waitingFor({ ...AP, confirms: ['deletes'] })
    render(<BuddyPage {...route} />)
    const group = await prompt()
    await userEvent.click(within(group).getByRole('button', { name: 'Allow Bash' }))
    expect(resolveApproval).not.toHaveBeenCalled()
    await userEvent.click(within(group).getByRole('button', { name: /^Yes, run it/ }))
    await waitFor(() => expect(resolveApproval).toHaveBeenCalledTimes(1))
    expect(resolveApproval).toHaveBeenCalledWith('ap-1', 'approve')
  })

  it('Escape on the focused alert decides nothing, and nor does Enter', async () => {
    render(<BuddyPage {...route} />)
    const group = await prompt()
    group.focus()
    await userEvent.keyboard('{Escape}{Enter}')
    expect(resolveApproval).not.toHaveBeenCalled()
    expect(await prompt()).toBeTruthy()
  })

  it('a frame that says a list moved reads both lists again, and an answer elsewhere takes the alert away', async () => {
    render(<BuddyPage {...route} />)
    await prompt()
    const before = { ...reads }
    lists.waiting = { waiting: [], unreadable: [] } as WaitingList
    await act(async () => { onFrame?.({ type: 'waiting_changed', data: {} }) })
    await waitFor(() => expect(reads.waiting).toBe(before.waiting + 1))
    expect(reads.running).toBe(before.running + 1)
    await waitFor(() => expect(screen.queryByRole('group', { name: /^Permission needed/ })).toBeNull())
    expect(resolveApproval).not.toHaveBeenCalled()
  })

  it('while work runs it shows the compact line; a click on it opens the card and answers nothing', async () => {
    lists.waiting = { waiting: [], unreadable: [] } as WaitingList
    const { container } = render(<BuddyPage {...route} />)
    const line = await waitFor(() => {
      const b = container.querySelector<HTMLButtonElement>('[data-buddy-island] button')
      expect(b).not.toBeNull()
      return b!
    }, { timeout: 2000 })
    await userEvent.click(line)
    await waitFor(() => expect(container.querySelector('[data-buddy-island][data-room="expanded"]')).not.toBeNull())
    expect(resolveApproval).not.toHaveBeenCalled()
    expect(answerChatQuestion).not.toHaveBeenCalled()
  })

  it('shows nothing when nothing runs and nothing waits', async () => {
    lists.running = { running: [], finished: [] }
    lists.waiting = { waiting: [], unreadable: [] } as WaitingList
    const { container } = render(<BuddyPage {...route} />)
    await waitFor(() => expect(reads.waiting).toBe(1))
    expect(container.querySelector('[data-buddy-island], [data-buddy-floating]')).toBeNull()
  })
})

describe("in the buddy's own window", () => {
  const bridge = { pointerOver: vi.fn(), setRegions: vi.fn(), dragStart: vi.fn(), dragBy: vi.fn(), dragEnd: vi.fn(), setPresentation: vi.fn(), openInApp: vi.fn() }
  beforeEach(() => {
    for (const f of Object.values(bridge)) f.mockReset()
    window.pclawBuddy = bridge
  })
  afterEach(() => { delete window.pclawBuddy })

  it('sizes the window to the room, and marks the open card as the shape that takes clicks', async () => {
    render(<BuddyPage {...route} />)
    const group = await prompt()
    await waitFor(() => expect(bridge.setPresentation).toHaveBeenLastCalledWith('expanded'))
    expect(group.closest('[data-buddy-shape]')).not.toBeNull()
    expect(bridge.setPresentation).not.toHaveBeenCalledWith('hidden')
    lists.waiting = { waiting: [], unreadable: [] } as WaitingList
    lists.running = { running: [], finished: [] }
    await act(async () => { onFrame?.({ type: 'running_changed', data: {} }) })
    await waitFor(() => expect(screen.queryByRole('group', { name: /^Permission needed/ })).toBeNull())
    // Nothing in the shell shows the window again yet, so the page never hides it: with nothing to
    // say it draws nothing, and every click passes through.
    expect(bridge.setPresentation).not.toHaveBeenCalledWith('hidden')
  })

  it('the face is the shape and the drag handle; a link to the app opens in the main window', async () => {
    lists.waiting = { waiting: [], unreadable: [] } as WaitingList
    const { container } = render(<BuddyPage {...route} />)
    const face = await waitFor(() => {
      const f = container.querySelector('[data-buddy-island] [data-buddy-shape][data-buddy-drag]')
      expect(f).not.toBeNull()
      return f!
    }, { timeout: 2000 })
    await userEvent.click(face.querySelector('button')!)
    const open = await screen.findByRole('link', { name: /Open in PersonalClaw/ })
    await userEvent.click(open)
    expect(bridge.openInApp).toHaveBeenCalledWith('#/mission-control')
    expect(resolveApproval).not.toHaveBeenCalled()
  })
})

describe("the buddy's approval is the app's own, word for word", () => {
  it('reads as the phone reads the same row: its text, its choices\' names and what declining does', async () => {
    const buddy = render(<BuddyPage {...route} />)
    const mine = await prompt()
    const read = (g: HTMLElement) => ({
      text: g.textContent?.replace(/\s+/g, ' ').trim(),
      names: within(g).getAllByRole('button').map((b) => b.getAttribute('aria-label') || b.textContent),
    })
    const words = read(mine)
    buddy.unmount()
    render(<CompanionPage {...route} />)
    const phone = await prompt()
    expect(words).toEqual(read(phone))
  })
})
