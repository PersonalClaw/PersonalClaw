// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import type { ApprovalRuleRow } from '../../lib/api'
import { invalidateKeys } from '../../lib/data'

// ── PA-5 §5.2: the triage rules manager ─────────────────────────────────────────────────────
//
// The digest teaches rules by answering "Always"/"Never"; this card is the half that lets the
// user SEE and REVOKE what they taught, and mark an approve-rule as one a send-capable provider
// may honour. The behaviour (not just that the page renders the card) is what the done_when names
// — shows/revokes rules with the send-capable graduation toggle — so each is driven here:
//   • revoke routes through DELETE /api/memory/approval-rules after a confirm;
//   • graduation upserts the SAME rule key with send_capable, never mints a second row;
//   • the toggle is APPROVE-ONLY (a deny rule cannot "send"), and the graduated badge tracks state;
//   • a failed read is surfaced as an error, never as the empty "no rules yet" — opposite claims
//     about what the machine will do on its own.

const approvalRules = vi.fn()
const revokeApprovalRule = vi.fn((_key: string) => Promise.resolve({ ok: true }))
const saveApprovalRule = vi.fn((_r: unknown) => Promise.resolve({ ok: true }))
vi.mock('../../lib/api', () => ({
  api: {
    approvalRules: () => approvalRules(),
    revokeApprovalRule: (k: string) => revokeApprovalRule(k),
    saveApprovalRule: (r: unknown) => saveApprovalRule(r),
  },
}))
const notify = vi.fn((_m: string, _t?: string) => undefined)
vi.mock('../../app/appSdk', () => ({ notify: (m: string, t?: string) => notify(m, t) }))
// Revoke is gated on a confirm dialog; auto-confirm so the endpoint call is observable.
vi.mock('../../ui/dialog', () => ({ confirm: () => Promise.resolve(true) }))

import { TriageRulesCard } from './TriageRulesCard'

const rule = (over: Partial<ApprovalRuleRow> = {}): ApprovalRuleRow => ({
  key: 'k1', pattern: 'reply_draft:inbox', verdict: 'approve', scope: 'global',
  expires_at: null, send_capable: false, hit_count: 3, created_from_digest: '',
  ...over,
} as ApprovalRuleRow)

beforeEach(() => {
  approvalRules.mockReset()
  revokeApprovalRule.mockClear()
  saveApprovalRule.mockClear()
  notify.mockClear()
  invalidateKeys('settings:approval-rules') // cold cache so useQuery re-reads this test's rows
})

describe('the triage rules manager (PA-5 §5.2)', () => {
  it('shows a taught rule with its verdict and pattern', async () => {
    approvalRules.mockResolvedValue({ rules: [rule()], unreadable: [] })
    render(<TriageRulesCard />)
    expect(await screen.findByText('reply_draft:inbox')).toBeInTheDocument()
    expect(screen.getByText('always')).toBeInTheDocument() // approve → "always"
  })

  it('revokes a rule through the endpoint after the confirm', async () => {
    approvalRules.mockResolvedValue({ rules: [rule()], unreadable: [] })
    render(<TriageRulesCard />)
    fireEvent.click(await screen.findByRole('button', { name: /Revoke/i }))
    await waitFor(() => expect(revokeApprovalRule).toHaveBeenCalledWith('k1'))
    expect(notify).toHaveBeenCalledWith('Rule revoked.', 'success')
  })

  it('graduates an approve rule to send-capable via an upsert on its own key', async () => {
    approvalRules.mockResolvedValue({ rules: [rule({ send_capable: false })], unreadable: [] })
    render(<TriageRulesCard />)
    fireEvent.click(await screen.findByRole('switch', { name: /Send capability for reply_draft:inbox/i }))
    await waitFor(() => expect(saveApprovalRule).toHaveBeenCalledWith(
      expect.objectContaining({ pattern: 'reply_draft:inbox', verdict: 'approve', send_capable: true }),
    ))
  })

  it('shows the graduated badge only when the rule is already send-capable', async () => {
    approvalRules.mockResolvedValue({ rules: [rule({ send_capable: true })], unreadable: [] })
    render(<TriageRulesCard />)
    expect(await screen.findByText('graduated')).toBeInTheDocument()
  })

  it('offers no send-capable toggle for a deny rule — graduation is approve-only', async () => {
    approvalRules.mockResolvedValue({ rules: [rule({ key: 'k2', pattern: 'spam:*', verdict: 'deny' })], unreadable: [] })
    render(<TriageRulesCard />)
    expect(await screen.findByText('spam:*')).toBeInTheDocument()
    expect(screen.queryByRole('switch', { name: /Send capability/i })).toBeNull()
  })

  it('surfaces a failed read as an error, never as "no rules yet"', async () => {
    approvalRules.mockRejectedValue(new Error('rules unreadable'))
    render(<TriageRulesCard />)
    expect(await screen.findByText(/rules unreadable/)).toBeInTheDocument()
    expect(screen.queryByText(/haven't taught the digest any rules/)).toBeNull()
  })
})
