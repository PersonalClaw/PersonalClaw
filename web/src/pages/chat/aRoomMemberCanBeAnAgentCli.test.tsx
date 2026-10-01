import { it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { RoomDetail, SavedAgent } from '../../lib/api'

// ── Rooms › Members › Add a member offers the agents a ready agent CLI lists ─────────────────
//
// 🔴 Before: the picker listed the configured agent bindings only, so an agent CLI that was
// installed, Ready and on the Agents page could never be a member, although the room promises each
// member "its own provider session". Now it offers the chat picker's catalog: a ready CLI's agents
// are listed, picking one saves the binding the chat's agent defaults use for it (its runtime and
// the agent it offered), and the member is added as that binding. A CLI that is not ready is
// listed, off, with why.

const created: Record<string, unknown>[] = []

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
          { id: 'acp:example-cli/careful-coder', name: 'Careful coder', runtime: id, description: 'Writes code in small steps', provider_agent: 'careful-coder', reasoning_effort: '', models: [] },
        ], permission_modes: [], tested_at: '2026-09-30T10:00:00Z' })
      : Promise.reject(new Error(`${id} was asked for its agents while it is not ready`)),
    agents: () => Promise.resolve({ agents: [], default_agent: 'PersonalClaw' }),
    createAgent: (body: Record<string, unknown>) => { created.push(body); return Promise.resolve({ ok: true }) },
  })
  return { ...real, api }
})

const AGENTS: SavedAgent[] = [{ name: 'analyst', provider: 'native', model: '', revision: 'r1' } as SavedAgent]

function detail(): RoomDetail {
  return {
    room: {
      id: 'release-check', title: 'Is the release ready?', created_at: '2026-09-30T00:00:00', archived: false,
      paused: false, rounds_used: 0, round_budget: 0, pending_queue: [], speaking: '', owed: [], round_running: false,
      members: [], effective_round_budget: 6, max_round_budget: 100, max_members: 8,
      transcript_path: '/rooms/release-check/transcript.jsonl',
    },
    member_posture: [], member_bindings: [], messages: [],
  } as RoomDetail
}

beforeEach(() => { created.length = 0 })

it('a ready CLI’s agent joins as the binding saved for it; a CLI that is not ready says why', async () => {
  const { RoomMembersPanel } = await import('./RoomMembersPanel')
  const onAdd = vi.fn()
  const user = userEvent.setup()
  render(<RoomMembersPanel detail={detail()} agents={AGENTS} busy={false} removing="" onAdd={onAdd} onRemove={() => {}} />)

  await user.click(screen.getByRole('button', { name: /Add a member/ }))
  const picker = screen.getByRole('combobox', { name: 'Agent' })
  const cli = await within(picker).findByRole('option', { name: 'Example Cli · Careful coder' })
  const notReady = within(picker).getByRole('option', { name: 'Other Cli — can’t join now: Sign in to Other Cli first' }) as HTMLOptionElement
  expect(notReady.disabled, 'a CLI that is not ready cannot be picked').toBe(true)

  await user.selectOptions(picker, (cli as HTMLOptionElement).value)
  await user.click(screen.getByRole('button', { name: /Add to the room/ }))

  await waitFor(() => expect(onAdd).toHaveBeenCalledTimes(1))
  expect(created).toEqual([expect.objectContaining({
    name: 'acp-example-cli-careful-coder', provider: 'acp:example-cli', provider_agent: 'careful-coder',
  })])
  expect(onAdd.mock.calls[0][0]).toMatchObject({ name: 'acp-example-cli-careful-coder' })
})

it('a native agent still joins by its own name, saving nothing', async () => {
  const { RoomMembersPanel } = await import('./RoomMembersPanel')
  const onAdd = vi.fn()
  const user = userEvent.setup()
  render(<RoomMembersPanel detail={detail()} agents={AGENTS} busy={false} removing="" onAdd={onAdd} onRemove={() => {}} />)

  await user.click(screen.getByRole('button', { name: /Add a member/ }))
  await user.click(screen.getByRole('button', { name: /Add to the room/ }))
  await waitFor(() => expect(onAdd).toHaveBeenCalledTimes(1))
  expect(onAdd.mock.calls[0][0]).toMatchObject({ name: 'analyst' })
  expect(created).toEqual([])
})
