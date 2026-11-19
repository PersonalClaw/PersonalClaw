import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ChatPlanGate } from './ChatPlanGate'

// #5792: the plan panel offered Cancel plan mode only once a draft waited for review. While the
// plan was still drafting, or waiting for the running turn to end, it said so and gave no way out
// but the task-mode pill. Now Cancel is there in every state, and it does what it does for a draft
// under review: the plan is dropped, the pill goes back to the chat's earlier mode, and a run the
// plan parked is let go (the server's cancel drops the plan and its binding together).

const drafting = {
  id: 'chat-plan-1',
  kind: 'chat_plan',
  title: 'Plan',
  objective: 'Plan the work before anything runs.',
  status: 'drafting',
  artifact: { markdown: '' },
  comments: [],
}

const chatPlanSession = vi.fn()
const chatPlanCancel = vi.fn()

vi.mock('../../lib/api', () => ({
  api: {
    chatPlanSession: (...a: unknown[]) => chatPlanSession(...a),
    chatPlanCancel: (...a: unknown[]) => chatPlanCancel(...a),
    chatPlanEdit: vi.fn(),
    chatPlanComment: vi.fn(),
    chatPlanApprove: vi.fn(),
  },
}))

function planIs(binding: Record<string, unknown>) {
  chatPlanSession.mockResolvedValue({
    session: { project_id: 'c1', steps: [drafting] },
    awaiting_step_id: '',
    binding,
    task_mode: 'plan',
  })
}

beforeEach(() => {
  chatPlanSession.mockReset()
  chatPlanCancel.mockReset()
  chatPlanCancel.mockResolvedValue({ ok: true, task_mode: 'agent' })
})

describe('a plan not yet drafted', () => {
  it.each([
    ['while it drafts', {}, 'Drafting…'],
    ['while it waits for the turn to end', { after_turn: true }, 'Starts when this turn ends'],
    ['while it holds a parked run', { parked: true }, 'Run parked — resumes when you approve'],
  ])('can be cancelled %s', async (_when, binding, shown) => {
    planIs(binding)
    const onTaskMode = vi.fn()
    const user = userEvent.setup()
    render(<ChatPlanGate session="c1" refreshKey={0} onTaskMode={onTaskMode} />)

    expect(await screen.findByText(shown)).toBeTruthy()
    await user.click(screen.getByRole('button', { name: /Cancel plan mode/i }))

    await waitFor(() => expect(chatPlanCancel).toHaveBeenCalledWith('c1'))
    await waitFor(() => expect(onTaskMode).toHaveBeenCalledWith('agent'))
  })
})
