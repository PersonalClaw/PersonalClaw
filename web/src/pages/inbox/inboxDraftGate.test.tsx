/**
 * #621 (frontend half) — the reply composer only renders where a reply can go.
 *
 * `can_reply` gated only the Send button, so Generate draft ran the model and
 * badged the row on items whose Send is permanently disabled. The composer is
 * now gated whole (the file's own "hidden rather than shown inert" doctrine);
 * a legacy draft saved before the gate still displays, with why it cannot send.
 */
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { InboxDetail } from './InboxDetail'
import type { InboxItem } from '../../lib/api'

function item(over: Partial<InboxItem> = {}): InboxItem {
  return {
    id: 'it-1', source: 'digest', item_kind: 'message', sender_id: 's', sender_name: 'Sender',
    subject: 'Subj', body: 'Body', ts: 1785890207, classification: 'fyi', confidence: 'high',
    status: 'pending', can_reply: false, draft: '',
    ...over,
  } as InboxItem
}

const mount = (it: InboxItem) =>
  render(<InboxDetail item={it} onChanged={() => {}} navigate={() => {}} />)

afterEach(() => cleanup())

describe('inbox draft composer is gated by can_reply (#621)', () => {
  it('a read-only item with no draft offers no composer at all', () => {
    mount(item())
    expect(screen.queryByText(/Generate draft/)).toBeNull()
    expect(screen.queryByText('Drafted reply')).toBeNull()
    expect(screen.queryByRole('textbox', { name: 'Drafted reply' })).toBeNull()
  })

  it('a legacy draft on a read-only item displays, says why it cannot send, and offers no regenerate', () => {
    mount(item({ draft: 'A saved reply that can never leave' }))
    expect(screen.getByText('A saved reply that can never leave')).toBeTruthy()
    expect(screen.getByText(/can.t be sent/i)).toBeTruthy()
    expect(screen.queryByText(/Regenerate|Generate draft/)).toBeNull()
    expect(screen.queryByText(/Send reply/)).toBeNull()
  })

  it('a replyable item keeps the full composer', () => {
    mount(item({ can_reply: true }))
    expect(screen.getByText(/Generate draft/)).toBeTruthy()
    expect(screen.getByText(/Send reply/)).toBeTruthy()
    expect(screen.getByRole('textbox', { name: 'Drafted reply' })).toBeTruthy()
  })
})
