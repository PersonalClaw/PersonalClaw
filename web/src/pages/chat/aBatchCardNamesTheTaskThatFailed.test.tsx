/**
 * A batch run's chat card says which task did not do its work, and why.
 *
 * `subagent_run` with two or more tasks runs them as one batch run, and the run completes when any
 * task does. A task whose every tool call was refused ends failed, saying why — but the card read
 * "Completed 2/2" and nothing else, so the chat that started the batch was told every task worked.
 * A finished run now names its failed steps, each with its cause; a run whose ending already names
 * them (its `error` line) is not told twice.
 *
 * A terminal run does not subscribe to the stream, so mounting one needs only the snapshot fetch.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'

const REFUSED = "Couldn't do its task: every tool call it made was refused — write_file: its tools are read-only, and write_file is not one of them"

let workflowRun: ReturnType<typeof vi.fn>

async function mountCard(over: Record<string, unknown>) {
  workflowRun = vi.fn(() => Promise.resolve({
    run_id: '5e1a7c20', workflow: 'subagent-batch-1', status: 'complete', spec_version: 1,
    error: '', attention: null, tokens: 240, elapsed_secs: 5,
    nodes: [
      { instance_path: 'root.children[0]', node_id: 'review_the_fix_0', state: 'failed',
        failure: { class: 'permission', cause_plain: REFUSED } },
      { instance_path: 'root.children[1]', node_id: 'review_the_test_1', state: 'done' },
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

describe('a batch that completed with a task that did not', () => {
  it('🔴 names the task and why it failed', async () => {
    await mountCard({})
    await waitFor(() => expect(screen.getByText(/review_the_fix_0 failed: Couldn't do its task/)).toBeTruthy())
    expect(screen.queryByText(/review_the_test_1 failed/)).toBeNull()
  })

  it('counts its steps as finished, not done', async () => {
    await mountCard({})
    await waitFor(() => expect(screen.getByRole('progressbar', { name: /2 of 2 steps finished/ })).toBeTruthy())
  })

  it('says nothing more for a batch whose every task did its work', async () => {
    await mountCard({ nodes: [{ instance_path: 'root.children[0]', node_id: 'review_the_fix_0', state: 'done' }] })
    await waitFor(() => expect(screen.getByText('subagent-batch-1')).toBeTruthy())
    expect(screen.queryByText(/failed/)).toBeNull()
  })

  it('does not repeat what a failed run already says in its ending', async () => {
    const ending = `“review_the_fix_0” failed: ${REFUSED}.`
    await mountCard({ status: 'failed', error: ending })
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe(ending))
    expect(screen.queryAllByText(/review_the_fix_0 failed/)).toHaveLength(0)
  })
})
