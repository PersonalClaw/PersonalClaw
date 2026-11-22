import { describe, expect, it } from 'vitest'
import { sessionTitle } from './sessionTitle'

// ── An untitled chat in a list is named by its first message ─────────────────────────────────────
//
// Seen once: an untitled chat in the live list read its first answer's raw Markdown, because the
// list named it by its latest line. The chat summary now carries its first message plain
// (`first_message`), which names it before the latest line does; a title it has comes first.

const KEY = 'chat-1-1791067183'

describe('an untitled chat', () => {
  it('🔴 is named by its first message, not its latest line', () => {
    expect(sessionTitle({ key: KEY, title: KEY, first_message: 'Plan three days in Lisbon', last_message: '**Days** | Date | Plan |' }))
      .toBe('Plan three days in Lisbon')
  })

  it('is named by its title once it has one', () => {
    expect(sessionTitle({ key: KEY, title: 'Lisbon in three days', first_message: 'Plan three days in Lisbon' })).toBe('Lisbon in three days')
  })

  it('is named by its latest line when the summary has no first message', () => {
    expect(sessionTitle({ key: KEY, title: KEY, last_message: 'Sounds good.' })).toBe('Sounds good.')
  })
})
