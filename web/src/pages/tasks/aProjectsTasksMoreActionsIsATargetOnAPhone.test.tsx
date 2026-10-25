// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { FolderKanban } from 'lucide-react'
import { resetDataStore } from '../../lib/data'
import { HeaderControl } from '../../ui/HeaderActions'

// ── A task list's `…` is a 44 px target on a phone ───────────────────────────────────────────────
//
// The shell's corner controls became 44 px targets below 1024 px, and the corners grew with them.
// At 768 px the band between them no longer holds a project's Tasks header (the view switcher, New
// task and All projects), so it folds into the `…` (More actions), which the UI matrix measured
// under 44 px on #/projects/<id>?tab=tasks, as it already had at 320, 390 and 412. The task list
// gives its `…` the compact widths' floor (`TOUCH_TARGET`), as the Inbox does.
//
// jsdom has no layout: the header's widths are stubbed so the cluster has no room and folds into
// the `…`, and the classes are pinned, since they are what set the box.

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

describe("a project's Tasks header on a phone", () => {
  it('folds into a `…` that carries the floor', async () => {
    // Every probe wants 400 px and every box offers 60: no tier fits, so the cluster folds.
    vi.spyOn(HTMLElement.prototype, 'scrollWidth', 'get').mockReturnValue(400)
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(60)
    render(
      <TasksListPage
        onCreate={noop} view="list" filter="all" openId="" setView={noop} setFilter={noop}
        setOpenId={noop} editing={false} setEditing={noop}
        q="" sort="" scope="" list="" tag=""
        setQ={noop} setSort={noop} setScope={noop} setList={noop} setTag={noop}
        inProject={{ name: 'Launch notes', title: <span data-type="title-l">Launch notes</span>, actions: <HeaderControl icon={FolderKanban} label="All projects" onClick={noop} /> }}
      />,
    )
    const more = await screen.findByRole('button', { name: 'More actions' })
    const cls = more.className.toString().split(/\s+/)
    expect(cls.includes('max-lg:min-h-11') && cls.includes('max-lg:min-w-11'), more.className).toBe(true)
  })
})
