/**
 * A batch's run card says which step you denied, as your decision — not as a failure.
 *
 * Denying the approval a batch step's subagent asked before it started ends the run `declined`,
 * its ending naming the step ("“Audit the notes” was denied by you."). The card showed every
 * ending as a red alert, so her own Deny read as the batch breaking. A declined run's ending, and
 * a run its loop's stop ended, read in the run's informational tone, as the run page reads them; a
 * failure is still an alert.
 *
 * A terminal run does not subscribe to the stream, so mounting one needs only the snapshot fetch.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'

let workflowRun: ReturnType<typeof vi.fn>

async function mountCard(over: Record<string, unknown>) {
  workflowRun = vi.fn(() => Promise.resolve({
    run_id: '5e1a7c20', workflow: 'subagent-batch-1', status: 'declined', spec_version: 1,
    error: '“Audit the notes” was denied by you.', attention: null, tokens: 0, elapsed_secs: 5,
    nodes: [
      { instance_path: 'root.children[0]', node_id: 'audit_the_notes_0', label: 'Audit the notes',
        state: 'declined', degraded_reason: 'denied by you' },
    ],
    ...over,
  }))
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return { ...real, api: { ...(real.api as object), workflowRun } }
  })
  const { WorkflowProgressCard } = await import('./WorkflowProgressCard')
  render(<WorkflowProgressCard refObj={{ runId: '5e1a7c20', created: true }} />)
  await waitFor(() => expect(workflowRun).toHaveBeenCalled())
}

beforeEach(() => { cleanup(); vi.resetModules() })
afterEach(() => cleanup())

describe('a batch whose step you denied', () => {
  it('🔴 names the step you denied, and not as an alert', async () => {
    await mountCard({})
    const ending = await screen.findByText('“Audit the notes” was denied by you.')
    expect(ending.closest('[role="alert"]')).toBeNull()
    expect(ending.className).not.toContain('text-danger')
  })

  it('reads a run its loop’s stop ended the same way', async () => {
    await mountCard({ status: 'cancelled', error: 'Stopped because its loop “Release notes” was stopped.' })
    const ending = await screen.findByText('Stopped because its loop “Release notes” was stopped.')
    expect(ending.closest('[role="alert"]')).toBeNull()
  })

  it('still alerts on a failure', async () => {
    await mountCard({ status: 'failed', error: '“Audit the notes” failed: the model call failed.' })
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('“Audit the notes” failed: the model call failed.'))
  })
})
