// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import { resetDataStore } from '../../lib/data'
import type { InboxItem, WorkflowRunSummary } from '../../lib/api'
import type { WsMessage } from '../../lib/useChatSocket'

// ── Mission Control follows a workflow run while it is open ────────────────────────────────────
//
// #3698 added running workflow runs to the Working lane and left one thing undone: the page read
// them once. A run that finished or failed kept its "running" card until a reload, and one that
// parked on a question sat in Working while its question also waited in "Your turn".
//
// The page already re-reads on the socket's frames for its other sources (approvals, the Inbox,
// chat sessions). A run changing status reaches the socket as the gateway's listing hint —
// `refresh` naming `workflow_runs` (`workflows/watchdog._raw_publish`) — and the loop listing's
// is `refresh` naming `loops`. These drive the page through that frame with the REAL lane
// derivation, so what is asserted is the card a user sees.

const inboxOpen = vi.fn()
const approvals = vi.fn()
const chatSessions = vi.fn()
const uLoops = vi.fn()
const workflowRuns = vi.fn()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    inboxOpen: (...a: unknown[]) => inboxOpen(...a),
    approvals: (...a: unknown[]) => approvals(...a),
    chatSessions: (...a: unknown[]) => chatSessions(...a),
    uLoops: (...a: unknown[]) => uLoops(...a),
    workflowRuns: (...a: unknown[]) => workflowRuns(...a),
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

function run(over: Partial<WorkflowRunSummary> = {}): WorkflowRunSummary {
  return {
    id: 'run-1', workflow: 'paper-ingest', workflow_name: 'paper-ingest', status: 'running', spec_version: 1,
    started_at: '2026-09-26T09:00:00Z', created_at: '2026-09-26T08:59:58Z', title: '', parent_run_id: '',
    ...over,
  } as WorkflowRunSummary
}
const runs = (...rows: WorkflowRunSummary[]) => ({ runs: rows, total: rows.length, limit: 200, offset: 0 })

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
  chatSessions.mockResolvedValue([])
  uLoops.mockResolvedValue([])
  workflowRuns.mockResolvedValue(runs(run()))
})

describe('Mission Control, while a workflow run changes', () => {
  it('🔑 a run that finishes leaves the Working lane without a reload', async () => {
    render(<MissionControl />)
    expect(await within(lane('Working')).findByText('paper-ingest')).toBeTruthy()

    workflowRuns.mockResolvedValue(runs())
    send({ type: 'refresh', data: { kinds: ['workflow_runs'] } })

    await waitFor(() => expect(within(lane('Working')).queryByText('paper-ingest')).toBeNull())
    expect(within(lane('Working')).getByText('Nothing is running right now.')).toBeTruthy()
  })

  it('🔑 a run that parks on a question moves from Working to Your turn', async () => {
    render(<MissionControl />)
    expect(await within(lane('Working')).findByText('paper-ingest')).toBeTruthy()

    // It is `needs_input` now, so the running list no longer has it, and its question is open.
    workflowRuns.mockResolvedValue(runs())
    inboxOpen.mockResolvedValue([question])
    send({ type: 'refresh', data: { kinds: ['workflow_runs'] } })

    await waitFor(() => expect(within(lane('Your turn')).getByText('Which paper next?')).toBeTruthy())
    expect(within(lane('Working')).queryByText('paper-ingest')).toBeNull()
  })

  it('a run that starts while the page is open appears', async () => {
    workflowRuns.mockResolvedValue(runs())
    render(<MissionControl />)
    expect(await within(lane('Working')).findByText('Nothing is running right now.')).toBeTruthy()

    workflowRuns.mockResolvedValue(runs(run({ id: 'run-2', workflow_name: 'nightly-digest' })))
    send({ type: 'refresh', data: { kinds: ['workflow_runs'] } })

    expect(await within(lane('Working')).findByText('nightly-digest')).toBeTruthy()
  })

  it('a loop changing is read the same way — the Working lane’s other list', async () => {
    render(<MissionControl />)
    await within(lane('Working')).findByText('paper-ingest')
    const before = uLoops.mock.calls.length

    send({ type: 'refresh', data: { kinds: ['loops'] } })

    await waitFor(() => expect(uLoops.mock.calls.length).toBeGreaterThan(before))
  })

  it('a hint for a listing this page does not show reads nothing', async () => {
    render(<MissionControl />)
    await within(lane('Working')).findByText('paper-ingest')
    const before = workflowRuns.mock.calls.length

    send({ type: 'refresh', data: { kinds: ['crons', 'history'] } })
    await new Promise((resolve) => setTimeout(resolve, 450))

    expect(workflowRuns.mock.calls.length).toBe(before)
  })
})
