import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ApiError, type InboxDrafting, type InboxItem } from '../../lib/api'

// ── A draft says what it stood on, and writes nothing it cannot stand on ─────────────────────
//
// Measured before this: Generate draft could read none of her notes and nothing said so. Asked for
// "my final abstract, 120 words max, based on my outline (Talks/exactly-once/outline-v2.md)", it
// wrote "I'll get you the final abstract by end of week", a promise she never made. And the
// button sat under the draft box and its Send controls, far from the field it drafts from.

const h = vi.hoisted(() => ({ draftInboxReply: vi.fn() }))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return { ...real, api: { ...real.api, draftInboxReply: h.draftInboxReply } }
})

let InboxDetail: typeof import('./InboxDetail')['InboxDetail']

const OUTLINE = 'Talks/exactly-once/outline-v2.md'
const SAID = `Thank them, confirm 12 Nov, and give my final abstract, 120 words max, based on my outline (${OUTLINE}).`

const accepted = (over: Partial<InboxItem> = {}): InboxItem => ({
  id: 'it-1', channel: 'mail', channel_name: 'Mail', source: 'mail', item_kind: 'email',
  sender_id: 'talks@events.example.org', sender_name: 'Talks team',
  message: 'Your talk is accepted. Please confirm the date and send your final abstract.',
  classification: 'needs_reply', confidence: 'high', status: 'pending', can_reply: true, draft: '',
  ...over,
} as InboxItem)

const drafting = (over: Partial<InboxDrafting> = {}): InboxDrafting => ({
  read: [], related: [], summary: '', word_limit: null, words: 0, question: '', skipped: false,
  answered_for_you: 0, unchecked: false, ...over,
})

const words = (n: number) => Array.from({ length: n }, () => 'word').join(' ')

beforeEach(async () => {
  h.draftInboxReply.mockReset()
  ;({ InboxDetail } = await import('./InboxDetail'))
})

afterEach(() => cleanup())

async function generateWith(said: string) {
  const user = userEvent.setup()
  await user.type(screen.getByRole('textbox', { name: 'What should the reply say?' }), said)
  await user.click(screen.getByRole('button', { name: /Generate draft|Regenerate/ }))
  await waitFor(() => expect(h.draftInboxReply).toHaveBeenCalled())
}

describe('the reply panel', () => {
  it('puts Generate draft directly under what the reply should say, before the draft and Send', () => {
    render(<InboxDetail item={accepted()} onChanged={() => {}} navigate={() => {}} />)
    const field = screen.getByRole('textbox', { name: 'What should the reply say?' })
    const button = screen.getByRole('button', { name: /Generate draft/ })
    const ask = screen.getByTestId('draft-ask')
    // One group holds the field and the button, and the button is the next thing after the field.
    expect(ask.contains(field) && ask.contains(button)).toBe(true)
    expect(ask.children).toHaveLength(2)
    expect(ask.children[0].contains(field)).toBe(true)
    expect(ask.children[1].contains(button)).toBe(true)
    // …and the draft box and Send come after it, in that order.
    const box = screen.getByRole('textbox', { name: 'Drafted reply' })
    const sendButton = screen.getByRole('button', { name: /Send reply/ })
    const follows = (a: Element, b: Element) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
    expect(follows(button, box)).toBe(true)
    expect(follows(box, sendButton)).toBe(true)
  })

  it('says which note the draft read, and counts it against her limit', async () => {
    const reply = `Thank you! 12 Nov is confirmed. ${words(100)}`
    h.draftInboxReply.mockResolvedValue({
      item: accepted({ draft: reply, context_summary: `Drafted from ${OUTLINE}, as you asked.` }),
      drafting: drafting({
        read: [{ name: OUTLINE, found: OUTLINE, where: 'library', truncated: false }],
        summary: `Drafted from ${OUTLINE}, as you asked.`, word_limit: 120, words: 106,
      }),
    })
    render(<InboxDetail item={accepted()} onChanged={() => {}} navigate={() => {}} />)
    await generateWith(SAID)

    expect(await screen.findByText(`Drafted from ${OUTLINE}, as you asked.`)).toBeTruthy()
    expect(screen.getByText('106 of 120 words')).toBeTruthy()
    expect((screen.getByRole('textbox', { name: 'Drafted reply' }) as HTMLTextAreaElement).value).toBe(reply)
  })

  it('says a draft is over her limit as she edits it', async () => {
    h.draftInboxReply.mockResolvedValue({
      item: accepted({ draft: words(118) }),
      drafting: drafting({ word_limit: 120, words: 118, summary: 'Drafted from the message alone: it read none of your notes.' }),
    })
    const user = userEvent.setup()
    render(<InboxDetail item={accepted()} onChanged={() => {}} navigate={() => {}} />)
    await generateWith('Keep it to 120 words max.')
    expect(await screen.findByText('118 of 120 words')).toBeTruthy()
    await user.type(screen.getByRole('textbox', { name: 'Drafted reply' }), ' one two three')
    expect(screen.getByText('121 words, over the 120 you asked for')).toBeTruthy()
  })

  it('says why nothing was written when a note she named could not be read', async () => {
    const sentence = `Couldn't read ${OUTLINE}: no note or file by that name is in your knowledge library or your workspace. No draft was written, so nothing was said or promised for you.`
    h.draftInboxReply.mockRejectedValue(new ApiError(sentence, 422, 'draft_source_unread'))
    render(<InboxDetail item={accepted({ draft: 'Her own words so far.' })} onChanged={() => {}} navigate={() => {}} />)
    await generateWith(SAID)

    const notice = await screen.findByText(sentence)
    const box = screen.getByRole('textbox', { name: 'Drafted reply' }) as HTMLTextAreaElement
    expect(box.value).toBe('Her own words so far.')
    // Said where she pressed the button: under it, and above the draft it did not write.
    const button = screen.getByRole('button', { name: /Regenerate/ })
    const follows = (a: Element, b: Element) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
    expect(follows(button, notice)).toBe(true)
    expect(follows(notice, box)).toBe(true)
  })

  it('shows the question it asks her instead of a draft, and writes nothing', async () => {
    h.draftInboxReply.mockResolvedValue({
      item: accepted({ draft: '' }),
      drafting: drafting({ question: 'What should your final abstract say?' }),
    })
    render(<InboxDetail item={accepted()} onChanged={() => {}} navigate={() => {}} />)
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /Generate draft/ }))

    expect(await screen.findByText(/It needs your word before it drafts: What should your final abstract say\?/)).toBeTruthy()
    expect((screen.getByRole('textbox', { name: 'Drafted reply' }) as HTMLTextAreaElement).value).toBe('')
  })
})
