import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import type { ChatSessionSummary } from '../../lib/api'
import { FromChannel } from './FromChannel'

// ── A chat that came in on a chat channel says which one, wherever chats are listed ──────────────
//
// A Telegram DM starts a chat through the channel door, and the chat list served it as one opened
// here: it sat among your own chats with the same icon and nothing saying it came from Telegram.
// The list now carries it under the Channels scope with the channel's name (`source_label`), and a
// row for it says "From <channel>".

const MINE: ChatSessionSummary = {
  key: 'chat-8-1', title: 'Grocery list', messages: 4, origin: 'manual', lifecycle: 'active',
}
const PHONE: ChatSessionSummary = {
  key: 'chat-9-1', title: 'Daily agenda overview', messages: 19, origin: 'channel',
  source_id: '5550001234', source_label: 'Telegram', lifecycle: 'active',
}

vi.mock('../../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      chatSessions: () => Promise.resolve([MINE, PHONE]),
      chatFolders: () => Promise.resolve([]),
      chatTags: () => Promise.resolve([]),
      rooms: () => Promise.resolve({ rooms: [] }),
      retagStatus: () => Promise.resolve({ id: '', status: 'done', done: 0, total: 0, updated: 0, skipped: 0, errors: 0, current: '', error: '' }),
    },
  }
})

import { ChatPage } from '../ChatPage'

beforeEach(() => {
  sessionStorage.clear()
})

describe('the mark', () => {
  it('names the channel a chat came in on', () => {
    render(<FromChannel s={PHONE} />)
    const mark = screen.getByText('From Telegram').closest('span[title]') as HTMLElement
    expect(mark.getAttribute('title')).toBe('This chat came in on Telegram.')
  })

  it('says a chat channel came in when that channel is no longer set up here', () => {
    render(<FromChannel s={{ origin: 'channel', source_label: '' }} />)
    expect(screen.getByText('From a chat channel')).toBeTruthy()
  })

  it('is absent on a chat that did not come in on a channel', () => {
    const { container } = render(<><FromChannel s={MINE} /><FromChannel s={{ origin: 'loop', source_label: 'Nightly' }} /></>)
    expect(container.innerHTML).toBe('')
  })
})

describe('the chat history', () => {
  it('keeps a channel chat under Channels, marked with its channel', async () => {
    const setQuery = vi.fn()
    const { rerender } = render(<ChatPage sub="history" navigate={() => {}} query={{}} setQuery={setQuery} />)
    await screen.findByText('Grocery list')
    expect(screen.queryByText('Daily agenda overview'), 'your own chats are the default scope').toBeNull()

    const tab = screen.getByRole('radio', { name: /Channels 1/ })
    fireEvent.click(tab)
    rerender(<ChatPage sub="history" navigate={() => {}} query={{ origin: 'channel' }} setQuery={setQuery} />)
    const title = await screen.findByText('Daily agenda overview')
    const row = title.closest('[role="button"]') as HTMLElement
    expect(within(row).getByText('From Telegram')).toBeTruthy()
    expect(screen.queryByText('Grocery list')).toBeNull()
  })
})
