/**
 * A run whose loop stopped at the budget it was given says it stopped at its budget, and nothing on
 * the page calls it an escalation, blames a step, or names the template's internal loop id.
 *
 * Setup's one-cycle loop ran its cycle, the judge did not accept it, and the loop stopped: the
 * budget of 1 was setup's own. The bell said "Loop stopped at its budget"; the run page said
 * "Escalated", "“project” escalated.", "The loop reached its iteration ceiling at project", and "A
 * new run of this workflow fails the same way until the step changes". The escalation record now
 * says it was a budget stop (`budget: true`), and every part of the page reads that.
 *
 * Driven through the real page with the api mocked at its module boundary.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import type { WorkflowRunDetailData } from '../../lib/api'
import { WorkflowRunDetail } from './WorkflowRunDetail'
import { DialogHost } from '../../ui/dialog/DialogHost'
import { runLook } from './workflowMeta'
import { stoppedAtBudget } from './attentionMeta'

const workflowRun = vi.fn<(id: string) => Promise<WorkflowRunDetailData>>()

vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      workflowRun: (id: string) => workflowRun(id),
      workflowContinuations: () => Promise.resolve({ continuations: [] }),
    },
  }
})

vi.mock('./useWorkflowStream', () => ({ useWorkflowStream: () => ({ connected: true }) }))
vi.mock('./RunToolApprovals', () => ({ RunToolApprovals: () => null }))
vi.mock('./DeliverablePanel', () => ({ DeliverablePanel: () => null }))

const SENTENCE = 'It used its budget of 1 cycle, and the judge did not accept the last one.'

function stop(reason: string, budget: boolean) {
  return {
    kind: 'escalation', node_id: 'project', instance_path: 'root', reason, budget,
    detail: SENTENCE, attempts: [],
  }
}

function run(reason = 'max_iterations', budget = true): WorkflowRunDetailData {
  const escalation = stop(reason, budget)
  return {
    run_id: 'fb54446b',
    workflow: 'general-project',
    title: 'Draft a short note describing what an agent could do',
    status: 'escalated',
    spec_version: 1,
    error: SENTENCE,
    attention: { ...escalation, instance_path: undefined },
    escalations: [escalation],
    nodes: [
      { instance_path: 'root', node_id: 'project', state: 'escalated' },
      { instance_path: 'root.body@0', node_id: 'step', state: 'done' },
      { instance_path: 'root.body@0.children[0]', node_id: 'work', state: 'done' },
      { instance_path: 'root.body@0.children[1]', node_id: 'judge', state: 'done' },
    ],
  }
}

function mount() {
  return render(<><WorkflowRunDetail runId="fb54446b" onBack={() => {}} onOpenRun={() => {}} /><DialogHost /></>)
}

beforeEach(() => { vi.clearAllMocks() })

describe('a run whose loop stopped at its budget', () => {
  it('🔴 says it stopped at its budget, and never "Escalated"', async () => {
    workflowRun.mockResolvedValue(run())
    const { container } = mount()
    const status = await screen.findByText((_, el) => el?.hasAttribute('data-run-status') ?? false)
    expect(status.textContent).toContain('Stopped at its budget')
    expect(container.textContent).not.toMatch(/Escalated/)
    // The loop's own row reads as the run it ended.
    expect(screen.getAllByText('Stopped at its budget').length).toBeGreaterThan(1)
  })

  it('🔴 does not blame the workflow or name the internal loop id in its account', async () => {
    workflowRun.mockResolvedValue(run())
    mount()
    const panel = await screen.findByTestId('escalation-panel')
    expect(within(panel).getByRole('heading').textContent).toBe('This run stopped at its budget')
    expect(panel.textContent).not.toMatch(/fails the same way/)
    expect(within(panel).queryByRole('link', { name: /Change the workflow/ })).toBeNull()
    expect(panel.textContent).not.toMatch(/project/)
    expect(panel.textContent).toMatch(/set Max cycles/)
    // The ending line is the budget sentence, in the page's informational tone.
    const line = screen.getByText(SENTENCE)
    expect(line.className).not.toMatch(/text-danger/)
  })

  it('a token budget is the workflow’s own, so that one offers the editor', async () => {
    workflowRun.mockResolvedValue(run('token_cap'))
    mount()
    const panel = await screen.findByTestId('escalation-panel')
    expect(within(panel).getByRole('link', { name: /Change the workflow/ })).toBeTruthy()
    expect(panel.textContent).not.toMatch(/fails the same way/)
  })

  it('a loop that escalated for another reason still reads Escalated (the control)', async () => {
    workflowRun.mockResolvedValue(run('iterations_failed', false))
    mount()
    const panel = await screen.findByTestId('escalation-panel')
    expect(within(panel).getByRole('heading').textContent).toBe('This run stopped')
    expect(panel.textContent).toMatch(/fails the same way/)
    expect(screen.getAllByText('Escalated').length).toBeGreaterThan(0)
  })
})

describe('the runs list reads the same classification', () => {
  it('labels a budget stop, and only a budget stop, as one', () => {
    const budget = run()
    expect(runLook(budget.status, '', stoppedAtBudget(budget.status, budget.attention)).label)
      .toBe('Stopped at its budget')
    const other = run('iterations_failed', false)
    expect(runLook(other.status, '', stoppedAtBudget(other.status, other.attention)).label).toBe('Escalated')
    // A finished run is never a budget stop, whatever its record says.
    expect(stoppedAtBudget('complete', budget.attention)).toBe(false)
  })
})
