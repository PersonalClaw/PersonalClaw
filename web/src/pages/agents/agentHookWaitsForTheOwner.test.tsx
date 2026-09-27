import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { SavedAgent, WaitingAgentHook } from '../../lib/api'

// ── An agent hook the owner has not allowed is listed as waiting, and Allow asks ──────────────────
//
// The agent CLI's own hooks — `agent.agent_hooks` in config.json and the scripts in `<home>/hooks`
// — run on the agent's events with nobody asked, so the gateway merges one only once the owner
// allowed it, sealed to its file (`agent_hook_grants.py`), and reports the rest as `waiting`.
// Measured before this: the Agents page listed the hooks in effect, read-only, and nothing could
// be allowed — a script that appeared in the hooks folder simply ran.

const { agentMetadata, routingStatus, mcpActive, agentHooks, allowAgentHook, empty } = vi.hoisted(() => ({
  agentMetadata: vi.fn(),
  routingStatus: vi.fn(),
  mcpActive: vi.fn(),
  agentHooks: vi.fn(),
  allowAgentHook: vi.fn(),
  empty: () => Promise.resolve([]),
}))

vi.mock('../../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      agentMetadata, routingStatus, mcpActive, agentHooks, allowAgentHook,
      chatModels: empty, skills: empty, tools: empty, hooks: empty,
    },
  }
})

import { NativeAgentDetail } from './AgentDetail'

const WAITING: WaitingAgentHook = {
  event: 'preToolUse', command: '/home/me/.personalclaw/hooks/audit-pre.sh', matcher: '', seal: 'f00d',
}

function mount() {
  render(
    <NativeAgentDetail
      agent={{ name: 'PersonalClaw', provider: 'native', description: '', system_prompt: '', skills: [], tools: [], triggers: [], revision: 'r1' } as SavedAgent}
      isDefault
      onSaved={vi.fn()}
      onDeleted={vi.fn()}
      onSetDefault={vi.fn()}
      editing={false}
      onEditingChange={vi.fn()}
    />,
  )
}

beforeEach(() => {
  agentMetadata.mockReset().mockResolvedValue({ value: '', revision: 'r0' })
  routingStatus.mockReset().mockResolvedValue({ enabled: true, muted: [], dismissals: {} })
  mcpActive.mockReset().mockResolvedValue([])
  agentHooks.mockReset().mockResolvedValue({ hooks: {}, waiting: [WAITING] })
  allowAgentHook.mockReset().mockResolvedValue({ ok: true })
})

describe('a waiting agent hook', () => {
  it('is listed as not allowed to run, with the file it runs and when', async () => {
    mount()
    expect(await screen.findByText(/Not allowed to run yet/)).toBeInTheDocument()
    expect(screen.getByText(/audit-pre\.sh/)).toBeInTheDocument()
    expect(screen.getByText('preToolUse')).toBeInTheDocument()
  })

  it('Allow sends the hook as the page read it, seal included', async () => {
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Allow' }))
    await waitFor(() => expect(allowAgentHook).toHaveBeenCalledWith(WAITING))
    await waitFor(() => expect(agentHooks).toHaveBeenCalledTimes(2))
  })

  it('a refusal — the file changed since, or the owner declined — is shown, not swallowed', async () => {
    allowAgentHook.mockRejectedValue(new Error('“/home/me/.personalclaw/hooks/audit-pre.sh” changed since this page read it.'))
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Allow' }))
    expect(await screen.findByText(/changed since this page read it/)).toBeInTheDocument()
  })

  it('a hook list that could not be read says so, rather than "No lifecycle hooks configured"', async () => {
    agentHooks.mockRejectedValue(new Error('gateway down'))
    mount()
    expect(await screen.findByText("Could not read the agent's hooks.")).toBeInTheDocument()
    expect(screen.queryByText('No lifecycle hooks configured.')).not.toBeInTheDocument()
  })
})
