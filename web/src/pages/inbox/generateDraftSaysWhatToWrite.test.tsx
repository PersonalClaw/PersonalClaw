import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { InboxItem } from '../../lib/api'

// ── Generate draft takes what the reply should say ──────────────────────────────────────────
//
// Measured before this: the reply panel offered Generate draft and Regenerate and nothing else,
// so "accept, with my abstract, 120 words" had nowhere to go. The draft it made left out the
// abstract the message asked for and answered a question nobody asked.

const h = vi.hoisted(() => ({ draftInboxReply: vi.fn() }))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return { ...real, api: { ...real.api, draftInboxReply: h.draftInboxReply } }
})

let InboxDetail: typeof import('./InboxDetail')['InboxDetail']

const accepted = (over: Partial<InboxItem> = {}): InboxItem => ({
  id: 'it-1', source: 'mail', item_kind: 'message', sender_id: 'talks@events.example.org',
  sender_name: 'Talks team', subject: 'Your talk is accepted', body: 'Please send the abstract.',
  ts: 1785890207, classification: 'needs_reply', confidence: 'high', status: 'pending',
  can_reply: true, draft: '', ...over,
} as InboxItem)

const drafted = (draft: string) => ({
  item: accepted({ draft }),
  drafting: { read: [], related: [], summary: '', word_limit: null, words: 0, question: '', skipped: false },
})

beforeEach(async () => {
  h.draftInboxReply.mockReset().mockResolvedValue(drafted('Thank you! Here is my abstract.'))
  ;({ InboxDetail } = await import('./InboxDetail'))
})

afterEach(() => cleanup())

describe('the reply panel', () => {
  it('sends what she says the reply should contain with Generate draft', async () => {
    const user = userEvent.setup()
    render(<InboxDetail item={accepted()} onChanged={() => {}} navigate={() => {}} />)
    await user.type(
      screen.getByRole('textbox', { name: 'What should the reply say?' }),
      'Accept, and include my abstract, 120 words at most.',
    )
    await user.click(screen.getByRole('button', { name: /Generate draft/ }))

    await waitFor(() => expect(h.draftInboxReply).toHaveBeenCalledTimes(1))
    expect(h.draftInboxReply).toHaveBeenCalledWith('it-1', 'Accept, and include my abstract, 120 words at most.')
    expect(await screen.findByDisplayValue('Thank you! Here is my abstract.')).toBeTruthy()
  })

  it('drafts with nothing said, as before, when the field is left empty', async () => {
    const user = userEvent.setup()
    render(<InboxDetail item={accepted()} onChanged={() => {}} navigate={() => {}} />)
    await user.click(screen.getByRole('button', { name: /Generate draft/ }))
    await waitFor(() => expect(h.draftInboxReply).toHaveBeenCalledWith('it-1', ''))
  })

  it('keeps what she said for Regenerate', async () => {
    const user = userEvent.setup()
    const { rerender } = render(<InboxDetail item={accepted()} onChanged={() => {}} navigate={() => {}} />)
    const said = screen.getByRole('textbox', { name: 'What should the reply say?' })
    await user.type(said, 'Decline politely.')
    await user.click(screen.getByRole('button', { name: /Generate draft/ }))
    await waitFor(() => expect(h.draftInboxReply).toHaveBeenCalledTimes(1))
    rerender(<InboxDetail item={accepted({ draft: 'Thank you! Here is my abstract.' })} onChanged={() => {}} navigate={() => {}} />)

    await user.click(screen.getByRole('button', { name: /Regenerate/ }))
    await waitFor(() => expect(h.draftInboxReply).toHaveBeenCalledTimes(2))
    expect(h.draftInboxReply).toHaveBeenLastCalledWith('it-1', 'Decline politely.')
  })
})
