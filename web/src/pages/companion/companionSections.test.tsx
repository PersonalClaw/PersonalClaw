import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { InboxSection, RecentSection, RunningLoopsSection, TasksSection } from './CompanionSections'
import { CompanionPage } from './CompanionPage'
import { invalidateKeys } from '../../lib/data'
import type { InboxItem, Loop, NotificationItem, TaskItem } from '../../lib/api'

// ── `#/companion`'s loops / tasks / inbox / recent sections (MOBILE-COMPANION `MC-6`) ──────
//
// The atom says these must work "per the original S2 done-whens", which are two sentences:
// *renders on a phone viewport; URL doctrine holds* (T2.1) and *every action round-trips
// against a dev gateway; optimistic UI reverts on failure* (T2.2). So every test here drives
// a control and asserts the CALL that left the browser, plus the two failure behaviours that
// are the whole reason optimism needs a contract:
//
//   1. A FAILED action REVERTS. The row snaps back to the server's truth and the user is told.
//   2. A resolved row that the server STILL LISTS comes BACK. This is `MC-3`'s trap
//      generalized (its optimistic hide was never reconciled, so a live approvals queue
//      rendered as "nothing waiting on you"). Four more sections with the same shape is four
//      more chances to re-make it, which is why `useCompanionAction` owns the reconcile.
//
// And the honesty rail that outranks both: a failed FETCH must never fall through to the
// empty state. "Inbox clear" when the truth is "we could not ask" is the lie this surface
// exists not to tell, and `data === undefined` is true for loading, error and empty alike —
// so the branch order is load-bearing and is asserted per section, not once.

const uLoops = vi.fn()
const uLoopAction = vi.fn()
const uLoopNudge = vi.fn()
const tasks = vi.fn()
const updateTask = vi.fn()
const inboxPending = vi.fn()
const updateInboxItem = vi.fn()
const notifications = vi.fn()
const ackNotification = vi.fn()
const approvals = vi.fn()

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      approvals: () => approvals(),
      uLoops: () => uLoops(),
      uLoopAction: (id: string, action: string) => uLoopAction(id, action),
      uLoopNudge: (id: string, text: string) => uLoopNudge(id, text),
      tasks: (opts: Record<string, unknown>) => tasks(opts),
      updateTask: (id: string, body: Record<string, unknown>) => updateTask(id, body),
      inboxPending: () => inboxPending(),
      updateInboxItem: (id: string, body: Record<string, unknown>) => updateInboxItem(id, body),
      notifications: () => notifications(),
      ackNotification: (ts: string) => ackNotification(ts),
    },
  }
})

// The section keys live in their COLLECTION's namespace, not a `companion:` one — see the
// trap note in `CompanionSections.tsx`. Cleared per test so `data === undefined` (and with it
// every load-branch assertion below) is actually reachable.
const KEYS = ['companion:approvals', 'loops-companion', 'tasks-companion', 'inbox-companion', 'notifications-companion']

/** A loop, freshly allocated per call. The fetcher must hand back a NEW array/object every
 *  time, exactly as a real `fetch().json()` does: the reconcile effect is keyed on the
 *  fetched value's identity, so a shared mock object would make it silently never re-run and
 *  every reconcile assertion below would pass vacuously. */
const loop = (over: Partial<Loop> = {}): Loop => ({
  id: 'lp-1', kind: 'goal', name: 'Nightly sweep', task: 'Sweep the inbox',
  execution: 'solo', agent: 'default', model: 'sonnet', attended: false,
  max_cycles: 10, idle_secs: 60, success_criteria: null,
  status: 'running', total_cycles: 3, error_message: null,
  created_at: 1, started_at: 2, completed_at: null, kind_config: {},
  ...over,
} as Loop)

const task = (over: Partial<TaskItem> = {}): TaskItem => ({
  id: 'tk-1', title: 'Ship the release notes', status: 'open', priority: 'high',
  ...over,
} as TaskItem)

const item = (over: Partial<InboxItem> = {}): InboxItem => ({
  id: 'ib-1', channel: 'C1', channel_name: 'general', message: 'Can you sign this off?',
  sender_id: 'U1', sender_name: 'Dana', classification: 'needs_reply', confidence: 'high',
  status: 'pending', item_kind: 'message',
  ...over,
} as InboxItem)

const note = (over: Partial<NotificationItem> = {}): NotificationItem => ({
  kind: 'loop', title: 'Nightly sweep finished', body: 'Closed 4 items', ts: '2026-08-26T09:00:00Z',
  acked: false, ...over,
})

// ── Fake servers, not fake responses ─────────────────────────────────────────────────────
//
// 🪤 A STATELESS MOCK MAKES EVERY SUCCESS PATH LOOK BROKEN. `useCompanionAction` drops an
// optimistic patch as soon as a fetch confirms the action, so a mock that keeps returning the
// PRE-action row is a server that silently rejected the write — and the surface correctly
// snaps back to it. That is the hook working, not a bug, and the first draft of this file
// mistook it for one. So each section gets a tiny mutable store: the action mutates it, the
// refetch reads it, and the assertion is a genuine round-trip.
//
// Each read hands back FRESH objects, as `fetch().json()` does. The reconcile effect is keyed
// on the fetched value's identity, so a shared object would make it never re-run and every
// reconcile assertion here would pass vacuously.

function fakeLoops(initial: Loop[]) {
  let store = initial
  uLoops.mockImplementation(() => Promise.resolve(store.map((l) => ({ ...l }))))
  uLoopAction.mockImplementation((id: string, action: string) => {
    const to = action === 'pause' ? 'paused' : action === 'stop' ? 'stopped' : 'running'
    store = store.map((l) => (l.id === id ? { ...l, status: to as Loop['status'] } : l))
    return Promise.resolve({})
  })
  uLoopNudge.mockResolvedValue({})
}

function fakeTasks(initial: TaskItem[]) {
  let store = initial
  tasks.mockImplementation((o: { status: string }) =>
    Promise.resolve({ tasks: store.filter((t) => t.status === o.status).map((t) => ({ ...t })), total: store.length }))
  updateTask.mockImplementation((id: string, body: Record<string, unknown>) => {
    store = store.map((t) => (t.id === id ? { ...t, ...body } : t))
    return Promise.resolve({})
  })
}

function fakeInbox(initial: InboxItem[]) {
  let store = initial
  inboxPending.mockImplementation(() =>
    Promise.resolve(store.filter((i) => i.status === 'pending').map((i) => ({ ...i }))))
  updateInboxItem.mockImplementation((id: string, body: Record<string, unknown>) => {
    store = store.map((i) => (i.id === id ? { ...i, ...body } : i))
    return Promise.resolve({})
  })
}

function fakeFeed(initial: NotificationItem[]) {
  let store = initial
  notifications.mockImplementation(() => Promise.resolve({
    notifications: store.map((n) => ({ ...n })),
    unread: store.filter((n) => !n.acked).length,
  }))
  ackNotification.mockImplementation((ts: string) => {
    store = store.map((n) => (n.ts === ts ? { ...n, acked: true } : n))
    return Promise.resolve({})
  })
}

beforeEach(() => {
  for (const fn of [uLoops, uLoopAction, uLoopNudge, tasks, updateTask, inboxPending, updateInboxItem, notifications, ackNotification, approvals]) fn.mockReset()
  // COLD cache per test. A warm entry paints instantly, which makes `data === undefined`
  // unreachable and quietly disables every load-branch assertion in this file.
  for (const k of KEYS) invalidateKeys(k)
  sessionStorage.clear()
})
afterEach(cleanup)

// ─────────────────────────────────────────────────────────────────────────────
describe('the Running section — pause / resume / stop / nudge via loop_routes', () => {
  it('lists only the STEERABLE loops and pauses one through PATCH /api/loops/{id}', async () => {
    fakeLoops([loop(), loop({ id: 'lp-2', name: 'Finished thing', status: 'complete' })])
    render(<RunningLoopsSection />)

    // A loop that has finished is not a decision, so it is not on the phone at all.
    expect(await screen.findByText('Nightly sweep')).toBeTruthy()
    expect(screen.queryByText('Finished thing')).toBeNull()
    // The count in the heading is the STEERABLE count, not the raw list length.
    expect(screen.getByRole('heading', { name: 'Running (1)' })).toBeTruthy()

    // The name carries the loop, because a phone paints one card per loop and four bare
    // "Pause"es announce identically.
    await userEvent.click(screen.getByRole('button', { name: 'Pause Nightly sweep' }))
    await waitFor(() => expect(uLoopAction).toHaveBeenCalledWith('lp-1', 'pause'))
    // Optimistic: the row already reads paused, and the control has become Resume.
    expect(await screen.findByRole('button', { name: 'Resume Nightly sweep' })).toBeTruthy()
  })

  it('REVERTS the status when the pause fails, and says so', async () => {
    fakeLoops([loop()])
    uLoopAction.mockReset()
    uLoopAction.mockRejectedValue(new Error('gateway said no'))
    const toasts: string[] = []
    window.addEventListener('ne:toast', (e) => toasts.push((e as CustomEvent).detail.message))
    render(<RunningLoopsSection />)

    await userEvent.click(await screen.findByRole('button', { name: 'Pause Nightly sweep' }))
    // Back to Pause — the optimistic 'paused' was withdrawn, not left on screen where it
    // would read as "this loop is paused" for a loop that is still burning tokens.
    await waitFor(() => expect(screen.getByRole('button', { name: 'Pause Nightly sweep' })).toBeTruthy())
    expect(screen.queryByRole('button', { name: 'Resume Nightly sweep' })).toBeNull()
    expect(toasts.join(' ')).toContain('gateway said no')
  })

  it('sends a nudge, and gives the text BACK when the send fails', async () => {
    fakeLoops([loop()])
    uLoopNudge.mockReset()
    uLoopNudge.mockRejectedValue(new Error('nudge rejected'))
    render(<RunningLoopsSection />)

    await userEvent.click(await screen.findByRole('button', { name: 'Nudge Nightly sweep' }))
    const box = screen.getByRole('textbox', { name: 'What should Nightly sweep do next?' })
    await userEvent.type(box, 'check the staging logs first')
    await userEvent.click(screen.getByRole('button', { name: 'Send nudge' }))
    await waitFor(() => expect(uLoopNudge).toHaveBeenCalledWith('lp-1', 'check the staging logs first'))

    // Retyping a nudge you already wrote is the worst possible apology for a failed POST.
    const back = await screen.findByRole('textbox', { name: 'What should Nightly sweep do next?' })
    expect((back as HTMLTextAreaElement).value).toBe('check the staging logs first')
  })

  it('announces a failed fetch instead of claiming nothing is running', async () => {
    uLoops.mockRejectedValue(new Error('loops unreachable'))
    render(<RunningLoopsSection />)
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('loops unreachable')
    expect(screen.queryByText('Nothing running')).toBeNull()
  })
})

// ─────────────────────────────────────────────────────────────────────────────
describe('the Tasks section — state transitions', () => {
  it('reads each open status in its OWN request, so done history cannot eat the window', async () => {
    fakeTasks([task(), task({ id: 'tk-2', title: 'Already going', status: 'in_progress' })])
    render(<TasksSection />)
    await screen.findByText('Ship the release notes')
    // The bug this shape prevents: one unfiltered `limit`ed read whose window is filled by
    // DONE tasks, leaving the phone to say "no open tasks" while open tasks exist.
    expect(tasks.mock.calls.map((c) => c[0].status).sort()).toEqual(['in_progress', 'open'])
    expect(screen.getByText('Already going')).toBeTruthy()
  })

  it('starts a task, and takes a finished one off the list', async () => {
    fakeTasks([task()])
    render(<TasksSection />)

    await userEvent.click(await screen.findByRole('button', { name: 'Start Ship the release notes' }))
    await waitFor(() => expect(updateTask).toHaveBeenCalledWith('tk-1', { status: 'in_progress' }))

    await userEvent.click(screen.getByRole('button', { name: 'Mark Ship the release notes done' }))
    await waitFor(() => expect(updateTask).toHaveBeenLastCalledWith('tk-1', { status: 'done' }))
    // 'done' is not an open status, so the row leaves at once.
    await waitFor(() => expect(screen.queryByText('Ship the release notes')).toBeNull())
  })

  it('REVERTS a failed transition — the row comes back open', async () => {
    fakeTasks([task()])
    updateTask.mockReset()
    updateTask.mockRejectedValue(new Error('task store down'))
    render(<TasksSection />)

    await userEvent.click(await screen.findByRole('button', { name: 'Mark Ship the release notes done' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Start Ship the release notes' })).toBeTruthy())
  })

  it('reverts IMMEDIATELY — before the confirming refetch has landed', async () => {
    // 🪤 MEASURED, AND IT CHANGED THIS FILE. Deleting the explicit revert from
    // `useCompanionAction` left all 33 tests GREEN, because the post-action collection bust
    // triggers a refetch and the reconcile then drops the patch anyway. So the three sibling
    // "REVERTS" tests above only prove that ONE OF the two mechanisms works — they cannot tell
    // them apart, and on their own they would let the immediate revert be deleted silently.
    //
    // The revert's actual job is LATENCY: on a phone on cell data the refetch is exactly the
    // thing that is slow, and until it lands a withdrawn action must not sit on screen looking
    // like it succeeded. This test isolates it by holding the refetch open — with `data`
    // unchanged the reconcile effect cannot fire, so only the revert can bring the row back.
    // An object, not a `let`: TypeScript narrows a `let` assigned only inside a closure to
    // `null` and then refuses the call at the end of the test.
    const gate: { release?: () => void } = {}
    let reads = 0
    const store = [task()]
    tasks.mockImplementation(async (o: { status: string }) => {
      reads++
      // The first paint costs two reads (one per open status); block everything after it.
      if (reads > 2) await new Promise<void>((r) => { gate.release = r })
      return { tasks: store.filter((t) => t.status === o.status).map((t) => ({ ...t })), total: 1 }
    })
    updateTask.mockRejectedValue(new Error('task store down'))
    render(<TasksSection />)

    await userEvent.click(await screen.findByRole('button', { name: 'Mark Ship the release notes done' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Start Ship the release notes' })).toBeTruthy())
    // The isolation, asserted rather than assumed: if the refetch had already resolved, the
    // reconcile could have produced this result and the test would prove nothing.
    expect(reads, 'the confirming refetch must still be in flight to isolate the revert').toBeGreaterThan(2)
    gate.release?.()
  })

  it('busts the whole tasks COLLECTION, so the desktop list cannot keep the pre-edit row', async () => {
    // 🔑 The cross-surface half of the contract, and the one a `refresh()` would silently
    // break: the phone's own key repaints either way, so this is the only assertion that can
    // tell the two apart. `#/tasks` and the dependency picker (`tasks-all`, persist:true) hold
    // their own copies; a task finished here must drop BOTH, or the next desktop mount paints
    // the row as still open for one revalidation window — with a reload repainting it again.
    const { writeQuery, peekQuery } = await import('../../lib/data')
    fakeTasks([task()])
    writeQuery('tasks', ['the desktop list'])
    writeQuery('tasks-all', ['the dependency picker'])
    writeQuery('triggers', ['an unrelated collection'])
    render(<TasksSection />)

    await userEvent.click(await screen.findByRole('button', { name: 'Mark Ship the release notes done' }))
    await waitFor(() => expect(peekQuery('tasks'), "the desktop list's copy survived the phone's write").toBeUndefined())
    expect(peekQuery('tasks-all'), "the dependency picker's copy survived").toBeUndefined()
    // Vacuity floor: a bust that dropped everything would satisfy the two lines above while
    // proving nothing about the prefix.
    expect(peekQuery('triggers'), 'an unrelated collection must survive').toEqual(['an unrelated collection'])
  })

  it('announces a failed fetch instead of claiming there are no open tasks', async () => {
    tasks.mockRejectedValue(new Error('tasks unreachable'))
    render(<TasksSection />)
    expect((await screen.findByRole('alert')).textContent).toContain('tasks unreachable')
    expect(screen.queryByText('No open tasks')).toBeNull()
  })
})

// ─────────────────────────────────────────────────────────────────────────────
describe('the Inbox section — resolve through plan 42\'s own lifecycle', () => {
  it('resolves an item to HANDLED and takes it off the phone', async () => {
    fakeInbox([item()])
    render(<InboxSection />)

    await userEvent.click(await screen.findByRole('button', { name: 'Mark the message from Dana handled' }))
    // plan 42's status vocabulary, on plan 42's route. No second notion of "dealt with".
    await waitFor(() => expect(updateInboxItem).toHaveBeenCalledWith('ib-1', { status: 'handled' }))
    // HANDLED is no longer pending, so the refetch does not list it and the row is gone.
    await waitFor(() => expect(screen.queryByText('Dana')).toBeNull())
  })

  it('dismisses through the same route with the DISMISSED status', async () => {
    fakeInbox([item()])
    render(<InboxSection />)
    await userEvent.click(await screen.findByRole('button', { name: 'Dismiss the message from Dana' }))
    await waitFor(() => expect(updateInboxItem).toHaveBeenCalledWith('ib-1', { status: 'dismissed' }))
  })

  it('🪤 brings the row BACK when the server still lists it after the POST settled', async () => {
    // `MC-3`'s bug, generalized: an optimistic hide that is never reconciled leaves a row the
    // backend is still serving hidden FOREVER. On an attention surface that is not a cosmetic
    // glitch — it is work the user believes they dealt with and never did.
    inboxPending.mockImplementation(() => Promise.resolve([item()]))
    updateInboxItem.mockResolvedValue({})
    render(<InboxSection />)

    await userEvent.click(await screen.findByRole('button', { name: 'Mark the message from Dana handled' }))
    await waitFor(() => expect(updateInboxItem).toHaveBeenCalled())
    // The refresh re-lists it as still pending. The fetched list is authoritative.
    await waitFor(() => expect(screen.getByRole('button', { name: 'Mark the message from Dana handled' })).toBeTruthy())
  })

  it('REVERTS a failed resolve', async () => {
    fakeInbox([item()])
    updateInboxItem.mockReset()
    updateInboxItem.mockRejectedValue(new Error('inbox write failed'))
    render(<InboxSection />)
    await userEvent.click(await screen.findByRole('button', { name: 'Mark the message from Dana handled' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Dismiss the message from Dana' })).toBeTruthy())
  })

  it('announces a failed fetch instead of claiming the inbox is clear', async () => {
    inboxPending.mockRejectedValue(new Error('inbox unreachable'))
    render(<InboxSection />)
    expect((await screen.findByRole('alert')).textContent).toContain('inbox unreachable')
    expect(screen.queryByText('Inbox clear')).toBeNull()
  })

  it('says how much it is NOT showing rather than truncating in silence', async () => {
    inboxPending.mockImplementation(() => Promise.resolve(
      Array.from({ length: 9 }, (_, i) => item({ id: `ib-${i}`, sender_name: `Sender ${i}` }))))
    render(<InboxSection />)
    expect(await screen.findByText(/Showing 6 of 9/)).toBeTruthy()
  })
})

// ─────────────────────────────────────────────────────────────────────────────
describe('the Recent section — the notification feed', () => {
  it('marks one read through POST /api/notifications/ack, keyed on its ts', async () => {
    fakeFeed([note()])
    render(<RecentSection />)

    await userEvent.click(await screen.findByRole('button', { name: 'Mark "Nightly sweep finished" read' }))
    // `ts` is the row identity by construction — the log has no id and every ack/unack/delete
    // route takes the timestamp.
    await waitFor(() => expect(ackNotification).toHaveBeenCalledWith('2026-08-26T09:00:00Z'))
    expect(await screen.findByText('read')).toBeTruthy()
  })

  it('REVERTS to the unread control when the ack fails', async () => {
    fakeFeed([note()])
    ackNotification.mockReset()
    ackNotification.mockRejectedValue(new Error('ack failed'))
    render(<RecentSection />)
    await userEvent.click(await screen.findByRole('button', { name: 'Mark "Nightly sweep finished" read' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Mark "Nightly sweep finished" read' })).toBeTruthy())
  })

  it('announces a failed fetch instead of claiming nothing happened', async () => {
    notifications.mockRejectedValue(new Error('notifications unreachable'))
    render(<RecentSection />)
    expect((await screen.findByRole('alert')).textContent).toContain('notifications unreachable')
    expect(screen.queryByText('Nothing recent')).toBeNull()
  })
})

// ─────────────────────────────────────────────────────────────────────────────
describe('the companion page as one column', () => {
  const route = { sub: '', navigate: vi.fn(), navEpoch: 0, query: {}, setQuery: vi.fn() }

  it('carries all five sections, approvals FIRST, and no "not yet" stub', async () => {
    approvals.mockResolvedValue([])
    uLoops.mockImplementation(() => Promise.resolve([]))
    tasks.mockImplementation(() => Promise.resolve({ tasks: [], total: 0 }))
    inboxPending.mockImplementation(() => Promise.resolve([]))
    notifications.mockImplementation(() => Promise.resolve({ notifications: [], unread: 0 }))
    render(<CompanionPage {...route} />)

    const headings = await waitFor(() => {
      const hs = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)
      expect(hs.length).toBeGreaterThanOrEqual(5)
      return hs
    })
    // Order IS the priority order: a blocked run is the only row another person waits on.
    expect(headings).toEqual(['Approvals', 'Running', 'Tasks', 'Inbox', 'Recent'])
    // The stub list `MC-3` shipped is DELETED, not hidden behind a flag.
    expect(screen.queryByText('Not on the phone yet')).toBeNull()
  })

  it('links to the ONE device list rather than growing a second', async () => {
    approvals.mockResolvedValue([])
    uLoops.mockImplementation(() => Promise.resolve([]))
    tasks.mockImplementation(() => Promise.resolve({ tasks: [], total: 0 }))
    inboxPending.mockImplementation(() => Promise.resolve([]))
    notifications.mockImplementation(() => Promise.resolve({ notifications: [], unread: 0 }))
    const navigate = vi.fn()
    render(<CompanionPage {...route} navigate={navigate} />)
    await userEvent.click(await screen.findByRole('button', { name: /Paired devices/ }))
    expect(navigate).toHaveBeenCalledWith('settings/devices')
  })
})
