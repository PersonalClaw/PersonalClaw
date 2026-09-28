import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { InboxDetail } from './InboxDetail'
import type { InboxItem } from '../../lib/api'

// ── Someone new is answered from the Inbox, and only by you ─────────────────────────────────────
//
// A channel that sends as you (your own mailbox) used to answer any stranger with a pairing note,
// from your address, before you knew they had written. It sends them nothing now: their message
// waits here, as someone new, and you choose. Reply answers them as you when you press Send, Pair
// lets them talk to your agent there, and Ignore dismisses the row.

const pairInboxSender = vi.fn()
const updateInboxItem = vi.fn()
const sendInboxReply = vi.fn()

vi.mock('../../lib/api', () => ({
  api: {
    pairInboxSender: (...a: unknown[]) => pairInboxSender(...a),
    updateInboxItem: (...a: unknown[]) => updateInboxItem(...a),
    sendInboxReply: (...a: unknown[]) => sendInboxReply(...a),
    favoriteInboxItem: () => Promise.resolve({}),
    draftInboxReply: () => Promise.resolve({}),
    restoreInboxItem: () => Promise.resolve({}),
  },
  ApiError: class ApiError extends Error {},
}))
vi.mock('../../ui/InvestigateButton', () => ({ InvestigateButton: () => null }))
vi.mock('../../ui/FeedbackThumbs', () => ({ FeedbackThumbs: () => null }))

/** The row the door holds for a stranger's mail. */
function held(over: Partial<InboxItem> = {}): InboxItem {
  return {
    id: 'someone_new_0123456789abcdef_1790000000.0', channel: 'pat@example.org', channel_name: 'DM',
    message: 'Ladder\n\nCould I borrow your ladder on Saturday?', sender_id: 'pat@example.org',
    sender_name: 'Pat Example', classification: 'needs_reply', confidence: 'needs_review',
    status: 'pending', source: 'channel:email', can_reply: true, item_kind: 'message',
    refs: { someone_new: 'email', channel_name: 'Email' }, created_at: 1790000000,
    ...over,
  } as InboxItem
}

function OpenRow({ server }: { server: { row: InboxItem } }) {
  const [row, setRow] = useState(server.row)
  return <InboxDetail item={row} onChanged={() => setRow({ ...server.row })} navigate={() => {}} />
}

beforeEach(() => { pairInboxSender.mockReset(); updateInboxItem.mockReset(); sendInboxReply.mockReset() })
afterEach(() => cleanup())

describe('a message from someone new', () => {
  it('🔑 says nothing went to them and offers Reply, Pair and Ignore, not a second Dismiss', () => {
    render(<OpenRow server={{ row: held() }} />)

    expect(screen.getByText('Someone new')).toBeInTheDocument()
    expect(screen.getByText(/Pat Example wrote to you on Email, and nothing was sent to them\./)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Pair/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Ignore/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Send reply/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Dismiss/ })).toBeNull()
  })

  it('Pair lets them talk to your agent, and the row says so', async () => {
    const server = { row: held() }
    pairInboxSender.mockImplementation(async () => {
      server.row = { ...server.row, status: 'handled', refs: { ...server.row.refs, paired: true } }
      return { ok: true, paired: true }
    })
    render(<OpenRow server={server} />)

    fireEvent.click(screen.getByRole('button', { name: /Pair/ }))

    await waitFor(() => expect(pairInboxSender).toHaveBeenCalledWith(server.row.id))
    expect(await screen.findByText('Paired. Pat Example can talk to your agent on Email now.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Pair/ })).toBeNull()
  })

  it('Ignore dismisses the row and sends them nothing', async () => {
    updateInboxItem.mockResolvedValue({})
    render(<OpenRow server={{ row: held() }} />)

    fireEvent.click(screen.getByRole('button', { name: /Ignore/ }))

    await waitFor(() => expect(updateInboxItem).toHaveBeenCalledWith(held().id, { status: 'dismissed' }))
    expect(sendInboxReply).not.toHaveBeenCalled()
    expect(pairInboxSender).not.toHaveBeenCalled()
  })

  it('a pairing that fails says why and leaves the choices', async () => {
    pairInboxSender.mockRejectedValue(new Error('Only a message from someone new can pair its sender.'))
    render(<OpenRow server={{ row: held() }} />)

    fireEvent.click(screen.getByRole('button', { name: /Pair/ }))

    expect(await screen.findByText('Only a message from someone new can pair its sender.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Pair/ })).toBeInTheDocument()
  })

  it('an ordinary row keeps its Dismiss and shows no someone-new section', () => {
    render(<OpenRow server={{ row: held({ refs: {}, source: 'mail-inbox', id: 'mail-inbox_1_1790000000.0' }) }} />)

    expect(screen.queryByText('Someone new')).toBeNull()
    expect(screen.getByRole('button', { name: /Dismiss/ })).toBeInTheDocument()
  })
})
