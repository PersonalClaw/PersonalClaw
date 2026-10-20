import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { ToolItem } from '../../lib/api'
import { ToolInspector } from './ToolInspector'

// ── Try it asks what the route refuses BEFORE it asks the owner to confirm ──
//
// `bash` is declared destructive, so Run tool opens the typed-name confirmation at once. A command
// the shell denylist refuses (a pattern added under Settings → Security) was confirmed
// by typing "bash", sent, and only then refused by the tool: the owner was asked to confirm a run
// that could never happen. Every tier now asks the route's check (`dry_run`) first, and a refusal
// is shown in the rule's own words with no confirmation asked and nothing sent to run.

const invokeTool = vi.fn()
const checkTool = vi.fn()
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      invokeTool: (...a: unknown[]) => invokeTool(...a),
      checkTool: (...a: unknown[]) => checkTool(...a),
    },
  }
})

const confirmDialog = vi.fn()
const promptInput = vi.fn()
vi.mock('../../ui/dialog', () => ({
  confirm: (...a: unknown[]) => confirmDialog(...a),
  promptInput: (...a: unknown[]) => promptInput(...a),
}))

const REFUSAL =
  'Blocked: the command matches `pcfixture-cloudctl`, a pattern added to the shell denylist under ' +
  'Settings → Security. It was not run.'

function bash(over: Partial<ToolItem> = {}): ToolItem {
  return {
    name: 'bash', provider: 'core', description: 'Run a command',
    risk_level: 'destructive', requires_approval: true, disabled: false,
    parameters: { type: 'object', properties: { command: { type: 'string' } }, required: ['command'] },
    ...over,
  } as unknown as ToolItem
}

function runCommand(command: string, t: ToolItem = bash()) {
  render(<ToolInspector tool={t} />)
  fireEvent.click(screen.getByRole('button', { name: /Try it/i }))
  fireEvent.change(screen.getByLabelText(/command/i), { target: { value: command } })
  fireEvent.click(screen.getByRole('button', { name: /^Run tool$/i }))
}

beforeEach(() => {
  invokeTool.mockReset()
  invokeTool.mockResolvedValue({ ok: true, output: 'hello' })
  checkTool.mockReset()
  confirmDialog.mockReset()
  confirmDialog.mockResolvedValue(true)
  promptInput.mockReset()
  promptInput.mockResolvedValue('bash')
})

describe('Try it refuses what cannot run before anyone is asked', () => {
  it('a command the shell denylist refuses is shown refused, with no confirmation and no run', async () => {
    checkTool.mockResolvedValue({ ok: false, error: REFUSAL, not_run: 'refused_by_tool', dry_run: true })
    runCommand('pcfixture-cloudctl status')

    expect(await screen.findByText(/a pattern added to the shell denylist under Settings → Security/)).toBeTruthy()
    expect(checkTool).toHaveBeenCalledWith('bash', { command: 'pcfixture-cloudctl status' }, 'core')
    expect(promptInput).not.toHaveBeenCalled()
    expect(confirmDialog).not.toHaveBeenCalled()
    expect(invokeTool).not.toHaveBeenCalled()
  })

  it('an ordinary command is still confirmed and run as before (the control)', async () => {
    checkTool.mockResolvedValue({ ok: true, dry_run: true })
    runCommand('echo hello')

    await waitFor(() => expect(invokeTool).toHaveBeenCalled())
    expect(promptInput).toHaveBeenCalled()
    expect(invokeTool).toHaveBeenCalledWith('bash', { command: 'echo hello' }, 'core', 'destructive')
  })

  it('a check that cannot be made refuses nothing: the confirmation is asked as before', async () => {
    checkTool.mockRejectedValue(new Error('the gateway did not answer'))
    runCommand('echo hello')

    await waitFor(() => expect(promptInput).toHaveBeenCalled())
    await waitFor(() => expect(invokeTool).toHaveBeenCalled())
  })

  it('a tool switched off is refused before the confirmation too', async () => {
    const { ApiError } = await import('../../lib/api')
    checkTool.mockRejectedValue(new ApiError("'bash' is disabled — re-enable it on the Tools page to invoke it", 403, 'tool_disabled'))
    runCommand('echo hello')

    expect(await screen.findByText(/re-enable it on the Tools page/)).toBeTruthy()
    expect(promptInput).not.toHaveBeenCalled()
    expect(invokeTool).not.toHaveBeenCalled()
  })
})
