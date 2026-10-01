/**
 * Where she answers an approval, she can read all of what it would run.
 *
 * A subagent's start was announced as "subagent_run(Do the next meaningful step on this task, then
 * stop and report. Task: Draft a s)" on the Inbox, the bell and the detail pane, and nothing showed
 * the rest of the task. The start now carries its whole task as its input, and the decision under
 * the row is the one approval card, read live from the registry: a line of the input cut at a word,
 * all of it a click away, and where the call came from.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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
    ts: 0, session_title: '', agent: '', risk: 'caution', grant_agent: '',
    source_label: 'workflow “weekly” · step “work”', ...over,
  }
}

beforeEach(() => { approvals.mockReset() })

describe('an approval’s decision', () => {
  it('🔴 shows the whole task a subagent’s start would work on, and who asked', async () => {
    approvals.mockResolvedValue([pending({})])
    render(<ApprovalDecision approvalId="spawn:fa8ed388" />)
    const allow = await screen.findByRole('button', { name: /^Allow subagent_run/ })
    expect(allow).toBeTruthy()
    expect(screen.getByText('workflow “weekly” · step “work”')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Show all of what subagent_run would run' }))
    expect(screen.getByRole('group', { name: 'Tool arguments' }).textContent).toBe(TASK)
  })

  it('shows a short input whole, with nothing to open', async () => {
    approvals.mockResolvedValue([pending({ tool: 'bash', tool_input: '{"command": "ls -F"}', risk: 'safe' })])
    render(<ApprovalDecision approvalId="spawn:fa8ed388" />)
    await screen.findByRole('button', { name: /^Allow bash/ })
    expect(screen.getByText('bash({"command": "ls -F"})')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Show all/ })).toBeNull()
  })

  it('shows nothing extra for a call with no input', async () => {
    approvals.mockResolvedValue([pending({ tool: 'memory_recall', tool_input: '' })])
    render(<ApprovalDecision approvalId="spawn:fa8ed388" />)
    await screen.findByRole('button', { name: /^Allow memory_recall/ })
    expect(screen.queryByRole('button', { name: /Show all/ })).toBeNull()
  })
})
