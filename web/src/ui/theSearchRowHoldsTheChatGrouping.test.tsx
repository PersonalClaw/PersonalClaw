import { describe, it, expect, afterEach } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThreadRail } from './ThreadRail'
import type { Thread } from '../lib/useThreads'

// ── The sidebar's search row holds the chat grouping ─────────────────────────────────────────────
//
// The sidebar's chat history opened on a row of its own: a "Chat history" label and, at its end,
// the Group by control. The label repeated what the list is, and the row cost the sidebar its
// height. The row is gone: the Group by control sits at the end of the Search chats row, the
// field's height, after the search in tab order. The list keeps "Chat history" as its name, so a
// screen reader still hears what it is.

const t = (key: string): Thread => ({
  key, kind: 'chat', id: key, title: `Title ${key}`, projectId: '', route: `chat/${key}`,
  pip: null, pinned: false, activeAt: 0, folderId: '', tagIds: [],
})
const GROUPS = [{ id: 'folder:', name: 'No folder', threads: [t('chat:a')] }]

function rail() {
  return render(<ThreadRail groups={GROUPS} selectedKey="" status="ready" onOpen={() => {}} onRetry={() => {}}
    onNewThread={() => {}} groupBy="folder" onGroupBy={() => {}} />)
}
afterEach(cleanup)

const control = () => screen.getByRole('button', { name: 'Group by: Folders' })
const search = () => screen.getByRole('searchbox', { name: 'Search chats' })

describe('the search row holds the chat grouping', () => {
  it('shows no "Chat history" label, and the list keeps it as its name', () => {
    rail()
    const list = screen.getByRole('group', { name: 'Chat history' })
    expect(screen.queryByText('Chat history')).toBeNull()
    expect(list.contains(search())).toBe(true)
  })

  it('puts the Group by control in the Search chats row, not a row of its own', () => {
    rail()
    // The smallest box that holds both is the search row: it holds New chat only if the control
    // sat anywhere above the field.
    let row = search().parentElement!
    while (!row.contains(control())) row = row.parentElement!
    expect(row.contains(screen.getByRole('button', { name: /^New chat/ }))).toBe(false)
  })

  it('is the height of the field beside it', () => {
    rail()
    expect(control().className).toMatch(/\bh-8\b/)
    expect(search().closest('.h-8')).not.toBeNull()
  })

  it('comes after the search in tab order', async () => {
    const user = userEvent.setup()
    rail()
    screen.getByRole('button', { name: /^New chat/ }).focus()
    await user.tab()
    expect(document.activeElement).toBe(search())
    await user.tab()
    expect(document.activeElement).toBe(control())
  })
})
