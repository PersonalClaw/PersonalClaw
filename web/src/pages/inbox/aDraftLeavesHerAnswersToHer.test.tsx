import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { InboxDrafting, InboxItem } from '../../lib/api'
import { openAnswers } from './DraftNotices'

// ── A draft leaves her answers to her, and says which ─────────────────────────────────────────
//
// Measured before this: asked for "my final abstract, 120 words max, from outline-v2.md", a draft
// gave the abstract and then answered the rest of the mail for her ("I'm in for the dinner — no
// dietary restrictions"), and the panel offered it for sending as it was. Now what no source answers
// is marked [your answer: …], the panel lists what is left for her as she edits, says when its check
// took out answers she never gave, and Send waits until every place is answered or taken out.

const h = vi.hoisted(() => ({ draftInboxReply: vi.fn(), sendInboxReply: vi.fn() }))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: { ...real.api, draftInboxReply: h.draftInboxReply, sendInboxReply: h.sendInboxReply },
  }
})

let InboxDetail: typeof import('./InboxDetail')['InboxDetail']

const PHOTO = 'speaker photo — when you’ll send it'
const DINNER = 'dinner — yes or no, dietary needs'
const LEFT = `Thank you! Here's my final abstract: what exactly-once really promises. [your answer: ${PHOTO}] [your answer: ${DINNER}]`

const accepted = (over: Partial<InboxItem> = {}): InboxItem => ({
  id: 'it-1', channel: 'mail', channel_name: 'Mail', source: 'mail', item_kind: 'email',
  sender_id: 'talks@events.example.org', sender_name: 'Talks team',
  message: 'Your talk is accepted. Send a final abstract and a photo, and tell us about dietary needs.',
  classification: 'needs_reply', confidence: 'high', status: 'pending', can_reply: true, draft: '',
  ...over,
} as InboxItem)

const drafting = (over: Partial<InboxDrafting> = {}): InboxDrafting => ({
  read: [], related: [], summary: 'Drafted from Talks/outline-v2.md, as you asked.', word_limit: 120,
  words: 20, question: '', skipped: false, answered_for_you: 0, unchecked: false, ...over,
})

beforeEach(async () => {
  h.draftInboxReply.mockReset()
  h.sendInboxReply.mockReset().mockResolvedValue({ ok: true })
  ;({ InboxDetail } = await import('./InboxDetail'))
})

afterEach(() => cleanup())

const sendButton = () => screen.getByRole('button', { name: /Send reply/ })
const box = () => screen.getByRole('textbox', { name: 'Drafted reply' }) as HTMLTextAreaElement

async function generate() {
  const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: /Generate draft|Regenerate/ }))
  await waitFor(() => expect(h.draftInboxReply).toHaveBeenCalled())
  return user
}

describe('a drafted reply', () => {
  it('lists what it leaves for her, and Send waits until each is answered or taken out', async () => {
    h.draftInboxReply.mockResolvedValue({ item: accepted({ draft: LEFT }), drafting: drafting() })
    render(<InboxDetail item={accepted()} onChanged={() => {}} navigate={() => {}} />)
    const user = await generate()

    expect(await screen.findByText(`Left for you to answer, where it says [your answer: …]: ${PHOTO}; ${DINNER}.`)).toBeTruthy()
    expect(sendButton().getAttribute('aria-disabled')).toBe('true')
    expect(sendButton().getAttribute('title')).toContain('Write your answers in the 2 places that say [your answer: …]')
    await user.click(sendButton())
    expect(h.sendInboxReply).not.toHaveBeenCalled()

    // She answers one: the list says what is still left, and Send still waits. (Pasted: `type`
    // reads a bracket as a key name.)
    await user.clear(box())
    await user.paste(`Thank you! Photo to follow. [your answer: ${DINNER}]`)
    expect(screen.getByText(`Left for you to answer, where it says [your answer: …]: ${DINNER}.`)).toBeTruthy()
    expect(sendButton().getAttribute('title')).toContain('Write your answer where it says [your answer: …], or take that out')

    // She answers the other: nothing is left, and the reply goes.
    await user.clear(box())
    await user.type(box(), 'Thank you! Photo to follow, and I will confirm the dinner next week.')
    expect(screen.queryByText(/Left for you to answer/)).toBeNull()
    expect(sendButton().getAttribute('aria-disabled')).toBeNull()
    await user.click(sendButton())
    await waitFor(() => expect(h.sendInboxReply).toHaveBeenCalledWith('it-1', 'Thank you! Photo to follow, and I will confirm the dinner next week.'))
  })

  it('says when its check took out answers she never gave', async () => {
    h.draftInboxReply.mockResolvedValue({ item: accepted({ draft: LEFT }), drafting: drafting({ answered_for_you: 2 }) })
    render(<InboxDetail item={accepted()} onChanged={() => {}} navigate={() => {}} />)
    await generate()

    expect(await screen.findByText(/It had answered 2 of the message’s questions for you with things you didn’t say and your notes don’t say\. Those are left for you to answer instead\./)).toBeTruthy()
    // What the draft stood on is still said, first.
    expect(screen.getByText('Drafted from Talks/outline-v2.md, as you asked.')).toBeTruthy()
  })

  it('says when it could not be checked, so she reads each answer herself', async () => {
    const draft = 'Thank you! I’m in for the dinner.'
    h.draftInboxReply.mockResolvedValue({ item: accepted({ draft }), drafting: drafting({ unchecked: true }) })
    render(<InboxDetail item={accepted()} onChanged={() => {}} navigate={() => {}} />)
    await generate()

    expect(await screen.findByText(/It couldn’t check this draft against what you said and your notes, so read each answer in it before you send\./)).toBeTruthy()
    expect(box().value).toBe(draft)
  })

  it('still lists what is left for her after a reload, from the kept draft', () => {
    render(<InboxDetail item={accepted({ draft: LEFT })} onChanged={() => {}} navigate={() => {}} />)
    expect(screen.getByText(`Left for you to answer, where it says [your answer: …]: ${PHOTO}; ${DINNER}.`)).toBeTruthy()
    expect(sendButton().getAttribute('aria-disabled')).toBe('true')
  })
})

describe('openAnswers reads a reply as the server does', () => {
  it.each([
    ['Thanks! [your answer: dinner — yes or no]', ['dinner — yes or no']],
    ['[YOUR ANSWER:slides]\n[ your answer :  photo ]', ['slides', 'photo']],
    ['See [the agenda](https://example.org/agenda) [1].', []],
    ["I'll be there [your answer: arrival time", ['arrival time']],
    ['No placeholders at all.', []],
  ])('%s', (text, labels) => {
    expect(openAnswers(text)).toEqual(labels)
  })
})
