import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, cleanup, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Compass, Settings, Terminal } from 'lucide-react'

// ── ⌘K finds a page by the words its row shows ─────────────────────────────────────────────────
//
// Every page's row reads "<Page> · Go to", and the palette matched the typed text as ONE substring
// of "<label> <hint> <keywords>". So "Go to Discover" — the row's own words, in the order a person
// says them — matched no command, the first row was a chat the content search found, and Enter
// opened that chat.

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

import { CommandPalette, rankCommands } from './CommandPalette'

const discover = vi.fn()
const commands = [
  { id: 'go:settings', label: 'Settings', hint: 'Go to', icon: Settings, keywords: 'preferences', run: vi.fn() },
  { id: 'go:discover', label: 'Discover', hint: 'Go to', icon: Compass, keywords: 'tips tour learn features guide', run: discover },
  { id: 'act:terminal-drawer', label: 'Toggle terminal drawer', hint: 'Action', icon: Terminal, keywords: 'shell pty console', run: vi.fn() },
]
const ids = (q: string) => rankCommands(commands, q).map((c) => c.id)

beforeEach(() => {
  Element.prototype.scrollIntoView ??= () => {}
  discover.mockReset()
  h.sessionsSearch.mockReset().mockResolvedValue({
    sessions: [{ key: 'dashboard_chat-9', title: 'Investigating an issue', snippet: 'we should <<go>> <<to>> the <<discover>> page' }],
    source: 'index',
  })
  h.searchEpisodic.mockReset().mockResolvedValue([])
  h.memorySemantic.mockReset().mockResolvedValue([])
  h.knowledgeItems.mockReset().mockResolvedValue({ items: [], total: 0, page: 1, limit: 5 })
  h.searchTasks.mockReset().mockResolvedValue({ tasks: [], total: 0 })
})

afterEach(() => cleanup())

describe('⌘K matches a command by its words', () => {
  it('finds a page by its row\'s own words, "Go to <Page>"', () => {
    expect(ids('Go to Discover')).toEqual(['go:discover'])
    expect(ids('go to disc')).toEqual(['go:discover'])
  })

  it('still ranks a label match above a keyword match', () => {
    expect(ids('discover')).toEqual(['go:discover'])
    expect(ids('tour')).toEqual(['go:discover'])
    expect(ids('terminal')).toEqual(['act:terminal-drawer'])
    expect(ids('set')[0]).toBe('go:settings')
  })

  it('needs every word it was given', () => {
    expect(ids('go to nowhere')).toEqual([])
    expect(ids('open discover drawer')).toEqual([])
  })

  it('Enter on "Go to Discover" opens Discover, not a chat that mentions it', async () => {
    const user = userEvent.setup()
    const navigate = vi.fn()
    render(<CommandPalette commands={commands} navigate={navigate} />)
    act(() => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true })) })
    await user.type(await screen.findByRole('searchbox'), 'Go to Discover')
    const list = screen.getByRole('listbox')
    // The content search's chat hit arrives too, BELOW the command.
    await within(list).findByRole('group', { name: 'Chats' })
    expect(within(list).getAllByRole('option')[0].textContent).toContain('Discover')
    await user.keyboard('{Enter}')
    expect(discover).toHaveBeenCalledTimes(1)
    expect(navigate).not.toHaveBeenCalled()
  })
})
