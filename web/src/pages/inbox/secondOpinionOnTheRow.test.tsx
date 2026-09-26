import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { InboxDetail } from './InboxDetail'
import { ProposalsLens } from './ProposalsLens'
import type { InboxItem } from '../../lib/api'

// ── INU-6: the second opinion runs after a row is listed, so the row says what it is doing ────
//
// A proposal's row is published the moment it is raised and the check answers a moment later
// (`refs.verify` is `checking` until then), so a user can be looking at a row whose claim is
// still being checked. And a REFUTED verdict that lands on a row they already opened or answered
// is written on the row instead of moving it. Neither is visible unless the row says so: the
// open row would read like any other, and the flagged one like one nobody questioned.

vi.mock('../../lib/api', () => ({
  api: {
    restoreInboxItem: () => Promise.resolve({}),
    updateInboxItem: () => Promise.resolve({}),
    favoriteInboxItem: () => Promise.resolve({}),
    draftInboxReply: () => Promise.resolve({}),
    sendInboxReply: () => Promise.resolve({}),
    applyInboxProposal: () => Promise.resolve({ ok: true }),
  },
}))
vi.mock('../../ui/InvestigateButton', () => ({ InvestigateButton: () => null }))
vi.mock('../../ui/FeedbackThumbs', () => ({ FeedbackThumbs: () => null }))
vi.mock('./WorkflowGateActions', () => ({ WorkflowGateActions: () => null }))
vi.mock('../../ui/Markdown', () => ({ Markdown: ({ children }: { children?: unknown }) => (children ?? null) }))

const CHECKING = /A second opinion is checking this claim/
const FLAGGED = /A second-opinion check flagged this claim\.$/

function proposal(over: Partial<InboxItem> = {}): InboxItem {
  return {
    id: 'proposal_x_100.0',
    channel: 'skills',
    channel_name: 'skills',
    message: 'Add a skill for weekly reports',
    sender_id: 'skills',
    sender_name: 'skills',
    item_kind: 'proposal',
    status: 'pending',
    refs: {
      verify: 'checking',
      proposal: {
        title: 'Add a skill for weekly reports', preview: '', preview_kind: 'text',
        provenance: 'learning', editable: false, apply: { workflow: { ref: 'weekly' } },
      },
    },
    ...over,
  } as InboxItem
}

describe('the second opinion on an Inbox row', () => {
  it('says the claim is being checked while it is', () => {
    render(<InboxDetail item={proposal()} onChanged={() => {}} navigate={() => {}} />)
    expect(screen.getByText(CHECKING)).toBeTruthy()
  })

  it('says a row you had opened was flagged, and offers no Restore for a row that was never filtered', () => {
    const seen = proposal({ status: 'seen', refs: { ...proposal().refs, verify: 'refuted' } })
    render(<InboxDetail item={seen} onChanged={() => {}} navigate={() => {}} />)
    expect(screen.getByText(FLAGGED)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /restore/i })).toBeNull()
  })

  it('leaves a filtered row to its Restore banner', () => {
    const filtered = proposal({ status: 'filtered', refs: { ...proposal().refs, verify: 'refuted' } })
    render(<InboxDetail item={filtered} onChanged={() => {}} navigate={() => {}} />)
    expect(screen.getByRole('button', { name: /restore/i })).toBeTruthy()
    expect(screen.queryByText(FLAGGED)).toBeNull()
  })

  it('says it on the proposals lens too', () => {
    render(<ProposalsLens items={[proposal()]} onChanged={() => {}} />)
    expect(screen.getByText(CHECKING)).toBeTruthy()
  })

  it('says nothing once the check confirmed the claim', () => {
    const confirmed = proposal({ refs: { ...proposal().refs, verify: 'confirmed' } })
    render(<InboxDetail item={confirmed} onChanged={() => {}} navigate={() => {}} />)
    expect(screen.queryByText(CHECKING)).toBeNull()
    expect(screen.queryByText(FLAGGED)).toBeNull()
  })
})
