/** Tasks are a view of each project, and All tasks is a palette row (#3818).
 *
 *  Tasks was a sidebar row of its own. A project's tasks are a view of that project now: its page
 *  switches between Overview and Tasks (`#/projects/<id>?tab=tasks`), and the Tasks view is the task
 *  list and board, held to the project. The cross-project list stays at `#/tasks`, with every query
 *  it always read, and ⌘K's All tasks opens it. No address is retired.
 *
 *  The old shell's local state still opens the new home with what it held: a starter sidebar that
 *  had pinned Tasks keeps a row for Projects, where tasks went, and the remembered list view and
 *  scope still shape `#/tasks`. A project's Tasks view never writes the scope All tasks remembers.
 *
 *  The real shell is booted as `main.tsx` boots it, at each address, and asserted on what a person
 *  sees; the palette's rows are the ones the shell hands it.
 */
import { StrictMode } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import type { Command } from './CommandPalette'
import type { ProjectItem, TaskItem } from '../lib/api'

/** What the gateway answers; every other read stays on its way. */
const answers: Record<string, unknown> = {}
let rows: readonly Command[] = []

vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {}, refreshKinds: () => [] }))
vi.mock('../ui/DotGlow', () => ({ DotGlow: () => null }))
vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  const gateway = new Proxy({}, {
    get: (_t, method: string) => (...args: unknown[]) => method === 'dashboardConfig'
      ? Promise.resolve({ user_name: 'Ada', username: 'ada' })
      : method === 'project' ? Promise.resolve(PROJECTS.find((p) => p.id === args[0]))
        : method in answers ? Promise.resolve(structuredClone(answers[method])) : new Promise(() => {}),
  })
  return { ...real, api: gateway }
})
vi.mock('./CommandPalette', async (orig) => {
  const real = await orig<typeof import('./CommandPalette')>()
  return { ...real, CommandPalette: ({ commands }: { commands: Command[] }) => { rows = commands; return null } }
})

const PROJECTS: ProjectItem[] = [
  { id: 'p-garden', name: 'Garden', status: 'active' },
  { id: 'p-house', name: 'House move', status: 'active' },
]
const task = (id: string, title: string, project: string): TaskItem => ({ id, title, status: 'open', project } as TaskItem)
const TASKS = [task('t1', 'Order the soil', 'Garden'), task('t2', 'Book the movers', 'House move'), task('t3', 'Plant the beans', 'Garden')]

const { App } = await import('./App')
const { ErrorBoundary } = await import('./ErrorBoundary')
const { ThemeProvider } = await import('./theme')
const { AppearanceProvider } = await import('./appearance')
const { PersonalityProvider } = await import('./personality')
const { IdentityProvider } = await import('./identity')
const { installAppSdk } = await import('./appSdk')
const { registerBuiltinContentTypes } = await import('../ui/content/registerBuiltins')
const { resetDataStore } = await import('../lib/data')

installAppSdk()
registerBuiltinContentTypes()

beforeAll(async () => {
  await Promise.all([
    import('../pages/tasks/TasksSection'),
    import('../pages/projects/ProjectsSection'),
    import('../pages/dashboard/DashboardPage'),
    import('../pages/discover/DiscoverPage'),
  ])
}, 120_000)

beforeEach(() => {
  vi.stubGlobal('fetch', () => new Promise(() => {}))
  localStorage.clear()
  sessionStorage.clear()
  resetDataStore()
  for (const key of Object.keys(answers)) delete answers[key]
  Object.assign(answers, {
    projects: PROJECTS,
    allTasks: { tasks: TASKS, total: TASKS.length, complete: true, owner: 'ada' },
    taskLists: [],
    readyTasks: [],
    uLoops: [],
    projectWork: { board: [] },
  })
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function open(address: string) {
  cleanup()
  history.replaceState(null, '', `#/${address}`)
  render(
    <StrictMode>
      <ThemeProvider><AppearanceProvider><PersonalityProvider><IdentityProvider>
        <ErrorBoundary><App /></ErrorBoundary>
      </IdentityProvider></PersonalityProvider></AppearanceProvider></ThemeProvider>
    </StrictMode>,
  )
  await settle()
}

async function settle() {
  let last = ''
  let still = 0
  for (let tick = 0; tick < 600 && still < 3; tick++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 20)) })
    const now = `${location.hash}|${document.querySelector('main h1')?.textContent ?? ''}|${document.querySelectorAll('main li, main [role="row"]').length}`
    still = now === last && now.split('|')[1] ? still + 1 : 0
    last = now
  }
}

const heading = () => document.querySelector('main h1')?.textContent ?? ''
const rail = () => document.querySelector('nav[data-tour="rail"]') as HTMLElement
const railRows = () => within(rail()).getAllByRole('button').map((b) => b.getAttribute('aria-label') ?? '')
const projectView = () => screen.queryByRole('radiogroup', { name: 'Project view' })
const checked = (group: HTMLElement | null) => (group ? within(group).getAllByRole('radio').filter((r) => r.getAttribute('aria-checked') === 'true').map((r) => r.textContent) : [])
const shown = (title: string) => screen.queryAllByText(title).length > 0

describe('a project\'s tasks are a view of the project', () => {
  it('the sidebar has no Tasks row', async () => {
    await open('dashboard')
    expect(railRows()).toContain('Projects')
    expect(railRows()).not.toContain('Tasks')
  })

  it('a project\'s page switches between Overview and Tasks, and Tasks lists that project\'s tasks alone', async () => {
    await open('projects/p-garden')
    expect(checked(projectView())).toEqual(['Overview'])
    const entries = history.length
    fireEvent.click(within(projectView()!).getByRole('radio', { name: 'Tasks' }))
    await settle()
    expect(location.hash).toBe('#/projects/p-garden?tab=tasks')
    expect(history.length, 'a view of the page, so it replaces the entry').toBe(entries)
    expect(heading()).toBe('Garden')
    expect(checked(projectView())).toEqual(['Tasks'])
    expect(shown('Order the soil') && shown('Plant the beans')).toBe(true)
    expect(shown('Book the movers'), 'another project\'s task').toBe(false)
  })

  it('a reload, or a copied address, opens the project\'s board as it was', async () => {
    await open('projects/p-garden?tab=tasks&view=board')
    expect(checked(projectView())).toEqual(['Tasks'])
    expect(checked(screen.getByRole('radiogroup', { name: 'View' }))).toEqual(['Kanban board'])
    expect(shown('Order the soil')).toBe(true)
    expect(shown('Book the movers')).toBe(false)
  })

  it('a project\'s Tasks view leaves the scope All tasks remembers as it was', async () => {
    localStorage.setItem('tasks-scope', 'House move')
    await open('projects/p-garden?tab=tasks')
    expect(localStorage.getItem('tasks-scope')).toBe('House move')
  })
})

describe('All tasks', () => {
  it('⌘K\'s All tasks opens #/tasks, every project\'s tasks', async () => {
    await open('dashboard')
    const row = rows.find((r) => r.id === 'go:tasks')
    expect(row?.label).toBe('All tasks')
    act(() => row!.run())
    await settle()
    expect(location.hash).toBe('#/tasks')
    expect(heading()).toBe('All tasks')
    expect(shown('Order the soil') && shown('Book the movers')).toBe(true)
  })

  it('#/tasks keeps every query it read: the board, held to one project', async () => {
    await open('tasks?view=board&scope=Garden')
    expect(location.hash).toBe('#/tasks?view=board&scope=Garden')
    expect(checked(screen.getByRole('radiogroup', { name: 'View' }))).toEqual(['Kanban board'])
    expect(shown('Order the soil')).toBe(true)
    expect(shown('Book the movers')).toBe(false)
  })
})

describe('on a phone, where no ⌘K is, each moved page has an on-screen way in', () => {
  /** A phone's window: the rail is the drawer the shell's toggle opens. */
  function phone() {
    vi.stubGlobal('matchMedia', (q: string) => ({
      matches: q.includes('max-width'), media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false,
    }))
  }

  it('Tasks: the drawer\'s Projects, then All tasks there, or a project and its Tasks view', async () => {
    phone()
    await open('dashboard')
    // The drawer, opened as a person opens it.
    fireEvent.click(screen.getByRole('button', { name: 'Expand sidebar' }))
    await settle()
    const drawer = screen.getByRole('dialog', { name: 'Navigation' })
    const drawerRows = within(drawer).getAllByRole('button').map((b) => b.getAttribute('aria-label') ?? '')
    expect(drawerRows).toContain('Projects')
    expect(drawerRows).not.toContain('Tasks')
    await open('projects')
    fireEvent.click(screen.getByRole('button', { name: 'All tasks' }))
    await settle()
    expect(location.hash).toBe('#/tasks')
    expect(heading()).toBe('All tasks')
    await open('projects/p-garden')
    expect(projectView(), 'the project\'s view switch').not.toBeNull()
  })

  it('Notifications: the bell, which opens the Inbox on its notifications', async () => {
    phone()
    answers.notifications = { notifications: [], unread: 0 }
    await open('dashboard')
    expect(screen.getAllByRole('button', { name: /notifications/i }).length, 'the bell is on screen').toBeGreaterThan(0)
  })

  it('Discover: Home\'s deck, whose footer opens #/discover', async () => {
    phone()
    answers.discover = {
      enabled: true, visible_count: 2, total: 2, dismissed_count: 0,
      areas: [{ area: 'Stay organized', tips: [
        { id: 'projects', area: 'Stay organized', title: 'Group related work into a project', lesson: 'One place for it.', try_it: { route: 'projects', label: 'Open Projects' } },
        { id: 'inbox', area: 'Stay organized', title: 'Route messages into one inbox', lesson: 'One inbox.', try_it: { route: 'inbox', label: 'Open Inbox' } },
      ] }],
    }
    await open('dashboard')
    fireEvent.click(await screen.findByRole('button', { name: /See all 2 in Discover/ }))
    await settle()
    expect(location.hash).toBe('#/discover')
  })
})

describe('the old shell\'s local state opens the new home with what it held', () => {
  it('a starter sidebar that pinned Tasks shows Projects, where tasks went', async () => {
    localStorage.setItem('nav-disclosure', JSON.stringify({ mode: 'starter', pinned: ['tasks'] }))
    await open('dashboard')
    expect(railRows()).toContain('Projects')
    expect(railRows()).not.toContain('Tasks')
  })

  it('a starter sidebar opens #/tasks, keeps a row for Projects, and writes its record once', async () => {
    // The visit pinned `tasks`, the record read the pin back as `projects`, and the next render
    // pinned `tasks` again: thousands of writes a second until the page hit React's update limit.
    // Under `act` that loop never yields, so the twentieth change stops it as React's limit does.
    localStorage.setItem('nav-disclosure', JSON.stringify({ mode: 'starter', pinned: [] }))
    const writes = vi.spyOn(Storage.prototype, 'setItem')
    const dispatch = window.dispatchEvent.bind(window)
    let changes = 0
    const limit = vi.spyOn(window, 'dispatchEvent').mockImplementation((event) => {
      if (event.type === 'ne:nav-disclosure' && ++changes > 20) throw new Error('the record is rewritten in a loop')
      return dispatch(event)
    })
    await open('tasks')
    limit.mockRestore()
    expect(heading()).toBe('All tasks')
    expect(writes.mock.calls.filter(([key]) => key === 'nav-disclosure').length, 'the record is not rewritten in a loop').toBeLessThanOrEqual(1)
    expect(railRows()).toContain('Projects')
    expect(railRows()).not.toContain('Tasks')
    writes.mockRestore()
  })

  it('the remembered list view and scope still shape #/tasks', async () => {
    localStorage.setItem('tasks-view', 'board')
    localStorage.setItem('tasks-scope', 'House move')
    await open('tasks')
    expect(checked(screen.getByRole('radiogroup', { name: 'View' }))).toEqual(['Kanban board'])
    expect(shown('Book the movers')).toBe(true)
    expect(shown('Order the soil')).toBe(false)
  })

  it('a Discover deep link the old shell wrote opens Discover, with the tips the home still holds', async () => {
    answers.discover = {
      enabled: true, visible_count: 1, total: 2, dismissed_count: 1,
      areas: [{ area: 'Stay organized', tips: [
        { id: 'projects', area: 'Stay organized', title: 'Group related work into a project', lesson: 'One place for it.', try_it: { route: 'projects', label: 'Open Projects' } },
      ] }],
    }
    await open('discover')
    expect(location.hash).toBe('#/discover')
    expect(heading()).toMatch(/^Discover/)
    expect(shown('Group related work into a project')).toBe(true)
  })
})
