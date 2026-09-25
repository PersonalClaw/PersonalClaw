import { describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import type { InboxItem, InboxStatus } from '../../lib/api'

// ── An approval answered anywhere leaves an OPEN Inbox at once ────────────────────────────────────
//
// A pending approval's Inbox row is raised and closed with the approval itself, by the approval
// registry — and that path broadcasts the approval frames (`approval`, `approval_resolved`), not an
// `inbox_*` one. This page listened only to `inbox_*`, so a user looking at the Inbox while the
// approval was answered in its chat (or on the phone) kept seeing a row asking for a decision that
// had already been made.

const h = vi.hoisted(() => ({
  deliver: null as null | ((m: { type: string; data: Record<string, unknown> }) => void),
  answered: false,
  reads: 0,
}))

const ROW = {
  id: 'inb-appr', status: 'pending', item_kind: 'agent_request',
  channel: 'system', channel_name: 'system', thread_ts: null,
  message: 'Approval needed: bash\n\nresearcher in a chat is waiting for your decision on bash.',
  sender_id: 'system', sender_name: 'system', classification: 'needs_reply', confidence: 'high',
  draft: '', created_at: 1000, source: 'system', can_reply: false,
  refs: { approval: 'chat-a:1', session: 'chat-a' },
} as unknown as InboxItem

const status = (open: number): InboxStatus => ({
  enabled: true, open_count: open, total_count: 1, health: { running: true }, sources: [], watched_channels: [],
})

vi.mock('../../lib/api', () => ({
  api: {
    // The store after the answer: the row is HANDLED, so the open list no longer carries it.
    inbox: () => { h.reads += 1; return Promise.resolve(h.answered ? [{ ...ROW, status: 'handled' }] : [ROW]) },
    inboxStatus: () => Promise.resolve(status(h.answered ? 0 : 1)),
    markInboxSeen: () => Promise.resolve({ ok: true, seen: 0 }),
    openInboxItem: () => Promise.resolve({ ok: true }),
  },
}))
vi.mock('../../lib/useChatSocket', () => ({
  useChatSocket: (cb: (m: { type: string; data: Record<string, unknown> }) => void) => { h.deliver = cb },
}))
vi.mock('./InboxDetail', () => ({ InboxDetail: () => null }))
vi.mock('./InboxSettingsPanel', () => ({ InboxSettingsPanel: () => null }))
vi.mock('./ProposalsLens', () => ({ ProposalsLens: () => null }))
vi.mock('./TriageDigestCard', () => ({ TriageDigestCard: () => null }))

describe('the Inbox follows the approval registry', () => {
  it('drops the row of an approval answered elsewhere, without a reload', async () => {
    const { InboxPage } = await import('./InboxPage')
    render(<InboxPage query={{}} setQuery={() => {}} navigate={() => {}} />)
    await waitFor(() => expect(screen.getByText(/Approval needed: bash/)).toBeTruthy())
    const before = h.reads

    // The approval is answered in its chat: the registry closes the row, then says so.
    h.answered = true
    act(() => {
      h.deliver?.({
        type: 'approval_resolved',
        data: { id: 'chat-a:1', request_id: '1', session: 'chat-a', approved: false },
      })
    })

    await waitFor(() => expect(h.reads).toBeGreaterThan(before))
    await waitFor(() => expect(screen.queryByText(/Approval needed: bash/)).toBeNull())
  })
})
