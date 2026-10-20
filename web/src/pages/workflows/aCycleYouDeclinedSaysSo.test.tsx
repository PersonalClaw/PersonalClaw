/**
 * A loop cycle you ended with a Deny says so on its run's page, and the loop waits for you.
 *
 * A General loop is a workflow run. You denied its worker's write, and the loop ran the step again
 * with the judge's critique, which asked you for the same write; the step's row read "done" with no
 * trace of your answer. Now the cycle ends at your Deny and the run pauses for you
 * (`declines.end_cycle`): the page says so as your wait, not as a fault, Resume runs the next cycle,
 * the step's row names what you declined, and the page lists it under "Declined by you" once the
 * wait is over (while it waits, the line that says why says it, once).
 *
 * Driven through the real page with the api mocked at its module boundary.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { WorkflowRunDetailData } from '../../lib/api'
import { WorkflowRunDetail } from './WorkflowRunDetail'

const SAID = 'Cycle 1 ended at “work”: you declined write_file (notes/plan.md).'
const WAITS = `${SAID} The loop waits for you: tell it what to do instead, or resume or stop it.`

const workflowRun = vi.fn<(id: string) => Promise<WorkflowRunDetailData>>()
const resumeWorkflowRun = vi.fn()

vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      workflowRun: (id: string) => workflowRun(id),
      workflowContinuations: () => Promise.resolve({ continuations: [] }),
      resumeWorkflowRun: (...args: unknown[]) => resumeWorkflowRun(...args),
    },
  }
})

vi.mock('./useWorkflowStream', () => ({ useWorkflowStream: () => ({ connected: true }) }))
vi.mock('./RunToolApprovals', () => ({ RunToolApprovals: () => null }))
vi.mock('./DeliverablePanel', () => ({ DeliverablePanel: () => null }))

function loopRun(over: Partial<WorkflowRunDetailData> = {}): WorkflowRunDetailData {
  return {
    run_id: 'run-1',
    workflow: 'general-project',
    status: 'paused',
    spec_version: 1,
    loop_kind: 'general',
    title: 'Week plan',
    pause_requested: true,
    error: WAITS,
    declined: [SAID],
    declined_wait: true,
    nodes: [
      {
        instance_path: 'root.body@0.children[0]',
        node_id: 'work',
        state: 'done',
        declined: 'You declined write_file (notes/plan.md).',
      },
    ],
    ...over,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  resumeWorkflowRun.mockResolvedValue({ ok: true, resumed: true })
})

const mount = () => render(<WorkflowRunDetail runId="run-1" onBack={() => {}} onOpenRun={() => {}} />)

describe('a loop waiting for you after your Deny', () => {
  it('says why it waits as your wait, not as a fault', async () => {
    workflowRun.mockResolvedValue(loopRun())
    mount()
    const line = await screen.findByText(WAITS)
    expect(line.className).toContain('text-on-surface-var')
    expect(line.className).not.toContain('text-danger')
  })

  it('offers Resume, which runs its next cycle', async () => {
    workflowRun.mockResolvedValue(loopRun())
    mount()
    const resume = await screen.findByRole('button', { name: /resume/i })
    expect(resume.getAttribute('title')).toMatch(/next cycle runs/)
    fireEvent.click(resume)
    await waitFor(() => expect(resumeWorkflowRun).toHaveBeenCalledWith('run-1', {}))
  })

  it('names what you declined on the step it stopped', async () => {
    workflowRun.mockResolvedValue(loopRun())
    mount()
    expect(await screen.findByText('You declined write_file (notes/plan.md).')).toBeTruthy()
  })
})

describe('what you declined', () => {
  it('is said once while the loop waits on it: by the line that says why it waits', async () => {
    workflowRun.mockResolvedValue(loopRun())
    mount()
    expect(await screen.findByText(WAITS)).toBeTruthy()
    expect(screen.queryByText(SAID)).toBeNull()
    expect(screen.queryByRole('heading', { name: /Declined by you/ })).toBeNull()
  })

  it('is listed under "Declined by you" for an earlier cycle while the loop waits on a later one', async () => {
    const later = 'Cycle 2 ended at “work”: you declined write_file (notes/week.md).'
    workflowRun.mockResolvedValue(loopRun({
      error: `${later} The loop waits for you: tell it what to do instead, or resume or stop it.`,
      declined: [SAID, later],
    }))
    mount()
    const heading = await screen.findByRole('heading', { name: /Declined by you/ })
    const listed = heading.closest('section')?.textContent ?? ''
    expect(listed).toContain(SAID)
    expect(listed).not.toContain(later)
  })

  it('is still listed after the wait is over', async () => {
    workflowRun.mockResolvedValue(loopRun({ status: 'complete', error: '', declined_wait: false, pause_requested: false }))
    mount()
    expect(await screen.findByRole('heading', { name: /Declined by you/ })).toBeTruthy()
    expect(screen.getByText(SAID)).toBeTruthy()
  })

  it('is not listed for a run you declined nothing in, and a plain pause keeps its own Resume', async () => {
    workflowRun.mockResolvedValue(loopRun({ error: '', declined: [], declined_wait: false, nodes: [] }))
    mount()
    const resume = await screen.findByRole('button', { name: /resume/i })
    expect(resume.getAttribute('title')).toMatch(/the step the pause stopped runs again/)
    expect(screen.queryByRole('heading', { name: /Declined by you/ })).toBeNull()
  })
})
