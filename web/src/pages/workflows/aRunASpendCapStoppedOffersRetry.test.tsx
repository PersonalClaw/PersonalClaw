/**
 * A run a daily spend cap stopped says so, and offers Retry once the cap has room.
 *
 * Measured: a best-of-n run whose sampling step the daily dollar cap refused ended "This run
 * stopped", with "A new run of this workflow fails the same way until the step changes" and only
 * "Change the workflow" on offer. Nothing in the workflow was wrong, and the step's own fix said
 * so: raise or remove the cap in Settings → Guardrails, or wait for it to reset. The panel gave
 * every failure a retry cannot clear the same sentence.
 *
 * Driven through the real page with the api mocked at its module boundary, as the transient-failure
 * Retry is (`failedRunRetry.test.tsx`).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { UsageBudget, WorkflowRunDetailData } from '../../lib/api'
import { WorkflowRunDetail } from './WorkflowRunDetail'
import { capsHaveRoom, capResetTime, retryWindow, spendCapStop } from './attentionMeta'

const workflowRun = vi.fn<(id: string) => Promise<WorkflowRunDetailData>>()
const usageBudget = vi.fn<() => Promise<UsageBudget>>()
const forkWorkflowRun = vi.fn()
const startDraftWorkflowRun = vi.fn()
const notify = vi.fn()

vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      workflowRun: (id: string) => workflowRun(id),
      workflowContinuations: () => Promise.resolve({ continuations: [] }),
      usageBudget: () => usageBudget(),
      forkWorkflowRun: (...args: unknown[]) => forkWorkflowRun(...args),
      startDraftWorkflowRun: (...args: unknown[]) => startDraftWorkflowRun(...args),
    },
  }
})

vi.mock('../../app/appSdk', async (importActual) => {
  const actual = await importActual<typeof import('../../app/appSdk')>()
  return { ...actual, notify: (...args: unknown[]) => notify(...args) }
})

vi.mock('./useWorkflowStream', () => ({ useWorkflowStream: () => ({ connected: true }) }))
vi.mock('./RunToolApprovals', () => ({ RunToolApprovals: () => null }))
vi.mock('./DeliverablePanel', () => ({ DeliverablePanel: () => null }))

/** The step's own way out, as the gateway words every cap refusal (`BudgetExceededError.fix`). */
const FIX = 'raise Max dollars / day in Settings → Guardrails (0 removes the cap), or wait for it to reset at midnight, then Retry'

/** The measured run: `sample` refused by the daily dollar cap, `select` skipped. */
function cappedRun(failureClass = 'budget', fixInstruction = ''): WorkflowRunDetailData {
  const escalation = {
    kind: 'escalation',
    node_id: 'sample',
    instance_path: 'root.children[0]',
    reason: 'not_retried',
    detail: 'The daily dollar budget is spent ($33.79 of $33.50)',
    attempts: [{
      attempt: 1, failure_class: failureClass, error: 'The daily dollar budget is spent ($33.79 of $33.50)',
      fix_instruction: fixInstruction,
    }],
  }
  return {
    run_id: 'run-1',
    workflow: 'best-of-n',
    status: 'failed',
    spec_version: 1,
    error: '',
    attention: escalation,
    escalations: [escalation],
    nodes: [
      {
        instance_path: 'root.children[0]',
        node_id: 'sample',
        state: 'failed',
        failure: {
          class: failureClass,
          cause_plain: 'The daily dollar budget is spent ($33.79 of $33.50)',
          remediation: failureClass === 'budget' ? FIX : 'check this step’s configuration',
          retryable: false,
        },
      },
      { instance_path: 'root.children[1]', node_id: 'select', state: 'skipped' },
    ],
  }
}

/** The next local midnight, as `/api/usage/budget` reports it. */
function nextMidnight(): number {
  const d = new Date()
  d.setHours(24, 0, 0, 0)
  return d.getTime() / 1000
}

function caps(spent: number, cap: number | null): UsageBudget {
  return {
    spent_dollars: spent, spent_tokens: 0, unpriced_calls: 0,
    max_dollars_per_day: cap, max_tokens_per_day: null, cap_unreadable: false,
    resets_at: nextMidnight(),
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  forkWorkflowRun.mockResolvedValue({ child_run_id: 'child-9', shared_axes: ['a', 'b', 'c'] })
  startDraftWorkflowRun.mockResolvedValue({ ok: true, run_id: 'child-9', started: true })
})

describe('a run a spend cap stopped', () => {
  it('says a spend cap stopped it, in the step’s own words, and never that the step must change', async () => {
    workflowRun.mockResolvedValue(cappedRun())
    usageBudget.mockResolvedValue(caps(33.79, 33.5))
    render(<WorkflowRunDetail runId="run-1" onBack={() => {}} onOpenRun={() => {}} />)
    const panel = await screen.findByTestId('escalation-panel')
    expect(within(panel).getByRole('heading').textContent).toBe('This run stopped at a spend cap')
    expect(panel.textContent).not.toMatch(/fails the same way until the step changes/)
    expect(within(panel).queryByRole('link', { name: /change the workflow/i })).toBeNull()
    await waitFor(() => expect(panel.textContent).toContain(`${FIX.charAt(0).toUpperCase()}${FIX.slice(1)}.`))
  })

  it('says the fix once when the attempt above already suggests it', async () => {
    workflowRun.mockResolvedValue(cappedRun('budget', FIX))
    usageBudget.mockResolvedValue(caps(33.79, 33.5))
    render(<WorkflowRunDetail runId="run-1" onBack={() => {}} onOpenRun={() => {}} />)
    const panel = await screen.findByTestId('escalation-panel')
    await waitFor(() => expect(panel.textContent).toContain('Retry is offered once the cap has room.'))
    expect(panel.textContent?.split('wait for it to reset at midnight').length).toBe(2)
  })

  it('links Settings → Guardrails and says when the caps reset, on her own clock', async () => {
    workflowRun.mockResolvedValue(cappedRun())
    usageBudget.mockResolvedValue(caps(33.79, 33.5))
    render(<WorkflowRunDetail runId="run-1" onBack={() => {}} onOpenRun={() => {}} />)
    const panel = await screen.findByTestId('escalation-panel')
    const link = within(panel).getByRole('link', { name: /open settings → guardrails/i })
    expect(link.getAttribute('href')).toBe('#/settings/guardrails')
    expect(capResetTime(nextMidnight())).toMatch(/^12:00\sAM$/)
    await waitFor(() =>
      expect(panel.textContent).toContain(`The day's caps reset at ${capResetTime(nextMidnight())}, your time.`),
    )
  })

  it('holds Retry while the cap is still full, and says what frees it', async () => {
    workflowRun.mockResolvedValue(cappedRun())
    usageBudget.mockResolvedValue(caps(33.79, 33.5))
    render(<WorkflowRunDetail runId="run-1" onBack={() => {}} onOpenRun={() => {}} />)
    const button = await screen.findByRole('button', { name: /retry/i })
    await waitFor(() => expect(button.getAttribute('aria-disabled')).toBe('true'))
    expect(button.getAttribute('title')).toMatch(/Raise it in Settings → Guardrails, or Retry after it resets at 12:00\sAM/)
    expect(screen.getByText(/Retry is offered once the cap has room\./)).toBeTruthy()
    fireEvent.click(button)
    expect(forkWorkflowRun).not.toHaveBeenCalled()
  })

  it('Retries once the cap has been raised: a new run that keeps every finished step', async () => {
    workflowRun.mockResolvedValue(cappedRun())
    usageBudget.mockResolvedValue(caps(33.79, 50))
    const onOpenRun = vi.fn()
    render(<WorkflowRunDetail runId="run-1" onBack={() => {}} onOpenRun={onOpenRun} />)
    await screen.findByText(/The caps have room now/)
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))
    await waitFor(() => expect(onOpenRun).toHaveBeenCalledWith('child-9'))
    expect(forkWorkflowRun).toHaveBeenCalledWith('run-1', expect.anything())
    expect(startDraftWorkflowRun).toHaveBeenCalledWith('child-9')
  })

  it('reads the caps again on Retry, and starts nothing when they filled up since', async () => {
    workflowRun.mockResolvedValue(cappedRun())
    usageBudget.mockResolvedValueOnce(caps(10, 33.5)).mockResolvedValue(caps(33.6, 33.5))
    render(<WorkflowRunDetail runId="run-1" onBack={() => {}} onOpenRun={() => {}} />)
    await screen.findByText(/The caps have room now/)
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))
    await screen.findByText(/Retry is offered once the cap has room\./)
    expect(forkWorkflowRun).not.toHaveBeenCalled()
  })

  it('a step that gave up for another reason still points at the workflow (the control)', async () => {
    workflowRun.mockResolvedValue(cappedRun('user'))
    render(<WorkflowRunDetail runId="run-1" onBack={() => {}} onOpenRun={() => {}} />)
    const panel = await screen.findByTestId('escalation-panel')
    expect(within(panel).getByRole('heading').textContent).toBe('This run stopped')
    expect(panel.textContent).toMatch(/fails the same way until the step changes/)
    expect(screen.queryByRole('button', { name: /retry/i })).toBeNull()
    expect(usageBudget).not.toHaveBeenCalled()
  })
})

describe('what decides it', () => {
  it('a spend-cap stop is every escalated step failing at a cap, which the engine never retries', () => {
    expect(spendCapStop(cappedRun())).toEqual({ fix: FIX })
    expect(retryWindow(cappedRun())).toBeNull()
    expect(spendCapStop(cappedRun('user'))).toBeNull()
  })

  it('the caps have room when each one set has less spent than it allows', () => {
    expect(capsHaveRoom(caps(33.79, 33.5))).toBe(false)
    expect(capsHaveRoom(caps(33.79, 50))).toBe(true) // raised
    expect(capsHaveRoom(caps(33.79, 0))).toBe(true) // removed
    expect(capsHaveRoom(caps(0, 33.5))).toBe(true) // reset
    expect(capsHaveRoom({ ...caps(0, 33.5), max_tokens_per_day: 1000, spent_tokens: 1000 })).toBe(false)
    expect(capsHaveRoom({ ...caps(0, 33.5), cap_unreadable: true })).toBe(false)
    expect(capsHaveRoom(null)).toBe(false)
  })
})
