import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ChatPlanGate } from './ChatPlanGate'

// Approving a plan says how the work that follows runs (#3821). Approve & run it keeps today's
// behaviour and words, and its menu names the ways to run it, each saying what it changes:
// "Approve, and ask for each change", today's default, named; and "Approve, and run it in a fresh
// chat", a new chat with only the plan as its brief, which the gate opens. The fresh chat runs the
// whole plan, so it is offered only when this approval completes it. "Approve, and trust edits
// until this plan is done" waits for the mode it runs in (#4077), and is absent until then.

const step = (id: string, status = 'awaiting_review') => ({
  id, kind: 'chat_plan', title: 'Plan', objective: '', status,
  artifact: { markdown: '### Plan\n1. first' }, comments: [],
})

const chatPlanSession = vi.fn()
const chatPlanApprove = vi.fn()

vi.mock('../../lib/api', () => ({
  api: {
    chatPlanSession: (...a: unknown[]) => chatPlanSession(...a),
    chatPlanApprove: (...a: unknown[]) => chatPlanApprove(...a),
    chatPlanComment: vi.fn(),
    chatPlanEdit: vi.fn(),
    chatPlanCancel: vi.fn(),
  },
}))

function onePlan(steps = [step('chat-plan-1')]) {
  chatPlanSession.mockResolvedValue({
    session: { project_id: 'c1', steps }, awaiting_step_id: 'chat-plan-1', binding: {}, task_mode: 'plan',
  })
}

beforeEach(() => {
  chatPlanSession.mockReset()
  chatPlanApprove.mockReset()
  chatPlanApprove.mockResolvedValue({ ok: true, complete: true, resumed: false, task_mode: 'agent', fresh_session: null })
  onePlan()
})

const menuItems = () => within(screen.getByRole('menu', { name: 'Ways to approve' })).getAllByRole('menuitem').map((m) => m.textContent ?? '')

describe('Approve & run it says how the plan runs', () => {
  it('the button, clicked or with Enter, approves as today: here, asking for each change', async () => {
    const user = userEvent.setup()
    render(<ChatPlanGate session="c1" refreshKey={0} onTaskMode={() => {}} />)
    await user.click(await screen.findByRole('button', { name: /Approve & run it/ }))
    await waitFor(() => expect(chatPlanApprove).toHaveBeenCalledWith('c1', 'chat-plan-1', 'here'))
  })

  it('its menu names each way to run the plan, and none that is not built yet', async () => {
    const user = userEvent.setup()
    render(<ChatPlanGate session="c1" refreshKey={0} onTaskMode={() => {}} />)
    await user.click(await screen.findByRole('button', { name: 'More ways to approve' }))
    const items = menuItems()
    expect(items.map((t) => t.split('Runs here')[0].split('A new chat')[0])).toEqual([
      'Approve, and ask for each change',
      'Approve, and run it in a fresh chat',
    ])
    expect(items.join(' ')).not.toMatch(/trust edits/i)
  })

  it('running it in a fresh chat approves with run fresh, and opens that chat', async () => {
    chatPlanApprove.mockResolvedValue({ ok: true, complete: true, resumed: false, task_mode: 'agent', fresh_session: 'chat-9-1792387100' })
    const onOpenChat = vi.fn()
    const user = userEvent.setup()
    render(<ChatPlanGate session="c1" refreshKey={0} onTaskMode={() => {}} onOpenChat={onOpenChat} />)
    await user.click(await screen.findByRole('button', { name: 'More ways to approve' }))
    await user.click(screen.getByRole('menuitem', { name: /run it in a fresh chat/ }))
    await waitFor(() => expect(chatPlanApprove).toHaveBeenCalledWith('c1', 'chat-plan-1', 'fresh'))
    await waitFor(() => expect(onOpenChat).toHaveBeenCalledWith('chat-9-1792387100'))
  })

  it('offers no fresh chat while a step still waits after this one', async () => {
    onePlan([step('chat-plan-1'), step('chat-plan-2', 'pending')])
    const user = userEvent.setup()
    render(<ChatPlanGate session="c1" refreshKey={0} onTaskMode={() => {}} />)
    await user.click(await screen.findByRole('button', { name: 'More ways to approve' }))
    expect(menuItems().join(' ')).not.toMatch(/fresh chat/)
  })
})
