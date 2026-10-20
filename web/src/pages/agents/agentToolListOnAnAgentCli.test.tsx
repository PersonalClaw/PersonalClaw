/**
 * An agent's tool list is held on PersonalClaw's own agent, so the editor says what it does there
 * and that it does not apply to an agent that runs on an agent CLI.
 *
 * PersonalClaw's own runtime shows an agent's model only the tools its list allows and refuses a
 * call to any other. An agent CLI runs its own tools where no list of PersonalClaw's can hold them,
 * so on such an agent the picker saying "Tools this agent may call" would promise a limit nothing
 * enforces. It names the CLI and says the list is not applied instead.
 *
 * An entry the catalog does not offer still holds the agent: a pattern (`mcp/files/*`), or a tool
 * that is not installed here now. The picker lists only the catalog's tools, so such an entry was
 * counted in "Tools · N" and shown nowhere. It is shown as a ticked row saying what it is, and a
 * click removes it.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'

vi.mock('../../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      tools: () => Promise.resolve([{ name: 'read_file', provider: 'core', risk_level: 'safe' }]),
      skills: () => Promise.resolve([]),
      hooks: () => Promise.resolve([]),
    },
  }
})
vi.mock('../../lib/agents', () => ({ useActiveChatModelOptions: () => ({ options: [] }) }))

import { AgentForm, emptyDraft } from './AgentForm'

afterEach(cleanup)

describe("an agent's tool list says where it holds", () => {
  it('on PersonalClaw\'s own agent: the tools it may call, and none selected is every tool', () => {
    render(<AgentForm draft={{ ...emptyDraft(), name: 'researcher' }} onChange={vi.fn()} />)
    expect(screen.getByText('Tools this agent may call. None selected = all available tools.')).toBeTruthy()
  })

  it('on an agent CLI: that it is not applied, naming the CLI', () => {
    render(<AgentForm draft={{ ...emptyDraft(), name: 'reviewer' }} onChange={vi.fn()} runsOn="Claude Code" />)
    expect(screen.getByText(
      "Not applied: Claude Code runs its own tools, and a tool list holds only on PersonalClaw's own agent.",
    )).toBeTruthy()
    expect(screen.queryByText('Tools this agent may call. None selected = all available tools.')).toBeNull()
  })
})

describe('an entry the catalog does not offer is shown, and can be removed', () => {
  it('shows a pattern and a tool not here as ticked rows saying what they are', async () => {
    const onChange = vi.fn()
    render(<AgentForm draft={{ ...emptyDraft(), name: 'reader', tools: ['read_file', 'mcp/files/*', 'old_tool'] }} onChange={onChange} />)
    await waitFor(() => expect(screen.getByText('No tool of this name is here now')).toBeTruthy())
    const row = (name: string) => screen.getByRole('button', { name: new RegExp(`^${name.replace(/[*/]/g, '\\$&')}`) })
    expect(row('read_file').getAttribute('aria-pressed')).toBe('true')
    expect(row('mcp/files/*').getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByText('Pattern: every tool whose name fits it')).toBeTruthy()
    expect(row('old_tool').getAttribute('aria-pressed')).toBe('true')

    fireEvent.click(row('old_tool'))
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ tools: ['read_file', 'mcp/files/*'] }))
  })
})
