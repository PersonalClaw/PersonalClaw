// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import { resetDataStore } from '../../lib/data'
import type { RunningList, RunningRow, WaitingList } from '../../lib/api'
import type { WsMessage } from '../../lib/useChatSocket'

// ── Home follows a workflow run while it is open ───────────────────────────────────────────────
//
// #3698 added running workflow runs to the Working lane and left one thing undone: the page read
// them once. A run that finished or failed kept its "running" card until a reload.
//
// Running Now is the running list (`GET /api/running`), and the list says it moved in one
// `running_changed` frame per window. Needs You is the waiting list, read again on the same ring.
// These drive the page through that frame, so what is asserted is the card a user sees.

const waiting = vi.fn<() => Promise<WaitingList>>()
const running = vi.fn<() => Promise<RunningList>>()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    waiting: () => waiting(),
    inboxOpen: () => Promise.resolve([]),
    chatSessions: () => Promise.resolve([]),
    running: () => running(),
    status: () => Promise.resolve({ start_time: 1 }),
    // No skill proposals waiting: this suite is about the other sources.
    skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
  },
}))

// The socket is the one thing faked: these are the handlers the page registers on it.
const frames = new Map<string, (m: WsMessage) => void>()
vi.mock('../../lib/useChatSocket', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatSocket: (onMessage: (m: WsMessage) => void, _r: unknown, onStatus?: unknown) => {
    frames.set(onStatus ? 'running' : 'needs', onMessage)
  },
}))
vi.mock('../../app/identity', () => ({ useIdentity: () => ({ name: '' }), firstNameOf: (n: string) => n }))
vi.mock('./PinnedTiles', () => ({ PinnedTiles: () => null }))
vi.mock('../../ui/surfaces/SurfaceOverlay', () => ({ SurfaceOverlay: () => null }))
vi.mock('./widgets/Suggestions', () => ({ Suggestions: () => null }))

import { HomeHarness, gateItem, waitingList } from '../../test/homeHarness'

function run(over: Partial<RunningRow> = {}): RunningRow {
  return {
    work: 'workflow:run-1', kind: 'workflow', session: '', phase: 'tool', line: 'Fetching the paper',
    since: '2026-09-26T09:00:05Z', name: 'paper-ingest', agent: '', started_at: '2026-09-26T09:00:00Z',
    route: '#/workflows/runs/run-1', blocked: false, waiting: [], ...over,
  }
}
const list = (live: RunningRow[] = [], finished: RunningRow[] = []): RunningList => ({ running: live, finished })
const RING: WsMessage = { type: 'running_changed', data: { changed: [{ work: 'workflow:run-1', session: '' }] } }

/** The gate a parked run raises, as the waiting list serves it. */
const question = gateItem({ kind: 'choice', prompt: 'Which paper next?', choices: ['A', 'B'] }, {
  id: 'gate:run-1:pick:1', work: 'workflow:run-1',
  answer: { method: 'POST', path: '/api/workflows/runs/run-1/resume', takes: ['choice'], body: { resume_token: 't' } },
  route: '#/workflows/runs/run-1',
})

function lane(name: string): HTMLElement {
  return screen.getByRole('region', { name })
}

function send(m: WsMessage): void {
  act(() => { for (const f of [...frames.values()]) f(m) })
}

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  frames.clear()
  waiting.mockResolvedValue(waitingList())
  running.mockResolvedValue(list([run()]))
})

describe('Home, while a workflow run changes', () => {
  it('🔑 a run that finishes leaves Running Now for Recent and completed without a reload', async () => {
    render(<HomeHarness />)
    expect(await within(lane('Running Now')).findByText('Fetching the paper')).toBeTruthy()

    running.mockResolvedValue(list([], [run({ phase: 'done', line: 'Done in 2 minutes', since: '2026-09-26T09:02:00Z' })]))
    send(RING)

    expect(await within(lane('Running Now')).findByText('Nothing is running right now.')).toBeTruthy()
    const tail = within(lane('Recent and completed')).getByRole('list', { name: 'Just finished' })
    expect(within(tail).getByText('Done in 2 minutes')).toBeTruthy()
  })

  it('🔑 a run that parks on a question says it waits on you, and its question is in Needs You', async () => {
    render(<HomeHarness />)
    expect(await within(lane('Running Now')).findByText('Fetching the paper')).toBeTruthy()

    // Still live, as the list counts a run waiting on you, and never said to be running.
    running.mockResolvedValue(list([run({ phase: 'input', line: 'Needs your answer', waiting: ['q-1'] })]))
    waiting.mockResolvedValue(waitingList([question]))
    send(RING)

    await waitFor(() => expect(within(lane('Needs You')).getByText('Which paper next?')).toBeTruthy())
    expect(within(lane('Running Now')).getByText('Waiting for your answer')).toBeTruthy()
    expect(within(lane('Running Now')).getByText('1 waiting on you')).toBeTruthy()
    expect(within(lane('Running Now')).queryByText('Fetching the paper')).toBeNull()
  })

  it('a run that starts while the page is open appears', async () => {
    running.mockResolvedValue(list())
    render(<HomeHarness />)
    expect(await within(lane('Running Now')).findByText('Nothing is running right now.')).toBeTruthy()

    running.mockResolvedValue(list([run({ work: 'workflow:run-2', name: 'nightly-digest' })]))
    send(RING)

    expect(await within(lane('Running Now')).findByText('nightly-digest')).toBeTruthy()
  })

  it('a listing hint reads no running list: the list rings for itself', async () => {
    render(<HomeHarness />)
    await within(lane('Running Now')).findByText('paper-ingest')
    const before = running.mock.calls.length

    send({ type: 'refresh', data: { kinds: ['workflow_runs', 'loops'] } })
    send({ type: 'work_phase', data: { work: 'workflow:run-1', phase: 'done' } })
    await new Promise((resolve) => setTimeout(resolve, 450))

    expect(running.mock.calls.length).toBe(before)
  })
})
