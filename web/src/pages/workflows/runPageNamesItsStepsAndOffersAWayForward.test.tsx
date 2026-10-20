/**
 * The run page names a step the way the run's own sentences do, and a stopped run offers a way
 * forward rather than a decision nobody can make.
 *
 * Measured on a run: its error line said "“Check the draft” failed: the draft is empty, so nothing
 * after it ran" while the rows, the graph, the dialogs, the inspector and the escalation panel said
 * `check` — the run's status carried only each node's id. It carries each step's label now, and
 * every surface here reads it.
 *
 * And the escalation panel was headed "This run stopped and needs a decision" on a run that waits
 * for nothing, with no control at all unless the failure was one a Retry clears. A stopped run now
 * says it stopped and offers what works on a finished run: Retry where a fresh attempt can succeed,
 * and the workflow's editor, for the failure that repeats until the step changes.
 *
 * Driven through the real page with the api mocked at its module boundary.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { WorkflowRunDetailData } from '../../lib/api'
import { WorkflowRunDetail } from './WorkflowRunDetail'
import { DialogHost } from '../../ui/dialog/DialogHost'

const workflowRun = vi.fn<(id: string) => Promise<WorkflowRunDetailData>>()
const workflowRunNodeInspect = vi.fn()

vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      workflowRun: (id: string) => workflowRun(id),
      workflowContinuations: () => Promise.resolve({ continuations: [] }),
      workflowRunNodeInspect: (...args: unknown[]) => workflowRunNodeInspect(...args),
    },
  }
})

vi.mock('./useWorkflowStream', () => ({ useWorkflowStream: () => ({ connected: true }) }))
vi.mock('./RunToolApprovals', () => ({ RunToolApprovals: () => null }))
vi.mock('./DeliverablePanel', () => ({ DeliverablePanel: () => null }))

const ESCALATION = {
  kind: 'escalation',
  node_id: 'check',
  instance_path: 'root.children[1]',
  reason: 'not_retried',
  detail: 'the draft is empty',
  attempts: [{ attempt: 1, failure_class: 'user', error: 'the draft is empty' }],
}

function stoppedRun(retryable = false): WorkflowRunDetailData {
  return {
    run_id: 'run-1',
    workflow: 'publish-article',
    status: 'failed',
    spec_version: 1,
    error: '“Check the draft” failed: the draft is empty, so nothing after it ran.',
    attention: ESCALATION,
    escalations: [ESCALATION],
    nodes: [
      { instance_path: 'root.children[0]', node_id: 'gather', label: 'Gather the sources', state: 'done' },
      {
        instance_path: 'root.children[1]', node_id: 'check', label: 'Check the draft', state: 'failed',
        failure: { class: retryable ? 'network' : 'user', cause_plain: 'the draft is empty', retryable },
      },
      { instance_path: 'root.children[2]', node_id: 'plain', state: 'skipped' },
    ],
  }
}

function runningRun(): WorkflowRunDetailData {
  return {
    run_id: 'run-1', workflow: 'publish-article', status: 'running', spec_version: 1,
    nodes: [
      { instance_path: 'root.children[0]', node_id: 'gather', label: 'Gather the sources', state: 'done' },
      { instance_path: 'root.children[1]', node_id: 'check', label: 'Check the draft', state: 'running' },
    ],
  }
}

function mount() {
  return render(<><WorkflowRunDetail runId="run-1" onBack={() => {}} onOpenRun={() => {}} /><DialogHost /></>)
}

beforeEach(() => {
  vi.clearAllMocks()
  workflowRunNodeInspect.mockResolvedValue({
    run_id: 'run-1', node_id: 'gather', instance_path: 'root.children[0]', state: 'done',
    resolved_prompt: '', resolved_inputs: {}, output: 'sources', attempts: [], ledger_events: [], cached: false,
  })
})

describe('the run page names each step by its label', () => {
  it('🔴 a row says the step’s label, where it said its id', async () => {
    workflowRun.mockResolvedValue(stoppedRun())
    mount()
    expect(await screen.findByText('Gather the sources')).toBeTruthy()
    expect(screen.getByText('Check the draft')).toBeTruthy()
    // A step with no label has nothing better than its id.
    expect(screen.getByText('plain')).toBeTruthy()
    expect(screen.queryByText('gather')).toBeNull()
  })

  it('🔴 the graph names each node by its label', async () => {
    workflowRun.mockResolvedValue(stoppedRun())
    mount()
    await screen.findByText('Gather the sources')
    fireEvent.click(screen.getByRole('radio', { name: 'Graph' }))
    expect(await screen.findByLabelText('Gather the sources — done')).toBeTruthy()
    expect(screen.getByLabelText('Check the draft — failed')).toBeTruthy()
  })

  it('🔴 the escalation panel names the step as the run’s ending does', async () => {
    workflowRun.mockResolvedValue(stoppedRun())
    mount()
    const panel = await screen.findByTestId('escalation-panel')
    expect(within(panel).getByText('“Check the draft”')).toBeTruthy()
    expect(within(panel).queryByText('check')).toBeNull()
  })

  it('🔴 the inspector is titled with the step’s label, its id beside it', async () => {
    workflowRun.mockResolvedValue(stoppedRun())
    mount()
    await screen.findByText('Gather the sources')
    fireEvent.click(screen.getAllByTitle(/Inspect this node/)[0])
    const body = await screen.findByTestId('node-inspector-body')
    const drawer = body.closest('aside, [role="dialog"], section') ?? document.body
    expect(within(drawer as HTMLElement).getAllByText('Gather the sources').length).toBeGreaterThan(0)
    expect(within(drawer as HTMLElement).getByText('gather')).toBeTruthy()
  })

  it('🔴 the Edit dialog names the stage it edits by its label', async () => {
    workflowRun.mockResolvedValue(runningRun())
    mount()
    await screen.findByText('Gather the sources')
    fireEvent.click(screen.getAllByTitle(/Edit this stage's instruction/)[0])
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Edit “Gather the sources”')).toBeTruthy()
  })
})

describe('a stopped run offers a way forward, not a decision', () => {
  it('🔴 does not say it needs a decision it offers no way to make', async () => {
    workflowRun.mockResolvedValue(stoppedRun())
    mount()
    const panel = await screen.findByTestId('escalation-panel')
    expect(within(panel).getByRole('heading').textContent).toBe('This run stopped')
    expect(panel.textContent).not.toMatch(/needs a decision/i)
  })

  it('🔴 offers the workflow’s editor for a failure a retry repeats', async () => {
    workflowRun.mockResolvedValue(stoppedRun(false))
    mount()
    const panel = await screen.findByTestId('escalation-panel')
    const change = within(panel).getByRole('link', { name: /Change the workflow/ })
    expect(change.getAttribute('href')).toBe('#/workflows/defs/publish-article/edit')
    expect(panel.textContent).toMatch(/fails the same way until the step changes/)
    expect(within(panel).queryByRole('button', { name: /retry/i })).toBeNull()
  })

  it('offers Retry and the editor for a failure a fresh attempt can clear', async () => {
    workflowRun.mockResolvedValue(stoppedRun(true))
    mount()
    const panel = await screen.findByTestId('escalation-panel')
    expect(within(panel).getByRole('button', { name: /retry/i })).toBeTruthy()
    expect(within(panel).getByRole('link', { name: /Change the workflow/ })).toBeTruthy()
  })

  it('offers nothing on a run that finished with failed items', async () => {
    const run = stoppedRun(false)
    run.status = 'complete'
    run.error = ''
    workflowRun.mockResolvedValue(run)
    mount()
    const panel = await screen.findByTestId('escalation-panel')
    await waitFor(() => expect(within(panel).getByRole('heading').textContent).toMatch(/finished, but 1 step failed/))
    expect(within(panel).queryByRole('link')).toBeNull()
    expect(within(panel).queryByRole('button')).toBeNull()
  })
})
