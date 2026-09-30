import { describe, expect, it, vi, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ManualAutomationCard, manualAutomationFromTool } from './ManualAutomationCard'

// ── "Make me a button that runs …" is shown with a button that runs it ──────────────────────────
//
// Asked for a button that runs the kitchen-quote comparison, the agent drew a widget whose button
// only posted "[UI] run_comparison" back into the chat, and said it had made a button that runs
// the comparison. Nothing ran. The chat now makes a manual automation for it (`automation_create`,
// `kind: "manual"`), and its result is shown with Run now: the Triggers page's run, where she asked.

const runStoreTrigger = vi.fn()
vi.mock('../../lib/api', () => ({ api: { runStoreTrigger: (...a: unknown[]) => runStoreTrigger(...a) } }))

const ROW = {
  id: 'manual:kitchen-quote-comparison', name: 'Kitchen quote comparison', kind: 'manual', enabled: true,
  workflow: { inline: { provider: 'run-prompt', config: { message: 'Compare the three quotes.' } } },
}
const created = (row: object, needs: string[] = ['Run Prompt']) =>
  'Created “Kitchen quote comparison”.\n  it runs only when you run it\n\n'
  + `<automation-data>${JSON.stringify({ trigger: row, needs_grant: needs })}</automation-data>`

afterEach(() => { cleanup(); runStoreTrigger.mockReset() })

describe('an automation made to run when she runs it', () => {
  it('is recognized from what automation_create answered, and nothing else is', () => {
    expect(manualAutomationFromTool('automation_create', created(ROW))).toEqual({
      id: 'manual:kitchen-quote-comparison', name: 'Kitchen quote comparison', needsGrant: ['Run Prompt'],
    })
    // The same tool reached through an agent's MCP client carries its server's prefix.
    expect(manualAutomationFromTool('mcp__personalclaw__automation_create', created(ROW))?.id)
      .toBe('manual:kitchen-quote-comparison')
    // Another kind is its own automation, run by what it waits for; another tool is not this one.
    expect(manualAutomationFromTool('automation_create', created({ ...ROW, kind: 'clock' }))).toBeNull()
    expect(manualAutomationFromTool('automation_update', created(ROW))).toBeNull()
    expect(manualAutomationFromTool('automation_create', 'Created it.')).toBeNull()
    expect(manualAutomationFromTool('automation_create', '<automation-data>{not json</automation-data>')).toBeNull()
  })

  it('🔑 Run now runs it, and says it started', async () => {
    runStoreTrigger.mockResolvedValue({ ok: true, name: ROW.name, status: 'launched' })
    render(<ManualAutomationCard refObj={{ id: ROW.id, name: ROW.name, needsGrant: [] }} />)
    expect(screen.getByText('It runs only when you run it.')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Run Kitchen quote comparison now' }))
    await waitFor(() => expect(runStoreTrigger).toHaveBeenCalledWith('manual:kitchen-quote-comparison'))
    expect(await screen.findByRole('status')).toHaveTextContent('Started: launched.')
  })

  it('one not yet allowed says so before it is pressed, and pressing it says what it needs', async () => {
    runStoreTrigger.mockResolvedValue({
      ok: false, name: ROW.name, refused: 'Kitchen quote comparison is not allowed to use Run Prompt yet. Allow it on the Triggers page.',
    })
    render(<ManualAutomationCard refObj={{ id: ROW.id, name: ROW.name, needsGrant: ['Run Prompt'] }} />)
    expect(screen.getByText(/not until you allow it on the Triggers page/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Open/ })).toHaveAttribute('href', '#/triggers?open=manual%3Akitchen-quote-comparison')

    fireEvent.click(screen.getByRole('button', { name: 'Run Kitchen quote comparison now' }))
    expect(await screen.findByRole('status')).toHaveTextContent('is not allowed to use Run Prompt yet')
  })
})
