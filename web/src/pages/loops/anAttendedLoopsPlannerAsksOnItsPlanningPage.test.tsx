import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { PendingApproval, PlanSession } from '../../lib/api'
import type { WsMessage } from '../../lib/useChatSocket'
import { approvalDestination, loopApprovalOf, loopApprovalSession } from '../../app/approvalDestination'

// ── An Attended loop's planner asks for its tool calls, and its planning page answers them ──────
//
// Measured: an Attended Code loop's planner ran 23 tool calls with no approval (the codebase's
// test suite among them), because every planner ran on a standing grant whatever the loop's Mode.
// Now an Attended loop's planner asks as its workers do, and its session is no chat anyone opened:
// the walkthrough you plan the loop on carries the card, and the nudge and the Inbox row lead
// there.

const { STORE, approve } = vi.hoisted(() => ({
  STORE: { pending: [] as PendingApproval[] },
  approve: vi.fn(),
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    uLoopPlanSession: () => Promise.resolve({
      project_id: LOOP_ID, created_at: Date.now() / 1000, updated_at: Date.now() / 1000, steps: [],
    } as unknown as PlanSession),
    uLoopPlanStart: () => Promise.resolve({}),
    uLoop: () => Promise.resolve({ id: LOOP_ID, status: 'planning' }),
    approvals: () => Promise.resolve(STORE.pending),
    approve: (...args: unknown[]) => approve(...args),
  },
}))
vi.mock('../../lib/useChatSocket', () => ({
  useChatSocket: (_cb: (m: WsMessage) => void) => {},
}))

const { CodePlanningView } = await import('../code/CodePlanningView')
const { LoopPlanningView } = await import('./LoopPlanningView')

const LOOP_ID = '0a1b2c3d'
const PLANNER = `loop-plan-${LOOP_ID}`

function plannerAsk(over: Partial<PendingApproval> = {}): PendingApproval {
  return {
    id: `chat:${PLANNER}:call-1`, request_id: 'call-1', source: '', tool: 'bash',
    tool_input: '{"command": "uv run pytest -q"}', tool_purpose: '', session: PLANNER, ts: 0,
    session_title: '', agent: 'personalclaw-code-planner', risk: 'destructive', grant_agent: '',
    ...over,
  }
}

const noop = () => Promise.resolve(true)

beforeEach(() => {
  STORE.pending = []
  approve.mockReset()
  approve.mockResolvedValue({ ok: true })
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
})
afterEach(() => cleanup())

describe('where the planner’s ask is answered', () => {
  it('the nudge and the Inbox row lead to the loop, which is planning', () => {
    expect(approvalDestination(PLANNER).href).toBe(`#/loops/${LOOP_ID}`)
    expect(loopApprovalOf(PLANNER, LOOP_ID)).toBe(true)
    expect(loopApprovalSession(PLANNER)).toEqual({ loopId: LOOP_ID, taskId: '', planner: true })
    expect(loopApprovalSession(`loop-${LOOP_ID}`)).toEqual({ loopId: LOOP_ID, taskId: '', planner: false })
  })

  it.each([
    ['a Code loop', (p: { onBack: () => void }) => <CodePlanningView projectId={LOOP_ID} onReady={() => {}} onBack={p.onBack} onCancel={noop} onStop={noop} />],
    ['any other loop', (p: { onBack: () => void }) => <LoopPlanningView loopId={LOOP_ID} onReady={() => {}} onBack={p.onBack} onCancel={noop} onStop={noop} />],
  ])('the planning walkthrough of %s carries the planner’s card, and Deny answers it', async (_name, view) => {
    STORE.pending = [plannerAsk(), plannerAsk({ id: 'other', session: 'loop-plan-99999999' })]
    render(view({ onBack: () => {} }))

    await waitFor(() => expect(screen.queryAllByText('Permission needed')).toHaveLength(1))
    fireEvent.click(screen.getByRole('button', { name: /^Deny bash/ }))
    await waitFor(() => expect(approve).toHaveBeenCalledWith(PLANNER, 'rejected', 'call-1'))
  })

  it('🪤 VACUITY: with nothing waiting, the walkthrough shows no card', async () => {
    render(<LoopPlanningView loopId={LOOP_ID} onReady={() => {}} onBack={() => {}} onCancel={noop} onStop={noop} />)
    expect(await screen.findByText(/The planner is preparing the steps/)).toBeTruthy()
    expect(screen.queryAllByText('Permission needed')).toHaveLength(0)
  })
})
