import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import type { SavedAgent } from '../../lib/api'

// ── An agent pinned to a model that cannot run is shown as such, with the fix ──────────────────
//
// Measured on main: the Agents page showed the pin like any other model, and the editor's picker
// — which only offers the active chat models — fell back to its placeholder, so it read "Auto"
// for an agent pinned to a model that does not exist. Meanwhile every turn ran on the chat model.
// The pin is kept; these pin that the page says it cannot run, why, and how to fix it.

const { agentMetadata, routingStatus, mcpActive, agentHooks, chatModels, empty } = vi.hoisted(() => ({
  agentMetadata: vi.fn(),
  routingStatus: vi.fn(),
  mcpActive: vi.fn(),
  agentHooks: vi.fn(),
  chatModels: vi.fn(),
  empty: () => Promise.resolve([]),
}))

vi.mock('../../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      agentMetadata, routingStatus, mcpActive, agentHooks, chatModels,
      skills: empty, tools: empty, hooks: empty,
    },
  }
})

import { NativeAgentDetail } from './AgentDetail'
import { AgentForm, toDraft } from './AgentForm'

const PIN = 'fake-oai:no-such-model'
const WHY = 'it is not one of the chat models set up in Settings → Models'
const FIX = 'add it in Settings → Models'

const researcher = (unavailable = true): SavedAgent =>
  ({
    name: 'researcher', provider: 'native', model: PIN, description: '', system_prompt: '',
    skills: [], tools: [], triggers: [],
    model_unavailable: unavailable ? { why: WHY, fix: FIX } : null,
    revision: 'r1',
  }) as SavedAgent

function mount(agent: SavedAgent) {
  render(
    <NativeAgentDetail agent={agent} isDefault={false} onSaved={vi.fn()} onDeleted={vi.fn()}
      onSetDefault={vi.fn()} editing={false} onEditingChange={vi.fn()} />,
  )
}

beforeEach(() => {
  agentMetadata.mockReset().mockResolvedValue('')
  routingStatus.mockReset().mockResolvedValue({ enabled: true, muted: [], dismissals: {} })
  mcpActive.mockReset().mockResolvedValue([])
  agentHooks.mockReset().mockResolvedValue({ hooks: {}, waiting: [] })
  chatModels.mockReset().mockResolvedValue([
    { name: 'fake-oai:fake-model-1', model_id: 'fake-model-1', provider: 'fake-oai' },
  ])
})

describe('the agent panel', () => {
  it('marks the pin unavailable and says why, what happens meanwhile, and the fix', () => {
    mount(researcher())
    expect(screen.getByText('· unavailable')).toBeInTheDocument()
    const note = screen.getByTestId('agent-model-unavailable')
    expect(note).toHaveTextContent(`${PIN} is unavailable: ${WHY}.`)
    expect(note).toHaveTextContent('this agent answers on your chat model, and each reply says which model answered')
    expect(note).toHaveTextContent(`Choose another model with Edit, or ${FIX}.`)
  })

  it('says nothing for a pin that can run (vacuity floor)', () => {
    mount(researcher(false))
    expect(screen.queryByText('· unavailable')).not.toBeInTheDocument()
    expect(screen.queryByTestId('agent-model-unavailable')).not.toBeInTheDocument()
  })
})

describe('the agent editor', () => {
  it('names the unavailable pin instead of showing Auto', async () => {
    const agent = researcher()
    render(<AgentForm draft={toDraft(agent)} onChange={vi.fn()} nameLocked
      unavailable={{ model: PIN, reason: agent.model_unavailable! }} />)
    await waitFor(() => expect(screen.getByText(`${PIN} (unavailable)`)).toBeInTheDocument())
    expect(screen.queryByText('Auto — provider default')).not.toBeInTheDocument()
    expect(screen.getByTestId('agent-model-unavailable')).toHaveTextContent(`Choose another model above, or ${FIX}.`)
  })
})
