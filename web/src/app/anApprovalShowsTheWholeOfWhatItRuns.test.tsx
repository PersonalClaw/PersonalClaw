/**
 * Where she answers an approval, she can read all of what it would run.
 *
 * A subagent's start was announced as "subagent_run(Do the next meaningful step on this task, then
 * stop and report. Task: Draft a s)" on the Inbox, the bell and the detail pane, and nothing showed
 * the rest of the task. The start now carries its whole task as its input, and the row shows a line
 * of it cut at a word; the decision under the row shows it whole, read live from the registry. An
 * input the row already shows whole (a short shell command) is not said twice.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { PendingApproval } from '../lib/api'

const approvals = vi.fn<() => Promise<PendingApproval[]>>()
vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  return { ...real, api: { ...real.api, approvals } }
})
vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

const { ApprovalDecision } = await import('./ApprovalDecision')

const TASK = 'Do the next meaningful step on this task, then stop and report. Task:\n'
  + 'Draft a short note describing three things an agent could take off your plate this week, and '
  + 'save it where you can find it again tomorrow morning before the standup.'

function pending(over: Partial<PendingApproval>): PendingApproval {
  return {
    id: 'spawn:fa8ed388', request_id: 'spawn:fa8ed388', source: 'subagent', tool: 'subagent_run',
    tool_input: TASK, tool_purpose: 'Starts a subagent on this task.', session: 'workflow:fb54446b:work',
    ts: 0, session_title: '', agent: '', risk: 'caution', grant_agent: '', ...over,
  }
}

beforeEach(() => { approvals.mockReset() })

describe('an approval’s decision', () => {
  it('🔴 shows the whole task a subagent’s start would work on', async () => {
    approvals.mockResolvedValue([pending({})])
    const row = 'The “work” step of a workflow run is waiting for your decision on subagent_run (risk: caution).\n'
      + 'Starts a subagent on this task.\n'
      + 'Do the next meaningful step on this task, then stop and report. Task: Draft a short note…'
    render(<ApprovalDecision approvalId="spawn:fa8ed388" shown={row} />)
    const whole = await screen.findByLabelText('What subagent_run would run')
    expect(whole.textContent).toBe(TASK)
    expect(screen.getByRole('button', { name: 'Approve: subagent_run' })).toBeTruthy()
  })

  it('does not repeat an input the row already shows whole', async () => {
    approvals.mockResolvedValue([pending({ tool: 'bash', tool_input: '{"command": "ls -F"}', risk: 'safe' })])
    const row = 'A workflow step is waiting for your decision on bash (risk: safe).\n{"command": "ls -F"}'
    render(<ApprovalDecision approvalId="spawn:fa8ed388" shown={row} />)
    await screen.findByRole('button', { name: 'Approve: bash' })
    expect(screen.queryByLabelText('What bash would run')).toBeNull()
  })

  it('shows nothing extra for a call with no input', async () => {
    approvals.mockResolvedValue([pending({ tool: 'memory_recall', tool_input: '' })])
    render(<ApprovalDecision approvalId="spawn:fa8ed388" shown="" />)
    await screen.findByRole('button', { name: 'Approve: memory_recall' })
    expect(screen.queryByLabelText(/would run/)).toBeNull()
  })
})
