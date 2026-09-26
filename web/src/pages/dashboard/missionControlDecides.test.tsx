// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDataStore } from '../../lib/data'
import { ApiError, type InboxItem, type PendingApproval } from '../../lib/api'
import type { WsMessage } from '../../lib/useChatSocket'

// ── Mission Control decides — with the REAL lane derivation ─────────────────────────────────────
//
// Measured on day 8: the page's header says "Approve, reject, and answer from here", and no card
// on it ever rendered Approve, Reject or an answer. `toLanes` built cards with no `approval` and no
// `item`, and `MissionControl` read exactly those; its own card type made every one optional, so
// the two shapes were assignable and typecheck passed. `missionControl.test.tsx` mocks `toLanes`
// and hand-builds the cards the view wanted, which is how the gap stayed green.
//
// So nothing here is mocked between the wire and the pixels except the wire itself: the three
// endpoints answer with the shapes the gateway sends (a pending approval AND the Inbox row the
// registry raises for it, plus a parked run's question), the REAL `toLanes` folds them, and the
// assertions are on the controls a user can reach and the calls they make.

const inboxOpen = vi.fn()
const approvals = vi.fn()
const chatSessions = vi.fn()
const resolveApproval = vi.fn()
const resumeWorkflowRun = vi.fn()
const socket: { onMessage: ((m: WsMessage) => void) | null } = { onMessage: null }

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    inboxOpen: (...a: unknown[]) => inboxOpen(...a),
    approvals: (...a: unknown[]) => approvals(...a),
    chatSessions: (...a: unknown[]) => chatSessions(...a),
    uLoops: () => Promise.resolve([]),
    resolveApproval: (...a: unknown[]) => resolveApproval(...a),
    resumeWorkflowRun: (...a: unknown[]) => resumeWorkflowRun(...a),
  },
}))
vi.mock('../../lib/useChatSocket', () => ({
  useChatSocket: (onMessage: (m: WsMessage) => void) => { socket.onMessage = onMessage },
}))

import { MissionControl } from './MissionControl'

/** The registry entry a cancelled-run repro left behind (day 8), as `GET /api/approvals` serves it. */
const PENDING: PendingApproval = {
  id: 'spawn:207dceb3', request_id: 'spawn:207dceb3', source: 'subagent',
  tool: 'subagent_run(Advance one round of deep research)', tool_purpose: '',
  session: 'workflow:df5827ca:sweep', ts: 100, session_title: '', agent: '', risk: '', grant_agent: '',
}

/** The Inbox row `_raise_approval_row` raises for it: same decision, second listing. */
const MIRROR: InboxItem = {
  id: 'agent_request_1', channel: 'native', channel_name: '', message: 'Approval needed: subagent_run',
  sender_id: 'system', sender_name: 'system', classification: 'needs_reply', confidence: 'needs_review',
  status: 'pending', item_kind: 'agent_request',
  refs: { approval: 'spawn:207dceb3', session: 'workflow:df5827ca:sweep' },
}

/** A parked run's question, as `workflows/needs_input.card_refs()` writes it. */
const QUESTION: InboxItem = {
  id: 'needs_input_1', channel: 'native', channel_name: 'deep-research', message: 'Which source first?',
  sender_id: 'agent', sender_name: 'deep-research', classification: 'needs_reply',
  confidence: 'needs_review', status: 'pending', item_kind: 'needs_input', created_at: 50,
  refs: {
    workflow: 'df5827ca',
    needs_input: {
      run_id: 'df5827ca', node_id: 'triage', blocker: 'Which source first?',
      choices: ['Papers', 'Blogs'], resume_token: 'tok-1', actionable: true,
    },
  },
}

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  socket.onMessage = null
  inboxOpen.mockResolvedValue([MIRROR, QUESTION])
  approvals.mockResolvedValue([PENDING])
  chatSessions.mockResolvedValue([])
})

const approveButtons = () => screen.queryAllByRole('button', { name: /^Approve .*subagent_run/ })

describe('Mission Control renders the verbs its header promises', () => {
  it('offers Approve and Reject on a pending approval — once, not once per listing', async () => {
    render(<MissionControl />)
    await waitFor(() => expect(approveButtons()).toHaveLength(1))
    expect(screen.getAllByRole('button', { name: /^Reject .*subagent_run/ })).toHaveLength(1)
    const lane = screen.getByRole('region', { name: 'Needs approval' })
    expect(within(lane).getAllByRole('listitem')).toHaveLength(1)
  })

  it('Approve goes through the one decision path, by the registry id', async () => {
    resolveApproval.mockResolvedValue({ ok: true })
    render(<MissionControl />)
    await waitFor(() => expect(approveButtons()).toHaveLength(1))
    await userEvent.click(approveButtons()[0])
    expect(resolveApproval).toHaveBeenCalledWith('spawn:207dceb3', 'approve')
    expect(await screen.findByRole('status')).toHaveTextContent('Approved.')
  })

  it('offers a parked run’s options as answers', async () => {
    resumeWorkflowRun.mockResolvedValue({ resumed: true })
    render(<MissionControl />)
    const papers = await screen.findByRole('button', { name: /^Answer .* — Papers$/ })
    expect(screen.getByRole('button', { name: /^Answer .* — Blogs$/ })).toBeTruthy()
    await userEvent.click(papers)
    expect(resumeWorkflowRun).toHaveBeenCalledWith('df5827ca', { answer: 'Papers', resume_token: 'tok-1' })
  })

  it('says nothing ran when the work that asked has ended — not "try again"', async () => {
    resolveApproval.mockRejectedValue(new ApiError(
      'Nothing was run: the workflow run that asked for it was cancelled.', 409, 'approval_owner_ended',
    ))
    render(<MissionControl />)
    await waitFor(() => expect(approveButtons()).toHaveLength(1))
    await userEvent.click(approveButtons()[0])
    const status = await screen.findByRole('status')
    expect(status).toHaveTextContent('Nothing was run: the workflow run that asked for it was cancelled.')
    expect(status).not.toHaveTextContent(/try again/i)
    expect(screen.queryByRole('alert')).toBeNull()
    await waitFor(() => expect(approveButtons()).toHaveLength(0))
  })

  it('re-reads on an approval frame, so a decision made elsewhere leaves this page too', async () => {
    render(<MissionControl />)
    await waitFor(() => expect(approveButtons()).toHaveLength(1))
    approvals.mockResolvedValue([])
    inboxOpen.mockResolvedValue([QUESTION])
    await act(async () => {
      socket.onMessage?.({ type: 'approval_resolved', data: { id: PENDING.id, outcome: 'cancelled' } } as WsMessage)
    })
    await waitFor(() => expect(approveButtons()).toHaveLength(0), { timeout: 3000 })
  })

  it('names the page with its own title, in the page chrome', async () => {
    render(<MissionControl />)
    expect(await screen.findByRole('heading', { level: 1, name: 'Mission Control' })).toBeTruthy()
  })
})
