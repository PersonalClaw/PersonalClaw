import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import type { InboxItem, Loop, NotificationItem, PendingApproval, TaskItem } from '../../lib/api'
import type { WsMessage } from '../../lib/useChatSocket'
import { invalidateKeys } from '../../lib/data'
import { CompanionPage } from './CompanionPage'

// ── A companion page left open shows what is true now, in every lane ─────────────────────────────
//
// The phone's page read each lane once, when it mounted, and only the approvals queue followed its
// frames. Left open for days, it still showed a finished loop as running with Pause and Stop, a
// completed task as open and an answered approval's Inbox row; only a reload told the truth. Each
// lane now follows the frames the gateway already sends about its list, and reads again when the
// socket comes back and when the page is shown again. These drive the tab's real shared socket.

/** What the gateway holds. Each read hands back fresh copies, as `fetch().json()` does. */
const world: {
  approvals: PendingApproval[]
  loops: Loop[]
  tasks: TaskItem[]
  inbox: InboxItem[]
  notes: NotificationItem[]
} = { approvals: [], loops: [], tasks: [], inbox: [], notes: [] }
const reads = { approvals: 0, loops: 0, tasks: 0, inbox: 0, notifications: 0 }

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      approvals: () => { reads.approvals++; return Promise.resolve(world.approvals.map((a) => ({ ...a }))) },
      uLoops: () => { reads.loops++; return Promise.resolve(world.loops.map((l) => ({ ...l }))) },
      tasks: (o: { status: string }) => {
        reads.tasks++
        const page = world.tasks.filter((t) => t.status === o.status).map((t) => ({ ...t }))
        return Promise.resolve({ tasks: page, total: page.length })
      },
      inboxOpen: () => { reads.inbox++; return Promise.resolve(world.inbox.filter((i) => i.status === 'pending').map((i) => ({ ...i }))) },
      notifications: () => { reads.notifications++; return Promise.resolve({ notifications: world.notes.map((n) => ({ ...n })), unread: 0 }) },
      pushStatus: () => Promise.reject(new Error('no push in this test')),
    },
  }
})

class FakeSocket {
  static all: FakeSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  constructor(public url: string) { FakeSocket.all.push(this) }
  close(): void {}
  open(): void { act(() => { this.onopen?.() }) }
  frame(m: WsMessage): void { act(() => { this.onmessage?.({ data: JSON.stringify(m) }) }) }
  drop(): void { act(() => { this.onclose?.() }) }
}
const gateway = () => FakeSocket.all[FakeSocket.all.length - 1]

/** Let the reads in flight land, and the renders they cause. */
const settle = () => act(async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve() })
/** A frame's lane reads once the rest of its burst has had time to arrive. */
const afterTheBurst = async () => { await act(async () => { vi.advanceTimersByTime(200) }); await settle() }

function setHidden(hidden: boolean) {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
  act(() => { document.dispatchEvent(new Event('visibilitychange')) })
}

const LOOP: Loop = {
  id: 'lp-1', kind: 'goal', name: 'Update the storage section', task: 'Rewrite the storage section for the new database',
  execution: 'solo', agent: 'default', model: 'scripted', attended: false,
  max_cycles: 10, idle_secs: 60, success_criteria: null,
  status: 'running', total_cycles: 5, error_message: null,
  created_at: 1, started_at: 2, completed_at: null, kind_config: {},
} as Loop
const TASK = { id: 'tk-1', title: 'Fix the feed parser', status: 'open', priority: 'high' } as TaskItem
const APPROVAL: PendingApproval = {
  id: 'ap-1', request_id: 'ap-1', source: 'loop', tool: 'edit_file', tool_input: 'README.md',
  tool_purpose: 'Rewrite the storage section', session: 'loop-lp-1', ts: 0,
  session_title: '', agent: '', risk: '', grant_agent: '', source_label: 'loop “Update the storage section”',
}
// The approval's Inbox row, raised with it and closed with it.
const ROW = {
  id: 'ib-1', channel: 'system', channel_name: 'system', message: 'Approval needed: edit_file',
  sender_id: 'system', sender_name: 'system', classification: 'needs_reply', confidence: 'high',
  status: 'pending', item_kind: 'agent_request', source: 'system',
} as InboxItem
const NOTE: NotificationItem = { kind: 'info', title: 'Morning brief finished', body: 'Three things today', ts: '2026-10-05T07:15:00Z', acked: false }

const LANE_KEYS = ['companion:approvals', 'loops-companion', 'tasks-companion', 'inbox-companion', 'notifications-companion']

beforeEach(() => {
  vi.useFakeTimers()
  FakeSocket.all = []
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  for (const k of LANE_KEYS) invalidateKeys(k)
  sessionStorage.clear()
  world.approvals = [{ ...APPROVAL }]
  world.loops = [{ ...LOOP }]
  world.tasks = [{ ...TASK }]
  world.inbox = [{ ...ROW }]
  world.notes = [{ ...NOTE }]
  for (const k of Object.keys(reads) as (keyof typeof reads)[]) reads[k] = 0
})
afterEach(async () => {
  cleanup()
  await act(async () => { await Promise.resolve() })  // the last subscriber's socket closes
  Reflect.deleteProperty(document, 'hidden')
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

/** The page as it stood when the phone was put down: every lane holding something. */
async function openThePage() {
  render(<CompanionPage sub="" navigate={vi.fn()} navEpoch={0} query={{}} setQuery={vi.fn()} />)
  gateway().open()
  await settle()
  expect(screen.getByRole('button', { name: 'Stop Update the storage section' })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Mark Fix the feed parser done' })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Mark the message from system handled' })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Allow edit_file' })).toBeTruthy()
  expect(screen.getByText('Morning brief finished')).toBeTruthy()
}

/** What happened while nobody looked: the loop ended, the task was finished on the desk, the
 *  approval was answered there (closing its Inbox row), and a new note landed. */
function theDayMovedOn() {
  world.loops = [{ ...LOOP, status: 'complete', completed_at: 3 }]
  world.tasks = [{ ...TASK, status: 'done' }]
  world.approvals = []
  world.inbox = [{ ...ROW, status: 'handled' }]
  world.notes = [{ ...NOTE, title: 'Feed digest finished', ts: '2026-10-05T08:00:00Z' }, { ...NOTE }]
}

function expectEveryLaneCurrent() {
  expect(screen.queryByRole('button', { name: 'Stop Update the storage section' }), 'a finished loop still offers Stop').toBeNull()
  expect(screen.getByText('Nothing running')).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Mark Fix the feed parser done' }), 'a finished task still reads open').toBeNull()
  expect(screen.getByText('No open tasks')).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Allow edit_file' }), 'an answered approval still offers Allow').toBeNull()
  expect(screen.queryByRole('button', { name: 'Mark the message from system handled' }), "the answered approval's row is still in the Inbox").toBeNull()
  expect(screen.getByText('Inbox clear')).toBeTruthy()
  expect(screen.getByText('Feed digest finished')).toBeTruthy()
}

describe('each lane follows the frames about its list', () => {
  it.each([['loops'], ['workflow_runs']])('a loop that ends leaves Running on a `%s` hint', async (kind) => {
    await openThePage()
    world.loops = [{ ...LOOP, status: 'complete', completed_at: 3 }]
    gateway().frame({ type: 'refresh', data: { kinds: [kind] } })
    await afterTheBurst()
    expect(screen.queryByRole('button', { name: 'Stop Update the storage section' })).toBeNull()
    expect(screen.getByRole('heading', { name: 'Running' })).toBeTruthy()
    expect(screen.getByText('Nothing running')).toBeTruthy()
  })

  it('a running loop reads the cycle it is on as its cycles go by', async () => {
    await openThePage()
    // Five cycles done means the sixth is under way: the number the cockpit and the Loops list read.
    expect(screen.getByText('Running · cycle 6')).toBeTruthy()
    world.loops = [{ ...LOOP, total_cycles: 6 }]
    gateway().frame({ type: 'refresh', data: { kinds: ['loops'] } })
    await afterTheBurst()
    expect(screen.getByText('Running · cycle 7')).toBeTruthy()
  })

  it('a task finished elsewhere leaves Tasks on the task store hint', async () => {
    await openThePage()
    world.tasks = [{ ...TASK, status: 'done' }]
    gateway().frame({ type: 'refresh', data: { kinds: ['tasks'] } })
    await afterTheBurst()
    expect(screen.queryByRole('button', { name: 'Mark Fix the feed parser done' })).toBeNull()
    expect(screen.getByText('No open tasks')).toBeTruthy()
  })

  it('a row handled elsewhere leaves the Inbox', async () => {
    await openThePage()
    world.inbox = [{ ...ROW, status: 'handled' }]
    gateway().frame({ type: 'inbox_item_updated', data: { id: 'ib-1', status: 'handled' } })
    await afterTheBurst()
    expect(screen.queryByRole('button', { name: 'Mark the message from system handled' })).toBeNull()
    expect(screen.getByText('Inbox clear')).toBeTruthy()
  })

  it('an approval answered elsewhere leaves Approvals and takes its Inbox row along', async () => {
    await openThePage()
    world.approvals = []
    world.inbox = [{ ...ROW, status: 'handled' }]
    gateway().frame({ type: 'approval_resolved', data: { id: 'ap-1' } })
    await afterTheBurst()
    expect(screen.queryByRole('button', { name: 'Allow edit_file' })).toBeNull()
    expect(screen.getByText('Nothing waiting on you')).toBeTruthy()
    expect(screen.getByText('Inbox clear')).toBeTruthy()
  })

  it('a note that lands is in Recent', async () => {
    await openThePage()
    world.notes = [{ ...NOTE, title: 'Feed digest finished', ts: '2026-10-05T08:00:00Z' }, { ...NOTE }]
    gateway().frame({ type: 'notification', data: { title: 'Feed digest finished' } })
    await afterTheBurst()
    expect(screen.getByText('Feed digest finished')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Recent (2)' })).toBeTruthy()
  })

  it('reads a lane once for a burst of its frames, and no lane for a frame about something else', async () => {
    await openThePage()
    const before = { ...reads }
    for (let i = 0; i < 20; i += 1) gateway().frame({ type: 'inbox_item_updated', data: { id: `row-${i}` } })
    gateway().frame({ type: 'refresh', data: { kinds: ['crons', 'history'] } })
    await afterTheBurst()
    expect(reads.inbox - before.inbox, 'one read for the burst').toBe(1)
    expect({ ...reads, inbox: before.inbox }, 'a frame about another list moved a lane').toEqual(before)
  })
})

describe('what the page missed is read when it can look again', () => {
  it('every lane reads again when the page is shown again', async () => {
    await openThePage()
    theDayMovedOn()  // and no frame reached the page: a phone suspends a hidden one
    const before = { ...reads }
    setHidden(true)
    await settle()
    expect(reads, 'a hidden page reads nothing').toEqual(before)
    setHidden(false)
    await settle()
    expectEveryLaneCurrent()
  })

  it('every lane reads again when the socket comes back', async () => {
    await openThePage()
    gateway().drop()
    theDayMovedOn()  // while the socket was down, so its frames went to nobody
    await act(async () => { vi.advanceTimersByTime(1_000) })
    expect(FakeSocket.all.length, 'the tab never reconnected').toBeGreaterThan(1)
    gateway().open()
    await settle()
    expectEveryLaneCurrent()
  })
})
