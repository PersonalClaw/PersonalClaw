import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act, cleanup, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Settings } from 'lucide-react'

// ── ⌘K finds what is inside chats, memory, knowledge and tasks ────────────────────────────────
//
// Measured before: the palette searched its own list of pages and actions and nothing else, so a
// chat you remembered by what was said in it was four pages away. Each source is searched through
// the API its own page uses, each hit opens where that page would open it, and a source that fails
// says so instead of looking empty.

const h = vi.hoisted(() => ({
  sessionsSearch: vi.fn(),
  searchEpisodic: vi.fn(),
  memorySemantic: vi.fn(),
  knowledgeItems: vi.fn(),
  searchTasks: vi.fn(),
}))

vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  return { ...real, api: { ...real.api, ...h } }
})

import { CommandPalette } from './CommandPalette'
import { ApiError } from '../lib/api'

const commands = [
  { id: 'go:settings', label: 'Open Settings', hint: 'Action', icon: Settings, run: vi.fn() },
]

beforeEach(() => {
  // jsdom has no layout, so no `scrollIntoView`; the palette keeps its cursor row in view with it.
  Element.prototype.scrollIntoView ??= () => {}
  h.sessionsSearch.mockReset().mockResolvedValue({
    sessions: [{ key: 'dashboard_chat-7', title: 'Budget planning', snippet: '…the <<budget>> for Q3…' }],
    source: 'index',
  })
  h.searchEpisodic.mockReset().mockResolvedValue([{ id: 'e1', text: 'Talked the budget through with Sam' }])
  h.memorySemantic.mockReset().mockResolvedValue([
    { key: 'budget.limit', value_json: '"4000 a month"' },
    { key: 'user.timezone', value_json: '"Europe/Lisbon"' },
  ])
  h.knowledgeItems.mockReset().mockResolvedValue({ items: [{ id: 'k1', title: 'Budget template', summary: 'A sheet' }], total: 1, page: 1, limit: 5 })
  h.searchTasks.mockReset().mockResolvedValue({ tasks: [{ id: 't1', title: 'Send the budget', status: 'open' }], total: 1 })
})

afterEach(() => cleanup())

async function openAndType(text: string) {
  const user = userEvent.setup()
  const navigate = vi.fn()
  render(<CommandPalette commands={commands} navigate={navigate} />)
  act(() => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true })) })
  const input = await screen.findByRole('searchbox')
  await user.type(input, text)
  return { user, navigate, input }
}

describe('⌘K content search', () => {
  it('finds a chat, a memory, a knowledge item and a task, each under its own heading', async () => {
    const { input } = await openAndType('budget')
    expect(input.getAttribute('aria-label'), 'the field says it searches content too').toBe('Search pages, actions and content')
    const list = screen.getByRole('listbox')
    for (const heading of ['Chats', 'Memory', 'Knowledge', 'Tasks']) {
      expect(await within(list).findByRole('group', { name: heading })).toBeTruthy()
    }
    expect(within(screen.getByRole('group', { name: 'Chats' })).getByRole('option', { name: /Budget planning/ })).toBeTruthy()
    const memory = screen.getByRole('group', { name: 'Memory' })
    expect(within(memory).getByRole('option', { name: /budget\.limit/ })).toBeTruthy()
    expect(within(memory).getByRole('option', { name: /Talked the budget through/ })).toBeTruthy()
    expect(within(memory).queryByRole('option', { name: /user\.timezone/ }), 'a fact that does not match stays out').toBeNull()
    expect(within(screen.getByRole('group', { name: 'Knowledge' })).getByRole('option', { name: /Budget template/ })).toBeTruthy()
    expect(within(screen.getByRole('group', { name: 'Tasks' })).getByRole('option', { name: /Send the budget/ })).toBeTruthy()
    // The index's match marks are not shown as text.
    expect(screen.getByText('…the budget for Q3…')).toBeTruthy()
  })

  it('opens each hit where its own page would open it', async () => {
    const { user, navigate } = await openAndType('budget')
    await user.click(await screen.findByRole('option', { name: /Budget planning/ }))
    expect(navigate).toHaveBeenCalledWith('chat/chat-7?find=budget')
    act(() => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true })) })
    await user.type(await screen.findByRole('searchbox'), 'budget')
    await user.click(await screen.findByRole('option', { name: /Send the budget/ }))
    expect(navigate).toHaveBeenLastCalledWith('tasks?open=t1')
  })

  it('reaches a content hit from the keyboard, after the commands', async () => {
    const { user, navigate } = await openAndType('budget')
    await screen.findByRole('option', { name: /Budget planning/ })
    await user.keyboard('{Enter}')
    expect(navigate).toHaveBeenCalledWith('chat/chat-7?find=budget')
  })

  it('says which source failed, and still shows the others', async () => {
    h.knowledgeItems.mockRejectedValue(new ApiError('the knowledge index is rebuilding', 503))
    await openAndType('budget')
    expect(await screen.findByText("Couldn't search your knowledge: the knowledge index is rebuilding")).toBeTruthy()
    expect(screen.getByRole('option', { name: /Budget planning/ })).toBeTruthy()
    expect(screen.queryByRole('group', { name: 'Knowledge' })).toBeNull()
  })

  it('lists a chat once when the search answers it under both spellings of its key', async () => {
    h.sessionsSearch.mockResolvedValue({
      sessions: [
        { key: 'dashboard:chat-7', title: 'Budget planning' },
        { key: 'dashboard_chat-7', title: 'Budget planning' },
      ],
      source: 'index',
    })
    await openAndType('budget')
    const chats = await screen.findByRole('group', { name: 'Chats' })
    expect(within(chats).getAllByRole('option')).toHaveLength(1)
  })

  it('says when the chats it found come from only part of them', async () => {
    // While the search index is still being built, a chats answer covers the chats it holds; a
    // short list must not read as all there is.
    h.sessionsSearch.mockResolvedValue({
      sessions: [{ key: 'dashboard_chat-7', title: 'Budget planning' }], source: 'index',
      searched: { chats: 3210, of: 12005 }, complete: false, index: { indexed: 3210, of: 12005, building: true, long: 0 },
    })
    await openAndType('budget')
    const note = await screen.findByText(/^Searched 3,210 of 12,005 chats — the search index is still being built/)
    expect(note.getAttribute('data-partial')).toBe('true')
    expect(within(screen.getByRole('group', { name: 'Chats' })).getByRole('option', { name: /Budget planning/ })).toBeTruthy()
  })

  it('does not search content for a single character', async () => {
    await openAndType('b')
    await new Promise((r) => setTimeout(r, 400))
    expect(h.sessionsSearch).not.toHaveBeenCalled()
    expect(h.searchTasks).not.toHaveBeenCalled()
  })

  it('shows the page commands as before', async () => {
    await openAndType('settings')
    expect(screen.getByRole('option', { name: /Open Settings/ })).toBeTruthy()
    await waitFor(() => expect(h.sessionsSearch).toHaveBeenCalledWith('settings'))
  })
})
