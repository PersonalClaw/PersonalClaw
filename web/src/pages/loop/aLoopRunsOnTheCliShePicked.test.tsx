import { it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── The loop composer sends the agent CLI she picked under "Runs on" ──────────────────────────
//
// 🔴 Before: the composer had no agent, runtime or model control, and its create body carried no
// `provider` / `provider_agent`, so a Code loop always ran on PersonalClaw even with two agent CLIs
// set up and ready. Now its toolbar carries "Runs on": PersonalClaw, or one of the agents a ready
// CLI offers. A CLI that is not ready is listed with why and offers nothing to pick.
//
// Driven through the real LoopComposer and the real picker, against the agent providers the
// gateway lists; what is asserted is the body `POST /api/loops` was sent.

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

async function composer(onCreated = vi.fn()) {
  const { LoopComposer } = await import('./LoopComposer')
  const { AppearanceProvider } = await import('../../app/appearance')
  render(
    <AppearanceProvider>
      <LoopComposer onCreated={onCreated} onHistory={() => {}} initialKind="code"
        initialTask="Add a health endpoint that reports the build version." />
    </AppearanceProvider>,
  )
  return onCreated
}

it('a Code loop is created on the agent she picked, and a CLI that is not ready says why', async () => {
  const user = userEvent.setup()
  const onCreated = await composer()

  await user.click(await screen.findByRole('button', { name: 'Runs on: PersonalClaw' }))
  // The ready CLI's agents are offered; the one that needs sign-in is listed, with why, and
  // offers nothing to pick.
  await screen.findByText('Careful coder')
  expect(screen.getByText('Can’t run a loop now: Sign in to Other Cli first.')).toBeTruthy()
  expect(screen.queryByRole('button', { name: /Other Cli/ })).toBeNull()
  await user.click(screen.getByRole('button', { name: /Careful coder/ }))

  expect(screen.getByRole('button', { name: 'Runs on: Example Cli · Careful coder' })).toBeTruthy()
  await user.click(screen.getByRole('button', { name: 'Send message' }))

  await waitFor(() => expect(created).toHaveLength(1))
  expect(created[0]).toMatchObject({ kind: 'code', provider: 'acp:example-cli', provider_agent: 'careful-coder' })
  expect(validated[0], 'the pre-flight judges the same runtime').toMatchObject({ provider: 'acp:example-cli', provider_agent: 'careful-coder' })
  expect(onCreated).toHaveBeenCalledTimes(1)
})

it('a loop she puts back on PersonalClaw is sent with no agent CLI', async () => {
  const user = userEvent.setup()
  await composer()

  await user.click(await screen.findByRole('button', { name: 'Runs on: PersonalClaw' }))
  await user.click(await screen.findByRole('button', { name: /Everyday/ }))
  await user.click(screen.getByRole('button', { name: 'Runs on: Example Cli · Everyday' }))
  await screen.findByText('Careful coder')
  await user.click(screen.getByRole('button', { name: /^PersonalClaw/ }))
  await user.click(screen.getByRole('button', { name: 'Send message' }))

  await waitFor(() => expect(created).toHaveLength(1))
  expect(created[0]).toMatchObject({ provider: '', provider_agent: '' })
})
