/**
 * A paused run can be RESUMED from its page, a pause in flight says so, and a loop's run page is
 * named by the loop.
 *
 * Measured 2026-09-25 (a General loop is a workflow run, PP-16):
 *   - the header's active arm offered Steer / Pause / Cancel to a PAUSED run — Pause again, and no
 *     Resume anywhere on the page, so a paused loop had no way forward from where it opened;
 *   - Pause read "Pause — in-flight steps finish", a promise the engine did not keep (the pause was
 *     inert); now it stops the step in flight, and between the click and the controller's next step
 *     the status still reads `running`, so the button must say "Pausing…" rather than look ignored;
 *   - the page was titled "general-project" for a loop the user had just named.
 *
 * Driven through the real page with the api mocked at its module boundary, asserting on the call
 * the control makes — a button that renders and calls nothing is the failure mode being fixed.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { WorkflowRunDetailData } from '../../lib/api'
import { WorkflowRunDetail } from './WorkflowRunDetail'

const workflowRun = vi.fn<(id: string) => Promise<WorkflowRunDetailData>>()
const resumeWorkflowRun = vi.fn()
const pauseWorkflowRun = vi.fn()

vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      workflowRun: (id: string) => workflowRun(id),
      workflowContinuations: () => Promise.resolve({ continuations: [] }),
      resumeWorkflowRun: (...args: unknown[]) => resumeWorkflowRun(...args),
      pauseWorkflowRun: (...args: unknown[]) => pauseWorkflowRun(...args),
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
    status: 'running',
    spec_version: 1,
    loop_kind: 'general',
    title: 'Weekly checklist',
    pause_requested: false,
    nodes: [{ instance_path: 'root.children[0]', node_id: 'work', state: 'running' }],
    ...over,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  resumeWorkflowRun.mockResolvedValue({ ok: true, resumed: true })
  pauseWorkflowRun.mockResolvedValue({ run_id: 'run-1', pause_requested: true })
})

const mount = () => render(<WorkflowRunDetail runId="run-1" onBack={() => {}} onOpenRun={() => {}} />)

describe('a paused run has a way forward on its own page', () => {
  it('offers Resume — and it clears the pause rather than answering a gate', async () => {
    workflowRun.mockResolvedValue(loopRun({ status: 'paused', pause_requested: true }))
    mount()
    fireEvent.click(await screen.findByRole('button', { name: /resume/i }))
    await waitFor(() => expect(resumeWorkflowRun).toHaveBeenCalledWith('run-1', {}))
    // Pause again is not an action on a paused run.
    expect(screen.queryByRole('button', { name: /^pause/i })).toBeNull()
  })

  it('says "Pausing…" while the pause is on its way, and offers no second Pause', async () => {
    workflowRun.mockResolvedValue(loopRun({ status: 'running', pause_requested: true }))
    mount()
    const pausing = await screen.findByRole('button', { name: /pausing/i })
    expect(pausing.getAttribute('aria-disabled')).toBe('true')
    fireEvent.click(pausing)
    expect(pauseWorkflowRun).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: /resume/i })).toBeNull()
  })

  it('a running run still pauses, and Pause says it stops the step in flight', async () => {
    workflowRun.mockResolvedValue(loopRun())
    mount()
    const pause = await screen.findByRole('button', { name: /^pause/i })
    // The promise it makes is the one the engine keeps now.
    expect(pause.getAttribute('title')).toMatch(/stops the step in flight/)
    fireEvent.click(pause)
    await waitFor(() => expect(pauseWorkflowRun).toHaveBeenCalledWith('run-1'))
  })
})

describe('a loop run is named by the loop', () => {
  it('heads the page with the loop title, not the template', async () => {
    workflowRun.mockResolvedValue(loopRun())
    mount()
    expect(await screen.findByRole('heading', { level: 1, name: 'Weekly checklist' })).toBeTruthy()
    expect(screen.queryByRole('heading', { level: 1, name: 'general-project' })).toBeNull()
  })

  it('a template run keeps its template name', async () => {
    workflowRun.mockResolvedValue(loopRun({ loop_kind: '', title: '' }))
    mount()
    expect(await screen.findByRole('heading', { level: 1, name: 'general-project' })).toBeTruthy()
  })
})
