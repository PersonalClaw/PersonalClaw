// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDataStore } from '../../lib/data'
import type { InboxItem, PendingApproval, WaitingList } from '../../lib/api'
import type { WsMessage } from '../../lib/useChatSocket'

// ── Mission Control's Needs-approval and Your-turn lanes are the waiting list's (#3888) ─────────
//
// The lanes were folded from six reads, and a loop's question or a workflow's gate showed only if
// its Inbox row did. They are now the gateway's one list of what waits on you (`GET /api/waiting`):
// each item once, in its order (by consequence, then age), an approval on the one approval card, a
// gate and a park answered in place with the token you hold, every other item opening its own
// page. An Inbox row that is an item's listing is carded once, as the item; one whose item is not
// in the list keeps its own card. An item past answering keeps no verb that could still send.
// Nothing is mocked between the wire and the pixels but the wire: the REAL `toLanes` folds it.

const inboxOpen = vi.fn()
const waiting = vi.fn<() => Promise<WaitingList>>()
const resolveApproval = vi.fn()
const confirmWorkflowRun = vi.fn()
const answerTriggerPark = vi.fn()
const socket: { onMessage: ((m: WsMessage) => void) | null } = { onMessage: null }

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    inboxOpen: (...a: unknown[]) => inboxOpen(...a),
    waiting: () => waiting(),
    running: () => Promise.resolve({ running: [], finished: [] }),
    status: () => Promise.resolve({}),
    skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
    resolveApproval: (...a: unknown[]) => resolveApproval(...a),
    confirmWorkflowRun: (...a: unknown[]) => confirmWorkflowRun(...a),
    answerTriggerPark: (...a: unknown[]) => answerTriggerPark(...a),
  },
}))
vi.mock('../../lib/useChatSocket', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatSocket: (onMessage: (m: WsMessage) => void) => { socket.onMessage = onMessage },
}))

import { MissionControl } from './MissionControl'
import { waitingApproval, waitingItem, waitingList } from '../../test/waiting'

const now = Date.now()
const ago = (secs: number) => new Date(now - secs * 1000).toISOString()

/** A destructive approval asked a minute ago. */
const DELETES: PendingApproval = {
  id: 'ap-1', request_id: 'ap-1', source: 'cron', tool: 'shell', tool_input: 'rm -rf build',
  tool_purpose: '', session: '', ts: Math.round(now / 1000) - 60, session_title: '', agent: '',
  risk: 'destructive', grant_agent: '', source_label: 'automation “Nightly”', confirms: [],
}

/** A workflow run's confirmation gate, asked ten minutes ago: older, and only changes something. */
const GATE = waitingItem({
  kind: 'gate', id: 'gate:r1:conf-1:0', work: 'workflow:r1', consequence: 'changes', asked_at: ago(600),
  brief: { workflow: 'release', node_id: 'approve', ask: { kind: 'approval', prompt: 'Ship v1.2?' } },
  answer: { method: 'POST', path: '/api/workflows/runs/r1/confirm', takes: ['approve', 'reject'], body: { resume_token: 'tok-gate' } },
  route: '#/workflows/runs/r1',
})

/** A loop's question: it only asks. */
const LOOP = waitingItem({
  kind: 'loop_question', id: 'loop_question:l1', work: 'loop:l1', consequence: 'asks', asked_at: ago(120),
  brief: { name: 'Sort the inbox', question: 'May I archive last year’s mail?' },
  answer: { method: 'POST', path: '/api/loops/l1/nudge', takes: ['text'], body: {} },
  route: '#/loops/l1',
})

/** A trigger's parked action. */
const PARK = waitingItem({
  kind: 'park', id: 'park:schedule:clock:nightly', work: 'trigger:schedule:clock:nightly', consequence: 'changes',
  asked_at: ago(30), brief: { trigger_name: 'Nightly', question: 'Sign in to the bank’s site' },
  answer: { method: 'POST', path: '/api/triggers/schedule%3Aclock%3Anightly/answer', takes: [true, false], body: { resume_token: 'tok-park' } },
  route: '#/triggers?open=schedule%3Aclock%3Anightly',
})

const row = (id: string, message: string, refs: Record<string, unknown>): InboxItem => ({
  id, channel: 'native', channel_name: '', message, sender_id: 'loop', sender_name: 'loop',
  classification: 'needs_reply', confidence: 'needs_review', status: 'pending', item_kind: 'needs_input',
  created_at: Math.round(now / 1000) - 10, refs,
})

/** The Inbox rows the gate's, the loop's and the park's sources raise beside them. */
const GATE_ROW = row('needs_input_gate', 'Ship v1.2?', {
  workflow: 'r1', workflow_node: 'approve', resume_token: 'tok-gate',
  needs_input: { run_id: 'r1', node_id: 'approve', block_kind: 'approval', blocker: 'Ship v1.2?', resume_token: 'tok-gate' },
})
const LOOP_ROW = row('needs_input_loop', 'Sort the inbox needs your answer', { loop: 'l1', dedup_key: 'loop:l1:needs_input:3' })
const PARK_ROW = row('needs_input_park', 'Sign in to the bank’s site', { trigger_park: 'schedule:clock:nightly', resume_token: 'tok-park' })
/** A blocked loop's row: no item of the list is its listing, so it keeps its own card. */
const BLOCKED_ROW = row('needs_input_blocked', 'Tidy the repo is blocked', { loop: 'l2', dedup_key: 'loop:l2:blocked:1' })

function lane(name: string): HTMLElement {
  return screen.getByRole('heading', { name, level: 2 }).closest('section') as HTMLElement
}

function cardsIn(name: string): string[] {
  return [...lane(name).querySelectorAll(':scope > ul > li')].map((li) => li.textContent ?? '')
}

beforeEach(() => {
  resetDataStore()
  inboxOpen.mockReset().mockResolvedValue([])
  waiting.mockReset()
  resolveApproval.mockReset().mockResolvedValue({ ok: true })
  confirmWorkflowRun.mockReset().mockResolvedValue({ ok: true })
  answerTriggerPark.mockReset().mockResolvedValue({ ok: true })
  socket.onMessage = null
})

describe('the lanes are the waiting list’s items', () => {
  it('cards a gate and a loop question beside an approval, by consequence then age', async () => {
    waiting.mockResolvedValue(waitingList(waitingApproval(DELETES), GATE, LOOP))
    render(<MissionControl />)
    await screen.findByText('Ship v1.2?')
    const needs = cardsIn('Needs approval')
    expect(needs).toHaveLength(2)
    // The destructive approval first, though the gate has waited longer: consequence, then age.
    expect(needs[0]).toContain('shell')
    expect(needs[1]).toContain('Ship v1.2?')
    expect(needs[1]).toContain('Workflow “release”')
    const yours = cardsIn('Your turn')
    expect(yours).toHaveLength(1)
    expect(yours[0]).toContain('May I archive last year’s mail?')
  })

  it('answers a gate and a park in place, with the token you hold, and opens the rest', async () => {
    waiting.mockResolvedValue(waitingList(GATE, PARK, LOOP))
    render(<MissionControl />)
    await userEvent.click(await screen.findByRole('button', { name: /^Approve Ship v1\.2\?/ }))
    expect(confirmWorkflowRun).toHaveBeenCalledWith('r1', { verb: 'approve', resume_token: 'tok-gate' })
    await userEvent.click(screen.getByRole('button', { name: /^Deny Sign in to the bank’s site/ }))
    expect(answerTriggerPark).toHaveBeenCalledWith('schedule:clock:nightly', { resume_token: 'tok-park', answer: false })
    const open = within(lane('Your turn')).getByRole('link', { name: /^Open loop: May I archive/ })
    expect(open.getAttribute('href')).toBe('#/loops/l1')
  })

  it('cards an item once though its Inbox row lists it too, and keeps a row the list does not hold', async () => {
    waiting.mockResolvedValue(waitingList(GATE, LOOP, PARK))
    inboxOpen.mockResolvedValue([GATE_ROW, LOOP_ROW, PARK_ROW, BLOCKED_ROW])
    render(<MissionControl />)
    await screen.findByText('Ship v1.2?')
    expect(cardsIn('Needs approval')).toHaveLength(2)
    expect(cardsIn('Your turn').filter((t) => t.includes('May I archive'))).toHaveLength(1)
    expect(screen.queryByText('Sort the inbox needs your answer')).toBeNull()
    expect(screen.getByText('Tidy the repo is blocked')).toBeTruthy()
  })

  it('says a source it could not read, rather than an empty lane', async () => {
    waiting.mockResolvedValue({ waiting: [], unreadable: ['gate', 'park'] })
    render(<MissionControl />)
    expect(await screen.findByText(/could not be read: Workflow, Automation/)).toBeTruthy()
  })

  it('reads the list again when it rings', async () => {
    waiting.mockResolvedValue(waitingList(LOOP))
    render(<MissionControl />)
    await screen.findByText('May I archive last year’s mail?')
    waiting.mockResolvedValue(waitingList())
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      act(() => socket.onMessage?.({ type: 'waiting_changed', data: {} }))
      await act(async () => { vi.advanceTimersByTime(400) })
    } finally {
      vi.useRealTimers()
    }
    await waitFor(() => expect(screen.queryByText('May I archive last year’s mail?')).toBeNull())
  })
})

describe('an item is answered on its own route alone', () => {
  it('an item whose route is another kind’s, or climbs out of its own, is listed with no verb and sends nothing', async () => {
    const asA = (path: string) => ({ method: 'POST', path, takes: ['approve', 'reject'], body: { resume_token: 'tok' } })
    waiting.mockResolvedValue(waitingList(
      // An approval handed a browser grant's route.
      { ...waitingApproval(DELETES), answer: asA('/api/browse/grants/g-1/{answer}') },
      // A gate whose run id decodes to `..`, and a park handed a run's route.
      { ...GATE, answer: asA('/api/workflows/runs/%2E%2E/confirm') },
      { ...PARK, answer: asA('/api/workflows/runs/r1/resume') },
    ))
    render(<MissionControl />)
    await screen.findByText('Ship v1.2?')
    expect(cardsIn('Needs approval')).toHaveLength(3)
    expect(within(lane('Needs approval')).queryAllByRole('button')).toEqual([])
    expect(resolveApproval).not.toHaveBeenCalled()
    expect(confirmWorkflowRun).not.toHaveBeenCalled()
    expect(answerTriggerPark).not.toHaveBeenCalled()
  })
})

describe('an item past answering keeps no verb that could still send', () => {
  it('an expired gate and an expired approval say so, and offer nothing to press', async () => {
    const gone = ago(1)
    waiting.mockResolvedValue(waitingList(waitingApproval(DELETES, { expires_at: gone }), { ...GATE, expires_at: gone }))
    render(<MissionControl />)
    await screen.findByText('Ship v1.2?')
    expect(within(lane('Needs approval')).getAllByText('Expired — it can no longer be answered.')).toHaveLength(2)
    expect(within(lane('Needs approval')).queryAllByRole('button')).toEqual([])
    expect(confirmWorkflowRun).not.toHaveBeenCalled()
    expect(resolveApproval).not.toHaveBeenCalled()
  })
})
