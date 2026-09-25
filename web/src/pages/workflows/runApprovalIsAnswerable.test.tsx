import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { PendingApproval } from '../../lib/api'

// ── #258, the other half: the run view must be able to ANSWER the approval it is sent ────────────
//
// A `stage` node that spawns a subagent blocks on the global approvals queue, not on an engine
// gate. The run view rendered `WorkflowAsk` for a gate and NOTHING for a tool approval — the node
// rows offered only "Re-run" — so the user who pressed Run and was watching it had no control, and
// the dashboard's Action Center was the only surface that could resolve it.
//
// 🪤 FIXING ONLY THE LINK WOULD BE A NICER DEAD END. `approvalNudgeResolves.test.tsx` proves the
// nudge now lands on `#/workflows/runs/<run>?node=<node>`; that is worth nothing unless the
// question can be answered there. So this asserts the REACHABLE control and its EFFECT — the row
// is on screen and pressing Approve calls `resolveApproval` with that approval's id — not that a
// component was imported.
//
// 🪤 AND THE FILTER IS THE WHOLE MECHANISM. `/api/approvals` is global. A card that rendered every
// pending approval would pass the positive leg while showing one run another run's question, so the
// foreign-approval and chat-approval legs below are load-bearing, not decoration.

const approvals = vi.fn<() => Promise<PendingApproval[]>>()
const resolveApproval = vi.fn<(id: string, action: 'approve' | 'reject') => Promise<{ ok: boolean }>>()

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return { ...real, api: { ...real.api, approvals, resolveApproval } }
})

const approval = (over: Partial<PendingApproval> = {}): PendingApproval => ({
  id: 'spawn:b961a327', source: 'subagent',
  tool: 'subagent_run(Write ONE consolidated article)',
  tool_purpose: 'Consolidate the recalled material into one article',
  session: 'workflow:11b9a34c:synthesize', ts: 0,
  request_id: 'spawn:b961a327', session_title: '', agent: '', risk: '', grant_agent: '', ...over,
})

async function mountFor(runId: string, queue: PendingApproval[]) {
  approvals.mockReset(); resolveApproval.mockReset()
  approvals.mockResolvedValue(queue)
  resolveApproval.mockResolvedValue({ ok: true })
  const { RunToolApprovals } = await import('./RunToolApprovals')
  await act(async () => {
    render(<RunToolApprovals runId={runId} />)
    await new Promise((res) => setTimeout(res, 0))
  })
}

beforeEach(() => { cleanup() })

describe('a run view can answer the approval its own stage raised', () => {
  it('shows the blocked call with Approve and Reject', async () => {
    await mountFor('11b9a34c', [approval()])
    expect(await screen.findByRole('button', { name: /Approve: subagent_run/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Reject: subagent_run/ })).toBeTruthy()
    // The purpose is what tells the watcher what they are approving.
    expect(screen.getByText(/Consolidate the recalled material/)).toBeTruthy()
  })

  it('Approve RESOLVES that approval — the effect, not the button', async () => {
    await mountFor('11b9a34c', [approval()])
    const btn = await screen.findByRole('button', { name: /Approve: subagent_run/ })
    await userEvent.click(btn)
    await waitFor(() => expect(resolveApproval).toHaveBeenCalledWith('spawn:b961a327', 'approve'))
  })

  it('Reject resolves it the other way', async () => {
    await mountFor('11b9a34c', [approval()])
    await userEvent.click(await screen.findByRole('button', { name: /Reject: subagent_run/ }))
    await waitFor(() => expect(resolveApproval).toHaveBeenCalledWith('spawn:b961a327', 'reject'))
  })
})

describe('🪤 VACUITY: it claims ONLY its own run\'s approvals', () => {
  it('another run\'s approval is not shown here', async () => {
    await mountFor('11b9a34c', [approval({ session: 'workflow:deadbeef:synthesize' })])
    await waitFor(() => expect(approvals).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: /Approve:/ })).toBeNull()
  })

  it('a CHAT approval is not shown here — the chat card owns that one', async () => {
    await mountFor('11b9a34c', [approval({ session: 'main' })])
    await waitFor(() => expect(approvals).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: /Approve:/ })).toBeNull()
  })

  it('an empty queue renders nothing at all (no empty card on every run)', async () => {
    const { container } = await (async () => {
      await mountFor('11b9a34c', [])
      return { container: document.body }
    })()
    await waitFor(() => expect(approvals).toHaveBeenCalled())
    expect(container.querySelectorAll('button')).toHaveLength(0)
  })
})
