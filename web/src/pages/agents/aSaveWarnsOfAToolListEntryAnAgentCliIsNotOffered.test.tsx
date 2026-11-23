/**
 * A save of an agent on an agent CLI shows what the gateway warned of: each tool-list entry no
 * PersonalClaw tool the CLI is offered matches, which lets the CLI call nothing (the save stood).
 *
 * The gateway answers such a save with `warnings`; the page showed nothing of them, so an owner who
 * listed `read_file` to keep a CLI agent to reading files was not told the entry holds nothing
 * there. Driven through the real `api.updateAgent` over a stubbed `fetch` standing in for the
 * gateway. 🔴 Red before: the row that expects the warning on the page.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import type { SavedAgent } from '../../lib/api'

const reads = vi.hoisted(() => ({
  agents: vi.fn(),
  routingStatus: vi.fn(async () => ({ enabled: true, muted: [], dismissals: {} })),
  mcpActive: vi.fn(async () => []),
  agentHooks: vi.fn(async () => ({ hooks: {}, waiting: [] })),
  chatModels: vi.fn(async () => []),
  skills: vi.fn(async () => []),
  hooks: vi.fn(async () => []),
  tools: vi.fn(async () => [{ name: 'memory_recall', provider: 'core', risk_level: 'safe' }]),
}))
vi.mock('../../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../lib/api')>()
  // `updateAgent` stays the real one, so the save goes over the wire to the gateway below.
  return { ...mod, api: { ...mod.api, ...reads } }
})

import { NativeAgentDetail } from './AgentDetail'

const WARNED = '“read_file” is not one of the PersonalClaw tools an agent CLI is offered, so on this agent it lets the CLI call nothing. The CLI\'s own built-in tools follow its permission mode.'

const reply = (o: unknown) => new Response(JSON.stringify(o), { status: 200, headers: { 'Content-Type': 'application/json' } })

const agent: SavedAgent = {
  name: 'reviewer', provider: 'acp:claude-code', description: 'reviews diffs', system_prompt: '', model: '',
  approval_mode: '', skills: [], tools: ['memory_recall', 'read_file'], triggers: [], revision: 'r1',
}

/** The agent's page, opened in its editor, as the URL keeps the editor open or closed. */
function Page() {
  const [editing, setEditing] = useState(true)
  return (
    <NativeAgentDetail agent={agent} isDefault={false} onSaved={vi.fn()} onDeleted={vi.fn()}
      onSetDefault={vi.fn()} editing={editing} onEditingChange={setEditing} providerLabel="Claude Code" />
  )
}

async function saveAnswered(answer: Record<string, unknown>) {
  reads.agents.mockResolvedValue({ agents: [agent], default_agent: 'PersonalClaw' })
  vi.stubGlobal('fetch', vi.fn(async () => reply(answer)))
  render(<Page />)
  await act(async () => { fireEvent.click(await screen.findByRole('button', { name: /Save/ })) })
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('a save of an agent CLI says which tool-list entries hold nothing there', () => {
  it('shows each warning the gateway answered with, once the editor closes', async () => {
    await saveAnswered({ ok: true, name: 'reviewer', revision: 'r2', warnings: [WARNED] })
    await waitFor(() => expect(screen.getAllByRole('status').some((n) => n.textContent?.includes(WARNED))).toBe(true))
    expect(screen.queryByRole('button', { name: /Save/ })).toBeNull()
  })

  it('shows nothing when the gateway warned of nothing', async () => {
    await saveAnswered({ ok: true, name: 'reviewer', revision: 'r2' })
    await waitFor(() => expect(screen.queryByRole('button', { name: /Save/ })).toBeNull())
    expect(screen.queryByText(/is not one of the PersonalClaw tools/)).toBeNull()
  })
})
