import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ChatPlanGate } from './ChatPlanGate'

// Driven live before this test existed: editing the plan, pressing "Save edits" and
// watching the panel STAY in edit mode. The save had persisted — the server already held
// the new markdown — but the only remaining control was "Cancel", a word that reads as
// "throw away what I just wrote". So the one path back to "Approve & run it" looked like
// the path that discarded the edit, on the surface whose entire job is a deliberate
// review. Saving now returns to review.
//
// The gate is mounted directly (not through ChatPage) so the assertion is about this
// component's own state machine rather than the page's turn bookkeeping.

const step = {
  id: 'chat-plan-1',
  kind: 'chat_plan',
  title: 'Plan',
  objective: 'Plan the work before anything runs.',
  status: 'awaiting_review',
  artifact: { markdown: '### Plan\n1. first' },
  comments: [],
}

const chatPlanSession = vi.fn()
const chatPlanEdit = vi.fn()

vi.mock('../../lib/api', () => ({
  api: {
    chatPlanSession: (...a: unknown[]) => chatPlanSession(...a),
    chatPlanEdit: (...a: unknown[]) => chatPlanEdit(...a),
    chatPlanComment: vi.fn(),
    chatPlanApprove: vi.fn(),
    chatPlanCancel: vi.fn(),
  },
}))

beforeEach(() => {
  chatPlanSession.mockReset()
  chatPlanEdit.mockReset()
  chatPlanSession.mockResolvedValue({
    session: { project_id: 'c1', steps: [step] },
    awaiting_step_id: 'chat-plan-1',
    binding: {},
    task_mode: 'plan',
  })
  chatPlanEdit.mockResolvedValue({ ok: true, session: { project_id: 'c1', steps: [step] } })
})

describe('the plan gate’s editor', () => {
  it('returns to review after a successful save, so Approve is reachable', async () => {
    const user = userEvent.setup()
    render(<ChatPlanGate session="c1" refreshKey={0} onTaskMode={() => {}} />)

    await user.click(await screen.findByLabelText('Edit this plan'))
    const box = await screen.findByLabelText('Plan markdown')
    await user.clear(box)
    await user.type(box, 'edited by the operator')
    await user.click(screen.getByRole('button', { name: /Save edits/i }))

    await waitFor(() => expect(chatPlanEdit).toHaveBeenCalledTimes(1))
    expect(chatPlanEdit.mock.calls[0][2]).toBe('edited by the operator')

    // The editor is gone and the review controls are back — no "Cancel" required.
    await waitFor(() => expect(screen.queryByLabelText('Plan markdown')).toBeNull())
    expect(screen.getByRole('button', { name: /Approve & run it/i })).toBeTruthy()
  })

  it('keeps the editor open when the save fails, so the text is not lost', async () => {
    const user = userEvent.setup()
    chatPlanEdit.mockRejectedValue(new Error('storage is read-only'))
    render(<ChatPlanGate session="c1" refreshKey={0} onTaskMode={() => {}} />)

    await user.click(await screen.findByLabelText('Edit this plan'))
    const box = await screen.findByLabelText('Plan markdown')
    await user.clear(box)
    await user.type(box, 'work I do not want to retype')
    await user.click(screen.getByRole('button', { name: /Save edits/i }))

    await waitFor(() => expect(chatPlanEdit).toHaveBeenCalled())
    // Still editing, still holding the text, and the failure is stated.
    const still = await screen.findByLabelText('Plan markdown')
    expect((still as HTMLTextAreaElement).value).toBe('work I do not want to retype')
    expect(screen.getByText(/read-only/i)).toBeTruthy()
  })
})
