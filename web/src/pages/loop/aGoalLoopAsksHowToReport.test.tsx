import { it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { LoopKind } from '../../lib/api'

// ── A goal loop asks once how it should report (#3824) ────────────────────────────────────────────
//
// The loop composer offers "Reports" beside the loop's Mode: as it goes (a note each cycle), only when
// it's done (the default, so a new loop never sends a note per cycle unasked) or only if it gets
// stuck. The choice is sent with the loop it creates. A run-backed kind (a general loop) reports as a
// workflow run does, through the run's notifications, so the composer does not ask it.

const created: Record<string, unknown>[] = []
const validated: Record<string, unknown>[] = []

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const api = new Proxy({} as Record<string, unknown>, {
    get(t, p: string) {
      if (p in t) return t[p]
      return (t[p] = () => Promise.resolve([]))
    },
  })
  Object.assign(api, {
    agentProviders: () => Promise.resolve([
      { name: 'native', provider_id: 'native', type: 'native', ready: true, state: 'ready', detail: '', tested_at: null },
      { name: 'acp:example-cli', provider_id: 'acp:example-cli', type: 'acp_agent', ready: true, state: 'ready', detail: '', tested_at: '2026-09-30T10:00:00Z' },
      { name: 'acp:other-cli', provider_id: 'acp:other-cli', type: 'acp_agent', ready: false, state: 'needs_login', detail: 'Sign in to Other Cli first.', tested_at: '2026-09-30T10:00:00Z' },
    ]),
    agentProviderAgents: (id: string) => id === 'acp:example-cli'
      ? Promise.resolve({ agents: [
          { id: 'acp:example-cli/everyday', name: 'Everyday', runtime: id, description: 'Answers questions', provider_agent: 'everyday', reasoning_effort: '', models: [] },
          { id: 'acp:example-cli/careful-coder', name: 'Careful coder', runtime: id, description: 'Writes code in small steps', provider_agent: 'careful-coder', reasoning_effort: '', models: [] },
        ], permission_modes: [], tested_at: '2026-09-30T10:00:00Z' })
      : Promise.reject(new Error(`${id} was asked for its agents while it is not ready`)),
    classifyULoop: () => Promise.resolve({ title: 'Health endpoint', summary: '', kind_config: {}, plan: [], intake_rigor: 'minimal' }),
    validateULoop: (body: Record<string, unknown>) => { validated.push(body); return Promise.resolve({ can_start: true, errors: [], warnings: [] }) },
    createULoop: (body: Record<string, unknown>) => { created.push(body); return Promise.resolve({ id: 'ab12cd34', kind: 'code', files_dir: '' }) },
  })
  return { ...real, api }
})

beforeEach(() => {
  created.length = 0
  validated.length = 0
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

async function composer(kind: LoopKind) {
  const { LoopComposer } = await import('./LoopComposer')
  const { AppearanceProvider } = await import('../../app/appearance')
  render(
    <AppearanceProvider>
      <LoopComposer onCreated={vi.fn()} onHistory={() => {}} initialKind={kind}
        initialTask="Find a seed supplier that ships north before the frost." />
    </AppearanceProvider>,
  )
}

it('a goal loop starts on "only when it\'s done", and the choice made is sent with it', async () => {
  const user = userEvent.setup()
  await composer('goal')
  await user.click(await screen.findByRole('button', { name: 'Reports: Only when it’s done' }))
  await user.click(await screen.findByRole('menuitemradio', { name: /As it goes/ }))
  expect(screen.getByRole('button', { name: 'Reports: As it goes' })).toBeTruthy()
  await user.click(screen.getByRole('button', { name: 'Send message' }))
  await waitFor(() => expect(created).toHaveLength(1))
  expect(created[0]).toMatchObject({ kind: 'goal', report: 'as_it_goes' })
})

it('the keyboard reaches the choices: the menu opens on the loop\'s choice, and the arrows move from it', async () => {
  const user = userEvent.setup()
  await composer('goal')
  await user.click(await screen.findByRole('button', { name: 'Reports: Only when it’s done' }))
  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('menuitemradio', { name: 'Only when it’s done' })))
  await user.keyboard('{ArrowDown}')
  expect(document.activeElement).toBe(screen.getByRole('menuitemradio', { name: 'Only if it gets stuck' }))
  await user.keyboard('{Enter}')
  expect(screen.getByRole('button', { name: 'Reports: Only if it gets stuck' })).toBeTruthy()
})

it('left alone, a goal loop is sent reporting only when it\'s done', async () => {
  const user = userEvent.setup()
  await composer('goal')
  await screen.findByRole('button', { name: 'Reports: Only when it’s done' })
  await user.click(screen.getByRole('button', { name: 'Send message' }))
  await waitFor(() => expect(created).toHaveLength(1))
  expect(created[0]).toMatchObject({ report: 'when_done' })
})

it('a code loop is asked too: the choice is the loop watchdog\'s, whatever the kind', async () => {
  await composer('code')
  expect(await screen.findByRole('button', { name: 'Reports: Only when it’s done' })).toBeTruthy()
})

it('a general loop, which runs as a workflow run, is not asked, and is sent with no choice', async () => {
  const user = userEvent.setup()
  await composer('general')
  await screen.findByRole('button', { name: /^Runs on/ })
  expect(screen.queryByRole('button', { name: /^Reports:/ })).toBeNull()
  await user.click(screen.getByRole('button', { name: 'Send message' }))
  await waitFor(() => expect(created).toHaveLength(1))
  expect(created[0]).not.toHaveProperty('report')
})
