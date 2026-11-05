// @vitest-environment jsdom
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FolderKanban } from 'lucide-react'
import { resetDataStore } from '../../lib/data'
import { api } from '../../lib/api'
import { HeaderControl } from '../../ui/HeaderActions'
import { findHeaderFilter, headerFilter } from '../../test/headerFilter'

// ── A search keeps a project's filter as it was ──────────────────────────────────────────────────
//
// A project's Tasks filter held one section, Status, which hid while a query was typed. The filter
// stayed, but its label lost the chosen Status on the first key and changed width, so the row
// re-tiered under the field being typed in. Status stays while searching and narrows the matches
// too. The query here is the page's own, through the search's debounce.

const BLOCKED = { id: 't-1', title: 'Weed the beds', status: 'blocked', priority: 'medium', project: 'Launch notes' }
const OPEN = { id: 't-2', title: 'Oil the beds', status: 'open', priority: 'medium', project: 'Launch notes' }

vi.mock('../../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      allTasks: vi.fn(async () => ({ tasks: [BLOCKED, OPEN], total: 2, complete: true, owner: '' })),
      projects: vi.fn(async () => []),
      taskLists: vi.fn(async () => []),
      readyTasks: vi.fn(async () => []),
      searchTasks: vi.fn(async () => ({ tasks: [BLOCKED, OPEN], total: 2 })),
      uLoops: vi.fn(async () => []),
    },
  }
})
vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

const { TasksListPage } = await import('./TasksListPage')
const noop = () => {}

afterEach(() => {
  cleanup()
  resetDataStore()
})

function Page() {
  const [q, setQ] = useState('')
  return (
    <TasksListPage
      onCreate={noop} view="list" filter="blocked" openId="" setView={noop} setFilter={noop}
      setOpenId={noop} editing={false} setEditing={noop}
      q={q} sort="" scope="" list="" tag=""
      setQ={setQ} setSort={noop} setScope={noop} setList={noop} setTag={noop}
      inProject={{ name: 'Launch notes', title: <span data-type="title-l">Launch notes</span>, actions: <HeaderControl icon={FolderKanban} label="All projects" onClick={noop} /> }}
    />
  )
}

describe("a project's Tasks header while searching", () => {
  it("keeps its filter and the filter's label, the chosen Status narrowing the matches", async () => {
    render(<Page />)
    await screen.findByText('Weed the beds')
    const filter = await findHeaderFilter()
    const said = filter.getAttribute('aria-label')
    expect(said, 'the label names the chosen Status').toContain('Blocked')

    await userEvent.type(screen.getByRole('searchbox', { name: 'Search tasks' }), 'beds')
    await waitFor(() => expect(api.searchTasks).toHaveBeenCalledWith(expect.objectContaining({ query: 'beds' })), { timeout: 2000 })
    await new Promise((r) => setTimeout(r, 50))
    expect(headerFilter(), 'the same filter in the row').toBe(filter)
    expect(filter.getAttribute('aria-label'), 'its label once the search answered').toBe(said)
    expect(screen.getByText('Weed the beds')).toBeTruthy()
    expect(screen.queryByText('Oil the beds'), 'an Open match, under Blocked').toBeNull()
  })
})
