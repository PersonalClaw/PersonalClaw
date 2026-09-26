import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import type { PendingApproval } from '../../lib/api'
import type { WsMessage } from '../../lib/useChatSocket'

// ── A cancelled run's page stops offering Approve/Reject ──────────────────────────────────────────
//
// Measured (day-8, run df5827ca): after Cancel, the run read "Cancelled" and its page still showed
// "This step needs your approval to run" with Approve and Reject — and approving it spawned the
// subagent. The backend now ends that approval with the run; this is the page's half: it reloaded
// only on the `approval` frame (raised), never on `approval_resolved` (ended), so an approval that
// left every other surface stayed on this one until something else happened to refetch.

const approvals = vi.fn<() => Promise<PendingApproval[]>>()
const resolveApproval = vi.fn<(id: string, action: 'approve' | 'reject') => Promise<{ ok: boolean }>>()
const socket: { handler: ((m: WsMessage) => void) | null } = { handler: null }

vi.mock('../../lib/useChatSocket', () => ({
  useChatSocket: (handler: (m: WsMessage) => void) => { socket.handler = handler },
}))
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return { ...real, api: { ...real.api, approvals, resolveApproval } }
})

const pending: PendingApproval = {
  id: 'spawn:207dceb3', source: 'subagent',
  tool: 'subagent_run(Advance one round of deep research)', tool_purpose: '',
  session: 'workflow:df5827ca:sweep', ts: 0, request_id: 'spawn:207dceb3',
  session_title: '', agent: '', risk: '', grant_agent: '',
}

beforeEach(() => {
  cleanup()
  socket.handler = null
  approvals.mockReset()
  resolveApproval.mockReset()
})

describe('the run page drops an approval the moment it ends', () => {
  it('reloads on approval_resolved, so a cancelled run offers nothing to approve', async () => {
    approvals.mockResolvedValue([pending])
    const { RunToolApprovals } = await import('./RunToolApprovals')
    await act(async () => { render(<RunToolApprovals runId="df5827ca" />) })
    expect(await screen.findByRole('button', { name: /Approve: subagent_run/ })).toBeTruthy()

    // The run was cancelled: the registry no longer lists it, and the frame says so.
    approvals.mockResolvedValue([])
    await act(async () => {
      socket.handler?.({
        type: 'approval_resolved',
        data: { id: 'spawn:207dceb3', request_id: 'spawn:207dceb3', session: 'workflow:df5827ca:sweep',
          approved: false, outcome: 'cancelled' },
      } as WsMessage)
    })

    await waitFor(() => expect(screen.queryByRole('button', { name: /Approve: subagent_run/ })).toBeNull())
    expect(screen.queryByText(/needs your approval/)).toBeNull()
    expect(resolveApproval).not.toHaveBeenCalled()
  })

  it('still reloads on a raised approval (the baseline)', async () => {
    approvals.mockResolvedValue([])
    const { RunToolApprovals } = await import('./RunToolApprovals')
    await act(async () => { render(<RunToolApprovals runId="df5827ca" />) })
    approvals.mockResolvedValue([pending])
    await act(async () => { socket.handler?.({ type: 'approval', data: pending } as unknown as WsMessage) })
    expect(await screen.findByRole('button', { name: /Approve: subagent_run/ })).toBeTruthy()
  })
})
