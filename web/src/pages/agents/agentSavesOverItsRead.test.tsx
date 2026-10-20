import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { SavedAgent } from '../../lib/api'

// ── An agent, and its routing note, are saved only over the copy the page read ────────────────────
//
// The agent editor sends every field it shows, the untouched ones as it read them; the routing-note
// editor sends the whole note. When the gateway changed the same record in between — the chat's
// "Always allow for this agent" writes `approval_mode: "auto"` onto the profile, the orchestrator
// seeds a missing note — the save replaced that change, and nothing on the page said so. The
// gateway now refuses a save whose base is stale (`409 stale_write`); these pin what the page does.

const { api } = vi.hoisted(() => ({
  api: {
    agents: vi.fn(),
    updateAgent: vi.fn(),
    agentMetadata: vi.fn(),
    saveAgentMetadata: vi.fn(),
    routingStatus: vi.fn(),
    mcpActive: vi.fn(),
    agentHooks: vi.fn(),
    // AgentForm's pickers
    chatModels: () => Promise.resolve([]),
    skills: () => Promise.resolve([]),
    tools: () => Promise.resolve([]),
    hooks: () => Promise.resolve([]),
  },
}))

vi.mock('../../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../lib/api')>()
  return { ...mod, api: { ...mod.api, ...api } }
})

import { NativeAgentDetail } from './AgentDetail'

function staleWrite() {
  return Object.assign(new Error('This write replaces the agent, which changed after the copy it was built from was read.'), { status: 409, code: 'stale_write' })
}

const PAINTED: SavedAgent = {
  name: 'researcher', provider: '', description: 'reads papers', system_prompt: '', model: '',
  approval_mode: '', skills: ['search'], tools: [], triggers: [], revision: 'r1',
}
// What is stored by the time the editor saves: the owner answered a tool prompt in a chat with this
// agent "Always allow for this agent".
const STORED: SavedAgent = { ...PAINTED, approval_mode: 'auto', revision: 'r2' }

function mount(agent: SavedAgent, editing: boolean) {
  const onSaved = vi.fn()
  const onEditingChange = vi.fn()
  render(
    <NativeAgentDetail agent={agent} isDefault={false} onSaved={onSaved} onDeleted={vi.fn()}
      onSetDefault={vi.fn()} editing={editing} onEditingChange={onEditingChange} />,
  )
  return { onSaved, onEditingChange }
}

beforeEach(() => {
  sessionStorage.clear()
  for (const f of Object.values(api)) if (vi.isMockFunction(f)) f.mockReset()
  api.agents.mockResolvedValue({ agents: [STORED], default_agent: 'researcher' })
  api.updateAgent.mockImplementation((_name: string, _body: unknown, base: string) =>
    base === 'r2' ? Promise.resolve({ ok: true, revision: 'r3' }) : Promise.reject(staleWrite()))
  api.routingStatus.mockResolvedValue({ enabled: true, muted: [], dismissals: {} })
  api.mcpActive.mockResolvedValue([])
  api.agentHooks.mockResolvedValue({ hooks: {}, waiting: [] })
})

describe('the agent editor', () => {
  it('a save from a stale copy is refused with the notice, and the edit is kept', async () => {
    const { onSaved, onEditingChange } = mount(PAINTED, true)
    const description = await screen.findByPlaceholderText('One line: what this agent is for')
    fireEvent.change(description, { target: { value: 'reads papers and datasets' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Save/ })) })

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/The agent “researcher” changed elsewhere/)
    // The draft survives: still in the editor, still what was typed.
    expect((screen.getByPlaceholderText('One line: what this agent is for') as HTMLInputElement).value)
      .toBe('reads papers and datasets')
    expect(onSaved).not.toHaveBeenCalled()
    expect(onEditingChange).not.toHaveBeenCalledWith(false)
    // It was sent over the revision the page painted.
    expect(api.updateAgent).toHaveBeenCalledTimes(1)
    const [name, body, base] = api.updateAgent.mock.calls[0]
    expect([name, base]).toEqual(['researcher', 'r1'])
    expect(body).toMatchObject({ description: 'reads papers and datasets', approval_mode: '' })
  })

  it('Reload and reapply keeps the standing grant and puts the edit on top', async () => {
    const { onSaved } = mount(PAINTED, true)
    fireEvent.change(await screen.findByPlaceholderText('One line: what this agent is for'),
      { target: { value: 'reads papers and datasets' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Save/ })) })
    const alert = await screen.findByRole('alert')
    const reapply = within(alert).getByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.hasAttribute('disabled')).toBe(false))
    await act(async () => { fireEvent.click(reapply) })

    await waitFor(() => expect(api.updateAgent).toHaveBeenCalledTimes(2))
    const [, body, base] = api.updateAgent.mock.calls[1]
    expect(base).toBe('r2')
    expect(body).toMatchObject({ description: 'reads papers and datasets', approval_mode: 'auto' })
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
  })
})

describe('the routing-note editor', () => {
  async function openNotes() {
    mount({ ...PAINTED, revision: 'r1' }, false)
    fireEvent.click(screen.getByRole('button', { name: /^Advanced$/ }))
    return await screen.findByRole('textbox', { name: 'Routing notes' })
  }

  it('a save over a note seeded since is refused with the notice, and the typing is kept', async () => {
    api.agentMetadata
      .mockResolvedValueOnce({ value: '', revision: 'n1' })
      .mockResolvedValue({ value: 'reads papers', revision: 'n2' })
    api.saveAgentMetadata.mockImplementation((_n: string, _c: string, base: string) =>
      base === 'n2' ? Promise.resolve({ ok: true, content: _c, revision: 'n3' }) : Promise.reject(staleWrite()))
    const notes = await openNotes()
    fireEvent.change(notes, { target: { value: 'use for literature reviews' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Save notes/ })) })

    const alert = await screen.findByText(/This routing note changed elsewhere/)
    expect(alert.closest('[role="alert"]')).toBeTruthy()
    expect((screen.getByRole('textbox', { name: 'Routing notes' }) as HTMLTextAreaElement).value)
      .toBe('use for literature reviews')
    expect(api.saveAgentMetadata).toHaveBeenCalledTimes(1)
    expect(api.saveAgentMetadata.mock.calls[0]).toEqual(['researcher', 'use for literature reviews', 'n1'])
    expect(screen.queryByText('Saved ✓')).toBeNull()
  })

  it('a save that lands names the painted revision, and the next one is based on its answer', async () => {
    api.agentMetadata.mockResolvedValue({ value: 'use for reviews', revision: 'n1' })
    api.saveAgentMetadata
      .mockResolvedValueOnce({ ok: true, content: 'use for deep reviews', revision: 'n2' })
      .mockResolvedValueOnce({ ok: true, content: 'use for deep code reviews', revision: 'n3' })
    const notes = await openNotes()
    fireEvent.change(notes, { target: { value: 'use for deep reviews' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Save notes/ })) })
    expect(await screen.findByText('Saved ✓')).toBeTruthy()
    fireEvent.change(screen.getByRole('textbox', { name: 'Routing notes' }), { target: { value: 'use for deep code reviews' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Save notes/ })) })
    await waitFor(() => expect(api.saveAgentMetadata).toHaveBeenCalledTimes(2))
    expect(api.saveAgentMetadata.mock.calls.map((c) => c[2])).toEqual(['n1', 'n2'])
  })
})
