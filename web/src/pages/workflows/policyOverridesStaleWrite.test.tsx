import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { PolicyOverridesPanel } from './PolicyOverridesPanel'

// ── A policy-overlay save from a stale copy is refused, and the edit is kept ───────────────────
//
// Every edit PUTs the panel's WHOLE overlay with one knob changed. Tab A painted `{max_cycles: 5}`;
// tab B then overrode Attended; tab A's next edit sent `{max_cycles: 5, idle_secs: 120}` and the
// gateway replaced tab B's overlay with it — Attended back to the kind default, nothing said. The
// gateway now refuses a stale copy (`409 stale_write`); this pins what the panel does with that.

const setWorkflowRunPolicyOverrides = vi.fn()
const workflowRun = vi.fn()
vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      setWorkflowRunPolicyOverrides: (...a: unknown[]) => setWorkflowRunPolicyOverrides(...a),
      workflowRun: (...a: unknown[]) => workflowRun(...a),
    },
  }
})

const stale = () => Object.assign(new Error('This write replaces the policy overrides…'), { status: 409, code: 'stale_write' })

beforeEach(() => {
  setWorkflowRunPolicyOverrides.mockReset()
  // What is stored now: the other tab's Attended override landed after this panel read the run.
  workflowRun.mockReset().mockResolvedValue({
    run_id: 'r1', workflow: 'w', status: 'draft', spec_version: 1, nodes: [],
    policy_overrides: { max_cycles: 5, attended: true }, revisions: { policy_overrides: 'v2' },
  })
})

const openOverride = (label: string) => act(async () => { fireEvent.click(screen.getByTitle(`Override ${label} for this run only`)) })

const notice = () => waitFor(() => {
  const el = document.querySelector<HTMLElement>('[data-stale-write="true"]')
  expect(el).not.toBeNull()
  return el!
})

describe('a policy-overlay save from a stale copy', () => {
  it('is refused with the notice, names the painted revision, and keeps the edit for re-applying', async () => {
    setWorkflowRunPolicyOverrides.mockImplementation((_id: string, overrides: Record<string, unknown>, base: string) =>
      base === 'v2'
        ? Promise.resolve({ run_id: 'r1', status: 'draft', policy_overrides: overrides, revisions: { policy_overrides: 'v3' } })
        : Promise.reject(stale()))
    render(<PolicyOverridesPanel runId="r1" initial={{ value: { max_cycles: 5 }, revision: 'v1' }} />)
    await openOverride('Idle seconds')
    const el = await notice()
    expect(el.getAttribute('role')).toBe('alert')
    expect(el.textContent).toMatch(/This run's policy overrides changed elsewhere/)
    expect(setWorkflowRunPolicyOverrides.mock.calls[0]).toEqual(['r1', { max_cycles: 5, idle_secs: 120 }, 'v1'])

    const reapply = within(el).getByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.hasAttribute('disabled')).toBe(false))
    await act(async () => { fireEvent.click(reapply) })
    await waitFor(() => expect(setWorkflowRunPolicyOverrides).toHaveBeenCalledTimes(2))
    // The other tab's Attended override survives; the Idle seconds edit lands on top, over v2.
    expect(setWorkflowRunPolicyOverrides.mock.calls[1]).toEqual(['r1', { max_cycles: 5, attended: true, idle_secs: 120 }, 'v2'])
    await waitFor(() => expect(document.querySelector('[data-stale-write="true"]')).toBeNull())
    expect(screen.getByLabelText('Attended override')).toBeTruthy()
  })

  it('a landed save moves the base to the revision the gateway answered', async () => {
    setWorkflowRunPolicyOverrides
      .mockResolvedValueOnce({ run_id: 'r1', status: 'draft', policy_overrides: { max_cycles: 5, idle_secs: 120 }, revisions: { policy_overrides: 'v2' } })
      .mockResolvedValue({ run_id: 'r1', status: 'draft', policy_overrides: { max_cycles: 5, idle_secs: 120, attended: true }, revisions: { policy_overrides: 'v3' } })
    render(<PolicyOverridesPanel runId="r1" initial={{ value: { max_cycles: 5 }, revision: 'v1' }} />)
    await openOverride('Idle seconds')
    await waitFor(() => expect(setWorkflowRunPolicyOverrides).toHaveBeenCalledTimes(1))
    await openOverride('Attended')
    await waitFor(() => expect(setWorkflowRunPolicyOverrides).toHaveBeenCalledTimes(2))
    expect(setWorkflowRunPolicyOverrides.mock.calls.map((c) => c[2])).toEqual(['v1', 'v2'])
  })
})
