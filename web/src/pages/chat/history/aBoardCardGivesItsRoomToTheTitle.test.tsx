import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { useState } from 'react'
import type { ChatSessionSummary, ChatTag } from '../../../lib/api'
import { resetDataStore } from '../../../lib/data/store'
import { runState } from '../../../test/runState'

// ── A Chat history board card gives its room to the chat's title (#5879) ──────────────────────
//
// The board (`?view=board`, one column per tag) drew each chat as the list's row squeezed into a
// 260 px column: its padding, a 36 px icon tile and a checkbox with a column of its own took most
// of the width, the title was cut to one short line, and the tags crowded in beside an "N messages"
// count that says nothing about which chat it is. A board card now gives its room to the title,
// which wraps to three lines, with the tags on their own line under it and no count. A loop's, a
// code project's or a channel's chat keeps a small kind mark before its title. The selection tick
// hangs on the card's top-right corner, outside its content, and shows on hover, on focus and while
// selecting; a tick never opens the peek, and a click on the card does. The column of chats with no
// tag is "No tag", as the sidebar says. The list keeps its row.
//
// jsdom lays nothing out, so this reads the card's structure and the classes that decide its
// layout; `web/e2e/chatHistoryBoardCard.spec.ts` measures it in a browser.

const TAGS: ChatTag[] = [
  { id: 't-work', name: 'Work', color: '#3f8f6b' } as ChatTag,
  { id: 't-home', name: 'Home', color: '' } as ChatTag,
]
const LONG = 'Plan the allotment beds for spring, with the seed order and the watering rota for the summer'

const chat = (key: string, title: string, over: Partial<ChatSessionSummary> = {}) => ({
  key, title, origin: 'manual', messages: 12, tags: [], run_state: runState({ session: key }),
  last_activity_ts: '2026-10-01T09:00:00Z', ...over,
}) as ChatSessionSummary

const SESSIONS: ChatSessionSummary[] = [
  chat('chat-plan', LONG, { tags: ['t-work'] }),
  chat('chat-loop', 'Nightly digest of the reading list', { origin: 'loop', source_id: 'loop-1', source_label: 'Reading digest', tags: ['t-work'] }),
  chat('chat-code', 'Tidy the notes app build', { origin: 'code', source_id: 'proj-1', source_label: 'Notes app', tags: ['t-home'] }),
  chat('chat-channel', 'What is on the calendar this week', { origin: 'channel', source_label: 'Telegram' }),
]

vi.mock('../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/api')>()
  const api = new Proxy({
    chatSessions: () => Promise.resolve(SESSIONS),
    chatFolders: () => Promise.resolve([]),
    chatTags: () => Promise.resolve(TAGS),
    rooms: () => Promise.resolve({ enabled: false }),
  } as Record<string, unknown>, {
    get(t, p: string) { return p in t ? t[p] : (t[p] = () => Promise.resolve({})) },
  })
  return { ...real, api }
})

/** The page with its query held as the app holds it, so a peek opens and a view switches. */
const host = { query: {} as Record<string, string> }
async function paint(view: 'board' | 'list' = 'board') {
  const { ChatHistoryPage } = await import('./ChatHistoryPage')
  function Host() {
    // Every origin, so the loop's, the code project's and the channel's chats are listed too.
    const [query, setQuery] = useState<Record<string, string>>({ view, origin: 'all' })
    host.query = query
    return (
      <ChatHistoryPage navigate={() => {}} query={query}
        setQuery={(patch) => setQuery((q) => {
          const next = { ...q }
          for (const [k, v] of Object.entries(patch)) { if (v == null || v === '') delete next[k]; else next[k] = v }
          return next
        })} />
    )
  }
  render(<Host />)
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
}

const cardOf = (key: string) => {
  const el = document.querySelector<HTMLElement>(`[data-board-card="${key}"]`)
  expect(el, `the board draws ${key} as a board card`).not.toBeNull()
  return el!
}
const bodyOf = (key: string) => cardOf(key).querySelector<HTMLElement>('[data-board-card-body]')!
const titleOf = (key: string) => cardOf(key).querySelector<HTMLElement>('[data-board-card-title]')!
const tickOf = (key: string) => cardOf(key).querySelector<HTMLElement>('[data-board-card-tick]')!
const classes = (el: Element) => el.className.split(/\s+/)
const after = (a: Element, b: Element) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0

beforeEach(() => {
  vi.useFakeTimers()
  resetDataStore()
  localStorage.clear()
  host.query = {}
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('a board card gives its room to the chat\'s title', () => {
  it('says no message count, where the list row still does', async () => {
    await paint('board')
    for (const s of SESSIONS) expect(cardOf(s.key).textContent).not.toMatch(/\d+ messages?/)
    cleanup()
    await paint('list')
    expect(screen.getAllByText(/^12 messages/).length, 'the list row keeps its count').toBe(SESSIONS.length)
  })

  it('wraps its title to three lines across the card, with no icon tile beside it', async () => {
    await paint('board')
    const title = titleOf('chat-plan')
    expect(title.textContent).toBe(LONG)
    expect(classes(title)).toContain('line-clamp-3')
    expect(classes(title)).not.toContain('truncate')
    expect(cardOf('chat-plan').querySelector('.size-9'), 'no 36 px icon tile').toBeNull()
  })

  it('puts the tags on their own line under the title', async () => {
    await paint('board')
    const tags = cardOf('chat-plan').querySelector<HTMLElement>('[data-board-card-tags]')
    expect(tags, 'a line of its own for the tags').not.toBeNull()
    expect(within(tags!).getByText('Work')).toBeTruthy()
    expect(tags!.contains(titleOf('chat-plan')), 'the tags line is not the title\'s').toBe(false)
    expect(after(titleOf('chat-plan'), tags!), 'the tags come under the title').toBe(true)
  })

  it('marks a loop\'s, a code project\'s and a channel\'s chat before its title, and a chat you started not at all', async () => {
    await paint('board')
    for (const [key, kind] of [['chat-loop', 'loop'], ['chat-code', 'code'], ['chat-channel', 'channel']] as const) {
      const mark = cardOf(key).querySelector<HTMLElement>('[data-kind-mark]')
      expect(mark?.getAttribute('data-kind-mark'), `${key}'s mark`).toBe(kind)
      expect(after(mark!, titleOf(key)), `${key}'s mark is before its title`).toBe(true)
    }
    expect(cardOf('chat-plan').querySelector('[data-kind-mark]')).toBeNull()
  })
})

describe('its selection tick hangs on the card\'s corner', () => {
  it('sits on the top-right corner, outside the card\'s content, and names the chat', async () => {
    await paint('board')
    const tick = tickOf('chat-plan')
    expect(tick.parentElement, 'the tick is the card\'s own, not its content\'s').toBe(cardOf('chat-plan'))
    expect(bodyOf('chat-plan').contains(tick)).toBe(false)
    expect(classes(tick)).toEqual(expect.arrayContaining(['absolute', 'top-0', 'right-0', 'translate-x-1/2', '-translate-y-1/2']))
    expect(within(tick).getByRole('checkbox', { name: `Select ${LONG}` })).toBeTruthy()
  })

  it('shows on hover and on focus at rest, and on every card while selecting', async () => {
    await paint('board')
    const rest = classes(tickOf('chat-loop'))
    expect(rest).toContain('opacity-0')
    expect(rest).toEqual(expect.arrayContaining(['group-hover:opacity-100', 'focus-within:opacity-100']))
    fireEvent.click(within(tickOf('chat-plan')).getByRole('checkbox'))
    for (const s of SESSIONS) expect(classes(tickOf(s.key)), `${s.key} shows its tick while selecting`).toContain('opacity-100')
  })

  it('a tick selects without opening the peek, and a click on the card peeks', async () => {
    await paint('board')
    fireEvent.click(within(tickOf('chat-plan')).getByRole('checkbox'))
    expect((within(tickOf('chat-plan')).getByRole('checkbox') as HTMLInputElement).checked).toBe(true)
    expect(host.query.peek, 'a tick opens no peek').toBeUndefined()
    fireEvent.click(titleOf('chat-plan'))
    expect(host.query.peek).toBe('chat-plan')
  })
})

describe('the board\'s columns', () => {
  it('names the column of chats with no tag "No tag"', async () => {
    await paint('board')
    expect(screen.getByText('No tag')).toBeTruthy()
    expect(screen.queryByText('Untagged')).toBeNull()
    expect(cardOf('chat-channel')).toBeTruthy()
  })
})
