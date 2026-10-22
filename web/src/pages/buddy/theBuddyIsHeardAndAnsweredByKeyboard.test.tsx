import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { PendingApproval, RunningList, WaitingList } from '../../lib/api'
import { invalidateKeys } from '../../lib/data'
import type { WsMessage } from '../../lib/useChatSocket'
import { waitingApproval, waitingList } from '../../test/waiting'
import { BuddyPage } from './BuddyPage'

// ── The buddy is heard, and answered by keyboard alone (#3902) ─────────────────────────────────────
//
// A screen reader follows the focused window, and the buddy never takes focus, so what it says has
// to reach a listener without it: the page's one polite live region says the buddy's line, and an
// alert as "Needs you: <its line>", once when it arrives and never again while it waits. The
// character is decorative (hidden from the tree), its state always in words beside it. By keyboard,
// ⌘Enter (Ctrl+Enter) on a focused approval sends exactly one approve, a broad-reach one only after
// its confirmation, and Escape, a key with focus elsewhere or a request that has gone send none.

let onFrame: ((m: WsMessage) => void) | null = null
let sockets = 0
vi.mock('../../lib/useChatSocket', async (orig) => ({
  ...(await orig<typeof import('../../lib/useChatSocket')>()),
  useChatSocket: (onMessage: (m: WsMessage) => void) => { sockets++; onFrame = onMessage },
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
  sockets = 0
  invalidateKeys('companion:', true)
})
afterEach(() => cleanup())

const live = () => document.querySelector<HTMLElement>('[data-buddy-announce]')

describe('what a screen reader hears', () => {
  it('an alert is announced once, as "Needs you: <its line>", and not again while it waits', async () => {
    render(<BuddyPage {...route} />)
    await prompt()
    const region = live()!
    expect(region.getAttribute('role')).toBe('status')
    expect(region.getAttribute('aria-live')).toBe('polite')
    await waitFor(() => expect(region.textContent).toMatch(/^Needs you: .*Nightly sweep/))
    const said = region.textContent
    const changes: string[] = []
    const watch = new MutationObserver(() => changes.push(region.textContent ?? ''))
    watch.observe(region, { childList: true, characterData: true, subtree: true })
    // The lists are read again, and again, with the same alert still waiting.
    for (let i = 0; i < 3; i++) await act(async () => { onFrame?.({ type: 'waiting_changed', data: {} }) })
    await waitFor(() => expect(reads.waiting).toBeGreaterThanOrEqual(4))
    // Its brief is said again in other words: the same request, so nothing new is announced.
    lists.waiting = waitingFor({ ...AP, tool_purpose: 'Clear the scratch directory again' })
    await act(async () => { onFrame?.({ type: 'waiting_changed', data: {} }) })
    await waitFor(() => expect(reads.waiting).toBeGreaterThanOrEqual(5))
    watch.disconnect()
    expect(changes, 'nothing new is said while it waits').toEqual([])
    expect(region.textContent).toBe(said)
  })

  it('the next alert is announced in its turn', async () => {
    render(<BuddyPage {...route} />)
    await prompt()
    const region = live()!
    await waitFor(() => expect(region.textContent).toMatch(/^Needs you: /))
    const first = region.textContent
    lists.waiting = waitingFor({ ...AP, id: 'ap-2', request_id: 'ap-2', tool: 'Write', tool_input: 'notes.md', tool_purpose: 'Write the release notes' })
    lists.running = { ...RUNNING, running: [{ ...RUNNING.running[0], waiting: ['ap-2'] }] }
    await act(async () => { onFrame?.({ type: 'waiting_changed', data: {} }) })
    await waitFor(() => expect(region.textContent).toMatch(/^Needs you: .*Write the release notes/))
    expect(region.textContent).not.toBe(first)
  })

  it('with nothing waiting, it says the buddy\'s one line', async () => {
    lists.waiting = { waiting: [], unreadable: [] } as WaitingList
    render(<BuddyPage {...route} />)
    await waitFor(() => expect(live()?.textContent).toMatch(/Nightly sweep/))
    expect(live()!.textContent).not.toMatch(/^Needs you/)
  })

  it('the character is decorative: hidden from the tree, its state in words', async () => {
    const { container } = render(<BuddyPage {...route} />)
    await prompt()
    const art = [...container.querySelectorAll('canvas')]
    expect(art.length).toBeGreaterThan(0)
    for (const c of art) {
      expect(c.getAttribute('aria-hidden'), 'the orb is decoration').toBe('true')
      expect(c.getAttribute('role')).toBeNull()
    }
    expect(screen.queryAllByRole('img')).toEqual([])
  })
})

describe('answered by keyboard alone', () => {
  const allow = async () => within(await prompt()).getByRole('button', { name: 'Allow Bash' })

  it.each(['{Meta>}{Enter}{/Meta}', '{Control>}{Enter}{/Control}'])('%s on the focused approval sends exactly one approve', async (keys) => {
    render(<BuddyPage {...route} />)
    ;(await allow()).focus()
    await userEvent.keyboard(keys)
    await waitFor(() => expect(resolveApproval).toHaveBeenCalledTimes(1))
    expect(resolveApproval).toHaveBeenCalledWith('ap-1', 'approve')
    await userEvent.keyboard(keys)
    expect(resolveApproval, 'one answer per request').toHaveBeenCalledTimes(1)
  })

  it('Escape on the focused approval sends nothing', async () => {
    render(<BuddyPage {...route} />)
    ;(await allow()).focus()
    await userEvent.keyboard('{Escape}')
    expect(resolveApproval).not.toHaveBeenCalled()
  })

  it('⌘Enter with focus outside the request sends nothing', async () => {
    render(<BuddyPage {...route} />)
    await prompt()
    ;(document.activeElement as HTMLElement | null)?.blur()
    await userEvent.keyboard('{Meta>}{Enter}{/Meta}')
    expect(resolveApproval).not.toHaveBeenCalled()
  })

  it('a broad-reach approval asks its confirmation first: ⌘Enter sends nothing until it is confirmed', async () => {
    lists.waiting = waitingFor({ ...AP, confirms: ['deletes'] })
    render(<BuddyPage {...route} />)
    ;(await allow()).focus()
    await userEvent.keyboard('{Meta>}{Enter}{/Meta}')
    expect(resolveApproval).not.toHaveBeenCalled()
    await userEvent.click(within(await prompt()).getByRole('button', { name: /^Yes, run it/ }))
    await waitFor(() => expect(resolveApproval).toHaveBeenCalledTimes(1))
    expect(resolveApproval).toHaveBeenCalledWith('ap-1', 'approve')
  })

  it('a request answered elsewhere takes its keys with it', async () => {
    render(<BuddyPage {...route} />)
    ;(await allow()).focus()
    lists.waiting = { waiting: [], unreadable: [] } as WaitingList
    await act(async () => { onFrame?.({ type: 'waiting_changed', data: {} }) })
    await waitFor(() => expect(screen.queryByRole('group', { name: /^Permission needed/ })).toBeNull())
    await userEvent.keyboard('{Meta>}{Enter}{/Meta}')
    expect(resolveApproval).not.toHaveBeenCalled()
  })
})
