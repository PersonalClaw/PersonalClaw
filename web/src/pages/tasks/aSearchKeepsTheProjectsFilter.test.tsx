// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { FolderKanban } from 'lucide-react'
import { resetDataStore } from '../../lib/data'
import { HeaderControl } from '../../ui/HeaderActions'

// ── A search keeps a project's filter in its header ──────────────────────────────────────────────
//
// A project's Tasks filter held one section, Status, which hid while a query was typed, so the
// first key took the filter out of the row and the row re-tiered under the field being typed in.
// Status stays while searching and narrows the matches too.

vi.mock('../../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      tasks: vi.fn(async () => ({ tasks: [], owner: '' })),
      allTasks: vi.fn(async () => ({ tasks: [], total: 0, complete: true, owner: '' })),
      projects: vi.fn(async () => []),
      taskLists: vi.fn(async () => []),
      readyTasks: vi.fn(async () => []),
      searchTasks: vi.fn(async () => ({ tasks: [] })),
      uLoops: vi.fn(async () => []),
    },
  }
})
vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

const { TasksListPage } = await import('./TasksListPage')
const noop = () => {}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  resetDataStore()
})

function Page({ q }: { q: string }) {
  return (
    <TasksListPage
      onCreate={noop} view="list" filter="all" openId="" setView={noop} setFilter={noop}
      setOpenId={noop} editing={false} setEditing={noop}
      q={q} sort="" scope="" list="" tag=""
      setQ={noop} setSort={noop} setScope={noop} setList={noop} setTag={noop}
      inProject={{ name: 'Launch notes', title: <span data-type="title-l">Launch notes</span>, actions: <HeaderControl icon={FolderKanban} label="All projects" onClick={noop} /> }}
    />
  )
}

describe("a project's Tasks header while searching", () => {
  it('keeps its filter, whatever the query', () => {
    const { container, rerender } = render(<Page q="" />)
    const filter = () => container.querySelector('header [data-header-filter]:not([inert] *)')
    expect(filter()).not.toBeNull()
    rerender(<Page q="no such task" />)
    expect(filter(), 'the first key took the filter out of the row').not.toBeNull()
  })
})
