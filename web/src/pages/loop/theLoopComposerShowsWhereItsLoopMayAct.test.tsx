import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── The loop composer shows where its loop may act, beside the input (#3808) ─────────────────
//
// Wherever the agent can act, its permission mode is next to the input. A loop's is its Mode
// (Attended or Unattended), which sat in the page's header. It now sits in the composer's footer as
// the loop's Execution Boundary control, beside "Runs on": the Mode, which it changes, and the
// folder the loop will work in, the sandbox its commands run in and the network its work reaches,
// which it shows. The sandbox and the network are the gateway's words for a loop not created yet,
// read for its runtime and its Mode (`GET /api/chat/boundary?loop=`), and read again when either
// changes. The loop is created with the Mode the control shows.
//
// Driven through the real LoopComposer; what is asserted is what a person sees and reads, the
// reads the page makes, and the body `POST /api/loops` was sent.

const created: Record<string, unknown>[] = []
const boundaryReads: Array<{ session?: string; runtime?: string; loop?: string; kind?: string }> = []
const classify = { hold: false, held: [] as Array<() => void> }

const READINGS = {
  unattended: { sandbox: { confined: true, label: 'Sandboxed', said: 'The commands it runs are sandboxed on this computer.' },
    reach: { tier: 'registry', label: 'Package registries only', said: 'Its web tools reach only package registries.' } },
  attended: { sandbox: { confined: true, label: 'Sandboxed', said: 'The commands it runs are sandboxed on this computer.' },
    reach: { tier: 'all', label: 'Network on', said: 'Its web tools reach public hosts.' } },
} as const

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const api = new Proxy({} as Record<string, unknown>, {
    get(t, p: string) {
      if (p in t) return t[p]
      return (t[p] = () => Promise.resolve([]))
    },
  })
  Object.assign(api, {
    chatBoundary: (q: { session?: string; runtime?: string; loop?: 'attended' | 'unattended' } = {}) => {
      boundaryReads.push(q)
      return Promise.resolve(READINGS[q.loop ?? 'attended'])
    },
    agentProviders: () => Promise.resolve([]),
    classifyULoop: () => {
      const answer = { title: 'Health endpoint', summary: '', kind_config: {}, plan: [], intake_rigor: 'minimal' }
      return classify.hold ? new Promise((resolve) => { classify.held.push(() => resolve(answer)) }) : Promise.resolve(answer)
    },
    validateULoop: () => Promise.resolve({ can_start: true, errors: [], warnings: [] }),
    createULoop: (body: Record<string, unknown>) => { created.push(body); return Promise.resolve({ id: 'ab12cd34', kind: 'goal', files_dir: '' }) },
  })
  return { ...real, api }
})

beforeEach(() => {
  created.length = 0
  boundaryReads.length = 0
  classify.hold = false
  classify.held.length = 0
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: (query: string) => ({
      matches: false, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }),
  })
})

async function composer(props: Record<string, unknown> = {}) {
  const { LoopComposer } = await import('./LoopComposer')
  const { AppearanceProvider } = await import('../../app/appearance')
  render(
    <AppearanceProvider>
      <LoopComposer onCreated={vi.fn()} onHistory={() => {}} initialKind="goal"
        initialTask="Find out why the nightly build got slower." {...props} />
    </AppearanceProvider>,
  )
  return screen.findByRole('button', { name: /^Execution boundary:/ })
}

describe('the loop’s Mode', () => {
  it('sits beside the input, not in the header', async () => {
    const pill = await composer()
    expect(pill.getAttribute('aria-label')).toBe('Execution boundary: Unattended')
    const header = document.querySelector('header') as HTMLElement
    expect(within(header).queryByRole('group', { name: 'Mode' }), 'no Mode dial left in the header').toBeNull()
    expect(within(header).queryByRole('button', { name: /Unattended|Attended/ })).toBeNull()
    // The composer holds it, with "Runs on", beside the input.
    const stage = pill.closest('[data-composer-stage]')
    expect(stage, 'in the composer').not.toBeNull()
    expect(stage!.contains(screen.getByRole('button', { name: /^Runs on:/ }))).toBe(true)
    expect(stage!.contains(screen.getByRole('button', { name: 'Send message' }))).toBe(true)
  })

  it('is changed from the control, with each Mode’s meaning, and the loop is created with it', async () => {
    const user = userEvent.setup()
    const pill = await composer()
    await user.click(pill)
    const menu = await screen.findByRole('menu', { name: 'Execution boundary' })
    const attended = within(menu).getByRole('menuitemradio', { name: 'Attended' })
    expect(attended.getAttribute('title')).toMatch(/^Attended: you are watching\./)
    expect(within(menu).getByRole('menuitemradio', { name: 'Unattended' }).getAttribute('aria-checked')).toBe('true')
    await user.click(attended)
    await waitFor(() => expect(screen.getByRole('button', { name: /^Execution boundary:/ }).getAttribute('aria-label')).toBe('Execution boundary: Attended'))
    await user.click(screen.getByRole('button', { name: 'Send message' }))
    await waitFor(() => expect(created).toHaveLength(1))
    expect(created[0]).toMatchObject({ attended: true })
  })

  it('opens on the checked Mode and is chosen from the keyboard', async () => {
    const pill = await composer({ initialAttended: true })
    fireEvent.click(pill)
    const menu = await screen.findByRole('menu', { name: 'Execution boundary' })
    await waitFor(() => expect(document.activeElement?.getAttribute('aria-label')).toBe('Attended'))
    fireEvent.keyDown(menu, { key: 'ArrowUp' })
    expect(document.activeElement?.getAttribute('aria-label')).toBe('Unattended')
    expect(screen.getByRole('button', { name: /^Execution boundary:/ }).getAttribute('aria-label'), 'moving chooses nothing').toBe('Execution boundary: Attended')
    fireEvent.keyDown(document.activeElement!, { key: 'Enter' })
    fireEvent.click(document.activeElement!)
    await waitFor(() => expect(screen.getByRole('button', { name: /^Execution boundary:/ }).getAttribute('aria-label')).toBe('Execution boundary: Unattended'))
  })
})

describe('where the loop may act', () => {
  it('says the gateway’s sandbox and network for the loop’s Mode, read again when it changes', async () => {
    const user = userEvent.setup()
    const pill = await composer()
    await waitFor(() => expect(boundaryReads.at(-1)).toMatchObject({ loop: 'unattended', runtime: '', kind: 'goal' }))
    await user.click(pill)
    const menu = await screen.findByRole('menu', { name: 'Execution boundary' })
    const facts = document.getElementById(menu.getAttribute('aria-describedby')!)!
    expect(facts.textContent).toContain('The commands it runs are sandboxed on this computer.')
    expect(facts.textContent).toContain('Its web tools reach only package registries.')
    await user.click(within(menu).getByRole('menuitemradio', { name: 'Attended' }))
    await waitFor(() => expect(boundaryReads.at(-1)).toMatchObject({ loop: 'attended' }))
    expect(screen.getByRole('button', { name: /^Execution boundary:/ }).getAttribute('title')).toContain('Network: Network on')
  })

  it('names the folder the loop will work in, or where the gateway puts it', async () => {
    const { loopFolderSaid } = await import('./LoopBoundary')
    expect(loopFolderSaid({ folder: '/home/user/src/app', kind: 'code', projectId: '' })).toBe('/home/user/src/app')
    // With no folder bound: a Code loop works in a folder of its own, any other in the workspace
    // a new chat starts in; under a project, in the project's folder.
    expect(loopFolderSaid({ folder: '', kind: 'code', projectId: '' })).toBe('A folder of its own, made when it starts')
    expect(loopFolderSaid({ folder: '', kind: 'goal', projectId: '' })).toBe('The workspace, where a new chat starts')
    expect(loopFolderSaid({ folder: '', kind: 'goal', projectId: 'p1' })).toBe('Its project’s folder')
    expect(loopFolderSaid({ folder: '', kind: 'code', projectId: 'p1' })).toMatch(/^Its project’s folder, or a folder of its own/)
    // A General loop is a workflow run, which works in a scratch folder of its own, project or not.
    expect(loopFolderSaid({ folder: '', kind: 'general', projectId: 'p1' })).toBe('A folder of its own, made when it starts')
    expect(loopFolderSaid({ folder: '', kind: 'general', projectId: '' })).toBe('A folder of its own, made when it starts')
    const pill = await composer({ initialKind: 'code', initialCodebase: '/home/user/src/app' })
    expect(pill.getAttribute('title')).toContain('Working folder: /home/user/src/app')
  })
})

describe('while the loop is being set up', () => {
  it('the control is unavailable, says why, and opens nothing', async () => {
    const user = userEvent.setup()
    classify.hold = true
    const pill = await composer()
    await user.click(screen.getByRole('button', { name: 'Send message' }))
    await waitFor(() => expect(pill.getAttribute('aria-disabled')).toBe('true'))
    expect(pill.getAttribute('title')).toContain('The loop is being set up')
    expect(pill.className, 'it looks unavailable too').toMatch(/opacity-40/)
    expect(pill.className).not.toMatch(/hover:bg-surface-high/)
    await user.click(pill)
    expect(screen.queryByRole('menu', { name: 'Execution boundary' })).toBeNull()
    classify.held.forEach((answer) => answer())
  })
})
