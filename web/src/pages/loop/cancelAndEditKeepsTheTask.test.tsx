import { it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

// ── "Cancel and edit the task" keeps the task and stops the planning ──────────────────────────
//
// 🔴 Before: the walkthrough's Cancel only navigated to the composer. Nothing was sent, so the
// cancelled loop stayed "planning" with its planner still drafting, and the composer came back
// empty on "New project", "Fresh start" and "Unattended" — the task, its project, its codebase
// and its Mode all gone. Now the cancel deletes the draft (which ends its planner) after reading
// what was typed for it, and the composer opens with that.

const LOOP = {
  id: 'ab12cd34',
  kind: 'code',
  name: 'Digest titles',
  task: 'Fix the double-escaped digest titles and add a Fixed line to CHANGELOG.',
  project_id: 'p-0a1b2c3d',
  attended: true,
  workspace_dir: '/home/user/src/newsfold',
  kind_config: { project_kind: 'brownfield' },
}

const calls: string[] = []
let deleteFails = false

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const api = new Proxy({} as Record<string, unknown>, {
    get(t, p: string) {
      if (p in t) return t[p]
      return (t[p] = () => Promise.resolve([]))
    },
  })
  Object.assign(api, {
    uLoop: (id: string) => { calls.push(`read ${id}`); return Promise.resolve(LOOP) },
    deleteULoop: (id: string) => {
      calls.push(`delete ${id}`)
      return deleteFails ? Promise.reject(new Error('The gateway is restarting.')) : Promise.resolve()
    },
    project: () => Promise.resolve({ id: LOOP.project_id, name: 'newsfold 1.0', workspace_dir: LOOP.workspace_dir }),
  })
  return { ...real, api }
})

beforeEach(() => {
  calls.length = 0
  deleteFails = false
  // jsdom ships no matchMedia; the composer reads it for its mobile breakpoint.
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: (query: string) => ({
      matches: false, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }),
  })
})

function queryOf(route: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [k, v] of new URLSearchParams(route.slice(route.indexOf('?') + 1))) out[k] = v
  return out
}

it('reads what was typed, then deletes the draft, and the composer opens with it', async () => {
  const { cancelPlanningToEdit } = await import('./editTask')
  const { LoopSection } = await import('./LoopSection')
  const { AppearanceProvider } = await import('../../app/appearance')

  const to = await cancelPlanningToEdit(LOOP.id)

  expect(calls, 'the task is read before the draft that holds it goes').toEqual([`read ${LOOP.id}`, `delete ${LOOP.id}`])
  expect(to?.startsWith('loop?')).toBe(true)
  const query = queryOf(to!)
  render(
    <AppearanceProvider>
      <LoopSection sub="" navigate={() => {}} navEpoch={0} query={query} setQuery={() => {}} />
    </AppearanceProvider>,
  )

  const input = await screen.findByRole('textbox', { name: 'Message input' })
  await waitFor(() => expect(input.textContent).toContain(LOOP.task))
  expect(screen.getByRole('radio', { name: 'Attended' }).getAttribute('aria-checked')).toBe('true')
  expect(screen.getByRole('radio', { name: 'Existing codebase' }).getAttribute('aria-checked')).toBe('true')
  expect(screen.getByText('src/newsfold')).toBeTruthy()
})

it('a cancel that does not go through leaves the walkthrough where it is', async () => {
  const { cancelPlanningToEdit } = await import('./editTask')
  deleteFails = true

  expect(await cancelPlanningToEdit(LOOP.id)).toBeNull()
})

it('a Code loop with no project brings its codebase back as the path typed for it', async () => {
  const { composerRouteFor } = await import('./editTask')
  const { LoopSection } = await import('./LoopSection')
  const { AppearanceProvider } = await import('../../app/appearance')
  const loose = { ...LOOP, project_id: '' }

  const query = queryOf(composerRouteFor(loose as unknown as import('../../lib/api').Loop))
  render(
    <AppearanceProvider>
      <LoopSection sub="" navigate={() => {}} navEpoch={0} query={query} setQuery={() => {}} />
    </AppearanceProvider>,
  )

  expect(query.ws, 'not offered as a project\'s codebase').toBeUndefined()
  const path = await screen.findByPlaceholderText(/Codebase path/)
  expect((path as HTMLInputElement).value).toBe(LOOP.workspace_dir)
  expect(screen.getByRole('radio', { name: 'Existing codebase' }).getAttribute('aria-checked')).toBe('true')
})

it('a fresh-start Code loop does not hand its own workspace back as an existing codebase', async () => {
  const { composerRouteFor } = await import('./editTask')
  const fresh = { ...LOOP, kind_config: { project_kind: 'greenfield' } }

  const query = queryOf(composerRouteFor(fresh as unknown as import('../../lib/api').Loop))

  expect(query.ws).toBeUndefined()
  expect(query.codebase).toBeUndefined()
  expect(query.task).toBe(LOOP.task)
})
