import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { SavedAgent } from '../../lib/api'

// The built-in PersonalClaw agent ships with NO prompt of its own, so a turn is served the
// prompt bound in Settings → Prompts. Its detail page used to hide the System prompt section
// whenever the agent's own prompt was empty, which read as "this agent runs with no
// instructions". The section now says where the prompt comes from, and only for the default
// agent: a custom agent with no prompt really has none.

const { agentMetadata, routingStatus, mcpActive, agentHooks, empty } = vi.hoisted(() => ({
  agentMetadata: vi.fn(),
  routingStatus: vi.fn(),
  mcpActive: vi.fn(),
  agentHooks: vi.fn(),
  empty: () => Promise.resolve([]),
}))

vi.mock('../../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      agentMetadata, routingStatus, mcpActive, agentHooks,
      // AgentForm's pickers
      chatModels: empty, skills: empty, tools: empty, hooks: empty,
    },
  }
})

import { NativeAgentDetail } from './AgentDetail'
import { AgentForm, toDraft } from './AgentForm'
import { isBuiltinDefaultAgent } from './agentMeta'

const agentNamed = (name: string, system_prompt = ''): SavedAgent =>
  ({ name, provider: 'native', description: '', system_prompt, skills: [], tools: [], triggers: [] }) as SavedAgent

function mount(agent: SavedAgent, isDefault: boolean) {
  render(
    <NativeAgentDetail
      agent={agent}
      isDefault={isDefault}
      onSaved={vi.fn()}
      onDeleted={vi.fn()}
      onSetDefault={vi.fn()}
      editing={false}
      onEditingChange={vi.fn()}
    />,
  )
}

beforeEach(() => {
  agentMetadata.mockReset().mockResolvedValue('')
  routingStatus.mockReset().mockResolvedValue({ enabled: true, muted: [], dismissals: {} })
  mcpActive.mockReset().mockResolvedValue([])
  agentHooks.mockReset().mockResolvedValue({})
})

describe('the default agent says where its prompt comes from', () => {
  it('with no prompt of its own, it points at the binding in Settings → Prompts', () => {
    mount(agentNamed('PersonalClaw'), true)
    expect(screen.getByText(/None of its own/)).toBeTruthy()
    const link = screen.getByRole('link', { name: 'Settings → Prompts' })
    expect(link.getAttribute('href')).toBe('#/settings/prompts')
  })

  it('a prompt the user wrote into it is shown instead', () => {
    mount(agentNamed('PersonalClaw', 'OWN-PROMPT-TEXT'), true)
    expect(screen.getByRole('group', { name: 'System prompt' }).textContent).toContain('OWN-PROMPT-TEXT')
    expect(screen.queryByText(/None of its own/)).toBeNull()
  })

  it('a custom agent with no prompt gets no such claim (vacuity floor)', () => {
    mount(agentNamed('scout'), false)
    expect(screen.queryByText(/None of its own/)).toBeNull()
    expect(screen.queryByText('System prompt')).toBeNull()
  })

  it('its edit form says an empty prompt means the bound one, and a custom agent keeps its hint', () => {
    const { unmount } = render(<AgentForm draft={toDraft(agentNamed('PersonalClaw'))} onChange={vi.fn()} nameLocked />)
    expect(screen.getByText(/Leave empty to answer with the prompt bound in Settings → Prompts/)).toBeTruthy()
    unmount()
    render(<AgentForm draft={toDraft(agentNamed('scout'))} onChange={vi.fn()} nameLocked />)
    expect(screen.queryByText(/Leave empty to answer with the prompt bound/)).toBeNull()
    expect(screen.getByText(/The agent's standing instructions/)).toBeTruthy()
  })

  it('matches the default agent the way the server does: case-insensitive, trimmed', () => {
    expect(isBuiltinDefaultAgent({ name: 'PersonalClaw' })).toBe(true)
    expect(isBuiltinDefaultAgent({ name: ' personalclaw ' })).toBe(true)
    expect(isBuiltinDefaultAgent({ name: 'personalclaw-2' })).toBe(false)
  })
})
