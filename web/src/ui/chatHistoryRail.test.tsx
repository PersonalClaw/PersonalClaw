import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import type { Thread } from '../lib/useThreads'
import { ThreadRail } from './ThreadRail'

// ── The sidebar's middle is Chat history, grouped by your folders or tags (#5877) ──────────────────
//
// 🔴 Red before: it was "Threads", with New thread and Search threads, a word the rest of PersonalClaw
// does not use for what Chat history lists, grouped by project, with no way to group by the folders
// and tags Chat history keeps. Now it is Chat history: New chat, Search chats with a Group by control
// at the end of its row (Folders or Tags), and a goal loop or code session says its kind on its row.

const t = (key: string, over: Partial<Thread> = {}): Thread => ({
  key, kind: 'chat', id: key, title: `Title ${key}`, projectId: '', route: `chat/${key}`,
  pip: null, pinned: false, activeAt: 0, folderId: '', tagIds: [], ...over,
})
const GROUPS = [
  { id: 'folder:f-work', name: 'Work', threads: [t('chat:a'), t('loop:l1', { kind: 'loop', title: 'Ship the release', route: 'loops/l1' })] },
  { id: 'folder:', name: 'No folder', threads: [t('code:c1', { kind: 'code', title: 'Fix the login', route: 'code/c1' })] },
]

function rail(over: Partial<Parameters<typeof ThreadRail>[0]> = {}) {
  return render(<ThreadRail groups={GROUPS} selectedKey="" status="ready" onOpen={() => {}} onRetry={() => {}}
    onNewThread={() => {}} groupBy="folder" onGroupBy={() => {}} {...over} />)
}
afterEach(cleanup)

/** Every word a person reads or hears from *el*: its text, and each name, title and placeholder. */
function words(el: HTMLElement): string {
  const attrs = [...el.querySelectorAll('*')].flatMap((e) => ['aria-label', 'title', 'placeholder'].map((a) => e.getAttribute(a) ?? ''))
  return [el.textContent ?? '', ...attrs].join(' ')
}

describe('Chat history in the sidebar', () => {
  it('is Chat history, with New chat and Search chats, and says no "thread" anywhere', () => {
    const { container } = rail()
    expect(screen.getByRole('group', { name: 'Chat history' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^New chat/ })).toBeInTheDocument()
    expect(screen.getByRole('searchbox', { name: 'Search chats' })).toBeInTheDocument()
    expect(words(container)).not.toMatch(/thread/i)
  })

  it('says no "thread" while loading, failed, offline, empty or matching nothing', () => {
    for (const status of ['loading', 'failed', 'offline'] as const) {
      const { container } = rail({ status })
      expect(words(container), status).not.toMatch(/thread/i)
      cleanup()
    }
    const empty = rail({ groups: [] })
    expect(words(empty.container)).not.toMatch(/thread/i)
    expect(screen.getByText('No chats yet')).toBeInTheDocument()
    cleanup()
    const none = rail()
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search chats' }), { target: { value: 'zzz' } })
    expect(words(none.container)).not.toMatch(/thread/i)
  })

  it('names each group by its folder, with how many chats it holds', () => {
    rail()
    expect(screen.getByRole('button', { name: 'Work, 2 chats' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'No folder, 1 chat' })).toBeInTheDocument()
  })

  it('a goal loop or code session says its kind on its row; a chat says none', () => {
    rail()
    expect(screen.getByRole('button', { name: 'Goal loop: Ship the release' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Code session: Fix the login' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Title chat:a' })).toBeInTheDocument()
  })

  it('the Group by control switches between folders and tags', () => {
    const onGroupBy = vi.fn()
    rail({ onGroupBy })
    const control = screen.getByRole('button', { name: 'Group by: Folders' })
    expect(within(screen.getByRole('group', { name: 'Chat history' })).getByRole('button', { name: 'Group by: Folders' })).toBe(control)
    fireEvent.click(control)
    fireEvent.click(screen.getByRole('button', { name: 'Tags' }))
    expect(onGroupBy).toHaveBeenCalledWith('tag')
    cleanup()
    rail({ groupBy: 'tag' })
    expect(screen.getByRole('button', { name: 'Group by: Tags' })).toBeInTheDocument()
  })

  it('the Group by control stays pinned with New chat and the search', () => {
    rail()
    const bar = screen.getByRole('button', { name: /^New chat/ }).parentElement!
    expect(getComputedStyle(bar).position).toBe('sticky')
    expect(bar.contains(screen.getByRole('button', { name: 'Group by: Folders' }))).toBe(true)
    expect(bar.contains(screen.getByRole('searchbox', { name: 'Search chats' }))).toBe(true)
  })
})
