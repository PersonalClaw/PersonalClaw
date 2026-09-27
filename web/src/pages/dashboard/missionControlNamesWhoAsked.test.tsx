// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { resetDataStore } from '../../lib/data'
import type { InboxItem, PendingApproval } from '../../lib/api'

// ── Mission Control names what asked (ledger 295) ─────────────────────────────────────────────
//
// Measured on `main`: every card a workflow's gate, a trigger's question or the control bridge
// raised read "loop", and an approval card said nothing about who wanted the call. Each Inbox row
// here is shaped the way `emit_attention_item` writes it — sender and channel are the notification
// pair's source, which for all of these is "loop" — and each approval the way
// `approval_state._approval_entry` does. The lane derivation is the REAL one, so what is asserted
// is the card a user sees.

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
  },
}))

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

import { MissionControl } from './MissionControl'

function onThePair(id: string, message: string, refs: Record<string, unknown>): InboxItem {
  return {
    id, channel: 'loop', channel_name: 'loop', message, sender_id: 'loop', sender_name: 'loop',
    classification: 'needs_reply', confidence: 'high', status: 'pending', item_kind: 'needs_input',
    created_at: 100, refs,
  } as InboxItem
}

function approval(over: Partial<PendingApproval>): PendingApproval {
  return {
    id: 'a', request_id: 'a', source: 'subagent', tool: 'subagent_run', tool_purpose: '',
    session: '', ts: 100, session_title: '', agent: '', risk: '', grant_agent: '',
    trigger: '', trigger_name: '', ...over,
  } as PendingApproval
}

function card(lane: string, title: string): HTMLElement {
  const region = screen.getByRole('region', { name: lane })
  const heading = within(region).getByText(title)
  const li = heading.closest('li')
  if (!li) throw new Error(`no card titled ${title}`)
  return li
}

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  chatSessions.mockResolvedValue([])
  uLoops.mockResolvedValue([])
  workflowRuns.mockResolvedValue({ runs: [], total: 0, limit: 200, offset: 0 })
  inboxOpen.mockResolvedValue([
    onThePair('gate', 'Ship the release to production?', {
      workflow: 'run-1', workflow_name: 'release', workflow_node: 'approve', resume_token: 't1',
    }),
    onThePair('park', 'Sign in to example.com, then confirm', {
      trigger_park: 'balance', trigger: 'balance', trigger_name: 'Check my balance', resume_token: 't2',
    }),
    onThePair('wait', 'Loop needs your input', { loop: 'abc123', loop_kind: 'general' }),
    onThePair('bridge', 'Confirm a control-bridge action', {
      source: 'control_bridge', action: 'restart', confirmation: 'c1',
    }),
  ])
  approvals.mockResolvedValue([
    approval({ id: 'chat', source: '', tool: 'shell.run', session: 'dashboard:abc', session_title: 'Trip planning' }),
    approval({ id: 'step', tool: 'web.search', session: 'workflow:df5827ca:sweep' }),
    approval({ id: 'fire', tool: 'notes.write', session: 'cron:balance', trigger: 'balance', trigger_name: 'Check my balance' }),
    approval({ id: 'worker', tool: 'fs.write', session: 'loop-abc123' }),
  ])
})

describe('Mission Control names what asked', () => {
  it('🔑 a question card names the workflow, trigger, loop or bridge that asked — not "loop"', async () => {
    render(<MissionControl />)
    await screen.findByText('Ship the release to production?')

    expect(within(card('Your turn', 'Ship the release to production?')).getByText('Workflow · release')).toBeTruthy()
    expect(within(card('Your turn', 'Sign in to example.com, then confirm')).getByText('Trigger · Check my balance')).toBeTruthy()
    expect(within(card('Your turn', 'Loop needs your input')).getByText('Loop')).toBeTruthy()
    expect(within(card('Your turn', 'Confirm a control-bridge action')).getByText('Control bridge')).toBeTruthy()
    // The pair's source reaches no card.
    const lane = screen.getByRole('region', { name: 'Your turn' })
    expect(within(lane).queryByText('loop')).toBeNull()
  })

  it('🔑 an approval card names the chat, workflow step, trigger or loop the call is for', async () => {
    render(<MissionControl />)
    await screen.findByText('shell.run')

    expect(within(card('Needs approval', 'shell.run')).getByText('Chat · Trip planning')).toBeTruthy()
    expect(within(card('Needs approval', 'web.search')).getByText('Workflow · sweep step')).toBeTruthy()
    expect(within(card('Needs approval', 'notes.write')).getByText('Trigger · Check my balance')).toBeTruthy()
    expect(within(card('Needs approval', 'fs.write')).getByText('Loop')).toBeTruthy()
    // A bare session key is not a line a person reads.
    expect(screen.queryByText('workflow:df5827ca:sweep')).toBeNull()
  })

  it('the name rides the card\'s verbs too, so two cards\' buttons are told apart by it', async () => {
    render(<MissionControl />)
    await screen.findByText('web.search')

    expect(screen.getByRole('button', { name: /^Approve web\.search — Workflow · sweep step/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /^Approve notes\.write — Trigger · Check my balance/ })).toBeTruthy()
  })
})
