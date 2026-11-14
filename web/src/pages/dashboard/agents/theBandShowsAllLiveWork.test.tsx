// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import { resetDataStore } from '../../../lib/data'
import type { RunningRow } from '../../../lib/api'

// ── Home's band shows all live work ──────────────────────────────────────────────────────────────
//
// Running now left Home: the band, "Your agents today", is where running work shows, so it must not
// hide any. It drew its live rows from the activity scene's fold, cut at 64 entities with failed
// loops ranked above working work, so a long list of failed loops pushed a running chat off it. It
// blanked whenever any of its reads failed, the schedule's included. Now its live rows are every
// row of the running list, it draws whenever that list answered (each other read settles on its
// own and a failed one is named), a stalled loop is an ask, and an agent's second run has its own
// Open.

const reads = vi.hoisted(() => ({
  running: [] as RunningRow[],
  loops: [] as unknown[],
  waiting: [] as unknown[],
  week: null as null | (() => Promise<unknown>),
  approvals: null as null | (() => Promise<unknown>),
}))

vi.mock('../../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    running: () => Promise.resolve({ running: reads.running, finished: [] }),
    status: () => Promise.resolve({ start_time: 100 }),
    chatSessions: () => Promise.resolve([]),
    uLoops: () => Promise.resolve(reads.loops),
    approvals: () => (reads.approvals ? reads.approvals() : Promise.resolve([])),
    waiting: () => Promise.resolve({ waiting: reads.waiting, unreadable: [] }),
    inboxOpen: () => Promise.resolve([]),
    skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
    agentsDay: (since: string, until: string) => Promise.resolve({ since, until, agents: [], cut: 0 }),
    spawnedAgents: () => Promise.resolve([]),
    triggersWeek: () => (reads.week ? reads.week() : Promise.resolve({ start: '', end: '', occurrences: [] })),
    agents: () => Promise.resolve({ agents: [], default_agent: 'PersonalClaw' }),
    usageBudget: () => Promise.resolve(null),
  },
}))
vi.mock('../../../lib/useChatSocket', () => ({ useChatSocket: () => {}, refreshKinds: () => [] }))

import { AgentDay } from './AgentDay'
import { waitingItem } from '../../../test/waiting'

function row(over: Partial<RunningRow> = {}): RunningRow {
  return {
    work: 'chat:c1', kind: 'chat', session: 'c1', phase: 'tool', line: 'Running shell', since: null,
    name: 'File the receipts', agent: '', started_at: null, route: '#/chat/c1', blocked: false, waiting: [],
    project_id: '', loop_kind: '', ...over,
  }
}
const band = () => screen.getByRole('list', { name: 'Your agents' })

beforeEach(() => {
  resetDataStore()
  reads.running = [row()]
  reads.loops = []
  reads.waiting = []
  reads.week = null
  reads.approvals = null
})
afterEach(cleanup)

describe('the band shows all live work', () => {
  it('🔴 70 failed loops and one running chat: the chat is drawn working', async () => {
    reads.loops = Array.from({ length: 70 }, (_, i) => ({
      id: `f${i}`, name: `Failed loop ${i}`, status: 'failed', agent: `Agent ${i}`, max_cycles: 0, total_cycles: 0,
      completed_at: Date.now() / 1000 - 3600 * 30, error_message: 'boom',
    }))
    render(<AgentDay />)
    const chat = await within(await screen.findByRole('list', { name: 'Your agents' }))
      .findByRole('listitem', { name: /^PersonalClaw, working: File the receipts/ })
    expect(chat).toBeInTheDocument()
  })

  it('🔴 a failed schedule read still draws the live rows, and says what it could not read', async () => {
    reads.week = () => Promise.reject(new Error('the schedule is down'))
    render(<AgentDay />)
    await waitFor(() => expect(within(band()).getByRole('listitem', { name: /^PersonalClaw, working: File the receipts/ })).toBeInTheDocument())
    expect(screen.getByRole('alert')).toHaveTextContent("Couldn't load the schedule.")
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
    expect(screen.queryByText(/this is unknown right now/)).toBeNull()
  })

  it('a failed read behind the scene (the approvals) still draws the live rows', async () => {
    reads.approvals = () => Promise.reject(new Error('approvals are down'))
    render(<AgentDay />)
    await waitFor(() => expect(within(band()).getByRole('listitem', { name: /^PersonalClaw, working: File the receipts/ })).toBeInTheDocument())
  })

  it('a stalled loop is an ask, answered on Needs you', async () => {
    reads.running = [row({ work: 'loop:l1', kind: 'loop', session: '', name: 'Docs', agent: 'Docs agent', phase: 'input', line: 'Stalled', route: '#/loops/l1' })]
    reads.waiting = [waitingItem({ id: 'loop_question:l1', kind: 'loop_question', work: 'loop:l1', session: '', consequence: 'asks',
      brief: { name: 'Docs', question: '', status: 'stagnant', why: 'No new findings in 3 cycles' },
      answer: { method: 'POST', path: '/api/loops/l1/nudge', takes: ['text'], body: {} }, route: '#/loops/l1' })]
    render(<AgentDay />)
    const ask = await within(await screen.findByRole('list', { name: 'Your agents' })).findByRole('listitem', { name: /^Docs agent, waiting on you: Docs has stalled/ })
    expect(within(ask).getByRole('link', { name: /^Answer:/ })).toHaveAttribute('href', '#/mission-control?open=waiting%3Aloop_question%3Al1')
    expect(screen.getByRole('link', { name: '1 need you' })).toHaveAttribute('href', '#/mission-control')
  })

  it('an agent’s second run has its own Open', async () => {
    reads.running = [
      row({ work: 'loop:a', kind: 'loop', session: '', name: 'Fix the flaky upload test', agent: 'Code agent', route: '#/code/a' }),
      row({ work: 'loop:b', kind: 'loop', session: '', name: 'Update the API reference', agent: 'Code agent', route: '#/code/b' }),
    ]
    render(<AgentDay />)
    const agent = await within(await screen.findByRole('list', { name: 'Your agents' })).findByRole('listitem', { name: /^Code agent, working:/ })
    expect(within(agent).getByRole('link', { name: 'Open Fix the flaky upload test' })).toHaveAttribute('href', '#/code/a')
    expect(within(agent).getByRole('link', { name: 'Open Update the API reference' })).toHaveAttribute('href', '#/code/b')
    expect(within(agent).getByRole('link', { name: 'Code agent' })).toHaveAttribute('href', '#/code/a')
  })

  it('a running list that cannot be read is unknown, never that nothing runs (the control)', async () => {
    const { api } = await import('../../../lib/api')
    vi.spyOn(api, 'running').mockRejectedValue(new Error('gateway down'))
    render(<AgentDay />)
    expect(await screen.findByText(/Couldn’t read what your agents did, so this is unknown right now\./)).toBeInTheDocument()
    expect(screen.queryByText(/Nothing ran/)).toBeNull()
  })
})
