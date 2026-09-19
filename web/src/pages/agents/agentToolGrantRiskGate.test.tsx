/**
 * #506 (third surface) — binding a DESTRUCTIVE tool to an agent is a standing grant, and it
 * costs the same as binding a read.
 *
 * `AgentForm.tsx:114` maps `risk: x.risk_level` onto every tool option and `RiskTag` paints
 * `caution`/`destructive` beside the name. That is the whole of it: the tick is a plain toggle,
 * so adding `bash` to an agent's allowlist is one click, exactly like `artifact_list`. And this
 * grant outlives any single call — every future run of that agent may invoke the tool — which
 * makes it the most durable of the three ceremonies in this cluster and, before this change,
 * the cheapest.
 *
 * The measurement that put this surface in #506's scope: across `web/src`, `risk_level` had four
 * non-type consumers and not one gated a control (this file's line 114 among them).
 *
 * Gated in ONE direction only. Adding a destructive capability asks; removing one does not —
 * a confirmation on the withdrawal of a permission is friction that protects nothing, and it
 * would discourage the safe edit.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'

const toolsMock = vi.fn()
vi.mock('../../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      tools: (...a: unknown[]) => toolsMock(...a),
      skills: () => Promise.resolve([]),
      hooks: () => Promise.resolve([]),
    },
  }
})

const confirmDialog = vi.fn()
vi.mock('../../ui/dialog', () => ({ confirm: (...a: unknown[]) => confirmDialog(...a) }))

vi.mock('../../lib/agents', () => ({ useActiveChatModelOptions: () => ({ options: [] }) }))

import { AgentForm, emptyDraft, type AgentDraft } from './AgentForm'

const TOOLS = [
  { name: 'artifact_list', provider: 'core', risk_level: 'safe' },
  { name: 'task_create', provider: 'core', risk_level: 'caution' },
  { name: 'bash', provider: 'core', risk_level: 'destructive' },
]

/** Mount the form and hand back the latest draft the form emitted. */
function mount() {
  const onChange = vi.fn()
  let draft: AgentDraft = { ...emptyDraft(), name: 'auditor' }
  onChange.mockImplementation((d: AgentDraft) => { draft = d })
  render(<AgentForm draft={draft} onChange={onChange} />)
  return { onChange, latest: () => draft }
}

const toolRow = (name: string) => screen.getByRole('button', { name: new RegExp(`^${name}`) })

beforeEach(() => {
  toolsMock.mockReset()
  toolsMock.mockResolvedValue(TOOLS)
  confirmDialog.mockReset()
  confirmDialog.mockResolvedValue(true)
})
afterEach(cleanup)

describe('#506 — granting an agent a destructive tool takes more than a tick', () => {
  it('asks before adding a destructive tool, and does not add it when declined', async () => {
    confirmDialog.mockResolvedValue(false)
    const { onChange } = mount()
    await waitFor(() => expect(toolRow('bash')).toBeTruthy())

    fireEvent.click(toolRow('bash'))

    await waitFor(() => expect(confirmDialog).toHaveBeenCalled())
    expect(onChange).not.toHaveBeenCalled()
  })

  it('adds it once confirmed', async () => {
    const { onChange, latest } = mount()
    await waitFor(() => expect(toolRow('bash')).toBeTruthy())

    fireEvent.click(toolRow('bash'))

    await waitFor(() => expect(onChange).toHaveBeenCalled())
    expect(latest().tools).toEqual(['bash'])
  })

  it('names the tool and what the grant means, so the dialog is a decision not a speed bump', async () => {
    mount()
    await waitFor(() => expect(toolRow('bash')).toBeTruthy())

    fireEvent.click(toolRow('bash'))

    await waitFor(() => expect(confirmDialog).toHaveBeenCalled())
    const opts = confirmDialog.mock.calls[0][0] as { title: string; body: string; danger?: boolean }
    expect(opts.title).toContain('bash')
    // The grant's REACH is the thing being consented to — "every run of this agent", not
    // "this call". A dialog that only said "are you sure?" would be ceremony.
    expect(`${opts.title} ${opts.body}`).toMatch(/every run|future|without asking/i)
    expect(opts.danger).toBe(true)
  })
})

describe('#506 — the ladder stays proportional on this surface too', () => {
  it('a safe tool is still one tick', async () => {
    const { onChange, latest } = mount()
    await waitFor(() => expect(toolRow('artifact_list')).toBeTruthy())

    fireEvent.click(toolRow('artifact_list'))

    await waitFor(() => expect(onChange).toHaveBeenCalled())
    expect(confirmDialog).not.toHaveBeenCalled()
    expect(latest().tools).toEqual(['artifact_list'])
  })

  it('a caution tool is still one tick — the gate is destructive-only, as on the route', async () => {
    const { onChange } = mount()
    await waitFor(() => expect(toolRow('task_create')).toBeTruthy())

    fireEvent.click(toolRow('task_create'))

    await waitFor(() => expect(onChange).toHaveBeenCalled())
    expect(confirmDialog).not.toHaveBeenCalled()
  })

  it('REMOVING a destructive grant does not ask', async () => {
    const onChange = vi.fn()
    let draft: AgentDraft = { ...emptyDraft(), name: 'auditor', tools: ['bash'] }
    onChange.mockImplementation((d: AgentDraft) => { draft = d })
    render(<AgentForm draft={draft} onChange={onChange} />)
    await waitFor(() => expect(toolRow('bash')).toBeTruthy())

    fireEvent.click(toolRow('bash'))

    await waitFor(() => expect(onChange).toHaveBeenCalled())
    // Withdrawing a permission is the safe direction; gating it would protect nothing and
    // discourage the edit that reduces blast radius.
    expect(confirmDialog).not.toHaveBeenCalled()
    expect(draft.tools).toEqual([])
  })

  it('skills are untouched — only a risk-carrying option escalates', async () => {
    // The gate lives in the shared CheckList and keys on the OPTION's risk, so the Skills and
    // Triggers lists (which carry none) behave exactly as before.
    const { onChange } = mount()
    await waitFor(() => expect(toolRow('artifact_list')).toBeTruthy())
    expect(screen.queryByRole('button', { name: /^some-skill/ })).toBeNull()
    fireEvent.click(toolRow('artifact_list'))
    await waitFor(() => expect(onChange).toHaveBeenCalled())
    expect(confirmDialog).not.toHaveBeenCalled()
  })
})
