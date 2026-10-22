// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import { resetDataStore } from '../../lib/data'
import type { InboxItem, RunningList, RunningRow } from '../../lib/api'
import type { WsMessage } from '../../lib/useChatSocket'

// ── Mission Control follows a workflow run while it is open ────────────────────────────────────
//
// #3698 added running workflow runs to the Working lane and left one thing undone: the page read
// them once. A run that finished or failed kept its "running" card until a reload.
//
// Working is the running list now (`GET /api/running`), and the list says it moved in one
// `running_changed` frame per window. These drive the page through that frame with the REAL lane
// derivation, so what is asserted is the card a user sees.

const inboxOpen = vi.fn()
const approvals = vi.fn()
const running = vi.fn<() => Promise<RunningList>>()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    inboxOpen: (...a: unknown[]) => inboxOpen(...a),
    approvals: (...a: unknown[]) => approvals(...a),
    // Mission Control's lanes are the waiting list's, which carries each approval as its brief.
    waiting: async () => waitingList(...((await approvals()) as Parameters<typeof waitingApproval>[0][]).map((a) => waitingApproval(a))),
    running: () => running(),
    status: () => Promise.resolve({ start_time: 1 }),
    // No skill proposals waiting: this suite is about the other sources.
    skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
  },
}))

// The socket is the one thing faked: this is the handler the page registers on it.
let frame: ((m: WsMessage) => void) | null = null
vi.mock('../../lib/useChatSocket', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatSocket: (onMessage: (m: WsMessage) => void) => { frame = onMessage },
}))

import { MissionControl } from './MissionControl'
import { waitingApproval, waitingList } from '../../test/waiting'

function run(over: Partial<RunningRow> = {}): RunningRow {
  return {
    work: 'workflow:run-1', kind: 'workflow', session: '', phase: 'tool', line: 'Fetching the paper',
    since: '2026-09-26T09:00:05Z', name: 'paper-ingest', agent: '', started_at: '2026-09-26T09:00:00Z',
    route: '#/workflows/runs/run-1', blocked: false, waiting: [], ...over,
  }
}
const list = (live: RunningRow[] = [], finished: RunningRow[] = []): RunningList => ({ running: live, finished })
const RING: WsMessage = { type: 'running_changed', data: { changed: [{ work: 'workflow:run-1', session: '' }] } }

/** The question a parked run raises, as `workflows/needs_input.card_refs()` writes it. */
const question: InboxItem = {
  id: 'inbox-q', channel: 'native', channel_name: 'paper-ingest', message: 'Which paper next?',
  sender_id: 'agent', sender_name: 'paper-ingest', classification: 'needs_reply', confidence: 'high',
  status: 'pending', item_kind: 'needs_input',
  refs: { workflow: 'run-1', needs_input: { run_id: 'run-1', node_id: 'pick', blocker: 'Which paper next?', choices: ['A', 'B'] } },
} as InboxItem

function lane(name: string): HTMLElement {
  return screen.getByRole('region', { name })
}

function send(m: WsMessage): void {
  act(() => { frame?.(m) })
}

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  frame = null
  inboxOpen.mockResolvedValue([])
  approvals.mockResolvedValue([])
  running.mockResolvedValue(list([run()]))
})

describe('Mission Control, while a workflow run changes', () => {
  it('🔑 a run that finishes leaves the Working lane for its tail without a reload', async () => {
    render(<MissionControl />)
    expect(await within(lane('Working')).findByText('Fetching the paper')).toBeTruthy()

    running.mockResolvedValue(list([], [run({ phase: 'done', line: 'Done in 2 minutes', since: '2026-09-26T09:02:00Z' })]))
    send(RING)

    expect(await within(lane('Working')).findByText('Nothing is running right now.')).toBeTruthy()
    const tail = within(lane('Working')).getByRole('list', { name: 'Just finished' })
    expect(within(tail).getByText('Done in 2 minutes')).toBeTruthy()
  })

  it('🔑 a run that parks on a question says it waits on you, and its question is your turn', async () => {
    render(<MissionControl />)
    expect(await within(lane('Working')).findByText('Fetching the paper')).toBeTruthy()

    // Still live, as the list counts a run waiting on you, and never said to be running.
    running.mockResolvedValue(list([run({ phase: 'input', line: 'Needs your answer', waiting: ['q-1'] })]))
    inboxOpen.mockResolvedValue([question])
    send(RING)

    await waitFor(() => expect(within(lane('Your turn')).getByText('Which paper next?')).toBeTruthy())
    expect(within(lane('Working')).getByText('Waiting for your answer')).toBeTruthy()
    expect(within(lane('Working')).getByText('1 waiting on you')).toBeTruthy()
    expect(within(lane('Working')).queryByText('Fetching the paper')).toBeNull()
  })

  it('a run that starts while the page is open appears', async () => {
    running.mockResolvedValue(list())
    render(<MissionControl />)
    expect(await within(lane('Working')).findByText('Nothing is running right now.')).toBeTruthy()

    running.mockResolvedValue(list([run({ work: 'workflow:run-2', name: 'nightly-digest' })]))
    send(RING)

    expect(await within(lane('Working')).findByText('nightly-digest')).toBeTruthy()
  })

  it('a listing hint reads nothing: the list rings for itself', async () => {
    render(<MissionControl />)
    await within(lane('Working')).findByText('paper-ingest')
    const before = running.mock.calls.length

    send({ type: 'refresh', data: { kinds: ['workflow_runs', 'loops'] } })
    send({ type: 'work_phase', data: { work: 'workflow:run-1', phase: 'done' } })
    await new Promise((resolve) => setTimeout(resolve, 450))

    expect(running.mock.calls.length).toBe(before)
  })
})
