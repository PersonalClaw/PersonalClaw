import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── "Attach to another chat…" picks from the chats the sidebar lists, in its order ─────────────
//
// The picker reads the sidebar's own rows (`useThreads`), keeps the chats (no goal loop, code
// session or archived chat), orders them as the sidebar's Recent activity does, pinned first
// (`chatListView.rowOrder('activity')`), and finds one by name as the sidebar's search does.

const h = vi.hoisted(() => ({ chatSessions: vi.fn() }))
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      chatSessions: h.chatSessions,
      uLoops: () => Promise.resolve([{ id: 'loop-1', name: 'Nightly digest', kind: 'goal', status: 'running', created_at: '2026-11-01T08:00:00Z' }]),
      inboxOpen: () => Promise.resolve([]),
      approvals: () => Promise.resolve([]),
      projects: () => Promise.resolve([]),
    },
  }
})

import { ChatPicker } from './ChatPicker'

const at = (h_: number) => new Date(Date.UTC(2026, 10, 2, h_)).toISOString()

beforeEach(() => {
  h.chatSessions.mockReset().mockResolvedValue([
    { key: 'chat-old', title: 'Garden plans', updated: at(6) },
    { key: 'chat-new', title: 'Lisbon weekend', updated: at(11) },
    { key: 'chat-pin', title: 'Homelab notes', updated: at(2), pinned: true },
  ])
})
afterEach(() => cleanup())

function open() {
  const onPick = vi.fn()
  const onClose = vi.fn()
  render(<ChatPicker onPick={onPick} onClose={onClose} />)
  return { onPick, onClose }
}

const rows = () => within(screen.getByRole('dialog')).queryAllByRole('button').map((b) => b.textContent?.trim() ?? '')
  .filter((t) => ['Garden plans', 'Lisbon weekend', 'Homelab notes', 'Nightly digest'].some((name) => t.includes(name)))

describe('the chat picker', () => {
  it('🔑 lists the chats, pinned first, then by recent activity, and no goal loop', async () => {
    open()
    await waitFor(() => expect(rows()).toHaveLength(3))
    expect(rows().map((t) => t.replace(/\s+/g, ' '))).toEqual(['Homelab notes', 'Lisbon weekend', 'Garden plans'])
  })

  it('🔑 finds a chat by name, as the sidebar does, and picks it by its key', async () => {
    const user = userEvent.setup()
    const { onPick, onClose } = open()
    await waitFor(() => expect(rows()).toHaveLength(3))
    await user.type(screen.getByRole('searchbox', { name: 'Search chats' }), 'lisbon')
    expect(rows()).toEqual(['Lisbon weekend'])
    await user.click(screen.getByRole('button', { name: /Lisbon weekend/ }))
    expect(onPick).toHaveBeenCalledWith('chat-new')
    expect(onClose).toHaveBeenCalled()
  })

  it('says when no chat matches', async () => {
    const user = userEvent.setup()
    open()
    await waitFor(() => expect(rows()).toHaveLength(3))
    await user.type(screen.getByRole('searchbox', { name: 'Search chats' }), 'zz')
    // The narrowed state is the kit's, as the chat history's is: it says the chats are there, and
    // Clear search brings them back.
    expect(screen.getByText('No chats match “zz”')).toBeTruthy()
    expect(screen.getByText('You have 3 chats — just none matching the search.')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Clear search' }))
    expect(rows()).toHaveLength(3)
  })

  it('says the chats could not be loaded, and offers Retry', async () => {
    h.chatSessions.mockRejectedValue(new Error('the gateway is restarting'))
    open()
    expect(await screen.findByText(/Couldn't load your chats/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /Retry/ })).toBeTruthy()
  })
})
