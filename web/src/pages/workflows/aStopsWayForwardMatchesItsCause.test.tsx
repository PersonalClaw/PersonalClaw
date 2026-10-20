/**
 * A run that stopped says why it stopped, and offers the way forward its cause has.
 *
 * Two first-run loops, measured: one whose judge could not decide (it could not check what the task
 * needed, because the file tools did not reach the folder), and one whose judge ran out of time
 * while one of its calls waited for an answer. Both pages read "the loop spent its iterations
 * failing rather than working", offered "Change the workflow", and said "A new run of this workflow
 * fails the same way until the step changes", and nothing about either workflow needed changing.
 * The escalation record now carries its cause and its remedy (`ending_sentence.loop_stop`), and the
 * page heads the stop by its cause, shows the record's remedy, and offers the editor only where a
 * step is the cause.
 *
 * The records are the engine's own, as `tests/test_a_loops_ending_reads_why_it_stopped.py` drives
 * them. Driven through the real page with the api mocked at its module boundary.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import type { WorkflowRunDetailData } from '../../lib/api'
import { WorkflowRunDetail } from './WorkflowRunDetail'
import { DialogHost } from '../../ui/dialog/DialogHost'

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

/** The judge's own words, quoted by the engine's sentence. */
const JUDGE_DETAIL =
  'The judge could not decide whether the work is done, and said why: “Whether the note covers every deadline: '
  + 'the worker could not list the home folder, and neither could I”'
const JUDGE_REMEDY =
  "The decision is yours: read the work and the judge's reason. If it needed a folder the file tools do not reach, "
  + 'add that folder in Settings › Agent defaults › Allowed working directories, then fork the run and start it again.'

const WAIT_DETAIL =
  '“judge” stopped: its time limit of 30 minutes ran out while its bash call had been waiting 12 minutes for your answer.'
const WAIT_REMEDY =
  "Fork the run and start it again, then answer its asks while it waits: a step's time limit counts from when the "
  + 'step starts, and Subagent timeout in Settings › Agent defaults sets it.'

const STEP_DETAIL = '“work” failed: the model provider returned an error.'
const STEP_REMEDY =
  'A new run of this workflow fails the same way until the step changes: change the step that gave up, then run the '
  + 'workflow again.'

/** Words that are the engine's or an agent's bookkeeping, never a person's sentence. */
const INTERNAL = /\[turn|\bturn \d+\/\d+|elapsed:|last tool:|iterations_failed|judge_escalated|approval_timeout/

function run(reason: string, cause: string, detail: string, remedy: string): WorkflowRunDetailData {
  const escalation = {
    kind: 'escalation', node_id: 'project', instance_path: 'root', reason, cause, detail, remedy, attempts: [],
  }
  return {
    run_id: '5cb52f66',
    workflow: 'general-project',
    title: 'Draft a short note describing what I could use an agent for this week',
    status: 'escalated',
    spec_version: 1,
    error: detail,
    attention: { ...escalation, instance_path: undefined },
    escalations: [escalation],
    nodes: [
      { instance_path: 'root', node_id: 'project', state: 'escalated' },
      { instance_path: 'root.body@0', node_id: 'step', state: 'done' },
      { instance_path: 'root.body@0.children[0]', node_id: 'work', state: 'done' },
      { instance_path: 'root.body@0.children[1]', node_id: 'judge', state: cause === 'judge' ? 'done' : 'failed' },
    ],
  }
}

async function panelFor(data: WorkflowRunDetailData) {
  workflowRun.mockResolvedValue(data)
  render(<><WorkflowRunDetail runId={data.run_id} onBack={() => {}} onOpenRun={() => {}} /><DialogHost /></>)
  return screen.findByTestId('escalation-panel')
}

beforeEach(() => { vi.clearAllMocks() })

describe('a stop is headed by its cause, and offers what its cause needs', () => {
  it('🔴 a judge that could not decide: its reason, the decision handed over, no workflow change', async () => {
    const panel = await panelFor(run('judge_escalated', 'judge', JUDGE_DETAIL, JUDGE_REMEDY))
    expect(panel.textContent).toMatch(/the judge could not decide whether the work is done/i)
    expect(panel.textContent).toContain(JUDGE_REMEDY)
    expect(panel.textContent).toMatch(/Allowed working directories/)
    expect(within(panel).queryByRole('link', { name: /Change the workflow/ })).toBeNull()
    expect(panel.textContent).not.toMatch(/fails the same way|spent its iterations failing/)
    // The judge's reason is on the page, in its own words.
    expect(document.body.textContent).toContain('neither could I')
    expect(document.body.textContent).not.toMatch(INTERNAL)
  })

  it('🔴 an ask its time limit ended: the limit, the real wait, answer it next time', async () => {
    const panel = await panelFor(run('iterations_failed', 'approval_timeout', WAIT_DETAIL, WAIT_REMEDY))
    expect(panel.textContent).toMatch(/time limit ran out while it waited for your answer/)
    expect(panel.textContent).toContain(WAIT_REMEDY)
    expect(within(panel).queryByRole('link', { name: /Change the workflow/ })).toBeNull()
    expect(panel.textContent).not.toMatch(/fails the same way|spent its iterations failing/)
    expect(document.body.textContent).toContain('waiting 12 minutes for your answer')
    expect(document.body.textContent).not.toMatch(INTERNAL)
  })

  it('a step that failed at its work is what to change (the control)', async () => {
    const panel = await panelFor(run('iterations_failed', 'step', STEP_DETAIL, STEP_REMEDY))
    expect(panel.textContent).toMatch(/spent its iterations failing rather than working/)
    expect(within(panel).getByRole('link', { name: /Change the workflow/ })).toBeTruthy()
    expect(panel.textContent).toMatch(/fails the same way until the step changes/)
  })
})
