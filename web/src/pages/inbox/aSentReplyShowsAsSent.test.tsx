import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { InboxDetail } from './InboxDetail'
import type { InboxItem } from '../../lib/api'

// ── After Send, the detail panel shows the reply as sent ────────────────────────────────────────
//
// A sent reply moves its row to Handled, and the Inbox re-reads the row into the open panel. The
// panel still offered Send reply (a second send of the same text) and Mark handled (on a handled
// row), because it rendered its controls whatever the row's status. A settled row now shows what
// became of it: the reply it sent, and when, or the draft it was closed with; and Reopen instead.

const sendInboxReply = vi.fn()
const updateInboxItem = vi.fn()

vi.mock('../../lib/api', () => ({
  api: {
    sendInboxReply: (...a: unknown[]) => sendInboxReply(...a),
    updateInboxItem: (...a: unknown[]) => updateInboxItem(...a),
    favoriteInboxItem: () => Promise.resolve({}),
    draftInboxReply: () => Promise.resolve({}),
    restoreInboxItem: () => Promise.resolve({}),
  },
  ApiError: class ApiError extends Error {},
}))
vi.mock('../../ui/InvestigateButton', () => ({ InvestigateButton: () => null }))
vi.mock('../../ui/FeedbackThumbs', () => ({ FeedbackThumbs: () => null }))

const REPLY = 'Thanks! We accept, see you on the 14th.'

function mail(over: Partial<InboxItem> = {}): InboxItem {
  return {
    id: 'mail-inbox_0123456789abcdef_1790000000.0', channel: 'me@example.test', channel_name: 'me@example.test',
    message: 'Subject: Your talk\n\nWe would love to have you.', sender_id: 'talks@example.test', sender_name: 'PyTO',
    classification: 'needs_reply', confidence: 'high', status: 'seen', source: 'mail-inbox', can_reply: true,
    item_kind: 'email', draft: REPLY, created_at: 1790000000,
    ...over,
  } as InboxItem
}

/** The Inbox, reduced to what matters here: the open panel is fed the row as the server holds it. */
function OpenRow({ server }: { server: { row: InboxItem } }) {
  const [row, setRow] = useState(server.row)
  return <InboxDetail item={row} onChanged={() => setRow({ ...server.row })} navigate={() => {}} />
}

beforeEach(() => { sendInboxReply.mockReset(); updateInboxItem.mockReset() })
afterEach(() => cleanup())

describe('a settled row shows what became of it', () => {
  it('🔑 after Send, the panel shows the reply as sent and offers neither Send nor Mark handled', async () => {
    const server = { row: mail() }
    sendInboxReply.mockImplementation(async (_id: string, text: string) => {
      server.row = { ...server.row, status: 'handled', draft: text, replied_at: Date.now() / 1000 }
      return { ok: true, sent: true }
    })
    render(<OpenRow server={server} />)

    fireEvent.click(screen.getByRole('button', { name: /Send reply/ }))
    await waitFor(() => expect(sendInboxReply).toHaveBeenCalledWith(server.row.id, REPLY))

    expect(await screen.findByText('Your reply')).toBeInTheDocument()
    expect(screen.getByText(REPLY)).toBeInTheDocument()
    expect(screen.getByText('Sent just now.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Send reply/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Mark handled/ })).toBeNull()
    expect(screen.queryByRole('textbox', { name: 'Drafted reply' })).toBeNull()
    expect(screen.getByRole('button', { name: /Reopen/ })).toBeInTheDocument()
  })

  it('Reopen moves the row back to open', async () => {
    updateInboxItem.mockResolvedValue({})
    render(<InboxDetail item={mail({ status: 'handled', replied_at: 1790000100 })} onChanged={() => {}} navigate={() => {}} />)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Reopen/ })) })
    expect(updateInboxItem).toHaveBeenCalledWith(mail().id, { status: 'seen' })
  })

  it('a row handled without a reply shows its draft and never says it was sent', () => {
    render(<InboxDetail item={mail({ status: 'handled' })} onChanged={() => {}} navigate={() => {}} />)
    expect(screen.getByText('Drafted reply')).toBeInTheDocument()
    expect(screen.getByText(REPLY)).toBeInTheDocument()
    expect(screen.queryByText(/^Sent /)).toBeNull()
    expect(screen.queryByRole('button', { name: /Send reply/ })).toBeNull()
  })

  it('an open row keeps the composer and Mark handled', () => {
    render(<InboxDetail item={mail()} onChanged={() => {}} navigate={() => {}} />)
    expect(screen.getByRole('button', { name: /Send reply/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Mark handled/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Reopen/ })).toBeNull()
  })
})
