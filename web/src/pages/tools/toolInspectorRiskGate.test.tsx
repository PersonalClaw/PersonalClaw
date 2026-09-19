import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { ToolItem } from '../../lib/api'
import { ToolInspector } from './ToolInspector'

// ── #506 — the Try-it ceremony scales with the tool's risk, and the SERVER agrees ──
//
// The confirm was one warn-toned inline step, byte-identical for `artifact_list` and for
// `bash`: `tool.risk_level` was in scope, rendered as a pill two rows up, and never read by
// the run path. Meanwhile `/api/tools/invoke` resolved the same risk and spent it entirely
// on the audit row. So the taxonomy described and nothing anywhere refused.
//
// These tests assert REACHABLE BEHAVIOUR — did `api.invokeTool` get called, and with what —
// never that a string appears in the file. A source-text assertion passes with the control
// deleted, and the defect being fixed here is precisely a control that looked right.
//
// The ladder, lowest rung first (the PR body states the cost of each):
//   safe        → the inline "Confirm & run" step. Unchanged: two clicks, no dialog.
//   caution     → a modal that must be dismissed deliberately, with a named confirm verb.
//   destructive → a modal that requires TYPING the tool name, and the request then carries
//                 `confirm_risk: "destructive"`, which the route now requires.
// Plus: a 403 `risk_confirmation_required` from the server escalates and retries rather than
// dead-ending, because the EFFECTIVE tier the route resolves can exceed the DECLARED tier
// this component renders (name inference, an unscreenable shell command).

const invokeTool = vi.fn()
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return { ...real, api: { ...real.api, invokeTool: (...a: unknown[]) => invokeTool(...a) } }
})

const confirmDialog = vi.fn()
const promptInput = vi.fn()
vi.mock('../../ui/dialog', () => ({
  confirm: (...a: unknown[]) => confirmDialog(...a),
  promptInput: (...a: unknown[]) => promptInput(...a),
}))

function tool(over: Partial<ToolItem> = {}): ToolItem {
  return {
    name: 'bash', provider: 'core', description: 'Run a command',
    risk_level: 'destructive', requires_approval: true, disabled: false,
    parameters: { type: 'object', properties: {} },
    ...over,
  } as unknown as ToolItem
}

/** Open Try it and press the entry control — the same two clicks at every tier. */
function pressRun(t: ToolItem) {
  render(<ToolInspector tool={t} />)
  fireEvent.click(screen.getByRole('button', { name: /Try it/i }))
  fireEvent.click(screen.getByRole('button', { name: /^Run tool$/i }))
}

beforeEach(() => {
  invokeTool.mockReset()
  invokeTool.mockResolvedValue({ ok: true, output: '[]' })
  confirmDialog.mockReset()
  confirmDialog.mockResolvedValue(true)
  promptInput.mockReset()
  promptInput.mockResolvedValue('bash')
})

describe('#506 — a destructive tool cannot be invoked through the low-friction path', () => {
  it('does not invoke on the inline step: the cheap ceremony is not even offered', async () => {
    pressRun(tool({ name: 'automation_delete_all', risk_level: 'destructive' }))

    // The safe tier's inline "Confirm & run" must not be reachable for a destructive tool —
    // if it were, the same two clicks that run `artifact_list` would run this.
    expect(screen.queryByRole('button', { name: /Confirm & run/i })).toBeNull()
    await waitFor(() => expect(promptInput).toHaveBeenCalled())
    expect(invokeTool).not.toHaveBeenCalled()
  })

  it('requires the tool NAME to be typed, and refuses a near-miss', async () => {
    promptInput.mockResolvedValue('automation_delete')  // a prefix of the real name
    pressRun(tool({ name: 'automation_delete_all', risk_level: 'destructive' }))

    await waitFor(() => expect(promptInput).toHaveBeenCalled())
    // The component re-checks the returned value rather than trusting the dialog's own
    // validator: the ceremony is the gate, so the gate cannot live only in the host.
    expect(invokeTool).not.toHaveBeenCalled()
  })

  it('never invokes when the ceremony is cancelled', async () => {
    promptInput.mockResolvedValue(null)
    pressRun(tool({ name: 'memory_forget', risk_level: 'destructive' }))

    await waitFor(() => expect(promptInput).toHaveBeenCalled())
    expect(invokeTool).not.toHaveBeenCalled()
  })

  it('invokes WITH the tier acknowledged once the name is typed', async () => {
    promptInput.mockResolvedValue('memory_forget')
    pressRun(tool({ name: 'memory_forget', risk_level: 'destructive' }))

    await waitFor(() => expect(invokeTool).toHaveBeenCalled())
    // The fourth argument is the wire ack. Without it the route answers 403, so a ceremony
    // that did not send it would be theatre on the client and a dead end on the server.
    expect(invokeTool).toHaveBeenCalledWith('memory_forget', {}, 'core', 'destructive')
  })
})

describe('#506 — the ladder is proportional, not uniform', () => {
  it('safe keeps the inline step and sends no acknowledgement (the floor)', async () => {
    pressRun(tool({ name: 'artifact_list', risk_level: 'safe' }))

    // No dialog at all for a read: the cheap path stays cheap, which is the property that
    // keeps this a gate rather than an outage.
    expect(promptInput).not.toHaveBeenCalled()
    expect(confirmDialog).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: /Confirm & run/i }))
    await waitFor(() => expect(invokeTool).toHaveBeenCalled())
    expect(invokeTool).toHaveBeenCalledWith('artifact_list', {}, 'core', undefined)
  })

  it('caution takes a modal — dismissing it does not invoke', async () => {
    confirmDialog.mockResolvedValue(false)
    pressRun(tool({ name: 'task_create', risk_level: 'caution' }))

    await waitFor(() => expect(confirmDialog).toHaveBeenCalled())
    expect(promptInput).not.toHaveBeenCalled()  // no typing at this rung
    expect(invokeTool).not.toHaveBeenCalled()
  })

  it('caution invokes when the modal is accepted, and sends no acknowledgement', async () => {
    pressRun(tool({ name: 'task_create', risk_level: 'caution' }))

    await waitFor(() => expect(invokeTool).toHaveBeenCalled())
    // The route gates destructive only — 26 caution tools include the ones cron scripts
    // write through, so sending an ack here would imply a refusal that does not exist.
    expect(invokeTool).toHaveBeenCalledWith('task_create', {}, 'core', undefined)
  })

  it('an undeclared risk gets the caution rung, not the safe one', async () => {
    // External MCP tools can arrive with no declared tier. Defaulting such a tool to the
    // cheapest ceremony would put the unknown calls on the easiest path.
    confirmDialog.mockResolvedValue(false)
    pressRun(tool({ name: 'mystery_tool', risk_level: undefined }))

    await waitFor(() => expect(confirmDialog).toHaveBeenCalled())
    expect(invokeTool).not.toHaveBeenCalled()
  })
})

describe('#506 — a server refusal escalates instead of dead-ending', () => {
  it('re-asks with the typed ceremony and retries with the acknowledgement', async () => {
    const { ApiError } = await import('../../lib/api')
    // The route resolved a HIGHER effective tier than this tool declares — what happens
    // when name inference classifies an undeclared MCP tool, or a shell call arrives with
    // no command to screen. The component renders the declared tier, so without this path
    // the user would fill in arguments, confirm, and collect a 403 they cannot act on.
    invokeTool
      .mockRejectedValueOnce(new ApiError('nope', 403, 'risk_confirmation_required'))
      .mockResolvedValueOnce({ ok: true, output: 'done' })
    promptInput.mockResolvedValue('sneaky_delete')

    pressRun(tool({ name: 'sneaky_delete', risk_level: 'safe' }))
    fireEvent.click(screen.getByRole('button', { name: /Confirm & run/i }))

    await waitFor(() => expect(invokeTool).toHaveBeenCalledTimes(2))
    expect(invokeTool.mock.calls[0]).toEqual(['sneaky_delete', {}, 'core', undefined])
    expect(invokeTool.mock.calls[1]).toEqual(['sneaky_delete', {}, 'core', 'destructive'])
  })

  it('a declined escalation reports the refusal rather than retrying', async () => {
    const { ApiError } = await import('../../lib/api')
    invokeTool.mockRejectedValue(new ApiError('nope', 403, 'risk_confirmation_required'))
    promptInput.mockResolvedValue(null)

    pressRun(tool({ name: 'sneaky_delete', risk_level: 'safe' }))
    fireEvent.click(screen.getByRole('button', { name: /Confirm & run/i }))

    await waitFor(() => expect(promptInput).toHaveBeenCalled())
    expect(invokeTool).toHaveBeenCalledTimes(1)
    // The user learns why, in the result area, instead of the flow going quiet.
    expect(await screen.findByText(/destructive/i)).toBeTruthy()
  })
})
