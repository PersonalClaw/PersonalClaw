/**
 * An approval nobody answered says it expired, and why — never that it was handled.
 *
 * At a gateway shutdown two approvals a batch run was waiting on were closed in the Inbox as
 * "Handled", with a check mark, though nobody had decided anything. Such a row is now `expired`,
 * settled like a handled one but labelled as what happened, and its decision says why it ended
 * (`refs.ended`) in place of a decision nothing awaits any more.
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
const { statusMeta, isSettled } = await import('../pages/inbox/inboxMeta')
const { isOpenStatus } = await import('../lib/attentionLanes')

beforeEach(() => { approvals.mockReset() })

describe('an approval that ended unanswered', () => {
  it('🔴 says why it expired instead of guessing', async () => {
    approvals.mockResolvedValue([])
    render(<ApprovalDecision approvalId="spawn:fa8ed388" ended="the loop that started its run was stopped" />)
    expect(await screen.findByText('Expired: the loop that started its run was stopped, so it did not run.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Approve/ })).toBeNull()
  })

  it('still says nothing is waiting when the surface does not know why', async () => {
    approvals.mockResolvedValue([])
    render(<ApprovalDecision approvalId="spawn:fa8ed388" />)
    expect(await screen.findByText(/Nothing is waiting on this any more/)).toBeTruthy()
  })

  it('🔴 reads Expired in the Inbox, settled but not handled', () => {
    expect(statusMeta('expired').label).toBe('Expired')
    expect(statusMeta('expired').label).not.toBe(statusMeta('handled').label)
    expect(isSettled('expired')).toBe(true)
    expect(isOpenStatus('expired')).toBe(false)
  })
})
