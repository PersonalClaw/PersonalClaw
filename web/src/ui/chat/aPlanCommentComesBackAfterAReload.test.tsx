import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ChatPlanGate } from './ChatPlanGate'

// An unsent comment on a chat's plan step comes back after a reload (#3821's local-draft recovery):
// it is kept for that draft of that step until it is sent, and a new draft of the step starts with
// none, as it always has. Nothing is sent until Send.

const step = (revision: string) => ({
  id: 'chat-plan-1', kind: 'chat_plan', title: 'Plan', objective: '', status: 'awaiting_review', revision,
  artifact: { markdown: '### Plan\n1. first' }, comments: [],
})

const h = vi.hoisted(() => ({ revision: 'r1', chatPlanComment: vi.fn() }))

vi.mock('../../lib/api', () => ({
  api: {
    chatPlanSession: () => Promise.resolve({
      session: { project_id: 'c1', steps: [step(h.revision)] }, awaiting_step_id: 'chat-plan-1', binding: {}, task_mode: 'plan',
    }),
    chatPlanApprove: vi.fn(),
    chatPlanComment: (...a: unknown[]) => h.chatPlanComment(...a),
    chatPlanEdit: vi.fn(),
    chatPlanCancel: vi.fn(),
  },
}))

beforeEach(() => {
  localStorage.clear()
  h.revision = 'r1'
  h.chatPlanComment.mockReset()
  h.chatPlanComment.mockResolvedValue({ ok: true })
})
afterEach(cleanup)

const gate = () => render(<ChatPlanGate session="c1" refreshKey={0} onTaskMode={() => {}} />)
const field = () => screen.findByRole('textbox', { name: 'Comment on this plan' })

describe('a comment on a chat plan step', () => {
  it('comes back unsent after a reload', async () => {
    gate()
    fireEvent.change(await field(), { target: { value: 'Split step 2 in two' } })
    cleanup()
    gate()
    await waitFor(async () => expect(await field()).toHaveValue('Split step 2 in two'))
    expect(h.chatPlanComment).not.toHaveBeenCalled()
  })

  it('is sent only by Send, and is gone once sent', async () => {
    gate()
    fireEvent.change(await field(), { target: { value: 'Split step 2 in two' } })
    expect(h.chatPlanComment).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: /Send comment/ }))
    await waitFor(() => expect(h.chatPlanComment).toHaveBeenCalledWith('c1', 'chat-plan-1', 'Split step 2 in two'))
    cleanup()
    gate()
    await new Promise((r) => setTimeout(r, 0))
    expect(await field()).toHaveValue('')
  })

  it('starts empty on a new draft of the step', async () => {
    gate()
    fireEvent.change(await field(), { target: { value: 'About the first draft' } })
    cleanup()
    h.revision = 'r2'
    gate()
    await new Promise((r) => setTimeout(r, 0))
    expect(await field()).toHaveValue('')
  })
})
