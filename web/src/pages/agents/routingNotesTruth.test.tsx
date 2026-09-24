import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { SavedAgent } from '../../lib/api'

// #345 — agent metadata and automatic suggestions are two different routing inputs.
//
// The Advanced editor writes the free-form metadata consumed by the orchestrator's generated
// delegation roster. Automatic suggestions instead read AgentProfile.specialty + route_hints.
// The old panel sentence and three neighboring source comments collapsed those paths into one.
//
// Failability control: the rendered assertion below rejects the old "the auto-router reads"
// sentence, while the source scan rejects every old routing-note line that names the auto-router.

const CORRECTED_HINT =
  "Used by the orchestrator's generated delegation roster. Automatic suggestions use Specialty and Routing hints instead."

const { agentMetadata, routingStatus, mcpActive, agentHooks } = vi.hoisted(() => ({
  agentMetadata: vi.fn(),
  routingStatus: vi.fn(),
  mcpActive: vi.fn(),
  agentHooks: vi.fn(),
}))

vi.mock('../../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      agentMetadata,
      routingStatus,
      mcpActive,
      agentHooks,
    },
  }
})

import { NativeAgentDetail } from './AgentDetail'

const agent = {
  name: 'routing-truth-probe',
  provider: 'native',
  description: '',
  system_prompt: '',
  skills: [],
  tools: [],
  triggers: [],
} as SavedAgent

beforeEach(() => {
  agentMetadata.mockReset().mockResolvedValue('')
  routingStatus.mockReset().mockResolvedValue({ enabled: true, muted: [], dismissals: {} })
  mcpActive.mockReset().mockResolvedValue([])
  agentHooks.mockReset().mockResolvedValue({})
})

describe('routing notes name their real consumer (#345)', () => {
  it('the Advanced panel distinguishes the delegation roster from automatic suggestions', async () => {
    render(
      <NativeAgentDetail
        agent={agent}
        isDefault={false}
        onSaved={vi.fn()}
        onDeleted={vi.fn()}
        onSetDefault={vi.fn()}
        editing={false}
        onEditingChange={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /^Advanced$/ }))

    expect(await screen.findByText(CORRECTED_HINT, { exact: true })).toBeTruthy()
    expect(screen.queryByText(/routing note.*auto-router|auto-router reads/i)).toBeNull()
  })

  it('all four source assertions keep metadata out of the auto-router contract', () => {
    const sources = [
      readFileSync(join(__dirname, 'AgentDetail.tsx'), 'utf8'),
      readFileSync(join(__dirname, '../../lib/api.ts'), 'utf8'),
    ]
    const routingNoteAssertions = sources
      .flatMap((source) => source.split('\n'))
      .filter((line) => /routing notes|when to use this agent/i.test(line))

    expect(routingNoteAssertions.length, 'the scan must see the routing-note surfaces').toBeGreaterThanOrEqual(4)
    for (const line of routingNoteAssertions) {
      expect(line, `false metadata consumer claim: ${line.trim()}`).not.toMatch(/auto-router/i)
    }
  })
})
