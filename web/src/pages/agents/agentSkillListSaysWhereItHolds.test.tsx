/**
 * An agent's skill list says what it does: on PersonalClaw's own agent, the skills the agent may
 * use, none selected being every skill; on an agent that runs on an agent CLI, that it is not
 * applied.
 *
 * The list's words were "Skills surfaced to this agent." while nothing read it: a custom agent was
 * offered no skill whatever its list said, and the default agent every skill. The list now decides
 * which skills an agent's turns are offered and which its skill tools reach (`agents/skill_list.py`).
 * An agent CLI loads its own skills where no list of PersonalClaw's can hold them, so on such an
 * agent the list names the CLI and says it is not applied, as the Tools list does.
 *
 * A skill in an agent's own folder (`agent-local`) is that agent's whatever its list says, so it is
 * not offered as a choice, and an entry that names no skill here is shown, as a ticked row saying
 * so, where it used to be counted and shown nowhere.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

vi.mock('../../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      skills: () => Promise.resolve([
        { key: 'trip-research', name: 'trip-research', description: 'Plan a trip', always: false, source: 'local', type: 'installed', loaded_by_agents: [] },
        { key: 'planner/packing', name: 'packing', description: 'Pack for a trip', always: false, source: 'agent-local', type: 'agent-local', loaded_by_agents: ['planner'], agent: 'planner' },
      ]),
      tools: () => Promise.resolve([]),
      hooks: () => Promise.resolve([]),
    },
  }
})
vi.mock('../../lib/agents', () => ({ useActiveChatModelOptions: () => ({ options: [] }) }))

import { AgentForm, emptyDraft } from './AgentForm'

afterEach(cleanup)

const NATIVE = 'Skills this agent may use. None selected = all available skills.'

describe("an agent's skill list says where it holds", () => {
  it("on PersonalClaw's own agent: the skills it may use, and none selected is every skill", () => {
    render(<AgentForm draft={{ ...emptyDraft(), name: 'researcher' }} onChange={vi.fn()} />)
    expect(screen.getByText(NATIVE)).toBeTruthy()
    expect(screen.queryByText('Skills surfaced to this agent.')).toBeNull()
  })

  it('on an agent CLI: that it is not applied, naming the CLI', () => {
    render(<AgentForm draft={{ ...emptyDraft(), name: 'reviewer' }} onChange={vi.fn()} runsOn="Claude Code" />)
    expect(screen.getByText(
      "Not applied: Claude Code loads its own skills, and a skill list holds only on PersonalClaw's own agent.",
    )).toBeTruthy()
    expect(screen.queryByText(NATIVE)).toBeNull()
  })
})

describe('the choices are the skills a list decides', () => {
  it("offers the library's skills, not another agent's own", async () => {
    render(<AgentForm draft={{ ...emptyDraft(), name: 'researcher' }} onChange={vi.fn()} />)
    await screen.findByRole('button', { name: /^trip-research/ })
    expect(screen.queryByRole('button', { name: /^packing/ })).toBeNull()
  })

  it('shows an entry that names no skill here as a ticked row, and a click removes it', async () => {
    const onChange = vi.fn()
    render(<AgentForm draft={{ ...emptyDraft(), name: 'researcher', skills: ['trip-research', 'gone-skill'] }} onChange={onChange} />)
    await waitFor(() => expect(screen.getByText('No skill of this name is here now')).toBeTruthy())
    const row = screen.getByRole('button', { name: /^gone-skill/ })
    expect(row.getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(row)
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ skills: ['trip-research'] }))
  })
})
