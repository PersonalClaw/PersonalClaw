import { describe, expect, it, vi, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { InboxDetail } from './InboxDetail'
import { AttachmentCount } from './InboxAttachments'
import type { InboxItem } from '../../lib/api'

// ── A message's attachment is listed, and it downloads ──────────────────────────────────────────
//
// A mail with a PDF quote attached showed the PDF's bytes as the message and listed no attachment:
// the builder's "the revised quote is attached" had nothing attached to it anywhere in the Inbox.
// A row now carries the files its message came with, and the panel lists each one by its name,
// type and size, with Download for each one that was kept and, for one that was not, why.

const downloadFrom = vi.fn()

vi.mock('../../lib/download', () => ({ downloadFrom: (url: string) => downloadFrom(url) }))
vi.mock('../../lib/api', () => ({
  api: {
    updateInboxItem: () => Promise.resolve({}),
    favoriteInboxItem: () => Promise.resolve({}),
    draftInboxReply: () => Promise.resolve({}),
    restoreInboxItem: () => Promise.resolve({}),
    sendInboxReply: () => Promise.resolve({}),
    inboxAttachmentUrl: (id: string, aid: string) => `/api/inbox/${encodeURIComponent(id)}/attachments/${aid}`,
  },
  ApiError: class ApiError extends Error {},
}))
vi.mock('../../ui/InvestigateButton', () => ({ InvestigateButton: () => null }))
vi.mock('../../ui/FeedbackThumbs', () => ({ FeedbackThumbs: () => null }))

function mail(over: Partial<InboxItem> = {}): InboxItem {
  return {
    id: 'mail-inbox_0123456789abcdef_1790726807.0', channel: 'owner@example.com', channel_name: 'owner@example.com',
    message: 'Subject: Kitchen estimate\n\nThe revised quote is attached.',
    sender_id: 'builder@build.example.com', sender_name: 'Dana Builder',
    classification: 'needs_reply', confidence: 'high', status: 'seen', source: 'mail-inbox', can_reply: true,
    item_kind: 'email', created_at: 1790726807,
    attachments: [
      { id: '1', name: 'revised-quote.pdf', mimetype: 'application/pdf', size: 48_213, kept: true },
      { id: '2', name: 'site-video.mov', mimetype: 'video/quicktime', size: 41_000_000, kept: false,
        not_kept: 'it is larger than 25.0 MB, the most an attachment may be' },
    ],
    ...over,
  } as InboxItem
}

afterEach(() => { cleanup(); downloadFrom.mockReset() })

describe("a message's attachments", () => {
  it('🔑 each one is listed by its name, type and size, and a kept one downloads', () => {
    render(<InboxDetail item={mail()} onChanged={() => {}} navigate={() => {}} />)

    const list = screen.getByRole('list', { name: 'Attachments' })
    const [quote, video] = within(list).getAllByRole('listitem')
    expect(within(quote).getByText('revised-quote.pdf')).toBeInTheDocument()
    expect(within(quote).getByText(/application\/pdf · 47 KB/)).toBeInTheDocument()

    fireEvent.click(within(quote).getByRole('button', { name: 'Download revised-quote.pdf' }))
    expect(downloadFrom).toHaveBeenCalledWith('/api/inbox/mail-inbox_0123456789abcdef_1790726807.0/attachments/1')

    // One that was not kept says why, and offers nothing to download.
    expect(within(video).getByText(/Not kept: it is larger than 25.0 MB/)).toBeInTheDocument()
    expect(within(video).queryByRole('button')).toBeNull()
  })

  it('the message itself is only what the sender wrote', () => {
    render(<InboxDetail item={mail()} onChanged={() => {}} navigate={() => {}} />)
    expect(screen.getByText(/The revised quote is attached\./)).toBeInTheDocument()
  })

  it('a message that came with nothing lists nothing', () => {
    render(<InboxDetail item={mail({ attachments: [] })} onChanged={() => {}} navigate={() => {}} />)
    expect(screen.queryByRole('list', { name: 'Attachments' })).toBeNull()
    expect(screen.queryByText(/Attachments ·/)).toBeNull()
  })

  it('the row says how many files came with it, before it is opened', () => {
    const { container, rerender } = render(<AttachmentCount item={mail()} />)
    expect(screen.getByText(/2 attached/)).toBeInTheDocument()
    rerender(<AttachmentCount item={mail({ attachments: undefined })} />)
    expect(container).toBeEmptyDOMElement()
  })
})
