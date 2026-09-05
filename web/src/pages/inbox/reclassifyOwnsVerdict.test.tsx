import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { InboxDetail } from './InboxDetail'
import { confMeta } from './inboxMeta'
import type { InboxItem } from '../../lib/api'

// ── Issue 623: a manual reclassification must not keep the AI's confidence ─────────
//
// Reclassifying an item is a HUMAN verdict. Leaving the model's `confidence` attached
// renders "Noise · High confidence" — the user's choice wearing the machine's certainty
// in the verdict it just overrode — and the classification thumbs then snapshot that
// synthetic pair as if the model produced it. These rails pin the three seams:
//
//  1. the Reclassify control patches classification AND confidence:'user' as ONE call;
//  2. with confidence:'user' the verdict chip reads "Set by you" and the classification
//     thumbs are GONE (no machine judgment on display means nothing to rate);
//  3. with a machine confidence the thumbs render as before (the gate must not
//     over-hide — rating real AI verdicts is the whole point of the control).

const updateInboxItem = vi.fn((_id: string, _body: Record<string, unknown>) => Promise.resolve({}))

vi.mock('../../lib/api', () => ({
  api: {
    updateInboxItem: (id: string, body: Record<string, unknown>) => updateInboxItem(id, body),
    restoreInboxItem: () => Promise.resolve({}),
    favoriteInboxItem: () => Promise.resolve({}),
    draftInboxReply: () => Promise.resolve({}),
    sendInboxReply: () => Promise.resolve({}),
  },
}))
vi.mock('../../ui/InvestigateButton', () => ({ InvestigateButton: () => null }))
vi.mock('../../ui/FeedbackThumbs', () => ({
  FeedbackThumbs: ({ targetKind }: { targetKind: string }) => <span data-testid={`thumbs-${targetKind}`} />,
}))
vi.mock('./WorkflowGateActions', () => ({ WorkflowGateActions: () => null }))
vi.mock('../../ui/Markdown', () => ({ Markdown: ({ children }: { children?: unknown }) => (children ?? null) }))

function makeItem(over: Partial<InboxItem> = {}): InboxItem {
  return {
    id: 'chan_1.000',
    channel: 'C1',
    channel_name: 'general',
    message: 'hello',
    sender_id: 'U1',
    sender_name: 'Ada',
    item_kind: 'message',
    classification: 'fyi',
    confidence: 'high',
    status: 'seen',
    refs: {},
    ...over,
  } as InboxItem
}

describe('reclassify owns the verdict (issue 623)', () => {
  beforeEach(() => updateInboxItem.mockClear())

  it('reclassifying patches the new classification WITH confidence:user in one call', async () => {
    render(<InboxDetail item={makeItem()} onChanged={() => {}} navigate={() => {}} />)
    fireEvent.click(screen.getByRole('tab', { name: 'Noise' }))
    await waitFor(() => expect(updateInboxItem).toHaveBeenCalledWith('chan_1.000', {
      classification: 'noise',
      confidence: 'user',
    }))
  })

  it("a user-owned verdict reads 'Set by you' and offers no classification thumbs", () => {
    render(<InboxDetail item={makeItem({ classification: 'noise', confidence: 'user' })} onChanged={() => {}} navigate={() => {}} />)
    expect(screen.getByText('Set by you')).toBeInTheDocument()
    expect(screen.queryByText('High confidence')).not.toBeInTheDocument()
    expect(screen.queryByTestId('thumbs-inbox_classification')).not.toBeInTheDocument()
    expect(screen.queryByTestId('thumbs-inbox_digest')).not.toBeInTheDocument()
  })

  it('a machine verdict still renders its thumbs (the gate must not over-hide)', () => {
    render(<InboxDetail item={makeItem()} onChanged={() => {}} navigate={() => {}} />)
    expect(screen.getByTestId('thumbs-inbox_classification')).toBeInTheDocument()
    expect(screen.getByText('High confidence')).toBeInTheDocument()
  })

  it("a digest item's thumbs are gated by the same override", () => {
    render(<InboxDetail item={makeItem({ source: 'digest', confidence: 'user' })} onChanged={() => {}} navigate={() => {}} />)
    expect(screen.queryByTestId('thumbs-inbox_digest')).not.toBeInTheDocument()
  })

  it("confMeta resolves 'user' to its own entry, not the needs_review fallback", () => {
    expect(confMeta('user').label).toBe('Set by you')
  })
})
